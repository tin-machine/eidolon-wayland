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
