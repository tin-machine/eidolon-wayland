use russh::{ChannelMsg, client};
use tokio::time::{Duration, timeout};
use tokio_util::sync::CancellationToken;

const CHANNEL_TIMEOUT: Duration = Duration::from_secs(5);
const COMMAND_TIMEOUT: Duration = Duration::from_secs(13);
const CLOSE_TIMEOUT: Duration = Duration::from_secs(1);
const STDOUT_LIMIT: usize = 4096;
const STDERR_LIMIT: usize = 16 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExchangeError {
    /// The exec request was never attempted.
    NotSent,
    /// The request may have run. Journal and reconcile its generation.
    OutcomeUnknown,
    /// The remote command closed with a distinct exit status.
    Rejected(u32),
}

/// Run one typed systemd command on the existing host-key-pinned connection.
///
/// The journal writer must durably record the operation before returning success from
/// before_exec. The caller retains its capture whenever the result is OutcomeUnknown.
pub async fn exchange<H: client::Handler>(
    client: &client::Handle<H>,
    command: String,
    cancel: &CancellationToken,
    before_exec: impl FnOnce() -> Result<(), ExchangeError>,
) -> Result<Vec<u8>, ExchangeError> {
    let mut channel = tokio::select! {
        biased;
        _ = cancel.cancelled() => return Err(ExchangeError::NotSent),
        outcome = timeout(CHANNEL_TIMEOUT, client.channel_open_session()) => {
            outcome.ok().and_then(Result::ok).ok_or(ExchangeError::NotSent)?
        }
    };
    if cancel.is_cancelled() {
        let _ = timeout(CLOSE_TIMEOUT, channel.close()).await;
        return Err(ExchangeError::NotSent);
    }
    if let Err(error) = before_exec() {
        let _ = timeout(CLOSE_TIMEOUT, channel.close()).await;
        return Err(error);
    }
    let remote = async {
        channel
            .exec(true, command)
            .await
            .map_err(|_| ExchangeError::OutcomeUnknown)?;
        let mut reply = Reply::default();
        while let Some(message) = channel.wait().await {
            if !reply.receive(message)? {
                break;
            }
        }
        reply.finish()
    };
    let result = tokio::select! {
        biased;
        // Cancellation after the durable write cannot prove that the remote did nothing.
        _ = cancel.cancelled() => Err(ExchangeError::OutcomeUnknown),
        output = timeout(COMMAND_TIMEOUT, remote) => {
            output.unwrap_or(Err(ExchangeError::OutcomeUnknown))
        }
    };
    let _ = timeout(CLOSE_TIMEOUT, channel.eof()).await;
    let _ = timeout(CLOSE_TIMEOUT, channel.close()).await;
    result
}

#[derive(Default)]
struct Reply {
    stdout: Vec<u8>,
    stderr_bytes: usize,
    status: Option<u32>,
    eof: bool,
}

impl Reply {
    fn receive(&mut self, message: ChannelMsg) -> Result<bool, ExchangeError> {
        match message {
            ChannelMsg::Data { data }
                if !self.eof && self.stdout.len() + data.len() <= STDOUT_LIMIT =>
            {
                self.stdout.extend_from_slice(&data);
            }
            ChannelMsg::ExtendedData { data, ext: 1 }
                if !self.eof && self.stderr_bytes + data.len() <= STDERR_LIMIT =>
            {
                self.stderr_bytes += data.len();
            }
            ChannelMsg::ExitStatus { exit_status } if self.status.is_none() => {
                self.status = Some(exit_status);
            }
            ChannelMsg::Eof if !self.eof => self.eof = true,
            ChannelMsg::Close => return Ok(false),
            ChannelMsg::Data { .. }
            | ChannelMsg::ExtendedData { .. }
            | ChannelMsg::ExitSignal { .. }
            | ChannelMsg::ExitStatus { .. }
            | ChannelMsg::Eof
            | ChannelMsg::Failure => return Err(ExchangeError::OutcomeUnknown),
            _ => {}
        }
        Ok(true)
    }

    fn finish(self) -> Result<Vec<u8>, ExchangeError> {
        match (self.eof, self.status) {
            (true, Some(0)) => Ok(self.stdout),
            (true, Some(status)) => Err(ExchangeError::Rejected(status)),
            _ => Err(ExchangeError::OutcomeUnknown),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signalled_or_truncated_response_requires_reconciliation() {
        let mut reply = Reply::default();
        assert_eq!(
            reply.receive(ChannelMsg::Data {
                data: vec![0; STDOUT_LIMIT + 1].into()
            }),
            Err(ExchangeError::OutcomeUnknown),
        );
        assert_eq!(reply.receive(ChannelMsg::Eof), Ok(true));
        assert_eq!(
            reply.receive(ChannelMsg::Data {
                data: vec![0].into()
            }),
            Err(ExchangeError::OutcomeUnknown),
        );
        assert_eq!(reply.finish(), Err(ExchangeError::OutcomeUnknown));
    }

    #[test]
    fn only_eof_and_exactly_one_status_establish_an_outcome() {
        for status in [0, 5] {
            let mut reply = Reply::default();
            assert_eq!(
                reply.receive(ChannelMsg::ExitStatus {
                    exit_status: status
                }),
                Ok(true)
            );
            assert_eq!(
                reply.receive(ChannelMsg::ExitStatus {
                    exit_status: status
                }),
                Err(ExchangeError::OutcomeUnknown),
            );
            assert_eq!(reply.receive(ChannelMsg::Eof), Ok(true));
            assert_eq!(reply.receive(ChannelMsg::Close), Ok(false));
            let expected = if status == 0 {
                Ok(Vec::new())
            } else {
                Err(ExchangeError::Rejected(status))
            };
            assert_eq!(reply.finish(), expected);
        }
    }
}
