//! V3 SSH owner, deliberately separate from the frozen v2 bootstrap.
use super::{
    Action, Binding, Event, ExpectedEvidence, FrameDecoder, Phase, Protocol, Selection, sources,
};
use eidolon_model::linux_desktop_v3::{Bootstrap, FailureReason, StopReason};
use russh::{Channel, ChannelMsg, client};
use serde::Serialize;
use tokio::time::{Duration, Instant, sleep_until, timeout};
use tokio_util::sync::CancellationToken;

const PREPARE_TIMEOUT: Duration = Duration::from_secs(10);
const INSPECT_TIMEOUT: Duration = Duration::from_secs(10);
const START_TIMEOUT: Duration = Duration::from_secs(12);
const HEARTBEAT_TIMEOUT: Duration = Duration::from_secs(5);
const STOP_TIMEOUT: Duration = Duration::from_secs(8);
const CLOSE_TIMEOUT: Duration = Duration::from_secs(3);
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(5);
const MAX_STDERR: u64 = 65536;
const STOP_BUDGET: Duration = Duration::from_secs(33);
const BOOTSTRAP: &str = include_str!("bootstrap.py");

/// Construct a reviewable fixed command from B1's typed 14/15 vector. This pure
/// function grants no execution authority: open/recover require source closure.
pub fn command(binding: &Binding, bootstrap: &Bootstrap) -> Result<String, &'static str> {
    bootstrap
        .matches(binding.context(), binding.pins())
        .map_err(|e| e.0)?;
    let args = serde_json::to_string(&bootstrap.vector()).map_err(|_| "DESKTOP_BINDING_INVALID")?;
    Bootstrap::parse(args.as_bytes())
        .map_err(|e| e.0)?
        .matches(binding.context(), binding.pins())
        .map_err(|e| e.0)?;
    let hashes = sources::current_hashes();
    sources::validate_hashes(&hashes)?;
    let inventory =
        std::str::from_utf8(sources::inventory_bytes()).map_err(|_| "DESKTOP_V3_SOURCE_INVALID")?;
    let literal = |value: &str| serde_json::to_string(value).map_err(|_| "DESKTOP_BINDING_INVALID");
    let pins =
        serde_json::to_string(&binding.pins().record()).map_err(|_| "DESKTOP_BINDING_INVALID")?;
    let hashes = serde_json::to_string(&hashes).map_err(|_| "DESKTOP_BINDING_INVALID")?;
    let source = format!(
        "import json\nexpected_uid={}\npins=json.loads({})\nsource_hashes=json.loads({})\ninventory_raw={}\ninventory=json.loads(inventory_raw)\nbuild_id={}\nargv=json.loads({})\n{}",
        binding.uid(),
        literal(&pins)?,
        literal(&hashes)?,
        literal(inventory)?,
        literal(&binding.pins().build_id())?,
        literal(&args)?,
        BOOTSTRAP
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

/// A mailbox is sampled only while awaiting channel input. Pending SSH writes
/// are never abandoned by an outer select! simply to receive a Launch.
pub enum OwnerTurn<T> {
    Events(Vec<Event>),
    Launch(T),
    MailboxClosed,
    Closed,
}

pub struct Owner {
    channel: Option<Channel<client::Msg>>,
    protocol: Protocol,
    cancel: CancellationToken,
    deadline: Option<Instant>,
    heartbeat: Instant,
    stopping: bool,
    stop_deadline: Option<Instant>,
    frames: FrameDecoder,
    boot_id: Option<String>,
    remote_socket: Option<String>,
    report: RemoteReport,
}
impl Owner {
    pub async fn open<H: client::Handler>(
        client: &client::Handle<H>,
        binding: &Binding,
        evidence: &ExpectedEvidence,
        cancel: CancellationToken,
        before_exec: impl FnOnce() -> Result<(), &'static str>,
    ) -> Result<Self, &'static str> {
        sources::require_complete()?;
        sources::validate_hashes(&evidence.source_hashes())?;
        let protocol = Protocol::new(binding.clone(), evidence.clone())?;
        let text = command(
            binding,
            &Bootstrap::serve(binding.context().clone(), binding.pins().clone()),
        )?;
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
        Ok(Self::from_channel(channel, protocol, cancel))
    }

    fn from_channel(
        channel: Channel<client::Msg>,
        protocol: Protocol,
        cancel: CancellationToken,
    ) -> Self {
        Self {
            channel: Some(channel),
            protocol,
            cancel,
            deadline: Some(Instant::now() + PREPARE_TIMEOUT),
            heartbeat: Instant::now() + HEARTBEAT_INTERVAL,
            stopping: false,
            stop_deadline: None,
            frames: FrameDecoder::default(),
            boot_id: None,
            remote_socket: None,
            report: RemoteReport::default(),
        }
    }
    pub fn phase(&self) -> Phase {
        self.protocol.phase()
    }
    pub fn selection(&self, app_id: &str) -> Result<Selection, &'static str> {
        if self.stopping {
            return Err("CANCELLED");
        }
        self.protocol.selection(app_id)
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
            && self.frames.finish().is_ok()
            && self.report.protocol_valid
    }
    fn fail(&mut self, error: &'static str) -> &'static str {
        self.protocol.fail();
        self.report.phase = "failed";
        self.report.protocol_valid = false;
        self.deadline = None;
        error
    }
    pub async fn control(
        &mut self,
        action: Action,
        selection: Option<&Selection>,
    ) -> Result<(), &'static str> {
        if self.channel.is_none() || self.closed() {
            return Err("STALE");
        }
        let bytes = self.protocol.command(action, selection)?;
        // The ACK deadline includes enqueue time, not just time after send.
        // C2 Start can perform a five-second settings probe and a three-second
        // private-bus wait before acknowledging the command.
        let sent_at = Instant::now();
        let operation_timeout = match action {
            Action::Inspect => INSPECT_TIMEOUT,
            Action::Start => START_TIMEOUT,
            Action::Heartbeat => HEARTBEAT_TIMEOUT,
            Action::Stop => STOP_TIMEOUT,
        };
        self.deadline = Some(sent_at + operation_timeout);
        // The C2 lease renews when the command arrives, not when its ACK is
        // observed. A late Start ACK must not postpone the next heartbeat.
        self.heartbeat = sent_at + HEARTBEAT_INTERVAL;
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
    pub fn begin_stop(&mut self, observed_at: Instant) {
        self.stopping = true;
        self.stop_deadline.get_or_insert(observed_at + STOP_BUDGET);
    }
    pub fn command_idle(&self) -> bool {
        !self.closed() && !self.report.terminal_event && self.protocol.in_flight().is_none()
    }
    /// C2's lease is 15 seconds from command receive, while heartbeat is
    /// scheduled five seconds from the send start. Conservatively bound long
    /// local/forward operations to the remaining interval.
    pub fn lease_margin(&self, needed: Duration) -> bool {
        Instant::now() + needed <= self.heartbeat + Duration::from_secs(10)
    }
    pub fn can_launch(&self) -> bool {
        !self.stopping && !self.cancel.is_cancelled() && self.command_idle()
            && self.phase() == Phase::Catalog
    }
    /// Renew the C2 lease on both sides of a potentially slow forward setup.
    /// A terminal event in the same frame as the ACK invalidates the barrier.
    pub async fn renew_catalog(&mut self) -> Result<(), &'static str> {
        while !self.command_idle() {
            if self.stopping || self.cancel.is_cancelled() || self.report.terminal_event {
                return Err("CANCELLED");
            }
            self.next().await?;
        }
        if !self.can_launch() {
            return Err("STALE");
        }
        self.control(Action::Heartbeat, None).await?;
        while !self.command_idle() {
            self.next().await?;
            if self.stopping || self.cancel.is_cancelled() || self.report.terminal_event {
                return Err("CANCELLED");
            }
        }
        if !self.can_launch() { return Err("STALE"); }
        Ok(())
    }
    pub async fn stop(&mut self) -> Result<(), &'static str> {
        self.begin_stop(Instant::now());
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
                    StopReason::Stop => "stop",
                    StopReason::LeaseExpired => "lease_expired",
                    StopReason::InputEof => "input_eof",
                    StopReason::ProcessExited => "process_exited",
                });
            }
            Event::Pending {
                reason,
                public_reason: _,
                process_complete,
                pipes_eof,
            } => {
                self.report.terminal_event = true;
                self.report.process_complete = process_complete;
                self.report.pipes_eof = pipes_eof;
                self.report.reason = Some(match reason {
                    FailureReason::ProcessOwnership => "process_ownership",
                    FailureReason::PipeEof => "pipe_eof",
                    FailureReason::DirectoryCleanup => "directory_cleanup",
                    FailureReason::ReceiptWrite => "receipt_write",
                    FailureReason::Protocol => "protocol",
                    FailureReason::ToolInspection => "tool_inspection",
                    FailureReason::Start => "start",
                });
            }
            _ => (),
        }
        // No total-runtime timer. The normal session stays alive as long as its
        // renewable lease and each individual request/ACK remain healthy.
        if self.report.terminal_event {
            self.deadline = Some(Instant::now() + CLOSE_TIMEOUT);
        } else {
            // An ACK retires the in-flight request. Preserve the heartbeat
            // deadline established when the request was sent.
            self.deadline = None;
        }
    }
    fn data(&mut self, bytes: &[u8]) -> Result<Vec<Event>, &'static str> {
        if self.report.eof {
            return Err(self.fail("DESKTOP_V3_PROTOCOL_INVALID"));
        }
        self.report.stdout_bytes = self.report.stdout_bytes.saturating_add(bytes.len() as u64);
        let frames = self.frames.feed(bytes).map_err(|error| self.fail(error))?;
        let mut events = Vec::new();
        for line in frames {
            let event = self
                .protocol
                .accept(&line)
                .map_err(|error| self.fail(error))?;
            self.event(event.clone());
            events.push(event);
        }
        Ok(events)
    }
    /// Never race an entire next() call against recv(): control() mutates the
    /// protocol before waiting for a bounded SSH send. Only channel.wait() and
    /// the cancel-safe mailbox receive compete in select!.
    pub async fn next(&mut self) -> Result<Vec<Event>, &'static str> {
        match self.next_input::<()>(None).await? {
            OwnerTurn::Events(events) => Ok(events),
            OwnerTurn::Closed => Ok(Vec::new()),
            OwnerTurn::Launch(()) | OwnerTurn::MailboxClosed => unreachable!("no mailbox"),
        }
    }

    pub async fn next_launch<T>(
        &mut self,
        launches: &mut tokio::sync::mpsc::Receiver<T>,
    ) -> Result<OwnerTurn<T>, &'static str> {
        self.next_input(Some(launches)).await
    }

    async fn next_input<T>(
        &mut self,
        mut launches: Option<&mut tokio::sync::mpsc::Receiver<T>>,
    ) -> Result<OwnerTurn<T>, &'static str> {
        enum Input<T> {
            Cancel,
            Deadline,
            Heartbeat,
            Launch(Option<T>),
            Channel(Option<ChannelMsg>),
        }
        if self.closed() || self.phase() == Phase::Failed {
            return Err("STALE");
        }
        loop {
            if self.cancel.is_cancelled() && !self.stopping {
                self.begin_stop(Instant::now());
            }
            let now = Instant::now();
            if self.stop_deadline.is_some_and(|deadline| now >= deadline) {
                return Err(self.fail("DESKTOP_HELPER_TIMEOUT"));
            }
            if self.deadline.is_some_and(|deadline| now >= deadline) {
                return Err(self.fail("DESKTOP_HELPER_TIMEOUT"));
            }
            if self.stopping && self.protocol.in_flight().is_none() {
                self.stop().await?;
                // stop() is a bounded send. Processing resumes only after it
                // resolves; a pending ACK keeps the original deadline intact.
            }
            let heartbeat_eligible = !self.stopping
                && !self.report.eof && !self.report.terminal_event
                && self.protocol.in_flight().is_none()
                && matches!(self.phase(), Phase::Prepared | Phase::Catalog | Phase::Started);
            if heartbeat_eligible && Instant::now() >= self.heartbeat {
                self.control(Action::Heartbeat, None).await?;
                continue;
            }
            let launch_enabled = self.can_launch() && launches.is_some();
            if launch_enabled {
                if let Some(receiver) = launches.as_mut() {
                    match receiver.try_recv() {
                        Ok(launch) => return Ok(OwnerTurn::Launch(launch)),
                        Err(tokio::sync::mpsc::error::TryRecvError::Disconnected) => {
                            self.begin_stop(Instant::now());
                            return Ok(OwnerTurn::MailboxClosed);
                        }
                        Err(tokio::sync::mpsc::error::TryRecvError::Empty) => {}
                    }
                }
            }
            let request_deadline = self.deadline;
            let stop_deadline = self.stop_deadline;
            let next_input = {
                let channel = self.channel.as_mut().ok_or("STALE")?;
                tokio::select! { biased;
                    _ = self.cancel.cancelled(), if !self.stopping => Input::Cancel,
                    _ = sleep_until(request_deadline.unwrap_or(now + STOP_BUDGET)), if request_deadline.is_some() => Input::Deadline,
                    _ = sleep_until(stop_deadline.unwrap_or(now + STOP_BUDGET)), if stop_deadline.is_some() => Input::Deadline,
                    _ = sleep_until(self.heartbeat), if heartbeat_eligible => Input::Heartbeat,
                    message = channel.wait() => Input::Channel(message),
                    launch = async { launches.as_mut().expect("mailbox gated").recv().await }, if launch_enabled => Input::Launch(launch),
                }
            };
            match next_input {
                Input::Cancel => { self.begin_stop(Instant::now()); }
                Input::Deadline => return Err(self.fail("DESKTOP_HELPER_TIMEOUT")),
                Input::Heartbeat => {},
                Input::Launch(Some(launch)) => {
                    if !self.can_launch() { return Err("STALE"); }
                    return Ok(OwnerTurn::Launch(launch));
                }
                Input::Launch(None) => {
                    self.begin_stop(Instant::now());
                    return Ok(OwnerTurn::MailboxClosed);
                }
                Input::Channel(Some(ChannelMsg::Data { data })) => {
                    return self.data(&data).map(OwnerTurn::Events);
                }
                Input::Channel(Some(ChannelMsg::ExtendedData { data, ext: 1 })) => {
                    if self.report.eof { return Err(self.fail("DESKTOP_V3_PROTOCOL_INVALID")); }
                    self.report.stderr_bytes = self.report.stderr_bytes.saturating_add(data.len() as u64);
                    if self.report.stderr_bytes > MAX_STDERR {
                        return Err(self.fail("DESKTOP_OUTPUT_LIMIT"));
                    }
                }
                Input::Channel(Some(ChannelMsg::Eof)) => {
                    if self.report.eof || self.frames.finish().is_err() {
                        return Err(self.fail("DESKTOP_V3_PROTOCOL_INVALID"));
                    }
                    self.report.eof = true;
                    self.deadline = Some(Instant::now() + CLOSE_TIMEOUT);
                }
                Input::Channel(Some(ChannelMsg::ExitStatus { exit_status })) => {
                    if self.report.exit_status.replace(exit_status).is_some() {
                        return Err(self.fail("DESKTOP_V3_PROTOCOL_INVALID"));
                    }
                    self.deadline = Some(Instant::now() + CLOSE_TIMEOUT);
                }
                Input::Channel(Some(ChannelMsg::ExitSignal { .. })) => {
                    self.report.exit_signal = true;
                    self.deadline = Some(Instant::now() + CLOSE_TIMEOUT);
                }
                Input::Channel(None | Some(ChannelMsg::Close)) => {
                    self.report.channel_closed = true;
                    if !self.channel_complete() || !self.report.terminal_event {
                        return Err(self.fail("DESKTOP_HELPER_LOST"));
                    }
                    return Ok(OwnerTurn::Closed);
                }
                Input::Channel(Some(ChannelMsg::Failure | ChannelMsg::ExtendedData { .. })) => {
                    return Err(self.fail("DESKTOP_V3_PROTOCOL_INVALID"));
                }
                Input::Channel(Some(_)) => {},
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

#[cfg(test)]
pub(super) mod test_support;
#[cfg(test)]
mod tests;
