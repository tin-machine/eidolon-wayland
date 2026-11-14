//! Single-use, native-owned v3 launch admission. A one-slot Tokio mailbox is
//! not an authorization gate: its slot becomes vacant as soon as the actor
//! dequeues a Launch. Gate transitions are therefore independent of capacity.
use super::{Binding, ExpectedEvidence, Selection, sources};
use eidolon_model::{
    ids::{CommandSeq, RuntimeHandle, SessionId},
    linux_desktop_v3::presentation::NativeOrigin,
};
use std::{
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicU8, Ordering},
    },
};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

const OPEN: u8 = 0;
const RESERVED: u8 = 1;
const QUEUED: u8 = 2;
const START_ATTEMPTED: u8 = 3;
const SEALED: u8 = 4;

/// Private authenticated state, not a UI-generated capability. There is no
/// Clone/Deserialize implementation for a LaunchPermit or its owner.
pub struct RunCapture {
    binding: Binding,
    evidence: ExpectedEvidence,
    pub inspect_request: CommandSeq,
    pub generation: u64,
}
impl RunCapture {
    pub fn new(
        binding: Binding,
        evidence: ExpectedEvidence,
        inspect_request: CommandSeq,
        generation: u64,
    ) -> Result<Self, &'static str> {
        if generation == 0 {
            return Err("DESKTOP_BINDING_INVALID");
        }
        sources::require_complete()?;
        sources::validate_hashes(&evidence.source_hashes())?;
        evidence.matches_binding(&binding)?;
        Ok(Self {
            binding,
            evidence,
            inspect_request,
            generation,
        })
    }
    pub fn binding(&self) -> &Binding {
        &self.binding
    }
    pub fn evidence(&self) -> &ExpectedEvidence {
        &self.evidence
    }
}

struct Gate {
    state: AtomicU8,
    cancel: CancellationToken,
}
impl Gate {
    fn seal(&self) {
        self.state.store(SEALED, Ordering::Release);
    }
}

/// Native code must hold the reservation while it prepares local resources.
/// Dropping it before Start was durably attempted stops this helper rather
/// than allowing an uncontrolled second launch in the same catalog.
pub struct LaunchPermit {
    capture: Arc<RunCapture>,
    gate: Arc<Gate>,
    origin: NativeOrigin,
    selection: Selection,
    request: CommandSeq,
    armed: bool,
}
impl Drop for LaunchPermit {
    fn drop(&mut self) {
        if self.armed {
            self.gate.seal();
            self.gate.cancel.cancel();
        }
    }
}
impl LaunchPermit {
    pub fn origin(&self) -> &NativeOrigin {
        &self.origin
    }
    /// Native-only borrowed capture for I3 preparation and later ownership checks.
    /// Neither this reference nor the move-only permit grants a remote/UI command.
    pub fn captured_context(&self) -> &super::Context {
        self.capture.binding.context()
    }
    /// The exact validated nonce used by NativeOrigin::capture for this permit.
    /// I3 must not reconstruct it from report JSON or an Android input field.
    pub fn local_nonce(&self) -> &eidolon_model::linux_desktop_v3::Nonce {
        self.captured_context().nonce()
    }
    pub fn selection(&self) -> &Selection {
        &self.selection
    }
    pub fn request(&self) -> CommandSeq {
        self.request
    }
    pub fn generation(&self) -> u64 {
        self.capture.generation
    }
    fn mark_start_attempted(&mut self) -> Result<(), &'static str> {
        self.gate
            .state
            .compare_exchange(QUEUED, START_ATTEMPTED, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "DESKTOP_CATALOG_STALE")?;
        self.armed = false;
        Ok(())
    }
}

/// Only the authenticated actor can consume this mailbox message. No field is
/// deserialized from an untrusted command.
pub struct Launch {
    pub permit: LaunchPermit,
    pub local_socket: PathBuf,
}
impl Launch {
    /// Call exactly after Stage::StartRequested has been durably persisted.
    /// Never call this to "retry" a failed enqueue or an uncertain Start.
    pub fn start_attempted(&mut self) -> Result<(), &'static str> {
        self.permit.mark_start_attempted()
    }
}

