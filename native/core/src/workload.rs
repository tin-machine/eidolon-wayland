//! 明示launch、PTY登録、同session内app-idの三者をjoinする。到着順は使わない。
use crate::compositor::protocol::WindowReport;
use eidolon_model::{
    ids::{SampleSeq, SessionId, TerminalId, WindowId},
    window::WindowLife,
    workload::{
        Activity, Capability, Display, FOOT_APP_ID, Registration, Telemetry, claimed_terminal,
    },
};
use eidolon_transport::launcher::TerminalLaunch;
use serde::Serialize;
use std::{collections::BTreeMap, time::Duration};

#[derive(Clone, Debug, Serialize)]
pub struct WindowWorkload {
    pub window: WindowId,
    pub terminal_id: Option<TerminalId>,
    pub state: &'static str,
    pub identity: Option<String>,
    pub base_app_id: Option<&'static str>,
    pub display: Display,
}
#[derive(Default)]
struct Binding {
    app_id: Option<String>,
    registration: Option<Registration>,
    activity: Activity,
    sequence: Option<SampleSeq>,
    identity: Option<String>,
    launch_accepted: bool,
    waiting_since: Option<Duration>,
}
#[derive(Default)]
pub struct Tracker {
    telemetry: Option<Telemetry>,
    launches: Vec<TerminalLaunch>,
    windows: BTreeMap<WindowId, Binding>,
    activities: BTreeMap<String, Activity>,
}
impl Tracker {
    pub fn ingest(&mut self, remote: &eidolon_transport::launcher::Report, now: Duration) {
        self.launches = remote.terminal_launches.clone();
        if let Some(value) = remote.workload.as_ref()
            && self.telemetry.as_ref().is_none_or(|before| {
                before.sequence != value.sequence || before.epoch != value.epoch
            })
        {
            self.activities.retain(|key, _| {
                value
                    .workloads
                    .iter()
                    .any(|workload| Self::identity(value, workload) == *key)
            });
            let mut updated = std::collections::BTreeSet::new();
            for workload in &value.workloads {
                let key = Self::identity(value, workload);
                if updated.insert(key.clone()) {
                    self.activities.entry(key).or_default().observe(
                        &value.epoch,
                        value.boot.as_deref().unwrap_or_default(),
                        workload,
                        value.interval_seconds,
                        now,
                    );
                }
            }
            self.telemetry = Some(value.clone());
        }
    }
    fn identity(value: &Telemetry, workload: &eidolon_model::workload::Workload) -> String {
        format!(
            "{}:{}:{}",
            value.epoch,
            value.boot.as_deref().unwrap_or_default(),
            workload.workload_id
        )
    }
    pub fn sync(
        &mut self,
        session: SessionId,
        nonce: &str,
        windows: &[WindowReport],
        now: Duration,
        connected: bool,
    ) -> Vec<WindowWorkload> {
        self.windows.retain(|id, _| {
            windows
                .iter()
                .any(|w| w.window == *id && w.life == WindowLife::Mapped)
        });
        let telemetry = self
            .telemetry
            .as_ref()
            .filter(|t| t.session == session && t.nonce == nonce && t.valid());
        let mut reports = Vec::new();
        for window in windows.iter().filter(|w| w.life == WindowLife::Mapped) {
            let terminal = window
                .app_id
                .as_deref()
                .and_then(|app| claimed_terminal(app, nonce));
            let binding = self.windows.entry(window.window).or_default();
            if binding.app_id != window.app_id {
                *binding = Binding {
                    app_id: window.app_id.clone(),
                    ..Default::default()
                };
            }
            let mut state = "unavailable";
            if binding.waiting_since.is_none() {
                binding.waiting_since = Some(now);
            }
            binding.launch_accepted |= terminal.is_some_and(|terminal| {
                self.launches
                    .iter()
                    .any(|launch| launch.terminal_id == terminal && launch.accepted == Some(true))
            });
            let unique = terminal.is_some()
                && windows
                    .iter()
                    .filter(|w| w.life == WindowLife::Mapped && w.app_id == window.app_id)
                    .count()
                    == 1;
            if !unique {
                binding.registration = None;
                binding.activity.clear();
                binding.identity = None;
                binding.sequence = None;
                if terminal.is_some() {
                    state = "ambiguous";
                }
            } else if let (Some(terminal), Some(telemetry)) = (terminal, telemetry) {
                let registered = telemetry
                    .registrations
                    .iter()
                    .find(|r| r.terminal_id == terminal);
                let accepted = self
                    .launches
                    .iter()
                    .any(|launch| launch.terminal_id == terminal && launch.accepted == Some(true));
                if binding.registration.is_none() && accepted {
                    binding.registration = registered.cloned();
                }
                if registered.is_some()
                    && binding.registration.is_some()
                    && registered != binding.registration.as_ref()
                {
                    // 同TerminalIdのidentity変更は他processへの付け替えにしない。
                    binding.activity.clear();
                    binding.identity = None;
                    binding.sequence = None;
                    state = "identity_changed";
                } else if binding.registration.is_some() {
                    state = "registered";
                    if let Some(value) = telemetry
                        .workloads
                        .iter()
                        .find(|w| w.terminal_id == terminal)
                    {
                        let identity = Self::identity(telemetry, value);
                        if binding.identity.as_ref() != Some(&identity) {
                            binding.activity.clear();
                            binding.sequence = None;
                            binding.identity = Some(identity);
                        }
                        if binding.sequence != Some(telemetry.sequence) {
                            binding.activity = self
                                .activities
                                .get(binding.identity.as_ref().unwrap())
                                .cloned()
                                .unwrap_or_default();
                            binding.sequence = Some(telemetry.sequence);
                        }
                    } else {
                        binding.activity.missing();
                    }
                    if telemetry.capability != Capability::Available {
                        binding.activity.missing();
                    }
                } else {
                    state = if binding
                        .waiting_since
                        .is_some_and(|at| now.saturating_sub(at) > Duration::from_secs(6))
                    {
                        "registration_timeout"
                    } else {
                        "pending"
                    };
                    binding.activity.missing();
                }
            } else {
                binding.activity.missing();
            }
            if !connected {
                binding.activity.missing();
                state = "disconnected";
            }
            reports.push(WindowWorkload {
                window: window.window,
                terminal_id: terminal,
                state,
                identity: binding.identity.clone(),
                base_app_id: binding.launch_accepted.then_some(FOOT_APP_ID),
                display: binding.activity.snapshot(now),
            });
        }
        reports
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use eidolon_model::workload::terminal_app_id;
    fn session() -> SessionId {
        SessionId::new(1).unwrap()
    }
    fn window(id: i64, terminal: i64) -> WindowReport {
        WindowReport {
            window: WindowId::new(id).unwrap(),
            app_id: terminal_app_id("aaaaaaaaaaaaaaaa", TerminalId::new(terminal).unwrap()),
            parent: None,
            life: WindowLife::Mapped,
            frame: None,
            logical_size: None,
            layers: 1,
            resize: Default::default(),
            sizing: Default::default(),
        }
    }
    fn remote(sequence: i64) -> eidolon_transport::launcher::Report {
        let telemetry:Telemetry=serde_json::from_value(serde_json::json!({
            "schema":1,"nonce":"aaaaaaaaaaaaaaaa","uid":10158,"session":"1","epoch":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "sequence":sequence.to_string(),"sampled_monotonic_ms":sequence*1000,"boot":"00000000-0000-0000-0000-000000000001",
            "capability":"available","interval_seconds":1.0,"scan_seconds":0.01,
            "registrations":[{"terminal_id":"1","pid":100,"start":"10","tty":"/dev/pts/1"},
                             {"terminal_id":"2","pid":200,"start":"20","tty":"/dev/pts/2"}],
            "workloads":[{"terminal_id":"1","workload_id":"shell:1:100:10","scope":"shell_tree","cpu_cores":1.,"rss_bytes":4096,"processes":1,"coverage":"complete","reason":null},
                         {"terminal_id":"2","workload_id":"shell:2:200:20","scope":"shell_tree","cpu_cores":0.,"rss_bytes":8192,"processes":1,"coverage":"complete","reason":null}]
        })).unwrap();
        eidolon_transport::launcher::Report {
            workload: Some(telemetry),
            terminal_launches: (1..=2)
                .map(|id| TerminalLaunch {
                    terminal_id: TerminalId::new(id).unwrap(),
                    request_id: None,
                    accepted: Some(true),
                    mode: eidolon_model::catalog::Mode::Terminal,
                })
                .collect(),
            ..Default::default()
        }
    }
    #[test]
    fn late_second_view_of_shared_tmux_uses_the_same_smoothed_value() {
        let mut tracker = Tracker::default();
        for sequence in 1..=2 {
            let mut report = remote(sequence);
            for workload in &mut report.workload.as_mut().unwrap().workloads {
                workload.scope = eidolon_model::workload::Scope::TmuxSession;
                workload.workload_id = "tmux:300:30:$1".into();
                workload.cpu_cores = Some(if sequence == 1 { 0.1 } else { 1.0 });
                workload.rss_bytes = Some(4096);
            }
            tracker.ingest(&report, Duration::from_secs(sequence as u64));
            tracker.sync(
                session(),
                "aaaaaaaaaaaaaaaa",
                &[window(10, 1)],
                Duration::from_secs(sequence as u64),
                true,
            );
        }
        let reports = tracker.sync(
            session(),
            "aaaaaaaaaaaaaaaa",
            &[window(10, 1), window(20, 2)],
            Duration::from_secs(2),
            true,
        );
        assert_eq!(
            reports[0].display.smoothed_cores,
            reports[1].display.smoothed_cores
        );
        assert!(reports[0].display.smoothed_cores.unwrap() < 1.);
    }
    #[test]
    fn reversed_map_and_registration_order_joins_by_token_and_does_not_refresh_stale_samples() {
        let mut tracker = Tracker::default();
        let windows = [window(20, 2), window(10, 1)];
        assert!(
            tracker
                .sync(
                    session(),
                    "aaaaaaaaaaaaaaaa",
                    &windows,
                    Duration::ZERO,
                    true
                )
                .iter()
                .all(|w| w.display.stale)
        );
        tracker.ingest(&remote(1), Duration::ZERO);
        let reports = tracker.sync(
            session(),
            "aaaaaaaaaaaaaaaa",
            &windows,
            Duration::ZERO,
            true,
        );
        assert_eq!(reports[0].display.displayed_cores, Some(0.));
        assert_eq!(reports[1].display.displayed_cores, Some(1.));
        assert!(
            reports
                .iter()
                .all(|w| w.state == "registered" && w.base_app_id == Some(FOOT_APP_ID))
        );
        tracker.ingest(&remote(1), Duration::from_secs(4));
        let reports = tracker.sync(
            session(),
            "aaaaaaaaaaaaaaaa",
            &windows,
            Duration::from_secs(4),
            true,
        );
        assert!(reports.iter().all(|w| w.display.stale && !w.display.busy));
        assert_eq!(reports[1].display.displayed_cores, Some(1.));
        tracker.ingest(&remote(2), Duration::from_secs(5));
        assert!(
            tracker.sync(
                session(),
                "aaaaaaaaaaaaaaaa",
                &windows,
                Duration::from_secs(5),
                true
            )[1]
            .display
            .busy
        );
        assert!(
            tracker
                .sync(
                    session(),
                    "aaaaaaaaaaaaaaaa",
                    &windows,
                    Duration::from_secs(5),
                    false
                )
                .iter()
                .all(|w| w.display.stale)
        );
    }
    #[test]
    fn launch_receipt_is_separate_and_ambiguous_or_changed_identity_cannot_claim_load() {
        let mut tracker = Tracker::default();
        let mut remote = remote(1);
        let mut windows = vec![window(10, 1)];
        remote.terminal_launches[0].accepted = None;
        tracker.ingest(&remote, Duration::ZERO);
        assert_eq!(
            tracker.sync(
                session(),
                "aaaaaaaaaaaaaaaa",
                &windows,
                Duration::ZERO,
                true
            )[0]
            .state,
            "pending"
        );
        remote.terminal_launches[0].accepted = Some(true);
        tracker.ingest(&remote, Duration::ZERO);
        assert!(
            tracker.sync(
                session(),
                "aaaaaaaaaaaaaaaa",
                &windows,
                Duration::ZERO,
                true
            )[0]
            .display
            .busy
        );
        windows.push(window(20, 1));
        let duplicated = tracker.sync(
            session(),
            "aaaaaaaaaaaaaaaa",
            &windows,
            Duration::ZERO,
            true,
        );
        assert!(
            duplicated
                .iter()
                .all(|w| w.state == "ambiguous" && w.display.displayed_cores.is_none())
        );
        windows.pop();
        tracker.sync(
            session(),
            "aaaaaaaaaaaaaaaa",
            &windows,
            Duration::ZERO,
            true,
        );
        let telemetry = remote.workload.as_mut().unwrap();
        telemetry.sequence = SampleSeq::new(2).unwrap();
        telemetry.registrations[0].start = "999".into();
        telemetry.workloads[0].workload_id = "shell:1:100:999".into();
        tracker.ingest(&remote, Duration::from_secs(1));
        let changed = tracker.sync(
            session(),
            "aaaaaaaaaaaaaaaa",
            &windows,
            Duration::from_secs(1),
            true,
        );
        assert_eq!(changed[0].state, "identity_changed");
        assert_eq!(changed[0].display.displayed_cores, None);
        windows[0].app_id = terminal_app_id("bbbbbbbbbbbbbbbb", TerminalId::new(1).unwrap());
        let other_session = tracker.sync(
            session(),
            "aaaaaaaaaaaaaaaa",
            &windows,
            Duration::from_secs(1),
            true,
        );
        assert_eq!(other_session[0].terminal_id, None);
        assert_eq!(other_session[0].display.displayed_cores, None);
    }
    #[test]
    fn io_uses_registered_window_binding_and_does_not_refresh_without_new_sequence() {
        let mut tracker = Tracker::default();
        let mut remote = remote(1);
        let telemetry = remote.workload.as_mut().unwrap();
        telemetry.workloads[0].io = Some(eidolon_model::workload::IoSample {
            state: eidolon_model::workload::IoState::Available,
            read_bytes_per_second: Some(100.),
            write_bytes_per_second: Some(200.),
            interval_seconds: Some(1.),
            sampled_monotonic_ms: Some(1000),
            reason: None,
        });
        // CPU baselineはIO配送を妨げない。
        telemetry.workloads[0].cpu_cores = None;
        telemetry.workloads[0].coverage = eidolon_model::workload::Coverage::Partial;
        telemetry.workloads[0].reason = Some("baseline".into());
        assert!(telemetry.valid());
        tracker.ingest(&remote, Duration::ZERO);
        let windows = [window(10, 1), window(20, 2)];
        let reports = tracker.sync(
            session(),
            "aaaaaaaaaaaaaaaa",
            &windows,
            Duration::ZERO,
            true,
        );
        assert!(reports[0].display.stale);
        assert_eq!(reports[0].display.io.bytes_per_second, Some(300.));
        assert_eq!(reports[1].display.io.bytes_per_second, None);
        tracker.ingest(&remote, Duration::from_secs(4));
        let stale = tracker.sync(
            session(),
            "aaaaaaaaaaaaaaaa",
            &windows,
            Duration::from_secs(4),
            true,
        );
        assert!(
            stale
                .iter()
                .all(|w| w.display.io.bytes_per_second.is_none())
        );
        let disconnected = tracker.sync(
            session(),
            "aaaaaaaaaaaaaaaa",
            &windows,
            Duration::ZERO,
            false,
        );
        assert!(
            disconnected
                .iter()
                .all(|w| w.display.io.bytes_per_second.is_none())
        );
    }
}
