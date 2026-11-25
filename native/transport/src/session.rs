//! SSHはI/O ownerが作るTokio runtime上だけで実行する。再接続前に旧runtimeを回収する。
use crate::linux_desktop::v3 as desktop_v3;
use crate::{
    connect_profile::{ConnectSpec, DesktopConfig},
    exec, forward, forward_app, forward_probe,
    host_key::Profile,
    journal, launcher, linux_desktop,
};
use eidolon_model::{
    commands::{AppMode, CleanupScenario},
    ids::{CommandSeq, RuntimeHandle, SessionId},
    state::Status,
};
use russh::{
    client,
    keys::{PrivateKey, PrivateKeyWithHashAlg, PublicKey, PublicKeyOrCertificate},
};
use serde::Serialize;
use std::{
    net::{Ipv4Addr, SocketAddr},
    path::{Path, PathBuf},
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread::{self, JoinHandle},
    time::Duration,
};
use tokio::{
    sync::Notify,
    time::{Instant, timeout, timeout_at},
};
use tokio_util::sync::CancellationToken;

pub(crate) fn ssh_config() -> client::Config {
    client::Config {
        window_size: 256 * 1024,
        channel_buffer_size: 128,
        ..Default::default()
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug, Default, Serialize)]
pub enum State {
    #[default]
    Idle,
    Connecting,
    AwaitingCredential,
    Authenticating,
    ProbeReady,
    DesktopReady,
    Ready,
    Recovering,
    CleanupPending,
    Disconnected,
    Disconnecting,
    Failed,
    Stopped,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Snapshot {
    // Wake the UI when its authenticated catalog/preview completes without copying the catalog.
    #[serde(skip)]
    pub catalog_progress: (Option<CommandSeq>, bool, Option<(CommandSeq, bool)>),
    #[serde(skip)]
    pub authenticated_termux: bool,
    pub state: State,
    pub session: Option<SessionId>,
    pub profile_id: Option<String>,
    pub profile_revision: Option<String>,
    pub credential_request: Option<CommandSeq>,
    pub workers: usize,
    pub tokio_threads: usize,
    pub owned_connections: usize,
    pub credential_requests: u64,
    pub error: Option<&'static str>,
    pub probe: exec::Report,
    pub forward: forward::Report,
    pub forward_probe: Option<forward_probe::Report>,
    pub app_forward: Option<forward_app::Report>,
    pub desktop: Option<linux_desktop::Report>,
    pub desktop_recovery: Option<linux_desktop::RecoveryReport>,
    /// I2–I5 typed v3 evidence; absent until a real v3 controller emits it.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub desktop_v3: Option<eidolon_model::linux_desktop_v3::report::V3Report>,
    /// I2 transport only; no SDK-visible Ready can be inferred from this.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub desktop_v3_transport: Option<desktop_v3::runtime::TransportSnapshot>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub desktop_v3_recovery: Option<desktop_v3::recovery::RecoveryReport>,
    /// Dedicated normal Linux owner; independent from Termux pending_cleanup.
    pub desktop_pending: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub systemd_chrome: Option<linux_desktop::systemd::Report>,
    pub desktop_reboot_needed: bool,
    #[cfg(any(test, feature = "debug-fixtures"))]
    pub linux_forward: Option<crate::linux_probe::Report>,
    #[cfg(any(test, feature = "debug-fixtures"))]
    pub linux_recovery: Option<crate::linux_probe::RecoveryReport>,
    pub journal_ready: bool,
    pub pending_cleanup: Vec<journal::Pending>,
    pub cleanup_audits: Vec<CleanupAudit>,
}
/// An owner-side cut of work and retained recovery authority. This is not a
/// journal proof and cannot be deserialized or supplied by an Android caller.
#[derive(Clone, Debug)]
pub struct ReclamationState {
    pub pending_operations: usize,
    pub remote_cleanup_pending: bool,
    pub recovery_connect_allowed: bool,
    pub recovery_command_allowed: bool,
    pub no_local_allocation_proven: bool,
    pub durable_v3_local_proven: bool,
    pub v3_remote_only_recovery: bool,
    pub v2_capture_proven: bool,
}
#[derive(Clone, Debug, Serialize)]
pub struct CleanupAudit {
    pub nonce: String,
    pub origin_session: SessionId,
    pub current_session: SessionId,
    pub verified: bool,
    pub child: Option<launcher::Identity>,
    pub active: bool,
    pub orphaned: bool,
    pub child_alive: bool,
    pub reason: Option<String>,
    pub error: Option<&'static str>,
}
impl Snapshot {
    /// The SSH probe and the old launch's cleanup audit use the same authenticated
    /// connection. A missing termination receipt blocks launches on it, but the
    /// shelf command only reads the app list.
    pub fn termux_shelf_readable(&self) -> bool {
        self.authenticated_termux
            && self.owned_connections == 1
            && !self.desktop_pending
            && (self.state == State::Ready
                || (self.state == State::CleanupPending
                    && self.error == Some("CLEANUP_PENDING")
                    && !self.pending_cleanup.is_empty()))
    }
}
#[derive(Clone, Copy)]
struct Deadlines {
    connect: Duration,
    auth: Duration,
    exec: Duration,
}
impl Default for Deadlines {
    fn default() -> Self {
        Self {
            connect: Duration::from_secs(5),
            auth: Duration::from_secs(10),
            exec: Duration::from_secs(10),
        }
    }
}
struct PendingConnect {
    profile: ConnectSpec,
    request: CommandSeq,
    cancel: CancellationToken,
    discovery_previous: Option<Snapshot>,
}
struct Connect {
    session: SessionId,
    router: Arc<forward::Router>,
    profile: ConnectSpec,
    request: CommandSeq,
    cancel: CancellationToken,
    discovery_only: bool,
}
struct Shared {
    ime_endpoint: Option<crate::input_method::Endpoint>,
    report: Snapshot,
    connect: Option<PendingConnect>,
    credential: Option<PrivateKey>,
    credential_scope: Option<CredentialScope>,
    busy: bool,
    active_connect_request: Option<CommandSeq>,
    cancel: Option<CancellationToken>,
    router: Option<Arc<forward::Router>>,
    forward: Option<(PathBuf, String, Option<CleanupScenario>)>,
    app: Option<(
        PathBuf, String, AppMode, Option<crate::catalog::ShelfStart>,
        Option<eidolon_model::gui_session_policy::GuiUnresponsiveGrace>,
    )>,
    termux_ticks: Option<tokio::sync::watch::Sender<Option<eidolon_model::gui_session_lifecycle::LivenessTick>>>,
    desktop_profile: Option<DesktopConfig>,
    desktop_pending: Result<Option<linux_desktop::Pending>, &'static str>,
    // Only the authenticated in-process actor may mint this capture. A cold
    // journal can still use v2's existing recovery, but is not local evidence
    // for a settings-save barrier in a new Runtime.
    desktop_v2_capture: Option<linux_desktop::Binding>,
    desktop_v2_io_retired: bool,
    desktop_work: Option<DesktopWork>,
    desktop_launches: Option<tokio::sync::mpsc::Sender<linux_desktop::Launch>>,
    desktop_launch_queued: bool,
    desktop_v3_pending: Result<Option<desktop_v3::journal::Pending>, &'static str>,
    desktop_v3_work: Option<DesktopV3Work>,
    desktop_v3_launch: Option<Arc<desktop_v3::runtime::LaunchAdmission>>,
    // Survives an unknown remote cleanup and a new SSH connection. A recovery
    // bridge needs the original live capture, never a reconstructed report.
    desktop_v3_capture: Option<Arc<desktop_v3::runtime::RunCapture>>,
    desktop_v3_no_local_allocation: Option<desktop_v3::runtime::NoLocalAllocation>,
    desktop_v3_selections: Option<Vec<desktop_v3::Selection>>,
    systemd_requests: Option<tokio::sync::mpsc::Sender<linux_desktop::systemd::Request>>,
    systemd_launch_queued: bool,
    systemd_ticks: Option<tokio::sync::watch::Sender<Option<linux_desktop::systemd::Tick>>>,
    desktop_v3_handoff: Option<desktop_v3::reclamation::ReclamationHandoff>,
    desktop_v3_io_retired: bool,
    desktop_v3_finalization_pending: bool,
    #[cfg(any(test, feature = "debug-fixtures"))]
    linux: Option<LinuxWork>,
    forward_busy: bool,
    terminal_requests:
        Option<tokio::sync::mpsc::Sender<(CommandSeq, Option<crate::catalog::Target>)>>,
    catalog_requests: Option<tokio::sync::mpsc::Sender<(CommandSeq, crate::catalog::Operation)>>,
    catalog: CatalogUi,
    shelf_request: Option<(SessionId, CommandSeq, String, bool)>,
    shelf_result: Option<(SessionId, serde_json::Value)>,
    desktop_preview: Option<DesktopPreview>,
}
#[derive(Clone, Copy)]
enum DesktopWork {
    Inspect(CommandSeq),
    Recover(CommandSeq),
}
#[derive(Clone, Copy)]
enum DesktopV3Work {
    Inspect(CommandSeq),
    Recover(CommandSeq),
}
#[cfg(any(test, feature = "debug-fixtures"))]
struct LinuxWork {
    files: PathBuf,
    local_socket: Option<PathBuf>,
    nonce: String,
    pins: crate::linux_probe::Pins,
    gtk: Option<crate::linux_probe::GtkBinding>,
}
/// UI専用。通常Snapshot/receiptへのserialize経路から分離する。
#[derive(Clone, Default, Serialize)]
pub struct CatalogUi {
    pub session: Option<SessionId>,
    pub request_id: Option<CommandSeq>,
    pub pending: bool,
    pub reply: Option<crate::catalog::Reply>,
    pub error: Option<&'static str>,
    pub desktop: Option<DesktopCatalogUi>,
    /// Exact B1 descriptor, published only after the authenticated Inspect.
    pub desktop_v3: Option<serde_json::Value>,
    pub desktop_v3_installed: Option<Vec<desktop_v3::installed::InstalledApp>>,
    pub desktop_v3_editor_available: bool,
    pub desktop_preview: Option<DesktopPreview>,
    pub termux_shelf: Option<serde_json::Value>,
}
#[derive(Clone, Serialize)]
pub struct DesktopPreview {
    pub request_id: CommandSeq,
    pub pending: bool,
    pub entries: Option<Vec<desktop_v3::installed::InstalledApp>>,
    pub error: Option<&'static str>,
}
impl DesktopPreview {
    fn pending(request_id: CommandSeq) -> Self {
        Self {
            request_id,
            pending: true,
            entries: None,
            error: None,
        }
    }
    fn complete(&mut self, result: Result<Vec<desktop_v3::installed::InstalledApp>, &'static str>) {
        self.pending = false;
        match result {
            Ok(entries) => self.entries = Some(entries),
            Err(error) => {
                self.entries = None;
                self.error = Some(error);
            }
        }
    }
}
#[derive(Clone, Serialize)]
pub struct DesktopCatalogUi {
    pub namespace: &'static str,
    pub selection: linux_desktop::Selection,
    pub label: &'static str,
    pub mode: &'static str,
    pub instance_limit: u32,
}
struct Signals {
    shared: Mutex<Shared>,
    cv: Condvar,
    notify: Notify,
    desktop_menu_notify: Notify,
    stop: AtomicBool,
    tokio_workers: AtomicUsize,
    observation: tokio::sync::watch::Sender<bool>,
    files: Option<PathBuf>,
    android_boot_count: Option<u32>,
    #[cfg(feature = "host-acceptance")]
    host_trace: Arc<crate::host_acceptance::Trace>,
}
impl Signals {
    fn update(&self, action: impl FnOnce(&mut Shared)) {
        action(&mut self.shared.lock().unwrap_or_else(|e| e.into_inner()));
    }
}
pub struct Controller {
    signals: Arc<Signals>,
    worker: JoinHandle<()>,
    #[cfg(test)]
    legacy_test_endpoint: SocketAddr,
}

/// UI/owner専用、通常Snapshotには含めない。鍵の供給先を設定世代まで固定する。
#[derive(Clone)]
pub struct CredentialScope {
    pub session: SessionId,
    pub request: CommandSeq,
    pub profile_id: String,
    pub revision: Option<String>,
    pub binding: String,
}
impl Controller {
    pub fn start() -> std::io::Result<Self> {
        Self::start_with_root(None)
    }
    pub fn start_with_root(root: Option<PathBuf>) -> std::io::Result<Self> {
        Self::start_with_local_boot(root, None)
    }
    pub fn start_with_local_boot(
        root: Option<PathBuf>,
        android_boot_count: Option<u32>,
    ) -> std::io::Result<Self> {
        Self::start_at_with_hook(
            (Ipv4Addr::LOCALHOST, 8022).into(),
            Deadlines::default(),
            root,
            android_boot_count,
            || {},
        )
    }
    #[cfg(test)]
    fn start_on(endpoint: SocketAddr, deadlines: Deadlines) -> std::io::Result<Self> {
        Self::start_at(endpoint, deadlines, None)
    }
    #[cfg(test)]
    fn start_at(
        endpoint: SocketAddr,
        deadlines: Deadlines,
        root: Option<PathBuf>,
    ) -> std::io::Result<Self> {
        Self::start_at_with_hook(endpoint, deadlines, root, None, || {})
    }
    fn start_at_with_hook(
        _legacy_endpoint: SocketAddr,
        deadlines: Deadlines,
        root: Option<PathBuf>,
        android_boot_count: Option<u32>,
        before_owner: impl FnOnce() + Send + 'static,
    ) -> std::io::Result<Self> {
        // Read the normal barrier before a caller can queue its first Connect.
        // An invalid record remains pending; it must not become an empty state
        // merely because the app/Controller was restarted.
        let desktop_pending = root
            .as_deref()
            .map(linux_desktop::pending)
            .unwrap_or(Ok(None));
        let desktop_v3_pending = root
            .as_deref()
            .map(desktop_v3::journal::pending)
            .unwrap_or(Ok(None));
        let desktop_v3_pending = match (&desktop_v3_pending, root.as_deref(), android_boot_count) {
            (Ok(Some(pending)), Some(files), Some(boot))
                if desktop_v3::journal::Journal::remember_legacy_boot(files, pending, boot)
                    .is_ok() =>
            {
                desktop_v3::journal::pending(files)
            }
            _ => desktop_v3_pending,
        };
        let has_desktop_pending =
            !matches!(desktop_pending, Ok(None)) || !matches!(desktop_v3_pending, Ok(None));
        let signals = Arc::new(Signals {
            shared: Mutex::new(Shared {
                ime_endpoint: None,
                report: Snapshot {
                    workers: 1,
                    journal_ready: root.is_none(),
                    desktop_pending: has_desktop_pending,
                    state: if has_desktop_pending {
                        State::CleanupPending
                    } else {
                        State::Idle
                    },
                    ..Default::default()
                },
                connect: None,
                credential: None,
                credential_scope: None,
                busy: false,
                active_connect_request: None,
                cancel: None,
                router: None,
                forward: None,
                app: None,
                termux_ticks: None,
                desktop_profile: None,
                desktop_pending,
                desktop_v2_capture: None,
                desktop_v2_io_retired: false,
                desktop_work: None,
                desktop_launches: None,
                desktop_launch_queued: false,
                desktop_v3_pending,
                desktop_v3_work: None,
                desktop_v3_launch: None,
                desktop_v3_capture: None,
                desktop_v3_no_local_allocation: None,
                desktop_v3_selections: None,
                systemd_requests: None,
                systemd_launch_queued: false,
                systemd_ticks: None,
                desktop_v3_handoff: None,
                desktop_v3_io_retired: false,
                desktop_v3_finalization_pending: false,
                #[cfg(any(test, feature = "debug-fixtures"))]
                linux: None,
                forward_busy: false,
                terminal_requests: None,
                catalog_requests: None,
                catalog: CatalogUi::default(),
                shelf_request: None,
                shelf_result: None,
                desktop_preview: None,
            }),
            cv: Condvar::new(),
            notify: Notify::new(),
            desktop_menu_notify: Notify::new(),
            stop: AtomicBool::new(false),
            tokio_workers: AtomicUsize::new(0),
            observation: tokio::sync::watch::channel(true).0,
            files: root.clone(),
            android_boot_count,
            #[cfg(feature = "host-acceptance")]
            host_trace: Arc::new(crate::host_acceptance::Trace::default()),
        });
        let owned = signals.clone();
        let worker = thread::Builder::new()
            .name("eidolon-ssh-owner".into())
            .spawn(move || {
                let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    before_owner();
                    owner(&owned, deadlines, root.as_deref())
                }));
                owned.update(|s| {
                    s.connect = None;
                    s.credential = None;
                    s.credential_scope = None;
                    s.busy = false;
                    s.active_connect_request = None;
                    s.report.workers = 0;
                    s.report.owned_connections = 0;
                    s.report.credential_request = None;
                    if let Some(preview) = s.desktop_preview.as_mut().filter(|p| p.pending) {
                        preview.pending = false;
                        preview.error = Some("SSH_IO_FAILED");
                        preview.entries = None;
                    }
                    match outcome {
                        Err(_) => {
                            s.report.error = Some("JNI_FAILURE");
                            s.report.state = State::Failed;
                        }
                        Ok(Err(error)) => {
                            s.report.error = Some(error);
                            s.report.state = State::Failed;
                        }
                        Ok(Ok(())) => {
                            s.report.state = if s.report.desktop_pending {
                                State::CleanupPending
                            } else {
                                State::Stopped
                            }
                        }
                    }
                });
            })?;
        Ok(Self {
            signals,
            worker,
            #[cfg(test)]
            legacy_test_endpoint: _legacy_endpoint,
        })
    }
    pub fn connect(&self, profile: Profile, request: CommandSeq) -> Status {
        let profile = match ConnectSpec::legacy(profile) {
            Ok(profile) => profile,
            Err(_) => return Status::InvalidArgument,
        };
        #[cfg(test)]
        let profile = ConnectSpec {
            endpoint: self.legacy_test_endpoint,
            ..profile
        };
        self.connect_spec(profile, request)
    }
    /// Normal Linux uses its captured C1 profile and bundle on this Controller's
    /// persistent files root. Authentication alone grants no helper capability.
    pub fn connect_linux(
        &self,
        profile: eidolon_model::connection_profile::Profile,
        pins: linux_desktop::Pins,
        files: &Path,
        request: CommandSeq,
    ) -> Status {
        if self.signals.files.as_deref() != Some(files) {
            return Status::InvalidArgument;
        }
        let spec = match ConnectSpec::linux_desktop(profile, pins, files.into()) {
            Ok(spec) => spec,
            Err(error) => {
                self.signals.update(|s| {
                    if !s.busy {
                        s.report.error = Some(error.code());
                    }
                });
                return Status::InvalidArgument;
            }
        };
        self.connect_spec(spec, request)
    }
    /// Captures a versioned B1/E1 v3 profile. No v2 fallback, DNS guessing or
    /// synthetic app evidence. Only the native caller can provide these types.
    pub fn connect_linux_desktop_v3(
        &self,
        profile: eidolon_model::connection_profile::Profile,
        configuration: eidolon_model::linux_desktop_v3::configuration::ConnectionConfig,
        storage: eidolon_model::linux_desktop_v3::StorageRecord,
        files: &Path,
        request: CommandSeq,
    ) -> Status {
        if self.signals.files.as_deref() != Some(files) {
            return Status::InvalidArgument;
        }
        let spec = match ConnectSpec::linux_desktop_v3(
            profile,
            configuration,
            storage,
            files.to_owned(),
        ) {
            Ok(value) => value,
            Err(_) => return Status::InvalidArgument,
        };
        self.connect_spec(spec, request)
    }
    pub fn connect_ubuntu_systemd(
        &self,
        profile: eidolon_model::connection_profile::Profile,
        binding: eidolon_model::ubuntu_systemd::RuntimeBinding,
        files: &Path,
        request: CommandSeq,
    ) -> Status {
        if self.signals.files.as_deref() != Some(files) {
            return Status::InvalidArgument;
        }
        match ConnectSpec::ubuntu_systemd(profile, binding, files.into()) {
            Ok(spec) => self.connect_spec(spec, request),
            Err(_) => Status::InvalidArgument,
        }
    }
    pub fn systemd_request(
        &self,
        session: SessionId,
        request: linux_desktop::systemd::Request,
    ) -> Status {
        use linux_desktop::systemd::{Phase, Request};
        let mut s = self.signals.shared.lock().unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        let (Some(report), Some(queue)) = (&s.report.systemd_chrome, &s.systemd_requests) else {
            return Status::NotReady;
        };
        let launch = matches!(&request, Request::Launch { .. });
        let accepted = match &request {
            Request::Launch { .. } => report.phase == Phase::Available && !s.systemd_launch_queued,
            Request::Displayed(request) => report.phase == Phase::Forwarded && report.launch_request == Some(*request),
            Request::Liveness { launch, .. } => report.phase == Phase::Running && report.launch_request == Some(*launch),
            Request::LocalRetired(request) => report.phase == Phase::Stopping && report.launch_request == Some(*request),
        };
        if !accepted {
            return Status::NotReady;
        }
        if let Request::Liveness { launch, scene, sequence } = request {
            let Some(ticks) = &s.systemd_ticks else { return Status::NotReady };
            ticks.send_replace(Some(linux_desktop::systemd::Tick { launch, scene, sequence }));
            return Status::Accepted;
        }
        if queue.try_send(request).is_err() {
            return Status::Busy;
        }
        s.systemd_launch_queued |= launch;
        Status::Accepted
    }
    pub fn clear_gui_liveness(&self) {
        self.signals.update(|s| {
            if let Some(ticks) = &s.systemd_ticks {
                ticks.send_replace(None);
            }
            if let Some(ticks) = &s.termux_ticks {
                ticks.send_replace(None);
            }
        });
    }
    pub fn termux_gui_liveness(
        &self, session: SessionId, scene: eidolon_model::ids::SceneEpoch, sequence: CommandSeq,
    ) -> Status {
        use eidolon_model::gui_session_lifecycle::LivenessTick;
        let s = self.signals.shared.lock().unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if !s.report.authenticated_termux
            || !s.report.app_forward.as_ref().and_then(|r| r.remote.as_ref())
                .is_some_and(|remote| remote.phase == "running" && remote.gui_grace_seconds.is_some())
        {
            return Status::NotReady;
        }
        let Some(ticks) = &s.termux_ticks else { return Status::NotReady };
        let (Ok(owner), Ok(sequence)) = ((scene.get() as u64).try_into(), (sequence.get() as u64).try_into())
            else { return Status::InvalidArgument };
        ticks.send_replace(Some(LivenessTick { owner, sequence }));
        Status::Accepted
    }
    pub fn connect_spec(&self, profile: ConnectSpec, request: CommandSeq) -> Status {
        self.enqueue_connect(profile, request, false)
    }
    pub fn discover_desktop(
        &self,
        profile: &eidolon_model::connection_profile::Profile,
        request: CommandSeq,
    ) -> Status {
        if profile.backend() != eidolon_model::connection_profile::Backend::LinuxSsh {
            return Status::InvalidArgument;
        }
        {
            let mut s = self
                .signals
                .shared
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            if s.busy {
                if self.signals.stop.load(Ordering::Acquire)
                    || !matches!(
                        s.report.state,
                        State::ProbeReady | State::DesktopReady | State::CleanupPending
                    )
                    || !matches!(s.desktop_profile.as_ref(),
                        Some(DesktopConfig::V3 { profile: captured, .. }) if captured == profile)
                    || s.cancel.as_ref().is_none_or(|c| c.is_cancelled())
                {
                    return Status::NotReady;
                }
                if s.desktop_preview
                    .as_ref()
                    .is_some_and(|preview| preview.pending)
                {
                    return Status::Busy;
                }
                s.desktop_preview = Some(DesktopPreview::pending(request));
                self.signals.notify.notify_one();
                self.signals.desktop_menu_notify.notify_one();
                return Status::Accepted;
            }
        }
        match ConnectSpec::from_v2(profile) {
            Ok(spec) => self.enqueue_connect(spec, request, true),
            Err(_) => Status::InvalidArgument,
        }
    }
    fn enqueue_connect(
        &self,
        profile: ConnectSpec,
        request: CommandSeq,
        discovery: bool,
    ) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if self.signals.stop.load(Ordering::Acquire) || self.worker.is_finished() {
            return Status::NotReady;
        }
        // v2 and v3 are selected by a captured configuration; never fall back
        // to the other decoder on pending, a pin mismatch, or a stale profile.
        if s.busy {
            return Status::Busy;
        }
        if s.desktop_v3_handoff.is_some() || s.desktop_v3_finalization_pending {
            // Local retirement for the previous run is still owned by core.
            return Status::NotReady;
        }
        if !discovery {
            let status = Self::admit_desktop_connect(&mut s, &profile);
            if status != Status::Accepted {
                return status;
            }
        }
        let discovery_previous = discovery.then(|| s.report.clone());
        if discovery {
            s.desktop_preview = Some(DesktopPreview::pending(request));
        } else {
            s.desktop_profile = profile.desktop.clone();
            s.desktop_work = None;
            s.desktop_launches = None;
            s.desktop_launch_queued = false;
            s.desktop_v2_io_retired = false;
            s.desktop_v3_work = None;
            s.systemd_requests = None;
            s.systemd_launch_queued = false;
            s.systemd_ticks = None;
            s.termux_ticks = None;
            s.desktop_v3_launch = None;
            s.desktop_v3_selections = None;
            s.desktop_v3_io_retired = false;
            s.catalog = CatalogUi::default();
        }
        s.busy = true;
        s.active_connect_request = Some(request);
        s.ime_endpoint = None;
        s.report = Snapshot {
            state: State::Connecting,
            session: None,
            profile_id: Some(profile.profile_id.clone()),
            profile_revision: profile.revision.clone(),
            workers: 1,
            credential_requests: s.report.credential_requests,
            journal_ready: s.report.journal_ready,
            pending_cleanup: s.report.pending_cleanup.clone(),
            desktop_pending: !matches!(s.desktop_pending, Ok(None))
                || !matches!(s.desktop_v3_pending, Ok(None)),
            ..Default::default()
        };
        let cancel = CancellationToken::new();
        s.credential_scope = None;
        s.cancel = Some(cancel.clone());
        s.router = None;
        s.connect = Some(PendingConnect {
            profile,
            request,
            cancel,
            discovery_previous,
        });
        self.signals.cv.notify_one();
        Status::Accepted
    }
    fn admit_desktop_connect(s: &mut Shared, profile: &ConnectSpec) -> Status {
        match &s.desktop_v3_pending {
            Ok(None) => (),
            Ok(Some(pending)) => {
                if !matches!(&profile.desktop,
                    Some(DesktopConfig::V3 { profile, configuration, .. })
                        if pending.matches(profile, configuration.pins()))
                {
                    s.report.state = State::CleanupPending;
                    s.report.error = Some("DESKTOP_CLEANUP_SCOPE_MISMATCH");
                    return Status::NotReady;
                }
            }
            Err(error) => {
                let error = *error;
                s.report.state = State::CleanupPending;
                s.report.error = Some(error);
                return Status::NotReady;
            }
        }
        match &s.desktop_pending {
            Ok(None) => (),
            Ok(Some(pending)) => {
                if profile.desktop.as_ref().is_none_or(|config| match config {
                    DesktopConfig::V2 { profile, pins, .. } => !pending.matches(profile, pins),
                    DesktopConfig::V3 { .. } | DesktopConfig::Systemd { .. } => true,
                }) {
                    s.report.state = State::CleanupPending;
                    s.report.error = Some("DESKTOP_CLEANUP_SCOPE_MISMATCH");
                    return Status::NotReady;
                }
            }
            Err(error) => {
                let error = *error;
                s.report.state = State::CleanupPending;
                s.report.error = Some(error);
                return Status::NotReady;
            }
        }
        Status::Accepted
    }
    pub fn unknown_profile(&self) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.busy {
            return Status::Busy;
        }
        if s.report.desktop_pending {
            s.report.state = State::CleanupPending;
            s.report.error = Some("DESKTOP_CLEANUP_PENDING");
            return Status::NotReady;
        }
        s.report.state = State::Failed;
        s.report.error = Some("HOST_KEY_UNKNOWN");
        Status::NotReady
    }
    pub fn provide(&self, session: SessionId, request: CommandSeq, key: PrivateKey) -> Status {
        self.provide_scoped(session, request, None, key)
    }
    /// v2はlegacyの無束縛provideを受け付けず、このprivate scopeの照合を必須にする。
    pub fn provide_for(
        &self,
        session: SessionId,
        request: CommandSeq,
        binding: &str,
        key: PrivateKey,
    ) -> Status {
        self.provide_scoped(session, request, Some(binding), key)
    }
    pub fn credential_scope(&self) -> Option<CredentialScope> {
        let s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        (s.report.state == State::AwaitingCredential
            && !self.signals.stop.load(Ordering::Acquire)
            && s.cancel.as_ref().is_some_and(|c| !c.is_cancelled()))
        .then(|| s.credential_scope.clone())
        .flatten()
    }
    fn provide_scoped(
        &self,
        session: SessionId,
        request: CommandSeq,
        binding: Option<&str>,
        key: PrivateKey,
    ) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if self.signals.stop.load(Ordering::Acquire)
            || s.cancel.as_ref().is_some_and(|c| c.is_cancelled())
            || s.report.session != Some(session)
            || s.report.credential_request != Some(request)
            || s.report.state != State::AwaitingCredential
            || s.credential_scope.as_ref().is_none_or(|scope| {
                scope.session != session
                    || scope.request != request
                    || match binding {
                        Some(value) => value != scope.binding,
                        None => scope.revision.is_some(),
                    }
            })
            || s.credential.is_some()
        {
            return Status::Stale;
        }
        s.credential = Some(key);
        self.signals.notify.notify_one();
        Status::Accepted
    }
    pub fn disconnect(&self, session: SessionId) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        // Close native allocation admission before cancellation can send Stop.
        // The proof comes from this live capture; absence of a Session is not it.
        if let Some(admission) = &s.desktop_v3_launch {
            let proof = admission.seal_without_local_allocation();
            #[cfg(feature = "host-acceptance")]
            if proof.is_some() {
                admission.capture().observe(
                    "no_local_allocation_sealed",
                    serde_json::json!({"before_cancel":true}),
                );
            }
            if proof.is_some() {
                s.desktop_v3_no_local_allocation = proof;
            }
        }
        if let Some(cancel) = &s.cancel {
            cancel.cancel();
        }
        s.credential = None;
        s.report.credential_request = None;
        s.credential_scope = None;
        if s.busy {
            s.report.state = State::Disconnecting;
        }
        self.signals.notify.notify_one();
        Status::Accepted
    }
    /// SessionIdの割当より前でも、自分が開始した接続だけを取り消す。
    pub fn cancel_connect(&self, request: CommandSeq) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if !s.busy || s.active_connect_request != Some(request) {
            return Status::Stale;
        }
        if let Some(cancel) = &s.cancel {
            cancel.cancel();
        }
        s.credential = None;
        s.report.credential_request = None;
        s.credential_scope = None;
        s.report.state = State::Disconnecting;
        self.signals.notify.notify_one();
        Status::Accepted
    }
    pub fn termux_ime_endpoint(&self) -> Option<crate::input_method::Endpoint> {
        let s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        s.ime_endpoint
            .as_ref()
            .filter(|ep| s.report.authenticated_termux && ep.ready())
            .cloned()
    }
    pub fn snapshot(&self) -> Snapshot {
        self.reclamation_snapshot().0
    }
    #[cfg(feature = "host-acceptance")]
    pub fn host_acceptance_trace(&self) -> crate::host_acceptance::Snapshot {
        self.signals.host_trace.snapshot()
    }
    /// Snapshot and recovery admission are observed under the same owner lock.
    /// NoLocalAllocation comes from the original live gate, never from an
    /// empty Session or a complete record found after process restart.
    pub fn reclamation_snapshot(&self) -> (Snapshot, ReclamationState) {
        let shared = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let mut report = shared.report.clone();
        report.catalog_progress = (
            shared.catalog.request_id,
            shared.catalog.pending,
            shared
                .catalog
                .desktop_preview
                .as_ref()
                .map(|preview| (preview.request_id, preview.pending)),
        );
        if let Some(router) = &shared.router {
            report.forward = router.report();
        }
        report.tokio_threads = self.signals.tokio_workers.load(Ordering::Acquire);
        let pending_operations = [
            shared.connect.is_some(),
            shared.busy,
            shared.forward.is_some(),
            shared.app.is_some(),
            shared.desktop_work.is_some(),
            shared.desktop_launch_queued,
            shared.desktop_v3_work.is_some(),
            shared.desktop_v3_launch.is_some(),
            shared.desktop_v3_handoff.is_some(),
            shared.desktop_v3_finalization_pending,
            shared.forward_busy,
            shared.credential.is_some(),
            report.credential_request.is_some(),
            shared.catalog.pending,
        ]
        .into_iter()
        .filter(|pending| *pending)
        .count()
            + shared
                .terminal_requests
                .as_ref()
                .map_or(0, |queue| queue.max_capacity() - queue.capacity())
            + shared
                .catalog_requests
                .as_ref()
                .map_or(0, |queue| queue.max_capacity() - queue.capacity());
        let remote_cleanup_pending = !report.journal_ready
            || report.desktop_pending
            || !report.pending_cleanup.is_empty()
            || !matches!(shared.desktop_pending, Ok(None))
            || !matches!(shared.desktop_v3_pending, Ok(None))
            || shared.desktop_v3_handoff.is_some()
            || shared.desktop_v3_finalization_pending
            || report
                .desktop_v3_transport
                .as_ref()
                .is_some_and(|run| run.transport.journal_pending || run.cleanup_pending);
        let durable_v3_local_proven = matches!(&shared.desktop_v3_pending, Ok(Some(old))
            if old.has_durable_local_proof(self.signals.android_boot_count))
            && shared.desktop_v3_capture.is_none()
            && shared.desktop_v3_handoff.is_none()
            && !shared.desktop_v3_finalization_pending;
        let v3_remote_only_recovery = matches!(&shared.desktop_v3_pending, Ok(Some(old))
            if old.needs_remote_confirmation_on_same_boot(self.signals.android_boot_count))
            && matches!(&shared.desktop_pending, Ok(None))
            && shared.desktop_v3_capture.is_none()
            && shared.desktop_v3_handoff.is_none()
            && !shared.desktop_v3_finalization_pending;
        report.desktop_reboot_needed = shared.desktop_v3_capture.is_none()
            && matches!(&shared.desktop_v3_pending, Ok(Some(pending))
                if pending.needs_android_reboot(self.signals.android_boot_count));
        let captured_pending = matches!(
            (&shared.desktop_v3_pending, &shared.desktop_v3_capture),
            (Ok(Some(pending)), Some(capture))
                if pending.context() == capture.binding().context()
                    && pending.pins() == capture.binding().pins()
                    && pending.sources() == &capture.evidence().source_hashes()
        ) && shared.desktop_v3_handoff.is_none()
            && !shared.desktop_v3_finalization_pending;
        let live = !self.signals.stop.load(Ordering::Acquire) && !self.worker.is_finished();
        let v2_capture_proven = matches!(
            (&shared.desktop_pending, &shared.desktop_v2_capture, &shared.desktop_profile),
            (Ok(Some(pending)), Some(capture), Some(DesktopConfig::V2 { profile, pins, .. }))
                if pending.matches_binding(capture)
                    && pending.matches(profile, pins)
                    && capture.matches_profile(profile, capture.session())
        );
        let recovery_connect_allowed = live
            && ((captured_pending && shared.desktop_v3_io_retired)
                || durable_v3_local_proven
                || v3_remote_only_recovery
                || (v2_capture_proven && shared.desktop_v2_io_retired))
            && !shared.busy
            && shared.connect.is_none()
            && shared.desktop_work.is_none()
            && shared.desktop_launches.is_none()
            && shared.desktop_v3_launch.is_none()
            && report.owned_connections == 0
            && report.tokio_threads == 0
            && report.forward.active == 0;
        let recovery_command_allowed = live
            && (((captured_pending || durable_v3_local_proven || v3_remote_only_recovery)
                && matches!(shared.desktop_profile, Some(DesktopConfig::V3 { .. })))
                || (v2_capture_proven
                    && report.desktop.is_none()
                    && report.pending_cleanup.is_empty()))
            && report.state == State::CleanupPending
            && report.owned_connections == 1
            && shared
                .cancel
                .as_ref()
                .is_some_and(|cancel| !cancel.is_cancelled())
            && !shared.forward_busy
            && shared.desktop_work.is_none()
            && shared.desktop_launches.is_none()
            && shared.desktop_v3_work.is_none()
            && shared.desktop_v3_launch.is_none();
        let no_local_allocation_proven = shared
            .desktop_v3_no_local_allocation
            .as_ref()
            .zip(shared.desktop_v3_capture.as_ref())
            .is_some_and(|(proof, capture)| {
                desktop_v3::reclamation::LocalRetirementGate::verify(
                    proof,
                    capture.binding().context(),
                    capture.generation,
                )
                .is_ok_and(|local| local.complete())
            });
        (
            report,
            ReclamationState {
                pending_operations,
                remote_cleanup_pending,
                recovery_connect_allowed,
                recovery_command_allowed,
                no_local_allocation_proven,
                durable_v3_local_proven,
                v3_remote_only_recovery,
                v2_capture_proven,
            },
        )
    }
    pub fn forward_probe(&self, session: SessionId, files: &Path, nonce: &str) -> Status {
        self.queue_probe(session, files, nonce, None)
    }
    pub fn start_app(
        &self,
        session: SessionId,
        local_socket: &Path,
        nonce: &str,
        mode: AppMode,
    ) -> Status {
        self.start_selected_app(session, local_socket, nonce, mode, None, None)
    }
    pub fn start_selected_app(
        &self,
        session: SessionId,
        local_socket: &Path,
        nonce: &str,
        mode: AppMode,
        selection: Option<crate::catalog::ShelfStart>,
        gui_grace: Option<eidolon_model::gui_session_policy::GuiUnresponsiveGrace>,
    ) -> Status {
        if selection
            .as_ref()
            .is_some_and(|item| !item.valid() || mode != AppMode::Foot)
            || gui_grace.is_some() && mode != AppMode::Foot
        {
            return Status::InvalidArgument;
        }
        if mode == AppMode::LinuxDesktop
            || mode.linux_gtk_case().is_some()
            || mode != AppMode::Foot && !cfg!(debug_assertions)
        {
            return Status::NotReady;
        }
        if !local_socket.is_absolute()
            || local_socket.as_os_str().len() > 103
            || !forward_probe::valid_nonce(nonce)
        {
            return Status::InvalidArgument;
        }
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if self.signals.stop.load(Ordering::Acquire)
            || s.report.state != State::Ready
            || s.cancel.as_ref().is_some_and(|c| c.is_cancelled())
        {
            return Status::NotReady;
        }
        if s.forward_busy {
            return Status::Busy;
        }
        s.forward_busy = true;
        s.report.app_forward = Some(forward_app::Report {
            state: "queued",
            nonce: nonce.into(),
            mode: mode.as_str().into(),
            ..Default::default()
        });
        s.app = Some((local_socket.into(), nonce.into(), mode, selection, gui_grace));
        self.signals.notify.notify_one();
        Status::Accepted
    }
    pub fn set_workload_observation(&self, enabled: bool) {
        self.signals.observation.send_if_modified(|current| {
            if *current == enabled {
                false
            } else {
                *current = enabled;
                true
            }
        });
    }

    /// Begin Inspect on the authenticated v3 connection. Transport Catalog is
    /// not product Ready; core must still prepare and own the local display.
    pub fn inspect_linux_desktop_v3(&self, session: SessionId, request: CommandSeq) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if s.report.state == State::DesktopReady {
            if s.catalog.pending {
                return Status::Busy;
            }
            if s.desktop_v3_launch
                .as_ref()
                .is_none_or(|owner| !owner.refresh(request))
            {
                return Status::NotReady;
            }
            s.catalog.request_id = Some(request);
            s.catalog.pending = true;
            return Status::Accepted;
        }
        if !matches!(s.desktop_profile, Some(DesktopConfig::V3 { .. }))
            || s.report.state != State::ProbeReady
            || s.report.desktop_pending
            || !s.report.pending_cleanup.is_empty()
            || s.cancel.as_ref().is_none_or(|c| c.is_cancelled())
        {
            return Status::NotReady;
        }
        if s.forward_busy || s.desktop_v3_work.is_some() {
            return Status::Busy;
        }
        s.forward_busy = true;
        s.desktop_v3_work = Some(DesktopV3Work::Inspect(request));
        s.catalog = CatalogUi {
            session: Some(session),
            request_id: Some(request),
            pending: true,
            ..Default::default()
        };
        self.signals.notify.notify_one();
        Status::Accepted
    }

    /// Move-only native LaunchPermit; it is minted before local Waypipe or a
    /// listener is created. Dropping an unconsumed permit cancels the helper.
    pub fn reserve_linux_desktop_v3_launch(
        &self,
        runtime: RuntimeHandle,
        session: SessionId,
        request: CommandSeq,
        selection: &desktop_v3::Selection,
    ) -> Result<desktop_v3::runtime::LaunchPermit, Status> {
        let s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Err(Status::Stale);
        }
        if s.report.state != State::DesktopReady
            || s.catalog.pending
            || s.cancel.as_ref().is_none_or(|c| c.is_cancelled())
            || s.desktop_v3_selections
                .as_ref()
                .is_none_or(|selections| !selections.contains(selection))
            || s.report.desktop_v3_transport.as_ref().is_none_or(|r| {
                r.transport.phase
                    != eidolon_model::linux_desktop_v3::report::TransportPhase::Catalog
                    || r.terminal_observed
                    || !r.transport.journal_pending
            })
        {
            return Err(Status::NotReady);
        }
        let admission = s.desktop_v3_launch.as_ref().ok_or(Status::NotReady)?;
        if !admission.gate_open() {
            return Err(Status::Busy);
        }
        admission
            .reserve(runtime, session, request, selection)
            .map_err(|_| Status::Stale)
    }

    pub fn start_linux_desktop_v3(
        &self,
        permit: desktop_v3::runtime::LaunchPermit,
        local_socket: &Path,
    ) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.state != State::DesktopReady
            || s.cancel.as_ref().is_none_or(|c| c.is_cancelled())
        {
            return Status::NotReady;
        }
        let Some(admission) = &s.desktop_v3_launch else {
            return Status::NotReady;
        };
        match admission.enqueue(permit, local_socket) {
            Ok(()) => {
                s.catalog.desktop_v3 = None;
                s.catalog.desktop_v3_installed = None;
                s.desktop_v3_selections = None;
                Status::Accepted
            }
            Err("DESKTOP_COMMANDS_FULL") => Status::Busy,
            Err("DESKTOP_LOCAL_SOCKET_INVALID") => Status::InvalidArgument,
            Err(_) => Status::NotReady,
        }
    }

    pub fn abort_linux_desktop_v3_launch(&self, permit: desktop_v3::runtime::LaunchPermit) {
        drop(permit); // closes the launch gate and cancels the helper
    }

    pub fn recover_linux_desktop_v3(&self, session: SessionId, request: CommandSeq) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if !matches!(s.desktop_profile, Some(DesktopConfig::V3 { .. }))
            || s.report.state != State::CleanupPending
            || !matches!(s.desktop_v3_pending, Ok(Some(_)))
            || s.report.owned_connections != 1
            || s.cancel.as_ref().is_none_or(|c| c.is_cancelled())
        {
            return Status::NotReady;
        }
        if s.forward_busy || s.desktop_v3_work.is_some() || s.desktop_v3_launch.is_some() {
            return Status::Busy;
        }
        s.forward_busy = true;
        s.report.state = State::Recovering;
        s.desktop_v3_work = Some(DesktopV3Work::Recover(request));
        self.signals.notify.notify_one();
        Status::Accepted
    }

    fn v3_local_persist_capture(&self) -> Option<Arc<desktop_v3::runtime::RunCapture>> {
        let s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.busy
            || !s.desktop_v3_io_retired
            || s.desktop_v3_handoff.is_some()
            || s.desktop_v3_finalization_pending
            || !matches!(s.desktop_v3_pending, Ok(Some(_)))
        {
            return None;
        }
        s.desktop_v3_capture.clone()
    }

    pub fn v3_local_persist_ready(&self) -> bool {
        self.v3_local_persist_capture().is_some()
    }

    pub fn persist_v3_local_retirement(
        &self,
        proof: &impl desktop_v3::reclamation::LocalRetirementGate,
    ) -> Result<(), &'static str> {
        let capture = self
            .v3_local_persist_capture()
            .ok_or("DESKTOP_V3_LOCAL_PROOF_DEFERRED")?;
        let files = self
            .signals
            .files
            .as_deref()
            .ok_or("DESKTOP_V3_LOCAL_PROOF_DEFERRED")?;
        desktop_v3::journal::Journal::record_local_retirement(files, &capture, proof)
    }

    /// The journal handoff can be issued only AFTER the SSH Tokio generation
    /// has actually been destroyed; callbacks on that runtime must not wait
    /// for local/SDK retirement or they can deadlock shutdown.
    pub fn take_v3_reclamation(&self) -> Option<desktop_v3::reclamation::ReclamationHandoff> {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.busy || !s.desktop_v3_io_retired {
            return None;
        }
        let handoff = s.desktop_v3_handoff.take();
        if handoff.is_some() {
            s.desktop_v3_finalization_pending = true;
        }
        handoff
    }

    pub fn publish_v3_finalized(
        &self,
        result: &desktop_v3::reclamation::FinalizedReclamation,
    ) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if !s.desktop_v3_finalization_pending
            || s.busy
            || !s.desktop_v3_io_retired
            || s.report
                .session
                .is_none_or(|id| id.get() as u64 != result.generation())
        {
            return Status::Stale;
        }
        let Some(files) = self.signals.files.as_deref() else {
            return Status::NotReady;
        };
        let pending = desktop_v3::journal::pending(files);
        if !matches!(pending, Ok(None)) {
            s.desktop_v3_pending = pending;
            return Status::NotReady;
        }
        s.desktop_v3_pending = Ok(None);
        s.desktop_v3_finalization_pending = false;
        #[cfg(feature = "host-acceptance")]
        if let Some(capture) = &s.desktop_v3_capture {
            capture.observe(
                "finalizer_published",
                serde_json::json!({
                    "generation":result.generation().to_string(),"status":Status::Accepted as i32
                }),
            );
        }
        s.desktop_v3_capture = None;
        s.desktop_v3_no_local_allocation = None;
        s.report.desktop_pending = !matches!(s.desktop_pending, Ok(None));
        if let Some(snapshot) = &mut s.report.desktop_v3_transport {
            snapshot.transport.journal_pending = false;
            snapshot.cleanup_pending = false;
        }
        s.report.state = State::Disconnected;
        // Finalization has discharged the same capture's transient pending
        // cleanup. Preserve an unrelated legacy barrier if one still exists.
        if !s.report.desktop_pending {
            s.report.error = None;
        }
        Status::Accepted
    }

    pub fn inspect_linux_desktop(&self, session: SessionId, request: CommandSeq) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if !matches!(s.desktop_profile, Some(DesktopConfig::V2 { .. }))
            || s.report.state != State::ProbeReady
            || s.report.desktop_pending
            || !s.report.pending_cleanup.is_empty()
            || s.cancel.as_ref().is_none_or(|c| c.is_cancelled())
        {
            return Status::NotReady;
        }
        if s.forward_busy || s.desktop_work.is_some() {
            return Status::Busy;
        }
        s.forward_busy = true;
        s.desktop_work = Some(DesktopWork::Inspect(request));
        s.catalog = CatalogUi {
            session: Some(session),
            request_id: Some(request),
            pending: true,
            ..Default::default()
        };
        self.signals.notify.notify_one();
        Status::Accepted
    }

    /// Native prepares the local display, Waypipe and target reservation before
    /// Start enqueue. This is a read of a live inspected catalog, not permission
    /// to replay a selection against another helper/session.
    pub fn desktop_launch_nonce(
        &self,
        session: SessionId,
        selection: &linux_desktop::Selection,
    ) -> Result<String, Status> {
        let s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        Self::desktop_selection(&s, session, selection)?;
        Ok(s.report
            .desktop
            .as_ref()
            .ok_or(Status::NotReady)?
            .nonce
            .clone())
    }

    fn desktop_selection(
        s: &Shared,
        session: SessionId,
        selection: &linux_desktop::Selection,
    ) -> Result<(), Status> {
        if !selection.valid() {
            return Err(Status::InvalidArgument);
        }
        if s.report.session != Some(session) {
            return Err(Status::Stale);
        }
        if s.desktop_launch_queued {
            return Err(Status::Busy);
        }
        if !matches!(s.desktop_profile, Some(DesktopConfig::V2 { .. }))
            || s.report.state != State::DesktopReady
            || s.cancel.as_ref().is_none_or(|c| c.is_cancelled())
            || s.desktop_launches.is_none()
            || s.report.desktop.as_ref().is_none_or(|r| {
                !r.tools_verified
                    || r.start_requested
                    || r.finished
                    || r.state != "catalog"
                    || r.remote.terminal_event
                    || r.remote.channel_closed
            })
        {
            return Err(Status::NotReady);
        }
        if s.catalog
            .desktop
            .as_ref()
            .is_none_or(|catalog| &catalog.selection != selection)
        {
            return Err(Status::Stale);
        }
        Ok(())
    }

    pub fn start_linux_desktop(
        &self,
        session: SessionId,
        request: CommandSeq,
        local_socket: &Path,
        selection: linux_desktop::Selection,
    ) -> Status {
        if !local_socket.is_absolute() || local_socket.as_os_str().len() > 103 {
            return Status::InvalidArgument;
        }
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if let Err(status) = Self::desktop_selection(&s, session, &selection) {
            return status;
        }
        let Some(sender) = s.desktop_launches.as_ref() else {
            return Status::NotReady;
        };
        match sender.try_send(linux_desktop::Launch {
            request_id: Some(request),
            local_socket: local_socket.into(),
            selection,
        }) {
            Ok(()) => {
                s.desktop_launch_queued = true;
                s.catalog.desktop = None;
                Status::Accepted
            }
            Err(tokio::sync::mpsc::error::TrySendError::Full(_)) => Status::Busy,
            Err(_) => Status::NotReady,
        }
    }

    pub fn recover_linux_desktop(&self, session: SessionId, request: CommandSeq) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if !matches!(s.desktop_profile, Some(DesktopConfig::V2 { .. }))
            || s.report.state != State::CleanupPending
            || !s.report.desktop_pending
            || s.report.owned_connections != 1
            || s.report.desktop.is_some()
            || !s.report.pending_cleanup.is_empty()
            || s.cancel.as_ref().is_none_or(|c| c.is_cancelled())
        {
            return Status::NotReady;
        }
        if s.forward_busy || s.desktop_work.is_some() || s.desktop_launches.is_some() {
            return Status::Busy;
        }
        s.forward_busy = true;
        s.report.state = State::Recovering;
        s.desktop_work = Some(DesktopWork::Recover(request));
        self.signals.notify.notify_one();
        Status::Accepted
    }
    /// debug固定SHMと明示receipt回収だけ。ProbeReadyの通常app/catalog能力は変えない。
    #[cfg(any(test, feature = "debug-fixtures"))]
    pub fn linux_probe(
        &self,
        session: SessionId,
        files: &Path,
        local_socket: Option<&Path>,
        nonce: &str,
        pins: crate::linux_probe::Pins,
    ) -> Status {
        self.linux_work(session, files, local_socket, nonce, pins, None)
    }
    #[cfg(any(test, feature = "debug-fixtures"))]
    pub fn linux_gtk_probe(
        &self,
        session: SessionId,
        files: &Path,
        local_socket: Option<&Path>,
        nonce: &str,
        binding: crate::linux_probe::GtkBinding,
    ) -> Status {
        self.linux_work(
            session,
            files,
            local_socket,
            nonce,
            binding.base_pins(),
            Some(binding),
        )
    }
    #[cfg(any(test, feature = "debug-fixtures"))]
    fn linux_work(
        &self,
        session: SessionId,
        files: &Path,
        local_socket: Option<&Path>,
        nonce: &str,
        pins: crate::linux_probe::Pins,
        gtk: Option<crate::linux_probe::GtkBinding>,
    ) -> Status {
        if !files.is_absolute()
            || !forward_probe::valid_nonce(nonce)
            || local_socket.is_some_and(|p| !p.is_absolute() || p.as_os_str().len() > 103)
        {
            return Status::InvalidArgument;
        }
        let mut shared = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if shared.report.session != Some(session) {
            return Status::Stale;
        }
        if self.signals.stop.load(Ordering::Acquire)
            || shared.report.state != State::ProbeReady
            || shared.desktop_profile.is_some()
            || shared.cancel.as_ref().is_none_or(|c| c.is_cancelled())
        {
            return Status::NotReady;
        }
        if shared.forward_busy {
            return Status::Busy;
        }
        shared.forward_busy = true;
        if local_socket.is_some() {
            shared.report.linux_forward = Some(crate::linux_probe::Report {
                state: "queued",
                session: Some(session),
                nonce: nonce.into(),
                gtk: gtk
                    .as_ref()
                    .map(|gtk| crate::linux_probe::GtkReport::initial(gtk.case)),
                ..Default::default()
            });
        } else {
            shared.report.linux_recovery = None;
        }
        shared.linux = Some(LinuxWork {
            files: files.into(),
            local_socket: local_socket.map(Path::to_path_buf),
            nonce: nonce.into(),
            pins,
            gtk,
        });
        self.signals.notify.notify_one();
        Status::Accepted
    }
    pub fn open_terminal(&self, session: SessionId, request: CommandSeq) -> Status {
        self.launch_terminal(session, request, None)
    }
    pub fn launch_terminal(
        &self,
        session: SessionId,
        request: CommandSeq,
        target: Option<crate::catalog::Target>,
    ) -> Status {
        if target.as_ref().is_some_and(|target| !target.valid()) {
            return Status::InvalidArgument;
        }
        let s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if s.report.state != State::Ready || s.cancel.as_ref().is_none_or(|c| c.is_cancelled()) {
            return Status::NotReady;
        }
        let Some(sender) = s.terminal_requests.as_ref() else {
            return Status::NotReady;
        };
        if s.report
            .app_forward
            .as_ref()
            .and_then(|r| r.remote.as_ref())
            .is_none_or(|r| r.terminal_count == 0 || r.terminal_pending.is_some())
        {
            return Status::Busy;
        }
        match sender.try_send((request, target)) {
            Ok(()) => Status::Accepted,
            Err(tokio::sync::mpsc::error::TrySendError::Full(_)) => Status::Busy,
            Err(_) => Status::NotReady,
        }
    }
    pub fn catalog_ui(&self) -> CatalogUi {
        let s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let connected = s.cancel.as_ref().is_some_and(|c| !c.is_cancelled())
            && if s.desktop_profile.is_some() {
                matches!(s.report.state, State::ProbeReady | State::DesktopReady)
            } else {
                s.report.state == State::Ready && s.catalog_requests.is_some()
            };
        let mut catalog = if connected {
            s.catalog.clone()
        } else {
            CatalogUi::default()
        };
        if catalog.desktop_v3_installed.is_none() {
            catalog.desktop_preview = s.desktop_preview.clone();
        }
        catalog.termux_shelf = s
            .shelf_result
            .as_ref()
            .filter(|(session, _)| {
                Some(*session) == s.report.session
                    && s.report.termux_shelf_readable()
                    && s.cancel.as_ref().is_some_and(|c| !c.is_cancelled())
            })
            .map(|(_, data)| data.clone());
        catalog
    }
    pub fn catalog(
        &self,
        session: SessionId,
        request: CommandSeq,
        operation: crate::catalog::Operation,
    ) -> Status {
        if !operation.valid() {
            return Status::InvalidArgument;
        }
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if s.cancel.as_ref().is_none_or(|c| c.is_cancelled()) {
            return Status::NotReady;
        }
        if let crate::catalog::Operation::Shelf { path, diagnostics } = operation {
            if s.desktop_profile.is_some() || !s.report.termux_shelf_readable() {
                return Status::NotReady;
            }
            if s.shelf_request
                .as_ref()
                .is_some_and(|(owner, ..)| *owner == session)
            {
                return Status::Busy;
            }
            s.shelf_request = Some((session, request, path, diagnostics));
            self.signals.notify.notify_one();
            self.signals.desktop_menu_notify.notify_one();
            return Status::Accepted;
        }
        if s.report.state != State::Ready {
            return Status::NotReady;
        }
        if s.catalog.pending {
            return Status::Busy;
        }
        let Some(sender) = s.catalog_requests.as_ref() else {
            return Status::NotReady;
        };
        if s.report
            .app_forward
            .as_ref()
            .is_none_or(|r| r.state != "running")
        {
            return Status::NotReady;
        }
        match sender.try_send((request, operation)) {
            Ok(()) => {
                s.catalog = CatalogUi {
                    session: Some(session),
                    request_id: Some(request),
                    pending: true,
                    ..Default::default()
                };
                Status::Accepted
            }
            Err(tokio::sync::mpsc::error::TrySendError::Full(_)) => Status::Busy,
            Err(_) => Status::NotReady,
        }
    }
    pub fn cleanup_probe(
        &self,
        session: SessionId,
        files: &Path,
        nonce: &str,
        scenario: CleanupScenario,
    ) -> Status {
        if !cfg!(debug_assertions) {
            return Status::NotReady;
        }
        self.queue_probe(session, files, nonce, Some(scenario))
    }
    fn queue_probe(
        &self,
        session: SessionId,
        files: &Path,
        nonce: &str,
        scenario: Option<CleanupScenario>,
    ) -> Status {
        if !files.is_absolute() || !forward_probe::valid_nonce(nonce) {
            return Status::InvalidArgument;
        }
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if self.signals.stop.load(Ordering::Acquire)
            || s.report.state != State::Ready
            || s.cancel.as_ref().is_some_and(|c| c.is_cancelled())
        {
            return Status::NotReady;
        }
        if s.forward_busy {
            return Status::Busy;
        }
        s.forward_busy = true;
        s.report.forward_probe = Some(forward_probe::Report {
            state: "queued",
            nonce: nonce.into(),
            ..Default::default()
        });
        s.forward = Some((files.into(), nonce.into(), scenario));
        self.signals.notify.notify_one();
        Status::Accepted
    }
    pub fn request_stop(&self) {
        #[cfg(feature = "host-acceptance")]
        if !self.signals.stop.load(Ordering::Acquire) {
            self.signals
                .host_trace
                .record_current("controller_stop_requested", serde_json::json!({}));
        }
        self.signals.stop.store(true, Ordering::Release);
        self.signals.update(|s| {
            s.credential = None;
            s.credential_scope = None;
            if let Some(cancel) = &s.cancel {
                cancel.cancel();
            }
        });
        self.signals.cv.notify_one();
        self.signals.notify.notify_one();
    }
    pub fn is_finished(&self) -> bool {
        self.worker.is_finished()
    }
}
impl Drop for Controller {
    fn drop(&mut self) {
        self.request_stop();
    }
}

