use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

fn digest(path: &Path) -> String {
    Sha256::digest(std::fs::read(path).expect("dependency input missing"))
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
fn main() {
    let root = PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap())
        .join("../..")
        .canonicalize()
        .unwrap();
    let target = if std::env::var("CARGO_CFG_TARGET_OS").unwrap() == "android" {
        "android"
    } else {
        "host"
    };
    let marker = root.join(format!("native/generated/xkbcommon-{target}.json"));
    println!("cargo:rerun-if-changed={}", marker.display());
    let bytes = std::fs::read(&marker).expect(
        "先に scripts/build-native-deps --target host または --target android を実行してください",
    );
    let metadata: serde_json::Value =
        serde_json::from_slice(&bytes).expect("invalid dependency marker");
    assert_eq!(metadata["schema"], 1);
    assert_eq!(metadata["target"], target);
    for name in [
        "native/deps.lock.json",
        "scripts/build-native-deps",
        "scripts/native-keymap-generator.c",
    ] {
        let path = root.join(name);
        println!("cargo:rerun-if-changed={}", path.display());
        assert_eq!(
            metadata["inputs"][name],
            digest(&path),
            "依存入力が変更されています。scripts/build-native-depsを再実行してください"
        );
    }
    let library = PathBuf::from(
        metadata["library"]
            .as_str()
            .expect("missing static archive"),
    );
    assert_eq!(library.file_name().unwrap(), "libxkbcommon.a");
    assert_eq!(
        metadata["sha256"],
        digest(&library),
        "static archive changed"
    );
    println!("cargo:rerun-if-changed={}", library.display());
    println!(
        "cargo:rustc-link-search=native={}",
        library.parent().unwrap().display()
    );
    println!("cargo:rustc-link-lib=static=xkbcommon");
    println!("cargo:rustc-link-lib=m");
}
