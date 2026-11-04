use jni::{
    EnvUnowned,
    objects::{JClass, JObject, JString},
    sys::{jboolean, jint, jlong},
};
use ndk::native_window::NativeWindow;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    ffi::CString,
    io::{Read, Write},
    os::unix::{fs::DirBuilderExt, net::UnixStream},
    path::Path,
    process::{Child, Command, Stdio},
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicBool, AtomicI64, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

#[path = "egl.rs"]
mod egl;

#[link(name = "log")]
unsafe extern "C" {
    fn __android_log_write(
        priority: i32,
        tag: *const std::ffi::c_char,
        text: *const std::ffi::c_char,
    ) -> i32;
}

pub fn log(text: &str) {
    if let Ok(text) = CString::new(text) {
        unsafe {
            __android_log_write(4, c"EidolonProbeNative".as_ptr(), text.as_ptr());
        }
    }
}

struct RenderRun {
    stop: Arc<AtomicBool>,
    paused: Arc<AtomicBool>,
    stats: Arc<Mutex<Value>>,
    join: thread::JoinHandle<()>,
}
static RUNS: OnceLock<Mutex<HashMap<i64, RenderRun>>> = OnceLock::new();
static NEXT: AtomicI64 = AtomicI64::new(1);
fn runs() -> &'static Mutex<HashMap<i64, RenderRun>> {
    RUNS.get_or_init(Mutex::default)
}

