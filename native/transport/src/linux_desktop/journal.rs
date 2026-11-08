//! Normal Linux ownership only. A record is never adopted by a new Profile or
//! source revision. No stored PID is needed or used to signal a process.
use super::{Binding, Pins, REVISION, Recovery, boot_identity, decimal, hex};
use eidolon_model::{connection_profile::Profile, ids::SessionId};
use serde::{Deserialize, Serialize};
use std::{
    ffi::CString,
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::{
        fd::AsRawFd,
        unix::{
            ffi::OsStrExt,
            fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
        },
    },
    path::{Path, PathBuf},
};

const DIRECTORY: &str = "linux-desktop-state";
const LIMIT: usize = 8192;

#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Stage {
    Reserved,
    Prepared,
    ForwardRequested,
    StartRequested,
    Started,
    Unknown,
    Complete,
}

#[cfg(test)]
mod tests;

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Cleanup {
    pub process_complete: bool,
    pub pipes_eof: bool,
    pub remote_cleaned: bool,
    pub receipt_written: bool,
    pub channel_complete: bool,
    pub forward_retired: bool,
    pub router_drained: bool,
    pub boot_changed: bool,
}
impl Cleanup {
    fn complete(&self) -> bool {
        self.process_complete
            && (self.pipes_eof || self.boot_changed)
            && self.remote_cleaned
            && self.receipt_written
            && self.channel_complete
            && self.forward_retired
            && self.router_drained
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    schema: u32,
    backend: String,
    protocol_revision: String,
    session: SessionId,
    profile_binding: String,
    profile_revision: String,
    nonce: String,
    uid: u32,
    pins: [String; 5],
    sources: [String; 5],
    stage: Stage,
    boot_id: Option<String>,
    cleanup: Cleanup,
}

/// Private owner descriptor for the Controller's cross-backend barrier. It is
/// never serialized into ordinary diagnostics. Reading it grants no recovery
/// authority: matching() still checks the authenticated UID and locks the file.
#[derive(Clone)]
pub(crate) struct Pending(Record);
impl Pending {
    pub(crate) fn matches(&self, profile: &Profile, expected: &Pins) -> bool {
        self.0.profile_binding == profile.binding()
            && self.0.profile_revision == profile.revision()
            && self.0.pins == pins(expected)
            && self.0.sources == sources(expected)
            && self.0.protocol_revision == REVISION
    }
}

/// Read without changing, clearing or adopting a pending owner. A malformed or
/// inaccessible record is an error, not an empty journal. The active actor holds
/// the write lock; all replacements are atomic and become a conservative barrier.
pub(crate) fn pending(files: &Path) -> Result<Option<Pending>, &'static str> {
    check_directory(files, false)?;
    let directory = files.join(DIRECTORY);
    match std::fs::symlink_metadata(&directory) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        _ => check_directory(&directory, true)?,
    }
    let path = directory.join("pending.json");
    match std::fs::symlink_metadata(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        _ => (),
    }
    let (_, bytes) = read(&path)?;
    let record: Record = serde_json::from_slice(&bytes).map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
    if record.schema != 1
        || record.backend != "linux_desktop"
        || !hex(&record.profile_binding, 64)
        || decimal(&record.profile_revision, false).is_none()
        || !hex(&record.nonce, 16)
        || record.uid == 0
        || record
            .pins
            .iter()
            .chain(record.sources.iter())
            .any(|value| !hex(value, 64))
        || record
            .boot_id
            .as_deref()
            .is_some_and(|boot| !boot_identity(boot))
        || (record.boot_id.is_none() && !matches!(record.stage, Stage::Reserved | Stage::Unknown))
    {
        return Err("DESKTOP_JOURNAL_INVALID");
    }
    Ok(Some(Pending(record)))
}
fn pins(p: &Pins) -> [String; 5] {
    [
        p.base_bundle_sha256.clone(),
        p.desktop_bundle_sha256.clone(),
        p.waypipe_sha256.clone(),
        p.shm_fixture_sha256.clone(),
        p.desktop_binary_sha256.clone(),
    ]
}
fn sources(p: &Pins) -> [String; 5] {
    let s = p.source_hashes();
    [s.base, s.adapter, s.owner, s.demo, s.wire]
}

pub(super) struct Journal {
    directory: PathBuf,
    path: PathBuf,
    // Held through run/recovery. Another task or process cannot perform an
    // ownership transition between validation and atomic publication.
    _lock: File,
    bytes: Vec<u8>,
    device: u64,
    inode: u64,
    record: Record,
}

