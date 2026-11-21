// A single Activity drives these futures on its scene thread. UUIDs returned to Kotlin
// go to noBackupFilesDir only. The evidence logger receives counts and status codes.
use super::*;
use std::sync::Mutex;

#[derive(Clone, Copy, PartialEq)]
enum Action {
    List,
    Save,
    Find,
    Erase,
}
#[derive(Clone, Copy)]
enum Phase {
    Store,
    Context,
    Snapshot,
    Persist,
    Unpersist,
}
struct Trial {
    api: Session,
    owner: String,
    action: Action,
    uuid: Option<xr::Uuid>,
    phase: Phase,
    future: xr::FutureEXT,
    store: xr::SpatialPersistenceContextEXT,
    context: xr::SpatialContextEXT,
    entity: xr::SpatialEntityEXT,
    space: xr::Space,
    snapshot: xr::SpatialSnapshotEXT,
}
static CURRENT: Mutex<Option<Trial>> = Mutex::new(None);

fn store_result(op: &str, result: xr::SpatialPersistenceContextResultEXT) -> Result<(), String> {
    if result == xr::SpatialPersistenceContextResultEXT::SUCCESS {
        Ok(())
    } else {
        Err(format!("{op}: {result:?}"))
    }
}
fn decode_uuid(hex: &str) -> Result<xr::Uuid, String> {
    let mut data = [0u8; 16];
    if hex.len() != 32 || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("invalid private UUID".into());
    }
    for (i, b) in data.iter_mut().enumerate() {
        *b = u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).map_err(|_| "uuid")?;
    }
    Ok(xr::Uuid { data })
}
fn encode_uuid(uuid: xr::Uuid) -> String {
    uuid.data.iter().map(|b| format!("{b:02x}")).collect()
}
fn now(api: &Session) -> Result<xr::Time, String> {
    let convert = load!(api, ConvertTimespecTimeToTimeKHR);
    let mut stamp = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut stamp) } != 0 {
        return Err("clock unavailable".into());
    }
    let mut time = xr::Time::from_nanos(0);
    check("convert time", unsafe {
        convert(api.instance, &stamp, &mut time)
    })?;
    Ok(time)
}

impl Trial {
    fn open(
        api: Session,
        owner: String,
        action: Action,
        uuid: Option<xr::Uuid>,
    ) -> Result<Self, String> {
        let create = load!(api, CreateSpatialPersistenceContextAsyncEXT);
        let _ = load!(api, CancelFutureEXT);
        let _ = load!(api, DestroySpatialPersistenceContextEXT);
        let info = xr::SpatialPersistenceContextCreateInfoEXT {
            ty: xr::SpatialPersistenceContextCreateInfoEXT::TYPE,
            next: ptr::null(),
            scope: xr::SpatialPersistenceScopeEXT::LOCAL_ANCHORS,
        };
        let mut t = Self {
            api,
            owner,
            action,
            uuid,
            phase: Phase::Store,
            future: Default::default(),
            store: xr::SpatialPersistenceContextEXT::NULL,
            context: xr::SpatialContextEXT::NULL,
            entity: xr::SpatialEntityEXT::NULL,
            space: xr::Space::NULL,
            snapshot: xr::SpatialSnapshotEXT::NULL,
        };
        check("open local store", unsafe {
            create(t.api.session, &info, &mut t.future)
        })?;
        Ok(t)
    }

