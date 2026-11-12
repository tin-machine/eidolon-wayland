//! Product-version v3 SSH coordinator. It never reuses the v2 bootstrap.
//! One authenticated connection owns Inspect, one optional Start and Stop.
//! Remote Complete is not local/SDK retirement; no journal is cleared here.
use super::{
    Binding, ExpectedEvidence, Phase, Selection,
    journal::{Cleanup, Journal, Stage},
    owner::{self, OwnerTurn},
};
use crate::forward;
use eidolon_model::ids::{CommandSeq, SessionId};
use russh::client;
use serde::Serialize;
use std::{
    os::unix::fs::{FileTypeExt, MetadataExt},
    path::{Path, PathBuf},
    sync::Arc,
};
use tokio::{
    sync::mpsc,
    time::{Duration, timeout},
};
use tokio_util::sync::CancellationToken;

pub(crate) struct Inspection<'a> {
    pub binding: &'a Binding,
    pub evidence: &'a ExpectedEvidence,
    pub files: &'a Path,
}

/// A move-only command. The Controller must reserve a one-shot gate before
/// preparing Waypipe/listener resources and before queuing this message.
pub(crate) struct Launch {
    pub request_id: CommandSeq,
    pub local_socket: PathBuf,
    pub selection: Selection,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub session: Option<SessionId>,
    pub nonce: String,
    pub request_id: Option<CommandSeq>,
    pub state: &'static str,
    pub finished: bool,
    pub catalog_verified: bool,
    pub start_attempted: bool,
    pub started: bool,
    pub forward_requested: bool,
    pub forward_registered: bool,
    pub forward_retired: bool,
    pub router_drained: bool,
    pub channel_complete: bool,
    /// The journal must remain pending after remote Complete until a separate
    /// local/SDK finalizer has consumed matching retirement evidence.
    pub journal_pending: bool,
    pub remote_complete: bool,
    pub cancelled: bool,
    pub error: Option<&'static str>,
    pub remote: owner::RemoteReport,
    pub forwarding: forward::Report,
}

/// Handoff has no Owner/SSH handle/Router/forward Lease. Its Journal still
/// owns the durable remote proof; only the trusted retirement actor may clear.
pub struct RunOutcome {
    pub report: Report,
    pub deferred: Option<Journal>,
}

