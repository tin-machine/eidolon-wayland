//! NX18固定Linux SHM専用。通常GUI/Termux Launcher/Journalとは独立しdebug featureだけで公開。
mod journal;
mod owner;
mod protocol;
mod recovery;
pub use recovery::{Recovery, RecoveryReport, recover};

use crate::forward;
use eidolon_model::ids::SessionId;
use russh::client;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    os::unix::fs::{FileTypeExt, MetadataExt},
    path::Path,
    sync::Arc,
};
use tokio::time::{Duration, timeout};
use tokio_util::sync::CancellationToken;

const SOURCE: &[u8] = include_bytes!("../../../../scripts/linux/eidolon-linux-session.py");
const REVISION: &str = "eidolon-linux-shm-v1";
fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
fn hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Private configだけ。通常snapshotへSerializeせず、Debugで値を出さない。
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Pins {
    schema: u32,
    bundle_sha256: String,
    waypipe_sha256: String,
    fixture_sha256: String,
}
impl std::fmt::Debug for Pins {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LinuxPins(<private>)")
    }
}
impl Pins {
    pub fn parse(json: &str) -> Result<Self, &'static str> {
        if json.len() > 4096 {
            return Err("LINUX_PIN_INVALID");
        }
        let value: Self = serde_json::from_str(json).map_err(|_| "LINUX_PIN_INVALID")?;
        if !value.valid() {
            return Err("LINUX_PIN_INVALID");
        }
        Ok(value)
    }
    pub fn helper_sha256(&self) -> String {
        hash(SOURCE)
    }
    fn valid(&self) -> bool {
        self.schema == 1
            && hex(&self.bundle_sha256, 64)
            && hex(&self.waypipe_sha256, 64)
            && hex(&self.fixture_sha256, 64)
    }
}

/// 同じSSH接続のimmutable v2 identityをownerが渡す。別connectionへ再利用しない。
pub struct Request<'a> {
    pub session: SessionId,
    pub profile_binding: &'a str,
    pub profile_revision: &'a str,
    pub files: &'a Path,
    pub local_socket: &'a Path,
    pub nonce: &'a str,
    pub uid: u32,
    pub pins: &'a Pins,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub state: &'static str,
    pub session: Option<SessionId>,
    pub nonce: String,
    pub uid: u32,
    pub tools_verified: bool,
    pub forward_requested: bool,
    pub registered: bool,
    pub forward_cancelled: bool,
    pub remote_cleaned: bool,
    pub channel_closed: bool,
    pub journal_pending: bool,
    pub pattern_verified: bool,
    pub frames: u32,
    pub cancelled: bool,
    pub elapsed_ms: u64,
    pub error: Option<&'static str>,
    pub forwarding: forward::Report,
    pub remote: protocol::Report,
}