fn check_directory(path: &Path, private: bool) -> Result<(), &'static str> {
    let m = std::fs::symlink_metadata(path).map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
    if !path.is_absolute()
        || !m.is_dir()
        || m.uid() != unsafe { libc::getuid() }
        || (private && m.mode() & 0o777 != 0o700)
        || (!private
            && (m.mode() & 0o002 != 0
                || m.mode() & 0o020 != 0 && m.gid() != unsafe { libc::getgid() }))
    {
        return Err("DESKTOP_JOURNAL_INVALID");
    }
    Ok(())
}
fn sync(path: &Path) -> Result<(), &'static str> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)
        .and_then(|file| file.sync_all())
        .map_err(|_| "DESKTOP_JOURNAL_SYNC_FAILED")
}
fn lock(directory: &Path) -> Result<File, &'static str> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(directory.join("owner.lock"))
        .map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
    let m = file.metadata().map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
    if !m.is_file()
        || m.uid() != unsafe { libc::getuid() }
        || m.mode() & 0o777 != 0o600
        || m.nlink() != 1
        || m.len() != 0
    {
        return Err("DESKTOP_JOURNAL_INVALID");
    }
    // SAFETY: file remains open for the lifetime of this journal.
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err("DESKTOP_JOURNAL_BUSY");
    }
    Ok(file)
}
fn read(path: &Path) -> Result<(File, Vec<u8>), &'static str> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
    let m = file.metadata().map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
    if !m.is_file()
        || m.uid() != unsafe { libc::getuid() }
        || m.mode() & 0o777 != 0o600
        || m.nlink() != 1
        || m.len() == 0
        || m.len() > LIMIT as u64
    {
        return Err("DESKTOP_JOURNAL_INVALID");
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take((LIMIT + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
    if bytes.len() > LIMIT || bytes.len() as u64 != m.len() {
        return Err("DESKTOP_JOURNAL_INVALID");
    }
    Ok((file, bytes))
}
fn exclusive(source: &Path, target: &Path) -> Result<(), &'static str> {
    #[cfg(any(target_os = "linux", target_os = "android"))]
    {
        let source =
            CString::new(source.as_os_str().as_bytes()).map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
        let target =
            CString::new(target.as_os_str().as_bytes()).map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
        // Android app domains may deny hard links. Never replace a pending owner
        // or fall back to ordinary rename when RENAME_NOREPLACE is unavailable.
        let result = unsafe {
            libc::syscall(
                libc::SYS_renameat2,
                libc::AT_FDCWD,
                source.as_ptr(),
                libc::AT_FDCWD,
                target.as_ptr(),
                libc::RENAME_NOREPLACE,
            )
        };
        if result == 0 {
            Ok(())
        } else if std::io::Error::last_os_error().raw_os_error() == Some(libc::EEXIST) {
            Err("DESKTOP_CLEANUP_PENDING")
        } else {
            Err("DESKTOP_JOURNAL_PUBLISH_FAILED")
        }
    }
    #[cfg(not(any(target_os = "linux", target_os = "android")))]
    {
        let _ = (source, target);
        Err("DESKTOP_JOURNAL_PUBLISH_UNSUPPORTED")
    }
}
fn temporary(directory: &Path, bytes: &[u8]) -> Result<PathBuf, &'static str> {
    let path = directory.join(format!(".pending-{:016x}", rand::random::<u64>()));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(&path)
            .map_err(|_| "DESKTOP_JOURNAL_CREATE_FAILED")?;
        file.write_all(bytes)
            .map_err(|_| "DESKTOP_JOURNAL_WRITE_FAILED")?;
        file.sync_all().map_err(|_| "DESKTOP_JOURNAL_SYNC_FAILED")
    })();
    if let Err(error) = result {
        let _ = std::fs::remove_file(&path);
        return Err(error);
    }
    Ok(path)
}

