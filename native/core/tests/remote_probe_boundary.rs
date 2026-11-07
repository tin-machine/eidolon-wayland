//! feature無効の本libraryで検査する。test profileのdebug_assertions=trueでも拒否必須。
#[cfg(not(feature = "debug-fixtures"))]
#[test]
fn remote_probe_is_rejected_without_the_packaged_debug_feature() {
    use eidolon_core::runtime::Runtime;
    use eidolon_model::{
        commands::{Command, RuntimeConfig},
        state::Status,
    };
    let config = RuntimeConfig {
        installation: Some("1".repeat(32)),
        debug_probe_profile_v2: Some(profile_json()),
        ..Default::default()
    };
    assert!(config.validate().is_ok());
    assert!(Runtime::start(config).is_err());
    let runtime = Runtime::start(RuntimeConfig::default()).unwrap();
    assert_eq!(
        runtime.command(
            Command::parse(r#"{"schema":1,"type":"connect_saved_probe","request_id":"1"}"#)
                .unwrap()
        ),
        Status::InvalidArgument
    );
    assert!(matches!(
        runtime.credential_scope(),
        Err(Status::InvalidArgument)
    ));
    assert_eq!(
        runtime.provide_credential_for(
            eidolon_model::ids::SessionId::new(1).unwrap(),
            eidolon_model::ids::CommandSeq::new(1).unwrap(),
            &"0".repeat(64),
            vec![].into()
        ),
        Status::InvalidArgument
    );
    runtime.request_stop();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while !runtime.can_destroy() {
        assert!(std::time::Instant::now() < deadline);
        std::thread::sleep(std::time::Duration::from_millis(1));
    }
}
#[cfg(not(feature = "debug-fixtures"))]
fn profile_json() -> String {
    let key = include_str!("../../../android/app/src/test/resources/connection-profiles-v2.tsv")
        .lines()
        .find(|l| l.starts_with("linux-"))
        .unwrap()
        .split('\t')
        .nth(7)
        .unwrap();
    serde_json::json!({"schema":2,"installation":"1".repeat(32),"profile_id":"linux-probe","revision":"1","display_name":"fixture","backend":"linux_ssh","host":"192.0.2.1","port":22,"username":"fixture","host_key":key}).to_string()
}
