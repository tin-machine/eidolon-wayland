//! B2 transport boundary. B1 remains the sole owner of values and hash inputs.
//! A captured value or catalog is not source admission, launch, or presentation.
use eidolon_model::{
    connection_profile::{Backend, Profile},
    ids::SessionId,
};
use serde_json::{Value, json};
use std::collections::BTreeMap;

use eidolon_model::linux_desktop_v3::{self as contract, StorageCapture, StorageRecord};
pub use eidolon_model::linux_desktop_v3::{Context, Manifest, Pins, REVISION, Selection};

pub mod protocol;
pub use protocol::{Action, Event, FrameDecoder, Phase, Protocol};
pub mod receipt;
pub use receipt::Receipt;
pub mod io_fence;
pub mod journal;
pub mod owner;
pub mod reclamation;
pub mod recovery;
pub mod runtime;
pub mod sources;

pub type Result<T> = std::result::Result<T, &'static str>;

#[derive(Clone)]
pub struct Binding {
    context: Context,
    pins: Pins,
}
impl std::fmt::Debug for Binding {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LinuxDesktopV3Binding(<private>)")
    }
}
impl Binding {
    /// The caller captures profile/session/UID on the same authenticated actor.
    /// Storage equality is necessary; it is not proof that persistence succeeded.
    pub fn capture(
        profile: &Profile,
        session: SessionId,
        uid: u32,
        nonce: &str,
        pins: Pins,
        storage: &StorageRecord,
    ) -> Result<Self> {
        let capture = StorageCapture::from_profile(profile).map_err(|e| e.0)?;
        storage.matches_capture(&capture).map_err(|e| e.0)?;
        let storage = storage.record();
        let context = Context::parse(&canonical(&json!({
            "schema":1,"backend":"linux_desktop","protocol_revision":REVISION,
            "session":session,"profile_revision":profile.revision(),"profile_binding":profile.binding(),
            "nonce":nonce,"uid":uid,"installation_id":storage["installation_id"],
            "profile_storage_id":storage["profile_storage_id"]
        }))?).map_err(|e| e.0)?;
        Self::from_captured(context, pins)
    }
    /// Reconstruct a validated, private journal capture; no remote authority.
    pub fn from_captured(context: Context, pins: Pins) -> Result<Self> {
        Ok(Self { context, pins })
    }
    pub fn context(&self) -> &Context {
        &self.context
    }
    pub fn pins(&self) -> &Pins {
        &self.pins
    }
    pub fn scope(&self) -> String {
        self.context.scope()
    }
    pub fn catalog_revision(&self) -> String {
        contract::catalog_revision(&self.context, &self.pins)
    }
    pub fn session(&self) -> SessionId {
        serde_json::from_value(self.context.record()["session"].clone()).expect("validated session")
    }
    pub fn nonce(&self) -> String {
        self.context.record()["nonce"].as_str().unwrap().into()
    }
    pub fn uid(&self) -> u32 {
        self.context.record()["uid"].as_u64().unwrap() as u32
    }
    pub fn matches_profile(&self, profile: &Profile, session: SessionId) -> bool {
        let c = self.context.record();
        profile.backend() == Backend::LinuxSsh
            && session == self.session()
            && c["profile_binding"] == profile.binding()
            && c["profile_revision"] == profile.revision()
    }
}

