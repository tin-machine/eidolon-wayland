use super::chrome::chrome_app_key;
use super::{
    ActiveState, Capabilities, ChromeControl, ChromeStorage, ExchangeError, Generation,
    InvocationId, Journal, JournalRecord, UnitStatus, capability_command, chrome_control_command,
    chrome_start_command, exchange, inspect_command,
};
use crate::forward::{Lease, Router};
use eidolon_model::{
    connection_profile::Profile,
    gui_session_policy::GuiUnresponsiveGrace,
    ids::SessionId,
    linux_desktop_v3::{StorageCapture, StorageRecord},
};
use russh::client;
use std::{os::unix::fs::FileTypeExt, path::Path};
use tokio::time::{Duration, Instant, sleep, timeout};
use tokio_util::sync::CancellationToken;

const FORWARD_TIMEOUT: Duration = Duration::from_secs(5);
const STARTUP_PROBE_WINDOW: Duration = Duration::from_secs(8);

/// Immutable connection and storage data captured before the owner gets permission to start.
pub struct ChromeTarget<'a> {
    pub profile: &'a Profile,
    pub storage: &'a StorageRecord,
    pub files: &'a Path,
    pub waypipe_path: &'a str,
    pub waypipe_hash: &'a str,
    pub uid: u32,
    pub session: SessionId,
}

/// An SSH-connected owner for one Chrome session. Until the compositor calls
/// accept_display, the remote service is only starting, even if forwarding succeeded.
pub struct ChromeOwner<'a, H: client::Handler> {
    client: &'a client::Handle<H>,
    router: &'a Router,
    target: ChromeTarget<'a>,
    capability: Capabilities,
    journal: Journal,
    route: Option<Lease>,
    forward_established: bool,
    forward_may_exist: bool,
}

