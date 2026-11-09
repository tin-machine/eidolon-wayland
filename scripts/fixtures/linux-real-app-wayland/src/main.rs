//! Host-only A0 compositor. The Python owner starts unmodified Mousepad through
//! real Waypipe; this process owns only the production ProtocolState and seat.
//! Control replies deliberately exclude titles, text, paths and pixel contents.
use eidolon_core::compositor::protocol::ProtocolState;
use eidolon_model::{
    ids::{InputSourceId, SessionId, WindowId},
    keyboard::{EvdevCode, KeyAction, KeyPacket, KeyboardLayout},
    pointer::{PointerEvent, PointerPacket},
    window::WindowLife,
};
use serde::Deserialize;
use serde_json::{Value, json};
use smithay::reexports::wayland_server::{Display, backend::DisconnectReason};
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    io::{self, Read, Write},
    os::{fd::RawFd, unix::{fs::{MetadataExt, OpenOptionsExt}, net::UnixListener}},
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    thread,
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, &'static str>;
const SOCKET_NAME: &str = "wayland-l7a";
const MAX_LINE: usize = 4096;
const MAX_OUTPUT: usize = 128 * 1024;
const MAX_OBSERVATIONS: usize = 64;
const LIFETIME_POLICY: &str = include_str!("../lifetime.json");
static STOP_REQUESTED: AtomicBool = AtomicBool::new(false);

