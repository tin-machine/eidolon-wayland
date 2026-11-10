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
