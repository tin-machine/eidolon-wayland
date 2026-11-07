//! NX18a: 保存/移行用の純粋な契約。remote backendをruntimeへ有効化しない。
use base64ct::{Base64, Encoding};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const LIMIT: usize = 16 * 1024;
const LEGACY_ID: &str = "termux-local";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    Invalid,
    Stale,
    ConfirmationRequired,
    RevisionExhausted,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Backend {
    TermuxLocal,
    LinuxSsh,
}
impl Backend {
    fn name(self) -> &'static str {
        match self {
            Self::TermuxLocal => "termux_local",
            Self::LinuxSsh => "linux_ssh",
        }
    }
}

/// endpointと鍵を通常diagnosticsへ出さない。JSONは設定専用経路で扱う。
#[derive(Clone, Serialize, PartialEq, Eq)]
pub struct Profile {
    schema: u32,
    profile_id: String,
    installation: String,
    revision: String,
    display_name: String,
    backend: Backend,
    host: String,
    port: u16,
    username: String,
    host_key: String,
}
impl std::fmt::Debug for Profile {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("ProfileV2(<private>)")
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Fields {
    schema: u32,
    profile_id: String,
    installation: String,
    revision: String,
    display_name: String,
    backend: Backend,
    host: String,
    port: u16,
    username: String,
    host_key: String,
}

impl Profile {
    pub fn parse(json: &str, installation: &str) -> Result<Self, Error> {
        if json.len() > LIMIT {
            return Err(Error::Invalid);
        }
        Self::from_fields(
            serde_json::from_str(json).map_err(|_| Error::Invalid)?,
            installation,
        )
    }

    fn from_fields(value: Fields, installation: &str) -> Result<Self, Error> {
        if value.schema != 2
            || !valid_installation(installation)
            || value.installation != installation
            || value.profile_id.is_empty()
            || value.profile_id.len() > 64
            || !value
                .profile_id
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            || !value.profile_id.as_bytes()[0].is_ascii_lowercase()
            || revision(&value.revision).is_none()
            || value.display_name.trim().is_empty()
            || value.display_name.len() > 160
            || value.display_name.chars().any(char::is_control)
            || value.port == 0
            || value.username.is_empty()
            || value.username.len() > 64
            || !value
                .username
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
            || value.username.starts_with('-')
        {
            return Err(Error::Invalid);
        }
        let host = canonical_host(&value.host)?;
        let host_key = canonical_key(&value.host_key)?;
        match value.backend {
            Backend::TermuxLocal
                if value.profile_id != LEGACY_ID
                    || host != "127.0.0.1"
                    || value.port != 8022
                    || !value
                        .username
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'_') =>
            {
                return Err(Error::Invalid);
            }
            Backend::LinuxSsh if value.profile_id == LEGACY_ID => return Err(Error::Invalid),
            _ => (),
        }
        Ok(Self {
            schema: 2,
            profile_id: value.profile_id,
            installation: value.installation,
            revision: value.revision,
            display_name: value.display_name,
            backend: value.backend,
            host,
            port: value.port,
            username: value.username,
            host_key,
        })
    }

    /// binding domainは旧journalと別。DNS回答や表示名をidentityへ混ぜない。
    pub fn binding(&self) -> String {
        digest(&[
            "eidolon-profile-v2",
            &self.installation,
            &self.profile_id,
            &self.revision,
            self.backend.name(),
            &self.host,
            &self.port.to_string(),
            &self.username,
            &self.host_key,
        ])
    }

    /// 設定ownerはexpectedを現在の保存値と比較してからatomic writeする。
    pub fn replacement(
        &self,
        expected: &str,
        next_json: &str,
        confirmed: bool,
    ) -> Result<Self, Error> {
        if expected != self.binding() {
            return Err(Error::Stale);
        }
        if !confirmed {
            return Err(Error::ConfirmationRequired);
        }
        let next = Self::parse(next_json, &self.installation)?;
        let revision = revision(&self.revision)
            .ok_or(Error::Invalid)?
            .checked_add(1)
            .ok_or(Error::RevisionExhausted)?;
        if next.profile_id != self.profile_id || next.revision != revision.to_string() {
            return Err(Error::Invalid);
        }
        Ok(next)
    }

    pub fn profile_id(&self) -> &str {
        &self.profile_id
    }
    pub fn revision(&self) -> &str {
        &self.revision
    }
    pub fn backend(&self) -> Backend {
        self.backend
    }
    pub fn installation(&self) -> &str {
        &self.installation
    }
    pub fn username(&self) -> &str {
        &self.username
    }
    pub fn host_key(&self) -> &str {
        &self.host_key
    }
    pub fn endpoint(&self) -> (&str, u16) {
        (&self.host, self.port)
    }

