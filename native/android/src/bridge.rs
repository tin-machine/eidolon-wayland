use crate::{build_info, registry::global};
use eidolon_model::{limits::COMMAND_BYTES, state::Status};
use jni::{
    Env, EnvUnowned,
    errors::{Error, ErrorPolicy},
    jni_str,
    objects::{JByteArray, JClass, JObject, JString},
    sys::{jint, jlong},
};

/// payloadをJavaの例外文へ含めず、対象runtimeをFailedにする。
struct BridgePolicy;
impl<T: Default> ErrorPolicy<T, Error> for BridgePolicy {
    type Captures<'local: 'call, 'call> = i64;
    fn on_error<'local: 'call, 'call>(
        env: &mut Env<'local>,
        handle: &mut i64,
        _error: Error,
    ) -> jni::errors::Result<T> {
        global().fail(*handle);
        if !env.exception_check() {
            env.throw_new(
                jni_str!("java/lang/RuntimeException"),
                jni_str!("JNI_FAILURE"),
            )?;
        }
        Ok(T::default())
    }
    fn on_panic<'local: 'call, 'call>(
        env: &mut Env<'local>,
        handle: &mut i64,
        payload: Box<dyn std::any::Any + Send>,
    ) -> jni::errors::Result<T> {
        global().fail(*handle);
        if let Err(secondary) =
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| drop(payload)))
        {
            std::mem::forget(secondary);
        }
        if !env.exception_check() {
            env.throw_new(
                jni_str!("java/lang/RuntimeException"),
                jni_str!("JNI_FAILURE"),
            )?;
        }
        Ok(T::default())
    }
}

fn bounded_json(env: &mut Env<'_>, text: &JString<'_>) -> jni::errors::Result<Option<String>> {
    // Java側のUTF-16長で巨大入力をcopy前に拒否し、その後UTF-8 byte長も確認する。
    if text.as_char_sequence().length(env)? as usize > COMMAND_BYTES {
        return Ok(None);
    }
    let json = text.try_to_string(env)?;
    Ok((json.len() <= COMMAND_BYTES).then_some(json))
}

