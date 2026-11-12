//! Version-three ownership journal. Saved identity chooses recovery; a newer
//! profile, source set or decoder never adopts an old pending owner.
use super::{
    Binding, Pins, REVISION,
    recovery::Recovery,
    sources::{self, SourceHashes},
    valid_boot,
};
use eidolon_model::{
    connection_profile::Profile,
    linux_desktop_v3::{Context, StorageCapture, canonical_json, parse_json},
};
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

const DIRECTORY: &str = "linux-desktop-v3-state";
const LIMIT: usize = 32768;

#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    Reserved,
    Prepared,
    ForwardRequested,
    StartRequested,
    Started,
    Unknown,
    Complete,
}

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cleanup {
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
    context: Context,
    pins: Pins,
    build_id: String,
    sources: SourceHashes,
    stage: Stage,
    boot_id: Option<String>,
    cleanup: Cleanup,
}
fn exact_object(value: &serde_json::Value, keys: &[&str]) -> Result<(), &'static str> {
    let object = value.as_object().ok_or("DESKTOP_V3_JOURNAL_INVALID")?;
    if object.len() != keys.len() || keys.iter().any(|key| !object.contains_key(*key)) {
        return Err("DESKTOP_V3_JOURNAL_INVALID");
    }
    Ok(())
}
impl Record {
    fn parse(raw: &[u8]) -> Result<Self, &'static str> {
        let value = parse_json(raw, LIMIT).map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
        exact_object(
            &value,
            &[
                "schema",
                "backend",
                "protocol_revision",
                "context",
                "pins",
                "build_id",
                "sources",
                "stage",
                "boot_id",
                "cleanup",
            ],
        )?;
        exact_object(
            &value["cleanup"],
            &[
                "process_complete",
                "pipes_eof",
                "remote_cleaned",
                "receipt_written",
                "channel_complete",
                "forward_retired",
                "router_drained",
                "boot_changed",
            ],
        )?;
        if !value["stage"].is_string() || !value["sources"].is_object() {
            return Err("DESKTOP_V3_JOURNAL_INVALID");
        }
        let record: Self =
            serde_json::from_value(value).map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
        if record.schema != 2
            || record.backend != "linux_desktop"
            || record.protocol_revision != REVISION
            || record.build_id != record.pins.build_id()
            || record
                .boot_id
                .as_deref()
                .is_some_and(|boot| !valid_boot(boot))
            || (record.boot_id.is_none()
                && !matches!(record.stage, Stage::Reserved | Stage::Unknown))
            || (record.stage == Stage::Complete && !record.cleanup.complete())
        {
            return Err("DESKTOP_V3_JOURNAL_INVALID");
        }
        sources::validate_hashes(&record.sources)?;
        Ok(record)
    }
    fn bytes(&self) -> Result<Vec<u8>, &'static str> {
        let value = serde_json::to_value(self).map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
        let bytes = canonical_json(&value).map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
        Self::parse(&bytes)?;
        Ok(bytes)
    }
}
#[derive(Clone)]
pub struct Pending(Record);
impl Pending {
    pub fn matches(&self, profile: &Profile, pins: &Pins) -> bool {
        let Ok(binding) = Binding::from_captured(self.0.context.clone(), self.0.pins.clone())
        else {
            return false;
        };
        binding.matches_profile(profile, binding.session())
            && &self.0.pins == pins
            && sources::validate_hashes(&self.0.sources).is_ok()
    }
}
/// Reading an inaccessible/malformed journal never means "no pending owner".
pub fn pending(files: &Path) -> Result<Option<Pending>, &'static str> {
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
    Ok(Some(Pending(Record::parse(&bytes)?)))
}
/// The existing v2 journal is a barrier and is recovered by the existing v2
/// implementation with its captured pins, never through this journal.
pub fn check_v2_barrier(files: &Path) -> Result<(), &'static str> {
    if super::super::pending(files)?.is_some() {
        Err("RECOVERY_REQUIRED")
    } else {
        Ok(())
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ClearState {
    Linked,
    UnlinkedNeedsSync,
    Cleared,
}

pub struct Journal {
    directory: PathBuf,
    path: PathBuf,
    _lock: File,
    bytes: Vec<u8>,
    device: u64,
    inode: u64,
    record: Record,
    clear_state: ClearState,
}
fn check_directory(path: &Path, private: bool) -> Result<(), &'static str> {
    let m = std::fs::symlink_metadata(path).map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
    if !path.is_absolute()
        || !m.is_dir()
        || m.uid() != unsafe { libc::getuid() }
        || (private && m.mode() & 0o777 != 0o700)
        || (!private
            && (m.mode() & 0o002 != 0
                || m.mode() & 0o020 != 0 && m.gid() != unsafe { libc::getgid() }))
    {
        return Err("DESKTOP_V3_JOURNAL_INVALID");
    }
    Ok(())
}
fn sync(path: &Path) -> Result<(), &'static str> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)
        .and_then(|file| file.sync_all())
        .map_err(|_| "DESKTOP_V3_JOURNAL_SYNC_FAILED")
}
fn lock(directory: &Path) -> Result<File, &'static str> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(directory.join("owner.lock"))
        .map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
    let m = file.metadata().map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
    if !m.is_file()
        || m.uid() != unsafe { libc::getuid() }
        || m.mode() & 0o777 != 0o600
        || m.nlink() != 1
        || m.len() != 0
    {
        return Err("DESKTOP_V3_JOURNAL_INVALID");
    }
    // SAFETY: file remains open for the lifetime of this journal.
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err("DESKTOP_V3_JOURNAL_BUSY");
    }
    Ok(file)
}
fn read(path: &Path) -> Result<(File, Vec<u8>), &'static str> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(path)
        .map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
    let m = file.metadata().map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
    if !m.is_file()
        || m.uid() != unsafe { libc::getuid() }
        || m.mode() & 0o777 != 0o600
        || m.nlink() != 1
        || m.len() == 0
        || m.len() > LIMIT as u64
    {
        return Err("DESKTOP_V3_JOURNAL_INVALID");
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take((LIMIT + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
    if bytes.len() > LIMIT || bytes.len() as u64 != m.len() {
        return Err("DESKTOP_V3_JOURNAL_INVALID");
    }
    Ok((file, bytes))
}
fn exclusive(source: &Path, target: &Path) -> Result<(), &'static str> {
    #[cfg(any(target_os = "linux", target_os = "android"))]
    {
        let source = CString::new(source.as_os_str().as_bytes())
            .map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
        let target = CString::new(target.as_os_str().as_bytes())
            .map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
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
            Err("DESKTOP_V3_JOURNAL_PUBLISH_FAILED")
        }
    }
    #[cfg(not(any(target_os = "linux", target_os = "android")))]
    {
        let _ = (source, target);
        Err("DESKTOP_V3_JOURNAL_PUBLISH_UNSUPPORTED")
    }
}
fn temporary(directory: &Path, bytes: &[u8]) -> Result<PathBuf, &'static str> {
    let path = directory.join(format!(".pending-{:016x}", rand::random::<u64>()));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
            .open(&path)
            .map_err(|_| "DESKTOP_V3_JOURNAL_CREATE_FAILED")?;
        file.write_all(bytes)
            .map_err(|_| "DESKTOP_V3_JOURNAL_WRITE_FAILED")?;
        file.sync_all()
            .map_err(|_| "DESKTOP_V3_JOURNAL_SYNC_FAILED")
    })();
    if let Err(error) = result {
        let _ = std::fs::remove_file(&path);
        return Err(error);
    }
    Ok(path)
}

