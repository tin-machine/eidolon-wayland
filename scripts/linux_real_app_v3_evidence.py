"""I6 evidence contracts, independent of the producer and product authority.

This checks envelopes, provenance, ownership joins, bounded traces and artifact
integrity. N01 and N02 have independent typed Host oracles; the remaining
product/file/pixel case oracles are unavailable, so these alone cannot pass V11. No
function here opens a product admission or reclamation barrier.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess

import linux_desktop_v3_contract as contract
import linux_real_app_inspect as inspect

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "scripts/fixtures/linux-desktop-v3-acceptance/cases-v1.json"
# A profile is a fixed set, not a producer-supplied list of required cases.
MANIFEST_SHA256 = "e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33"
PROFILE = "i6-host-v1"
REVISION = "eidolon-i6-host-acceptance-v1"
VERIFICATION_IDS = ["I3-T20", "V11"]
MAX_REPORT_BYTES = 1024 * 1024
MAX_ARTIFACTS = 256
MAX_CASE_VARIANTS = 256
MAX_TRACE_EVENTS = 4096
MAX_EVENT_BYTES = 64 * 1024
MAX_TRACE_BYTES = 1024 * 1024
MAX_I64 = (1 << 63) - 1
FAMILIES = {f"{prefix}{n:02}" for prefix, count in (("N", 11), ("F", 12), ("C", 5))
            for n in range(1, count + 1)}
COVERAGE = {"sdk": "synthetic", "android_storage_save": "not_observed",
            "real_reboot": "not_run", "old_apk_upgrade": "I7_only",
            "quest": "not_observed", "physical_input": "not_observed"}
PUBLIC_FLAGS = ("available", "production_acceptance_passed", "quest_verified", "physical_input_verified")
CASE_REASONS = frozenset({
    "I6_OK", "I6_NOT_RUN", "I6_RUNNER_UNAVAILABLE", "I6_ENVIRONMENT_UNAVAILABLE",
    "I6_SOURCE_UNAVAILABLE", "I6_SOURCE_DIRTY", "I6_WATCHDOG", "I6_OPERATOR_CANCELLED",
    "I6_ASSERTION_FAILED", "I6_CLEANUP_FAILED", "I6_EVIDENCE_MISSING", "I6_TRACE_OVERFLOW",
    "I6_EXPECTED_FAILURE_RECOVERED", "I6_EXPECTED_PENDING", "I6_ORACLE_UNAVAILABLE",
    "I6_FAULT_NOT_REACHED", "I6_PRODUCT_FAILURE", "I6_DRIVER_DIED",
})
PRODUCT_REASONS = frozenset({
    "DESKTOP_HELPER_TIMEOUT", "DESKTOP_HELPER_FAILED", "DESKTOP_V3_JOURNAL_IO",
    "APP_WINDOW_UNSUPPORTED", "APP_STATE_RECOVERY_REQUIRED", "APP_DEPENDENCY_CHANGED",
    "APP_PACKAGE_UNSUPPORTED", "lease_expired", "input_eof", "receipt_write",
    "stdout_limit", "stderr_limit", "process_exit", "authority_lost",
    "callback_pending", "dispose_failed", "scene_delete_missing",
})
OUTCOMES = {"complete", "retained_pending", "not_applicable"}
PRODUCT_STATES = {"complete", "pending", "failed", "unknown", "not_applicable"}
RESOURCE_STATES = {"complete", "pending", "failed", "unknown", "not_created"}
RESOURCES = frozenset({
    "remote_processes", "remote_pidfds", "remote_pipes", "profile_lock", "remote_receipt",
    "ssh_channels", "ssh_forwards", "ssh_router", "io_fence", "local_waypipe", "local_pipes",
    "wayland_clients", "wayland_objects", "wayland_surfaces", "wayland_shm", "native_windows",
    "renderer_target", "sdk_lease", "journal_finalizer", "origin_retirement",
    "runtime_shutdown", "registry_destroy",
})
TARGET_RESOURCES = {"renderer_target", "sdk_lease"}
EVENTS = frozenset({
    "observation", "allocation_sealed", "retirement_started", "live_recover",
    "journal_finalized", "origin_retired", "runtime_stop", "census",
    "registry_destroy_accepted", "input_rejected", "renderer_retired", "sdk_retired",
    "pending_preserved", "product_result_fixed", "fixture_rescue", "environment_disposed",
    "resource_complete", "resource_pending", "resource_failed", "resource_unknown", "runtime_stopped",
})
COMPLETE_EVENTS = {"registry_destroy": "registry_destroy_accepted", "renderer_target": "renderer_retired",
                   "sdk_lease": "sdk_retired", "journal_finalizer": "journal_finalized",
                   "origin_retirement": "origin_retired", "runtime_shutdown": "runtime_stopped"}
OWNER_FIELDS = {"owner_id", "runtime", "admission_epoch", "connect_request", "launch_request", "session",
                "capture_sha256", "context_sha256", "io_generation", "nonce", "scope", "native_origin"}
OPTIONAL_OWNER_IDS = {"connect_request", "launch_request", "session", "io_generation", "native_origin"}
TARGET_FIELDS = {"target_id", "owner_id", "session", "window", "scene", "generation", "create_seq", "adapter_id"}
REF_FIELDS = {"artifact", "owner_id", "target_id", "event_seq"}
SOURCE_FIELDS = {"commit", "tree_sha256", "dirty", "build_features", "pr_head"}
INPUT_FIELDS = {"package_fingerprint", "bundle_build_id", "bundle_sha256", "inventory_sha256",
                "compiled_source_hashes", "renderer_fingerprint", "golden_revision"}
CASE_FIELDS = {"case_id", "variant", "status", "reason", "expected_product_outcome", "observed_product_outcome",
               "owners", "targets", "assertions", "trace", "first_product_failure", "first_case_failure",
               "cleanup_failures", "cleanup"}
ARTIFACT_FIELDS = {"path", "size", "sha256", "evidence_class", "kind", "run_id", "case_id", "variant",
                   "owner_id", "target_id", "source_sha256"}
REPORT_FIELDS = {"schema", "stage", "revision", "profile", "verification_ids", "run_id", "manifest_sha256",
                 "source", "inputs", "cases", "artifacts", "coverage", "status", "reason",
                 "host_v3_acceptance_passed", "exit_code", *PUBLIC_FLAGS}


class EvidenceError(ValueError):
    """Fixed reason only; never echo a private path, payload or exception."""


def require(ok, reason="I6_REPORT_INVALID"):
    if not ok:
        raise EvidenceError(reason)


def exact(value, fields):
    require(type(value) is dict and set(value) == set(fields))


def parse(raw, limit=MAX_REPORT_BYTES):
    try:
        return contract.parse_json(raw, limit)
    except ValueError:
        raise EvidenceError("I6_REPORT_INVALID") from None


def canonical(value):
    try:
        return contract.canonical_json(value)
    except ValueError:
        raise EvidenceError("I6_REPORT_INVALID") from None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def token(value):
    return type(value) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) is not None


def identity(value, nullable=False):
    return (nullable and value is None) or contract.hexadecimal(value, 32)


def decimal(value, nullable=False):
    return (nullable and value is None) or contract.decimal(value)


def sequence(value):
    require(type(value) is list and len(value) <= MAX_CASE_VARIANTS)


def relative_path(value):
    require(type(value) is str and 0 < len(value) <= 240 and value.isascii()
            and all(33 <= ord(c) < 127 for c in value) and "\\" not in value,
            "I6_ARTIFACT_INVALID")
    parts = value.split("/")
    require(all(p not in {"", ".", ".."} for p in parts), "I6_ARTIFACT_INVALID")
    return parts


def _private_directory(fd):
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and info.st_mode & 0o077 == 0, "I6_ARTIFACT_INVALID")
    return info


def _root_fd(path):
    try:
        fd = inspect._open_absolute_directory(path)
        try:
            _private_directory(fd)
            return fd
        except BaseException:
            os.close(fd)
            raise
    except (OSError, ValueError):
        raise EvidenceError("I6_ARTIFACT_INVALID") from None


def _read_at(root_fd, path, limit):
    """Hold every ancestor, reject links/devices, and check identities after read."""
    parts = relative_path(path)
    opened = []
    links = []
    parent = root_fd
    try:
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            opened.append(fd)
            info = _private_directory(fd)
            links.append((parent, part, info.st_dev, info.st_ino))
            parent = fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        opened.append(fd)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_mode & 0o077 == 0 and before.st_nlink == 1
                and 0 <= before.st_size <= limit, "I6_ARTIFACT_INVALID")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        require(len(data) == before.st_size and len(data) <= limit
                and inspect._identity(before) == inspect._identity(os.fstat(fd)), "I6_ARTIFACT_INVALID")
        links.append((parent, parts[-1], before.st_dev, before.st_ino))
        for directory, name, device, inode in links:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            require((current.st_dev, current.st_ino) == (device, inode), "I6_ARTIFACT_INVALID")
        return bytes(data)
    finally:
        for fd in reversed(opened):
            os.close(fd)


def read_report(path):
    path = Path(path)
    root_fd = _root_fd(path.parent)
    try:
        return _read_at(root_fd, path.name, MAX_REPORT_BYTES)
    except (OSError, ValueError):
        raise EvidenceError("I6_REPORT_INVALID") from None
    finally:
        os.close(root_fd)


def write_report(path, value):
    """I6-only 1 MiB writer; the A0 128 KiB limit remains unchanged."""
    data = canonical(value) + b"\n"
    require(len(data) <= MAX_REPORT_BYTES, "I6_REPORT_INVALID")
    path = Path(path)
    relative_path(path.name)
    fd = _root_fd(path.parent)
    try:
        leaf = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                       0o600, dir_fd=fd)
        with os.fdopen(leaf, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(fd)
    finally:
        os.close(fd)


def load_manifest(raw=None):
    raw = MANIFEST_PATH.read_bytes() if raw is None else raw
    manifest = parse(raw)
    require(sha(raw) == MANIFEST_SHA256, "I6_MANIFEST_INVALID")
    exact(manifest, {"schema", "profile", "revision", "verification_ids", "cases"})
    require(type(manifest["schema"]) is int and manifest["schema"] == 1
            and manifest["profile"] == PROFILE and manifest["revision"] == REVISION
            and manifest["verification_ids"] == VERIFICATION_IDS, "I6_MANIFEST_INVALID")
    sequence(manifest["cases"])
    seen = set()
    families = set()
    for row in manifest["cases"]:
        exact(row, {"case_id", "variant", "evidence_classes", "expected_product_outcome", "cleanup_policy", "assertions"})
        key = (row["case_id"], row["variant"])
        require(key[0] in FAMILIES and token(key[1]) and key not in seen, "I6_MANIFEST_INVALID")
        seen.add(key)
        families.add(key[0])
        require(type(row["evidence_classes"]) is list and row["evidence_classes"]
                and all(c in {"R", "S", "T"} for c in row["evidence_classes"])
                and len(set(row["evidence_classes"])) == len(row["evidence_classes"]), "I6_MANIFEST_INVALID")
        require(row["expected_product_outcome"] in OUTCOMES
                and row["cleanup_policy"] == {"complete": "product_complete",
                    "retained_pending": "preserve_unknown_then_rescue", "not_applicable": "contract_only"}[row["expected_product_outcome"]],
                "I6_MANIFEST_INVALID")
        assertions = row["assertions"]
        require(type(assertions) is list and assertions and all(token(a) for a in assertions)
                and len(assertions) == len(set(assertions)), "I6_MANIFEST_INVALID")
    require(families == FAMILIES, "I6_MANIFEST_INVALID")
    return manifest


def manifest_digest():
    load_manifest()
    return MANIFEST_SHA256


def _source(value):
    exact(value, SOURCE_FIELDS)
    require(contract.hexadecimal(value["commit"], 40) and contract.hexadecimal(value["tree_sha256"], 64)
            and type(value["dirty"]) is bool
            and (value["pr_head"] is None or contract.hexadecimal(value["pr_head"], 40)))
    require(type(value["build_features"]) is list and value["build_features"] in ([], ["host-acceptance"]))


def source_input_hashes():
    """Current source inventory and compiled include_bytes inputs, not package evidence."""
    raw = _source_bytes(ROOT / "scripts/linux-desktop-v3-sources.json")
    inventory = contract.parse_json(raw)
    return {"inventory_sha256": sha(raw),
            "compiled_source_hashes": {name: sha(_source_bytes(ROOT / path))
                                        for name, path in inventory["sources"].items()}}


def _source_bytes(path):
    parent = inspect._open_absolute_directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_REPORT_BYTES, "I6_SOURCE_UNAVAILABLE")
            data = source.read(MAX_REPORT_BYTES + 1)
            require(len(data) == before.st_size and len(data) <= MAX_REPORT_BYTES
                    and inspect._identity(before) == inspect._identity(os.fstat(source.fileno()))
                    and inspect._identity(before) == inspect._identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)),
                    "I6_SOURCE_UNAVAILABLE")
            return data
    finally:
        os.close(parent)


def source_snapshot(root=ROOT):
    """Hash live tracked path/mode/content (symlink target bytes, never its referent).

    Canonical encoding is a sorted JSON array of [hex(path bytes), Git mode,
    SHA-256(content bytes)]. Hash both index and status before/after to reject a
    moving checkout. No source content or Git error output escapes this helper.
    build_features=[] marks a checkout-only observation, not a binary build.
    """
    def git(*args):
        return subprocess.run(["git", "--no-optional-locks", "-C", os.fspath(root), *args], check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=30).stdout
    try:
        head = git("rev-parse", "HEAD").decode("ascii").strip()
        index = git("ls-files", "--stage", "-z")
        status_before = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        rows = []
        for entry in index.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, _, stage = metadata.split()
            require(stage == b"0" and mode in {b"100644", b"100755", b"120000"}, "I6_SOURCE_UNAVAILABLE")
            path = Path(root) / os.fsdecode(raw_path)
            parent = inspect._open_absolute_directory(path.parent)
            try:
                info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    content = os.fsencode(os.readlink(path.name, dir_fd=parent))
                    live_mode = "120000"
                else:
                    require(stat.S_ISREG(info.st_mode), "I6_SOURCE_UNAVAILABLE")
                    fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                    with os.fdopen(fd, "rb") as source:
                        require(inspect._identity(info) == inspect._identity(os.fstat(source.fileno())), "I6_SOURCE_UNAVAILABLE")
                        digest = hashlib.file_digest(source, "sha256").hexdigest()
                        require(inspect._identity(info) == inspect._identity(os.fstat(source.fileno())), "I6_SOURCE_UNAVAILABLE")
                    live_mode = "100755" if info.st_mode & 0o111 else "100644"
                    content = None
                require(inspect._identity(info) == inspect._identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)),
                        "I6_SOURCE_UNAVAILABLE")
            finally:
                os.close(parent)
            rows.append([raw_path.hex(), live_mode, sha(content) if content is not None else digest])
        require(index == git("ls-files", "--stage", "-z") and head == git("rev-parse", "HEAD").decode("ascii").strip()
                and status_before == git("status", "--porcelain=v1", "-z", "--untracked-files=all"), "I6_SOURCE_UNAVAILABLE")
        result = {"commit": head, "tree_sha256": sha(canonical(sorted(rows))), "dirty": bool(status_before),
                  "build_features": [], "pr_head": None}
        _source(result)
        return result
    except (OSError, ValueError, subprocess.SubprocessError):
        raise EvidenceError("I6_SOURCE_UNAVAILABLE") from None


def _owner(owner):
    exact(owner, OWNER_FIELDS)
    require(identity(owner["owner_id"]) and decimal(owner["runtime"]) and decimal(owner["admission_epoch"])
            and all(decimal(owner[k], True) for k in OPTIONAL_OWNER_IDS)
            and contract.hexadecimal(owner["capture_sha256"], 64)
            and (owner["context_sha256"] is None or contract.hexadecimal(owner["context_sha256"], 64))
            and (owner["nonce"] is None or contract.hexadecimal(owner["nonce"], 16))
            and (owner["scope"] is None or contract.hexadecimal(owner["scope"], 64)))
    if owner["launch_request"] is not None or owner["native_origin"] is not None:
        require(all(owner[k] is not None for k in ("connect_request", "launch_request", "session", "context_sha256",
                                                    "nonce", "scope", "native_origin", "io_generation")), "I6_OWNER_MISMATCH")


def _target(target, owners):
    exact(target, TARGET_FIELDS)
    require(identity(target["target_id"]) and target["owner_id"] in owners
            and all(decimal(target[k]) for k in TARGET_FIELDS - {"target_id", "owner_id"}))
    owner = owners[target["owner_id"]]
    require(target["session"] == owner["session"] and owner["native_origin"] is not None, "I6_OWNER_MISMATCH")


def _failure(value, owners):
    if value is None:
        return
    exact(value, {"reason", "owner_id", "sequence"})
    require(value["reason"] in CASE_REASONS - {"I6_OK", "I6_EXPECTED_FAILURE_RECOVERED", "I6_EXPECTED_PENDING"}
            and (value["owner_id"] is None or value["owner_id"] in owners)
            and decimal(value["sequence"], True))


def _reference(ref, owners, targets):
    exact(ref, REF_FIELDS)
    relative_path(ref["artifact"])
    require((ref["owner_id"] is None or ref["owner_id"] in owners)
            and (ref["target_id"] is None or ref["target_id"] in targets)
            and decimal(ref["event_seq"], True), "I6_OWNER_MISMATCH")
    if ref["target_id"] is not None:
        require(targets[ref["target_id"]]["owner_id"] == ref["owner_id"], "I6_OWNER_MISMATCH")


def _summary(status, reason, *, report=None, missing=None, failures=None, cleanup=None, unverified=0,
             verified=()):
    code = {"pass": 0, "fail": 1, "incomplete": 2, "invalid": 3, "cancelled": 130}[status]
    return {"schema": 1, "stage": "acceptance", "revision": REVISION, "profile": PROFILE,
            "verification_ids": list(VERIFICATION_IDS), "run_id": report["run_id"] if report else None,
            "status": status, "reason": reason, "exit_code": code,
            "evidence_contract_valid": report is not None and status != "invalid", "validation_scope": "evidence_contract",
            "host_v3_acceptance_passed": False, **dict.fromkeys(PUBLIC_FLAGS, False),
            "missing": sorted(set(missing or [])), "unverified_assertions": unverified,
            "first_case_failure": (failures or [None])[0], "cleanup_failures": cleanup or [],
            "coverage": dict(COVERAGE), "product_oracles_verified": False,
            "verified_case_oracles": sorted(set(verified)),
            "n01_inspect_only_passed": "N01/inspect_only" in verified}


def invalid_result(reason="I6_REPORT_INVALID"):
    allowed = {"I6_REPORT_INVALID", "I6_MANIFEST_INVALID", "I6_SOURCE_MISMATCH", "I6_ARTIFACT_INVALID",
               "I6_OWNER_MISMATCH", "I6_TRACE_INVALID", "I6_SOURCE_UNAVAILABLE",
               "I6_N01_RECEIPT_INVALID", "I6_N01_ENVIRONMENT_INVALID",
               "I6_N02_RECEIPT_INVALID", "I6_N03_RECEIPT_INVALID"}
    return _summary("invalid", reason if reason in allowed else "I6_REPORT_INVALID")


def cancelled_result():
    return _summary("cancelled", "I6_OPERATOR_CANCELLED")


def incomplete_report(source, *, run_id=None, reason="I6_RUNNER_UNAVAILABLE"):
    manifest = load_manifest()
    require(reason in CASE_REASONS)
    if source is not None:
        _source(source)
    cases = []
    for row in manifest["cases"]:
        cases.append({"case_id": row["case_id"], "variant": row["variant"], "status": "not_run", "reason": reason,
            "expected_product_outcome": row["expected_product_outcome"], "observed_product_outcome": None,
            "owners": [], "targets": [], "assertions": [], "trace": {"overflow": False, "artifacts": []},
            "first_product_failure": None, "first_case_failure": None, "cleanup_failures": [],
            "cleanup": {"product": "unknown", "fault_resources": "not_needed", "fixture_rescue": "not_needed",
                        "environment_disposal": "not_yet", "census": []}})
    return {"schema": 1, "stage": "acceptance", "revision": REVISION, "profile": PROFILE,
            "verification_ids": list(VERIFICATION_IDS), "run_id": run_id or secrets.token_hex(16),
            "manifest_sha256": MANIFEST_SHA256, "source": {"before": source, "after": source}, "inputs": None,
            "cases": cases, "artifacts": [], "coverage": dict(COVERAGE), "status": "incomplete", "reason": reason,
            "host_v3_acceptance_passed": False, "exit_code": 2, **dict.fromkeys(PUBLIC_FLAGS, False)}


def validate_acceptance(raw, artifact_root, expected_source, expected_manifest=None):
    """Return a sanitized result; producer status/exit/pass never grants success.

    Missing evidence is incomplete; malformed/mixed evidence is invalid. Always
    inspect the entire report before applying cancel/failure/incomplete priority.
    N01/N02 alone cannot return Host PASS; every remaining variant stays required.
    """
    try:
        return _validate(raw, artifact_root, expected_source, expected_manifest)
    except EvidenceError as error:
        return invalid_result(str(error))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return invalid_result()


def _validate(raw, artifact_root, expected_source, expected_manifest):
    manifest = load_manifest(expected_manifest)
    required = {(r["case_id"], r["variant"]): r for r in manifest["cases"]}
    report = parse(raw)
    exact(report, REPORT_FIELDS)
    require(type(report["schema"]) is int and report["schema"] == 1 and report["stage"] == "acceptance"
            and report["revision"] == REVISION and report["profile"] == PROFILE
            and report["verification_ids"] == VERIFICATION_IDS and identity(report["run_id"])
            and report["manifest_sha256"] == MANIFEST_SHA256)
    require(report["coverage"] == COVERAGE and all(report[k] is False for k in PUBLIC_FLAGS))
    require(report["status"] in {"pass", "fail", "incomplete", "cancelled"} and report["reason"] in CASE_REASONS
            and type(report["host_v3_acceptance_passed"]) is bool
            and type(report["exit_code"]) is int and report["exit_code"] in {0, 1, 2, 3, 130})
    missing, failures, cleanup_failures = [], [], []
    missing_by_case = {key: set() for key in required}

    def case_missing(key, diagnostic):
        # Public diagnostics also use artifact/ and typed_receipt/ prefixes.
        # Track their owning case directly; a display prefix is not authority
        # for deciding whether a case has all of its required evidence.
        missing.append(diagnostic)
        missing_by_case[key].add(diagnostic)

    exact(report["source"], {"before", "after"})
    for source in report["source"].values():
        if source is not None:
            _source(source)
    if expected_source is not None:
        _source(expected_source)
    before, after = report["source"]["before"], report["source"]["after"]
    if before is None or after is None or expected_source is None:
        missing.append("source")
    if before is not None and after is not None:
        require(before == after, "I6_SOURCE_MISMATCH")
    if expected_source is not None:
        require(all(s is None or s == expected_source for s in (before, after)), "I6_SOURCE_MISMATCH")
    if any(s is not None and s["dirty"] for s in (before, after, expected_source)):
        missing.append("clean_source")
    inputs = report["inputs"]
    if inputs is None:
        missing.append("inputs")
    else:
        exact(inputs, {"before", "after"})
        source_inputs = source_input_hashes()
        for value in inputs.values():
            exact(value, INPUT_FIELDS)
            require(all(contract.hexadecimal(value[k], 64) for k in INPUT_FIELDS - {"compiled_source_hashes", "golden_revision"})
                    and type(value["golden_revision"]) is str
                    and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value["golden_revision"]) is not None)
            hashes = value["compiled_source_hashes"]
            exact(hashes, source_inputs["compiled_source_hashes"])
            require(all(contract.hexadecimal(h, 64) for h in hashes.values()))
            require(all(value[k] == source_inputs[k] for k in source_inputs), "I6_SOURCE_MISMATCH")
        require(inputs["before"] == inputs["after"], "I6_SOURCE_MISMATCH")

    sequence(report["cases"])
    cases, all_owners, all_targets, references = {}, set(), set(), []
    cancelled = report["status"] == "cancelled" or report["reason"] == "I6_OPERATOR_CANCELLED"
    unverified = 0
    for case in report["cases"]:
        exact(case, CASE_FIELDS)
        key = (case["case_id"], case["variant"])
        require(key in required and key not in cases)
        row = required[key]
        label = "/".join(key)
        require(case["status"] in {"pass", "fail", "not_run", "incomplete", "cancelled"}
                and case["reason"] in CASE_REASONS and case["expected_product_outcome"] == row["expected_product_outcome"]
                and (case["observed_product_outcome"] is None or case["observed_product_outcome"] in OUTCOMES))
        cancelled |= case["status"] == "cancelled" or case["reason"] == "I6_OPERATOR_CANCELLED"
        if case["status"] in {"not_run", "incomplete"}:
            case_missing(key, label)
        sequence(case["owners"])
        owners = {}
        for owner in case["owners"]:
            _owner(owner)
            identifier = owner["owner_id"]
            require(identifier not in all_owners, "I6_OWNER_MISMATCH")
            owners[identifier] = owner
            all_owners.add(identifier)
        sequence(case["targets"])
        targets, target_keys = {}, set()
        for target in case["targets"]:
            _target(target, owners)
            identifier = target["target_id"]
            target_key = tuple(target[k] for k in ("owner_id", "session", "window", "scene", "generation"))
            require(identifier not in all_targets and target_key not in target_keys, "I6_OWNER_MISMATCH")
            targets[identifier] = target
            all_targets.add(identifier)
            target_keys.add(target_key)
        cases[key] = (case, owners, targets)
        _failure(case["first_case_failure"], owners)
        sequence(case["cleanup_failures"])
        for failure in case["cleanup_failures"]:
            require(failure is not None)
            _failure(failure, owners)
            cleanup_failures.append({"case_id": key[0], "variant": key[1], **failure})
        if case["first_case_failure"] is not None:
            failures.append({"case_id": key[0], "variant": key[1], **case["first_case_failure"]})
        elif case["status"] == "fail":
            failures.append({"case_id": key[0], "variant": key[1], "reason": "I6_ASSERTION_FAILED"})
        require(case["first_product_failure"] is None or case["first_product_failure"] in PRODUCT_REASONS)
        exact(case["trace"], {"overflow", "artifacts"})
        require(type(case["trace"]["overflow"]) is bool)
        sequence(case["trace"]["artifacts"])
        require(len(case["trace"]["artifacts"]) == len(set(case["trace"]["artifacts"])))
        for path in case["trace"]["artifacts"]:
            relative_path(path)
        if case["trace"]["overflow"]:
            case_missing(key, label + "/trace_overflow")
        sequence(case["assertions"])
        found = set()
        for assertion in case["assertions"]:
            exact(assertion, {"assertion_id", "status", "reason", "evidence_refs"})
            assertion_id = assertion["assertion_id"]
            require(assertion_id in row["assertions"] and assertion_id not in found
                    and assertion["status"] in {"pass", "fail", "not_run"} and assertion["reason"] in CASE_REASONS)
            found.add(assertion_id)
            sequence(assertion["evidence_refs"])
            for ref in assertion["evidence_refs"]:
                _reference(ref, owners, targets)
                references.append((key, ref, {"assertion_id": assertion_id}))
            if assertion["status"] == "fail":
                failures.append({"case_id": key[0], "variant": key[1], "reason": "I6_ASSERTION_FAILED", "assertion_id": assertion_id})
            elif assertion["status"] == "not_run" or not assertion["evidence_refs"]:
                case_missing(key, label + "/" + assertion_id)
            # R/S/T provenance and references are checked below. Their semantic
            # oracle must still be independently implemented before V11 can pass.
            unverified += 1
        for assertion in set(row["assertions"]) - found:
            case_missing(key, label + "/" + assertion)
        cleanup = case["cleanup"]
        exact(cleanup, {"product", "fault_resources", "fixture_rescue", "environment_disposal", "census"})
        require(cleanup["product"] in PRODUCT_STATES
                and cleanup["fault_resources"] in {"complete", "pending", "failed", "unknown", "not_needed"}
                and cleanup["fixture_rescue"] in {"complete", "pending", "failed", "unknown", "not_needed"}
                and cleanup["environment_disposal"] in {"complete", "pending", "failed", "unknown", "not_yet", "not_needed"})
        for domain in ("product", "fault_resources", "fixture_rescue", "environment_disposal"):
            if cleanup[domain] == "failed":
                cleanup_failures.append({"case_id": key[0], "variant": key[1], "reason": "I6_CLEANUP_FAILED",
                                         "owner_id": None, "sequence": None, "resource": domain})
        observed = case["observed_product_outcome"]
        if observed is not None:
            require(cleanup["product"] in {"complete": {"complete"}, "retained_pending": {"pending", "unknown"},
                                            "not_applicable": {"not_applicable"}}[observed])
            if observed != row["expected_product_outcome"]:
                failures.append({"case_id": key[0], "variant": key[1], "reason": "I6_CLEANUP_FAILED"})
        else:
            case_missing(key, label + "/product_outcome")
        sequence(cleanup["census"])
        census = set()
        for resource in cleanup["census"]:
            exact(resource, {"resource", "owner_id", "target_id", "state", "evidence_refs", "missing_reasons"})
            require(resource["resource"] in RESOURCES and resource["owner_id"] in owners
                    and resource["state"] in RESOURCE_STATES)
            rid = (resource["resource"], resource["owner_id"], resource["target_id"])
            require(rid not in census, "I6_OWNER_MISMATCH")
            census.add(rid)
            if resource["target_id"] is not None:
                require(resource["resource"] in TARGET_RESOURCES and resource["target_id"] in targets
                        and targets[resource["target_id"]]["owner_id"] == resource["owner_id"], "I6_OWNER_MISMATCH")
            elif resource["resource"] in TARGET_RESOURCES:
                require(resource["state"] == "not_created"
                        and not any(t["owner_id"] == resource["owner_id"] for t in targets.values()), "I6_OWNER_MISMATCH")
            sequence(resource["evidence_refs"])
            sequence(resource["missing_reasons"])
            require(all(r in CASE_REASONS for r in resource["missing_reasons"]))
            for ref in resource["evidence_refs"]:
                _reference(ref, owners, targets)
                require(ref["owner_id"] == resource["owner_id"] and ref["target_id"] == resource["target_id"], "I6_OWNER_MISMATCH")
                expected_event = ("allocation_sealed" if resource["state"] == "not_created" else
                                  COMPLETE_EVENTS.get(resource["resource"], "resource_complete")
                                  if resource["state"] == "complete" else "resource_" + resource["state"])
                references.append((key, ref, {"event": expected_event, "resource": resource["resource"]}))
            if not resource["evidence_refs"] or resource["missing_reasons"]:
                case_missing(key, label + "/" + resource["resource"])
            if resource["state"] == "failed":
                cleanup_failures.append({"case_id": key[0], "variant": key[1], "reason": "I6_CLEANUP_FAILED",
                    "owner_id": resource["owner_id"], "sequence": None, "resource": resource["resource"]})
            if cleanup["product"] == "complete" and resource["state"] not in {"complete", "not_created"}:
                raise EvidenceError("I6_REPORT_INVALID")
        if row["cleanup_policy"] != "contract_only":
            if not owners:
                case_missing(key, label + "/owners")
            for owner in owners:
                expected = {(r, owner, None) for r in RESOURCES - TARGET_RESOURCES}
                owned_targets = [t for t in targets.values() if t["owner_id"] == owner]
                expected |= {(r, owner, t["target_id"]) for r in TARGET_RESOURCES for t in owned_targets}
                if not owned_targets:
                    expected |= {(r, owner, None) for r in TARGET_RESOURCES}
                for resource, _, target in expected - census:
                    case_missing(key, label + "/census/" + resource + ("/" + target if target else ""))
            if cleanup["environment_disposal"] != "complete":
                case_missing(key, label + "/environment_disposal")
            if row["cleanup_policy"] == "preserve_unknown_then_rescue" and cleanup["fixture_rescue"] != "complete":
                case_missing(key, label + "/fixture_rescue")
        if cleanup["fault_resources"] not in {"complete", "not_needed"}:
            case_missing(key, label + "/fault_resources")
    for key in required.keys() - cases.keys():
        case_missing(key, "/".join(key))
    require(len({key[0] for key in cases}) <= 64)

    sequence(report["artifacts"])
    require(len(report["artifacts"]) <= MAX_ARTIFACTS)
    artifacts, events, totals, owner_sequences = {}, {}, {}, {}
    receipts, verified_assertions = {}, set()
    root_fd = _root_fd(artifact_root)
    root_identity = os.fstat(root_fd)
    try:
        for artifact in report["artifacts"]:
            exact(artifact, ARTIFACT_FIELDS)
            path = artifact["path"]
            relative_path(path)
            key = (artifact["case_id"], artifact["variant"])
            require(path not in artifacts and key in cases and artifact["run_id"] == report["run_id"]
                    and before is not None and artifact["source_sha256"] == before["tree_sha256"], "I6_SOURCE_MISMATCH")
            require(contract.integer(artifact["size"], 0, MAX_TRACE_BYTES) and contract.hexadecimal(artifact["sha256"], 64)
                    and artifact["evidence_class"] in required[key]["evidence_classes"]
                    and artifact["kind"] in {"trace", "opaque", "n01_receipt", "n02_receipt", "n03_receipt", "n04_receipt", "n05_receipt"})
            _, owners, targets = cases[key]
            _reference({"artifact": path, "owner_id": artifact["owner_id"], "target_id": artifact["target_id"], "event_seq": None}, owners, targets)
            artifacts[path] = artifact
            try:
                data = _read_at(root_fd, path, MAX_TRACE_BYTES)
            except FileNotFoundError:
                case_missing(key, "artifact/" + path)
                continue
            except OSError:
                raise EvidenceError("I6_ARTIFACT_INVALID") from None
            require(len(data) == artifact["size"] and sha(data) == artifact["sha256"], "I6_ARTIFACT_INVALID")
            if artifact["kind"] == "opaque":
                continue
            if artifact["kind"] == "n01_receipt":
                from linux_real_app_v3_n01_oracle import CASE, verify_receipt
                require(key == CASE and artifact["evidence_class"] == "R"
                        and artifact["owner_id"] is not None and artifact["target_id"] is None,
                        "I6_OWNER_MISMATCH")
                require(not any((artifacts[p]["case_id"], artifacts[p]["variant"]) == key
                                for p in receipts), "I6_N01_RECEIPT_INVALID")
                result = verify_receipt(parse(data), expected_run_id=report["run_id"],
                                        expected_source_sha256=before["tree_sha256"],
                                        expected_owner=owners[artifact["owner_id"]], expected_source=before)
                receipts[path] = result
                for item in result["missing"]:
                    case_missing(key, "/".join(key) + "/" + item)
                failures.extend({"case_id": key[0], "variant": key[1],
                                 "reason": "I6_ASSERTION_FAILED", "assertion_id": item}
                                for item in result["failures"])
                continue
            if artifact["kind"] == "n02_receipt":
                from linux_real_app_v3_n02_oracle import verify_receipt as verify_n02
                require(key[0] == "N02" and artifact["evidence_class"] == "R"
                        and artifact["owner_id"] is not None
                        and artifact["target_id"] is None
                        and len(targets) == 1, "I6_OWNER_MISMATCH")
                require(not any((artifacts[p]["case_id"], artifacts[p]["variant"]) == key
                                for p in receipts), "I6_N02_RECEIPT_INVALID")
                result = verify_n02(
                    parse(data), expected_run_id=report["run_id"],
                    expected_source_sha256=before["tree_sha256"],
                    expected_owner=owners[artifact["owner_id"]],
                    expected_target=next(iter(targets.values())),
                    expected_source=before,
                )
                # The current Host-only receipt proves product retirement,
                # NOT deletion of the disposable Docker/container environment.
                # A producer may not upgrade that independent boundary by
                # editing the enclosing case; a future exterior disposal
                # witness needs its own separately reviewed contract.
                require(cases[key][0]["cleanup"]["environment_disposal"] == "not_yet",
                        "I6_N02_RECEIPT_INVALID")
                receipts[path] = result
                continue
            if artifact["kind"] == "n03_receipt":
                from linux_real_app_v3_n03_oracle import verify_receipt as verify_n03
                require(key[0] == "N03" and artifact["evidence_class"] == "R"
                        and artifact["owner_id"] is not None and artifact["target_id"] is None
                        and len(targets) <= 1, "I6_OWNER_MISMATCH")
                require(not any((artifacts[p]["case_id"], artifacts[p]["variant"]) == key
                                for p in receipts), "I6_N03_RECEIPT_INVALID")
                result = verify_n03(
                    parse(data), expected_run_id=report["run_id"],
                    expected_source_sha256=before["tree_sha256"],
                    expected_owner=owners[artifact["owner_id"]],
                    expected_target=next(iter(targets.values())) if targets else None,
                    expected_source=before,
                )
                # A native Host receipt cannot attest exterior Docker deletion.
                require(cases[key][0]["cleanup"]["environment_disposal"] == "not_yet",
                        "I6_N03_RECEIPT_INVALID")
                receipts[path] = result
                continue
            if artifact["kind"] in {"n04_receipt", "n05_receipt"}:
                from linux_real_app_v3_n04_oracle import verify_receipt as verify_n04
                require(key == ("N04", "idle_125s") and artifact["evidence_class"] == "R"
                        and artifact["owner_id"] is not None and artifact["target_id"] is None
                        and len(targets) == 3, "I6_OWNER_MISMATCH")
                require(not any((artifacts[p]["case_id"], artifacts[p]["variant"]) == key
                                for p in receipts), "I6_N04_RECEIPT_INVALID")
                result = verify_n04(
                    parse(data), expected_run_id=report["run_id"],
                    expected_source_sha256=before["tree_sha256"],
                    expected_owner=owners[artifact["owner_id"]],
                    expected_targets=list(targets.values()),
                    expected_source=before,
                )
                require(cases[key][0]["cleanup"]["environment_disposal"] == "not_yet",
                        "I6_N04_RECEIPT_INVALID")
                receipts[path] = result
                continue
            if artifact["kind"] == "n05_receipt":
                from linux_real_app_v3_n05_oracle import verify_receipt as verify_n05
                require(key == ("N05", "open_edit_save_as") and artifact["evidence_class"] == "R"
                        and artifact["owner_id"] is not None and artifact["target_id"] is None
                        and len(targets) == 3, "I6_OWNER_MISMATCH")
                require(not any((artifacts[p]["case_id"], artifacts[p]["variant"]) == key
                                for p in receipts), "I6_N05_RECEIPT_INVALID")
                result = verify_n05(
                    parse(data), expected_run_id=report["run_id"],
                    expected_source_sha256=before["tree_sha256"],
                    expected_owner=owners[artifact["owner_id"]],
                    expected_targets=list(targets.values()),
                    expected_source=before,
                )
                require(cases[key][0]["cleanup"]["environment_disposal"] == "not_yet",
                        "I6_N05_RECEIPT_INVALID")
                receipts[path] = result
                continue
            require(path in cases[key][0]["trace"]["artifacts"], "I6_TRACE_INVALID")
            trace = parse(data, MAX_TRACE_BYTES)
            exact(trace, {"schema", "kind", "run_id", "case_id", "variant", "source_sha256", "overflow", "events"})
            require(type(trace["schema"]) is int and trace["schema"] == 1 and trace["kind"] == "eidolon-i6-trace-v1"
                    and all(trace[k] == artifact[k] for k in ("run_id", "case_id", "variant", "source_sha256"))
                    and type(trace["overflow"]) is bool and type(trace["events"]) is list, "I6_TRACE_INVALID")
            require(trace["overflow"] == cases[key][0]["trace"]["overflow"], "I6_TRACE_INVALID")
            count, size = totals.get(key, (0, 0))
            totals[key] = (count + len(trace["events"]), size + len(data))
            require(totals[key][0] <= MAX_TRACE_EVENTS and totals[key][1] <= MAX_TRACE_BYTES, "I6_TRACE_INVALID")
            previous = 0
            events[path] = {}
            for event in trace["events"]:
                exact(event, {"seq", "event", "owner", "target", "evidence_class", "assertion_id", "resource"})
                require(len(canonical(event)) <= MAX_EVENT_BYTES and decimal(event["seq"])
                        and int(event["seq"]) > previous and event["event"] in EVENTS
                        and event["evidence_class"] == artifact["evidence_class"]
                        and (event["assertion_id"] is None or event["assertion_id"] in required[key]["assertions"])
                        and (event["resource"] is None or event["resource"] in RESOURCES), "I6_TRACE_INVALID")
                previous = int(event["seq"])
                _owner(event["owner"])
                require(owners.get(event["owner"]["owner_id"]) == event["owner"], "I6_OWNER_MISMATCH")
                stream = owner_sequences.setdefault(event["owner"]["owner_id"], {})
                require(event["seq"] not in stream, "I6_TRACE_INVALID")
                stream[event["seq"]] = (key, event)
                if event["target"] is not None:
                    _target(event["target"], owners)
                    require(targets.get(event["target"]["target_id"]) == event["target"]
                            and event["target"]["owner_id"] == event["owner"]["owner_id"], "I6_OWNER_MISMATCH")
                if artifact["owner_id"] is not None:
                    require(artifact["owner_id"] == event["owner"]["owner_id"], "I6_OWNER_MISMATCH")
                if artifact["target_id"] is not None:
                    require(event["target"] is not None and artifact["target_id"] == event["target"]["target_id"], "I6_OWNER_MISMATCH")
                events[path][event["seq"]] = event
        for key, (case, _, _) in cases.items():
            for path in case["trace"]["artifacts"]:
                if path not in artifacts:
                    case_missing(key, "artifact/" + path)
                else:
                    require((artifacts[path]["case_id"], artifacts[path]["variant"]) == key
                            and artifacts[path]["kind"] == "trace", "I6_TRACE_INVALID")
        for key, ref, expected_fields in references:
            artifact = artifacts.get(ref["artifact"])
            if artifact is None:
                case_missing(key, "artifact/" + ref["artifact"])
                continue
            require((artifact["case_id"], artifact["variant"]) == key, "I6_OWNER_MISMATCH")
            if ref["event_seq"] is None:
                receipt = receipts.get(ref["artifact"])
                # A N02 typed receipt is owner-scoped but proves one exact
                # native target. Target-resource refs may use only that derived
                # TargetKey identity, never a producer-selected alias.
                if artifact["kind"] in {"n02_receipt", "n03_receipt", "n04_receipt", "n05_receipt"}:
                    require(artifact["owner_id"] == ref["owner_id"]
                            and artifact["target_id"] is None
                            and (ref["target_id"] is None
                                 or ref["target_id"] in cases[key][2]), "I6_OWNER_MISMATCH")
                    if receipt is not None and ref["target_id"] is not None:
                        if artifact["kind"] in {"n04_receipt", "n05_receipt"}:
                            require(ref["target_id"] in receipt["target_ids"],
                                    "I6_OWNER_MISMATCH")
                        else:
                            require(ref["target_id"] == receipt["target_id"],
                                    "I6_OWNER_MISMATCH")
                else:
                    require(artifact["owner_id"] == ref["owner_id"]
                            and artifact["target_id"] == ref["target_id"], "I6_OWNER_MISMATCH")
                if artifact["kind"] in {"n01_receipt", "n02_receipt", "n03_receipt", "n04_receipt", "n05_receipt"}:
                    if receipt is None or receipt["status"] != "pass":
                        case_missing(key, "typed_receipt/" + ref["artifact"])
                    elif "assertion_id" in expected_fields:
                        assertion = expected_fields["assertion_id"]
                        require(assertion in receipt["verified_assertions"],
                                "I6_N03_RECEIPT_INVALID" if artifact["kind"] == "n03_receipt" else
                                "I6_N03_RECEIPT_INVALID" if artifact["kind"] == "n03_receipt" else
                                "I6_N02_RECEIPT_INVALID" if artifact["kind"] == "n02_receipt"
                                else "I6_N01_RECEIPT_INVALID")
                        verified_assertions.add((key, assertion))
                    else:
                        resource = expected_fields["resource"]
                        if artifact["kind"] in {"n02_receipt", "n03_receipt", "n04_receipt", "n05_receipt"}:
                            require((ref["target_id"] in receipt["target_ids"] if artifact["kind"] in {"n04_receipt", "n05_receipt"}
                                     else ref["target_id"] == receipt["target_id"])
                                    if resource in TARGET_RESOURCES else ref["target_id"] is None,
                                    "I6_OWNER_MISMATCH")
                        state = receipt["census"].get(resource)
                        event = ("allocation_sealed" if state == "not_created" else
                                 COMPLETE_EVENTS.get(resource, "resource_complete")
                                 if state == "complete" else None)
                        require(event is not None and expected_fields["event"] == event,
                                "I6_N02_RECEIPT_INVALID" if artifact["kind"] == "n02_receipt"
                                else "I6_N01_RECEIPT_INVALID")
                else:
                    case_missing(key, "typed_receipt/" + ref["artifact"])
            elif ref["artifact"] in events:
                event = events[ref["artifact"]].get(ref["event_seq"])
                require(event is not None, "I6_TRACE_INVALID")
                require(event["owner"]["owner_id"] == ref["owner_id"]
                        and (event["target"]["target_id"] if event["target"] else None) == ref["target_id"], "I6_OWNER_MISMATCH")
                require(all(event[field] == value for field, value in expected_fields.items()), "I6_TRACE_INVALID")
            else:
                require(artifact["kind"] == "trace", "I6_TRACE_INVALID")
        for stream in owner_sequences.values():
            ordered = [(int(seq), key, event) for seq, (key, event) in stream.items()]
            for seq, key, event in ordered:
                dependencies = []
                if event["event"] == "runtime_stop":
                    dependencies = [("origin_retirement", {"origin_retired", "allocation_sealed"}),
                                    ("journal_finalizer", {"journal_finalized", "allocation_sealed"})]
                elif event["event"] == "runtime_stopped":
                    dependencies = [(None, {"runtime_stop"})]
                elif event["event"] == "registry_destroy_accepted":
                    dependencies = [("runtime_shutdown", {"runtime_stopped"}), (None, {"census"})]
                elif event["event"] in {"fixture_rescue", "environment_disposed"}:
                    dependencies = [(None, {"product_result_fixed"})]
                for resource, kinds in dependencies:
                    candidates = [n for n, _, observed in ordered if observed["event"] in kinds
                                  and (resource is None or observed["resource"] == resource)]
                    if not candidates:
                        case_missing(key, "/".join(key) + "/order/" + event["event"])
                    else:
                        require(any(n < seq for n in candidates), "I6_TRACE_INVALID")
        # A renamed/replaced artifact root cannot be substituted during a run.
        check_fd = _root_fd(artifact_root)
        try:
            current = os.fstat(check_fd)
            require((current.st_dev, current.st_ino) == (root_identity.st_dev, root_identity.st_ino), "I6_ARTIFACT_INVALID")
        finally:
            os.close(check_fd)
    finally:
        os.close(root_fd)
    # N01/N02/N03 verify only their own fixed assertions. There is deliberately no
    # success path for the remaining unimplemented product/file/pixel oracles.
    if cancelled:
        status, reason = "cancelled", "I6_OPERATOR_CANCELLED"
    elif failures or cleanup_failures or report["status"] == "fail":
        status, reason = "fail", "I6_ASSERTION_FAILED" if failures else "I6_CLEANUP_FAILED"
    elif missing:
        status, reason = "incomplete", "I6_EVIDENCE_MISSING"
    else:
        status, reason = "incomplete", "I6_ORACLE_UNAVAILABLE"
    verified_cases = ["/".join(key) for key, row in required.items()
                      if key in cases and cases[key][0]["status"] == "pass"
                      and cases[key][0]["reason"] != "I6_OPERATOR_CANCELLED"
                      and all((key, assertion) in verified_assertions for assertion in row["assertions"])
                      and not any(f.get("case_id") == key[0] and f.get("variant") == key[1]
                                  for f in failures + cleanup_failures)
                      and expected_source is not None and before is not None and after is not None
                      and not any(s["dirty"] for s in (before, after, expected_source))
                      and not (missing_by_case[key] - {"/".join(key) + "/environment_disposal"})]
    return _summary(status, reason, report=report, missing=missing, failures=failures,
                    cleanup=cleanup_failures, unverified=unverified - len(verified_assertions),
                    verified=verified_cases)