fn owner(
    signals: &Arc<Signals>,
    deadlines: Deadlines,
    root: Option<&Path>,
) -> Result<(), &'static str> {
    let mut journal = journal::Journal::load(root)?;
    signals.update(|s| {
        s.report.journal_ready = true;
        s.report.pending_cleanup = journal.pending();
    });
    loop {
        let pending = {
            let mut s = signals.shared.lock().unwrap_or_else(|e| e.into_inner());
            while s.connect.is_none() && !signals.stop.load(Ordering::Acquire) {
                s = signals
                    .cv
                    .wait_timeout(s, Duration::from_millis(100))
                    .unwrap_or_else(|e| e.into_inner())
                    .0;
            }
            if signals.stop.load(Ordering::Acquire) {
                break;
            }
            s.connect.take().unwrap()
        };
        if pending.cancel.is_cancelled() {
            signals.update(|s| {
                if let Some(previous) = pending.discovery_previous {
                    finish_discovery(s, previous, Some("CANCELLED"));
                    return;
                }
                s.cancel = None;
                s.active_connect_request = None;
                s.busy = false;
                s.report.state = if s.report.desktop_pending {
                    State::CleanupPending
                } else {
                    State::Disconnected
                };
            });
            continue;
        }
        let session = match journal.allocate_session() {
            Ok(session) => session,
            Err(error) => {
                signals.update(|s| {
                    if let Some(previous) = pending.discovery_previous {
                        finish_discovery(s, previous, Some(error));
                        return;
                    }
                    s.cancel = None;
                    s.busy = false;
                    s.active_connect_request = None;
                    s.report.state = State::Failed;
                    s.report.error = Some(error);
                });
                continue;
            }
        };
        let discovery_previous = pending.discovery_previous;
        let connect = Connect {
            session,
            router: Arc::new(forward::Router::new(session)),
            profile: pending.profile,
            request: pending.request,
            cancel: pending.cancel,
            discovery_only: discovery_previous.is_some(),
        };
        signals.update(|s| {
            s.report.session = Some(session);
            s.credential_scope = Some(CredentialScope {
                session,
                request: connect.request,
                profile_id: connect.profile.profile_id.clone(),
                revision: connect.profile.revision.clone(),
                binding: connect.profile.binding.clone(),
            });
            s.router = Some(connect.router.clone());
        });
        // The v3 lock precedes every SSH handle and Tokio runtime in this
        // generation. Its Drop must follow the runtime's Drop, not block inside
        // the SSH actor waiting for core-owned SDK resources.
        let mut v3_io_fence = match &connect.profile.desktop {
            Some(DesktopConfig::V3 { files, .. }) => {
                match desktop_v3::io_fence::IoGenerationFence::acquire(
                    files,
                    connect.session,
                    connect.session.get() as u64,
                ) {
                    Ok(fence) => Some(fence),
                    Err(error) => {
                        signals.update(|s| {
                            s.busy = false;
                            s.active_connect_request = None;
                            s.cancel = None;
                            s.router = None;
                            s.credential_scope = None;
                            s.report.state = State::CleanupPending;
                            s.report.error = Some(error);
                            s.report.owned_connections = 0;
                        });
                        continue;
                    }
                }
            }
            _ => None,
        };
        let previous_io = v3_io_fence.as_mut().and_then(|fence| fence.take_previous());
        let (start, end) = (signals.clone(), signals.clone());
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .max_blocking_threads(1)
            .enable_all()
            .thread_name("eidolon-ssh")
            .on_thread_start(move || {
                start.tokio_workers.fetch_add(1, Ordering::AcqRel);
            })
            .on_thread_stop(move || {
                end.tokio_workers.fetch_sub(1, Ordering::AcqRel);
            })
            .build();
        let result = match runtime {
            Ok(runtime) => {
                let result = runtime.block_on(connection(
                    signals,
                    &connect,
                    deadlines,
                    &mut journal,
                    previous_io.as_ref(),
                ));
                // UI/coreではなくownerでdrop。未完SSH task/FD/秘密keyとTokio workerを回収。
                drop(runtime);
                result
            }
            Err(_) => Err("SSH_IO_FAILED"),
        };
        // The fence outlives the entire SSH runtime, including worker tasks.
        drop(v3_io_fence);
        if let Some(previous) = discovery_previous {
            signals.update(|s| finish_discovery(s, previous, result.err()));
            continue;
        }
        let was_v3 = matches!(connect.profile.desktop, Some(DesktopConfig::V3 { .. }));
        let was_v2 = matches!(connect.profile.desktop, Some(DesktopConfig::V2 { .. }));
        let desktop_pending = root.map(linux_desktop::pending).unwrap_or(Ok(None));
        let desktop_v3_pending = root.map(desktop_v3::journal::pending).unwrap_or(Ok(None));
        signals.update(|s| {
            if was_v2 {
                s.desktop_v2_io_retired = true;
            }
            if was_v3 {
                s.desktop_v3_io_retired = true;
                #[cfg(feature = "host-acceptance")]
                if let Some(capture) = &s.desktop_v3_capture {
                    capture.observe(
                        "io_retired",
                        serde_json::json!({
                            "generation":connect.session.get().to_string(),
                            "tokio_threads":signals.tokio_workers.load(Ordering::Acquire)
                        }),
                    );
                }
                if let Some(report) = &mut s.report.desktop_v3_transport {
                    report.io_retired = true;
                }
            }
            s.credential = None;
            s.credential_scope = None;
            s.cancel = None;
            s.busy = false;
            s.active_connect_request = None;
            s.forward = None;
            s.app = None;
            s.desktop_work = None;
            s.desktop_launches = None;
            s.desktop_launch_queued = false;
            s.desktop_v3_work = None;
            s.systemd_requests = None;
            s.systemd_launch_queued = false;
            s.systemd_ticks = None;
            s.termux_ticks = None;
            s.desktop_v3_launch = None;
            s.desktop_v3_selections = None;
            s.catalog = CatalogUi::default();
            if let Some(preview) = s.desktop_preview.as_mut().filter(|p| p.pending) {
                preview.complete(Err("CANCELLED"));
            }
            s.desktop_pending =
                if !was_v3 && s.report.desktop_pending && matches!(desktop_pending, Ok(None)) {
                    // A file disappearing without the actor/recovery's proof must
                    // not turn Unknown into Clean at connection retirement.
                    Err("DESKTOP_JOURNAL_OWNER_CHANGED")
                } else {
                    desktop_pending
                };
            s.desktop_v3_pending = if was_v3
                && s.report
                    .desktop_v3_transport
                    .as_ref()
                    .is_some_and(|r| r.transport.journal_pending)
                && matches!(desktop_v3_pending, Ok(None))
            {
                Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED")
            } else {
                desktop_v3_pending
            };
            s.report.desktop_pending = !matches!(s.desktop_pending, Ok(None))
                || !matches!(s.desktop_v3_pending, Ok(None))
                || s.desktop_v3_finalization_pending
                || s.report.systemd_chrome.as_ref().is_some_and(|r| r.phase == linux_desktop::systemd::Phase::CleanupPending);
            #[cfg(any(test, feature = "debug-fixtures"))]
            {
                s.linux = None;
            }
            s.forward_busy = false;
            s.report.forward = connect.router.report();
            s.router = None;
            s.report.owned_connections = 0;
            s.report.credential_request = None;
            if s.report.desktop_pending {
                s.report.state = State::CleanupPending;
                s.report.error = s
                    .desktop_pending
                    .as_ref()
                    .err()
                    .copied()
                    .or_else(|| result.err().filter(|error| *error != "CANCELLED"))
                    .or(Some("DESKTOP_CLEANUP_PENDING"));
            } else if result == Err("CANCELLED") || result.is_ok() {
                s.report.state = State::Disconnected;
                s.report.error = None;
            } else {
                s.report.state = State::Failed;
                s.report.error = Some(result.err().unwrap_or("REMOTE_EXIT"));
            }
        });
    }
    Ok(())
}
fn finish_discovery(s: &mut Shared, mut previous: Snapshot, error: Option<&'static str>) {
    previous.credential_requests = s.report.credential_requests;
    previous.journal_ready = s.report.journal_ready;
    previous.pending_cleanup = std::mem::take(&mut s.report.pending_cleanup);
    s.report = previous;
    s.credential = None;
    s.credential_scope = None;
    s.cancel = None;
    s.busy = false;
    s.active_connect_request = None;
    s.router = None;
    if let Some(preview) = &mut s.desktop_preview {
        let result = error.map_or_else(
            || preview.entries.take().ok_or("DESKTOP_DISCOVERY_INVALID"),
            Err,
        );
        preview.complete(result);
    }
}
struct Peer {
    pin: PublicKey,
    mismatch: Arc<AtomicBool>,
    session: SessionId,
    router: Arc<forward::Router>,
}
impl client::Handler for Peer {
    type Error = russh::Error;
    // この製品で受け付けるserver起点channelは登録済みStreamLocalだけ。
    async fn server_channel_open_session(
        &mut self,
        _: russh::Channel<client::Msg>,
        _: client::ChannelOpenHandle,
        _: &mut client::Session,
    ) -> Result<(), Self::Error> {
        Ok(())
    }
    async fn server_channel_open_agent_forward(
        &mut self,
        _: russh::Channel<client::Msg>,
        _: client::ChannelOpenHandle,
        _: &mut client::Session,
    ) -> Result<(), Self::Error> {
        Ok(())
    }
    async fn server_channel_open_direct_streamlocal(
        &mut self,
        _: russh::Channel<client::Msg>,
        _: &str,
        _: client::ChannelOpenHandle,
        _: &mut client::Session,
    ) -> Result<(), Self::Error> {
        Ok(())
    }
    async fn server_channel_open_x11(
        &mut self,
        _: russh::Channel<client::Msg>,
        _: &str,
        _: u32,
        _: client::ChannelOpenHandle,
        _: &mut client::Session,
    ) -> Result<(), Self::Error> {
        Ok(())
    }
    async fn server_channel_open_forwarded_tcpip(
        &mut self,
        _: russh::Channel<client::Msg>,
        _: &str,
        _: u32,
        _: &str,
        _: u32,
        _: client::ChannelOpenHandle,
        _: &mut client::Session,
    ) -> Result<(), Self::Error> {
        Ok(())
    }
    async fn server_channel_open_direct_tcpip(
        &mut self,
        _: russh::Channel<client::Msg>,
        _: &str,
        _: u32,
        _: &str,
        _: u32,
        _: client::ChannelOpenHandle,
        _: &mut client::Session,
    ) -> Result<(), Self::Error> {
        Ok(())
    }
    async fn check_server_key(
        &mut self,
        key: &PublicKeyOrCertificate,
    ) -> Result<bool, Self::Error> {
        let accepted =
            key.certificate().is_none() && self.pin.key_data() == key.public_key().key_data();
        if !accepted {
            self.mismatch.store(true, Ordering::Release);
        }
        Ok(accepted)
    }
    async fn server_channel_open_forwarded_streamlocal(
        &mut self,
        channel: russh::Channel<client::Msg>,
        path: &str,
        reply: client::ChannelOpenHandle,
        _: &mut client::Session,
    ) -> Result<(), Self::Error> {
        self.router.open(self.session, path, channel, reply);
        Ok(())
    }
}
async fn connection(
    signals: &Signals,
    connect: &Connect,
    deadlines: Deadlines,
    journal: &mut journal::Journal,
    previous_io: Option<&desktop_v3::io_fence::PreviousIoClosed>,
) -> Result<(), &'static str> {
    signals.update(|s| s.report.owned_connections = 1);
    let mismatch = Arc::new(AtomicBool::new(false));
    let peer = Peer {
        pin: connect.profile.pin.clone(),
        mismatch: mismatch.clone(),
        session: connect.session,
        router: connect.router.clone(),
    };
    let setup = async {
        let mut client = timeout(
            deadlines.connect,
            client::connect(Arc::new(ssh_config()), connect.profile.endpoint, peer),
        )
        .await
        .map_err(|_| "CONNECT_TIMEOUT")?
        .map_err(|error| {
            if mismatch.load(Ordering::Acquire) {
                "HOST_KEY_MISMATCH"
            } else if matches!(error, russh::Error::IO(ref io) if io.kind() == std::io::ErrorKind::ConnectionRefused) {
                "SSH_CONNECTION_REFUSED"
            } else {
                "SSH_CONNECT_FAILED"
            }
        })?;
        signals.update(|s| {
            s.report.owned_connections = 1;
            s.report.state = State::AwaitingCredential;
            s.report.credential_request = Some(connect.request);
            s.report.credential_requests += 1;
        });
        let auth_deadline = Instant::now() + deadlines.auth;
        let key = timeout_at(auth_deadline, async {
            loop {
                let notified = signals.notify.notified();
                if let Some(key) = signals
                    .shared
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .credential
                    .take()
                {
                    break key;
                }
                notified.await;
            }
        })
        .await
        .map_err(|_| "AUTH_TIMEOUT")?;
        signals.update(|s| {
            s.report.state = State::Authenticating;
            s.report.credential_request = None;
        });
        let authenticated = timeout_at(
            auth_deadline,
            client.authenticate_publickey(
                &connect.profile.username,
                PrivateKeyWithHashAlg::new(Arc::new(key), None),
            ),
        )
        .await
        .map_err(|_| "AUTH_TIMEOUT")?
        .map_err(|_| "AUTH_FAILED")?;
        if !authenticated.success() {
            return Err("AUTH_FAILED");
        }
        if connect.discovery_only {
            return Ok(client);
        }
        let report = timeout(deadlines.exec, async {
            let channel = client
                .channel_open_session()
                .await
                .map_err(|_| "SSH_IO_FAILED")?;
            exec::probe(channel, |report| {
                signals.update(|s| s.report.probe = report.clone())
            })
            .await
        })
        .await
        .map_err(|_| "EXEC_TIMEOUT")??;
        signals.update(|s| {
            s.report.probe = report;
        });
        Ok::<_, &'static str>(client)
    };
    let mut client = tokio::select! { biased; _=connect.cancel.cancelled()=>return Err("CANCELLED"), result=setup=>result? };
    if connect.discovery_only {
        let entries = desktop_v3::installed::discover(&client, &connect.cancel).await?;
        signals.update(|s| {
            if let Some(preview) = &mut s.desktop_preview {
                preview.entries = Some(entries);
            }
        });
        return Ok(());
    }
    let binding = connect.profile.binding.clone();
    let uid = signals
        .shared
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .report
        .probe
        .uid
        .ok_or("PROTOCOL_ERROR")?;
    if connect.profile.desktop.is_some() && uid == 0 {
        // The normal GUI lease is a non-root capability. Reject the verified
        // UID before exposing Inspect; debug probe/legacy semantics stay intact.
        return Err("DESKTOP_BINDING_INVALID");
    }
    if connect.profile.helper_supported {
        signals.update(|s| s.report.authenticated_termux = true);
    }
    reconcile(signals, &client, connect, journal, &binding, uid).await?;
    if let Some(DesktopConfig::Systemd { profile, binding, files }) = &connect.profile.desktop {
        let target = linux_desktop::systemd::ChromeTarget {
            profile,
            storage: binding.storage(),
            files,
            waypipe_path: binding.waypipe_path(),
            waypipe_hash: binding.waypipe_sha256(),
            uid,
            session: connect.session,
        };
        let owner = linux_desktop::systemd::ChromeOwner::connect(
            &client, &connect.router, target, &connect.cancel,
        ).await?;
        let (sender, requests) = tokio::sync::mpsc::channel(4);
        let (tick_sender, ticks) = tokio::sync::watch::channel(None);
        signals.update(|s| {
            s.systemd_requests = Some(sender);
            s.systemd_ticks = Some(tick_sender);
        });
        return linux_desktop::systemd::serve(owner, requests, ticks, &connect.cancel, |report| {
            use linux_desktop::systemd::Phase;
            signals.update(|s| {
                s.report.systemd_chrome = Some(report.clone());
                s.report.state = match report.phase {
                    Phase::Available | Phase::Starting | Phase::Forwarded | Phase::Running => State::DesktopReady,
                    Phase::Stopping | Phase::Complete => State::Disconnecting,
                    Phase::CleanupPending => State::CleanupPending,
                };
            });
        }).await;
    }

    let (ime_endpoint, mut ime_requests) = crate::input_method::Endpoint::channel(&connect.cancel);
    let ime_enabled = connect.profile.helper_supported;
    if ime_enabled {
        signals.update(|s| s.ime_endpoint = Some(ime_endpoint.clone()));
    }
    loop {
        if connect.cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        let notified = signals.notify.notified();
        if matches!(connect.profile.desktop, Some(DesktopConfig::V3 { .. })) {
            refresh_desktop_preview(signals, &client, &connect.cancel).await;
        }
        if connect.profile.helper_supported {
            refresh_termux_shelf(signals, &client, connect).await;
        }
        let forward = signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .forward
            .take();
        let app = signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .app
            .take();
        let desktop_work = signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .desktop_work
            .take();
        let desktop_v3_work = signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .desktop_v3_work
            .take();
        if let Some(work) = desktop_v3_work {
            let (profile, configuration, storage, files) = match connect.profile.desktop.as_ref() {
                Some(DesktopConfig::V3 {
                    profile,
                    configuration,
                    storage,
                    files,
                }) => (profile, configuration, storage, files),
                _ => return Err("DESKTOP_VERSION_SELECTION"),
            };
            match work {
                DesktopV3Work::Inspect(inspect_request) => {
                    let nonce = format!("{:016x}", rand::random::<u64>());
                    let binding = desktop_v3::Binding::capture(
                        profile,
                        connect.session,
                        uid,
                        &nonce,
                        configuration.pins().clone(),
                        storage,
                    )?;
                    let evidence = desktop_v3::ExpectedEvidence::capture(
                        configuration.pins(),
                        configuration.manifest(),
                        &desktop_v3::sources::current_hashes(),
                    )?;
                    let capture = desktop_v3::runtime::RunCapture::new(
                        binding,
                        evidence,
                        inspect_request,
                        connect.session.get() as u64,
                    )?;
                    #[cfg(feature = "host-acceptance")]
                    let capture = capture.with_host_trace(signals.host_trace.clone());
                    let (sender, launches) = tokio::sync::mpsc::channel(1);
                    let admission = Arc::new(desktop_v3::runtime::LaunchAdmission::new(
                        capture,
                        sender,
                        connect.cancel.clone(),
                    ));
                    let capture = admission.capture();
                    let catalog_descriptor = eidolon_model::linux_desktop_v3::catalog_descriptor(
                        capture.binding().context(),
                        capture.binding().pins(),
                    );
                    signals.update(|s| {
                        s.desktop_v3_launch = Some(admission.clone());
                        s.desktop_v3_capture = Some(capture.clone());
                        s.desktop_v3_no_local_allocation = None;
                    });
                    let serve = desktop_v3::runtime::serve(
                        &client,
                        &connect.router,
                        desktop_v3::runtime::Inspection {
                            capture,
                            files,
                            android_boot_count: signals.android_boot_count,
                        },
                        launches,
                        &connect.cancel,
                        |report| {
                            signals.update(|s| {
                                s.report.desktop_v3_transport = Some(report.clone());
                                s.report.desktop_pending = report.transport.journal_pending;
                                if matches!(report.transport.phase,
                                    eidolon_model::linux_desktop_v3::report::TransportPhase::Stopping
                                    | eidolon_model::linux_desktop_v3::report::TransportPhase::CleanupPending
                                    | eidolon_model::linux_desktop_v3::report::TransportPhase::Complete)
                                {
                                    s.catalog.desktop_v3 = None;
                                    s.catalog.desktop_v3_installed = None;
                                    s.desktop_v3_selections = None;
                                    s.catalog.pending = false;
                                    s.report.state = State::CleanupPending;
                                }
                            });
                        },
                        |request_id, selections, installed| {
                            signals.update(|s| {
                                if s.report.session == Some(connect.session)
                                    && s.catalog.request_id == Some(request_id)
                                    && !connect.cancel.is_cancelled()
                                {
                                    s.catalog.pending = false;
                                    s.catalog.desktop_v3 = Some(catalog_descriptor.clone());
                                    s.catalog.desktop_v3_editor_available =
                                        selections.iter().any(|selection| {
                                            selection.record()["app_id"] == "mousepad-editor-v1"
                                        });
                                    s.desktop_v3_selections = Some(selections.to_vec());
                                    s.catalog.desktop_v3_installed = installed.map(<[_]>::to_vec);
                                    s.report.state = State::DesktopReady;
                                }
                            });
                        },
                    );
                    // The read-only menu uses an additional SSH channel while
                    // the app helper continues to service its lease.
                    let refresh_menu = async {
                        loop {
                            let notified = signals.desktop_menu_notify.notified();
                            refresh_desktop_preview(signals, &client, &connect.cancel).await;
                            notified.await;
                        }
                    };
                    let mut outcome = tokio::select! {
                        result = serve => result,
                        _ = refresh_menu => unreachable!(),
                    };
                    let mut no_local_allocation = admission.seal_without_local_allocation();
                    if let Some(handoff) = outcome.handoff.as_mut() {
                        handoff.no_local_allocation = no_local_allocation.take();
                    }
                    let fault = outcome.report.error;
                    signals.update(|s| {
                        s.forward_busy = false;
                        s.desktop_v3_launch = None;
                        let pending = outcome.report.transport.journal_pending;
                        s.desktop_v3_no_local_allocation = no_local_allocation.filter(|_| pending);
                        if !pending {
                            s.desktop_v3_capture = None;
                        }
                        s.desktop_v3_selections = None;
                        s.catalog.desktop_v3 = None;
                        s.catalog.desktop_v3_installed = None;
                        s.catalog.pending = false;
                        s.report.desktop_v3_transport = Some(outcome.report);
                        s.desktop_v3_handoff = outcome.handoff;
                        s.report.state = if pending {
                            State::CleanupPending
                        } else {
                            State::Failed
                        };
                        s.report.desktop_pending = pending;
                        s.report.error = fault;
                    });
                    return if connect.cancel.is_cancelled() {
                        Err("CANCELLED")
                    } else if let Some(error) = fault {
                        Err(error)
                    } else {
                        Ok(())
                    };
                }
                DesktopV3Work::Recover(_recover_request) => {
                    let previous = previous_io.ok_or("DESKTOP_PREVIOUS_CONNECTION_UNPROVEN")?;
                    let live_capture = signals
                        .shared
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .desktop_v3_capture
                        .clone();
                    let mut outcome = desktop_v3::recovery::recover_remote(
                        &client,
                        &connect.router,
                        desktop_v3::recovery::Recovery {
                            profile,
                            session: connect.session,
                            uid,
                            files,
                            pins: configuration.pins(),
                            storage,
                            previous_connection_closed: false,
                        },
                        previous,
                        connect.session.get() as u64,
                        &connect.cancel,
                        #[cfg(feature = "host-acceptance")]
                        live_capture.as_deref(),
                    )
                    .await;
                    let bridged = if let (Some(handoff), Some(capture)) =
                        (outcome.handoff.as_mut(), live_capture.as_ref())
                    {
                        match handoff.bind_live_recovery(previous, capture) {
                            Ok(()) => {
                                #[cfg(feature = "host-acceptance")]
                                {
                                    handoff.deferred.observe_with(capture);
                                    capture.observe("recovery_bound", serde_json::json!({"generation":connect.session.get().to_string()}));
                                }
                                true
                            }
                            Err(error) => {
                                outcome.report.error = Some(error);
                                false
                            }
                        }
                    } else {
                        false
                    };
                    if live_capture.is_none()
                        && let Some(handoff) = outcome.handoff.as_mut()
                    {
                        let local = handoff
                            .bind_durable_local_retirement(files, signals.android_boot_count);
                        if let Err(error) = local {
                            outcome.handoff = None;
                            outcome.report.error = Some(error);
                        }
                    }
                    let fault = outcome.report.error;
                    signals.update(|s| {
                        if bridged && let Some(handoff) = outcome.handoff.as_mut() {
                            handoff.no_local_allocation = s.desktop_v3_no_local_allocation.take();
                        }
                        s.forward_busy = false;
                        s.report.desktop_v3_recovery = Some(outcome.report);
                        s.desktop_v3_handoff = outcome.handoff;
                        s.report.state = State::CleanupPending;
                        s.report.desktop_pending = true;
                        s.report.error = fault;
                    });
                    return if connect.cancel.is_cancelled() {
                        Err("CANCELLED")
                    } else if let Some(error) = fault {
                        Err(error)
                    } else {
                        Ok(())
                    };
                }
            }
        }
        if let Some(work) = desktop_work {
            let config = connect
                .profile
                .desktop
                .as_ref()
                .ok_or("DESKTOP_BACKEND_MISMATCH")?;
            let (profile, pins, files) = match config {
                DesktopConfig::V2 {
                    profile,
                    pins,
                    files,
                } => (profile, pins, files),
                _ => return Err("DESKTOP_VERSION_SELECTION"),
            };
            match work {
                DesktopWork::Inspect(request) => {
                    let nonce = format!("{:016x}", rand::random::<u64>());
                    let captured = linux_desktop::Binding::capture(
                        profile,
                        connect.session,
                        uid,
                        &nonce,
                        pins.clone(),
                    )?;
                    let (sender, requests) = tokio::sync::mpsc::channel(1);
                    signals.update(|s| {
                        s.desktop_v2_capture = Some(captured.clone());
                        s.desktop_launches = Some(sender);
                    });
                    let report = linux_desktop::serve(
                        &client,
                        &connect.router,
                        linux_desktop::Inspection {
                            binding: &captured,
                            files,
                        },
                        requests,
                        &connect.cancel,
                        |report| {
                            signals.update(|s| {
                                s.report.desktop = Some(report.clone());
                                s.report.desktop_pending = report.journal_pending;
                                if matches!(
                                    report.state,
                                    "stopping" | "cleanup_pending" | "failed" | "stopped"
                                ) {
                                    s.catalog.desktop = None;
                                    s.catalog.pending = false;
                                    s.catalog.error = report.error;
                                    if report.journal_pending && report.finished {
                                        s.report.state = State::CleanupPending;
                                    } else {
                                        s.report.state = State::Disconnecting;
                                    }
                                }
                            })
                        },
                        |selection| {
                            signals.update(|s| {
                                if s.report.session == Some(connect.session)
                                    && s.catalog.request_id == Some(request)
                                    && !connect.cancel.is_cancelled()
                                {
                                    s.catalog.pending = false;
                                    s.catalog.desktop = Some(DesktopCatalogUi {
                                        namespace: "linux-desktop",
                                        selection: selection.clone(),
                                        label: "Linux アプリ",
                                        mode: "gui",
                                        instance_limit: 1,
                                    });
                                    s.report.state = State::DesktopReady;
                                }
                            })
                        },
                    )
                    .await;
                    signals.update(|s| {
                        s.forward_busy = false;
                        s.desktop_launches = None;
                        s.catalog.desktop = None;
                    });
                    // A helper is single use. Retire its SSH runtime before a
                    // new Connect/Inspect or explicit recovery can be accepted.
                    return if connect.cancel.is_cancelled() {
                        Err("CANCELLED")
                    } else if let Some(error) = report.error {
                        Err(error)
                    } else {
                        Ok(())
                    };
                }
                DesktopWork::Recover(_request) => {
                    let mut report = linux_desktop::recover(
                        &client,
                        &connect.router,
                        linux_desktop::Recovery {
                            profile,
                            session: connect.session,
                            uid,
                            files,
                            pins,
                            // A pending helper always ended connection() above.
                            // owner() dropped that runtime before accepting this
                            // new Connect, and no helper is active on this one.
                            previous_connection_closed: true,
                        },
                        &connect.cancel,
                    )
                    .await;
                    if !report.pending && report.origin_session.is_none() {
                        // We entered through a persisted pending barrier. A
                        // missing file is not a receipt for that known owner.
                        report.pending = true;
                        report.error = Some("DESKTOP_JOURNAL_OWNER_CHANGED");
                    }
                    let pending = linux_desktop::pending(files);
                    signals.update(|s| {
                        s.forward_busy = false;
                        s.desktop_pending = if report.pending && matches!(pending, Ok(None)) {
                            Err("DESKTOP_JOURNAL_OWNER_CHANGED")
                        } else {
                            pending
                        };
                        s.report.desktop_pending =
                            report.pending || !matches!(s.desktop_pending, Ok(None));
                        s.report.state = if s.report.desktop_pending {
                            State::CleanupPending
                        } else {
                            State::ProbeReady
                        };
                        s.report.error = report.error;
                        s.report.desktop_recovery = Some(report);
                    });
                    continue;
                }
            }
        }
        #[cfg(any(test, feature = "debug-fixtures"))]
        let linux_work = signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .linux
            .take();
        #[cfg(any(test, feature = "debug-fixtures"))]
        if let Some(work) = linux_work {
            if connect.profile.helper_supported {
                return Err("LINUX_BACKEND_MISMATCH");
            }
            let revision = connect
                .profile
                .revision
                .as_deref()
                .ok_or("LINUX_BACKEND_MISMATCH")?;
            if let Some(socket) = work.local_socket.as_deref() {
                let report = crate::linux_probe::run(
                    &client,
                    &connect.router,
                    crate::linux_probe::Request {
                        session: connect.session,
                        profile_binding: &binding,
                        profile_revision: revision,
                        files: &work.files,
                        local_socket: socket,
                        nonce: &work.nonce,
                        uid,
                        pins: &work.pins,
                        gtk: work.gtk.as_ref(),
                    },
                    &connect.cancel,
                    |value| signals.update(|s| s.report.linux_forward = Some(value.clone())),
                )
                .await;
                signals.update(|s| {
                    s.report.linux_forward = Some(report);
                    s.forward_busy = false;
                });
            } else {
                let report = crate::linux_probe::recover(
                    &client,
                    crate::linux_probe::Recovery {
                        session: connect.session,
                        profile_binding: &binding,
                        profile_revision: revision,
                        files: &work.files,
                        nonce: &work.nonce,
                        uid,
                        pins: &work.pins,
                        gtk: work.gtk.as_ref(),
                    },
                    &connect.cancel,
                )
                .await;
                signals.update(|s| {
                    s.report.linux_recovery = Some(report);
                    s.forward_busy = false;
                });
            }
            if connect.cancel.is_cancelled() {
                return Err("CANCELLED");
            }
            continue;
        }
        if let Some((local_socket, nonce, mode, selection, gui_grace)) = app {
            let (sender, terminal_requests) = tokio::sync::mpsc::channel(1);
            let (catalog_sender, catalog_requests) = tokio::sync::mpsc::channel(1);
            let (tick_sender, liveness) = tokio::sync::watch::channel(None);
            signals.update(|s| {
                s.termux_ticks = Some(tick_sender);
                s.terminal_requests = Some(sender);
                s.catalog_requests = Some(catalog_sender);
                s.catalog = CatalogUi::default();
            });
            let forward_app = forward_app::run(
                &client,
                &connect.router,
                forward_app::Request {
                    session: connect.session,
                    local_socket: &local_socket,
                    nonce: &nonce,
                    mode,
                    uid,
                    journal,
                    binding: &binding,
                    terminal_requests,
                    catalog_requests,
                    selection,
                    observation: signals.observation.subscribe(),
                    gui_grace,
                    liveness,
                },
                &connect.cancel,
                |r| {
                    signals.update(|s| {
                        s.report.app_forward = Some(r.clone());
                        s.report.pending_cleanup = r.pending_cleanup.clone();
                    })
                },
                |request, result| {
                    signals.update(|s| {
                        if s.report.session == Some(connect.session)
                            && s.catalog.request_id == Some(request)
                        {
                            s.catalog.pending = false;
                            match result {
                                Ok(reply) => s.catalog.reply = Some(reply),
                                Err(error) => s.catalog.error = Some(error),
                            }
                        }
                    })
                },
            );
            let refresh = async {
                loop {
                    let notified = signals.desktop_menu_notify.notified();
                    refresh_termux_shelf(signals, &client, connect).await;
                    notified.await;
                }
            };
            let report = {
                let ime = async {
                    while let Some(request) = ime_requests.recv().await {
                        crate::input_method::start(&client, request).await;
                    }
                };
                tokio::select! { value = forward_app => value, _ = refresh => unreachable!(), _ = ime, if ime_enabled => unreachable!() }
            };
            signals.update(|s| {
                s.forward_busy = false;
                s.terminal_requests = None;
                s.catalog_requests = None;
                s.catalog = CatalogUi::default();
            });
            if connect.cancel.is_cancelled() {
                return Err("CANCELLED");
            }
            if let Some(error) = report.error {
                return Err(error);
            }
        } else if let Some((files, nonce, scenario)) = forward {
            let report = forward_probe::run(
                &client,
                &connect.router,
                forward_probe::Request {
                    session: connect.session,
                    files: &files,
                    nonce: &nonce,
                    uid,
                    journal,
                    binding: &binding,
                    scenario,
                },
                &connect.cancel,
                |r| {
                    signals.update(|s| {
                        s.report.forward_probe = Some(r.clone());
                        s.report.pending_cleanup = r.pending_cleanup.clone();
                    })
                },
            )
            .await;
            signals.update(|s| {
                s.forward_busy = false;
                s.terminal_requests = None;
            });
            if connect.cancel.is_cancelled() {
                return Err("CANCELLED");
            }
            if let Some(error) = report.error {
                return Err(error);
            }
        } else {
            tokio::select! { biased;
                _=connect.cancel.cancelled()=>return Err("CANCELLED"),
                request = ime_requests.recv(), if ime_enabled => {
                    if let Some(request) = request { crate::input_method::start(&client, request).await; }
                }
                _=&mut client=>return Err(if mismatch.load(Ordering::Acquire) { "HOST_KEY_MISMATCH" } else { "REMOTE_EXIT" }),
                _=notified=>(),
                _=tokio::time::sleep(Duration::from_secs(1)),if !journal.is_empty()=>{reconcile(signals,&client,connect,journal,&binding,uid).await?;},
            }
        }
    }
}

