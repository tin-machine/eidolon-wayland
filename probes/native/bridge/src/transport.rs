//! APK内で鍵を生成し、公開情報のみをADBで交換する独立transport試験。
use russh::keys::{
    Algorithm, PrivateKey, PrivateKeyWithHashAlg, PublicKey, PublicKeyOrCertificate,
};
use russh::{Channel, ChannelMsg, Disconnect, client};
use serde_json::{Value, json};
use std::{
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{UnixListener, UnixStream},
};
type Error = Box<dyn std::error::Error + Send + Sync>;

struct Peer {
    pin: PublicKey,
    path: String,
    local: PathBuf,
    mismatch: Arc<AtomicBool>,
    accepted: Arc<AtomicUsize>,
}
impl client::Handler for Peer {
    type Error = russh::Error;
    async fn check_server_key(
        &mut self,
        key: &PublicKeyOrCertificate,
    ) -> Result<bool, Self::Error> {
        let ok = key.certificate().is_none() && key.public_key().key_data() == self.pin.key_data();
        if !ok {
            self.mismatch.store(true, Ordering::SeqCst);
        }
        Ok(ok)
    }
    async fn server_channel_open_forwarded_streamlocal(
        &mut self,
        channel: Channel<client::Msg>,
        path: &str,
        reply: client::ChannelOpenHandle,
        _: &mut client::Session,
    ) -> Result<(), Self::Error> {
        if path != self.path || self.accepted.load(Ordering::SeqCst) >= 4 {
            return Ok(());
        }
        let Ok(mut socket) = UnixStream::connect(&self.local).await else {
            return Ok(());
        };
        self.accepted.fetch_add(1, Ordering::SeqCst);
        reply.accept().await;
        tokio::spawn(async move {
            let mut stream = channel.into_stream();
            let _ = tokio::time::timeout(
                Duration::from_secs(25),
                tokio::io::copy_bidirectional(&mut stream, &mut socket),
            )
            .await;
        });
        Ok(())
    }
}

struct Files(Vec<PathBuf>);
impl Drop for Files {
    fn drop(&mut self) {
        for path in &self.0 {
            let _ = std::fs::remove_file(path);
        }
    }
}

pub fn run(directory: &str, nonce: &str) -> Result<Value, Error> {
    if nonce.is_empty()
        || nonce.len() > 64
        || !nonce
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
    {
        return Err("invalid nonce".into());
    }
    let base = Path::new(directory);
    let request = base.join("ssh-request.json");
    let profile = base.join(format!("ssh-profile-{nonce}.json"));
    let local = base.join("ssh-echo.sock");
    let _files = Files(vec![request.clone(), profile.clone(), local.clone()]);
    let key = Arc::new(PrivateKey::random(&mut rand::rng(), Algorithm::Ed25519)?);
    // 秘密鍵はメモリーのみ。hostへ出すのは公開鍵とnonceだけ。
    std::fs::write(
        &request,
        json!({"nonce":nonce,"public_key":key.public_key().to_openssh()?}).to_string(),
    )?;
    let deadline = Instant::now() + Duration::from_secs(90);
    while !profile.exists() {
        if Instant::now() > deadline {
            return Err("public profile timeout".into());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let profile: Value = serde_json::from_slice(&std::fs::read(profile)?)?;
    let username = profile["username"]
        .as_str()
        .ok_or("username missing")?
        .to_owned();
    if !username
        .bytes()
        .all(|c| c.is_ascii_alphanumeric() || c == b'_')
    {
        return Err("invalid username".into());
    }
    let pin = PublicKey::from_openssh(profile["host_key"].as_str().ok_or("host key missing")?)?;
    let remote = format!("/data/data/com.termux/files/home/.eidolon-probe-{nonce}");
    let remote_socket = format!("{remote}/s");
    if remote_socket.len() > 103 || local.as_os_str().len() > 103 {
        return Err("socket path too long".into());
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    let result = runtime.block_on(async { tokio::time::timeout(Duration::from_secs(45), async {
        let mismatch = Arc::new(AtomicBool::new(false));
        let accepted = Arc::new(AtomicUsize::new(0));
        let config = Arc::new(client::Config { inactivity_timeout: Some(Duration::from_secs(15)), ..Default::default() });
        let wrong = Peer { pin: key.public_key().clone(), path: remote_socket.clone(), local: local.clone(), mismatch: mismatch.clone(), accepted: accepted.clone() };
        if client::connect(config.clone(), ("127.0.0.1",8022), wrong).await.is_ok() || !mismatch.load(Ordering::SeqCst) { return Err::<Value,Error>("host mismatch was not rejected".into()); }
        let listener = UnixListener::bind(&local)?;
        let echo = tokio::spawn(async move {
            for _ in 0..4 {
                let (mut socket, _) = listener.accept().await?;
                let mut data = [0u8; 65536];
                loop {
                    let n = socket.read(&mut data).await?;
                    if n == 0 { break; }
                    socket.write_all(&data[..n]).await?;
                }
            }
            Ok::<(),std::io::Error>(())
        });
        let peer = Peer { pin, path: remote_socket.clone(), local, mismatch, accepted: accepted.clone() };
        let mut session = client::connect(config, ("127.0.0.1",8022), peer).await?;
        if !session.authenticate_publickey(username, PrivateKeyWithHashAlg::new(key, None)).await?.success() { return Err("authentication rejected".into()); }
        session.streamlocal_forward(remote_socket.clone()).await?;
        let mut channel = session.channel_open_session().await?;
        // pathは固定prefix+検証済みnonce。ユーザー入力や任意shell commandは受け付けない。
        channel.exec(true, format!("{remote}/peer --ssh-client {remote_socket}")).await?;
        let mut output = Vec::new(); let mut code = None;
        while let Some(message) = channel.wait().await {
            match message {
                ChannelMsg::Data { data } | ChannelMsg::ExtendedData { data, .. } => {
                    if output.len() + data.len() > 65536 { return Err("exec output limit".into()); }
                    output.extend_from_slice(&data);
                },
                ChannelMsg::ExitStatus { exit_status } => code = Some(exit_status),
                _ => {},
            }
        }
        session.cancel_streamlocal_forward(remote_socket).await?;
        session.disconnect(Disconnect::ByApplication,"probe finished","en").await?;
        echo.await??;
        let ok = code == Some(0) && output == b"echo-pass channels=4 bytes=33554432\n" && accepted.load(Ordering::SeqCst) == 4;
        if !ok { return Err(format!("echo/exec mismatch code={code:?}").into()); }
        Ok(json!({"pass":true,"uid":unsafe{libc::getuid()},"host_mismatch_rejected":true,"streamlocal_channels":4,"echo_bytes":33554432,"forward_cancelled":true}))
    }).await });
    runtime.shutdown_timeout(Duration::from_secs(2));
    result?
}
