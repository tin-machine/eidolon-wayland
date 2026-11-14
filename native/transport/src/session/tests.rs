use super::*;
use russh::{
    Channel, ChannelId,
    server::{self, PublicKeyOffer, Server as _, Session, VerifiedPublicKey},
};
use tokio::net::TcpListener;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cold_journal_and_live_capture_have_distinct_recovery_admission() {
    use crate::linux_desktop::v3::{
        Binding, Context, ExpectedEvidence, Manifest, Pins,
        journal::Journal,
        runtime::{LaunchAdmission, RunCapture},
        sources,
    };
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
    ))
    .unwrap();
    let raw = |id| {
        fixture["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|record| record["id"] == id)
            .unwrap()["raw"]
            .as_str()
            .unwrap()
            .as_bytes()
    };
    let binding = Binding::from_captured(
        Context::parse(raw("context")).unwrap(),
        Pins::parse(raw("pins")).unwrap(),
    )
    .unwrap();
    let evidence = ExpectedEvidence::capture(
        binding.pins(),
        &Manifest::parse(raw("manifest")).unwrap(),
        &sources::current_hashes(),
    )
    .unwrap();
    let root = JournalRoot::new();
    let journal = Journal::reserve(&root.0, &binding).unwrap();
    drop(journal);
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    wait(&controller, |snapshot| snapshot.journal_ready).await;
    let (_, cold) = controller.reclamation_snapshot();
    assert!(cold.remote_cleanup_pending);
    assert!(!cold.no_local_allocation_proven);
    assert!(!cold.recovery_connect_allowed && !cold.recovery_command_allowed);

    // This gate is held in-process before any local allocation. An on-disk
    // Complete or a new Runtime's empty counters cannot construct its proof.
    let (sender, _receiver) = tokio::sync::mpsc::channel(1);
    let admission = LaunchAdmission::new(
        RunCapture::new(binding, evidence, request(7), 7).unwrap(),
        sender,
        CancellationToken::new(),
    );
    controller.signals.update(|shared| {
        shared.desktop_v3_capture = Some(admission.capture());
        shared.desktop_v3_no_local_allocation = admission.seal_without_local_allocation();
    });
    let (_, before_io_retirement) = controller.reclamation_snapshot();
    assert!(before_io_retirement.no_local_allocation_proven);
    assert!(!before_io_retirement.recovery_connect_allowed);
    controller
        .signals
        .update(|shared| shared.desktop_v3_io_retired = true);
    let (_, live) = controller.reclamation_snapshot();
    assert!(live.recovery_connect_allowed);
    assert!(
        !live.recovery_command_allowed,
        "a new authenticated connection is still needed"
    );
    controller.request_stop();
    assert!(!controller.reclamation_snapshot().1.recovery_connect_allowed);
    stop(&controller).await;
    let (stopped, retained) = controller.reclamation_snapshot();
    assert_eq!(stopped.workers, 0);
    assert!(
        retained.remote_cleanup_pending,
        "stopping a Controller cannot clear a journal"
    );
    assert!(
        crate::linux_desktop::v3::journal::pending(&root.0)
            .unwrap()
            .is_some()
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn finalized_receipt_survives_a_transient_publication_read_failure() {
    use crate::linux_desktop::v3::{
        Binding, Context, Pins,
        journal::{Cleanup, Journal},
        reclamation::{DeferredReclamation, finalize_reclaimed},
    };
    use eidolon_model::linux_desktop_v3::report::LocalRetirement;
    use std::os::unix::fs::PermissionsExt;
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
    ))
    .unwrap();
    let raw = |id| {
        fixture["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|r| r["id"] == id)
            .unwrap()["raw"]
            .as_str()
            .unwrap()
            .as_bytes()
    };
    let binding = Binding::from_captured(
        Context::parse(raw("context")).unwrap(),
        Pins::parse(raw("pins")).unwrap(),
    )
    .unwrap();
    let root = JournalRoot::new();
    let mut journal = Journal::reserve(&root.0, &binding).unwrap();
    journal
        .prepared("12345678-1234-4234-8234-123456789abc")
        .unwrap();
    journal
        .finish(Cleanup {
            process_complete: true,
            pipes_eof: true,
            remote_cleaned: true,
            receipt_written: true,
            channel_complete: true,
            forward_retired: true,
            router_drained: true,
            boot_changed: false,
        })
        .unwrap();
    let mut handoff = DeferredReclamation::from_complete(journal, &binding, 7).unwrap();
    struct Gate(Context);
    impl crate::linux_desktop::v3::reclamation::LocalRetirementGate for Gate {
        fn verify(
            &self,
            context: &Context,
            generation: u64,
        ) -> Result<LocalRetirement, &'static str> {
            if context != &self.0 || generation != 7 {
                return Err("DESKTOP_V3_CLEANUP_SCOPE_MISMATCH");
            }
            Ok(LocalRetirement {
                waypipe_stopped: true,
                listener_retired: true,
                windows_retired: true,
                sdk_bindings_released: true,
                sdk_entities_disposed: true,
            })
        }
    }
    let local = handoff
        .authority
        .confirm(&Gate(binding.context().clone()))
        .unwrap();
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    controller.signals.update(|s| {
        s.report.session = Some(SessionId::new(7).unwrap());
        s.desktop_v3_io_retired = true;
        s.desktop_v3_finalization_pending = true;
    });
    let receipt = finalize_reclaimed(&mut handoff.deferred, &local).unwrap();
    let directory = root.0.join("linux-desktop-v3-state");
    std::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o755)).unwrap();
    assert_eq!(controller.publish_v3_finalized(&receipt), Status::NotReady);
    assert!(controller.reclamation_snapshot().1.remote_cleanup_pending);
    assert!(
        controller
            .signals
            .shared
            .lock()
            .unwrap()
            .desktop_v3_finalization_pending
    );
    std::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o700)).unwrap();
    assert_eq!(controller.publish_v3_finalized(&receipt), Status::Accepted);
    assert!(
        !controller
            .signals
            .shared
            .lock()
            .unwrap()
            .desktop_v3_finalization_pending
    );
    assert!(
        crate::linux_desktop::v3::journal::pending(&root.0)
            .unwrap()
            .is_none()
    );
    stop(&controller).await;
    assert!(!controller.reclamation_snapshot().1.remote_cleanup_pending);
    assert_eq!(controller.reclamation_snapshot().1.pending_operations, 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn gtk_mailbox_captures_case_without_upgrading_probe_ready_or_normal_routes() {
    use sha2::{Digest, Sha256};
    let controller = Controller::start().unwrap();
    let session = SessionId::new(7).unwrap();
    let source: String = Sha256::digest(include_bytes!(
        "../../../../scripts/fixtures/gtk3-interaction-fixture.c"
    ))
    .iter()
    .map(|byte| format!("{byte:02x}"))
    .collect();
    let pins=crate::linux_probe::GtkPins::parse(&serde_json::json!({"schema":1,"base":{"schema":1,"bundle_sha256":"b".repeat(64),"waypipe_sha256":"c".repeat(64),"fixture_sha256":"d".repeat(64)},
        "gtk_bundle_sha256":"e".repeat(64),"source_sha256":source,"binary_sha256":"f".repeat(64),"gtk_version":"3.24.52"}).to_string()).unwrap();
    controller.signals.update(|s| {
        s.report.state = State::Ready;
        s.report.session = Some(session);
        s.cancel = Some(CancellationToken::new());
    });
    assert_eq!(
        controller.linux_gtk_probe(
            session,
            Path::new("/fixture"),
            None,
            "1234567890abcdef",
            pins.binding(crate::linux_probe::GtkCase::Input)
        ),
        Status::NotReady
    );
    assert_eq!(
        controller.start_app(
            session,
            Path::new("/fixture/p"),
            "1234567890abcdef",
            AppMode::LinuxGtkInput
        ),
        Status::NotReady
    );
    assert_eq!(
        controller.start_app(
            session,
            Path::new("/fixture/p"),
            "1234567890abcdef",
            AppMode::LinuxDesktop
        ),
        Status::NotReady
    );
    controller
        .signals
        .update(|s| s.report.state = State::ProbeReady);
    assert_eq!(
        controller.linux_gtk_probe(
            SessionId::new(8).unwrap(),
            Path::new("/fixture"),
            None,
            "1234567890abcdef",
            pins.binding(crate::linux_probe::GtkCase::Input)
        ),
        Status::Stale
    );
    assert_eq!(
        controller.linux_gtk_probe(
            session,
            Path::new("/fixture"),
            Some(Path::new("/fixture/p")),
            "1234567890abcdef",
            pins.binding(crate::linux_probe::GtkCase::Dialog)
        ),
        Status::Accepted
    );
    assert_eq!(
        controller.linux_gtk_probe(
            session,
            Path::new("/fixture"),
            None,
            "1234567890abcdef",
            pins.binding(crate::linux_probe::GtkCase::Input)
        ),
        Status::Busy
    );
    assert_eq!(controller.snapshot().state, State::ProbeReady);
    let queued = controller.snapshot().linux_forward.unwrap();
    let gtk = queued.gtk.unwrap();
    assert_eq!(gtk.case, crate::linux_probe::GtkCase::Dialog);
    assert_eq!(gtk.reports, 0);
    assert!(!gtk.process_bound && !gtk.helper_verified);
    assert_eq!(
        controller.open_terminal(session, request(1)),
        Status::NotReady
    );
    {
        let shared = controller.signals.shared.lock().unwrap();
        assert_eq!(
            shared.linux.as_ref().unwrap().gtk.as_ref().unwrap().case,
            crate::linux_probe::GtkCase::Dialog
        );
    }
    assert!(
        !serde_json::to_string(&controller.snapshot())
            .unwrap()
            .contains(&"f".repeat(64))
    );
    stop(&controller).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn linux_mailbox_requires_probe_ready_and_never_enables_normal_app_routes() {
    let controller = Controller::start().unwrap();
    let session = SessionId::new(7).unwrap();
    let pins=crate::linux_probe::Pins::parse(&serde_json::json!({"schema":1,"bundle_sha256":"b".repeat(64),"waypipe_sha256":"c".repeat(64),"fixture_sha256":"d".repeat(64)}).to_string()).unwrap();
    controller.signals.update(|s| {
        s.report.state = State::Ready;
        s.report.session = Some(session);
        s.cancel = Some(CancellationToken::new());
    });
    assert_eq!(
        controller.linux_probe(
            session,
            Path::new("/fixture"),
            None,
            "1234567890abcdef",
            pins.clone()
        ),
        Status::NotReady
    );
    controller
        .signals
        .update(|s| s.report.state = State::ProbeReady);
    assert_eq!(
        controller.start_app(
            session,
            Path::new("/fixture/p"),
            "1234567890abcdef",
            AppMode::Foot
        ),
        Status::NotReady
    );
    assert_eq!(
        controller.open_terminal(session, request(10)),
        Status::NotReady
    );
    assert_eq!(
        controller.linux_probe(
            SessionId::new(8).unwrap(),
            Path::new("/fixture"),
            None,
            "1234567890abcdef",
            pins.clone()
        ),
        Status::Stale
    );
    assert_eq!(
        controller.linux_probe(
            session,
            Path::new("/fixture"),
            Some(Path::new("/fixture/p")),
            "1234567890abcdef",
            pins.clone()
        ),
        Status::Accepted
    );
    assert_eq!(
        controller.linux_probe(
            session,
            Path::new("/fixture"),
            None,
            "1234567890abcdef",
            pins
        ),
        Status::Busy
    );
    let snapshot = controller.snapshot();
    assert_eq!(snapshot.state, State::ProbeReady);
    assert_eq!(snapshot.linux_forward.unwrap().state, "queued");
    assert!(
        !serde_json::to_string(&controller.snapshot())
            .unwrap()
            .contains(&"b".repeat(64))
    );
    stop(&controller).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn catalog_mailbox_is_bounded_scoped_and_separate_from_diagnostics() {
    let controller = Controller::start().unwrap();
    let (sender, mut receiver) = tokio::sync::mpsc::channel(1);
    let session = SessionId::new(7).unwrap();
    controller.signals.update(|s| {
        s.report.state = State::Ready;
        s.report.session = Some(session);
        s.cancel = Some(CancellationToken::new());
        s.catalog_requests = Some(sender);
        s.report.app_forward = Some(forward_app::Report {
            state: "running",
            ..Default::default()
        });
    });
    let op = crate::catalog::Operation::Reload {
        path: "private-file.toml".into(),
    };
    assert_eq!(
        controller.catalog(SessionId::new(6).unwrap(), request(1), op.clone()),
        Status::Stale
    );
    assert!(receiver.try_recv().is_err());
    assert_eq!(
        controller.catalog(session, request(2), op.clone()),
        Status::Accepted
    );
    assert_eq!(controller.catalog(session, request(3), op), Status::Busy);
    let received = receiver.try_recv().unwrap();
    assert_eq!(received.0, request(2));
    assert!(controller.catalog_ui().pending);
    assert!(
        !serde_json::to_string(&controller.snapshot())
            .unwrap()
            .contains("private-file")
    );
    controller
        .signals
        .update(|s| s.cancel.as_ref().unwrap().cancel());
    assert!(controller.catalog_ui().session.is_none());
    assert!(receiver.try_recv().is_err());
    stop(&controller).await;
}

#[derive(Clone, Copy)]
enum Mode {
    Quiet,
    Flood,
    Hang,
    Exit,
    Signal,
}
#[derive(Clone)]
struct Fixture {
    allowed: PublicKey,
    mode: Mode,
    passwords: Arc<AtomicUsize>,
    execs: Arc<AtomicUsize>,
    handle: Arc<Mutex<Option<server::Handle>>>,
    audit: Arc<Mutex<Option<serde_json::Value>>>,
    audits: Arc<AtomicUsize>,
    non_probe_execs: Arc<AtomicUsize>,
}
impl server::Server for Fixture {
    type Handler = Self;
    fn new_client(&mut self, _: Option<SocketAddr>) -> Self {
        self.clone()
    }
    fn handle_session_error(&mut self, _: russh::Error) {}
}
impl server::Handler for Fixture {
    type Error = russh::Error;
    async fn auth_password(&mut self, _: &str, _: &str) -> Result<server::Auth, Self::Error> {
        self.passwords.fetch_add(1, Ordering::AcqRel);
        Ok(server::Auth::reject())
    }
    async fn auth_publickey_offered(
        &mut self,
        user: &str,
        offer: PublicKeyOffer<'_>,
    ) -> Result<server::Auth, Self::Error> {
        Ok(
            if user == "fixture" && offer.public_key.key_data() == self.allowed.key_data() {
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
        if data != exec::COMMAND.as_bytes() {
            self.non_probe_execs.fetch_add(1, Ordering::AcqRel);
            let value = self.audit.lock().unwrap().clone();
            if data
                == launcher::command("cleanup", "ffffffffffffffff")
                    .unwrap()
                    .as_bytes()
                && let Some(value) = value
            {
                self.audits.fetch_add(1, Ordering::AcqRel);
                session.channel_success(id)?;
                let handle = session.handle();
                tokio::spawn(async move {
                    let _ = handle.data(id, format!("{value}\n").into_bytes()).await;
                    let _ = handle.eof(id).await;
                    let _ = handle.exit_status_request(id, 0).await;
                    let _ = handle.close(id).await;
                });
                return Ok(());
            }
            session.channel_failure(id)?;
            return Ok(());
        }
        self.execs.fetch_add(1, Ordering::AcqRel);
        *self.handle.lock().unwrap() = Some(session.handle());
        session.channel_success(id)?;
        let (handle, mode) = (session.handle(), self.mode);
        tokio::spawn(async move {
            let _ = handle
                .data(id, format!("4242\n{}\n", exec::TOKEN).into_bytes())
                .await;
            if matches!(mode, Mode::Hang) {
                return;
            }
            if matches!(mode, Mode::Flood) {
                for _ in 0..40 {
                    if handle
                        .extended_data(id, 1, vec![b'x'; 65536])
                        .await
                        .is_err()
                    {
                        return;
                    }
                }
            }
            if matches!(mode, Mode::Signal) {
                let _ = handle
                    .exit_signal_request(id, russh::Sig::TERM, false, String::new(), String::new())
                    .await;
            } else {
                let _ = handle
                    .exit_status_request(id, if matches!(mode, Mode::Exit) { 17 } else { 0 })
                    .await;
            }
            let _ = handle.eof(id).await;
            let _ = handle.close(id).await;
        });
        Ok(())
    }
}
async fn fixture(
    mode: Mode,
) -> (
    SocketAddr,
    Profile,
    PrivateKey,
    Fixture,
    tokio::task::JoinHandle<()>,
) {
    let host = PrivateKey::random(&mut rand::rng(), russh::keys::Algorithm::Ed25519).unwrap();
    let identity = PrivateKey::random(&mut rand::rng(), russh::keys::Algorithm::Ed25519).unwrap();
    let profile = Profile {
        schema: 1,
        profile_id: "termux-local".into(),
        installation: "a".repeat(32),
        username: "fixture".into(),
        host_key: host.public_key().to_openssh().unwrap(),
    };
    let mut fixture = Fixture {
        allowed: identity.public_key().clone(),
        mode,
        passwords: Arc::new(AtomicUsize::new(0)),
        execs: Arc::new(AtomicUsize::new(0)),
        handle: Arc::new(Mutex::new(None)),
        audit: Arc::new(Mutex::new(None)),
        audits: Arc::new(AtomicUsize::new(0)),
        non_probe_execs: Arc::new(AtomicUsize::new(0)),
    };
    let state = fixture.clone();
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let address = listener.local_addr().unwrap();
    let config = Arc::new(server::Config {
        keys: vec![host],
        auth_rejection_time: Duration::ZERO,
        auth_rejection_time_initial: Some(Duration::ZERO),
        ..Default::default()
    });
    let task = tokio::spawn(async move {
        let _ = fixture.run_on_socket(config, &listener).await;
    });
    (address, profile, identity, state, task)
}
async fn wait(controller: &Controller, test: impl Fn(&Snapshot) -> bool) {
    let end = Instant::now() + Duration::from_secs(4);
    while !test(&controller.snapshot()) {
        assert!(Instant::now() < end, "{:?}", controller.snapshot());
        tokio::time::sleep(Duration::from_millis(2)).await;
    }
}
async fn stop(controller: &Controller) {
    let now = Instant::now();
    controller.request_stop();
    assert!(now.elapsed() < Duration::from_millis(100));
    let end = Instant::now() + Duration::from_secs(2);
    while !controller.is_finished() {
        assert!(Instant::now() < end);
        tokio::time::sleep(Duration::from_millis(2)).await;
    }
    let report = controller.snapshot();
    assert_eq!(report.workers, 0);
    assert_eq!(report.tokio_threads, 0);
    assert_eq!(report.owned_connections, 0);
}
fn request(n: i64) -> CommandSeq {
    CommandSeq::new(n).unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn twenty_peer_disconnects_reclaim_workers_and_require_explicit_pinned_reconnect() {
    let (address, profile, key, fixture, server) = fixture(Mode::Quiet).await;
    let pin = profile.host_key.clone();
    let controller = Controller::start_on(address, Deadlines::default()).unwrap();
    let mut previous = None;
    for cycle in 1..=21 {
        let request = request(cycle);
        assert_eq!(
            controller.connect(profile.clone(), request),
            Status::Accepted
        );
        assert_eq!(controller.connect(profile.clone(), request), Status::Busy);
        wait(&controller, |s| s.state == State::AwaitingCredential).await;
        let session = controller.snapshot().session.unwrap();
        if let Some(old) = previous {
            assert!(session > old);
            assert_eq!(controller.provide(old, request, key.clone()), Status::Stale);
            assert_eq!(controller.disconnect(old), Status::Stale);
        }
        assert_eq!(
            controller.provide(session, request, key.clone()),
            Status::Accepted
        );
        wait(&controller, |s| s.state == State::Ready).await;
        assert_eq!(controller.snapshot().owned_connections, 1);
        assert_eq!(profile.host_key, pin);
        if cycle == 21 {
            assert_eq!(controller.disconnect(session), Status::Accepted);
            wait(&controller, |s| s.state == State::Disconnected).await;
        } else {
            // 試験用SSH接続のpeerだけを切断。通常sshdや同UIDの他processへ信号を送らない。
            let peer = fixture.handle.lock().unwrap().clone().unwrap();
            peer.disconnect(
                russh::Disconnect::ByApplication,
                "fixture disconnect".into(),
                "en".into(),
            )
            .await
            .unwrap();
            wait(&controller, |s| s.state == State::Failed).await;
            assert_eq!(controller.snapshot().error, Some("REMOTE_EXIT"));
        }
        let stopped = controller.snapshot();
        assert_eq!(stopped.owned_connections, 0);
        assert_eq!(stopped.tokio_threads, 0);
        assert_eq!(stopped.forward.active, 0);
        assert_eq!(stopped.workers, 1);
        assert!(stopped.pending_cleanup.is_empty());
        let execs = fixture.execs.load(Ordering::Acquire);
        tokio::time::sleep(Duration::from_millis(30)).await;
        assert_eq!(controller.snapshot().session, Some(session));
        assert_eq!(controller.snapshot().state, stopped.state);
        assert_eq!(fixture.execs.load(Ordering::Acquire), execs); // 自動再試行なし。
        previous = Some(session);
    }
    assert_eq!(fixture.passwords.load(Ordering::Acquire), 0);
    stop(&controller).await;
    server.abort();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cancel_before_session_allocation_and_old_request_cannot_cancel_reconnect() {
    let (address, profile, key, state, server) = fixture(Mode::Flood).await;
    let (release, gate) = std::sync::mpsc::channel();
    let controller =
        Controller::start_at_with_hook(address, Deadlines::default(), None, move || {
            gate.recv_timeout(Duration::from_secs(4)).unwrap();
        })
        .unwrap();
    assert_eq!(
        controller.connect(profile.clone(), request(1)),
        Status::Accepted
    );
    assert_eq!(controller.snapshot().session, None);
    assert_eq!(controller.cancel_connect(request(2)), Status::Stale);
    assert_eq!(controller.snapshot().state, State::Connecting);
    assert_eq!(controller.cancel_connect(request(1)), Status::Accepted);
    assert_eq!(controller.snapshot().state, State::Disconnecting);
    assert_eq!(controller.snapshot().session, None);
    release.send(()).unwrap();
    wait(&controller, |s| s.state == State::Disconnected).await;
    let report = controller.snapshot();
    assert_eq!(report.session, None);
    assert_eq!(report.credential_requests, 0);
    assert_eq!(report.tokio_threads, 0);
    assert_eq!(report.owned_connections, 0);
    assert_eq!(controller.connect(profile, request(2)), Status::Accepted);
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    assert_eq!(controller.cancel_connect(request(1)), Status::Stale);
    assert_eq!(
        controller.provide(controller.snapshot().session.unwrap(), request(2), key),
        Status::Accepted
    );
    wait(&controller, |s| s.state == State::Ready).await;
    assert_eq!(controller.cancel_connect(request(1)), Status::Stale);
    assert_eq!(controller.snapshot().state, State::Ready);
    assert_eq!(state.execs.load(Ordering::Acquire), 1);
    assert_eq!(controller.cancel_connect(request(2)), Status::Accepted);
    wait(&controller, |s| s.state == State::Disconnected).await;
    assert_eq!(controller.snapshot().owned_connections, 0);
    assert_eq!(controller.snapshot().tokio_threads, 0);
    stop(&controller).await;
    server.abort();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn valid_pin_auth_uid_and_stderr_flood_drain_to_eof_then_owned_stop() {
    let (address, profile, key, state, server) = fixture(Mode::Flood).await;
    let controller = Controller::start_on(address, Deadlines::default()).unwrap();
    assert_eq!(controller.connect(profile, request(1)), Status::Accepted);
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    assert_eq!(
        controller.provide(controller.snapshot().session.unwrap(), request(1), key),
        Status::Accepted
    );
    wait(&controller, |s| s.state == State::Ready).await;
    let report = controller.snapshot();
    assert_eq!(report.probe.uid, Some(4242));
    assert!(report.probe.token_matched && report.probe.eof);
    assert_eq!(report.probe.stderr_bytes, 40 * 65536);
    assert!(report.probe.stderr_truncated);
    assert_eq!(report.probe.exit_status, Some(0));
    assert_eq!(state.passwords.load(Ordering::Acquire), 0);
    assert_eq!(state.execs.load(Ordering::Acquire), 1);
    let peer = state.handle.lock().unwrap().clone().unwrap();
    assert!(
        timeout(Duration::from_secs(1), peer.channel_open_session())
            .await
            .unwrap()
            .is_err()
    );
    assert!(
        timeout(
            Duration::from_secs(1),
            peer.channel_open_forwarded_tcpip("127.0.0.1", 8022, "127.0.0.1", 1234)
        )
        .await
        .unwrap()
        .is_err()
    );
    assert!(
        timeout(Duration::from_secs(1), peer.channel_open_agent())
            .await
            .unwrap()
            .is_err()
    );
    assert!(
        timeout(
            Duration::from_secs(1),
            peer.channel_open_x11("127.0.0.1", 1234)
        )
        .await
        .unwrap()
        .is_err()
    );
    assert_eq!(
        controller.disconnect(
            controller
                .snapshot()
                .session
                .unwrap()
                .checked_next()
                .unwrap()
        ),
        Status::Stale
    );
    assert_eq!(
        controller.forward_probe(
            controller
                .snapshot()
                .session
                .unwrap()
                .checked_next()
                .unwrap(),
            Path::new("/tmp"),
            "aaaaaaaaaaaaaaaa"
        ),
        Status::Stale
    );
    assert!(controller.snapshot().forward_probe.is_none());
    stop(&controller).await;
    server.abort();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn refused_connection_is_distinct_and_never_requests_identity() {
    let (_address, profile, _key, _state, server) = fixture(Mode::Flood).await;
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let address = listener.local_addr().unwrap();
    drop(listener);
    let controller = Controller::start_on(address, Deadlines::default()).unwrap();
    controller.connect(profile, request(1));
    wait(&controller, |s| {
        s.state == State::Failed && s.owned_connections == 0
    })
    .await;
    assert_eq!(controller.snapshot().error, Some("SSH_CONNECTION_REFUSED"));
    assert_eq!(controller.snapshot().credential_requests, 0);
    stop(&controller).await;
    server.abort();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn wrong_pin_never_requests_credential_and_wrong_identity_never_falls_back() {
    let (address, mut profile, _key, state, server) = fixture(Mode::Flood).await;
    let original = profile.clone();
    let wrong = PrivateKey::random(&mut rand::rng(), russh::keys::Algorithm::Ed25519).unwrap();
    profile.host_key = wrong.public_key().to_openssh().unwrap();
    let controller = Controller::start_on(address, Deadlines::default()).unwrap();
    controller.connect(profile, request(1));
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("HOST_KEY_MISMATCH"));
    assert_eq!(controller.snapshot().credential_requests, 0);
    let previous = controller.snapshot().session.unwrap();
    assert_eq!(controller.connect(original, request(2)), Status::Accepted);
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    assert!(controller.snapshot().session.unwrap() > previous);
    assert_eq!(
        controller.provide(previous, request(2), wrong.clone()),
        Status::Stale
    );
    controller.provide(controller.snapshot().session.unwrap(), request(2), wrong);
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("AUTH_FAILED"));
    assert_eq!(state.passwords.load(Ordering::Acquire), 0);
    assert_eq!(state.execs.load(Ordering::Acquire), 0);
    stop(&controller).await;
    server.abort();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn connect_auth_and_exec_deadlines_and_nonzero_exit_are_distinct() {
    let deadlines = Deadlines {
        connect: Duration::from_secs(1),
        auth: Duration::from_secs(1),
        exec: Duration::from_secs(1),
    };
    let (address, profile, key, _state, server) = fixture(Mode::Hang).await;
    let controller = Controller::start_on(address, deadlines).unwrap();
    controller.connect(profile.clone(), request(1));
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("AUTH_TIMEOUT"));
    controller.connect(profile, request(2));
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    controller.provide(controller.snapshot().session.unwrap(), request(2), key);
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("EXEC_TIMEOUT"));
    assert!(!controller.snapshot().probe.eof);
    stop(&controller).await;
    server.abort();
    let (address, profile, key, _state, server) = fixture(Mode::Exit).await;
    let controller = Controller::start_on(address, Deadlines::default()).unwrap();
    controller.connect(profile.clone(), request(1));
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    controller.provide(controller.snapshot().session.unwrap(), request(1), key);
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("REMOTE_EXIT"));
    assert_eq!(controller.snapshot().probe.exit_status, Some(17));
    stop(&controller).await;
    server.abort();
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let controller = Controller::start_on(listener.local_addr().unwrap(), deadlines).unwrap();
    controller.connect(profile, request(1));
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("CONNECT_TIMEOUT"));
    stop(&controller).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn stop_interrupts_a_peer_that_never_sends_ssh_banner() {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let (_address, profile, _key, _state, server) = fixture(Mode::Hang).await;
    let controller =
        Controller::start_on(listener.local_addr().unwrap(), Deadlines::default()).unwrap();
    controller.connect(profile, request(1));
    wait(&controller, |s| s.tokio_threads >= 2).await;
    stop(&controller).await;
    server.abort();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn exit_signal_is_not_an_exit_status_or_success() {
    let (address, profile, key, _state, server) = fixture(Mode::Signal).await;
    let controller = Controller::start_on(address, Deadlines::default()).unwrap();
    controller.connect(profile, request(1));
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    controller.provide(controller.snapshot().session.unwrap(), request(1), key);
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("REMOTE_EXIT"));
    assert!(controller.snapshot().probe.exit_signal);
    assert_eq!(controller.snapshot().probe.exit_status, None);
    stop(&controller).await;
    server.abort();
}

struct JournalRoot(PathBuf);
impl JournalRoot {
    fn new() -> Self {
        use std::os::unix::fs::DirBuilderExt;
        let path =
            std::env::temp_dir().join(format!("eidolon-controller-{:016x}", rand::random::<u64>()));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&path)
            .unwrap();
        Self(path)
    }
}
impl Drop for JournalRoot {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn pending_receipt_blocks_remote_start_then_survives_controller_recreation_and_verifies_new_epoch()
 {
    let (address, profile, key, state, server) = fixture(Mode::Flood).await;
    let root = JournalRoot::new();
    let child = launcher::Identity {
        pid: 42,
        group: 42,
        start: "123".into(),
        state: "S".into(),
    };
    let mut saved = journal::Journal::load(Some(&root.0)).unwrap();
    let origin = SessionId::new(77).unwrap();
    saved
        .remember(journal::Entry {
            nonce: "ffffffffffffffff".into(),
            session: origin,
            uid: 4242,
            binding: journal::binding(&profile),
            child: Some(child.clone()),
        })
        .unwrap();
    *state.audit.lock().unwrap() =
        Some(serde_json::json!({"schema":1,"cleaned":false,"active":true,"orphaned":false}));
    let controller =
        Controller::start_at(address, Deadlines::default(), Some(root.0.clone())).unwrap();
    assert_eq!(
        controller.connect(profile.clone(), request(1)),
        Status::Accepted
    );
    wait(&controller, |r| r.state == State::AwaitingCredential).await;
    let first = controller.snapshot().session.unwrap();
    assert!(first > origin);
    controller.provide(first, request(1), key.clone());
    wait(&controller, |r| r.state == State::CleanupPending).await;
    let report = controller.snapshot();
    assert_eq!(report.pending_cleanup.len(), 1);
    assert!(!report.cleanup_audits[0].verified);
    assert_eq!(
        controller.forward_probe(first, &root.0, "aaaaaaaaaaaaaaaa"),
        Status::NotReady
    );
    stop(&controller).await;
    drop(controller);
    assert_eq!(
        journal::Journal::load(Some(&root.0))
            .unwrap()
            .pending()
            .len(),
        1
    );
    *state.audit.lock().unwrap() = Some(
        serde_json::json!({"schema":1,"cleaned":true,"already_absent":true,"child_alive":false,
        "receipt":{"type":"finished","schema":1,"nonce":"ffffffffffffffff","uid":4242,"reason":"EOF","child":child,
        "exit_status":null,"exit_signal":15,"term_sent":true,"kill_sent":false,"cleaned":true,"group_alive":false,"pipes_closed":true,
        "stdout":{"bytes":0,"truncated":false,"retained":0},"stderr":{"bytes":0,"truncated":false,"retained":0}}}),
    );
    let controller =
        Controller::start_at(address, Deadlines::default(), Some(root.0.clone())).unwrap();
    controller.connect(profile, request(2));
    wait(&controller, |r| r.state == State::AwaitingCredential).await;
    let next = controller.snapshot().session.unwrap();
    assert!(next > first);
    controller.provide(next, request(2), key);
    wait(&controller, |r| r.state == State::Ready).await;
    let report = controller.snapshot();
    assert!(report.pending_cleanup.is_empty());
    assert_eq!(report.cleanup_audits.len(), 1);
    assert!(report.cleanup_audits[0].verified);
    assert_eq!(report.cleanup_audits[0].origin_session, origin);
    assert_eq!(report.cleanup_audits[0].current_session, next);
    assert!(
        journal::Journal::load(Some(&root.0))
            .unwrap()
            .pending()
            .is_empty()
    );
    assert!(state.audits.load(Ordering::Acquire) >= 2);
    stop(&controller).await;
    server.abort();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn corrupt_pending_journal_fails_before_network_and_preserves_its_contents() {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let (address, profile, _key, state, server) = fixture(Mode::Flood).await;
    let root = JournalRoot::new();
    journal::Journal::load(Some(&root.0)).unwrap();
    let path = root.0.join("transport-state/transport-pending.json");
    let mut file = std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(&path)
        .unwrap();
    file.write_all(b"corrupt fixture").unwrap();
    drop(file);
    let controller =
        Controller::start_at(address, Deadlines::default(), Some(root.0.clone())).unwrap();
    let _ = controller.connect(profile, request(1));
    wait(&controller, |r| r.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("CLEANUP_JOURNAL_INVALID"));
    assert_eq!(controller.snapshot().credential_requests, 0);
    assert_eq!(state.execs.load(Ordering::Acquire), 0);
    assert_eq!(std::fs::read(&path).unwrap(), b"corrupt fixture");
    stop(&controller).await;
    server.abort();
}

fn linux_profile(
    profile: &Profile,
    address: SocketAddr,
    revision: &str,
) -> eidolon_model::connection_profile::Profile {
    eidolon_model::connection_profile::Profile::parse(&serde_json::json!({
        "schema":2,"installation":profile.installation,"profile_id":"linux-fixture","revision":revision,
        "display_name":"Fixed SSH fixture","backend":"linux_ssh","host":address.ip().to_string(),"port":address.port(),
        "username":profile.username,"host_key":profile.host_key,
    }).to_string(), &profile.installation).unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn v2_endpoints_and_credentials_are_captured_per_request_and_linux_is_probe_only() {
    let (address_a, profile_a, key_a, state_a, server_a) = fixture(Mode::Quiet).await;
    let (address_b, profile_b, key_b, state_b, server_b) = fixture(Mode::Quiet).await;
    let a = linux_profile(&profile_a, address_a, "9007199254740993");
    let b = linux_profile(&profile_b, address_b, "9007199254740994");
    assert_ne!(a.binding(), b.binding());
    let controller = Controller::start().unwrap();
    assert_eq!(
        controller.connect_spec(ConnectSpec::from_v2(&a).unwrap(), request(1)),
        Status::Accepted
    );
    wait(&controller, |r| r.state == State::AwaitingCredential).await;
    let scope_a = controller.credential_scope().unwrap();
    assert_eq!(scope_a.profile_id, "linux-fixture");
    assert_eq!(scope_a.revision.as_deref(), Some("9007199254740993"));
    assert_eq!(scope_a.binding, a.binding());
    assert_eq!(
        controller.provide(scope_a.session, request(1), key_a.clone()),
        Status::Stale
    );
    assert_eq!(
        controller.provide_for(scope_a.session, request(1), &b.binding(), key_a.clone()),
        Status::Stale
    );
    assert_eq!(
        controller.provide_for(scope_a.session, request(1), &scope_a.binding, key_a.clone()),
        Status::Accepted
    );
    wait(&controller, |r| r.state == State::ProbeReady).await;
    assert!(controller.credential_scope().is_none());
    assert!(controller.snapshot().probe.token_matched);
    let nonce = "aaaaaaaaaaaaaaaa";
    assert_eq!(
        controller.start_app(
            scope_a.session,
            Path::new("/fixed/sock"),
            nonce,
            AppMode::Foot
        ),
        Status::NotReady
    );
    assert_eq!(
        controller.forward_probe(scope_a.session, Path::new("/fixed"), nonce),
        Status::NotReady
    );
    assert_eq!(
        controller.cleanup_probe(
            scope_a.session,
            Path::new("/fixed"),
            nonce,
            CleanupScenario::ControlEof
        ),
        Status::NotReady
    );
    assert_eq!(
        controller.open_terminal(scope_a.session, request(2)),
        Status::NotReady
    );
    assert_eq!(
        controller.catalog(
            scope_a.session,
            request(3),
            crate::catalog::Operation::Reload {
                path: "fixture.toml".into()
            }
        ),
        Status::NotReady
    );
    assert!(controller.catalog_ui().session.is_none());
    let diagnostics = serde_json::to_string(&controller.snapshot()).unwrap();
    assert!(!diagnostics.contains(&scope_a.binding));
    assert!(!diagnostics.contains(&profile_a.host_key));
    assert_eq!(state_a.execs.load(Ordering::Acquire), 1);
    assert_eq!(state_a.non_probe_execs.load(Ordering::Acquire), 0);
    assert_eq!(controller.disconnect(scope_a.session), Status::Accepted);
    wait(&controller, |r| {
        r.state == State::Disconnected && r.tokio_threads == 0
    })
    .await;
    assert!(controller.credential_scope().is_none());
    assert_eq!(
        controller.connect_spec(ConnectSpec::from_v2(&b).unwrap(), request(4)),
        Status::Accepted
    );
    wait(&controller, |r| r.state == State::AwaitingCredential).await;
    let scope_b = controller.credential_scope().unwrap();
    assert!(scope_b.session > scope_a.session);
    assert_eq!(controller.cancel_connect(request(1)), Status::Stale);
    assert_eq!(
        controller.provide_for(scope_a.session, request(1), &scope_a.binding, key_a),
        Status::Stale
    );
    assert_eq!(
        controller.provide_for(scope_b.session, request(4), &scope_a.binding, key_b.clone()),
        Status::Stale
    );
    assert_eq!(
        controller.provide_for(scope_b.session, request(4), &scope_b.binding, key_b),
        Status::Accepted
    );
    wait(&controller, |r| r.state == State::ProbeReady).await;
    assert_eq!(
        controller.snapshot().profile_revision.as_deref(),
        Some("9007199254740994")
    );
    assert_eq!(state_b.execs.load(Ordering::Acquire), 1);
    assert_eq!(state_b.non_probe_execs.load(Ordering::Acquire), 0);
    assert_eq!(
        state_a.passwords.load(Ordering::Acquire) + state_b.passwords.load(Ordering::Acquire),
        0
    );
    stop(&controller).await;
    server_a.abort();
    server_b.abort();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn v2_wrong_endpoint_pin_never_requests_identity_or_falls_back() {
    let (address_a, profile_a, _, state_a, server_a) = fixture(Mode::Quiet).await;
    let (_, profile_b, _, _, server_b) = fixture(Mode::Quiet).await;
    let wrong = linux_profile(&profile_b, address_a, "1");
    let controller = Controller::start().unwrap();
    assert_eq!(
        controller.connect_spec(ConnectSpec::from_v2(&wrong).unwrap(), request(1)),
        Status::Accepted
    );
    wait(&controller, |r| r.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("HOST_KEY_MISMATCH"));
    assert_eq!(controller.snapshot().credential_requests, 0);
    assert!(controller.credential_scope().is_none());
    assert_eq!(state_a.execs.load(Ordering::Acquire), 0);
    assert_ne!(profile_a.host_key, profile_b.host_key);
    stop(&controller).await;
    server_a.abort();
    server_b.abort();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn v2_probe_preserves_legacy_foreign_and_same_scope_journals_without_running_cleanup_helper()
{
    let (address, profile, key, state, server) = fixture(Mode::Quiet).await;
    let current = linux_profile(&profile, address, "1");
    let foreign = linux_profile(
        &profile,
        (Ipv4Addr::new(192, 0, 2, 1), address.port()).into(),
        "1",
    );
    let root = JournalRoot::new();
    let bindings = vec![
        journal::binding(&profile),
        foreign.binding(),
        current.binding(),
    ];
    assert_ne!(bindings[0], bindings[2]);
    let mut journal = journal::Journal::load(Some(&root.0)).unwrap();
    for (i, binding) in bindings.iter().enumerate() {
        journal
            .remember(journal::Entry {
                nonce: format!("{:016x}", i + 1),
                session: SessionId::new(i as i64 + 1).unwrap(),
                uid: 4242,
                binding: binding.clone(),
                child: None,
            })
            .unwrap();
    }
    drop(journal);
    let controller = Controller::start_with_root(Some(root.0.clone())).unwrap();
    controller.connect_spec(ConnectSpec::from_v2(&current).unwrap(), request(1));
    wait(&controller, |r| r.state == State::AwaitingCredential).await;
    let scope = controller.credential_scope().unwrap();
    controller.provide_for(scope.session, scope.request, &scope.binding, key);
    wait(&controller, |r| r.state == State::CleanupPending).await;
    let snapshot = controller.snapshot();
    assert_eq!(snapshot.pending_cleanup.len(), 3);
    assert_eq!(
        snapshot
            .cleanup_audits
            .iter()
            .filter(|a| a.error == Some("CLEANUP_PROFILE_MISMATCH"))
            .count(),
        2
    );
    assert_eq!(
        snapshot
            .cleanup_audits
            .iter()
            .filter(|a| a.error == Some("CLEANUP_BACKEND_UNSUPPORTED"))
            .count(),
        1
    );
    assert_eq!(state.non_probe_execs.load(Ordering::Acquire), 0);
    assert_eq!(state.audits.load(Ordering::Acquire), 0);
    stop(&controller).await;
    let persisted = journal::Journal::load(Some(&root.0)).unwrap();
    assert_eq!(
        persisted
            .entries()
            .iter()
            .map(|e| e.binding.clone())
            .collect::<Vec<_>>(),
        bindings
    );
    server.abort();
}
