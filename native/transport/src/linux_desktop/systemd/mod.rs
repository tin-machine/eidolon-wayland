//! Ubuntu user-systemd transport. Capability probes run on the actor's pinned SSH connection.
use super::{boot_identity, hex};
use eidolon_model::ubuntu_systemd::valid_absolute_path as absolute_path;
use std::path::PathBuf;

mod chrome;
pub use chrome::{ChromeControl, ChromeStorage, chrome_control_command, chrome_start_command};
mod driver;
pub use driver::{Phase, Report, Request};
pub(crate) use driver::{Tick, serve};
mod exchange;
pub use exchange::{ExchangeError, exchange};
mod journal;
pub use journal::{Journal, JournalRecord, JournalStage};
mod owner;
pub use owner::{ChromeOwner, ChromeTarget};
mod unit;
pub use unit::{ActiveState, InvocationId, UnitStatus, inspect_command, retired_command};

const PROBE: &str = include_str!("probe.sh");
const CHROME_EXECUTABLES: [&str; 2] = ["google-chrome-stable", "google-chrome"];
const WAYPIPE_HASH_LEN: usize = 64;
const SSH_SOCKET_BYTES: usize = 103;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Generation([u8; 16]);

impl Generation {
    pub fn new() -> Self {
        Self(rand::random())
    }

    pub fn parse(value: &str) -> Option<Self> {
        if !hex(value, 32) {
            return None;
        }
        let mut generation = [0; 16];
        for (index, byte) in generation.iter_mut().enumerate() {
            *byte = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16).ok()?;
        }
        Some(Self(generation))
    }

    pub fn bytes(self) -> [u8; 16] {
        self.0
    }
}

impl Default for Generation {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Display for Generation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.iter().try_for_each(|byte| write!(f, "{byte:02x}"))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct UnitNames {
    pub application: String,
    pub expiry_timer: String,
    pub expiry_service: String,
    pub runtime_dir: String,
}

impl From<Generation> for UnitNames {
    fn from(generation: Generation) -> Self {
        Self {
            application: format!("eidolon-gui-{generation}.service"),
            expiry_timer: format!("eidolon-exp-{generation}.timer"),
            expiry_service: format!("eidolon-exp-{generation}.service"),
            runtime_dir: format!("ei-{generation}"),
        }
    }
}

/// Pin data is captured from the selected connection. SSH authentication and the host key
/// check must have succeeded before executing the resulting command.
pub fn capability_command(waypipe: &str, waypipe_hash: &str) -> Result<String, &'static str> {
    if !absolute_path(waypipe) || !hex(waypipe_hash, WAYPIPE_HASH_LEN) {
        return Err("WAYPIPE_PIN_INVALID");
    }
    Ok(ssh_command(
        PROBE,
        &[
            waypipe,
            waypipe_hash,
            CHROME_EXECUTABLES[0],
            CHROME_EXECUTABLES[1],
        ],
    ))
}

fn ssh_command(script: &str, argv: &[&str]) -> String {
    let mut command = format!("/bin/sh -c {} eidolon-systemd", shell_arg(script));
    for argument in argv {
        command.push(' ');
        command.push_str(&shell_arg(argument));
    }
    command
}

