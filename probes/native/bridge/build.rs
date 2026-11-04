fn main() {
    for name in [
        "EIDOLON_PROBE_COMMIT",
        "EIDOLON_PROBE_DIGEST",
        "EIDOLON_PROBE_VERSION",
    ] {
        println!("cargo:rerun-if-env-changed={name}");
        println!(
            "cargo:rustc-env={name}={}",
            std::env::var(name).unwrap_or_else(|_| "unrecorded".into())
        );
    }
}
