//! I6 Host driver: normal Registry/Runtime/Controller, real SSH and unmodified C2.
//! A successful process exit is a smoke result; it never claims all-I6 acceptance.
mod host_n02;
mod host_n03;
mod host_n04;
mod host_n05;
mod host_n06;
mod host_n07;
mod host_n07_b2;
mod host_sdk;
use eidolon_core::runtime::Runtime;
use eidolon_model::{
    connection_profile::Profile,
    linux_desktop_v3::{
        self as b1, StorageCapture, StorageRecord,
        configuration::{ConnectionConfig, RuntimeBinding},
    },
    state::Status,
};
use eidolon_native::registry::Registry;
use eidolon_transport::{credentials::Secret, host_acceptance};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::{
        fd::FromRawFd,
        unix::fs::{MetadataExt, OpenOptionsExt},
    },
    path::{Path, PathBuf},
    thread,
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, &'static str>;
const MAX_RECEIPT: usize = 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Descriptor {
    schema: u32,
    run_id: String,
    installation: String,
    installation_id: String,
    profile_storage_id: String,
    profile: Value,
    configuration: Value,
    files_dir: String,
    native_library_dir: String,
}

fn digest(bytes: impl AsRef<[u8]>) -> String {
    Sha256::digest(bytes.as_ref())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}
fn hex32(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
fn read_private(path: &Path, limit: usize) -> Result<Vec<u8>> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|_| "I6_INPUT_INVALID")?;
    let metadata = file.metadata().map_err(|_| "I6_INPUT_INVALID")?;
    if !metadata.is_file()
        || metadata.nlink() != 1
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.mode() & 0o077 != 0
        || metadata.len() > limit as u64
    {
        return Err("I6_INPUT_INVALID");
    }
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "I6_INPUT_INVALID")?;
    let after = file.metadata().map_err(|_| "I6_INPUT_INVALID")?;
    if bytes.len() > limit
        || metadata.ino() != after.ino()
        || metadata.len() != after.len()
        || metadata.mtime_nsec() != after.mtime_nsec()
        || metadata.ctime_nsec() != after.ctime_nsec()
    {
        return Err("I6_INPUT_INVALID");
    }
    Ok(bytes)
}
fn parse_descriptor(bytes: &[u8]) -> Result<Descriptor> {
    let raw = b1::parse_json(bytes, 65536).map_err(|_| "I6_INPUT_INVALID")?;
    let descriptor: Descriptor = serde_json::from_value(raw).map_err(|_| "I6_INPUT_INVALID")?;
    if descriptor.schema != 1
        || !hex32(&descriptor.run_id)
        || !hex32(&descriptor.installation)
        || !hex32(&descriptor.installation_id)
        || !hex32(&descriptor.profile_storage_id)
    {
        return Err("I6_INPUT_INVALID");
    }
    for directory in [&descriptor.files_dir, &descriptor.native_library_dir] {
        let metadata = std::fs::symlink_metadata(directory).map_err(|_| "I6_INPUT_INVALID")?;
        if !Path::new(directory).is_absolute()
            || !metadata.is_dir()
            || metadata.uid() != unsafe { libc::geteuid() }
            || metadata.mode() & 0o077 != 0
        {
            return Err("I6_INPUT_INVALID");
        }
    }
    Ok(descriptor)
}
fn runtime_config(descriptor: &Descriptor) -> Result<String> {
    let profile_json =
        serde_json::to_string(&descriptor.profile).map_err(|_| "I6_INPUT_INVALID")?;
    let profile =
        Profile::parse(&profile_json, &descriptor.installation).map_err(|_| "I6_INPUT_INVALID")?;
    if profile.profile_id() != "remote-linux" || profile.endpoint().0 != "127.0.0.1" {
        return Err("I6_INPUT_INVALID");
    }
    let capture = StorageCapture::from_profile(&profile).map_err(|_| "I6_INPUT_INVALID")?;
    let mut storage = capture.record();
    storage["schema"] = json!(1);
    storage["installation_id"] = json!(descriptor.installation_id);
    storage["profile_storage_id"] = json!(descriptor.profile_storage_id);
    let storage = StorageRecord::parse(&serde_json::to_vec(&storage).unwrap())
        .map_err(|_| "I6_INPUT_INVALID")?;
    storage
        .matches_capture(&capture)
        .map_err(|_| "I6_INPUT_INVALID")?;
    let configuration =
        ConnectionConfig::parse(&serde_json::to_vec(&descriptor.configuration).unwrap())
            .map_err(|_| "I6_INPUT_INVALID")?;
    let binding = RuntimeBinding::parse(
        &serde_json::to_vec(
            &json!({"configuration":configuration.record(),"storage":storage.record()}),
        )
        .unwrap(),
    )
    .map_err(|_| "I6_INPUT_INVALID")?;
    binding
        .matches_profile(&profile)
        .map_err(|_| "I6_INPUT_INVALID")?;
    let config = json!({
        "schema":1,"installation":descriptor.installation,
        "paths":{"files_dir":descriptor.files_dir,"native_library_dir":descriptor.native_library_dir},
        "linux_profile_v2":profile_json,"linux_desktop_v3":String::from_utf8(binding.canonical()).unwrap()
    }).to_string();
    eidolon_model::commands::RuntimeConfig::parse(&config).map_err(|_| "I6_INPUT_INVALID")?;
    Ok(config)
}

