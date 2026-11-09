//! B1 isolated, pure v3 contracts. No runtime/SSH/builder/admission dispatch.
//! A valid value is not source attestation, installation or acceptance evidence.
use crate::ids::SessionId;
use serde::{Deserialize, Serialize, de};
use serde_json::{Value, json};
use std::fmt;

mod json_codec;
pub use json_codec::{canonical_json, digest, parse_json};

pub const REVISION: &str = "eidolon-linux-desktop-v3";
pub const MAX_RECORD_BYTES: usize = 16384;
pub const MAX_PINS_BYTES: usize = 4096;
pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Error(pub &'static str);
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for Error {}

fn require(value: bool, reason: &'static str) -> Result<()> {
    if value { Ok(()) } else { Err(Error(reason)) }
}

fn hex(value: &str, length: usize) -> bool {
    value.len() == length && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

macro_rules! hex_type {
    ($name:ident, $size:expr) => {
        #[derive(Clone, PartialEq, Eq, Serialize)]
        #[serde(transparent)]
        pub struct $name(String);
        impl $name {
            pub fn parse(value: &str) -> Result<Self> {
                require(hex(value, $size), "CONTRACT_INVALID")?;
                Ok(Self(value.to_owned()))
            }
            pub fn as_str(&self) -> &str { &self.0 }
        }
        impl fmt::Debug for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { f.write_str(concat!(stringify!($name), "(<private>)")) }
        }
        impl<'de> Deserialize<'de> for $name {
            fn deserialize<D: de::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
                Self::parse(&String::deserialize(d)?).map_err(de::Error::custom)
            }
        }
    };
}
hex_type!(Hash, 64);
hex_type!(Nonce, 16);
hex_type!(LegacyInstallationId, 32);
hex_type!(StorageInstallationId, 32);
hex_type!(ProfileStorageId, 32);

#[derive(Clone, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct Decimal(String);
impl Decimal {
    pub fn parse(value: &str) -> Result<Self> {
        require(!value.starts_with('0') && !value.is_empty() && value.bytes().all(|b| b.is_ascii_digit()) &&
            value.parse::<i64>().is_ok_and(|n| n > 0), "CONTRACT_INVALID")?;
        Ok(Self(value.into()))
    }
    pub fn as_str(&self) -> &str { &self.0 }
}
impl<'de> Deserialize<'de> for Decimal {
    fn deserialize<D: de::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        Self::parse(&String::deserialize(d)?).map_err(de::Error::custom)
    }
}

// Serde's externally tagged enum decoder also accepts {"variant":null}.
// Every wire enum in this contract is a JSON string, with no alternate shape.
macro_rules! string_enum {
    ($name:ident, {$($variant:ident => $wire:literal),* $(,)?}) => {
        impl<'de> Deserialize<'de> for $name {
            fn deserialize<D: de::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
                match String::deserialize(d)?.as_str() {
                    $($wire => Ok(Self::$variant),)*
                    _ => Err(de::Error::custom("unknown fixed string enum")),
                }
            }
        }
    };
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum AppId {
    #[serde(rename = "gtk3-desktop")]
    Demo,
    #[serde(rename = "mousepad-editor-v1")]
    Editor,
}
string_enum!(AppId, { Demo => "gtk3-desktop", Editor => "mousepad-editor-v1" });

