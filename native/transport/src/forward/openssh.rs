//! 製品と同じOpenSSH server。fixture専用鍵/port/process/directoryだけを使う。
use super::tests::{Peer, id};
use super::*;
use russh::keys::{Algorithm, PrivateKey, PrivateKeyWithHashAlg};
use std::{
    fs::OpenOptions,
    io::Write,
    net::{Ipv4Addr, TcpListener},
    os::unix::fs::{DirBuilderExt, OpenOptionsExt},
    process::{Child, Command, Stdio},
};

pub(crate) struct OpenSsh {
    pub client: client::Handle<Peer>,
    pub router: Arc<Router>,
    pub dir: PathBuf,
    pub remote: String,
    owner: Owner,
}
fn write(path: &std::path::Path, data: &[u8]) {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .unwrap();
    file.write_all(data).unwrap();
}
impl OpenSsh {
    pub async fn new() -> Self {
        let dir =
            std::env::temp_dir().join(format!("eidolon-openssh-{:016x}", rand::random::<u64>()));
        std::fs::DirBuilder::new().mode(0o700).create(&dir).unwrap();
        let mut owner = Owner {
            child: None,
            dir: dir.clone(),
        };
        let host = PrivateKey::random(&mut rand::rng(), Algorithm::Ed25519).unwrap();
        let identity = PrivateKey::random(&mut rand::rng(), Algorithm::Ed25519).unwrap();
        write(
            &dir.join("host"),
            host.to_openssh(Default::default()).unwrap().as_bytes(),
        );
        write(
            &dir.join("authorized_keys"),
            identity.public_key().to_openssh().unwrap().as_bytes(),
        );
        let socket = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let endpoint = socket.local_addr().unwrap();
        drop(socket);
        let user = Command::new("id").arg("-un").output().unwrap();
        assert!(user.status.success());
        let user = std::str::from_utf8(&user.stdout).unwrap().trim();
        assert!(
            user.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        );
        // /tmp上の0700 fixtureで合成鍵だけを使う。製品sshdのStrictModesは変更しない。
        write(&dir.join("config"),format!("Port {}\nListenAddress 127.0.0.1\nHostKey {}/host\nAuthorizedKeysFile {}/authorized_keys\nPidFile {}/pid\nStrictModes no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nAuthenticationMethods publickey\nUsePAM no\nPermitRootLogin yes\nAllowUsers {}\nAllowStreamLocalForwarding yes\nAllowTcpForwarding yes\nStreamLocalBindMask 0177\nStreamLocalBindUnlink no\nLogLevel VERBOSE\n",endpoint.port(),dir.display(),dir.display(),dir.display(),user).as_bytes());
        let log = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(dir.join("log"))
            .unwrap();
        let binary =
            std::env::var_os("EIDOLON_TEST_SSHD").unwrap_or_else(|| "/usr/sbin/sshd".into());
        let child = Command::new(binary)
            .args(["-D", "-e", "-f"])
            .arg(dir.join("config"))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(log)
            .spawn()
            .expect("host transport fixtureにはopenssh-serverが必要です");
        owner.child = Some(child);
        let deadline = tokio::time::Instant::now() + Duration::from_secs(4);
        loop {
            if tokio::net::TcpStream::connect(endpoint).await.is_ok() {
                break;
            }
            if let Some(status) = owner.child.as_mut().unwrap().try_wait().unwrap() {
                panic!(
                    "fixture sshd exit {status}: {}",
                    std::fs::read_to_string(dir.join("log")).unwrap()
                );
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "fixture sshd startup timeout"
            );
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        let router = Arc::new(Router::new(id(1)));
        let mut client = client::connect(
            Arc::new(crate::session::ssh_config()),
            endpoint,
            Peer {
                router: router.clone(),
                host: host.public_key().clone(),
            },
        )
        .await
        .unwrap();
        assert!(
            client
                .authenticate_publickey(user, PrivateKeyWithHashAlg::new(Arc::new(identity), None))
                .await
                .unwrap()
                .success(),
            "fixture auth failed: {}",
            std::fs::read_to_string(dir.join("log")).unwrap()
        );
        let remote = dir.join("r").to_str().unwrap().to_owned();
        Self {
            client,
            router,
            dir,
            remote,
            owner,
        }
    }
    pub async fn close(mut self) {
        self.client
            .disconnect(russh::Disconnect::ByApplication, "fixture complete", "en")
            .await
            .unwrap();
        let _ = tokio::time::timeout(Duration::from_secs(2), &mut self.client).await;
        let _ = &self.owner;
    }
    pub async fn open(&self) -> UnixStream {
        UnixStream::connect(&self.remote).await.unwrap()
    }
}
struct Owner {
    child: Option<Child>,
    dir: PathBuf,
}
impl Drop for Owner {
    fn drop(&mut self) {
        if let Some(child) = &mut self.child {
            let _ = child.kill();
            let _ = child.wait();
        }
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}
