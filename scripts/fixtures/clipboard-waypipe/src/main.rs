use eidolon_core::compositor::protocol::ProtocolState;
use eidolon_model::{
    ids::{SessionId, WindowId},
    keyboard::KeyboardLayout,
};
use eidolon_shm_fixture::clipboard;
use smithay::reexports::wayland_server::Display;
use std::{
    fs::{self, File},
    os::unix::{
        fs::PermissionsExt,
        net::{UnixListener, UnixStream},
        process::CommandExt,
    },
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};
struct ChildGroup(Child);
impl Drop for ChildGroup {
    fn drop(&mut self) {
        // leaderはwait前でID再利用されない。自分がspawnしたgroupだけに送る。
        unsafe {
            libc::kill(-(self.0.id() as i32), libc::SIGTERM);
        }
        let deadline = Instant::now() + Duration::from_secs(3);
        while Instant::now() < deadline {
            if self.0.try_wait().ok().flatten().is_some() {
                return;
            }
            thread::sleep(Duration::from_millis(5));
        }
        // wait前のleaderだけを使う。別の保存PIDへsignalしない。
        unsafe {
            libc::kill(-(self.0.id() as i32), libc::SIGKILL);
        }
        let _ = self.0.wait();
    }
}
fn main() {
    let mut args = std::env::args_os().skip(1);
    let repo = PathBuf::from(args.next().expect("repository"));
    let task = PathBuf::from(args.next().expect("artifact directory"));
    assert!(args.next().is_none() && repo.is_absolute() && task.is_absolute());
    let metadata: serde_json::Value =
        serde_json::from_slice(&fs::read(repo.join("native/generated/waypipe-host.json")).unwrap())
            .unwrap();
    let binary = metadata["executable"].as_str().unwrap();
    let root = PathBuf::from(format!("/tmp/eidolon-clip-proxy-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
    let listener = UnixListener::bind(root.join("wayland-0")).unwrap();
    listener.set_nonblocking(true).unwrap();
    let log = File::create(task.join("waypipe-child.log")).unwrap();
    log.set_permissions(fs::Permissions::from_mode(0o600))
        .unwrap();
    let common = |mode: &str| {
        let mut c = Command::new(binary);
        c.args([
            "--no-gpu",
            "--compress",
            "zstd",
            "--threads",
            "2",
            "--socket",
        ])
        .arg(root.join("channel"));
        c.env("XDG_RUNTIME_DIR", &root)
            .env("WAYLAND_DISPLAY", "wayland-0")
            .process_group(0)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::from(log.try_clone().unwrap()));
        if mode == "client" {
            c.arg("client");
        } else {
            c.args(["--display", "wayland-r", "server", "/usr/bin/sleep", "40"]);
        }
        c
    };
    let client = ChildGroup(common("client").spawn().unwrap());
    let deadline = Instant::now() + Duration::from_secs(40);
    while !root.join("channel").exists() {
        assert!(Instant::now() < deadline);
        thread::sleep(Duration::from_millis(5));
    }
    let server = ChildGroup(common("server").spawn().unwrap());
    let stop = Arc::new(AtomicBool::new(false));
    let child_stop = stop.clone();
    let remote = root.join("wayland-r");
    let (tx, rx) = mpsc::sync_channel(1);
    let (reply, answers) = mpsc::sync_channel(1);
    let worker = thread::spawn(move || {
        while !remote.exists() {
            if child_stop.load(Ordering::Acquire) || Instant::now() >= deadline {
                return Err("DISPLAY_TIMEOUT");
            }
            thread::sleep(Duration::from_millis(5));
        }
        let a = UnixStream::connect(&remote).map_err(|_| "CONNECT_A")?;
        let b = UnixStream::connect(&remote).map_err(|_| "CONNECT_B")?;
        clipboard::run(
            [a, b],
            child_stop,
            clipboard::Control {
                focus: tx,
                replies: answers,
            },
        )
    });
    let mut display = Display::<ProtocolState>::new().unwrap();
    let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
    state.configure_keyboard(KeyboardLayout::Us).unwrap();
    let drive = (|| {
        while !worker.is_finished() {
            if Instant::now() >= deadline {
                return Err("SERVER_TIMEOUT");
            }
            loop {
                match listener.accept() {
                    Ok((stream, _)) => {
                        state.accept(&display.handle(), stream)?;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(_) => return Err("ACCEPT"),
                }
            }
            display
                .dispatch_clients(&mut state)
                .map_err(|_| "DISPATCH")?;
            state.finish_dispatch(&display.handle());
            if let Ok(window) = rx.try_recv() {
                state.focus_window(WindowId::new(i64::from(window)).unwrap())?;
                reply.send(()).map_err(|_| "REPLY")?;
            }
            display.flush_clients().map_err(|_| "FLUSH")?;
            thread::sleep(Duration::from_millis(1));
        }
        Ok(())
    })();
    if drive.is_err() {
        stop.store(true, Ordering::Release);
    }
    let result = worker.join().unwrap();
    drop(server);
    drop(client);
    drop(listener);
    for _ in 0..10 {
        display.dispatch_clients(&mut state).unwrap();
        state.finish_dispatch(&display.handle());
    }
    let backend = display.handle().backend_handle();
    let mut clients = vec![];
    let mut objects = 0;
    backend.with_all_clients(|client| clients.push(client));
    for client in &clients {
        let _ = backend.with_all_objects_for(client.clone(), |_| objects += 1);
    }
    let clip = state.clipboard_report();
    let clean = state.surface_count() == 0
        && state.windows().is_empty()
        && state.pixel_bytes() == 0
        && state.pool_usage().bytes == 0
        && !clip.selection
        && clip.active == 0;
    let clean = clean && clients.is_empty() && objects == 0;
    let client_value = serde_json::to_value(result.as_ref().ok()).unwrap();
    let required = client_value
        .as_object()
        .is_some_and(|m| m.iter().all(|(k, v)| k == "stalled_receiver" || v == true));
    let buffered = result.as_ref().is_ok_and(|r| !r.stalled_receiver)
        && clip.completed == 3
        && clip.timed_out == 1;
    let backpressure = result.as_ref().is_ok_and(|r| r.stalled_receiver)
        && clip.completed == 2
        && clip.timed_out == 2;
    let valid = drive.is_ok()
        && result.is_ok()
        && required
        && clean
        && (buffered || backpressure)
        && clip.oversized == 1
        && clip.invalid_utf8 == 1
        && clip.cancelled >= 2;
    let status = if !valid {
        "failed"
    } else if buffered {
        "observed_intermediary_buffering"
    } else {
        "observed_core_backpressure"
    };
    let proof = serde_json::json!({"status":status,"end_to_end_timeout_verified":false,"core_bounds_verified":valid,"drive_error":drive.err(),"client_error":result.as_ref().err(),"client":result.as_ref().ok(),"clipboard":state.clipboard_report(),"clients_after":clients.len(),"objects_after":objects,"surfaces_after":state.surface_count(),"windows_after":state.windows().len(),"pixel_bytes_after":state.pixel_bytes(),"pool_bytes_after":state.pool_usage().bytes,"waypipe_version":metadata["version"],"waypipe_sha256":metadata["sha256"]});
    fs::write(
        task.join("result.json"),
        serde_json::to_vec_pretty(&proof).unwrap(),
    )
    .unwrap();
    println!("{proof}");
    fs::remove_dir_all(&root).unwrap();
    assert!(valid, "unexpected clipboard forwarding result");
}