fn shell_arg(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Capabilities {
    pub boot_id: String,
    pub uid: u32,
    pub home: PathBuf,
    pub waypipe: PathBuf,
    pub chrome: PathBuf,
}

impl Capabilities {
    pub fn parse(stdout: &[u8]) -> Result<Self, &'static str> {
        const INVALID: &str = "SYSTEMD_CAPABILITIES_INVALID";
        if stdout.len() > 4096 {
            return Err(INVALID);
        }
        let mut lines = std::str::from_utf8(stdout)
            .map_err(|_| INVALID)?
            .strip_suffix('\n')
            .ok_or(INVALID)?
            .split('\n');
        let [
            Some(boot_id),
            Some(uid),
            Some(home),
            Some(waypipe),
            Some(chrome),
            None,
        ] = std::array::from_fn(|_| lines.next())
        else {
            return Err(INVALID);
        };
        let numeric_uid = uid
            .parse::<u32>()
            .ok()
            .filter(|number| *number != 0 && *number != u32::MAX && number.to_string() == uid)
            .ok_or(INVALID)?;
        if !boot_identity(boot_id) || ![home, waypipe, chrome].into_iter().all(absolute_path) {
            return Err(INVALID);
        }
        Ok(Self {
            boot_id: boot_id.into(),
            uid: numeric_uid,
            home: home.into(),
            waypipe: waypipe.into(),
            chrome: chrome.into(),
        })
    }

    pub fn forward_socket(&self, generation: Generation) -> Result<String, &'static str> {
        let units = UnitNames::from(generation);
        let path = format!("/run/user/{}/{}/p", self.uid, units.runtime_dir);
        if path.len() > SSH_SOCKET_BYTES {
            return Err("SYSTEMD_SOCKET_PATH_TOO_LONG");
        }
        Ok(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BOOT_ID: &str = "8d57f608-6975-4ce8-b0e0-c1bb726d6e66";
    const GENERATION: &str = "0001112233445566778899aabbccddee";

    #[test]
    fn capability_probe_treats_pins_as_data_and_has_no_user_argv() {
        assert!(
            capability_command("/opt/waypipe", &"a".repeat(64))
                .unwrap()
                .ends_with(&format!(
                    " '/opt/waypipe' '{}' 'google-chrome-stable' 'google-chrome'",
                    "a".repeat(64)
                ))
        );
        for invalid_path in [
            "/",
            "waypipe",
            "/opt/../waypipe",
            "/opt/waypipe
false",
        ] {
            assert!(capability_command(invalid_path, &"a".repeat(64)).is_err());
        }
        for invalid in ["", "xyz", "pretend'; false", &"A".repeat(64)] {
            assert!(capability_command("/opt/waypipe", invalid).is_err());
        }
    }

    #[test]
    fn captures_exactly_one_non_root_uid_and_safe_paths() {
        let raw = format!(
            "{BOOT_ID}\n1000\n/home/test user's\n/usr/bin/waypipe\n/opt/google/chrome/chrome\n"
        );
        let capability = Capabilities::parse(raw.as_bytes()).unwrap();
        assert_eq!(capability.home, PathBuf::from("/home/test user's"));
        let generation = Generation::parse(GENERATION).unwrap();
        assert_eq!(generation.to_string(), GENERATION);
        assert_eq!(
            capability.forward_socket(generation).unwrap(),
            format!("/run/user/1000/ei-{GENERATION}/p")
        );
        assert_eq!(
            UnitNames::from(generation).expiry_service,
            format!("eidolon-exp-{GENERATION}.service")
        );
        assert!(Generation::parse(&GENERATION.to_uppercase()).is_none());
        assert!(Generation::parse("../../../wait").is_none());
        for malformed in [
            raw.replace("1000", "0"),
            raw.replace("1000", "01000"),
            raw.replace("/usr/bin/", "/usr/../"),
            raw.replace("/usr/bin/", "//usr/bin/"),
            raw.replace("/usr/bin/", "usr/bin/"),
            raw.replace("/usr/bin/", "/usr/\tbin/"),
            raw.trim_end().to_string(),
            format!("{raw}extra\n"),
        ] {
            assert!(
                Capabilities::parse(malformed.as_bytes()).is_err(),
                "{malformed:?}"
            );
        }
    }

    #[test]
    fn argv_round_trips_literal_quotes_shell_dollars_and_semicolons() {
        let out = std::process::Command::new("/bin/sh")
            .arg("-c")
            .arg(ssh_command(
                "printf '%s\\n' \"$@\"",
                &["'$HOME; /do-not-run'"],
            ))
            .output()
            .unwrap();
        assert!(out.status.success());
        assert_eq!(
            std::str::from_utf8(&out.stdout).unwrap(),
            "'$HOME; /do-not-run'\n"
        );
    }
}
