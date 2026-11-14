use super::*;
use presentation::{NativeOrigin, Observation, Parent, Startup};

fn fixture() -> Value {
    serde_json::from_str(include_str!(
        "../../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
    ))
    .unwrap()
}
fn item<'a>(fixture: &'a Value, id: &str) -> &'a Value {
    fixture["records"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["id"] == id)
        .unwrap()
}
fn raw(fixture: &Value, id: &str) -> Vec<u8> {
    item(fixture, id)["raw"]
        .as_str()
        .unwrap()
        .as_bytes()
        .to_vec()
}
fn parse(kind: &str, bytes: &[u8], fixture: &Value) -> Result<Value> {
    Ok(match kind {
        "pins" => Pins::parse(bytes)?.record(),
        "manifest" => Manifest::parse(bytes)?.record(),
        "context" => Context::parse(bytes)?.record(),
        "storage" => StorageRecord::parse(bytes)?.record(),
        "capture" => StorageCapture::parse(bytes)?.record(),
        "owner" => StorageOwner::parse(bytes)?.record(),
        "selection" => Selection::parse(bytes)?.record(),
        "origin" => NativeOrigin::parse(bytes)?.record(),
        "observation" => Observation::parse(bytes)?.record(),
        "bootstrap" => json!(Bootstrap::parse(bytes)?.vector()),
        "catalog" => validate_catalog(
            bytes,
            &Context::parse(&raw(fixture, "context"))?,
            &Pins::parse(&raw(fixture, "pins"))?,
        )?,
        "parent" => record(&Parent::parse(bytes)?),
        "target" => record(&presentation::parse_target(bytes)?),
        "json" => parse_json(bytes, MAX_RECORD_BYTES)?,
        kind if kind.starts_with("reason_") => {
            parse_reason(bytes, kind.strip_prefix("reason_").unwrap())?
        }
        _ => panic!("unknown vector kind"),
    })
}

#[test]
fn fixed_bytes_and_digests_match_independently_of_python() {
    let f = fixture();
    assert_eq!(
        f["purpose"],
        "synthetic-contract-only; never production pins or acceptance"
    );
    let mut ids = std::collections::BTreeSet::new();
    for case in f["records"].as_array().unwrap() {
        assert!(ids.insert(case["id"].as_str().unwrap()));
        let parsed = parse(
            case["kind"].as_str().unwrap(),
            case["raw"].as_str().unwrap().as_bytes(),
            &f,
        )
        .unwrap_or_else(|e| panic!("{}: {e}", case["id"]));
        let bytes = canonical_json(&parsed).unwrap();
        assert_eq!(
            bytes,
            case["canonical_utf8"].as_str().unwrap().as_bytes(),
            "{}",
            case["id"]
        );
        assert_eq!(
            digest(&parsed).unwrap(),
            case["sha256"].as_str().unwrap(),
            "{}",
            case["id"]
        );
        assert!(!bytes.ends_with(b"\n"));
    }
}

#[test]
fn raw_negative_vectors_reject_duplicates_types_versions_and_bounds() {
    let f = fixture();
    let mut ids = std::collections::BTreeSet::new();
    for case in f["invalid"].as_array().unwrap() {
        assert!(ids.insert(case["id"].as_str().unwrap()));
        let bytes = if let Some(hex) = case["raw_hex"].as_str() {
            hex.as_bytes()
                .chunks_exact(2)
                .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
                .collect()
        } else {
            case["raw"].as_str().unwrap().as_bytes().to_vec()
        };
        let result = parse(case["kind"].as_str().unwrap(), &bytes, &f);
        assert!(result.is_err(), "accepted {}", case["id"]);
        if let Some(expected) = case["error"].as_str() {
            assert_eq!(result.unwrap_err().0, expected, "{}", case["id"]);
        }
    }
}

