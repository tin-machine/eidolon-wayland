//! Explicit, pinned recovery on a new authenticated owner. Only a private
//! successful receipt or a verified previous boot can retire remote ownership.
use super::{
    Binding, Pins, REVISION, boot_identity,
    journal::{Cleanup, Journal},
    owner,
};
use crate::forward;
use eidolon_model::{
    connection_profile::{Backend, Profile},
    ids::SessionId,
};
use russh::{ChannelMsg, client};
use serde::{Deserialize, Serialize};
use std::{path::Path, sync::Arc};
use tokio::time::{Duration, timeout};
use tokio_util::sync::CancellationToken;

pub struct Recovery<'a> {
    pub profile: &'a Profile,
    pub session: SessionId,
    pub uid: u32,
    pub files: &'a Path,
    pub pins: &'a Pins,
    /// Evidence from the I/O owner: the previous connection and its forwarding
    /// tasks have been closed/joined (or the previous app process has exited).
    /// Never take this value from a UI request or a persisted journal flag.
    pub previous_connection_closed: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct RecoveryReport {
    pub session: SessionId,
    pub origin_session: Option<SessionId>,
    pub queried: bool,
    pub pending: bool,
    pub boot_identity_known: bool,
    pub boot_changed: bool,
    pub process_complete: bool,
    pub pipes_eof: bool,
    pub remote_cleaned: bool,
    pub receipt_written: bool,
    pub channel_complete: bool,
    pub router_drained: bool,
    pub error: Option<&'static str>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    schema: u32,
    backend: String,
    revision: String,
    session: SessionId,
    profile_revision: String,
    scope: String,
    nonce: String,
    uid: u32,
    expected_boot_id: String,
    origin_boot_id: Option<String>,
    boot_id: String,
    status: String,
    process_evidence: String,
    process_complete: bool,
    pipes_eof: bool,
    remote_cleaned: bool,
    receipt_written: bool,
}
impl Receipt {
    fn parse(
        raw: &[u8],
        binding: &Binding,
        expected_boot: &str,
        status: u32,
    ) -> Result<Self, &'static str> {
        let line = raw.strip_suffix(b"\n").ok_or("DESKTOP_RECOVERY_INVALID")?;
        if line.is_empty() || raw.len() > 16384 || line.contains(&b'\n') || line.contains(&b'\r') {
            return Err("DESKTOP_RECOVERY_INVALID");
        }
        let value: Self = serde_json::from_slice(line).map_err(|_| "DESKTOP_RECOVERY_INVALID")?;
        if value.schema != 1
            || value.backend != "linux_desktop"
            || value.revision != REVISION
            || value.session != binding.session
            || value.profile_revision != binding.profile_revision
            || value.scope != binding.scope
            || value.nonce != binding.nonce
            || value.uid != binding.uid
            || value.expected_boot_id != expected_boot
            || !boot_identity(&value.boot_id)
            || value.origin_boot_id.as_deref().is_some_and(|boot| {
                !boot_identity(boot) || expected_boot != "-" && boot != expected_boot
            })
            || !matches!(
                (value.status.as_str(), status),
                ("complete", 0) | ("pending", 1)
            )
        {
            return Err("DESKTOP_RECOVERY_SCOPE");
        }
        let proof = match value.process_evidence.as_str() {
            "receipt" => {
                value.origin_boot_id.is_some()
                    && value.process_complete
                    && value.pipes_eof
                    && value.receipt_written
            }
            "boot_changed" => {
                value
                    .origin_boot_id
                    .as_deref()
                    .is_some_and(|old| old != value.boot_id)
                    && value.process_complete
                    && !value.pipes_eof
            }
            "unknown" => {
                !value.process_complete
                    && !value.pipes_eof
                    && !value.remote_cleaned
                    && !value.receipt_written
            }
            _ => false,
        };
        if !proof
            || value.status == "complete"
                && (!value.process_complete || !value.remote_cleaned || !value.receipt_written)
        {
            return Err("DESKTOP_RECOVERY_INVALID");
        }
        Ok(value)
    }
}

