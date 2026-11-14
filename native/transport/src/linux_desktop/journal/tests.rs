use super::*;
use crate::linux_desktop::test_support::*;
use eidolon_model::connection_profile::Profile;
use std::os::unix::fs::{PermissionsExt, symlink};

#[test]
fn reservation_is_durable_exclusive_and_separate_from_probe_and_termux() {
    let dir = Temp::new();
    let binding = binding();
    let first = Journal::reserve(&dir.0, &binding).unwrap();
    assert!(matches!(
        Journal::reserve(&dir.0, &binding),
        Err("DESKTOP_JOURNAL_BUSY")
    ));
    let bytes = std::fs::read(&first.path).unwrap();
    assert_eq!(
        std::fs::metadata(&first.path).unwrap().mode() & 0o777,
        0o600
    );
    assert_eq!(first.path.parent().unwrap().file_name().unwrap(), DIRECTORY);
    drop(first);
    assert!(matches!(
        Journal::reserve(&dir.0, &binding),
        Err("DESKTOP_CLEANUP_PENDING")
    ));
    assert_eq!(
        std::fs::read(dir.0.join(DIRECTORY).join("pending.json")).unwrap(),
        bytes
    );
    assert!(!dir.0.join("linux-probe-state").exists());
    assert!(!dir.0.join("linux-gtk-probe-state").exists());
}

#[test]
fn pending_matches_only_the_original_live_binding() {
    let root = Temp::new();
    let original = binding();
    drop(Journal::reserve(&root.0, &original).unwrap());
    let pending = pending(&root.0).unwrap().unwrap();
    assert!(pending.matches_binding(&original));
    for field in 0..6 {
        let mut changed = original.clone();
        match field {
            0 => changed.session = SessionId::new(2).unwrap(),
            1 => changed.nonce = "8899aabbccddeeff".into(),
            2 => changed.uid += 1,
            3 => changed.profile_revision = "3".into(),
            4 => changed.profile_binding = "f".repeat(64),
            _ => changed.pins.desktop_binary_sha256 = "f".repeat(64),
        }
        assert!(!pending.matches_binding(&changed));
    }
}

#[test]
fn prepare_is_durable_before_forward_or_start_and_cannot_be_replayed() {
    let dir = Temp::new();
    let binding = binding();
    let profile = profile();
    let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
    assert!(journal.advance(Stage::StartRequested).is_err());
    assert!(
        journal
            .prepared("00000000-0000-0000-0000-000000000000")
            .is_err()
    );
    journal.prepared(BOOT).unwrap();
    assert!(journal.prepared(NEW_BOOT).is_err());
    journal.advance(Stage::ForwardRequested).unwrap();
    journal.advance(Stage::StartRequested).unwrap();
    assert!(journal.advance(Stage::StartRequested).is_err());
    drop(journal);
    let (loaded, origin) = Journal::matching(&recovery(&dir.0, &profile, &binding.pins))
        .unwrap()
        .unwrap();
    assert_eq!(loaded.boot_id(), Some(BOOT));
    assert_eq!(origin.session(), binding.session());
    assert!(matches!(loaded.record.stage, Stage::StartRequested));
}

#[test]
fn restart_restores_reserved_identity_and_recovered_boot_before_complete() {
    let dir = Temp::new();
    let binding = binding();
    let profile = profile();
    drop(Journal::reserve(&dir.0, &binding).unwrap());
    let (mut journal, origin) = Journal::matching(&recovery(&dir.0, &profile, &binding.pins))
        .unwrap()
        .unwrap();
    assert!(journal.boot_id().is_none());
    assert_eq!(origin.nonce, binding.nonce);
    journal.recovered_boot(BOOT).unwrap();
    journal.finish(complete()).unwrap();
    drop(journal); // crash after complete publication, before unlink
    let (mut again, _) = Journal::matching(&recovery(&dir.0, &profile, &binding.pins))
        .unwrap()
        .unwrap();
    assert_eq!(again.boot_id(), Some(BOOT));
    again.clear().unwrap();
    drop(again);
    assert!(
        Journal::matching(&recovery(&dir.0, &profile, &binding.pins))
            .unwrap()
            .is_none()
    );
}

