"""N06 independent file observations; GUI writes are never simulated.

Reuses the frozen N05 bytes and hostile-path-safe nofollow/fstat suboracle.
The fixture creates *only* input.txt. For the two Save As variants the output
must be created by Mousepad. For the others it MUST remain absent.
"""
from __future__ import annotations

import os
from pathlib import Path
import linux_real_app_v3_n05_files as n05

VARIANTS_WITH_OUTPUT = frozenset(("save_as_confirm", "unsaved_close_cancel"))
RECEIPT_KIND = "eidolon-i6-n06-file-proof-v1"
FIELDS = frozenset(("dev","inode","uid","nlink","size","mtime_ns","ctime_ns","sha256"))


def public(value: dict) -> dict:
    return {k: value[k] for k in sorted(FIELDS)}


def verify_after(dir_fd: int, uid: int, before: dict, variant: str) -> dict:
    """Independent supervisor snapshot, never a claim about GUI input."""
    n05.fixed_contract()
    if variant in VARIANTS_WITH_OUTPUT:
        outcome = n05.verify_after(dir_fd, uid, before)
        return {"baseline": before, "input": outcome["input"],
                "output": outcome["output"], "output_absent": False}
    n05.need(variant in ("open_cancel","close_confirm"), "N06_VARIANT_INVALID")
    current = n05.snapshot(dir_fd, n05.INPUT_NAME, uid)
    n05.need(current["bytes"] == n05.INPUT_BYTES
             and current["sha256"] == n05.INPUT_SHA256
             and current == before, "N06_INPUT_CHANGED")
    n05.checked_absent(dir_fd, n05.OUTPUT_NAME)
    n05.need(os.listdir(dir_fd) == [n05.INPUT_NAME], "N06_UNEXPECTED_FILES")
    return {"baseline": before, "input": current,
            "output": None, "output_absent": True}


def bounded_receipt(outcome: dict, run_id: str, capture_id: str) -> dict:
    n05.need(all(isinstance(s, str) and 0 < len(s) <= 128
                 for s in (run_id,capture_id)), "N06_OWNER_INVALID")
    return {"schema":1,"kind":RECEIPT_KIND,"run_id":run_id,
            "capture_id":capture_id,"owner":"disposable-supervisor",
            "baseline":public(outcome["baseline"]),
            "input":public(outcome["input"]),
            "output":public(outcome["output"]) if outcome["output"] else None,
            "output_absent":outcome["output_absent"]}
