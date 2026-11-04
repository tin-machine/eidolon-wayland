//! T03の固定echo。private session directory以外のsocketや任意execは受け付けない。
use crate::{
    exec,
    forward::{self, Router},
};
use eidolon_model::ids::SessionId;
use russh::client;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    os::unix::fs::{DirBuilderExt, MetadataExt},
    path::{Path, PathBuf},
    sync::Arc,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::UnixListener,
    task::JoinSet,
    time::{Duration, timeout},
};
use tokio_util::sync::CancellationToken;

const HOME: &str = "/data/data/com.termux/files/home";
const LAUNCHER: &str = "/data/data/com.termux/files/home/.local/lib/eidolon/eidolon-session.py";
const PYTHON: &str = "/data/data/com.termux/files/usr/bin/python3";
const SCRIPT: &[u8] = include_bytes!("../../../scripts/termux/eidolon-session.py");
const SIZES: [u64; 6] = [1, 65536, 8388608, 8388608, 8388608, 8388608];
const TRAILER: &[u8] = b"AFTER_EOF";
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub state: &'static str,
    pub nonce: String,
    pub uid: Option<u32>,
    pub channels: usize,
    pub bytes: u64,
    pub hashes_matched: bool,
    pub half_close: bool,
    pub forward_cancelled: bool,
    pub local_cleaned: bool,
    pub remote_cleaned: bool,
    pub error: Option<&'static str>,
    pub forwarding: forward::Report,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Prepared {
    schema: u32,
    socket: String,
    launcher_sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Copy {
    size: u64,
    bytes: u64,
    sha256: String,
    tail_matched: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Echo {
    schema: u32,
    result: String,
    uid: u32,
    channels: usize,
    bytes: u64,
    copies: Vec<Copy>,
}
pub fn valid_nonce(nonce: &str) -> bool {
    nonce.len() == 16
        && nonce
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn command(mode: &str, nonce: &str) -> Result<String, &'static str> {
    if !matches!(mode, "prepare" | "echo" | "cleanup") || !valid_nonce(nonce) {
        return Err("INVALID_ARGUMENT");
    }
    // すべて固定path・固定mode・16桁hex。ユーザー由来のshell文字列を連結しない。
    Ok(format!("'{PYTHON}' '{LAUNCHER}' '{mode}' '{nonce}'"))
}
async fn remote<H: client::Handler>(
    client: &client::Handle<H>,
    mode: &str,
    nonce: &str,
    deadline: u64,
) -> Result<serde_json::Value, &'static str> {
    timeout(Duration::from_secs(deadline), async {
        exec::json(
            client
                .channel_open_session()
                .await
                .map_err(|_| "SSH_IO_FAILED")?,
            command(mode, nonce)?,
        )
        .await
    })
    .await
    .map_err(|_| "EXEC_TIMEOUT")?
}
pub(crate) struct Request<'a> {
    pub session: SessionId,
    pub files: &'a Path,
    pub nonce: &'a str,
    pub uid: u32,
}