#[cfg(feature = "compositor")]
#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_compositor<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
) -> JString<'a> {
    env.with_env(|env| {
        let result = crate::compositor::run()
            .unwrap_or_else(|e| json!({"pass":false,"error":e.to_string()}));
        JString::from_str(env, result.to_string())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

#[cfg(feature = "ssh")]
#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_transport<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    directory: JString<'a>,
    nonce: JString<'a>,
) -> JString<'a> {
    env.with_env(|env| {
        let directory = directory.to_string();
        let nonce = nonce.to_string();
        let result = crate::transport::run(&directory, &nonce)
            .unwrap_or_else(|e| json!({"pass":false,"error":e.to_string()}));
        JString::from_str(env, result.to_string())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_buildInfo<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
) -> JString<'a> {
    env.with_env(|env| JString::from_str(env, json!({"schema":1,"probe":1,"version":env!("EIDOLON_PROBE_VERSION"),"commit":env!("EIDOLON_PROBE_COMMIT"),"digest":env!("EIDOLON_PROBE_DIGEST"),"uid":unsafe{libc::getuid()},"abi":"arm64-v8a"}).to_string())).resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_start<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    surface: JObject<'a>,
) -> jlong {
    env.with_env(|env| -> Result<i64, jni::errors::Error> {
        let mut all = runs().lock().unwrap();
        if !all.is_empty() {
            return Ok(0);
        } // 停滞したGLを残したままthreadを追加しない。
        let window =
            unsafe { NativeWindow::from_surface(env.get_raw().cast(), surface.as_raw().cast()) };
        let Some(window) = window else {
            return Ok(0);
        };
        let id = NEXT.fetch_add(1, Ordering::Relaxed);
        let stop = Arc::new(AtomicBool::new(false));
        let paused = Arc::new(AtomicBool::new(false));
        let stats = Arc::new(Mutex::new(
            json!({"id":id,"frames":0,"finished":false,"error":null,"readback":false}),
        ));
        let (s, p, st) = (stop.clone(), paused.clone(), stats.clone());
        let join = thread::spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                egl::render(window, &s, &p, &st)
            }));
            let error = match result {
                Ok(Ok(())) => None,
                Ok(Err(e)) => Some(e),
                Err(_) => Some("renderer panic".into()),
            };
            let mut stats = st.lock().unwrap_or_else(|e| e.into_inner());
            stats["finished"] = json!(true);
            stats["error"] = json!(error);
            log(&format!("render_finished {stats}"));
        });
        all.insert(
            id,
            RenderRun {
                stop,
                paused,
                stats,
                join,
            },
        );
        Ok(id)
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_pause<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    id: jlong,
    paused: jboolean,
) {
    env.with_env(|_| -> Result<(), jni::errors::Error> {
        if let Some(run) = runs().lock().unwrap().get(&id) {
            run.paused.store(paused, Ordering::Relaxed);
        }
        Ok(())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>();
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_stop<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    id: jlong,
) {
    env.with_env(|_| -> Result<(), jni::errors::Error> {
        if let Some(run) = runs().lock().unwrap().get(&id) {
            run.stop.store(true, Ordering::Relaxed);
        }
        Ok(())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>();
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_snapshot<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    id: jlong,
) -> JString<'a> {
    env.with_env(|env| {
        let all = runs().lock().unwrap();
        let result = all
            .get(&id)
            .map(|r| r.stats.lock().unwrap().clone())
            .unwrap_or(json!({"stale":true}));
        JString::from_str(env, result.to_string())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_collect<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    id: jlong,
) -> jint {
    env.with_env(|_| -> Result<i32, jni::errors::Error> {
        let mut all = runs().lock().unwrap();
        if let Some(run) = all.get(&id) {
            if !run.join.is_finished() {
                return Ok(2);
            }
        } else {
            return Ok(1);
        }
        let run = all.remove(&id).unwrap();
        Ok(if run.join.join().is_ok() { 0 } else { 5 })
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}

struct OwnedChild(Child);
impl Drop for OwnedChild {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn helper(library: &str, files: &str) -> Result<Value, Box<dyn std::error::Error>> {
    let executable = Path::new(library).join("libeidolon_helper_probe.so");
    let mut child = OwnedChild(
        Command::new(&executable)
            .arg("--version")
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?,
    );
    let deadline = Instant::now() + Duration::from_secs(3);
    let status = loop {
        if let Some(status) = child.0.try_wait()? {
            break status;
        }
        if Instant::now() > deadline {
            return Err("version timeout".into());
        }
        thread::sleep(Duration::from_millis(10));
    };
    let mut version = String::new();
    child
        .0
        .stdout
        .take()
        .unwrap()
        .take(1024)
        .read_to_string(&mut version)?;
    let uid = unsafe { libc::getuid() };
    if !status.success() || version.trim() != format!("eidolon-helper-probe/1 uid={uid}") {
        return Err("version/uid mismatch".into());
    }
    let root = Path::new(files).join(format!("p{}", std::process::id()));
    std::fs::DirBuilder::new().mode(0o700).create(&root)?;
    let result = (|| -> Result<(), Box<dyn std::error::Error>> {
        for cycle in 0..10 {
            let path = root.join(format!("s{cycle}"));
            if path.as_os_str().len() > 103 {
                return Err("socket path too long".into());
            }
            let mut child = OwnedChild(
                Command::new(&executable)
                    .arg("--echo")
                    .arg(&path)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()?,
            );
            let deadline = Instant::now() + Duration::from_secs(3);
            let mut stream = loop {
                match UnixStream::connect(&path) {
                    Ok(s) => break s,
                    Err(e) => {
                        if Instant::now() > deadline || child.0.try_wait()?.is_some() {
                            return Err(e.into());
                        }
                        thread::sleep(Duration::from_millis(10));
                    }
                }
            };
            stream.set_read_timeout(Some(Duration::from_secs(3)))?;
            stream.set_write_timeout(Some(Duration::from_secs(3)))?;
            let payload = format!("eidolon-echo-{cycle}");
            stream.write_all(payload.as_bytes())?;
            let mut reply = vec![0; payload.len()];
            stream.read_exact(&mut reply)?;
            if reply != payload.as_bytes() {
                return Err("echo mismatch".into());
            }
            loop {
                if let Some(status) = child.0.try_wait()? {
                    if !status.success() {
                        return Err("helper failed".into());
                    }
                    break;
                }
                if Instant::now() > deadline {
                    return Err("helper exit timeout".into());
                }
                thread::sleep(Duration::from_millis(10));
            }
            log(&format!("helper_echo cycle={cycle} pass=true uid={uid}"));
        }
        Ok(())
    })();
    // このprobeがexclusive作成したdirectoryだけを回収する。
    let cleanup = std::fs::remove_dir_all(&root);
    result?;
    cleanup?;
    Ok(
        json!({"pass":true,"cycles":10,"uid":uid,"version":version.trim(),"context":std::fs::read_to_string("/proc/self/attr/current")?.trim_end_matches('\0').trim()}),
    )
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_helper<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    library: JString<'a>,
    files: JString<'a>,
) -> JString<'a> {
    env.with_env(|env| {
        let result = helper(&library.to_string(), &files.to_string())
            .unwrap_or_else(|e| json!({"pass":false,"error":e.to_string()}));
        log(&format!("helper_result {result}"));
        JString::from_str(env, result.to_string())
    })
    .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}
