use super::{Pins, Request, protocol};
use russh::{Channel, ChannelMsg, client};
use tokio::time::{Duration, Instant, sleep_until, timeout};
use tokio_util::sync::CancellationToken;

// shell argvは固定codeと検査済みhexだけ。account HOMEを環境変数/remote応答からexecへ挿入しない。
pub(super) fn command(pins: &Pins, nonce: &str) -> Result<String, &'static str> {
    if !crate::forward_probe::valid_nonce(nonce) || !pins.valid() {
        return Err("INVALID_ARGUMENT");
    }
    let source = format!(
        r#"import os,pwd,stat,hashlib,sys
from pathlib import Path
assert os.getuid()==os.geteuid()!=0
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
p=b/'eidolon-linux-session.py'
source=read(p,65536)
assert hashlib.sha256(source).hexdigest()=='{}'
m={{'__name__':'eidolon_pinned','__file__':str(p)}}
exec(compile(source,str(p),'exec'),m)
original=m['bundle']
def pinned(home):
 value=original(home)
 assert value[2]=='{}' and value[1]['waypipe']=='{}' and value[1]['eidolon-shm-fixture']=='{}'
 return value
m['bundle']=pinned
m['main'](['prepare','{}'])
"#,
        pins.bundle_sha256,
        pins.helper_sha256(),
        pins.bundle_sha256,
        pins.waypipe_sha256,
        pins.fixture_sha256,
        nonce
    );
    Ok(format!(
        "/usr/bin/python3 -I -S -c '{}'",
        source.replace('\'', "'\\''")
    ))
}

