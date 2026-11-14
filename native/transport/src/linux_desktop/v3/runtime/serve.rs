//! One authenticated v3 helper, one Inspect, at most one Start. It owns the
//! remote half of the lifecycle; local/SDK retirement happens on a separate
//! thread after this SSH actor and its I/O generation are gone.
use super::{Launch, RunCapture};
use crate::{
    forward,
    linux_desktop::v3::{
        self, Phase, Selection,
        journal::{Cleanup, Journal, Stage},
        owner::{self, OwnerTurn},
        reclamation::{DeferredReclamation, ReclamationHandoff},
    },
};
use eidolon_model::{
    ids::{CommandSeq, SessionId},
    linux_desktop_v3::report::{
        ControllerFault, ControllerFaults, RemoteCleanup, TransportPhase, TransportState,
    },
};
use russh::client;
use serde::Serialize;
use std::{
    os::unix::fs::{FileTypeExt, MetadataExt},
    path::Path,
    sync::Arc,
};
use tokio::{
    sync::mpsc,
    time::{Duration, timeout},
};
use tokio_util::sync::CancellationToken;

/// This is transport-only. It never asserts B1 Startup::Ready or invents an
/// SDK Entity, rendered frame, or a local-retirement success report.
#[derive(Clone, Serialize)]
pub struct TransportSnapshot {
    pub protocol_revision: String,
    pub session: SessionId,
    pub inspect_request: CommandSeq,
    pub launch_request: Option<CommandSeq>,
    pub transport: TransportState,
    pub journal_stage: Option<Stage>,
    pub start_attempted: bool,
    pub started_observed: bool,
    pub terminal_observed: bool,
    pub terminal_reason: Option<eidolon_model::linux_desktop_v3::StopReason>,
    pub actor_finished: bool,
    pub io_retired: bool,
    pub remote_cleanup: RemoteCleanup,
    pub cleanup_pending: bool,
    pub faults: ControllerFaults,
    pub error: Option<&'static str>,
}
impl std::fmt::Debug for TransportSnapshot {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("V3TransportSnapshot(<private>)")
    }
}
impl TransportSnapshot {
    fn preparing(capture: &RunCapture) -> Self {
        Self {
            protocol_revision: v3::REVISION.into(),
            session: capture.binding().session(),
            inspect_request: capture.inspect_request,
            launch_request: None,
            transport: TransportState {
                phase: TransportPhase::Preparing,
                launch_accepted: false,
                journal_pending: false,
            },
            journal_stage: None,
            start_attempted: false,
            started_observed: false,
            terminal_observed: false,
            terminal_reason: None,
            actor_finished: false,
            io_retired: false,
            remote_cleanup: RemoteCleanup {
                process_complete: false,
                pipes_eof: false,
                boot_changed: false,
                remote_cleaned: false,
                receipt_written: false,
                ssh_channel_complete: false,
                forward_retired: false,
                router_drained: false,
            },
            cleanup_pending: false,
            faults: ControllerFaults::default(),
            error: None,
        }
    }
    fn fault(&mut self, error: &'static str) {
        self.error.get_or_insert(error);
        self.faults.first.get_or_insert(match error {
            "CANCELLED" => ControllerFault::CommandTimeout,
            "DESKTOP_FORWARD_REJECTED" | "DESKTOP_FORWARD_TIMEOUT" => {
                ControllerFault::ForwardRequest
            }
            "DESKTOP_LOCAL_SOCKET_INVALID" => ControllerFault::LocalAdmissionUnavailable,
            "DESKTOP_V3_JOURNAL_CREATE_FAILED" | "DESKTOP_V3_JOURNAL_BUSY" => {
                ControllerFault::JournalReserve
            }
            "DESKTOP_V3_JOURNAL_PUBLISH_FAILED" | "DESKTOP_V3_JOURNAL_SYNC_FAILED" => {
                ControllerFault::JournalUpdate
            }
            "DESKTOP_V3_EVIDENCE_STALE" | "DESKTOP_CATALOG_STALE" => ControllerFault::ScopeMismatch,
            "DESKTOP_HELPER_TIMEOUT" | "SSH_IO_TIMEOUT" => ControllerFault::CommandTimeout,
            _ => ControllerFault::SshIo,
        });
    }
}

pub struct RunOutcome {
    pub report: TransportSnapshot,
    pub handoff: Option<ReclamationHandoff>,
}

/// The caller captures credentials, profile/storage/UID, pins and local source
/// inventory before creating this request. These cannot be replaced by JSON.
pub struct Inspection<'a> {
    pub capture: Arc<RunCapture>,
    pub files: &'a Path,
}

