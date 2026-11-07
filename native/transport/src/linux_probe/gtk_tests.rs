use super::*;
use serde_json::{Value, json};

const NONCE: &str = "1234567890abcdef";
fn pins_json() -> Value {
    json!({"schema":1,"base":{"schema":1,"bundle_sha256":"b".repeat(64),"waypipe_sha256":"c".repeat(64),"fixture_sha256":"d".repeat(64)},
        "gtk_bundle_sha256":"e".repeat(64),"source_sha256":hash(include_bytes!("../../../../scripts/fixtures/gtk3-interaction-fixture.c")),
        "binary_sha256":"f".repeat(64),"gtk_version":"3.24.52"})
}
pub(super) fn binding(case: GtkCase) -> GtkBinding {
    GtkPins::parse(&pins_json().to_string())
        .unwrap()
        .binding(case)
}
fn frame(kind: &str) -> Value {
    json!({"schema":1,"backend":"linux_posix","revision":REVISION,"nonce":NONCE,"uid":1000,"type":kind})
}
fn prepared() -> Value {
    let mut value = frame("prepared");
    value["socket"] = format!("/home/fixture/.eidolon-linux/s-{NONCE}/p").into();
    value["launcher_sha256"] = binding(GtkCase::Input).pins.base.helper_sha256().into();
    value
}
fn tools() -> Value {
    let mut value = frame("tools");
    let pins = pins_json();
    for (key,item) in json!({"waypipe_version":"0.11.3","fixture_revision":"eidolon-gtk3-interaction-v1","bundle_sha256":pins["base"]["bundle_sha256"],
        "files":{"eidolon-linux-session.py":binding(GtkCase::Input).pins.base.helper_sha256(),"waypipe":pins["base"]["waypipe_sha256"],"eidolon-shm-fixture":pins["base"]["fixture_sha256"]},
        "gtk_bundle_sha256":pins["gtk_bundle_sha256"],"gtk_files":{"gtk3-interaction-fixture.c":pins["source_sha256"],"eidolon-gtk3-fixture":pins["binary_sha256"]},
        "gtk_version":pins["gtk_version"]}).as_object().unwrap() {value[key]=item.clone();}
    value
}
fn row(case: GtkCase, final_row: bool) -> Value {
    let run = format!("{NONCE}{NONCE}");
    let app = format!("io.github.tinmachine.eidolon.gtkfixture.r{run}");
    let mut value = json!({"schema":1,"probe":"eidolon-gtk3-interaction-v1","run_id":run,"case":case,"app_id":app,"dialog_app_id":format!("{app}.dialog"),
        "pid":43,"uid":1000,"sequence":if final_row{2}else{1},"phase":if final_row{"final"}else{"ready"},"reason":if final_row{"completed"}else{"running"},
        "complete":final_row,"host_selftest":false,"physical_input_verified":false,"a_down":0,"a_up":0,"delete_down":0,"delete_up":0,"other_keys":0,
        "entry_changes":0,"button_clicks":0,"token_seen":false,"delete_seen":false,"dialog_opened":0,"dialog_closed":0,"dialog_responses":0,
        "dialog_parent_set":false,"dialog_visible":false,"main_configures":1,"dialog_configures":0,"main_draws":1,"dialog_draws":0,
        "resize_large_seen":false,"resize_return_seen":false,"logical_size":[640,360],"entry_rect":[20,20,100,40],"button_rect":[20,100,100,40]});
    if final_row {
        match case {
            GtkCase::Input => {
                for key in [
                    "a_down",
                    "a_up",
                    "delete_down",
                    "delete_up",
                    "button_clicks",
                ] {
                    value[key] = 1.into();
                }
                value["entry_changes"] = 2.into();
                value["token_seen"] = true.into();
                value["delete_seen"] = true.into();
            }
            GtkCase::Dialog => {
                for key in [
                    "dialog_opened",
                    "dialog_closed",
                    "dialog_responses",
                    "dialog_draws",
                ] {
                    value[key] = 1.into();
                }
                value["dialog_parent_set"] = true.into();
            }
            GtkCase::Resize => {
                value["resize_large_seen"] = true.into();
                value["resize_return_seen"] = true.into();
            }
        }
    }
    value
}
fn report(case: GtkCase, final_row: bool) -> Value {
    let mut value = frame("gtk_report");
    value["process"] = json!({"pid":43,"group":42,"start":"3333","uid":1000,"state":"R"});
    value["report"] = row(case, final_row);
    value
}
fn finished(case: GtkCase) -> Value {
    let mut value = frame("finished");
    for (key,item) in json!({"reason":"Completed","cleaned":true,"group_alive":false,"child_reaped":true,"pipes_closed":true,
        "pattern_verified":false,"exit_status":0,"stdout":{"bytes":2500,"retained":2500,"truncated":false},"stderr":{"bytes":0,"retained":0,"truncated":false},
        "mode":case.helper_mode(),"gtk_verified":true,"gtk_reports":2,"gtk_report_error":null,"gtk_forced_stop":false}).as_object().unwrap(){value[key]=item.clone();}
    value
}
fn push(protocol: &mut protocol::Protocol, value: Value) -> Result<(), &'static str> {
    protocol.line(value.to_string().as_bytes())
}
fn running(case: GtkCase) -> protocol::Protocol {
    let binding = binding(case);
    let mut p = protocol::Protocol::new(NONCE, 1000, &binding.pins.base).with_gtk(Some(&binding));
    push(&mut p, prepared()).unwrap();
    p.inspect().unwrap();
    push(&mut p, tools()).unwrap();
    p.start().unwrap();
    let mut child = frame("child_started");
    child["child"] = json!({"pid":42,"group":42,"start":"2222","uid":1000,"state":"S"});
    push(&mut p, child).unwrap();
    p
}
fn completed(case: GtkCase) -> protocol::Protocol {
    let mut p = running(case);
    push(&mut p, report(case, false)).unwrap();
    push(&mut p, report(case, true)).unwrap();
    p
}
#[test]
fn pins_are_fixed_source_bound_strict_and_redacted() {
    let good = pins_json();
    assert_eq!(
        format!("{:?}", GtkPins::parse(&good.to_string()).unwrap()),
        "LinuxGtkPins(<private>)"
    );
    for (key, bad) in [
        ("schema", json!(true)),
        ("source_sha256", json!("0".repeat(64))),
        ("binary_sha256", json!("../binary")),
        ("gtk_version", json!("4.0.0")),
        ("argv", json!(["/bin/sh"])),
    ] {
        let mut value = good.clone();
        value[key] = bad;
        assert!(GtkPins::parse(&value.to_string()).is_err());
    }
    assert!(GtkPins::parse(&good.to_string().replacen("{", "{\"schema\":1,", 1)).is_err());
    assert!(GtkPins::parse(&"x".repeat(4097)).is_err());
    let binding = binding(GtkCase::Dialog);
    let command = owner::gtk_command(&binding.pins.base, NONCE, Some(&binding), false).unwrap();
    let code = command
        .strip_prefix("/usr/bin/python3 -I -S -c '")
        .unwrap()
        .strip_suffix('\'')
        .unwrap()
        .replace("'\\''", "'");
    assert!(code.contains("prepare-gtk-dialog") && code.contains("m['gtk_bundle']=pinned_gtk"));
    assert!(
        command.contains(&binding.pins.gtk_bundle_sha256)
            && command.contains(&binding.pins.source_sha256)
    );
    assert!(!command.contains("$HOME") && !command.contains("com.termux"));
    assert!(owner::gtk_command(&binding.pins.base, "';bad", Some(&binding), false).is_err());
}
#[test]
fn three_cases_require_reports_eof_exit_and_cleanup_and_never_pattern_hashes() {
    for case in [GtkCase::Input, GtkCase::Dialog, GtkCase::Resize] {
        let mut p = completed(case);
        push(&mut p, finished(case)).unwrap();
        assert!(!p.gtk_pass());
        assert!(p.finish(0).is_err());
        p.report.eof = true;
        p.report.exit_status = Some(0);
        p.finish(0).unwrap();
        assert!(p.gtk_pass() && p.cleaned());
        assert!(!p.pattern_pass());
        let report = p.report.gtk.unwrap();
        assert_eq!(report.reports, 2);
        assert!(report.process_bound && report.helper_verified);
    }
}
#[test]
fn report_pid_group_start_appid_type_and_order_are_not_rebound() {
    for (field, bad) in [
        ("pid", json!(44)),
        ("group", json!(99)),
        ("start", json!("4444")),
        ("uid", json!(1001)),
    ] {
        let mut p = running(GtkCase::Input);
        push(&mut p, report(GtkCase::Input, false)).unwrap();
        let mut second = report(GtkCase::Input, true);
        second["process"][field] = bad;
        assert!(push(&mut p, second).is_err());
    }
    for (field, bad) in [
        ("host_selftest", json!(true)),
        ("physical_input_verified", json!(true)),
        ("uid", json!(true)),
        ("case", json!("dialog")),
        ("app_id", json!("other")),
        ("sequence", json!(2)),
        ("entry_rect", json!([0, 0, 4097, 20])),
        ("a_down", json!(-1)),
        ("text", json!("never logged")),
    ] {
        let mut p = running(GtkCase::Input);
        let mut first = report(GtkCase::Input, false);
        first["report"][field] = bad;
        assert!(push(&mut p, first).is_err());
    }
    let mut p = completed(GtkCase::Input);
    let mut extra = report(GtkCase::Input, true);
    extra["report"]["sequence"] = 3.into();
    assert!(push(&mut p, extra).is_err());
}
#[test]
fn summary_cannot_claim_complete_with_missing_rows_forced_exit_wrong_mode_or_count() {
    for (key, bad) in [
        ("mode", json!("gtk_dialog")),
        ("gtk_forced_stop", json!(true)),
        ("gtk_reports", json!(1)),
        ("exit_status", json!(1)),
        ("pattern_verified", json!(true)),
        ("cleaned", json!(false)),
        ("gtk_report_error", json!("GTK_REPORT_IDENTITY")),
    ] {
        let mut p = completed(GtkCase::Input);
        let mut summary = finished(GtkCase::Input);
        summary[key] = bad;
        assert!(push(&mut p, summary).is_err());
    }
    assert!(push(&mut running(GtkCase::Input), finished(GtkCase::Input)).is_err());
    let mut p = completed(GtkCase::Input);
    p.stop();
    push(&mut p, finished(GtkCase::Input)).unwrap();
    p.report.eof = true;
    p.report.exit_status = Some(0);
    p.finish(0).unwrap();
    assert!(p.cleaned());
    assert!(!p.gtk_pass());
}
#[test]
fn pattern_mode_rejects_gtk_frame_and_gtk_rejects_pattern_receipt() {
    let b = binding(GtkCase::Input);
    let mut p = protocol::Protocol::new(NONCE, 1000, &b.pins.base);
    push(&mut p, prepared()).unwrap();
    p.inspect().unwrap();
    assert!(push(&mut p, tools()).is_err());
    let mut p = running(GtkCase::Input);
    let mut value = finished(GtkCase::Input);
    value["pattern"] = json!({"frames":100});
    assert!(push(&mut p, value).is_err());
}
#[test]
fn gtk_recovery_receipt_is_cleanup_only_and_requires_captured_case() {
    let b = binding(GtkCase::Input);
    let mut reply = frame("unused");
    reply.as_object_mut().unwrap().remove("type");
    reply["cleaned"] = true.into();
    reply["already_absent"] = true.into();
    reply["receipt"] = finished(GtkCase::Input);
    assert_eq!(
        gtk::audit(reply.to_string().as_bytes(), NONCE, 1000, &b),
        Ok(true)
    );
    assert!(
        gtk::audit(
            reply.to_string().as_bytes(),
            NONCE,
            1000,
            &binding(GtkCase::Dialog)
        )
        .is_err()
    );
    reply["receipt"]["group_alive"] = true.into();
    assert!(gtk::audit(reply.to_string().as_bytes(), NONCE, 1000, &b).is_err());
    reply["receipt"]["gtk_verified"] = false.into();
    assert_eq!(
        gtk::audit(reply.to_string().as_bytes(), NONCE, 1000, &b),
        Ok(false)
    );
}
#[test]
fn gtk_journal_is_separate_and_keeps_other_case_profile_or_pin_owner() {
    use std::os::unix::fs::DirBuilderExt;
    let dir = std::env::temp_dir().join(format!("elgj-{:016x}", rand::random::<u64>()));
    std::fs::DirBuilder::new().mode(0o700).create(&dir).unwrap();
    let b = binding(GtkCase::Input);
    let request = Request {
        session: SessionId::new(1).unwrap(),
        profile_binding: &"a".repeat(64),
        profile_revision: "9007199254740993",
        files: &dir,
        local_socket: Path::new("/fixture/p"),
        nonce: NONCE,
        uid: 1000,
        pins: &b.pins.base,
        gtk: Some(&b),
    };
    let mut journal = journal::Journal::reserve(&request).unwrap();
    assert!(dir.join("linux-gtk-probe-state/pending.json").exists());
    let mut recovery = Recovery {
        session: SessionId::new(2).unwrap(),
        profile_binding: request.profile_binding,
        profile_revision: request.profile_revision,
        files: &dir,
        nonce: NONCE,
        uid: 1000,
        pins: &b.pins.base,
        gtk: None,
    };
    assert!(journal::Journal::matching(&recovery).unwrap().is_none()); // old pattern path is not read or created.
    assert!(!dir.join("linux-probe-state").exists());
    recovery.gtk = Some(&b);
    assert!(journal::Journal::matching(&recovery).unwrap().is_some());
    let other = binding(GtkCase::Dialog);
    recovery.gtk = Some(&other);
    assert!(journal::Journal::matching(&recovery).is_err());
    recovery.gtk = Some(&b);
    recovery.profile_revision = "2";
    assert!(journal::Journal::matching(&recovery).is_err());
    assert!(dir.join("linux-gtk-probe-state/pending.json").exists());
    journal.clear().unwrap();
    std::fs::remove_dir_all(dir).unwrap();
}

