//! host固定fixture用compositor。virtual frame clockでSHMを検証し、GPU/HMD成功とはしない。
use eidolon_core::compositor::protocol::ProtocolState;
use eidolon_model::ids::SessionId;
use sha2::{Digest, Sha256};
use smithay::reexports::wayland_server::Display;
use std::{
    fs,
    os::unix::{
        fs::{MetadataExt, PermissionsExt},
        net::UnixListener,
    },
    path::PathBuf,
    thread,
    time::{Duration, Instant},
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    if arguments.len() != 1 {
        return Err("FIXTURE_ARGUMENT".into());
    }
    let directory = PathBuf::from(&arguments[0]);
    let meta = fs::symlink_metadata(&directory)?;
    if !directory.is_absolute()
        || !meta.is_dir()
        || meta.uid() != unsafe { libc::getuid() }
        || meta.permissions().mode() & 0o777 != 0o700
    {
        return Err("FIXTURE_DIRECTORY".into());
    }
    let socket = directory.join("w");
    let listener = UnixListener::bind(&socket)?;
    listener.set_nonblocking(true)?;
    let mut display = Display::<ProtocolState>::new()?;
    let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
    let started = Instant::now();
    let mut frames = [false; 100];
    let mut connections = 0;
    let result = (|| -> Result<(), &'static str> {
        loop {
            if started.elapsed() > Duration::from_secs(20) {
                return Err("FIXTURE_TIMEOUT");
            }
            loop {
                match listener.accept() {
                    Ok((stream, _)) => {
                        connections += 1;
                        if connections > 4 {
                            return Err("EXTRA_CLIENT");
                        }
                        state.accept(&display.handle(), stream)?;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(_) => return Err("ACCEPT_FAILED"),
                }
            }
            display
                .dispatch_clients(&mut state)
                .map_err(|_| "DISPATCH_FAILED")?;
            state.finish_dispatch(&display.handle());
            let windows = state.windows();
            if windows.len() > 1 {
                return Err("EXTRA_WINDOW");
            }
            for window in windows {
                if let Some(snapshot) = state.latest_snapshot(window.window) {
                    for layer in &snapshot.layers {
                        let pixels = layer.pixels.pixels();
                        let frame = pixels.first().copied().unwrap_or(255) as usize;
                        if frame < 100 {
                            let actual: String = Sha256::digest(pixels)
                                .iter()
                                .map(|b| format!("{b:02x}"))
                                .collect();
                            if actual == eidolon_shm_fixture::expected_hash(frame as u32) {
                                frames[frame] = true;
                            }
                        }
                    }
                }
                state.frame_callbacks(window.window, started.elapsed(), true, true, true);
            }
            display.flush_clients().map_err(|_| "FLUSH_FAILED")?;
            if connections >= 1
                && frames.iter().all(|value| *value)
                && state.windows().is_empty()
                && state.surface_count() == 0
                && state.pool_usage().bytes == 0
            {
                let mut live = 0;
                display
                    .handle()
                    .backend_handle()
                    .with_all_clients(|_| live += 1);
                if live == 0 {
                    break;
                }
            }
            thread::sleep(Duration::from_millis(1));
        }
        Ok(())
    })();
    drop(listener);
    fs::remove_file(socket)?;
    let backend = display.handle().backend_handle();
    let mut clients = 0;
    let mut objects = 0;
    backend.with_all_clients(|client| {
        clients += 1;
        let _ = backend.with_all_objects_for(client, |_| objects += 1);
    });
    let clean =
        clients == 0 && objects == 0 && state.pixel_bytes() == 0 && state.pool_usage().bytes == 0;
    let report = serde_json::json!({"schema":1,"case":"linux-shm-server","virtual_frame_clock":true,"gpu_verified":false,
        "connections":connections,"verified_frames":frames.iter().filter(|v| **v).count(),"copies":state.counters.copies,
        "callbacks":state.counters.callbacks,"clients_after":clients,"objects_after":objects,"pixel_bytes_after":state.pixel_bytes(),
        "pool_bytes_after":state.pool_usage().bytes,"cleaned":clean,"result":if result.is_ok() && clean {"pass"}else{"fail"},"error":result.err()});
    fs::write(
        directory.join("server-result.json"),
        serde_json::to_vec(&report)?,
    )?;
    if report["result"] != "pass" {
        return Err("SHM_FAILED".into());
    }
    Ok(())
}
