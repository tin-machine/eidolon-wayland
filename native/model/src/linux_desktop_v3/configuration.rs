//! Local, statically verified bundle configuration; never remote execution authority.
use super::*;

pub const MAX_CONFIG_BYTES: usize = 32768;
contract_record!(ConnectionConfig, ConnectionConfigWire, {
    schema: u32,
    kind: String,
    pins: Pins,
    app_manifest: Manifest,
});
impl ConnectionConfig {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        decode(raw, MAX_CONFIG_BYTES)
    }
    fn validate(&self) -> Result<()> {
        require(
            self.schema == 1 && self.kind == "linux-desktop-v3-connection",
            "CONTRACT_INVALID",
        )?;
        require(
            self.pins.canonical().len() <= MAX_PINS_BYTES
                && self.app_manifest.canonical().len() <= MAX_RECORD_BYTES,
            "CONTRACT_INVALID",
        )?;
        self.app_manifest.matches_pins(&self.pins)
    }
    pub fn pins(&self) -> &Pins {
        &self.pins
    }
    pub fn manifest(&self) -> &Manifest {
        &self.app_manifest
    }
    record_methods!();
}
impl fmt::Debug for ConnectionConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("ConnectionConfig(<private>)")
    }
}

/// JNI/runtime envelope for an already validated v3 connection and E1 storage
/// identity. The Android import limit belongs to ConnectionConfig; the actual
/// runtime JSON boundary is smaller and must remain bounded independently.
contract_record!(RuntimeBinding, RuntimeBindingWire, {
    configuration: ConnectionConfig,
    storage: StorageRecord,
});
impl RuntimeBinding {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        decode(raw, crate::limits::COMMAND_BYTES)
    }
    fn validate(&self) -> Result<()> {
        require(
            self.configuration.canonical().len() <= MAX_CONFIG_BYTES
                && self.storage.canonical().len() <= MAX_RECORD_BYTES,
            "CONTRACT_INVALID",
        )
    }
    /// The captured local profile, not any remote bytes, determines storage identity.
    pub fn matches_profile(&self, profile: &crate::connection_profile::Profile) -> Result<()> {
        let capture = StorageCapture::from_profile(profile)?;
        self.storage.matches_capture(&capture)
    }
    pub fn configuration(&self) -> &ConnectionConfig {
        &self.configuration
    }
    pub fn storage(&self) -> &StorageRecord {
        &self.storage
    }
    record_methods!();
}
impl fmt::Debug for RuntimeBinding {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("RuntimeBinding(<private>)")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn raw(case: &Value) -> Vec<u8> {
        let mut bytes = if let Some(hex) = case["raw_hex"].as_str() {
            (0..hex.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
                .collect()
        } else {
            case["raw"].as_str().unwrap().as_bytes().to_vec()
        };
        if let Some(size) = case["pad_to_bytes"].as_u64() {
            bytes.resize(size as usize, b' ');
        }
        bytes
    }
    #[test]
    fn shared_configuration_vectors_preserve_b1_and_reject_raw_ambiguity() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../scripts/tests/fixtures/linux-desktop-v3-configuration.json"
        ))
        .unwrap();
        for case in fixture["valid"].as_array().unwrap() {
            let config = ConnectionConfig::parse(&raw(case)).unwrap();
            assert_eq!(
                config.canonical(),
                case["canonical_utf8"].as_str().unwrap().as_bytes()
            );
            assert_eq!(config.sha256(), case["sha256"].as_str().unwrap());
            config.manifest().matches_pins(config.pins()).unwrap();
            assert_eq!(format!("{config:?}"), "ConnectionConfig(<private>)");
        }
        for case in fixture["invalid"].as_array().unwrap() {
            assert!(
                ConnectionConfig::parse(&raw(case)).is_err(),
                "{}",
                case["id"]
            );
            // Direct Serde still checks semantic constraints. Its input byte limit is enforced at parse().
            if case.get("pad_to_bytes").is_none() {
                assert!(
                    serde_json::from_slice::<ConnectionConfig>(&raw(case)).is_err(),
                    "{}",
                    case["id"]
                );
            }
        }
    }
}