    fn tick(&mut self) -> Result<Option<Value>, String> {
        let api = &self.api;
        let poll = load!(api, PollFutureEXT);
        let info = xr::FuturePollInfoEXT {
            ty: xr::FuturePollInfoEXT::TYPE,
            next: ptr::null(),
            future: self.future,
        };
        let mut out = xr::FuturePollResultEXT {
            ty: xr::FuturePollResultEXT::TYPE,
            next: ptr::null_mut(),
            state: xr::FutureStateEXT::PENDING,
        };
        check("poll", unsafe { poll(api.instance, &info, &mut out) })?;
        if out.state != xr::FutureStateEXT::READY {
            return Ok(None);
        }
        match self.phase {
            Phase::Store => {
                let complete = load!(api, CreateSpatialPersistenceContextCompleteEXT);
                let mut out = xr::CreateSpatialPersistenceContextCompletionEXT {
                    ty: xr::CreateSpatialPersistenceContextCompletionEXT::TYPE,
                    next: ptr::null_mut(),
                    future_result: xr::Result::ERROR_RUNTIME_FAILURE,
                    create_result: xr::SpatialPersistenceContextResultEXT::from_raw(-1),
                    persistence_context: xr::SpatialPersistenceContextEXT::NULL,
                };
                check("complete local store", unsafe {
                    complete(api.session, self.future, &mut out)
                })?;
                self.future = Default::default();
                check("store future", out.future_result)?;
                store_result("store result", out.create_result)?;
                self.store = out.persistence_context;
                if self.store == xr::SpatialPersistenceContextEXT::NULL {
                    return Err("empty local store".into());
                }
                if self.action == Action::Erase {
                    let erase = load!(api, UnpersistSpatialEntityAsyncEXT);
                    let info = xr::SpatialEntityUnpersistInfoEXT {
                        ty: xr::SpatialEntityUnpersistInfoEXT::TYPE,
                        next: ptr::null(),
                        persist_uuid: self.uuid.ok_or("missing erase uuid")?,
                    };
                    check("begin erase", unsafe {
                        erase(self.store, &info, &mut self.future)
                    })?;
                    self.phase = Phase::Unpersist;
                } else {
                    self.open_context()?;
                }
            }
            Phase::Context => {
                let complete = load!(api, CreateSpatialContextCompleteEXT);
                let mut out = xr::CreateSpatialContextCompletionEXT {
                    ty: xr::CreateSpatialContextCompletionEXT::TYPE,
                    next: ptr::null_mut(),
                    future_result: xr::Result::ERROR_RUNTIME_FAILURE,
                    spatial_context: xr::SpatialContextEXT::NULL,
                };
                check("complete context", unsafe {
                    complete(api.session, self.future, &mut out)
                })?;
                self.future = Default::default();
                check("context future", out.future_result)?;
                self.context = out.spatial_context;
                if self.context == xr::SpatialContextEXT::NULL {
                    return Err("empty context".into());
                }
                self.prepare_operation()?;
            }
            Phase::Persist => {
                let complete = load!(api, PersistSpatialEntityCompleteEXT);
                let mut out = xr::PersistSpatialEntityCompletionEXT {
                    ty: xr::PersistSpatialEntityCompletionEXT::TYPE,
                    next: ptr::null_mut(),
                    future_result: xr::Result::ERROR_RUNTIME_FAILURE,
                    persist_result: xr::SpatialPersistenceContextResultEXT::from_raw(-1),
                    persist_uuid: xr::Uuid { data: [0; 16] },
                };
                check("complete persist", unsafe {
                    complete(self.store, self.future, &mut out)
                })?;
                self.future = Default::default();
                check("persist future", out.future_result)?;
                store_result("persist result", out.persist_result)?;
                return Ok(Some(
                    json!({"stage":"saved","uuid":encode_uuid(out.persist_uuid)}),
                ));
            }
            Phase::Unpersist => {
                let complete = load!(api, UnpersistSpatialEntityCompleteEXT);
                let mut out = xr::UnpersistSpatialEntityCompletionEXT {
                    ty: xr::UnpersistSpatialEntityCompletionEXT::TYPE,
                    next: ptr::null_mut(),
                    future_result: xr::Result::ERROR_RUNTIME_FAILURE,
                    unpersist_result: xr::SpatialPersistenceContextResultEXT::from_raw(-1),
                };
                check("complete erase", unsafe {
                    complete(self.store, self.future, &mut out)
                })?;
                self.future = Default::default();
                check("erase future", out.future_result)?;
                if out.unpersist_result
                    == xr::SpatialPersistenceContextResultEXT::PERSIST_UUID_NOT_FOUND
                {
                    return Ok(Some(json!({"stage":"already_absent"})));
                }
                store_result("erase result", out.unpersist_result)?;
                return Ok(Some(json!({"stage":"erased"})));
            }
            Phase::Snapshot => {
                let complete = load!(api, CreateSpatialDiscoverySnapshotCompleteEXT);
                let info = xr::CreateSpatialDiscoverySnapshotCompletionInfoEXT {
                    ty: xr::CreateSpatialDiscoverySnapshotCompletionInfoEXT::TYPE,
                    next: ptr::null(),
                    base_space: self.space,
                    time: now(api)?,
                    future: self.future,
                };
                let mut out = xr::CreateSpatialDiscoverySnapshotCompletionEXT {
                    ty: xr::CreateSpatialDiscoverySnapshotCompletionEXT::TYPE,
                    next: ptr::null_mut(),
                    future_result: xr::Result::ERROR_RUNTIME_FAILURE,
                    snapshot: xr::SpatialSnapshotEXT::NULL,
                };
                check("complete snapshot", unsafe {
                    complete(self.context, &info, &mut out)
                })?;
                self.future = Default::default();
                check("snapshot future", out.future_result)?;
                self.snapshot = out.snapshot;
                return self.read_snapshot().map(Some);
            }
        }
        Ok(None)
    }