pub(crate) async fn serve<H: client::Handler>(
    client: &client::Handle<H>,
    router: &Arc<forward::Router>,
    request: Inspection<'_>,
    mut requests: mpsc::Receiver<Launch>,
    cancel: &CancellationToken,
    publish: impl Fn(&Report),
    catalog: impl Fn(&[Selection]),
) -> RunOutcome {
    let mut report = Report {
        session: Some(request.binding.session()),
        nonce: request.binding.nonce(),
        state: "preparing",
        ..Default::default()
    };
    let mut journal: Option<Journal> = None;
    let mut owner: Option<owner::Owner> = None;
    let mut lease = None;
    let mut remote_path: Option<String> = None;
    let result = async {
        if !router.belongs_to(request.binding.session()) || router.report().active != 0 {
            return Err("DESKTOP_ROUTER_SCOPE");
        }
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        owner = Some(
            owner::Owner::open(
                client,
                request.binding,
                request.evidence,
                cancel.clone(),
                || {
                    journal = Some(Journal::reserve(request.files, request.binding)?);
                    report.journal_pending = true;
                    publish(&report);
                    Ok(())
                },
            )
            .await?,
        );
        let active = owner.as_mut().ok_or("DESKTOP_V3_OWNER_INVALID")?;
        while active.phase() == Phase::AwaitingPrepared {
            active.next().await?;
            report.remote = active.report();
            publish(&report);
        }
        journal
            .as_mut()
            .ok_or("DESKTOP_V3_JOURNAL_INVALID")?
            .prepared(active.boot_id().ok_or("DESKTOP_V3_PROTOCOL_INVALID")?)?;
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        active.inspect().await?;
        while active.phase() == Phase::Prepared {
            active.next().await?;
            report.remote = active.report();
            publish(&report);
        }
        if active.phase() != Phase::Catalog || active.report().terminal_event {
            return Err("DESKTOP_CATALOG_UNAVAILABLE");
        }
        // The fixed B1 catalog has exactly two candidates, not arbitrary exec.
        let selections = [
            active.selection("gtk3-desktop")?,
            active.selection("mousepad-editor-v1")?,
        ];
        report.catalog_verified = true;
        report.state = "catalog";
        publish(&report);
        catalog(&selections);
        let launch = loop {
            if cancel.is_cancelled() {
                return Err("CANCELLED");
            }
            if active.closed() || active.phase() != Phase::Catalog {
                return Err("DESKTOP_CATALOG_UNAVAILABLE");
            }
            match active.next_launch(&mut requests).await? {
                OwnerTurn::Launch(command) => break command,
                OwnerTurn::Events(_) => {
                    report.remote = active.report();
                    if report.remote.terminal_event || report.remote.channel_closed {
                        return Err("DESKTOP_CATALOG_UNAVAILABLE");
                    }
                    publish(&report);
                }
                OwnerTurn::Closed => return Err("DESKTOP_HELPER_LOST"),
                OwnerTurn::MailboxClosed => return Err("CANCELLED"),
            }
        };
        report.request_id = Some(launch.request_id);
        if !selections.contains(&launch.selection) {
            return Err("DESKTOP_CATALOG_STALE");
        }
        launch
            .selection
            .matches(request.binding.context(), request.binding.pins())
            .map_err(|e| e.0)?;
        // A command that takes a long time to ACK must not shift the lease
        // clock or turn an old catalog into fresh authority.
        active.renew_catalog().await?;
        if !launch.local_socket.is_absolute() || launch.local_socket.as_os_str().len() > 103 {
            return Err("DESKTOP_LOCAL_SOCKET_INVALID");
        }
        let info = std::fs::symlink_metadata(&launch.local_socket)
            .map_err(|_| "DESKTOP_LOCAL_SOCKET_INVALID")?;
        if !info.file_type().is_socket() || info.uid() != unsafe { libc::getuid() } {
            return Err("DESKTOP_LOCAL_SOCKET_INVALID");
        }
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        let path = active
            .remote_socket()
            .ok_or("DESKTOP_V3_PROTOCOL_INVALID")?
            .to_owned();
        lease = Some(router.arm(request.binding.session(), path.clone(), launch.local_socket)?);
        // From this point the forward request might have reached the host.
        journal.as_mut().ok_or("DESKTOP_V3_JOURNAL_INVALID")?
            .advance(Stage::ForwardRequested)?;
        remote_path = Some(path.clone());
        report.forward_requested = true;
        timeout(Duration::from_secs(5), client.streamlocal_forward(path))
            .await
            .map_err(|_| "DESKTOP_FORWARD_TIMEOUT")?
            .map_err(|_| "DESKTOP_FORWARD_REJECTED")?;
        report.forward_registered = true;
        report.state = "forwarded";
        publish(&report);
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        active.renew_catalog().await?;
        // Persist before any possible enqueue; never retry Start on ACK loss.
        journal.as_mut().ok_or("DESKTOP_V3_JOURNAL_INVALID")?
            .advance(Stage::StartRequested)?;
        report.start_attempted = true;
        active.start(&launch.selection).await?;
        while !active.closed() {
            active.next().await?;
            report.remote = active.report();
            if report.remote.started && !report.started {
                journal.as_mut().ok_or("DESKTOP_V3_JOURNAL_INVALID")?
                    .advance(Stage::Started)?;
                report.started = true;
                report.state = "running";
            }
            // Started followed by terminal in the same SSH chunk must never
            // publish a live or SDK-ready window in the final snapshot.
            if report.remote.terminal_event || report.remote.channel_closed {
                report.state = "stopping";
            }
            publish(&report);
        }
        if !active.terminal_remote_clean() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        Ok::<(), &'static str>(())
    }
    .await;

    report.error = result.err();
    report.state = "stopping";
    publish(&report);

    if let Some(active) = owner.as_mut() {
        if !active.closed() {
            // No external timeout around Owner::next(): it may be in the
            // middle of an enqueue. Owner owns the absolute Stop budget.
            let _ = active.stop().await;
            while !active.closed() && active.phase() != Phase::Failed {
                if active.next().await.is_err() {
                    break;
                }
            }
        }
        active.close().await;
        report.remote = active.report();
        report.channel_complete = active.channel_complete();
    }
    if let Some(path) = remote_path {
        report.forward_retired = matches!(
            timeout(Duration::from_secs(3), client.cancel_streamlocal_forward(path)).await,
            Ok(Ok(()))
        );
    } else {
        report.forward_retired = true;
    }
    let joined = if let Some(lease) = lease {
        timeout(Duration::from_secs(3), lease.close()).await.is_ok()
    } else {
        true
    };
    report.forwarding = router.report();
    report.router_drained = joined && report.forwarding.active == 0;
    report.cancelled = cancel.is_cancelled();

    let remote_verified = owner
        .as_ref()
        .is_some_and(|active| active.terminal_remote_clean());
    let mut deferred = None;
    if let Some(mut current) = journal.take() {
        let proof = Cleanup {
            process_complete: remote_verified && report.remote.process_complete,
            pipes_eof: remote_verified && report.remote.pipes_eof,
            remote_cleaned: remote_verified && report.remote.remote_cleaned,
            receipt_written: remote_verified && report.remote.receipt_written,
            channel_complete: report.channel_complete,
            forward_retired: report.forward_retired,
            router_drained: report.router_drained,
            boot_changed: false,
        };
        match current.finish(proof).and_then(|()| current.durable_complete()) {
            Ok(Some(_)) => {
                report.remote_complete = true;
                deferred = Some(current);
            }
            Ok(None) => {
                report.error.get_or_insert("DESKTOP_CLEANUP_PENDING");
            }
            Err(error) => {
                report.error.get_or_insert(error);
            }
        }
    }
    // A verified remote completion is not sufficient for local/SDK release.
    // Both a durable Complete and an Unknown journal remain pending here.
    if report.journal_pending && !report.remote_complete {
        report.error.get_or_insert("DESKTOP_CLEANUP_PENDING");
    }
    report.state = if report.remote_complete {
        "remote_complete"
    } else if report.journal_pending {
        "cleanup_pending"
    } else if report.error.is_some() {
        "failed"
    } else {
        "stopped"
    };
    report.finished = true;
    publish(&report);
    RunOutcome { report, deferred }
}
