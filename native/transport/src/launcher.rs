//! 固定launcherの短いJSON lineとheartbeatだけを扱う。任意shell/pathを受けない。
use crate::forward_probe::valid_nonce;
use eidolon_model::{
    ids::{CommandSeq, SessionId, TerminalId},
    workload::Telemetry,
};
use russh::{Channel, ChannelMsg, client};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::time::{Duration, Instant, sleep_until, timeout};
use tokio_util::sync::CancellationToken;

const HOME: &str = "/data/data/com.termux/files/home";
const PYTHON: &str = "/data/data/com.termux/files/usr/bin/python3";
const SCRIPT_PATH: &str = "/data/data/com.termux/files/home/.local/lib/eidolon/eidolon-session.py";
const SCRIPT: &[u8] = include_bytes!("../../../scripts/termux/eidolon-session.py");
const LINE_BYTES: usize = 16384;

pub fn command(mode: &str, nonce: &str) -> Result<String, &'static str> {
    if !matches!(
        mode,
        "prepare" | "echo" | "pattern" | "foot" | "foot_fixture" | "cleanup"
    ) || !valid_nonce(nonce)
    {
        return Err("INVALID_ARGUMENT");
    }
    Ok(format!("'{PYTHON}' '{SCRIPT_PATH}' '{mode}' '{nonce}'"))
}
pub fn remote_path(nonce: &str) -> Result<String, &'static str> {
    if !valid_nonce(nonce) {
        return Err("INVALID_ARGUMENT");
    }
    Ok(format!("{HOME}/.eidolon/s-{nonce}/p"))
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Identity {
    pub pid: u32,
    pub start: String,
    pub group: u32,
    pub state: String,
}
impl Identity {
    pub(crate) fn valid(&self) -> bool {
        self.pid > 0
            && self.group == self.pid
            && !self.start.is_empty()
            && self.start.len() <= 32
            && self.start.bytes().all(|b| b.is_ascii_digit())
            && self.state.len() == 1
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Metrics {
    pub bytes: u64,
    pub truncated: bool,
    pub retained: usize,
}
impl Metrics {
    fn valid(&self) -> bool {
        self.retained == self.bytes.min(65536) as usize && self.truncated == (self.bytes > 65536)
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Copy {
    pub size: u64,
    pub bytes: u64,
    pub sha256: String,
    pub tail_matched: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Echo {
    pub schema: u32,
    pub result: String,
    pub uid: u32,
    pub channels: usize,
    pub bytes: u64,
    pub copies: Vec<Copy>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WaypipeTools {
    pub version: String,
    pub features: Vec<String>,
    pub unavailable: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FixtureTools {
    pub schema: u32,
    pub fixture_revision: String,
    pub frames: u32,
    pub sha256: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FootTools {
    pub version: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Tools {
    pub schema: u32,
    pub nonce: String,
    pub uid: u32,
    pub mode: String,
    pub waypipe: WaypipeTools,
    #[serde(default)]
    pub fixture: Option<FixtureTools>,
    #[serde(default)]
    pub foot: Option<FootTools>,
}
impl Tools {
    fn valid(&self) -> bool {
        self.schema == 1
            && self.waypipe.version == "0.11.3"
            && self.waypipe.features == ["lz4", "zstd"]
            && self.waypipe.unavailable == ["dmabuf", "video", "vaapi"]
            && match self.mode.as_str() {
                "pattern" => {
                    self.foot.is_none()
                        && self.fixture.as_ref().is_some_and(|f| {
                            f.schema == 1
                                && f.fixture_revision == "eidolon-shm-client-v1"
                                && f.frames == 100
                                && f.sha256.len() == 64
                                && f.sha256
                                    .bytes()
                                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                        })
                }
                "foot" | "foot_fixture" => {
                    self.fixture.is_none()
                        && self.foot.as_ref().is_some_and(|f| f.version == "1.28.0")
                }
                _ => false,
            }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Pattern {
    pub schema: u32,
    pub result: String,
    pub fixture_revision: String,
    pub uid: u32,
    pub protocol: serde_json::Value,
}
impl Pattern {
    fn valid(&self) -> bool {
        self.schema == 1
            && self.result == "pass"
            && self.fixture_revision == "eidolon-shm-client-v1"
            && self.protocol["protocol_error"] == false
            && ["frames", "callbacks", "releases"]
                .iter()
                .all(|key| self.protocol[key].as_u64() == Some(100))
            && ["configures", "acks"]
                .iter()
                .all(|key| self.protocol[key].as_u64().is_some_and(|n| n > 0))
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Finished {
    pub schema: u32,
    pub nonce: String,
    pub uid: u32,
    pub reason: String,
    pub child: Option<Identity>,
    pub exit_status: Option<u32>,
    pub exit_signal: Option<u32>,
    pub term_sent: bool,
    pub kill_sent: bool,
    pub cleaned: bool,
    pub group_alive: bool,
    pub pipes_closed: bool,
    pub stdout: Metrics,
    pub stderr: Metrics,
    #[serde(default)]
    pub echo: Option<Echo>,
    #[serde(default)]
    pub pattern: Option<Pattern>,
    #[serde(default)]
    pub error: Option<String>,
}
impl Finished {
    fn valid(&self) -> bool {
        matches!(
            self.reason.as_str(),
            "Completed"
                | "Stop"
                | "EOF"
                | "LeaseExpired"
                | "ControlInvalid"
                | "CleanupTimeout"
                | "IOFailed"
                | "Signal"
        ) && self.error.as_ref().is_none_or(|e| {
            matches!(
                e.as_str(),
                "CONTROL_INVALID"
                    | "DEPENDENCY_NOT_READY"
                    | "CHILD_SPAWN_FAILED"
                    | "CLEANUP_FAILED"
            )
        }) && self.child.as_ref().is_none_or(Identity::valid)
            && self.stdout.valid()
            && self.stderr.valid()
            && self.echo.as_ref().is_none_or(|e| {
                e.schema == 1
                    && e.result == "pass"
                    && e.channels == 6
                    && e.copies.len() == 6
                    && e.copies.iter().all(|c| {
                        c.sha256.len() == 64
                            && c.sha256
                                .bytes()
                                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                    })
            })
            && self
                .pattern
                .as_ref()
                .is_none_or(|p| p.uid == self.uid && p.valid())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Prepared {
    schema: u32,
    nonce: String,
    uid: u32,
    socket: String,
    launcher_sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Started {
    schema: u32,
    nonce: String,
    uid: u32,
    child: Identity,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TerminalOpened {
    pub schema: u32,
    pub nonce: String,
    pub uid: u32,
    pub request_id: Option<CommandSeq>,
    pub success: bool,
    pub ordinal: u32,
}
#[derive(Clone, Debug, Serialize)]
pub struct TerminalLaunch {
    pub terminal_id: TerminalId,
    pub request_id: Option<CommandSeq>,
    pub accepted: Option<bool>,
}
#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum Frame {
    Prepared(Prepared),
    Tools(Tools),
    ChildStarted(Started),
    TerminalOpened(TerminalOpened),
    #[serde(rename = "workload_v1")]
    WorkloadV1(Telemetry),
    Finished(Finished),
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub phase: &'static str,
    pub terminal_count: u32,
    pub terminal_pending: Option<CommandSeq>,
    pub last_terminal: Option<TerminalOpened>,
    pub terminal_launches: Vec<TerminalLaunch>,
    pub workload: Option<Telemetry>,
    pub tools: Option<Tools>,
    pub child: Option<Identity>,
    pub finished: Option<Finished>,
    pub stdout_bytes: u64,
    pub stderr_bytes: u64,
    pub eof: bool,
    pub exit_status: Option<u32>,
    pub exit_signal: bool,
    pub cleanup_pending: bool,
    pub error: Option<&'static str>,
}
pub struct Launcher {
    terminal_session: Option<SessionId>,
    next_terminal: Option<TerminalId>,
    expected_tools: Option<String>,
    channel: Option<Channel<client::Msg>>,
    nonce: String,
    uid: u32,
    expected_socket: String,
    beat_every: Duration,
    heartbeats: bool,
    cancel: CancellationToken,
    heartbeat: Instant,
    stopping: Option<Instant>,
    deadline: Instant,
    pending: Vec<u8>,
    pub report: Report,
}
impl Launcher {
    pub(crate) async fn open_recorded<H: client::Handler>(
        client: &client::Handle<H>,
        nonce: &str,
        uid: u32,
        cancel: CancellationToken,
        before_exec: impl FnOnce() -> Result<(), &'static str>,
    ) -> Result<Self, &'static str> {
        let text = command("prepare", nonce)?;
        let channel = tokio::select! {biased;
            _=cancel.cancelled()=>return Err("CANCELLED"),
            channel=timeout(Duration::from_secs(5),client.channel_open_session())=>channel,
        }
        .map_err(|_| "EXEC_TIMEOUT")?
        .map_err(|_| "SSH_IO_FAILED")?;
        if cancel.is_cancelled() {
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
            return Err("CANCELLED");
        }
        if let Err(error) = before_exec() {
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
            return Err(error);
        }
        // 永続化後はprepareを送ってからcancelを処理する。所有者にEOF/Stop回収を任せる。
        let exec = timeout(Duration::from_secs(2), channel.exec(true, text)).await;
        if !matches!(exec, Ok(Ok(()))) {
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
            return Err("SSH_IO_FAILED");
        }
        Self::from_channel(channel, nonce, uid, cancel)
    }
    fn from_channel(
        channel: Channel<client::Msg>,
        nonce: &str,
        uid: u32,
        cancel: CancellationToken,
    ) -> Result<Self, &'static str> {
        Ok(Self {
            terminal_session: None,
            next_terminal: Some(TerminalId::new(1).unwrap()),
            expected_tools: None,
            expected_socket: remote_path(nonce)?,
            beat_every: Duration::from_secs(10),
            heartbeats: true,
            channel: Some(channel),
            nonce: nonce.into(),
            uid,
            cancel,
            heartbeat: Instant::now() + Duration::from_secs(10),
            stopping: None,
            deadline: Instant::now() + Duration::from_secs(5),
            pending: Vec::new(),
            report: Report {
                phase: "preparing",
                cleanup_pending: true,
                ..Default::default()
            },
        })
    }
    pub async fn start_echo(&mut self) -> Result<(), &'static str> {
        if self.report.phase != "prepared" || self.stopping.is_some() {
            return Err("NOT_READY");
        }
        self.control("start", Some("echo")).await?;
        self.report.phase = "starting";
        self.deadline = Instant::now() + Duration::from_secs(70);
        Ok(())
    }
    /// remote helperの版を照合してからforward/startへ進む。Echo経路には要求しない。
    pub async fn inspect_tools(&mut self, mode: &str) -> Result<Tools, &'static str> {
        if self.report.phase != "prepared"
            || self.stopping.is_some()
            || self.report.tools.is_some()
            || self.expected_tools.is_some()
            || !matches!(mode, "pattern" | "foot" | "foot_fixture")
        {
            return Err("NOT_READY");
        }
        self.expected_tools = Some(mode.into());
        self.deadline = Instant::now() + Duration::from_secs(12);
        self.control("inspect_tools", Some(mode)).await?;
        while self.report.tools.is_none() {
            self.next().await?;
            if self.report.phase == "finished" {
                return Err("DEPENDENCY_NOT_READY");
            }
        }
        let tools = self.report.tools.clone().ok_or("DEPENDENCY_NOT_READY")?;
        if tools.mode != mode {
            return Err("PROTOCOL_ERROR");
        }
        Ok(tools)
    }
    pub async fn start_app(&mut self, mode: &str, session: SessionId) -> Result<(), &'static str> {
        if self.report.phase != "prepared"
            || self.stopping.is_some()
            || !matches!(mode, "pattern" | "foot" | "foot_fixture")
            || self.report.tools.as_ref().is_none_or(|t| t.mode != mode)
        {
            return Err("NOT_READY");
        }
        if matches!(mode, "foot" | "foot_fixture") {
            let terminal = self.allocate_terminal(None)?;
            self.terminal_session = Some(session);
            self.send_frame(
                serde_json::json!({"type":"start_terminal_v1","schema":1,"nonce":self.nonce,
                "mode":mode,"session":session,"terminal_id":terminal}),
            )
            .await?;
        } else {
            self.control("start", Some(mode)).await?;
        }
        self.report.phase = "starting";
        self.deadline = Instant::now() + Duration::from_secs(70);
        Ok(())
    }
    pub async fn open_terminal(&mut self, request: CommandSeq) -> Result<(), &'static str> {
        if self.report.phase != "running"
            || self.stopping.is_some()
            || self.report.terminal_count == 0
            || self.report.terminal_pending.is_some()
            || self
                .report
                .tools
                .as_ref()
                .is_none_or(|t| !matches!(t.mode.as_str(), "foot" | "foot_fixture"))
        {
            return Err("NOT_READY");
        }
        let terminal = self.allocate_terminal(Some(request))?;
        let session = self.terminal_session.ok_or("NOT_READY")?;
        self.send_frame(
            serde_json::json!({"type":"open_terminal_v1","schema":1,"nonce":self.nonce,"request_id":request,
                              "session":session,"terminal_id":terminal}),
        )
        .await?;
        self.report.terminal_pending = Some(request);
        Ok(())
    }
    fn allocate_terminal(
        &mut self,
        request: Option<CommandSeq>,
    ) -> Result<TerminalId, &'static str> {
        let terminal = self.next_terminal.ok_or("TERMINAL_ID_EXHAUSTED")?;
        self.next_terminal = terminal.checked_next();
        if self.report.terminal_launches.len() == 64 {
            self.report.terminal_launches.remove(0);
        }
        self.report.terminal_launches.push(TerminalLaunch {
            terminal_id: terminal,
            request_id: request,
            accepted: None,
        });
        Ok(terminal)
    }
    pub(crate) fn suppress_heartbeat(&mut self) {
        self.heartbeats = false;
    }
    pub(crate) async fn control_eof(&mut self) -> Result<(), &'static str> {
        self.stopping = Some(Instant::now());
        self.deadline = Instant::now() + Duration::from_secs(8);
        timeout(
            Duration::from_secs(2),
            self.channel.as_ref().ok_or("STALE")?.eof(),
        )
        .await
        .map_err(|_| "SSH_IO_TIMEOUT")?
        .map_err(|_| "SSH_IO_FAILED")
    }
    async fn control(&self, kind: &str, mode: Option<&str>) -> Result<(), &'static str> {
        let mut value = serde_json::json!({"type":kind,"nonce":self.nonce});
        if let Some(mode) = mode {
            value["mode"] = mode.into();
        }
        self.send_frame(value).await
    }
    async fn send_frame(&self, value: serde_json::Value) -> Result<(), &'static str> {
        let bytes = format!("{value}\n").into_bytes();
        timeout(
            Duration::from_secs(2),
            self.channel.as_ref().ok_or("STALE")?.data_bytes(bytes),
        )
        .await
        .map_err(|_| "SSH_IO_TIMEOUT")?
        .map_err(|_| "SSH_IO_FAILED")
    }
    pub async fn request_stop(&mut self) -> Result<(), &'static str> {
        if self.stopping.is_none() && self.report.phase != "finished" {
            self.stopping = Some(Instant::now());
            self.deadline = Instant::now() + Duration::from_secs(8);
            if self.report.finished.is_none() {
                self.control("stop", None).await?;
            }
        }
        Ok(())
    }
    /// SSH channelを読み続けるcallerだけがheartbeatを送る。UI/coreではawaitしない。
    pub async fn next(&mut self) -> Result<Report, &'static str> {
        loop {
            if self.cancel.is_cancelled() {
                self.request_stop().await?;
            }
            let message = tokio::select! {
                biased;
                _=self.cancel.cancelled(),if self.stopping.is_none()=>{self.request_stop().await?;continue;},
                _=sleep_until(self.deadline)=>{self.report.error=Some("LAUNCHER_TIMEOUT");return Err("LAUNCHER_TIMEOUT");},
                _=sleep_until(self.heartbeat),if self.stopping.is_none() && self.heartbeats=>{
                    self.control("heartbeat",None).await?;
                    self.heartbeat=Instant::now()+self.beat_every;
                    // quietなterminalはstdoutを送らない。正常な制御送信で稼働中appの監視期限を延長する。
                    if self.report.phase == "running" && self.report.tools.as_ref().is_some_and(|t| matches!(t.mode.as_str(), "foot" | "foot_fixture")) {
                        self.deadline=Instant::now()+Duration::from_secs(70);
                    }
                    continue;
                },
                message=self.channel.as_mut().ok_or("STALE")?.wait()=>message,
            };
            match message {
                Some(ChannelMsg::Data { data }) => {
                    self.report.stdout_bytes =
                        self.report.stdout_bytes.saturating_add(data.len() as u64);
                    for byte in data {
                        if byte == b'\n' {
                            let line = std::mem::take(&mut self.pending);
                            self.frame(&line)?;
                        } else {
                            if self.pending.len() >= LINE_BYTES {
                                return Err("PROTOCOL_ERROR");
                            }
                            self.pending.push(byte);
                        }
                    }
                    return Ok(self.report.clone());
                }
                Some(ChannelMsg::ExtendedData { data, ext: 1 }) => {
                    self.report.stderr_bytes =
                        self.report.stderr_bytes.saturating_add(data.len() as u64);
                }
                Some(ChannelMsg::Eof) => self.report.eof = true,
                Some(ChannelMsg::ExitStatus { exit_status }) => {
                    self.report.exit_status = Some(exit_status)
                }
                Some(ChannelMsg::ExitSignal { .. }) => self.report.exit_signal = true,
                None | Some(ChannelMsg::Close) => {
                    if !self.pending.is_empty()
                        || !self.report.eof
                        || self.report.exit_status != Some(0)
                        || self.report.exit_signal
                        || self.report.finished.is_none()
                    {
                        return Err("REMOTE_EXIT");
                    }
                    self.report.phase = "finished";
                    self.report.cleanup_pending = !self
                        .report
                        .finished
                        .as_ref()
                        .is_some_and(|r| r.cleaned && !r.group_alive && r.pipes_closed);
                    self.close().await;
                    return Ok(self.report.clone());
                }
                Some(ChannelMsg::Failure | ChannelMsg::ExtendedData { .. }) => {
                    return Err("PROTOCOL_ERROR");
                }
                _ => (),
            }
        }
    }
    fn frame(&mut self, line: &[u8]) -> Result<(), &'static str> {
        let frame: Frame = serde_json::from_slice(line).map_err(|_| "PROTOCOL_ERROR")?;
        match frame {
            Frame::Prepared(value) => {
                if self.report.phase != "preparing"
                    || value.schema != 1
                    || value.nonce != self.nonce
                    || value.uid != self.uid
                    || value.socket != self.expected_socket
                    || value.launcher_sha256 != script_hash()
                {
                    return Err("PROTOCOL_ERROR");
                }
                self.report.phase = "prepared";
                if self.stopping.is_none() {
                    self.deadline = Instant::now() + Duration::from_secs(60);
                }
            }
            Frame::ChildStarted(value) => {
                if self.report.phase != "starting"
                    || value.schema != 1
                    || value.nonce != self.nonce
                    || value.uid != self.uid
                    || !value.child.valid()
                {
                    return Err("PROTOCOL_ERROR");
                }
                self.report.child = Some(value.child);
                self.report.phase = "running";
            }
            Frame::TerminalOpened(value) => {
                let initial = value.request_id.is_none() && self.report.terminal_count == 0;
                if value.schema != 1
                    || value.nonce != self.nonce
                    || value.uid != self.uid
                    || !matches!(self.report.phase, "running" | "finishing")
                    || (!initial
                        && (value.request_id.is_none()
                            || value.request_id != self.report.terminal_pending))
                    || value.ordinal != self.report.terminal_count + u32::from(value.success)
                {
                    return Err("PROTOCOL_ERROR");
                }
                self.report.terminal_count = value.ordinal;
                self.report.terminal_pending = None;
                if self.terminal_session.is_some() {
                    let launch = self
                        .report
                        .terminal_launches
                        .iter_mut()
                        .rev()
                        .find(|launch| {
                            launch.request_id == value.request_id && launch.accepted.is_none()
                        })
                        .ok_or("PROTOCOL_ERROR")?;
                    launch.accepted = Some(value.success);
                }
                self.report.last_terminal = Some(value);
            }
            Frame::WorkloadV1(value) => {
                if !matches!(self.report.phase, "starting" | "running" | "finishing")
                    || Some(value.session) != self.terminal_session
                    || value.nonce != self.nonce
                    || value.uid != self.uid
                    || !value.valid()
                    || self
                        .report
                        .workload
                        .as_ref()
                        .is_some_and(|before| !value.follows(before))
                    || value
                        .registrations
                        .iter()
                        .map(|r| r.terminal_id)
                        .chain(value.workloads.iter().map(|w| w.terminal_id))
                        .any(|id| {
                            self.next_terminal.is_some_and(|next| id >= next)
                                || self.report.terminal_launches.iter().any(|launch| {
                                    launch.terminal_id == id && launch.accepted == Some(false)
                                })
                        })
                {
                    return Err("PROTOCOL_ERROR");
                }
                self.report.workload = Some(value);
            }
            Frame::Tools(value) => {
                if self.report.phase != "prepared"
                    || self.report.tools.is_some()
                    || self.stopping.is_some()
                    || self.expected_tools.as_deref() != Some(value.mode.as_str())
                    || value.nonce != self.nonce
                    || value.uid != self.uid
                    || !value.valid()
                {
                    return Err("PROTOCOL_ERROR");
                }
                self.expected_tools = None;
                self.report.tools = Some(value);
                self.deadline = Instant::now() + Duration::from_secs(60);
            }
            Frame::Finished(value) => {
                if value.schema != 1
                    || !value.valid()
                    || value.nonce != self.nonce
                    || value.uid != self.uid
                    || value.child != self.report.child
                    || self.report.finished.is_some()
                    || value.stdout.retained > 65536
                    || value.stderr.retained > 65536
                    || value.reason.len() > 64
                    || value.error.as_ref().is_some_and(|e| e.len() > 64)
                    || value.echo.as_ref().is_some_and(|e| {
                        e.copies.len() > 6 || e.copies.iter().any(|c| c.sha256.len() != 64)
                    })
                {
                    return Err("PROTOCOL_ERROR");
                }
                self.report.finished = Some(value);
                self.report.phase = "finishing";
                self.deadline = Instant::now() + Duration::from_secs(3);
            }
        }
        Ok(())
    }
    pub async fn finish(&mut self) -> Result<Report, &'static str> {
        while self.report.phase != "finished" {
            self.next().await?;
        }
        Ok(self.report.clone())
    }
    async fn close(&mut self) {
        if let Some(channel) = self.channel.take() {
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
        }
    }
}
impl Drop for Launcher {
    fn drop(&mut self) {
        if let Some(channel) = self.channel.take() {
            tokio::spawn(async move {
                let _ = timeout(Duration::from_secs(1), channel.close()).await;
            });
        }
    }
}
fn script_hash() -> String {
    Sha256::digest(SCRIPT)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Audit {
    pub verified: bool,
    pub active: bool,
    pub orphaned: bool,
    pub child_alive: bool,
    pub receipt: Option<Finished>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AuditWire {
    schema: u32,
    cleaned: bool,
    #[serde(default)]
    already_absent: bool,
    #[serde(default)]
    active: bool,
    #[serde(default)]
    orphaned: bool,
    #[serde(default)]
    child_alive: bool,
    #[serde(default)]
    pending: bool,
    #[serde(default)]
    receipt: Option<Frame>,
}
/// 新しい検証済みSSH接続から旧nonceを照合する。保存PIDへsignalする操作はない。
pub async fn audit<H: client::Handler>(
    client: &client::Handle<H>,
    nonce: &str,
    uid: u32,
    child: Option<&Identity>,
) -> Result<Audit, &'static str> {
    let command = command("cleanup", nonce)?;
    let value = timeout(Duration::from_secs(3), async {
        let channel = client
            .channel_open_session()
            .await
            .map_err(|_| "SSH_IO_FAILED")?;
        crate::exec::json(channel, command).await
    })
    .await
    .map_err(|_| "EXEC_TIMEOUT")??;
    let wire: AuditWire = serde_json::from_value(value).map_err(|_| "PROTOCOL_ERROR")?;
    if wire.schema != 1 {
        return Err("PROTOCOL_ERROR");
    }
    let receipt = match wire.receipt {
        Some(Frame::Finished(value)) => Some(value),
        None => None,
        _ => return Err("PROTOCOL_ERROR"),
    };
    if let Some(receipt) = &receipt
        && (!receipt.valid()
            || receipt.schema != 1
            || receipt.nonce != nonce
            || receipt.uid != uid
            || child.is_some_and(|child| receipt.child.as_ref() != Some(child)))
    {
        return Err("PROTOCOL_ERROR");
    }
    let verified = wire.cleaned
        && wire.already_absent
        && !wire.active
        && !wire.orphaned
        && !wire.child_alive
        && !wire.pending
        && receipt
            .as_ref()
            .is_some_and(|r| r.cleaned && !r.group_alive && r.pipes_closed);
    Ok(Audit {
        verified,
        active: wire.active,
        orphaned: wire.orphaned,
        child_alive: wire.child_alive,
        receipt,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn commands_are_fixed_and_process_identity_is_bounded() {
        assert!(command("shell", &"a".repeat(16)).is_err());
        assert!(command("prepare", "../bad").is_err());
        assert!(
            command("cleanup", &"a".repeat(16))
                .unwrap()
                .ends_with("'cleanup' 'aaaaaaaaaaaaaaaa'")
        );
        let mut identity = Identity {
            pid: 42,
            group: 42,
            start: "123".into(),
            state: "S".into(),
        };
        assert!(identity.valid());
        identity.group = 43;
        assert!(!identity.valid());
    }

    fn quote(text: &str) -> String {
        format!("'{}'", text.replace('\'', "'\\''"))
    }
    async fn fixture(
        child: &str,
        lease_seconds: f64,
    ) -> (crate::forward::openssh::OpenSsh, Launcher) {
        use std::os::unix::fs::MetadataExt;
        let ssh = crate::forward::openssh::OpenSsh::new().await;
        let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../scripts/termux/eidolon-session.py")
            .canonicalize()
            .unwrap();
        let nonce = "bbbbbbbbbbbbbbbb";
        let code = format!(
            "import runpy,json,sys,pathlib;m=runpy.run_path({});m['supervise'](pathlib.Path({}),{},0,lambda v:print(json.dumps(v,separators=(',',':')),flush=True),lease_seconds={},grace_seconds=.15,factory=lambda mode,token:[sys.executable,'-c',{}])",
            serde_json::to_string(script.to_str().unwrap()).unwrap(),
            serde_json::to_string(ssh.dir.to_str().unwrap()).unwrap(),
            serde_json::to_string(nonce).unwrap(),
            lease_seconds,
            serde_json::to_string(child).unwrap()
        );
        let channel = ssh.client.channel_open_session().await.unwrap();
        channel
            .exec(true, format!("/usr/bin/python3 -c {}", quote(&code)))
            .await
            .unwrap();
        let uid = std::fs::metadata(&ssh.dir).unwrap().uid();
        let mut owner =
            Launcher::from_channel(channel, nonce, uid, CancellationToken::new()).unwrap();
        owner.expected_socket = ssh
            .dir
            .join(format!(".eidolon/s-{nonce}/p"))
            .to_str()
            .unwrap()
            .into();
        (ssh, owner)
    }
    async fn prepared(owner: &mut Launcher) {
        while owner.report.phase != "prepared" {
            owner.next().await.unwrap();
        }
    }
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn workload_wire_is_separate_ordered_scoped_and_never_grants_child_ownership() {
        let (ssh, mut owner) = fixture("import time;time.sleep(30)", 5.).await;
        prepared(&mut owner).await;
        owner.terminal_session = Some(SessionId::new(17).unwrap());
        owner.allocate_terminal(None).unwrap();
        owner.report.phase = "starting";
        let value = serde_json::json!({"type":"workload_v1","schema":1,"nonce":owner.nonce,"uid":owner.uid,"session":"17",
            "epoch":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","sequence":"1","sampled_monotonic_ms":1000,
            "boot":"00000000-0000-0000-0000-000000000001","capability":"available","interval_seconds":1.,"scan_seconds":0.01,
            "registrations":[{"terminal_id":"1","pid":123,"start":"10","tty":"/dev/pts/1"}],
            "workloads":[{"terminal_id":"1","workload_id":"shell:1:123:10","scope":"shell_tree","cpu_cores":1.,"rss_bytes":4096,
                          "processes":1,"coverage":"complete","reason":null}]});
        owner.frame(&serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(owner.report.child.is_none());
        assert_eq!(owner.report.terminal_count, 0);
        assert!(owner.frame(&serde_json::to_vec(&value).unwrap()).is_err());
        for (field, bad) in [
            ("nonce", serde_json::json!("aaaaaaaaaaaaaaaa")),
            ("session", serde_json::json!("18")),
            ("uid", serde_json::json!(owner.uid + 1)),
            ("epoch", serde_json::json!("c".repeat(32))),
        ] {
            let mut modified = value.clone();
            modified["sequence"] = serde_json::json!("2");
            modified[field] = bad;
            assert!(
                owner
                    .frame(&serde_json::to_vec(&modified).unwrap())
                    .is_err()
            );
        }
        let mut unknown = value.clone();
        unknown["sequence"] = serde_json::json!("2");
        unknown["cmdline"] = serde_json::json!("not permitted");
        assert!(owner.frame(&serde_json::to_vec(&unknown).unwrap()).is_err());
        let receipt = serde_json::json!({"type":"terminal_opened","schema":1,"nonce":owner.nonce,"uid":owner.uid,
            "request_id":null,"success":true,"ordinal":1,"terminal_id":"1"});
        assert!(owner.frame(&serde_json::to_vec(&receipt).unwrap()).is_err());
        assert!(owner.report.child.is_none());
        owner.request_stop().await.unwrap();
        assert!(!owner.finish().await.unwrap().cleanup_pending);
        drop(owner);
        ssh.close().await;
    }
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn real_ssh_control_stop_waits_for_reap_eof_exit_and_cleanup_ack() {
        let (ssh, mut owner) = fixture("import time;time.sleep(30)", 5.0).await;
        prepared(&mut owner).await;
        owner.start_echo().await.unwrap();
        while owner.report.phase != "running" {
            owner.next().await.unwrap();
        }
        owner.cancel.cancel();
        let report = owner.finish().await.unwrap();
        assert!(!report.cleanup_pending);
        assert_eq!(report.finished.as_ref().unwrap().reason, "Stop");
        assert_eq!(report.finished.as_ref().unwrap().exit_signal, Some(15));
        assert!(report.finished.as_ref().unwrap().term_sent && report.eof);
        assert_eq!(report.exit_status, Some(0));
        assert!(!ssh.dir.join(".eidolon/s-bbbbbbbbbbbbbbbb").exists());
        drop(owner);
        ssh.close().await;
    }
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn real_ssh_heartbeat_keeps_lease_and_drains_flood_without_publishing_text() {
        let (ssh,mut owner)=fixture("import time,os;time.sleep(.5);os.write(1,b'fixture-private-text');[os.write(1,b'x'*65536) for _ in range(40)];[os.write(2,b'y'*65536) for _ in range(40)]",0.2).await;
        owner.beat_every = Duration::from_millis(40);
        owner.heartbeat = Instant::now() + owner.beat_every;
        prepared(&mut owner).await;
        owner.start_echo().await.unwrap();
        while owner.report.phase != "running" {
            owner.next().await.unwrap();
        }
        // 実SSHと固定helperで、footの監視期限だけを120msへ短縮して検査する。
        owner.report.tools=Some(serde_json::from_value(serde_json::json!({"schema":1,"nonce":"bbbbbbbbbbbbbbbb","uid":owner.uid,"mode":"foot","waypipe":{"version":"0.11.3","features":["lz4","zstd"],"unavailable":["dmabuf","video","vaapi"]},"foot":{"version":"1.28.0"}})).unwrap());
        owner.deadline = Instant::now() + Duration::from_millis(120);
        let report = owner.finish().await.unwrap();
        assert!(report.error.is_none());
        assert!(!report.cleanup_pending);
        let remote = report.finished.as_ref().unwrap();
        assert_eq!(remote.reason, "Completed");
        assert_eq!(remote.exit_status, Some(0));
        assert!(remote.stdout.truncated && remote.stderr.truncated);
        assert_eq!(remote.stderr.bytes, 40 * 65536);
        assert_eq!(remote.stdout.retained, 65536);
        assert!(
            !serde_json::to_string(&report)
                .unwrap()
                .contains("fixture-private-text")
        );
        drop(owner);
        ssh.close().await;
    }
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn real_ssh_missing_heartbeat_expires_lease_and_owned_child() {
        let (ssh, mut owner) = fixture("import time;time.sleep(30)", 0.2).await;
        prepared(&mut owner).await;
        owner.start_echo().await.unwrap();
        let report = owner.finish().await.unwrap();
        assert!(!report.cleanup_pending);
        assert_eq!(report.finished.as_ref().unwrap().reason, "LeaseExpired");
        assert_eq!(report.finished.as_ref().unwrap().exit_signal, Some(15));
        drop(owner);
        ssh.close().await;
    }
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn real_ssh_disconnect_has_no_local_ack_but_remote_owner_persists_reaped_receipt() {
        let (mut ssh, mut owner) = fixture("import time;time.sleep(30)", 60.0).await;
        prepared(&mut owner).await;
        owner.start_echo().await.unwrap();
        while owner.report.phase != "running" {
            owner.next().await.unwrap();
        }
        let child = owner.report.child.clone().unwrap();
        ssh.client
            .disconnect(russh::Disconnect::ByApplication, "fixed loss fixture", "en")
            .await
            .unwrap();
        assert!(owner.finish().await.is_err());
        assert!(owner.report.cleanup_pending);
        let _ = timeout(Duration::from_secs(2), &mut ssh.client).await;
        let path = ssh.dir.join(".eidolon/r-bbbbbbbbbbbbbbbb.json");
        timeout(Duration::from_secs(5), async {
            while !path.exists() {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        let receipt: serde_json::Value =
            serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        assert_eq!(receipt["cleaned"], true);
        assert_eq!(receipt["pipes_closed"], true);
        assert_eq!(receipt["group_alive"], false);
        assert_eq!(receipt["child"]["pid"], child.pid);
        assert_eq!(receipt["child"]["start"], child.start);
        assert!(!std::path::Path::new(&format!("/proc/{}", child.pid)).exists());
        drop(owner);
        drop(ssh);
    }
}
