//! noBackupFilesDirを親として渡す。原子的JSON保存はprotocol ownerからだけ呼ぶ。
use eidolon_model::workspace::{Preferences, SETTINGS_BYTES};
use serde::Serialize;
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};
const DIRECTORY: &str = "workspace";
const NAME: &str = "layout.json";
static TEMPORARY: AtomicU64 = AtomicU64::new(1);
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub persistent: bool,
    pub loaded: bool,
    pub saved: u64,
    pub error: Option<&'static str>,
}
pub struct Store {
    root: Option<PathBuf>,
    pub value: Preferences,
    pub report: Report,
}
impl Store {
    pub fn load(parent: Option<&Path>) -> Self {
        let mut store = Self {
            root: None,
            value: Preferences::default(),
            report: Report::default(),
        };
        let Some(parent) = parent else {
            return store;
        };
        let result = (|| {
            let info = std::fs::symlink_metadata(parent).map_err(|_| "LAYOUT_PATH_INVALID")?;
            // Androidのapp専用親directoryはprimary groupへ書込み可の場合がある。
            if !parent.is_absolute()
                || !info.is_dir()
                || info.uid() != unsafe { libc::getuid() }
                || info.mode() & 0o002 != 0
                || (info.mode() & 0o020 != 0 && info.gid() != unsafe { libc::getgid() })
            {
                return Err("LAYOUT_PATH_INVALID");
            }
            let root = parent.join(DIRECTORY);
            match std::fs::DirBuilder::new().mode(0o700).create(&root) {
                Ok(()) => File::open(parent)
                    .and_then(|file| file.sync_all())
                    .map_err(|_| "LAYOUT_IO")?,
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
                Err(_) => return Err("LAYOUT_PATH_INVALID"),
            }
            Self::check_root(&root)?;
            store.root = Some(root.clone());
            store.report.persistent = true;
            let path = root.join(NAME);
            let Some(file) = Self::existing(&path)? else {
                return Ok(());
            };
            let mut bytes = vec![];
            file.take((SETTINGS_BYTES + 1) as u64)
                .read_to_end(&mut bytes)
                .map_err(|_| "LAYOUT_IO")?;
            store.value = Preferences::decode(&bytes)?;
            store.report.loaded = true;
            Ok(())
        })();
        if let Err(error) = result {
            store.report.error = Some(error);
        }
        store
    }
    fn check_root(root: &Path) -> Result<(), &'static str> {
        let info = std::fs::symlink_metadata(root).map_err(|_| "LAYOUT_PATH_INVALID")?;
        if !info.is_dir() || info.uid() != unsafe { libc::getuid() } || info.mode() & 0o777 != 0o700
        {
            return Err("LAYOUT_PATH_INVALID");
        }
        Ok(())
    }
    fn existing(path: &Path) -> Result<Option<File>, &'static str> {
        let info = match std::fs::symlink_metadata(path) {
            Ok(info) => info,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err("LAYOUT_PATH_INVALID"),
        };
        if !info.is_file()
            || info.uid() != unsafe { libc::getuid() }
            || info.mode() & 0o777 != 0o600
        {
            return Err("LAYOUT_PATH_INVALID");
        }
        if info.len() > SETTINGS_BYTES as u64 {
            return Err("LAYOUT_TOO_LARGE");
        }
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(path)
            .map_err(|_| "LAYOUT_PATH_INVALID")?;
        let opened = file.metadata().map_err(|_| "LAYOUT_IO")?;
        if (opened.dev(), opened.ino()) != (info.dev(), info.ino()) {
            return Err("LAYOUT_PATH_INVALID");
        }
        Ok(Some(file))
    }
    pub fn save(&mut self) -> Result<(), &'static str> {
        self.save_before_rename(|| {})
    }
    fn save_before_rename(&mut self, before_rename: impl FnOnce()) -> Result<(), &'static str> {
        let result = (|| {
            let bytes = self.value.encode()?;
            let Some(root) = self.root.as_ref() else {
                return self.report.error.map_or(Ok(()), Err);
            };
            Self::check_root(root)?;
            let path = root.join(NAME);
            // oversized/corrupt regular JSONは明示的な設定変更で置換できる。symlink等は拒否する。
            if let Ok(info) = std::fs::symlink_metadata(&path)
                && (!info.is_file()
                    || info.uid() != unsafe { libc::getuid() }
                    || info.mode() & 0o777 != 0o600)
            {
                return Err("LAYOUT_PATH_INVALID");
            }
            let mut temporary = None;
            for _ in 0..16 {
                let candidate = root.join(format!(
                    ".layout-{}-{}",
                    std::process::id(),
                    TEMPORARY.fetch_add(1, Ordering::Relaxed)
                ));
                match OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(&candidate)
                {
                    Ok(file) => {
                        temporary = Some((candidate, file));
                        break;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
                    Err(_) => return Err("LAYOUT_IO"),
                }
            }
            let (temporary, mut file) = temporary.ok_or("LAYOUT_IO")?;
            let written = (|| -> std::io::Result<()> {
                file.write_all(&bytes)?;
                file.sync_all()?;
                before_rename();
                std::fs::rename(&temporary, &path)?;
                File::open(root)?.sync_all()
            })();
            let _ = std::fs::remove_file(&temporary);
            written.map_err(|_| "LAYOUT_IO")?;
            self.report.saved += 1;
            Ok(())
        })();
        self.report.error = result.err();
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use eidolon_model::workspace::Background;
    use std::os::unix::fs::{PermissionsExt, symlink};
    struct Directory(PathBuf);
    impl Directory {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "eidolon-layout-{}-{}",
                std::process::id(),
                TEMPORARY.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::DirBuilder::new()
                .mode(0o700)
                .create(&path)
                .unwrap();
            Self(path)
        }
    }
    impl Drop for Directory {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    #[test]
    #[ignore = "owned subprocess helper; invoked by crash recovery test"]
    fn crash_writer_fixture() {
        let root = std::path::PathBuf::from(
            std::env::var_os("EIDOLON_LAYOUT_CRASH_ROOT").expect("fixture parent is required"),
        );
        let mut store = Store::load(Some(&root));
        store.value.background = Background::Virtual;
        store
            .save_before_rename(|| {
                std::fs::write(root.join("writer-ready"), b"temporary file synced").unwrap();
                loop {
                    std::thread::park_timeout(std::time::Duration::from_secs(1));
                }
            })
            .unwrap();
    }
    #[test]
    fn killed_writer_after_temp_fsync_keeps_last_committed_settings() {
        use std::os::unix::process::ExitStatusExt;
        use std::{
            process::{Command, Stdio},
            time::{Duration, Instant},
        };
        let dir = Directory::new();
        let mut previous = Store::load(Some(&dir.0));
        previous.save().unwrap();
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--ignored",
                "--exact",
                "preferences::tests::crash_writer_fixture",
                "--nocapture",
            ])
            .env("EIDOLON_LAYOUT_CRASH_ROOT", &dir.0)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while !dir.0.join("writer-ready").exists() && Instant::now() < deadline {
            if child.try_wait().unwrap().is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        let ready = dir.0.join("writer-ready").exists();
        let _ = child.kill();
        let status = child.wait().unwrap();
        assert!(ready, "writer did not reach pre-rename checkpoint");
        assert_eq!(status.signal(), Some(libc::SIGKILL));
        let loaded = Store::load(Some(&dir.0));
        assert!(loaded.report.loaded);
        assert!(loaded.report.error.is_none());
        assert_eq!(loaded.value.background, Background::Passthrough);
        assert!(std::fs::read_dir(dir.0.join(DIRECTORY)).unwrap().any(|e| {
            e.unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".layout-")
        }));
    }
    #[test]
    fn atomic_file_preserves_previous_commit_ignores_incomplete_temp_and_recovers_corrupt_schema() {
        let dir = Directory::new();
        let mut store = Store::load(Some(&dir.0));
        assert!(store.report.persistent);
        assert!(!store.report.loaded);
        store.value.background = Background::Virtual;
        store.save().unwrap();
        let root = dir.0.join(DIRECTORY);
        let path = root.join(NAME);
        assert_eq!(std::fs::metadata(&path).unwrap().mode() & 0o777, 0o600);
        std::fs::write(root.join(".layout-interrupted"), b"partial JSON").unwrap();
        assert_eq!(
            Store::load(Some(&dir.0)).value.background,
            Background::Virtual
        );
        std::fs::write(&path,b"{\"schema\":999,\"background\":\"virtual\",\"keyboard_layout\":\"us\",\"placements\":[]}").unwrap();
        let mut invalid = Store::load(Some(&dir.0));
        assert_eq!(invalid.report.error, Some("LAYOUT_SCHEMA"));
        assert_eq!(invalid.value.background, Background::Passthrough);
        invalid.value.background = Background::Virtual;
        invalid.save().unwrap();
        assert!(Store::load(Some(&dir.0)).report.loaded);
    }
    #[test]
    fn unsafe_path_and_oversized_json_fall_back_without_following_symlink_or_losing_other_file() {
        let dir = Directory::new();
        let mut store = Store::load(Some(&dir.0));
        let path = dir.0.join(DIRECTORY).join(NAME);
        let other = dir.0.join("other");
        std::fs::write(&other, b"preserve").unwrap();
        symlink(&other, &path).unwrap();
        assert_eq!(
            Store::load(Some(&dir.0)).report.error,
            Some("LAYOUT_PATH_INVALID")
        );
        assert!(store.save().is_err());
        assert_eq!(std::fs::read(&other).unwrap(), b"preserve");
        std::fs::remove_file(&path).unwrap();
        std::fs::write(&path, vec![b' '; SETTINGS_BYTES + 1]).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        let mut large = Store::load(Some(&dir.0));
        assert_eq!(large.report.error, Some("LAYOUT_TOO_LARGE"));
        large.save().unwrap();
        assert!(Store::load(Some(&dir.0)).report.loaded);
    }
}
