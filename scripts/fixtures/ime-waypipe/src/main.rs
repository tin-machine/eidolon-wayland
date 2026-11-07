//! 実Eidolon ProtocolStateと固定Waypipe両端を接続するhost限定harness。
//! Python clientは別processでraw Wayland/SHMを送受信する。入力本文は結果へ出さない。
use eidolon_core::compositor::protocol::ProtocolState;
use eidolon_model::{
    ids::SessionId,
    ime::{ImePreedit, ImeSnapshot, ImeTransaction},
    keyboard::KeyboardLayout,
    window::WindowLife,
};
use smithay::reexports::wayland_server::Display;
use std::{
    fs::{self, File},
    io::{Read, Write},
    os::unix::{
        fs::PermissionsExt,
        net::{UnixListener, UnixStream},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::{Child, Command, ExitStatus, Stdio},
    thread,
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, &'static str>;
fn require(value: bool, tag: &'static str) -> Result<()> {
    if value { Ok(()) } else { Err(tag) }
}

struct Group {
    child: Child,
    reaped: bool,
}
impl Group {
    fn spawn(command: &mut Command) -> Result<Self> {
        command.process_group(0).stdin(Stdio::null());
        Ok(Self {
            child: command.spawn().map_err(|_| "SPAWN")?,
            reaped: false,
        })
    }
    fn exited(&self) -> Result<bool> {
        let mut info = std::mem::MaybeUninit::<libc::siginfo_t>::zeroed();
        // SAFETY: 自分の未wait childだけをWNOWAITで観測し、PID再利用を防ぐ。
        let result = unsafe {
            libc::waitid(
                libc::P_PID,
                self.child.id(),
                info.as_mut_ptr(),
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        };
        if result != 0 {
            return Err("WAITID");
        }
        Ok(unsafe { info.assume_init().si_pid() } != 0)
    }
    fn reap(&mut self) -> Result<ExitStatus> {
        let status = self.child.wait().map_err(|_| "WAIT")?;
        self.reaped = true;
        Ok(status)
    }
    fn finish(&mut self) -> Result<()> {
        if self.reaped {
            return Ok(());
        }
        unsafe {
            libc::kill(-(self.child.id() as i32), libc::SIGTERM);
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        while !self.exited()? && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        // 未wait leaderのIDがまだ予約されている間にfork先も回収する。
        unsafe {
            libc::kill(-(self.child.id() as i32), libc::SIGKILL);
        }
        self.reap()?;
        Ok(())
    }
}
impl Drop for Group {
    fn drop(&mut self) {
        let _ = self.finish();
    }
}

struct RuntimeDir(PathBuf);
impl Drop for RuntimeDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn counts(display: &Display<ProtocolState>) -> (usize, usize) {
    let backend = display.handle().backend_handle();
    let mut clients = vec![];
    backend.with_all_clients(|client| clients.push(client));
    let mut objects = 0;
    for client in &clients {
        let _ = backend.with_all_objects_for(client.clone(), |_| objects += 1);
    }
    (clients.len(), objects)
}

fn tx(
    snapshot: &ImeSnapshot,
    sequence: u64,
    commit: Option<&str>,
    preedit: Option<&str>,
) -> ImeTransaction {
    ImeTransaction {
        target: snapshot.target,
        edit_revision: snapshot.edit_revision,
        sequence,
        commit: commit.map(str::to_owned),
        preedit: preedit.map(|text| ImePreedit {
            text: text.into(),
            cursor_begin: text.len() as i32,
            cursor_end: text.len() as i32,
        }),
        delete_before: 0,
        delete_after: 0,
    }
}

#[derive(Default)]
struct Driver {
    first: Option<ImeSnapshot>,
    second: Option<ImeSnapshot>,
    recovered: Option<ImeSnapshot>,
    geometry: Option<ImeSnapshot>,
    queued_geometry: Option<ImeTransaction>,
    checkpoints: usize,
    geometry_race_preserved: bool,
}
impl Driver {
    fn step(&mut self, stage: &str, state: &mut ProtocolState) -> Result<bool> {
        match stage {
            "focus_a" | "focus_a_again" | "focus_b" => {
                let mut windows: Vec<_> = state
                    .windows()
                    .into_iter()
                    .filter(|w| w.life == WindowLife::Mapped)
                    .collect();
                windows.sort_by_key(|w| w.window);
                require(windows.len() == 2, "MAPPED_WINDOWS")?;
                state.focus_window(windows[usize::from(stage == "focus_b")].window)?;
            }
            "normal_a" => {
                let snapshot = state.ime_snapshot(5).ok_or("ACTIVE_A")?;
                state.apply_ime(5, &tx(&snapshot, 1, None, Some("にほん")))?;
                state.apply_ime(5, &tx(&snapshot, 2, Some("日本語"), None))?;
                self.first = Some(snapshot);
            }
            "stale_a" => {
                let snapshot = self.first.as_ref().ok_or("NO_A")?;
                let mut stale = tx(snapshot, 3, Some("古い文字"), None);
                stale.target.serial = stale.target.serial.wrapping_sub(1);
                require(
                    state.apply_ime(5, &stale) == Err("STALE"),
                    "STALE_A_ACCEPTED",
                )?;
                state.apply_ime(5, &tx(snapshot, 3, None, None))?;
            }
            "capture_geometry" => {
                let snapshot = state.ime_snapshot(5).ok_or("GEOMETRY_CAPTURE")?;
                state.apply_ime(
                    5,
                    &tx(&snapshot, snapshot.last_sequence + 1, None, Some("編集中")),
                )?;
                let snapshot = state.ime_snapshot(5).ok_or("GEOMETRY_CAPTURE")?;
                self.queued_geometry = Some(tx(
                    &snapshot,
                    snapshot.last_sequence + 1,
                    Some("幾何確定"),
                    Some("継続"),
                ));
                self.geometry = Some(snapshot);
            }
            "apply_geometry" => {
                let old = self.geometry.as_ref().ok_or("NO_GEOMETRY_CAPTURE")?;
                let current = state.ime_snapshot(5).ok_or("GEOMETRY_ACTIVE")?;
                require(
                    old.target.activation == current.target.activation
                        && old.target.focus_token == current.target.focus_token
                        && old.target.serial != current.target.serial
                        && old.edit_revision == current.edit_revision
                        && old.surrounding == current.surrounding
                        && old.content_hint == current.content_hint
                        && old.content_purpose == current.content_purpose
                        && old.change_cause == current.change_cause
                        && old.cursor_rectangle != current.cursor_rectangle,
                    "NOT_GEOMETRY_ONLY",
                )?;
                // queue済みDTOを最新serialへ書き換えずそのまま適用する。
                let queued = self.queued_geometry.as_ref().ok_or("NO_QUEUED_GEOMETRY")?;
                require(queued.target.serial == old.target.serial, "QUEUE_CHANGED")?;
                state.apply_ime(5, queued)?;
                self.geometry_race_preserved = state
                    .ime_snapshot(5)
                    .is_some_and(|snapshot| snapshot.last_sequence == queued.sequence);
                require(self.geometry_race_preserved, "GEOMETRY_SEQUENCE")?;
            }
            "late_a" => {
                let old = self.first.as_ref().ok_or("NO_A")?;
                require(
                    state.apply_ime(5, &tx(old, 4, Some("別窓へ漏らさない"), None)) == Err("STALE"),
                    "LATE_A_ACCEPTED",
                )?;
                let snapshot = state.ime_snapshot(5).ok_or("ACTIVE_B")?;
                require(snapshot.target.window != old.target.window, "WINDOW_SWITCH")?;
                state.apply_ime(5, &tx(&snapshot, 1, Some("別窓"), Some("べつ")))?;
                self.second = Some(snapshot);
            }
            "reset" => {
                state.reset_input();
                require(state.ime_snapshot(5).is_none(), "RESET_ACTIVE")?;
                require(
                    state.apply_ime(
                        5,
                        &tx(
                            self.second.as_ref().ok_or("NO_B")?,
                            2,
                            Some("reset後"),
                            None,
                        ),
                    ) == Err("NOT_READY"),
                    "RESET_ACCEPTED",
                )?;
            }
            "recover" => {
                let snapshot = state.ime_snapshot(5).ok_or("ACTIVE_RECOVERY")?;
                require(
                    snapshot.target.activation
                        != self.first.as_ref().ok_or("NO_A")?.target.activation,
                    "GENERATION_RECOVERY",
                )?;
                state.apply_ime(5, &tx(&snapshot, 1, Some("復帰"), None))?;
                self.recovered = Some(snapshot);
            }
            "destroyed" => {
                require(state.ime_snapshot(5).is_none(), "DESTROY_ACTIVE")?;
                require(
                    state.apply_ime(
                        5,
                        &tx(
                            self.recovered.as_ref().ok_or("NO_RECOVERY")?,
                            2,
                            Some("破棄後"),
                            None,
                        ),
                    ) == Err("NOT_READY"),
                    "DESTROY_ACCEPTED",
                )?;
            }
            "complete" => {
                self.checkpoints += 1;
                return Ok(true);
            }
            _ => return Err("UNKNOWN_STAGE"),
        }
        self.checkpoints += 1;
        Ok(false)
    }
}

fn run(repo: &Path, task: &Path) -> Result<()> {
    let metadata: serde_json::Value = serde_json::from_slice(
        &fs::read(repo.join("native/generated/waypipe-host.json")).map_err(|_| "METADATA_READ")?,
    )
    .map_err(|_| "METADATA_JSON")?;
    require(
        metadata["version"] == "0.11.3" && metadata["target"] == "host",
        "WAYPIPE_METADATA",
    )?;
    let binary = metadata["executable"].as_str().ok_or("WAYPIPE_PATH")?;
    let runtime = RuntimeDir(PathBuf::from(format!(
        "/tmp/eidolon-ime-waypipe-{}",
        std::process::id()
    )));
    fs::create_dir(&runtime.0).map_err(|_| "RUNTIME_CREATE")?;
    fs::set_permissions(&runtime.0, fs::Permissions::from_mode(0o700))
        .map_err(|_| "RUNTIME_MODE")?;
    let listener = UnixListener::bind(runtime.0.join("wayland-0")).map_err(|_| "LISTEN")?;
    listener.set_nonblocking(true).map_err(|_| "NONBLOCK")?;
    let control = UnixListener::bind(runtime.0.join("control")).map_err(|_| "CONTROL_LISTEN")?;
    control.set_nonblocking(true).map_err(|_| "NONBLOCK")?;
    let log = File::create(task.join("waypipe-child.log")).map_err(|_| "LOG_CREATE")?;
    log.set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(|_| "LOG_MODE")?;
    let common = || {
        let mut command = Command::new(binary);
        command
            .args([
                "--no-gpu",
                "--compress",
                "zstd",
                "--threads",
                "2",
                "--socket",
            ])
            .arg(runtime.0.join("channel"))
            .env("XDG_RUNTIME_DIR", &runtime.0)
            .env("WAYLAND_DISPLAY", "wayland-0")
            .env_remove("WAYLAND_DEBUG")
            .stdout(Stdio::null())
            .stderr(Stdio::from(log.try_clone().expect("log clone")));
        command
    };
    let mut local_command = common();
    local_command.arg("client");
    let mut local = Group::spawn(&mut local_command)?;
    let deadline = Instant::now() + Duration::from_secs(30);
    while !runtime.0.join("channel").exists() {
        require(
            Instant::now() < deadline && !local.exited()?,
            "WAYPIPE_CLIENT_START",
        )?;
        thread::sleep(Duration::from_millis(5));
    }
    let mut remote_command = common();
    remote_command.args(["--display", "wayland-r", "server", "/usr/bin/sleep", "40"]);
    let mut remote = Group::spawn(&mut remote_command)?;
    let mut python = Command::new("python3");
    python
        .arg(task.join("ime_client.py"))
        .arg(runtime.0.join("wayland-r"))
        .arg(runtime.0.join("control"))
        .arg(task.join("client-result.json"))
        .env_remove("WAYLAND_DEBUG")
        .stdout(Stdio::null())
        .stderr(Stdio::from(log.try_clone().map_err(|_| "LOG_CLONE")?));
    let mut client = Group::spawn(&mut python)?;
    let mut display = Display::<ProtocolState>::new().map_err(|_| "DISPLAY")?;
    let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
    state.configure_keyboard(KeyboardLayout::Us)?;
    state.set_ime_backend_enabled(true);
    let mut controller: Option<UnixStream> = None;
    let mut commands = vec![];
    let mut driver = Driver::default();
    let mut completed = false;
    let mut connections = 0;
    let drive = (|| {
        while !completed || !client.exited()? {
            require(Instant::now() < deadline, "DRIVE_TIMEOUT")?;
            if !completed && client.exited()? {
                return Err("INDEPENDENT_CLIENT_EXITED");
            }
            loop {
                match listener.accept() {
                    Ok((stream, _)) => {
                        state.accept(&display.handle(), stream)?;
                        connections += 1;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(_) => return Err("ACCEPT"),
                }
            }
            display
                .dispatch_clients(&mut state)
                .map_err(|_| "DISPATCH")?;
            state.finish_dispatch(&display.handle());
            if controller.is_none() {
                match control.accept() {
                    Ok((stream, _)) => {
                        stream.set_nonblocking(true).map_err(|_| "NONBLOCK")?;
                        controller = Some(stream);
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => (),
                    Err(_) => return Err("CONTROL_ACCEPT"),
                }
            }
            if let Some(controller) = &mut controller {
                let mut chunk = [0; 256];
                match controller.read(&mut chunk) {
                    Ok(n) => commands.extend_from_slice(&chunk[..n]),
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => (),
                    Err(_) => return Err("CONTROL_READ"),
                }
                require(commands.len() <= 1024, "CONTROL_BOUND")?;
                while let Some(end) = commands.iter().position(|b| *b == b'\n') {
                    let stage =
                        std::str::from_utf8(&commands[..end]).map_err(|_| "CONTROL_UTF8")?;
                    completed |= driver.step(stage, &mut state)?;
                    commands.drain(..=end);
                    controller.write_all(b"ok\n").map_err(|_| "CONTROL_REPLY")?;
                }
            }
            display.flush_clients().map_err(|_| "FLUSH")?;
            thread::sleep(Duration::from_millis(1));
        }
        Ok(())
    })();
    let independent_ok = if client.exited()? {
        client.reap()?.success()
    } else {
        client.finish()?;
        false
    };
    remote.finish()?;
    local.finish()?;
    let cleanup_deadline = Instant::now() + Duration::from_secs(3);
    loop {
        display
            .dispatch_clients(&mut state)
            .map_err(|_| "CLEANUP_DISPATCH")?;
        state.finish_dispatch(&display.handle());
        if counts(&display).0 == 0 && state.pool_usage().bytes == 0 {
            break;
        }
        require(Instant::now() < cleanup_deadline, "CLEANUP_TIMEOUT")?;
        thread::sleep(Duration::from_millis(2));
    }
    let (clients, objects) = counts(&display);
    let ime = state.ime_report();
    let client_result: serde_json::Value = fs::read(task.join("client-result.json"))
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or(serde_json::Value::Null);
    let cleanup_ok = clients == 0
        && objects == 0
        && state.surface_count() == 0
        && state.windows().is_empty()
        && state.pixel_bytes() == 0
        && state.pool_usage().bytes == 0
        && ime.objects == 0;
    let valid = drive.is_ok()
        && independent_ok
        && completed
        && cleanup_ok
        && connections > 0
        && client_result["state"] == "pass";
    let proof = serde_json::json!({
        "schema":1,"state":if valid {"pass"} else {"fail"},"fixture_revision":"eidolon-ime-waypipe-protocol-v2",
        "server":"Eidolon ProtocolState","synthetic_input":true,"physical_input_verified":false,"uid":unsafe{libc::getuid()},
        "waypipe_version":metadata["version"],"waypipe_sha256":metadata["sha256"],"waypipe_ends":2,
        "connections":connections,"checkpoints":driver.checkpoints,"client":client_result,
        "geometry_race_preserved":driver.geometry_race_preserved,"queued_payload_unchanged":true,
        "ime":ime,"clients_after":clients,"objects_after":objects,"surfaces_after":state.surface_count(),
        "windows_after":state.windows().len(),"pixel_bytes_after":state.pixel_bytes(),"pool_bytes_after":state.pool_usage().bytes,
        "owned_groups_waited":3,"drive_error":drive.err(),"fixed_payload_only":true,"payload_logged":false
    });
    fs::write(
        task.join("result.json"),
        serde_json::to_vec_pretty(&proof).map_err(|_| "RESULT_JSON")?,
    )
    .map_err(|_| "RESULT_WRITE")?;
    println!("{proof}");
    require(valid, "CHECK_FAILED")
}

fn main() {
    let mut args = std::env::args_os().skip(1);
    let repo = PathBuf::from(args.next().expect("repository"));
    let task = PathBuf::from(args.next().expect("artifact directory"));
    if !repo.is_absolute() || !task.is_absolute() || args.next().is_some() {
        std::process::exit(2);
    }
    if let Err(error) = run(&repo, &task) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
