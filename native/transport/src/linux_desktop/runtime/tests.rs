use super::*;
use crate::linux_desktop::{test_support::*, tests::actor_fixture};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[tokio::test]
async fn ssh_normal_run_connects_pinned_actor_forward_journal_stop_and_all_cleanup_proofs() {
    let dir = Temp::new();
    let binding = binding();
    let local = dir.0.join("local.sock");
    let listener = tokio::net::UnixListener::bind(&local).unwrap();
    let ssh = actor_fixture(&binding, "forward").await;
    let cancel = CancellationToken::new();
    let echo = async {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut bytes = [0u8; 14];
        stream.read_exact(&mut bytes).await.unwrap();
        assert_eq!(&bytes, b"normal-desktop");
        stream.write_all(&bytes).await.unwrap();
    };
    let execute = run(
        &ssh.client,
        &ssh.router,
        Request {
            binding: &binding,
            files: &dir.0,
            local_socket: &local,
        },
        &cancel,
        |report| {
            if report.tools_verified && !matches!(report.state, "stopping" | "stopped") {
                let record: serde_json::Value = serde_json::from_slice(
                    &std::fs::read(dir.0.join("linux-desktop-state/pending.json")).unwrap(),
                )
                .unwrap();
                assert_eq!(record["boot_id"], crate::linux_desktop::tests::BOOT_ID);
            }
            if report.started {
                cancel.cancel();
            }
        },
    );
    let (result, ()) = timeout(Duration::from_secs(20), async {
        tokio::join!(execute, echo)
    })
    .await
    .unwrap();
    assert_eq!(result.error, None);
    assert_eq!(result.state, "stopped");
    assert!(result.started && result.cancelled && result.tools_verified);
    assert!(result.forward_registered && result.forward_cancelled && result.router_drained);
    assert!(
        result.channel_complete
            && result.remote.process_complete
            && result.remote.pipes_eof
            && result.remote.remote_cleaned
            && result.remote.receipt_written
    );
    assert_eq!(result.forwarding.remote_to_local, 14);
    assert_eq!(result.forwarding.local_to_remote, 14);
    assert_eq!(result.forwarding.failed, 0);
    assert!(!result.journal_pending);
    assert!(!dir.0.join("linux-desktop-state/pending.json").exists());
    let commands: Vec<String> =
        serde_json::from_slice(&std::fs::read(ssh.dir.join("commands.json")).unwrap()).unwrap();
    assert_eq!(commands, ["inspect", "start", "stop"]);
    ssh.close().await;
}

#[tokio::test]
async fn ssh_lost_start_ack_retires_forwarding_but_preserves_journal_and_blocks_relaunch() {
    let dir = Temp::new();
    let binding = binding();
    let local = dir.0.join("local.sock");
    let _listener = tokio::net::UnixListener::bind(&local).unwrap();
    let ssh = actor_fixture(&binding, "lost_start").await;
    let result = run(
        &ssh.client,
        &ssh.router,
        Request {
            binding: &binding,
            files: &dir.0,
            local_socket: &local,
        },
        &CancellationToken::new(),
        |_| {},
    )
    .await;
    assert!(result.start_requested && !result.started && result.journal_pending);
    assert_eq!(result.state, "cleanup_pending");
    assert!(result.forward_cancelled && result.router_drained);
    assert!(!result.remote.process_complete);
    let pending = std::fs::read(dir.0.join("linux-desktop-state/pending.json")).unwrap();
    let retry = run(
        &ssh.client,
        &ssh.router,
        Request {
            binding: &binding,
            files: &dir.0,
            local_socket: &local,
        },
        &CancellationToken::new(),
        |_| {},
    )
    .await;
    assert_eq!(retry.error, Some("DESKTOP_CLEANUP_PENDING"));
    assert!(retry.journal_pending);
    assert!(!retry.start_requested);
    assert_eq!(
        std::fs::read(dir.0.join("linux-desktop-state/pending.json")).unwrap(),
        pending
    );
    let commands: Vec<String> =
        serde_json::from_slice(&std::fs::read(ssh.dir.join("commands.json")).unwrap()).unwrap();
    assert_eq!(commands, ["inspect", "start"]);
    ssh.close().await;
}

#[tokio::test]
async fn ssh_immediate_exit_keeps_started_ack_even_when_events_share_a_chunk() {
    let dir = Temp::new();
    let binding = binding();
    let local = dir.0.join("local.sock");
    let _listener = tokio::net::UnixListener::bind(&local).unwrap();
    let ssh = actor_fixture(&binding, "immediate_exit").await;
    let result = timeout(
        Duration::from_secs(15),
        run(
            &ssh.client,
            &ssh.router,
            Request {
                binding: &binding,
                files: &dir.0,
                local_socket: &local,
            },
            &CancellationToken::new(),
            |report| {
                if report.remote.terminal_event {
                    assert_ne!(report.state, "running");
                }
            },
        ),
    )
    .await
    .unwrap();
    assert_eq!(result.error, None);
    assert!(result.started && result.remote.started);
    assert!(!result.journal_pending);
    assert!(result.channel_complete);
    ssh.close().await;
}
