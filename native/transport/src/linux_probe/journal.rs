//! Linux専用の未回収owner。旧Termux journalは読まない・照合しない・削除しない。
use super::Request;
use eidolon_model::ids::SessionId;
use serde::Deserialize;
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
};

pub(super) struct Journal {
    directory: PathBuf,
    path: PathBuf,
    bytes: Vec<u8>,
    device: u64,
    inode: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Stored {
    schema: u32,
    backend: String,
    profile_binding: String,
    profile_revision: String,
    session: SessionId,
    uid: u32,
    nonce: String,
    helper_sha256: String,
    bundle_sha256: String,
    waypipe_sha256: String,
    fixture_sha256: String,
}
fn check_directory(path: &Path, private: bool) -> Result<(), &'static str> {
    let m = std::fs::symlink_metadata(path).map_err(|_| "LINUX_JOURNAL_INVALID")?;
    if !path.is_absolute()
        || !m.is_dir()
        || m.uid() != unsafe { libc::getuid() }
        || (private && m.mode() & 0o777 != 0o700)
        || (!private
            && (m.mode() & 0o002 != 0
                || m.mode() & 0o020 != 0 && m.gid() != unsafe { libc::getgid() }))
    {
        return Err("LINUX_JOURNAL_INVALID");
    }
    Ok(())
}
fn sync(path: &Path) -> Result<(), &'static str> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)
        .and_then(|f| f.sync_all())
        .map_err(|_| "LINUX_JOURNAL_FAILED")
}
fn read(path: &Path) -> Result<(File, Vec<u8>), &'static str> {
    let mut f = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|_| "LINUX_JOURNAL_INVALID")?;
    let m = f.metadata().map_err(|_| "LINUX_JOURNAL_INVALID")?;
    if !m.is_file()
        || m.uid() != unsafe { libc::getuid() }
        || m.mode() & 0o777 != 0o600
        || m.nlink() != 1
        || m.len() > 4096
    {
        return Err("LINUX_JOURNAL_INVALID");
    }
    let mut bytes = Vec::new();
    (&mut f)
        .take(4097)
        .read_to_end(&mut bytes)
        .map_err(|_| "LINUX_JOURNAL_INVALID")?;
    if bytes.len() > 4096 || bytes.len() as u64 != m.len() {
        return Err("LINUX_JOURNAL_INVALID");
    }
    Ok((f, bytes))
}
impl Journal {
    pub fn matching(
        request: &super::Recovery<'_>,
    ) -> Result<Option<(Self, SessionId)>, &'static str> {
        check_directory(request.files, false)?;
        let directory = request.files.join("linux-probe-state");
        match std::fs::symlink_metadata(&directory) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            _ => check_directory(&directory, true)?,
        }
        let path = directory.join("pending.json");
        match std::fs::symlink_metadata(&path) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            _ => (),
        }
        let (file, bytes) = read(&path)?;
        let stored: Stored = serde_json::from_slice(&bytes).map_err(|_| "LINUX_JOURNAL_INVALID")?;
        if stored.schema != 1
            || stored.backend != "linux_posix"
            || stored.profile_binding != request.profile_binding
            || stored.profile_revision != request.profile_revision
            || stored.uid != request.uid
            || stored.nonce != request.nonce
            || stored.helper_sha256 != request.pins.helper_sha256()
            || stored.bundle_sha256 != request.pins.bundle_sha256
            || stored.waypipe_sha256 != request.pins.waypipe_sha256
            || stored.fixture_sha256 != request.pins.fixture_sha256
        {
            return Err("LINUX_CLEANUP_SCOPE_MISMATCH");
        }
        let m = file.metadata().map_err(|_| "LINUX_JOURNAL_INVALID")?;
        Ok(Some((
            Self {
                directory,
                path,
                bytes,
                device: m.dev(),
                inode: m.ino(),
            },
            stored.session,
        )))
    }
    pub fn reserve(request: &Request<'_>) -> Result<Self, &'static str> {
        check_directory(request.files, false)?;
        let directory = request.files.join("linux-probe-state");
        match std::fs::DirBuilder::new().mode(0o700).create(&directory) {
            Ok(()) => sync(request.files)?,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err("LINUX_JOURNAL_FAILED"),
        }
        check_directory(&directory, true)?;
        let path = directory.join("pending.json");
        match std::fs::symlink_metadata(&path) {
            Ok(_) => return Err("LINUX_CLEANUP_PENDING"),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(_) => return Err("LINUX_JOURNAL_INVALID"),
        }
        let bytes=serde_json::to_vec(&serde_json::json!({"schema":1,"backend":"linux_posix","profile_binding":request.profile_binding,
            "profile_revision":request.profile_revision,"session":request.session,"uid":request.uid,"nonce":request.nonce,
            "helper_sha256":request.pins.helper_sha256(),"bundle_sha256":request.pins.bundle_sha256,
            "waypipe_sha256":request.pins.waypipe_sha256,"fixture_sha256":request.pins.fixture_sha256})).map_err(|_|"LINUX_JOURNAL_FAILED")?;
        let temporary = directory.join(format!(".pending-{:016x}", rand::random::<u64>()));
        let publish = (|| -> Result<(), &'static str> {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&temporary)
                .map_err(|_| "LINUX_JOURNAL_FAILED")?;
            file.write_all(&bytes)
                .and_then(|_| file.sync_all())
                .map_err(|_| "LINUX_JOURNAL_FAILED")?;
            // exclusive publication。別nonce/profileのpendingを置換しない。
            std::fs::hard_link(&temporary, &path).map_err(|_| "LINUX_JOURNAL_FAILED")?;
            Ok(())
        })();
        let _ = std::fs::remove_file(&temporary);
        publish?;
        sync(&directory)?;
        let (file, stored) = read(&path)?;
        if stored != bytes {
            return Err("LINUX_JOURNAL_INVALID");
        }
        let m = file.metadata().map_err(|_| "LINUX_JOURNAL_INVALID")?;
        Ok(Self {
            directory,
            path,
            bytes,
            device: m.dev(),
            inode: m.ino(),
        })
    }
    pub fn clear(&mut self) -> Result<(), &'static str> {
        check_directory(&self.directory, true)?;
        let (file, bytes) = read(&self.path)?;
        let m = file.metadata().map_err(|_| "LINUX_JOURNAL_INVALID")?;
        if bytes != self.bytes || m.dev() != self.device || m.ino() != self.inode {
            return Err("LINUX_JOURNAL_OWNER_CHANGED");
        }
        std::fs::remove_file(&self.path).map_err(|_| "LINUX_JOURNAL_FAILED")?;
        sync(&self.directory)
    }
}
