use crate::Error;
use russh::keys::{Algorithm, PublicKey};
use serde::{Deserialize, Serialize};

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Profile {
    pub schema: u32,
    pub profile_id: String,
    pub installation: String,
    pub username: String,
    pub host_key: String,
}
impl Profile {
    pub fn parse(json: &str, installation: &str) -> Result<Self, Error> {
        if json.len() > 16 * 1024 {
            return Err(Error::ConfigInvalid);
        }
        let profile: Self = serde_json::from_str(json).map_err(|_| Error::ConfigInvalid)?;
        if profile.schema != 1
            || profile.profile_id != "termux-local"
            || profile.installation != installation
            || installation.len() != 32
            || !installation
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || profile.username.is_empty()
            || profile.username.len() > 64
            || !profile
                .username
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_')
        {
            return Err(Error::ConfigInvalid);
        }
        parse_key(&profile.host_key)?;
        Ok(profile)
    }
}
pub fn parse_key(value: &str) -> Result<PublicKey, Error> {
    if value.len() > 4096 {
        return Err(Error::ConfigInvalid);
    }
    let key = PublicKey::from_openssh(value).map_err(|_| Error::ConfigInvalid)?;
    if key.algorithm() != Algorithm::Ed25519 {
        return Err(Error::ConfigInvalid);
    }
    Ok(key)
}
pub fn verify(expected: Option<&str>, seen: &PublicKey) -> Result<(), Error> {
    let pin = expected
        .filter(|s| !s.is_empty())
        .ok_or(Error::HostKeyUnknown)?;
    if parse_key(pin)?.key_data() != seen.key_data() {
        return Err(Error::HostKeyMismatch);
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::credentials;
    #[test]
    fn only_exact_known_key_is_accepted_and_profiles_bind_the_installation() {
        let a = credentials::generate().unwrap();
        let b = credentials::generate().unwrap();
        let public_a = credentials::public_key(&a).unwrap();
        let public_b = credentials::public_key(&b).unwrap();
        let seen = parse_key(&public_a).unwrap();
        assert_eq!(verify(Some(&public_a), &seen), Ok(()));
        assert_eq!(verify(None, &seen), Err(Error::HostKeyUnknown));
        assert_eq!(verify(Some(&public_b), &seen), Err(Error::HostKeyMismatch));
        let profile = Profile {
            schema: 1,
            profile_id: "termux-local".into(),
            installation: "1".repeat(32),
            username: "u0_a123".into(),
            host_key: public_a,
        };
        let json = serde_json::to_string(&profile).unwrap();
        assert!(Profile::parse(&json, &"1".repeat(32)).is_ok());
        assert!(matches!(
            Profile::parse(&json, &"2".repeat(32)),
            Err(Error::ConfigInvalid)
        ));
        assert!(Profile::parse(&json.replace("u0_a123", "bad;command"), &"1".repeat(32)).is_err());
    }
}
