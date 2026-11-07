//! 認証済みSSHの同じRouter/journalから、既存Waypipe clientへ転送する。
use crate::{forward, forward_probe, journal, launcher};
use eidolon_model::{commands::AppMode, ids::SessionId};
use russh::client;
use serde::Serialize;
use std::{
    os::unix::fs::{FileTypeExt, MetadataExt},
    path::Path,
    sync::Arc,
};
use tokio::time::{Duration, timeout};
use tokio_util::sync::CancellationToken;

#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub state: &'static str,
    pub mode: String,
    pub nonce: String,
    pub uid: u32,
    pub tools: Option<launcher::Tools>,
    pub registered: bool,
    pub remote_not_started: bool,
    pub forward_cancelled: bool,
    pub remote_cleaned: bool,
    pub cancelled: bool,
    pub forwarding: forward::Report,
    pub remote: Option<launcher::Report>,
    pub pending_cleanup: Vec<journal::Pending>,
    pub elapsed_ms: u64,
    pub error: Option<&'static str>,
}
pub(crate) struct Request<'a> {
    pub session: SessionId,
    pub local_socket: &'a Path,
    pub nonce: &'a str,
    pub mode: AppMode,
    pub uid: u32,
    pub journal: &'a mut journal::Journal,
    pub binding: &'a str,
    pub observation: tokio::sync::watch::Receiver<bool>,
    pub terminal_requests: tokio::sync::mpsc::Receiver<(
        eidolon_model::ids::CommandSeq,
        Option<crate::catalog::Target>,
    )>,
    pub catalog_requests:
        tokio::sync::mpsc::Receiver<(eidolon_model::ids::CommandSeq, crate::catalog::Operation)>,
}

