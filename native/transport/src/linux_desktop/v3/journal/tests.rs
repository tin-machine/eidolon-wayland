use super::super::owner::test_support::*;
use super::*;
use std::os::unix::fs::{PermissionsExt, symlink};

#[test]
fn integrated_sources_can_reserve_but_not_claim_cleanup_or_a_second_owner() {
    let dir = Temp::new();
    let mut journal = Journal::reserve(&dir.0, &binding()).unwrap();
    assert!(dir.0.join(DIRECTORY).exists());
    assert!(pending(&dir.0).unwrap().is_some());
    assert_eq!(journal.clear(), Err("DESKTOP_CLEANUP_PENDING"));
    assert!(matches!(
        Journal::reserve(&dir.0, &binding()),
        Err("DESKTOP_V3_JOURNAL_BUSY")
    ));
}
#[test]
fn strict_versioned_record_rejects_duplicate_shape_and_source_replacement() {
    let dir = Temp::new();
    let journal = Journal::reserve_record(&dir.0, &binding()).unwrap();
    let good = journal.bytes.clone();
    assert!(Record::parse(&good).is_ok());
    let raw = String::from_utf8(good.clone()).unwrap();
    assert!(Record::parse(raw.replacen('{', "{\"schema\":2,", 1).as_bytes()).is_err());
    for (key, value) in [
        ("schema", serde_json::json!(1)),
        (
            "protocol_revision",
            serde_json::json!("eidolon-linux-desktop-v2"),
        ),
        ("stage", serde_json::json!({"reserved":null})),
        (
            "cleanup",
            serde_json::json!([false, false, false, false, false, false, false, false]),
        ),
        ("build_id", serde_json::json!("e".repeat(64))),
    ] {
        let mut modified: serde_json::Value = serde_json::from_slice(&good).unwrap();
        modified[key] = value;
        assert!(
            Record::parse(modified.to_string().as_bytes()).is_err(),
            "{key}"
        );
    }
    for key in ["context", "pins", "sources"] {
        let mut modified: serde_json::Value = serde_json::from_slice(&good).unwrap();
        modified[key]["unknown"] = serde_json::json!("0".repeat(64));
        assert!(
            Record::parse(modified.to_string().as_bytes()).is_err(),
            "{key}"
        );
    }
    let mut value: serde_json::Value = serde_json::from_slice(&good).unwrap();
    value.as_object_mut().unwrap().remove("boot_id");
    assert!(Record::parse(value.to_string().as_bytes()).is_err());
}
#[test]
fn exclusive_reservation_state_transitions_and_every_cleanup_proof() {
    let dir = Temp::new();
    let binding = binding();
    let mut journal = Journal::reserve_record(&dir.0, &binding).unwrap();
    assert!(matches!(
        Journal::reserve_record(&dir.0, &binding),
        Err("DESKTOP_V3_JOURNAL_BUSY")
    ));
    assert!(journal.advance(Stage::StartRequested).is_err());
    journal.prepared(BOOT).unwrap();
    journal.advance(Stage::ForwardRequested).unwrap();
    journal.advance(Stage::StartRequested).unwrap();
    let path = journal.path.clone();
    drop(journal);
    assert!(matches!(
        Journal::reserve_record(&dir.0, &binding),
        Err("DESKTOP_CLEANUP_PENDING")
    ));
    let profile = profile();
    let storage = storage();
    let (mut journal, restored) =
        Journal::matching(&request(&dir.0, &profile, binding.pins(), &storage))
            .unwrap()
            .unwrap();
    assert_eq!(
        restored.context().canonical(),
        binding.context().canonical()
    );
    assert_eq!(journal.boot_id(), Some(BOOT));
    for index in 0..7 {
        let mut proof = complete();
        match index {
            0 => proof.process_complete = false,
            1 => proof.pipes_eof = false,
            2 => proof.remote_cleaned = false,
            3 => proof.receipt_written = false,
            4 => proof.channel_complete = false,
            5 => proof.forward_retired = false,
            _ => proof.router_drained = false,
        }
        journal.finish(proof).unwrap();
        assert_eq!(journal.clear(), Err("DESKTOP_CLEANUP_PENDING"));
        assert!(path.exists());
    }
    journal.finish(complete()).unwrap();
    journal.clear().unwrap();
    assert!(!path.exists());
}
#[test]
fn saved_profile_ids_pins_and_boot_cannot_be_replaced_during_recovery() {
    let dir = Temp::new();
    let binding = binding();
    let profile = profile();
    let storage = storage();
    let mut journal = Journal::reserve_record(&dir.0, &binding).unwrap();
    journal.prepared(BOOT).unwrap();
    let original = journal.bytes.clone();
    let path = journal.path.clone();
    drop(journal);
    let mut changed = storage.record();
    changed["profile_storage_id"] = serde_json::json!("d".repeat(32));
    let changed =
        eidolon_model::linux_desktop_v3::StorageRecord::parse(changed.to_string().as_bytes())
            .unwrap();
    assert!(Journal::matching(&request(&dir.0, &profile, binding.pins(), &changed)).is_err());
    let mut pins = binding.pins().record();
    pins["desktop_bundle_sha256"] = serde_json::json!("e".repeat(64));
    let pins = Pins::parse(pins.to_string().as_bytes()).unwrap();
    assert!(Journal::matching(&request(&dir.0, &profile, &pins, &storage)).is_err());
    let mut req = request(&dir.0, &profile, binding.pins(), &storage);
    req.uid = 1001;
    assert!(Journal::matching(&req).is_err());
    assert_eq!(std::fs::read(path).unwrap(), original);
    let (mut journal, _) = Journal::matching(&request(&dir.0, &profile, binding.pins(), &storage))
        .unwrap()
        .unwrap();
    assert_eq!(
        journal.recovered_boot(NEW_BOOT),
        Err("DESKTOP_V3_CLEANUP_SCOPE_MISMATCH")
    );
}
#[test]
fn unknown_boot_and_nonmatching_file_identity_keep_pending() {
    let dir = Temp::new();
    let mut journal = Journal::reserve_record(&dir.0, &binding()).unwrap();
    journal.finish(Cleanup::default()).unwrap();
    assert!(journal.clear().is_err());
    journal.recovered_boot(BOOT).unwrap();
    let mut proof = complete();
    proof.pipes_eof = false;
    proof.boot_changed = true;
    journal.finish(proof).unwrap();
    let copy = journal.path.with_extension("copy");
    std::fs::write(&copy, &journal.bytes).unwrap();
    std::fs::set_permissions(&copy, std::fs::Permissions::from_mode(0o600)).unwrap();
    std::fs::rename(copy, &journal.path).unwrap();
    assert_eq!(journal.clear(), Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED"));
}
#[test]
fn v2_pending_is_a_barrier_and_is_never_rewritten_as_v3_clean() {
    let dir = Temp::new();
    let legacy = crate::linux_desktop::test_support::binding();
    let journal = crate::linux_desktop::journal::Journal::reserve(&dir.0, &legacy).unwrap();
    drop(journal);
    let path = dir.0.join("linux-desktop-state/pending.json");
    let original = std::fs::read(&path).unwrap();
    assert_eq!(check_v2_barrier(&dir.0), Err("RECOVERY_REQUIRED"));
    assert!(Journal::reserve(&dir.0, &binding()).is_err());
    assert!(Journal::reserve_record(&dir.0, &binding()).is_err());
    assert_eq!(std::fs::read(path).unwrap(), original);
    assert!(!dir.0.join(DIRECTORY).exists());
}
#[test]
fn links_and_malformed_pending_are_errors_not_empty_state() {
    let dir = Temp::new();
    let outside = Temp::new();
    symlink(&outside.0, dir.0.join(DIRECTORY)).unwrap();
    assert!(pending(&dir.0).is_err());
    std::fs::remove_file(dir.0.join(DIRECTORY)).unwrap();
    let journal = Journal::reserve_record(&dir.0, &binding()).unwrap();
    let path = journal.path.clone();
    drop(journal);
    std::fs::write(&path, b"{}").unwrap();
    assert!(pending(&dir.0).is_err());
}

#[test]
fn fifo_pending_and_lock_are_rejected_without_blocking() {
    let dir = Temp::new();
    let journal = Journal::reserve_record(&dir.0, &binding()).unwrap();
    let path = journal.path.clone();
    drop(journal);
    std::fs::remove_file(&path).unwrap();
    let path_c = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(path_c.as_ptr(), 0o600) }, 0);
    assert!(pending(&dir.0).is_err());
    std::fs::remove_file(&path).unwrap();
    let lock_path = dir.0.join(DIRECTORY).join("owner.lock");
    std::fs::remove_file(&lock_path).unwrap();
    let lock_c = std::ffi::CString::new(lock_path.as_os_str().as_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(lock_c.as_ptr(), 0o600) }, 0);
    assert!(Journal::reserve_record(&dir.0, &binding()).is_err());
}