// Real SSH/StreamLocal with synthetic counters: neither GTK execution nor SHM pixels are asserted here.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn synthetic_gtk_ssh_owner_preserves_mode_cancel_channel_and_separate_journal() {
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    for cancel_running in [false, true] {
        let ssh = crate::forward::openssh::OpenSsh::with_forced_command(move |dir| {
            let mut frames = vec![
                prepared(),
                tools(),
                frame("child_started"),
                report(GtkCase::Input, false),
                report(GtkCase::Input, true),
                finished(GtkCase::Input),
            ];
            let uid = unsafe { libc::getuid() };
            for value in &mut frames {
                value["uid"] = uid.into();
            }
            frames[0]["socket"] = dir
                .join(format!(".eidolon-linux/s-{NONCE}/p"))
                .to_str()
                .unwrap()
                .into();
            frames[2]["child"] = json!({"pid":42,"group":42,"start":"2222","uid":uid,"state":"S"});
            for value in &mut frames[3..5] {
                value["process"]["uid"] = uid.into();
                value["report"]["uid"] = uid.into();
            }
            let source = format!(
                r#"import json,sys,os,socket
from pathlib import Path
frames=json.loads({})
cancel={}
directory=Path(frames[0]['socket']).parent
directory.mkdir(parents=True,mode=0o700)
def emit(value):print(json.dumps(value,separators=(',',':')),flush=True)
emit(frames[0])
started=False
count=0
for line in sys.stdin:
 value=json.loads(line)
 assert value['schema']==1 and value['nonce']=='1234567890abcdef'
 if value['type']=='heartbeat':continue
 if value['type']=='inspect_tools':emit(frames[1]);continue
 if value['type']=='start':
  assert value['mode']=='gtk_input'
  started=True
  emit(frames[2])
  s=socket.socket(socket.AF_UNIX);s.connect(str(directory/'p'));s.sendall(b'G'*1024)
  data=b''
  while len(data)<1024:
   block=s.recv(1024-len(data))
   if not block:raise RuntimeError('SHORT_REPLY')
   data+=block
  assert data==b'G'*1024
  s.close();emit(frames[3]);count=1
  if cancel:continue
  emit(frames[4]);count=2
 elif value['type']!='stop':raise RuntimeError('CONTROL_INVALID')
 final=frames[5]
 if count!=2:final.update(reason='Stop',gtk_verified=False,gtk_reports=count,gtk_forced_stop=True)
 final['child_reaped']=started
 (directory/'p').unlink(missing_ok=True);directory.rmdir()
 emit(final);break
"#,
                serde_json::to_string(&serde_json::to_string(&frames).unwrap()).unwrap(),
                if cancel_running { "True" } else { "False" }
            );
            let entry = dir.join("gtk-fixed.py");
            std::fs::write(&entry, source).unwrap();
            std::fs::set_permissions(&entry, std::fs::Permissions::from_mode(0o600)).unwrap();
            Some(format!("/usr/bin/python3 -I -S {}", entry.display()))
        })
        .await;
        let dir = std::env::temp_dir().join(format!("elgw-{:016x}", rand::random::<u64>()));
        std::fs::DirBuilder::new().mode(0o700).create(&dir).unwrap();
        let path = dir.join("p");
        let listener = tokio::net::UnixListener::bind(&path).unwrap();
        let echo = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut data = [0; 1024];
            socket.read_exact(&mut data).await.unwrap();
            socket.write_all(&data).await.unwrap();
            socket.shutdown().await.unwrap();
        });
        let b = binding(GtkCase::Input);
        let cancel = CancellationToken::new();
        let result = run(
            &ssh.client,
            &ssh.router,
            Request {
                session: SessionId::new(1).unwrap(),
                profile_binding: &"a".repeat(64),
                profile_revision: "1",
                files: &dir,
                local_socket: &path,
                nonce: NONCE,
                uid: unsafe { libc::getuid() },
                pins: &b.pins.base,
                gtk: Some(&b),
            },
            &cancel,
            |report| {
                if cancel_running && report.state == "running" {
                    cancel.cancel();
                }
            },
        )
        .await;
        if cancel_running {
            echo.abort();
            let _ = echo.await;
        } else {
            echo.await.unwrap();
        }
        ssh.close().await;
        assert_eq!(
            result.error,
            if cancel_running {
                Some("CANCELLED")
            } else {
                None
            },
            "{result:?}"
        );
        assert!(
            result.remote_cleaned
                && result.channel_closed
                && result.forward_cancelled
                && !result.journal_pending,
            "{result:?}"
        );
        assert_eq!(result.frames, 0);
        assert!(!result.pattern_verified);
        assert_eq!(result.forwarding.active, 0);
        assert_eq!(
            result.gtk.as_ref().unwrap().helper_verified,
            !cancel_running
        );
        assert!(!dir.join("linux-gtk-probe-state/pending.json").exists());
        assert!(!dir.join("linux-probe-state").exists());
        std::fs::remove_dir_all(dir).unwrap();
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn gtk_case_survives_cancel_before_owner_without_creating_a_journal() {
    use std::os::unix::{fs::DirBuilderExt, net::UnixListener};
    let dir = std::env::temp_dir().join(format!("elgc-{:016x}", rand::random::<u64>()));
    std::fs::DirBuilder::new().mode(0o700).create(&dir).unwrap();
    let gtk = binding(GtkCase::Resize);
    let socket = dir.join("p");
    let _listener = UnixListener::bind(&socket).unwrap();
    let ssh = crate::forward::openssh::OpenSsh::new().await;
    let cancel = CancellationToken::new();
    cancel.cancel();
    let report = run(
        &ssh.client,
        &ssh.router,
        Request {
            session: SessionId::new(9).unwrap(),
            profile_binding: &"a".repeat(64),
            profile_revision: "1",
            files: &dir,
            local_socket: &socket,
            nonce: NONCE,
            uid: unsafe { libc::getuid() },
            pins: &gtk.pins.base,
            gtk: Some(&gtk),
        },
        &cancel,
        |_| {},
    )
    .await;
    assert_eq!(report.error, Some("CANCELLED"));
    assert_eq!(report.state, "fail");
    let gtk = report.gtk.unwrap();
    assert_eq!(gtk.case, GtkCase::Resize);
    assert_eq!(gtk.reports, 0);
    assert!(!gtk.process_bound && !gtk.helper_verified);
    assert!(!report.journal_pending && !report.forward_requested);
    assert!(!dir.join("linux-gtk-probe-state").exists());
    ssh.close().await;
    std::fs::remove_dir_all(dir).unwrap();
}
