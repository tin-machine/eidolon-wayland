use super::{Capabilities, Generation, UnitNames, absolute_path, ssh_command};
use std::collections::BTreeMap;

const INSPECT: &str = include_str!("unit-inspect.sh");
const RETIRED: &str = include_str!("unit-retired.sh");

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct InvocationId([u8; 16]);

impl InvocationId {
    pub fn parse(value: &str) -> Option<Self> {
        let bytes = Generation::parse(value)?.bytes();
        (bytes != [0; 16]).then_some(Self(bytes))
    }

    pub fn bytes(self) -> [u8; 16] {
        self.0
    }
}

impl std::fmt::Display for InvocationId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.iter().try_for_each(|byte| write!(f, "{byte:02x}"))
    }
}

#[derive(Debug, Eq, PartialEq)]
pub enum UnitStatus {
    Absent,
    Loaded {
        active_state: ActiveState,
        sub_state: String,
        invocation: Option<InvocationId>,
        control_group: Option<String>,
        result: String,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ActiveState {
    Activating,
    Active,
    Deactivating,
    Inactive,
    Failed,
}

pub fn inspect_command(generation: Generation, capability: &Capabilities) -> String {
    let unit = UnitNames::from(generation);
    ssh_command(
        INSPECT,
        &[
            &capability.uid.to_string(),
            &capability.boot_id,
            &unit.application,
        ],
    )
}

pub fn retired_command(generation: Generation, capability: &Capabilities) -> String {
    let unit = UnitNames::from(generation);
    ssh_command(
        RETIRED,
        &[
            &capability.uid.to_string(),
            &capability.boot_id,
            &unit.application,
            &unit.expiry_timer,
            &unit.expiry_service,
            &unit.runtime_dir,
        ],
    )
}

impl UnitStatus {
    pub fn parse(generation: Generation, stdout: &[u8]) -> Result<Self, &'static str> {
        const INVALID: &str = "SYSTEMD_STATUS_INVALID";
        if stdout.len() > 3_072 {
            return Err(INVALID);
        }
        let mut fields = BTreeMap::new();
        let values = std::str::from_utf8(stdout)
            .map_err(|_| INVALID)?
            .strip_suffix('\n')
            .ok_or(INVALID)?;
        for line in values.split('\n') {
            let (key, value) = line.split_once('=').ok_or(INVALID)?;
            if fields.insert(key, value).is_some() {
                return Err(INVALID);
            }
        }
        let (
            Some(&load),
            Some(&active),
            Some(&sub_state),
            Some(&invocation),
            Some(&group),
            Some(&result),
        ) = (
            fields.get("LoadState"),
            fields.get("ActiveState"),
            fields.get("SubState"),
            fields.get("InvocationID"),
            fields.get("ControlGroup"),
            fields.get("Result"),
        )
        else {
            return Err(INVALID);
        };
        if fields.len() != 6 || !status_value(sub_state) || !status_value(result) {
            return Err(INVALID);
        }
        if load == "not-found"
            && active == "inactive"
            && sub_state == "dead"
            && invocation.is_empty()
            && group.is_empty()
        {
            return Ok(Self::Absent);
        }
        if load != "loaded" {
            return Err(INVALID);
        }
        let active_state = match active {
            "activating" => ActiveState::Activating,
            "active" => ActiveState::Active,
            "deactivating" => ActiveState::Deactivating,
            "inactive" => ActiveState::Inactive,
            "failed" => ActiveState::Failed,
            _ => return Err(INVALID),
        };
        let invocation = if invocation.is_empty() {
            None
        } else {
            Some(InvocationId::parse(invocation).ok_or(INVALID)?)
        };
        let control_group = if group.is_empty() {
            None
        } else {
            let unit = UnitNames::from(generation);
            if !valid_control_group(group, &unit.application) {
                return Err(INVALID);
            }
            Some(group.into())
        };
        Ok(Self::Loaded {
            active_state,
            sub_state: sub_state.into(),
            invocation,
            control_group,
            result: result.into(),
        })
    }
}

fn status_value(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

fn valid_control_group(group: &str, unit: &str) -> bool {
    absolute_path(group)
        && group.starts_with("/user.slice/")
        && group.rsplit_once('/').is_some_and(|(_, name)| name == unit)
}

#[cfg(test)]
mod tests {
    use super::*;

    const GENERATION: &str = "0001112233445566778899aabbccddee";
    const INVOCATION: &str = "102132435465768798a9bacbdcedfe0f";

    #[test]
    fn not_found_requires_dead_and_empty_identity() {
        let generation = Generation::parse(GENERATION).unwrap();
        let raw = "LoadState=not-found\nActiveState=inactive\nSubState=dead\nInvocationID=\nResult=success\nControlGroup=\n";
        assert_eq!(
            UnitStatus::parse(generation, raw.as_bytes()),
            Ok(UnitStatus::Absent)
        );
        for bad in [
            raw.replace("inactive", "active"),
            raw.replace("InvocationID=", &format!("InvocationID={INVOCATION}")),
            raw.replace("LoadState=", "LoadState=loaded\nLoadState="),
            raw.trim_end().into(),
        ] {
            assert!(UnitStatus::parse(generation, bad.as_bytes()).is_err());
        }
    }

    #[test]
    fn loaded_capability_checks_exact_generation_on_control_group() {
        let generation = Generation::parse(GENERATION).unwrap();
        let unit = UnitNames::from(generation);
        let group = format!(
            "/user.slice/user-1000.slice/user@1000.service/app.slice/{}",
            unit.application
        );
        let raw = format!(
            "LoadState=loaded\nActiveState=active\nSubState=running\nInvocationID={INVOCATION}\nResult=success\nControlGroup={group}\n"
        );
        assert_eq!(
            UnitStatus::parse(generation, raw.as_bytes()),
            Ok(UnitStatus::Loaded {
                active_state: ActiveState::Active,
                sub_state: "running".into(),
                invocation: InvocationId::parse(INVOCATION),
                control_group: Some(group),
                result: "success".into(),
            }),
        );
        for bad in [
            raw.replace(GENERATION, "aaabbbcccdddeeefffaabbccddeeffaa"),
            raw.replace("/app.slice/", "/../"),
            raw.replace("InvocationID=", "InvocationID=no:"),
            raw.replace("ActiveState=active", "ActiveState=unexpected"),
        ] {
            assert!(UnitStatus::parse(generation, bad.as_bytes()).is_err());
        }
    }
}
