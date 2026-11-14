use eidolon_core::runtime::Runtime;
use eidolon_model::{
    commands::{Command, RuntimeConfig},
    ids::RuntimeHandle,
    state::Status,
};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex, OnceLock},
};

struct Entries {
    next: Option<i64>,
    runtimes: HashMap<i64, Arc<Runtime>>,
}
pub struct Registry(Mutex<Entries>);
impl Default for Registry {
    fn default() -> Self {
        Self(Mutex::new(Entries {
            next: Some(1),
            runtimes: HashMap::new(),
        }))
    }
}
impl Registry {
    pub fn create(&self, config: &str) -> Result<i64, Status> {
        let config = RuntimeConfig::parse(config)?;
        let mut entries = self.0.lock().unwrap_or_else(|e| e.into_inner());
        if !entries.runtimes.is_empty() {
            return Err(Status::Busy);
        }
        let id = entries.next.ok_or(Status::Failed)?;
        entries.next = id.checked_add(1);
        let runtime = Arc::new(
            Runtime::start_registered(config, RuntimeHandle::new(id).map_err(|_| Status::Failed)?)
                .map_err(|error| {
                    if error.kind() == std::io::ErrorKind::InvalidInput {
                        Status::InvalidArgument
                    } else {
                        Status::Failed
                    }
                })?,
        );
        entries.runtimes.insert(id, runtime);
        Ok(id)
    }
    fn get(&self, id: i64) -> Result<Arc<Runtime>, Status> {
        RuntimeHandle::new(id).map_err(|_| Status::InvalidArgument)?;
        self.0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .runtimes
            .get(&id)
            .cloned()
            .ok_or(Status::Stale)
    }
    pub fn guarded<T>(&self, id: i64, action: impl FnOnce(&Runtime) -> T) -> Result<T, Status> {
        let runtime = self.get(id)?;
        runtime.guarded(|| action(&runtime))
    }
    pub fn command(&self, id: i64, json: &str) -> Status {
        let command = match Command::parse(json) {
            Ok(c) => c,
            Err(s) => return s,
        };
        self.guarded(id, |runtime| runtime.command(command))
            .unwrap_or_else(|s| s)
    }
    pub fn request_stop(&self, id: i64) -> Status {
        self.guarded(id, Runtime::request_stop)
            .unwrap_or_else(|s| s)
    }
    pub fn destroy(&self, id: i64) -> Status {
        let runtime = match self.get(id) {
            Ok(runtime) => runtime,
            Err(s) => return s,
        };
        if !runtime.can_destroy() {
            return Status::Busy;
        }
        let removed = self
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .runtimes
            .remove(&id);
        if removed.is_some() {
            Status::Accepted
        } else {
            Status::Stale
        }
    }
    pub fn snapshot(&self, id: i64) -> Value {
        match self.guarded(id, Runtime::snapshot) {
            Ok(snapshot) => {
                json!({"schema":1,"code":0,"handle":id.to_string(),"runtime":snapshot,"registry":self.counts()})
            }
            Err(status) => json!({"schema":1,"code":status as i32,"registry":self.counts()}),
        }
    }
    pub fn poll(&self, id: i64, max: i32) -> Value {
        match self.guarded(id, |runtime| runtime.poll(max as usize)) {
            Ok(Ok(events)) => json!({"schema":1,"code":0,"events":events}),
            Ok(Err(s)) | Err(s) => json!({"schema":1,"code":s as i32,"events":[]}),
        }
    }
    pub fn catalog_ui(&self, id: i64) -> Value {
        match self.guarded(id, Runtime::catalog_ui) {
            Ok(catalog) => json!({"schema":1,"code":0,"catalog":catalog}),
            Err(status) => json!({"schema":1,"code":status as i32}),
        }
    }
    /// IME本文を含み得る専用取得。snapshot/pollの記録経路へ含めない。
    pub fn ime_ui(&self, id: i64) -> Value {
        match self.guarded(id, Runtime::ime_ui) {
            Ok(ime) => json!({"schema":1,"code":0,"ime":ime}),
            Err(status) => json!({"schema":1,"code":status as i32}),
        }
    }
    /// 接続ownerだけが読む。bindingはsnapshot/pollへ複製しない。
    pub fn credential_scope(&self, id: i64) -> Value {
        match self.guarded(id, Runtime::credential_scope) {
            Ok(Ok(scope)) => {
                json!({"schema":1,"code":0,"scope":scope.map(|s| json!({"session":s.session,"request_id":s.request,"profile_id":s.profile_id,"revision":s.revision,"binding":s.binding}))})
            }
            Ok(Err(status)) | Err(status) => json!({"schema":1,"code":status as i32,"scope":null}),
        }
    }
    pub fn counts(&self) -> Value {
        let entries = self.0.lock().unwrap_or_else(|e| e.into_inner());
        json!({"runtimes":entries.runtimes.len(),"workers":entries.runtimes.values().filter(|r| !r.worker_finished()).count()})
    }
    pub fn fail(&self, id: i64) {
        if id > 0 {
            if let Ok(runtime) = self.get(id) {
                runtime.fail();
            }
        } else {
            let runtimes: Vec<_> = self
                .0
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .runtimes
                .values()
                .cloned()
                .collect();
            for runtime in runtimes {
                runtime.fail();
            }
        }
    }
}