    fn open_context(&mut self) -> Result<(), String> {
        let api = &self.api;
        let create = load!(api, CreateSpatialContextAsyncEXT);
        let _ = load!(api, DestroySpatialContextEXT);
        let components = [
            xr::SpatialComponentTypeEXT::ANCHOR,
            xr::SpatialComponentTypeEXT::PERSISTENCE,
        ];
        let anchor = xr::SpatialCapabilityConfigurationAnchorEXT {
            ty: xr::SpatialCapabilityConfigurationAnchorEXT::TYPE,
            next: ptr::null(),
            capability: xr::SpatialCapabilityEXT::ANCHOR,
            enabled_component_count: 2,
            enabled_components: components.as_ptr(),
        };
        let config = ptr::from_ref(&anchor).cast();
        let store = xr::SpatialContextPersistenceConfigEXT {
            ty: xr::SpatialContextPersistenceConfigEXT::TYPE,
            next: ptr::null(),
            persistence_context_count: 1,
            persistence_contexts: &self.store,
        };
        let info = xr::SpatialContextCreateInfoEXT {
            ty: xr::SpatialContextCreateInfoEXT::TYPE,
            next: ptr::from_ref(&store).cast(),
            capability_config_count: 1,
            capability_configs: &config,
        };
        check("begin context", unsafe {
            create(api.session, &info, &mut self.future)
        })?;
        self.phase = Phase::Context;
        Ok(())
    }

