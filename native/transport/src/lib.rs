//! SSH identity/pin。秘密型をSerialize/Debugへ公開しない。
pub use eidolon_model::catalog;
pub mod connect_profile;
pub mod credentials;
pub mod exec;
pub mod forward;
pub mod forward_app;
pub mod forward_probe;
pub mod host_key;
pub mod journal;
pub mod launcher;
pub mod session;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    ConfigInvalid,
    CredentialInvalid,
    HostKeyUnknown,
    HostKeyMismatch,
    Stale,
    NotReady,
}
impl Error {
    pub fn code(self) -> &'static str {
        match self {
            Self::ConfigInvalid => "CONFIG_INVALID",
            Self::CredentialInvalid => "CREDENTIAL_INVALID",
            Self::HostKeyUnknown => "HOST_KEY_UNKNOWN",
            Self::HostKeyMismatch => "HOST_KEY_MISMATCH",
            Self::Stale => "STALE",
            Self::NotReady => "NOT_READY",
        }
    }
}