#[test]
fn fixed_preimages_manifest_build_context_catalog_and_argv() {
    let f = fixture();
    let pins = Pins::parse(&raw(&f, "pins")).unwrap();
    let context = Context::parse(&raw(&f, "context")).unwrap();
    Manifest::parse(&raw(&f, "manifest"))
        .unwrap()
        .matches_pins(&pins)
        .unwrap();
    assert_eq!(
        pins.build_input(),
        serde_json::from_slice::<Value>(&raw(&f, "build_input")).unwrap()
    );
    assert_eq!(
        pins.build_id(),
        item(&f, "build_input")["sha256"].as_str().unwrap()
    );
    assert_eq!(
        context.scope(),
        item(&f, "context")["sha256"].as_str().unwrap()
    );
    assert_eq!(
        catalog_revision_input(&context, &pins),
        serde_json::from_slice::<Value>(&raw(&f, "catalog_input")).unwrap()
    );
    assert_eq!(
        catalog_revision(&context, &pins),
        item(&f, "catalog_input")["sha256"].as_str().unwrap()
    );
    assert_eq!(
        catalog_descriptor(&context, &pins),
        serde_json::from_slice::<Value>(&raw(&f, "catalog")).unwrap()
    );
    assert!(
        catalog_descriptor(&context, &pins)
            .get("available")
            .is_none()
    );
    Selection::parse(&raw(&f, "selection"))
        .unwrap()
        .matches(&context, &pins)
        .unwrap();
    let serve = Bootstrap::serve(context.clone(), pins.clone());
    let recover = Bootstrap::recover(
        context.clone(),
        pins.clone(),
        "11111111-2222-3333-4444-555555555555",
    )
    .unwrap();
    assert_eq!(
        json!(serve.vector()),
        serde_json::from_slice::<Value>(&raw(&f, "serve")).unwrap()
    );
    assert_eq!(
        json!(recover.vector()),
        serde_json::from_slice::<Value>(&raw(&f, "recover")).unwrap()
    );
    assert_eq!(serve.vector().len(), 14);
    assert_eq!(recover.vector().len(), 15);
    for id in ["serve", "recover", "recover_unknown_boot"] {
        Bootstrap::parse(&raw(&f, id))
            .unwrap()
            .matches(&context, &pins)
            .unwrap();
    }
    let mut changed = pins.record();
    changed["app_manifest_sha256"] = json!("f".repeat(64));
    assert!(
        Manifest::parse(&raw(&f, "manifest"))
            .unwrap()
            .matches_pins(&Pins::parse(&canonical_json(&changed).unwrap()).unwrap())
            .is_err()
    );
}

#[test]
fn stale_captures_id_swaps_and_old_selection_do_not_authorize() {
    let f = fixture();
    let pins = Pins::parse(&raw(&f, "pins")).unwrap();
    let context = Context::parse(&raw(&f, "context")).unwrap();
    let storage = StorageRecord::parse(&raw(&f, "storage")).unwrap();
    let selection = Selection::parse(&raw(&f, "selection")).unwrap();
    for case in f["relations"].as_array().unwrap() {
        let bytes = case["raw"].as_str().unwrap().as_bytes();
        let result = match case["kind"].as_str().unwrap() {
            "selection_context" => Context::parse(bytes).and_then(|c| selection.matches(&c, &pins)),
            "selection_pins" => Pins::parse(bytes).and_then(|p| selection.matches(&context, &p)),
            "storage_capture" => {
                StorageCapture::parse(bytes).and_then(|c| storage.matches_capture(&c))
            }
            "owner_context" => StorageOwner::parse(bytes).and_then(|o| o.matches_context(&context)),
            "bootstrap_context" => Bootstrap::parse(bytes).and_then(|b| b.matches(&context, &pins)),
            "origin_context" => {
                NativeOrigin::parse(bytes).and_then(|o| o.matches(&context, &selection, &pins))
            }
            _ => panic!("unknown relation"),
        };
        assert!(result.is_err(), "accepted {}", case["id"]);
    }
}