impl Journal {
    /// No reservation is published until the compiled C1/C2 source closure is
    /// complete. Tests exercise atomic I/O through the private value-only layer.
    pub fn reserve(files: &Path, binding: &Binding) -> Result<Self, &'static str> {
        check_v2_barrier(files)?;
        sources::require_complete()?;
        Self::reserve_record(files, binding)
    }
    fn reserve_record(files: &Path, binding: &Binding) -> Result<Self, &'static str> {
        check_v2_barrier(files)?;
        check_directory(files, false)?;
        let directory = files.join(DIRECTORY);
        match std::fs::DirBuilder::new().mode(0o700).create(&directory) {
            Ok(()) => sync(files)?,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err("DESKTOP_V3_JOURNAL_CREATE_FAILED"),
        }
        check_directory(&directory, true)?;
        let guard = lock(&directory)?;
        let path = directory.join("pending.json");
        let record = Record {
            schema: 2,
            backend: "linux_desktop".into(),
            protocol_revision: REVISION.into(),
            context: binding.context().clone(),
            pins: binding.pins().clone(),
            build_id: binding.pins().build_id(),
            sources: sources::current_hashes(),
            stage: Stage::Reserved,
            boot_id: None,
            cleanup: Cleanup::default(),
        };
        let bytes = record.bytes()?;
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
        let record = Record::parse(&bytes)?;
        let binding = Binding::from_captured(record.context.clone(), record.pins.clone())?;
        request
            .storage
            .matches_capture(
                &StorageCapture::from_profile(request.profile)
                    .map_err(|_| "DESKTOP_V3_CLEANUP_SCOPE_MISMATCH")?,
            )
            .map_err(|_| "DESKTOP_V3_CLEANUP_SCOPE_MISMATCH")?;
        request
            .storage
            .matches_context(&record.context)
            .map_err(|_| "DESKTOP_V3_CLEANUP_SCOPE_MISMATCH")?;
        if !binding.matches_profile(request.profile, binding.session())
            || record.context.record()["uid"].as_u64() != Some(u64::from(request.uid))
            || &record.pins != request.pins
        {
            return Err("DESKTOP_V3_CLEANUP_SCOPE_MISMATCH");
        }
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
            return Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED");
        }
        let m = file.metadata().map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
        Ok(Self {
            directory,
            path,
            _lock: guard,
            bytes,
            device: m.dev(),
            inode: m.ino(),
            record,
            clear_state: ClearState::Linked,
        })
    }
    fn unchanged(&self) -> Result<(), &'static str> {
        check_directory(&self.directory, true)?;
        let (file, bytes) = read(&self.path)?;
        let m = file.metadata().map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
        if bytes != self.bytes || m.dev() != self.device || m.ino() != self.inode {
            return Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED");
        }
        Ok(())
    }
    fn persist(&mut self, record: Record) -> Result<(), &'static str> {
        let bytes = record.bytes()?;
        let candidate = temporary(&self.directory, &bytes)?;
        let result = (|| {
            self.unchanged()?;
            std::fs::rename(&candidate, &self.path)
                .map_err(|_| "DESKTOP_V3_JOURNAL_PUBLISH_FAILED")?;
            let (file, current) = read(&self.path)?;
            if current != bytes {
                return Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED");
            }
            let m = file.metadata().map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
            self.bytes = bytes;
            self.device = m.dev();
            self.inode = m.ino();
            self.record = record;
            sync(&self.directory)
        })();
        let _ = std::fs::remove_file(candidate);
        result
    }
    pub fn prepared(&mut self, boot: &str) -> Result<(), &'static str> {
        if self.record.stage != Stage::Reserved || !valid_boot(boot) {
            return Err("DESKTOP_V3_JOURNAL_TRANSITION");
        }
        let mut next = self.record.clone();
        next.boot_id = Some(boot.into());
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
            return Err("DESKTOP_V3_JOURNAL_TRANSITION");
        }
        let mut next = self.record.clone();
        next.stage = stage;
        self.persist(next)
    }
    pub fn boot_id(&self) -> Option<&str> {
        self.record.boot_id.as_deref()
    }
    pub fn source_hashes(&self) -> &SourceHashes {
        &self.record.sources
    }
    pub fn recovered_boot(&mut self, boot: &str) -> Result<(), &'static str> {
        if !valid_boot(boot)
            || self
                .record
                .boot_id
                .as_deref()
                .is_some_and(|old| old != boot)
        {
            return Err("DESKTOP_V3_CLEANUP_SCOPE_MISMATCH");
        }
        let mut next = self.record.clone();
        next.boot_id = Some(boot.into());
        next.stage = Stage::Unknown;
        self.persist(next)
    }
    /// Complete is durable remote/transport proof only. The caller must
    /// separately establish local and SDK retirement before clearing.
    /// Revalidation also handles a finish() whose rename succeeded but the
    /// final directory fsync failed.
    pub fn durable_complete(&self) -> Result<Option<Cleanup>, &'static str> {
        if self.record.stage != Stage::Complete {
            return Ok(None);
        }
        if !self.record.cleanup.complete() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        self.unchanged()?;
        sync(&self.directory)?;
        Ok(Some(self.record.cleanup.clone()))
    }

    pub fn stage(&self) -> Stage {
        self.record.stage
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
    /// Only a trusted local/SDK finalizer may call clear. If unlink succeeds
    /// but directory fsync fails, the same owner retries the sync without a
    /// second unlink or pretending a missing path is proof of success.
    pub fn clear(&mut self) -> Result<(), &'static str> {
        if self.record.stage != Stage::Complete || !self.record.cleanup.complete() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        match self.clear_state {
            ClearState::Cleared => return Ok(()),
            ClearState::Linked => {
                self.unchanged()?;
                std::fs::remove_file(&self.path)
                    .map_err(|_| "DESKTOP_V3_JOURNAL_CLEAR_FAILED")?;
                self.clear_state = ClearState::UnlinkedNeedsSync;
            }
            ClearState::UnlinkedNeedsSync => {
                check_directory(&self.directory, true)?;
                match std::fs::symlink_metadata(&self.path) {
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
                    _ => return Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED"),
                }
            }
        }
        sync(&self.directory)?;
        self.clear_state = ClearState::Cleared;
        Ok(())
    }
}

#[cfg(test)]
mod tests;
