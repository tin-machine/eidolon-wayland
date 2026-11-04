//! shellを介さないhelper所有・deadline・回収。blocking処理は専用I/O workerで呼ぶ。
use serde::Serialize;
use std::{
    fs,
    io::{Read, Write},
    os::unix::{
        ffi::OsStrExt,
        fs::{DirBuilderExt, MetadataExt, PermissionsExt},
        net::UnixStream,
    },
    path::{Path, PathBuf},
    process::{Child, Command, ExitStatus, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

type Error = &'static str;
const OUTPUT_BYTES: usize = 64 * 1024;
#[derive(Debug, Clone, Default, Serialize)]
pub struct Report {
    pub cycles: u32,
    pub owned_children: usize,
    pub directory_removed: bool,
    pub version: String,
    pub uid: u32,
    pub context: String,
    pub error: Option<Error>,
    pub cancelled: bool,
    pub exit_code: Option<i32>,
}

struct RuntimeDirectory {
    path: PathBuf,
    inode: u64,
    device: u64,
}
impl RuntimeDirectory {
    fn create(files: &Path, nonce: &str) -> Result<Self, Error> {
        if nonce.len() != 16
            || !nonce
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err("CONFIG_INVALID");
        }
        let files = files.canonicalize().map_err(|_| "CONFIG_INVALID")?;
        let parent = files.join("rt");
        match fs::DirBuilder::new().mode(0o700).create(&parent) {
            Ok(()) => (),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err("HELPER_EXEC_FAILED"),
        }
        let meta = fs::symlink_metadata(&parent).map_err(|_| "CONFIG_INVALID")?;
        if !meta.is_dir()
            || meta.file_type().is_symlink()
            || meta.permissions().mode() & 0o777 != 0o700
        {
            return Err("CONFIG_INVALID");
        }
        let path = parent.join(nonce);
        if path.join("s9").as_os_str().as_bytes().len() > 103 {
            return Err("RESOURCE_LIMIT");
        }
        fs::DirBuilder::new()
            .mode(0o700)
            .create(&path)
            .map_err(|_| "CONFIG_INVALID")?;
        let meta = fs::symlink_metadata(&path).map_err(|_| "CONFIG_INVALID")?;
        Ok(Self {
            path,
            inode: meta.ino(),
            device: meta.dev(),
        })
    }
    fn cleanup(&self) -> Result<(), Error> {
        let meta = fs::symlink_metadata(&self.path).map_err(|_| "CLEANUP_FAILED")?;
        if !meta.is_dir()
            || meta.file_type().is_symlink()
            || meta.ino() != self.inode
            || meta.dev() != self.device
        {
            return Err("CLEANUP_FAILED");
        }
        fs::remove_dir_all(&self.path).map_err(|_| "CLEANUP_FAILED")
    }
}
impl Drop for RuntimeDirectory {
    fn drop(&mut self) {
        if self.path.exists() {
            let _ = self.cleanup();
        }
    }
}

fn drain(mut input: impl Read) -> std::io::Result<Vec<u8>> {
    let mut retained = Vec::new();
    let mut chunk = [0; 4096];
    loop {
        let count = input.read(&mut chunk)?;
        if count == 0 {
            return Ok(retained);
        }
        let keep = count.min(OUTPUT_BYTES - retained.len());
        retained.extend_from_slice(&chunk[..keep]); // 上限後もEOFまで読み捨てる。
    }
}
struct OwnedChild {
    child: Child,
    stdout: Option<JoinHandle<std::io::Result<Vec<u8>>>>,
    stderr: Option<JoinHandle<std::io::Result<Vec<u8>>>>,
}
impl OwnedChild {
    fn spawn(executable: &Path, args: &[&std::ffi::OsStr]) -> Result<Self, Error> {
        let child = Command::new(executable)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|_| "HELPER_EXEC_FAILED")?;
        let mut owned = Self {
            child,
            stdout: None,
            stderr: None,
        };
        let output = owned.child.stdout.take().ok_or("HELPER_EXEC_FAILED")?;
        owned.stdout = Some(
            thread::Builder::new()
                .name("helper-out".into())
                .spawn(|| drain(output))
                .map_err(|_| "RESOURCE_LIMIT")?,
        );
        let error = owned.child.stderr.take().ok_or("HELPER_EXEC_FAILED")?;
        owned.stderr = Some(
            thread::Builder::new()
                .name("helper-err".into())
                .spawn(|| drain(error))
                .map_err(|_| "RESOURCE_LIMIT")?,
        );
        Ok(owned)
    }
    fn wait(
        &mut self,
        stop: &AtomicBool,
        deadline: Instant,
    ) -> Result<(ExitStatus, Vec<u8>), Error> {
        loop {
            if stop.load(Ordering::Acquire) {
                return Err("CANCELLED");
            }
            if let Some(status) = self.child.try_wait().map_err(|_| "HELPER_EXEC_FAILED")? {
                let stdout = self
                    .stdout
                    .take()
                    .unwrap()
                    .join()
                    .map_err(|_| "HELPER_EXEC_FAILED")?
                    .map_err(|_| "HELPER_EXEC_FAILED")?;
                self.stderr
                    .take()
                    .unwrap()
                    .join()
                    .map_err(|_| "HELPER_EXEC_FAILED")?
                    .map_err(|_| "HELPER_EXEC_FAILED")?;
                return Ok((status, stdout));
            }
            if Instant::now() >= deadline {
                return Err("HELPER_EXEC_FAILED");
            }
            thread::sleep(Duration::from_millis(5));
        }
    }
}
impl Drop for OwnedChild {
    fn drop(&mut self) {
        // このstructがspawnしたpidだけを回収。I/O worker以外で保管しない。
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
        if let Some(stdout) = self.stdout.take() {
            let _ = stdout.join();
        }
        if let Some(stderr) = self.stderr.take() {
            let _ = stderr.join();
        }
    }
}