pub(crate) async fn run<H: client::Handler>(
    client: &client::Handle<H>,
    router: &Arc<forward::Router>,
    request: Request<'_>,
    cancel: &CancellationToken,
    publish: impl Fn(&Report),
    publish_catalog: impl Fn(
        eidolon_model::ids::CommandSeq,
        Result<crate::catalog::Reply, &'static str>,
    ),
) -> Report {
    let Request {
        session,
        local_socket,
        nonce,
        mode,
        uid,
        journal,
        binding,
        mut terminal_requests,
        mut catalog_requests,
        mut observation,
    } = request;
    let started = std::time::Instant::now();
    let mut report = Report {
        state: "preparing",
        mode: mode.as_str().into(),
        nonce: nonce.into(),
        uid,
        pending_cleanup: journal.pending(),
        ..Default::default()
    };
    publish(&report);
    let mut owner = None;
    let mut recorded = false;
    let mut lease = None;
    let remote_path = launcher::remote_path(nonce).unwrap_or_default();
    let result = async {
        if !forward_probe::valid_nonce(nonce)
            || !local_socket.is_absolute()
            || local_socket.as_os_str().len() > 103
        {
            return Err("INVALID_ARGUMENT");
        }
        let info = std::fs::symlink_metadata(local_socket).map_err(|_| "LOCAL_SOCKET_FAILED")?;
        if !info.file_type().is_socket() || info.uid() != unsafe { libc::getuid() } {
            return Err("LOCAL_SOCKET_FAILED");
        }
        owner = Some(
            launcher::Launcher::open_recorded(client, nonce, uid, cancel.clone(), || {
                journal.remember(journal::Entry {
                    nonce: nonce.into(),
                    session,
                    uid,
                    binding: binding.into(),
                    child: None,
                })?;
                recorded = true;
                report.pending_cleanup = journal.pending();
                publish(&report);
                Ok(())
            })
            .await?,
        );
        let remote = owner.as_mut().unwrap();
        while remote.report.phase != "prepared" {
            report.remote = Some(remote.next().await?);
            publish(&report);
            if remote.report.phase == "finished" {
                return Err("REMOTE_EXIT");
            }
        }
        report.tools = Some(remote.inspect_tools(mode.as_str()).await?);
        report.state = "tools_verified";
        report.remote = Some(remote.report.clone());
        publish(&report);
        // 版の照合・local listenの後だけforwardを登録し、ACKの前にremote appを起動しない。
        lease = Some(router.arm(session, remote_path.clone(), local_socket.into())?);
        timeout(
            Duration::from_secs(5),
            client.streamlocal_forward(remote_path.clone()),
        )
        .await
        .map_err(|_| "FORWARD_TIMEOUT")?
        .map_err(|_| "FORWARD_REJECTED")?;
        report.registered = true;
        report.state = "forwarded";
        publish(&report);
        remote.start_app(mode.as_str(), session).await?;
        let mut observation_open = matches!(mode, AppMode::Foot | AppMode::FootFixture);
        if observation_open {
            let enabled = *observation.borrow_and_update();
            remote.observe_workload(enabled).await?;
        }
        let mut requests_open = true;
        let mut catalog_open = true;
        while remote.report.phase != "finished" {
            tokio::select! {
                biased;
                value = remote.next() => { report.remote = Some(value?); }
                changed = observation.changed(), if observation_open => {
                    if changed.is_ok() {
                        let enabled = *observation.borrow_and_update();
                        remote.observe_workload(enabled).await?;
                    } else { observation_open = false; }
                }
                request = terminal_requests.recv(), if requests_open && remote.report.phase == "running" && remote.report.terminal_count > 0 && remote.report.terminal_pending.is_none() => {
                    if let Some((request,target)) = request { remote.launch_terminal(request,target).await?; report.remote=Some(remote.report.clone()); }
                    else { requests_open=false; }
                }
                request = catalog_requests.recv(), if catalog_open && remote.report.phase == "running" => {
                    if let Some((request, operation)) = request {
                        let result = remote.catalog(request, operation).await;
                        publish_catalog(request, result.clone());
                        result?;
                        report.remote = Some(remote.report.clone());
                    } else { catalog_open = false; }
                }
            }
            if let Some(child) = &remote.report.child {
                journal.child(nonce, child)?;
            }
            if remote.report.phase == "running" {
                report.state = "running";
            }
            report.pending_cleanup = journal.pending();
            publish(&report);
        }
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        let finished = remote.report.finished.as_ref().ok_or("PROTOCOL_ERROR")?;
        if remote.report.cleanup_pending
            || finished.reason != "Completed"
            || finished.exit_status != Some(0)
            || finished.exit_signal.is_some()
        {
            return Err("REMOTE_EXIT");
        }
        if mode == AppMode::Pattern && finished.pattern.is_none() {
            return Err("PATTERN_SUMMARY_INVALID");
        }
        Ok(())
    }
    .await;
    // remote停止/receiptを先に確定し、その後forwardとbridgeを閉じる。
    if let Some(remote) = owner.as_mut() {
        if remote.report.phase != "finished" {
            let _ = remote.request_stop().await;
            while remote.report.phase != "finished" {
                match remote.next().await {
                    Ok(value) => {
                        report.remote = Some(value);
                        publish(&report);
                    }
                    Err(_) => break,
                }
            }
        }
        report.remote_cleaned = remote.report.phase == "finished" && !remote.report.cleanup_pending;
        report.remote = Some(remote.report.clone());
    }
    if report.registered {
        report.forward_cancelled = matches!(
            timeout(
                Duration::from_secs(3),
                client.cancel_streamlocal_forward(remote_path)
            )
            .await,
            Ok(Ok(()))
        );
    }
    if let Some(lease) = lease {
        lease.close().await;
    }
    report.forwarding = router.report();
    report.remote_not_started = owner.is_none() && !recorded;
    report.cancelled = cancel.is_cancelled();
    report.error = result
        .err()
        .filter(|error| !(report.cancelled && matches!(*error, "CANCELLED" | "REMOTE_EXIT")));
    if report.remote_cleaned
        && let Err(error) = journal.forget(nonce)
    {
        report.error = Some(error);
    }
    report.pending_cleanup = journal.pending();
    if report.error.is_none()
        && ((!report.remote_cleaned && !report.remote_not_started)
            || (report.registered && !report.forward_cancelled)
            || report.forwarding.active != 0
            || (!report.cancelled && report.forwarding.failed != 0))
    {
        report.error = Some("CLEANUP_FAILED");
    }
    report.elapsed_ms = started.elapsed().as_millis() as u64;
    report.state = if report.error.is_some() {
        "fail"
    } else if report.cancelled {
        "stopped"
    } else {
        "pass"
    };
    publish(&report);
    report
}
