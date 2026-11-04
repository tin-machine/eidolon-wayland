use russh::{Channel, ChannelMsg, client};
use serde::Serialize;
use std::collections::VecDeque;

pub const TOKEN: &str = "EIDOLON_SSH_PROBE_1";
pub const COMMAND: &str = "id -u; printf 'EIDOLON_SSH_PROBE_1\\n'";
const CAP: usize = 64 * 1024;
#[derive(Clone, Default, Debug, Serialize)]
pub struct Report {
    pub uid: Option<u32>,
    pub token_matched: bool,
    pub stdout_bytes: u64,
    pub stderr_bytes: u64,
    pub stdout_truncated: bool,
    pub stderr_truncated: bool,
    pub exit_status: Option<u32>,
    pub exit_signal: bool,
    pub eof: bool,
}
#[derive(Default)]
struct Ring {
    bytes: VecDeque<u8>,
    total: u64,
}
impl Ring {
    fn push(&mut self, data: &[u8]) {
        self.total = self.total.saturating_add(data.len() as u64);
        if data.len() >= CAP {
            self.bytes.clear();
            self.bytes.extend(&data[data.len() - CAP..]);
        } else {
            while self.bytes.len() + data.len() > CAP {
                self.bytes.pop_front();
            }
            self.bytes.extend(data);
        }
    }
}
pub async fn probe(
    mut channel: Channel<client::Msg>,
    mut progress: impl FnMut(&Report),
) -> Result<Report, &'static str> {
    channel
        .exec(true, COMMAND)
        .await
        .map_err(|_| "SSH_IO_FAILED")?;
    let (mut out, mut err, mut report) = (Ring::default(), Ring::default(), Report::default());
    while let Some(message) = channel.wait().await {
        match message {
            ChannelMsg::Data { data } => out.push(&data),
            ChannelMsg::ExtendedData { data, ext: 1 } => err.push(&data),
            ChannelMsg::ExtendedData { .. } => return Err("PROTOCOL_ERROR"),
            ChannelMsg::ExitStatus { exit_status } => report.exit_status = Some(exit_status),
            ChannelMsg::ExitSignal { .. } => report.exit_signal = true,
            ChannelMsg::Eof => report.eof = true,
            ChannelMsg::Close => break,
            _ => (),
        }
        report.stdout_bytes = out.total;
        report.stderr_bytes = err.total;
        report.stdout_truncated = out.total > CAP as u64;
        report.stderr_truncated = err.total > CAP as u64;
        progress(&report);
    }
    if report.exit_status != Some(0) || report.exit_signal {
        return Err("REMOTE_EXIT");
    }
    if !report.eof || report.stdout_truncated {
        return Err("PROTOCOL_ERROR");
    }
    let bytes: Vec<_> = out.bytes.into_iter().collect();
    let text = std::str::from_utf8(&bytes).map_err(|_| "PROTOCOL_ERROR")?;
    let mut lines = text.lines();
    report.uid = lines.next().and_then(|line| line.parse().ok());
    report.token_matched = lines.next() == Some(TOKEN) && lines.next().is_none();
    progress(&report);
    if report.uid.is_none() || !report.token_matched {
        return Err("PROTOCOL_ERROR");
    }
    Ok(report)
}

/// 固定launcherの短いJSON応答。stderrはcapを超えてもEOFまで排出する。
pub(crate) async fn json(
    mut channel: Channel<client::Msg>,
    command: String,
) -> Result<serde_json::Value, &'static str> {
    channel
        .exec(true, command)
        .await
        .map_err(|_| "SSH_IO_FAILED")?;
    let (mut out, mut err) = (Ring::default(), Ring::default());
    let (mut status, mut signal, mut eof) = (None, false, false);
    while let Some(message) = channel.wait().await {
        match message {
            ChannelMsg::Data { data } => out.push(&data),
            ChannelMsg::ExtendedData { data, ext: 1 } => err.push(&data),
            ChannelMsg::ExitStatus { exit_status } => status = Some(exit_status),
            ChannelMsg::ExitSignal { .. } => signal = true,
            ChannelMsg::Eof => eof = true,
            ChannelMsg::Close => break,
            ChannelMsg::Failure | ChannelMsg::ExtendedData { .. } => return Err("PROTOCOL_ERROR"),
            _ => (),
        }
    }
    if status != Some(0) || signal {
        return Err("REMOTE_EXIT");
    }
    if !eof || out.total > CAP as u64 {
        return Err("PROTOCOL_ERROR");
    }
    serde_json::from_slice(&out.bytes.into_iter().collect::<Vec<_>>()).map_err(|_| "PROTOCOL_ERROR")
}