/// 固定fixtureだけを使う先行gate。実行pathはruntimeの信頼済みcapabilityから渡す。
pub fn probe(
    executable: &Path,
    files: &Path,
    nonce: &str,
    stop: Arc<AtomicBool>,
    progress: impl Fn(&Report),
) -> Report {
    let mut report = Report {
        uid: unsafe { libc::getuid() },
        ..Report::default()
    };
    let result = (|| -> Result<(), Error> {
        let directory = RuntimeDirectory::create(files, nonce)?;
        let result = (|| -> Result<(), Error> {
            let mut child = OwnedChild::spawn(executable, &[std::ffi::OsStr::new("--version")])?;
            report.owned_children = 1;
            progress(&report);
            let (status, output) = child.wait(&stop, Instant::now() + Duration::from_secs(3))?;
            report.exit_code = status.code();
            if !status.success() {
                return Err("HELPER_EXEC_FAILED");
            }
            let version: serde_json::Value =
                serde_json::from_slice(&output).map_err(|_| "HELPER_EXEC_FAILED")?;
            if version["schema"] != 1
                || version["version"] != "eidolon-probe-bin/1"
                || version["uid"] != report.uid
            {
                return Err("HELPER_EXEC_FAILED");
            }
            report.version = "eidolon-probe-bin/1".into();
            report.context = version["context"]
                .as_str()
                .ok_or("HELPER_EXEC_FAILED")?
                .to_owned();
            if report.context.len() > 1024 {
                return Err("RESOURCE_LIMIT");
            }
            report.owned_children = 0;
            for cycle in 0..10 {
                if stop.load(Ordering::Acquire) {
                    return Err("CANCELLED");
                }
                let path = directory.path.join(format!("s{cycle}"));
                let mut child = OwnedChild::spawn(
                    executable,
                    &[std::ffi::OsStr::new("--echo"), path.as_os_str()],
                )?;
                report.owned_children = 1;
                progress(&report);
                let deadline = Instant::now() + Duration::from_secs(3);
                let mut stream = loop {
                    if stop.load(Ordering::Acquire) {
                        return Err("CANCELLED");
                    }
                    match UnixStream::connect(&path) {
                        Ok(stream) => break stream,
                        Err(_) if Instant::now() < deadline => {
                            thread::sleep(Duration::from_millis(5))
                        }
                        Err(_) => return Err("HELPER_EXEC_FAILED"),
                    }
                };
                stream
                    .set_read_timeout(Some(Duration::from_millis(200)))
                    .map_err(|_| "HELPER_EXEC_FAILED")?;
                stream
                    .set_write_timeout(Some(Duration::from_millis(200)))
                    .map_err(|_| "HELPER_EXEC_FAILED")?;
                let data = b"eidolon-owned-helper-fixture";
                stream
                    .write_all(&(data.len() as u32).to_le_bytes())
                    .map_err(|_| "HELPER_EXEC_FAILED")?;
                stream.write_all(data).map_err(|_| "HELPER_EXEC_FAILED")?;
                let mut reply = vec![0; data.len() + 4];
                stream
                    .read_exact(&mut reply)
                    .map_err(|_| "HELPER_EXEC_FAILED")?;
                if reply[..4] != (data.len() as u32).to_le_bytes() || reply[4..] != data[..] {
                    return Err("HELPER_EXEC_FAILED");
                }
                stream
                    .write_all(&0u32.to_le_bytes())
                    .map_err(|_| "HELPER_EXEC_FAILED")?;
                let mut ack = [1; 4];
                stream
                    .read_exact(&mut ack)
                    .map_err(|_| "HELPER_EXEC_FAILED")?;
                if ack != [0; 4] {
                    return Err("HELPER_EXEC_FAILED");
                }
                let (status, _) = child.wait(&stop, deadline)?;
                report.exit_code = status.code();
                if !status.success() {
                    return Err("HELPER_EXEC_FAILED");
                }
                report.owned_children = 0;
                report.cycles = cycle + 1;
                progress(&report);
            }
            Ok(())
        })();
        directory.cleanup()?;
        report.directory_removed = true;
        result
    })();
    report.owned_children = 0;
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
    #[test]
    fn diagnostic_output_is_drained_after_limit_without_growing() {
        let data = vec![b'x'; super::OUTPUT_BYTES * 2];
        assert_eq!(
            super::drain(std::io::Cursor::new(data)).unwrap().len(),
            super::OUTPUT_BYTES
        );
    }
}