#[test]
fn every_resource_proof_is_required_before_clear() {
    for index in 0..7 {
        let dir = Temp::new();
        let binding = binding();
        let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
        journal.prepared(BOOT).unwrap();
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
        assert!(journal.path.exists());
    }
}

#[test]
fn verified_reboot_retires_old_pipes_without_claiming_observed_eof() {
    let dir = Temp::new();
    let binding = binding();
    let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
    journal.prepared(BOOT).unwrap();
    let mut proof = complete();
    proof.pipes_eof = false;
    proof.boot_changed = true;
    journal.finish(proof).unwrap();
    journal.clear().unwrap();
}

#[test]
fn profile_uid_pins_source_revision_and_boot_mismatch_keep_original_record() {
    let dir = Temp::new();
    let binding = binding();
    let profile = profile();
    let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
    journal.prepared(BOOT).unwrap();
    assert_eq!(
        journal.recovered_boot(NEW_BOOT),
        Err("DESKTOP_CLEANUP_SCOPE_MISMATCH")
    );
    let path = journal.path.clone();
    drop(journal);
    let original = std::fs::read(&path).unwrap();
    let mut changed = serde_json::to_value(&profile).unwrap();
    changed["revision"] = "7".into();
    let changed = Profile::parse(&changed.to_string(), &"1".repeat(32)).unwrap();
    assert!(Journal::matching(&recovery(&dir.0, &changed, &binding.pins)).is_err());
    let mut request = recovery(&dir.0, &profile, &binding.pins);
    request.uid = 1001;
    assert!(Journal::matching(&request).is_err());
    let mut changed_pins = binding.pins.clone();
    changed_pins.desktop_bundle_sha256 = "6".repeat(64);
    assert!(Journal::matching(&recovery(&dir.0, &profile, &changed_pins)).is_err());
    assert_eq!(std::fs::read(&path).unwrap(), original);
    for key in ["sources", "protocol_revision"] {
        let mut record: serde_json::Value = serde_json::from_slice(&original).unwrap();
        if key == "sources" {
            record[key][4] = "0".repeat(64).into();
        } else {
            record[key] = "eidolon-linux-desktop-v1".into();
        }
        std::fs::write(&path, record.to_string()).unwrap();
        assert!(Journal::matching(&recovery(&dir.0, &profile, &binding.pins)).is_err());
    }
}

#[test]
fn malformed_symlink_hardlink_and_nonprivate_records_fail_closed() {
    for kind in 0..5 {
        let dir = Temp::new();
        let binding = binding();
        let profile = profile();
        let journal = Journal::reserve(&dir.0, &binding).unwrap();
        let path = journal.path.clone();
        drop(journal);
        match kind {
            0 => {
                std::fs::write(&path, b"{}").unwrap();
            }
            1 => {
                std::fs::write(&path, vec![b'x'; LIMIT + 1]).unwrap();
            }
            2 => {
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
            }
            3 => {
                std::fs::hard_link(&path, dir.0.join("other-link")).unwrap();
            }
            _ => {
                std::fs::remove_file(&path).unwrap();
                symlink(dir.0.join("absent"), &path).unwrap();
            }
        }
        assert!(Journal::matching(&recovery(&dir.0, &profile, &binding.pins)).is_err());
        assert!(std::fs::symlink_metadata(&path).is_ok());
    }
}

#[test]
fn changed_owner_cannot_be_updated_or_cleared() {
    let dir = Temp::new();
    let binding = binding();
    let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
    journal.prepared(BOOT).unwrap();
    journal.finish(complete()).unwrap();
    let replacement = temporary(&journal.directory, &journal.bytes).unwrap();
    std::fs::rename(replacement, &journal.path).unwrap();
    assert_eq!(journal.clear(), Err("DESKTOP_JOURNAL_OWNER_CHANGED"));
    assert!(journal.path.exists());
}