    fn prepare_operation(&mut self) -> Result<(), String> {
        let api = &self.api;
        let base = load!(api, CreateReferenceSpace);
        let _ = load!(api, DestroySpace);
        let info = xr::ReferenceSpaceCreateInfo {
            ty: xr::ReferenceSpaceCreateInfo::TYPE,
            next: ptr::null(),
            // Create one metre in front of the wearer. Report located poses in the
            // same stationary STAGE space used by the SDK marker scene.
            reference_space_type: if self.action == Action::Find {
                xr::ReferenceSpaceType::STAGE
            } else {
                xr::ReferenceSpaceType::VIEW
            },
            pose_in_reference_space: xr::Posef::IDENTITY,
        };
        check("base space", unsafe {
            base(api.session, &info, &mut self.space)
        })?;
        if self.action == Action::Save {
            let create = load!(api, CreateSpatialAnchorEXT);
            let _ = load!(api, DestroySpatialEntityEXT);
            let persist = load!(api, PersistSpatialEntityAsyncEXT);
            let anchor = xr::SpatialAnchorCreateInfoEXT {
                ty: xr::SpatialAnchorCreateInfoEXT::TYPE,
                next: ptr::null(),
                base_space: self.space,
                time: now(api)?,
                pose: xr::Posef {
                    position: xr::Vector3f {
                        x: 0.0,
                        y: 0.0,
                        z: -1.0,
                    },
                    ..xr::Posef::IDENTITY
                },
            };
            let mut id = xr::SpatialEntityIdEXT::default();
            check("one anchor", unsafe {
                create(self.context, &anchor, &mut id, &mut self.entity)
            })?;
            let info = xr::SpatialEntityPersistInfoEXT {
                ty: xr::SpatialEntityPersistInfoEXT::TYPE,
                next: ptr::null(),
                spatial_context: self.context,
                spatial_entity_id: id,
            };
            check("begin persist", unsafe {
                persist(self.store, &info, &mut self.future)
            })?;
            self.phase = Phase::Persist;
        } else {
            let discover = load!(api, CreateSpatialDiscoverySnapshotAsyncEXT);
            let _ = load!(api, DestroySpatialSnapshotEXT);
            let filter =
                self.uuid
                    .as_ref()
                    .map(|uuid| xr::SpatialDiscoveryPersistenceUuidFilterEXT {
                        ty: xr::SpatialDiscoveryPersistenceUuidFilterEXT::TYPE,
                        next: ptr::null(),
                        persisted_uuid_count: 1,
                        persisted_uuids: uuid,
                    });
            let components = [
                xr::SpatialComponentTypeEXT::PERSISTENCE,
                xr::SpatialComponentTypeEXT::ANCHOR,
            ];
            let info = xr::SpatialDiscoverySnapshotCreateInfoEXT {
                ty: xr::SpatialDiscoverySnapshotCreateInfoEXT::TYPE,
                next: filter
                    .as_ref()
                    .map_or(ptr::null(), |f| ptr::from_ref(f).cast()),
                component_type_count: if self.action == Action::Find { 2 } else { 1 },
                component_types: components.as_ptr(),
            };
            check("begin discovery", unsafe {
                discover(self.context, &info, &mut self.future)
            })?;
            self.phase = Phase::Snapshot;
        }
        Ok(())
    }

