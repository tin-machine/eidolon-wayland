use super::*;
use crate::linux_desktop::test_support::*;

fn wire(value: &serde_json::Value) -> Vec<u8> {
    format!("{value}\n").into_bytes()
}

#[test]
fn receipt_and_verified_boot_change_are_distinct_proofs() {
    let binding = binding();
    let original = receipt(&binding);
    let row = Receipt::parse(&wire(&original), &binding, BOOT, 0).unwrap();
    assert!(row.pipes_eof);
    assert_eq!(row.process_evidence, "receipt");
    let mut changed = original;
    changed["process_evidence"] = "boot_changed".into();
    changed["boot_id"] = NEW_BOOT.into();
    changed["pipes_eof"] = false.into();
    let row = Receipt::parse(&wire(&changed), &binding, BOOT, 0).unwrap();
    assert!(!row.pipes_eof);
    assert!(row.process_complete);
    changed["boot_id"] = BOOT.into();
    assert!(Receipt::parse(&wire(&changed), &binding, BOOT, 0).is_err());
}

#[test]
fn unknown_or_missing_boot_never_means_clean_and_prepared_loss_can_use_private_origin() {
    let binding = binding();
    let mut value = receipt(&binding);
    value["expected_boot_id"] = "-".into();
    assert!(Receipt::parse(&wire(&value), &binding, "-", 0).is_ok());
    value["origin_boot_id"] = serde_json::Value::Null;
    assert!(Receipt::parse(&wire(&value), &binding, "-", 0).is_err());
    value["status"] = "pending".into();
    value["process_evidence"] = "unknown".into();
    for key in [
        "process_complete",
        "pipes_eof",
        "remote_cleaned",
        "receipt_written",
    ] {
        value[key] = false.into();
    }
    let row = Receipt::parse(&wire(&value), &binding, "-", 1).unwrap();
    assert!(row.origin_boot_id.is_none());
    assert_eq!(row.status, "pending");
    assert!(Receipt::parse(&wire(&value), &binding, "-", 0).is_err());
}

#[test]
fn partial_reboot_cleanup_keeps_pending_until_receipt_and_files_are_retired() {
    let binding = binding();
    let mut value = receipt(&binding);
    value["status"] = "pending".into();
    value["process_evidence"] = "boot_changed".into();
    value["boot_id"] = NEW_BOOT.into();
    value["pipes_eof"] = false.into();
    value["remote_cleaned"] = false.into();
    value["receipt_written"] = false.into();
    assert!(Receipt::parse(&wire(&value), &binding, BOOT, 1).is_ok());
    value["status"] = "complete".into();
    assert!(Receipt::parse(&wire(&value), &binding, BOOT, 0).is_err());
}

#[test]
fn recovery_rejects_other_scope_boot_source_shape_status_and_truncated_output() {
    let binding = binding();
    let original = receipt(&binding);
    for (key, replacement) in [
        ("session", serde_json::json!("2")),
        ("profile_revision", serde_json::json!("7")),
        ("scope", serde_json::json!("0".repeat(64))),
        ("nonce", serde_json::json!("0".repeat(16))),
        ("uid", serde_json::json!(1001)),
        ("backend", serde_json::json!("linux_posix")),
        ("revision", serde_json::json!("eidolon-linux-desktop-v1")),
        ("expected_boot_id", serde_json::json!(NEW_BOOT)),
        ("origin_boot_id", serde_json::json!(NEW_BOOT)),
        (
            "boot_id",
            serde_json::json!("00000000-0000-0000-0000-000000000000"),
        ),
        ("process_complete", serde_json::json!(false)),
        ("receipt_written", serde_json::json!(false)),
        ("remote_cleaned", serde_json::json!(false)),
        ("unknown_field", serde_json::json!(true)),
    ] {
        let mut value = original.clone();
        value[key] = replacement;
        assert!(
            Receipt::parse(&wire(&value), &binding, BOOT, 0).is_err(),
            "{key}"
        );
    }
    assert!(Receipt::parse(&wire(&original), &binding, BOOT, 1).is_err());
    assert!(Receipt::parse(original.to_string().as_bytes(), &binding, BOOT, 0).is_err());
    let repeated = [wire(&original), wire(&original)].concat();
    assert!(Receipt::parse(&repeated, &binding, BOOT, 0).is_err());
    let duplicate = original.to_string().replacen('{', "{\"schema\":1,", 1) + "\n";
    assert!(Receipt::parse(duplicate.as_bytes(), &binding, BOOT, 0).is_err());
}