pub(crate) async fn run<H: client::Handler>(
    client: &client::Handle<H>,
    router: &Arc<Router>,
    request: Request<'_>,
    cancel: &CancellationToken,
    publish: impl Fn(&Report),
) -> Report {
    let Request {
        session,
        files,
        nonce,
        uid,
    } = request;
    let mut report = Report {
        state: "running",
        nonce: nonce.into(),
        ..Default::default()
    };
    publish(&report);
    let mut local = None;
    let mut lease = None;
    let mut prepared = false;
    let mut registered = false;
    let echo_cancel = CancellationToken::new();
    let mut echoes = JoinSet::new();
    let remote_path = format!("{HOME}/.eidolon/s-{nonce}/p");
    let result = async {
        if !valid_nonce(nonce) { return Err("INVALID_ARGUMENT"); }
        let mut owned = Directory::create(files, nonce)?;
        let listener = owned.listen()?;
        local = Some(owned);
        let value = remote(client,"prepare",nonce,5).await?;
        let value: Prepared = serde_json::from_value(value).map_err(|_| "PROTOCOL_ERROR")?;
        if value.schema != 1 || value.socket != remote_path || value.launcher_sha256 != hex(Sha256::digest(SCRIPT)) { return Err("PROTOCOL_ERROR"); }
        // exclusive prepareの成功ackがないnonceは回収対象にしない。
        prepared = true;
        lease = Some(router.arm(session, remote_path.clone(), local.as_ref().unwrap().path.join("p"))?);
        // listenerはforward登録前にlisten済み。echo側も最大8task、各64KiBだけを保持する。
        let stop=echo_cancel.clone();
        echoes.spawn(async move {
            let mut tasks=JoinSet::new();
            loop {
                tokio::select! {
                    biased;
                    _=stop.cancelled()=>break,
                    result=tasks.join_next(),if !tasks.is_empty()=> { if !matches!(result,Some(Ok(Ok(())))) { return Err("LOCAL_ECHO_FAILED"); } },
                    accepted=listener.accept(),if tasks.len()<forward::MAX_CHANNELS=> {
                        let (mut socket,_)=accepted.map_err(|_| "LOCAL_ECHO_FAILED")?;
                        tasks.spawn(async move {
                            let mut data=[0u8;65536];
                            loop { let n=socket.read(&mut data).await.map_err(|_| "LOCAL_ECHO_FAILED")?; if n==0 { break; } socket.write_all(&data[..n]).await.map_err(|_| "LOCAL_ECHO_FAILED")?; }
                            socket.write_all(TRAILER).await.map_err(|_| "LOCAL_ECHO_FAILED")?;
                            socket.shutdown().await.map_err(|_| "LOCAL_ECHO_FAILED")?;
                            Ok::<_,&'static str>(())
                        });
                    }
                }
            }
            tasks.abort_all(); while tasks.join_next().await.is_some() {}
            Ok(())
        });
        timeout(Duration::from_secs(5),client.streamlocal_forward(remote_path.clone())).await.map_err(|_| "FORWARD_TIMEOUT")?.map_err(|_| "FORWARD_REJECTED")?;
        registered = true;
        let value = tokio::select! { biased; _=cancel.cancelled()=>return Err("CANCELLED"), value=remote(client,"echo",nonce,60)=>value? };
        let echo: Echo=serde_json::from_value(value).map_err(|_| "PROTOCOL_ERROR")?;
        if echo.schema!=1 || echo.result!="pass" || echo.uid!=uid || echo.channels!=6 || echo.bytes!=SIZES.iter().sum::<u64>() || echo.copies.len()!=6 { return Err("ECHO_MISMATCH"); }
        for (copy,size) in echo.copies.iter().zip(SIZES) {
            if copy.size!=size || copy.bytes!=size || !copy.tail_matched || copy.sha256!=hash(size) { return Err("ECHO_MISMATCH"); }
        }
        // Remote EOFの直後にlocal taskが終了するまで短く待つ。
        timeout(Duration::from_secs(2),async { while router.report().active!=0 { tokio::time::sleep(Duration::from_millis(2)).await; } }).await.map_err(|_| "FORWARD_TIMEOUT")?;
        report.uid=Some(echo.uid); report.channels=6; report.bytes=echo.bytes; report.hashes_matched=true; report.half_close=true;
        Ok(())
    }.await;
    if registered {
        report.forward_cancelled = matches!(
            timeout(
                Duration::from_secs(3),
                client.cancel_streamlocal_forward(remote_path)
            )
            .await,
            Ok(Ok(()))
        );
    }
    if let Some(lease) = lease {
        lease.close().await;
    }
    echo_cancel.cancel();
    while echoes.join_next().await.is_some() {}
    if prepared {
        report.remote_cleaned = remote(client, "cleanup", nonce, 3)
            .await
            .is_ok_and(|v| v["schema"] == 1 && v["cleaned"] == true);
    }
    if let Some(directory) = local {
        report.local_cleaned = directory.clean();
    }
    report.forwarding = router.report();
    report.error = result.err();
    if report.error.is_none()
        && (!report.forward_cancelled
            || !report.local_cleaned
            || !report.remote_cleaned
            || report.forwarding.active != 0
            || report.forwarding.failed != 0)
    {
        report.error = Some("CLEANUP_FAILED");
    }
    report.state = if report.error.is_none() {
        "pass"
    } else {
        "fail"
    };
    publish(&report);
    report
}
fn hash(size: u64) -> String {
    let block: Vec<_> = (0..65536).map(|n| (n % 256) as u8).collect();
    let mut hash = Sha256::new();
    let mut left = size;
    while left != 0 {
        let n = left.min(block.len() as u64) as usize;
        hash.update(&block[..n]);
        left -= n as u64;
    }
    hex(hash.finalize())
}
struct Directory {
    path: PathBuf,
    identity: (u64, u64),
    socket: Option<(u64, u64)>,
}
impl Directory {
    fn create(files: &Path, nonce: &str) -> Result<Self, &'static str> {
        let parent = std::fs::symlink_metadata(files).map_err(|_| "LOCAL_DIRECTORY_FAILED")?;
        if !parent.is_dir() || !files.is_absolute() {
            return Err("LOCAL_DIRECTORY_FAILED");
        }
        let path = files.join(format!("f-{nonce}"));
        if path.join("p").as_os_str().len() > 103 {
            return Err("LOCAL_SOCKET_FAILED");
        }
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&path)
            .map_err(|_| "LOCAL_DIRECTORY_FAILED")?;
        let info = std::fs::symlink_metadata(&path).map_err(|_| "LOCAL_DIRECTORY_FAILED")?;
        Ok(Self {
            path,
            identity: (info.dev(), info.ino()),
            socket: None,
        })
    }
    fn listen(&mut self) -> Result<UnixListener, &'static str> {
        let listener =
            UnixListener::bind(self.path.join("p")).map_err(|_| "LOCAL_SOCKET_FAILED")?;
        let info =
            std::fs::symlink_metadata(self.path.join("p")).map_err(|_| "LOCAL_SOCKET_FAILED")?;
        self.socket = Some((info.dev(), info.ino()));
        Ok(listener)
    }
    fn clean(&self) -> bool {
        let info = match std::fs::symlink_metadata(&self.path) {
            Ok(info) => info,
            Err(error) => return error.kind() == std::io::ErrorKind::NotFound,
        };
        if !info.is_dir() || (info.dev(), info.ino()) != self.identity {
            return false;
        }
        let socket = self.path.join("p");
        if let Ok(info) = std::fs::symlink_metadata(&socket) {
            use std::os::unix::fs::FileTypeExt;
            if !info.file_type().is_socket() || self.socket != Some((info.dev(), info.ino())) {
                return false;
            }
            if std::fs::remove_file(socket).is_err() {
                return false;
            }
        }
        std::fs::remove_dir(&self.path).is_ok()
    }
}
impl Drop for Directory {
    fn drop(&mut self) {
        let _ = self.clean();
    }
}

fn hex(bytes: impl AsRef<[u8]>) -> String {
    bytes.as_ref().iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_command_nonce_and_exclusive_local_directory() {
        assert!(command("shell", &"a".repeat(16)).is_err());
        assert!(command("echo", "../bad").is_err());
        let nonce = format!("{:016x}", rand::random::<u64>());
        let root = std::env::temp_dir();
        let directory = Directory::create(&root, &nonce).unwrap();
        assert!(Directory::create(&root, &nonce).is_err());
        assert_eq!(
            std::fs::metadata(&directory.path).unwrap().mode() & 0o777,
            0o700
        );
        assert!(directory.clean());
        assert_eq!(hash(1), hex(Sha256::digest([0u8])));
    }
}
