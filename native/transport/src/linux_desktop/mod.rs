//! 通常 Linux desktop の固定 capability と SSH owner。
//! 同じ認証済み接続の binding/router を使い、Host/UI への公開は呼出側が行う。
//! Profile の保存・認証成功だけでは catalog / GUIReady を付与しない。
use eidolon_model::{
    connection_profile::{Backend, Profile},
    ids::SessionId,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

mod protocol;
pub use protocol::{Action, Event, Phase, Protocol};
mod journal;
mod owner;
mod recovery;
mod runtime;
pub use owner::RemoteReport;
pub use recovery::{Recovery, RecoveryReport, recover};
pub use runtime::{Report, Request, run};

pub const REVISION: &str = "eidolon-linux-desktop-v2";
pub const APP_ID: &str = "gtk3-desktop";
const BASE_SOURCE: &[u8] = include_bytes!("../../../../scripts/linux/eidolon-linux-session.py");
const SOURCE: &[u8] = include_bytes!("../../../../scripts/linux/eidolon_linux_desktop.py");
const OWNER_SOURCE: &[u8] =
    include_bytes!("../../../../scripts/linux/eidolon_linux_process_owner.py");
const DEMO_SOURCE: &[u8] = include_bytes!("../../../../scripts/fixtures/gtk3-desktop-demo.c");
const WIRE_SOURCE: &[u8] =
    include_bytes!("../../../../scripts/linux/eidolon_linux_desktop_session.py");

fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}
fn hex(value: &str, size: usize) -> bool {
    value.len() == size
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn decimal(value: &str, zero: bool) -> Option<u64> {
    if zero && value == "0" {
        return Some(0);
    }
    (!value.starts_with('0') && value.bytes().all(|b| b.is_ascii_digit()))
        .then(|| value.parse::<i64>().ok())
        .flatten()
        .filter(|n| *n > 0)
        .map(|n| n as u64)
}
fn boot_identity(value: &str) -> bool {
    protocol::valid_boot_id(value)
}

/// Config 専用。既存 B4/B6 pins と互換扱いしない。通常 diagnostics へ Serialize しない。
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Pins {
    schema: u32,
    backend: String,
    base_bundle_sha256: String,
    desktop_bundle_sha256: String,
    waypipe_sha256: String,
    shm_fixture_sha256: String,
    desktop_binary_sha256: String,
}
impl std::fmt::Debug for Pins {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LinuxDesktopPins(<private>)")
    }
}
impl Pins {
    pub fn parse(raw: &str) -> Result<Self, &'static str> {
        if raw.len() > 4096 {
            return Err("DESKTOP_PIN_INVALID");
        }
        let value: Self = serde_json::from_str(raw).map_err(|_| "DESKTOP_PIN_INVALID")?;
        if value.schema != 1
            || value.backend != "linux_desktop"
            || [
                &value.base_bundle_sha256,
                &value.desktop_bundle_sha256,
                &value.waypipe_sha256,
                &value.shm_fixture_sha256,
                &value.desktop_binary_sha256,
            ]
            .iter()
            .any(|s| !hex(s, 64))
        {
            return Err("DESKTOP_PIN_INVALID");
        }
        Ok(value)
    }
    pub fn source_hashes(&self) -> SourceHashes {
        SourceHashes {
            base: hash(BASE_SOURCE),
            adapter: hash(SOURCE),
            owner: hash(OWNER_SOURCE),
            demo: hash(DEMO_SOURCE),
            wire: hash(WIRE_SOURCE),
        }
    }
}
#[derive(Clone, PartialEq, Eq)]
pub struct SourceHashes {
    pub base: String,
    pub adapter: String,
    pub owner: String,
    pub demo: String,
    pub wire: String,
}
impl std::fmt::Debug for SourceHashes {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LinuxDesktopSources(<private>)")
    }
}

/// 認証済み actor が同 Profile + session + remote UID を capture して構築する。
/// この型自体は SSH 認証 / 実行許可の証明ではない。
#[derive(Clone)]
pub struct Binding {
    session: SessionId,
    profile_revision: String,
    profile_binding: String,
    nonce: String,
    uid: u32,
    scope: String,
    catalog_revision: String,
    pins: Pins,
}
impl std::fmt::Debug for Binding {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LinuxDesktopBinding(<private>)")
    }
}
impl Binding {
    pub fn capture(
        profile: &Profile,
        session: SessionId,
        uid: u32,
        nonce: &str,
        pins: Pins,
    ) -> Result<Self, &'static str> {
        if profile.backend() != Backend::LinuxSsh || uid == 0 || !hex(nonce, 16) {
            return Err("DESKTOP_BINDING_INVALID");
        }
        let profile_binding = profile.binding();
        let scope = hash(serde_json::to_string(&serde_json::json!({
            "session":session, "revision":profile.revision(), "binding":profile_binding, "nonce":nonce
        })).unwrap().as_bytes());
        let catalog_revision = hash(
            serde_json::to_string(&serde_json::json!({
                "scope":scope,"bundle":pins.desktop_bundle_sha256,"app":APP_ID
            }))
            .unwrap()
            .as_bytes(),
        );
        Ok(Self {
            session,
            profile_revision: profile.revision().into(),
            profile_binding,
            nonce: nonce.into(),
            uid,
            scope,
            catalog_revision,
            pins,
        })
    }
    pub fn matches_profile(&self, profile: &Profile, session: SessionId) -> bool {
        session == self.session
            && profile.backend() == Backend::LinuxSsh
            && profile.binding() == self.profile_binding
            && profile.revision() == self.profile_revision
    }
    pub fn session(&self) -> SessionId {
        self.session
    }
}

/// 一度検証した catalog だけから作る選択。UI の任意 JSON / argv は受けない。
#[derive(Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    scope: String,
    revision: String,
    app_id: String,
}
impl std::fmt::Debug for Selection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LinuxDesktopSelection(<private>)")
    }
}

#[cfg(test)]
mod tests;

#[cfg(test)]
mod owner_tcp_tests;
#[cfg(test)]
mod test_support;
