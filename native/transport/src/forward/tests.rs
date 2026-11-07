use super::*;
use russh::{
    keys::{Algorithm, PrivateKey, PublicKeyOrCertificate},
    server::{self, Server as _, Session},
};
use sha2::{Digest, Sha256};
use std::{
    net::{Ipv4Addr, SocketAddr},
    path::Path,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, UnixListener},
    task::JoinHandle,
};

#[derive(Clone)]
struct Server {
    handle: Arc<Mutex<Option<server::Handle>>>,
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
    async fn streamlocal_forward(
        &mut self,
        path: &str,
        session: &mut Session,
    ) -> Result<bool, Self::Error> {
        *self.handle.lock().unwrap() = Some(session.handle());
        Ok(path == "/fixture/p")
    }
    async fn cancel_streamlocal_forward(
        &mut self,
        path: &str,
        _: &mut Session,
    ) -> Result<bool, Self::Error> {
        Ok(path == "/fixture/p")
    }
}
pub(crate) struct Peer {
    pub router: Arc<Router>,
    pub host: russh::keys::PublicKey,
}
impl client::Handler for Peer {
    type Error = russh::Error;
    async fn check_server_key(
        &mut self,
        key: &PublicKeyOrCertificate,
    ) -> Result<bool, Self::Error> {
        Ok(key.certificate().is_none() && key.public_key().key_data() == self.host.key_data())
    }
    async fn server_channel_open_forwarded_streamlocal(
        &mut self,
        channel: Channel<client::Msg>,
        path: &str,
        reply: client::ChannelOpenHandle,
        _: &mut client::Session,
    ) -> Result<(), Self::Error> {
        self.router.open(id(1), path, channel, reply);
        Ok(())
    }
}
pub(super) fn id(value: i64) -> SessionId {
    SessionId::new(value).unwrap()
}
struct Fixture {
    client: client::Handle<Peer>,
    router: Arc<Router>,
    server: server::Handle,
    task: JoinHandle<()>,
    dir: PathBuf,
}
impl Fixture {
    async fn new() -> Self {
        let dir = std::env::temp_dir().join(format!("eidolon-fwd-{:016x}", rand::random::<u64>()));
        std::fs::create_dir(&dir).unwrap();
        let host = PrivateKey::random(&mut rand::rng(), Algorithm::Ed25519).unwrap();
        let public = host.public_key().clone();
        let mut fixture = Server {
            handle: Arc::new(Mutex::new(None)),
        };
        let state = fixture.clone();
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
        let endpoint = listener.local_addr().unwrap();
        let config = Arc::new(server::Config {
            keys: vec![host],
            event_buffer_size: 256,
            ..Default::default()
        });
        let task = tokio::spawn(async move {
            let _ = fixture.run_on_socket(config, &listener).await;
        });
        let router = Arc::new(Router::new(id(1)));
        let mut client = client::connect(
            Arc::new(crate::session::ssh_config()),
            endpoint,
            Peer {
                router: router.clone(),
                host: public,
            },
        )
        .await
        .unwrap();
        assert!(client.authenticate_none("fixture").await.unwrap().success());
        client.streamlocal_forward("/fixture/p").await.unwrap();
        let server = state.handle.lock().unwrap().clone().unwrap();
        Self {
            client,
            router,
            server,
            task,
            dir,
        }
    }
    async fn open(&self, path: &str) -> Result<Channel<server::Msg>, russh::Error> {
        tokio::time::timeout(
            Duration::from_secs(4),
            self.server.channel_open_forwarded_streamlocal(path),
        )
        .await
        .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.task.abort();
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}
async fn wait_empty(router: &Router) {
    tokio::time::timeout(Duration::from_secs(3), async {
        while router.report().active != 0 {
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    })
    .await
    .unwrap();
}
async fn echo(listener: UnixListener, count: usize) {
    let mut tasks = tokio::task::JoinSet::new();
    for _ in 0..count {
        let (mut socket, _) = listener.accept().await.unwrap();
        tasks.spawn(async move {
            let (mut read, mut write) = socket.split();
            tokio::io::copy(&mut read, &mut write).await.unwrap();
            // 応答の末尾は受信EOF後にだけ返す。
            write.write_all(b"AFTER_EOF").await.unwrap();
            write.shutdown().await.unwrap();
        });
    }
    while let Some(result) = tasks.join_next().await {
        result.unwrap();
    }
}
fn data(n: usize) -> Vec<u8> {
    (0..n).map(|i| ((i * 31 + i / 251) % 256) as u8).collect()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn byte_hash_sizes_four_channels_half_close_and_slow_reader() {
    let f = super::openssh::OpenSsh::new().await;
    let local = f.dir.join("p");
    let listener = UnixListener::bind(&local).unwrap();
    let lease = f.router.arm(id(1), f.remote.clone(), local).unwrap();
    f.client
        .streamlocal_forward(f.remote.clone())
        .await
        .unwrap_or_else(|error| {
            panic!(
                "{error}: {}",
                std::fs::read_to_string(f.dir.join("log")).unwrap()
            )
        });
    let echo = tokio::spawn(echo(listener, 7));
    for size in [1, 65536, 8388608] {
        transfer(f.open().await, size, false).await;
    }
    let mut tasks = tokio::task::JoinSet::new();
    for n in 0..4 {
        let channel = f.open().await;
        tasks.spawn(transfer(channel, 8 * 1024 * 1024, n == 0));
    }
    while let Some(result) = tasks.join_next().await {
        result.unwrap();
    }
    echo.await.unwrap();
    wait_empty(&f.router).await;
    let report = f.router.report();
    assert_eq!(report.accepted, 7);
    assert!(report.peak >= 4 && report.peak <= MAX_CHANNELS);
    assert_eq!(report.remote_to_local, 1 + 65536 + 5 * 8 * 1024 * 1024);
    assert_eq!(report.local_to_remote, report.remote_to_local + 7 * 9);
    assert_eq!(report.copy_buffer_limit, 768 * 1024);
    assert_eq!(report.failed, 0);
    f.client
        .cancel_streamlocal_forward(f.remote.clone())
        .await
        .unwrap();
    lease.close().await;
    f.close().await;
}
async fn transfer(
    stream: impl tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
    size: usize,
    slow: bool,
) {
    let (sent, received) = (AtomicUsize::new(0), AtomicUsize::new(0));
    tokio::time::timeout(Duration::from_secs(25), async {
        let (mut read, mut write) = tokio::io::split(stream);
        let bytes = data(size);
        let expected = Sha256::digest(&bytes);
        let writer = async {
            for block in bytes.chunks(65536) {
                write.write_all(block).await.unwrap();
                sent.fetch_add(block.len(), Ordering::AcqRel);
            }
            write.shutdown().await.unwrap();
        };
        let reader = async {
            if slow {
                tokio::time::sleep(Duration::from_millis(150)).await;
            }
            let mut output = Vec::new();
            let mut block = [0u8; 65536];
            loop {
                let n = read.read(&mut block).await.unwrap();
                if n == 0 {
                    break;
                }
                output.extend_from_slice(&block[..n]);
                received.fetch_add(n, Ordering::AcqRel);
            }
            assert_eq!(&output[size..], b"AFTER_EOF");
            assert_eq!(Sha256::digest(&output[..size]), expected);
            assert_eq!(&output[..size], bytes);
        };
        tokio::join!(writer, reader);
    })
    .await
    .unwrap_or_else(|_| {
        panic!(
            "size={size} sent={} received={}",
            sent.load(Ordering::Acquire),
            received.load(Ordering::Acquire)
        )
    });
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn rejects_wrong_path_stale_session_limit_and_joins_cancelled_tasks() {
    let f = Fixture::new().await;
    let local = f.dir.join("p");
    let listener = UnixListener::bind(&local).unwrap();
    assert!(
        f.router
            .arm(id(2), "/fixture/p".into(), local.clone())
            .is_err()
    );
    assert!(
        f.router
            .arm(id(1), "/fixture/p".into(), Path::new("relative").into())
            .is_err()
    );
    let lease = f
        .router
        .arm(id(1), "/fixture/p".into(), local.clone())
        .unwrap();
    assert!(f.open("/fixture/other").await.is_err());
    assert!(f.client.streamlocal_forward("/denied/p").await.is_err());
    let mut channels = Vec::new();
    let mut sockets = Vec::new();
    for _ in 0..MAX_CHANNELS {
        channels.push(f.open("/fixture/p").await.unwrap());
        sockets.push(listener.accept().await.unwrap());
    }
    assert!(f.open("/fixture/p").await.is_err());
    assert_eq!(f.router.report().active, MAX_CHANNELS);
    let start = std::time::Instant::now();
    lease.close().await;
    assert!(start.elapsed() < Duration::from_secs(2));
    assert_eq!(f.router.report().active, 0);
    assert!(f.open("/fixture/p").await.is_err());
    assert!(f.router.report().rejected >= 3);
    assert_eq!(f.router.report().peak, MAX_CHANNELS);
    drop((channels, sockets));
    // 新しいsessionのRouterへ旧sessionを登録できない。
    let new = Router::new(id(2));
    assert!(new.arm(id(1), "/fixture/p".into(), local).is_err());
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn missing_local_listener_rejects_before_accepting() {
    let f = Fixture::new().await;
    let lease = f
        .router
        .arm(id(1), "/fixture/p".into(), f.dir.join("absent"))
        .unwrap();
    assert!(f.open("/fixture/p").await.is_err());
    wait_empty(&f.router).await;
    assert_eq!(f.router.report().accepted, 0);
    assert_eq!(f.router.report().failed, 1);
    lease.close().await;
}