extern "C" fn interrupted(_: libc::c_int) {
    STOP_REQUESTED.store(true, Ordering::Relaxed);
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Lifetime {
    Run,
    Precursor,
}
impl Lifetime {
    fn parse(value: &std::ffi::OsStr) -> Result<Self> {
        match value.to_str() {
            Some("run") => Ok(Self::Run),
            Some("precursor") => Ok(Self::Precursor),
            _ => Err("ARGS"),
        }
    }
    fn as_str(self) -> &'static str {
        match self {
            Self::Run => "run",
            Self::Precursor => "precursor",
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct LifetimePolicy {
    run_seconds: u64,
    precursor_seconds: u64,
    compositor_grace_seconds: u64,
}
impl LifetimePolicy {
    fn parse(source: &str) -> Result<Self> {
        let policy: Self = serde_json::from_str(source).map_err(|_| "LIFETIME_POLICY")?;
        if policy.run_seconds == 0 || policy.run_seconds > policy.precursor_seconds
            || policy.precursor_seconds > 600 || !(1..=30).contains(&policy.compositor_grace_seconds)
        {
            return Err("LIFETIME_POLICY");
        }
        Ok(policy)
    }
    fn compositor_limit(&self, lifetime: Lifetime) -> Duration {
        let owner_seconds = match lifetime {
            Lifetime::Run => self.run_seconds,
            Lifetime::Precursor => self.precursor_seconds,
        };
        // Python fixes its owner deadline before spawning this process. This
        // later-starting watchdog must not terminate an otherwise valid run.
        Duration::from_secs(owner_seconds + self.compositor_grace_seconds)
    }
}
fn check_lifetime(elapsed: Duration, limit: Duration) -> Result<()> {
    if elapsed >= limit { Err("FIXTURE_TIMEOUT") } else { Ok(()) }
}

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Snapshot { id: u32 },
    Focus { id: u32, window: WindowId },
    Key { id: u32, window: WindowId, evdev: u32, pressed: bool },
    Click { id: u32, window: WindowId, uv: [f64; 2] },
    Button { id: u32, window: WindowId, uv: [f64; 2], pressed: bool },
    Close { id: u32, window: WindowId },
    Shutdown { id: u32 },
}
impl Request {
    fn id(&self) -> u32 {
        match self {
            Self::Snapshot { id } | Self::Focus { id, .. } | Self::Key { id, .. }
            | Self::Click { id, .. } | Self::Button { id, .. }
            | Self::Close { id, .. } | Self::Shutdown { id } => *id,
        }
    }
}
fn parse_request(line: &[u8], previous: u32) -> Result<Request> {
    if line.is_empty() || line.len() > MAX_LINE {
        return Err("CONTROL_LINE_BOUND");
    }
    let request: Request = serde_json::from_slice(line).map_err(|_| "CONTROL_REQUEST")?;
    if request.id() == 0 || request.id() <= previous {
        return Err("CONTROL_SEQUENCE");
    }
    if let Request::Key { evdev, .. } = &request
        && EvdevCode::new(*evdev).is_none()
    {
        return Err("CONTROL_KEY");
    }
    if let Request::Click { uv, .. } | Request::Button { uv, .. } = &request
        && uv.iter().any(|v| !v.is_finite() || !(0.0..1.0).contains(v))
    {
        return Err("CONTROL_COORDINATE");
    }
    Ok(request)
}

fn private_dir(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path).map_err(|_| "DIRECTORY_MISSING")?;
    // The orchestrator creates empty, private directories. Never chmod a caller's
    // existing directory or follow a symlink as a supposedly private destination.
    if !path.is_absolute() || !metadata.is_dir() || metadata.file_type().is_symlink()
        || metadata.mode() & 0o077 != 0 || metadata.uid() != unsafe { libc::geteuid() }
    {
        return Err("DIRECTORY_NOT_PRIVATE");
    }
    Ok(())
}
struct OwnedSocket(PathBuf);
impl Drop for OwnedSocket {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn nonblocking(fd: RawFd) -> Result<()> {
    // SAFETY: fcntl borrows a live process-owned descriptor; no ownership transfer.
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return Err("CONTROL_NONBLOCK");
    }
    Ok(())
}
struct Output {
    bytes: Vec<u8>,
    start: usize,
}
impl Output {
    fn new() -> Result<Self> {
        nonblocking(libc::STDOUT_FILENO)?;
        Ok(Self { bytes: Vec::new(), start: 0 })
    }
    fn push(&mut self, value: Value) -> Result<()> {
        if self.start > 0 {
            self.bytes.drain(..self.start);
            self.start = 0;
        }
        let mut encoded = serde_json::to_vec(&value).map_err(|_| "CONTROL_JSON")?;
        encoded.push(b'\n');
        if self.bytes.len() + encoded.len() > MAX_OUTPUT {
            return Err("CONTROL_OUTPUT_BOUND");
        }
        self.bytes.extend_from_slice(&encoded);
        self.flush()
    }
    fn flush(&mut self) -> Result<()> {
        while self.start < self.bytes.len() {
            // SAFETY: valid byte slice for this synchronous write, borrowed stdout.
            let count = unsafe {
                libc::write(libc::STDOUT_FILENO, self.bytes[self.start..].as_ptr().cast(),
                    self.bytes.len() - self.start)
            };
            if count < 0 {
                let error = io::Error::last_os_error();
                if error.kind() == io::ErrorKind::WouldBlock { return Ok(()); }
                if error.kind() == io::ErrorKind::Interrupted { continue; }
                return Err("CONTROL_OUTPUT_CLOSED");
            }
            if count == 0 { return Err("CONTROL_OUTPUT_CLOSED"); }
            self.start += count as usize;
        }
        self.bytes.clear();
        self.start = 0;
        Ok(())
    }
    fn finish(&mut self) -> Result<()> {
        let deadline = Instant::now() + Duration::from_secs(2);
        while self.start < self.bytes.len() {
            self.flush()?;
            if Instant::now() >= deadline { return Err("CONTROL_OUTPUT_TIMEOUT"); }
            thread::sleep(Duration::from_millis(2));
        }
        Ok(())
    }
}

fn counts(display: &Display<ProtocolState>, state: &ProtocolState) -> Value {
    let backend = display.handle().backend_handle();
    let mut clients = Vec::new();
    backend.with_all_clients(|client| clients.push(client));
    let mut objects = 0usize;
    for client in &clients {
        let _ = backend.with_all_objects_for(client.clone(), |_| objects += 1);
    }
    json!({"clients": clients.len(), "objects": objects,
        "surfaces": state.surface_count(), "windows": state.windows().len(),
        "pixel_bytes": state.pixel_bytes(), "pool_bytes": state.pool_usage().bytes})
}
fn is_clean(display: &Display<ProtocolState>, state: &ProtocolState) -> bool {
    counts(display, state).as_object().is_some_and(|counts| counts.values().all(|n| n == 0))
}

#[derive(Default)]
struct Observations {
    seen: BTreeSet<WindowId>,
    mapped: Vec<Value>,
    dispatch_batch: u64,
    accepted_clients: u64,
}
impl Observations {
    fn sample(&mut self, state: &ProtocolState) -> Result<()> {
        self.dispatch_batch += 1;
        for window in state.windows() {
            if window.life == WindowLife::Mapped && self.seen.insert(window.window) {
                if self.mapped.len() >= MAX_OBSERVATIONS { return Err("OBSERVATION_BOUND"); }
                // A batch number makes simultaneous observations explicit. It must
                // not be interpreted as an order of Wayland requests within a dispatch.
                self.mapped.push(json!({"window": window.window, "parent": window.parent,
                    "app_id": safe_app_id(window.app_id.as_deref()),
                    "app_id_valid": app_id_valid(window.app_id.as_deref()),
                    "dispatch_batch": self.dispatch_batch, "frame": window.frame,
                    "logical_size": window.logical_size}));
            }
        }
        Ok(())
    }
    fn snapshot(&self, display: &Display<ProtocolState>, state: &ProtocolState) -> Value {
        let windows: Vec<Value> = state.windows().into_iter().map(|w| json!({
            "window": w.window, "parent": w.parent, "app_id": safe_app_id(w.app_id.as_deref()),
            "app_id_valid": app_id_valid(w.app_id.as_deref()), "life": w.life, "frame": w.frame,
            "logical_size": w.logical_size, "layers": w.layers,
            // GTK widget positions are in the root wl_surface coordinates.
            // Native pointer UVs address the xdg window geometry, excluding
            // CSD shadows: subtract this origin before dividing by size.
            // LogicalPoint is a checked i32 pair; no title/pixel is exposed.
            "geometry_origin": state.latest_snapshot(w.window).map(|s| s.geometry.origin)
        })).collect();
        let input = state.input_report();
        let c = state.counters;
        json!({"windows": windows, "counts": counts(display, state),
            "input": {"focus_window": input.focus_window, "focus_token": input.focus_token,
                "delivered": input.delivered, "pressed": input.pressed,
                // Keyboard delivered does not count pointer events. Keep the
                // genuine native pointer counters separate for GUI oracles.
                "pointer_buttons": input.pointer.buttons, "pointer_motions": input.pointer.motions,
                "pointer_pressed": input.pointer.pressed,
                "pointer_window": input.pointer.hover_window},
            "counters": {"commits": c.commits, "configures": c.configures, "acks": c.acks,
                "copies": c.copies, "releases": c.releases, "callbacks": c.callbacks,
                "maps": c.maps, "unmaps": c.unmaps, "bad_maps": c.bad_maps,
                "client_rejections": c.client_rejections, "toplevel_rejections": c.toplevel_rejections,
                "pool_rejections": c.pool_rejections, "surface_rejections": c.surface_rejections},
            "accepted_clients": self.accepted_clients, "observed_mapped": self.mapped})
    }
}
fn safe_app_id(value: Option<&str>) -> Option<&str> {
    // IDs are diagnostic metadata, never free-form window titles. Reject unexpected
    // bytes rather than copying arbitrary application-controlled text into a receipt.
    value.filter(|s| s.len() <= 128 && s.bytes().all(|b|
        b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-')))
}
fn app_id_valid(value: Option<&str>) -> bool {
    value.is_none() || safe_app_id(value).is_some()
}
fn key_time() -> u32 {
    let mut time = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: initialized timespec, synchronously filled by clock_gettime.
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } != 0 { return 0; }
    ((time.tv_sec as u64).wrapping_mul(1000) + time.tv_nsec as u64 / 1_000_000) as u32
}
fn apply(request: &Request, state: &mut ProtocolState) -> Result<()> {
    match *request {
        Request::Snapshot { .. } | Request::Shutdown { .. } => Ok(()),
        Request::Focus { window, .. } => state.focus_window(window),
        Request::Close { window, .. } => state.close_window(window),
        Request::Key { window, evdev, pressed, .. } => {
            let input = state.input_report();
            // Enter may close a chooser before the controller sends key-up.
            // finish_dispatch has already released all native keys when that
            // window unmaps. Accept only that redundant fixture release; do
            // not deliver input, refocus another window, or mask a live key.
            if !pressed && input.pressed == 0
                && !state.windows().iter().any(|w| w.window == window && w.life == WindowLife::Mapped)
            {
                return Ok(());
            }
            let token = input.focus_token.ok_or("INPUT_NOT_FOCUSED")?;
            state.key_at_focus(window, token, KeyPacket {
                action: if pressed { KeyAction::Pressed } else { KeyAction::Released },
                device: 0, scan_code: evdev as i32, key_code: 0, repeat: 0, meta: 0,
                time_ms: key_time(),
            })
        }
        Request::Click { window, uv, .. } => {
            let size = state.latest_snapshot(window).ok_or("INPUT_NOT_MAPPED")?.logical_size;
            let source = InputSourceId::new(1).map_err(|_| "INPUT_SOURCE")?;
            for event in [PointerEvent::Move,
                PointerEvent::Buttons { changed: 1, state: 1 },
                PointerEvent::Buttons { changed: 1, state: 0 }]
            {
                state.pointer(window, PointerPacket { source, uv: Some(uv), event,
                    time_ms: key_time() }, size.width as i32, size.height as i32)?;
            }
            Ok(())
        }
        Request::Button { window, uv, pressed, .. } => {
            // A real menu popup may request its grab after receiving button
            // down. Keep that primary-button state across separate controller
            // requests so dispatch can process the client's grab before up.
            // The owner still bounds the run and resets input on teardown.
            let size = state.latest_snapshot(window).ok_or("INPUT_NOT_MAPPED")?.logical_size;
            let source = InputSourceId::new(1).map_err(|_| "INPUT_SOURCE")?;
            for event in [PointerEvent::Move,
                PointerEvent::Buttons { changed: 1, state: u8::from(pressed) }]
            {
                state.pointer(window, PointerPacket { source, uv: Some(uv), event,
                    time_ms: key_time() }, size.width as i32, size.height as i32)?;
            }
            Ok(())
        }
    }
}
fn dispatch(display: &mut Display<ProtocolState>, state: &mut ProtocolState,
            observation: &mut Observations, elapsed: Duration) -> Result<()> {
    display.dispatch_clients(state).map_err(|_| "WAYLAND_DISPATCH")?;
    state.finish_dispatch(&display.handle());
    state.resize_tick(elapsed);
    observation.sample(state)?;
    for window in state.windows() {
        state.frame_callbacks(window.window, elapsed, true, true, true);
    }
    display.flush_clients().map_err(|_| "WAYLAND_FLUSH")
}
fn teardown(display: &mut Display<ProtocolState>, state: &mut ProtocolState) -> Result<()> {
    state.reset_input();
    let backend = display.handle().backend_handle();
    let mut clients = Vec::new();
    backend.with_all_clients(|client| clients.push(client));
    for client in clients {
        backend.kill_client(client, DisconnectReason::ConnectionClosed);
    }
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        display.dispatch_clients(state).map_err(|_| "CLEANUP_DISPATCH")?;
        state.finish_dispatch(&display.handle());
        if is_clean(display, state) { return Ok(()); }
        if Instant::now() >= deadline { return Err("CLEANUP_TIMEOUT"); }
        thread::sleep(Duration::from_millis(2));
    }
}
fn write_receipt(artifact: &Path, proof: &Value) -> Result<()> {
    let mut file = OpenOptions::new().write(true).create_new(true).mode(0o600)
        .open(artifact.join("result.json")).map_err(|_| "RESULT_CREATE")?;
    let mut bytes = serde_json::to_vec_pretty(proof).map_err(|_| "RESULT_JSON")?;
    bytes.push(b'\n');
    file.write_all(&bytes).map_err(|_| "RESULT_WRITE")?;
    file.sync_all().map_err(|_| "RESULT_SYNC")
}

fn run(runtime: &Path, artifact: &Path, lifetime: Lifetime) -> Result<()> {
    let run_limit = LifetimePolicy::parse(LIFETIME_POLICY)?.compositor_limit(lifetime);
    private_dir(runtime)?;
    private_dir(artifact)?;
    if artifact.join("result.json").exists() { return Err("RESULT_EXISTS"); }
    let socket_path = runtime.join(SOCKET_NAME);
    let listener = UnixListener::bind(&socket_path).map_err(|_| "WAYLAND_LISTEN")?;
    let _owned_socket = OwnedSocket(socket_path);
    listener.set_nonblocking(true).map_err(|_| "WAYLAND_NONBLOCK")?;
    let mut display = Display::<ProtocolState>::new().map_err(|_| "WAYLAND_DISPLAY")?;
    let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).map_err(|_| "SESSION_ID")?);
    state.configure_keyboard(KeyboardLayout::Us)?;
    let mut observation = Observations::default();
    nonblocking(libc::STDIN_FILENO)?;
    let mut output = Output::new()?;
    output.push(json!({"schema":1,"event":"ready","socket_name":SOCKET_NAME,
        "protocol":"eidolon-a0-control-v1","server":"Eidolon ProtocolState",
        "lifetime":lifetime.as_str(),"run_limit_seconds":run_limit.as_secs()}))?;
    let started = Instant::now();
    let mut pending = Vec::new();
    let mut last_request = 0;
    let drive = (|| -> Result<()> {
        loop {
            if STOP_REQUESTED.load(Ordering::Relaxed) { return Err("INTERRUPTED"); }
            check_lifetime(started.elapsed(), run_limit)?;
            for _ in 0..8 {
                match listener.accept() {
                    Ok((stream, _)) => {
                        state.accept(&display.handle(), stream)?;
                        observation.accepted_clients += 1;
                    }
                    Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
                    Err(_) => return Err("WAYLAND_ACCEPT"),
                }
            }
            dispatch(&mut display, &mut state, &mut observation, started.elapsed())?;
            let mut chunk = [0u8; 4096];
            match io::stdin().read(&mut chunk) {
                Ok(0) => return Err("CONTROL_EOF"),
                Ok(n) => pending.extend_from_slice(&chunk[..n]),
                Err(e) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted) => (),
                Err(_) => return Err("CONTROL_READ"),
            }
            // Deliberately process one command per dispatch so protocol callbacks
            // and surface updates cannot be starved by an input burst.
            if let Some(end) = pending.iter().position(|b| *b == b'\n') {
                let request = parse_request(&pending[..end], last_request)?;
                pending.drain(..=end);
                last_request = request.id();
                if matches!(request, Request::Shutdown { .. }) {
                    if !is_clean(&display, &state) {
                        output.push(json!({"id":request.id(),"ok":false,"error":"CLIENTS_REMAIN",
                            "snapshot":observation.snapshot(&display,&state)}))?;
                    } else {
                        output.push(json!({"id":request.id(),"ok":true,
                            "snapshot":observation.snapshot(&display,&state)}))?;
                        return Ok(());
                    }
                } else {
                    let applied = apply(&request, &mut state);
                    display.flush_clients().map_err(|_| "WAYLAND_FLUSH")?;
                    output.push(json!({"id":request.id(),"ok":applied.is_ok(),"error":applied.err(),
                        "snapshot":observation.snapshot(&display,&state)}))?;
                }
            }
            if pending.len() > MAX_LINE { return Err("CONTROL_LINE_BOUND"); }
            output.flush()?;
            thread::sleep(Duration::from_millis(2));
        }
    })();
    let clean_before_forced_disconnect = is_clean(&display, &state);
    let cleanup = teardown(&mut display, &mut state);
    let proof = json!({"schema":1,"fixture_revision":"eidolon-real-app-wayland-v1",
        "lifetime":lifetime.as_str(),"run_limit_seconds":run_limit.as_secs(),
        "scope":"compositor-and-seat-only","state":if drive.is_ok() && cleanup.is_ok() {"complete"} else {"fail"},
        "drive_error":drive.err(),"cleanup_error":cleanup.err(),
        "client_cleanup_verified":clean_before_forced_disconnect && cleanup.is_ok(),
        "forced_disconnect":!clean_before_forced_disconnect,
        "physical_input_verified":false,"mousepad_feasibility_verified":false,
        "titles_logged":false,"payload_logged":false,"pixels_logged":false,
        "snapshot":observation.snapshot(&display,&state)});
    write_receipt(artifact, &proof)?;
    output.push(json!({"schema":1,"event":"finished","state":proof["state"]}))?;
    output.finish()?;
    drive.and(cleanup)
}
fn arguments_from(mut args: impl Iterator<Item = std::ffi::OsString>) -> Result<(PathBuf, PathBuf, Lifetime)> {
    if args.next().as_deref() != Some(std::ffi::OsStr::new("--runtime-dir")) { return Err("ARGS"); }
    let runtime = PathBuf::from(args.next().ok_or("ARGS")?);
    if args.next().as_deref() != Some(std::ffi::OsStr::new("--artifact-dir")) { return Err("ARGS"); }
    let artifact = PathBuf::from(args.next().ok_or("ARGS")?);
    let lifetime = match args.next() {
        None => Lifetime::Run,
        Some(option) if option.as_os_str() == std::ffi::OsStr::new("--lifetime") => {
            Lifetime::parse(&args.next().ok_or("ARGS")?)?
        }
        Some(_) => return Err("ARGS"),
    };
    if args.next().is_some() { return Err("ARGS"); }
    Ok((runtime, artifact, lifetime))
}
fn main() {
    // SAFETY: only a lock-free flag is set in either handler. SIGPIPE becomes an
    // ordinary write failure so teardown still runs when the controller vanishes.
    unsafe {
        libc::signal(libc::SIGTERM, interrupted as libc::sighandler_t);
        libc::signal(libc::SIGINT, interrupted as libc::sighandler_t);
        libc::signal(libc::SIGPIPE, libc::SIG_IGN);
    }
    let result = arguments_from(std::env::args_os().skip(1))
        .and_then(|(runtime, artifact, lifetime)| run(&runtime, &artifact, lifetime));
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lifetime_policy_rejects_unbounded_or_ambiguous_configuration() {
        for source in [
            "{}",
            r#"{"run_seconds":110,"precursor_seconds":360,"compositor_grace_seconds":10,"extra":1}"#,
            r#"{"run_seconds":110,"run_seconds":110,"precursor_seconds":360,"compositor_grace_seconds":10}"#,
            r#"{"run_seconds":true,"precursor_seconds":360,"compositor_grace_seconds":10}"#,
            r#"{"run_seconds":110.0,"precursor_seconds":360,"compositor_grace_seconds":10}"#,
            r#"{"run_seconds":"110","precursor_seconds":360,"compositor_grace_seconds":10}"#,
            r#"{"run_seconds":-1,"precursor_seconds":360,"compositor_grace_seconds":10}"#,
            r#"{"run_seconds":0,"precursor_seconds":360,"compositor_grace_seconds":10}"#,
            r#"{"run_seconds":361,"precursor_seconds":360,"compositor_grace_seconds":10}"#,
            r#"{"run_seconds":110,"precursor_seconds":601,"compositor_grace_seconds":10}"#,
            r#"{"run_seconds":110,"precursor_seconds":360,"compositor_grace_seconds":0}"#,
            r#"{"run_seconds":110,"precursor_seconds":360,"compositor_grace_seconds":31}"#,
            r#"{"run_seconds":18446744073709551616,"precursor_seconds":360,"compositor_grace_seconds":10}"#,
        ] {
            assert!(LifetimePolicy::parse(source).is_err(), "accepted {source}");
        }
        assert!(LifetimePolicy::parse(
            r#"{"run_seconds":1,"precursor_seconds":600,"compositor_grace_seconds":30}"#).is_ok());
    }

    #[test]
    fn normal_and_precursor_watchdogs_have_distinct_bounded_lifetimes() {
        let policy = LifetimePolicy::parse(LIFETIME_POLICY).unwrap();
        let normal = policy.compositor_limit(Lifetime::Run);
        let precursor = policy.compositor_limit(Lifetime::Precursor);
        assert_eq!(normal, Duration::from_secs(120));
        assert_eq!(precursor, Duration::from_secs(370));
        assert_eq!(check_lifetime(Duration::from_secs(120), normal), Err("FIXTURE_TIMEOUT"));
        assert_eq!(check_lifetime(Duration::from_secs(121), normal), Err("FIXTURE_TIMEOUT"));
        assert_eq!(check_lifetime(Duration::from_secs(121), precursor), Ok(()));
        assert_eq!(check_lifetime(precursor - Duration::from_nanos(1), precursor), Ok(()));
        assert_eq!(check_lifetime(precursor, precursor), Err("FIXTURE_TIMEOUT"));
    }

    #[test]
    fn arguments_select_only_fixed_lifetimes_and_reject_numeric_overrides() {
        fn parse(suffix: &[&str]) -> Result<(PathBuf, PathBuf, Lifetime)> {
            arguments_from(["--runtime-dir", "/tmp/runtime", "--artifact-dir", "/tmp/artifact"]
                .into_iter().chain(suffix.iter().copied()).map(std::ffi::OsString::from))
        }
        assert_eq!(parse(&[]).unwrap(), (PathBuf::from("/tmp/runtime"), PathBuf::from("/tmp/artifact"), Lifetime::Run));
        assert_eq!(parse(&["--lifetime", "run"]).unwrap().2, Lifetime::Run);
        assert_eq!(parse(&["--lifetime", "precursor"]).unwrap().2, Lifetime::Precursor);
        for suffix in [
            vec!["--lifetime"],
            vec!["--lifetime", "unknown"],
            vec!["--lifetime", "360"],
            vec!["--run-limit-seconds", "360"],
            vec!["--lifetime", "precursor", "--lifetime", "run"],
            vec!["--lifetime", "precursor", "extra"],
            vec!["--artifact-dir", "/tmp/other"],
        ] {
            assert_eq!(parse(&suffix), Err("ARGS"), "accepted {suffix:?}");
        }
        assert_eq!(arguments_from(["--runtime-dir", "/tmp/runtime", "--lifetime", "precursor"]
            .into_iter().map(std::ffi::OsString::from)), Err("ARGS"));
    }

    #[test]
    fn chooser_unmap_between_key_down_and_up_only_accepts_reset_release() {
        use eidolon_shm_fixture::keyboard::{self, Stage};
        use std::{os::unix::net::UnixStream, sync::{Arc, mpsc}};

        fn next_stage(display: &mut Display<ProtocolState>, state: &mut ProtocolState,
                      observations: &mut Observations, steps: &mpsc::Receiver<Stage>,
                      started: Instant) -> Result<Stage> {
            loop {
                dispatch(display, state, observations, started.elapsed())?;
                match steps.try_recv() {
                    Ok(stage) => return Ok(stage),
                    Err(mpsc::TryRecvError::Disconnected) => return Err("TEST_CLIENT_EXIT"),
                    Err(mpsc::TryRecvError::Empty) => (),
                }
                if started.elapsed() > Duration::from_secs(10) { return Err("TEST_TIMEOUT"); }
                thread::sleep(Duration::from_millis(2));
            }
        }

        let mut display = Display::<ProtocolState>::new().unwrap();
        let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
        state.configure_keyboard(KeyboardLayout::Us).unwrap();
        let (server_socket, client_socket) = UnixStream::pair().unwrap();
        state.accept(&display.handle(), server_socket).unwrap();
        let (step_tx, steps) = mpsc::sync_channel(1);
        let (replies, reply_rx) = mpsc::sync_channel(1);
        let stop = Arc::new(AtomicBool::new(false));
        let client_stop = stop.clone();
        let client = thread::spawn(move || keyboard::run(client_socket, client_stop,
            keyboard::Control { steps: step_tx, replies: reply_rx }));
        let started = Instant::now();
        let mut observations = Observations::default();
        let key = |window, evdev, pressed| Request::Key { id: 1, window, evdev, pressed };
        let outcome = (|| -> Result<()> {
            if !matches!(next_stage(&mut display, &mut state, &mut observations, &steps, started)?, Stage::Mapped) {
                return Err("TEST_STAGE");
            }
            let window = state.windows().into_iter().find(|w| w.life == WindowLife::Mapped)
                .ok_or("TEST_NOT_MAPPED")?.window;
            // A mapped target without focus must still fail even with no held keys.
            if apply(&key(window, 28, false), &mut state) != Err("INPUT_NOT_FOCUSED") {
                return Err("TEST_LIVE_TARGET_RELEASE_ACCEPTED");
            }
            state.focus_window(window)?;
            replies.try_send(()).map_err(|_| "TEST_REPLY")?;
            if !matches!(next_stage(&mut display, &mut state, &mut observations, &steps, started)?, Stage::Focused) {
                return Err("TEST_STAGE");
            }
            apply(&key(window, 42, true), &mut state)?;
            let absent = WindowId::new(i64::MAX).map_err(|_| "TEST_ID")?;
            // An absent target is insufficient when native keys are still held.
            if state.input_report().pressed != 1
                || apply(&key(absent, 42, false), &mut state).is_ok()
                || state.input_report().pressed != 1
            {
                return Err("TEST_HELD_RELEASE_ACCEPTED");
            }
            apply(&key(window, 42, false), &mut state)?;
            replies.try_send(()).map_err(|_| "TEST_REPLY")?;
            if !matches!(next_stage(&mut display, &mut state, &mut observations, &steps, started)?, Stage::Keys) {
                return Err("TEST_STAGE");
            }
            replies.try_send(()).map_err(|_| "TEST_REPLY")?;
            if !matches!(next_stage(&mut display, &mut state, &mut observations, &steps, started)?, Stage::Reset) {
                return Err("TEST_STAGE");
            }
            apply(&key(window, 28, true), &mut state)?;
            if state.input_report().pressed != 1 { return Err("TEST_KEY_NOT_HELD"); }
            // The real client now attaches NULL, commits, and synchronizes.
            replies.try_send(()).map_err(|_| "TEST_REPLY")?;
            if !matches!(next_stage(&mut display, &mut state, &mut observations, &steps, started)?, Stage::Unmapped) {
                return Err("TEST_STAGE");
            }
            let reset = state.input_report();
            if reset.focus_token.is_some() || reset.pressed != 0
                || state.windows().iter().any(|w| w.window == window && w.life == WindowLife::Mapped)
            {
                return Err("TEST_NATIVE_RESET_MISSING");
            }
            apply(&key(window, 28, false), &mut state)?;
            if state.input_report().delivered != reset.delivered
                || apply(&key(window, 28, true), &mut state) != Err("INPUT_NOT_FOCUSED")
            {
                return Err("TEST_RESET_RELEASE_DELIVERED");
            }
            replies.try_send(()).map_err(|_| "TEST_REPLY")?;
            while !client.is_finished() {
                dispatch(&mut display, &mut state, &mut observations, started.elapsed())?;
                if started.elapsed() > Duration::from_secs(10) { return Err("TEST_TIMEOUT"); }
                thread::sleep(Duration::from_millis(2));
            }
            Ok(())
        })();
        // Even a failed assertion path releases the real wire client and joins it.
        stop.store(true, Ordering::Release);
        drop(replies);
        let client_outcome = client.join();
        let cleanup = teardown(&mut display, &mut state);
        outcome.unwrap();
        let report = client_outcome.unwrap().unwrap();
        cleanup.unwrap();
        let enter: Vec<_> = report.keys.iter().filter(|(code, _, _)| *code == 28)
            .map(|(_, state, _)| *state).collect();
        assert_eq!(enter, [1, 0], "only key-down and native-reset key-up reach the wire");
    }

    #[test]
    fn snapshot_preserves_nonzero_geometry_from_actual_wayland_client() {
        use eidolon_model::input::surface_hits;
        use eidolon_shm_fixture::state::{self as fixture, Scenario, Stage};
        use std::{os::unix::net::UnixStream, sync::{Arc, mpsc}};

        let mut display = Display::<ProtocolState>::new().unwrap();
        let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
        state.configure_keyboard(KeyboardLayout::Us).unwrap();
        let (server_socket, client_socket) = UnixStream::pair().unwrap();
        state.accept(&display.handle(), server_socket).unwrap();
        let (step_tx, steps) = mpsc::sync_channel(1);
        let (replies, reply_rx) = mpsc::sync_channel(1);
        let stop = Arc::new(AtomicBool::new(false));
        let client_stop = stop.clone();
        let client = thread::spawn(move || fixture::run(client_socket, Scenario::Tree, client_stop,
            fixture::Control { steps: step_tx, replies: reply_rx }));
        let started = Instant::now();
        let mut observations = Observations::default();
        let outcome = (|| -> Result<()> {
            let mut checked = false;
            while !client.is_finished() {
                dispatch(&mut display, &mut state, &mut observations, started.elapsed())?;
                if let Ok(stage) = steps.try_recv() {
                    if stage == Stage::FirstMapped {
                        // The real fixture commits xdg geometry (2,1,28,30)
                        // inside a 32x32 root surface, as CSD clients do.
                        let before = observations.snapshot(&display, &state);
                        let windows = before["windows"].as_array().ok_or("TEST_WINDOWS")?;
                        if windows.len() != 1
                            || windows[0]["geometry_origin"] != json!({"x":2,"y":1})
                            || windows[0]["logical_size"] != json!({"width":28,"height":30})
                        {
                            return Err("TEST_GEOMETRY_METADATA");
                        }
                        let window = state.windows()[0].window;
                        let native = state.latest_snapshot(window).ok_or("TEST_SNAPSHOT")?;
                        let surface_point = [12., 14.];
                        let logical_point = [surface_point[0] - 2., surface_point[1] - 1.];
                        let hit = surface_hits(&native, logical_point[0], logical_point[1])
                            .next().ok_or("TEST_SURFACE_HIT")?;
                        if hit.position != surface_point { return Err("TEST_GEOMETRY_SUBTRACTION"); }
                        // Tree does not bind wl_pointer. This fixture proves
                        // the nonzero geometry transform, not input delivery.
                        // A separate pointer client below checks actual wire
                        // events together with the exposed native counters.
                        checked = true;
                    }
                    replies.try_send(()).map_err(|_| "TEST_REPLY")?;
                }
                if started.elapsed() > Duration::from_secs(15) { return Err("TEST_TIMEOUT"); }
                thread::sleep(Duration::from_millis(2));
            }
            if !checked { return Err("TEST_GEOMETRY_NOT_OBSERVED"); }
            Ok(())
        })();
        stop.store(true, Ordering::Release);
        drop(replies);
        let client_outcome = client.join();
        let cleanup = teardown(&mut display, &mut state);
        outcome.unwrap();
        client_outcome.unwrap().unwrap();
        cleanup.unwrap();
    }

    #[test]
    fn snapshot_pointer_progress_matches_actual_client_events() {
        check_pointer_progress(false);
    }

    #[test]
    fn phased_button_holds_across_client_dispatch_and_releases_on_wire() {
        check_pointer_progress(true);
    }

    fn check_pointer_progress(phased: bool) {
        use eidolon_shm_fixture::pointer::{self as fixture, Event, Stage};
        use std::{os::unix::net::UnixStream, sync::{Arc, mpsc}};

        let mut display = Display::<ProtocolState>::new().unwrap();
        let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
        state.configure_keyboard(KeyboardLayout::Us).unwrap();
        let (server_socket, client_socket) = UnixStream::pair().unwrap();
        state.accept(&display.handle(), server_socket).unwrap();
        let (step_tx, steps) = mpsc::sync_channel(1);
        let (replies, reply_rx) = mpsc::sync_channel(1);
        let stop = Arc::new(AtomicBool::new(false));
        let client_stop = stop.clone();
        let client = thread::spawn(move || fixture::run(client_socket, client_stop,
            fixture::Control { steps: step_tx, replies: reply_rx }));
        let started = Instant::now();
        let mut observations = Observations::default();
        let mut observed_input = Value::Null;
        let outcome = (|| -> Result<()> {
            let mut checked = false;
            while !client.is_finished() {
                dispatch(&mut display, &mut state, &mut observations, started.elapsed())?;
                if let Ok(stage) = steps.try_recv() {
                    let send = match stage {
                        Stage::Mapped => Some(true),
                        Stage::HoverChild if phased => Some(false),
                        _ => None,
                    };
                    if let Some(pressed) = send {
                        // This fixture creates root_a first and binds both
                        // wl_pointer and wl_keyboard. (4,4) is on root_a,
                        // outside its child at (8,6), so the wire target is
                        // unambiguous and independent of child input regions.
                        let window = WindowId::new(1).map_err(|_| "TEST_ID")?;
                        let before = observations.snapshot(&display, &state);
                        let size = state.latest_snapshot(window).ok_or("TEST_SNAPSHOT")?.logical_size;
                        let uv = [4. / f64::from(size.width), 4. / f64::from(size.height)];
                        if phased {
                            // HoverChild is reached only after the real client
                            // has synchronized and received the preceding down.
                            if !pressed && before["input"]["pointer_pressed"] != 1 {
                                return Err("TEST_POINTER_NOT_HELD_ACROSS_DISPATCH");
                            }
                            apply(&Request::Button { id: 1, window, uv, pressed }, &mut state)?;
                        } else {
                            apply(&Request::Click { id: 1, window, uv }, &mut state)?;
                        }
                        let after = observations.snapshot(&display, &state);
                        observed_input = after["input"].clone();
                        if after["input"]["delivered"] != before["input"]["delivered"] {
                            return Err("TEST_POINTER_COUNTED_AS_KEYBOARD");
                        }
                        let held = phased && pressed;
                        let expected_buttons = if held { 1 } else { 2 };
                        let expected_pressed = if held { 1 } else { 0 };
                        if after["input"]["pointer_buttons"] != expected_buttons {
                            return Err("TEST_POINTER_BUTTON_COUNT");
                        }
                        if after["input"]["pointer_motions"].as_u64().is_none_or(|n| n == 0) {
                            return Err("TEST_POINTER_MOTION_COUNT");
                        }
                        if after["input"]["pointer_pressed"] != expected_pressed {
                            return Err("TEST_POINTER_HELD_STATE");
                        }
                        if after["input"]["pointer_window"] != json!(window) {
                            return Err("TEST_POINTER_WRONG_WINDOW");
                        }
                        checked = !held;
                    }
                    replies.try_send(()).map_err(|_| "TEST_REPLY")?;
                }
                if started.elapsed() > Duration::from_secs(15) { return Err("TEST_TIMEOUT"); }
                thread::sleep(Duration::from_millis(2));
            }
            if !checked { return Err("TEST_POINTER_NOT_OBSERVED"); }
            Ok(())
        })();
        stop.store(true, Ordering::Release);
        drop(replies);
        let client_outcome = client.join();
        let cleanup = teardown(&mut display, &mut state);
        assert_eq!(outcome, Ok(()), "synthetic pointer metadata: {observed_input}");
        let report = client_outcome.unwrap().unwrap();
        cleanup.unwrap();
        if phased {
            let checkpoint = report.checkpoints.iter().find(|c| c.stage == "HoverChild").unwrap();
            let held_buttons: Vec<_> = report.events[..checkpoint.events].iter().filter_map(|event| match event {
                Event::Button { surface, code, state, .. } => Some((*surface, *code, *state)),
                _ => None,
            }).collect();
            assert_eq!(held_buttons, vec![(Some("root_a"), 0x110, 1)]);
        }
        assert!(report.events.iter().any(|event| matches!(event,
            Event::Enter { surface: "root_a", position } if *position == [4., 4.])));
        let buttons: Vec<_> = report.events.iter().filter_map(|event| match event {
            Event::Button { surface, code, state, .. } => Some((*surface, *code, *state)),
            _ => None,
        }).collect();
        assert_eq!(buttons, vec![(Some("root_a"), 0x110, 1), (Some("root_a"), 0x110, 0)]);
        assert!(report.keyboard.keys.is_empty());
    }

    #[test]
    fn control_rejects_unknown_fields_bad_ids_and_invalid_input() {
        for line in [
            r#"{"op":"snapshot","id":0}"#,
            r#"{"op":"snapshot","id":2,"command":"anything"}"#,
            r#"{"op":"focus","id":2,"window":"01"}"#,
            r#"{"op":"key","id":2,"window":"1","evdev":0,"pressed":true}"#,
            r#"{"op":"click","id":2,"window":"1","uv":[1,0.5]}"#,
            r#"{"op":"button","id":2,"window":"1","uv":[1,0.5],"pressed":true}"#,
            r#"{"op":"button","id":2,"window":"1","uv":[0.5,0.5],"pressed":1}"#,
            r#"{"op":"button","id":2,"window":"1","uv":[0.5,0.5],"pressed":true,"button":2}"#,
            r#"{"op":"exec","id":2}"#,
        ] {
            assert!(parse_request(line.as_bytes(), 1).is_err());
        }
        assert!(parse_request(br#"{"op":"snapshot","id":1}"#, 1).is_err());
        assert!(parse_request(br#"{"op":"key","id":2,"window":"1","evdev":30,"pressed":true}"#, 1).is_ok());
        assert!(parse_request(br#"{"op":"button","id":2,"window":"1","uv":[0.5,0.5],"pressed":true}"#, 1).is_ok());
        assert!(parse_request(br#"{"op":"button","id":3,"window":"1","uv":[0.5,0.5],"pressed":false}"#, 2).is_ok());
        assert!(parse_request(&vec![b' '; MAX_LINE + 1], 0).is_err());
    }
    #[test]
    fn diagnostic_metadata_excludes_free_form_text() {
        assert_eq!(safe_app_id(Some("org.xfce.mousepad")), Some("org.xfce.mousepad"));
        assert_eq!(safe_app_id(Some("secret title /private/file")), None);
        assert_eq!(safe_app_id(Some("line\nbreak")), None);
        assert_eq!(safe_app_id(None), None);
    }
}