    fn read_snapshot(&self) -> Result<Value, String> {
        let query = load!(self.api, QuerySpatialComponentDataEXT);
        let component = xr::SpatialComponentTypeEXT::PERSISTENCE;
        let cond = xr::SpatialComponentDataQueryConditionEXT {
            ty: xr::SpatialComponentDataQueryConditionEXT::TYPE,
            next: ptr::null(),
            component_type_count: 1,
            component_types: &component,
        };
        let mut out = xr::SpatialComponentDataQueryResultEXT {
            ty: xr::SpatialComponentDataQueryResultEXT::TYPE,
            next: ptr::null_mut(),
            entity_id_capacity_input: 0,
            entity_id_count_output: 0,
            entity_ids: ptr::null_mut(),
            entity_state_capacity_input: 0,
            entity_state_count_output: 0,
            entity_states: ptr::null_mut(),
        };
        check("count snapshot", unsafe {
            query(self.snapshot, &cond, &mut out)
        })?;
        let count = out.entity_id_count_output;
        if count != out.entity_state_count_output {
            return Err("snapshot counts differ".into());
        }
        if count == 0 {
            return Ok(json!({"stage":"snapshot","rows":[]}));
        }
        let mut ids = vec![xr::SpatialEntityIdEXT::default(); count as usize];
        let mut states = vec![xr::SpatialEntityTrackingStateEXT::STOPPED; count as usize];
        let mut data = vec![
            xr::SpatialPersistenceDataEXT {
                persist_uuid: xr::Uuid { data: [0; 16] },
                persist_state: xr::SpatialPersistenceStateEXT::NOT_FOUND
            };
            count as usize
        ];
        let mut plist = xr::SpatialComponentPersistenceListEXT {
            ty: xr::SpatialComponentPersistenceListEXT::TYPE,
            next: ptr::null_mut(),
            persist_data_count: count,
            persist_data: data.as_mut_ptr(),
        };
        out.next = ptr::from_mut(&mut plist).cast();
        out.entity_id_capacity_input = count;
        out.entity_ids = ids.as_mut_ptr();
        out.entity_state_capacity_input = count;
        out.entity_states = states.as_mut_ptr();
        check("read persistence rows", unsafe {
            query(self.snapshot, &cond, &mut out)
        })?;
        if out.entity_id_count_output != count || out.entity_state_count_output != count {
            return Err("snapshot changed".into());
        }
        let mut rows = Vec::new();
        for i in 0..count as usize {
            let row = data[i];
            let pose = if self.action == Action::Find
                && states[i] == xr::SpatialEntityTrackingStateEXT::TRACKING
                && row.persist_state == xr::SpatialPersistenceStateEXT::LOADED
            {
                self.read_pose(ids[i])?
            } else {
                None
            };
            let stage_pose = pose.map(|p| {
                let v = p.position;
                let q = p.orientation;
                [v.x, v.y, v.z, q.x, q.y, q.z, q.w]
            });
            rows.push(json!({"uuid":encode_uuid(row.persist_uuid),"persist":format!("{:?}",row.persist_state),
                "tracking":format!("{:?}",states[i]),"pose_valid":stage_pose.is_some(),"stage_pose":stage_pose}));
        }
        Ok(json!({"stage":"snapshot","rows":rows}))
    }

    fn read_pose(&self, target: xr::SpatialEntityIdEXT) -> Result<Option<xr::Posef>, String> {
        let query = load!(self.api, QuerySpatialComponentDataEXT);
        let component = xr::SpatialComponentTypeEXT::ANCHOR;
        let cond = xr::SpatialComponentDataQueryConditionEXT {
            ty: xr::SpatialComponentDataQueryConditionEXT::TYPE,
            next: ptr::null(),
            component_type_count: 1,
            component_types: &component,
        };
        let mut out = xr::SpatialComponentDataQueryResultEXT {
            ty: xr::SpatialComponentDataQueryResultEXT::TYPE,
            next: ptr::null_mut(),
            entity_id_capacity_input: 0,
            entity_id_count_output: 0,
            entity_ids: ptr::null_mut(),
            entity_state_capacity_input: 0,
            entity_state_count_output: 0,
            entity_states: ptr::null_mut(),
        };
        check("count poses", unsafe {
            query(self.snapshot, &cond, &mut out)
        })?;
        if out.entity_id_count_output != 1 || out.entity_state_count_output != 1 {
            return Ok(None);
        }
        let mut pose = xr::Posef::IDENTITY;
        let mut list = xr::SpatialComponentAnchorListEXT {
            ty: xr::SpatialComponentAnchorListEXT::TYPE,
            next: ptr::null_mut(),
            location_count: 1,
            locations: &mut pose,
        };
        let mut id = Default::default();
        let mut state = xr::SpatialEntityTrackingStateEXT::STOPPED;
        out.next = ptr::from_mut(&mut list).cast();
        out.entity_id_capacity_input = 1;
        out.entity_ids = &mut id;
        out.entity_state_capacity_input = 1;
        out.entity_states = &mut state;
        check("read position", unsafe {
            query(self.snapshot, &cond, &mut out)
        })?;
        let q = pose.orientation;
        let valid = id == target
            && state == xr::SpatialEntityTrackingStateEXT::TRACKING
            && [
                pose.position.x,
                pose.position.y,
                pose.position.z,
                q.x,
                q.y,
                q.z,
                q.w,
            ]
            .iter()
            .all(|v| v.is_finite())
            && (q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w - 1.0).abs() < 0.01;
        Ok(valid.then_some(pose))
    }

