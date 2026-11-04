pub mod build_info;

#[cfg(target_os = "android")]
mod bridge {
    use jni::{
        EnvUnowned,
        objects::{JClass, JString},
    };

    #[unsafe(no_mangle)]
    pub extern "system" fn Java_io_github_tinmachine_eidolon_NativeBridge_nativeBuildInfo<
        'local,
    >(
        mut env: EnvUnowned<'local>,
        _: JClass<'local>,
    ) -> JString<'local> {
        env.with_env(|env| JString::from_str(env, crate::build_info::json()))
            .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
    }
}