async fn refresh_termux_shelf<H: client::Handler>(
    signals: &Signals,
    client: &client::Handle<H>,
    connect: &Connect,
) {
    let work = signals
        .shared
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .shelf_request
        .take();
    let Some((session, request, path, diagnostics)) = work else {
        return;
    };
    if session != connect.session {
        return;
    }
    let result = crate::launcher::shelf(client, &path, diagnostics, &connect.cancel).await;
    let value = match result {
        Ok(data) => serde_json::json!({"request":request, "data":data}),
        Err(code) => serde_json::json!({"request":request, "error":code}),
    };
    signals.update(|s| {
        if s.report.session == Some(session) && !connect.cancel.is_cancelled() {
            s.shelf_result = Some((session, value));
        }
    });
}

async fn refresh_desktop_preview<H: client::Handler>(
    signals: &Signals,
    client: &client::Handle<H>,
    cancel: &CancellationToken,
) {
    let request = signals
        .shared
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .desktop_preview
        .as_ref()
        .filter(|preview| preview.pending)
        .map(|preview| preview.request_id);
    let Some(request) = request else { return };
    let result = desktop_v3::installed::discover(client, cancel).await;
    signals.update(|s| {
        if let Some(preview) = s
            .desktop_preview
            .as_mut()
            .filter(|preview| preview.pending && preview.request_id == request)
        {
            preview.complete(result);
        }
    });
}

