//! One inspected helper and one explicit launch on the authenticated connection.
//! The idle launcher renews the lease without starting an application. A queued
//! Start is consumed by that same actor, never replayed after failure/reconnect.
use super::{
    Binding, Phase, RemoteReport, Selection,
    journal::{Cleanup, Journal, Stage},
    owner,
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

/// The caller owns the authenticated handle and its Router for this binding's
/// session, and has verified the remote UID on that same connection. A profile
/// save or a deserialized Selection is not permission to call this operation.
pub struct Request<'a> {
    pub binding: &'a Binding,
    pub files: &'a Path,
    pub local_socket: &'a Path,
}

pub(crate) struct Inspection<'a> {
    pub binding: &'a Binding,
    pub files: &'a Path,
}

pub(crate) struct Launch {
    pub request_id: Option<CommandSeq>,
    pub local_socket: PathBuf,
    pub selection: Selection,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub session: Option<SessionId>,
    pub nonce: String,
    pub request_id: Option<CommandSeq>,
    pub state: &'static str,
    /// Bounded teardown finished. Unknown cleanup still keeps journal_pending.
    pub finished: bool,
    pub tools_verified: bool,
    pub start_requested: bool,
    pub started: bool,
    pub forward_requested: bool,
    pub forward_registered: bool,
    pub forward_cancelled: bool,
    pub router_drained: bool,
    pub channel_complete: bool,
    pub journal_pending: bool,
    pub cancelled: bool,
    pub error: Option<&'static str>,
    pub remote: RemoteReport,
    pub forwarding: forward::Report,
}

/// Runs until natural exit, explicit cancellation/Stop, or a bounded failure.
/// There is deliberately no maximum GUI lifetime and no automatic Start replay.
pub async fn run<H: client::Handler>(
    client: &client::Handle<H>,
    router: &Arc<forward::Router>,
    request: Request<'_>,
    cancel: &CancellationToken,
    publish: impl Fn(&Report),
) -> Report {
    let (sender, requests) = mpsc::channel(1);
    serve(
        client,
        router,
        Inspection {
            binding: request.binding,
            files: request.files,
        },
        requests,
        cancel,
        publish,
        |selection| {
            // Compatibility transport API: its caller already explicitly
            // requested this one launch. Normal UI uses serve() with its mailbox.
            let _ = sender.try_send(Launch {
                request_id: None,
                local_socket: request.local_socket.into(),
                selection: selection.clone(),
            });
        },
    )
    .await
}

