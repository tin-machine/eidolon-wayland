//! One durable record for one app on one captured connection.
use super::{Generation, InvocationId, boot_identity, hex};
use eidolon_model::{gui_session_policy::GuiUnresponsiveGrace, linux_desktop_v3::ProfileStorageId};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, DirBuilder, File, OpenOptions},
    io::{self, Read, Write},
    os::{
        fd::AsRawFd,
        unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
    },
    path::{Path, PathBuf},
};

const INVALID: &str = "SYSTEMD_JOURNAL_INVALID";
const FAILED: &str = "SYSTEMD_JOURNAL_WRITE_FAILED";
const PENDING: &str = "SYSTEMD_CLEANUP_PENDING";
const BACKEND: &str = "ubuntu_systemd_v1";
const LIMIT: u64 = 4096;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum JournalStage {
    Reserved,
    Sent,
    Running,
    Stopping,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct JournalRecord {
    schema: u32,
    backend: String,
    host_binding: String,
    storage_identity: ProfileStorageId,
    app_key: String,
    generation: String,
    boot_id: String,
    invocation_id: Option<String>,
    grace: GuiUnresponsiveGrace,
    stage: JournalStage,
    local_retired: bool,
    remote_retired: bool,
}

impl JournalRecord {
    pub fn generation(&self) -> Option<Generation> {
        Generation::parse(&self.generation)
    }

    pub fn invocation(&self) -> Option<InvocationId> {
        self.invocation_id.as_deref().and_then(InvocationId::parse)
    }

    pub fn stage(&self) -> JournalStage {
        self.stage
    }

    pub fn boot_id(&self) -> &str {
        &self.boot_id
    }

    pub fn grace(&self) -> GuiUnresponsiveGrace {
        self.grace
    }

    pub fn remote_retired(&self) -> bool {
        self.remote_retired
    }

    pub fn local_retired(&self) -> bool {
        self.local_retired
    }

    pub fn complete(&self) -> bool {
        self.local_retired && self.remote_retired
    }
}

pub struct Journal {
    directory: PathBuf,
    guard: File,
    host_binding: String,
    storage_identity: ProfileStorageId,
    app_key: String,
    record: Option<JournalRecord>,
}

impl Journal {
    pub fn open(
        files: &Path,
        host_binding: &str,
        storage_identity: &ProfileStorageId,
        app_key: &str,
    ) -> Result<Self, &'static str> {
        if !files.is_absolute() || !hex(host_binding, 64) || !hex(app_key, 64) {
            return Err(INVALID);
        }
        let parent = fs::symlink_metadata(files).map_err(|_| INVALID)?;
        // Android's private files dir may allow the app's own primary group.
        if !parent.is_dir()
            || parent.uid() != uid()
            || parent.mode() & 0o002 != 0
            || (parent.mode() & 0o020 != 0 && parent.gid() != unsafe { libc::getgid() })
        {
            return Err(INVALID);
        }
        let mut directory = files.to_path_buf();
        for component in [BACKEND, host_binding, storage_identity.as_str(), app_key] {
            let next = directory.join(component);
            match DirBuilder::new().mode(0o700).create(&next) {
                Ok(()) => sync(&directory)?,
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
                Err(_) => return Err(FAILED),
            }
            let md = fs::symlink_metadata(&next).map_err(|_| INVALID)?;
            if !md.is_dir() || md.uid() != uid() || md.mode() & 0o777 != 0o700 {
                return Err(INVALID);
            }
            directory = next;
        }
        let guard = open_regular(&directory.join("owner.lock"), true).map_err(|_| INVALID)?;
        // SAFETY: guard owns an open regular file for the duration of this journal.
        if unsafe { libc::flock(guard.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err(PENDING);
        }
        let mut journal = Self {
            directory,
            guard,
            host_binding: host_binding.into(),
            storage_identity: storage_identity.clone(),
            app_key: app_key.into(),
            record: None,
        };
        journal.record = journal.read()?;
        Ok(journal)
    }

    fn read(&self) -> Result<Option<JournalRecord>, &'static str> {
        let file = match open_regular(&self.directory.join("pending.json"), false) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(INVALID),
        };
        let mut bytes = Vec::new();
        file.take(LIMIT + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| INVALID)?;
        if bytes.len() > LIMIT as usize {
            return Err(INVALID);
        }
        let record: JournalRecord = serde_json::from_slice(&bytes).map_err(|_| INVALID)?;
        if record.schema != 1
            || record.backend != BACKEND
            || record.host_binding != self.host_binding
            || record.storage_identity != self.storage_identity
            || record.app_key != self.app_key
            || Generation::parse(&record.generation).is_none()
            || !boot_identity(&record.boot_id)
            || record
                .invocation_id
                .as_deref()
                .is_some_and(|id| InvocationId::parse(id).is_none())
            || (record.stage == JournalStage::Running && record.invocation().is_none())
            || (record.stage == JournalStage::Reserved && record.invocation().is_some())
            || (record.stage != JournalStage::Stopping
                && (record.local_retired || record.remote_retired))
        {
            return Err(INVALID);
        }
        Ok(Some(record))
    }

    pub fn record(&self) -> Option<&JournalRecord> {
        self.record.as_ref()
    }

    pub fn reserve(
        &mut self,
        generation: Generation,
        boot_id: &str,
        grace: GuiUnresponsiveGrace,
    ) -> Result<(), &'static str> {
        if self.record.is_some() {
            return Err(PENDING);
        }
        if !boot_identity(boot_id) {
            return Err(INVALID);
        }
        self.write(JournalRecord {
            schema: 1,
            backend: BACKEND.into(),
            host_binding: self.host_binding.clone(),
            storage_identity: self.storage_identity.clone(),
            app_key: self.app_key.clone(),
            generation: generation.to_string(),
            boot_id: boot_id.into(),
            invocation_id: None,
            grace,
            stage: JournalStage::Reserved,
            local_retired: false,
            remote_retired: false,
        })
    }

    pub fn sent(&mut self, generation: Generation) -> Result<(), &'static str> {
        let mut next = self.current(generation)?;
        if next.stage != JournalStage::Reserved {
            return Err(PENDING);
        }
        next.stage = JournalStage::Sent;
        self.write(next)
    }

    pub fn identify(
        &mut self,
        generation: Generation,
        invocation: InvocationId,
    ) -> Result<(), &'static str> {
        let mut next = self.current(generation)?;
        if !matches!(next.stage, JournalStage::Sent | JournalStage::Stopping)
            || next.invocation().is_some_and(|id| id != invocation)
        {
            return Err(PENDING);
        }
        next.invocation_id = Some(invocation.to_string());
        self.write(next)
    }

    pub fn running(&mut self, generation: Generation) -> Result<(), &'static str> {
        let mut next = self.current(generation)?;
        if next.stage != JournalStage::Sent || next.invocation().is_none() {
            return Err(PENDING);
        }
        next.stage = JournalStage::Running;
        self.write(next)
    }

    pub fn stopping(&mut self, generation: Generation) -> Result<(), &'static str> {
        let mut next = self.current(generation)?;
        if next.stage == JournalStage::Stopping {
            return Ok(());
        }
        next.stage = JournalStage::Stopping;
        self.write(next)
    }

    pub fn retire(
        &mut self,
        generation: Generation,
        local_retired: bool,
        remote_retired: bool,
    ) -> Result<bool, &'static str> {
        let mut next = self.current(generation)?;
        if next.stage != JournalStage::Stopping {
            return Err(PENDING);
        }
        next.local_retired |= local_retired;
        next.remote_retired |= remote_retired;
        let complete = next.complete();
        self.write(next)?;
        if complete {
            fs::remove_file(self.directory.join("pending.json")).map_err(|_| FAILED)?;
            sync(&self.directory)?;
            self.record = None;
        }
        Ok(complete)
    }

    fn current(&self, generation: Generation) -> Result<JournalRecord, &'static str> {
        self.record
            .as_ref()
            .filter(|record| record.generation() == Some(generation))
            .cloned()
            .ok_or(INVALID)
    }

    fn write(&mut self, next: JournalRecord) -> Result<(), &'static str> {
        let bytes = serde_json::to_vec(&next).map_err(|_| INVALID)?;
        let temporary = self
            .directory
            .join(format!(".pending-{}", Generation::new()));
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(&temporary)
            .map_err(|_| FAILED)?;
        let result = (|| {
            file.write_all(&bytes)?;
            file.sync_all()?;
            fs::rename(&temporary, self.directory.join("pending.json"))
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
            return Err(FAILED);
        }
        // After rename the directory entry is visible even if persistence is uncertain.
        self.record = Some(next);
        sync(&self.directory)
    }
}

