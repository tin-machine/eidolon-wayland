//! Actual actor over encrypted TCP SSH with a deterministic peer. OpenSSH and
//! Python-helper integration remain separate fixtures; no Unix socket is needed.
use super::{Action, Binding, Phase, owner, tests};
use russh::{
    Channel, ChannelId, client,
    keys::{Algorithm, PrivateKey, PublicKey, PublicKeyOrCertificate},
    server::{self, Server as _, Session},
};
use serde_json::{Value, json};
use std::{
    net::{Ipv4Addr, SocketAddr},
    sync::{Arc, Mutex},
};
use tokio::{
    net::TcpListener,
    task::JoinHandle,
    time::{Duration, timeout},
};
use tokio_util::sync::CancellationToken;

#[derive(Clone, Copy)]
enum Mode {
    CrossExit,
    LostStart,
    Truncated,
    MissingEof,
    Nonzero,
    Coalesced,
}

#[derive(Clone)]
struct Server {
    binding: Binding,
    mode: Mode,
    commands: Arc<Mutex<Vec<String>>>,
    input: Vec<u8>,
}
impl Server {
    fn frame(&self, sequence: u64, request: u64, event: Value) -> Vec<u8> {
        format!("{}\n", tests::row(&self.binding, sequence, request, event)).into_bytes()
    }
    fn finish(&self, id: ChannelId, session: &mut Session) -> Result<(), russh::Error> {
        if !matches!(self.mode, Mode::MissingEof) {
            session.eof(id)?;
        }
        session.exit_status_request(
            id,
            if matches!(self.mode, Mode::Nonzero) {
                7
            } else {
                0
            },
        )?;
        session.close(id)
    }
}
impl server::Server for Server {
    type Handler = Self;
    fn new_client(&mut self, _: Option<SocketAddr>) -> Self {
        self.clone()
    }
    fn handle_session_error(&mut self, _: russh::Error) {}
}
impl server::Handler for Server {
    type Error = russh::Error;
    async fn auth_none(&mut self, user: &str) -> Result<server::Auth, Self::Error> {
        Ok(if user == "fixture" {
            server::Auth::Accept
        } else {
            server::Auth::reject()
        })
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
        assert_eq!(
            data,
            owner::command(&self.binding, None).unwrap().as_bytes()
        );
        session.channel_success(id)?;
        session.data(id, self.frame(1, 0, tests::prepared_event(&self.binding)))
    }
    async fn data(
        &mut self,
        id: ChannelId,
        data: &[u8],
        session: &mut Session,
    ) -> Result<(), Self::Error> {
        self.input.extend_from_slice(data);
        assert!(self.input.len() <= 16384);
        while let Some(end) = self.input.iter().position(|byte| *byte == b'\n') {
            let line: Vec<_> = self.input.drain(..=end).collect();
            let value: Value = serde_json::from_slice(&line).unwrap();
            let action = value["command"]["type"].as_str().unwrap();
            let count = {
                let mut commands = self.commands.lock().unwrap();
                commands.push(action.to_owned());
                commands.len()
            };
            assert_eq!(value["sequence"], count.to_string());
            match action {
                "inspect" => session.data(id, self.frame(2, 1, tests::catalog(&self.binding)))?,
                "start" => {
                    if matches!(self.mode, Mode::LostStart) {
                        return self.finish(id, session);
                    }
                    let mut frames = self.frame(
                        3,
                        2,
                        tests::event("started", json!({"app_id":"gtk3-desktop","mode":"gui"})),
                    );
                    if matches!(self.mode, Mode::Truncated) {
                        frames.extend_from_slice(b"{");
                    } else if !matches!(self.mode, Mode::CrossExit) {
                        frames.extend_from_slice(&self.frame(
                            4,
                            2,
                            tests::stopped("process_exited"),
                        ));
                    }
                    // Coalescing is intentional: an actor must retain the Started
                    // ACK even when the final phase of this one packet is Stopped.
                    session.data(id, frames)?;
                    if !matches!(self.mode, Mode::CrossExit) {
                        return self.finish(id, session);
                    }
                }
                "heartbeat" | "stop" if matches!(self.mode, Mode::CrossExit) => {
                    // The terminal notification was generated before request 3
                    // reached the helper; its request_sequence remains 2.
                    session.data(id, self.frame(4, 2, tests::stopped("process_exited")))?;
                    return self.finish(id, session);
                }
                _ => panic!("unexpected actor command"),
            }
        }
        Ok(())
    }
}