impl<'a, H: client::Handler> ChromeOwner<'a, H> {
    pub async fn connect(
        client: &'a client::Handle<H>,
        router: &'a Router,
        target: ChromeTarget<'a>,
        cancel: &CancellationToken,
    ) -> Result<Self, &'static str> {
        if !router.belongs_to(target.session) {
            return Err("STALE");
        }
        let capture =
            StorageCapture::from_profile(target.profile).map_err(|_| "SYSTEMD_PROFILE_INVALID")?;
        target
            .storage
            .matches_capture(&capture)
            .map_err(|_| "SYSTEMD_PROFILE_INVALID")?;
        let probe = capability_command(target.waypipe_path, target.waypipe_hash)?;
        let stdout = exchange(client, probe, cancel, || Ok(()))
            .await
            .map_err(exchange_code)?;
        let capability = Capabilities::parse(&stdout)?;
        if capability.uid != target.uid {
            return Err("SYSTEMD_OWNER_MISMATCH");
        }
        let journal = Journal::open(
            target.files,
            target.storage.host_user_binding().as_str(),
            target.storage.profile_storage_id(),
            &chrome_app_key(),
        )?;
        Ok(Self {
            client,
            router,
            target,
            capability,
            journal,
            route: None,
            forward_established: false,
            forward_may_exist: false,
        })
    }

    pub fn transport_connected(&self) -> bool {
        !self.client.is_closed()
    }

    pub fn pending(&self) -> Option<&JournalRecord> {
        self.journal.record()
    }

    fn storage(&self) -> ChromeStorage<'a> {
        ChromeStorage {
            installation: self.target.storage.installation_id(),
            profile: self.target.storage.profile_storage_id(),
        }
    }

    pub async fn start(
        &mut self,
        local_socket: &Path,
        grace: GuiUnresponsiveGrace,
        cancel: &CancellationToken,
    ) -> Result<Generation, &'static str> {
        if self.pending().is_some() {
            return Err("SYSTEMD_CLEANUP_PENDING");
        }
        if !local_socket.is_absolute()
            || local_socket.as_os_str().len() > 103
            || !std::fs::symlink_metadata(local_socket).is_ok_and(|m| m.file_type().is_socket())
        {
            return Err("FORWARD_SETUP_FAILED");
        }
        let generation = Generation::new();
        let remote_socket = self.capability.forward_socket(generation)?;
        let command = chrome_start_command(
            generation,
            &self.capability,
            self.storage(),
            self.target.waypipe_hash,
            grace,
        )?;
        self.journal
            .reserve(generation, &self.capability.boot_id, grace)?;
        let response = exchange(self.client, command, cancel, || {
            self.journal
                .sent(generation)
                .map_err(|_| ExchangeError::NotSent)
        })
        .await
        .map_err(exchange_code)?;
        if response != b"starting\n" {
            return Err("SYSTEMD_LAUNCH_UPDATE_UNKNOWN");
        }
        let invocation = self.await_active(generation, cancel).await?;
        self.journal.identify(generation, invocation)?;
        // Once the service is active its RuntimeDirectory exists; the service itself
        // waits for this socket before it starts Waypipe or Chrome.
        let lease = self.router.arm(
            self.target.session,
            remote_socket.clone(),
            local_socket.into(),
        )?;
        self.route = Some(lease);
        self.forward_may_exist = true;
        timeout(
            FORWARD_TIMEOUT,
            self.client.streamlocal_forward(remote_socket),
        )
        .await
        .map_err(|_| "FORWARD_TIMEOUT")?
        .map_err(|_| "FORWARD_REJECTED")?;
        Ok(generation)
    }

    pub fn accept_display(&mut self, generation: Generation) -> Result<(), &'static str> {
        if !self.forward_established || self.route.is_none() {
            return Err("FORWARD_SETUP_FAILED");
        }
        self.journal.running(generation)
    }

    pub async fn inspect(
        &self,
        generation: Generation,
        cancel: &CancellationToken,
    ) -> Result<UnitStatus, &'static str> {
        let command = inspect_command(generation, &self.capability);
        let stdout = exchange(self.client, command, cancel, || Ok(()))
            .await
            .map_err(exchange_code)?;
        UnitStatus::parse(generation, &stdout)
    }

    async fn await_active(
        &self,
        generation: Generation,
        cancel: &CancellationToken,
    ) -> Result<InvocationId, &'static str> {
        let deadline = Instant::now() + STARTUP_PROBE_WINDOW;
        while Instant::now() < deadline {
            match self.inspect(generation, cancel).await? {
                UnitStatus::Loaded {
                    active_state: ActiveState::Active,
                    invocation: Some(invocation),
                    control_group: Some(_),
                    ..
                } => return Ok(invocation),
                UnitStatus::Loaded {
                    active_state: ActiveState::Failed,
                    ..
                } => {
                    return Err("SYSTEMD_START_FAILED");
                }
                _ => {}
            }
            tokio::select! {
                biased;
                _ = cancel.cancelled() => return Err("CANCELLED"),
                _ = sleep(Duration::from_millis(75)) => {}
            }
        }
        Err("SYSTEMD_START_TIMEOUT")
    }

    /// Caller confirms the activity owner and serial before asking the host to renew.
    pub async fn renew(
        &self,
        generation: Generation,
        cancel: &CancellationToken,
    ) -> Result<Vec<u8>, &'static str> {
        let command = self.control(ChromeControl::Renew, generation)?;
        exchange(self.client, command, cancel, || Ok(()))
            .await
            .map_err(exchange_code)
    }

    pub async fn request_stop(&mut self, generation: Generation) -> Result<(), &'static str> {
        self.journal.stopping(generation)?;
        let command = self.control(ChromeControl::Stop, generation)?;
        let cancel = CancellationToken::new();
        exchange(self.client, command, &cancel, || Ok(()))
            .await
            .map_err(exchange_code)?;
        Ok(())
    }

    /// Call even after a lost forward-registration ACK so the route stops accepting channels.
    pub async fn close_forward(&mut self, generation: Generation) -> Result<(), &'static str> {
        let path = self.capability.forward_socket(generation)?;
        if self.pending().and_then(JournalRecord::generation) != Some(generation) {
            return Err("STALE");
        }
        self.forward_established = false;
        if let Some(route) = self.route.take() {
            route.close().await;
        }
        if self.forward_may_exist {
            if !self.client.is_closed() {
                timeout(
                    FORWARD_TIMEOUT,
                    self.client.cancel_streamlocal_forward(path),
                )
                .await
                .map_err(|_| "FORWARD_TIMEOUT")?
                .map_err(|_| "FORWARD_REJECTED")?;
            }
            self.forward_may_exist = false;
        }
        Ok(())
    }

    /// Check host-side retirement on the pinned connection before retiring the host journal proof.
    pub async fn finish_remote(&mut self, generation: Generation) -> Result<(), &'static str> {
        let pending = self
            .pending()
            .filter(|p| p.generation() == Some(generation))
            .ok_or("STALE")?;
        let old_boot_ended = pending.boot_id() != self.capability.boot_id;
        let can_reap_timer = pending.invocation().is_some();
        self.journal.stopping(generation)?;
        let cancel = CancellationToken::new();
        if !old_boot_ended {
            if can_reap_timer {
                // A valid invocation proves the original Start had already passed.
                let command = self.control(ChromeControl::ReapAfterExit, generation)?;
                let _ = exchange(self.client, command, &cancel, || Ok(())).await;
            } else {
                // An absent unit cannot prove that a previously unconfirmed Start
                // won't subsequently reach systemd.
                return Err("SYSTEMD_START_OUTCOME_UNKNOWN");
            }
            let command = super::retired_command(generation, &self.capability);
            let bytes = exchange(self.client, command, &cancel, || Ok(()))
                .await
                .map_err(exchange_code)?;
            if bytes != b"retired\n" {
                return Err("SYSTEMD_CLEANUP_PENDING");
            }
        }
        self.journal.retire(generation, false, true)?;
        Ok(())
    }

    pub fn finish_local(&mut self, generation: Generation) -> Result<bool, &'static str> {
        if self.route.is_some() || self.forward_may_exist {
            return Err("SYSTEMD_CLEANUP_PENDING");
        }
        self.journal.retire(generation, true, false)
    }

    fn control(
        &self,
        operation: ChromeControl,
        generation: Generation,
    ) -> Result<String, &'static str> {
        let pending = self
            .pending()
            .filter(|p| p.generation() == Some(generation))
            .ok_or("STALE")?;
        if operation == ChromeControl::Renew && pending.stage() != super::JournalStage::Running {
            return Err("SYSTEMD_LEASE_NOT_WAITING");
        }
        if pending.boot_id() != self.capability.boot_id {
            return Err("SYSTEMD_OWNER_MISMATCH");
        }
        let invocation = pending.invocation().ok_or("SYSTEMD_INVOCATION_UNKNOWN")?;
        chrome_control_command(
            operation,
            generation,
            invocation,
            &self.capability,
            self.storage(),
        )
    }
}

fn exchange_code(error: ExchangeError) -> &'static str {
    match error {
        ExchangeError::NotSent => "SYSTEMD_COMMAND_NOT_SENT",
        ExchangeError::OutcomeUnknown => "SYSTEMD_COMMAND_OUTCOME_UNKNOWN",
        ExchangeError::Rejected(_) => "SYSTEMD_COMMAND_REJECTED",
    }
}
