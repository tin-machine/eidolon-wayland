//! StreamLocal callbackでは境界検査と上限の予約だけを行う。転送は所有taskへ渡す。
use eidolon_model::ids::SessionId;
use russh::{Channel, client};
use serde::Serialize;
use std::{
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, AtomicUsize, Ordering},
    },
    time::Duration,
};
use tokio::{net::UnixStream, sync::Semaphore};
use tokio_util::{sync::CancellationToken, task::TaskTracker};

pub const MAX_CHANNELS: usize = eidolon_model::limits::FORWARD_CHANNELS;
pub const COPY_BUFFER: usize = 32 * 1024;
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub active: usize,
    pub peak: usize,
    pub accepted: u64,
    pub rejected: u64,
    pub failed: u64,
    pub remote_to_local: u64,
    pub local_to_remote: u64,
    pub copy_buffer_limit: usize,
}
#[derive(Default)]
struct Counters {
    active: AtomicUsize,
    peak: AtomicUsize,
    accepted: AtomicU64,
    rejected: AtomicU64,
    failed: AtomicU64,
    incoming: AtomicU64,
    outgoing: AtomicU64,
}
struct Route {
    remote: String,
    local: PathBuf,
    cancel: CancellationToken,
    tasks: TaskTracker,
    slots: Arc<Semaphore>,
    gate: Mutex<()>,
}
pub struct Router {
    session: SessionId,
    route: Mutex<Option<Arc<Route>>>,
    counts: Arc<Counters>,
}
pub struct Lease {
    route: Arc<Route>,
}
impl Router {
    pub fn new(session: SessionId) -> Self {
        Self {
            session,
            route: Mutex::new(None),
            counts: Arc::new(Counters::default()),
        }
    }
    /// この接続が所有するsessionと絶対socket pathだけを登録する。外部入力のpath検査はcallerが行う。
    pub fn arm(
        &self,
        session: SessionId,
        remote: String,
        local: PathBuf,
    ) -> Result<Lease, &'static str> {
        if session != self.session
            || !local.is_absolute()
            || !remote.starts_with('/')
            || local.as_os_str().len() > 103
            || remote.len() > 103
            || remote.contains('\0')
        {
            return Err("FORWARD_INVALID");
        }
        let mut current = self.route.lock().unwrap_or_else(|e| e.into_inner());
        if current
            .as_ref()
            .is_some_and(|route| !route.cancel.is_cancelled() || !route.tasks.is_empty())
        {
            return Err("FORWARD_BUSY");
        }
        let route = Arc::new(Route {
            remote,
            local,
            cancel: CancellationToken::new(),
            tasks: TaskTracker::new(),
            slots: Arc::new(Semaphore::new(MAX_CHANNELS)),
            gate: Mutex::new(()),
        });
        *current = Some(route.clone());
        Ok(Lease { route })
    }
    pub fn report(&self) -> Report {
        let c = &self.counts;
        Report {
            active: c.active.load(Ordering::Acquire),
            peak: c.peak.load(Ordering::Acquire),
            accepted: c.accepted.load(Ordering::Acquire),
            rejected: c.rejected.load(Ordering::Acquire),
            failed: c.failed.load(Ordering::Acquire),
            remote_to_local: c.incoming.load(Ordering::Acquire),
            local_to_remote: c.outgoing.load(Ordering::Acquire),
            copy_buffer_limit: MAX_CHANNELS * 2 * COPY_BUFFER,
        }
    }
    pub fn open(
        &self,
        session: SessionId,
        path: &str,
        channel: Channel<client::Msg>,
        reply: client::ChannelOpenHandle,
    ) {
        let current = self.route.lock().unwrap_or_else(|e| e.into_inner());
        let Some(route) = current.as_ref().filter(|route| {
            session == self.session && path == route.remote && !route.cancel.is_cancelled()
        }) else {
            self.counts.rejected.fetch_add(1, Ordering::AcqRel);
            return; // replyのDropがrejectする。
        };
        let _gate = route.gate.lock().unwrap_or_else(|e| e.into_inner());
        if route.cancel.is_cancelled() {
            self.counts.rejected.fetch_add(1, Ordering::AcqRel);
            return;
        }
        let Ok(slot) = route.slots.clone().try_acquire_owned() else {
            self.counts.rejected.fetch_add(1, Ordering::AcqRel);
            return;
        };
        let route = route.clone();
        let counts = self.counts.clone();
        let active = counts.active.fetch_add(1, Ordering::AcqRel) + 1;
        counts.peak.fetch_max(active, Ordering::AcqRel);
        let guard = Active(counts.clone());
        route.tasks.clone().spawn(async move {
            let (_slot, _guard) = (slot, guard);
            let result = tokio::select! {
                biased;
                _=route.cancel.cancelled()=>return,
                result=tokio::time::timeout(Duration::from_secs(2), UnixStream::connect(&route.local))=>result,
            };
            let Ok(Ok(mut local)) = result else {
                counts.failed.fetch_add(1, Ordering::AcqRel);
                return;
            };
            if route.cancel.is_cancelled() { return; }
            reply.accept().await;
            counts.accepted.fetch_add(1, Ordering::AcqRel);
            let mut ssh = channel.into_stream();
            // EOFを受けた方向だけshutdownし、反対方向の残りをdrainする。
            let result = tokio::select! {
                biased;
                _=route.cancel.cancelled()=>return,
                result=tokio::io::copy_bidirectional_with_sizes(&mut ssh, &mut local, COPY_BUFFER, COPY_BUFFER)=>result,
            };
            match result {
                Ok((incoming, outgoing)) => {
                    counts.incoming.fetch_add(incoming, Ordering::AcqRel);
                    counts.outgoing.fetch_add(outgoing, Ordering::AcqRel);
                }
                Err(_) => { counts.failed.fetch_add(1, Ordering::AcqRel); }
            }
        });
    }
}
impl Lease {
    /// callbackの新規受付を止めてから全転送taskをjoinする。core/UIからawaitしない。
    pub async fn close(self) {
        self.cancel();
        self.route.tasks.wait().await;
    }
    fn cancel(&self) {
        let _gate = self.route.gate.lock().unwrap_or_else(|e| e.into_inner());
        self.route.cancel.cancel();
        self.route.tasks.close();
    }
}
impl Drop for Lease {
    fn drop(&mut self) {
        self.cancel();
    }
}
struct Active(Arc<Counters>);
impl Drop for Active {
    fn drop(&mut self) {
        self.0.active.fetch_sub(1, Ordering::AcqRel);
    }
}

#[cfg(test)]
pub(crate) mod openssh;
#[cfg(test)]
pub(crate) mod tests;
