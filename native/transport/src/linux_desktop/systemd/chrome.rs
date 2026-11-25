use super::super::hash;
use super::{Capabilities, Generation, InvocationId, UnitNames, absolute_path, hex, ssh_command};
use eidolon_model::gui_session_policy::GuiUnresponsiveGrace;
use eidolon_model::linux_desktop_v3::{ProfileStorageId, StorageInstallationId};

const START: &str = include_str!("chrome-start.sh");
const CONTROL: &str = include_str!("chrome-control.sh");
pub const CHROME_DESKTOP_ID: &str = "google-chrome.desktop";

pub fn chrome_app_key() -> String {
    hash(CHROME_DESKTOP_ID.as_bytes())
}

/// The selected connection's durable storage identity. Both IDs must come from the
/// storage record verified against the captured SSH host and account.
pub struct ChromeStorage<'a> {
    pub installation: &'a StorageInstallationId,
    pub profile: &'a ProfileStorageId,
}

pub fn chrome_start_command(
    generation: Generation,
    capability: &Capabilities,
    storage: ChromeStorage<'_>,
    waypipe_hash: &str,
    grace: GuiUnresponsiveGrace,
) -> Result<String, &'static str> {
    if !hex(waypipe_hash, 64) {
        return Err("WAYPIPE_PIN_INVALID");
    }
    let home = capability.home.to_str().filter(|s| absolute_path(s));
    let waypipe = capability.waypipe.to_str().filter(|s| absolute_path(s));
    let chrome = capability.chrome.to_str().filter(|s| absolute_path(s));
    let (Some(home), Some(waypipe), Some(chrome)) = (home, waypipe, chrome) else {
        return Err("SYSTEMD_CAPABILITIES_INVALID");
    };
    let app_key = chrome_app_key();
    let units = UnitNames::from(generation);
    let expiry = units.expiry_timer.trim_end_matches(".timer");
    Ok(ssh_command(
        START,
        &[
            &capability.uid.to_string(),
            &capability.boot_id,
            home,
            storage.installation.as_str(),
            storage.profile.as_str(),
            &app_key,
            &units.application,
            expiry,
            &units.runtime_dir,
            &grace.seconds().to_string(),
            waypipe,
            waypipe_hash,
            chrome,
        ],
    ))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ChromeControl {
    Renew,
    Stop,
    ReapAfterExit,
}

pub fn chrome_control_command(
    control: ChromeControl,
    generation: Generation,
    invocation: InvocationId,
    capability: &Capabilities,
    storage: ChromeStorage<'_>,
) -> Result<String, &'static str> {
    let home = capability
        .home
        .to_str()
        .filter(|s| absolute_path(s))
        .ok_or("SYSTEMD_CAPABILITIES_INVALID")?;
    let app_key = chrome_app_key();
    let units = UnitNames::from(generation);
    Ok(ssh_command(
        CONTROL,
        &[
            match control {
                ChromeControl::Renew => "renew",
                ChromeControl::Stop => "stop",
                ChromeControl::ReapAfterExit => "reap-after-exit",
            },
            &capability.uid.to_string(),
            &capability.boot_id,
            &invocation.to_string(),
            &units.application,
            &units.expiry_timer,
            &units.expiry_service,
            home,
            storage.installation.as_str(),
            storage.profile.as_str(),
            &app_key,
        ],
    ))
}