pub async fn serve<H: client::Handler>(
    client: &client::Handle<H>,
    router: &Arc<forward::Router>,
    request: Inspection<'_>,
    mut launches: mpsc::Receiver<Launch>,
    cancel: &CancellationToken,
    publish: impl Fn(&TransportSnapshot),
    catalog: impl Fn(&[Selection; 2]),
) -> RunOutcome {
    let binding = request.capture.binding();
    let generation = request.capture.generation;
    let mut report = TransportSnapshot::preparing(&request.capture);
    let mut journal: Option<Journal> = None;
    let mut owner: Option<owner::Owner> = None;
    let mut lease: Option<forward::Lease> = None;
    let mut remote_path: Option<String> = None;
    let mut forward_requested = false;
    let mut handoff = None;

    let result: Result<(), &'static str> = async {
        if !router.belongs_to(binding.session()) || router.report().active != 0 {
            return Err("DESKTOP_ROUTER_SCOPE");
        }
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        // A partial journal reserve, including an fsync failure, remains
        // potentially pending. Mark uncertainty BEFORE trying the operation.
        owner = Some(
            owner::Owner::open(
                client,
                binding,
                request.capture.evidence(),
                cancel.clone(),
                || {
                    report.transport.journal_pending = true;
                    journal = Some(Journal::reserve(request.files, binding)?);
                    report.journal_stage = Some(Stage::Reserved);
                    publish(&report);
                    Ok(())
                },
            )
            .await?,
        );
        let active = owner.as_mut().expect("opened owner");
        while active.phase() == Phase::AwaitingPrepared {
            active.next().await?;
            if active.report().terminal_event {
                return Err("DESKTOP_HELPER_LOST");
            }
        }
        if active.phase() != Phase::Prepared {
            return Err("DESKTOP_CATALOG_UNAVAILABLE");
        }
        journal
            .as_mut()
            .unwrap()
            .prepared(active.boot_id().ok_or("DESKTOP_V3_PROTOCOL_INVALID")?)?;
        report.journal_stage = Some(Stage::Prepared);
        active.inspect().await?;
        while active.phase() == Phase::Prepared {
            active.next().await?;
            if active.report().terminal_event {
                return Err("DESKTOP_CATALOG_UNAVAILABLE");
            }
        }
        if active.phase() != Phase::Catalog {
            return Err("DESKTOP_CATALOG_UNAVAILABLE");
        }
        // The catalog is fully checked against B1/E1 source evidence by Owner.
        // Both fixed entries share this one live catalog and one-shot gate.
        // A two-entry launcher does not permit a second concurrent GUI lease.
        let selections = [
            active.selection("gtk3-desktop")?,
            active.selection("mousepad-editor-v1")?,
        ];
        report.transport.phase = TransportPhase::Catalog;
        publish(&report);
        catalog(&selections);

        let mut launch = loop {
            if active.phase() != Phase::Catalog || active.report().terminal_event {
                return Err("DESKTOP_CATALOG_UNAVAILABLE");
            }
            match active.next_launch(&mut launches).await? {
                OwnerTurn::Launch(value) => break value,
                OwnerTurn::Events(_events) => {
                    report.terminal_observed |= active.report().terminal_event;
                    report.terminal_reason = active.report().stop_reason;
                    publish(&report);
                }
                OwnerTurn::MailboxClosed => return Err("DESKTOP_COMMANDS_CLOSED"),
                OwnerTurn::Closed => return Err("DESKTOP_HELPER_LOST"),
            }
        };
        let selection = launch.permit.selection().clone();
        if !Arc::ptr_eq(&launch.permit.capture, &request.capture)
            || !selections.contains(&selection)
            || launch.permit.generation() != generation
            || launch
                .permit
                .origin()
                .matches(binding.context(), &selection, binding.pins())
                .is_err()
        {
            return Err("DESKTOP_CATALOG_STALE");
        }
        report.launch_request = Some(launch.permit.request());
        report.transport.launch_accepted = true;
        publish(&report);

        // In-flight heartbeat ACKs must finish before any forward. Each
        // barrier can receive terminal and heartbeat in the same SSH packet.
        active.renew_catalog().await?;
        if !active.lease_margin(Duration::from_secs(8)) {
            return Err("DESKTOP_HELPER_TIMEOUT");
        }
        let socket = &launch.local_socket;
        if !socket.is_absolute() || socket.as_os_str().len() > 103 {
            return Err("DESKTOP_LOCAL_SOCKET_INVALID");
        }
        let meta = std::fs::symlink_metadata(socket).map_err(|_| "DESKTOP_LOCAL_SOCKET_INVALID")?;
        if !meta.file_type().is_socket() || meta.uid() != unsafe { libc::getuid() } {
            return Err("DESKTOP_LOCAL_SOCKET_INVALID");
        }
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        let path = active
            .remote_socket()
            .ok_or("DESKTOP_V3_PROTOCOL_INVALID")?
            .to_owned();
        lease = Some(router.arm(binding.session(), path.clone(), socket.clone())?);
        remote_path = Some(path.clone());
        journal.as_mut().unwrap().advance(Stage::ForwardRequested)?;
        report.journal_stage = Some(Stage::ForwardRequested);
        forward_requested = true;
        if !active.lease_margin(Duration::from_secs(8)) {
            return Err("DESKTOP_HELPER_TIMEOUT");
        }
        timeout(Duration::from_secs(5), client.streamlocal_forward(path))
            .await
            .map_err(|_| "DESKTOP_FORWARD_TIMEOUT")?
            .map_err(|_| "DESKTOP_FORWARD_REJECTED")?;
        report.transport.phase = TransportPhase::Forwarded;
        publish(&report);
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        active.renew_catalog().await?;
        if !active.lease_margin(Duration::from_secs(3)) {
            return Err("DESKTOP_HELPER_TIMEOUT");
        }
        // Crash-consistency ordering: durable StartRequested BEFORE the one
        // possible Start enqueue. Ambiguous Start is never retransmitted.
        journal.as_mut().unwrap().advance(Stage::StartRequested)?;
        report.journal_stage = Some(Stage::StartRequested);
        report.start_attempted = true;
        launch.start_attempted()?;
        active.start(&selection).await?;
        loop {
            if active.closed() {
                break;
            }
            let _events = active.next().await?;
            let remote = active.report();
            if remote.started && !report.started_observed {
                journal.as_mut().unwrap().advance(Stage::Started)?;
                report.journal_stage = Some(Stage::Started);
                report.started_observed = true;
            }
            report.terminal_observed |= remote.terminal_event;
            report.terminal_reason = remote.stop_reason;
            report.transport.phase = if report.terminal_observed {
                TransportPhase::Stopping
            } else if report.started_observed {
                TransportPhase::Started
            } else {
                TransportPhase::Forwarded
            };
            publish(&report);
        }
        if !active.terminal_remote_clean() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        Ok(())
    }
    .await;

    if let Err(error) = result {
        report.fault(error);
    }
    report.transport.phase = TransportPhase::Stopping;
    publish(&report);
    let mut remote_verified = false;
    let mut remote = None;
    if let Some(active) = owner.as_mut() {
        if !active.closed() {
            let _ = timeout(Duration::from_secs(33), async {
                active.stop().await?;
                while !active.closed() {
                    active.next().await?;
                }
                Ok::<(), &'static str>(())
            })
            .await;
        }
        active.close().await;
        remote_verified = active.terminal_remote_clean();
        remote = Some(active.report());
    }
    if let Some(active) = &remote {
        report.terminal_observed |= active.terminal_event;
        report.terminal_reason = active.stop_reason;
    }
    let channel_complete = owner
        .as_ref()
        .is_some_and(|active| active.channel_complete());
    let forward_retired = if let Some(path) = remote_path {
        matches!(
            timeout(
                Duration::from_secs(3),
                client.cancel_streamlocal_forward(path)
            )
            .await,
            Ok(Ok(()))
        )
    } else {
        !forward_requested
    };
    let joined = if let Some(lease) = lease {
        timeout(Duration::from_secs(3), lease.close()).await.is_ok()
    } else {
        true
    };
    let router_drained = joined && router.report().active == 0;
    let cleanup = Cleanup {
        process_complete: remote_verified && remote.as_ref().is_some_and(|r| r.process_complete),
        pipes_eof: remote_verified && remote.as_ref().is_some_and(|r| r.pipes_eof),
        remote_cleaned: remote_verified && remote.as_ref().is_some_and(|r| r.remote_cleaned),
        receipt_written: remote_verified && remote.as_ref().is_some_and(|r| r.receipt_written),
        channel_complete,
        forward_retired,
        router_drained,
        boot_changed: false,
    };
    report.remote_cleanup = RemoteCleanup {
        process_complete: cleanup.process_complete,
        pipes_eof: cleanup.pipes_eof,
        boot_changed: cleanup.boot_changed,
        remote_cleaned: cleanup.remote_cleaned,
        receipt_written: cleanup.receipt_written,
        ssh_channel_complete: cleanup.channel_complete,
        forward_retired: cleanup.forward_retired,
        router_drained: cleanup.router_drained,
    };
    if let Some(mut owned) = journal {
        match owned.finish(cleanup) {
            Ok(()) => match DeferredReclamation::from_complete(owned, binding, generation) {
                Ok(value) => handoff = Some(value),
                Err(error) => report.fault(error),
            },
            Err(error) => {
                report.fault(error);
                // A publish-time sync error may have installed Complete in
                // memory; do not claim it durable or clear in this branch.
            }
        }
    }
    report.cleanup_pending = report.transport.journal_pending;
    report.transport.phase = if handoff.is_some() {
        TransportPhase::Complete
    } else {
        TransportPhase::CleanupPending
    };
    report.actor_finished = true;
    // The SSH connection and Tokio runtime still belong to session::owner;
    // only its eventual destruction can grant the I/O generation retirement.
    report.io_retired = false;
    publish(&report);
    RunOutcome { report, handoff }
}