#[test]
fn storage_transition_keeps_legacy_binding_and_rejects_partial_pairing() {
    let f = fixture();
    let storage = StorageRecord::parse(&raw(&f, "storage")).unwrap();
    let context = Context::parse(&raw(&f, "context")).unwrap();
    let profile = crate::connection_profile::Profile::parse(
        &serde_json::to_string(&f["profile"]).unwrap(),
        f["profile"]["installation"].as_str().unwrap(),
    )
    .unwrap();
    assert_eq!(profile.binding(), f["profile_binding"].as_str().unwrap());
    let capture = StorageCapture::from_profile(&profile).unwrap();
    assert_eq!(
        capture.record(),
        serde_json::from_slice::<Value>(&raw(&f, "capture")).unwrap()
    );
    storage.matches_capture(&capture).unwrap();
    storage.matches_context(&context).unwrap();
    StorageOwner::parse(&raw(&f, "owner"))
        .unwrap()
        .matches_context(&context)
        .unwrap();
    let host = serde_json::from_slice::<Value>(&raw(&f, "host_user_input")).unwrap();
    let key = Hash::parse(host["host_key_sha256"].as_str().unwrap()).unwrap();
    assert_eq!(host_user_input(&key, profile.username()).unwrap(), host);
    assert_eq!(
        host_user_binding(&key, profile.username()).unwrap(),
        storage.host_user_binding.as_str()
    );
    for case in f["storage_transitions"].as_array().unwrap() {
        let new = StorageRecord::parse(&canonical_json(&case["new"]).unwrap()).unwrap();
        let capture = StorageCapture::parse(&canonical_json(&case["capture"]).unwrap()).unwrap();
        let expected = Hash::parse(case["expected_old_sha256"].as_str().unwrap()).unwrap();
        let result = validate_storage_transition(
            &storage,
            &new,
            &expected,
            &capture,
            case["explicit_save"].as_bool().unwrap(),
            case["fully_reclaimed"].as_bool().unwrap(),
        );
        match case["expected_change"].as_str() {
            Some("retain") => assert_eq!(result, Ok(StorageChange::Retain), "{}", case["id"]),
            Some("replace") => assert_eq!(result, Ok(StorageChange::Replace), "{}", case["id"]),
            None => assert!(result.is_err(), "{}", case["id"]),
            _ => panic!("unknown storage result"),
        }
    }
}

#[test]
fn remote_revision_and_alternative_deserializer_cannot_bypass_validation() {
    let f = fixture();
    let pins = Pins::parse(&raw(&f, "pins")).unwrap();
    assert_eq!(
        DecoderVersion::selected(&pins, REVISION),
        Ok(DecoderVersion::V3)
    );
    for revision in ["eidolon-linux-desktop-v2", "eidolon-linux-desktop-v4", ""] {
        assert!(DecoderVersion::selected(&pins, revision).is_err());
    }
    let mut p = pins.record();
    p["schema"] = json!(1);
    assert!(serde_json::from_value::<Pins>(p).is_err());
    let mut c = Context::parse(&raw(&f, "context")).unwrap().record();
    c["uid"] = json!(0);
    assert!(serde_json::from_value::<Context>(c).is_err());
    let mut m = Manifest::parse(&raw(&f, "manifest")).unwrap().record();
    m["window_app_ids"] = json!(["org.xfce.mousepad"]);
    assert!(serde_json::from_value::<Manifest>(m).is_err());
    for case in f["invalid"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["kind"] == "observation")
    {
        if let Ok(v) = parse_json(case["raw"].as_str().unwrap().as_bytes(), MAX_RECORD_BYTES) {
            assert!(
                serde_json::from_value::<Observation>(v).is_err(),
                "alternate decoder accepted {}",
                case["id"]
            );
        }
    }
    let mut observation = Observation::parse(&raw(&f, "observation"))
        .unwrap()
        .record();
    observation["now_ms"] = json!(u64::MAX);
    assert!(serde_json::from_value::<Observation>(observation).is_err());
}

