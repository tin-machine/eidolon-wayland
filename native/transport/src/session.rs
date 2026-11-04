//! SSHはI/O ownerが作るTokio runtime上だけで実行する。再接続前に旧runtimeを回収する。
use crate::{
    exec,
    host_key::{Profile, parse_key},
};
use eidolon_model::{
    ids::{CommandSeq, SessionId},
    state::Status,
};
use russh::{
    client,
    keys::{PrivateKey, PrivateKeyWithHashAlg, PublicKey, PublicKeyOrCertificate},
};
use serde::Serialize;
use std::{
    net::{Ipv4Addr, SocketAddr},
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread::{self, JoinHandle},
    time::Duration,
};
use tokio::{
    sync::Notify,
    time::{Instant, timeout, timeout_at},
};
use tokio_util::sync::CancellationToken;

#[derive(Clone, Copy, PartialEq, Eq, Debug, Default, Serialize)]
pub enum State {
    #[default]
    Idle,
    Connecting,
    AwaitingCredential,
    Authenticating,
    Ready,
    Disconnected,
    Disconnecting,
    Failed,
    Stopped,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Snapshot {
    pub state: State,
    pub session: Option<SessionId>,
    pub profile_id: Option<String>,
    pub credential_request: Option<CommandSeq>,
    pub workers: usize,
    pub tokio_threads: usize,
    pub owned_connections: usize,
    pub credential_requests: u64,
    pub error: Option<&'static str>,
    pub probe: exec::Report,
}
#[derive(Clone, Copy)]
struct Deadlines {
    connect: Duration,
    auth: Duration,
    exec: Duration,
}
impl Default for Deadlines {
    fn default() -> Self {
        Self {
            connect: Duration::from_secs(5),
            auth: Duration::from_secs(10),
            exec: Duration::from_secs(10),
        }
    }
}
struct Connect {
    profile: Profile,
    pin: PublicKey,
    request: CommandSeq,
    cancel: CancellationToken,
}
struct Shared {
    report: Snapshot,
    connect: Option<Connect>,
    credential: Option<PrivateKey>,
    busy: bool,
    cancel: Option<CancellationToken>,
}
struct Signals {
    shared: Mutex<Shared>,
    cv: Condvar,
    notify: Notify,
    stop: AtomicBool,
    tokio_workers: AtomicUsize,
}
impl Signals {
    fn update(&self, action: impl FnOnce(&mut Shared)) {
        action(&mut self.shared.lock().unwrap_or_else(|e| e.into_inner()));
    }
}
pub struct Controller {
    signals: Arc<Signals>,
    worker: JoinHandle<()>,
}
impl Controller {
    pub fn start() -> std::io::Result<Self> {
        Self::start_on((Ipv4Addr::LOCALHOST, 8022).into(), Deadlines::default())
    }
    fn start_on(endpoint: SocketAddr, deadlines: Deadlines) -> std::io::Result<Self> {
        let signals = Arc::new(Signals {
            shared: Mutex::new(Shared {
                report: Snapshot {
                    workers: 1,
                    ..Default::default()
                },
                connect: None,
                credential: None,
                busy: false,
                cancel: None,
            }),
            cv: Condvar::new(),
            notify: Notify::new(),
            stop: AtomicBool::new(false),
            tokio_workers: AtomicUsize::new(0),
        });
        let owned = signals.clone();
        let worker = thread::Builder::new()
            .name("eidolon-ssh-owner".into())
            .spawn(move || {
                let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    owner(&owned, endpoint, deadlines)
                }));
                owned.update(|s| {
                    s.connect = None;
                    s.credential = None;
                    s.busy = false;
                    s.report.workers = 0;
                    s.report.owned_connections = 0;
                    s.report.credential_request = None;
                    if outcome.is_err() {
                        s.report.error = Some("JNI_FAILURE");
                        s.report.state = State::Failed;
                    } else {
                        s.report.state = State::Stopped;
                    }
                });
            })?;
        Ok(Self { signals, worker })
    }
    pub fn connect(&self, profile: Profile, session: SessionId, request: CommandSeq) -> Status {
        let pin = match parse_key(&profile.host_key) {
            Ok(pin) => pin,
            Err(_) => return Status::InvalidArgument,
        };
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if self.signals.stop.load(Ordering::Acquire) || self.worker.is_finished() {
            return Status::NotReady;
        }
        if s.busy {
            return Status::Busy;
        }
        if s.report.session.is_some_and(|previous| session <= previous) {
            return Status::Stale;
        }
        s.busy = true;
        s.report = Snapshot {
            state: State::Connecting,
            session: Some(session),
            profile_id: Some(profile.profile_id.clone()),
            workers: 1,
            credential_requests: s.report.credential_requests,
            ..Default::default()
        };
        let cancel = CancellationToken::new();
        s.cancel = Some(cancel.clone());
        s.connect = Some(Connect {
            profile,
            pin,
            request,
            cancel,
        });
        self.signals.cv.notify_one();
        Status::Accepted
    }
    pub fn unknown_profile(&self) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.busy {
            return Status::Busy;
        }
        s.report.state = State::Failed;
        s.report.error = Some("HOST_KEY_UNKNOWN");
        Status::NotReady
    }
    pub fn provide(&self, session: SessionId, request: CommandSeq, key: PrivateKey) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if self.signals.stop.load(Ordering::Acquire)
            || s.cancel.as_ref().is_some_and(|c| c.is_cancelled())
            || s.report.session != Some(session)
            || s.report.credential_request != Some(request)
            || s.report.state != State::AwaitingCredential
            || s.credential.is_some()
        {
            return Status::Stale;
        }
        s.credential = Some(key);
        self.signals.notify.notify_one();
        Status::Accepted
    }
    pub fn disconnect(&self, session: SessionId) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if let Some(cancel) = &s.cancel {
            cancel.cancel();
        }
        s.credential = None;
        s.report.credential_request = None;
        if s.busy {
            s.report.state = State::Disconnecting;
        }
        self.signals.notify.notify_one();
        Status::Accepted
    }
    pub fn snapshot(&self) -> Snapshot {
        let mut report = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .report
            .clone();
        report.tokio_threads = self.signals.tokio_workers.load(Ordering::Acquire);
        report
    }
    pub fn request_stop(&self) {
        self.signals.stop.store(true, Ordering::Release);
        self.signals.update(|s| {
            s.credential = None;
            if let Some(cancel) = &s.cancel {
                cancel.cancel();
            }
        });
        self.signals.cv.notify_one();
        self.signals.notify.notify_one();
    }
    pub fn is_finished(&self) -> bool {
        self.worker.is_finished()
    }
}
impl Drop for Controller {
    fn drop(&mut self) {
        self.request_stop();
    }
}

