//! Termux側の固定producer。Waypipe serverが設定したdisplayへ100frameを送る。
use eidolon_shm_fixture::{FRAMES, Mode};
use std::{
    os::unix::net::UnixStream,
    path::PathBuf,
    sync::{Arc, atomic::AtomicBool},
};

const REVISION: &str = "eidolon-shm-client-v1";

fn pattern() -> Result<eidolon_shm_fixture::Report, &'static str> {
    let name = std::env::var_os("WAYLAND_DISPLAY").ok_or("DISPLAY_MISSING")?;
    let name = PathBuf::from(name);
    let path = if name.is_absolute() {
        name
    } else {
        // Waypipeの生成display以外のpathをCLIから指定させない。
        if name.components().count() != 1 {
            return Err("DISPLAY_INVALID");
        }
        let root = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").ok_or("DISPLAY_MISSING")?);
        if !root.is_absolute() {
            return Err("DISPLAY_INVALID");
        }
        root.join(name)
    };
    if path.as_os_str().len() > 103 {
        return Err("DISPLAY_INVALID");
    }
    let stream = UnixStream::connect(path).map_err(|_| "DISPLAY_CONNECT")?;
    eidolon_shm_fixture::run(stream, Mode::Pattern, Arc::new(AtomicBool::new(false)))
}

fn main() {
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    if args.len() != 1 {
        std::process::exit(2);
    }
    if args[0] == "--version" {
        println!(
            "{}",
            serde_json::json!({"schema":1,"fixture_revision":REVISION,"frames":FRAMES})
        );
        return;
    }
    if args[0] != "--pattern" {
        std::process::exit(2);
    }
    match pattern() {
        Ok(report) => {
            println!(
                "{}",
                serde_json::json!({"schema":1,"result":"pass","fixture_revision":REVISION,
                "uid":unsafe { nix::libc::getuid() },"protocol":report})
            );
        }
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
