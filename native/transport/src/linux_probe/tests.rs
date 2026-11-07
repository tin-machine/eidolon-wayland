use super::*;
use std::{
    fs,
    os::unix::fs::{DirBuilderExt, PermissionsExt},
    os::unix::net::UnixListener,
    path::PathBuf,
};

fn pins() -> Pins {
    Pins::parse(&serde_json::json!({"schema":1,"bundle_sha256":"b".repeat(64),"waypipe_sha256":"c".repeat(64),"fixture_sha256":"d".repeat(64)}).to_string()).unwrap()
}
fn frame(kind: &str) -> serde_json::Value {
    serde_json::json!({"schema":1,"backend":"linux_posix","revision":REVISION,"nonce":"1234567890abcdef","uid":1000,"type":kind})
}
fn prepared() -> serde_json::Value {
    let mut v = frame("prepared");
    v["socket"] = "/home/fixture/.eidolon-linux/s-1234567890abcdef/p".into();
    v["launcher_sha256"] = pins().helper_sha256().into();
    v
}
fn tools() -> serde_json::Value {
    let mut v = frame("tools");
    v["waypipe_version"] = "0.11.3".into();
    v["fixture_revision"] = "eidolon-shm-client-v1".into();
    v["frames"] = 100.into();
    v["bundle_sha256"] = "b".repeat(64).into();
    v["files"] = serde_json::json!({"eidolon-linux-session.py":pins().helper_sha256(),"waypipe":"c".repeat(64),"eidolon-shm-fixture":"d".repeat(64)});
    v
}
fn child() -> serde_json::Value {
    let mut v = frame("child_started");
    v["child"] = serde_json::json!({"pid":42,"group":42,"uid":1000,"start":"12345","state":"S"});
    v
}
fn finished() -> serde_json::Value {
    let mut v = frame("finished");
    for(k,value)in serde_json::json!({"reason":"Completed","cleaned":true,"group_alive":false,"child_reaped":true,"pipes_closed":true,"pattern_verified":true,"exit_status":0,"pattern":{"frames":100,"configures":1,"acks":1,"releases":100,"callbacks":100},"stdout":{"bytes":500,"retained":500,"truncated":false},"stderr":{"bytes":0,"retained":0,"truncated":false}}).as_object().unwrap(){v[k]=value.clone();}
    v
}
fn push(p: &mut protocol::Protocol, v: serde_json::Value) -> Result<(), &'static str> {
    p.line(v.to_string().as_bytes())
}
fn running() -> protocol::Protocol {
    let mut p = protocol::Protocol::new("1234567890abcdef", 1000, &pins());
    push(&mut p, prepared()).unwrap();
    p.inspect().unwrap();
    push(&mut p, tools()).unwrap();
    p.start().unwrap();
    push(&mut p, child()).unwrap();
    p
}
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!("elp-{:016x}", rand::random::<u64>()));
        fs::DirBuilder::new().mode(0o700).create(&p).unwrap();
        Self(p)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn request<'a>(dir: &'a Path, socket: &'a Path, pins: &'a Pins) -> Request<'a> {
    Request {
        session: SessionId::new(1).unwrap(),
        profile_binding: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        profile_revision: "9007199254740993",
        files: dir,
        local_socket: socket,
        nonce: "1234567890abcdef",
        uid: 1000,
        pins,
    }
}

