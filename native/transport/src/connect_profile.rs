//! 接続開始時のimmutable owner。保存用v2の受理と実行可能backendを分離する。
use crate::{host_key, journal, linux_desktop};
use eidolon_model::{
    connection_profile::{Backend, Profile},
    linux_desktop_v3::{StorageRecord, configuration::ConnectionConfig},
};
use russh::keys::PublicKey;
use std::{
    net::{Ipv4Addr, SocketAddr},
    path::PathBuf,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PrepareError {
    InvalidProfile,
    DnsUnsupported,
}
impl PrepareError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidProfile => "CONFIG_INVALID",
            Self::DnsUnsupported => "DNS_NOT_IMPLEMENTED",
        }
    }
}

#[derive(Clone)]
pub struct ConnectSpec {
    pub(crate) endpoint: SocketAddr,
    pub(crate) profile_id: String,
    pub(crate) revision: Option<String>,
    pub(crate) username: String,
    pub(crate) pin: PublicKey,
    pub(crate) binding: String,
    pub(crate) helper_supported: bool,
    pub(crate) desktop: Option<DesktopConfig>,
}
/// Captured once at Connect. Neither profile saves nor later UI requests can
/// change the authenticated endpoint, identity, bundle or journal destination.
#[derive(Clone)]
pub(crate) enum DesktopConfig {
    V2 {
        profile: Profile,
        pins: linux_desktop::Pins,
        files: PathBuf,
    },
    // The full immutable capture is intentionally inert until the I2 v3
    // controller is connected. When I2 reads these fields in production,
    // this lint expectation will need to be removed.
    #[cfg_attr(not(test), expect(dead_code, reason = "I2 v3 controller not integrated yet"))]
    V3 {
        profile: Profile,
        configuration: Box<ConnectionConfig>,
        storage: StorageRecord,
        files: PathBuf,
    },
}
impl DesktopConfig {
    pub(crate) fn version(&self) -> linux_desktop::dispatch::Version {
        match self {
            Self::V2 { .. } => linux_desktop::dispatch::Version::V2,
            Self::V3 { .. } => linux_desktop::dispatch::Version::V3,
        }
    }
}
impl std::fmt::Debug for ConnectSpec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("ConnectSpec(<private>)")
    }
}
impl ConnectSpec {
    pub(crate) fn legacy(value: host_key::Profile) -> Result<Self, PrepareError> {
        // 旧public structを手作りしてもv1の固定Termux制約を迂回させない。
        let json = serde_json::to_string(&value).map_err(|_| PrepareError::InvalidProfile)?;
        let profile = host_key::Profile::parse(&json, &value.installation)
            .map_err(|_| PrepareError::InvalidProfile)?;
        Ok(Self {
            endpoint: (Ipv4Addr::LOCALHOST, 8022).into(),
            profile_id: profile.profile_id.clone(),
            revision: None,
            username: profile.username.clone(),
            pin: host_key::parse_key(&profile.host_key)
                .map_err(|_| PrepareError::InvalidProfile)?,
            binding: journal::binding(&profile),
            helper_supported: true,
            desktop: None,
        })
    }

    /// runtime/Host未配線。LinuxはSSHの固定id/token確認だけで、GUI/helperの採用ではない。
    pub fn from_v2(profile: &Profile) -> Result<Self, PrepareError> {
        let (host, port) = profile.endpoint();
        let ip = host
            .parse::<Ipv4Addr>()
            .map_err(|_| PrepareError::DnsUnsupported)?;
        Ok(Self {
            endpoint: (ip, port).into(),
            profile_id: profile.profile_id().into(),
            revision: Some(profile.revision().into()),
            username: profile.username().into(),
            pin: host_key::parse_key(profile.host_key())
                .map_err(|_| PrepareError::InvalidProfile)?,
            binding: profile.binding(),
            helper_supported: profile.backend() == Backend::TermuxLocal,
            desktop: None,
        })
    }

    pub(crate) fn linux_desktop(
        profile: Profile,
        pins: linux_desktop::Pins,
        files: PathBuf,
    ) -> Result<Self, PrepareError> {
        if profile.backend() != Backend::LinuxSsh || !files.is_absolute() {
            return Err(PrepareError::InvalidProfile);
        }
        let mut spec = Self::from_v2(&profile)?;
        spec.desktop = Some(DesktopConfig::V2 {
            profile,
            pins,
            files,
        });
        Ok(spec)
    }