fn snapshot_fields(value: &Value, fields: &[&str]) -> Result<Value> {
    let mut selected = serde_json::Map::new();
    for field in fields {
        selected.insert(
            (*field).to_owned(),
            value.get(*field).ok_or("I6_RECEIPT_INVALID")?.clone(),
        );
    }
    Ok(Value::Object(selected))
}

fn census_snapshot(snapshot: &Value) -> Result<Value> {
    // Ordinary Registry snapshots also contain UI geometry (f32/f64), which
    // is outside B1's integer-only evidence contract. Copy only census facts,
    // retaining their actual values and types; never round or invent zeroes.
    let mut census = snapshot_fields(snapshot, &["schema", "code", "handle", "registry"])?;
    let runtime = snapshot.get("runtime").ok_or("I6_RECEIPT_INVALID")?;
    let mut value = snapshot_fields(
        runtime,
        &[
            "state",
            "shutdown",
            "reclamation",
            "commands_pending",
            "app_sdk_bindings",
            "helper_workers",
            "dependency_workers",
            "compositor_workers",
            "app",
            "desktop_v3_pending_targets",
            "desktop_v3_finalizer_pending",
            "desktop_v3_cleanup_error",
            "panels",
            "error",
        ],
    )?;
    // N01 has no app; N02 retains the stopped Session's diagnostic record.
    // Project every live-resource field without copying UI float geometry.
    if runtime["app"].is_object() {
        value["app"] = snapshot_fields(
            &runtime["app"],
            &[
                "state",
                "ready",
                "clients",
                "objects",
                "surfaces",
                "pixel_bytes",
                "pool_bytes",
                "pool_count",
                "windows",
                "local",
                "directory_cleaned",
                "memfd_fds_after",
                "memfd_maps_after",
                "error",
            ],
        )?;
    }
    value["render"] = snapshot_fields(
        &runtime["render"],
        &[
            "workers",
            "targets",
            "contexts",
            "latest_images",
            "queued",
            "owner_tid",
            "composition",
            "error",
        ],
    )?;
    value["transport"] = snapshot_fields(
        &runtime["transport"],
        &[
            "state",
            "session",
            "workers",
            "tokio_threads",
            "owned_connections",
            "desktop_pending",
            "pending_cleanup",
            "error",
        ],
    )?;
    value["transport"]["forward"] = snapshot_fields(&runtime["transport"]["forward"], &["active"])?;
    census["runtime"] = value;
    Ok(census)
}

