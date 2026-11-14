//! Generation-wide SSH/Tokio I/O barrier, separate from journal owner.lock.
//! An old pending.json without a pre-existing participating io.lock can never
//! be upgraded to "previous connection closed" by creating a fresh lock.
use super::{
    Binding, Pins,
    journal::{self, Pending},
    sources::SourceHashes,
};
use eidolon_model::{ids::SessionId, linux_desktop_v3::Context};
use std::{
    fs::{File, OpenOptions},
    io,
    os::{
        fd::AsRawFd,
        unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
    },
    path::{Path, PathBuf},
};

const DIRECTORY: &str = "linux-desktop-v3-state";
const LOCK_FILE: &str = "io.lock";

/// This must remain alive *outside* the Tokio runtime until that runtime and
/// every SSH handle, forward route and task have been destroyed. It is not
/// evidence that remote children or local SDK resources have been retired.
pub struct IoGenerationFence {
    _lock: File,
    _directory: PathBuf,
    generation: u64,
    previous: Option<PreviousIoClosed>,
}

/// Move-only token for a particular prior journal owner. No Deserialize, no
/// Clone and no public constructor. Safe only if ALL product v3 generations
/// acquire the same stable lock before starting a connection.
pub struct PreviousIoClosed {
    old_context: Context,
    old_pins: Pins,
    old_sources: SourceHashes,
    current_session: SessionId,
}
impl PreviousIoClosed {
    pub fn matches(&self, session: SessionId, binding: &Binding, sources: &SourceHashes) -> bool {
        session == self.current_session
            && binding.session() != session
            && binding.context() == &self.old_context
            && binding.pins() == &self.old_pins
            && sources == &self.old_sources
    }
}

