//! One SSH exec channel to the paired Termux IME. Dropping its lease closes both streams.
use russh::client;
use std::{io::ErrorKind, path::PathBuf, time::Duration};
use tokio::{
    net::UnixStream,
    sync::mpsc,
    time::{sleep, timeout},
};
use tokio_util::sync::CancellationToken;

const RELAY: &str =
    "exec \"$HOME/.local/share/eidolon-ime-native/d55a051/stage/bin/eidolon-ime-relay\"";

#[derive(Clone)]
pub struct Endpoint {
    requests: mpsc::Sender<Request>,
    session: CancellationToken,
}

pub struct Lease(CancellationToken);
impl Lease {
    pub fn finished(&self) -> bool {
        self.0.is_cancelled()
    }
}
impl Drop for Lease {
    fn drop(&mut self) {
        self.0.cancel();
    }
}
pub(crate) struct Request {
    local: PathBuf,
    cancel: CancellationToken,
}
impl Drop for Request {
    fn drop(&mut self) {
        self.cancel.cancel();
    }
}
impl Endpoint {
    pub(crate) fn channel(session: &CancellationToken) -> (Self, mpsc::Receiver<Request>) {
        let (requests, rx) = mpsc::channel(1);
        (
            Self {
                requests,
                session: session.child_token(),
            },
            rx,
        )
    }
    pub fn ready(&self) -> bool {
        !self.requests.is_closed() && !self.session.is_cancelled()
    }
    pub fn same_connection(&self, other: &Self) -> bool {
        self.requests.same_channel(&other.requests)
    }
    pub fn attach(&self, local: PathBuf) -> Result<Lease, &'static str> {
        if !self.ready() {
            return Err("NOT_READY");
        }
        let cancel = self.session.child_token();
        self.requests
            .try_send(Request {
                local,
                cancel: cancel.clone(),
            })
            .map_err(|_| "NOT_READY")?;
        Ok(Lease(cancel))
    }
}

pub(crate) async fn start<H: client::Handler>(client: &client::Handle<H>, request: Request) {
    // Once opened, the SSH channel owns everything needed by the transfer.
    let Ok(Ok(channel)) = (tokio::select! {
        _ = request.cancel.cancelled() => return,
        result = timeout(Duration::from_secs(4), client.channel_open_session()) => result,
    }) else {
        return;
    };
    tokio::spawn(async move {
        let run = async {
            let setup = async {
                let local = loop {
                    match UnixStream::connect(&request.local).await {
                        Err(e)
                            if matches!(
                                e.kind(),
                                ErrorKind::NotFound | ErrorKind::ConnectionRefused
                            ) =>
                        {
                            sleep(Duration::from_millis(20)).await
                        }
                        other => break other.ok()?,
                    }
                };
                channel.exec(true, RELAY).await.ok()?;
                Some((local, channel.into_stream()))
            };
            if let Ok(Some((mut local, mut remote))) = timeout(Duration::from_secs(4), setup).await
            {
                let _ = tokio::io::copy_bidirectional(&mut local, &mut remote).await;
            }
        };
        tokio::select! { _ = request.cancel.cancelled() => (), _ = run => () }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_queued_request_per_connection_and_both_owners_can_cancel() {
        let session = CancellationToken::new();
        let (ep, mut rx) = Endpoint::channel(&session);
        let (other, _other_rx) = Endpoint::channel(&CancellationToken::new());
        assert!(ep.same_connection(&ep.clone()));
        assert!(!ep.same_connection(&other));
        let lease = ep.attach("first".into()).unwrap();
        assert!(ep.attach("queued".into()).is_err());
        let request = rx.try_recv().unwrap();
        drop(lease);
        assert!(request.cancel.is_cancelled());
        let lease = ep.attach("second".into()).unwrap();
        drop(rx.try_recv().unwrap());
        assert!(lease.finished());
        let lease = ep.attach("third".into()).unwrap();
        session.cancel();
        assert!(lease.finished());
        assert!(!ep.ready());
        assert!(ep.attach("stale".into()).is_err());
        drop(rx);
    }
}
