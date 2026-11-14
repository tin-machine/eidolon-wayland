//! Pinned v3 recovery on a newly authenticated SSH generation. A completed
//! remote receipt is durable evidence for deferred cleanup, never permission to
//! unlink the journal while local windows/SDK Entities may still exist.
use super::{
    Pins, Receipt,
    io_fence::PreviousIoClosed,
    journal::{Cleanup, Journal, Stage},
    owner,
    reclamation::{DeferredReclamation, ReclamationHandoff},
    sources,
};
use crate::forward;
use eidolon_model::linux_desktop_v3::{Bootstrap, StorageRecord};
use eidolon_model::{
    connection_profile::{Backend, Profile},
    ids::SessionId,
};
use russh::{ChannelMsg, client};
use serde::Serialize;
use std::{path::Path, sync::Arc};
use tokio::time::{Duration, timeout};
use tokio_util::sync::CancellationToken;

pub struct Recovery<'a> {
    pub profile: &'a Profile,
    pub session: SessionId,
    pub uid: u32,
    pub files: &'a Path,
    pub pins: &'a Pins,
    pub storage: &'a StorageRecord,
    /// Historical field for journal matching fixtures. A boolean is NOT
    /// recovery authority; recover_remote requires PreviousIoClosed instead.
    pub previous_connection_closed: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct RecoveryReport {
    pub session: SessionId,
    pub origin_session: Option<SessionId>,
    pub queried: bool,
    /// Remains true until the trusted local/SDK finalizer clears the journal.
    pub pending: bool,
    pub remote_complete: bool,
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
impl RecoveryReport {
    fn initial(session: SessionId) -> Self {
        Self {
            session,
            origin_session: None,
            queried: false,
            pending: true,
            remote_complete: false,
            boot_identity_known: false,
            boot_changed: false,
            process_complete: false,
            pipes_eof: false,
            remote_cleaned: false,
            receipt_written: false,
            channel_complete: false,
            router_drained: false,
            error: None,
        }
    }
    fn apply_cleanup(&mut self, cleanup: &Cleanup) {
        self.remote_complete = true;
        self.process_complete = cleanup.process_complete;
        self.pipes_eof = cleanup.pipes_eof;
        self.boot_changed = cleanup.boot_changed;
        self.remote_cleaned = cleanup.remote_cleaned;
        self.receipt_written = cleanup.receipt_written;
        self.channel_complete = cleanup.channel_complete;
        self.router_drained = cleanup.router_drained;
    }
}

/// The complete journal and its authority are moved to core while the SSH
/// actor terminates. The outcome cannot be serialized into UI evidence.
pub struct RemoteRecoveryOutcome {
    pub report: RecoveryReport,
    pub handoff: Option<ReclamationHandoff>,
}

/// A legacy unfenced caller must not convert "connection closed: true" into
/// final journal removal. Existing v2 recovery remains in its own dispatch lane.
pub async fn recover<H: client::Handler>(
    _client: &client::Handle<H>,
    _router: &Arc<forward::Router>,
    request: Recovery<'_>,
    _cancel: &CancellationToken,
) -> RecoveryReport {
    let mut report = RecoveryReport::initial(request.session);
    report.error = Some("DESKTOP_PREVIOUS_CONNECTION_UNPROVEN");
    report
}

/// Only a live IoGenerationFence can mint `previous`, and only after it
/// acquired a pre-existing lock that predates the captured pending journal.
pub async fn recover_remote<H: client::Handler>(
    client: &client::Handle<H>,
    router: &Arc<forward::Router>,
    request: Recovery<'_>,
    previous: &PreviousIoClosed,
    generation: u64,
    cancel: &CancellationToken,
) -> RemoteRecoveryOutcome {
    let mut report = RecoveryReport::initial(request.session);
    let result: Result<ReclamationHandoff, &'static str> = async {
        if request.profile.backend() != Backend::LinuxSsh || request.uid == 0 {
            return Err("DESKTOP_BINDING_INVALID");
        }
        if !router.belongs_to(request.session) || router.report().active != 0 {
            return Err("DESKTOP_PREVIOUS_CONNECTION_PENDING");
        }
        super::journal::check_v2_barrier(request.files)?;
        sources::require_complete()?;
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        let Some((mut journal, binding)) = Journal::matching(&request)? else {
            // A missing file is NOT cleanup proof for a known previous owner.
            return Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED");
        };
        report.origin_session = Some(binding.session());
        if !previous.matches(request.session, &binding, journal.source_hashes()) {
            return Err("DESKTOP_PREVIOUS_CONNECTION_PENDING");
        }
        report.boot_identity_known = journal.boot_id().is_some();
        if journal.stage() == Stage::Complete {
            // A crash after durable Complete but before local retirement does
            // not execute C2 Recover a second time; no fabricated new channel.
            let cleanup = journal.ensure_complete_durable()?;
            report.apply_cleanup(&cleanup);
            return DeferredReclamation::from_complete(journal, &binding, generation);
        }
        let expected_boot = journal.boot_id().unwrap_or("-").to_owned();
        report.boot_identity_known = expected_boot != "-";
        let bootstrap = Bootstrap::recover(
            binding.context().clone(),
            binding.pins().clone(),
            &expected_boot,
        )
        .map_err(|e| e.0)?;
        let command = owner::command(&binding, &bootstrap)?;
        let mut channel = tokio::select! { biased;
            _ = cancel.cancelled() => return Err("CANCELLED"),
            result = timeout(Duration::from_secs(5), client.channel_open_session()) => result,
        }
        .map_err(|_| "DESKTOP_V3_RECOVERY_TIMEOUT")?
        .map_err(|_| "SSH_IO_FAILED")?;
        let exchange = async {
            report.queried = true;
            channel
                .exec(true, command)
                .await
                .map_err(|_| "SSH_IO_FAILED")?;
            let (mut raw, mut stderr, mut eof, mut status, mut signal) =
                (Vec::new(), 0usize, false, None, false);
            loop {
                match channel.wait().await {
                    Some(ChannelMsg::Data { data }) => {
                        if eof || raw.len() + data.len() > 16384 {
                            return Err("DESKTOP_V3_RECOVERY_INVALID");
                        }
                        raw.extend(data);
                    }
                    Some(ChannelMsg::ExtendedData { data, ext: 1 }) => {
                        stderr = stderr.saturating_add(data.len());
                        if eof || stderr > 65536 {
                            return Err("DESKTOP_V3_RECOVERY_INVALID");
                        }
                    }
                    Some(ChannelMsg::Eof) => {
                        if eof {
                            return Err("DESKTOP_V3_RECOVERY_INVALID");
                        }
                        eof = true;
                    }
                    Some(ChannelMsg::ExitStatus { .. }) if status.is_some() => {
                        return Err("DESKTOP_V3_RECOVERY_INVALID");
                    }
                    Some(ChannelMsg::ExitStatus { exit_status }) => status = Some(exit_status),
                    Some(ChannelMsg::ExitSignal { .. }) => signal = true,
                    None | Some(ChannelMsg::Close) => break,
                    Some(ChannelMsg::Failure | ChannelMsg::ExtendedData { .. }) => {
                        return Err("DESKTOP_V3_RECOVERY_INVALID");
                    }
                    _ => (),
                }
            }
            if !eof || signal || status.is_none() {
                return Err("DESKTOP_V3_RECOVERY_CHANNEL");
            }
            let receipt = Receipt::parse(
                &raw,
                &binding,
                journal.source_hashes(),
                &expected_boot,
                status.unwrap(),
            )?;
            report.channel_complete = true;
            Ok(receipt)
        };
        let reply = tokio::select! { biased;
            _ = cancel.cancelled() => Err("CANCELLED"),
            result = timeout(Duration::from_secs(12), exchange) =>
                result.map_err(|_| "DESKTOP_V3_RECOVERY_TIMEOUT").and_then(|r| r),
        };
        let _ = timeout(Duration::from_secs(1), channel.eof()).await;
        let _ = timeout(Duration::from_secs(1), channel.close()).await;
        let receipt = reply?;
        if let Some(boot) = receipt.origin_boot_id() {
            journal.recovered_boot(boot)?;
            report.boot_identity_known = true;
        }
        if !receipt.complete() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        let cleanup = Cleanup {
            process_complete: receipt.process_complete(),
            pipes_eof: receipt.pipes_eof(),
            remote_cleaned: receipt.remote_cleaned(),
            receipt_written: receipt.receipt_written(),
            channel_complete: report.channel_complete,
            forward_retired: true, // previous I/O generation retired by fence
            router_drained: router.report().active == 0,
            boot_changed: receipt.process_evidence() == "boot_changed",
        };
        journal.finish(cleanup)?;
        let durable = journal.ensure_complete_durable()?;
        report.apply_cleanup(&durable);
        DeferredReclamation::from_complete(journal, &binding, generation)
    }
    .await;
    let handoff = match result {
        Ok(handoff) => Some(handoff),
        Err(error) => {
            report.error = Some(error);
            None
        }
    };
    RemoteRecoveryOutcome { report, handoff }
}

#[cfg(test)]
mod tests;