impl Journal {
    pub fn reserve(files: &Path, binding: &Binding) -> Result<Self, &'static str> {
        check_directory(files, false)?;
        let directory = files.join(DIRECTORY);
        match std::fs::DirBuilder::new().mode(0o700).create(&directory) {
            Ok(()) => sync(files)?,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err("DESKTOP_JOURNAL_CREATE_FAILED"),
        }
        check_directory(&directory, true)?;
        let guard = lock(&directory)?;
        let path = directory.join("pending.json");
        let record = Record {
            schema: 1,
            backend: "linux_desktop".into(),
            protocol_revision: REVISION.into(),
            session: binding.session,
            profile_binding: binding.profile_binding.clone(),
            profile_revision: binding.profile_revision.clone(),
            nonce: binding.nonce.clone(),
            uid: binding.uid,
            pins: pins(&binding.pins),
            sources: sources(&binding.pins),
            stage: Stage::Reserved,
            boot_id: None,
            cleanup: Cleanup::default(),
        };
        let bytes = serde_json::to_vec(&record).map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
        let candidate = temporary(&directory, &bytes)?;
        let result = exclusive(&candidate, &path);
        let _ = std::fs::remove_file(candidate);
        result?;
        sync(&directory)?;
        Self::load(directory, path, guard, record, bytes)
    }

    pub fn matching(request: &Recovery<'_>) -> Result<Option<(Self, Binding)>, &'static str> {
        check_directory(request.files, false)?;
        let directory = request.files.join(DIRECTORY);
        match std::fs::symlink_metadata(&directory) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            _ => check_directory(&directory, true)?,
        }
        let guard = lock(&directory)?;
        let path = directory.join("pending.json");
        match std::fs::symlink_metadata(&path) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            _ => (),
        }
        let (_, bytes) = read(&path)?;
        let record: Record =
            serde_json::from_slice(&bytes).map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
        if record.schema != 1
            || record.backend != "linux_desktop"
            || record.protocol_revision != REVISION
            || record.profile_binding != request.profile.binding()
            || record.profile_revision != request.profile.revision()
            || record.uid != request.uid
            || record.pins != pins(request.pins)
            || record.sources != sources(request.pins)
            || record
                .boot_id
                .as_deref()
                .is_some_and(|boot| !boot_identity(boot))
            || (record.boot_id.is_none()
                && !matches!(record.stage, Stage::Reserved | Stage::Unknown))
        {
            return Err("DESKTOP_CLEANUP_SCOPE_MISMATCH");
        }
        let binding = Binding::capture(
            request.profile,
            record.session,
            record.uid,
            &record.nonce,
            request.pins.clone(),
        )?;
        let journal = Self::load(directory, path, guard, record, bytes)?;
        Ok(Some((journal, binding)))
    }

    fn load(
        directory: PathBuf,
        path: PathBuf,
        guard: File,
        record: Record,
        bytes: Vec<u8>,
    ) -> Result<Self, &'static str> {
        let (file, current) = read(&path)?;
        if current != bytes {
            return Err("DESKTOP_JOURNAL_OWNER_CHANGED");
        }
        let m = file.metadata().map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
        Ok(Self {
            directory,
            path,
            _lock: guard,
            bytes,
            device: m.dev(),
            inode: m.ino(),
            record,
        })
    }
    fn unchanged(&self) -> Result<(), &'static str> {
        check_directory(&self.directory, true)?;
        let (file, bytes) = read(&self.path)?;
        let m = file.metadata().map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
        if bytes != self.bytes || m.dev() != self.device || m.ino() != self.inode {
            return Err("DESKTOP_JOURNAL_OWNER_CHANGED");
        }
        Ok(())
    }
    fn persist(&mut self, record: Record) -> Result<(), &'static str> {
        let bytes = serde_json::to_vec(&record).map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
        if bytes.len() > LIMIT {
            return Err("DESKTOP_JOURNAL_INVALID");
        }
        let candidate = temporary(&self.directory, &bytes)?;
        let result = (|| {
            self.unchanged()?;
            std::fs::rename(&candidate, &self.path)
                .map_err(|_| "DESKTOP_JOURNAL_PUBLISH_FAILED")?;
            let (file, current) = read(&self.path)?;
            if current != bytes {
                return Err("DESKTOP_JOURNAL_OWNER_CHANGED");
            }
            let m = file.metadata().map_err(|_| "DESKTOP_JOURNAL_INVALID")?;
            self.bytes = bytes;
            self.device = m.dev();
            self.inode = m.ino();
            self.record = record;
            sync(&self.directory)
        })();
        let _ = std::fs::remove_file(candidate);
        result
    }
    pub fn prepared(&mut self, boot_id: &str) -> Result<(), &'static str> {
        if self.record.stage != Stage::Reserved || !boot_identity(boot_id) {
            return Err("DESKTOP_JOURNAL_TRANSITION");
        }
        let mut next = self.record.clone();
        next.boot_id = Some(boot_id.into());
        next.stage = Stage::Prepared;
        self.persist(next)
    }
    pub fn advance(&mut self, stage: Stage) -> Result<(), &'static str> {
        if !matches!(
            (self.record.stage, stage),
            (Stage::Prepared, Stage::ForwardRequested)
                | (Stage::ForwardRequested, Stage::StartRequested)
                | (Stage::StartRequested, Stage::Started)
        ) {
            return Err("DESKTOP_JOURNAL_TRANSITION");
        }
        let mut next = self.record.clone();
        next.stage = stage;
        self.persist(next)
    }
    pub fn boot_id(&self) -> Option<&str> {
        self.record.boot_id.as_deref()
    }
    pub fn recovered_boot(&mut self, boot: &str) -> Result<(), &'static str> {
        if !boot_identity(boot)
            || self
                .record
                .boot_id
                .as_deref()
                .is_some_and(|old| old != boot)
        {
            return Err("DESKTOP_CLEANUP_SCOPE_MISMATCH");
        }
        let mut next = self.record.clone();
        next.boot_id = Some(boot.into());
        next.stage = Stage::Unknown;
        self.persist(next)
    }
    pub fn finish(&mut self, cleanup: Cleanup) -> Result<(), &'static str> {
        let mut next = self.record.clone();
        next.stage = if cleanup.complete() {
            Stage::Complete
        } else {
            Stage::Unknown
        };
        next.cleanup = cleanup;
        self.persist(next)
    }
    pub fn clear(&mut self) -> Result<(), &'static str> {
        if self.record.stage != Stage::Complete || !self.record.cleanup.complete() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        self.unchanged()?;
        std::fs::remove_file(&self.path).map_err(|_| "DESKTOP_JOURNAL_CLEAR_FAILED")?;
        sync(&self.directory)
    }
}
