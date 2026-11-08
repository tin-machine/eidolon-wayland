use super::*;
use serde_json::{Value, json};

pub(super) fn profile(index: usize) -> Profile {
    let lines: Vec<_> =
        include_str!("../../../../android/app/src/test/resources/connection-profiles-v2.tsv")
            .lines()
            .filter(|l| !l.starts_with('#') && !l.is_empty())
            .collect();
    let f: Vec<_> = lines[index].split('\t').collect();
    Profile::parse(&json!({"schema":2,"installation":"1".repeat(32),"profile_id":f[0],"revision":f[1],
        "display_name":f[2],"backend":f[3],"host":f[4],"port":f[5].parse::<u16>().unwrap(),"username":f[6],"host_key":f[7]}).to_string(), &"1".repeat(32)).unwrap()
}
pub(super) fn pins_json() -> Value {
    json!({"schema":1,"backend":"linux_desktop","base_bundle_sha256":"1".repeat(64),"desktop_bundle_sha256":"2".repeat(64),
        "waypipe_sha256":"3".repeat(64),"shm_fixture_sha256":"4".repeat(64),"desktop_binary_sha256":"5".repeat(64)})
}
pub(super) fn binding() -> Binding {
    Binding::capture(
        &profile(1),
        SessionId::new(i64::MAX).unwrap(),
        1000,
        "aabbccddeeff0011",
        Pins::parse(&pins_json().to_string()).unwrap(),
    )
    .unwrap()
}
pub(super) fn row(b: &Binding, seq: u64, request: u64, event: Value) -> Value {
    json!({"schema":1,"backend":"linux_desktop","revision":REVISION,"session":b.session,"profile_revision":b.profile_revision,
        "scope":b.scope,"nonce":b.nonce,"uid":b.uid,"sequence":seq.to_string(),"request_sequence":request.to_string(),"event":event})
}
pub(super) fn event(kind: &str, payload: Value) -> Value {
    json!({"type":kind,"payload":payload})
}
pub(super) fn catalog(b: &Binding) -> Value {
    let s = b.pins.source_hashes();
    let p = &b.pins;
    event(
        "catalog",
        json!({"schema":1,"namespace":"linux-desktop","scope":b.scope,"revision":b.catalog_revision,
        "entries":[{"id":APP_ID,"label":"Linux アプリ","mode":"gui","icon":"builtin:script","available":true}],
        "arbitrary_exec":false,"terminal":false,"instance_limit":1,"base_bundle_sha256":p.base_bundle_sha256,
        "desktop_bundle_sha256":p.desktop_bundle_sha256,"waypipe_sha256":p.waypipe_sha256,"shm_fixture_sha256":p.shm_fixture_sha256,
        "desktop_binary_sha256":p.desktop_binary_sha256,"helper_sha256":s.base,"adapter_sha256":s.adapter,"owner_sha256":s.owner,
        "demo_source_sha256":s.demo,"wire_sha256":s.wire,"waypipe_version":"0.11.3"}),
    )
}
pub(super) const BOOT_ID: &str = "8d57f608-6975-4ce8-b0e0-c1bb726d6e66";
pub(super) fn prepared_event(b: &Binding) -> Value {
    event(
        "prepared",
        json!({"lease_ms":15000,"boot_id":BOOT_ID,
        "remote_socket":format!("/home/fixture/.eidolon-desktop/s-{}/p",b.nonce)}),
    )
}
fn prepared(b: &Binding) -> Protocol {
    let mut p = Protocol::new(b.clone());
    p.accept(&serde_json::to_vec(&row(b, 1, 0, prepared_event(b))).unwrap())
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
        let mut v = row(&b, 1, 0, prepared_event(&b));
        v[key] = value;
        assert!(p.accept(&serde_json::to_vec(&v).unwrap()).is_err());
        assert_eq!(p.phase(), Phase::Failed);
    }
    let mut p = Protocol::new(b.clone());
    let raw = row(&b, 1, 0, prepared_event(&b)).to_string().replace(
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

fn running(b: &Binding) -> Protocol {
    let mut p = ready(b);
    p.command(Action::Start, p.selection().as_ref()).unwrap();
    p.accept(
        &row(
            b,
            3,
            2,
            event("started", json!({"app_id":APP_ID,"mode":"gui"})),
        )
        .to_string()
        .into_bytes(),
    )
    .unwrap();
    p
}
pub(super) fn stopped(reason: &str) -> Value {
    event(
        "stopped",
        json!({"reason":reason,"process_complete":true,"pipes_eof":true,
        "remote_cleaned":true,"receipt_written":true}),
    )
}
#[test]
fn asynchronous_exit_can_cross_generated_heartbeat_or_stop_without_acknowledging_them() {
    let b = binding();
    for action in [Action::Heartbeat, Action::Stop] {
        for reason in ["process_exited", "lease_expired", "input_eof"] {
            let mut p = running(&b);
            // The helper already emitted request_sequence=2. The next command's
            // bytes can be generated before that event is received or sent.
            let unsent = p.command(action, None).unwrap();
            assert_eq!(
                serde_json::from_slice::<Value>(&unsent).unwrap()["sequence"],
                "3"
            );
            p.accept(row(&b, 4, 2, stopped(reason)).to_string().as_bytes())
                .unwrap();
            assert_eq!(p.phase(), Phase::Stopped);
            assert!(p.command(action, None).is_err());
            assert!(p.selection().is_none());
        }
    }
    for request in [2, 3] {
        let mut p = running(&b);
        p.command(Action::Heartbeat, None).unwrap();
        p.accept(
            row(
                &b,
                4,
                request,
                event(
                    "pending",
                    json!({"reason":"process_ownership",
            "process_complete":false,"pipes_eof":false}),
                ),
            )
            .to_string()
            .as_bytes(),
        )
        .unwrap();
        assert_eq!(p.phase(), Phase::Pending);
        assert!(p.command(Action::Heartbeat, None).is_err());
    }
}
#[test]
fn heartbeat_ack_then_exit_uses_new_ack_floor_and_terminal_events_cannot_resurrect() {
    let b = binding();
    let mut p = running(&b);
    p.command(Action::Heartbeat, None).unwrap();
    p.accept(
        row(&b, 4, 3, event("heartbeat", json!({"phase":"started"})))
            .to_string()
            .as_bytes(),
    )
    .unwrap();
    p.accept(
        row(&b, 5, 3, stopped("process_exited"))
            .to_string()
            .as_bytes(),
    )
    .unwrap();
    assert_eq!(p.phase(), Phase::Stopped);
    let late_ack = row(&b, 6, 3, event("heartbeat", json!({"phase":"started"})));
    assert!(p.accept(late_ack.to_string().as_bytes()).is_err());
    assert!(p.selection().is_none());
    assert!(p.command(Action::Start, None).is_err());

    let mut p = running(&b);
    p.command(Action::Heartbeat, None).unwrap();
    p.accept(
        row(&b, 4, 3, event("heartbeat", json!({"phase":"started"})))
            .to_string()
            .as_bytes(),
    )
    .unwrap();
    p.command(Action::Stop, None).unwrap();
    assert!(
        p.accept(
            row(&b, 5, 2, stopped("process_exited"))
                .to_string()
                .as_bytes()
        )
        .is_err()
    );
}
#[test]
fn asynchronous_range_never_relaxes_binding_event_order_or_command_ack_correlation() {
    let b = binding();
    for (key, value) in [
        ("request_sequence", json!("1")),
        ("request_sequence", json!("4")),
        ("request_sequence", json!("03")),
        ("sequence", json!("3")),
        ("sequence", json!("5")),
        ("session", json!("1")),
        ("scope", json!("f".repeat(64))),
        ("nonce", json!("0000000000000000")),
        ("profile_revision", json!("1")),
        ("uid", json!(1001)),
    ] {
        let mut p = running(&b);
        p.command(Action::Heartbeat, None).unwrap();
        let mut value_row = row(&b, 4, 2, stopped("process_exited"));
        value_row[key] = value;
        assert!(p.accept(value_row.to_string().as_bytes()).is_err(), "{key}");
        assert_eq!(p.phase(), Phase::Failed);
    }
    for kind in ["heartbeat", "stop"] {
        let mut p = running(&b);
        p.command(
            if kind == "stop" {
                Action::Stop
            } else {
                Action::Heartbeat
            },
            None,
        )
        .unwrap();
        let value = if kind == "stop" {
            stopped("stop")
        } else {
            event("heartbeat", json!({"phase":"started"}))
        };
        assert!(
            p.accept(row(&b, 4, 2, value).to_string().as_bytes())
                .is_err()
        );
    }
    let mut p = running(&b);
    p.command(Action::Heartbeat, None).unwrap();
    assert!(
        p.accept(row(&b, 4, 3, stopped("stop")).to_string().as_bytes())
            .is_err()
    );
}
#[test]
fn prepared_requires_private_normal_socket_and_canonical_boot_identity() {
    let b = binding();
    for (key, value) in [
        ("boot_id", json!("00000000-0000-0000-0000-000000000000")),
        ("boot_id", json!(BOOT_ID.to_uppercase())),
        ("boot_id", json!("8d57f60869754ce8b0e0c1bb726d6e66")),
        (
            "remote_socket",
            json!(format!("/home/x/../x/.eidolon-desktop/s-{}/p", b.nonce)),
        ),
        (
            "remote_socket",
            json!(format!("/home/x/.eidolon-linux/s-{}/p", b.nonce)),
        ),
        (
            "remote_socket",
            json!(format!(
                "/data/data/com.termux/files/home/.eidolon-desktop/s-{}/p",
                b.nonce
            )),
        ),
        (
            "remote_socket",
            json!("/home/x/.eidolon-desktop/s-0000000000000000/p"),
        ),
        ("lease_ms", json!(15001)),
    ] {
        let mut p = Protocol::new(b.clone());
        let mut value_row = prepared_event(&b);
        value_row["payload"][key] = value;
        assert!(
            p.accept(row(&b, 1, 0, value_row).to_string().as_bytes())
                .is_err(),
            "{key}"
        );
    }
}
#[test]
fn normal_bootstrap_has_only_fixed_scope_and_pins_and_parses_as_python() {
    let b = binding();
    for boot in [None, Some(BOOT_ID), Some("-")] {
        let command = owner::command(&b, boot).unwrap();
        assert!(command.starts_with("/usr/bin/python3 -I -S -c '"));
        assert!(
            command.contains("pwd.getpwuid")
                && command.contains("O_NOFOLLOW")
                && command.contains("base.bundle=pinned")
        );
        assert!(!command.contains("$HOME") && !command.contains("maximum_seconds"));
        let hashes = b.pins.source_hashes();
        for hash in [
            hashes.base,
            hashes.adapter,
            hashes.owner,
            hashes.wire,
            hashes.demo,
        ] {
            assert!(command.contains(&hash));
        }
        let code = command
            .strip_prefix("/usr/bin/python3 -I -S -c '")
            .unwrap()
            .strip_suffix('\'')
            .unwrap()
            .replace("'\\''", "'");
        let mut child = std::process::Command::new("/usr/bin/python3")
            .args([
                "-I",
                "-S",
                "-c",
                "import ast,sys;ast.parse(sys.stdin.read())",
            ])
            .stdin(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        use std::io::Write;
        child
            .stdin
            .take()
            .unwrap()
            .write_all(code.as_bytes())
            .unwrap();
        assert!(child.wait().unwrap().success());
    }
    assert!(owner::command(&b, Some("';bad")).is_err());
    let mut bad = b;
    bad.nonce = "';bad".into();
    assert!(owner::command(&bad, None).is_err());
}

// The server is OpenSSH; only the pinned remote helper is replaced with a
// deterministic wire peer. It can reproduce both sides of an event/command race
// without sleeps in the Rust actor or exposing normal user credentials.
pub(super) async fn actor_fixture(
    b: &Binding,
    behavior: &'static str,
) -> crate::forward::openssh::OpenSsh {
    use std::{
        fs,
        os::unix::fs::{DirBuilderExt, PermissionsExt},
    };
    let b = b.clone();
    crate::forward::openssh::OpenSsh::with_forced_command(move |directory| {
        let mut marker = row(&b, 0, 0, Value::Null);
        for key in ["sequence", "request_sequence", "event"] {
            marker.as_object_mut().unwrap().remove(key);
        }
        let remote_directory = directory.join(".eidolon-desktop").join(format!("s-{}", b.nonce));
        fs::DirBuilder::new().recursive(true).mode(0o700).create(&remote_directory).unwrap();
        let mut prepared = prepared_event(&b)["payload"].clone();
        prepared["remote_socket"] = json!(remote_directory.join("p").to_str().unwrap());
        let quoted = |value: &Value| serde_json::to_string(&value.to_string()).unwrap();
        let code = format!(r#"import json,sys,socket
from pathlib import Path
marker=json.loads({})
prepared=json.loads({})
catalog=json.loads({})
behavior={}
log=Path({})
sequence=0
request_sequence=0
phase='prepared'
received=[]
def encode(kind,payload,request=None):
 global sequence
 sequence+=1
 value=dict(marker,sequence=str(sequence),request_sequence=str(request_sequence if request is None else request),event=dict(type=kind,payload=payload))
 return json.dumps(value,separators=(',',':'))+'\n'
def emit(kind,payload,request=None):
 sys.stdout.write(encode(kind,payload,request));sys.stdout.flush()
def stopped(reason):
 emit('stopped',dict(reason=reason,process_complete=True,pipes_eof=True,remote_cleaned=True,receipt_written=True))
emit('prepared',prepared)
for line in sys.stdin:
 value=json.loads(line)
 assert set(value)==set(marker)|{{'sequence','command'}}
 assert all(value[key]==item for key,item in marker.items())
 assert value['sequence']==str(request_sequence+1)
 kind=value['command']['type'];received.append(kind);log.write_text(json.dumps(received))
 if kind=='heartbeat' and behavior=='cross_exit':
  stopped('process_exited');break
 if kind=='start' and behavior=='lost_start':
  sys.exit(0)
 request_sequence+=1
 if kind=='inspect':
  assert phase=='prepared';phase='catalog';emit('catalog',catalog)
 elif kind=='start':
  if behavior=='forward':
   peer=socket.socket(socket.AF_UNIX);peer.connect(prepared['remote_socket']);peer.sendall(b'normal-desktop')
   reply=b''
   while len(reply)<14:
    chunk=peer.recv(14-len(reply));assert chunk;reply+=chunk
   assert reply==b'normal-desktop';peer.close()
  assert phase=='catalog';phase='started'
  if behavior=='immediate_exit':
   data=encode('started',dict(app_id='gtk3-desktop',mode='gui'))
   data+=encode('stopped',dict(reason='process_exited',process_complete=True,pipes_eof=True,remote_cleaned=True,receipt_written=True))
   sys.stdout.write(data);sys.stdout.flush();break
  emit('started',dict(app_id='gtk3-desktop',mode='gui'))
  if behavior=='partial_eof':
   sys.stdout.write('{{');sys.stdout.flush();break
 elif kind=='heartbeat':
  emit('heartbeat',dict(phase=phase))
 elif kind=='stop':
  stopped('stop')
  if behavior=='duplicate_terminal':stopped('stop')
  break
 else:
  raise AssertionError('UNEXPECTED_COMMAND')
"#,
            quoted(&marker),
            quoted(&prepared),
            quoted(&catalog(&b)["payload"]),
            serde_json::to_string(behavior).unwrap(),
            serde_json::to_string(directory.join("commands.json").to_str().unwrap()).unwrap(),
        );
        let entry = directory.join("desktop.py");
        fs::write(&entry, code).unwrap();
        fs::set_permissions(&entry, fs::Permissions::from_mode(0o600)).unwrap();
        Some(format!("/usr/bin/python3 -I -S {}", entry.display()))
    }).await
}

async fn actor_prepared(
    ssh: &crate::forward::openssh::OpenSsh,
    b: &Binding,
    cancel: tokio_util::sync::CancellationToken,
) -> owner::Owner {
    let mut reserved = false;
    let mut active = owner::Owner::open(&ssh.client, b, cancel, || {
        reserved = true;
        Ok(())
    })
    .await
    .unwrap();
    assert!(reserved);
    while active.phase() == Phase::AwaitingPrepared {
        active.next().await.unwrap();
    }
    assert_eq!(active.phase(), Phase::Prepared);
    assert_eq!(active.boot_id(), Some(BOOT_ID));
    active
}

async fn actor_started(active: &mut owner::Owner) {
    active.inspect().await.unwrap();
    while active.phase() != Phase::Catalog {
        active.next().await.unwrap();
    }
    active.start(&active.selection().unwrap()).await.unwrap();
    while active.phase() != Phase::Started {
        active.next().await.unwrap();
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ssh_actor_serializes_stop_after_inflight_ack_and_proves_channel_completion() {
    let b = binding();
    let ssh = actor_fixture(&b, "normal").await;
    let mut active = actor_prepared(&ssh, &b, tokio_util::sync::CancellationToken::new()).await;
    active.inspect().await.unwrap();
    active.stop().await.unwrap();
    // Stop cannot overwrite the inspect correlation or be sent twice.
    active.stop().await.unwrap();
    while !active.closed() {
        active.next().await.unwrap();
    }
    assert!(active.terminal_remote_clean() && active.channel_complete());
    assert_eq!(active.report().reason, Some("stop"));
    assert!(active.close().await);
    let requests: Value =
        serde_json::from_slice(&std::fs::read(ssh.dir.join("commands.json")).unwrap()).unwrap();
    assert_eq!(requests, json!(["inspect", "stop"]));
    let diagnostic = serde_json::to_string(&active.report()).unwrap();
    assert!(!diagnostic.contains(&b.nonce) && !diagnostic.contains(BOOT_ID));
    ssh.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ssh_actor_accepts_async_exit_crossing_heartbeat_and_stops_on_cancellation() {
    for behavior in ["cross_exit", "normal"] {
        let b = binding();
        let ssh = actor_fixture(&b, behavior).await;
        let cancel = tokio_util::sync::CancellationToken::new();
        let mut active = actor_prepared(&ssh, &b, cancel.clone()).await;
        actor_started(&mut active).await;
        if behavior == "cross_exit" {
            active.control(Action::Heartbeat, None).await.unwrap();
        } else {
            cancel.cancel();
        }
        while !active.closed() {
            active.next().await.unwrap();
        }
        assert!(active.terminal_remote_clean() && active.channel_complete());
        assert_eq!(
            active.report().reason,
            Some(if behavior == "cross_exit" {
                "process_exited"
            } else {
                "stop"
            })
        );
        assert!(active.close().await);
        let requests: Value =
            serde_json::from_slice(&std::fs::read(ssh.dir.join("commands.json")).unwrap()).unwrap();
        assert_eq!(
            requests,
            if behavior == "cross_exit" {
                json!(["inspect", "start", "heartbeat"])
            } else {
                json!(["inspect", "start", "stop"])
            }
        );
        ssh.close().await;
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ssh_actor_lost_start_ack_partial_eof_and_duplicate_terminal_never_become_clean() {
    for behavior in ["lost_start", "partial_eof", "duplicate_terminal"] {
        let b = binding();
        let ssh = actor_fixture(&b, behavior).await;
        let mut active = actor_prepared(&ssh, &b, tokio_util::sync::CancellationToken::new()).await;
        active.inspect().await.unwrap();
        while active.phase() != Phase::Catalog {
            active.next().await.unwrap();
        }
        let selection = active.selection().unwrap();
        active.start(&selection).await.unwrap();
        let error = loop {
            match active.next().await {
                Err(error) => break error,
                Ok(()) if behavior == "duplicate_terminal" && active.phase() == Phase::Started => {
                    active.stop().await.unwrap();
                }
                Ok(()) => assert!(!active.closed()),
            }
        };
        assert!(matches!(
            error,
            "DESKTOP_HELPER_LOST" | "DESKTOP_PROTOCOL_INVALID"
        ));
        assert!(!active.terminal_remote_clean() && !active.channel_complete());
        assert!(active.start(&selection).await.is_err());
        active.close().await;
        let requests: Vec<String> =
            serde_json::from_slice(&std::fs::read(ssh.dir.join("commands.json")).unwrap()).unwrap();
        assert_eq!(
            requests.iter().filter(|action| *action == "start").count(),
            1
        );
        ssh.close().await;
    }
}
