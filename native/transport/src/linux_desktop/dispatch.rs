//! Explicit local version selection. Remote bytes never choose a decoder.
//! Existing v2 types and semantics stay unchanged behind the v2 branch.
use super::{self as v2, v3};
use eidolon_model::{connection_profile::Profile, ids::SessionId, linux_desktop_v3::StorageRecord};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Version {
    V2,
    V3,
}
impl Version {
    pub fn revision(self) -> &'static str {
        match self {
            Self::V2 => v2::REVISION,
            Self::V3 => v3::REVISION,
        }
    }
}
#[derive(Clone)]
pub enum Pins {
    V2(v2::Pins),
    V3(v3::Pins),
}
impl Pins {
    /// The caller chooses a saved version before reading remote protocol bytes.
    /// No retry against another parser on errors, and no schema-based upgrade.
    pub fn parse(selected: Version, raw: &[u8]) -> Result<Self, &'static str> {
        match selected {
            Version::V2 => {
                v2::Pins::parse(std::str::from_utf8(raw).map_err(|_| "DESKTOP_PIN_INVALID")?)
                    .map(Self::V2)
            }
            Version::V3 => v3::Pins::parse(raw).map(Self::V3).map_err(|e| e.0),
        }
    }
    pub fn version(&self) -> Version {
        match self {
            Self::V2(_) => Version::V2,
            Self::V3(_) => Version::V3,
        }
    }
}
#[derive(Clone)]
pub enum Binding {
    V2(v2::Binding),
    V3(v3::Binding),
}
impl Binding {
    pub fn capture(
        profile: &Profile,
        session: SessionId,
        uid: u32,
        nonce: &str,
        pins: Pins,
        storage: Option<&StorageRecord>,
    ) -> Result<Self, &'static str> {
        match (pins, storage) {
            (Pins::V2(pins), None) => {
                v2::Binding::capture(profile, session, uid, nonce, pins).map(Self::V2)
            }
            (Pins::V3(pins), Some(storage)) => {
                v3::Binding::capture(profile, session, uid, nonce, pins, storage).map(Self::V3)
            }
            _ => Err("DESKTOP_VERSION_SELECTION"),
        }
    }
    pub fn version(&self) -> Version {
        match self {
            Self::V2(_) => Version::V2,
            Self::V3(_) => Version::V3,
        }
    }
}

pub enum Selection {
    V2(v2::Selection),
    V3(v3::Selection),
}
pub enum Event {
    V2(v2::Event),
    V3(v3::Event),
}
pub enum Protocol {
    V2(v2::Protocol),
    V3(v3::Protocol),
}
impl Protocol {
    pub fn new(
        binding: Binding,
        evidence: Option<v3::ExpectedEvidence>,
    ) -> Result<Self, &'static str> {
        match (binding, evidence) {
            (Binding::V2(binding), None) => Ok(Self::V2(v2::Protocol::new(binding))),
            (Binding::V3(binding), Some(evidence)) => {
                v3::Protocol::new(binding, evidence).map(Self::V3)
            }
            _ => Err("DESKTOP_VERSION_SELECTION"),
        }
    }
    pub fn version(&self) -> Version {
        match self {
            Self::V2(_) => Version::V2,
            Self::V3(_) => Version::V3,
        }
    }
    pub fn accept(&mut self, raw: &[u8]) -> Result<Event, &'static str> {
        match self {
            Self::V2(protocol) => protocol.accept(raw).map(Event::V2),
            Self::V3(protocol) => protocol.accept(raw).map(Event::V3),
        }
    }
    pub fn command(
        &mut self,
        action: v3::Action,
        selection: Option<&Selection>,
    ) -> Result<Vec<u8>, &'static str> {
        match self {
            Self::V2(protocol) => {
                let selection = match selection {
                    None => None,
                    Some(Selection::V2(value)) => Some(value),
                    _ => return Err("DESKTOP_VERSION_SELECTION"),
                };
                let action = match action {
                    v3::Action::Inspect => v2::Action::Inspect,
                    v3::Action::Start => v2::Action::Start,
                    v3::Action::Heartbeat => v2::Action::Heartbeat,
                    v3::Action::Stop => v2::Action::Stop,
                };
                protocol.command(action, selection)
            }
            Self::V3(protocol) => {
                let selection = match selection {
                    None => None,
                    Some(Selection::V3(value)) => Some(value),
                    _ => return Err("DESKTOP_VERSION_SELECTION"),
                };
                protocol.command(action, selection)
            }
        }
    }
}

/// A pending journal's directory selects its existing decoder. Each decoder
/// still validates its own schema, revision, captured pins, and sources before
/// SSH execution. Inaccessible/malformed journals remain a barrier.
pub fn captured_version(files: &std::path::Path) -> Result<Option<Version>, &'static str> {
    let old = v2::pending(files)?.is_some();
    let new = v3::journal::pending(files)?.is_some();
    match (old, new) {
        (true, true) => Err("RECOVERY_REQUIRED"),
        (true, false) => Ok(Some(Version::V2)),
        (false, true) => Ok(Some(Version::V3)),
        (false, false) => Ok(None),
    }
}

pub fn validate_recovery_selection(
    files: &std::path::Path,
    selected: Version,
) -> Result<(), &'static str> {
    if captured_version(files)?.is_some_and(|captured| captured != selected) {
        return Err("RECOVERY_REQUIRED");
    }
    Ok(())
}

pub enum RecoveryRequest<'a> {
    V2(v2::Recovery<'a>),
    V3(v3::recovery::Recovery<'a>),
}
pub enum RecoveryReport {
    V2(v2::RecoveryReport),
    V3(v3::recovery::RecoveryReport),
}

/// Explicit capture selects the implementation once. An error in either lane
/// returns in that lane, never retried or converted into another lane's clean.
pub async fn recover<H: russh::client::Handler>(
    client: &russh::client::Handle<H>,
    router: &std::sync::Arc<crate::forward::Router>,
    request: RecoveryRequest<'_>,
    cancel: &tokio_util::sync::CancellationToken,
) -> Result<RecoveryReport, &'static str> {
    match request {
        RecoveryRequest::V2(request) => {
            validate_recovery_selection(request.files, Version::V2)?;
            Ok(RecoveryReport::V2(
                v2::recover(client, router, request, cancel).await,
            ))
        }
        RecoveryRequest::V3(request) => {
            validate_recovery_selection(request.files, Version::V3)?;
            Ok(RecoveryReport::V3(
                v3::recovery::recover(client, router, request, cancel).await,
            ))
        }
    }
}