fn secret_bytes(
    env: &Env<'_>,
    array: &JByteArray<'_>,
) -> jni::errors::Result<Option<eidolon_transport::credentials::Secret>> {
    if array.is_null()
        || !(1..=eidolon_transport::credentials::KEY_BYTES).contains(&array.len(env)?)
    {
        return Ok(None);
    }
    Ok(Some(eidolon_transport::credentials::Secret::new(
        env.convert_byte_array(array)?,
    )))
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeAnchorScope<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    instance: jlong,
    proc_addr: jlong,
) -> jni::sys::jboolean {
    env.with_env(|_| {
        // The handles are obtained together from the Activity's live Scene.
        let info = unsafe { eidolon_spatial_anchor::inspect(instance as u64, proc_addr as usize) };
        Ok::<_, jni::errors::Error>(
            info["persistence_scopes"]["local_anchors"].as_bool() == Some(true),
        )
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeAnchorStep<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    instance: jlong,
    session: jlong,
    proc_addr: jlong,
    owner: JString<'a>,
    command: JString<'a>,
    uuid: JString<'a>,
    pose_json: JString<'a>,
) -> JString<'a> {
    env.with_env(|env| {
        let owner = owner.try_to_string(env)?;
        let command = command.try_to_string(env)?;
        let uuid = uuid.try_to_string(env)?;
        let pose = bounded_json(env, &pose_json)?
            .and_then(|json| {
                serde_json::from_str::<eidolon_model::workspace::PanelPose>(&json).ok()
            })
            .filter(|pose| pose.valid())
            .map(|pose| {
                let [x, y, z] = pose.position_m;
                let [qx, qy, qz, qw] = pose.rotation_xyzw;
                [x, y, z, qx, qy, qz, qw]
            });
        let result = if command == "save" && pose.is_none() {
            serde_json::json!({"stage":"error","error":"invalid_panel_pose"})
        } else {
            // This runs on the owning Activity thread while the SDK session is live.
            unsafe {
                eidolon_spatial_anchor::panel_step(
                    (instance as u64, session as u64, proc_addr as usize),
                    &owner,
                    &command,
                    &uuid,
                    pose,
                )
            }
        };
        JString::from_str(env, result.to_string())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

/// This optional capability is checked before an Eidolon Runtime exists.
#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeAnchorRequiredExtensions<
    'a,
>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    context: JObject<'a>,
) -> JString<'a> {
    env.with_env(|env| {
        let vm = env.get_java_vm()?;
        // MainActivity passes its application Context before starting the Spatial SDK.
        let advertised = unsafe {
            eidolon_spatial_anchor::startup_extensions(vm.get_raw().cast(), context.as_raw().cast())
        };
        let mut required = Vec::new();
        if let Ok(advertised) = advertised {
            required.extend_from_slice(eidolon_spatial_anchor::required_if_advertised(&advertised));
            required.extend(eidolon_spatial_anchor::hand_extensions(&advertised));
        }
        required.sort_unstable();
        required.dedup();
        JString::from_str(env, serde_json::json!(required).to_string())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeSampleHands<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    instance: jlong,
    session: jlong,
    proc_addr: jlong,
    unobstructed: jni::sys::jboolean,
    output: jni::objects::JFloatArray<'a>,
) -> jni::sys::jboolean {
    env.with_env(|env| {
        let mut values = [0.0; 32];
        let available = unsafe {
            eidolon_spatial_anchor::sample_hands(
                instance as u64,
                session as u64,
                proc_addr as usize,
                unobstructed,
                &mut values,
            )
        };
        output.set_region(env, 0, &values)?;
        Ok::<_, jni::errors::Error>(available)
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeCloseHands(
    _: EnvUnowned<'_>,
    _: JClass<'_>,
    instance: jlong,
    session: jlong,
) {
    unsafe { eidolon_spatial_anchor::close_hands(instance as u64, session as u64) }
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeGenerateIdentity<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
) -> JByteArray<'a> {
    env.with_env(|env| match eidolon_transport::credentials::generate() {
        Ok(secret) => env.byte_array_from_slice(&secret),
        Err(_) => {
            env.throw_new(
                jni_str!("java/lang/IllegalStateException"),
                jni_str!("CREDENTIAL_INVALID"),
            )?;
            Ok(JByteArray::default())
        }
    })
    .resolve_with::<BridgePolicy, _>(|| 0)
}
#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativePublicKey<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    private: JByteArray<'a>,
) -> JString<'a> {
    env.with_env(|env| {
        let public = secret_bytes(env, &private)?
            .and_then(|bytes| eidolon_transport::credentials::public_key(&bytes).ok());
        if let Some(public) = public {
            JString::from_str(env, public)
        } else {
            env.throw_new(
                jni_str!("java/lang/IllegalArgumentException"),
                jni_str!("CREDENTIAL_INVALID"),
            )?;
            Ok(JString::default())
        }
    })
    .resolve_with::<BridgePolicy, _>(|| 0)
}
#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeProvideCredential<
    'a,
>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
    session: jlong,
    request: jlong,
    private: JByteArray<'a>,
) -> jint {
    env.with_env(|env| -> jni::errors::Result<i32> {
        use eidolon_model::ids::{CommandSeq, SessionId};
        let (Ok(session), Ok(request)) = (SessionId::new(session), CommandSeq::new(request)) else {
            return Ok(Status::InvalidArgument as i32);
        };
        let Some(secret) = secret_bytes(env, &private)? else {
            return Ok(Status::InvalidArgument as i32);
        };
        Ok(global()
            .guarded(handle, |runtime| {
                runtime.provide_credential(session, request, secret)
            })
            .unwrap_or_else(|s| s) as i32)
    })
    .resolve_with::<BridgePolicy, _>(|| handle)
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeCredentialScope<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
) -> JString<'a> {
    env.with_env(|env| JString::from_str(env, global().credential_scope(handle).to_string()))
        .resolve_with::<BridgePolicy, _>(|| handle)
}
#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeProvideCredentialFor<
    'a,
>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
    session: jlong,
    request: jlong,
    binding: JString<'a>,
    private: JByteArray<'a>,
) -> jint {
    env.with_env(|env| -> jni::errors::Result<i32> {
        use eidolon_model::ids::{CommandSeq, SessionId};
        let (Ok(session), Ok(request)) = (SessionId::new(session), CommandSeq::new(request)) else {
            return Ok(Status::InvalidArgument as i32);
        };
        let Some(binding) = bounded_json(env, &binding)?.filter(|value| {
            value.len() == 64
                && value
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        }) else {
            return Ok(Status::InvalidArgument as i32);
        };
        let Some(secret) = secret_bytes(env, &private)? else {
            return Ok(Status::InvalidArgument as i32);
        };
        Ok(global()
            .guarded(handle, |runtime| {
                runtime.provide_credential_for(session, request, &binding, secret)
            })
            .unwrap_or_else(|s| s) as i32)
    })
    .resolve_with::<BridgePolicy, _>(|| handle)
}

#[cfg(debug_assertions)]
#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_DebugNativeBridge_nativeVerifyPin<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    pin: JString<'a>,
    candidate: JString<'a>,
) -> JString<'a> {
    env.with_env(|env| {
        let expected = if pin.is_null() {
            None
        } else {
            bounded_json(env, &pin)?
        };
        let candidate = bounded_json(env, &candidate)?;
        let result = candidate
            .as_deref()
            .ok_or(eidolon_transport::Error::ConfigInvalid)
            .and_then(eidolon_transport::host_key::parse_key)
            .and_then(|seen| eidolon_transport::host_key::verify(expected.as_deref(), &seen));
        JString::from_str(env, result.err().map_or("ACCEPTED", |e| e.code()))
    })
    .resolve_with::<BridgePolicy, _>(|| 0)
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeAttachSurface<'a>(
    env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
    session: jlong,
    window: jlong,
    scene: jlong,
    generation: jlong,
    surface: JObject<'a>,
    width: jint,
    height: jint,
) -> jint {
    attach_surface_impl(
        env, handle, session, window, scene, generation, surface, width, height, false,
    )
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeAttachWindowSurface<
    'a,
>(
    env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
    session: jlong,
    window: jlong,
    scene: jlong,
    generation: jlong,
    surface: JObject<'a>,
    width: jint,
    height: jint,
) -> jint {
    attach_surface_impl(
        env, handle, session, window, scene, generation, surface, width, height, true,
    )
}

// 二つのflat JNI ABIで同じ検査・参照取得・ErrorPolicyを共有する。
#[allow(clippy::too_many_arguments)]
fn attach_surface_impl<'a>(
    mut env: EnvUnowned<'a>,
    handle: jlong,
    session: jlong,
    window: jlong,
    scene: jlong,
    generation: jlong,
    surface: JObject<'a>,
    width: jint,
    height: jint,
    window_mode: bool,
) -> jint {
    env.with_env(|env| -> jni::errors::Result<i32> {
        use eidolon_model::surface::{TargetKey, validate_dimensions};
        let key = match TargetKey::from_raw(session, window, scene, generation) {
            Ok(key) => key,
            Err(status) => return Ok(status as i32),
        };
        if let Err(status) = validate_dimensions(width, height) {
            return Ok(status as i32);
        }
        if surface.is_null() {
            return Ok(Status::InvalidArgument as i32);
        }
        // JNI local ref/envは保存しない。失敗時もRAIIで追加参照を回収。
        let Some(native) = (unsafe {
            eidolon_render::PlatformWindow::from_surface(
                env.get_raw().cast(),
                surface.as_raw().cast(),
            )
        }) else {
            return Ok(Status::InvalidArgument as i32);
        };
        Ok(global()
            .guarded(handle, |runtime| {
                if window_mode {
                    runtime.attach_window_surface(key, native, width, height)
                } else {
                    runtime.attach_surface(key, native, width, height)
                }
            })
            .unwrap_or_else(|s| s) as i32)
    })
    .resolve_with::<BridgePolicy, _>(|| handle)
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeBuildInfo<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
) -> JString<'a> {
    env.with_env(|env| JString::from_str(env, build_info::json()))
        .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeCreate<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    config: JString<'a>,
) -> jlong {
    env.with_env(|env| -> jni::errors::Result<i64> {
        let Some(json) = bounded_json(env, &config)? else {
            env.throw_new(
                jni_str!("java/lang/IllegalArgumentException"),
                jni_str!("CONFIG_INVALID"),
            )?;
            return Ok(0);
        };
        match global().create(&json) {
            Ok(id) => Ok(id),
            Err(status) => {
                match status {
                    Status::InvalidArgument => env.throw_new(
                        jni_str!("java/lang/IllegalArgumentException"),
                        jni_str!("CONFIG_INVALID"),
                    )?,
                    Status::Busy => env.throw_new(
                        jni_str!("java/lang/IllegalStateException"),
                        jni_str!("RUNTIME_BUSY"),
                    )?,
                    _ => env.throw_new(
                        jni_str!("java/lang/RuntimeException"),
                        jni_str!("JNI_FAILURE"),
                    )?,
                }
                Ok(0)
            }
        }
    })
    .resolve_with::<BridgePolicy, _>(|| 0)
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeCreateCompanion<'a>(
    _: EnvUnowned<'a>,
    _: JClass<'a>,
    primary: jlong,
    slot: jint,
) -> jlong {
    use std::panic::{AssertUnwindSafe, catch_unwind};
    match catch_unwind(AssertUnwindSafe(|| {
        u8::try_from(slot)
            .map_err(|_| Status::InvalidArgument)
            .and_then(|number| global().create_companion(primary, number))
    })) {
        Ok(Ok(handle)) => handle,
        Ok(Err(status)) => -i64::from(status as i32),
        Err(_) => -i64::from(Status::Failed as i32),
    }
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeCreateTermux<'a>(
    _: EnvUnowned<'a>,
    _: JClass<'a>,
    primary: jlong,
    slot: jint,
) -> jlong {
    use std::panic::{AssertUnwindSafe, catch_unwind};
    match catch_unwind(AssertUnwindSafe(|| {
        u8::try_from(slot)
            .map_err(|_| Status::InvalidArgument)
            .and_then(|number| global().create_termux(primary, number))
    })) {
        Ok(Ok(handle)) => handle,
        Ok(Err(status)) => -i64::from(status as i32),
        Err(_) => -i64::from(Status::Failed as i32),
    }
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeCommand<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
    command: JString<'a>,
) -> jint {
    env.with_env(|env| -> jni::errors::Result<i32> {
        // JNI文字列の取得自体の失敗を通常JSON文字列へ置換しない。
        let Some(json) = bounded_json(env, &command)? else {
            return Ok(Status::InvalidArgument as i32);
        };
        Ok(global().command(handle, &json) as i32)
    })
    .resolve_with::<BridgePolicy, _>(|| handle)
}

