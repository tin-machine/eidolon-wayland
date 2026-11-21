use jni::{
    EnvUnowned,
    objects::{JClass, JObject, JString},
    sys::jlong,
};
use serde_json::json;

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_anchorExtensions<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    instance: jlong,
    proc_addr: jlong,
) -> JString<'a> {
    env.with_env(|env| {
        // AnchorProbeActivity supplies both handles from the same live scene.
        let value = unsafe { eidolon_spatial_anchor::inspect(instance as u64, proc_addr as usize) };
        JString::from_str(env, value.to_string())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_startupExtensions<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    context: JObject<'a>,
) -> JString<'a> {
    env.with_env(|env| {
        let vm = env.get_java_vm()?;
        let value = match unsafe {
            eidolon_spatial_anchor::startup_extensions(vm.get_raw().cast(), context.as_raw().cast())
        } {
            Ok(extensions) => json!({ "extensions": extensions }),
            Err(error) => json!({ "error": error }),
        };
        JString::from_str(env, value.to_string())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
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
        // The controlling Activity stops the trial before it releases the SDK scene.
        let value = unsafe {
            eidolon_spatial_anchor::step(
                instance as u64,
                session as u64,
                proc_addr as usize,
                &owner.try_to_string(env)?,
                &command.try_to_string(env)?,
                &uuid.try_to_string(env)?,
            )
        };
        JString::from_str(env, value.to_string())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}
