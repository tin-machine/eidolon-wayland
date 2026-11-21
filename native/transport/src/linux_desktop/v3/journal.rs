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

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
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
    #[serde(skip_serializing_if = "Option::is_none")]
    android_boot_count: Option<u32>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    local_retired: bool,
    // Admission starts closed, and publishing the first launchable catalog
    // requires removing this proof durably before exposing the permit path.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    local_launch_blocked: bool,
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
        let mut fields = vec![
            "schema", "backend", "protocol_revision", "context", "pins", "build_id",
            "sources", "stage", "boot_id", "cleanup",
        ];
        if value.get("android_boot_count").is_some() {
            fields.push("android_boot_count");
            if !value["android_boot_count"].is_u64() {
                return Err("DESKTOP_V3_JOURNAL_INVALID");
            }
        }
        if value.get("local_retired").is_some() {
            fields.push("local_retired");
            if value["local_retired"] != true {
                return Err("DESKTOP_V3_JOURNAL_INVALID");
            }
        }
        if value.get("local_launch_blocked").is_some() {
            fields.push("local_launch_blocked");
            if value["local_launch_blocked"] != true {
                return Err("DESKTOP_V3_JOURNAL_INVALID");
            }
        }
        exact_object(&value, &fields)?;
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
            || (record.local_launch_blocked
                && matches!(record.stage, Stage::ForwardRequested | Stage::StartRequested | Stage::Started))
        {
            return Err("DESKTOP_V3_JOURNAL_INVALID");
        }
        sources::validate_recovery_hashes(&record.sources)?;
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
    pub(crate) fn context(&self) -> &Context {
        &self.0.context
    }
    pub(crate) fn pins(&self) -> &Pins {
        &self.0.pins
    }
    pub(crate) fn sources(&self) -> &SourceHashes {
        &self.0.sources
    }
    pub(crate) fn has_durable_local_proof(&self, current: Option<u32>) -> bool {
        self.0.local_retired || self.0.local_launch_blocked
            || self.0.android_boot_count.zip(current).is_some_and(|(old, new)| new > old)
    }

    pub(crate) fn needs_android_reboot(&self, current: Option<u32>) -> bool {
        !self.0.local_retired && !self.0.local_launch_blocked && self.0.android_boot_count.zip(current)
            .is_some_and(|(old, new)| old == new)
    }

    /// The remote side can be closed independently while the old local SDK
    /// owner remains unproven. A durable Complete never needs a second query.
    pub(crate) fn needs_remote_confirmation_on_same_boot(&self, current: Option<u32>) -> bool {
        self.needs_android_reboot(current)
            && matches!(self.0.stage, Stage::StartRequested | Stage::Started | Stage::Unknown)
    }

    pub fn matches(&self, profile: &Profile, pins: &Pins) -> bool {
        let Ok(binding) = Binding::from_captured(self.0.context.clone(), self.0.pins.clone())
        else {
            return false;
        };
        binding.matches_profile(profile, binding.session())
            && &self.0.pins == pins
            && sources::validate_recovery_hashes(&self.0.sources).is_ok()
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
pub struct Journal {
    directory: PathBuf,
    path: PathBuf,
    _lock: File,
    bytes: Vec<u8>,
    device: u64,
    inode: u64,
    record: Record,
    /// unlink succeeded but parent sync may have failed; retry sync, never unlink another inode.
    clear_unlinked: bool,
}
pub(super) fn check_directory(path: &Path, private: bool) -> Result<(), &'static str> {
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
pub(super) fn sync(path: &Path) -> Result<(), &'static str> {
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
        Self::reserve_record(files, binding, None)
    }
    pub(crate) fn reserve_inspection(files: &Path, binding: &Binding, android_boot_count: Option<u32>) -> Result<Self, &'static str> {
        check_v2_barrier(files)?;
        sources::require_complete()?;
        Self::reserve_with_admission(files, binding, android_boot_count, true)
    }
    fn reserve_record(files: &Path, binding: &Binding, android_boot_count: Option<u32>) -> Result<Self, &'static str> {
        Self::reserve_with_admission(files, binding, android_boot_count, false)
    }
    fn reserve_with_admission(files: &Path, binding: &Binding, android_boot_count: Option<u32>, local_launch_blocked: bool) -> Result<Self, &'static str> {
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
            android_boot_count,
            local_retired: false,
            local_launch_blocked,
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
            clear_unlinked: false,
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
    pub(crate) fn open_local_launch(&mut self) -> Result<(), &'static str> {
        if self.record.stage != Stage::Prepared || !self.record.local_launch_blocked {
            return Err("DESKTOP_V3_JOURNAL_TRANSITION");
        }
        let mut next = self.record.clone();
        next.local_launch_blocked = false;
        self.persist(next)
    }

    pub(super) fn local_launch_was_blocked(&self) -> bool {
        self.record.local_launch_blocked
    }
    pub(crate) fn remember_legacy_boot(
        files: &Path,
        pending: &Pending,
        boot: u32,
    ) -> Result<(), &'static str> {
        if pending.0.android_boot_count.is_some() || pending.0.local_retired {
            return Ok(());
        }
        let directory = files.join(DIRECTORY);
        check_directory(&directory, true)?;
        let guard = lock(&directory)?;
        let path = directory.join("pending.json");
        let (_, bytes) = read(&path)?;
        let record = Record::parse(&bytes)?;
        if record.bytes()? != pending.0.bytes()? {
            return Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED");
        }
        super::local_directory::remember_legacy_owner(files, &record.context)?;
        let mut journal = Self::load(directory, path, guard, record, bytes)?;
        let mut next = journal.record.clone();
        next.android_boot_count = Some(boot);
        journal.persist(next)
    }

    pub(super) fn local_retired(&self) -> bool {
        self.record.local_retired
    }

    pub(crate) fn record_local_retirement(
        files: &Path,
        capture: &super::runtime::RunCapture,
        proof: &impl super::reclamation::LocalRetirementGate,
    ) -> Result<(), &'static str> {
        if !proof.verify(capture.binding().context(), capture.generation)?.complete() {
            return Err("DESKTOP_LOCAL_RETIREMENT_PENDING");
        }
        check_directory(files, false)?;
        let directory = files.join(DIRECTORY);
        check_directory(&directory, true)?;
        let guard = lock(&directory)?;
        let path = directory.join("pending.json");
        let (_, bytes) = read(&path)?;
        let record = Record::parse(&bytes)?;
        if &record.context != capture.binding().context() || &record.pins != capture.binding().pins()
            || record.sources != capture.evidence().source_hashes()
        {
            return Err("DESKTOP_V3_CLEANUP_SCOPE_MISMATCH");
        }
        let mut journal = Self::load(directory, path, guard, record, bytes)?;
        if journal.record.local_retired {
            return Ok(());
        }
        let mut next = journal.record.clone();
        next.local_retired = true;
        journal.persist(next)
    }

    pub(super) fn predates_android_boot(&self, current: u32) -> bool {
        self.record.android_boot_count.is_some_and(|old| current > old)
    }
    pub fn stage(&self) -> Stage {
        self.record.stage
    }
    pub fn cleanup(&self) -> Cleanup {
        self.record.cleanup.clone()
    }
    /// Reconfirm the bytes and inode, then sync both pending.json and its
    /// directory. A prior finish() fsync error does not grant durable Complete.
    pub fn ensure_complete_durable(&self) -> Result<Cleanup, &'static str> {
        if self.record.stage != Stage::Complete || !self.record.cleanup.complete() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        self.unchanged()?;
        let (file, data) = read(&self.path)?;
        if data != self.bytes {
            return Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED");
        }
        file.sync_all()
            .map_err(|_| "DESKTOP_V3_JOURNAL_SYNC_FAILED")?;
        sync(&self.directory)?;
        Ok(self.record.cleanup.clone())
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
    /// Only call from the local/SDK finalizer after capability validation.
    /// A failed directory fsync *after* unlink must not make the next attempt
    /// unlink a different pending.json; that would destroy an unrelated owner.
    pub(super) fn clear(&mut self) -> Result<(), &'static str> {
        if self.record.stage != Stage::Complete || !self.record.cleanup.complete() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        if !self.clear_unlinked {
            self.unchanged()?;
            std::fs::remove_file(&self.path).map_err(|_| "DESKTOP_V3_JOURNAL_CLEAR_FAILED")?;
            self.clear_unlinked = true;
        }
        match std::fs::symlink_metadata(&self.path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            _ => return Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED"),
        }
        sync(&self.directory)
    }
}

#[cfg(test)]
mod tests;
