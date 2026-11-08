//! Production Controller path over encrypted TCP SSH. No private Shared state is
//! injected: credentials, UID, inspection, catalog, mailbox and recovery all use
//! the same public entry points used by native/Host.
use super::{test_support::Temp, tests, *};
use crate::{
    exec,
    session::{Controller, Snapshot, State},
};
use eidolon_model::{commands::AppMode, ids::CommandSeq, state::Status};
use russh::{
    Channel, ChannelId,
    keys::{Algorithm, PrivateKey, PublicKey},
    server::{self, PublicKeyOffer, Server as _, Session, VerifiedPublicKey},
};
use serde_json::{Value, json};
use std::{
    net::{Ipv4Addr, SocketAddr},
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
};
use tokio::{
    net::TcpListener,
    task::JoinHandle,
    time::{Duration, Instant},
};

#[derive(Default)]
struct Evidence {
    probes: AtomicUsize,
    helpers: AtomicUsize,
    recoveries: AtomicUsize,
    forwards: AtomicUsize,
    retired: AtomicUsize,
    commands: Mutex<Vec<String>>,
    active: Mutex<Option<(server::Handle, ChannelId)>>,
    origin: Mutex<Option<Binding>>,
    lose_start: AtomicBool,
    root_uid: AtomicBool,
}
#[derive(Clone)]
struct Peer {
    allowed: PublicKey,
    profile: Profile,
    pins: Pins,
    evidence: Arc<Evidence>,
    binding: Option<Binding>,
    sequence: u64,
    input: Vec<u8>,
}
impl Peer {
    fn frame(&mut self, request: u64, event: Value) -> Vec<u8> {
        self.sequence += 1;
        format!(
            "{}\n",
            tests::row(
                self.binding.as_ref().unwrap(),
                self.sequence,
                request,
                event
            )
        )
        .into_bytes()
    }
    fn finish(&self, id: ChannelId, session: &mut Session) -> Result<(), russh::Error> {
        session.eof(id)?;
        session.exit_status_request(id, 0)?;
        session.close(id)
    }
}
impl server::Server for Peer {
    type Handler = Self;
    fn new_client(&mut self, _: Option<SocketAddr>) -> Self {
        self.clone()
    }
    fn handle_session_error(&mut self, _: russh::Error) {}
}
impl server::Handler for Peer {
    type Error = russh::Error;
    async fn auth_publickey_offered(
        &mut self,
        user: &str,
        key: PublicKeyOffer<'_>,
    ) -> Result<server::Auth, Self::Error> {
        Ok(
            if user == "fixture" && key.public_key.key_data() == self.allowed.key_data() {
                server::Auth::Accept
            } else {
                server::Auth::reject()
            },
        )
    }
    async fn auth_publickey(
        &mut self,
        user: &str,
        key: VerifiedPublicKey<'_>,
    ) -> Result<server::Auth, Self::Error> {
        Ok(
            if user == "fixture" && key.public_key.key_data() == self.allowed.key_data() {
                server::Auth::Accept
            } else {
                server::Auth::reject()
            },
        )
    }
    async fn channel_open_session(
        &mut self,
        _: Channel<server::Msg>,
        reply: server::ChannelOpenHandle,
        _: &mut Session,
    ) -> Result<(), Self::Error> {
        reply.accept().await;
        Ok(())
    }
    async fn exec_request(
        &mut self,
        id: ChannelId,
        data: &[u8],
        session: &mut Session,
    ) -> Result<(), Self::Error> {
        if data == exec::COMMAND.as_bytes() {
            self.evidence.probes.fetch_add(1, Ordering::AcqRel);
            session.channel_success(id)?;
            let uid = if self.evidence.root_uid.load(Ordering::Acquire) {
                0
            } else {
                1000
            };
            session.data(id, format!("{uid}\n{}\n", exec::TOKEN).into_bytes())?;
            return self.finish(id, session);
        }
        let command = std::str::from_utf8(data).unwrap();
        let python = command
            .strip_prefix("/usr/bin/python3 -I -S -c '")
            .unwrap()
            .strip_suffix('\'')
            .unwrap()
            .replace("'\\''", "'");
        let tail = python
            .split("raise SystemExit(modules['wire'].main(")
            .nth(1)
            .unwrap();
        let args: Vec<String> =
            serde_json::from_str(tail.split(",base=base,").next().unwrap()).unwrap();
        let binding = Binding::capture(
            &self.profile,
            SessionId::new(args[1].parse().unwrap()).unwrap(),
            args[5].parse().unwrap(),
            &args[4],
            self.pins.clone(),
        )
        .unwrap();
        let recover = args[0] == "recover";
        assert_eq!(
            command,
            owner::command(&binding, recover.then_some(tests::BOOT_ID)).unwrap()
        );
        session.channel_success(id)?;
        if recover {
            self.evidence.recoveries.fetch_add(1, Ordering::AcqRel);
            let origin = self.evidence.origin.lock().unwrap().clone().unwrap();
            assert_eq!(binding.session(), origin.session());
            assert_eq!(binding.nonce(), origin.nonce());
            let receipt = json!({"schema":1,"backend":"linux_desktop","revision":REVISION,
                "session":binding.session,"profile_revision":binding.profile_revision,"scope":binding.scope,
                "nonce":binding.nonce,"uid":binding.uid,"expected_boot_id":tests::BOOT_ID,
                "origin_boot_id":tests::BOOT_ID,"boot_id":tests::BOOT_ID,"status":"complete",
                "process_evidence":"receipt","process_complete":true,"pipes_eof":true,
                "remote_cleaned":true,"receipt_written":true});
            session.data(id, format!("{receipt}\n").into_bytes())?;
            return self.finish(id, session);
        }
        assert_eq!(args[0], "serve");
        self.evidence.helpers.fetch_add(1, Ordering::AcqRel);
        *self.evidence.active.lock().unwrap() = Some((session.handle(), id));
        *self.evidence.origin.lock().unwrap() = Some(binding.clone());
        let prepared = tests::prepared_event(&binding);
        self.binding = Some(binding);
        self.sequence = 0;
        session.data(id, self.frame(0, prepared))
    }
    async fn data(
        &mut self,
        id: ChannelId,
        bytes: &[u8],
        session: &mut Session,
    ) -> Result<(), Self::Error> {
        self.input.extend_from_slice(bytes);
        while let Some(end) = self.input.iter().position(|byte| *byte == b'\n') {
            let line: Vec<_> = self.input.drain(..=end).collect();
            let value: Value = serde_json::from_slice(&line).unwrap();
            let request: u64 = value["sequence"].as_str().unwrap().parse().unwrap();
            let action = value["command"]["type"].as_str().unwrap();
            self.evidence.commands.lock().unwrap().push(action.into());
            match action {
                "inspect" => {
                    let event = tests::catalog(self.binding.as_ref().unwrap());
                    session.data(id, self.frame(request, event))?;
                }
                "heartbeat" => session.data(
                    id,
                    self.frame(
                        request,
                        tests::event("heartbeat", json!({"phase":"catalog"})),
                    ),
                )?,
                "stop" => {
                    session.data(id, self.frame(request, tests::stopped("stop")))?;
                    return self.finish(id, session);
                }
                "start" => {
                    if self.evidence.lose_start.load(Ordering::Acquire) {
                        return session.close(id);
                    }
                    let mut bytes = self.frame(
                        request,
                        tests::event("started", json!({"app_id":APP_ID,"mode":"gui"})),
                    );
                    bytes.extend(self.frame(request, tests::stopped("process_exited")));
                    session.data(id, bytes)?;
                    return self.finish(id, session);
                }
                _ => panic!("unexpected command"),
            }
        }
        Ok(())
    }
    async fn streamlocal_forward(
        &mut self,
        path: &str,
        _: &mut Session,
    ) -> Result<bool, Self::Error> {
        let nonce = self.binding.as_ref().unwrap().nonce();
        assert_eq!(path, format!("/home/fixture/.eidolon-desktop/s-{nonce}/p"));
        self.evidence.forwards.fetch_add(1, Ordering::AcqRel);
        Ok(true)
    }
    async fn cancel_streamlocal_forward(
        &mut self,
        _: &str,
        _: &mut Session,
    ) -> Result<bool, Self::Error> {
        self.evidence.retired.fetch_add(1, Ordering::AcqRel);
        Ok(true)
    }
}
struct Fixture {
    profile: Profile,
    pins: Pins,
    identity: PrivateKey,
    evidence: Arc<Evidence>,
    task: JoinHandle<()>,
}
impl Fixture {
    async fn new() -> Self {
        let host = PrivateKey::random(&mut rand::rng(), Algorithm::Ed25519).unwrap();
        let identity = PrivateKey::random(&mut rand::rng(), Algorithm::Ed25519).unwrap();
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let profile = Profile::parse(
            &json!({"schema":2,"installation":"a".repeat(32),"profile_id":"remote-linux",
            "revision":"9007199254740993","display_name":"Linux fixture","backend":"linux_ssh",
            "host":address.ip().to_string(),"port":address.port(),"username":"fixture",
            "host_key":host.public_key().to_openssh().unwrap()})
            .to_string(),
            &"a".repeat(32),
        )
        .unwrap();
        let pins = Pins::parse(&tests::pins_json().to_string()).unwrap();
        let evidence = Arc::new(Evidence::default());
        let mut server = Peer {
            allowed: identity.public_key().clone(),
            profile: profile.clone(),
            pins: pins.clone(),
            evidence: evidence.clone(),
            binding: None,
            sequence: 0,
            input: Vec::new(),
        };
        let config = Arc::new(server::Config {
            keys: vec![host],
            auth_rejection_time: Duration::ZERO,
            auth_rejection_time_initial: Some(Duration::ZERO),
            ..Default::default()
        });
        let task = tokio::spawn(async move {
            let _ = server.run_on_socket(config, &listener).await;
        });
        Self {
            profile,
            pins,
            identity,
            evidence,
            task,
        }
    }
    async fn connect(
        &self,
        controller: &Controller,
        files: &Path,
        request: i64,
        pending: bool,
    ) -> SessionId {
        assert_eq!(
            controller.connect_linux(self.profile.clone(), self.pins.clone(), files, seq(request)),
            Status::Accepted
        );
        wait(controller, |r| r.state == State::AwaitingCredential).await;
        let scope = controller.credential_scope().unwrap();
        assert_eq!(scope.revision.as_deref(), Some("9007199254740993"));
        assert_eq!(
            controller.provide(scope.session, scope.request, self.identity.clone()),
            Status::Stale
        );
        assert_eq!(
            controller.provide_for(
                scope.session,
                scope.request,
                &"f".repeat(64),
                self.identity.clone()
            ),
            Status::Stale
        );
        assert_eq!(
            controller.provide_for(
                scope.session,
                scope.request,
                &scope.binding,
                self.identity.clone()
            ),
            Status::Accepted
        );
        wait(controller, |r| {
            r.state
                == if pending {
                    State::CleanupPending
                } else {
                    State::ProbeReady
                }
        })
        .await;
        assert_eq!(controller.snapshot().probe.uid, Some(1000));
        scope.session
    }
    async fn inspect(
        &self,
        controller: &Controller,
        session: SessionId,
        request: i64,
    ) -> Selection {
        assert_eq!(
            controller.inspect_linux_desktop(session, seq(request)),
            Status::Accepted
        );
        wait(controller, |r| r.state == State::DesktopReady).await;
        let ui = controller.catalog_ui();
        assert_eq!(ui.request_id, Some(seq(request)));
        assert!(!ui.pending && ui.reply.is_none() && ui.error.is_none());
        let desktop = ui.desktop.unwrap();
        assert_eq!(
            (
                desktop.namespace,
                desktop.label,
                desktop.mode,
                desktop.instance_limit
            ),
            ("linux-desktop", "Linux アプリ", "gui", 1)
        );
        desktop.selection
    }
    async fn lose_helper(&self, controller: &Controller) {
        let (handle, channel) = self.evidence.active.lock().unwrap().clone().unwrap();
        handle.close(channel).await.unwrap();
        wait(controller, |r| {
            r.state == State::CleanupPending && r.owned_connections == 0
        })
        .await;
        let report = controller.snapshot();
        assert!(report.desktop_pending && report.desktop.unwrap().finished);
        assert_eq!(report.tokio_threads, 0);
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.task.abort();
    }
}
fn seq(n: i64) -> CommandSeq {
    CommandSeq::new(n).unwrap()
}
async fn wait(controller: &Controller, condition: impl Fn(&Snapshot) -> bool) {
    let deadline = Instant::now() + Duration::from_secs(8);
    while !condition(&controller.snapshot()) {
        assert!(Instant::now() < deadline, "{:?}", controller.snapshot());
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
}
async fn stop(controller: &Controller) {
    controller.request_stop();
    wait(controller, |r| {
        r.workers == 0 && r.tokio_threads == 0 && r.owned_connections == 0
    })
    .await;
}
fn legacy(fixture: &Fixture) -> crate::host_key::Profile {
    crate::host_key::Profile {
        schema: 1,
        profile_id: "termux-local".into(),
        installation: "a".repeat(32),
        username: "fixture".into(),
        host_key: fixture.profile.host_key().into(),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn controller_normal_root_uid_is_rejected_before_inspect_or_journal_creation() {
    let root = Temp::new();
    let fixture = Fixture::new().await;
    fixture.evidence.root_uid.store(true, Ordering::Release);
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    assert_eq!(
        controller.connect_linux(
            fixture.profile.clone(),
            fixture.pins.clone(),
            &root.0,
            seq(1)
        ),
        Status::Accepted
    );
    wait(&controller, |r| r.state == State::AwaitingCredential).await;
    let scope = controller.credential_scope().unwrap();
    assert_eq!(
        controller.provide_for(
            scope.session,
            scope.request,
            &scope.binding,
            fixture.identity.clone()
        ),
        Status::Accepted
    );
    wait(&controller, |r| {
        r.state == State::Failed && r.owned_connections == 0
    })
    .await;
    assert_eq!(controller.snapshot().probe.uid, Some(0));
    assert_eq!(controller.snapshot().error, Some("DESKTOP_BINDING_INVALID"));
    assert_eq!(
        controller.inspect_linux_desktop(scope.session, seq(2)),
        Status::NotReady
    );
    assert_eq!(fixture.evidence.helpers.load(Ordering::Acquire), 0);
    assert!(!root.0.join("linux-desktop-state").exists());
    assert!(!controller.snapshot().desktop_pending);
    stop(&controller).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn controller_connect_inspect_idle_heartbeat_stop_and_fresh_catalog_are_explicit() {
    let root = Temp::new();
    let fixture = Fixture::new().await;
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    let session = fixture.connect(&controller, &root.0, 1, false).await;
    assert_eq!(fixture.evidence.helpers.load(Ordering::Acquire), 0);
    assert!(!controller.snapshot().desktop_pending);
    assert!(controller.catalog_ui().desktop.is_none());
    assert_eq!(
        controller.start_app(
            session,
            Path::new("/fixture/p"),
            "aabbccddeeff0011",
            AppMode::LinuxDesktop
        ),
        Status::NotReady
    );
    let selection = fixture.inspect(&controller, session, 2).await;
    let diagnostics = serde_json::to_string(&controller.snapshot()).unwrap();
    assert!(!diagnostics.contains(&fixture.profile.binding()));
    assert!(!diagnostics.contains(fixture.profile.host_key()));
    assert!(!diagnostics.contains(&fixture.pins.desktop_binary_sha256));
    let mut stale = selection.clone();
    stale.revision = "f".repeat(64);
    assert_eq!(
        controller.desktop_launch_nonce(session, &stale),
        Err(Status::Stale)
    );
    assert_eq!(
        controller.desktop_launch_nonce(SessionId::new(session.get() + 1).unwrap(), &selection),
        Err(Status::Stale)
    );
    assert_eq!(
        controller
            .desktop_launch_nonce(session, &selection)
            .unwrap(),
        controller.snapshot().desktop.unwrap().nonce
    );
    assert_eq!(
        controller.recover_linux_desktop(session, seq(3)),
        Status::NotReady
    );
    assert_eq!(controller.connect(legacy(&fixture), seq(4)), Status::Busy);
    // Waiting in the launcher renews only the lease; it does not Start the GUI.
    let deadline = Instant::now() + Duration::from_secs(7);
    while !fixture
        .evidence
        .commands
        .lock()
        .unwrap()
        .iter()
        .any(|c| c == "heartbeat")
    {
        assert!(Instant::now() < deadline);
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(controller.snapshot().state, State::DesktopReady);
    assert!(
        !fixture
            .evidence
            .commands
            .lock()
            .unwrap()
            .iter()
            .any(|c| c == "start")
    );
    assert_eq!(controller.disconnect(session), Status::Accepted);
    wait(&controller, |r| r.state == State::Disconnected).await;
    let report = controller.snapshot();
    let desktop = report.desktop.unwrap();
    assert!(!report.desktop_pending && desktop.finished);
    assert_eq!(desktop.error, None);
    assert!(!root.0.join("linux-desktop-state/pending.json").exists());
    let next = fixture.connect(&controller, &root.0, 5, false).await;
    assert!(next > session);
    assert_eq!(fixture.evidence.helpers.load(Ordering::Acquire), 1);
    let fresh = fixture.inspect(&controller, next, 6).await;
    assert_ne!(fresh, selection);
    assert_eq!(
        controller.start_linux_desktop(next, seq(7), Path::new("/fixture/p"), selection),
        Status::Stale
    );
    assert_eq!(controller.disconnect(next), Status::Accepted);
    wait(&controller, |r| r.state == State::Disconnected).await;
    stop(&controller).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn controller_unknown_survives_restart_blocks_switch_and_requires_explicit_recovery() {
    let root = Temp::new();
    let fixture = Fixture::new().await;
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    let session = fixture.connect(&controller, &root.0, 1, false).await;
    fixture.inspect(&controller, session, 2).await;
    fixture.lose_helper(&controller).await;
    let path = root.0.join("linux-desktop-state/pending.json");
    let retained = std::fs::read(&path).unwrap();
    assert_eq!(
        controller.connect(legacy(&fixture), seq(3)),
        Status::NotReady
    );
    let mut changed = serde_json::to_value(&fixture.profile).unwrap();
    changed["revision"] = json!("9007199254740994");
    let changed = Profile::parse(&changed.to_string(), &"a".repeat(32)).unwrap();
    assert_eq!(
        controller.connect_linux(changed, fixture.pins.clone(), &root.0, seq(4)),
        Status::NotReady
    );
    let mut pins = tests::pins_json();
    pins["desktop_binary_sha256"] = json!("f".repeat(64));
    assert_eq!(
        controller.connect_linux(
            fixture.profile.clone(),
            Pins::parse(&pins.to_string()).unwrap(),
            &root.0,
            seq(4)
        ),
        Status::NotReady
    );
    assert_eq!(std::fs::read(&path).unwrap(), retained);
    stop(&controller).await;
    drop(controller);
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    assert!(controller.snapshot().desktop_pending);
    assert_eq!(
        controller.connect(legacy(&fixture), seq(5)),
        Status::NotReady
    );
    let next = fixture.connect(&controller, &root.0, 6, true).await;
    assert!(next > session);
    assert_eq!(fixture.evidence.helpers.load(Ordering::Acquire), 1);
    assert_eq!(fixture.evidence.recoveries.load(Ordering::Acquire), 0);
    assert_eq!(
        controller.inspect_linux_desktop(next, seq(7)),
        Status::NotReady
    );
    assert_eq!(
        controller.recover_linux_desktop(next, seq(8)),
        Status::Accepted
    );
    wait(&controller, |r| {
        r.state == State::ProbeReady && !r.desktop_pending
    })
    .await;
    assert_eq!(
        controller
            .snapshot()
            .desktop_recovery
            .unwrap()
            .origin_session,
        Some(session)
    );
    assert!(!path.exists());
    assert_eq!(fixture.evidence.helpers.load(Ordering::Acquire), 1);
    assert_eq!(fixture.evidence.recoveries.load(Ordering::Acquire), 1);
    fixture.inspect(&controller, next, 9).await;
    assert_eq!(controller.disconnect(next), Status::Accepted);
    wait(&controller, |r| r.state == State::Disconnected).await;
    stop(&controller).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn controller_missing_known_pending_record_is_not_recovery_evidence() {
    let root = Temp::new();
    let fixture = Fixture::new().await;
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    let session = fixture.connect(&controller, &root.0, 1, false).await;
    fixture.inspect(&controller, session, 2).await;
    fixture.lose_helper(&controller).await;
    let next = fixture.connect(&controller, &root.0, 3, true).await;
    std::fs::remove_file(root.0.join("linux-desktop-state/pending.json")).unwrap();
    assert_eq!(
        controller.recover_linux_desktop(next, seq(4)),
        Status::Accepted
    );
    wait(&controller, |r| {
        r.state == State::CleanupPending && r.desktop_recovery.is_some()
    })
    .await;
    assert!(controller.snapshot().desktop_pending);
    assert_eq!(
        controller.snapshot().error,
        Some("DESKTOP_JOURNAL_OWNER_CHANGED")
    );
    assert_eq!(fixture.evidence.recoveries.load(Ordering::Acquire), 0);
    stop(&controller).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn controller_invalid_local_preparation_never_starts_and_retires_the_inspected_owner() {
    let root = Temp::new();
    let fixture = Fixture::new().await;
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    let session = fixture.connect(&controller, &root.0, 1, false).await;
    let selection = fixture.inspect(&controller, session, 2).await;
    assert_eq!(
        controller.start_linux_desktop(session, seq(3), &root.0.join("missing"), selection),
        Status::Accepted
    );
    wait(&controller, |r| {
        r.state == State::Failed && r.owned_connections == 0
    })
    .await;
    let snapshot = controller.snapshot();
    let report = snapshot.desktop.unwrap();
    assert_eq!(report.request_id, Some(seq(3)));
    assert_eq!(report.error, Some("DESKTOP_LOCAL_SOCKET_INVALID"));
    assert!(report.finished && report.channel_complete && report.router_drained);
    assert!(!report.started && !report.start_requested && !snapshot.desktop_pending);
    assert_eq!(
        fixture.evidence.commands.lock().unwrap().as_slice(),
        ["inspect", "stop"]
    );
    assert_eq!(fixture.evidence.forwards.load(Ordering::Acquire), 0);
    stop(&controller).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn controller_unix_explicit_launch_once_keeps_coalesced_started_and_retires_every_owner() {
    let root = Temp::new();
    let local = root.0.join("p");
    let _socket = tokio::net::UnixListener::bind(&local).unwrap();
    let fixture = Fixture::new().await;
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    let session = fixture.connect(&controller, &root.0, 1, false).await;
    let selection = fixture.inspect(&controller, session, 2).await;
    assert_eq!(
        controller.start_linux_desktop(session, seq(3), &local, selection.clone()),
        Status::Accepted
    );
    assert!(matches!(
        controller.start_linux_desktop(session, seq(4), &local, selection),
        Status::Busy | Status::NotReady
    ));
    wait(&controller, |r| r.state == State::Disconnected).await;
    let snapshot = controller.snapshot();
    let report = snapshot.desktop.unwrap();
    assert_eq!(report.request_id, Some(seq(3)));
    assert!(report.finished && report.started && report.remote.started && report.start_requested);
    assert!(
        report.forward_registered
            && report.forward_cancelled
            && report.router_drained
            && report.channel_complete
    );
    assert!(!report.journal_pending && !snapshot.desktop_pending);
    assert_eq!(
        fixture.evidence.commands.lock().unwrap().as_slice(),
        ["inspect", "start"]
    );
    assert_eq!(fixture.evidence.forwards.load(Ordering::Acquire), 1);
    assert_eq!(fixture.evidence.retired.load(Ordering::Acquire), 1);
    assert_eq!(snapshot.tokio_threads, 0);
    stop(&controller).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn controller_unix_lost_start_never_replays_and_retains_unknown() {
    let root = Temp::new();
    let local = root.0.join("p");
    let _socket = tokio::net::UnixListener::bind(&local).unwrap();
    let fixture = Fixture::new().await;
    fixture.evidence.lose_start.store(true, Ordering::Release);
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    let session = fixture.connect(&controller, &root.0, 1, false).await;
    let selection = fixture.inspect(&controller, session, 2).await;
    assert_eq!(
        controller.start_linux_desktop(session, seq(3), &local, selection.clone()),
        Status::Accepted
    );
    wait(&controller, |r| {
        r.state == State::CleanupPending && r.owned_connections == 0
    })
    .await;
    let report = controller.snapshot().desktop.unwrap();
    assert!(report.finished && report.start_requested && !report.started && report.journal_pending);
    assert!(report.forward_cancelled && report.router_drained);
    assert_eq!(
        controller.start_linux_desktop(session, seq(4), &local, selection),
        Status::NotReady
    );
    assert_eq!(
        fixture.evidence.commands.lock().unwrap().as_slice(),
        ["inspect", "start"]
    );
    stop(&controller).await;
}
