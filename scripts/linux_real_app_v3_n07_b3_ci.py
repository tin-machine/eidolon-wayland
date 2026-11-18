"""Reopen bounded B3 observations and prepare an explicitly unapproved packet.

Called after the unchanged B2 saved-receipt gate, including on failure. No
container success flag, candidate digest or source screenshot grants approval.
The uploaded artifact's ID/digest are attached after download, never invented
inside the archive whose own digest does not exist yet.
"""
from __future__ import annotations

import argparse
import copy
import os
from pathlib import Path
import re
import stat

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n07_b2_probe import inspect
import linux_real_app_v3_n07_review as review

MAX_CONTEXT_BYTES = 4 * 1024 * 1024
EXTRA_FILES = {"n07-b3-context.json": MAX_CONTEXT_BYTES,
               "n07-b3-fingerprint.json": 1024 * 1024}
OPTIONAL_FILES = {"n07-b3-collection.json": 65536}


def read_bounded(path: Path, limit: int) -> tuple[bytes, dict]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        review.need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and 0 < before.st_size <= limit, "N07_B3_FILE_BOUNDS")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after, named = os.fstat(fd), os.stat(path, follow_symlinks=False)
        identity = lambda v: (v.st_dev, v.st_ino, v.st_size, v.st_mtime_ns, v.st_ctime_ns)
        review.need(len(raw) == before.st_size and identity(before) == identity(after)
                    == identity(named), "N07_B3_FILE_CHANGED")
        parsed = evidence.parse(raw, limit=limit)
        review.need(type(parsed) is dict, "N07_B3_FILE_SHAPE")
        return raw, parsed
    finally:
        os.close(fd)


def _container(value: dict, source: dict, pr_head: str) -> None:
    review.need(type(value.get("schema")) is int and value["schema"] == 1
                and value.get("source_commit") == source["commit"]
                and value.get("pr_head_sha") == (pr_head or None)
                and type(value.get("image_id")) is str
                and re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"]) is not None
                and type(value.get("attach_exit")) is int and value["attach_exit"] == 0
                and type(value.get("container_exit")) is int and value["container_exit"] == 0
                and value.get("external_rescue") is False
                and value.get("network") == "none" and value.get("host_mounts") is False
                and value.get("formal_i6_passed") is False
                and value.get("n07_assertions_verified") == [], "N07_B3_CONTAINER_REJECTED")


def reject_mutations(candidate: dict, inputs: dict) -> None:
    """Revalidate real saved bytes; changed proposal digests never mean approval."""
    raw = review.canonical(candidate)
    digest = review.sha(raw)
    for mutate in (
        lambda c: c["approval"].update(status="approved", reviewer="invented"),
        lambda c: c["coverage"]["observed_states"].append("second_on_checkmark"),
        lambda c: c["states"].update(second_on_checkmark=c["states"]["first_on_checkmark"]),
        lambda c: c["proposal"].update(unreviewed_override=True),
        lambda c: c.update(menu_identity_verified=True),
    ):
        altered = copy.deepcopy(candidate)
        mutate(altered)
        encoded = review.canonical(altered)
        # Even a newly computed digest cannot detach the proposal from saved
        # provenance or assert approval; the externally retained original hash
        # independently detects *all* changes to masks/tolerances/fingerprint.
        for expected in (digest, review.sha(encoded)):
            try:
                review.validate_candidate(encoded, expected, **inputs)
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            raise review.ReviewError("N07_B3_MUTATION_ACCEPTED")
    try:
        review.ensure_independent(candidate, candidate["run_id"], candidate["capture_id"])
    except review.ReviewError:
        pass
    else:
        raise review.ReviewError("N07_B3_SELF_REFERENCE_ACCEPTED")