struct Peer(PublicKey);
impl client::Handler for Peer {
    type Error = russh::Error;
    async fn check_server_key(
        &mut self,
        key: &PublicKeyOrCertificate,
    ) -> Result<bool, Self::Error> {
        Ok(key.certificate().is_none() && key.public_key().key_data() == self.0.key_data())
    }
}
struct Fixture {
    client: client::Handle<Peer>,
    commands: Arc<Mutex<Vec<String>>>,
    task: JoinHandle<()>,
}
impl Fixture {
    async fn new(binding: &Binding, mode: Mode) -> Self {
        let key = PrivateKey::random(&mut rand::rng(), Algorithm::Ed25519).unwrap();
        let public = key.public_key().clone();
        let commands = Arc::new(Mutex::new(Vec::new()));
        let mut server = Server {
            binding: binding.clone(),
            mode,
            commands: commands.clone(),
            input: Vec::new(),
        };
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
        let endpoint = listener.local_addr().unwrap();
        let config = Arc::new(server::Config {
            keys: vec![key],
            ..Default::default()
        });
        let task = tokio::spawn(async move {
            let _ = server.run_on_socket(config, &listener).await;
        });
        let mut client = timeout(
            Duration::from_secs(3),
            client::connect(
                Arc::new(crate::session::ssh_config()),
                endpoint,
                Peer(public),
            ),
        )
        .await
        .unwrap()
        .unwrap();
        assert!(client.authenticate_none("fixture").await.unwrap().success());
        Self {
            client,
            commands,
            task,
        }
    }
    async fn catalog(&self, binding: &Binding) -> owner::Owner {
        let mut reserved = false;
        let mut active =
            owner::Owner::open(&self.client, binding, CancellationToken::new(), || {
                reserved = true;
                Ok(())
            })
            .await
            .unwrap();
        assert!(reserved);
        while active.phase() == Phase::AwaitingPrepared {
            next(&mut active).await.unwrap();
        }
        active.inspect().await.unwrap();
        while active.phase() == Phase::Prepared {
            next(&mut active).await.unwrap();
        }
        assert_eq!(active.phase(), Phase::Catalog);
        active
    }
    async fn close(self) {
        self.client
            .disconnect(russh::Disconnect::ByApplication, "fixture complete", "en")
            .await
            .unwrap();
        self.task.abort();
    }
}
async fn next(active: &mut owner::Owner) -> Result<(), &'static str> {
    timeout(Duration::from_secs(3), active.next())
        .await
        .expect("actor fixture stalled")
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn tcp_actor_accepts_crossed_exit_after_heartbeat_or_stop_enqueue() {
    for action in [Action::Heartbeat, Action::Stop] {
        let binding = tests::binding();
        let fixture = Fixture::new(&binding, Mode::CrossExit).await;
        let mut active = fixture.catalog(&binding).await;
        let selection = active.selection().unwrap();
        active.start(&selection).await.unwrap();
        while active.phase() == Phase::Catalog {
            next(&mut active).await.unwrap();
        }
        active.control(action, None).await.unwrap();
        while !active.closed() {
            next(&mut active).await.unwrap();
        }
        assert!(active.terminal_remote_clean() && active.channel_complete());
        assert!(active.report().started);
        assert_eq!(active.report().reason, Some("process_exited"));
        assert!(active.start(&selection).await.is_err());
        assert_eq!(
            *fixture.commands.lock().unwrap(),
            [
                "inspect",
                "start",
                if action == Action::Stop {
                    "stop"
                } else {
                    "heartbeat"
                }
            ]
        );
        active.close().await;
        fixture.close().await;
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn tcp_actor_never_replays_a_start_with_no_acknowledgement() {
    let binding = tests::binding();
    let fixture = Fixture::new(&binding, Mode::LostStart).await;
    let mut active = fixture.catalog(&binding).await;
    let selection = active.selection().unwrap();
    active.start(&selection).await.unwrap();
    assert_eq!(next(&mut active).await, Err("DESKTOP_HELPER_LOST"));
    assert!(!active.report().started && !active.terminal_remote_clean());
    assert!(!active.channel_complete());
    assert!(active.start(&selection).await.is_err());
    assert_eq!(*fixture.commands.lock().unwrap(), ["inspect", "start"]);
    active.close().await;
    fixture.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn tcp_actor_separates_terminal_json_from_channel_proof_and_retains_started_ack() {
    for mode in [
        Mode::Truncated,
        Mode::MissingEof,
        Mode::Nonzero,
        Mode::Coalesced,
    ] {
        let binding = tests::binding();
        let fixture = Fixture::new(&binding, mode).await;
        let mut active = fixture.catalog(&binding).await;
        active.start(&active.selection().unwrap()).await.unwrap();
        let mut error = None;
        while !active.closed() {
            if let Err(value) = next(&mut active).await {
                error = Some(value);
                break;
            }
        }
        assert!(active.report().started);
        if matches!(mode, Mode::Coalesced) {
            assert_eq!(error, None);
            assert!(active.terminal_remote_clean() && active.channel_complete());
        } else {
            assert!(error.is_some());
            assert!(!active.channel_complete());
        }
        assert_eq!(*fixture.commands.lock().unwrap(), ["inspect", "start"]);
        active.close().await;
        fixture.close().await;
    }
}
