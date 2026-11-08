use super::*;
use serde_json::{Value, json};

fn profile(index: usize) -> Profile {
    let lines: Vec<_> =
        include_str!("../../../../android/app/src/test/resources/connection-profiles-v2.tsv")
            .lines()
            .filter(|l| !l.starts_with('#') && !l.is_empty())
            .collect();
    let f: Vec<_> = lines[index].split('\t').collect();
    Profile::parse(&json!({"schema":2,"installation":"1".repeat(32),"profile_id":f[0],"revision":f[1],
        "display_name":f[2],"backend":f[3],"host":f[4],"port":f[5].parse::<u16>().unwrap(),"username":f[6],"host_key":f[7]}).to_string(), &"1".repeat(32)).unwrap()
}
fn pins_json() -> Value {
    json!({"schema":1,"backend":"linux_desktop","base_bundle_sha256":"1".repeat(64),"desktop_bundle_sha256":"2".repeat(64),
        "waypipe_sha256":"3".repeat(64),"shm_fixture_sha256":"4".repeat(64),"desktop_binary_sha256":"5".repeat(64)})
}
fn binding() -> Binding {
    Binding::capture(
        &profile(1),
        SessionId::new(i64::MAX).unwrap(),
        1000,
        "aabbccddeeff0011",
        Pins::parse(&pins_json().to_string()).unwrap(),
    )
    .unwrap()
}
fn row(b: &Binding, seq: u64, request: u64, event: Value) -> Value {
    json!({"schema":1,"backend":"linux_desktop","revision":REVISION,"session":b.session,"profile_revision":b.profile_revision,
        "scope":b.scope,"nonce":b.nonce,"uid":b.uid,"sequence":seq.to_string(),"request_sequence":request.to_string(),"event":event})
}
fn event(kind: &str, payload: Value) -> Value {
    json!({"type":kind,"payload":payload})
}
fn catalog(b: &Binding) -> Value {
    let s = b.pins.source_hashes();
    let p = &b.pins;
    event(
        "catalog",
        json!({"schema":1,"namespace":"linux-desktop","scope":b.scope,"revision":b.catalog_revision,
        "entries":[{"id":APP_ID,"label":"Linux アプリ","mode":"gui","icon":"builtin:script","available":true}],
        "arbitrary_exec":false,"terminal":false,"instance_limit":1,"base_bundle_sha256":p.base_bundle_sha256,
        "desktop_bundle_sha256":p.desktop_bundle_sha256,"waypipe_sha256":p.waypipe_sha256,"shm_fixture_sha256":p.shm_fixture_sha256,
        "desktop_binary_sha256":p.desktop_binary_sha256,"helper_sha256":s.base,"adapter_sha256":s.adapter,"owner_sha256":s.owner,
        "demo_source_sha256":s.demo,"waypipe_version":"0.11.3"}),
    )
}
fn prepared(b: &Binding) -> Protocol {
    let mut p = Protocol::new(b.clone());
    p.accept(
        &serde_json::to_vec(&row(b, 1, 0, event("prepared", json!({"lease_ms":15000})))).unwrap(),
    )
    .unwrap();
    p
}
fn ready(b: &Binding) -> Protocol {
    let mut p = prepared(b);
    p.command(Action::Inspect, None).unwrap();
    p.accept(&serde_json::to_vec(&row(b, 2, 1, catalog(b))).unwrap())
        .unwrap();
    p
}
#[test]
fn pins_are_strict_bounded_and_private() {
    let p = Pins::parse(&pins_json().to_string()).unwrap();
    assert_eq!(format!("{p:?}"), "LinuxDesktopPins(<private>)");
    assert_eq!(
        format!("{:?}", p.source_hashes()),
        "LinuxDesktopSources(<private>)"
    );
    for (field, value) in [
        ("backend", json!("linux_posix")),
        ("schema", json!(true)),
        ("waypipe_sha256", json!("a;true")),
        ("extra", json!(1)),
    ] {
        let mut v = pins_json();
        v[field] = value;
        assert!(Pins::parse(&v.to_string()).is_err());
    }
    assert!(Pins::parse(&pins_json().to_string().replacen('{', "{\"schema\":1,", 1)).is_err());
    assert!(Pins::parse(&" ".repeat(4097)).is_err());
}
#[test]
fn binding_preserves_large_ids_profile_revision_and_separate_backend() {
    let b = binding();
    assert!(b.matches_profile(&profile(1), b.session()));
    assert!(!b.matches_profile(&profile(2), b.session()));
    assert!(!b.matches_profile(&profile(1), SessionId::new(1).unwrap()));
    assert!(
        Binding::capture(
            &profile(0),
            SessionId::new(1).unwrap(),
            1000,
            "aabbccddeeff0011",
            b.pins.clone()
        )
        .is_err()
    );
    assert!(
        Binding::capture(
            &profile(1),
            SessionId::new(1).unwrap(),
            0,
            "aabbccddeeff0011",
            b.pins.clone()
        )
        .is_err()
    );
    assert_eq!(format!("{b:?}"), "LinuxDesktopBinding(<private>)");
    let mut p = prepared(&b);
    let command = p.command(Action::Inspect, None).unwrap();
    let v: Value = serde_json::from_slice(&command).unwrap();
    assert_eq!(v["session"], "9223372036854775807");
    assert_eq!(v["profile_revision"], "9007199254740993");
}
#[test]
fn context_hash_matches_python_adapter_exactly_without_floating_point_ids() {
    let b = binding();
    let source = r#"import sys,json,runpy
m=runpy.run_path(sys.argv[1]);v=json.load(sys.stdin);c=m['Context'](*v);print(json.dumps({'scope':c.scope,'revision':m['digest']({'scope':c.scope,'bundle':'2'*64,'app':m['APP_ID']})}))"#;
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scripts/linux/eidolon_linux_desktop.py");
    let mut child = std::process::Command::new("/usr/bin/python3")
        .args(["-I", "-S", "-B", "-c", source])
        .arg(path)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .unwrap();
    use std::io::Write;
    child
        .stdin
        .take()
        .unwrap()
        .write_all(
            serde_json::to_string(&json!([
                b.session,
                b.profile_revision,
                b.profile_binding,
                b.nonce
            ]))
            .unwrap()
            .as_bytes(),
        )
        .unwrap();
    let result = child.wait_with_output().unwrap();
    assert!(result.status.success());
    let got: Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(got["scope"], b.scope);
    assert_eq!(got["revision"], b.catalog_revision);
}
#[test]
fn catalog_requires_all_pins_mode_and_the_fixed_single_entry() {
    let b = binding();
    for (key, value) in [
        ("waypipe_version", json!("0.11.4")),
        ("adapter_sha256", json!("f".repeat(64))),
        ("scope", json!("f".repeat(64))),
        ("revision", json!("f".repeat(64))),
        ("terminal", json!(true)),
        ("arbitrary_exec", json!(true)),
        ("instance_limit", json!(9)),
    ] {
        let mut p = prepared(&b);
        p.command(Action::Inspect, None).unwrap();
        let mut v = catalog(&b);
        v["payload"][key] = value;
        assert!(
            p.accept(&serde_json::to_vec(&row(&b, 2, 1, v)).unwrap())
                .is_err()
        );
        assert_eq!(p.phase(), Phase::Failed);
        assert!(p.selection().is_none());
    }
    let mut p = prepared(&b);
    p.command(Action::Inspect, None).unwrap();
    let mut v = catalog(&b);
    v["payload"]["entries"][0]["mode"] = json!("terminal");
    assert!(
        p.accept(&serde_json::to_vec(&row(&b, 2, 1, v)).unwrap())
            .is_err()
    );
}
#[test]
fn no_start_before_catalog_no_second_enqueue_or_automatic_retry() {
    let b = binding();
    let mut p = Protocol::new(b.clone());
    assert!(p.command(Action::Start, None).is_err());
    assert!(p.selection().is_none());
    p = ready(&b);
    let selection = p.selection().unwrap();
    assert_eq!(format!("{selection:?}"), "LinuxDesktopSelection(<private>)");
    p.command(Action::Start, Some(&selection)).unwrap();
    assert!(p.command(Action::Start, Some(&selection)).is_err());
    assert!(p.command(Action::Heartbeat, None).is_err());
    p.fail();
    assert!(p.selection().is_none());
    assert!(p.command(Action::Start, Some(&selection)).is_err());
}
#[test]
fn full_lifecycle_distinguishes_catalog_started_and_remote_stopped() {
    let b = binding();
    let mut p = ready(&b);
    let selection = p.selection().unwrap();
    assert_eq!(p.phase(), Phase::Catalog);
    p.command(Action::Start, Some(&selection)).unwrap();
    p.accept(
        &serde_json::to_vec(&row(
            &b,
            3,
            2,
            event("started", json!({"app_id":APP_ID,"mode":"gui"})),
        ))
        .unwrap(),
    )
    .unwrap();
    assert_eq!(p.phase(), Phase::Started);
    assert!(p.selection().is_none());
    p.command(Action::Heartbeat, None).unwrap();
    p.accept(
        &serde_json::to_vec(&row(
            &b,
            4,
            3,
            event("heartbeat", json!({"phase":"started"})),
        ))
        .unwrap(),
    )
    .unwrap();
    p.command(Action::Stop, None).unwrap();
    let stop = row(
        &b,
        5,
        4,
        event(
            "stopped",
            json!({"reason":"stop","process_complete":true,"pipes_eof":true,"remote_cleaned":true,"receipt_written":true}),
        ),
    );
    p.accept(&serde_json::to_vec(&stop).unwrap()).unwrap();
    assert_eq!(p.phase(), Phase::Stopped);
    assert!(p.command(Action::Start, Some(&selection)).is_err());
    assert!(p.accept(&serde_json::to_vec(&stop).unwrap()).is_err());
}
#[test]
fn wrong_owner_sequence_ack_duplicate_and_oversize_are_terminal() {
    let b = binding();
    for (key, value) in [
        ("session", json!("1")),
        ("uid", json!(1001)),
        ("nonce", json!("0000000000000000")),
        ("scope", json!("f".repeat(64))),
        ("profile_revision", json!("1")),
        ("sequence", json!("2")),
        ("request_sequence", json!("1")),
        ("sequence", json!(1)),
        ("unknown", json!(1)),
    ] {
        let mut p = Protocol::new(b.clone());
        let mut v = row(&b, 1, 0, event("prepared", json!({"lease_ms":15000})));
        v[key] = value;
        assert!(p.accept(&serde_json::to_vec(&v).unwrap()).is_err());
        assert_eq!(p.phase(), Phase::Failed);
    }
    let mut p = Protocol::new(b.clone());
    let raw = row(&b, 1, 0, event("prepared", json!({"lease_ms":15000})))
        .to_string()
        .replace(
            "\"lease_ms\":15000",
            "\"lease_ms\":15000,\"lease_ms\":15000",
        );
    assert!(p.accept(raw.as_bytes()).is_err());
    assert!(Protocol::new(b).accept(&[b' '; 16385]).is_err());
}
#[test]
fn cleanup_pending_or_missing_eof_cannot_be_reported_as_stopped() {
    let b = binding();
    let mut unstarted = prepared(&b);
    let false_exit = row(
        &b,
        2,
        0,
        event(
            "stopped",
            json!({"reason":"process_exited","process_complete":true,"pipes_eof":true,"remote_cleaned":true,"receipt_written":true}),
        ),
    );
    assert!(
        unstarted
            .accept(&serde_json::to_vec(&false_exit).unwrap())
            .is_err()
    );
    let mut p = prepared(&b);
    p.command(Action::Stop, None).unwrap();
    let pending = row(
        &b,
        2,
        1,
        event(
            "pending",
            json!({"reason":"process_ownership","process_complete":false,"pipes_eof":false}),
        ),
    );
    p.accept(&serde_json::to_vec(&pending).unwrap()).unwrap();
    assert_eq!(p.phase(), Phase::Pending);
    assert!(p.command(Action::Inspect, None).is_err());
    for key in [
        "process_complete",
        "pipes_eof",
        "remote_cleaned",
        "receipt_written",
    ] {
        let mut p = prepared(&b);
        p.command(Action::Stop, None).unwrap();
        let mut v = event(
            "stopped",
            json!({"reason":"stop","process_complete":true,"pipes_eof":true,"remote_cleaned":true,"receipt_written":true}),
        );
        v["payload"][key] = json!(false);
        assert!(
            p.accept(&serde_json::to_vec(&row(&b, 2, 1, v)).unwrap())
                .is_err()
        );
    }
}
