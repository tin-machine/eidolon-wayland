//! APK source pins and the single B2/C source inventory.
//!
//! The hashes below attest only to compiled bytes, never to a complete product
//! bundle. C1/C2 have not been integrated. Every execution/journal reservation
//! boundary must additionally call `require_complete`; missing sources are not
//! replaced with empty files or synthetic hashes.
use eidolon_model::linux_desktop_v3::{MAX_RECORD_BYTES, REVISION, parse_json};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

pub type SourceHashes = BTreeMap<String, String>;

const INVENTORY: &[u8] = include_bytes!("../../../../../scripts/linux-desktop-v3-sources.json");

struct Source {
    name: &'static str,
    path: &'static str,
    bytes: &'static [u8],
}

macro_rules! source {
    ($name:literal, $path:literal) => {
        Source {
            name: $name,
            path: $path,
            bytes: include_bytes!(concat!("../../../../../", $path)),
        }
    };
}

// Adding a runtime source requires updating the inventory, this compiled set,
// the Python import closure, bundle validation and their shared tests together.
const COMPILED: &[Source] = &[
    source!(
        "eidolon-linux-session.py",
        "scripts/linux/eidolon-linux-session.py"
    ),
    source!(
        "eidolon_linux_process_owner.py",
        "scripts/linux/eidolon_linux_process_owner.py"
    ),
    source!(
        "eidolon_settings_backend.c",
        "scripts/linux/eidolon_settings_backend.c"
    ),
    source!(
        "gtk3-desktop-demo.c",
        "scripts/fixtures/gtk3-desktop-demo.c"
    ),
    source!(
        "linux-desktop-v3-sources.json",
        "scripts/linux-desktop-v3-sources.json"
    ),
    source!(
        "linux-settings-probe.c",
        "scripts/fixtures/linux-settings-probe.c"
    ),
    source!(
        "linux_desktop_v3_bundle.py",
        "scripts/linux_desktop_v3_bundle.py"
    ),
    source!(
        "linux_desktop_v3_contract.py",
        "scripts/linux_desktop_v3_contract.py"
    ),
    source!(
        "linux_desktop_v3_wire.py",
        "scripts/linux_desktop_v3_wire.py"
    ),
    source!(
        "linux_real_app_inspect.py",
        "scripts/linux_real_app_inspect.py"
    ),
    source!(
        "linux_real_app_manifest.py",
        "scripts/linux_real_app_manifest.py"
    ),
    source!(
        "eidolon_linux_persistent_profile.py",
        "scripts/linux/eidolon_linux_persistent_profile.py"
    ),
    source!(
        "eidolon_linux_desktop_session_v3.py",
        "scripts/linux/eidolon_linux_desktop_session_v3.py"
    ),
    source!(
        "eidolon_linux_desktop_v3.py",
        "scripts/linux/eidolon_linux_desktop_v3.py"
    ),
    source!(
        "linux-desktop-v3-bus.conf.in",
        "scripts/linux/linux-desktop-v3-bus.conf.in"
    ),
];

// This is an explicit integration debt list, NOT an allowed-missing runtime
// source set. No source in this list can be executed by the current APK.
const PENDING: &[(&str, &str)] = &[];

pub fn inventory_bytes() -> &'static [u8] {
    INVENTORY
}

fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// All *present compiled* source bytes. These are not exportable product pins.
pub fn current_hashes() -> SourceHashes {
    COMPILED
        .iter()
        .map(|s| (s.name.to_owned(), hash(s.bytes)))
        .collect()
}

