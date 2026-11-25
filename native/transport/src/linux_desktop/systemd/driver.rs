use super::{ActiveState, ChromeOwner, Generation, UnitStatus};
use eidolon_model::{gui_session_policy::GuiUnresponsiveGrace, ids::CommandSeq};
use russh::client;
use serde::Serialize;
use std::path::PathBuf;
use tokio::{
    sync::{mpsc, watch},
    time::{Duration, Instant, MissedTickBehavior, interval, timeout},
};
use tokio_util::sync::CancellationToken;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Available,
    Starting,
    Forwarded,
    Running,
    Stopping,
    Complete,
    CleanupPending,
}

#[derive(Clone, Debug, Serialize)]
pub struct Report {
    pub phase: Phase,
    pub launch_request: Option<CommandSeq>,
    pub generation: Option<String>,
    pub grace_seconds: Option<u32>,
    pub reason: Option<&'static str>,
    pub host_retired: bool,
    pub forward_retired: bool,
}

impl Report {
    pub fn available() -> Self {
        Self {
            phase: Phase::Available,
            launch_request: None,
            generation: None,
            grace_seconds: None,
            reason: None,
            host_retired: false,
            forward_retired: false,
        }
    }
}

pub enum Request {
    Launch {
        request: CommandSeq,
        local_socket: PathBuf,
        grace: GuiUnresponsiveGrace,
    },
    Displayed(CommandSeq),
    Liveness {
        launch: CommandSeq,
        scene: u64,
        sequence: u64,
    },
    LocalRetired(CommandSeq),
}

#[derive(Clone, Copy)]
pub(crate) struct Tick {
    pub launch: CommandSeq,
    pub scene: u64,
    pub sequence: u64,
}

pub(crate) async fn serve<H: client::Handler>(
    mut owner: ChromeOwner<'_, H>,
    mut requests: mpsc::Receiver<Request>,
    mut ticks: watch::Receiver<Option<Tick>>,
    cancel: &CancellationToken,
    mut publish: impl FnMut(&Report),
) -> Result<(), &'static str> {
    let mut report = Report::available();
    if let Err(reason) = owner.recover_pending(cancel).await {
        report.phase = Phase::CleanupPending;
        report.reason = Some(reason);
        publish(&report);
        return Err(reason);
    }
    publish(&report);
    let mut connection_probe = interval(Duration::from_millis(500));
    connection_probe.set_missed_tick_behavior(MissedTickBehavior::Skip);
    let (launch, local_socket, grace) = loop {
        tokio::select! {
            biased;
            _ = cancel.cancelled() => return Err("CANCELLED"),
            item = requests.recv() => match item {
                Some(Request::Launch { request, local_socket, grace }) => break (request, local_socket, grace),
                Some(_) => (),
                None => return Err("CANCELLED"),
            },
            _ = connection_probe.tick() => {
                if !owner.transport_connected() { return Err("SSH_IO_FAILED"); }
            }
        }
    };
    report.phase = Phase::Starting;
    report.launch_request = Some(launch);
    report.grace_seconds = Some(grace.seconds());
    publish(&report);

    let started = owner.start(&local_socket, grace, cancel).await;
    let generation = owner.pending().and_then(|record| record.generation());
    report.generation = generation.map(|generation| generation.to_string());
    let reason = match started {
        Ok(generation) => {
            report.phase = Phase::Forwarded;
            publish(&report);
            monitor(
                &mut owner,
                &mut requests,
                &mut ticks,
                generation,
                launch,
                cancel,
                &mut report,
                &mut publish,
            )
            .await
        }
        Err(error) => error,
    };
    report.phase = Phase::Stopping;
    report.reason = Some(reason);
    if let Some(generation) = generation {
        // Stop the user's workload before draining the forwarder. A negative
        // response (including an already exited app) still needs host evidence.
        let _ = owner.request_stop(generation).await;
        let _ = owner.close_forward(generation).await;
        report.forward_retired = owner.forward_retired();
        report.host_retired = owner.finish_remote(generation).await.is_ok();
    } else {
        report.host_retired = true;
        report.forward_retired = true;
    }
    publish(&report);

    // Let the compositor retire independently while this connection still
    // owns the record. Closing the socket cannot prove SDK resources are gone.
    let confirmation = timeout(Duration::from_secs(10), async {
        while let Some(message) = requests.recv().await {
            if matches!(message, Request::LocalRetired(request) if request == launch) {
                return true;
            }
        }
        false
    })
    .await
    .unwrap_or(false);
    let complete = match generation {
        Some(generation) if confirmation => owner.finish_local(generation).unwrap_or(false),
        Some(_) => false,
        None => confirmation,
    };
    if complete {
        report.phase = Phase::Complete;
    } else {
        report.phase = Phase::CleanupPending;
        report.reason = Some("SYSTEMD_CLEANUP_PENDING");
    }
    publish(&report);
    if complete {
        Ok(())
    } else {
        Err("SYSTEMD_CLEANUP_PENDING")
    }
}

async fn monitor<H: client::Handler>(
    owner: &mut ChromeOwner<'_, H>,
    requests: &mut mpsc::Receiver<Request>,
    ticks: &mut watch::Receiver<Option<Tick>>,
    generation: Generation,
    launch: CommandSeq,
    cancel: &CancellationToken,
    report: &mut Report,
    publish: &mut impl FnMut(&Report),
) -> &'static str {
    let map_deadline = Instant::now() + Duration::from_secs(25);
    let mut last_tick = (0, 0);
    let mut probe_timer = interval(Duration::from_secs(1));
    probe_timer.set_missed_tick_behavior(MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            biased;
            _ = cancel.cancelled() => return "SYSTEMD_STOP_REQUESTED",
            message = requests.recv() => match message {
                Some(Request::Displayed(request)) if request == launch && report.phase == Phase::Forwarded => {
                    if let Err(error) = owner.accept_display(generation) { return error; }
                    report.phase = Phase::Running;
                    publish(report);
                }
                Some(_) => (),
                None => return "SYSTEMD_STOP_REQUESTED",
            },
            changed = ticks.changed() => {
                if changed.is_err() { return "SYSTEMD_STOP_REQUESTED"; }
                let latest = *ticks.borrow_and_update();
                if let Some(tick) = latest
                    && tick.launch == launch && report.phase == Phase::Running
                    && tick.scene != 0 && tick.sequence != 0 && (tick.scene, tick.sequence) > last_tick
                {
                    last_tick = (tick.scene, tick.sequence);
                    // An unknown renewal response does not establish SSH loss.
                    let _ = owner.renew(generation, cancel).await;
                }
            }
            _ = probe_timer.tick() => {
                if !owner.transport_connected() { return "SYSTEMD_TRANSFER_LOST"; }
                if report.phase == Phase::Forwarded && Instant::now() >= map_deadline {
                    return "SYSTEMD_WINDOW_TIMEOUT";
                }
                match owner.inspect(generation, cancel).await {
                    Ok(UnitStatus::Absent)
                    | Ok(UnitStatus::Loaded {
                        active_state: ActiveState::Inactive | ActiveState::Failed | ActiveState::Deactivating,
                        ..
                    }) => return "SYSTEMD_HOST_EXIT",
                    Ok(_) | Err(_) => (),
                }
            },
        }
    }
}