struct Driver {
    registry: Registry,
    handle: i64,
    secret: Secret,
    supplied: Vec<(String, String)>,
    completed: BTreeMap<String, Value>,
    // N03 only: a specific in-flight request may be rejected by the gate.
    n03_expected: BTreeMap<String, String>,
    observations: Vec<Value>,
    trace: host_acceptance::Snapshot,
    catalog: Value,
    last_snapshot: Value,
    destroy_status: Option<i32>,
    // Only populated by explicit N02. N01 uses the unchanged receipt and path.
    n02_sdk: Option<host_sdk::HostSdkAdapter>,
    n02_readback: Option<Value>,
    n02_ready: Option<Value>,
    n02_input: Option<Value>,
    n04: Option<Value>,
    n04_active: bool,
    n04_stop_monitoring: bool,
    n04_stop_sent_ns: Option<u64>,
    n04_stop_ack_ns: Option<u64>,
    n05_active: bool,
    n05: Option<Value>,
    n06: Option<Value>,
    n07_probe: Option<Value>,
}
impl Driver {
    fn new(config: &str, secret: Secret) -> Result<Self> {
        let registry = Registry::default();
        let handle = registry
            .create(config)
            .map_err(|_| "I6_REGISTRY_CREATE_FAILED")?;
        let mut driver = Self {
            registry,
            handle,
            secret,
            supplied: vec![],
            completed: BTreeMap::new(),
            n03_expected: BTreeMap::new(),
            observations: vec![],
            trace: host_acceptance::Snapshot {
                overflow: false,
                events: vec![],
            },
            catalog: Value::Null,
            last_snapshot: Value::Null,
            destroy_status: None,
            n02_sdk: None,
            n02_readback: None,
            n02_ready: None,
            n02_input: None,
            n04: None,
            n04_active: false,
            n04_stop_monitoring: false,
            n04_stop_sent_ns: None,
            n04_stop_ack_ns: None,
            n05_active: false,
            n05: None,
            n06: None,
            n07_probe: None,
        };
        driver.observe(
            "registry_created",
            json!({"status":0,"handle":handle.to_string()}),
        )?;
        Ok(driver)
    }
    fn refresh_trace(&mut self) -> Result<()> {
        self.trace = self
            .registry
            .guarded(self.handle, Runtime::host_acceptance_trace)
            .map_err(|_| "I6_RUNTIME_STALE")?;
        if self.trace.overflow {
            return Err("I6_TRACE_OVERFLOW");
        }
        Ok(())
    }
    fn observe(&mut self, kind: &str, data: Value) -> Result<()> {
        self.refresh_trace()?;
        self.append_observation(kind, data)
    }
    fn append_observation(&mut self, kind: &str, data: Value) -> Result<()> {
        if self.observations.len() >= 128 {
            return Err("I6_TRACE_OVERFLOW");
        }
        let watermark = self
            .trace
            .events
            .last()
            .map_or("0", |event| event.sequence.as_str());
        self.observations.push(json!({"sequence":(self.observations.len()+1).to_string(),"kind":kind,"transport_sequence":watermark,"data":data}));
        Ok(())
    }
    fn pump(&mut self) -> Result<Value> {
        let events = self.registry.poll(self.handle, 64);
        if events["code"] != 0 {
            return Err("I6_RUNTIME_STALE");
        }
        for event in events["events"].as_array().ok_or("I6_RUNTIME_STALE")? {
            if event["type"] == "command_complete" {
                let request = event["request_id"]
                    .as_str()
                    .ok_or("I6_RUNTIME_STALE")?
                    .to_owned();
                self.completed
                    .insert(request.clone(), event["code"].clone());
                // N05 sends hundreds of real physical key events. Preserve
                // every completion status and delivery, but do not fill the
                // bounded evidence envelope with one observation per key.
                if !(self.n05_active && request.parse::<u32>().is_ok_and(|n| n >= 5000)) {
                    self.observe(
                        "command_complete",
                        json!({"request_id":request.clone(),"status":event["code"]}),
                    )?;
                }
                if event["code"] != "ACCEPTED"
                    && self
                        .n03_expected
                        .get(&request)
                        .is_none_or(|status| event["code"].as_str() != Some(status.as_str()))
                {
                    return Err("I6_COMMAND_REJECTED");
                }
            }
        }
        let scope = self
            .registry
            .guarded(self.handle, Runtime::credential_scope)
            .map_err(|_| "I6_RUNTIME_STALE")?
            .map_err(|_| "I6_CREDENTIAL_FAILED")?;
        if let Some(scope) = scope {
            let key = (
                scope.session.get().to_string(),
                scope.request.get().to_string(),
            );
            if !self.supplied.contains(&key) {
                let status = self
                    .registry
                    .guarded(self.handle, |runtime| {
                        runtime.provide_credential_for(
                            scope.session,
                            scope.request,
                            &scope.binding,
                            Secret::new(self.secret.to_vec()),
                        )
                    })
                    .map_err(|_| "I6_CREDENTIAL_FAILED")?;
                self.observe(
                    "credential_provided",
                    json!({"session":key.0,"request_id":key.1,"status":status as i32}),
                )?;
                if status != Status::Accepted {
                    return Err("I6_CREDENTIAL_FAILED");
                }
                self.supplied.push(key);
            }
        }
        let snapshot = self.registry.snapshot(self.handle);
        if snapshot["code"] != 0 {
            return Err("I6_RUNTIME_STALE");
        }
        self.last_snapshot = census_snapshot(&snapshot)?;
        if self.n04_active && self.n04_stop_monitoring {
            // Observed clock timestamps cannot replace the original protocol
            // and cleanup deadlines. The independent oracle validates the wire.
            self.refresh_trace()?;
            let send = self
                .trace
                .events
                .iter()
                .find(|event| {
                    event.kind == "control_send_attempt" && event.data["action"] == "stop"
                })
                .map(|e| e.sequence.clone());
            if let Some(send) = send
                && self.n04_stop_sent_ns.is_none()
            {
                let ns = host_n04::clock_ns()?;
                self.n04_stop_sent_ns = Some(ns);
                self.append_observation(
                    "n04_stop_send_seen",
                    json!({"ns":ns, "transport_sequence":send}),
                )?;
            }
            let stopped = self
                .trace
                .events
                .iter()
                .find(|event| {
                    event.kind == "protocol_event"
                        && event.data["record"]["event"]["type"] == "stopped"
                })
                .map(|e| e.sequence.clone());
            if let Some(stopped) = stopped
                && self.n04_stop_sent_ns.is_some()
                && self.n04_stop_ack_ns.is_none()
            {
                let ns = host_n04::clock_ns()?;
                self.n04_stop_ack_ns = Some(ns);
                self.append_observation(
                    "n04_stop_ack_seen",
                    json!({"ns":ns,"transport_sequence":stopped}),
                )?;
            }
        }
        Ok(snapshot)
    }
    fn wait(&mut self, budget: u64, ready: impl Fn(&Value) -> bool) -> Result<Value> {
        let deadline = Instant::now() + Duration::from_secs(budget);
        loop {
            let snapshot = self.pump()?;
            if ready(&snapshot) {
                return Ok(snapshot);
            }
            if Instant::now() >= deadline {
                return Err("I6_OUTER_TIMEOUT");
            }
            thread::sleep(Duration::from_millis(10));
        }
    }
    fn command(&mut self, command: &str, request: i64, session: Option<&str>) -> Result<()> {
        let mut value = json!({"type":command,"schema":1,"request_id":request.to_string()});
        if command == "connect" {
            value["profile_id"] = json!("remote-linux");
        }
        if let Some(session) = session {
            value["session"] = json!(session);
        }
        let status = self.registry.command(self.handle, &value.to_string());
        self.observe(
            "command_accepted",
            json!({"command":command,"request_id":request.to_string(),"status":status as i32}),
        )?;
        if status != Status::Accepted {
            return Err("I6_COMMAND_REJECTED");
        }
        Ok(())
    }
    fn connect(&mut self, request: i64, recovering: bool) -> Result<String> {
        self.command("connect", request, None)?;
        let snapshot = self.wait(30, |snapshot| {
            snapshot["runtime"]["transport"]["state"] == "ProbeReady"
                || recovering
                    && snapshot["runtime"]["reclamation"]["recovery_command_allowed"] == true
        })?;
        snapshot["runtime"]["transport"]["session"]
            .as_str()
            .map(str::to_owned)
            .ok_or("I6_RUNTIME_STALE")
    }
    fn exercise(&mut self) -> Result<()> {
        self.wait(15, |snapshot| snapshot["runtime"]["state"] == "Ready")?;
        self.observe("runtime_ready", json!({}))?;
        let session = self.connect(1, false)?;
        self.command("linux_desktop_v3_inspect", 2, Some(&session))?;
        self.wait(45, |snapshot| {
            snapshot["runtime"]["transport"]["state"] == "DesktopReady"
        })?;
        self.catalog = self.registry.catalog_ui(self.handle);
        if self.catalog["code"] != 0 || self.catalog["catalog"]["desktop_v3"].is_null() {
            return Err("I6_CATALOG_MISSING");
        }
        self.observe("catalog_observed", json!({}))?;
        self.command("disconnect", 3, Some(&session))?;
        let snapshot = self.wait(60, |snapshot| {
            snapshot["runtime"]["reclamation"]["origin_retired"] == true
                || snapshot["runtime"]["reclamation"]["recovery_connect_allowed"] == true
        })?;
        if snapshot["runtime"]["reclamation"]["recovery_connect_allowed"] == true {
            let recovery_session = self.connect(4, true)?;
            self.command("linux_desktop_v3_recover", 5, Some(&recovery_session))?;
        }
        let snapshot = self.wait(60, |snapshot| {
            snapshot["runtime"]["reclamation"]["origin_retired"] == true
        })?;
        self.refresh_trace()?;
        if !self
            .trace
            .events
            .iter()
            .any(|event| event.kind == "finalizer_published")
        {
            return Err("I6_FINALIZER_MISSING");
        }
        self.observe(
            "origin_retired",
            json!({"reclamation":snapshot["runtime"]["reclamation"]}),
        )?;
        Ok(())
    }
    fn shutdown(&mut self) -> Result<()> {
        let status = self.registry.request_stop(self.handle);
        self.observe("runtime_stop_requested", json!({"status":status as i32}))?;
        if status != Status::Accepted {
            return Err("I6_COMMAND_REJECTED");
        }
        let snapshot = self.wait(15, |snapshot| {
            snapshot["runtime"]["shutdown"]["complete"] == true
                && snapshot["runtime"]["reclamation"]["workers"] == 0
                && snapshot["runtime"]["state"] == "Stopped"
        })?;
        self.observe("runtime_shutdown_complete",json!({"shutdown":snapshot["runtime"]["shutdown"],"reclamation":snapshot["runtime"]["reclamation"]}))?;
        let snapshot = self.pump()?;
        self.observe(
            "last_live_census",
            json!({"snapshot":census_snapshot(&snapshot)?}),
        )?;
        let status = self.registry.destroy(self.handle);
        self.destroy_status = Some(status as i32);
        self.append_observation(
            "registry_destroy_return",
            json!({"status":status as i32,"counts":self.registry.counts()}),
        )?;
        if status != Status::Accepted {
            return Err("I6_DESTROY_REJECTED");
        }
        Ok(())
    }
    fn retire_after_failure(&mut self) -> Result<()> {
        let snapshot = self.pump()?;
        if snapshot["runtime"]["reclamation"]["origin_retired"] == true {
            self.refresh_trace()?;
            let captured = self
                .trace
                .events
                .iter()
                .any(|event| event.kind == "capture_created");
            if captured
                && !self
                    .trace
                    .events
                    .iter()
                    .any(|event| event.kind == "finalizer_published")
            {
                return Err("I6_FINALIZER_MISSING");
            }
            self.observe(
                "origin_retired",
                json!({"reclamation":snapshot["runtime"]["reclamation"]}),
            )?;
            return Ok(());
        }
        if snapshot["runtime"]["reclamation"]["recovery_connect_allowed"] != true
            && let Some(session) = snapshot["runtime"]["transport"]["session"].as_str()
        {
            self.command("disconnect", 10, Some(session))?;
        }
        let snapshot = self.wait(60, |snapshot| {
            snapshot["runtime"]["reclamation"]["origin_retired"] == true
                || snapshot["runtime"]["reclamation"]["recovery_connect_allowed"] == true
        })?;
        if snapshot["runtime"]["reclamation"]["recovery_connect_allowed"] == true {
            let session = self.connect(11, true)?;
            self.command("linux_desktop_v3_recover", 12, Some(&session))?;
        }
        let snapshot = self.wait(60, |snapshot| {
            snapshot["runtime"]["reclamation"]["origin_retired"] == true
        })?;
        self.refresh_trace()?;
        if self
            .trace
            .events
            .iter()
            .any(|event| event.kind == "capture_created")
            && !self
                .trace
                .events
                .iter()
                .any(|event| event.kind == "finalizer_published")
        {
            return Err("I6_FINALIZER_MISSING");
        }
        self.observe(
            "origin_retired",
            json!({"reclamation":snapshot["runtime"]["reclamation"]}),
        )?;
        Ok(())
    }
    fn receipt(&self, run_id: &str, error: Option<&str>, cleanup_error: Option<&str>) -> Value {
        let build_source: Value =
            serde_json::from_str(env!("EIDOLON_I6_BUILD_SOURCE")).expect("compiled source");
        let capture = self
            .trace
            .events
            .iter()
            .find_map(|event| event.capture.as_ref());
        let owner_id = capture.map(|capture| {
            digest(format!("{run_id}:{}:{}", self.handle, capture.capture_id))[..32].to_owned()
        });
        json!({
            "schema":"eidolon-i6-n01-native-v1","run_id":run_id,"runtime":self.handle.to_string(),"owner_id":owner_id,
            "source_sha256":build_source["tree_sha256"],"build_source":build_source,
            "inventory_sha256":digest(eidolon_transport::linux_desktop::v3::sources::inventory_bytes()),
            "compiled_source_hashes":eidolon_transport::linux_desktop::v3::sources::current_hashes(),
            "trace":self.trace,"catalog":self.catalog,"observations":self.observations,
            "start_count":self.trace.events.iter().filter(|event|event.kind=="control_send_attempt"&&event.data["action"]=="start").count(),
            "last_snapshot":self.last_snapshot,"destroy_status":self.destroy_status,
            "registry_counts":self.registry.counts(),"error":error,"cleanup_error":cleanup_error
        })
    }
}

