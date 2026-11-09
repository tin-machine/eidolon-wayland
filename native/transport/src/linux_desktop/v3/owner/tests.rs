use super::test_support::*;
use super::*;
use std::io::Write;
use std::process::{Command, Stdio};

fn decoded_command(command: &str) -> String {
    command
        .strip_prefix("/usr/bin/python3 -I -S -c '")
        .unwrap()
        .strip_suffix('\'')
        .unwrap()
        .replace("'\\''", "'")
}
#[test]
fn typed_serve_and_recover_are_actual_fixed_bootstrap_arguments() {
    let binding = binding();
    let vectors = [
        Bootstrap::serve(binding.context().clone(), binding.pins().clone()),
        Bootstrap::recover(binding.context().clone(), binding.pins().clone(), BOOT).unwrap(),
    ];
    for vector in vectors {
        let text = command(&binding, &vector).unwrap();
        let script = decoded_command(&text);
        let mut child=Command::new("/usr/bin/python3").args(["-I","-S","-c",r#"import ast,json,sys
source=sys.stdin.read(); tree=ast.parse(source)
for node in tree.body:
 if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='argv' for t in node.targets):
  print(json.dumps(json.loads(ast.literal_eval(node.value.args[0]))))
"#]).stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
        child
            .stdin
            .take()
            .unwrap()
            .write_all(script.as_bytes())
            .unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(output.status.success());
        let actual: Vec<String> = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(actual, vector.vector());
        assert!(script.contains(".local/lib/eidolon-linux-desktop-v3"));
        assert!(script.contains("validator.validate_bundle"));
        assert!(!script.contains("validate_launch_package("));
    }
}
#[test]
fn bootstrap_rejects_well_formed_replaced_identity_or_pins_before_ssh() {
    let binding = binding();
    for id in [12, 13, 6, 7, 11] {
        let mut vector =
            Bootstrap::serve(binding.context().clone(), binding.pins().clone()).vector();
        vector[id] = if id == 12 || id == 13 {
            "d".repeat(32)
        } else {
            "e".repeat(64)
        };
        let changed = Bootstrap::parse(serde_json::to_string(&vector).unwrap().as_bytes()).unwrap();
        assert!(command(&binding, &changed).is_err());
    }
    assert!(
        Bootstrap::recover(
            binding.context().clone(),
            binding.pins().clone(),
            "';echo escaped"
        )
        .is_err()
    );
    let mut v = Bootstrap::serve(binding.context().clone(), binding.pins().clone()).vector();
    v.push("extra".into());
    assert!(Bootstrap::parse(serde_json::to_string(&v).unwrap().as_bytes()).is_err());
}
#[tokio::test]
async fn output_frame_incomplete_or_event_after_terminal_never_closes_cleanly() {
    let mut owner = Owner {
        channel: None,
        protocol: Protocol::new(binding(), evidence()).unwrap(),
        cancel: CancellationToken::new(),
        deadline: Some(Instant::now() + PREPARE_TIMEOUT),
        heartbeat: Instant::now() + HEARTBEAT_INTERVAL,
        stopping: false,
        frames: FrameDecoder::default(),
        boot_id: None,
        remote_socket: None,
        report: RemoteReport::default(),
    };
    assert!(owner.data(b"{").unwrap().is_empty());
    owner.report.eof = true;
    owner.report.channel_closed = true;
    owner.report.exit_status = Some(0);
    assert!(!owner.channel_complete());
    assert!(owner.data(b"}\n").is_err());
    assert!(!owner.report.protocol_valid);
}
