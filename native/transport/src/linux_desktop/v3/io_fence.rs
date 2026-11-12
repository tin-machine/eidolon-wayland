//! An OS-owned, non-unlinked v3 I/O generation lock. This only proves that
//! a *participating* older SSH/Tokio generation has released its resources;
//! it never proves remote-process, Waypipe or SDK retirement.
use super::journal;
use std::{
    fs::{File, OpenOptions},
    os::{
        fd::AsRawFd,
        unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
    },
    path::Path,
};

/// Keep this value in the synchronous SSH owner until *after* Tokio's runtime
/// and every transport task have been joined and dropped.
pub struct IoGenerationFence {
    _file: File,
    preexisting: bool,
}

impl IoGenerationFence {
    pub fn acquire(files: &Path) -> Result<Self, &'static str> {
        journal::check_directory(files, false)?;
        // Inspect the pending state BEFORE creating any lock. If an old
        // journal predates the fence, creating io.lock would manufacture
        // evidence that its unknown old I/O generation participated.
        let had_pending = journal::pending(files)?.is_some();
        let directory = files.join(journal::DIRECTORY);
        match std::fs::DirBuilder::new().mode(0o700).create(&directory) {
            Ok(()) => journal::sync(files)?,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err("DESKTOP_V3_JOURNAL_CREATE_FAILED"),
        }
        journal::check_directory(&directory, true)?;
        let path = directory.join("io.lock");
        let preexisting = match std::fs::symlink_metadata(&path) {
            Ok(_) => true,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
            Err(_) => return Err("DESKTOP_V3_JOURNAL_INVALID"),
        };
        if had_pending && !preexisting {
            return Err("DESKTOP_PREVIOUS_CONNECTION_PENDING");
        }
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
            .open(&path)
            .map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
        let actual = file.metadata().map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
        let named = std::fs::symlink_metadata(&path)
            .map_err(|_| "DESKTOP_V3_JOURNAL_INVALID")?;
        if !actual.is_file()
            || actual.uid() != unsafe { libc::getuid() }
            || actual.mode() & 0o777 != 0o600
            || actual.nlink() != 1
            || actual.len() != 0
            || actual.dev() != named.dev()
            || actual.ino() != named.ino()
        {
            return Err("DESKTOP_V3_JOURNAL_INVALID");
        }
        // SAFETY: file remains open and is never unlinked by this component.
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err("DESKTOP_PREVIOUS_CONNECTION_PENDING");
        }
        if !preexisting {
            file.sync_all().map_err(|_| "DESKTOP_V3_JOURNAL_SYNC_FAILED")?;
            journal::sync(&directory)?;
        }
        Ok(Self {
            _file: file,
            preexisting,
        })
    }

    /// A successful reacquisition of a previously durable lock is evidence
    /// only for earlier *participating* I/O owners. The caller must also
    /// match the captured pending journal and independently drain its Router.
    pub fn previous_io_closed(&self) -> bool {
        self.preexisting
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::linux_desktop::v3::owner::test_support::{Temp, binding};

    #[test]
    fn fresh_fence_is_exclusive_and_reacquisition_is_separate_from_cleanup() {
        let dir = Temp::new();
        let first = IoGenerationFence::acquire(&dir.0).unwrap();
        assert!(!first.previous_io_closed());
        assert!(IoGenerationFence::acquire(&dir.0).is_err());
        drop(first);
        let next = IoGenerationFence::acquire(&dir.0).unwrap();
        assert!(next.previous_io_closed());
    }

    #[test]
    fn pre_fence_pending_journal_never_becomes_previous_io_proof() {
        let dir = Temp::new();
        let prior = journal::Journal::reserve(&dir.0, &binding()).unwrap();
        drop(prior);
        let path = dir.0.join(journal::DIRECTORY).join("io.lock");
        assert!(!path.exists());
        assert_eq!(
            IoGenerationFence::acquire(&dir.0).err(),
            Some("DESKTOP_PREVIOUS_CONNECTION_PENDING")
        );
        assert!(!path.exists());
    }
}
