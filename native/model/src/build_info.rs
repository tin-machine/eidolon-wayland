use serde::{Deserialize, Serialize};

/// 同じ入力は同じJSONになる。時刻や絶対パスは版識別に含めない。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BuildInfo {
    pub schema: u32,
    pub source_commit: String,
    pub source_digest: String,
    pub source_dirty: bool,
    pub apk_version_code: u32,
    pub crate_version: String,
    pub target: String,
    pub profile: String,
}

impl BuildInfo {
    pub fn to_json(&self) -> String {
        // この型は文字列・整数・boolだけを持ち、JSONへの変換は失敗しない。
        serde_json::to_string(self).expect("BuildInfo contains only JSON values")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn version_contract_round_trip_is_deterministic() {
        let input = r#"{"schema":1,"source_commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","source_dirty":true,"apk_version_code":3,"crate_version":"0.1.0","target":"aarch64-linux-android","profile":"debug"}"#;
        let info: BuildInfo = serde_json::from_str(input).unwrap();
        assert_eq!(info.to_json(), input);
        assert_eq!(info.to_json(), info.clone().to_json());
        let mut next = info.clone();
        next.apk_version_code += 1;
        assert_ne!(info.to_json(), next.to_json());
        assert!(serde_json::from_str::<BuildInfo>(r#"{"schema":1}"#).is_err());
        assert!(serde_json::from_str::<BuildInfo>(&input.replace(":3,", ":\"3\",")).is_err());
        assert!(
            serde_json::from_str::<BuildInfo>(
                &input.replace("\"debug\"}", "\"debug\",\"extra\":0}")
            )
            .is_err()
        );
    }
}