def prepare(incoming: Path, output: Path, source_root: Path,
            source_commit: str, pr_head: str, ci: dict | None = None) -> bool:
    ci = os.environ if ci is None else ci
    report = {"schema": 1, "revision": "eidolon-i6-n07-b3-preparation-v1",
              "status": "rejected", "reason": "N07_B3_INPUT_MISSING",
              "candidate_created": False, "product_cleanup_verified": False,
              "reviewed_golden": False, "verified_n07_assertions": [],
              "host_v3_acceptance_passed": False, "observations": {}}
    try:
        extra = {}
        # Export only named, bounded JSON observations, even when the product
        # failed. No recursive Docker directory/HOME/log/credential export.
        collection_errors = []
        for name, limit in {**EXTRA_FILES, **OPTIONAL_FILES}.items():
            try:
                raw, parsed = read_bounded(incoming / name, limit)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
                # A malformed first observation cannot discard the other
                # independently readable evidence when the container is removed.
                report["observations"][name] = {"status": "rejected"}
                collection_errors.append(name)
                continue
            with (output / name).open("xb") as stream:
                stream.write(raw)
            (output / name).chmod(0o600)
            if name in EXTRA_FILES:
                extra[name] = (raw, parsed)
            report["observations"][name] = {"bytes": len(raw), "sha256": review.sha(raw)}
        source = evidence.source_snapshot(source_root)
        review.need(source["dirty"] is False and source["commit"] == source_commit,
                    "N07_B3_SOURCE_MISMATCH")
        _, native = read_bounded(output / "n07-b2-native.json", 1024 * 1024)
        _, environment = read_bounded(output / "n07-b2-environment.json", 1024 * 1024)
        _, validation = read_bounded(output / "n07-b2-validation.json", 1024 * 1024)
        _, container = read_bounded(output / "container.json", 1024 * 1024)
        _container(container, source, pr_head)
        review.need(evidence.canonical(validation.get("source")) == evidence.canonical(source),
                    "N07_B3_SOURCE_MISMATCH")
        inspect(native)
        review.need(environment.get("run_id") == native.get("run_id")
                    and environment.get("source_sha256") == native.get("source_sha256")
                    == source["tree_sha256"], "N07_B3_ENVIRONMENT_MISMATCH")
        census = environment.get("final")
        review.need(type(census) is dict and set(census) == {
            "app_process_count", "driver_child_count", "sshd_child_count",
            "journal_entries", "journal_lock_held", "profile_lock_held"}
            and all(type(census[key]) is int and census[key] == 0 for key in (
                "app_process_count", "driver_child_count", "sshd_child_count", "journal_entries"))
            and census["journal_lock_held"] is False and census["profile_lock_held"] is False,
            "N07_B3_CLEANUP_MISSING")
        report["product_cleanup_verified"] = True
        review.need(not collection_errors, "N07_B3_OBSERVATION_INVALID")
        review.need(set(extra) == set(EXTRA_FILES), "N07_B3_OBSERVATION_MISSING")
        provenance = {
            "repository": review.REPOSITORY,
            "workflow_run_id": ci.get("GITHUB_RUN_ID") or None,
            "run_attempt": ci.get("GITHUB_RUN_ATTEMPT") or None,
            "artifact_id": None,
            "artifact_name": "linux-desktop-v3-n07-b3-" + source_commit,
            "artifact_archive_sha256": None, "artifact_status": "pending_upload",
            "source_commit": source_commit, "pr_head": pr_head or None,
        }
        inputs = dict(native=native, environment=environment, validation=validation,
                      provenance=provenance, fingerprint=extra["n07-b3-fingerprint.json"][1],
                      context_raw=extra["n07-b3-context.json"][0])
        # A new B3 run must collect all four context captures. Legacy B2 packets
        # may separately be exported without a sidecar by the review CLI.
        review.need(native.get("review_context", {}).get("complete") is True,
                    "N07_B3_CONTEXT_INCOMPLETE")
        result = review.export_bundle(output / "n07-b3-review", **inputs)
        candidate = review.build_candidate(**inputs)
        reject_mutations(candidate, inputs)
        review.validate_bundle(output / "n07-b3-review", result["candidate_sha256"])
        report.update(candidate_created=True, candidate=result,
                      fingerprint_status=inputs["fingerprint"]["status"],
                      fingerprint_missing=inputs["fingerprint"]["missing"])
        # A legacy/partial packet remains useful diagnostic material, but a
        # fresh --prepare-review run must actually observe the promised process,
        # mapped fonts/libraries and locale. Only effective theme is the known
        # intentionally unobserved field. Keep the validated packet and cleanup
        # evidence when a collector fails; do not turn missing observations into
        # a green collection job or claim the fingerprint is production-ready.
        fingerprint = inputs["fingerprint"]
        observation = fingerprint["evidence"]
        review.need(fingerprint["status"] == "partial"
                    and fingerprint["missing"] == ["theme"]
                    and observation["observation_errors"] == []
                    and observation["process"] is not None
                    and bool(observation["fonts"])
                    and set(observation["libraries"]) == {"gtk", "pango", "cairo"},
                    "N07_B3_REQUIRED_OBSERVATION_MISSING")
        report.update(status="review_prepared", reason="N07_B3_UNAPPROVED_REVIEW_ONLY")
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        reason = str(error)
        report["reason"] = reason if re.fullmatch(r"[A-Z0-9_]{1,100}", reason) else "N07_B3_REJECTED"
    evidence.write_report(output / "n07-b3-validation.json", report)
    return report["status"] == "review_prepared"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("incoming", "output", "source-root"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--pr-head", default="")
    args = parser.parse_args(argv)
    ok = prepare(args.incoming, args.output, args.source_root, args.source_commit, args.pr_head)
    print("N07-B3 review preparation " + ("completed (unapproved; N07 0/13)" if ok else "rejected"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
