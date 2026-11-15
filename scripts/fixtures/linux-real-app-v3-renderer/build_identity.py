#!/usr/bin/env python3
"""Compile the renderer fixture's real source and feature identity into its binary."""
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from linux_real_app_v3_evidence import source_input_hashes, source_snapshot


def build_identity():
    manifest = tomllib.loads(Path(__file__).with_name("Cargo.toml").read_text(encoding="utf-8"))
    for name in ("eidolon-core", "eidolon-render"):
        dependency = manifest["dependencies"][name]
        if (type(dependency) is not dict or dependency.get("features") != ["host-acceptance"]
                or dependency.get("optional", False) is not False):
            raise ValueError("I6_SOURCE_MISMATCH")
    source = source_snapshot(ROOT)
    if source["build_features"]:
        raise ValueError("I6_SOURCE_MISMATCH")
    return {"source": {**source, "build_features": ["host-acceptance"]},
            "inputs": source_input_hashes()}


if __name__ == "__main__":
    print(json.dumps(build_identity(), sort_keys=True, separators=(",", ":")))
