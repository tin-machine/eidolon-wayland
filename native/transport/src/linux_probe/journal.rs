//! Linux専用の未回収owner。旧Termux journalは読まない・照合しない・削除しない。
use super::Request;
use eidolon_model::ids::SessionId;
use serde::{Deserialize, Serialize};
use std::{
    ffi::CString,
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::unix::{
        ffi::OsStrExt,
        fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
    },
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
    #[serde(default)]
    gtk: Option<GtkStored>,
}
#[derive(Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct GtkStored {
    case: super::GtkCase,
    bundle_sha256: String,
    source_sha256: String,
    binary_sha256: String,
    gtk_version: String,
}
impl GtkStored {
    fn from(binding: &super::GtkBinding) -> Self {
        Self {
            case: binding.case,
            bundle_sha256: binding.pins.gtk_bundle_sha256.clone(),
            source_sha256: binding.pins.source_sha256.clone(),
            binary_sha256: binding.pins.binary_sha256.clone(),
            gtk_version: binding.pins.gtk_version.clone(),
        }
    }
}
fn directory_name(gtk: Option<&super::GtkBinding>) -> &'static str {
    if gtk.is_some() {
        "linux-gtk-probe-state"
    } else {
        "linux-probe-state"
    }
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
fn sync(path: &Path) -> std::io::Result<()> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)
        .and_then(|f| f.sync_all())
}

fn publish_error(error: std::io::Error) -> &'static str {
    match error.raw_os_error() {
        Some(libc::EEXIST) => "LINUX_CLEANUP_PENDING",
        Some(libc::ENOSYS) => "LINUX_JOURNAL_PUBLISH_ENOSYS",
        Some(libc::EINVAL) => "LINUX_JOURNAL_PUBLISH_EINVAL",
        Some(libc::EOPNOTSUPP) => "LINUX_JOURNAL_PUBLISH_EOPNOTSUPP",
        Some(libc::EPERM) => "LINUX_JOURNAL_PUBLISH_EPERM",
        Some(libc::EACCES) => "LINUX_JOURNAL_PUBLISH_EACCES",
        Some(libc::ENOSPC) => "LINUX_JOURNAL_PUBLISH_ENOSPC",
        Some(libc::EROFS) => "LINUX_JOURNAL_PUBLISH_EROFS",
        Some(libc::EXDEV) => "LINUX_JOURNAL_PUBLISH_EXDEV",
        _ => "LINUX_JOURNAL_PUBLISH_FAILED",
    }
}