fn owner(signals: &Arc<Signals>, endpoint: SocketAddr, deadlines: Deadlines) {
    loop {
        let connect = {
            let mut s = signals.shared.lock().unwrap_or_else(|e| e.into_inner());
            while s.connect.is_none() && !signals.stop.load(Ordering::Acquire) {
                s = signals
                    .cv
                    .wait_timeout(s, Duration::from_millis(100))
                    .unwrap_or_else(|e| e.into_inner())
                    .0;
            }
            if signals.stop.load(Ordering::Acquire) {
                break;
            }
            s.connect.take().unwrap()
        };
        let (start, end) = (signals.clone(), signals.clone());
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .max_blocking_threads(1)
            .enable_all()
            .thread_name("eidolon-ssh")
            .on_thread_start(move || {
                start.tokio_workers.fetch_add(1, Ordering::AcqRel);
            })
            .on_thread_stop(move || {
                end.tokio_workers.fetch_sub(1, Ordering::AcqRel);
            })
            .build();
        let result = match runtime {
            Ok(runtime) => {
                let result = runtime.block_on(async {
                    tokio::select! {
                        result=connection(signals,endpoint,&connect,deadlines)=>result,
                        _=connect.cancel.cancelled()=>Err("CANCELLED"),
                    }
                });
                // UI/coreではなくownerでdrop。未完SSH task/FD/秘密keyとTokio workerを回収。
                drop(runtime);
                result
            }
            Err(_) => Err("SSH_IO_FAILED"),
        };
        signals.update(|s| {
            s.credential = None;
            s.cancel = None;
            s.busy = false;
            s.report.owned_connections = 0;
            s.report.credential_request = None;
            if result == Err("CANCELLED") {
                s.report.state = State::Disconnected;
                s.report.error = None;
            } else {
                s.report.state = State::Failed;
                s.report.error = Some(result.err().unwrap_or("REMOTE_EXIT"));
            }
        });
    }
}
struct Peer {
    pin: PublicKey,
    mismatch: Arc<AtomicBool>,
}
impl client::Handler for Peer {
    type Error = russh::Error;
    async fn check_server_key(
        &mut self,
        key: &PublicKeyOrCertificate,
    ) -> Result<bool, Self::Error> {
        let accepted =
            key.certificate().is_none() && self.pin.key_data() == key.public_key().key_data();
        if !accepted {
            self.mismatch.store(true, Ordering::Release);
        }
        Ok(accepted)
    }
}
async fn connection(
    signals: &Signals,
    endpoint: SocketAddr,
    connect: &Connect,
    deadlines: Deadlines,
) -> Result<(), &'static str> {
    signals.update(|s| s.report.owned_connections = 1);
    let mismatch = Arc::new(AtomicBool::new(false));
    let peer = Peer {
        pin: connect.pin.clone(),
        mismatch: mismatch.clone(),
    };
    let mut client = timeout(
        deadlines.connect,
        client::connect(Arc::new(client::Config::default()), endpoint, peer),
    )
    .await
    .map_err(|_| "CONNECT_TIMEOUT")?
    .map_err(|_| {
        if mismatch.load(Ordering::Acquire) {
            "HOST_KEY_MISMATCH"
        } else {
            "SSH_CONNECT_FAILED"
        }
    })?;
    signals.update(|s| {
        s.report.owned_connections = 1;
        s.report.state = State::AwaitingCredential;
        s.report.credential_request = Some(connect.request);
        s.report.credential_requests += 1;
    });
    let auth_deadline = Instant::now() + deadlines.auth;
    let key = timeout_at(auth_deadline, async {
        loop {
            let notified = signals.notify.notified();
            if let Some(key) = signals
                .shared
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .credential
                .take()
            {
                break key;
            }
            notified.await;
        }
    })
    .await
    .map_err(|_| "AUTH_TIMEOUT")?;
    signals.update(|s| {
        s.report.state = State::Authenticating;
        s.report.credential_request = None;
    });
    let authenticated = timeout_at(
        auth_deadline,
        client.authenticate_publickey(
            &connect.profile.username,
            PrivateKeyWithHashAlg::new(Arc::new(key), None),
        ),
    )
    .await
    .map_err(|_| "AUTH_TIMEOUT")?
    .map_err(|_| "AUTH_FAILED")?;
    if !authenticated.success() {
        return Err("AUTH_FAILED");
    }
    let report = timeout(deadlines.exec, async {
        let channel = client
            .channel_open_session()
            .await
            .map_err(|_| "SSH_IO_FAILED")?;
        exec::probe(channel, |report| {
            signals.update(|s| s.report.probe = report.clone())
        })
        .await
    })
    .await
    .map_err(|_| "EXEC_TIMEOUT")??;
    signals.update(|s| {
        s.report.probe = report;
        s.report.state = State::Ready;
    });
    // T03でforward操作をここへ追加する。接続はStop/Disconnectまで保持する。
    let _ = client.await;
    Err("REMOTE_EXIT")
}

#[cfg(test)]
mod tests;
