use crate::{build_info, registry::global};
use eidolon_model::{limits::COMMAND_BYTES, state::Status};
use jni::{
    Env, EnvUnowned,
    errors::{Error, ErrorPolicy},
    jni_str,
    objects::{JClass, JString},
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