/// 認証済みの同じSSH handleとRouterだけを使う。終了後も通常capabilityはProbeReadyのまま。
pub async fn run<H: client::Handler>(
    client: &client::Handle<H>,
    router: &Arc<forward::Router>,
    request: Request<'_>,
    cancel: &CancellationToken,
    publish: impl Fn(&Report),
) -> Report {
    let started = std::time::Instant::now();
    let mut report = Report {
        state: "preparing",
        session: Some(request.session),
        nonce: request.nonce.into(),
        uid: request.uid,
        ..Default::default()
    };
    let mut journal = None;
    let mut owner = None;
    let mut lease = None;
    let mut remote_path = None;
    let mut result = async {
        if !crate::forward_probe::valid_nonce(request.nonce)
            || request.uid == 0
            || !hex(request.profile_binding, 64)
            || request.profile_revision.parse::<u64>().is_err()
            || request.profile_revision.starts_with('0')
            || !request.profile_revision.bytes().all(|b| b.is_ascii_digit())
            || !request.pins.valid()
            || !request.local_socket.is_absolute()
            || request.local_socket.as_os_str().len() > 103
        {
            return Err("INVALID_ARGUMENT");
        }
        let info =
            std::fs::symlink_metadata(request.local_socket).map_err(|_| "LOCAL_SOCKET_FAILED")?;
        if !info.file_type().is_socket() || info.uid() != unsafe { libc::getuid() } {
            return Err("LOCAL_SOCKET_FAILED");
        }
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        owner = Some(
            owner::Owner::open(client, &request, cancel.clone(), || {
                // 既存/破損記録で拒否した場合も、新owner未取得をpending=falseへ読み替えない。
                report.journal_pending = true;
                journal = Some(journal::Journal::reserve(&request)?);
                publish(&report);
                Ok(())
            })
            .await?,
        );
        let active = owner.as_mut().unwrap();
        while !active.prepared() {
            active.next().await?;
            report.remote = active.report();
            publish(&report);
        }
        active.inspect().await?;
        while !active.tools_ready() {
            active.next().await?;
            report.remote = active.report();
            publish(&report);
        }
        report.tools_verified = true;
        report.state = "tools_verified";
        publish(&report);
        let path = active
            .remote_socket()
            .ok_or("LINUX_PROTOCOL_ERROR")?
            .to_owned();
        lease = Some(router.arm(request.session, path.clone(), request.local_socket.into())?);
        // 登録要求が送信された後のtimeoutはACK不明。成功boolと取消試行を別に保つ。
        remote_path = Some(path.clone());
        report.forward_requested = true;
        timeout(Duration::from_secs(5), client.streamlocal_forward(path))
            .await
            .map_err(|_| "FORWARD_TIMEOUT")?
            .map_err(|_| "FORWARD_REJECTED")?;
        report.registered = true;
        report.state = "forwarded";
        publish(&report);
        active.start().await?;
        while !active.closed() {
            active.next().await?;
            report.remote = active.report();
            if active.running() {
                report.state = "running";
            }
            publish(&report);
        }
        if cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        if !active.pattern_pass() {
            return Err("LINUX_PATTERN_FAILED");
        }
        Ok(())
    }
    .await;
    if let Some(active) = owner.as_mut() {
        if !active.closed() {
            let _ = active.stop().await;
            while !active.closed() {
                if active.next().await.is_err() {
                    break;
                }
            }
        }
        report.remote = active.report();
        report.remote_cleaned = active.cleaned();
        report.pattern_verified = active.pattern_pass();
        report.frames = if report.pattern_verified { 100 } else { 0 };
        report.channel_closed = active.close().await;
    }
    if result.is_ok()
        && timeout(Duration::from_secs(2), async {
            while router.report().active != 0 {
                tokio::time::sleep(Duration::from_millis(2)).await;
            }
        })
        .await
        .is_err()
    {
        result = Err("LINUX_FORWARD_DRAIN_TIMEOUT");
    }
    if let Some(path) = remote_path {
        report.forward_cancelled = matches!(
            timeout(
                Duration::from_secs(3),
                client.cancel_streamlocal_forward(path)
            )
            .await,
            Ok(Ok(()))
        );
    }
    if let Some(lease) = lease {
        lease.close().await;
    }
    report.forwarding = router.report();
    report.cancelled = cancel.is_cancelled();
    report.error = result.err();
    if report.remote_cleaned
        && report.channel_closed
        && (!report.forward_requested || report.forward_cancelled)
        && report.forwarding.active == 0
        && let Some(journal) = journal.as_mut()
    {
        match journal.clear() {
            Ok(()) => report.journal_pending = false,
            Err(error) => report.error = Some(error),
        }
    }
    if report.error.is_none()
        && (!report.remote_cleaned
            || !report.channel_closed
            || !report.forward_cancelled
            || report.journal_pending
            || report.forwarding.active != 0
            || report.forwarding.failed != 0)
    {
        report.error = Some("LINUX_CLEANUP_PENDING");
    }
    if report.error.is_none()
        && (report.forwarding.accepted == 0
            || report.forwarding.remote_to_local == 0
            || report.forwarding.local_to_remote == 0)
    {
        report.error = Some("LINUX_FORWARD_NO_DATA");
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

#[cfg(test)]
mod tests;