impl Drop for Journal {
    fn drop(&mut self) {
        // Release ownership explicitly. A concurrent fork can inherit a copy of
        // the open file description until exec closes it.
        unsafe {
            libc::flock(self.guard.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

fn uid() -> u32 {
    // SAFETY: getuid has no arguments.
    unsafe { libc::getuid() }
}

fn open_regular(path: &Path, lock: bool) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK);
    if lock {
        options.write(true).create(true).mode(0o600);
    }
    let file = options.open(path)?;
    let md = file.metadata()?;
    if !md.is_file()
        || md.nlink() != 1
        || md.uid() != uid()
        || md.mode() & 0o777 != 0o600
        || (lock && md.len() != 0)
    {
        return Err(io::ErrorKind::InvalidData.into());
    }
    Ok(file)
}

fn sync(path: &Path) -> Result<(), &'static str> {
    File::open(path)
        .and_then(|f| f.sync_all())
        .map_err(|_| FAILED)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Temp(PathBuf);
    impl Drop for Temp {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn unresolved_launch_survives_reopen_and_can_only_be_retired_for_its_generation() {
        let temp =
            Temp(std::env::temp_dir().join(format!("eidolon-systemd-{}", Generation::new())));
        DirBuilder::new().mode(0o700).create(&temp.0).unwrap();
        let storage = ProfileStorageId::parse(&"a".repeat(32)).unwrap();
        let boot = "8d57f608-6975-4ce8-b0e0-c1bb726d6e66";
        let host = "b".repeat(64);
        let app = "c".repeat(64);
        let generation = Generation::new();
        let other = Generation::new();
        let mut journal = Journal::open(&temp.0, &host, &storage, &app).unwrap();
        journal
            .reserve(generation, boot, GuiUnresponsiveGrace::DEFAULT)
            .unwrap();
        assert!(Journal::open(&temp.0, &host, &storage, &app).is_err());
        journal.sent(generation).unwrap();
        drop(journal);
        let mut journal = Journal::open(&temp.0, &host, &storage, &app).unwrap();
        assert_eq!(journal.record().unwrap().stage(), JournalStage::Sent);
        assert_eq!(
            journal.reserve(other, boot, GuiUnresponsiveGrace::DEFAULT),
            Err(PENDING)
        );
        assert!(journal.stopping(other).is_err());
        journal.stopping(generation).unwrap();
        assert!(!journal.retire(generation, false, true).unwrap());
        drop(journal);
        let mut journal = Journal::open(&temp.0, &host, &storage, &app).unwrap();
        assert!(journal.retire(generation, true, false).unwrap());
        assert!(journal.record().is_none());
    }
}
