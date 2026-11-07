//! 観測値のwire契約と表示状態。process identityはcleanupの権限ではない。
use crate::ids::{SampleSeq, SessionId, TerminalId};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, time::Duration};

pub const FOOT_APP_ID: &str = "io.github.tinmachine.eidolon.foot";
pub const MAX_TERMINALS: usize = 16;
pub const STALE_AFTER: Duration = Duration::from_secs(3);

pub fn hex(text: &str, length: usize) -> bool {
    text.len() == length
        && text
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn decimal(text: &str) -> bool {
    !text.starts_with('0')
        && !text.is_empty()
        && text.len() <= 20
        && text.bytes().all(|c| c.is_ascii_digit())
        && text.parse::<u64>().is_ok()
}
fn boot_id(text: &str) -> bool {
    text.len() == 36
        && text.bytes().enumerate().all(|(i, c)| {
            if [8, 13, 18, 23].contains(&i) {
                c == b'-'
            } else {
                c.is_ascii_digit() || (b'a'..=b'f').contains(&c)
            }
        })
}
pub fn terminal_app_id(nonce: &str, terminal: TerminalId) -> Option<String> {
    hex(nonce, 16).then(|| format!("{FOOT_APP_ID}.t-{nonce}-{}", terminal.get()))
}
/// clientの申告値だけを解析する。登録・起動receiptとの照合前には認証済みとしない。
pub fn claimed_terminal(app_id: &str, nonce: &str) -> Option<TerminalId> {
    if !hex(nonce, 16) {
        return None;
    }
    let text = app_id.strip_prefix(&format!("{FOOT_APP_ID}.t-{nonce}-"))?;
    if !decimal(text) {
        return None;
    }
    TerminalId::new(text.parse().ok()?).ok()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Capability {
    Available,
    Unavailable,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Coverage {
    Complete,
    Partial,
    Unknown,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Scope {
    ShellTree,
    TmuxSession,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Registration {
    pub terminal_id: TerminalId,
    pub pid: u32,
    pub start: String,
    pub tty: String,
}
impl Registration {
    pub fn valid(&self) -> bool {
        self.pid > 0
            && decimal(&self.start)
            && self.tty.strip_prefix("/dev/pts/").is_some_and(|text| {
                !text.is_empty() && text.len() <= 10 && text.bytes().all(|c| c.is_ascii_digit())
            })
    }
}
/// /proc/<tgid>/ioのstorage帰属量。physical disk throughputではない。
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IoState {
    Available,
    Baseline,
    Partial,
    Unavailable,
    Stale,
    Stopped,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IoSample {
    pub state: IoState,
    pub read_bytes_per_second: Option<f64>,
    pub write_bytes_per_second: Option<f64>,
    pub interval_seconds: Option<f64>,
    pub sampled_monotonic_ms: Option<u64>,
    pub reason: Option<String>,
}
impl IoSample {
    pub fn valid(&self) -> bool {
        if self.state == IoState::Available {
            let rate = |v: Option<f64>| {
                v.is_some_and(|v| {
                    v.is_finite() && (0.0..=18_446_744_073_709_551_616.0).contains(&v)
                })
            };
            rate(self.read_bytes_per_second)
                && rate(self.write_bytes_per_second)
                && self
                    .interval_seconds
                    .is_some_and(|v| v.is_finite() && v > 0.0 && v <= 3.0)
                && self.sampled_monotonic_ms.is_some()
                && self.reason.is_none()
        } else {
            self.read_bytes_per_second.is_none()
                && self.write_bytes_per_second.is_none()
                && self.interval_seconds.is_none()
                && self.sampled_monotonic_ms.is_none()
                && matches!(
                    (self.state, self.reason.as_deref()),
                    (IoState::Baseline, Some("baseline"))
                        | (
                            IoState::Unavailable,
                            Some("access_denied" | "io_unavailable")
                        )
                        | (IoState::Stale, Some("clock"))
                        | (IoState::Stopped, Some("disabled"))
                        | (
                            IoState::Partial,
                            Some(
                                "scope_changed"
                                    | "snapshot_changed"
                                    | "zombie_pending"
                                    | "root_missing"
                                    | "scan_partial"
                                    | "identity_changed"
                                    | "read_limit"
                                    | "parse_invalid"
                                    | "counter_regressed"
                                    | "rate_limit"
                                    | "budget"
                                    | "tmux_uncertain",
                            ),
                        )
                )
        }
    }
}
#[derive(Clone, Debug, Serialize)]
pub struct IoDisplay {
    pub state: IoState,
    pub bytes_per_second: Option<f64>,
    pub reason: Option<String>,
}
impl Default for IoDisplay {
    fn default() -> Self {
        Self {
            state: IoState::Unavailable,
            bytes_per_second: None,
            reason: Some("unreported".into()),
        }
    }
}
impl IoDisplay {
    pub fn stopped() -> Self {
        Self {
            state: IoState::Stopped,
            bytes_per_second: None,
            reason: Some("disabled".into()),
        }
    }
    fn missing(&mut self) {
        self.state = IoState::Stale;
        self.bytes_per_second = None;
        self.reason = Some("missing".into());
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Workload {
    pub terminal_id: TerminalId,
    pub workload_id: String,
    pub scope: Scope,
    pub cpu_cores: Option<f64>,
    #[serde(default)]
    pub io: Option<IoSample>,
    pub rss_bytes: Option<u64>,
    pub processes: usize,
    pub coverage: Coverage,
    pub reason: Option<String>,
}
impl Workload {
    fn valid(&self) -> bool {
        let parts: Vec<_> = self.workload_id.split(':').collect();
        let key = match (self.scope, parts.as_slice()) {
            (Scope::ShellTree, ["shell", terminal, pid, start]) => {
                decimal(terminal)
                    && decimal(pid)
                    && decimal(start)
                    && terminal.parse::<i64>().ok() == Some(self.terminal_id.get())
            }
            (Scope::TmuxSession, ["tmux", pid, start, session]) => {
                decimal(pid)
                    && decimal(start)
                    && session.strip_prefix('$').is_some_and(|s| {
                        !s.is_empty() && s.len() <= 20 && s.bytes().all(|c| c.is_ascii_digit())
                    })
            }
            _ => false,
        };
        key && self
            .io
            .as_ref()
            .is_none_or(|io| io.valid() && (io.state != IoState::Available || self.processes > 0))
            && self.workload_id.len() <= 128
            && self.processes <= 512
            && self
                .cpu_cores
                .is_none_or(|v| v.is_finite() && (0.0..=4096.0).contains(&v))
            && match self.coverage {
                Coverage::Complete => {
                    self.reason.is_none()
                        && self.cpu_cores.is_some()
                        && self.rss_bytes.is_some()
                        && self.processes > 0
                }
                Coverage::Partial | Coverage::Unknown => {
                    self.cpu_cores.is_none()
                        && self.reason.as_deref().is_some_and(|s| {
                            matches!(
                                s,
                                "root_missing" | "scan_partial" | "baseline" | "tmux_uncertain"
                            )
                        })
                }
            }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Telemetry {
    pub schema: u32,
    pub nonce: String,
    pub uid: u32,
    pub session: SessionId,
    pub epoch: String,
    pub sequence: SampleSeq,
    pub sampled_monotonic_ms: u64,
    pub boot: Option<String>,
    pub capability: Capability,
    pub interval_seconds: Option<f64>,
    pub scan_seconds: f64,
    pub registrations: Vec<Registration>,
    pub workloads: Vec<Workload>,
}
impl Telemetry {
    pub fn valid(&self) -> bool {
        self.schema == 1
            && hex(&self.nonce, 16)
            && hex(&self.epoch, 32)
            && self.boot.as_deref().is_none_or(boot_id)
            && self.scan_seconds.is_finite()
            && (0.0..=60.0).contains(&self.scan_seconds)
            && self
                .interval_seconds
                .is_none_or(|s| s.is_finite() && s > 0.0 && s <= 3.0)
            && self.registrations.len() <= MAX_TERMINALS
            && self.workloads.len() <= MAX_TERMINALS
            && self.registrations.iter().all(Registration::valid)
            && self.workloads.iter().all(|w| {
                w.valid()
                    && w.io.as_ref().is_none_or(|io| {
                        io.sampled_monotonic_ms.is_none_or(|at| {
                            at <= self.sampled_monotonic_ms
                                && self.sampled_monotonic_ms - at <= 3000
                        })
                    })
            })
            && self.workloads.iter().all(|workload| {
                (workload.coverage != Coverage::Complete
                    && !workload
                        .io
                        .as_ref()
                        .is_some_and(|io| io.state == IoState::Available))
                    || self.registrations.iter().any(|registration| {
                        registration.terminal_id == workload.terminal_id
                            && (workload.scope != Scope::ShellTree
                                || workload.workload_id
                                    == format!(
                                        "shell:{}:{}:{}",
                                        registration.terminal_id.get(),
                                        registration.pid,
                                        registration.start
                                    ))
                    })
            })
            && self.workloads.iter().enumerate().all(|(index, row)| {
                self.workloads[..index]
                    .iter()
                    .filter(|other| other.workload_id == row.workload_id)
                    .all(|other| {
                        other.scope == row.scope
                            && other.cpu_cores == row.cpu_cores
                            && other.rss_bytes == row.rss_bytes
                            && other.processes == row.processes
                            && other.coverage == row.coverage
                            && other.reason == row.reason
                            && other.io == row.io
                    })
            })
            && self
                .registrations
                .iter()
                .map(|r| r.terminal_id)
                .collect::<BTreeSet<_>>()
                .len()
                == self.registrations.len()
            && self
                .workloads
                .iter()
                .map(|w| w.terminal_id)
                .collect::<BTreeSet<_>>()
                .len()
                == self.workloads.len()
            && (self.interval_seconds.is_some()
                || self.workloads.iter().all(|w| w.cpu_cores.is_none()))
            && match self.capability {
                Capability::Available => self.boot.is_some(),
                Capability::Unavailable => {
                    self.registrations.is_empty()
                        && self.workloads.is_empty()
                        && self.interval_seconds.is_none()
                }
            }
    }
    pub fn follows(&self, before: &Self) -> bool {
        self.valid()
            && self.session == before.session
            && self.nonce == before.nonce
            && self.uid == before.uid
            && self.epoch == before.epoch
            && self.boot == before.boot
            && self.sequence > before.sequence
            && self.sampled_monotonic_ms >= before.sampled_monotonic_ms
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Display {
    pub workload_id: Option<String>,
    pub scope: Option<Scope>,
    pub instantaneous_cores: Option<f64>,
    pub smoothed_cores: Option<f64>,
    /// 欠測時には同じworkloadの最後の値だけを保持。別workloadへは持ち越さない。
    pub displayed_cores: Option<f64>,
    pub rss_bytes: Option<u64>,
    pub io: IoDisplay,
    pub busy: bool,
    pub stale: bool,
    pub coverage: Coverage,
}
impl Default for Display {
    fn default() -> Self {
        Self {
            workload_id: None,
            scope: None,
            instantaneous_cores: None,
            smoothed_cores: None,
            displayed_cores: None,
            rss_bytes: None,
            io: IoDisplay::default(),
            busy: false,
            stale: true,
            coverage: Coverage::Unknown,
        }
    }
}
#[derive(Default, Clone)]
pub struct Activity {
    display: Display,
    identity: Option<(String, String, String)>,
    last_valid: Option<Duration>,
    io_last_valid: Option<Duration>,
}
impl Activity {
    pub fn observe(
        &mut self,
        epoch: &str,
        boot: &str,
        value: &Workload,
        interval: Option<f64>,
        now: Duration,
    ) {
        let identity = (epoch.to_owned(), boot.to_owned(), value.workload_id.clone());
        if self.identity.as_ref() != Some(&identity) {
            self.display = Display::default();
            self.last_valid = None;
            self.io_last_valid = None;
            self.identity = Some(identity);
        }
        self.display.workload_id = Some(value.workload_id.clone());
        self.display.scope = Some(value.scope);
        self.display.coverage = value.coverage;
        // CPUの欠測early-returnとは独立。IO partialもCPU/RSSへ伝播しない。
        self.io_last_valid = None;
        self.display.io = if let Some(io) = value.io.as_ref().filter(|io| io.valid()) {
            let rate = io
                .read_bytes_per_second
                .zip(io.write_bytes_per_second)
                .map(|(read, write)| read + write);
            if io.state == IoState::Available {
                self.io_last_valid = Some(now);
            }
            IoDisplay {
                state: io.state,
                bytes_per_second: rate,
                reason: io.reason.clone(),
            }
        } else {
            IoDisplay::default()
        };
        let valid = value.valid()
            && value.coverage == Coverage::Complete
            && interval.is_some_and(|dt| dt.is_finite() && dt > 0.0 && dt <= 3.0);
        if !valid {
            self.missing_cpu();
            return;
        }
        let cpu = value.cpu_cores.expect("complete");
        let contiguous = self
            .last_valid
            .is_some_and(|at| now >= at && now - at <= STALE_AFTER)
            && !self.display.stale;
        let ema = if contiguous {
            self.display.smoothed_cores.map_or(cpu, |old| {
                old + (1.0 - (-interval.unwrap()).exp()) * (cpu - old)
            })
        } else {
            cpu
        };
        self.display.instantaneous_cores = Some(cpu);
        self.display.smoothed_cores = Some(ema);
        self.display.displayed_cores = Some(ema);
        self.display.rss_bytes = value.rss_bytes;
        self.display.busy = if self.display.busy {
            ema >= 0.08
        } else {
            ema > 0.15
        };
        self.display.stale = false;
        self.last_valid = Some(now);
    }
    pub fn missing(&mut self) {
        self.missing_cpu();
        self.display.io.missing();
        self.io_last_valid = None;
    }
    fn missing_cpu(&mut self) {
        self.display.stale = true;
        self.display.busy = false;
        self.display.instantaneous_cores = None;
    }
    pub fn snapshot(&self, now: Duration) -> Display {
        let mut display = self.display.clone();
        if display.io.state == IoState::Available
            && self
                .io_last_valid
                .is_none_or(|at| now < at || now - at > STALE_AFTER)
        {
            display.io.missing();
        }
        if self
            .last_valid
            .is_none_or(|at| now < at || now - at > STALE_AFTER)
        {
            display.stale = true;
            display.busy = false;
            display.instantaneous_cores = None;
        }
        display
    }
    pub fn clear(&mut self) {
        *self = Self::default();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn workload(cpu: Option<f64>, name: &str) -> Workload {
        Workload {
            terminal_id: TerminalId::new(1).unwrap(),
            workload_id: name.into(),
            scope: Scope::ShellTree,
            cpu_cores: cpu,
            io: None,
            rss_bytes: Some(4096),
            processes: 1,
            coverage: if cpu.is_some() {
                Coverage::Complete
            } else {
                Coverage::Partial
            },
            reason: cpu.is_none().then(|| "baseline".into()),
        }
    }
    #[test]
    fn correlation_is_session_scoped_and_does_not_accept_similar_client_strings() {
        let id = TerminalId::new(i64::MAX).unwrap();
        let nonce = "a".repeat(16);
        let value = terminal_app_id(&nonce, id).unwrap();
        assert_eq!(claimed_terminal(&value, &nonce), Some(id));
        assert_eq!(claimed_terminal(&value, &"b".repeat(16)), None);
        for value in [
            format!("{FOOT_APP_ID}.t-{nonce}-01"),
            format!("{FOOT_APP_ID}.t-{nonce}-0"),
            format!("{FOOT_APP_ID}.t-{nonce}-1.extra"),
        ] {
            assert_eq!(claimed_terminal(&value, &nonce), None);
        }
    }
    #[test]
    fn activity_missing_holds_last_without_busy_and_new_workload_clears_it() {
        let mut activity = Activity::default();
        activity.observe(
            "a",
            "boot",
            &workload(Some(1.0), "shell:1:123:100"),
            Some(1.),
            Duration::ZERO,
        );
        assert!(activity.snapshot(Duration::ZERO).busy);
        activity.observe(
            "a",
            "boot",
            &workload(None, "shell:1:123:100"),
            Some(1.),
            Duration::from_secs(1),
        );
        let missing = activity.snapshot(Duration::from_secs(1));
        assert!(missing.stale);
        assert!(!missing.busy);
        assert_eq!(missing.displayed_cores, Some(1.));
        activity.observe(
            "a",
            "boot",
            &workload(None, "shell:1:123:200"),
            Some(1.),
            Duration::from_secs(2),
        );
        assert_eq!(
            activity.snapshot(Duration::from_secs(2)).displayed_cores,
            None
        );
        activity.observe(
            "a",
            "boot",
            &workload(Some(0.01), "shell:1:123:200"),
            Some(1.),
            Duration::from_secs(3),
        );
        assert_eq!(
            activity.snapshot(Duration::from_secs(3)).smoothed_cores,
            Some(0.01)
        );
        assert!(activity.snapshot(Duration::from_secs(7)).stale);
        activity.observe(
            "a",
            "boot",
            &workload(Some(1.), "shell:1:123:200"),
            Some(1.),
            Duration::from_secs(8),
        );
        assert_eq!(
            activity.snapshot(Duration::from_secs(8)).smoothed_cores,
            Some(1.)
        );
    }
    #[test]
    fn activity_ema_uses_sample_interval_and_hysteresis() {
        let mut activity = Activity::default();
        activity.observe(
            "a",
            "boot",
            &workload(Some(0.14), "shell:1:123:100"),
            Some(1.),
            Duration::ZERO,
        );
        assert!(!activity.snapshot(Duration::ZERO).busy);
        activity.observe(
            "a",
            "boot",
            &workload(Some(0.2), "shell:1:123:100"),
            Some(0.5),
            Duration::from_millis(500),
        );
        let next = activity.snapshot(Duration::from_millis(500));
        assert!(next.busy);
        assert!(
            (next.smoothed_cores.unwrap() - (0.14 + (1. - (-0.5f64).exp()) * 0.06)).abs() < 1e-10
        );
        activity.observe(
            "a",
            "boot",
            &workload(Some(0.1), "shell:1:123:100"),
            Some(1.),
            Duration::from_millis(1500),
        );
        assert!(activity.snapshot(Duration::from_millis(1500)).busy);
        activity.observe(
            "a",
            "boot",
            &workload(Some(0.), "shell:1:123:100"),
            Some(1.),
            Duration::from_millis(2500),
        );
        assert!(!activity.snapshot(Duration::from_millis(2500)).busy);
    }
    #[test]
    fn wire_rejects_unknown_fields_bad_numbers_duplicate_and_reordered_samples() {
        let value = serde_json::json!({"schema":1,"nonce":"aaaaaaaaaaaaaaaa","uid":10158,"session":"1",
            "epoch":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","sequence":"1","sampled_monotonic_ms":1000,
            "boot":"1138d4e6-1cd2-4f26-b9bc-92fd8dcad20b","capability":"available","interval_seconds":1.0,
            "scan_seconds":0.01,"registrations":[{"terminal_id":"1","pid":123,"start":"100","tty":"/dev/pts/1"}],
            "workloads":[workload(Some(1.),"shell:1:123:100")]});
        let before: Telemetry = serde_json::from_value(value.clone()).unwrap();
        assert!(before.valid());
        assert!(!before.follows(&before));
        let mut next = before.clone();
        next.sequence = SampleSeq::new(2).unwrap();
        assert!(next.follows(&before));
        next.epoch = "c".repeat(32);
        assert!(!next.follows(&before));
        for (field, bad) in [
            ("scan_seconds", serde_json::json!(-1)),
            ("interval_seconds", serde_json::json!(5.)),
            ("boot", serde_json::json!("other")),
        ] {
            let mut modified = value.clone();
            modified[field] = bad;
            assert!(
                !serde_json::from_value::<Telemetry>(modified)
                    .unwrap()
                    .valid()
            );
        }
        let mut extra = value.clone();
        extra["cmdline"] = serde_json::json!("never accept this");
        assert!(serde_json::from_value::<Telemetry>(extra).is_err());
        let mut duplicate = before.clone();
        duplicate
            .registrations
            .push(duplicate.registrations[0].clone());
        assert!(!duplicate.valid());
        let mut bad = before;
        bad.workloads[0].cpu_cores = Some(f64::NAN);
        assert!(!bad.valid());
    }
    fn io(rate: f64) -> IoSample {
        IoSample {
            state: IoState::Available,
            read_bytes_per_second: Some(0.),
            write_bytes_per_second: Some(rate),
            interval_seconds: Some(1.),
            sampled_monotonic_ms: Some(1000),
            reason: None,
        }
    }
    fn telemetry(rows: Vec<Workload>) -> Telemetry {
        Telemetry {
            schema: 1,
            nonce: "a".repeat(16),
            uid: 10158,
            session: SessionId::new(1).unwrap(),
            epoch: "b".repeat(32),
            sequence: SampleSeq::new(1).unwrap(),
            sampled_monotonic_ms: 1000,
            boot: Some("00000000-0000-0000-0000-000000000001".into()),
            capability: Capability::Available,
            interval_seconds: Some(1.),
            scan_seconds: 0.001,
            registrations: vec![Registration {
                terminal_id: TerminalId::new(1).unwrap(),
                pid: 123,
                start: "100".into(),
                tty: "/dev/pts/1".into(),
            }],
            workloads: rows,
        }
    }
    #[test]
    fn io_channel_is_independent_of_cpu_baseline_and_cpu_survives_io_partial() {
        let mut activity = Activity::default();
        let mut row = workload(None, "shell:1:123:100");
        row.io = Some(io(1048576.));
        activity.observe("epoch", "boot", &row, None, Duration::ZERO);
        let value = activity.snapshot(Duration::ZERO);
        assert!(value.stale);
        assert_eq!(value.io.bytes_per_second, Some(1048576.));
        row.cpu_cores = Some(2.);
        row.coverage = Coverage::Complete;
        row.reason = None;
        row.io = Some(IoSample {
            state: IoState::Partial,
            read_bytes_per_second: None,
            write_bytes_per_second: None,
            interval_seconds: None,
            sampled_monotonic_ms: None,
            reason: Some("zombie_pending".into()),
        });
        activity.observe("epoch", "boot", &row, Some(1.), Duration::from_secs(1));
        let value = activity.snapshot(Duration::from_secs(1));
        assert!(!value.stale);
        assert_eq!(value.rss_bytes, Some(4096));
        assert_eq!(value.io.state, IoState::Partial);
        assert_eq!(value.io.bytes_per_second, None);
    }
    #[test]
    fn io_zero_stale_disconnect_identity_and_disabled_are_distinct() {
        let mut activity = Activity::default();
        let mut row = workload(Some(0.), "shell:1:123:100");
        row.io = Some(io(0.));
        activity.observe("epoch", "boot", &row, Some(1.), Duration::from_secs(1));
        assert_eq!(
            activity
                .snapshot(Duration::from_secs(1))
                .io
                .bytes_per_second,
            Some(0.)
        );
        assert_eq!(activity.snapshot(Duration::ZERO).io.state, IoState::Stale);
        assert_eq!(
            activity
                .snapshot(Duration::from_secs(5))
                .io
                .bytes_per_second,
            None
        );
        activity.missing();
        assert_eq!(
            activity.snapshot(Duration::from_secs(1)).io.state,
            IoState::Stale
        );
        row.io = None;
        activity.observe("new", "boot", &row, Some(1.), Duration::from_secs(6));
        assert_eq!(
            activity.snapshot(Duration::from_secs(6)).io.state,
            IoState::Unavailable
        );
        assert_eq!(IoDisplay::stopped().state, IoState::Stopped);
        assert!(IoDisplay::stopped().bytes_per_second.is_none());
    }
    #[test]
    fn io_wire_rejects_bad_numbers_wrong_state_and_old_or_future_time() {
        let mut row = workload(Some(1.), "shell:1:123:100");
        row.io = Some(io(0.));
        let value = telemetry(vec![row]);
        assert!(value.valid());
        for rate in [-1., f64::NAN, f64::INFINITY, f64::MAX] {
            let mut bad = value.clone();
            bad.workloads[0].io.as_mut().unwrap().write_bytes_per_second = Some(rate);
            assert!(!bad.valid());
        }
        for dt in [-1., 0., 3.1, f64::NAN] {
            let mut bad = value.clone();
            bad.workloads[0].io.as_mut().unwrap().interval_seconds = Some(dt);
            assert!(!bad.valid());
        }
        let mut bad = value.clone();
        bad.workloads[0].io.as_mut().unwrap().read_bytes_per_second = None;
        assert!(!bad.valid());
        let mut bad = value.clone();
        bad.workloads[0].io.as_mut().unwrap().state = IoState::Partial;
        assert!(!bad.valid());
        let mut bad = value.clone();
        bad.workloads[0].io.as_mut().unwrap().sampled_monotonic_ms = Some(1001);
        assert!(!bad.valid());
        let mut bad = value.clone();
        bad.sampled_monotonic_ms = 4001;
        assert!(!bad.valid());
        let mut json = serde_json::to_value(value).unwrap();
        json["workloads"][0]["io"]["path"] = serde_json::json!("not_allowed");
        assert!(serde_json::from_value::<Telemetry>(json).is_err());
    }
    #[test]
    fn io_cannot_bypass_registered_scope_when_cpu_is_partial() {
        let mut row = workload(None, "shell:1:123:100");
        row.io = Some(io(1.));
        let value = telemetry(vec![row]);
        assert!(value.valid());
        let mut bad = value.clone();
        bad.registrations.clear();
        assert!(!bad.valid());
        let mut bad = value.clone();
        bad.workloads[0].workload_id = "shell:1:999:100".into();
        assert!(!bad.valid());
        let mut bad = value;
        bad.registrations[0].start = "101".into();
        assert!(!bad.valid());
    }
    #[test]
    fn old_helper_omission_is_unknown_and_shared_tmux_rows_require_equal_io() {
        let row = workload(Some(1.), "shell:1:123:100");
        let mut json = serde_json::to_value(telemetry(vec![row])).unwrap();
        json["workloads"][0].as_object_mut().unwrap().remove("io");
        let old: Telemetry = serde_json::from_value(json).unwrap();
        assert!(old.valid());
        assert!(old.workloads[0].io.is_none());
        let mut value = old;
        value.workloads[0].workload_id = "tmux:987:200:$1".into();
        value.workloads[0].scope = Scope::TmuxSession;
        value.workloads[0].io = Some(io(10.));
        let mut duplicate = value.workloads[0].clone();
        duplicate.terminal_id = TerminalId::new(2).unwrap();
        value.workloads.push(duplicate);
        value.registrations.push(Registration {
            terminal_id: TerminalId::new(2).unwrap(),
            pid: 456,
            start: "300".into(),
            tty: "/dev/pts/2".into(),
        });
        assert!(value.valid());
        value.workloads[1]
            .io
            .as_mut()
            .unwrap()
            .write_bytes_per_second = Some(11.);
        assert!(!value.valid());
    }
}
