pub fn json() -> &'static str {
    env!("EIDOLON_BUILD_INFO_JSON")
}

#[cfg(test)]
mod tests {
    #[test]
    fn embedded_identity_obeys_model_contract() {
        let info: eidolon_model::build_info::BuildInfo =
            serde_json::from_str(super::json()).unwrap();
        assert_eq!(info.schema, 1);
        assert!(info.apk_version_code > 0);
        assert_eq!(info.crate_version, env!("CARGO_PKG_VERSION"));
        assert_eq!(info.source_digest.len(), 64);
    }
}
