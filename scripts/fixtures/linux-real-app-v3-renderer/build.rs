use std::{env, path::PathBuf, process::Command};

fn main() {
    let directory = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let root = directory.join("../../..").canonicalize().unwrap();
    // The entire tracked tree is evidence, including Python's independent oracle.
    for path in [
        "native",
        "scripts",
        "docs",
        "android",
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        ".github",
    ] {
        println!("cargo:rerun-if-changed={}", root.join(path).display());
    }
    for name in ["HEAD", "index", "packed-refs"] {
        let output = Command::new("git")
            .args(["rev-parse", "--git-path", name])
            .current_dir(&root)
            .output()
            .unwrap();
        assert!(output.status.success(), "Git identity unavailable");
        println!(
            "cargo:rerun-if-changed={}",
            root.join(String::from_utf8(output.stdout).unwrap().trim())
                .display()
        );
    }
    let branch = Command::new("git")
        .args(["symbolic-ref", "-q", "HEAD"])
        .current_dir(&root)
        .output()
        .unwrap();
    if branch.status.success() {
        let branch = String::from_utf8(branch.stdout).unwrap();
        let output = Command::new("git")
            .args(["rev-parse", "--git-path", branch.trim()])
            .current_dir(&root)
            .output()
            .unwrap();
        assert!(output.status.success(), "Git ref unavailable");
        println!(
            "cargo:rerun-if-changed={}",
            root.join(String::from_utf8(output.stdout).unwrap().trim())
                .display()
        );
    }
    let output = Command::new("python3")
        .arg(directory.join("build_identity.py"))
        .output()
        .expect("python3 required");
    assert!(
        output.status.success(),
        "I6 renderer build provenance unavailable"
    );
    let identity = String::from_utf8(output.stdout).expect("UTF-8 build provenance");
    println!(
        "cargo:rustc-env=EIDOLON_I6_RENDERER_BUILD_IDENTITY={}",
        identity.trim()
    );
}
