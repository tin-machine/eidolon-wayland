//! C2 emits this receipt after actual ownership checks; B2 only validates it.
use super::*;

#[derive(Clone)]
pub struct Receipt(Value);
impl Receipt {
    pub fn parse(
        raw: &[u8],
        binding: &Binding,
        sources: &BTreeMap<String, String>,
        expected_boot: &str,
        status: u32,
    ) -> Result<Self> {
        let line = raw
            .strip_suffix(b"\n")
            .ok_or("DESKTOP_V3_RECOVERY_INVALID")?;
        if raw.len() > contract::MAX_RECORD_BYTES || line.contains(&b'\n') || line.contains(&b'\r')
        {
            return Err("DESKTOP_V3_RECOVERY_INVALID");
        }
        let value = contract::parse_json(line, contract::MAX_RECORD_BYTES)
            .map_err(|_| "DESKTOP_V3_RECOVERY_INVALID")?;
        fields(
            &value,
            &[
                "schema",
                "protocol_revision",
                "context",
                "pins",
                "build_id",
                "source_hashes",
                "expected_boot_id",
                "origin_boot_id",
                "boot_id",
                "status",
                "process_evidence",
                "process_complete",
                "pipes_eof",
                "remote_cleaned",
                "receipt_written",
            ],
        )?;
        source_map(&json!(sources))?;
        if value["schema"] != json!(1)
            || value["protocol_revision"] != REVISION
            || value["context"] != binding.context().record()
            || value["pins"] != binding.pins().record()
            || value["build_id"] != binding.pins().build_id()
            || value["source_hashes"] != json!(sources)
            || value["expected_boot_id"] != expected_boot
            || expected_boot != "-" && !valid_boot(expected_boot)
            || !value["boot_id"].as_str().is_some_and(valid_boot)
        {
            return Err("DESKTOP_V3_RECOVERY_SCOPE");
        }
        let origin = match &value["origin_boot_id"] {
            Value::Null => None,
            Value::String(boot)
                if valid_boot(boot) && (expected_boot == "-" || boot == expected_boot) =>
            {
                Some(boot.as_str())
            }
            _ => return Err("DESKTOP_V3_RECOVERY_SCOPE"),
        };
        if !matches!(
            (value["status"].as_str(), status),
            (Some("complete"), 0) | (Some("pending"), 1)
        ) {
            return Err("DESKTOP_V3_RECOVERY_SCOPE");
        }
        for name in [
            "process_complete",
            "pipes_eof",
            "remote_cleaned",
            "receipt_written",
        ] {
            if !value[name].is_boolean() {
                return Err("DESKTOP_V3_RECOVERY_INVALID");
            }
        }
        let process = value["process_complete"] == true;
        let pipes = value["pipes_eof"] == true;
        let remote = value["remote_cleaned"] == true;
        let receipt = value["receipt_written"] == true;
        let proof = match value["process_evidence"].as_str() {
            Some("receipt") => origin.is_some() && process && pipes && receipt,
            Some("boot_changed") => {
                origin.is_some_and(|old| value["boot_id"] != old) && process && !pipes
            }
            Some("unknown") => !process && !pipes && !remote && !receipt,
            _ => false,
        };
        if !proof || value["status"] == "complete" && !(process && remote && receipt) {
            return Err("DESKTOP_V3_RECOVERY_INVALID");
        }
        Ok(Self(value))
    }
    pub fn record(&self) -> Value {
        self.0.clone()
    }
    pub fn origin_boot_id(&self) -> Option<&str> {
        self.0["origin_boot_id"].as_str()
    }
    pub fn boot_id(&self) -> &str {
        self.0["boot_id"].as_str().unwrap()
    }
    pub fn complete(&self) -> bool {
        self.status() == "complete"
    }
    pub fn status(&self) -> &str {
        self.0["status"].as_str().unwrap()
    }
    pub fn process_evidence(&self) -> &str {
        self.0["process_evidence"].as_str().unwrap()
    }
    pub fn process_complete(&self) -> bool {
        self.0["process_complete"].as_bool().unwrap()
    }
    pub fn pipes_eof(&self) -> bool {
        self.0["pipes_eof"].as_bool().unwrap()
    }
    pub fn remote_cleaned(&self) -> bool {
        self.0["remote_cleaned"].as_bool().unwrap()
    }
    pub fn receipt_written(&self) -> bool {
        self.0["receipt_written"].as_bool().unwrap()
    }
}