pub(crate) async fn serve<H: client::Handler>(
    client: &client::Handle<H>,
    router: &Arc<forward::Router>,
    request: Inspection<'_>,
    mut requests: mpsc::Receiver<Launch>,
    cancel: &CancellationToken,
    publish: impl Fn(&Report),
    catalog: impl Fn(&Selection),
) -> Report {
    let mut report = Report {
        session: Some(request.binding.session()),
        nonce: request.binding.nonce().into(),
        state: "preparing",
        ..Default::default()
    };
    let mut journal = None;
    let mut owner = None;
    let mut lease = None;
    let mut remote_path = None;
    let result = async {
        if !router.belongs_to(request.binding.session()) || router.report().active != 0 {
            return Err("DESKTOP_ROUTER_SCOPE");
        }
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        owner = Some(
            owner::Owner::open(client, request.binding, cancel.clone(), || {
                report.journal_pending = true;
                journal = Some(Journal::reserve(request.files, request.binding)?);
                publish(&report);
                Ok(())
            })
            .await?,
        );
        let active = owner.as_mut().unwrap();
        while active.phase() == Phase::AwaitingPrepared {
            active.next().await?;
            report.remote = active.report();
            publish(&report);
        }
        // No inspector/application child is requested before the boot identity
        // is durable. If Prepared is lost the reserved record remains unknown.
        journal
            .as_mut()
            .unwrap()
            .prepared(active.boot_id().ok_or("DESKTOP_PROTOCOL_INVALID")?)?;
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        active.inspect().await?;
        while active.phase() == Phase::Prepared {
            active.next().await?;
            report.remote = active.report();
            publish(&report);
        }
        let selection = active.selection().ok_or("DESKTOP_CATALOG_UNAVAILABLE")?;
        report.tools_verified = true;
        report.state = "catalog";
        publish(&report);
        catalog(&selection);
        // Owner reads commands only at cancellation-safe input boundaries; an
        // incoming launch must not drop a half-enqueued heartbeat request.
        let launch = loop {
            if cancel.is_cancelled() {
                return Err("CANCELLED");
            }
            if active.closed() || active.phase() != Phase::Catalog {
                return Err("DESKTOP_CATALOG_UNAVAILABLE");
            }
            if let Some(launch) = active.next_launch(&mut requests).await? {
                break launch;
            }
            report.remote = active.report();
            if report.remote.terminal_event || report.remote.channel_closed {
                report.state = "stopping";
            }
            publish(&report);
        };
        report.request_id = launch.request_id;
        if !launch.selection.valid() || launch.selection != selection {
            return Err("DESKTOP_CATALOG_STALE");
        }
        // A queued heartbeat ACK may still be outstanding. Preserve its original
        // deadline, then issue exactly one Start after the actor becomes idle.
        while active.selection().is_none() {
            if cancel.is_cancelled() || active.phase() != Phase::Catalog {
                return Err("DESKTOP_CATALOG_UNAVAILABLE");
            }
            active.next().await?;
            report.remote = active.report();
            if report.remote.terminal_event || report.remote.channel_closed {
                report.state = "stopping";
            }
            publish(&report);
        }
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
            .ok_or("DESKTOP_PROTOCOL_INVALID")?
            .to_owned();
        lease = Some(router.arm(request.binding.session(), path.clone(), launch.local_socket)?);
        journal.as_mut().unwrap().advance(Stage::ForwardRequested)?;
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
        // Persist before enqueue: timeout cannot be interpreted as not started.
        journal.as_mut().unwrap().advance(Stage::StartRequested)?;
        report.start_requested = true;
        active.start(&selection).await?;
        while !active.closed() {
            active.next().await?;
            report.remote = active.report();
            if report.remote.started && !report.started {
                journal.as_mut().unwrap().advance(Stage::Started)?;
                report.started = true;
                report.state = "running";
            }
            // Started is irreversible evidence, not current liveness. A natural
            // terminal event can share its SSH packet with Started and must win
            // before native publishes a surface or accepts more GUI input.
            if report.remote.terminal_event || report.remote.channel_closed {
                report.state = "stopping";
            }
            publish(&report);
        }
        if !active.terminal_remote_clean() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        Ok(())
    }
    .await;
    report.error = result.err();
    report.state = "stopping";
    publish(&report);
    if let Some(active) = owner.as_mut() {
        if !active.closed() {
            // Bound teardown independently of the app lifetime. The actor waits
            // for its outstanding ACK before Stop and never reissues Start.
            let _ = timeout(Duration::from_secs(14), async {
                active.stop().await?;
                while !active.closed() {
                    active.next().await?;
                }
                Ok::<(), &'static str>(())
            })
            .await;
        }
        active.close().await;
        report.remote = active.report();
        report.channel_complete = active.channel_complete();
    }
    if let Some(path) = remote_path {
        report.forward_cancelled = matches!(
            timeout(
                Duration::from_secs(3),
                client.cancel_streamlocal_forward(path)
            )
            .await,
            Ok(Ok(()))
        );
    }
    let joined = if let Some(lease) = lease {
        timeout(Duration::from_secs(3), lease.close()).await.is_ok()
    } else {
        true
    };
    report.forwarding = router.report();
    report.router_drained = joined && report.forwarding.active == 0;
    report.cancelled = cancel.is_cancelled();
    if let Some(journal) = journal.as_mut() {
        let remote_verified = owner
            .as_ref()
            .is_some_and(|active| active.terminal_remote_clean());
        let cleanup = Cleanup {
            process_complete: remote_verified && report.remote.process_complete,
            pipes_eof: remote_verified && report.remote.pipes_eof,
            remote_cleaned: remote_verified && report.remote.remote_cleaned,
            receipt_written: remote_verified && report.remote.receipt_written,
            channel_complete: report.channel_complete,
            forward_retired: !report.forward_requested || report.forward_cancelled,
            router_drained: report.router_drained,
            boot_changed: false,
        };
        let outcome = journal.finish(cleanup).and_then(|()| journal.clear());
        match outcome {
            Ok(()) => report.journal_pending = false,
            Err(error) => {
                report.error.get_or_insert(error);
            }
        }
    }
    if report.journal_pending {
        report.error.get_or_insert("DESKTOP_CLEANUP_PENDING");
    }
    if report.cancelled && !report.journal_pending && report.error == Some("CANCELLED") {
        report.error = None;
    }
    report.state = if report.journal_pending {
        "cleanup_pending"
    } else if report.error.is_some() {
        "failed"
    } else {
        "stopped"
    };
    report.finished = true;
    publish(&report);
    report
}

#[cfg(test)]
mod tests;
