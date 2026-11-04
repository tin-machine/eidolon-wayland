//! T03の固定echo。private session directory以外のsocketや任意execは受け付けない。
use crate::{
    forward::{self, Router},
    journal, launcher,
};
use eidolon_model::{commands::CleanupScenario, ids::SessionId};
use russh::client;
use serde::Serialize;
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

const SIZES: [u64; 6] = [1, 65536, 8388608, 8388608, 8388608, 8388608];
const TRAILER: &[u8] = b"AFTER_EOF";
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub state: &'static str,
    pub scenario: Option<CleanupScenario>,
    pub remote_not_started: bool,
    pub elapsed_ms: u64,
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
    pub remote: Option<launcher::Report>,
    pub pending_cleanup: Vec<journal::Pending>,
}
pub fn valid_nonce(nonce: &str) -> bool {
    nonce.len() == 16
        && nonce
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub(crate) struct Request<'a> {
    pub session: SessionId,
    pub files: &'a Path,
    pub nonce: &'a str,
    pub uid: u32,
    pub journal: &'a mut journal::Journal,
    pub binding: &'a str,
    pub scenario: Option<CleanupScenario>,
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
        journal,
        binding,
        scenario,
    } = request;
    let started = std::time::Instant::now();
    let mut report = Report {
        state: "running",
        scenario,
        uid: Some(uid),
        nonce: nonce.into(),
        pending_cleanup: journal.pending(),
        ..Default::default()
    };
    publish(&report);
    let mut local = None;
    let mut lease = None;
    let mut remote_owner = None;
    let mut registered = false;
    let echo_cancel = CancellationToken::new();
    let mut echoes = JoinSet::new();
    let remote_path = launcher::remote_path(nonce).unwrap_or_default();
    let result = async {
        if !valid_nonce(nonce) { return Err("INVALID_ARGUMENT"); }
        let mut owned = Directory::create(files, nonce)?;
        let listener = owned.listen()?;
        local = Some(owned);
        if scenario==Some(CleanupScenario::CancelBeforeExec) {
            let cancelled=CancellationToken::new();cancelled.cancel();
            match launcher::Launcher::open_recorded(client,nonce,uid,cancelled,||Err("UNEXPECTED_EXEC")).await {
                Err("CANCELLED")=>{report.remote_not_started=true;return Err("CANCELLED_BEFORE_EXEC");},
                _=>return Err("UNEXPECTED_EXEC"),
            }
        }
        remote_owner=Some(launcher::Launcher::open_recorded(client,nonce,uid,cancel.clone(),|| {
            journal.remember(journal::Entry{nonce:nonce.into(),session,uid,binding:binding.into(),child:None})?;
            report.pending_cleanup=journal.pending(); publish(&report); Ok(())
        }).await?);
        let owner=remote_owner.as_mut().unwrap();
        if scenario==Some(CleanupScenario::LeaseExpire) {owner.suppress_heartbeat();}

        loop {
            report.remote=Some(owner.next().await?);
            if let Some(child)=&owner.report.child {journal.child(nonce,child)?;}
            report.pending_cleanup=journal.pending();publish(&report);
            if owner.report.phase=="prepared" {break;}
            if owner.report.phase=="finished" {return Err("REMOTE_EXIT");}
        }
        if scenario==Some(CleanupScenario::PrepareEof) {
            owner.control_eof().await?;
            while owner.report.phase!="finished" {report.remote=Some(owner.next().await?);publish(&report);}
            if owner.report.cleanup_pending || owner.report.finished.as_ref().is_none_or(|r|r.reason!="EOF" || r.child.is_some()) {return Err("CLEANUP_FAILED");}
            return Ok(());
        }
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
                        if scenario.is_some() {
                            let stop=stop.clone();
                            tasks.spawn(async move {let _socket=socket;stop.cancelled().await;Ok::<_,&'static str>(())});
                        } else {tasks.spawn(async move {
                            let mut data=[0u8;65536];
                            loop { let n=socket.read(&mut data).await.map_err(|_| "LOCAL_ECHO_FAILED")?; if n==0 { break; } socket.write_all(&data[..n]).await.map_err(|_| "LOCAL_ECHO_FAILED")?; }
                            socket.write_all(TRAILER).await.map_err(|_| "LOCAL_ECHO_FAILED")?;
                            socket.shutdown().await.map_err(|_| "LOCAL_ECHO_FAILED")?;
                            Ok::<_,&'static str>(())
                        });}
                    }
                }
            }
            tasks.abort_all(); while tasks.join_next().await.is_some() {}
            Ok(())
        });
        timeout(Duration::from_secs(5),client.streamlocal_forward(remote_path.clone())).await.map_err(|_| "FORWARD_TIMEOUT")?.map_err(|_| "FORWARD_REJECTED")?;
        registered = true;
        owner.start_echo().await?;
        let mut fault_applied=false;
        while owner.report.phase!="finished" {
            report.remote=Some(owner.next().await?);
            if let Some(child)=&owner.report.child {journal.child(nonce,child)?;}
            report.pending_cleanup=journal.pending();publish(&report);
            if owner.report.phase=="running" && !fault_applied && matches!(scenario,Some(CleanupScenario::ControlEof|CleanupScenario::SshDrop)) {
                let limit=tokio::time::Instant::now()+Duration::from_secs(2);
                while router.report().peak==0 {
                    if tokio::time::Instant::now()>=limit {return Err("FORWARD_TIMEOUT");}
                    tokio::select! {biased;_=cancel.cancelled()=>return Err("CANCELLED"),_=tokio::time::sleep(Duration::from_millis(2))=>()}
                }
                fault_applied=true;
                if scenario==Some(CleanupScenario::SshDrop) {
                    let _=timeout(Duration::from_secs(2),client.disconnect(russh::Disconnect::ByApplication,"fixed SSH loss fixture","en")).await;
                    return Err("SSH_DROPPED");
                }
                owner.control_eof().await?;
            }
        }
        if cancel.is_cancelled() { return Err("CANCELLED"); }
        if let Some(scenario)=scenario {
            let finished=owner.report.finished.as_ref().ok_or("PROTOCOL_ERROR")?;
            let reason=match scenario {CleanupScenario::ControlEof=>"EOF",CleanupScenario::LeaseExpire=>"LeaseExpired",_=>return Err("UNEXPECTED_COMPLETION")};
            if finished.reason!=reason || owner.report.cleanup_pending || finished.child.is_none() {return Err("CLEANUP_FAILED");}
            return Ok(());
        }
        let finished=owner.report.finished.as_ref().ok_or("PROTOCOL_ERROR")?;
        if owner.report.cleanup_pending || finished.reason!="Completed" || finished.exit_status!=Some(0) || finished.exit_signal.is_some() {return Err("REMOTE_EXIT");}
        let echo=finished.echo.as_ref().ok_or("PROTOCOL_ERROR")?;
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
    if let Some(owner) = remote_owner.as_mut() {
        if owner.report.phase != "finished" {
            let _ = owner.request_stop().await;
            while owner.report.phase != "finished" {
                match owner.next().await {
                    Ok(value) => {
                        report.remote = Some(value);
                        publish(&report);
                    }
                    Err(_) => break,
                }
            }
        }
        report.remote_cleaned = owner.report.phase == "finished" && !owner.report.cleanup_pending;
        report.remote = Some(owner.report.clone());
    }
    if let Some(directory) = local {
        report.local_cleaned = directory.clean();
    }
    report.forwarding = router.report();
    report.error = result.err();
    if report.error == Some("CANCELLED_BEFORE_EXEC") && report.remote_not_started {
        report.error = None;
    }
    if report.remote_cleaned
        && let Err(error) = journal.forget(nonce)
    {
        report.error = Some(error);
    }
    report.pending_cleanup = journal.pending();
    if report.error.is_none()
        && ((registered && !report.forward_cancelled)
            || !report.local_cleaned
            || (!report.remote_cleaned && !report.remote_not_started)
            || report.forwarding.active != 0
            || (scenario.is_none() && report.forwarding.failed != 0))
    {
        report.error = Some("CLEANUP_FAILED");
    }
    report.elapsed_ms = started.elapsed().as_millis().min(u64::MAX as u128) as u64;
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
        assert!(launcher::command("shell", &"a".repeat(16)).is_err());
        assert!(launcher::command("echo", "../bad").is_err());
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
