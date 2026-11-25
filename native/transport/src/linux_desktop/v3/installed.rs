//! Read-only installed application discovery on the authenticated desktop account.
use eidolon_model::linux_desktop_v3::{AppId, Hash};
use russh::client;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use tokio::time::{Duration, timeout};
use tokio_util::sync::CancellationToken;

pub(crate) const SOURCE: &str =
    include_str!("../../../../../scripts/linux/eidolon_desktop_entries.py");
const MAX_CATALOG_BYTES: usize = 512 * 1024;
const MAX_ENTRIES: usize = 1024;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct InstalledApp {
    id: String,
    name: String,
    revision: Hash,
    disabled_reason: Option<DisabledReason>,
}

#[derive(Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum DisabledReason {
    TerminalRequired,
    DbusActivationRequired,
    ExecutableUnavailable,
    ExecFormatUnsupported,
    WorkdirUnavailable,
}

impl InstalledApp {
    pub(crate) fn id(&self) -> &str {
        &self.id
    }
    pub(crate) fn revision(&self) -> &Hash {
        &self.revision
    }
    pub(crate) fn available(&self) -> bool {
        self.disabled_reason.is_none()
    }

    pub(crate) fn selection_id(&self) -> Option<String> {
        self.disabled_reason
            .is_none()
            .then(|| format!("{}{}", AppId::DESKTOP_ENTRY_PREFIX, self.revision.as_str()))
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InstalledCatalog {
    schema: u32,
    entries: Vec<InstalledApp>,
}

pub(crate) async fn discover<H: client::Handler>(
    client: &client::Handle<H>,
    cancel: &CancellationToken,
) -> Result<Vec<InstalledApp>, &'static str> {
    let read = async {
        let channel = client
            .channel_open_session()
            .await
            .map_err(|_| "SSH_IO_FAILED")?;
        let command = format!(
            "exec /usr/bin/python3 -I -B -c '{}'",
            SOURCE.replace('\'', "'\\''"),
        );
        let payload = crate::exec::json_bounded(channel, command, MAX_CATALOG_BYTES).await?;
        let catalog: InstalledCatalog =
            serde_json::from_value(payload).map_err(|_| "DESKTOP_DISCOVERY_INVALID")?;
        let mut seen_ids = BTreeSet::new();
        let mut seen_revisions = BTreeSet::new();
        if catalog.schema != 1
            || catalog.entries.len() > MAX_ENTRIES
            || catalog.entries.iter().any(|entry| {
                !entry.id.ends_with(".desktop")
                    || entry.id.len() > 255
                    || entry.name.is_empty()
                    || entry.name.len() > 255
                    || entry
                        .id
                        .chars()
                        .chain(entry.name.chars())
                        .any(char::is_control)
                    || !seen_ids.insert(&entry.id)
                    || !seen_revisions.insert(entry.revision.as_str())
            })
        {
            return Err("DESKTOP_DISCOVERY_INVALID");
        }
        Ok(catalog.entries)
    };
    tokio::select! {
        biased;
        _ = cancel.cancelled() => Err("CANCELLED"),
        result = timeout(Duration::from_secs(5), read) => result.map_err(|_| "DESKTOP_DISCOVERY_TIMEOUT")?,
    }
}
