//! Saved selection for the Ubuntu user-systemd backend.
use crate::{
    connection_profile::Profile,
    limits::COMMAND_BYTES,
    linux_desktop_v3::{Hash, StorageCapture, StorageRecord},
};
use serde::{Deserialize, Serialize};

const INVALID: &str = "SYSTEMD_CONFIGURATION_INVALID";

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeBinding {
    schema: u32,
    waypipe_path: String,
    waypipe_sha256: Hash,
    storage: StorageRecord,
}

impl RuntimeBinding {
    pub fn parse(raw: &str) -> Result<Self, &'static str> {
        if raw.len() > COMMAND_BYTES {
            return Err(INVALID);
        }
        let value: Self = serde_json::from_str(raw).map_err(|_| INVALID)?;
        value.validate()?;
        Ok(value)
    }

    pub fn validate(&self) -> Result<(), &'static str> {
        if self.schema != 1 || !valid_absolute_path(&self.waypipe_path) {
            return Err(INVALID);
        }
        Ok(())
    }

    pub fn matches_profile(&self, profile: &Profile) -> Result<(), &'static str> {
        self.validate()?;
        let capture = StorageCapture::from_profile(profile).map_err(|_| INVALID)?;
        self.storage.matches_capture(&capture).map_err(|_| INVALID)
    }

    pub fn waypipe_path(&self) -> &str {
        &self.waypipe_path
    }

    pub fn waypipe_sha256(&self) -> &str {
        self.waypipe_sha256.as_str()
    }

    pub fn storage(&self) -> &StorageRecord {
        &self.storage
    }
}

pub fn valid_absolute_path(value: &str) -> bool {
    value.len() <= 1024
        && !value.chars().any(char::is_control)
        && value.strip_prefix('/').is_some_and(|tail| {
            tail.split('/')
                .all(|component| !matches!(component, "" | "." | ".."))
        })
}