fn fixed_inventory() -> Result<Value, &'static str> {
    let invalid = "APP_V3_SOURCE_INVALID";
    let value = parse_json(INVENTORY, MAX_RECORD_BYTES).map_err(|_| invalid)?;
    let object = value.as_object().ok_or(invalid)?;
    let keys: BTreeSet<_> = object.keys().map(String::as_str).collect();
    if keys
        != BTreeSet::from([
            "schema",
            "protocol_revision",
            "integration",
            "sources",
            "generated",
        ])
        || value["schema"] != json!(1)
        || value["protocol_revision"] != REVISION
    {
        return Err(invalid);
    }
    let sources = value["sources"].as_object().ok_or(invalid)?;
    let expected: BTreeMap<_, _> = COMPILED
        .iter()
        .map(|s| (s.name, s.path))
        .chain(PENDING.iter().copied())
        .collect();
    if expected.len() != COMPILED.len() + PENDING.len()
        || sources.len() != expected.len()
        || expected
            .iter()
            .any(|(name, path)| sources.get(*name).and_then(Value::as_str) != Some(*path))
        || COMPILED.iter().any(|s| s.bytes.is_empty())
    {
        return Err(invalid);
    }
    let integration = value["integration"].as_object().ok_or(invalid)?;
    if integration.len() != 2
        || !integration.contains_key("c1_commit")
        || !integration.contains_key("c2_commit")
        || integration
            .values()
            .any(|v| !v.is_null() && !v.as_str().is_some_and(commit_sha))
    {
        return Err(invalid);
    }
    if value["generated"]
        != json!([
            "app-manifest.json",
            "eidolon-gtk3-desktop",
            "gio/giomodule.cache",
            "gio/libeidolon-settings.so",
            "linux-settings-probe",
            "package-inspection.json",
            "reviewed-package.json",
            "settings-backend.json"
        ])
    {
        return Err(invalid);
    }
    Ok(value)
}

fn commit_sha(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Reject unknown, missing, or changed compiled source pins without normalizing
/// source names. Completeness remains a separate mandatory execution gate.
pub fn validate_hashes(hashes: &SourceHashes) -> Result<(), &'static str> {
    fixed_inventory()?;
    if hashes != &current_hashes() {
        return Err("APP_V3_SOURCE_CHANGED");
    }
    Ok(())
}

/// Enable only after the exact C1/C2 commits and complete transitive closure
/// have been integrated. A remote self-report cannot satisfy this boundary.
pub fn require_complete() -> Result<(), &'static str> {
    let inventory = fixed_inventory()?;
    if inventory["sources"].as_object().map(|v| v.len()) != Some(COMPILED.len())
        || !inventory["integration"]["c1_commit"]
            .as_str()
            .is_some_and(commit_sha)
        || !inventory["integration"]["c2_commit"]
            .as_str()
            .is_some_and(commit_sha)
    {
        return Err("APP_V3_SOURCE_INCOMPLETE");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inventory_and_compiled_closure_are_one_exact_set() {
        fixed_inventory().unwrap();
        validate_hashes(&current_hashes()).unwrap();
        assert_eq!(COMPILED.len(), 15);
        assert_eq!(PENDING.len(), 0);
        assert_eq!(require_complete(), Err("APP_V3_SOURCE_INCOMPLETE"));
        assert_eq!(
            current_hashes()["linux-desktop-v3-sources.json"],
            hash(INVENTORY)
        );
    }

    #[test]
    fn unknown_missing_modified_and_unintegrated_sources_are_rejected() {
        let expected = current_hashes();
        for name in expected.keys() {
            let mut missing = expected.clone();
            missing.remove(name);
            assert!(validate_hashes(&missing).is_err());
            let mut modified = expected.clone();
            modified.insert(name.clone(), "0".repeat(64));
            assert!(validate_hashes(&modified).is_err());
        }
        let mut extra = expected.clone();
        extra.insert("unknown.py".to_owned(), "1".repeat(64));
        assert!(validate_hashes(&extra).is_err());
    }

    #[test]
    fn fixed_v2_sources_keep_the_original_golden() {
        let fixture = parse_json(
            include_bytes!(
                "../../../../../scripts/tests/fixtures/linux-desktop-v2-e401918-sources.json"
            ),
            MAX_RECORD_BYTES,
        )
        .unwrap();
        // Read the actual old embedded constants, never a copied v3 definition.
        for (path, bytes) in [
            (
                "scripts/linux/eidolon-linux-session.py",
                super::super::super::BASE_SOURCE,
            ),
            (
                "scripts/linux/eidolon_linux_desktop.py",
                super::super::super::SOURCE,
            ),
            (
                "scripts/linux/eidolon_linux_process_owner.py",
                super::super::super::OWNER_SOURCE,
            ),
            (
                "scripts/linux/eidolon_linux_desktop_session.py",
                super::super::super::WIRE_SOURCE,
            ),
            (
                "scripts/fixtures/gtk3-desktop-demo.c",
                super::super::super::DEMO_SOURCE,
            ),
        ] {
            assert_eq!(
                fixture["source_sha256"][path].as_str(),
                Some(hash(bytes).as_str())
            );
        }
    }
}
