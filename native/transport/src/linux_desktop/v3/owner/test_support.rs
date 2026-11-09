use super::super::{Binding, Context, ExpectedEvidence, Manifest, Pins, sources};
use eidolon_model::{connection_profile::Profile, ids::SessionId, linux_desktop_v3::StorageRecord};
use serde_json::Value;
use std::{os::unix::fs::DirBuilderExt, path::PathBuf};
pub(crate) const BOOT: &str = "12345678-1234-4234-8234-123456789abc";
pub(crate) const NEW_BOOT: &str = "abcdef12-1234-4234-8234-123456789abc";
pub(crate) fn fixture() -> Value {
    serde_json::from_str(include_str!(
        "../../../../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
    ))
    .unwrap()
}
pub(crate) fn raw(id: &str) -> Vec<u8> {
    let fixture = fixture();
    fixture["records"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["id"] == id)
        .unwrap()["raw"]
        .as_str()
        .unwrap()
        .as_bytes()
        .to_vec()
}
pub(crate) fn binding() -> Binding {
    Binding::from_captured(
        Context::parse(&raw("context")).unwrap(),
        Pins::parse(&raw("pins")).unwrap(),
    )
    .unwrap()
}
pub(crate) fn profile() -> Profile {
    Profile::parse(&fixture()["profile"].to_string(), &"a".repeat(32)).unwrap()
}
pub(crate) fn storage() -> StorageRecord {
    StorageRecord::parse(&raw("storage")).unwrap()
}
pub(crate) fn evidence() -> ExpectedEvidence {
    ExpectedEvidence::capture(
        binding().pins(),
        &Manifest::parse(&raw("manifest")).unwrap(),
        &sources::current_hashes(),
    )
    .unwrap()
}
pub(crate) struct Temp(pub PathBuf);
impl Temp {
    pub(crate) fn new() -> Self {
        let path = std::env::temp_dir().join(format!("eld-v3-{:016x}", rand::random::<u64>()));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&path)
            .unwrap();
        Self(path)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
pub(crate) fn request<'a>(
    files: &'a std::path::Path,
    profile: &'a Profile,
    pins: &'a Pins,
    storage: &'a StorageRecord,
) -> super::super::recovery::Recovery<'a> {
    super::super::recovery::Recovery {
        profile,
        session: SessionId::new(7).unwrap(),
        uid: 1000,
        files,
        pins,
        storage,
        previous_connection_closed: true,
    }
}
pub(crate) fn complete() -> super::super::journal::Cleanup {
    super::super::journal::Cleanup {
        process_complete: true,
        pipes_eof: true,
        remote_cleaned: true,
        receipt_written: true,
        channel_complete: true,
        forward_retired: true,
        router_drained: true,
        boot_changed: false,
    }
}
pub(crate) fn receipt() -> Value {
    let binding = binding();
    serde_json::json!({"schema":1,"protocol_revision":super::super::REVISION,"context":binding.context(),"pins":binding.pins(),
        "build_id":binding.pins().build_id(),"source_hashes":sources::current_hashes(),"expected_boot_id":BOOT,
        "origin_boot_id":BOOT,"boot_id":BOOT,"status":"complete","process_evidence":"receipt","process_complete":true,
        "pipes_eof":true,"remote_cleaned":true,"receipt_written":true})
}