impl IoGenerationFence {
    pub fn acquire(
        files: &Path,
        session: SessionId,
        generation: u64,
    ) -> Result<Self, &'static str> {
        if generation == 0 {
            return Err("DESKTOP_PREVIOUS_CONNECTION_PENDING");
        }
        journal::check_v2_barrier(files)?;
        journal::check_directory(files, false)?;
        // Read the old journal *before* creating a lock. A lock that did not
        // predate a pending owner cannot prove its participation or retirement.
        let pending = journal::pending(files)?;
        let directory = files.join(DIRECTORY);
        match std::fs::DirBuilder::new().mode(0o700).create(&directory) {
            Ok(()) => journal::sync(files)?,
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err("DESKTOP_V3_IO_FENCE_INVALID"),
        }
        journal::check_directory(&directory, true)?;
        let dm =
            std::fs::symlink_metadata(&directory).map_err(|_| "DESKTOP_V3_IO_FENCE_INVALID")?;
        let path = directory.join(LOCK_FILE);
        let preexisting = match std::fs::symlink_metadata(&path) {
            Ok(_) => true,
            Err(e) if e.kind() == io::ErrorKind::NotFound => false,
            Err(_) => return Err("DESKTOP_V3_IO_FENCE_INVALID"),
        };
        if pending.is_some() && !preexisting {
            return Err("DESKTOP_PREVIOUS_CONNECTION_UNPROVEN");
        }
        let mut options = OpenOptions::new();
        options
            .read(true)
            .write(true)
            .truncate(false)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK);
        if !preexisting {
            options.create_new(true).mode(0o600);
        }
        let file = options
            .open(&path)
            .map_err(|_| "DESKTOP_V3_IO_FENCE_INVALID")?;
        let metadata = file.metadata().map_err(|_| "DESKTOP_V3_IO_FENCE_INVALID")?;
        if !metadata.is_file()
            || metadata.uid() != unsafe { libc::getuid() }
            || metadata.mode() & 0o777 != 0o600
            || metadata.nlink() != 1
            || metadata.len() != 0
        {
            return Err("DESKTOP_V3_IO_FENCE_INVALID");
        }
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err("DESKTOP_PREVIOUS_CONNECTION_PENDING");
        }
        let named = std::fs::symlink_metadata(&path).map_err(|_| "DESKTOP_V3_IO_FENCE_INVALID")?;
        let current_dir =
            std::fs::symlink_metadata(&directory).map_err(|_| "DESKTOP_V3_IO_FENCE_INVALID")?;
        if metadata.dev() != named.dev()
            || metadata.ino() != named.ino()
            || current_dir.dev() != dm.dev()
            || current_dir.ino() != dm.ino()
        {
            return Err("DESKTOP_V3_IO_FENCE_INVALID");
        }
        if !preexisting {
            file.sync_all().map_err(|_| "DESKTOP_V3_IO_FENCE_INVALID")?;
            journal::sync(&directory)?;
        }
        let previous = pending
            .map(|pending| Self::capture_previous(pending, session))
            .transpose()?;
        Ok(Self {
            _lock: file,
            _directory: directory,
            generation,
            previous,
        })
    }
    fn capture_previous(
        pending: Pending,
        session: SessionId,
    ) -> Result<PreviousIoClosed, &'static str> {
        let old = Binding::from_captured(pending.context().clone(), pending.pins().clone())?;
        if old.session() == session {
            return Err("DESKTOP_PREVIOUS_CONNECTION_PENDING");
        }
        Ok(PreviousIoClosed {
            old_context: pending.context().clone(),
            old_pins: pending.pins().clone(),
            old_sources: pending.sources().clone(),
            current_session: session,
        })
    }
    pub fn generation(&self) -> u64 {
        self.generation
    }
    /// Consuming this token does not release the lock. It remains held for the
    /// full lifetime of the subsequent recovery connection and Tokio runtime.
    pub fn take_previous(&mut self) -> Option<PreviousIoClosed> {
        self.previous.take()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::linux_desktop::v3::{
        journal::Journal,
        owner::test_support::{Temp, binding},
    };
    use std::os::unix::fs::symlink;

    #[test]
    fn stable_lock_excludes_live_owners_then_allows_new_generation() {
        let dir = Temp::new();
        let old = IoGenerationFence::acquire(&dir.0, SessionId::new(1).unwrap(), 1).unwrap();
        assert_eq!(old.generation(), 1);
        assert!(IoGenerationFence::acquire(&dir.0, SessionId::new(2).unwrap(), 2).is_err());
        drop(old);
        assert!(dir.0.join(DIRECTORY).join(LOCK_FILE).is_file());
        IoGenerationFence::acquire(&dir.0, SessionId::new(2).unwrap(), 2).unwrap();
    }
    #[test]
    fn preexisting_pending_without_participating_lock_stays_unproven() {
        let dir = Temp::new();
        let _journal = Journal::reserve(&dir.0, &binding()).unwrap();
        assert!(matches!(
            IoGenerationFence::acquire(&dir.0, SessionId::new(7).unwrap(), 2),
            Err("DESKTOP_PREVIOUS_CONNECTION_UNPROVEN")
        ));
    }
    #[test]
    fn recovered_owner_token_is_scoped_to_new_session_and_old_capture() {
        let dir = Temp::new();
        let old = IoGenerationFence::acquire(&dir.0, SessionId::new(1).unwrap(), 1).unwrap();
        let _journal = Journal::reserve(&dir.0, &binding()).unwrap();
        drop(old);
        let mut current =
            IoGenerationFence::acquire(&dir.0, SessionId::new(7).unwrap(), 2).unwrap();
        let proof = current.take_previous().unwrap();
        let binding = binding();
        assert!(proof.matches(
            SessionId::new(7).unwrap(),
            &binding,
            &super::super::sources::current_hashes()
        ));
        assert!(!proof.matches(
            SessionId::new(8).unwrap(),
            &binding,
            &super::super::sources::current_hashes()
        ));
        assert!(current.take_previous().is_none());
    }
    #[test]
    fn symlink_lock_never_becomes_a_participating_owner() {
        let dir = Temp::new();
        let path = dir.0.join(DIRECTORY);
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&path)
            .unwrap();
        let target = dir.0.join("target");
        std::fs::write(&target, "").unwrap();
        symlink(&target, path.join(LOCK_FILE)).unwrap();
        assert!(IoGenerationFence::acquire(&dir.0, SessionId::new(1).unwrap(), 1).is_err());
    }
}