pub fn global() -> &'static Registry {
    static REGISTRY: OnceLock<Registry> = OnceLock::new();
    REGISTRY.get_or_init(Registry::default)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn invalid_public_pin_is_rejected_before_any_owner_is_started() {
        let registry = Registry::default();
        let config=json!({"schema":1,"installation":"a".repeat(32),"profiles":[{"schema":1,"profile_id":"termux-local","installation":"a".repeat(32),"username":"fixture","host_key":"invalid"}]}).to_string();
        assert_eq!(registry.create(&config), Err(Status::InvalidArgument));
        assert_eq!(registry.counts(), json!({"runtimes":0,"workers":0}));
    }
    use std::{
        thread,
        time::{Duration, Instant},
    };
    #[test]
    fn ten_cycles_never_reuse_handles_and_destroy_waits_for_stop() {
        let registry = Registry::default();
        let mut previous = 0;
        for _ in 0..10 {
            let id = registry.create(r#"{"schema":1}"#).unwrap();
            assert!(id > previous);
            assert_eq!(registry.destroy(id), Status::Busy);
            assert_eq!(registry.create(r#"{"schema":1}"#), Err(Status::Busy));
            assert_eq!(registry.request_stop(id), Status::Accepted);
            assert_eq!(registry.request_stop(id), Status::Accepted);
            let deadline = Instant::now() + Duration::from_secs(2);
            loop {
                if registry.destroy(id) == Status::Accepted {
                    break;
                }
                assert!(Instant::now() < deadline);
                thread::sleep(Duration::from_millis(1));
            }
            assert_eq!(registry.destroy(id), Status::Stale);
            assert_eq!(registry.request_stop(id), Status::Stale);
            assert_eq!(registry.poll(id, 64)["code"], 1);
            assert_eq!(registry.snapshot(id)["code"], 1);
            assert_eq!(registry.catalog_ui(id)["code"], 1);
            assert_eq!(registry.ime_ui(id)["code"], 1);
            assert_eq!(registry.counts(), json!({"runtimes":0,"workers":0}));
            previous = id;
        }
        assert_eq!(
            registry.create(r#"{"schema":1,"unknown":0}"#),
            Err(Status::InvalidArgument)
        );
        assert_eq!(registry.request_stop(0), Status::InvalidArgument);
    }
    #[test]
    fn storage_receipt_requires_the_last_live_snapshot_and_actual_destroy_result() {
        let registry = Registry::default();
        let id = registry.create(r#"{"schema":1}"#).unwrap();
        assert_eq!(registry.request_stop(id), Status::Accepted);
        let deadline = Instant::now() + Duration::from_secs(2);
        let last = loop {
            let snapshot = registry.snapshot(id);
            if snapshot["runtime"]["shutdown"]["complete"] == true
                && snapshot["runtime"]["reclamation"]["workers"] == 0
            {
                break snapshot;
            }
            assert!(Instant::now() < deadline);
            thread::sleep(Duration::from_millis(1));
        };
        assert_eq!(last["code"], 0);
        assert_eq!(last["handle"], id.to_string());
        assert_eq!(last["runtime"]["reclamation"]["runtime"], id.to_string());
        assert!(
            last["runtime"]["reclamation"]
                .get("native_destroy_accepted")
                .is_none()
        );
        assert_eq!(registry.destroy(id), Status::Accepted);
        assert_eq!(registry.destroy(id), Status::Stale);
        let stale = registry.snapshot(id);
        assert_eq!(stale["code"], Status::Stale as i32);
        assert!(stale.get("runtime").is_none());
        let next = registry.create(r#"{"schema":1}"#).unwrap();
        assert_ne!(next, id);
        assert_eq!(
            registry.snapshot(next)["runtime"]["reclamation"]["runtime"],
            next.to_string()
        );
        assert_eq!(registry.request_stop(next), Status::Accepted);
        let deadline = Instant::now() + Duration::from_secs(2);
        while registry.destroy(next) != Status::Accepted {
            assert!(Instant::now() < deadline);
            thread::sleep(Duration::from_millis(1));
        }
    }
    #[test]
    fn native_result_constants_match_kotlin() {
        let kotlin = include_str!(
            "../../../android/app/src/main/java/io/github/tinmachine/eidolon/NativeBridge.kt"
        );
        for (name, value) in [
            ("ACCEPTED", Status::Accepted),
            ("STALE", Status::Stale),
            ("BUSY", Status::Busy),
            ("INVALID_ARGUMENT", Status::InvalidArgument),
            ("NOT_READY", Status::NotReady),
            ("FAILED", Status::Failed),
        ] {
            assert!(kotlin.contains(&format!("const val {name} = {}", value as i32)));
        }
    }
}