#[test]
fn recovery_dispatch_selects_captured_journal_and_rejects_cross_version() {
    use crate::linux_desktop::dispatch::{Version, captured_version, validate_recovery_selection};
    let dir = Temp::new();
    assert_eq!(captured_version(&dir.0).unwrap(), None);
    let journal = Journal::reserve_record(&dir.0, &binding()).unwrap();
    drop(journal);
    assert_eq!(captured_version(&dir.0).unwrap(), Some(Version::V3));
    assert_eq!(
        validate_recovery_selection(&dir.0, Version::V2),
        Err("RECOVERY_REQUIRED")
    );
    assert!(validate_recovery_selection(&dir.0, Version::V3).is_ok());
    let legacy = crate::linux_desktop::test_support::binding();
    let old = crate::linux_desktop::journal::Journal::reserve(&dir.0, &legacy).unwrap();
    drop(old);
    assert_eq!(captured_version(&dir.0), Err("RECOVERY_REQUIRED"));
    std::fs::remove_file(dir.0.join(DIRECTORY).join("pending.json")).unwrap();
    assert_eq!(captured_version(&dir.0).unwrap(), Some(Version::V2));
    assert_eq!(
        validate_recovery_selection(&dir.0, Version::V3),
        Err("RECOVERY_REQUIRED")
    );
    assert!(validate_recovery_selection(&dir.0, Version::V2).is_ok());
}

#[test]
fn complete_is_durable_but_not_local_retirement_and_clear_preserves_new_owner() {
    use std::os::unix::fs::OpenOptionsExt;
    let dir = Temp::new();
    let mut journal = Journal::reserve(&dir.0, &binding()).unwrap();
    assert!(journal.ensure_complete_durable().is_err());
    journal.finish(complete()).unwrap();
    assert_eq!(journal.stage(), Stage::Complete);
    assert!(journal.ensure_complete_durable().is_ok());
    assert!(pending(&dir.0).unwrap().is_some(), "remote Complete is still pending local retirement");
    journal.clear().unwrap();
    let new_pending = dir.0.join(DIRECTORY).join("pending.json");
    std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600)
        .open(&new_pending).unwrap();
    assert_eq!(journal.clear(), Err("DESKTOP_V3_JOURNAL_OWNER_CHANGED"));
    assert!(new_pending.exists(), "retry must never unlink another generation");
}
