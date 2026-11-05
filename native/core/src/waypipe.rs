//! APK同梱の固定C版Waypipe。所有するprocess groupを回収してからruntimeを停止する。
use crate::{
    compositor::protocol::{ClientState, ProtocolState},
    helper::{Report, RuntimeDirectory},
};
use eidolon_model::ids::SessionId;
use serde::Serialize;
use sha2::{Digest, Sha256};
use smithay::reexports::wayland_server::Display;
use std::{
    fs,
    io::Read,
    os::{
        fd::AsRawFd,
        unix::{
            net::{UnixListener, UnixStream},
            process::CommandExt,
        },
    },
    path::Path,
    process::{Child, Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

type Error = &'static str;
const OUTPUT_LIMIT: usize = 64 * 1024;

#[derive(Clone, Debug, Default, Serialize)]
pub struct Proof {
    pub nonce: String,
    pub executable_sha256: String,
    pub child_uid: u32,
    pub child_context: String,
    pub features: Vec<String>,
    pub unavailable: Vec<String>,
    pub compositor_connections: u32,
    pub transfer_connections: u32,
    pub owned_groups: usize,
    pub waited: u32,
    pub stop_ms: u64,
    pub clients_after: usize,
    pub objects_after: usize,
    pub socket_cleaned: bool,
}

/// Childが未waitの間だけgroupへsignalを送り、PID再利用後にsignalしない。
/// pipeはnonblockingで同じI/O ownerがdrainし、fork先のpipeでreader joinが止まらない。
pub(crate) struct OwnedProcess {
    child: Child,
    reaped: bool,
    output: Vec<u8>,
    error_output: Vec<u8>,
}
impl OwnedProcess {
    fn identity(&self, uid: u32) -> Result<String, Error> {
        let status = fs::read_to_string(format!("/proc/{}/status", self.child.id()))
            .map_err(|_| "CHILD_IDENTITY")?;
        let ids: Vec<_> = status
            .lines()
            .find_map(|line| line.strip_prefix("Uid:"))
            .ok_or("CHILD_IDENTITY")?
            .split_whitespace()
            .map(str::parse::<u32>)
            .collect::<Result<_, _>>()
            .map_err(|_| "CHILD_IDENTITY")?;
        if ids.len() != 4 || ids.iter().any(|value| *value != uid) {
            return Err("CHILD_IDENTITY");
        }
        let context = fs::read_to_string(format!("/proc/{}/attr/current", self.child.id()))
            .map_err(|_| "CHILD_IDENTITY")?;
        let context = context.trim().trim_end_matches('\0');
        if context.is_empty() || context.len() > 1024 {
            return Err("CHILD_IDENTITY");
        }
        Ok(context.into())
    }
    pub(crate) fn spawn(command: &mut Command) -> Result<Self, Error> {
        let child = command
            .process_group(0)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|_| "HELPER_EXEC_FAILED")?;
        let owned = Self {
            child,
            reaped: false,
            output: vec![],
            error_output: vec![],
        };
        for fd in [
            owned.child.stdout.as_ref().unwrap().as_raw_fd(),
            owned.child.stderr.as_ref().unwrap().as_raw_fd(),
        ] {
            // SAFETY: fdはこのChildのpipeを借用し、所有者は同じthreadにいる。
            let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
            if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0
            {
                return Err("HELPER_EXEC_FAILED");
            }
        }
        Ok(owned)
    }
    pub(crate) fn exited(&self) -> Result<bool, Error> {
        if self.reaped {
            return Ok(true);
        }
        let mut info = std::mem::MaybeUninit::<libc::siginfo_t>::zeroed();
        // SAFETY: infoの領域は有効。所有するChildだけをWNOWAITで観測し、group回収までPIDを保持する。
        let result = unsafe {
            libc::waitid(
                libc::P_PID,
                self.child.id(),
                info.as_mut_ptr(),
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        };
        if result != 0 {
            return Err("HELPER_EXEC_FAILED");
        }
        Ok(unsafe { info.assume_init().si_pid() } != 0)
    }
    fn drain(pipe: &mut impl Read, retained: &mut Vec<u8>) -> Result<(), Error> {
        let mut chunk = [0; 4096];
        // 一度のpollでchildの無限stdoutに占有されない。
        for _ in 0..32 {
            match pipe.read(&mut chunk) {
                Ok(0) => break,
                Ok(count) => {
                    let keep = count.min(OUTPUT_LIMIT - retained.len());
                    retained.extend_from_slice(&chunk[..keep]);
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => return Err("HELPER_EXEC_FAILED"),
            }
        }
        Ok(())
    }
    pub(crate) fn pump(&mut self) -> Result<(), Error> {
        if let Some(pipe) = &mut self.child.stdout {
            Self::drain(pipe, &mut self.output)?;
        }
        if let Some(pipe) = &mut self.child.stderr {
            Self::drain(pipe, &mut self.error_output)?;
        }
        Ok(())
    }
    fn signal(&self, signal: i32) {
        if !self.reaped {
            // SAFETY: spawn直後の自分のprocess group。leaderをwaitする前だけ使用する。
            unsafe {
                libc::kill(-(self.child.id() as i32), signal);
            }
        }
    }
    pub(crate) fn finish(&mut self) -> Result<u64, Error> {
        if self.reaped {
            return Ok(0);
        }
        let started = Instant::now();
        self.signal(libc::SIGTERM);
        while !self.exited()? && started.elapsed() < Duration::from_secs(3) {
            let _ = self.pump();
            thread::sleep(Duration::from_millis(5));
        }
        // leaderが既に終了していても、未waitのPIDを保持する間にfork先を確実に終了させる。
        self.signal(libc::SIGKILL);
        self.child.wait().map_err(|_| "CLEANUP_FAILED")?;
        self.reaped = true;
        self.pump()?;
        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            // wait後はread-only観測のみ。保存したgroup番号へsignalしない。
            let exists = unsafe { libc::kill(-(self.child.id() as i32), 0) } == 0;
            if !exists {
                break;
            }
            if Instant::now() >= deadline {
                return Err("CLEANUP_FAILED");
            }
            thread::sleep(Duration::from_millis(5));
        }
        Ok(started.elapsed().as_millis() as u64)
    }
    fn capture(self, stop: &AtomicBool) -> Result<Vec<u8>, Error> {
        self.capture_until(stop, Duration::from_secs(3))
    }
    pub(crate) fn capture_until(
        mut self,
        stop: &AtomicBool,
        duration: Duration,
    ) -> Result<Vec<u8>, Error> {
        let deadline = Instant::now() + duration;
        loop {
            self.pump()?;
            if stop.load(Ordering::Acquire) {
                return Err("CANCELLED");
            }
            if self.exited()? {
                break;
            }
            if Instant::now() >= deadline {
                return Err("HELPER_EXEC_FAILED");
            }
            thread::sleep(Duration::from_millis(5));
        }
        self.signal(libc::SIGKILL);
        let status = self.child.wait().map_err(|_| "HELPER_EXEC_FAILED")?;
        self.reaped = true;
        self.pump()?;
        if !status.success() {
            return Err("HELPER_EXEC_FAILED");
        }
        Ok(std::mem::take(&mut self.output))
    }
}
impl Drop for OwnedProcess {
    fn drop(&mut self) {
        let _ = self.finish();
    }
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct ClientReport {
    pub ready: bool,
    pub workers: usize,
    pub owned_groups: usize,
    pub waited: bool,
    pub cancelled: bool,
    pub uid: u32,
    pub child_uid: u32,
    pub child_context: String,
    pub version: String,
    pub executable_sha256: String,
    pub stop_ms: u64,
    pub error: Option<Error>,
}

/// protocol ownerは起動/停止flagとsnapshotだけを扱い、process待ちはこのI/O workerが所有する。
pub(crate) struct LocalClient {
    state: Arc<Mutex<ClientReport>>,
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}
impl LocalClient {
    pub(crate) fn start(executable: &Path, directory: &Path) -> Result<Self, Error> {
        let (executable, directory) = (executable.to_path_buf(), directory.to_path_buf());
        let state = Arc::new(Mutex::new(ClientReport {
            workers: 1,
            uid: unsafe { libc::getuid() },
            ..Default::default()
        }));
        let stop = Arc::new(AtomicBool::new(false));
        let (output, cancel) = (state.clone(), stop.clone());
        let worker = thread::Builder::new()
            .name("eidolon-waypipe".into())
            .spawn(move || {
                let update = |action: &dyn Fn(&mut ClientReport)| {
                    action(&mut output.lock().unwrap_or_else(|e| e.into_inner()))
                };
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    let bytes = fs::read(&executable).map_err(|_| "HELPER_EXEC_FAILED")?;
                    let hash: String = Sha256::digest(bytes)
                        .iter()
                        .map(|b| format!("{b:02x}"))
                        .collect();
                    let version = OwnedProcess::spawn(Command::new(&executable).arg("--version"))?
                        .capture(&cancel)?;
                    if version
                        != b"waypipe 0.11.3\nfeatures: lz4 zstd\nunavailable: dmabuf video vaapi\n"
                    {
                        return Err("WAYPIPE_VERSION_MISMATCH");
                    }
                    update(&|r| {
                        r.version = "0.11.3".into();
                        r.executable_sha256 = hash.clone();
                    });
                    if cancel.load(Ordering::Acquire) {
                        return Err("CANCELLED");
                    }
                    let transfer = directory.join("p");
                    let mut child = OwnedProcess::spawn(
                        Command::new(&executable)
                            .args([
                                "--no-gpu",
                                "--compress",
                                "none",
                                "--threads",
                                "1",
                                "--socket",
                            ])
                            .arg(&transfer)
                            .arg("client")
                            .env("XDG_RUNTIME_DIR", &directory)
                            .env("WAYLAND_DISPLAY", "w"),
                    )?;
                    update(&|r| r.owned_groups = 1);
                    let run = (|| {
                        let uid = unsafe { libc::getuid() };
                        let context = child.identity(uid)?;
                        update(&|r| {
                            r.child_uid = uid;
                            r.child_context = context.clone();
                        });
                        let deadline = Instant::now() + Duration::from_secs(3);
                        let mut ready = false;
                        loop {
                            if cancel.load(Ordering::Acquire) {
                                break;
                            }
                            child.pump()?;
                            if child.exited()? {
                                return Err("WAYPIPE_EARLY_EXIT");
                            }
                            if !ready {
                                if let Ok(stream) = UnixStream::connect(&transfer) {
                                    drop(stream);
                                    ready = true;
                                    update(&|r| r.ready = true);
                                } else if Instant::now() >= deadline {
                                    return Err("HELPER_EXEC_FAILED");
                                }
                            }
                            thread::sleep(Duration::from_millis(5));
                        }
                        Ok(())
                    })();
                    let stopped = child.finish()?;
                    update(&|r| {
                        r.ready = false;
                        r.waited = true;
                        r.owned_groups = 0;
                        r.stop_ms = stopped;
                    });
                    run
                }))
                .unwrap_or(Err("JNI_FAILURE"));
                update(&|r| {
                    r.workers = 0;
                    r.ready = false;
                    r.cancelled = cancel.load(Ordering::Acquire);
                    r.error = result.err().filter(|e| *e != "CANCELLED");
                });
            })
            .map_err(|_| "RESOURCE_LIMIT")?;
        Ok(Self {
            state,
            stop,
            worker: Some(worker),
        })
    }
    pub(crate) fn snapshot(&self) -> ClientReport {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }
    pub(crate) fn request_stop(&self) {
        self.stop.store(true, Ordering::Release);
    }
    pub(crate) fn finished(&mut self) -> bool {
        if self.worker.as_ref().is_some_and(|w| w.is_finished()) {
            let _ = self.worker.take().unwrap().join();
        }
        self.worker.is_none()
    }
}
impl Drop for LocalClient {
    fn drop(&mut self) {
        self.request_stop();
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

/// 固定argvのclient起動を3回検査する。転送protocol/SHMの合格はW05の実通信で判定する。
pub fn probe(
    executable: &Path,
    files: &Path,
    nonce: &str,
    stop: Arc<AtomicBool>,
    progress: impl Fn(&Report),
) -> Report {
    let mut report = Report {
        uid: unsafe { libc::getuid() },
        waypipe: Some(Proof {
            nonce: nonce.into(),
            ..Default::default()
        }),
        ..Default::default()
    };
    let result = (|| {
        let bytes = fs::read(executable).map_err(|_| "HELPER_EXEC_FAILED")?;
        report.waypipe.as_mut().unwrap().executable_sha256 = Sha256::digest(bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        report.context = fs::read_to_string("/proc/self/attr/current")
            .unwrap_or_default()
            .trim()
            .trim_end_matches('\0')
            .to_owned();
        let version =
            OwnedProcess::spawn(Command::new(executable).arg("--version"))?.capture(&stop)?;
        if version != b"waypipe 0.11.3\nfeatures: lz4 zstd\nunavailable: dmabuf video vaapi\n" {
            return Err("WAYPIPE_VERSION_MISMATCH");
        }
        report.version = "waypipe 0.11.3".into();
        report.waypipe.as_mut().unwrap().features = vec!["lz4".into(), "zstd".into()];
        report.waypipe.as_mut().unwrap().unavailable =
            vec!["dmabuf".into(), "video".into(), "vaapi".into()];
        let directory = RuntimeDirectory::create(files, nonce)?;
        let outcome = (|| {
            for _ in 0..3 {
                if stop.load(Ordering::Acquire) {
                    return Err("CANCELLED");
                }
                let path = directory.path.join("w");
                let transfer = directory.path.join("p");
                let listener = UnixListener::bind(&path).map_err(|_| "HELPER_EXEC_FAILED")?;
                listener
                    .set_nonblocking(true)
                    .map_err(|_| "HELPER_EXEC_FAILED")?;
                let mut display = Display::<ProtocolState>::new().map_err(|_| "DISPLAY_CREATE")?;
                let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
                let mut child = OwnedProcess::spawn(
                    Command::new(executable)
                        .args([
                            "--no-gpu",
                            "--compress",
                            "none",
                            "--threads",
                            "1",
                            "--socket",
                        ])
                        .arg(&transfer)
                        .arg("client")
                        .env("XDG_RUNTIME_DIR", &directory.path)
                        .env("WAYLAND_DISPLAY", "w"),
                )?;
                let context = child.identity(report.uid)?;
                report.waypipe.as_mut().unwrap().child_uid = report.uid;
                report.waypipe.as_mut().unwrap().child_context = context;
                report.owned_children = 1;
                report.waypipe.as_mut().unwrap().owned_groups = 1;
                progress(&report);
                let started = Instant::now();
                let mut connected = false;
                let mut accepted = false;
                let cycle = (|| {
                    while started.elapsed() < Duration::from_millis(300) || !accepted || !connected
                    {
                        if stop.load(Ordering::Acquire) {
                            return Err("CANCELLED");
                        }
                        child.pump()?;
                        if child.exited()? {
                            return Err("WAYPIPE_EARLY_EXIT");
                        }
                        while let Ok((stream, _)) = listener.accept() {
                            display
                                .handle()
                                .insert_client(stream, Arc::new(ClientState::default()))
                                .map_err(|_| "CLIENT_CREATE")?;
                            report.waypipe.as_mut().unwrap().compositor_connections += 1;
                            accepted = true;
                        }
                        if !connected && let Ok(stream) = UnixStream::connect(&transfer) {
                            drop(stream); // listenerの実accept/EOFだけ。転送成立とは扱わない。
                            report.waypipe.as_mut().unwrap().transfer_connections += 1;
                            connected = true;
                        }
                        display
                            .dispatch_clients(&mut state)
                            .map_err(|_| "SERVER_DISPATCH")?;
                        display.flush_clients().map_err(|_| "SERVER_FLUSH")?;
                        if started.elapsed() >= Duration::from_secs(3) {
                            return Err("HELPER_EXEC_FAILED");
                        }
                        thread::sleep(Duration::from_millis(5));
                    }
                    Ok(())
                })();
                let stopped = child.finish()?;
                report.owned_children = 0;
                let proof = report.waypipe.as_mut().unwrap();
                proof.owned_groups = 0;
                proof.waited += 1;
                proof.stop_ms = proof.stop_ms.max(stopped);
                for _ in 0..20 {
                    display
                        .dispatch_clients(&mut state)
                        .map_err(|_| "SERVER_DISPATCH")?;
                    let mut objects = 0;
                    let backend = display.handle().backend_handle();
                    let mut ids = vec![];
                    backend.with_all_clients(|client| ids.push(client));
                    let clients = ids.len();
                    for client in ids {
                        backend.with_all_objects_for(client, |_| objects += 1).ok();
                    }
                    proof.clients_after = clients;
                    proof.objects_after = objects;
                    if clients == 0 {
                        break;
                    }
                    thread::sleep(Duration::from_millis(5));
                }
                if proof.clients_after != 0 || proof.objects_after != 0 {
                    return Err("CLEANUP_FAILED");
                }
                drop(listener);
                fs::remove_file(path).map_err(|_| "CLEANUP_FAILED")?;
                // Waypipeの正常終了時は自分でpをunlinkする。異常終了時だけ所有directory内で回収する。
                if transfer.exists() {
                    fs::remove_file(transfer).map_err(|_| "CLEANUP_FAILED")?;
                }
                cycle?;
                report.cycles += 1;
                progress(&report);
            }
            Ok(())
        })();
        directory.cleanup()?;
        report.directory_removed = true;
        report.waypipe.as_mut().unwrap().socket_cleaned = true;
        outcome
    })();
    match result {
        Err("CANCELLED") => report.cancelled = true,
        Err(error) => report.error = Some(error),
        Ok(()) => (),
    }
    progress(&report);
    report
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_waypipe_client_restarts_and_cancel_reap_owned_groups() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let marker: serde_json::Value = serde_json::from_slice(
            &fs::read(root.join("native/generated/waypipe-host.json"))
                .expect("先にscripts/build-waypipe --target hostを実行する"),
        )
        .unwrap();
        let executable = Path::new(marker["executable"].as_str().unwrap());
        let files = std::env::temp_dir().join(format!("eidolon-wp-{}", std::process::id()));
        fs::create_dir(&files).unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let normal = probe(executable, &files, "aabbccddeeff0001", stop.clone(), |_| {});
        assert_eq!(normal.error, None, "{normal:?}");
        assert_eq!(normal.cycles, 3);
        let proof = normal.waypipe.unwrap();
        assert_eq!(proof.executable_sha256, marker["sha256"].as_str().unwrap());
        assert_eq!(proof.child_uid, normal.uid);
        assert!(!proof.child_context.is_empty());
        assert!(proof.compositor_connections >= 3);
        assert_eq!(proof.transfer_connections, 3);
        assert_eq!(proof.waited, 3);
        assert_eq!(proof.owned_groups, 0);
        assert!(proof.socket_cleaned && normal.directory_removed);
        let cancelled = probe(
            executable,
            &files,
            "aabbccddeeff0002",
            stop.clone(),
            |report| {
                if report.owned_children == 1 {
                    stop.store(true, Ordering::Release);
                }
            },
        );
        assert_eq!(cancelled.error, None, "{cancelled:?}");
        assert!(cancelled.cancelled && cancelled.directory_removed);
        let proof = cancelled.waypipe.unwrap();
        assert_eq!(proof.waited, 1);
        assert_eq!(proof.owned_groups, 0);
        assert!(proof.socket_cleaned);
        fs::remove_dir_all(files).unwrap();
    }
}