pub(crate) fn canonical(value: &Value) -> Result<Vec<u8>> {
    contract::canonical_json(value).map_err(|e| e.0)
}
pub(crate) fn fields<'a>(
    value: &'a Value,
    names: &[&str],
) -> Result<&'a serde_json::Map<String, Value>> {
    let value = value.as_object().ok_or("DESKTOP_V3_PROTOCOL_INVALID")?;
    if value.len() != names.len() || names.iter().any(|name| !value.contains_key(*name)) {
        return Err("DESKTOP_V3_PROTOCOL_INVALID");
    }
    Ok(value)
}
pub(crate) fn string(value: &Value) -> Result<&str> {
    value.as_str().ok_or("DESKTOP_V3_PROTOCOL_INVALID")
}
pub(crate) fn decimal(value: &Value, zero: bool) -> Result<u64> {
    let value = string(value)?;
    if zero && value == "0" {
        return Ok(0);
    }
    contract::Decimal::parse(value).map_err(|_| "DESKTOP_V3_PROTOCOL_SCOPE")?;
    value.parse().map_err(|_| "DESKTOP_V3_PROTOCOL_SCOPE")
}
pub(crate) fn valid_boot(value: &str) -> bool {
    super::protocol::valid_boot_id(value)
}
pub(crate) fn source_map(value: &Value) -> Result<BTreeMap<String, String>> {
    let value = value.as_object().ok_or("DESKTOP_V3_SOURCE_INVALID")?;
    if value.is_empty() || value.len() > 64 {
        return Err("DESKTOP_V3_SOURCE_INVALID");
    }
    value
        .iter()
        .map(|(name, sha)| {
            let sha = sha.as_str().ok_or("DESKTOP_V3_SOURCE_INVALID")?;
            if name.is_empty()
                || name.len() > 128
                || name.starts_with('/')
                || !name
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_./-".contains(&b))
                || name.split('/').any(|part| matches!(part, "" | "." | ".."))
                || !super::hex(sha, 64)
            {
                return Err("DESKTOP_V3_SOURCE_INVALID");
            }
            Ok((name.clone(), sha.into()))
        })
        .collect()
}

/// Expected immutable manifest/pins/source inputs. Capture/parse validate only
/// the shape and equality contract. Owner::open must also verify the compiled
/// complete closure; remote booleans alone never create a trusted catalog.
#[derive(Clone, PartialEq, Eq)]
pub struct ExpectedEvidence(Value);
impl std::fmt::Debug for ExpectedEvidence {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LinuxDesktopV3Evidence(<private>)")
    }
}
impl ExpectedEvidence {
    pub fn capture(
        pins: &Pins,
        manifest: &Manifest,
        source_hashes: &BTreeMap<String, String>,
    ) -> Result<Self> {
        manifest.matches_pins(pins).map_err(|e| e.0)?;
        Self::parse(&canonical(
            &json!({"pins":pins,"app_manifest":manifest,"source_hashes":source_hashes,
            "waypipe_version":"0.11.3","demo_verified":true,"package_verified":true,"settings_backend_verified":true}),
        )?)
    }
    pub fn parse(raw: &[u8]) -> Result<Self> {
        let value = contract::parse_json(raw, contract::MAX_RECORD_BYTES).map_err(|e| e.0)?;
        fields(
            &value,
            &[
                "pins",
                "app_manifest",
                "source_hashes",
                "waypipe_version",
                "demo_verified",
                "package_verified",
                "settings_backend_verified",
            ],
        )?;
        let pins = Pins::parse(&canonical(&value["pins"])?).map_err(|e| e.0)?;
        Manifest::parse(&canonical(&value["app_manifest"])?)
            .map_err(|e| e.0)?
            .matches_pins(&pins)
            .map_err(|e| e.0)?;
        source_map(&value["source_hashes"])?;
        if value["waypipe_version"] != "0.11.3"
            || [
                "demo_verified",
                "package_verified",
                "settings_backend_verified",
            ]
            .iter()
            .any(|name| value[*name] != Value::Bool(true))
        {
            return Err("DESKTOP_V3_EVIDENCE_STALE");
        }
        Ok(Self(value))
    }
    pub fn record(&self) -> Value {
        self.0.clone()
    }
    pub fn source_hashes(&self) -> BTreeMap<String, String> {
        source_map(&self.0["source_hashes"]).unwrap()
    }
    pub fn matches_binding(&self, binding: &Binding) -> Result<()> {
        if self.0["pins"] != binding.pins.record() {
            return Err("DESKTOP_V3_EVIDENCE_STALE");
        }
        Ok(())
    }
}

pub fn catalog_payload(binding: &Binding, evidence: &ExpectedEvidence) -> Result<Value> {
    evidence.matches_binding(binding)?;
    Ok(
        json!({"descriptor":contract::catalog_descriptor(binding.context(),binding.pins()),"evidence":evidence.record()}),
    )
}

#[cfg(test)]
mod tests;
