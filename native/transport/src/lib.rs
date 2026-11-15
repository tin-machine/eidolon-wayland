//! SSH identity/pin。秘密型をSerialize/Debugへ公開しない。
#[cfg(all(feature = "host-acceptance", not(target_os = "linux")))]
compile_error!("host-acceptance is available only on Linux hosts");
#[cfg(feature = "host-acceptance")]
pub mod host_acceptance;
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
pub mod linux_desktop;
#[cfg(any(test, feature = "debug-fixtures"))]
pub mod linux_probe;
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