    /// Linuxは保存可能でも現runtimeには未実装。未知backendをTermuxへ落とさない。
    pub fn legacy_runtime_profile(&self) -> Option<crate::commands::ConnectionProfile> {
        (self.backend == Backend::TermuxLocal).then(|| crate::commands::ConnectionProfile {
            schema: 1,
            profile_id: self.profile_id.clone(),
            installation: self.installation.clone(),
            username: self.username.clone(),
            host_key: self.host_key.clone(),
        })
    }
}

/// この値は実際に読んだv1からのみ作る。v2ファイルに自己申告の移行フラグは置かない。
pub struct LegacyTermuxMigration {
    profile: Profile,
    legacy_binding: String,
}
impl LegacyTermuxMigration {
    pub fn profile(&self) -> &Profile {
        &self.profile
    }
    /// 候補bindingでありprocess所有権の証明ではない。UID/nonce/starttime検査は依然必要。
    pub fn legacy_cleanup_candidate(&self, current: &Profile) -> Option<&str> {
        (&self.profile == current).then_some(self.legacy_binding.as_str())
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyFields {
    schema: u32,
    profile_id: String,
    installation: String,
    username: String,
    host_key: String,
}
pub fn migrate_legacy_termux(
    json: &str,
    installation: &str,
) -> Result<LegacyTermuxMigration, Error> {
    if json.len() > LIMIT {
        return Err(Error::Invalid);
    }
    let old: LegacyFields = serde_json::from_str(json).map_err(|_| Error::Invalid)?;
    if old.schema != 1 || old.profile_id != LEGACY_ID {
        return Err(Error::Invalid);
    }
    let profile = Profile::from_fields(
        Fields {
            schema: 2,
            profile_id: old.profile_id,
            installation: old.installation,
            revision: "1".into(),
            display_name: "Quest / Termux".into(),
            backend: Backend::TermuxLocal,
            host: "127.0.0.1".into(),
            port: 8022,
            username: old.username,
            host_key: old.host_key,
        },
        installation,
    )?;
    let legacy_binding = digest(&[
        &profile.installation,
        &profile.profile_id,
        &profile.username,
        &profile.host_key,
    ]);
    Ok(LegacyTermuxMigration {
        profile,
        legacy_binding,
    })
}

fn revision(text: &str) -> Option<i64> {
    if text.is_empty() || text.starts_with('0') || !text.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    text.parse::<i64>().ok().filter(|v| *v > 0)
}
fn valid_installation(text: &str) -> bool {
    text.len() == 32
        && text
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn digest(fields: &[&str]) -> String {
    Sha256::digest(fields.join("\0").as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
fn canonical_key(value: &str) -> Result<String, Error> {
    if value.len() > 4096 {
        return Err(Error::Invalid);
    }
    let clean = value.trim();
    if clean.contains(['\n', '\r']) {
        return Err(Error::Invalid);
    }
    let fields: Vec<_> = clean.split_ascii_whitespace().collect();
    if fields.len() < 2 || fields[0] != "ssh-ed25519" {
        return Err(Error::Invalid);
    }
    let wire = Base64::decode_vec(fields[1]).map_err(|_| Error::Invalid)?;
    if wire.len() != 51 || !wire.starts_with(b"\0\0\0\x0bssh-ed25519\0\0\0\x20") {
        return Err(Error::Invalid);
    }
    Ok(format!("ssh-ed25519 {}", Base64::encode_string(&wire)))
}
fn canonical_host(value: &str) -> Result<String, Error> {
    if value.is_empty() || value.len() > 253 || !value.is_ascii() {
        return Err(Error::Invalid);
    }
    if value.bytes().all(|b| b.is_ascii_digit() || b == b'.') {
        let ip = value
            .parse::<std::net::Ipv4Addr>()
            .map_err(|_| Error::Invalid)?;
        return (ip.to_string() == value)
            .then(|| value.to_string())
            .ok_or(Error::Invalid);
    }
    let host = value.to_ascii_lowercase();
    let labels: Vec<_> = host.split('.').collect();
    if (labels.len() > 1
        && !labels
            .last()
            .unwrap()
            .bytes()
            .all(|b| b.is_ascii_lowercase()))
        || host
            .strip_prefix("0x")
            .is_some_and(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_hexdigit()))
        || labels.iter().any(|s| {
            s.is_empty()
                || s.len() > 63
                || s.starts_with('-')
                || s.ends_with('-')
                || !s
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        })
    {
        return Err(Error::Invalid);
    }
    Ok(host)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};
    const INSTALLATION: &str = "11111111111111111111111111111111";
    fn vectors() -> Vec<(Value, String)> {
        include_str!("../../../android/app/src/test/resources/connection-profiles-v2.tsv")
            .lines().filter(|line| !line.starts_with('#') && !line.is_empty())
            .map(|line| {
                let f: Vec<_> = line.split('\t').collect();
                (json!({"schema":2,"installation":INSTALLATION,"profile_id":f[0],"revision":f[1],
                    "display_name":f[2],"backend":f[3],"host":f[4],"port":f[5].parse::<u16>().unwrap(),
                    "username":f[6],"host_key":f[7]}), f[8].to_string())
            }).collect()
    }
    fn parse(value: &Value) -> Result<Profile, Error> {
        Profile::parse(&value.to_string(), INSTALLATION)
    }
    fn legacy() -> Value {
        let mut value = vectors()[0].0.clone();
        value.as_object_mut().unwrap().retain(|key, _| {
            [
                "schema",
                "installation",
                "profile_id",
                "username",
                "host_key",
            ]
            .contains(&key.as_str())
        });
        value["schema"] = json!(1);
        value
    }

    #[test]
    fn shared_kotlin_vectors_preserve_decimal_ids_and_canonical_identity() {
        for (value, expected) in vectors() {
            let profile = parse(&value).unwrap();
            assert_eq!(profile.binding(), expected);
            assert_eq!(profile.revision(), value["revision"].as_str().unwrap());
            assert_eq!(
                parse(&serde_json::to_value(&profile).unwrap()).unwrap(),
                profile
            );
            assert_eq!(format!("{profile:?}"), "ProfileV2(<private>)");
            assert_eq!(
                profile.legacy_runtime_profile().is_some(),
                profile.backend() == Backend::TermuxLocal
            );
        }
        assert_eq!(
            parse(&vectors()[1].0).unwrap().endpoint(),
            ("host.example", 22)
        );
        let mut short = vectors()[1].0.clone();
        short["host"] = json!("LINUX-BUILD-01");
        assert_eq!(parse(&short).unwrap().endpoint().0, "linux-build-01");
    }

    #[test]
    fn explicit_legacy_migration_preserves_current_runtime_and_limits_cleanup_candidate() {
        let migration = migrate_legacy_termux(&legacy().to_string(), INSTALLATION).unwrap();
        let profile = migration.profile();
        assert_eq!(serde_json::to_value(profile).unwrap(), vectors()[0].0);
        assert_eq!(
            serde_json::to_value(profile.legacy_runtime_profile().unwrap()).unwrap(),
            legacy()
        );
        let config = crate::commands::RuntimeConfig {
            installation: Some(INSTALLATION.into()),
            profiles: vec![profile.legacy_runtime_profile().unwrap()],
            ..Default::default()
        };
        assert!(config.validate().is_ok());
        assert_eq!(
            migration.legacy_cleanup_candidate(profile),
            Some("b73bed18a0eb5cfc4a328cc058dc15027894bc46a462cd1be2449796b1133012")
        );
        assert_ne!(
            migration.legacy_cleanup_candidate(profile).unwrap(),
            profile.binding()
        );
        assert!(
            migration
                .legacy_cleanup_candidate(&parse(&vectors()[1].0).unwrap())
                .is_none()
        );
        for (key, new) in [("revision", json!("2")), ("display_name", json!("Changed"))] {
            let mut value = vectors()[0].0.clone();
            value[key] = new;
            assert!(
                migration
                    .legacy_cleanup_candidate(&parse(&value).unwrap())
                    .is_none()
            );
        }
        for (key, new) in [
            ("schema", json!(2)),
            ("profile_id", json!("linux-test")),
            ("host", json!("192.0.2.1")),
            ("installation", json!("2".repeat(32))),
        ] {
            let mut value = legacy();
            value[key] = new;
            assert!(migrate_legacy_termux(&value.to_string(), INSTALLATION).is_err());
        }
    }

    #[test]
    fn identity_edits_need_fresh_revision_confirmation_and_matching_owner() {
        let value = vectors()[1].0.clone();
        let before = parse(&value).unwrap();
        for (key, new) in [
            ("host", json!("other.example")),
            ("port", json!(2200)),
            ("username", json!("other-user")),
            ("profile_id", json!("other-linux")),
            ("revision", json!("9007199254740994")),
            (
                "host_key",
                json!(value["host_key"].as_str().unwrap().replace("AQEB", "AgIC")),
            ),
        ] {
            let mut changed = value.clone();
            changed[key] = new;
            assert_ne!(before.binding(), parse(&changed).unwrap().binding());
        }
        let mut next = value.clone();
        next["revision"] = json!("9007199254740994");
        next["host"] = json!("other.example");
        assert_eq!(
            before
                .replacement(&before.binding(), &next.to_string(), true)
                .unwrap()
                .endpoint()
                .0,
            "other.example"
        );
        assert_eq!(
            before.replacement("stale", &next.to_string(), true),
            Err(Error::Stale)
        );
        assert_eq!(
            before.replacement(&before.binding(), &next.to_string(), false),
            Err(Error::ConfirmationRequired)
        );
        assert!(
            before
                .replacement(&before.binding(), &value.to_string(), true)
                .is_err()
        );
        for (key, new) in [
            ("profile_id", json!("other-linux")),
            ("installation", json!("2".repeat(32))),
        ] {
            let mut changed = next.clone();
            changed[key] = new;
            assert!(
                before
                    .replacement(&before.binding(), &changed.to_string(), true)
                    .is_err()
            );
        }
        let max = parse(&vectors()[2].0).unwrap();
        assert_eq!(
            max.replacement(&max.binding(), &vectors()[2].0.to_string(), true),
            Err(Error::RevisionExhausted)
        );
    }

    #[test]
    fn bad_schema_unknown_fields_numeric_rounding_and_unsupported_endpoints_fail_closed() {
        let value = vectors()[1].0.clone();
        for (key, new) in [
            ("revision", json!(1)),
            ("revision", json!("01")),
            ("revision", json!("0")),
            ("revision", json!("9223372036854775808")),
            ("schema", json!(2.0)),
            ("port", json!(22.0)),
            ("port", json!(true)),
            ("port", json!(0)),
            ("port", json!(65536)),
            ("backend", json!("unknown")),
            ("host", json!("user@host.example")),
            ("host", json!("host.example:22")),
            ("host", json!("127.1")),
            ("host", json!("127.00.0.1")),
            ("host", json!("[::1]")),
            ("host", json!("::1")),
            ("host", json!("host.example.")),
            ("host", json!("x\n.example")),
            ("host", json!("a_foo.example")),
            ("host", json!("0x7f000001")),
            ("username", json!("-user")),
            ("username", json!("user;id")),
            ("profile_id", json!("termux-local")),
            ("display_name", json!("line\nbreak")),
            ("secret_key", json!("fixture")),
        ] {
            let mut changed = value.clone();
            changed[key] = new;
            assert!(parse(&changed).is_err(), "field={key}");
        }
        let duplicate = value.to_string().replacen('{', "{\"revision\":\"1\",", 1);
        assert!(Profile::parse(&duplicate, INSTALLATION).is_err());
        assert!(Profile::parse(&" ".repeat(LIMIT + 1), INSTALLATION).is_err());
        let mut missing = value.clone();
        missing.as_object_mut().unwrap().remove("revision");
        assert!(parse(&missing).is_err());
        for (key, new) in [
            ("host", json!("localhost")),
            ("port", json!(22)),
            ("backend", json!("linux_ssh")),
            ("username", json!("linux-user")),
        ] {
            let mut changed = vectors()[0].0.clone();
            changed[key] = new;
            assert!(parse(&changed).is_err());
        }
    }

    #[test]
    fn pin_canonicalization_and_display_limits_use_wire_and_utf8_bytes() {
        let value = vectors()[1].0.clone();
        let original = parse(&value).unwrap();
        let mut comments = value.clone();
        comments["host_key"] = json!(format!("{} fixture comment\n", original.host_key()));
        assert_eq!(parse(&comments).unwrap().binding(), original.binding());
        for bad in [
            "ssh-rsa AAAA".into(),
            "ssh-ed25519 !!!!".into(),
            format!("{}\n{}", original.host_key(), original.host_key()),
            "x".repeat(4097),
        ] {
            let mut changed = value.clone();
            changed["host_key"] = json!(bad);
            assert!(parse(&changed).is_err());
        }
        for (name, valid) in [
            ("あ".repeat(53), true),
            ("あ".repeat(54), false),
            (" ".into(), false),
        ] {
            let mut changed = value.clone();
            changed["display_name"] = json!(name);
            assert_eq!(parse(&changed).is_ok(), valid);
        }
    }
}
