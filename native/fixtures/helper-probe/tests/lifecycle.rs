use eidolon_core::{helper, runtime::Runtime};
use eidolon_model::{
    commands::{Command, RuntimeConfig, RuntimePaths},
    ids::CommandSeq,
    state::{RuntimeState, Status},
};
use std::{
    fs,
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(1);
        let root = std::env::temp_dir().join(format!(
            "eh-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        fs::create_dir(root.join("files")).unwrap();
        fs::create_dir(root.join("lib")).unwrap();
        fs::copy(
            env!("CARGO_BIN_EXE_eidolon-probe-bin"),
            root.join("lib/libeidolon_probe_bin.so"),
        )
        .unwrap();
        Self(root)
    }
    fn exe(&self) -> PathBuf {
        self.0.join("lib/libeidolon_probe_bin.so")
    }
    fn files(&self) -> PathBuf {
        self.0.join("files")
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
fn wait(runtime: &Runtime, condition: impl Fn() -> bool) {
    let end = Instant::now() + Duration::from_secs(5);
    while !condition() {
        assert!(Instant::now() < end, "{:?}", runtime.snapshot());
        thread::sleep(Duration::from_millis(2));
    }
}

#[test]
fn apk_shape_helper_has_ten_framed_echoes_explicit_exit_and_owned_cleanup() {
    let fixture = Fixture::new();
    let report = helper::probe(
        &fixture.exe(),
        &fixture.files(),
        "0123456789abcdef",
        Arc::new(AtomicBool::new(false)),
        |_| {},
    );
    assert_eq!(report.error, None);
    assert_eq!(report.cycles, 10);
    assert_eq!(report.owned_children, 0);
    assert!(report.directory_removed);
    assert!(!fixture.files().join("rt/0123456789abcdef").exists());
}

#[test]
fn cancellation_reaps_child_and_collision_preserves_other_owners_files() {
    let fixture = Fixture::new();
    let stop = Arc::new(AtomicBool::new(false));
    let report = helper::probe(
        &fixture.exe(),
        &fixture.files(),
        "0123456789abcdef",
        stop.clone(),
        |report| {
            if report.owned_children == 1 {
                stop.store(true, Ordering::Release);
            }
        },
    );
    assert!(report.cancelled && report.directory_removed && report.owned_children == 0);
    let existing = fixture.files().join("rt/0123456789abcdef");
    fs::create_dir(&existing).unwrap();
    fs::write(existing.join("sentinel"), b"another owner").unwrap();
    let report = helper::probe(
        &fixture.exe(),
        &fixture.files(),
        "0123456789abcdef",
        Arc::new(AtomicBool::new(false)),
        |_| {},
    );
    assert_eq!(report.error, Some("CONFIG_INVALID"));
    assert_eq!(
        fs::read(existing.join("sentinel")).unwrap(),
        b"another owner"
    );
}

#[test]
fn runtime_owner_keeps_snapshot_responsive_and_stops_helper_before_destroy() {
    let fixture = Fixture::new();
    let config = RuntimeConfig {
        schema: 1,
        paths: Some(RuntimePaths {
            native_library_dir: fixture.0.join("lib").to_str().unwrap().into(),
            files_dir: fixture.files().to_str().unwrap().into(),
        }),
    };
    let runtime = Runtime::start(config).unwrap();
    wait(&runtime, || runtime.snapshot().state == RuntimeState::Ready);
    assert_eq!(
        runtime.command(Command::HelperProbe {
            schema: 1,
            request_id: CommandSeq::new(1).unwrap(),
            nonce: "0123456789abcdef".into()
        }),
        Status::Accepted
    );
    wait(&runtime, || runtime.snapshot().helper_workers == 1);
    assert_eq!(runtime.snapshot().state, RuntimeState::Ready);
    runtime.request_stop();
    wait(&runtime, || runtime.can_destroy());
    assert_eq!(runtime.snapshot().helper_workers, 0);
    assert_eq!(runtime.snapshot().helper.unwrap().owned_children, 0);
    assert!(!fixture.files().join("rt/0123456789abcdef").exists());
}