    fn close(&mut self) -> Vec<String> {
        let api = &self.api;
        let mut errors = Vec::new();
        macro_rules! release {
            ($call:expr) => {
                if let Err(e) = (|| -> Result<(), String> { $call })() {
                    errors.push(e);
                }
            };
        }
        if self.future != Default::default() {
            release!({
                let cancel = load!(api, CancelFutureEXT);
                let info = xr::FutureCancelInfoEXT {
                    ty: xr::FutureCancelInfoEXT::TYPE,
                    next: ptr::null(),
                    future: self.future,
                };
                check("release future", unsafe { cancel(api.instance, &info) })
            });
        }
        macro_rules! destroy {
            ($field:ident,$handle:ident,$name:ident) => {
                if self.$field != xr::$handle::NULL {
                    release!({
                        let f = load!(api, $name);
                        check(stringify!($name), unsafe { f(self.$field) })
                    });
                }
            };
        }
        destroy!(snapshot, SpatialSnapshotEXT, DestroySpatialSnapshotEXT);
        destroy!(entity, SpatialEntityEXT, DestroySpatialEntityEXT);
        destroy!(space, Space, DestroySpace);
        destroy!(context, SpatialContextEXT, DestroySpatialContextEXT);
        destroy!(
            store,
            SpatialPersistenceContextEXT,
            DestroySpatialPersistenceContextEXT
        );
        errors
    }
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_persistence<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    instance: jlong,
    session: jlong,
    proc_addr: jlong,
    owner: JString<'a>,
    command: JString<'a>,
    uuid: JString<'a>,
) -> JString<'a> {
    env.with_env(|env| {
        let owner = owner.to_string();
        let command = command.to_string();
        let uuid = uuid.to_string();
        let run = (|| -> Result<Value, String> {
            let mut current = CURRENT.lock().map_err(|_| "trial lock")?;
            if let Some(t) = current.as_ref() {
                if t.owner != owner
                    || t.api.session.into_raw() != session as u64
                    || t.api.instance.into_raw() != instance as u64
                {
                    return Err("different trial owner".into());
                }
            }
            if command == "poll" || command == "cancel" {
                let t = current.as_mut().ok_or("no active trial")?;
                let mut done = if command == "cancel" {
                    json!({"stage":"cancelled"})
                } else {
                    match t.tick() {
                        Ok(None) => return Ok(json!({"stage":"pending"})),
                        Ok(Some(v)) => v,
                        Err(e) => json!({"stage":"error","error":e}),
                    }
                };
                let errors = t.close();
                *current = None;
                done["release_errors"] = json!(errors);
                return Ok(done);
            }
            if current.is_some() || instance == 0 || session == 0 || proc_addr == 0 {
                return Err("invalid trial state".into());
            }
            let action = match command.as_str() {
                "list" => Action::List,
                "save" => Action::Save,
                "find" => Action::Find,
                "erase" => Action::Erase,
                _ => return Err("unknown trial action".into()),
            };
            let uuid = if matches!(action, Action::Find | Action::Erase) {
                Some(decode_uuid(&uuid)?)
            } else {
                None
            };
            let api = Session {
                instance: xr::Instance::from_raw(instance as u64),
                session: xr::Session::from_raw(session as u64),
                get: unsafe {
                    std::mem::transmute::<usize, pfn::GetInstanceProcAddr>(proc_addr as usize)
                },
            };
            *current = Some(Trial::open(api, owner, action, uuid)?);
            Ok(json!({"stage":"pending"}))
        })();
        JString::from_str(
            env,
            run.unwrap_or_else(|e| json!({"stage":"error","error":e}))
                .to_string(),
        )
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}
