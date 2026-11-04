//! 未確認のremote資源を新接続・runtime再生成で忘れない。FS操作はI/O ownerだけ。
use crate::{forward_probe::valid_nonce, host_key::Profile, launcher::Identity};
use eidolon_model::ids::SessionId;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
    sync::atomic::{AtomicI64, Ordering},
};

const MAX_RECORDS: usize = 8;
const MAX_BYTES: u64 = 16384;
const NAME: &str = "transport-pending.json";
const DIRECTORY: &str = "transport-state";
static NEXT_SESSION: AtomicI64 = AtomicI64::new(1);
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Entry {
    pub nonce: String,
    pub session: SessionId,
    pub uid: u32,
    pub binding: String,
    pub child: Option<Identity>,
}
#[derive(Clone, Debug, Serialize)]
pub struct Pending {
    pub nonce: String,
    pub session: SessionId,
    pub uid: u32,
    pub child: Option<Identity>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Stored {
    schema: u32,
    entries: Vec<Entry>,
    #[serde(default)]
    last_session: Option<SessionId>,
}
pub(crate) struct Journal {
    root: Option<PathBuf>,
    entries: Vec<Entry>,
    last_session: Option<SessionId>,
}

pub(crate) fn binding(profile: &Profile) -> String {
    let key = profile
        .host_key
        .split_whitespace()
        .take(2)
        .collect::<Vec<_>>()
        .join(" ");
    let text = format!(
        "{}\0{}\0{}\0{key}",
        profile.installation, profile.profile_id, profile.username
    );
    Sha256::digest(text.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
impl Journal {
    pub fn load(root: Option<&Path>) -> Result<Self, &'static str> {
        let mut journal = Self {
            root: None,
            entries: Vec::new(),
            last_session: None,
        };
        let Some(parent) = root else {
            return Ok(journal);
        };
        let info = std::fs::symlink_metadata(parent).map_err(|_| "CLEANUP_JOURNAL_INVALID")?;
        // Android filesDirは0771で、groupもappのprimary group。親の権限は変更しない。
        // SAFETY: getuid/getgidはいずれも引数やポインターを持たない。
        if !parent.is_absolute()
            || !info.is_dir()
            || info.uid() != unsafe { libc::getuid() }
            || info.mode() & 0o002 != 0
            || (info.mode() & 0o020 != 0 && info.gid() != unsafe { libc::getgid() })
        {
            return Err("CLEANUP_JOURNAL_INVALID");
        }
        let root = parent.join(DIRECTORY);
        match std::fs::DirBuilder::new().mode(0o700).create(&root) {
            Ok(()) => File::open(parent)
                .and_then(|file| file.sync_all())
                .map_err(|_| "CLEANUP_JOURNAL_FAILED")?,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err("CLEANUP_JOURNAL_INVALID"),
        }
        journal.root = Some(root.clone());
        Self::check_root(&root).map_err(|_| "CLEANUP_JOURNAL_INVALID")?;
        let path = root.join(NAME);
        let file = match Self::open_existing(&path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(journal),
            Err(_) => return Err("CLEANUP_JOURNAL_INVALID"),
        };
        let mut bytes = Vec::new();
        file.take(MAX_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| "CLEANUP_JOURNAL_INVALID")?;
        if bytes.len() > MAX_BYTES as usize {
            return Err("CLEANUP_JOURNAL_INVALID");
        }
        let stored: Stored =
            serde_json::from_slice(&bytes).map_err(|_| "CLEANUP_JOURNAL_INVALID")?;
        if stored.schema != 1 || !Self::valid(&stored.entries) {
            return Err("CLEANUP_JOURNAL_INVALID");
        }
        let largest = stored.entries.iter().map(|e| e.session).max();
        if stored.last_session.is_some() && stored.last_session < largest {
            return Err("CLEANUP_JOURNAL_INVALID");
        }
        journal.last_session = stored.last_session.or(largest);
        journal.entries = stored.entries;
        Ok(journal)
    }
    fn check_root(root: &Path) -> std::io::Result<()> {
        let metadata = std::fs::symlink_metadata(root)?;
        // SAFETY: getuidは引数もポインターも持たない。
        if !root.is_absolute()
            || !metadata.is_dir()
            || metadata.uid() != unsafe { libc::getuid() }
            || metadata.mode() & 0o777 != 0o700
        {
            return Err(std::io::ErrorKind::PermissionDenied.into());
        }
        Ok(())
    }
    fn open_existing(path: &Path) -> std::io::Result<File> {
        let metadata = std::fs::symlink_metadata(path)?;
        // SAFETY: getuidは引数もポインターも持たない。
        if !metadata.is_file()
            || metadata.uid() != unsafe { libc::getuid() }
            || metadata.mode() & 0o777 != 0o600
            || metadata.len() > MAX_BYTES
        {
            return Err(std::io::ErrorKind::InvalidData.into());
        }
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(path)?;
        let opened = file.metadata()?;
        if (opened.dev(), opened.ino()) != (metadata.dev(), metadata.ino()) {
            return Err(std::io::ErrorKind::InvalidData.into());
        }
        Ok(file)
    }
    fn valid(entries: &[Entry]) -> bool {
        entries.len() <= MAX_RECORDS
            && entries.iter().enumerate().all(|(index, e)| {
                valid_nonce(&e.nonce)
                    && e.binding.len() == 64
                    && e.binding
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                    && e.child.as_ref().is_none_or(Identity::valid)
                    && !entries[..index]
                        .iter()
                        .any(|previous| previous.nonce == e.nonce)
            })
    }
    fn replace(&mut self, entries: Vec<Entry>) -> Result<(), &'static str> {
        let last = self
            .last_session
            .max(entries.iter().map(|e| e.session).max());
        self.store(entries, last)
    }
    fn store(
        &mut self,
        entries: Vec<Entry>,
        last_session: Option<SessionId>,
    ) -> Result<(), &'static str> {
        if !Self::valid(&entries) {
            return Err("CLEANUP_JOURNAL_FULL");
        }
        if let Some(root) = &self.root {
            Self::check_root(root).map_err(|_| "CLEANUP_JOURNAL_FAILED")?;
            let path = root.join(NAME);
            match Self::open_existing(&path) {
                Ok(_) => (),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
                Err(_) => return Err("CLEANUP_JOURNAL_FAILED"),
            }
            let bytes = serde_json::to_vec(&Stored {
                schema: 1,
                entries: entries.clone(),
                last_session,
            })
            .map_err(|_| "CLEANUP_JOURNAL_FAILED")?;
            if bytes.len() > MAX_BYTES as usize {
                return Err("CLEANUP_JOURNAL_FULL");
            }
            let temporary = root.join(format!(".transport-pending-{:016x}", rand::random::<u64>()));
            let result = (|| -> std::io::Result<()> {
                let mut file = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(&temporary)?;
                file.write_all(&bytes)?;
                file.sync_all()?;
                std::fs::rename(&temporary, &path)?;
                File::open(root)?.sync_all()?;
                Ok(())
            })();
            let _ = std::fs::remove_file(&temporary);
            result.map_err(|_| "CLEANUP_JOURNAL_FAILED")?;
        }
        self.entries = entries;
        self.last_session = last_session;
        Ok(())
    }
    pub fn allocate_session(&mut self) -> Result<SessionId, &'static str> {
        let floor = match self.last_session {
            Some(id) => id.checked_next().ok_or("SESSION_EXHAUSTED")?.get(),
            None => 1,
        };
        let mut current = NEXT_SESSION.load(Ordering::Acquire);
        loop {
            let selected = current.max(floor);
            let next = selected.checked_add(1).ok_or("SESSION_EXHAUSTED")?;
            match NEXT_SESSION.compare_exchange(current, next, Ordering::AcqRel, Ordering::Acquire)
            {
                Ok(_) => {
                    let id = SessionId::new(selected).map_err(|_| "SESSION_EXHAUSTED")?;
                    self.store(self.entries.clone(), Some(id))?;
                    return Ok(id);
                }
                Err(value) => current = value,
            }
        }
    }
    pub fn remember(&mut self, entry: Entry) -> Result<(), &'static str> {
        if self.entries.iter().any(|e| e.nonce == entry.nonce) {
            return Err("NONCE_REUSED");
        }
        let mut entries = self.entries.clone();
        entries.push(entry);
        self.replace(entries)
    }
    pub fn child(&mut self, nonce: &str, child: &Identity) -> Result<(), &'static str> {
        let Some(index) = self.entries.iter().position(|e| e.nonce == nonce) else {
            return Err("STALE");
        };
        if self.entries[index].child.as_ref() == Some(child) {
            return Ok(());
        }
        if self.entries[index].child.is_some() {
            return Err("STALE");
        }
        let mut entries = self.entries.clone();
        entries[index].child = Some(child.clone());
        self.replace(entries)
    }
    pub fn forget(&mut self, nonce: &str) -> Result<(), &'static str> {
        let entries = self
            .entries
            .iter()
            .filter(|e| e.nonce != nonce)
            .cloned()
            .collect();
        self.replace(entries)
    }
    pub fn entries(&self) -> Vec<Entry> {
        self.entries.clone()
    }
    pub fn pending(&self) -> Vec<Pending> {
        self.entries
            .iter()
            .map(|e| Pending {
                nonce: e.nonce.clone(),
                session: e.session,
                uid: e.uid,
                child: e.child.clone(),
            })
            .collect()
    }
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
    struct Root(PathBuf);
    impl Root {
        fn new() -> Self {
            let root = std::env::temp_dir()
                .join(format!("eidolon-journal-{:016x}", rand::random::<u64>()));
            std::fs::DirBuilder::new()
                .mode(0o700)
                .create(&root)
                .unwrap();
            Self(root)
        }
    }
    impl Drop for Root {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    fn entry(n: u64) -> Entry {
        Entry {
            nonce: format!("{n:016x}"),
            session: SessionId::new(1).unwrap(),
            uid: 4242,
            binding: "a".repeat(64),
            child: None,
        }
    }
    #[test]
    fn remembers_child_across_recreation_bounds_records_and_clears_only_persisted_ack() {
        let root = Root::new();
        let mut journal = Journal::load(Some(&root.0)).unwrap();
        for n in 0..8 {
            journal.remember(entry(n)).unwrap();
        }
        assert_eq!(journal.remember(entry(9)), Err("CLEANUP_JOURNAL_FULL"));
        assert_eq!(journal.remember(entry(0)), Err("NONCE_REUSED"));
        let child = Identity {
            pid: 42,
            group: 42,
            start: "123".into(),
            state: "S".into(),
        };
        journal.child(&format!("{:016x}", 0), &child).unwrap();
        drop(journal);
        let mut reloaded = Journal::load(Some(&root.0)).unwrap();
        assert_eq!(reloaded.pending().len(), 8);
        assert_eq!(reloaded.pending()[0].child, Some(child));
        assert_eq!(
            std::fs::metadata(root.0.join(DIRECTORY).join(NAME))
                .unwrap()
                .mode()
                & 0o777,
            0o600
        );
        reloaded.forget(&format!("{:016x}", 0)).unwrap();
        assert_eq!(Journal::load(Some(&root.0)).unwrap().pending().len(), 7);
    }
    #[test]
    fn corrupt_foreign_or_symlink_journal_is_rejected_without_replacement() {
        let root = Root::new();
        Journal::load(Some(&root.0)).unwrap();
        let target = root.0.join("unrelated");
        std::fs::write(&target, b"preserve").unwrap();
        std::os::unix::fs::symlink(&target, root.0.join(DIRECTORY).join(NAME)).unwrap();
        assert!(Journal::load(Some(&root.0)).is_err());
        assert_eq!(std::fs::read(&target).unwrap(), b"preserve");
        std::fs::remove_file(root.0.join(DIRECTORY).join(NAME)).unwrap();
        std::fs::write(
            root.0.join(DIRECTORY).join(NAME),
            b"{\"schema\":2,\"entries\":[]}",
        )
        .unwrap();
        std::fs::set_permissions(
            root.0.join(DIRECTORY).join(NAME),
            std::fs::Permissions::from_mode(0o600),
        )
        .unwrap();
        assert!(Journal::load(Some(&root.0)).is_err());
        assert_eq!(
            std::fs::read(root.0.join(DIRECTORY).join(NAME)).unwrap(),
            b"{\"schema\":2,\"entries\":[]}"
        );
    }
    #[test]
    fn android_parent_0771_keeps_its_mode_and_uses_private_journal_directory() {
        let root = Root::new();
        std::fs::set_permissions(&root.0, std::fs::Permissions::from_mode(0o771)).unwrap();
        Journal::load(Some(&root.0))
            .unwrap()
            .remember(entry(1))
            .unwrap();
        assert_eq!(std::fs::metadata(&root.0).unwrap().mode() & 0o777, 0o771);
        assert_eq!(
            std::fs::metadata(root.0.join("transport-state"))
                .unwrap()
                .mode()
                & 0o777,
            0o700
        );
    }
    #[test]
    fn session_serial_advances_across_journal_and_supervisor_recreation() {
        let root = Root::new();
        let first = Journal::load(Some(&root.0))
            .unwrap()
            .allocate_session()
            .unwrap();
        let second = Journal::load(Some(&root.0))
            .unwrap()
            .allocate_session()
            .unwrap();
        let other = Journal::load(None).unwrap().allocate_session().unwrap();
        assert!(second > first && other > second);
        assert!(
            Journal::load(Some(&root.0))
                .unwrap()
                .allocate_session()
                .unwrap()
                > other
        );
    }
    #[test]
    fn failed_persistence_does_not_claim_the_state_changed() {
        let root = Root::new();
        let mut journal = Journal::load(Some(&root.0)).unwrap();
        journal.remember(entry(1)).unwrap();
        std::fs::set_permissions(
            root.0.join(DIRECTORY).join(NAME),
            std::fs::Permissions::from_mode(0o644),
        )
        .unwrap();
        assert_eq!(
            journal.forget(&entry(1).nonce),
            Err("CLEANUP_JOURNAL_FAILED")
        );
        assert_eq!(journal.pending().len(), 1);
    }
}