fn write_receipt(path: &Path, value: &Value) -> Result<()> {
    let bytes = serde_json::to_vec(value).map_err(|_| "I6_RECEIPT_INVALID")?;
    if bytes.len() > MAX_RECEIPT {
        return Err("I6_TRACE_OVERFLOW");
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|_| "I6_RECEIPT_INVALID")?;
    file.write_all(&bytes)
        .and_then(|()| file.sync_all())
        .map_err(|_| "I6_RECEIPT_INVALID")?;
    File::open(path.parent().ok_or("I6_RECEIPT_INVALID")?)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| "I6_RECEIPT_INVALID")
}
fn run() -> Result<bool> {
    let mut descriptor = None;
    let mut credential = None;
    let mut output = None;
    let mut variant: Option<String> = None;
    let mut n03_variant: Option<String> = None;
    let mut n04_variant: Option<String> = None;
    let mut n05_variant: Option<String> = None;
    let mut n05_directory: Option<String> = None;
    let mut n06_variant: Option<String> = None;
    let mut n06_directory: Option<String> = None;
    let mut n07_probe_variant: Option<String> = None;
    let mut args = std::env::args().skip(1);
    while let Some(argument) = args.next() {
        let value = args.next().ok_or("I6_ARGUMENT_INVALID")?;
        match argument.as_str() {
            "--descriptor" if descriptor.is_none() => descriptor = Some(PathBuf::from(value)),
            "--credential-fd" if credential.is_none() => {
                credential = Some(value.parse::<i32>().map_err(|_| "I6_ARGUMENT_INVALID")?)
            }
            "--output" if output.is_none() => output = Some(PathBuf::from(value)),
            "--variant"
                if variant.is_none()
                    && n03_variant.is_none()
                    && n04_variant.is_none()
                    && n05_variant.is_none()
                    && n06_variant.is_none()
                    && n07_probe_variant.is_none() =>
            {
                if !host_n02::N02_VARIANTS.contains(&value.as_str()) {
                    return Err("I6_ARGUMENT_INVALID");
                }
                variant = Some(value);
            }
            "--n03-variant"
                if n03_variant.is_none()
                    && variant.is_none()
                    && n04_variant.is_none()
                    && n05_variant.is_none()
                    && n06_variant.is_none()
                    && n07_probe_variant.is_none() =>
            {
                if !host_n03::N03_VARIANTS.contains(&value.as_str()) {
                    return Err("I6_ARGUMENT_INVALID");
                }
                n03_variant = Some(value);
            }
            "--n04-variant"
                if n04_variant.is_none()
                    && variant.is_none()
                    && n03_variant.is_none()
                    && n05_variant.is_none()
                    && n06_variant.is_none()
                    && n07_probe_variant.is_none() =>
            {
                if value != host_n04::VARIANT {
                    return Err("I6_ARGUMENT_INVALID");
                }
                n04_variant = Some(value);
            }
            "--n05-variant"
                if n05_variant.is_none()
                    && variant.is_none()
                    && n03_variant.is_none()
                    && n04_variant.is_none()
                    && n06_variant.is_none()
                    && n07_probe_variant.is_none() =>
            {
                if value != host_n05::VARIANT {
                    return Err("I6_ARGUMENT_INVALID");
                }
                n05_variant = Some(value);
            }
            "--n05-directory" if n05_directory.is_none() => n05_directory = Some(value),
            "--n06-variant"
                if n06_variant.is_none()
                    && variant.is_none()
                    && n03_variant.is_none()
                    && n04_variant.is_none()
                    && n05_variant.is_none() =>
            {
                if !host_n06::VARIANTS.contains(&value.as_str()) {
                    return Err("I6_ARGUMENT_INVALID");
                }
                n06_variant = Some(value);
            }
            "--n06-directory" if n06_directory.is_none() => n06_directory = Some(value),
            "--n07-probe-variant"
                if n07_probe_variant.is_none()
                    && variant.is_none()
                    && n03_variant.is_none()
                    && n04_variant.is_none()
                    && n05_variant.is_none()
                    && n06_variant.is_none() =>
            {
                if value != host_n07::VARIANT && value != host_n07_b2::VARIANT {
                    return Err("I6_ARGUMENT_INVALID");
                }
                n07_probe_variant = Some(value);
            }
            _ => return Err("I6_ARGUMENT_INVALID"),
        }
    }
    if n06_variant.is_some() != n06_directory.is_some() {
        return Err("I6_ARGUMENT_INVALID");
    }
    if n05_variant.is_some() != n05_directory.is_some() {
        return Err("I6_ARGUMENT_INVALID");
    }
    if unsafe { libc::geteuid() } == 0 {
        return Err("I6_NONROOT_REQUIRED");
    }
    eprintln!("I6_RUNNER_STAGE descriptor");
    let descriptor = parse_descriptor(&read_private(
        &descriptor.ok_or("I6_ARGUMENT_INVALID")?,
        65536,
    )?)?;
    let fd = credential
        .filter(|fd| *fd >= 3)
        .ok_or("I6_ARGUMENT_INVALID")?;
    let mut key = Secret::new(vec![]);
    // The inherited private FD is consumed exactly once; no argv/env/file key data.
    unsafe { File::from_raw_fd(fd) }
        .take(eidolon_transport::credentials::KEY_BYTES as u64 + 1)
        .read_to_end(&mut key)
        .map_err(|_| "I6_CREDENTIAL_FAILED")?;
    eidolon_transport::credentials::decode(&key).map_err(|_| "I6_CREDENTIAL_FAILED")?;
    eprintln!("I6_RUNNER_STAGE configuration");
    let config = runtime_config(&descriptor)?;
    eprintln!("I6_RUNNER_STAGE registry_create");
    let mut driver = Driver::new(&config, key)?;
    eprintln!("I6_RUNNER_STAGE exercise");
    let first = if n07_probe_variant.as_deref() == Some(host_n07_b2::VARIANT) {
        driver.exercise_n07_b2_probe(&descriptor.run_id).err()
    } else if n07_probe_variant.is_some() {
        driver.exercise_n07_probe(&descriptor.run_id).err()
    } else if let Some(value) = n06_variant.as_deref() {
        driver
            .exercise_n06(
                &descriptor.run_id,
                n06_directory.as_deref().ok_or("I6_ARGUMENT_INVALID")?,
                value,
            )
            .err()
    } else {
        match (
            variant.as_deref(),
            n03_variant.as_deref(),
            n04_variant.as_deref(),
            n05_variant.as_deref(),
        ) {
            (Some(value), None, None, None) => driver.exercise_n02(value).err(),
            (None, Some(value), None, None) => driver.exercise_n03(value).err(),
            (None, None, Some(_), None) => driver.exercise_n04(&descriptor.run_id).err(),
            (None, None, None, Some(_)) => driver
                .exercise_n05(
                    &descriptor.run_id,
                    n05_directory.as_deref().ok_or("I6_ARGUMENT_INVALID")?,
                )
                .err(),
            (None, None, None, None) => driver.exercise().err(),
            _ => return Err("I6_ARGUMENT_INVALID"),
        }
    };
    let retirement = if n03_variant.is_some() {
        if first.is_none() {
            None // N03 completed real owner retirement in its exercise.
        } else if driver.n02_sdk.as_ref().is_some_and(|sdk| sdk.owns_lease()) {
            driver.retire_n02().err()
        } else {
            driver.retire_after_failure().err()
        }
    } else if variant.is_some()
        || n04_variant.is_some()
        || n05_variant.is_some()
        || n06_variant.is_some()
        || n07_probe_variant.is_some()
    {
        eprintln!("I6_RUNNER_STAGE n02_retire");
        // A failed launch without a native Create still needs the existing
        // same-capture recovery. The SDK ledger never invents an empty receipt.
        if driver.n02_sdk.as_ref().is_some_and(|sdk| sdk.owns_lease()) {
            driver.retire_n02().err()
        } else {
            driver.retire_after_failure().err()
        }
    } else if first.is_some() {
        eprintln!("I6_RUNNER_STAGE retire");
        driver.retire_after_failure().err()
    } else {
        None
    };
    let cleanup = retirement.or_else(|| {
        eprintln!("I6_RUNNER_STAGE shutdown");
        driver.shutdown().err()
    });
    eprintln!("I6_RUNNER_STAGE receipt");
    let receipt = if n07_probe_variant.as_deref() == Some(host_n07_b2::VARIANT) {
        driver.receipt_n07_b2_probe(&descriptor.run_id, first, cleanup)
    } else if n07_probe_variant.is_some() {
        driver.receipt_n07_probe(&descriptor.run_id, first, cleanup)
    } else if let Some(value) = n06_variant.as_deref() {
        driver.receipt_n06(&descriptor.run_id, value, first, cleanup)
    } else {
        match (
            variant.as_deref(),
            n03_variant.as_deref(),
            n04_variant.as_deref(),
            n05_variant.as_deref(),
        ) {
            (Some(value), None, None, None) => {
                driver.receipt_n02(&descriptor.run_id, value, first, cleanup)
            }
            (None, Some(value), None, None) => {
                driver.receipt_n03(&descriptor.run_id, value, first, cleanup)
            }
            (None, None, Some(_), None) => driver.receipt_n04(&descriptor.run_id, first, cleanup),
            (None, None, None, Some(_)) => driver.receipt_n05(&descriptor.run_id, first, cleanup),
            (None, None, None, None) => driver.receipt(&descriptor.run_id, first, cleanup),
            _ => return Err("I6_ARGUMENT_INVALID"),
        }
    };
    write_receipt(&output.ok_or("I6_ARGUMENT_INVALID")?, &receipt)?;
    if retirement.is_some() {
        // Product remains unresolved. Freeze that result before the external
        // supervisor's rescue; do not run Drop's Runtime-stop path as success.
        std::process::exit(2);
    }
    Ok(first.is_none() && cleanup.is_none())
}
fn main() {
    match run() {
        Ok(true) => (),
        Ok(false) => std::process::exit(2),
        Err(reason) => {
            eprintln!("{reason}");
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn strict_descriptor_rejects_duplicates_before_filesystem_or_runtime() {
        assert!(parse_descriptor(br#"{"schema":1,"schema":1}"#).is_err());
        assert!(parse_descriptor(br#"{"schema":true}"#).is_err());
    }
    #[test]
    fn ownership_id_is_bound_to_run_runtime_and_capture() {
        let owner =
            |run: &str, runtime: &str, capture: &str| digest(format!("{run}:{runtime}:{capture}"));
        assert_ne!(owner("a", "1", "c"), owner("b", "1", "c"));
        assert_ne!(owner("a", "1", "c"), owner("a", "2", "c"));
        assert_ne!(owner("a", "1", "c"), owner("a", "1", "d"));
    }
    #[test]
    fn census_projects_actual_snapshot_without_ui_floats_or_value_coercion() {
        let mut raw = json!({
            "schema":1,"code":0,"handle":"1",
            "registry":{"runtimes":1,"workers":1},
            "runtime":eidolon_core::diagnostics::Snapshot::default()
        });
        let bytes = serde_json::to_vec(&raw).unwrap();
        assert!(b1::parse_json(&bytes, MAX_RECEIPT).is_err());
        raw["runtime"]["transport"]["owned_connections"] = json!(2);
        let census = census_snapshot(&raw).unwrap();
        let bytes = serde_json::to_vec(&census).unwrap();
        assert!(b1::parse_json(&bytes, MAX_RECEIPT).is_ok());
        assert!(census["runtime"].get("workspace").is_none());
        assert!(census["runtime"]["render"].get("window_controls").is_none());
        assert_eq!(census["runtime"]["transport"]["owned_connections"], 2);
        assert_eq!(
            census["runtime"]["reclamation"],
            raw["runtime"]["reclamation"]
        );
        assert_eq!(census["runtime"]["shutdown"], raw["runtime"]["shutdown"]);
        // An unexpected retained panel cannot become an empty successful census.
        raw["runtime"]["panels"] = json!([{"position_m":[0.0,1.0,2.0]}]);
        let census = census_snapshot(&raw).unwrap();
        assert_eq!(census["runtime"]["panels"], raw["runtime"]["panels"]);
        assert!(b1::parse_json(&serde_json::to_vec(&census).unwrap(), MAX_RECEIPT).is_err());
        raw["runtime"].as_object_mut().unwrap().remove("error");
        assert_eq!(census_snapshot(&raw), Err("I6_RECEIPT_INVALID"));
    }
}