fn decode<T: de::DeserializeOwned>(raw: &[u8], limit: usize) -> Result<T> {
    serde_json::from_value(parse_json(raw, limit)?).map_err(|_| Error("CONTRACT_INVALID"))
}
fn record<T: Serialize>(value: &T) -> Value {
    serde_json::to_value(value).expect("fixed contract types serialize")
}
// Named Serde structs also accept positional arrays by default. Restrict the
// wire to objects before invoking a private, deny_unknown_fields struct decoder.
fn object_only<'de, D, T>(d: D) -> std::result::Result<T, D::Error>
where D: de::Deserializer<'de>, T: Deserialize<'de> {
    struct Visitor<T>(std::marker::PhantomData<T>);
    impl<'de, T: Deserialize<'de>> de::Visitor<'de> for Visitor<T> {
        type Value = T;
        fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { f.write_str("a contract object") }
        fn visit_map<A: de::MapAccess<'de>>(self, map: A) -> std::result::Result<T, A::Error> {
            T::deserialize(de::value::MapAccessDeserializer::new(map))
        }
    }
    d.deserialize_map(Visitor(std::marker::PhantomData))
}

macro_rules! object_record {
    ($visibility:vis $name:ident, $wire:ident, {$($(#[$attribute:meta])* $field:ident: $ty:ty),* $(,)?}) => {
        #[derive(Clone, PartialEq, Eq, Serialize)]
        $visibility struct $name { $($field: $ty),* }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct $wire { $($(#[$attribute])* $field: $ty),* }
        impl<'de> Deserialize<'de> for $name {
            fn deserialize<D: de::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
                let wire: $wire = object_only(d)?;
                Ok(Self { $($field: wire.$field),* })
            }
        }
    };
}
macro_rules! record_methods {
    () => {
        pub fn record(&self) -> Value { record(self) }
        pub fn canonical(&self) -> Vec<u8> { canonical_json(&self.record()).expect("validated contract") }
        pub fn sha256(&self) -> String { digest(&self.record()).expect("validated contract") }
    };
}

// Wire decoding also validates when callers use Serde directly; the public
// nominal type cannot contain a wrong schema/revision through a second parser.
macro_rules! contract_record {
    ($name:ident, $wire:ident, {$($(#[$attribute:meta])* $field:ident: $ty:ty),* $(,)?}) => {
        #[derive(Clone, PartialEq, Eq, Serialize)]
        pub struct $name { $($field: $ty),* }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct $wire { $($(#[$attribute])* $field: $ty),* }
        impl<'de> Deserialize<'de> for $name {
            fn deserialize<D: de::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
                let wire: $wire = object_only(d)?;
                let value = Self { $($field: wire.$field),* };
                value.validate().map_err(de::Error::custom)?;
                Ok(value)
            }
        }
    };
}

contract_record!(Pins, PinsWire, {
    schema: u32,
    backend: String,
    protocol_revision: String,
    base_bundle_sha256: Hash,
    desktop_bundle_sha256: Hash,
    waypipe_sha256: Hash,
    shm_fixture_sha256: Hash,
    desktop_binary_sha256: Hash,
    app_manifest_sha256: Hash,
});
impl Pins {
    pub fn parse(raw: &[u8]) -> Result<Self> { decode(raw,MAX_PINS_BYTES) }
    fn validate(&self) -> Result<()> {
        let v = self;
        require(v.schema == 2 && v.backend == "linux_desktop" && v.protocol_revision == REVISION, "CONTRACT_INVALID")
    }
    record_methods!();
    pub fn build_input(&self) -> Value {
        json!({"protocol_revision":REVISION,"base_bundle_sha256":self.base_bundle_sha256,
            "desktop_bundle_sha256":self.desktop_bundle_sha256,"app_manifest_sha256":self.app_manifest_sha256})
    }
    pub fn build_id(&self) -> String { digest(&self.build_input()).expect("validated contract") }
}

contract_record!(Manifest, ManifestWire, {
    schema: u32,
    app_id: AppId,
    package: String,
    package_version: String,
    architecture: String,
    executable: String,
    executable_sha256: Hash,
    dependency_fingerprint: Hash,
    settings_kind: String,
    settings_policy: String,
    settings_module_sha256: Hash,
    settings_relative_path: String,
    window_app_ids: Vec<String>,
    launch_policy: String,
    state_policy: String,
});
impl Manifest {
    pub fn parse(raw: &[u8]) -> Result<Self> { decode(raw,MAX_RECORD_BYTES).map_err(|_| Error("APP_PACKAGE_UNSUPPORTED")) }
    fn validate(&self) -> Result<()> {
        let v = self;
        let ascii = |s: &str| !s.is_empty() && s.len() <= 128 && s.bytes().all(|b| (33..=126).contains(&b));
        require(v.schema == 1 && v.app_id == AppId::Editor && v.package == "mousepad" &&
            ascii(&v.package_version) && ascii(&v.architecture) && v.executable == "/usr/bin/mousepad" &&
            v.settings_kind == "eidolon-keyfile-v1" && v.settings_policy == "mousepad-single-main-no-restore-v1" &&
            v.settings_relative_path == "config/glib-2.0/settings/keyfile" && v.window_app_ids == ["mousepad"] &&
            v.launch_policy == "mousepad-private-bus-v1" && v.state_policy == "persistent-keyfile-locked-v1", "APP_PACKAGE_UNSUPPORTED")
    }
    record_methods!();
    pub fn matches_pins(&self, pins: &Pins) -> Result<()> {
        require(self.sha256() == pins.app_manifest_sha256.as_str(), "APP_DEPENDENCY_CHANGED")
    }
}

contract_record!(Context, ContextWire, {
    schema: u32,
    backend: String,
    protocol_revision: String,
    session: SessionId,
    profile_revision: Decimal,
    profile_binding: Hash,
    nonce: Nonce,
    uid: u32,
    installation_id: StorageInstallationId,
    profile_storage_id: ProfileStorageId,
});
impl Context {
    pub fn parse(raw: &[u8]) -> Result<Self> { decode(raw,MAX_RECORD_BYTES) }
    fn validate(&self) -> Result<()> {
        let v = self;
        require(v.schema == 1 && v.backend == "linux_desktop" && v.protocol_revision == REVISION && v.uid != 0, "CONTRACT_INVALID")
    }
    record_methods!();
    pub fn scope(&self) -> String { self.sha256() }
}

pub fn host_user_input(host_key_sha256: &Hash, username: &str) -> Result<Value> {
    require(!username.is_empty() && username.len() <= 64 && !username.starts_with('-') &&
        username.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-'), "CONTRACT_INVALID")?;
    Ok(json!({"domain":"eidolon-linux-storage-host-user-v1","host_key_sha256":host_key_sha256,"username":username}))
}
pub fn host_user_binding(host_key_sha256: &Hash, username: &str) -> Result<String> {
    digest(&host_user_input(host_key_sha256, username)?)
}

contract_record!(StorageCapture, StorageCaptureWire, {
    legacy_installation: LegacyInstallationId,
    profile_id: String,
    profile_binding: Hash,
    host_user_binding: Hash,
});
impl StorageCapture {
    pub fn parse(raw: &[u8]) -> Result<Self> { decode(raw,MAX_RECORD_BYTES) }
    fn validate(&self) -> Result<()> {
        let v = self;
        require(v.profile_id == "remote-linux", "CONTRACT_INVALID")
    }
    record_methods!();
    /// Existing profile codec and binding are unchanged. Caller captures actual storage separately.
    pub fn from_profile(profile: &crate::connection_profile::Profile) -> Result<Self> {
        require(profile.backend() == crate::connection_profile::Backend::LinuxSsh && profile.profile_id() == "remote-linux", "CONTRACT_INVALID")?;
        let key_hash = Hash::parse(&json_codec::hash_bytes(profile.host_key().as_bytes()))?;
        Self::parse(&canonical_json(&json!({"legacy_installation":profile.installation(),"profile_id":profile.profile_id(),
            "profile_binding":profile.binding(),"host_user_binding":host_user_binding(&key_hash,profile.username())?}))?)
    }
}

contract_record!(StorageRecord, StorageRecordWire, {
    schema: u32,
    legacy_installation: LegacyInstallationId,
    installation_id: StorageInstallationId,
    profile_id: String,
    profile_binding: Hash,
    profile_storage_id: ProfileStorageId,
    host_user_binding: Hash,
});
impl StorageRecord {
    pub fn parse(raw: &[u8]) -> Result<Self> { decode(raw,MAX_RECORD_BYTES) }
    fn validate(&self) -> Result<()> {
        let v = self;
        require(v.schema == 1 && v.profile_id == "remote-linux", "CONTRACT_INVALID")
    }
    record_methods!();
    pub fn matches_capture(&self, capture: &StorageCapture) -> Result<()> {
        require(self.legacy_installation == capture.legacy_installation && self.profile_id == capture.profile_id &&
            self.profile_binding == capture.profile_binding && self.host_user_binding == capture.host_user_binding, "STORAGE_STALE")
    }
    pub fn matches_context(&self, context: &Context) -> Result<()> {
        require(self.profile_binding == context.profile_binding && self.installation_id == context.installation_id &&
            self.profile_storage_id == context.profile_storage_id, "STORAGE_STALE")
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum StorageChange { Retain, Replace }
pub fn validate_storage_transition(old: &StorageRecord, new: &StorageRecord, expected_old_sha256: &Hash,
    capture: &StorageCapture, explicit_save: bool, fully_reclaimed: bool) -> Result<StorageChange> {
    require(explicit_save && fully_reclaimed, "RECOVERY_REQUIRED")?;
    require(old.sha256() == expected_old_sha256.as_str(), "STORAGE_STALE")?;
    new.matches_capture(capture)?;
    require(old.legacy_installation == new.legacy_installation && old.installation_id == new.installation_id &&
        old.profile_id == new.profile_id, "STORAGE_STALE")?;
    let retained = old.host_user_binding == new.host_user_binding;
    require((old.profile_storage_id == new.profile_storage_id) == retained, "STORAGE_STALE")?;
    Ok(if retained { StorageChange::Retain } else { StorageChange::Replace })
}

contract_record!(StorageOwner, StorageOwnerWire, {
    schema: u32,
    uid: u32,
    installation_id: StorageInstallationId,
    profile_storage_id: ProfileStorageId,
    app_id: AppId,
    state_policy: String,
});
impl StorageOwner {
    pub fn parse(raw: &[u8]) -> Result<Self> { decode(raw,MAX_RECORD_BYTES) }
    fn validate(&self) -> Result<()> {
        let v = self;
        require(v.schema == 1 && v.uid != 0 && v.app_id == AppId::Editor && v.state_policy == "persistent-keyfile-locked-v1", "CONTRACT_INVALID")
    }
    record_methods!();
    pub fn matches_context(&self, context: &Context) -> Result<()> {
        require(self.uid == context.uid && self.installation_id == context.installation_id &&
            self.profile_storage_id == context.profile_storage_id, "APP_STATE_INVALID")
    }
}

pub fn catalog_entries() -> Value {
    json!([{"id":"gtk3-desktop","label":"Linux アプリ","mode":"gui","icon":"builtin:script"},
        {"id":"mousepad-editor-v1","label":"Mousepad","mode":"gui","icon":"builtin:editor"}])
}
pub fn catalog_revision_input(context: &Context, pins: &Pins) -> Value {
    json!({"protocol_revision":REVISION,"scope":context.scope(),"pins":pins,"entries":catalog_entries()})
}
pub fn catalog_revision(context: &Context, pins: &Pins) -> String {
    digest(&catalog_revision_input(context,pins)).expect("validated contract")
}
/// Fixed descriptor; availability and source attestation are deliberately absent.
pub fn catalog_descriptor(context: &Context, pins: &Pins) -> Value {
    json!({"schema":1,"namespace":"linux-desktop","protocol_revision":REVISION,"scope":context.scope(),
        "revision":catalog_revision(context,pins),"entries":catalog_entries(),"arbitrary_exec":false,"terminal":false,"instance_limit":1})
}
pub fn validate_catalog(raw: &[u8], context: &Context, pins: &Pins) -> Result<Value> {
    let v = parse_json(raw,MAX_RECORD_BYTES)?;
    require(v == catalog_descriptor(context,pins), "CAPABILITY_STALE")?;
    Ok(v)
}

contract_record!(Selection, SelectionWire, { scope: Hash, revision: Hash, app_id: AppId });
impl Selection {
    fn validate(&self) -> Result<()> { Ok(()) }
    pub fn parse(raw: &[u8]) -> Result<Self> { decode(raw,MAX_RECORD_BYTES) }
    record_methods!();
    pub fn matches(&self, context: &Context, pins: &Pins) -> Result<()> {
        require(self.scope.as_str() == context.scope() && self.revision.as_str() == catalog_revision(context,pins), "CAPABILITY_STALE")
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum DecoderVersion { V3 }
impl DecoderVersion {
    pub fn selected(_pins: &Pins, remote_revision: &str) -> Result<Self> {
        require(remote_revision == REVISION,"PROTOCOL_VERSION")?;
        Ok(Self::V3)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Reason {
    None, AppNotInstalled, AppPackageUnsupported, AppDependencyChanged, AppBackendUnsupported,
    AppSettingsUnverified, AppOriginUnknown, AppWindowUnsupported, AppStateBusy, AppStateInvalid,
    AppStateRecoveryRequired, AppLaunchTimeout, AppPresentationTimeout, AppPresentationFailed,
    AppStartupCancelled, AppOutputRateLimit, AppV3NotImplemented, StateLimit, RecoveryRequired, CapabilityStale,
}
string_enum!(Reason, {
    None => "NONE", AppNotInstalled => "APP_NOT_INSTALLED", AppPackageUnsupported => "APP_PACKAGE_UNSUPPORTED",
    AppDependencyChanged => "APP_DEPENDENCY_CHANGED", AppBackendUnsupported => "APP_BACKEND_UNSUPPORTED",
    AppSettingsUnverified => "APP_SETTINGS_UNVERIFIED", AppOriginUnknown => "APP_ORIGIN_UNKNOWN",
    AppWindowUnsupported => "APP_WINDOW_UNSUPPORTED", AppStateBusy => "APP_STATE_BUSY", AppStateInvalid => "APP_STATE_INVALID",
    AppStateRecoveryRequired => "APP_STATE_RECOVERY_REQUIRED", AppLaunchTimeout => "APP_LAUNCH_TIMEOUT",
    AppPresentationTimeout => "APP_PRESENTATION_TIMEOUT", AppPresentationFailed => "APP_PRESENTATION_FAILED",
    AppStartupCancelled => "APP_STARTUP_CANCELLED", AppOutputRateLimit => "APP_OUTPUT_RATE_LIMIT",
    AppV3NotImplemented => "APP_V3_NOT_IMPLEMENTED", StateLimit => "STATE_LIMIT", RecoveryRequired => "RECOVERY_REQUIRED",
    CapabilityStale => "CAPABILITY_STALE",
});
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StopReason { Stop, LeaseExpired, InputEof, ProcessExited }
string_enum!(StopReason, { Stop => "stop", LeaseExpired => "lease_expired", InputEof => "input_eof", ProcessExited => "process_exited" });
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureReason { ProcessOwnership, PipeEof, DirectoryCleanup, ReceiptWrite, Protocol, ToolInspection, Start }
string_enum!(FailureReason, { ProcessOwnership => "process_ownership", PipeEof => "pipe_eof", DirectoryCleanup => "directory_cleanup",
    ReceiptWrite => "receipt_write", Protocol => "protocol", ToolInspection => "tool_inspection", Start => "start" });
pub fn parse_reason(raw: &[u8], kind: &str) -> Result<Value> {
    let v = parse_json(raw,MAX_RECORD_BYTES)?;
    let valid = match kind {
        "public" => serde_json::from_value::<Reason>(v.clone()).is_ok(),
        "stop" => serde_json::from_value::<StopReason>(v.clone()).is_ok(),
        "failure" => serde_json::from_value::<FailureReason>(v.clone()).is_ok(),
        _ => false,
    };
    require(valid,"PROTOCOL_REASON")?;
    Ok(v)
}

fn valid_boot(value: &str) -> bool {
    value == "-" || (value.len() == 36 && value.bytes().enumerate().all(|(i,b)| {
        if [8,13,18,23].contains(&i) { b == b'-' } else { b.is_ascii_digit() || (b'a'..=b'f').contains(&b) }
    }))
}
#[derive(Clone, PartialEq, Eq)]
pub enum Operation { Serve, Recover }
#[derive(Clone, PartialEq, Eq)]
pub struct Bootstrap { operation: Operation, context: Context, pins: Pins, expected_boot_id: Option<String> }
impl Bootstrap {
    pub fn serve(context: Context, pins: Pins) -> Self { Self { operation:Operation::Serve,context,pins,expected_boot_id:None } }
    pub fn recover(context: Context, pins: Pins, boot: &str) -> Result<Self> {
        require(valid_boot(boot),"CONTRACT_INVALID")?;
        Ok(Self { operation:Operation::Recover,context,pins,expected_boot_id:Some(boot.into()) })
    }
    pub fn vector(&self) -> Vec<String> {
        let c = &self.context;
        let p = &self.pins;
        let mut v = vec![if self.operation == Operation::Serve { "serve" } else { "recover" }.into(),
            c.session.get().to_string(),c.profile_revision.as_str().into(),c.profile_binding.as_str().into(),c.nonce.as_str().into(),c.uid.to_string(),
            p.base_bundle_sha256.as_str().into(),p.desktop_bundle_sha256.as_str().into(),p.waypipe_sha256.as_str().into(),
            p.shm_fixture_sha256.as_str().into(),p.desktop_binary_sha256.as_str().into(),p.app_manifest_sha256.as_str().into(),
            c.installation_id.as_str().into(),c.profile_storage_id.as_str().into()];
        if let Some(boot) = &self.expected_boot_id { v.push(boot.clone()); }
        v
    }
    pub fn parse(raw: &[u8]) -> Result<Self> {
        let a: Vec<String> = decode(raw,MAX_RECORD_BYTES)?;
        require((a.len() == 14 && a[0] == "serve") || (a.len() == 15 && a[0] == "recover"),"CONTRACT_INVALID")?;
        Decimal::parse(&a[5])?;
        let uid = a[5].parse::<u32>().map_err(|_| Error("CONTRACT_INVALID"))?;
        let context = Context::parse(&canonical_json(&json!({"schema":1,"backend":"linux_desktop","protocol_revision":REVISION,
            "session":a[1],"profile_revision":a[2],"profile_binding":a[3],"nonce":a[4],"uid":uid,"installation_id":a[12],"profile_storage_id":a[13]}))?)?;
        let pins = Pins::parse(&canonical_json(&json!({"schema":2,"backend":"linux_desktop","protocol_revision":REVISION,
            "base_bundle_sha256":a[6],"desktop_bundle_sha256":a[7],"waypipe_sha256":a[8],"shm_fixture_sha256":a[9],
            "desktop_binary_sha256":a[10],"app_manifest_sha256":a[11]}))?)?;
        if a[0] == "serve" { Ok(Self::serve(context,pins)) } else { Self::recover(context,pins,&a[14]) }
    }
    pub fn matches(&self, context: &Context, pins: &Pins) -> Result<()> {
        require(&self.context == context && &self.pins == pins,"CONTEXT_STALE")
    }
}

pub mod presentation;

#[cfg(test)]
mod tests;
