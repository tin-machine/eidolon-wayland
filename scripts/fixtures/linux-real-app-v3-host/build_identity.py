#!/usr/bin/env python3
"""Compile the live checkout identity into the runner, without env overrides."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from linux_real_app_v3_evidence import source_snapshot

print(json.dumps(source_snapshot(ROOT), sort_keys=True, separators=(",", ":")))
