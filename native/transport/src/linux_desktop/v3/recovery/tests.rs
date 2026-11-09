use super::super::owner::test_support::*;
use super::*;
use serde_json::{Value, json};
fn parse(value: &Value, boot: &str, status: u32) -> Result<Receipt, &'static str> {
    let mut bytes = value.to_string().into_bytes();
    bytes.push(b'\n');
    Receipt::parse(&bytes, &binding(), &sources::current_hashes(), boot, status)
}
#[test]
fn receipt_uses_captured_version_source_and_origin_without_current_package_input() {
    let good = receipt();
    assert!(parse(&good, BOOT, 0).unwrap().complete());
    for (field, value) in [
        ("protocol_revision", json!("eidolon-linux-desktop-v2")),
        ("build_id", json!("a".repeat(64))),
        ("context", json!({})),
        ("pins", json!({})),
        ("source_hashes", json!({})),
        ("expected_boot_id", json!(NEW_BOOT)),
    ] {
        let mut changed = good.clone();
        changed[field] = value;
        assert!(parse(&changed, BOOT, 0).is_err(), "{field}");
    }
    let mut missing_ack = good;
    missing_ack["expected_boot_id"] = json!("-");
    assert_eq!(
        parse(&missing_ack, "-", 0).unwrap().origin_boot_id(),
        Some(BOOT)
    );
    assert!(parse(&missing_ack, BOOT, 0).is_err());
}
#[test]
fn same_boot_unknown_is_pending_and_boot_change_requires_owned_origin() {
    let mut unknown = receipt();
    unknown["status"] = json!("pending");
    unknown["process_evidence"] = json!("unknown");
    for field in [
        "process_complete",
        "pipes_eof",
        "remote_cleaned",
        "receipt_written",
    ] {
        unknown[field] = json!(false);
    }
    assert!(!parse(&unknown, BOOT, 1).unwrap().complete());
    assert!(parse(&unknown, BOOT, 0).is_err());
    let mut reboot = receipt();
    reboot["process_evidence"] = json!("boot_changed");
    reboot["pipes_eof"] = json!(false);
    assert!(parse(&reboot, BOOT, 0).is_err());
    reboot["boot_id"] = json!(NEW_BOOT);
    assert!(parse(&reboot, BOOT, 0).unwrap().complete());
    reboot["origin_boot_id"] = Value::Null;
    assert!(parse(&reboot, BOOT, 0).is_err());
}
#[test]
fn wrong_exit_status_duplicate_and_multiple_receipts_are_rejected() {
    assert!(parse(&receipt(), BOOT, 1).is_err());
    let line = receipt().to_string();
    let duplicate = line.replacen('{', "{\"schema\":1,", 1) + "\n";
    assert!(
        Receipt::parse(
            duplicate.as_bytes(),
            &binding(),
            &sources::current_hashes(),
            BOOT,
            0
        )
        .is_err()
    );
    assert!(
        Receipt::parse(
            format!("{line}\n{line}\n").as_bytes(),
            &binding(),
            &sources::current_hashes(),
            BOOT,
            0
        )
        .is_err()
    );
}
