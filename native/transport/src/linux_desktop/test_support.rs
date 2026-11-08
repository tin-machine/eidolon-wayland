use super::*;
use std::{os::unix::fs::DirBuilderExt, path::PathBuf};
pub(super) const BOOT: &str = "12345678-1234-4234-8234-123456789abc";
pub(super) const NEW_BOOT: &str = "abcdef12-1234-4234-8234-123456789abc";
pub(super) fn profile() -> Profile {
    let fields: Vec<_> =
        include_str!("../../../../android/app/src/test/resources/connection-profiles-v2.tsv")
            .lines()
            .filter(|line| !line.starts_with('#') && !line.is_empty())
            .nth(1)
            .unwrap()
            .split('\t')
            .collect();
    Profile::parse(
        &serde_json::json!({"schema":2,"installation":"1".repeat(32),"profile_id":fields[0],
        "revision":fields[1],"display_name":fields[2],"backend":fields[3],"host":fields[4],
        "port":fields[5].parse::<u16>().unwrap(),"username":fields[6],"host_key":fields[7]})
        .to_string(),
        &"1".repeat(32),
    )
    .unwrap()
}
pub(super) fn binding() -> Binding {
    let pins = Pins::parse(&serde_json::json!({"schema":1,"backend":"linux_desktop", "base_bundle_sha256":"1".repeat(64),
        "desktop_bundle_sha256":"2".repeat(64),"waypipe_sha256":"3".repeat(64),
        "shm_fixture_sha256":"4".repeat(64),"desktop_binary_sha256":"5".repeat(64)}).to_string()).unwrap();
    Binding::capture(
        &profile(),
        SessionId::new(1).unwrap(),
        1000,
        "aabbccddeeff0011",
        pins,
    )
    .unwrap()
}
pub(super) struct Temp(pub PathBuf);
impl Temp {
    pub fn new() -> Self {
        let path = std::env::temp_dir().join(format!("eld-{:016x}", rand::random::<u64>()));
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
pub(super) fn recovery<'a>(
    files: &'a std::path::Path,
    profile: &'a Profile,
    pins: &'a Pins,
) -> Recovery<'a> {
    Recovery {
        profile,
        session: SessionId::new(2).unwrap(),
        uid: 1000,
        files,
        pins,
        previous_connection_closed: true,
    }
}
pub(super) fn complete() -> journal::Cleanup {
    journal::Cleanup {
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
pub(super) fn receipt(binding: &Binding) -> serde_json::Value {
    serde_json::json!({"schema":1,"backend":"linux_desktop","revision":REVISION,"session":binding.session,
        "profile_revision":binding.profile_revision,"scope":binding.scope,"nonce":binding.nonce,"uid":binding.uid,
        "expected_boot_id":BOOT,"origin_boot_id":BOOT,"boot_id":BOOT,"status":"complete","process_evidence":"receipt",
        "process_complete":true,"pipes_eof":true,"remote_cleaned":true,"receipt_written":true})
}
