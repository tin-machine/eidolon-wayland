use super::{Action, Binding, Event, Phase, Protocol, Selection, decimal, hex, protocol};
use russh::{Channel, ChannelMsg, client};
use serde::Serialize;
use tokio::time::{Duration, Instant, sleep_until, timeout};
use tokio_util::sync::CancellationToken;

const PREPARE_TIMEOUT: Duration = Duration::from_secs(10);
const ACK_TIMEOUT: Duration = Duration::from_secs(5);
const STOP_TIMEOUT: Duration = Duration::from_secs(8);
const CLOSE_TIMEOUT: Duration = Duration::from_secs(3);
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(5);
const MAX_LINE: usize = 16384;
const MAX_STDERR: u64 = 65536;

/// Fixed bootstrap shared with explicit receipt recovery. Only validated decimal,
/// hex and UUID arguments enter the shell command; remote paths are never argv.
pub(super) fn command(
    binding: &Binding,
    expected_boot_id: Option<&str>,
) -> Result<String, &'static str> {
    let pins = &binding.pins;
    if binding.uid == 0
        || !hex(&binding.nonce, 16)
        || !hex(&binding.profile_binding, 64)
        || decimal(&binding.profile_revision, false).is_none()
        || expected_boot_id.is_some_and(|boot| boot != "-" && !protocol::valid_boot_id(boot))
        || pins.schema != 1
        || pins.backend != "linux_desktop"
        || [
            &pins.base_bundle_sha256,
            &pins.desktop_bundle_sha256,
            &pins.waypipe_sha256,
            &pins.shm_fixture_sha256,
            &pins.desktop_binary_sha256,
        ]
        .iter()
        .any(|pin| !hex(pin, 64))
    {
        return Err("DESKTOP_BINDING_INVALID");
    }
    let hashes = pins.source_hashes();
    let mut args = vec![
        if expected_boot_id.is_some() {
            "recover".to_owned()
        } else {
            "serve".to_owned()
        },
        binding.session.get().to_string(),
        binding.profile_revision.clone(),
        binding.profile_binding.clone(),
        binding.nonce.clone(),
        binding.uid.to_string(),
        pins.base_bundle_sha256.clone(),
        pins.desktop_bundle_sha256.clone(),
        pins.waypipe_sha256.clone(),
        pins.shm_fixture_sha256.clone(),
        pins.desktop_binary_sha256.clone(),
    ];
    if let Some(boot) = expected_boot_id {
        args.push(boot.into());
    }
    let source = format!(
        r#"import os,pwd,stat,hashlib,types,sys
from pathlib import Path
assert os.getuid()==os.geteuid()=={} and os.getuid()!=0
h=Path(pwd.getpwuid(os.getuid()).pw_dir)
assert h.is_absolute() and h.resolve()==h
b=h/'.local/lib/eidolon-linux'
for p in (h,h/'.local',h/'.local/lib',b):
 s=p.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid==os.getuid() and not s.st_mode&0o022
assert stat.S_IMODE(b.stat().st_mode)==0o700
def read(p,limit):
 f=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 with os.fdopen(f,'rb') as stream:
  s=os.fstat(stream.fileno());assert stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=limit
  data=stream.read(limit+1);assert len(data)==s.st_size
  return data
assert hashlib.sha256(read(b/'bundle.json',16384)).hexdigest()=='{}'
assert hashlib.sha256(read(b/'desktop-bundle.json',16384)).hexdigest()=='{}'
hashes={}
files={{'base':'eidolon-linux-session.py','adapter':'eidolon_linux_desktop.py','owner':'eidolon_linux_process_owner.py','wire':'eidolon_linux_desktop_session.py'}}
sources={{}}
for name,file in files.items():
 source=read(b/file,131072);assert hashlib.sha256(source).hexdigest()==hashes[name]
 sources[name]=source
modules={{}}
for name,file in files.items():
 m=types.ModuleType('eidolon_pinned_'+name);m.__file__=str(b/file);sys.modules[m.__name__]=m
 exec(compile(sources[name],m.__file__,'exec'),m.__dict__);modules[name]=m
base=modules['base']
original=base.bundle
def pinned(home):
 value=original(home)
 assert value[2]=='{}' and value[1]['waypipe']=='{}' and value[1]['eidolon-shm-fixture']=='{}'
 return value
base.bundle=pinned
raise SystemExit(modules['wire'].main({},base=base,desktop=modules['adapter'],owner=modules['owner'],source_hashes=hashes))
"#,
        binding.uid,
        pins.base_bundle_sha256,
        pins.desktop_bundle_sha256,
        serde_json::json!({"base":hashes.base,"adapter":hashes.adapter,"owner":hashes.owner,"wire":hashes.wire,"demo":hashes.demo}),
        pins.base_bundle_sha256,
        pins.waypipe_sha256,
        pins.shm_fixture_sha256,
        serde_json::to_string(&args).map_err(|_| "DESKTOP_BINDING_INVALID")?,
    );
    Ok(format!(
        "/usr/bin/python3 -I -S -c '{}'",
        source.replace('\'', "'\\''")
    ))
}

