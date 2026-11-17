"""N07-B2 disposable real-GUI Host diagnostic, never N07 acceptance.

The normal Registry -> Runtime -> SSH -> C2 -> Waypipe -> Mousepad path is
driven by the unprivileged Host runner. The supervisor is a separate process
owner: it pins SSH host keys, builds the reviewed package, retains source and
isolated account identities and NEVER toggles GTK settings or fabricates ROIs.
The single-session candidate toggle uses real pointer delivery and four renderer
readbacks but cannot certify menu identity without a separately reviewed golden.
It never verifies N07's 13 assertions or the new-nonce persistence case.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess

import linux_real_app_v3_n01 as fixture
import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n07_b2_probe import ProbeInvalid, inspect

ROOT = Path(__file__).resolve().parents[1]
RUNNER = Path("/opt/i6/eidolon-i6-host")


def run(args) -> int:
    from linux_real_app_check import private_artifact_root

    os.umask(0o077)
    artifacts = private_artifact_root(args.artifact_root)
    source = evidence.source_snapshot(ROOT)
    report = {
        "schema": 1, "revision": "eidolon-i6-n07-b2-diagnostic-v1",
        "source": source, "status": "not_run", "reason": "I6_NOT_RUN",
        "probe_status": "not_run", "verified_n07_assertions": [],
        "host_v3_acceptance_passed": False,
    }
    try:
        prepare_and_run(args, artifacts, source, report)
    except KeyboardInterrupt:
        report.update(status="cancelled", reason="I6_OPERATOR_CANCELLED")
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        report.update(status="fail", reason="I6_EVIDENCE_MISSING")
    if report["status"] == "not_run":
        report.update(status="fail", reason="I6_EVIDENCE_MISSING")
    # The status intentionally cannot be "pass", even after a real B1 probe.
    # A probe-only verdict must not enter I6 formal acceptance aggregation.
    report["exit_code"] = 0 if report["status"] == "probe_only" else 2
    evidence.write_report(artifacts / "n07-b2-validation.json", report)
    print(json.dumps({
        "status": report["status"], "reason": report["reason"],
        "verified_n07_assertions": [],
        "host_v3_acceptance_passed": False,
    }, sort_keys=True))
    return report["exit_code"]


def prepare_and_run(args, artifacts: Path, source: dict, report: dict) -> None:
    report["stage"] = "disposable_preflight"
    isolation = fixture.disposable_preflight(args.disposable_container)
    fixture.need(args.source_root == ROOT and source["dirty"] is False,
                 "I6_SOURCE_DIRTY")
    fixture_root = fixture.fresh_directory(args.fixture_root)
    accounts = fixture.prepare_accounts()
    app_home = Path(accounts["app"]["passwd_home"])
    driver_home = Path(accounts["driver"]["passwd_home"])
    report["stage"] = "prepare_bundle"
    waypipe = fixture.prepare_base_bundle(app_home)
    raw = fixture.command([
        "/usr/bin/python3", ROOT / "scripts/build-linux-desktop-v3-bundle",
        "--reviewed-package", args.reviewed_package,
    ], uid=fixture.ACCOUNTS["app"][0], home=app_home, seconds=180)
    bundle = evidence.parse(raw)
    fixture.need(bundle.get("status") == "built")
    configuration, before_inputs = fixture.export_bundle(bundle["build_id"], app_home)
    runner_hash = evidence.sha(fixture.read_owned(
        args.runner, 0, limit=128 * 1024 * 1024, private=False))
    report["stage"] = "start_sshd"
    sshd = None
    try:
        sshd, pidfd, host_key, ssh = fixture.prepare_sshd(
            fixture_root, app_home, n02_remote_streamlocal=True)
        run_id = secrets.token_hex(16)
        installation, installation_id, storage_id = (
            secrets.token_hex(16) for _ in range(3))
        driver = fixture.fresh_directory(
            driver_home / ("n07-b2-" + run_id),
            uid=fixture.ACCOUNTS["driver"][0], gid=fixture.ACCOUNTS["driver"][0])
        files = fixture.fresh_directory(
            driver / "files",
            uid=fixture.ACCOUNTS["driver"][0], gid=fixture.ACCOUNTS["driver"][0])
        libraries = fixture.fresh_directory(
            driver / "lib",
            uid=fixture.ACCOUNTS["driver"][0], gid=fixture.ACCOUNTS["driver"][0])
        fixture.exclusive(
            libraries / "libwaypipe_bin.so", waypipe,
            uid=fixture.ACCOUNTS["driver"][0],
            gid=fixture.ACCOUNTS["driver"][0], mode=0o700)
        descriptor = {
            "schema": 1, "run_id": run_id,
            "installation": installation, "installation_id": installation_id,
            "profile_storage_id": storage_id,
            "profile": {
                "schema": 2, "profile_id": "remote-linux",
                "installation": installation, "revision": "1",
                "display_name": "N07-B2 fixture", "backend": "linux_ssh",
                "host": "127.0.0.1", "port": ssh["port"],
                "username": "eidolon-app", "host_key": host_key,
            },
            "configuration": configuration,
            "files_dir": str(files), "native_library_dir": str(libraries),
        }
        descriptor_path = driver / "descriptor.json"
        output_path = driver / "native.json"
        fixture.exclusive(
            descriptor_path, evidence.canonical(descriptor),
            uid=fixture.ACCOUNTS["driver"][0], gid=fixture.ACCOUNTS["driver"][0])
        credential = os.open(
            fixture_root / "client-key", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        diagnostic = {}
        report["stage"] = "native_runner"
        try:
            returncode, _ = fixture.run_capture([
                args.runner, "--descriptor", descriptor_path,
                "--credential-fd", str(credential),
                "--output", output_path,
                "--n07-probe-variant", "line_numbers_four_rois_probe",
            ], uid=fixture.ACCOUNTS["driver"][0], home=driver_home,
                seconds=180, pass_fds=(credential,), diagnostic=diagnostic)
        finally:
            os.close(credential)
        report["runner_diagnostic"] = diagnostic
        report["stage"] = "native_receipt"
        native = fixture.read_native_receipt(
            output_path, fixture.ACCOUNTS["driver"][0], {"stage": "native_receipt"})
        evidence.write_report(artifacts / "n07-b2-native.json", native)
        report["stage"] = "independent_environment"
        counts = fixture.settled_census(sshd)
        counts.update(fixture.filesystem_census(
            files, app_home, installation_id, storage_id))
        _, after_inputs = fixture.export_bundle(bundle["build_id"], app_home)
        fixture.need(evidence.sha(fixture.read_owned(
            args.runner, 0, limit=128 * 1024 * 1024,
            private=False)) == runner_hash, "I6_SOURCE_MISMATCH")
        environment = {
            "schema": 1, "revision": "eidolon-i6-n07-b2-environment-v1",
            "run_id": run_id, "source_sha256": source["tree_sha256"],
            **accounts, "ssh": ssh, "before": before_inputs,
            "after": after_inputs, "runner": {
                "source_sha256": source["tree_sha256"],
                "binary_sha256": runner_hash,
            }, "isolation": isolation, "final": counts,
        }
        evidence.write_report(artifacts / "n07-b2-environment.json", environment)
        report["stage"] = "independent_probe_validation"
        measured = evidence.parse(evidence.read_report(
            artifacts / "n07-b2-native.json"))
        result = inspect(measured)
        if returncode != 0 or diagnostic.get("reason") == "I6_DRIVER_DIED":
            raise ProbeInvalid("N07_B2_DRIVER_INCOMPLETE")
        # A fresh consumer must see exactly this source and owner, not a
        # second producer's success flag or untrusted bundle substitution.
        fixture.need(measured["source_sha256"] == source["tree_sha256"]
                     and environment["run_id"] == measured["run_id"]
                     and environment["runner"]["source_sha256"] ==
                     source["tree_sha256"], "I6_SOURCE_MISMATCH")
        report.update(status="probe_only", reason="N07_B2_DIAGNOSTIC_ONLY",
                      probe_status=result["status"], probe=result)
    finally:
        if sshd is not None:
            fixture.stop_owned(sshd, pidfd)
        for name in ("client-key", "host-key"):
            path = fixture_root / name
            if path.is_file() and not path.is_symlink():
                path.unlink()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disposable-container", action="store_true")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--fixture-root", type=Path,
                        default=Path("/run/eidolon-i6"))
    parser.add_argument("--reviewed-package", type=Path,
                        default=Path("/opt/i6/reviewed-package.json"))
    parser.add_argument("--runner", type=Path, default=RUNNER)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args(argv)
    old = signal.signal(
        signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        return run(args)
    finally:
        signal.signal(signal.SIGTERM, old)


if __name__ == "__main__":
    raise SystemExit(main())