async fn reconcile<H: client::Handler>(
    signals: &Signals,
    client: &client::Handle<H>,
    connect: &Connect,
    journal: &mut journal::Journal,
    binding: &str,
    uid: u32,
) -> Result<(), &'static str> {
    let mut audits = Vec::new();
    if !journal.is_empty() {
        signals.update(|s| {
            if s.report.state != State::CleanupPending {
                s.report.state = State::Recovering;
            }
        });
    }
    for entry in journal.entries() {
        let mut view = CleanupAudit {
            nonce: entry.nonce.clone(),
            origin_session: entry.session,
            current_session: connect.session,
            verified: false,
            child: entry.child.clone(),
            active: false,
            orphaned: false,
            child_alive: false,
            reason: None,
            error: None,
        };
        if entry.binding != binding || entry.uid != uid {
            view.error = Some("CLEANUP_PROFILE_MISMATCH");
        } else if !connect.profile.helper_supported {
            view.error = Some("CLEANUP_BACKEND_UNSUPPORTED");
        } else {
            let audit = tokio::select! {biased;
                _=connect.cancel.cancelled()=>return Err("CANCELLED"),
                audit=launcher::audit(client,&entry.nonce,uid,entry.child.as_ref())=>audit,
            };
            match audit {
                Ok(value) => {
                    view.verified = value.verified;
                    view.active = value.active;
                    view.orphaned = value.orphaned;
                    view.child_alive = value.child_alive;
                    view.reason = value
                        .receipt
                        .map(|r| r.reason)
                        .or_else(|| value.recovery_basis.map(|basis| format!("{basis:?}")));
                    if value.verified {
                        journal.forget(&entry.nonce)?;
                    }
                }
                Err(error) => view.error = Some(error),
            }
        }
        audits.push(view);
    }
    signals.update(|s| {
        s.report.pending_cleanup = journal.pending();
        s.report.cleanup_audits = audits;
        if journal.is_empty() && !s.report.desktop_pending {
            s.report.state = if connect.profile.helper_supported {
                State::Ready
            } else {
                State::ProbeReady
            };
            s.report.error = None;
        } else {
            s.report.state = State::CleanupPending;
            s.report.error = Some(if s.report.desktop_pending {
                "DESKTOP_CLEANUP_PENDING"
            } else {
                "CLEANUP_PENDING"
            });
        }
    });
    Ok(())
}

#[cfg(test)]
mod tests;
