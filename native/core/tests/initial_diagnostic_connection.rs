//! 通常起動の診断panelを残したまま、実Runtimeの接続/command dispatchを通す。
//! transportへのConnect受理までで止め、SSH接続・認証・Quest実描画の受入れとは区別する。
use eidolon_core::{diagnostics::Event, runtime::Runtime};
use eidolon_model::{
    commands::{Command, ConnectionProfile, RuntimeConfig, RuntimePaths},
    ids::{CommandSeq, SceneEpoch, SessionId},
    state::{RuntimeState, Status, XrState},
    surface::TargetKey,
};
use std::{
    os::unix::fs::DirBuilderExt,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
    thread,
    time::{Duration, Instant},
};

struct Fixture {
    runtime: Runtime,
    files: PathBuf,
    diagnostic: TargetKey,
    sdk_owned: bool,
}
impl Fixture {
    fn new(profile_id: &str, sdk_owned: bool) -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(1);
        let files = std::env::temp_dir().join(format!(
            "eidolon-diagnostic-connect-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&files)
            .unwrap();
        let installation = "1".repeat(32);
        let private = eidolon_transport::credentials::generate().unwrap();
        let host_key = eidolon_transport::credentials::public_key(&private).unwrap();
        let mut config = RuntimeConfig {
            installation: Some(installation.clone()),
            paths: Some(RuntimePaths {
                files_dir: files.to_str().unwrap().into(),
                native_library_dir: "/unused-diagnostic-connect-fixture".into(),
            }),
            ..Default::default()
        };
        if profile_id == "remote-linux" {
            config.linux_profile_v2 = Some(
                serde_json::json!({
                    "schema": 2, "installation": installation, "profile_id": profile_id,
                    "revision": "1", "display_name": "fixture", "backend": "linux_ssh",
                    "host": "127.0.0.1", "port": 8022, "username": "fixture", "host_key": host_key,
                })
                .to_string(),
            );
            config.linux_desktop_pins = Some(
                serde_json::json!({
                    "schema": 1, "backend": "linux_desktop", "base_bundle_sha256": "a".repeat(64),
                    "desktop_bundle_sha256": "b".repeat(64), "waypipe_sha256": "c".repeat(64),
                    "shm_fixture_sha256": "d".repeat(64), "desktop_binary_sha256": "e".repeat(64),
                })
                .to_string(),
            );
        } else {
            config.profiles.push(ConnectionProfile {
                schema: 1,
                profile_id: profile_id.into(),
                installation,
                username: "u0_a123".into(),
                host_key,
            });
        }
        let runtime = Runtime::start(config).unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        assert_eq!(
            completed(
                &runtime,
                Command::SceneReady {
                    schema: 1,
                    request_id: request(1),
                    scene: SceneEpoch::new(1).unwrap(),
                }
            ),
            Status::Accepted
        );
        assert_eq!(
            completed(
                &runtime,
                Command::SetActivityState {
                    schema: 1,
                    request_id: request(2),
                    visible: true,
                }
            ),
            Status::Accepted
        );
        assert_eq!(
            completed(
                &runtime,
                Command::SetXrState {
                    schema: 1,
                    request_id: request(3),
                    state: XrState::Focused,
                }
            ),
            Status::Accepted
        );
        let diagnostic = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        if sdk_owned {
            // 実adapterと同順序。SDK所有はnative targetがまだない時点で登録する。
            assert!(runtime.snapshot().render.targets.is_empty());
            assert_eq!(
                runtime.command(Command::RegisterSdkBinding {
                    schema: 1,
                    request_id: request(4),
                    target: diagnostic,
                    diagnostic: true,
                }),
                Status::Accepted
            );
            assert_eq!(runtime.snapshot().app_sdk_bindings, 0);
            assert_eq!(runtime.snapshot().shutdown.sdk_bindings, 1);
        }
        assert_eq!(
            runtime.attach_surface(
                diagnostic,
                eidolon_render::PlatformWindow::default(),
                64,
                64
            ),
            Status::Accepted
        );
        wait(&runtime, |r| {
            r.snapshot().render.targets.iter().any(|target| {
                target.key == diagnostic
                    && target.role == eidolon_render::RenderMode::Diagnostic
                    && target.frames > 0
            })
        });
        assert!(runtime.snapshot().app.is_none());
        Self {
            runtime,
            files,
            diagnostic,
            sdk_owned,
        }
    }

    fn connect(&self, profile_id: &str) {
        let id = request(10);
        assert_eq!(
            completed(
                &self.runtime,
                Command::Connect {
                    schema: 1,
                    request_id: id,
                    profile_id: profile_id.into(),
                },
            ),
            Status::Accepted,
            "initial diagnostic panel blocked Connect"
        );
        let snapshot = self.runtime.snapshot();
        // connect_specが同期的に記録する接続先まで検査する。localhostのSSH有無や
        // host鍵確認後のCredentialRequiredは、このadmission回帰検査の成功条件にしない。
        assert_eq!(snapshot.transport.profile_id.as_deref(), Some(profile_id));
        assert!(snapshot.app.is_none());
        assert_eq!(snapshot.window_slots.occupied, 0);
        // 接続開始だけで診断panelのSDK/native所有を消したことにはしない。
        assert_eq!(snapshot.shutdown.sdk_bindings, usize::from(self.sdk_owned));
        assert!(
            snapshot
                .render
                .targets
                .iter()
                .any(|target| target.key == self.diagnostic)
        );
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.runtime.request_stop();
        if self.sdk_owned {
            self.runtime.command(Command::ReleaseSdkBinding {
                schema: 1,
                request_id: request(100),
                target: self.diagnostic,
            });
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        while !self.runtime.can_destroy() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        if self.runtime.can_destroy() {
            let _ = std::fs::remove_dir_all(&self.files);
        }
    }
}
fn request(id: i64) -> CommandSeq {
    CommandSeq::new(id).unwrap()
}
fn wait(runtime: &Runtime, mut predicate: impl FnMut(&Runtime) -> bool) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !predicate(runtime) {
        assert!(Instant::now() < deadline, "{:?}", runtime.snapshot());
        thread::sleep(Duration::from_millis(1));
    }
}
fn completed(runtime: &Runtime, command: Command) -> Status {
    let id = command.request_id();
    assert_eq!(runtime.command(command), Status::Accepted);
    let mut result = None;
    wait(runtime, |runtime| {
        for event in runtime.poll(64).unwrap() {
            if let Event::CommandComplete { request_id, code } = event
                && request_id == id
            {
                result = Some(code);
            }
        }
        result.is_some()
    });
    result.unwrap()
}

#[test]
fn initial_diagnostic_native_target_allows_normal_termux_connect() {
    // 新SDK wire fieldを使わず、元のnative_busy判定だけでも失敗する回帰case。
    Fixture::new("termux-local", false).connect("termux-local");
}

#[test]
fn initial_diagnostic_sdk_owner_allows_normal_termux_connect() {
    Fixture::new("termux-local", true).connect("termux-local");
}

#[test]
fn initial_diagnostic_sdk_owner_allows_normal_linux_connect() {
    Fixture::new("remote-linux", true).connect("remote-linux");
}

#[test]
fn initial_diagnostic_panel_does_not_replace_linux_session_validation_with_busy() {
    let fixture = Fixture::new("remote-linux", true);
    let session = SessionId::new(1).unwrap();
    // 未認証sessionは許可しない。診断panelによるBUSYではなくtransportのSTALEを返す。
    for command in [
        Command::LinuxDesktopInspect {
            schema: 1,
            request_id: request(10),
            session,
        },
        Command::LinuxDesktopRecover {
            schema: 1,
            request_id: request(11),
            session,
        },
        Command::LinuxDesktopLaunch {
            schema: 1,
            request_id: request(12),
            session,
            selection: eidolon_model::linux_desktop::Selection {
                scope: "a".repeat(64),
                revision: "b".repeat(64),
                app_id: eidolon_model::linux_desktop::APP_ID.into(),
            },
        },
    ] {
        assert_eq!(completed(&fixture.runtime, command), Status::Stale);
    }
    assert_eq!(fixture.runtime.snapshot().transport.credential_requests, 0);
    assert!(fixture.runtime.snapshot().app.is_none());
    assert_eq!(fixture.runtime.snapshot().window_slots.occupied, 0);
}