    /// Pure capture for I2. A v3 request is never interpreted as a v2
    /// controller request until the dedicated v3 actor is implemented.
    pub fn linux_desktop_v3(
        profile: Profile,
        configuration: ConnectionConfig,
        storage: StorageRecord,
        files: PathBuf,
    ) -> Result<Self, PrepareError> {
        if profile.backend() != Backend::LinuxSsh
            || profile.profile_id() != "remote-linux"
            || !files.is_absolute()
        {
            return Err(PrepareError::InvalidProfile);
        }
        let capture = eidolon_model::linux_desktop_v3::StorageCapture::from_profile(&profile)
            .map_err(|_| PrepareError::InvalidProfile)?;
        storage
            .matches_capture(&capture)
            .map_err(|_| PrepareError::InvalidProfile)?;
        let mut spec = Self::from_v2(&profile)?;
        spec.desktop = Some(DesktopConfig::V3 {
            profile,
            configuration: Box::new(configuration),
            storage,
            files,
        });
        Ok(spec)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::credentials;
    fn legacy() -> host_key::Profile {
        host_key::Profile {
            schema: 1,
            profile_id: "termux-local".into(),
            installation: "a".repeat(32),
            username: "fixture".into(),
            host_key: credentials::public_key(&credentials::generate().unwrap()).unwrap(),
        }
    }
    #[test]
    fn v1_and_v2_termux_use_distinct_bindings_without_rewriting_old_journal_identity() {
        let old = legacy();
        let spec = ConnectSpec::legacy(old.clone()).unwrap();
        assert_eq!(spec.endpoint, (Ipv4Addr::LOCALHOST, 8022).into());
        assert_eq!(spec.binding, journal::binding(&old));
        let migration = eidolon_model::connection_profile::migrate_legacy_termux(
            &serde_json::to_string(&old).unwrap(),
            &old.installation,
        )
        .unwrap();
        let next = ConnectSpec::from_v2(migration.profile()).unwrap();
        assert!(next.helper_supported);
        assert_eq!(next.endpoint, spec.endpoint);
        assert_ne!(next.binding, spec.binding);
        assert_eq!(next.revision.as_deref(), Some("1"));
        assert_eq!(format!("{next:?}"), "ConnectSpec(<private>)");
    }
    #[test]
    fn stored_dns_name_is_explicitly_unsupported_for_connect_and_bad_v1_is_rejected() {
        let mut old = legacy();
        let value = Profile::parse(&serde_json::json!({"schema":2,"installation":old.installation,
            "profile_id":"linux-fixture","revision":"1","display_name":"Fixture","backend":"linux_ssh",
            "host":"fixture.example","port":22,"username":old.username,"host_key":old.host_key}).to_string(), &old.installation).unwrap();
        assert!(matches!(
            ConnectSpec::from_v2(&value),
            Err(PrepareError::DnsUnsupported)
        ));
        old.profile_id = "remote-not-v1".into();
        assert!(matches!(
            ConnectSpec::legacy(old),
            Err(PrepareError::InvalidProfile)
        ));
    }
    #[test]
    fn v3_is_a_distinct_local_capture_and_storage_must_match() {
        use eidolon_model::linux_desktop_v3::{StorageCapture, configuration::ConnectionConfig};
        let host_key =
            include_str!("../../../android/app/src/test/resources/connection-profiles-v2.tsv")
                .lines()
                .find(|line| line.starts_with("linux-"))
                .unwrap()
                .split('\t')
                .nth(7)
                .unwrap();
        let profile_json = serde_json::json!({
            "schema":2,"installation":"1".repeat(32),"profile_id":"remote-linux",
            "revision":"1","display_name":"v3","backend":"linux_ssh",
            "host":"192.0.2.1","port":22,"username":"fixture","host_key":host_key
        })
        .to_string();
        let profile = Profile::parse(&profile_json, &"1".repeat(32)).unwrap();
        let f: serde_json::Value = serde_json::from_str(include_str!(
            "../../../scripts/tests/fixtures/linux-desktop-v3-configuration.json"
        ))
        .unwrap();
        let config =
            ConnectionConfig::parse(f["valid"][0]["raw"].as_str().unwrap().as_bytes()).unwrap();
        let mut storage = StorageCapture::from_profile(&profile).unwrap().record();
        storage["schema"] = serde_json::json!(1);
        storage["installation_id"] = serde_json::json!("a".repeat(32));
        storage["profile_storage_id"] = serde_json::json!("b".repeat(32));
        let record = StorageRecord::parse(storage.to_string().as_bytes()).unwrap();
        let spec = ConnectSpec::linux_desktop_v3(
            profile.clone(),
            config.clone(),
            record.clone(),
            PathBuf::from("/tmp/eidolon"),
        )
        .unwrap();
        assert!(matches!(
            spec.desktop.as_ref(),
            Some(DesktopConfig::V3 { .. })
        ));
        let captured = spec.desktop.as_ref().unwrap();
        assert_eq!(captured.version(), linux_desktop::dispatch::Version::V3);
        if let DesktopConfig::V3 {
            profile: p,
            configuration: c,
            storage: st,
            files,
        } = captured
        {
            assert!(p == &profile && c.as_ref() == &config && st == &record);
            assert_eq!(files, &PathBuf::from("/tmp/eidolon"));
        } else {
            panic!("local v3 selection must be immutable");
        }
        let mut stale = storage;
        stale["profile_binding"] = serde_json::json!("f".repeat(64));
        let stale = StorageRecord::parse(stale.to_string().as_bytes()).unwrap();
        assert!(matches!(
            ConnectSpec::linux_desktop_v3(
                profile.clone(),
                config.clone(),
                stale,
                PathBuf::from("/tmp/eidolon")
            ),
            Err(PrepareError::InvalidProfile)
        ));
        assert!(matches!(
            ConnectSpec::linux_desktop_v3(profile, config, record, PathBuf::from("relative")),
            Err(PrepareError::InvalidProfile)
        ));
    }
}