#[test]
fn size_boundaries_count_bytes_and_allow_only_bounded_whitespace() {
    let f = fixture();
    for (kind, limit) in [
        ("pins", MAX_PINS_BYTES),
        ("manifest", MAX_RECORD_BYTES),
        ("context", MAX_RECORD_BYTES),
    ] {
        let mut bytes = raw(&f, kind);
        bytes.resize(limit, b' ');
        assert!(parse(kind, &bytes, &f).is_ok());
        bytes.push(b' ');
        assert!(parse(kind, &bytes, &f).is_err());
    }
}

#[test]
fn presentation_sequences_use_the_same_owner_target_deadline_and_failure_vectors() {
    let f = fixture();
    let origin = NativeOrigin::parse(&raw(&f, "origin")).unwrap();
    origin
        .matches(
            &Context::parse(&raw(&f, "context")).unwrap(),
            &Selection::parse(&raw(&f, "selection")).unwrap(),
            &Pins::parse(&raw(&f, "pins")).unwrap(),
        )
        .unwrap();
    let mut ids = std::collections::BTreeSet::new();
    for case in f["presentation"].as_array().unwrap() {
        assert!(ids.insert(case["id"].as_str().unwrap()));
        let selected_origin = if case.get("origin").is_some() {
            NativeOrigin::parse(&canonical_json(&case["origin"]).unwrap()).unwrap()
        } else {
            origin.clone()
        };
        let before = serde_json::from_value(case["before"].clone()).unwrap();
        let mut state =
            Startup::begin(selected_origin, case["t0_ms"].as_u64().unwrap(), before).unwrap();
        for (index, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            let bytes = canonical_json(&step["observation"]).unwrap();
            let old = state.summary();
            match step["error"].as_str() {
                Some("parse") => assert!(
                    Observation::parse(&bytes).is_err(),
                    "{} step {index}",
                    case["id"]
                ),
                Some(expected) => {
                    let observed = Observation::parse(&bytes).unwrap();
                    match state.advance(&observed) {
                        Err(e) => assert_eq!(e.0, expected, "{} step {index}", case["id"]),
                        Ok(_) => panic!("accepted {} step {index}", case["id"]),
                    }
                }
                None => {
                    state = state
                        .advance(&Observation::parse(&bytes).unwrap())
                        .unwrap_or_else(|e| panic!("{} step {index}: {e}", case["id"]));
                    assert_eq!(
                        state.summary(),
                        step["expected"],
                        "{} step {index}",
                        case["id"]
                    );
                }
            }
            if step.get("error").is_some() {
                assert_eq!(state.summary(), old);
            }
        }
    }
}

