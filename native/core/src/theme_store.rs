//! テーマ設定と最後に適用したimportを一つの原子的recordで保存する。
//! fsync/8MiBのI/Oは専用worker。最新一件だけを待機させ、core/UIのlockでは実行しない。
use eidolon_model::commands::ThemeSelection;
use serde::Serialize;
use std::{
    fs::{File, OpenOptions},
    io::Write,
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
    sync::{Arc, Condvar, Mutex},
    thread::{self, JoinHandle},
};
#[derive(Clone, Default)]
pub struct Value {
    pub selection: ThemeSelection,
    pub reduced_motion: bool,
    pub imported: Option<Arc<[u8]>>,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub persistent: bool,
    pub loaded: bool,
    pub saved: u64,
    pub pending: bool,
    pub error: Option<&'static str>,
}
#[derive(Default)]
struct State {
    pending: Option<Value>,
    stop: bool,
    report: Report,
}
pub struct Store {
    pub root: Option<PathBuf>,
    shared: Arc<(Mutex<State>, Condvar)>,
    worker: Option<JoinHandle<()>>,
}
fn check_directory(path: &Path, parent: bool) -> Result<(), &'static str> {
    let m = std::fs::symlink_metadata(path).map_err(|_| "THEME_STORAGE_PATH")?;
    let permissions = if parent {
        m.mode() & 0o002 == 0 && (m.mode() & 0o020 == 0 || m.gid() == unsafe { libc::getgid() })
    } else {
        m.mode() & 0o777 == 0o700
    };
    if !path.is_absolute() || !m.is_dir() || m.uid() != unsafe { libc::getuid() } || !permissions {
        return Err("THEME_STORAGE_PATH");
    }
    Ok(())
}
fn open(parent: &Path) -> Result<PathBuf, &'static str> {
    check_directory(parent, true)?;
    let root = parent.join("themes");
    match std::fs::DirBuilder::new().mode(0o700).create(&root) {
        Ok(()) => File::open(parent)
            .and_then(|f| f.sync_all())
            .map_err(|_| "THEME_STORAGE_IO")?,
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
        Err(_) => return Err("THEME_STORAGE_PATH"),
    }
    check_directory(&root, false)?;
    Ok(root)
}
fn load(root: &Path) -> Result<(Value, bool), &'static str> {
    let file = root.join("state.bin");
    if !file.try_exists().map_err(|_| "THEME_STORAGE_IO")? {
        return Ok((Value::default(), false));
    }
    let bytes = eidolon_theme::package::read_file(&file, unsafe { libc::getuid() })
        .map_err(|_| "THEME_STORAGE_READ")?;
    let value = decode(&bytes)?;
    Ok((value, true))
}
fn encode(value: &Value) -> Vec<u8> {
    let mut bytes = b"EIDPREF1".to_vec();
    bytes.push(match value.selection {
        ThemeSelection::Off => 0,
        ThemeSelection::Builtin => 1,
        ThemeSelection::Imported => 2,
    });
    bytes.push(u8::from(value.reduced_motion));
    bytes.extend_from_slice(&(value.imported.as_ref().map_or(0, |b| b.len()) as u32).to_le_bytes());
    if let Some(source) = &value.imported {
        bytes.extend_from_slice(source);
    }
    bytes
}
fn decode(bytes: &[u8]) -> Result<Value, &'static str> {
    if bytes.len() < 14 || &bytes[..8] != b"EIDPREF1" || bytes[9] > 1 {
        return Err("THEME_STORAGE_INVALID");
    }
    let selection = match bytes[8] {
        0 => ThemeSelection::Off,
        1 => ThemeSelection::Builtin,
        2 => ThemeSelection::Imported,
        _ => return Err("THEME_STORAGE_INVALID"),
    };
    if u32::from_le_bytes(bytes[10..14].try_into().unwrap()) as usize != bytes.len() - 14 {
        return Err("THEME_STORAGE_INVALID");
    }
    let imported = if bytes.len() > 14 {
        eidolon_theme::package::decode(&bytes[14..]).map_err(|_| "THEME_STORAGE_INVALID")?;
        Some(Arc::from(&bytes[14..]))
    } else {
        None
    };
    if selection == ThemeSelection::Imported && imported.is_none() {
        return Err("THEME_STORAGE_INVALID");
    }
    Ok(Value {
        selection,
        reduced_motion: bytes[9] == 1,
        imported,
    })
}
fn save(root: &Path, value: &Value, serial: u64) -> Result<(), &'static str> {
    check_directory(root, false)?;
    let temporary = root.join(format!(".state-{}-{serial}", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&temporary)
        .map_err(|_| "THEME_STORAGE_IO")?;
    let result = (|| {
        file.write_all(&encode(value))?;
        file.sync_all()?;
        std::fs::rename(&temporary, root.join("state.bin"))?;
        File::open(root)?.sync_all()
    })();
    let _ = std::fs::remove_file(temporary);
    result.map_err(|_| "THEME_STORAGE_IO")
}
impl Store {
    pub fn start(parent: Option<&Path>) -> std::io::Result<(Self, Value)> {
        let mut report = Report::default();
        let mut value = Value::default();
        let root = parent.and_then(|parent| match open(parent) {
            Ok(root) => {
                match load(&root) {
                    Ok((loaded, exists)) => {
                        value = loaded;
                        report.loaded = exists;
                    }
                    Err(error) => report.error = Some(error),
                }
                report.persistent = true;
                Some(root)
            }
            Err(error) => {
                report.error = Some(error);
                None
            }
        });
        let shared = Arc::new((
            Mutex::new(State {
                report,
                ..Default::default()
            }),
            Condvar::new(),
        ));
        let owned = shared.clone();
        let directory = root.clone();
        let worker = thread::Builder::new()
            .name("eidolon-theme-store".into())
            .spawn(move || {
                let mut serial = 0;
                loop {
                    let value = {
                        let mut state = owned.0.lock().unwrap_or_else(|e| e.into_inner());
                        while state.pending.is_none() && !state.stop {
                            state = owned.1.wait(state).unwrap_or_else(|e| e.into_inner());
                        }
                        if state.pending.is_none() && state.stop {
                            break;
                        }
                        state.pending.take().unwrap()
                    };
                    serial += 1;
                    let result = directory
                        .as_ref()
                        .map_or(Ok(()), |root| save(root, &value, serial));
                    let mut state = owned.0.lock().unwrap_or_else(|e| e.into_inner());
                    if directory.is_some() {
                        state.report.error = result.err();
                        state.report.saved += u64::from(result.is_ok());
                    }
                    state.report.pending = state.pending.is_some();
                }
            })?;
        Ok((
            Self {
                root,
                shared,
                worker: Some(worker),
            },
            value,
        ))
    }
    pub fn save(&self, value: Value) {
        let mut state = self.shared.0.lock().unwrap_or_else(|e| e.into_inner());
        if !state.stop {
            state.pending = Some(value);
            state.report.pending = true;
            self.shared.1.notify_one();
        }
    }
    pub fn report(&self) -> Report {
        self.shared
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .report
            .clone()
    }
    pub fn stop(&self) {
        self.shared.0.lock().unwrap_or_else(|e| e.into_inner()).stop = true;
        self.shared.1.notify_one();
    }
    pub fn finished(&self) -> bool {
        self.worker.as_ref().is_none_or(|w| w.is_finished())
    }
}
impl Drop for Store {
    fn drop(&mut self) {
        self.stop();
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn record_is_atomic_complete_and_corrupt_contents_can_be_replaced() {
        let root = std::env::temp_dir().join(format!(
            "eidolon-theme-store-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&root)
            .unwrap();
        let (store, _) = Store::start(Some(&root)).unwrap();
        let value = Value {
            selection: ThemeSelection::Imported,
            reduced_motion: true,
            imported: Some(Arc::from(eidolon_theme::package::encode(
                &eidolon_theme::cyan_orbit(),
            ))),
        };
        let bytes = encode(&value);
        for size in 0..bytes.len() {
            assert!(decode(&bytes[..size]).is_err());
        }
        store.save(value);
        drop(store); // stop drains final pending write before join.
        let (store, value) = Store::start(Some(&root)).unwrap();
        assert!(store.report().loaded);
        assert_eq!(value.selection, ThemeSelection::Imported);
        assert!(value.reduced_motion);
        let file = store.root.as_ref().unwrap().join("state.bin");
        drop(store);
        std::fs::write(&file, b"interrupted invalid settings").unwrap();
        let (store, value) = Store::start(Some(&root)).unwrap();
        assert!(store.report().error.is_some() && store.report().persistent);
        assert_eq!(value.selection, ThemeSelection::Off);
        store.save(Value::default());
        drop(store);
        let (store, value) = Store::start(Some(&root)).unwrap();
        assert!(store.report().loaded && store.report().error.is_none());
        assert!(value.imported.is_none());
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }
}