macro_rules! status_entry {
    ($name:ident, $method:ident) => {
        #[unsafe(no_mangle)]
        pub extern "system" fn $name<'a>(
            mut env: EnvUnowned<'a>,
            _: JClass<'a>,
            handle: jlong,
        ) -> jint {
            env.with_env(|_| -> jni::errors::Result<i32> { Ok(global().$method(handle) as i32) })
                .resolve_with::<BridgePolicy, _>(|| handle)
        }
    };
}
status_entry!(
    Java_io_github_tinmachine_eidolon_NativeBridge_nativeRequestStop,
    request_stop
);
status_entry!(
    Java_io_github_tinmachine_eidolon_NativeBridge_nativeDestroy,
    destroy
);

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeSnapshot<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
) -> JString<'a> {
    env.with_env(|env| JString::from_str(env, global().snapshot(handle).to_string()))
        .resolve_with::<BridgePolicy, _>(|| handle)
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeUiSnapshot<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
    panel: jni::sys::jboolean,
) -> JString<'a> {
    env.with_env(|env| JString::from_str(env, global().ui_snapshot(handle, panel).to_string()))
        .resolve_with::<BridgePolicy, _>(|| handle)
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeCatalogUi<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
) -> JString<'a> {
    env.with_env(|env| JString::from_str(env, global().catalog_ui(handle).to_string()))
        .resolve_with::<BridgePolicy, _>(|| handle)
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeImeUi<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
) -> JString<'a> {
    env.with_env(|env| JString::from_str(env, global().ime_ui(handle).to_string()))
        .resolve_with::<BridgePolicy, _>(|| handle)
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeWaitForUiChange(
    _: EnvUnowned<'_>,
    _: JClass<'_>,
    previous: jlong,
) -> jlong {
    eidolon_core::ui_changes::wait(previous, std::time::Duration::from_secs(1))
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativePoll<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
    max: jint,
) -> JString<'a> {
    env.with_env(|env| JString::from_str(env, global().poll(handle, max).to_string()))
        .resolve_with::<BridgePolicy, _>(|| handle)
}

#[cfg(debug_assertions)]
#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_DebugNativeBridge_nativePanic<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    handle: jlong,
) -> jint {
    env.with_env(|_| -> jni::errors::Result<i32> { panic!("synthetic runtime panic") })
        .resolve_with::<BridgePolicy, _>(|| handle)
}
