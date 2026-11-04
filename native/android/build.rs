use std::{env, path::PathBuf, process::Command};

fn main() {
    for key in [
        "EIDOLON_BUILD_INFO_JSON",
        "EIDOLON_SOURCE_COMMIT",
        "EIDOLON_SOURCE_DIGEST",
        "EIDOLON_SOURCE_DIRTY",
    ] {
        println!("cargo:rerun-if-env-changed={key}");
    }
    let root = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("../..");
    // 直接host testする場合も同じ入口を使う。Android製品buildはwrapperがJSONを渡す。
    let json = env::var("EIDOLON_BUILD_INFO_JSON").unwrap_or_else(|_| {
        for path in [
            "native",
            "Cargo.toml",
            "Cargo.lock",
            "rust-toolchain.toml",
            "scripts/native-build-inputs",
            "scripts/native_build.py",
        ] {
            println!("cargo:rerun-if-changed={}", root.join(path).display());
        }
        let output = Command::new("python3")
            .arg(root.join("scripts/native-build-inputs"))
            .args([
                "--version",
                "1",
                "--profile",
                &env::var("PROFILE").unwrap(),
                "--target",
                &env::var("TARGET").unwrap(),
            ])
            .output()
            .expect("python3 is required for build identity");
        assert!(
            output.status.success(),
            "build identity: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        String::from_utf8(output.stdout).unwrap().trim().to_owned()
    });
    println!("cargo:rustc-env=EIDOLON_BUILD_INFO_JSON={json}");
}