pub async fn recover<H: client::Handler>(
    client: &client::Handle<H>,
    router: &Arc<forward::Router>,
    request: Recovery<'_>,
    cancel: &CancellationToken,
) -> RecoveryReport {
    let mut report = RecoveryReport {
        session: request.session,
        origin_session: None,
        queried: false,
        pending: true,
        boot_identity_known: false,
        boot_changed: false,
        process_complete: false,
        pipes_eof: false,
        remote_cleaned: false,
        receipt_written: false,
        channel_complete: false,
        router_drained: false,
        error: None,
    };
    let result = async {
        if request.profile.backend() != Backend::LinuxSsh || request.uid == 0 {
            return Err("DESKTOP_BINDING_INVALID");
        }
        if !request.previous_connection_closed || !router.belongs_to(request.session) || router.report().active != 0 {
            return Err("DESKTOP_PREVIOUS_CONNECTION_PENDING");
        }
        report.router_drained = true;
        if cancel.is_cancelled() { return Err("CANCELLED"); }
        let Some((mut journal, binding)) = Journal::matching(&request)? else {
            report.pending = false; return Ok(());
        };
        report.origin_session = Some(binding.session());
        let expected_boot = journal.boot_id().unwrap_or("-").to_owned();
        report.boot_identity_known = expected_boot != "-";
        let command = owner::command(&binding, Some(&expected_boot))?;
        let mut channel = tokio::select! {biased;
            _=cancel.cancelled()=>return Err("CANCELLED"),
            channel=timeout(Duration::from_secs(5), client.channel_open_session())=>channel,
        }.map_err(|_| "DESKTOP_RECOVERY_TIMEOUT")?.map_err(|_| "SSH_IO_FAILED")?;
        let exchange = async {
            report.queried = true;
            channel.exec(true, command).await.map_err(|_| "SSH_IO_FAILED")?;
            let (mut raw, mut stderr, mut eof, mut status, mut signal) =
                (Vec::new(), 0usize, false, None, false);
            loop {
                match channel.wait().await {
                    Some(ChannelMsg::Data { data }) => {
                        if eof || raw.len() + data.len() > 16384 { return Err("DESKTOP_RECOVERY_INVALID"); }
                        raw.extend(data);
                    }
                    Some(ChannelMsg::ExtendedData { data, ext: 1 }) => {
                        stderr = stderr.saturating_add(data.len());
                        if eof || stderr > 65536 { return Err("DESKTOP_RECOVERY_INVALID"); }
                    }
                    Some(ChannelMsg::Eof) => {
                        if eof { return Err("DESKTOP_RECOVERY_INVALID"); }
                        eof = true;
                    }
                    Some(ChannelMsg::ExitStatus { .. }) if status.is_some() => return Err("DESKTOP_RECOVERY_INVALID"),
                    Some(ChannelMsg::ExitStatus { exit_status }) => status = Some(exit_status),
                    Some(ChannelMsg::ExitSignal { .. }) => signal = true,
                    None | Some(ChannelMsg::Close) => break,
                    Some(ChannelMsg::Failure | ChannelMsg::ExtendedData { .. }) => return Err("DESKTOP_RECOVERY_INVALID"),
                    _ => (),
                }
            }
            if !eof || signal || status.is_none() { return Err("DESKTOP_RECOVERY_CHANNEL"); }
            let receipt = Receipt::parse(&raw, &binding, &expected_boot, status.unwrap())?;
            report.channel_complete = true;
            Ok(receipt)
        };
        let result = tokio::select! {biased;
            _=cancel.cancelled()=>Err("CANCELLED"),
            result=timeout(Duration::from_secs(12), exchange)=>result.map_err(|_| "DESKTOP_RECOVERY_TIMEOUT").and_then(|r|r),
        };
        let _ = timeout(Duration::from_secs(1), channel.eof()).await;
        let _ = timeout(Duration::from_secs(1), channel.close()).await;
        let receipt = result?;
        if let Some(boot) = receipt.origin_boot_id.as_deref() {
            journal.recovered_boot(boot)?;
            report.boot_identity_known = true;
        }
        report.boot_changed = receipt.process_evidence == "boot_changed";
        report.process_complete = receipt.process_complete;
        report.pipes_eof = receipt.pipes_eof;
        report.remote_cleaned = receipt.remote_cleaned;
        report.receipt_written = receipt.receipt_written;
        if receipt.status != "complete" { return Err("DESKTOP_CLEANUP_PENDING"); }
        if cancel.is_cancelled() { return Err("CANCELLED"); }
        journal.finish(Cleanup {
            process_complete: receipt.process_complete, pipes_eof: receipt.pipes_eof,
            remote_cleaned: receipt.remote_cleaned, receipt_written: receipt.receipt_written,
            channel_complete: report.channel_complete, forward_retired: request.previous_connection_closed,
            router_drained: report.router_drained, boot_changed: report.boot_changed,
        })?;
        journal.clear()?; report.pending = false;
        Ok(())
    }.await;
    report.error = result.err();
    report
}

#[cfg(test)]
mod tests;
