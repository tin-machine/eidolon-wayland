use super::super::dispatch;
use super::*;

const B1: &[u8] =
    include_bytes!("../../../../../scripts/tests/fixtures/linux-desktop-v3-contract.json");
const B2: &[u8] =
    include_bytes!("../../../../../scripts/tests/fixtures/linux-desktop-v3-wire.json");
fn fixture() -> &'static Value {
    static VALUE: std::sync::OnceLock<Value> = std::sync::OnceLock::new();
    VALUE.get_or_init(|| serde_json::from_slice(B2).unwrap())
}
fn b1_record(id: &str) -> Vec<u8> {
    static VALUE: std::sync::OnceLock<Value> = std::sync::OnceLock::new();
    let f = VALUE.get_or_init(|| serde_json::from_slice(B1).unwrap());
    f["records"]
        .as_array()
        .unwrap()
        .iter()
        .find(|record| record["id"] == id)
        .unwrap()["raw"]
        .as_str()
        .unwrap()
        .as_bytes()
        .to_vec()
}
fn inputs() -> (Binding, ExpectedEvidence) {
    static VALUE: std::sync::OnceLock<(Binding, ExpectedEvidence)> = std::sync::OnceLock::new();
    VALUE
        .get_or_init(|| {
            let binding = Binding::from_captured(
                Context::parse(&b1_record("context")).unwrap(),
                Pins::parse(&b1_record("pins")).unwrap(),
            )
            .unwrap();
            let evidence =
                ExpectedEvidence::parse(&canonical(&fixture()["evidence"]).unwrap()).unwrap();
            (binding, evidence)
        })
        .clone()
}
fn parse(case: &Value) -> Result<Value> {
    let (binding, evidence) = inputs();
    let raw = case["raw"].as_str().unwrap().as_bytes();
    match case["kind"].as_str().unwrap() {
        "event" => {
            protocol::parse_event(raw, &binding, &evidence)?;
        }
        "command" => {
            protocol::parse_command(raw, &binding)?;
        }
        "receipt" => {
            let sources = source_map(&fixture()["source_hashes"])?;
            Receipt::parse(
                raw,
                &binding,
                &sources,
                case["expected_boot"].as_str().unwrap(),
                case["exit_status"].as_u64().unwrap() as u32,
            )?;
        }
        _ => panic!("unknown fixture kind"),
    }
    contract::parse_json(raw, contract::MAX_RECORD_BYTES).map_err(|e| e.0)
}
#[test]
fn same_frozen_b1_and_b2_bytes_in_both_languages() {
    let f = fixture();
    assert_eq!(
        super::super::hash(B1),
        f["b1_fixture_sha256"].as_str().unwrap()
    );
    assert_eq!(
        f["purpose"],
        "synthetic-wire-contract-only; never production pins or acceptance"
    );
    assert_eq!(f["records"].as_array().unwrap().len(), 16);
    for case in f["records"].as_array().unwrap() {
        let value = parse(case).unwrap_or_else(|e| panic!("{}: {e}", case["id"]));
        let bytes = canonical(&value).unwrap();
        assert_eq!(
            bytes,
            case["canonical_utf8"].as_str().unwrap().as_bytes(),
            "{}",
            case["id"]
        );
        assert_eq!(
            super::super::hash(&bytes),
            case["sha256"].as_str().unwrap(),
            "{}",
            case["id"]
        );
    }
}
#[test]
fn same_frozen_malformed_identity_version_source_package_and_receipt_rejections() {
    let f = fixture();
    assert_eq!(f["invalid"].as_array().unwrap().len(), 383);
    let mut ids = std::collections::BTreeSet::new();
    for case in f["invalid"].as_array().unwrap() {
        assert!(ids.insert(case["id"].as_str().unwrap()));
        assert!(parse(case).is_err(), "accepted {}", case["id"]);
    }
}
#[test]
fn same_frozen_request_ack_and_terminal_races() {
    let f = fixture();
    assert_eq!(f["traces"].as_array().unwrap().len(), 19);
    for trace in f["traces"].as_array().unwrap() {
        let (binding, evidence) = inputs();
        let mut protocol = Protocol::new(binding, evidence).unwrap();
        for (index, step) in trace["steps"].as_array().unwrap().iter().enumerate() {
            let result = if let Some(raw) = step["event_raw"].as_str() {
                protocol.accept(raw.as_bytes()).map(|_| None)
            } else {
                let action = match step["command"].as_str().unwrap() {
                    "inspect" => Action::Inspect,
                    "start" => Action::Start,
                    "heartbeat" => Action::Heartbeat,
                    "stop" => Action::Stop,
                    _ => panic!("unknown action"),
                };
                let selection = if step["selection"].is_null() {
                    None
                } else {
                    Some(Selection::parse(&canonical(&step["selection"]).unwrap()).unwrap())
                };
                protocol.command(action, selection.as_ref()).map(Some)
            };
            if step["error"] == true {
                assert!(result.is_err(), "{} step {index}", trace["id"]);
            } else {
                let result = result.unwrap_or_else(|e| panic!("{} step {index}: {e}", trace["id"]));
                if let Some(expected) = step["expected_raw"].as_str() {
                    assert_eq!(
                        result.unwrap(),
                        expected.as_bytes(),
                        "{} step {index}",
                        trace["id"]
                    );
                } else {
                    assert_eq!(
                        protocol.phase().as_str(),
                        step["phase"].as_str().unwrap(),
                        "{} step {index}",
                        trace["id"]
                    );
                }
            }
        }
    }
}
#[test]
fn fragment_chunk_queue_limits_and_poisoning_are_finite() {
    let f = fixture();
    let raw = f["records"][0]["canonical_utf8"]
        .as_str()
        .unwrap()
        .as_bytes();
    let mut decoder = FrameDecoder::default();
    for byte in raw {
        assert!(decoder.feed(&[*byte]).unwrap().is_empty());
    }
    assert_eq!(decoder.feed(b"\n").unwrap(), [raw]);
    decoder.finish().unwrap();
    assert_eq!(decoder.feed(b"{}\n{}\n").unwrap(), [b"{}", b"{}"]);
    for chunk in [
        b"\n".to_vec(),
        b"{}\r\n".to_vec(),
        vec![b'x'; 16385],
        b"{}\n".repeat(17),
        vec![b'x'; 65537],
    ] {
        let mut decoder = FrameDecoder::default();
        assert!(decoder.feed(&chunk).is_err());
        assert!(decoder.feed(b"{}\n").is_err());
    }
    let mut decoder = FrameDecoder::default();
    decoder.feed(b"{").unwrap();
    assert!(decoder.finish().is_err());
    let mut decoder = FrameDecoder::default();
    decoder.feed(&vec![b'x'; 16384]).unwrap();
    assert_eq!(decoder.feed(b"\n").unwrap()[0].len(), 16384);
}
#[test]
fn bindings_match_authentication_storage_and_never_regenerate_missing_ids() {
    let f: Value = serde_json::from_slice(B1).unwrap();
    let profile = Profile::parse(
        &f["profile"].to_string(),
        f["profile"]["installation"].as_str().unwrap(),
    )
    .unwrap();
    let (expected, _) = inputs();
    let storage = StorageRecord::parse(&b1_record("storage")).unwrap();
    let captured = Binding::capture(
        &profile,
        expected.session(),
        1000,
        &expected.nonce(),
        expected.pins().clone(),
        &storage,
    )
    .unwrap();
    assert_eq!(captured.context().record(), expected.context().record());
    assert!(captured.matches_profile(&profile, expected.session()));
    assert!(!captured.matches_profile(&profile, SessionId::new(2).unwrap()));
    let mut bad = storage.record();
    bad["host_user_binding"] = json!("f".repeat(64));
    let bad = StorageRecord::parse(&canonical(&bad).unwrap()).unwrap();
    assert!(
        Binding::capture(
            &profile,
            expected.session(),
            1000,
            &expected.nonce(),
            expected.pins().clone(),
            &bad
        )
        .is_err()
    );
}
#[test]
fn explicit_dispatch_has_no_schema_autodetect_remote_upgrade_or_success_fallback() {
    let (binding, evidence) = inputs();
    let raw_v3 = b1_record("pins");
    let mut raw_v2 = binding.pins().record();
    raw_v2["schema"] = json!(1);
    raw_v2.as_object_mut().unwrap().remove("protocol_revision");
    raw_v2
        .as_object_mut()
        .unwrap()
        .remove("app_manifest_sha256");
    let raw_v2 = canonical(&raw_v2).unwrap();
    assert!(dispatch::Pins::parse(dispatch::Version::V2, &raw_v2).is_ok());
    assert!(dispatch::Pins::parse(dispatch::Version::V3, &raw_v3).is_ok());
    assert!(dispatch::Pins::parse(dispatch::Version::V2, &raw_v3).is_err());
    assert!(dispatch::Pins::parse(dispatch::Version::V3, &raw_v2).is_err());
    assert!(dispatch::Protocol::new(dispatch::Binding::V3(binding.clone()), None).is_err());
    let v2_binding = super::super::test_support::binding();
    assert!(
        dispatch::Protocol::new(
            dispatch::Binding::V2(v2_binding.clone()),
            Some(evidence.clone())
        )
        .is_err()
    );
    let mut v2 = dispatch::Protocol::new(dispatch::Binding::V2(v2_binding), None).unwrap();
    let f = fixture();
    assert!(
        v2.accept(f["records"][0]["raw"].as_str().unwrap().as_bytes())
            .is_err()
    );
    assert_eq!(v2.version(), dispatch::Version::V2);
    let mut v3 = dispatch::Protocol::new(dispatch::Binding::V3(binding), Some(evidence)).unwrap();
    let mut event: Value = serde_json::from_str(f["records"][0]["raw"].as_str().unwrap()).unwrap();
    event["protocol_revision"] = json!(super::super::REVISION);
    assert!(v3.accept(&canonical(&event).unwrap()).is_err());
    assert_eq!(v3.version(), dispatch::Version::V3);
    assert!(
        v3.accept(f["records"][0]["raw"].as_str().unwrap().as_bytes())
            .is_err()
    );
}
#[test]
fn both_catalog_entries_are_descriptors_without_product_acceptance_claims() {
    let (binding, evidence) = inputs();
    let payload = catalog_payload(&binding, &evidence).unwrap();
    assert!(payload.get("available").is_none());
    assert!(payload.get("production_acceptance_passed").is_none());
    assert!(payload.get("quest_verified").is_none());
    let mut protocol = Protocol::new(binding, evidence).unwrap();
    let f = fixture();
    protocol
        .accept(f["records"][0]["raw"].as_str().unwrap().as_bytes())
        .unwrap();
    protocol.command(Action::Inspect, None).unwrap();
    protocol
        .accept(f["records"][1]["raw"].as_str().unwrap().as_bytes())
        .unwrap();
    assert!(protocol.selection("gtk3-desktop").is_ok());
    assert!(protocol.selection("mousepad-editor-v1").is_ok());
    assert!(protocol.selection("shell").is_err());
}
