use super::super::v3::installed::{self, InstalledApp};
use super::{CHROME_DESKTOP_ID, Capabilities, absolute_path, hash, shell_arg};
use eidolon_model::linux_desktop_v3::Hash;
use russh::client;
use serde::Deserialize;
use tokio::time::{Duration, timeout};
use tokio_util::sync::CancellationToken;

pub use installed::InstalledApp as CatalogApp;
const STARTUP_ARGV_BYTES: usize = 12 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DesktopCommand {
    argv: Vec<String>,
    cwd: Option<String>,
}

pub(crate) struct Application {
    pub id: String,
    pub argv: Vec<String>,
    pub cwd: Option<String>,
}

impl Application {
    pub fn chrome(capability: &Capabilities) -> Result<Self, &'static str> {
        let executable = capability
            .chrome
            .as_ref()
            .and_then(|path| path.to_str())
            .ok_or("SYSTEMD_APP_UNAVAILABLE")?;
        Ok(Self {
            id: CHROME_DESKTOP_ID.into(),
            argv: vec![executable.into()],
            cwd: None,
        })
    }

    pub fn app_key(&self) -> String {
        hash(self.id.as_bytes())
    }

    pub fn is_chrome(&self) -> bool {
        self.id == CHROME_DESKTOP_ID
    }
}

pub(crate) async fn discover<H: client::Handler>(
    client: &client::Handle<H>,
    cancel: &CancellationToken,
) -> Result<Vec<CatalogApp>, &'static str> {
    installed::discover(client, cancel).await
}

pub(crate) async fn resolve<H: client::Handler>(
    client: &client::Handle<H>,
    catalog: &[InstalledApp],
    revision: &Hash,
    cancel: &CancellationToken,
) -> Result<Application, &'static str> {
    let entry = catalog
        .iter()
        .find(|item| item.revision() == revision && item.available())
        .ok_or("SYSTEMD_CATALOG_CHANGED")?;
    if entry.id() == CHROME_DESKTOP_ID {
        return Err("SYSTEMD_CATALOG_CHANGED");
    }
    let command = format!(
        "exec /usr/bin/python3 -I -B -c {} resolve {}",
        shell_arg(installed::SOURCE),
        shell_arg(revision.as_str()),
    );
    let read = async {
        let channel = client
            .channel_open_session()
            .await
            .map_err(|_| "SSH_IO_FAILED")?;
        let payload = crate::exec::json_bounded(channel, command, STARTUP_ARGV_BYTES).await?;
        let command: DesktopCommand =
            serde_json::from_value(payload).map_err(|_| "SYSTEMD_CATALOG_CHANGED")?;
        validate(&command)?;
        Ok(Application {
            id: entry.id().into(),
            argv: command.argv,
            cwd: command.cwd,
        })
    };
    tokio::select! {
        biased;
        _ = cancel.cancelled() => Err("CANCELLED"),
        result = timeout(Duration::from_secs(5), read) => result.unwrap_or(Err("SYSTEMD_DISCOVERY_TIMEOUT")),
    }
}

fn validate(command: &DesktopCommand) -> Result<(), &'static str> {
    let Some(executable) = command.argv.first().filter(|a| absolute_path(a)) else {
        return Err("SYSTEMD_EXEC_INVALID");
    };
    if executable.len() > 1024
        || command.argv.len() > 64
        || command
            .argv
            .iter()
            .any(|a| a.len() > 2048 || a.chars().any(char::is_control))
        || command.cwd.as_ref().is_some_and(|cwd| !absolute_path(cwd))
    {
        return Err("SYSTEMD_EXEC_INVALID");
    }
    Ok(())
}