#[test]
fn native_observation_builder_rejects_foreign_evidence_and_unfresh_frames() {
    use crate::{
        ids::{CommandSeq, SceneEpoch, WindowId},
        surface::TargetKey,
    };
    use presentation::{
        Life, NativeAckFact, NativeCreateIdentity, NativeFrameFact, NativePanelFact, NativeSdkFact,
        NativeWindowFact, Role,
    };
    let f = fixture();
    let owner = NativeOrigin::parse(&raw(&f, "origin")).unwrap();
    let main = WindowId::new(42).unwrap();
    let scene = SceneEpoch::new(8).unwrap();
    let good_target = TargetKey::from_raw(owner.session().get(), 42, 8, 1).unwrap();
    let stale_target = TargetKey::from_raw(owner.session().get(), 42, 8, 2).unwrap();
    let create = NativeCreateIdentity {
        runtime: owner.runtime(),
        target: good_target,
        command_seq: CommandSeq::new(7).unwrap(),
    };
    let adopted = Observation::native_builder(owner.clone(), 1001)
        .lifecycle(true, Some(scene), true)
        .transport(Some(owner.clone()), None, false, false)
        .windows(
            1,
            vec![NativeWindowFact {
                origin: owner.clone(),
                window: main,
                role: Role::Main,
                life: Life::Mapped,
                app_id: Some("mousepad".into()),
                parent: Parent::None {},
            }],
        )
        .finish()
        .unwrap();
    let startup = Startup::begin(owner.clone(), 1000, vec![])
        .unwrap()
        .advance(&adopted)
        .unwrap();
    assert_eq!(startup.summary()["state"], "awaiting_sdk");
    // Native accepted ACK, an app binding and a current *content* frame are
    // distinct. A blank swap or a diagnostic frame cannot be made Ready.
    let make = |target: TargetKey,
                content_frame_seen: bool,
                windows_mode: bool,
                diagnostic: bool,
                ack: bool,
                now: u64| {
        Observation::native_builder(owner.clone(), now)
            .lifecycle(true, Some(scene), true)
            .windows(
                1,
                vec![NativeWindowFact {
                    origin: owner.clone(),
                    window: main,
                    role: Role::Main,
                    life: Life::Mapped,
                    app_id: Some("mousepad".into()),
                    parent: Parent::None {},
                }],
            )
            .current_create(Some(create.clone()))
            .panel(Some(NativePanelFact {
                create: create.clone(),
                applied: true,
                error: false,
            }))
            .ack(ack.then_some(NativeAckFact {
                create: create.clone(),
                success: true,
            }))
            .sdk(Some(NativeSdkFact {
                runtime: owner.runtime(),
                target: good_target,
                bound: true,
                diagnostic,
            }))
            .frame(Some(NativeFrameFact {
                runtime: owner.runtime(),
                target,
                frames: 9,
                paused: false,
                presentation_paused: false,
                windows_mode,
                content_frame_seen,
            }))
            .finish()
    };
    assert!(
        !make(good_target, false, true, false, true, 1100)
            .unwrap()
            .current_evidence()
            .unwrap()
            .renderer_frame_seen
    );
    assert!(
        !make(good_target, true, false, false, true, 1100)
            .unwrap()
            .current_evidence()
            .unwrap()
            .renderer_frame_seen
    );
    let no_ack = startup
        .advance(&make(good_target, true, true, false, false, 1100).unwrap())
        .unwrap();
    assert_eq!(no_ack.summary()["state"], "awaiting_sdk");
    let diagnostic = startup
        .advance(&make(good_target, true, true, true, true, 1100).unwrap())
        .unwrap();
    assert_eq!(diagnostic.summary()["state"], "awaiting_sdk");
    assert_eq!(
        make(stale_target, true, true, false, true, 1100)
            .err()
            .unwrap()
            .0,
        "PRESENTATION_STALE"
    );
    let ready = startup
        .advance(&make(good_target, true, true, false, true, 1100).unwrap())
        .unwrap();
    assert_eq!(ready.summary()["state"], "ready");
    let timeout = startup
        .advance(&make(good_target, true, true, false, true, 11001).unwrap())
        .unwrap();
    assert_eq!(timeout.summary()["state"], "stopping");
    assert_eq!(
        timeout.summary()["first_failure"],
        "APP_PRESENTATION_TIMEOUT"
    );
    let cancelled = ready
        .advance(
            &Observation::native_builder(owner, 1101)
                .lifecycle(false, Some(scene), false)
                .transport(None, None, true, false)
                .windows(
                    1,
                    vec![NativeWindowFact {
                        origin: ready.origin().clone(),
                        window: main,
                        role: Role::Main,
                        life: Life::Mapped,
                        app_id: Some("mousepad".into()),
                        parent: Parent::None {},
                    }],
                )
                .finish()
                .unwrap(),
        )
        .unwrap();
    assert_eq!(cancelled.summary()["state"], "stopping");
    assert!(
        cancelled.summary()["first_failure"].is_null(),
        "normal post-Ready Stop must not manufacture startup cancellation"
    );
}