pub struct LaunchAdmission {
    capture: Arc<RunCapture>,
    gate: Arc<Gate>,
    sender: mpsc::Sender<Launch>,
}
impl LaunchAdmission {
    pub(crate) fn capture(&self) -> Arc<RunCapture> {
        self.capture.clone()
    }
    pub fn gate_open(&self) -> bool {
        !self.gate.cancel.is_cancelled() && self.gate.state.load(Ordering::Acquire) == OPEN
    }
    pub fn new(
        capture: RunCapture,
        sender: mpsc::Sender<Launch>,
        cancel: CancellationToken,
    ) -> Self {
        Self {
            capture: Arc::new(capture),
            gate: Arc::new(Gate {
                state: AtomicU8::new(OPEN),
                cancel,
            }),
            sender,
        }
    }
    pub fn reserve(
        &self,
        runtime: RuntimeHandle,
        session: SessionId,
        request: CommandSeq,
        selection: &Selection,
    ) -> Result<LaunchPermit, &'static str> {
        if self.gate.cancel.is_cancelled()
            || self.sender.is_closed()
            || session != self.capture.binding.session()
        {
            return Err("DESKTOP_CATALOG_STALE");
        }
        // Compute the immutable native origin before consuming the gate. A
        // foreign session/selection must not burn an otherwise valid launch.
        let origin = NativeOrigin::capture(
            runtime,
            request,
            self.capture.binding.context(),
            selection,
            self.capture.binding.pins(),
        )
        .map_err(|e| e.0)?;
        self.gate
            .state
            .compare_exchange(OPEN, RESERVED, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "DESKTOP_CATALOG_STALE")?;
        Ok(LaunchPermit {
            capture: self.capture.clone(),
            gate: self.gate.clone(),
            origin,
            selection: selection.clone(),
            request,
            armed: true,
        })
    }
    pub fn enqueue(&self, permit: LaunchPermit, socket: &Path) -> Result<(), &'static str> {
        if !Arc::ptr_eq(&self.capture, &permit.capture) || !Arc::ptr_eq(&self.gate, &permit.gate) {
            return Err("DESKTOP_CATALOG_STALE");
        }
        if !socket.is_absolute()
            || socket.as_os_str().len() > 103
            || socket.as_os_str().as_encoded_bytes().contains(&0)
        {
            return Err("DESKTOP_LOCAL_SOCKET_INVALID");
        }
        if self.gate.cancel.is_cancelled() || self.sender.is_closed() {
            return Err("CANCELLED");
        }
        self.gate
            .state
            .compare_exchange(RESERVED, QUEUED, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "DESKTOP_CATALOG_STALE")?;
        self.sender
            .try_send(Launch {
                permit,
                local_socket: socket.to_owned(),
            })
            .map_err(|e| match e {
                mpsc::error::TrySendError::Full(_) => "DESKTOP_COMMANDS_FULL",
                mpsc::error::TrySendError::Closed(_) => "DESKTOP_COMMANDS_CLOSED",
            })
    }
    pub fn seal(&self) {
        self.gate.seal();
    }
}

mod serve;
pub use serve::{Inspection, RunOutcome, TransportSnapshot, serve};

#[cfg(test)]
mod tests {
    use super::*;
    use crate::linux_desktop::v3::owner::test_support::{binding, evidence, raw};

