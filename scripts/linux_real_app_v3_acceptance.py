"""I6 evidence entrypoints; real Host case execution is a later implementation.

The fixed profile remains required even when no product runner is available.
Only the independent validator decides the stage outcome. Revalidating a saved
report never changes its files or creates a new run.
"""
from __future__ import annotations

import json
from pathlib import Path

import linux_real_app_v3_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]


def _source_snapshot():
    try:
        return evidence.source_snapshot(ROOT)
    except (OSError, ValueError):
        # Missing Git/source access is an unmet environment prerequisite. It
        # cannot create provenance or turn revalidation into a passing run.
        return None


def run_acceptance(artifact_root):
    """Record the current source and every unperformed required variant.

    This first implementation supplies the evidence contract and CLI boundary.
    It does not spawn an application or produce Host/SDK/Android evidence.
    """
    # Reuse the established no-follow, fresh/private artifact-root boundary.
    # The late import avoids coupling feasibility to I6 at module import time.
    from linux_real_app_check import private_artifact_root

    artifact = private_artifact_root(artifact_root)
    before = _source_snapshot()
    report = evidence.incomplete_report(before, reason=("I6_RUNNER_UNAVAILABLE" if before is not None
                                                       else "I6_SOURCE_UNAVAILABLE"))
    report["source"]["after"] = _source_snapshot()
    raw = json.dumps(report, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False).encode("utf-8")
    result = evidence.validate_acceptance(raw, artifact, before)
    evidence.write_report(artifact / "acceptance.json", report)
    evidence.write_report(artifact / "acceptance-validation.json", result)
    return result


def validate_saved_acceptance(report_path, artifact_root):
    """Read-only revalidation against this checkout, never report-owned pins."""
    expected_source = _source_snapshot()
    raw = evidence.read_report(Path(report_path))
    return evidence.validate_acceptance(raw, Path(artifact_root), expected_source)


def invalid_result():
    """Do not expose filesystem paths or arbitrary exception payloads."""
    return evidence.invalid_result()


def cancelled_result():
    """A CLI interrupt is not evidence of successful product cleanup."""
    return evidence.cancelled_result()
