use super::*;
use russh::{
    Channel, ChannelId,
    server::{self, PublicKeyOffer, Server as _, Session, VerifiedPublicKey},
};
use tokio::net::TcpListener;

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