    fn setup() -> (
        LaunchAdmission,
        mpsc::Receiver<Launch>,
        CancellationToken,
        Selection,
    ) {
        let capture =
            RunCapture::new(binding(), evidence(), CommandSeq::new(7).unwrap(), 1).unwrap();
        let selection = Selection::parse(&raw("selection")).unwrap();
        let (sender, receiver) = mpsc::channel(1);
        let cancel = CancellationToken::new();
        (
            LaunchAdmission::new(capture, sender, cancel.clone()),
            receiver,
            cancel,
            selection,
        )
    }
    fn reserve(admission: &LaunchAdmission, selection: &Selection) -> LaunchPermit {
        admission
            .reserve(
                RuntimeHandle::new(9).unwrap(),
                admission.capture.binding.session(),
                CommandSeq::new(8).unwrap(),
                selection,
            )
            .unwrap()
    }
    #[test]
    fn reservation_precedes_socket_and_dropped_permit_cancels_owner() {
        let (admission, _rx, cancel, selection) = setup();
        let permit = reserve(&admission, &selection);
        assert!(
            admission
                .reserve(
                    RuntimeHandle::new(9).unwrap(),
                    admission.capture.binding.session(),
                    CommandSeq::new(9).unwrap(),
                    &selection,
                )
                .is_err()
        );
        drop(permit);
        assert!(cancel.is_cancelled());
    }
    #[test]
    fn local_nonce_and_context_are_from_the_same_native_capture() {
        let (admission, _rx, _cancel, selection) = setup();
        let permit = reserve(&admission, &selection);
        assert!(permit.captured_context() == admission.capture.binding.context());
        assert_eq!(permit.local_nonce(), permit.captured_context().nonce());
        assert_eq!(
            permit.origin().record()["nonce"].as_str(),
            Some(permit.local_nonce().as_str())
        );
        assert_eq!(permit.generation(), admission.capture.generation);
    }

    #[test]
    fn full_mailbox_fails_closed_without_a_second_start() {
        let (admission, _rx, cancel, selection) = setup();
        let (other, _other_rx, _other_cancel, other_selection) = setup();
        let foreign = Launch {
            permit: reserve(&other, &other_selection),
            local_socket: PathBuf::from("/tmp/eidolon-v3-other.sock"),
        };
        assert!(admission.sender.try_send(foreign).is_ok());
        let permit = reserve(&admission, &selection);
        assert!(matches!(
            admission.enqueue(permit, Path::new("/tmp/eidolon-v3-test.sock")),
            Err("DESKTOP_COMMANDS_FULL")
        ));
        assert!(cancel.is_cancelled(), "a failed send must seal the helper");
        assert!(!admission.gate_open());
    }

    #[test]
    fn stop_seals_a_reserved_permit_before_enqueue() {
        let (admission, _rx, cancel, selection) = setup();
        let permit = reserve(&admission, &selection);
        admission.seal();
        assert!(
            admission
                .enqueue(permit, Path::new("/tmp/eidolon-v3-test.sock"))
                .is_err()
        );
        assert!(cancel.is_cancelled());
    }

    #[tokio::test]
    async fn dequeuing_does_not_reopen_launch_gate() {
        let (admission, mut rx, cancel, selection) = setup();
        let permit = reserve(&admission, &selection);
        admission
            .enqueue(permit, Path::new("/tmp/eidolon-v3-test.sock"))
            .unwrap();
        let mut launch = rx.recv().await.unwrap();
        assert!(!cancel.is_cancelled());
        assert!(
            admission
                .reserve(
                    RuntimeHandle::new(9).unwrap(),
                    admission.capture.binding.session(),
                    CommandSeq::new(9).unwrap(),
                    &selection,
                )
                .is_err(),
            "receiver capacity cannot authorize the second launch"
        );
        launch.start_attempted().unwrap();
        assert!(launch.start_attempted().is_err());
        drop(launch);
        assert!(
            !cancel.is_cancelled(),
            "durable Start uncertainty is recovered, not cancelled by permit Drop"
        );
    }
    #[tokio::test]
    async fn mailbox_drop_before_start_seals_helper() {
        let (admission, rx, cancel, selection) = setup();
        let permit = reserve(&admission, &selection);
        admission
            .enqueue(permit, Path::new("/tmp/eidolon-v3-test.sock"))
            .unwrap();
        drop(rx);
        assert!(cancel.is_cancelled());
        assert!(
            admission
                .reserve(
                    RuntimeHandle::new(9).unwrap(),
                    admission.capture.binding.session(),
                    CommandSeq::new(9).unwrap(),
                    &selection,
                )
                .is_err()
        );
    }
}