/// Android app domains can forbid hard links. Publish without replacing an existing
/// pending file (including a dangling symlink), using the kernel operation directly.
/// Unsupported syscall/filesystem/policy is a failure; never fall back to rename().
fn publish_exclusive(temporary: &Path, path: &Path) -> Result<(), &'static str> {
    #[cfg(any(target_os = "linux", target_os = "android"))]
    {
        let source =
            CString::new(temporary.as_os_str().as_bytes()).map_err(|_| "LINUX_JOURNAL_INVALID")?;
        let target =
            CString::new(path.as_os_str().as_bytes()).map_err(|_| "LINUX_JOURNAL_INVALID")?;
        // SAFETY: both C strings remain alive throughout this synchronous syscall.
        // AT_FDCWD is ignored for these absolute paths. No libc renameat2 symbol
        // (and therefore no newer Android API-level dynamic symbol) is required.
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
        } else {
            Err(publish_error(std::io::Error::last_os_error()))
        }
    }
    #[cfg(not(any(target_os = "linux", target_os = "android")))]
    {
        let _ = (temporary, path);
        Err("LINUX_JOURNAL_PUBLISH_UNSUPPORTED")
    }
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
        let directory = request.files.join(directory_name(request.gtk));
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
        if stored.schema != if request.gtk.is_some() { 2 } else { 1 }
            || stored.gtk != request.gtk.map(GtkStored::from)
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
        let directory = request.files.join(directory_name(request.gtk));
        match std::fs::DirBuilder::new().mode(0o700).create(&directory) {
            Ok(()) => sync(request.files).map_err(|_| "LINUX_JOURNAL_PARENT_SYNC_FAILED")?,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err("LINUX_JOURNAL_MKDIR_FAILED"),
        }
        check_directory(&directory, true)?;
        let path = directory.join("pending.json");
        match std::fs::symlink_metadata(&path) {
            Ok(_) => return Err("LINUX_CLEANUP_PENDING"),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(_) => return Err("LINUX_JOURNAL_INVALID"),
        }
        let mut value = serde_json::json!({"schema":if request.gtk.is_some(){2}else{1},"backend":"linux_posix","profile_binding":request.profile_binding,
            "profile_revision":request.profile_revision,"session":request.session,"uid":request.uid,"nonce":request.nonce,
            "helper_sha256":request.pins.helper_sha256(),"bundle_sha256":request.pins.bundle_sha256,
            "waypipe_sha256":request.pins.waypipe_sha256,"fixture_sha256":request.pins.fixture_sha256});
        if let Some(gtk) = request.gtk {
            value["gtk"] =
                serde_json::to_value(GtkStored::from(gtk)).map_err(|_| "LINUX_JOURNAL_FAILED")?;
        }
        let bytes = serde_json::to_vec(&value).map_err(|_| "LINUX_JOURNAL_FAILED")?;
        let temporary = directory.join(format!(".pending-{:016x}", rand::random::<u64>()));
        let publish = (|| -> Result<(), &'static str> {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&temporary)
                .map_err(|_| "LINUX_JOURNAL_CREATE_FAILED")?;
            file.write_all(&bytes)
                .map_err(|_| "LINUX_JOURNAL_WRITE_FAILED")?;
            file.sync_all()
                .map_err(|_| "LINUX_JOURNAL_FILE_SYNC_FAILED")?;
            // exclusive publication。別nonce/profileのpendingを置換しない。
            publish_exclusive(&temporary, &path)
        })();
        let _ = std::fs::remove_file(&temporary);
        publish?;
        sync(&directory).map_err(|_| "LINUX_JOURNAL_DIRECTORY_SYNC_FAILED")?;
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
        sync(&self.directory).map_err(|_| "LINUX_JOURNAL_DIRECTORY_SYNC_FAILED")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{os::unix::fs::symlink, sync::Barrier};

    struct Directory(PathBuf);
    impl Directory {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!("elj-{:016x}", rand::random::<u64>()));
            std::fs::DirBuilder::new()
                .mode(0o700)
                .create(&path)
                .unwrap();
            Self(path)
        }
        fn candidate(&self, name: &str, bytes: &[u8]) -> PathBuf {
            let path = self.0.join(name);
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&path)
                .unwrap();
            file.write_all(bytes).unwrap();
            file.sync_all().unwrap();
            path
        }
    }
    impl Drop for Directory {
        fn drop(&mut self) {
            std::fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn exclusive_publish_preserves_complete_file_owner_inode_and_single_link() {
        let dir = Directory::new();
        let source = dir.candidate("candidate", b"complete owner record");
        let before = std::fs::metadata(&source).unwrap();
        let target = dir.0.join("pending.json");
        publish_exclusive(&source, &target).unwrap();
        sync(&dir.0).unwrap();
        assert!(!source.exists());
        let (file, bytes) = read(&target).unwrap();
        let after = file.metadata().unwrap();
        assert_eq!(bytes, b"complete owner record");
        assert_eq!(
            (after.dev(), after.ino(), after.uid()),
            (before.dev(), before.ino(), before.uid())
        );
        assert_eq!(after.mode() & 0o777, 0o600);
        assert_eq!(after.nlink(), 1);
    }

    #[test]
    fn interposed_file_and_dangling_symlink_are_never_replaced() {
        for link in [false, true] {
            let dir = Directory::new();
            let source = dir.candidate("candidate", b"new record");
            let target = dir.0.join("pending.json");
            // Models another owner publishing after reserve's absence check.
            let missing = dir.0.join("absent-other-owner");
            if link {
                symlink(&missing, &target).unwrap();
            } else {
                dir.candidate("pending.json", b"existing record");
            }
            let before = std::fs::symlink_metadata(&target).unwrap();
            assert_eq!(
                publish_exclusive(&source, &target),
                Err("LINUX_CLEANUP_PENDING")
            );
            let after = std::fs::symlink_metadata(&target).unwrap();
            assert_eq!((after.dev(), after.ino()), (before.dev(), before.ino()));
            assert_eq!(std::fs::read(&source).unwrap(), b"new record");
            if link {
                assert_eq!(std::fs::read_link(&target).unwrap(), missing);
                assert!(!missing.exists());
            } else {
                assert_eq!(std::fs::read(&target).unwrap(), b"existing record");
            }
        }
    }

    #[test]
    fn simultaneous_publish_has_one_winner_and_keeps_loser_candidate() {
        let dir = Directory::new();
        let first = dir.candidate("first", b"first record");
        let second = dir.candidate("second", b"second record");
        let target = dir.0.join("pending.json");
        let barrier = Barrier::new(2);
        let results = std::thread::scope(|scope| {
            let a = scope.spawn(|| {
                barrier.wait();
                publish_exclusive(&first, &target)
            });
            let b = scope.spawn(|| {
                barrier.wait();
                publish_exclusive(&second, &target)
            });
            [a.join().unwrap(), b.join().unwrap()]
        });
        assert_eq!(results.iter().filter(|r| r.is_ok()).count(), 1);
        assert_eq!(
            results
                .iter()
                .filter(|r| **r == Err("LINUX_CLEANUP_PENDING"))
                .count(),
            1
        );
        let winner = usize::from(results[0].is_err());
        assert_eq!(
            std::fs::read(&target).unwrap(),
            [b"first record".as_slice(), b"second record".as_slice()][winner]
        );
        assert_eq!(
            [first.exists(), second.exists()],
            [winner != 0, winner != 1]
        );
        assert_eq!(std::fs::metadata(&target).unwrap().nlink(), 1);
    }

    #[test]
    fn unsupported_or_denied_publication_has_fixed_fail_closed_reason() {
        for (errno, expected) in [
            (libc::ENOSYS, "LINUX_JOURNAL_PUBLISH_ENOSYS"),
            (libc::EINVAL, "LINUX_JOURNAL_PUBLISH_EINVAL"),
            (libc::EOPNOTSUPP, "LINUX_JOURNAL_PUBLISH_EOPNOTSUPP"),
            (libc::EPERM, "LINUX_JOURNAL_PUBLISH_EPERM"),
            (libc::EACCES, "LINUX_JOURNAL_PUBLISH_EACCES"),
            (libc::ENOSPC, "LINUX_JOURNAL_PUBLISH_ENOSPC"),
        ] {
            assert_eq!(
                publish_error(std::io::Error::from_raw_os_error(errno)),
                expected
            );
        }
    }
}