pub(super) struct Owner {
    channel: Option<Channel<client::Msg>>,
    protocol: protocol::Protocol,
    nonce: String,
    cancel: CancellationToken,
    deadline: Instant,
    heartbeat: Instant,
    stopping: bool,
    pending: Vec<u8>,
    terminal: bool,
}
impl Owner {
    pub async fn open<H: client::Handler>(
        client: &client::Handle<H>,
        request: &Request<'_>,
        cancel: CancellationToken,
        before_exec: impl FnOnce() -> Result<(), &'static str>,
    ) -> Result<Self, &'static str> {
        let text = command(request.pins, request.nonce)?;
        let channel = tokio::select! {biased;
            _=cancel.cancelled()=>return Err("CANCELLED"),
            channel=timeout(Duration::from_secs(5),client.channel_open_session())=>channel,
        }
        .map_err(|_| "LINUX_EXEC_TIMEOUT")?
        .map_err(|_| "SSH_IO_FAILED")?;
        if cancel.is_cancelled() {
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
            return Err("CANCELLED");
        }
        if let Err(error) = before_exec() {
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
            return Err(error);
        }
        // journalをexec直前に永続化。以降のACK不明をremote未起動へ読み替えない。
        if !matches!(
            timeout(Duration::from_secs(2), channel.exec(true, text)).await,
            Ok(Ok(()))
        ) {
            let _ = timeout(Duration::from_secs(1), channel.close()).await;
            return Err("SSH_IO_FAILED");
        }
        Ok(Self::from_channel(
            channel,
            request.nonce,
            request.uid,
            request.pins,
            cancel,
        ))
    }
    pub(super) fn from_channel(
        channel: Channel<client::Msg>,
        nonce: &str,
        uid: u32,
        pins: &Pins,
        cancel: CancellationToken,
    ) -> Self {
        Self {
            channel: Some(channel),
            protocol: protocol::Protocol::new(nonce, uid, pins),
            nonce: nonce.into(),
            cancel,
            deadline: Instant::now() + Duration::from_secs(10),
            heartbeat: Instant::now() + Duration::from_secs(10),
            stopping: false,
            pending: Vec::new(),
            terminal: false,
        }
    }
    async fn control(&self, kind: &str) -> Result<(), &'static str> {
        let mut value = serde_json::json!({"schema":1,"nonce":self.nonce,"type":kind});
        if kind == "start" {
            value["mode"] = "pattern".into();
        }
        timeout(
            Duration::from_secs(2),
            self.channel
                .as_ref()
                .ok_or("STALE")?
                .data_bytes(format!("{value}\n").into_bytes()),
        )
        .await
        .map_err(|_| "SSH_IO_TIMEOUT")?
        .map_err(|_| "SSH_IO_FAILED")
    }
    pub fn prepared(&self) -> bool {
        self.protocol.report.phase == "prepared"
    }
    pub fn tools_ready(&self) -> bool {
        self.protocol.report.phase == "tools_verified"
    }
    pub fn running(&self) -> bool {
        self.protocol.report.phase == "running"
    }
    pub fn closed(&self) -> bool {
        self.terminal
    }
    pub fn remote_socket(&self) -> Option<&str> {
        self.protocol.socket.as_deref()
    }
    pub fn report(&self) -> protocol::Report {
        self.protocol.report.clone()
    }
    pub fn cleaned(&self) -> bool {
        self.protocol.cleaned()
    }
    pub fn pattern_pass(&self) -> bool {
        self.protocol.pattern_pass()
    }
    pub async fn inspect(&mut self) -> Result<(), &'static str> {
        self.protocol.inspect()?;
        self.deadline = Instant::now() + Duration::from_secs(10);
        self.control("inspect_tools").await
    }
    pub async fn start(&mut self) -> Result<(), &'static str> {
        self.protocol.start()?;
        self.deadline = Instant::now() + Duration::from_secs(30);
        self.control("start").await
    }
    pub async fn stop(&mut self) -> Result<(), &'static str> {
        if !self.stopping && !self.terminal {
            self.stopping = true;
            self.protocol.stop();
            self.deadline = Instant::now() + Duration::from_secs(8);
            if !self.protocol.report.finished_received {
                self.control("stop").await?;
            }
        }
        Ok(())
    }
    pub async fn next(&mut self) -> Result<(), &'static str> {
        if self.terminal {
            return Err("STALE");
        }
        loop {
            let message = tokio::select! {biased;
                _=self.cancel.cancelled(),if !self.stopping=>{self.stop().await?;continue;},
                _=sleep_until(self.deadline)=>return Err("LINUX_HELPER_TIMEOUT"),
                _=sleep_until(self.heartbeat),if !self.stopping&&!self.protocol.report.finished_received=>{
                    self.control("heartbeat").await?;self.heartbeat=Instant::now()+Duration::from_secs(10);continue;
                },
                message=self.channel.as_mut().ok_or("STALE")?.wait()=>message,
            };
            match message {
                Some(ChannelMsg::Data { data }) => {
                    self.protocol.report.stdout_bytes = self
                        .protocol
                        .report
                        .stdout_bytes
                        .saturating_add(data.len() as u64);
                    if self.protocol.report.stdout_bytes > 256 * 1024 {
                        return Err("LINUX_OUTPUT_LIMIT");
                    }
                    for byte in data {
                        if byte == b'\n' {
                            let line = std::mem::take(&mut self.pending);
                            self.protocol.line(&line)?;
                        } else {
                            if self.pending.len() >= 16384 {
                                return Err("LINUX_OUTPUT_LIMIT");
                            }
                            self.pending.push(byte);
                        }
                    }
                    return Ok(());
                }
                Some(ChannelMsg::ExtendedData { data, ext: 1 }) => {
                    self.protocol.report.stderr_bytes = self
                        .protocol
                        .report
                        .stderr_bytes
                        .saturating_add(data.len() as u64);
                    if self.protocol.report.stderr_bytes > 128 * 1024 {
                        return Err("LINUX_OUTPUT_LIMIT");
                    }
                }
                Some(ChannelMsg::Eof) => self.protocol.report.eof = true,
                Some(ChannelMsg::ExitStatus { .. })
                    if self.protocol.report.exit_status.is_some() =>
                {
                    return Err("LINUX_PROTOCOL_ERROR");
                }
                Some(ChannelMsg::ExitStatus { exit_status }) => {
                    self.protocol.report.exit_status = Some(exit_status)
                }
                Some(ChannelMsg::ExitSignal { .. }) => self.protocol.report.exit_signal = true,
                None | Some(ChannelMsg::Close) => {
                    self.terminal = true;
                    self.protocol.finish(self.pending.len())?;
                    return Ok(());
                }
                Some(ChannelMsg::Failure | ChannelMsg::ExtendedData { .. }) => {
                    return Err("LINUX_PROTOCOL_ERROR");
                }
                _ => (),
            }
        }
    }
    pub async fn close(&mut self) -> bool {
        let Some(channel) = self.channel.take() else {
            return self.terminal;
        };
        let closed = matches!(
            timeout(Duration::from_secs(1), channel.close()).await,
            Ok(Ok(()))
        );
        self.terminal || closed
    }
}
