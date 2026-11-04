use std::{env, path::PathBuf, process::Command};

fn git(root: &std::path::Path, args: &[&str]) -> Option<String> {
    let output = Command::new("git")
        .args(args)
        .current_dir(root)
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

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
            "scripts/build-native",
            "scripts/check-native-package",
            "scripts/termux/eidolon-session.py",
            "android/app/src",
            "android/app/build.gradle.kts",
            "android/build.gradle.kts",
            "android/settings.gradle.kts",
            "android/gradle",
        ] {
            println!("cargo:rerun-if-changed={}", root.join(path).display());
        }
        for name in ["HEAD", "index", "packed-refs"] {
            if let Some(path) = git(&root, &["rev-parse", "--git-path", name]) {
                let path = root.join(path);
                if path.exists() {
                    println!("cargo:rerun-if-changed={}", path.display());
                }
            }
        }
        if let Some(path) = git(&root, &["symbolic-ref", "-q", "HEAD"])
            .and_then(|branch| git(&root, &["rev-parse", "--git-path", &branch]))
        {
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