#[test]
fn private_pins_are_strict_bounded_and_bootstrap_has_no_remote_path_argument() {
    let p = pins();
    assert_eq!(format!("{p:?}"), "LinuxPins(<private>)");
    for text in [
        "{\"schema\":1,\"schema\":1}",
        "{\"schema\":true}",
        &"x".repeat(4097),
    ] {
        assert!(Pins::parse(text).is_err());
    }
    let command = owner::command(&p, "1234567890abcdef").unwrap();
    assert!(command.starts_with("/usr/bin/python3 -I -S -c "));
    assert!(
        command.contains("pwd.getpwuid")
            && command.contains("O_NOFOLLOW")
            && command.contains(&p.helper_sha256())
    );
    assert!(!command.contains("$HOME") && !command.contains("com.termux"));
    assert!(owner::command(&p, "';touch x;").is_err());
    // bootstrapを実行せずPythonとしてparseする。helper再読込時も固定pinsを再照合する。
    let code = command
        .strip_prefix("/usr/bin/python3 -I -S -c '")
        .unwrap()
        .strip_suffix('\'')
        .unwrap()
        .replace("'\\''", "'");
    assert!(
        code.contains("m['bundle']=pinned")
            && code.contains(&p.waypipe_sha256)
            && code.contains(&p.fixture_sha256)
    );
    use std::io::Write;
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
    child
        .stdin
        .take()
        .unwrap()
        .write_all(code.as_bytes())
        .unwrap();
    assert!(child.wait().unwrap().success());
}
#[test]
fn every_identity_and_pin_is_checked_before_start() {
    for key in [
        "uid",
        "nonce",
        "backend",
        "revision",
        "launcher_sha256",
        "socket",
    ] {
        let mut p = protocol::Protocol::new("1234567890abcdef", 1000, &pins());
        let mut v = prepared();
        v[key] = if key == "uid" {
            999.into()
        } else {
            "bad".into()
        };
        assert!(push(&mut p, v).is_err(), "{key}");
        assert!(p.start().is_err());
    }
    let mut p = protocol::Protocol::new("1234567890abcdef", 1000, &pins());
    assert!(push(&mut p, tools()).is_err());
    push(&mut p, prepared()).unwrap();
    p.inspect().unwrap();
    let mut wrong = tools();
    wrong["files"]["waypipe"] = "e".repeat(64).into();
    assert!(push(&mut p, wrong).is_err());
    assert!(p.start().is_err());
    assert!(
        p.line(b"{\"type\":\"prepared\",\"type\":\"prepared\"}")
            .is_err()
    );
}
#[test]
fn socket_cannot_escape_private_nonce_home_or_use_termux() {
    let nonce = "1234567890abcdef";
    for path in [
        "/home/x/../x/.eidolon-linux/s-1234567890abcdef/p",
        "/home//x/.eidolon-linux/s-1234567890abcdef/p",
        "/data/data/com.termux/files/home/.eidolon-linux/s-1234567890abcdef/p",
        "/.eidolon-linux/s-1234567890abcdef/p",
        "/home/x/.eidolon-linux/s-1234567890abcdee/p",
    ] {
        assert!(!protocol::valid_socket(path, nonce));
    }
    assert!(protocol::valid_socket(
        "/home/x/.eidolon-linux/s-1234567890abcdef/p",
        nonce
    ));
}
#[test]
fn receipt_is_not_success_until_channel_eof_and_exit_and_exact_pattern() {
    let mut p = running();
    push(&mut p, finished()).unwrap();
    assert!(!p.cleaned() && !p.pattern_pass());
    assert!(p.finish(0).is_err());
    p.report.eof = true;
    p.report.exit_status = Some(0);
    assert!(p.finish(1).is_err());
    p.finish(0).unwrap();
    assert!(p.cleaned() && p.pattern_pass());
    assert!(push(&mut p, finished()).is_err());
    for key in ["frames", "callbacks", "releases"] {
        let mut p = running();
        let mut f = finished();
        f["pattern"][key] = 99.into();
        assert!(push(&mut p, f).is_err());
    }
    let mut p = running();
    p.stop();
    push(&mut p, finished()).unwrap();
    p.report.eof = true;
    p.report.exit_status = Some(0);
    p.finish(0).unwrap();
    assert!(p.cleaned() && !p.pattern_pass());
}
#[test]
fn separate_journal_never_replaces_other_profile_or_termux_state() {
    let temp = Temp::new();
    let p = pins();
    let socket = temp.0.join("p");
    let r = request(&temp.0, &socket, &p);
    fs::write(temp.0.join("transport-pending.json"), b"old termux journal").unwrap();
    let mut j = journal::Journal::reserve(&r).unwrap();
    let path = temp.0.join("linux-probe-state/pending.json");
    let bytes = fs::read(&path).unwrap();
    let mut other = request(&temp.0, &socket, &p);
    other.profile_binding = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    assert!(matches!(
        journal::Journal::reserve(&other),
        Err("LINUX_CLEANUP_PENDING")
    ));
    assert_eq!(fs::read(&path).unwrap(), bytes);
    j.clear().unwrap();
    assert!(!path.exists());
    assert_eq!(
        fs::read(temp.0.join("transport-pending.json")).unwrap(),
        b"old termux journal"
    );
}
#[test]
fn changed_journal_is_not_removed_and_bad_directory_is_rejected() {
    let temp = Temp::new();
    let p = pins();
    let socket = temp.0.join("p");
    let r = request(&temp.0, &socket, &p);
    let mut j = journal::Journal::reserve(&r).unwrap();
    let path = temp.0.join("linux-probe-state/pending.json");
    fs::write(&path, b"different owner").unwrap();
    assert!(j.clear().is_err());
    assert!(path.exists());
    fs::set_permissions(
        temp.0.join("linux-probe-state"),
        fs::Permissions::from_mode(0o777),
    )
    .unwrap();
    assert!(matches!(
        journal::Journal::reserve(&r),
        Err("LINUX_JOURNAL_INVALID")
    ));
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cancellation_before_exec_has_no_journal_or_forward() {
    let temp = Temp::new();
    let p = pins();
    let socket = temp.0.join("p");
    let _listener = UnixListener::bind(&socket).unwrap();
    let ssh = crate::forward::openssh::OpenSsh::new().await;
    let cancel = CancellationToken::new();
    cancel.cancel();
    let report = run(
        &ssh.client,
        &ssh.router,
        request(&temp.0, &socket, &p),
        &cancel,
        |_| {},
    )
    .await;
    assert_eq!(report.error, Some("CANCELLED"));
    assert!(!report.journal_pending && !report.forward_requested);
    assert!(!temp.0.join("linux-probe-state").exists());
    ssh.close().await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn real_ssh_linux_helper_eof_and_cleanup_are_separate_from_pattern() {
    let ssh = crate::forward::openssh::OpenSsh::new().await;
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scripts/linux/eidolon-linux-session.py")
        .canonicalize()
        .unwrap();
    let nonce = "1234567890abcdef";
    let code = format!(
        "import runpy,json;from pathlib import Path;m=runpy.run_path({});m['serve'](Path({}),{},0,lambda v:print(json.dumps(v,separators=(',',':')),flush=True),maximum_seconds=10)",
        serde_json::to_string(path.to_str().unwrap()).unwrap(),
        serde_json::to_string(ssh.dir.to_str().unwrap()).unwrap(),
        serde_json::to_string(nonce).unwrap()
    );
    let channel = ssh.client.channel_open_session().await.unwrap();
    channel
        .exec(
            true,
            format!(
                "/usr/bin/python3 -I -S -c '{}'",
                code.replace('\'', "'\\''")
            ),
        )
        .await
        .unwrap();
    let mut active = owner::Owner::from_channel(
        channel,
        nonce,
        unsafe { libc::getuid() },
        &pins(),
        CancellationToken::new(),
    );
    while !active.prepared() {
        active.next().await.unwrap();
    }
    assert!(
        active
            .remote_socket()
            .unwrap()
            .ends_with("/.eidolon-linux/s-1234567890abcdef/p")
    );
    active.stop().await.unwrap();
    while !active.closed() {
        active.next().await.unwrap();
    }
    assert!(active.cleaned());
    assert!(!active.pattern_pass());
    assert_eq!(active.report().reason.as_deref(), Some("Stop"));
    assert!(active.close().await);
    assert!(!ssh.dir.join(".eidolon-linux/s-1234567890abcdef").exists());
    assert!(
        ssh.dir
            .join(".eidolon-linux/r-1234567890abcdef.json")
            .exists()
    );
    let diagnostic = serde_json::to_string(&active.report()).unwrap();
    assert!(!diagnostic.contains(ssh.dir.to_str().unwrap()));
    ssh.close().await;
}

// helper制御・SSH/StreamLocal・回収の合成fixture。SHM本文の実検証はB3aと後続AppSessionで行う。
async fn synthetic_helper(
    cancel_running: bool,
    wrong_pin: bool,
) -> crate::forward::openssh::OpenSsh {
    crate::forward::openssh::OpenSsh::with_forced_command(move |dir| {
        let path=Path::new(env!("CARGO_MANIFEST_DIR")).join("../../scripts/linux/eidolon-linux-session.py").canonicalize().unwrap();
        let remote=dir.join(".eidolon-linux/s-1234567890abcdef/p");
        let pattern=serde_json::json!({"schema":1,"result":"pass","uid":unsafe{libc::getuid()},"fixture_revision":"eidolon-shm-client-v1","protocol":{"frames":100,"configures":1,"acks":1,"releases":100,"callbacks":100,"protocol_error":false}});
        let child=format!("import socket,time,json\ns=socket.socket(socket.AF_UNIX);s.connect({})\ns.sendall(b'F'*65536)\nb=b''\nwhile len(b)<65536:\n r=s.recv(65536-len(b))\n if not r:raise RuntimeError('SHORT_REPLY')\n b+=r\nassert b==b'F'*65536\ns.close()\ntime.sleep({})\nprint({},flush=True)\n",serde_json::to_string(remote.to_str().unwrap()).unwrap(),if cancel_running {10}else{0},serde_json::to_string(&pattern.to_string()).unwrap());
        let tool=serde_json::json!({"waypipe_version":"0.11.3","fixture_revision":"eidolon-shm-client-v1","frames":100,"bundle_sha256":if wrong_pin {"f".repeat(64)}else{"b".repeat(64)},"files":{"eidolon-linux-session.py":pins().helper_sha256(),"waypipe":"c".repeat(64),"eidolon-shm-fixture":"d".repeat(64)}});
        let source=format!("import runpy,json,sys\nfrom pathlib import Path\nm=runpy.run_path({})\nm['serve'](Path({}),'1234567890abcdef',0,lambda v:print(json.dumps(v,separators=(',',':')),flush=True),maximum_seconds=15,inspect=lambda *_:json.loads({}),factory=lambda *_:[sys.executable,'-I','-S','-c',{}])\n",serde_json::to_string(path.to_str().unwrap()).unwrap(),serde_json::to_string(dir.to_str().unwrap()).unwrap(),serde_json::to_string(&tool.to_string()).unwrap(),serde_json::to_string(&child).unwrap());
        let entry=dir.join("entry.py");fs::write(&entry,source).unwrap();fs::set_permissions(&entry,fs::Permissions::from_mode(0o600)).unwrap();
        Some(format!("/usr/bin/python3 -I -S {}",entry.display()))
    }).await
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn synthetic_ssh_forward_lifecycle_cancels_route_and_clears_only_its_journal() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    for cancel_running in [false, true] {
        let temp = Temp::new();
        let path = temp.0.join("p");
        let listener = tokio::net::UnixListener::bind(&path).unwrap();
        let echo = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut data = vec![0; 65536];
            socket.read_exact(&mut data).await.unwrap();
            socket.write_all(&data).await.unwrap();
            socket.shutdown().await.unwrap();
        });
        let ssh = synthetic_helper(cancel_running, false).await;
        let p = pins();
        let mut r = request(&temp.0, &path, &p);
        r.uid = unsafe { libc::getuid() };
        let cancel = CancellationToken::new();
        let report = run(&ssh.client, &ssh.router, r, &cancel, |value| {
            if cancel_running && value.state == "running" {
                cancel.cancel();
            }
        })
        .await;
        if cancel_running {
            echo.abort();
            let _ = echo.await;
            assert_eq!(report.error, Some("CANCELLED"));
            assert!(!report.pattern_verified);
        } else {
            echo.await.unwrap();
            assert_eq!(report.error, None, "{report:?}");
            assert_eq!(report.state, "pass");
            assert_eq!(report.frames, 100);
            assert!(
                report.forwarding.remote_to_local >= 65536
                    && report.forwarding.local_to_remote >= 65536
            );
        }
        assert!(
            report.tools_verified
                && report.registered
                && report.forward_cancelled
                && report.remote_cleaned
                && report.channel_closed,
            "{report:?}"
        );
        assert!(!report.journal_pending);
        assert_eq!(report.forwarding.active, 0);
        assert!(!temp.0.join("linux-probe-state/pending.json").exists());
        ssh.close().await;
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn synthetic_wrong_tool_pin_never_forwards_and_stops_prepared_helper() {
    let temp = Temp::new();
    let path = temp.0.join("p");
    let _listener = UnixListener::bind(&path).unwrap();
    let ssh = synthetic_helper(false, true).await;
    let p = pins();
    let mut r = request(&temp.0, &path, &p);
    r.uid = unsafe { libc::getuid() };
    let report = run(
        &ssh.client,
        &ssh.router,
        r,
        &CancellationToken::new(),
        |_| {},
    )
    .await;
    assert_eq!(report.error, Some("LINUX_TOOLS_INVALID"));
    assert!(!report.forward_requested && !report.registered && !report.pattern_verified);
    assert!(report.remote_cleaned && report.channel_closed);
    assert!(!report.journal_pending);
    assert_eq!(report.forwarding.accepted, 0);
    ssh.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn preexisting_journal_is_reported_pending_without_remote_exec_or_replacement() {
    let temp = Temp::new();
    let path = temp.0.join("p");
    let _listener = UnixListener::bind(&path).unwrap();
    let ssh = synthetic_helper(false, false).await;
    let p = pins();
    let mut r = request(&temp.0, &path, &p);
    r.uid = unsafe { libc::getuid() };
    let _retained = journal::Journal::reserve(&r).unwrap();
    let file = temp.0.join("linux-probe-state/pending.json");
    let before = fs::read(&file).unwrap();
    let report = run(
        &ssh.client,
        &ssh.router,
        r,
        &CancellationToken::new(),
        |_| {},
    )
    .await;
    assert_eq!(report.error, Some("LINUX_CLEANUP_PENDING"));
    assert!(report.journal_pending);
    assert!(!report.forward_requested && !report.remote_cleaned);
    assert_eq!(fs::read(&file).unwrap(), before);
    assert!(!ssh.dir.join(".eidolon-linux").exists());
    ssh.close().await;
}

fn recovery<'a>(dir: &'a Path, pins: &'a Pins) -> Recovery<'a> {
    Recovery {
        session: SessionId::new(2).unwrap(),
        profile_binding: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        profile_revision: "9007199254740993",
        files: dir,
        nonce: "1234567890abcdef",
        uid: unsafe { libc::getuid() },
        pins,
    }
}
#[test]
fn cleanup_receipt_requires_exact_scope_and_resource_absence() {
    let nonce = "1234567890abcdef";
    let mut reply = frame("unused");
    reply.as_object_mut().unwrap().remove("type");
    reply["cleaned"] = true.into();
    reply["already_absent"] = true.into();
    reply["receipt"] = finished();
    let check =
        |v: &serde_json::Value| protocol::audit(v.to_string().as_bytes(), nonce, 1000, &pins());
    assert_eq!(check(&reply), Ok(true));
    for key in ["uid", "nonce", "revision", "backend"] {
        let mut v = reply.clone();
        v["receipt"][key] = if key == "uid" {
            999.into()
        } else {
            "wrong".into()
        };
        assert!(check(&v).is_err());
    }
    let mut v = reply.clone();
    v["receipt"] = serde_json::Value::Null;
    assert_eq!(check(&v), Err("LINUX_RECEIPT_MISSING"));
    for key in ["group_alive", "pipes_closed"] {
        let mut v = reply.clone();
        v["receipt"][key] = (key == "group_alive").into();
        assert_eq!(check(&v), Ok(false));
    }
    let mut pending = frame("unused");
    pending.as_object_mut().unwrap().remove("type");
    pending["cleaned"] = false.into();
    pending["pending"] = true.into();
    assert_eq!(check(&pending), Ok(false));
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn explicit_ssh_receipt_recovery_requires_saved_binding_and_does_not_signal_pid() {
    let ssh=crate::forward::openssh::OpenSsh::with_forced_command(|dir|{
        fs::DirBuilder::new().mode(0o700).create(dir.join(".eidolon-linux")).unwrap();
        let helper=Path::new(env!("CARGO_MANIFEST_DIR")).join("../../scripts/linux/eidolon-linux-session.py").canonicalize().unwrap();
        let entry=dir.join("audit.py");
        fs::write(&entry,format!("import runpy,json\nfrom pathlib import Path\nm=runpy.run_path({})\nh=Path({})\n(h/'audit_called').write_text('1')\nprint(json.dumps(m['cleanup'](h,'1234567890abcdef')),flush=True)\n",serde_json::to_string(helper.to_str().unwrap()).unwrap(),serde_json::to_string(dir.to_str().unwrap()).unwrap())).unwrap();fs::set_permissions(&entry,fs::Permissions::from_mode(0o600)).unwrap();
        Some(format!("/usr/bin/python3 -I -S {}",entry.display()))
    }).await;
    let temp = Temp::new();
    let p = pins();
    let socket = temp.0.join("p");
    let mut original = request(&temp.0, &socket, &p);
    original.uid = unsafe { libc::getuid() };
    let _journal = journal::Journal::reserve(&original).unwrap();
    let pending = temp.0.join("linux-probe-state/pending.json");
    let bytes = fs::read(&pending).unwrap();
    for field in ["binding", "revision", "nonce", "uid"] {
        let mut r = recovery(&temp.0, &p);
        match field {
            "binding" => {
                r.profile_binding =
                    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            }
            "revision" => r.profile_revision = "1",
            "nonce" => r.nonce = "aaaaaaaaaaaaaaaa",
            "uid" => r.uid += 1,
            _ => unreachable!(),
        };
        let result = recover(&ssh.client, r, &CancellationToken::new()).await;
        assert_eq!(result.error, Some("LINUX_CLEANUP_SCOPE_MISMATCH"));
        assert!(!result.queried && result.pending);
        assert!(!ssh.dir.join("audit_called").exists());
        assert_eq!(fs::read(&pending).unwrap(), bytes);
    }
    for field in ["bundle", "waypipe", "fixture"] {
        let mut changed = p.clone();
        match field {
            "bundle" => changed.bundle_sha256 = "f".repeat(64),
            "waypipe" => changed.waypipe_sha256 = "f".repeat(64),
            "fixture" => changed.fixture_sha256 = "f".repeat(64),
            _ => unreachable!(),
        };
        let wrong = recover(
            &ssh.client,
            recovery(&temp.0, &changed),
            &CancellationToken::new(),
        )
        .await;
        assert_eq!(wrong.error, Some("LINUX_CLEANUP_SCOPE_MISMATCH"));
        assert!(!wrong.queried && wrong.pending);
        assert!(!ssh.dir.join("audit_called").exists());
    }
    let missing = recover(
        &ssh.client,
        recovery(&temp.0, &p),
        &CancellationToken::new(),
    )
    .await;
    assert_eq!(missing.error, Some("LINUX_RECEIPT_MISSING"));
    assert!(missing.queried && missing.pending);
    assert_eq!(fs::read(&pending).unwrap(), bytes);
    let mut receipt = finished();
    receipt["uid"] = unsafe { libc::getuid() }.into();
    let receipt_path = ssh.dir.join(".eidolon-linux/r-1234567890abcdef.json");
    fs::write(&receipt_path, receipt.to_string()).unwrap();
    fs::set_permissions(&receipt_path, fs::Permissions::from_mode(0o600)).unwrap();
    let result = recover(
        &ssh.client,
        recovery(&temp.0, &p),
        &CancellationToken::new(),
    )
    .await;
    assert_eq!(result.error, None, "{result:?}");
    assert_eq!(result.origin_session, Some(SessionId::new(1).unwrap()));
    assert_eq!(result.session, SessionId::new(2).unwrap());
    assert!(result.receipt_verified && result.channel_closed && !result.pending);
    assert!(!pending.exists());
    assert!(receipt_path.exists());
    let absent = recover(
        &ssh.client,
        recovery(&temp.0, &p),
        &CancellationToken::new(),
    )
    .await;
    assert_eq!(absent.error, None);
    assert!(!absent.queried && !absent.pending && !absent.receipt_verified);
    ssh.close().await;
}