async fn recovery_fixture(
    value: serde_json::Value,
    status: u8,
) -> crate::forward::openssh::OpenSsh {
    crate::forward::openssh::OpenSsh::with_forced_command(move |directory| {
        let script = directory.join("recovery.py");
        let text = serde_json::to_string(&(value.to_string() + "\n")).unwrap();
        std::fs::write(&script, format!("import sys\nsys.stdout.write({text})\nsys.stdout.flush()\nraise SystemExit({status})\n")).unwrap();
        Some(format!("/usr/bin/python3 -I -S -B {}", script.display()))
    }).await
}

#[tokio::test]
async fn ssh_recovery_preserves_unknown_and_scope_mismatch_then_accepts_same_owner_receipt() {
    for outcome in ["receipt", "unknown", "other_scope", "boot_changed"] {
        let dir = Temp::new();
        let binding = binding();
        let profile = profile();
        let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
        journal.prepared(BOOT).unwrap();
        drop(journal);
        let mut value = receipt(&binding);
        let status = match outcome {
            "unknown" => {
                value["status"] = "pending".into();
                value["process_evidence"] = "unknown".into();
                for key in [
                    "process_complete",
                    "pipes_eof",
                    "remote_cleaned",
                    "receipt_written",
                ] {
                    value[key] = false.into();
                }
                1
            }
            "other_scope" => {
                value["scope"] = "0".repeat(64).into();
                0
            }
            "boot_changed" => {
                value["boot_id"] = NEW_BOOT.into();
                value["process_evidence"] = "boot_changed".into();
                value["pipes_eof"] = false.into();
                0
            }
            _ => 0,
        };
        let ssh = recovery_fixture(value, status).await;
        let mut request = recovery(&dir.0, &profile, &binding.pins);
        request.session = SessionId::new(1).unwrap();
        let result = recover(&ssh.client, &ssh.router, request, &CancellationToken::new()).await;
        let pending = matches!(outcome, "unknown" | "other_scope");
        assert_eq!(result.pending, pending, "{outcome}: {:?}", result.error);
        assert_eq!(
            dir.0.join("linux-desktop-state/pending.json").exists(),
            pending
        );
        assert_eq!(result.boot_changed, outcome == "boot_changed");
        ssh.close().await;
    }
}

#[tokio::test]
async fn ssh_recovery_requires_prior_transport_retirement_and_handles_lost_prepared() {
    let dir = Temp::new();
    let binding = binding();
    let profile = profile();
    drop(Journal::reserve(&dir.0, &binding).unwrap());
    let mut value = receipt(&binding);
    value["expected_boot_id"] = "-".into();
    let ssh = recovery_fixture(value, 0).await;
    let mut request = recovery(&dir.0, &profile, &binding.pins);
    request.session = SessionId::new(1).unwrap();
    request.previous_connection_closed = false;
    let denied = recover(&ssh.client, &ssh.router, request, &CancellationToken::new()).await;
    assert_eq!(denied.error, Some("DESKTOP_PREVIOUS_CONNECTION_PENDING"));
    assert!(!denied.queried);
    let mut request = recovery(&dir.0, &profile, &binding.pins);
    request.session = SessionId::new(1).unwrap();
    let recovered = recover(&ssh.client, &ssh.router, request, &CancellationToken::new()).await;
    assert!(!recovered.pending, "{:?}", recovered.error);
    assert!(recovered.boot_identity_known);
    assert_eq!(recovered.origin_session, Some(binding.session()));
    ssh.close().await;
}
