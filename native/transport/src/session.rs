//! SSHはI/O ownerが作るTokio runtime上だけで実行する。再接続前に旧runtimeを回収する。
use crate::{
    connect_profile::{ConnectSpec, DesktopConfig},
    exec, forward, forward_app, forward_probe,
    host_key::Profile,
    journal, launcher, linux_desktop,
};
use eidolon_model::{
    commands::{AppMode, CleanupScenario},
    ids::{CommandSeq, SessionId},
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
    /// Dedicated normal Linux owner; independent from Termux pending_cleanup.
    pub desktop_pending: bool,
    #[cfg(any(test, feature = "debug-fixtures"))]
    pub linux_forward: Option<crate::linux_probe::Report>,
    #[cfg(any(test, feature = "debug-fixtures"))]
    pub linux_recovery: Option<crate::linux_probe::RecoveryReport>,
    pub journal_ready: bool,
    pub pending_cleanup: Vec<journal::Pending>,
    pub cleanup_audits: Vec<CleanupAudit>,
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
}
struct Connect {
    session: SessionId,
    router: Arc<forward::Router>,
    profile: ConnectSpec,
    request: CommandSeq,
    cancel: CancellationToken,
}
struct Shared {
    report: Snapshot,
    connect: Option<PendingConnect>,
    credential: Option<PrivateKey>,
    credential_scope: Option<CredentialScope>,
    busy: bool,
    active_connect_request: Option<CommandSeq>,
    cancel: Option<CancellationToken>,
    router: Option<Arc<forward::Router>>,
    forward: Option<(PathBuf, String, Option<CleanupScenario>)>,
    app: Option<(PathBuf, String, AppMode)>,
    desktop_profile: Option<DesktopConfig>,
    desktop_pending: Result<Option<linux_desktop::Pending>, &'static str>,
    desktop_work: Option<DesktopWork>,
    desktop_launches: Option<tokio::sync::mpsc::Sender<linux_desktop::Launch>>,
    desktop_launch_queued: bool,
    #[cfg(any(test, feature = "debug-fixtures"))]
    linux: Option<LinuxWork>,
    forward_busy: bool,
    terminal_requests:
        Option<tokio::sync::mpsc::Sender<(CommandSeq, Option<crate::catalog::Target>)>>,
    catalog_requests: Option<tokio::sync::mpsc::Sender<(CommandSeq, crate::catalog::Operation)>>,
    catalog: CatalogUi,
}
#[derive(Clone, Copy)]
enum DesktopWork {
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
    stop: AtomicBool,
    tokio_workers: AtomicUsize,
    observation: tokio::sync::watch::Sender<bool>,
    files: Option<PathBuf>,
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
        Self::start_at(
            (Ipv4Addr::LOCALHOST, 8022).into(),
            Deadlines::default(),
            root,
        )
    }
    #[cfg(test)]
    fn start_on(endpoint: SocketAddr, deadlines: Deadlines) -> std::io::Result<Self> {
        Self::start_at(endpoint, deadlines, None)
    }
    fn start_at(
        endpoint: SocketAddr,
        deadlines: Deadlines,
        root: Option<PathBuf>,
    ) -> std::io::Result<Self> {
        Self::start_at_with_hook(endpoint, deadlines, root, || {})
    }
    fn start_at_with_hook(
        _legacy_endpoint: SocketAddr,
        deadlines: Deadlines,
        root: Option<PathBuf>,
        before_owner: impl FnOnce() + Send + 'static,
    ) -> std::io::Result<Self> {
        // Read the normal barrier before a caller can queue its first Connect.
        // An invalid record remains pending; it must not become an empty state
        // merely because the app/Controller was restarted.
        let desktop_pending = root
            .as_deref()
            .map(linux_desktop::pending)
            .unwrap_or(Ok(None));
        let has_desktop_pending = !matches!(desktop_pending, Ok(None));
        let signals = Arc::new(Signals {
            shared: Mutex::new(Shared {
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
                desktop_profile: None,
                desktop_pending,
                desktop_work: None,
                desktop_launches: None,
                desktop_launch_queued: false,
                #[cfg(any(test, feature = "debug-fixtures"))]
                linux: None,
                forward_busy: false,
                terminal_requests: None,
                catalog_requests: None,
                catalog: CatalogUi::default(),
            }),
            cv: Condvar::new(),
            notify: Notify::new(),
            stop: AtomicBool::new(false),
            tokio_workers: AtomicUsize::new(0),
            observation: tokio::sync::watch::channel(true).0,
            files: root.clone(),
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
    pub fn connect_spec(&self, profile: ConnectSpec, request: CommandSeq) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if self.signals.stop.load(Ordering::Acquire) || self.worker.is_finished() {
            return Status::NotReady;
        }
        // I2 owns v3 execution. Never run an admitted v3 capture through the
        // existing v2 SSH actor while this PR defines only shared contracts.
        if profile
            .desktop
            .as_ref()
            .is_some_and(|config| config.version() == linux_desktop::dispatch::Version::V3)
        {
            return Status::NotReady;
        }
        if s.busy {
            return Status::Busy;
        }
        match &s.desktop_pending {
            Ok(None) => (),
            Ok(Some(pending)) => {
                if profile
                    .desktop
                    .as_ref()
                    .is_none_or(|config| match config {
                        DesktopConfig::V2 { profile, pins, .. } => !pending.matches(profile, pins),
                        DesktopConfig::V3 { .. } => true,
                    })
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
        s.busy = true;
        s.active_connect_request = Some(request);
        s.report = Snapshot {
            state: State::Connecting,
            session: None,
            profile_id: Some(profile.profile_id.clone()),
            profile_revision: profile.revision.clone(),
            workers: 1,
            credential_requests: s.report.credential_requests,
            journal_ready: s.report.journal_ready,
            pending_cleanup: s.report.pending_cleanup.clone(),
            desktop_pending: s.report.desktop_pending,
            ..Default::default()
        };
        let cancel = CancellationToken::new();
        s.credential_scope = None;
        s.cancel = Some(cancel.clone());
        s.router = None;
        s.desktop_profile = profile.desktop.clone();
        s.desktop_work = None;
        s.desktop_launches = None;
        s.desktop_launch_queued = false;
        s.catalog = CatalogUi::default();
        s.connect = Some(PendingConnect {
            profile,
            request,
            cancel,
        });
        self.signals.cv.notify_one();
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
    pub fn snapshot(&self) -> Snapshot {
        let shared = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let mut report = shared.report.clone();
        if let Some(router) = &shared.router {
            report.forward = router.report();
        }
        report.tokio_threads = self.signals.tokio_workers.load(Ordering::Acquire);
        report
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
        s.app = Some((local_socket.into(), nonce.into(), mode));
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

    pub fn inspect_linux_desktop(&self, session: SessionId, request: CommandSeq) -> Status {
        let mut s = self
            .signals
            .shared
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if s.report.session != Some(session) {
            return Status::Stale;
        }
        if s.desktop_profile.is_none()
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
        if s.desktop_profile.is_none()
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
        if s.desktop_profile.is_none()
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
        if s.desktop_profile.is_some() {
            return if matches!(s.report.state, State::ProbeReady | State::DesktopReady)
                && s.cancel.as_ref().is_some_and(|c| !c.is_cancelled())
            {
                s.catalog.clone()
            } else {
                CatalogUi::default()
            };
        }
        if s.report.state != State::Ready
            || s.cancel.as_ref().is_none_or(|c| c.is_cancelled())
            || s.catalog_requests.is_none()
        {
            return CatalogUi::default();
        }
        s.catalog.clone()
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
        if s.report.state != State::Ready || s.cancel.as_ref().is_none_or(|c| c.is_cancelled()) {
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
                    s.cancel = None;
                    s.busy = false;
                    s.active_connect_request = None;
                    s.report.state = State::Failed;
                    s.report.error = Some(error);
                });
                continue;
            }
        };
        let connect = Connect {
            session,
            router: Arc::new(forward::Router::new(session)),
            profile: pending.profile,
            request: pending.request,
            cancel: pending.cancel,
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
                let result =
                    runtime.block_on(connection(signals, &connect, deadlines, &mut journal));
                // UI/coreではなくownerでdrop。未完SSH task/FD/秘密keyとTokio workerを回収。
                drop(runtime);
                result
            }
            Err(_) => Err("SSH_IO_FAILED"),
        };
        let desktop_pending = root.map(linux_desktop::pending).unwrap_or(Ok(None));
        signals.update(|s| {
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
            s.catalog = CatalogUi::default();
            s.desktop_pending = if s.report.desktop_pending && matches!(desktop_pending, Ok(None)) {
                // A file disappearing without the actor/recovery's proof must
                // not turn Unknown into Clean at connection retirement.
                Err("DESKTOP_JOURNAL_OWNER_CHANGED")
            } else {
                desktop_pending
            };
            s.report.desktop_pending = !matches!(s.desktop_pending, Ok(None));
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
    reconcile(signals, &client, connect, journal, &binding, uid).await?;
    loop {
        if connect.cancel.is_cancelled() {
            return Err("CANCELLED");
        }
        let notified = signals.notify.notified();
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
                DesktopConfig::V3 { .. } => return Err("DESKTOP_VERSION_SELECTION"),
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
                    signals.update(|s| s.desktop_launches = Some(sender));
                    let report = linux_desktop::serve(
                        &client,
                        &connect.router,
                        linux_desktop::Inspection {
                            binding: &captured,
                            files: files,
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
                            profile: profile,
                            session: connect.session,
                            uid,
                            files: files,
                            pins: pins,
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
        if let Some((local_socket, nonce, mode)) = app {
            let (sender, terminal_requests) = tokio::sync::mpsc::channel(1);
            let (catalog_sender, catalog_requests) = tokio::sync::mpsc::channel(1);
            signals.update(|s| {
                s.terminal_requests = Some(sender);
                s.catalog_requests = Some(catalog_sender);
                s.catalog = CatalogUi::default();
            });
            let report = forward_app::run(
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
                    observation: signals.observation.subscribe(),
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
            )
            .await;
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
                _=&mut client=>return Err(if mismatch.load(Ordering::Acquire) { "HOST_KEY_MISMATCH" } else { "REMOTE_EXIT" }),
                _=notified=>(),
                _=tokio::time::sleep(Duration::from_secs(1)),if !journal.is_empty()=>{reconcile(signals,&client,connect,journal,&binding,uid).await?;},
            }
        }
    }
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
                    view.reason = value.receipt.map(|r| r.reason);
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
