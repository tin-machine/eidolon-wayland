"""Explicit, synthetic-only A0 checks; never claims v3 or Quest acceptance.

Inspection does not call this module. A positive settings probe is necessary but
not sufficient: the actual Mousepad/Waypipe checks and any pending GUI oracle
remain visible in the receipt. Missing dependencies never become a passing run.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sys
import time

import linux_real_app_inspect as inspect
import linux_real_app_evidence as evidence
import linux_real_app_gui_evidence as gui_evidence
from linux_real_app_build import BACKEND_NAME, BuildError, ROOT, build_gui_observer, build_settings, fixed_environment, require, run_bounded

REVISION = "eidolon-real-app-feasibility-v2"
KEYFILE = Path(inspect.SETTINGS_RELATIVE_PATH)
FIXTURE = ROOT / "scripts/fixtures/linux-real-app-wayland"
FROZEN_V2 = (
    "scripts/linux/eidolon-linux-session.py", "scripts/linux/eidolon_linux_desktop.py",
    "scripts/linux/eidolon_linux_process_owner.py", "scripts/linux/eidolon_linux_desktop_session.py",
    "scripts/fixtures/gtk3-desktop-demo.c",
)
APPLICATION_CHECKS = (
    "private_bus", "independent_instance", "first_main", "expected_app_id", "transient_dialogs",
    "open_edit_save_as", "clean_close_restart", "gui_setting_persistence", "protected_settings_gui_readonly",
)
FOUNDATION_PENDING = evidence.PENDING
SETTINGS_CHECKS = frozenset({
    "policy_values_types_and_readonly", "ordinary_notifications_and_transactions",
    "ordinary_persistence_in_new_process", "separate_profile_isolation",
    "missing_module_fallback_rejected", "invalid_profile_exits_without_fallback",
})
RECOVERY_CHECKS = frozenset({
    "empty_profile_admitted", "nonempty_session_refused", "autosave_refused", "recovery_bytes_preserved",
})
CONFLICTING = (b"[org/xfce/mousepad/preferences/file]\nsession-restore='after-a-crash'\nautosave-timer=30\n"
               b"[org/xfce/mousepad/preferences/window]\nopening-mode='window'\n")


def write_json(path, value):
    path = Path(path)
    data = inspect.canonical_json(value) + b"\n"
    require(len(data) <= inspect.MAX_JSON_BYTES, "APP_CHECK_RECEIPT_LIMIT")
    parent = inspect._open_absolute_directory(path.parent)
    try:
        inspect._directory_info(parent, os.getuid(), True, "APP_CHECK_ARTIFACT_INVALID")
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)


def private_artifact_root(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, "APP_CHECK_ARTIFACT_INVALID")
    path.mkdir(mode=0o700, exist_ok=True)
    fd = inspect._open_absolute_directory(path)
    try:
        inspect._directory_info(fd, os.getuid(), True, "APP_CHECK_ARTIFACT_INVALID")
        require(not any(path.iterdir()), "APP_CHECK_ARTIFACT_NOT_EMPTY")
    finally:
        os.close(fd)
    return path


def profile(root):
    root.mkdir(mode=0o700)
    for name in ("home", "config", "data", "state", "cache", "runtime", "Documents"):
        (root / name).mkdir(mode=0o700)
    marker = root / "settings-probe-v1"
    marker.write_bytes(b"eidolon-settings-probe-v1\n")
    marker.chmod(0o600)
    return root


def environment(root, bundle):
    env = fixed_environment(root / "home")
    env.update({"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_DATA_HOME": str(root / "data"), "XDG_STATE_HOME": str(root / "state"),
                "XDG_CACHE_HOME": str(root / "cache"), "XDG_RUNTIME_DIR": str(root / "runtime"),
                "GSETTINGS_BACKEND": BACKEND_NAME, "GIO_EXTRA_MODULES": str(bundle / "gio"),
                "GIO_USE_VFS": "local", "GSETTINGS_SCHEMA_DIR": "/usr/share/glib-2.0/schemas"})
    return env


def seed(root, contents):
    keyfile = root / KEYFILE
    keyfile.parent.parent.mkdir(mode=0o700, exist_ok=True)
    keyfile.parent.mkdir(mode=0o700, exist_ok=True)
    with keyfile.open("xb") as output:
        output.write(contents)
    keyfile.chmod(0o600)


def probe(bundle, root, mode, *, expected=0, env=None, reason=None):
    result = run_bounded([bundle / "linux-settings-probe", "--" + mode], env or environment(root, bundle), seconds=10)
    require(result["returncode"] == expected, "APP_SETTINGS_PROBE_FAILED")
    if expected == 78:
        require(result["stdout"] == b"", "APP_SETTINGS_PROBE_FAILED")
    else:
        value = inspect.decode_record(result["stdout"])
        expected_value = ({"schema": 1, "status": "passed", "mode": mode, "backend": BACKEND_NAME}
                          if expected == 0 else {"schema": 1, "status": "failed", "reason": reason})
        require(value == expected_value and type(value["schema"]) is int, "APP_SETTINGS_PROBE_FAILED")
    return {"returncode": result["returncode"], "output_bytes": result["output_bytes"], "group_reaped": result["group_reaped"]}


def settings_checks(artifact, bundle):
    root = profile(artifact / "settings-profile")
    seed(root, CONFLICTING)
    require(inspect.guard_recovery_state(root)["clear"], "APP_STATE_RECOVERY_REQUIRED")
    first = probe(bundle, root, "check")
    require((root / KEYFILE).read_bytes() == CONFLICTING, "APP_SETTINGS_PROBE_WROTE_POLICY")
    exercised = probe(bundle, root, "exercise")
    restarted = probe(bundle, root, "verify-persistence")
    second = profile(artifact / "settings-independent-profile")
    isolated = probe(bundle, second, "check")
    require(not (second / KEYFILE).exists(), "APP_SETTINGS_PROFILE_NOT_ISOLATED")
    missing = environment(second, bundle)
    missing["GIO_EXTRA_MODULES"] = str(artifact / "absent-module")
    fallback = probe(bundle, second, "check", expected=1, env=missing, reason="APP_SETTINGS_BACKEND_UNVERIFIED")
    broken = profile(artifact / "settings-broken-profile")
    seed(broken, b"[invalid")
    fail_closed = probe(bundle, broken, "check", expected=78)
    require((broken / KEYFILE).read_bytes() == b"[invalid", "APP_SETTINGS_INVALID_PROFILE_CHANGED")
    return {"policy_values_types_and_readonly": True, "ordinary_notifications_and_transactions": True,
            "ordinary_persistence_in_new_process": True, "separate_profile_isolation": True,
            "missing_module_fallback_rejected": True, "invalid_profile_exits_without_fallback": True,
            "probes": {"initial": first, "exercise": exercised, "restart": restarted,
                       "isolation": isolated, "missing_module": fallback, "invalid_profile": fail_closed}}


def recovery_checks(artifact):
    root = profile(artifact / "recovery-profile")
    documents = root / "Documents/input.txt"
    documents.write_bytes(b"Eidolon A0 recovery sentinel\n")
    documents.chmod(0o600)
    lock = root / "profile.lock"
    with lock.open("xb") as held:
        lock.chmod(0o600)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(inspect.guard_recovery_state(root)["clear"], "APP_RECOVERY_GUARD_UNVERIFIED")
        session = b"[org/xfce/mousepad/state/application]\nsession=['file:///fixed-a0-unsaved']\n"
        seed(root, session)
        refusal = inspect.guard_recovery_state(root)
        require(not refusal["clear"] and refusal["reason"] == "APP_STATE_RECOVERY_REQUIRED" and
                (root / KEYFILE).read_bytes() == session, "APP_RECOVERY_GUARD_UNVERIFIED")
        # Replace only our own synthetic seed between independent guard cases.
        (root / KEYFILE).unlink()
        seed(root, b"[org/xfce/mousepad/state/application]\nsession=[]\n")
        (root / "data/Mousepad").mkdir(mode=0o700)
        autosave = root / "data/Mousepad/autosave-1"
        autosave.write_bytes(b"Eidolon A0 unsaved document\n")
        autosave.chmod(0o600)
        refusal = inspect.guard_recovery_state(root)
        require(not refusal["clear"] and refusal["reason"] == "APP_STATE_RECOVERY_REQUIRED" and
                autosave.read_bytes() == b"Eidolon A0 unsaved document\n" and
                documents.read_bytes() == b"Eidolon A0 recovery sentinel\n", "APP_RECOVERY_GUARD_UNVERIFIED")
    return {"empty_profile_admitted": True, "nonempty_session_refused": True,
            "autosave_refused": True, "recovery_bytes_preserved": True, "application_spawned_for_seeded_recovery": False}


def build_compositor(artifact):
    cargo = shutil.which("cargo")
    require(cargo is not None, "APP_WAYLAND_BUILD_UNAVAILABLE")
    # This is a developer host fixture, not the remote launch environment.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "CARGO_HOME", "RUSTUP_HOME") if key in os.environ}
    env.update(LANG="C.UTF-8", LC_ALL="C.UTF-8")
    manifest = FIXTURE / "Cargo.toml"
    argv = [cargo, "+1.95.0", "build", "--locked", "--manifest-path", str(manifest), "--target-dir", str(ROOT / "target")]
    with (artifact / "compositor-build.log").open("xb") as log:
        result = run_bounded(argv, env, seconds=600, stdout_limit=1024 * 1024, total_limit=8 * 1024 * 1024, build_log=log)
    require(result["returncode"] == 0, "APP_WAYLAND_BUILD_FAILED")
    executable = ROOT / "target/debug/linux-real-app-wayland-probe"
    require(executable.is_file(), "APP_WAYLAND_BUILD_FAILED")
    return executable


def waypipe_binary():
    try:
        metadata_path = ROOT / "native/generated/waypipe-host.json"
        parent = inspect._open_absolute_directory(metadata_path.parent)
        try:
            raw = inspect._read_file_at(parent, metadata_path.name, uid=os.getuid(), limit=65536,
                                       private=False, reason="APP_WAYPIPE_UNVERIFIED")
        finally:
            os.close(parent)
        metadata = inspect.decode_record(raw)
        binary = Path(metadata["executable"])
        require(metadata["target"] == "host" and metadata["version"] == "0.11.3" and binary.is_absolute(), "APP_WAYPIPE_UNVERIFIED")
        parent = inspect._open_absolute_directory(binary.parent)
        try:
            data = inspect._read_file_at(parent, binary.name, uid=os.getuid(), limit=64 * 1024 * 1024,
                                        private=False, executable=True, reason="APP_WAYPIPE_UNVERIFIED")
        finally:
            os.close(parent)
        require(hashlib.sha256(data).hexdigest() == metadata["sha256"], "APP_WAYPIPE_UNVERIFIED")
        return binary, metadata["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BuildError("APP_WAYPIPE_UNVERIFIED") from error


def validate_application_foundation(value, *, require_precursor_hold=False):
    """Distinguish measured A0 success from an explicitly unperformed GUI stage.

    A per-run receipt, settings API result or summary boolean cannot certify
    GUI persistence. Both paths retain every existing lifecycle requirement;
    only complete relational GUI evidence can remove the two pending checks.
    """
    require(type(value) is dict and type(value.get("schema")) is int and value["schema"] == 1 and
            value.get("stage") == "wayland" and value.get("error") is None and
            value.get("physical_input_verified") is False and value.get("production_ready") is False,
            "APP_WAYLAND_RECEIPT_INVALID")
    complete = value.get("status") == "passed"
    require((value.get("state") == "complete" and value.get("feasibility_verified") is True
             and value.get("pending") == []) if complete else
            (value.get("status") == "incomplete" and value.get("state") == "partial"
             and value.get("feasibility_verified") is False
             and value.get("pending") == list(FOUNDATION_PENDING)
             and value.get("gui") is None), "APP_WAYLAND_RECEIPT_INVALID")
    checks = value.get("checks")
    require(type(checks) is dict and set(checks) == set(APPLICATION_CHECKS) and
            all(type(item) is bool for item in checks.values()) and
            all(checks[name] is (complete or name not in FOUNDATION_PENDING) for name in APPLICATION_CHECKS),
            "APP_WAYLAND_RECEIPT_INVALID")
    evidence.validate_cleanup(value.get("cleanup"))
    cleanup = value["cleanup"]
    require(cleanup.get("workers_complete") is True and type(cleanup.get("worker_count")) is int
            and cleanup["worker_count"] == 2 and type(cleanup.get("worker_returncodes")) is dict
            and set(cleanup["worker_returncodes"]) == {"precursor", "target"}
            and all(type(code) is int and code == 0 for code in cleanup["worker_returncodes"].values()),
            "APP_WAYLAND_CLEANUP_PENDING")
    require(value.get("payload_logged") is False and value.get("titles_logged") is False and
            value.get("pixels_logged") is False and value.get("waypipe_ends") == 2 and
            type(value.get("waypipe_ends")) is int and value.get("profile_kind") == "synthetic",
            "APP_WAYLAND_RECEIPT_INVALID")
    evidence.validate_lifecycle(value.get("lifecycle"), value, require_precursor_hold=require_precursor_hold)
    if complete:
        gui_evidence.validate_gui(value.get("gui"), value)
    return value


def validate_feasibility_a0(value, *, require_precursor_hold=False):
    """The CI gate requires measured A0, keeping production and Quest separate."""
    require(type(value) is dict and type(value.get("schema")) is int and value["schema"] == 1
            and value.get("stage") == "feasibility" and value.get("status") == "passed"
            and value.get("reason") == "APP_A0_PASSED" and value.get("a0_passed") is True
            and value.get("available") is False and value.get("production_acceptance_passed") is False
            and value.get("quest_verified") is False and value.get("application_payload_logged") is False
            and value.get("pending") == ["v3_acceptance", "quest"], "APP_A0_RECEIPT_INVALID")
    checks = value.get("checks")
    require(type(checks) is dict and set(checks) == {"settings", "recovery", "application"}
            and type(checks.get("settings")) is dict and type(checks.get("recovery")) is dict
            and all(checks["settings"].get(name) is True for name in SETTINGS_CHECKS)
            and all(checks["recovery"].get(name) is True for name in RECOVERY_CHECKS)
            and checks["recovery"].get("application_spawned_for_seeded_recovery") is False,
            "APP_A0_RECEIPT_INVALID")
    application = validate_application_foundation(checks["application"], require_precursor_hold=require_precursor_hold)
    require(application["status"] == "passed" and application["feasibility_verified"] is True,
            "APP_A0_RECEIPT_INVALID")
    observer = application["gui"]["observer"]
    require(value.get("gui_observer_module_sha256") == observer["module_sha256"]
            and value.get("gui_observer_source_sha256") == observer["source_sha256"],
            "APP_WAYLAND_EVIDENCE_MISMATCH")
    return value


def retain_cleanup_owner(pending, artifact):
    """Keep the live pidfd owner until cleanup is proven; never recover by PID.

    Each attempt is bounded by the owner (five seconds). An unresolved owner
    keeps this CLI alive and the profile locked; it is not converted to a
    successful or completed run. Interrupt requests cannot drop that owner.
    The first failed receipt remains failed after eventual cleanup succeeds.
    """
    from linux_real_app_wayland import CleanupPending, retry_cleanup
    require(isinstance(pending, CleanupPending), "APP_WAYLAND_PENDING_HANDLE")
    try:
        write_json(artifact / "cleanup-pending.json", {"schema": 1, "stage": "cleanup", "complete": False,
                   "reason": "APP_WAYLAND_CLEANUP_PENDING", "available": False})
    except (OSError, ValueError):
        # Writing evidence must never interrupt ownership of live descendants.
        pass
    # The run installed its latch-only handlers before the first spawn and
    # retains them through this handoff and the receipt write above. Only its
    # completed finish restores them; a nested guard would restore stale ones.
    while True:
        try:
            result = retry_cleanup(pending)
            if result.get("complete") is True:
                return result
        except CleanupPending as current:
            pending = current
        except (OSError, ValueError):
            # A transient scan/descriptor error is still an unresolved live
            # owner. Retain it just as for an explicit pending result.
            pass
        # The unresolved handle remains live; no stored PID is used.
        time.sleep(.05)


def run_feasibility(artifact, descriptor, *, precursor_hold_check=False):
    artifact = private_artifact_root(artifact)
    result = {"schema": 1, "revision": REVISION, "stage": "feasibility", "status": "failed",
              "reason": "APP_CHECK_NOT_COMPLETED", "a0_passed": False, "available": False,
              "production_acceptance_passed": False, "quest_verified": False, "checks": {},
              "pending": ["application_feasibility", "v3_acceptance", "quest"], "application_payload_logged": False}
    try:
        observed = inspect.inspect_host(descriptor, home=Path(pwd.getpwuid(os.getuid()).pw_dir))
        inspect.write_inspection_receipt(artifact / "inspection", observed)
        require(observed["inspection_passed"], observed["reason"])
        before = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FROZEN_V2}
        result["dependency_fingerprint"] = observed["dependency_fingerprint"]
        bundle = artifact / "settings-bundle"
        built = build_settings(bundle)
        result["settings_module_sha256"] = built["files"]["gio/libeidolon-settings.so"]
        result["checks"]["settings"] = settings_checks(artifact, bundle)
        result["checks"]["recovery"] = recovery_checks(artifact)
        gui_observer_bundle = artifact / "gui-observer"
        gui_built = build_gui_observer(gui_observer_bundle)
        result["gui_observer_module_sha256"] = gui_built["files"]["libeidolon-a0-gui-observer.so"]
        result["gui_observer_source_sha256"] = gui_built["files"]["linux-real-app-gui-observer.c"]
        binary, waypipe_hash = waypipe_binary()
        compositor = build_compositor(artifact)
        result["waypipe_sha256"] = waypipe_hash
        result["compositor_sha256"] = hashlib.sha256(compositor.read_bytes()).hexdigest()
        from linux_real_app_wayland import CleanupPending, run_feasibility as run_wayland
        wayland = artifact / "wayland"
        wayland.mkdir(mode=0o700)
        try:
            result["checks"]["application"] = run_wayland(ROOT, wayland, bundle, waypipe=binary, compositor=compositor,
                                                         precursor_hold_check=precursor_hold_check,
                                                         gui_observer_bundle=gui_observer_bundle)
        except CleanupPending as pending:
            result["checks"]["application"] = pending.receipt
            result["checks"]["application"]["cleanup"] = retain_cleanup_owner(pending, artifact)
            raise BuildError("APP_WAYLAND_CLEANUP_PENDING") from pending
        after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FROZEN_V2}
        require(before == after, "APP_V2_SOURCE_CHANGED")
        final_inspection = inspect.inspect_host(descriptor)
        require(final_inspection["inspection_passed"] and final_inspection["dependency_fingerprint"] == observed["dependency_fingerprint"],
                "APP_DEPENDENCY_CHANGED")
        application = result["checks"]["application"]
        if application.get("status") == "failed":
            token = application.get("error")
            raise BuildError(token if type(token) is str and re.fullmatch(r"APP_[A-Z0-9_]{1,64}", token)
                             else "APP_WAYLAND_FEASIBILITY_FAILED")
        validate_application_foundation(application, require_precursor_hold=precursor_hold_check)
        require(application.get("mousepad_sha256") == observed["executable_sha256"] and
                application.get("waypipe_sha256") == waypipe_hash and application.get("waypipe_version") == "0.11.3",
                "APP_WAYLAND_EVIDENCE_MISMATCH")
        if application["feasibility_verified"] is True:
            result.update(status="passed", reason="APP_A0_PASSED", a0_passed=True,
                          pending=["v3_acceptance", "quest"], frozen_v2_source_sha256=before)
            validate_feasibility_a0(result, require_precursor_hold=precursor_hold_check)
        else:
            result.update(status="incomplete", reason="APP_A0_INCOMPLETE",
                          pending=list(FOUNDATION_PENDING) + ["v3_acceptance", "quest"], frozen_v2_source_sha256=before)
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        token = str(error)
        result.update(status="failed", a0_passed=False)
        result["reason"] = token if re.fullmatch(r"(?:APP|SETTINGS|BUILD|STATE)_[A-Z0-9_]{1,64}", token) else "APP_CHECK_UNAVAILABLE"
    write_json(artifact / "feasibility.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=("feasibility", "acceptance"))
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--reviewed-package", type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.stage == "acceptance":
            artifact = private_artifact_root(args.artifact_root)
            result = {"schema": 1, "stage": "acceptance", "status": "not_implemented", "reason": "APP_V3_NOT_IMPLEMENTED",
                      "available": False, "production_acceptance_passed": False, "quest_verified": False}
            write_json(artifact / "acceptance.json", result)
        else:
            descriptor = inspect._read_operator_descriptor(args.reviewed_package) if args.reviewed_package else None
            result = run_feasibility(args.artifact_root, descriptor)
    except (OSError, ValueError) as error:
        token = str(error)
        result = {"schema": 1, "stage": args.stage, "status": "failed", "available": False,
                  "reason": token if re.fullmatch(r"APP_[A-Z_]{1,64}", token) else "APP_CHECK_UNAVAILABLE"}
    print(inspect.canonical_json(result).decode("utf-8"))
    return 0 if result.get("a0_passed") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
