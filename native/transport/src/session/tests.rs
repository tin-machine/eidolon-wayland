use super::*;
use russh::{
    Channel, ChannelId,
    server::{self, PublicKeyOffer, Server as _, Session, VerifiedPublicKey},
};
use tokio::net::TcpListener;

#[derive(Clone, Copy)]
enum Mode {
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
            session.channel_failure(id)?;
            return Ok(());
        }
        self.execs.fetch_add(1, Ordering::AcqRel);
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
fn session(n: i64) -> SessionId {
    SessionId::new(n).unwrap()
}
fn request(n: i64) -> CommandSeq {
    CommandSeq::new(n).unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn valid_pin_auth_uid_and_stderr_flood_drain_to_eof_then_owned_stop() {
    let (address, profile, key, state, server) = fixture(Mode::Flood).await;
    let controller = Controller::start_on(address, Deadlines::default()).unwrap();
    assert_eq!(
        controller.connect(profile, session(1), request(1)),
        Status::Accepted
    );
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    assert_eq!(
        controller.provide(session(1), request(1), key),
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
    assert_eq!(controller.disconnect(session(2)), Status::Stale);
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
    controller.connect(profile, session(1), request(1));
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("HOST_KEY_MISMATCH"));
    assert_eq!(controller.snapshot().credential_requests, 0);
    assert_eq!(
        controller.connect(original, session(2), request(2)),
        Status::Accepted
    );
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    controller.provide(session(2), request(2), wrong);
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
    controller.connect(profile.clone(), session(1), request(1));
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("AUTH_TIMEOUT"));
    controller.connect(profile, session(2), request(2));
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    controller.provide(session(2), request(2), key);
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("EXEC_TIMEOUT"));
    assert!(!controller.snapshot().probe.eof);
    stop(&controller).await;
    server.abort();
    let (address, profile, key, _state, server) = fixture(Mode::Exit).await;
    let controller = Controller::start_on(address, Deadlines::default()).unwrap();
    controller.connect(profile.clone(), session(1), request(1));
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    controller.provide(session(1), request(1), key);
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("REMOTE_EXIT"));
    assert_eq!(controller.snapshot().probe.exit_status, Some(17));
    stop(&controller).await;
    server.abort();
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let controller = Controller::start_on(listener.local_addr().unwrap(), deadlines).unwrap();
    controller.connect(profile, session(1), request(1));
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
    controller.connect(profile, session(1), request(1));
    wait(&controller, |s| s.tokio_threads >= 2).await;
    stop(&controller).await;
    server.abort();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn exit_signal_is_not_an_exit_status_or_success() {
    let (address, profile, key, _state, server) = fixture(Mode::Signal).await;
    let controller = Controller::start_on(address, Deadlines::default()).unwrap();
    controller.connect(profile, session(1), request(1));
    wait(&controller, |s| s.state == State::AwaitingCredential).await;
    controller.provide(session(1), request(1), key);
    wait(&controller, |s| s.state == State::Failed).await;
    assert_eq!(controller.snapshot().error, Some("REMOTE_EXIT"));
    assert!(controller.snapshot().probe.exit_signal);
    assert_eq!(controller.snapshot().probe.exit_status, None);
    stop(&controller).await;
    server.abort();
}