/// Payloads, paths, boot IDs and the saved Profile binding remain private. Each
/// resource proof is independent; a terminal JSON event is not channel closure.
#[derive(Clone, Debug, Serialize)]
pub struct RemoteReport {
    pub phase: &'static str,
    pub started: bool,
    pub stdout_bytes: u64,
    pub stderr_bytes: u64,
    pub process_complete: bool,
    pub pipes_eof: bool,
    pub remote_cleaned: bool,
    pub receipt_written: bool,
    pub terminal_event: bool,
    pub eof: bool,
    pub exit_status: Option<u32>,
    pub exit_signal: bool,
    pub channel_closed: bool,
    pub protocol_valid: bool,
    pub reason: Option<&'static str>,
}
impl Default for RemoteReport {
    fn default() -> Self {
        Self {
            phase: "awaiting_prepared",
            started: false,
            stdout_bytes: 0,
            stderr_bytes: 0,
            process_complete: false,
            pipes_eof: false,
            remote_cleaned: false,
            receipt_written: false,
            terminal_event: false,
            eof: false,
            exit_status: None,
            exit_signal: false,
            channel_closed: false,
            protocol_valid: true,
            reason: None,
        }
    }
}

pub(super) struct Owner {
    channel: Option<Channel<client::Msg>>,
    protocol: Protocol,
    cancel: CancellationToken,
    deadline: Option<Instant>,
    heartbeat: Instant,
    stopping: bool,
    pending: Vec<u8>,
    boot_id: Option<String>,
    remote_socket: Option<String>,
    report: RemoteReport,
}
impl Owner {
    pub async fn open<H: client::Handler>(
        client: &client::Handle<H>,
        binding: &Binding,
        cancel: CancellationToken,
        before_exec: impl FnOnce() -> Result<(), &'static str>,
    ) -> Result<Self, &'static str> {
        let text = command(binding, None)?;
        let channel = tokio::select! {biased;
            _=cancel.cancelled()=>return Err("CANCELLED"),
            channel=timeout(Duration::from_secs(5),client.channel_open_session())=>channel,
        }
        .map_err(|_| "DESKTOP_EXEC_TIMEOUT")?
        .map_err(|_| "SSH_IO_FAILED")?;
        if cancel.is_cancelled() {
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
            return Err("CANCELLED");
        }
        if let Err(error) = before_exec() {
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
            return Err(error);
        }
        // From this point the journal must retain uncertainty, including an exec
        // enqueue timeout: neither the request nor Start is ever retransmitted.
        if !matches!(
            timeout(Duration::from_secs(2), channel.exec(true, text)).await,
            Ok(Ok(()))
        ) {
            let _ = timeout(Duration::from_secs(1), channel.eof()).await;
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
            return Err("SSH_IO_FAILED");
        }
        Ok(Self::from_channel(channel, binding.clone(), cancel))
    }

    pub(super) fn from_channel(
        channel: Channel<client::Msg>,
        binding: Binding,
        cancel: CancellationToken,
    ) -> Self {
        Self {
            channel: Some(channel),
            protocol: Protocol::new(binding),
            cancel,
            deadline: Some(Instant::now() + PREPARE_TIMEOUT),
            heartbeat: Instant::now() + HEARTBEAT_INTERVAL,
            stopping: false,
            pending: Vec::new(),
            boot_id: None,
            remote_socket: None,
            report: RemoteReport::default(),
        }
    }
    pub fn phase(&self) -> Phase {
        self.protocol.phase()
    }
    pub fn selection(&self) -> Option<Selection> {
        (!self.stopping)
            .then(|| self.protocol.selection())
            .flatten()
    }
    pub fn boot_id(&self) -> Option<&str> {
        self.boot_id.as_deref()
    }
    pub fn remote_socket(&self) -> Option<&str> {
        self.remote_socket.as_deref()
    }
    pub fn report(&self) -> RemoteReport {
        self.report.clone()
    }
    pub fn closed(&self) -> bool {
        self.report.channel_closed
    }
    pub fn terminal_remote_clean(&self) -> bool {
        self.phase() == Phase::Stopped
            && self.report.terminal_event
            && self.report.protocol_valid
            && self.report.process_complete
            && self.report.pipes_eof
            && self.report.remote_cleaned
            && self.report.receipt_written
    }
    pub fn channel_complete(&self) -> bool {
        self.report.channel_closed
            && self.report.eof
            && self.report.exit_status == Some(0)
            && !self.report.exit_signal
            && self.pending.is_empty()
            && self.report.protocol_valid
    }
    fn fail(&mut self, error: &'static str) -> &'static str {
        self.protocol.fail();
        self.report.phase = "failed";
        self.report.protocol_valid = false;
        self.deadline = None;
        error
    }
    pub(super) async fn control(
        &mut self,
        action: Action,
        selection: Option<&Selection>,
    ) -> Result<(), &'static str> {
        if self.channel.is_none() || self.closed() {
            return Err("STALE");
        }
        let bytes = self.protocol.command(action, selection)?;
        self.deadline = Some(
            Instant::now()
                + if action == Action::Stop {
                    STOP_TIMEOUT
                } else {
                    ACK_TIMEOUT
                },
        );
        let result = timeout(
            Duration::from_secs(2),
            self.channel.as_ref().ok_or("STALE")?.data_bytes(bytes),
        )
        .await;
        match result {
            Ok(Ok(())) => Ok(()),
            Ok(Err(_)) => Err(self.fail("SSH_IO_FAILED")),
            Err(_) => Err(self.fail("SSH_IO_TIMEOUT")),
        }
    }
    pub async fn inspect(&mut self) -> Result<(), &'static str> {
        if self.stopping || self.cancel.is_cancelled() {
            self.stopping = true;
            return Err("CANCELLED");
        }
        self.control(Action::Inspect, None).await
    }
    pub async fn start(&mut self, selection: &Selection) -> Result<(), &'static str> {
        if self.stopping || self.cancel.is_cancelled() {
            self.stopping = true;
            return Err("CANCELLED");
        }
        self.control(Action::Start, Some(selection)).await
    }
    /// Stop is queued behind the single outstanding request, with its original
    /// ACK deadline intact. A terminal event can retire that request instead.
    pub async fn stop(&mut self) -> Result<(), &'static str> {
        self.stopping = true;
        if self.protocol.in_flight().is_none()
            && matches!(
                self.phase(),
                Phase::Prepared | Phase::Catalog | Phase::Started
            )
        {
            self.control(Action::Stop, None).await?;
        }
        Ok(())
    }
    fn event(&mut self, event: Event) {
        self.report.phase = match self.phase() {
            Phase::AwaitingPrepared => "awaiting_prepared",
            Phase::Prepared => "prepared",
            Phase::Catalog => "catalog",
            Phase::Started => "started",
            Phase::Stopped => "stopped",
            Phase::Pending => "pending",
            Phase::Failed => "failed",
        };
        match event {
            Event::Prepared {
                boot_id,
                remote_socket,
                ..
            } => {
                self.boot_id = Some(boot_id);
                self.remote_socket = Some(remote_socket);
            }
            Event::Started { .. } => self.report.started = true,
            Event::Stopped {
                reason,
                process_complete,
                pipes_eof,
                remote_cleaned,
                receipt_written,
            } => {
                self.report.terminal_event = true;
                self.report.process_complete = process_complete;
                self.report.pipes_eof = pipes_eof;
                self.report.remote_cleaned = remote_cleaned;
                self.report.receipt_written = receipt_written;
                self.report.reason = Some(match reason {
                    protocol::StopReason::Stop => "stop",
                    protocol::StopReason::LeaseExpired => "lease_expired",
                    protocol::StopReason::InputEof => "input_eof",
                    protocol::StopReason::ProcessExited => "process_exited",
                });
            }
            Event::Pending {
                reason,
                process_complete,
                pipes_eof,
            } => {
                self.report.terminal_event = true;
                self.report.process_complete = process_complete;
                self.report.pipes_eof = pipes_eof;
                self.report.reason = Some(match reason {
                    protocol::FailureReason::ProcessOwnership => "process_ownership",
                    protocol::FailureReason::PipeEof => "pipe_eof",
                    protocol::FailureReason::DirectoryCleanup => "directory_cleanup",
                    protocol::FailureReason::ReceiptWrite => "receipt_write",
                    protocol::FailureReason::Protocol => "protocol",
                    protocol::FailureReason::ToolInspection => "tool_inspection",
                    protocol::FailureReason::Start => "start",
                });
            }
            _ => (),
        }
        // No total-runtime timer. The normal session stays alive as long as its
        // renewable lease and each individual request/ACK remain healthy.
        self.deadline = self
            .report
            .terminal_event
            .then(|| Instant::now() + CLOSE_TIMEOUT);
        self.heartbeat = Instant::now() + HEARTBEAT_INTERVAL;
    }
    fn data(&mut self, bytes: &[u8]) -> Result<(), &'static str> {
        if self.report.eof {
            return Err(self.fail("DESKTOP_PROTOCOL_INVALID"));
        }
        self.report.stdout_bytes = self.report.stdout_bytes.saturating_add(bytes.len() as u64);
        for &byte in bytes {
            if byte == b'\n' {
                let line = std::mem::take(&mut self.pending);
                let event = self
                    .protocol
                    .accept(&line)
                    .map_err(|error| self.fail(error))?;
                self.event(event);
            } else {
                if self.pending.len() >= MAX_LINE {
                    return Err(self.fail("DESKTOP_OUTPUT_LIMIT"));
                }
                self.pending.push(byte);
            }
        }
        Ok(())
    }
    pub async fn next(&mut self) -> Result<(), &'static str> {
        self.next_input(None).await.map(|_| ())
    }
    pub async fn next_launch(
        &mut self,
        requests: &mut tokio::sync::mpsc::Receiver<super::runtime::Launch>,
    ) -> Result<Option<super::runtime::Launch>, &'static str> {
        self.next_input(Some(requests)).await
    }
    /// The mailbox participates only in the channel/timer wait. Once a control
    /// command begins enqueueing it must finish before a UI command is observed.
    async fn next_input(
        &mut self,
        mut requests: Option<&mut tokio::sync::mpsc::Receiver<super::runtime::Launch>>,
    ) -> Result<Option<super::runtime::Launch>, &'static str> {
        if self.closed() || self.phase() == Phase::Failed {
            return Err("STALE");
        }
        loop {
            if self.stopping && self.protocol.in_flight().is_none() {
                self.stop().await?;
            }
            let deadline = self.deadline.unwrap_or(self.heartbeat);
            let message = tokio::select! {biased;
                _=self.cancel.cancelled(),if !self.stopping=>{self.stop().await?;continue;},
                _=sleep_until(deadline),if self.deadline.is_some()=>return Err(self.fail("DESKTOP_HELPER_TIMEOUT")),
                message=self.channel.as_mut().ok_or("STALE")?.wait()=>message,
                launch=async { requests.as_mut().unwrap().recv().await },if requests.is_some() && !self.stopping=>{
                    return launch.map(Some).ok_or("DESKTOP_COMMANDS_CLOSED");
                },
                _=sleep_until(self.heartbeat),if !self.stopping && !self.report.eof && !self.report.terminal_event && self.protocol.in_flight().is_none() && matches!(self.phase(), Phase::Prepared | Phase::Catalog | Phase::Started)=>{
                    self.control(Action::Heartbeat, None).await?;
                    continue;
                },
            };
            match message {
                Some(ChannelMsg::Data { data }) => {
                    self.data(&data)?;
                    return Ok(None);
                }
                Some(ChannelMsg::ExtendedData { data, ext: 1 }) => {
                    if self.report.eof {
                        return Err(self.fail("DESKTOP_PROTOCOL_INVALID"));
                    }
                    // Discard diagnostics immediately: no private remote text in
                    // snapshots, and no lifetime stdout cap on valid heartbeats.
                    self.report.stderr_bytes =
                        self.report.stderr_bytes.saturating_add(data.len() as u64);
                    if self.report.stderr_bytes > MAX_STDERR {
                        return Err(self.fail("DESKTOP_OUTPUT_LIMIT"));
                    }
                }
                Some(ChannelMsg::Eof) => {
                    if self.report.eof {
                        return Err(self.fail("DESKTOP_PROTOCOL_INVALID"));
                    }
                    self.report.eof = true;
                    self.deadline = Some(Instant::now() + CLOSE_TIMEOUT);
                }
                Some(ChannelMsg::ExitStatus { exit_status }) => {
                    if self.report.exit_status.replace(exit_status).is_some() {
                        return Err(self.fail("DESKTOP_PROTOCOL_INVALID"));
                    }
                    self.deadline = Some(Instant::now() + CLOSE_TIMEOUT);
                }
                Some(ChannelMsg::ExitSignal { .. }) => {
                    self.report.exit_signal = true;
                    self.deadline = Some(Instant::now() + CLOSE_TIMEOUT);
                }
                None | Some(ChannelMsg::Close) => {
                    self.report.channel_closed = true;
                    if !self.channel_complete() || !self.report.terminal_event {
                        return Err(self.fail("DESKTOP_HELPER_LOST"));
                    }
                    return Ok(None);
                }
                Some(ChannelMsg::Failure | ChannelMsg::ExtendedData { .. }) => {
                    return Err(self.fail("DESKTOP_PROTOCOL_INVALID"));
                }
                _ => (),
            }
        }
    }
    /// EOF/close requests are bounded best-effort teardown. Only observing the
    /// closed channel counts as channel_closed; it says nothing about children.
    pub async fn close(&mut self) -> bool {
        let Some(mut channel) = self.channel.take() else {
            return self.closed();
        };
        if !self.closed() {
            let _ = timeout(Duration::from_secs(1), channel.eof()).await;
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
            let deadline = Instant::now() + Duration::from_secs(1);
            while Instant::now() < deadline {
                match timeout(
                    deadline.saturating_duration_since(Instant::now()),
                    channel.wait(),
                )
                .await
                {
                    Ok(None | Some(ChannelMsg::Close)) => {
                        self.report.channel_closed = true;
                        break;
                    }
                    Ok(Some(ChannelMsg::Eof)) => self.report.eof = true,
                    Ok(Some(ChannelMsg::ExitStatus { exit_status })) => {
                        if self.report.exit_status.replace(exit_status).is_some() {
                            self.fail("DESKTOP_PROTOCOL_INVALID");
                        }
                    }
                    Ok(Some(ChannelMsg::ExitSignal { .. })) => self.report.exit_signal = true,
                    Ok(Some(
                        ChannelMsg::Data { .. }
                        | ChannelMsg::Failure
                        | ChannelMsg::ExtendedData { .. },
                    )) => {
                        self.fail("DESKTOP_PROTOCOL_INVALID");
                    }
                    Ok(Some(_)) if Instant::now() < deadline => (),
                    _ => break,
                }
            }
        }
        self.closed()
    }
}
