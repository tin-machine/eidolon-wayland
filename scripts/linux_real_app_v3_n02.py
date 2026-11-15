"""Explicit disposable N02 launch smoke, separate from N01 and formal I6.

Run both fixed variants sequentially on real pinned SSH + ordinary Registry.
No app is spawned by this supervisor. The independent oracle reopens each
native receipt and combines R/T with the synthetic Host SDK owner's S evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys

import linux_real_app_v3_n01 as fixture
import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n02_oracle import CASES, ASSERTIONS, verify, invalid

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = tuple(CASES)
RUNNER = Path("/opt/i6/eidolon-i6-host")
MAX_NATIVE = 1024 * 1024


def run(args):
    from linux_real_app_check import private_artifact_root

    os.umask(0o077)
    artifacts = private_artifact_root(args.artifact_root)
    source = evidence.source_snapshot(ROOT)
    report = {
        "schema":1, "revision":"eidolon-i6-n02-launch-smoke-v1",
        "source":source, "required_variants":list(VERSIONS),
        "required_assertions":list(ASSERTIONS),
        "cases":[{"case_id":"N02","variant":v,"status":"not_run",
                  "reason":"I6_NOT_RUN","verified_assertions":[],
                  "environment_disposal":"not_yet"} for v in VERSIONS],
        "host_v3_acceptance_passed":False,
    }
    try:
        prepare_and_run(args, artifacts, source, report)
    except KeyboardInterrupt:
        report["reason"] = "I6_OPERATOR_CANCELLED"
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        report["reason"] = "I6_EVIDENCE_MISSING"
    if any(c["status"] == "not_run" for c in report["cases"]):
        report.setdefault("reason", "I6_EVIDENCE_MISSING")
    passed = all(c["status"] == "pass" for c in report["cases"])
    report["status"] = "pass" if passed else "cancelled" if report.get("reason") == "I6_OPERATOR_CANCELLED" else "fail"
    report["reason"] = "I6_OK" if passed else report.get("reason", "I6_ASSERTION_FAILED")
    report["n02_two_variant_smoke_passed"] = passed
    report["exit_code"] = 0 if passed else 130 if report["status"] == "cancelled" else 2
    evidence.write_report(artifacts / "n02-validation.json", report)
    print(json.dumps({"status":report["status"],"reason":report["reason"],
                      "n02_two_variant_smoke_passed":passed,
                      "host_v3_acceptance_passed":False},sort_keys=True))
    return report["exit_code"]


def prepare_and_run(args, artifacts, source, report):
    isolation = fixture.disposable_preflight(args.disposable_container)
    fixture.need(args.source_root == ROOT and source["dirty"] is False, "I6_SOURCE_DIRTY")
    fixture_root = fixture.fresh_directory(args.fixture_root)
    accounts = fixture.prepare_accounts()
    app_home = Path(accounts["app"]["passwd_home"])
    driver_home = Path(accounts["driver"]["passwd_home"])
    waypipe = fixture.prepare_base_bundle(app_home)
    raw = fixture.command(["/usr/bin/python3", ROOT / "scripts/build-linux-desktop-v3-bundle",
                           "--reviewed-package", args.reviewed_package],
                          uid=fixture.ACCOUNTS["app"][0],home=app_home,seconds=180)
    build = evidence.parse(raw)
    fixture.need(build.get("status") == "built")
    configuration, before_inputs = fixture.export_bundle(build["build_id"], app_home)
    binary_hash = evidence.sha(fixture.read_owned(args.runner,0,limit=128*1024*1024,private=False))
    sshd = None
    try:
        sshd, pidfd, host_key, ssh = fixture.prepare_sshd(\n            fixture_root, app_home, n02_remote_streamlocal=True)
        for row in report["cases"]:
            variant = row["variant"]
            name = variant.replace("_", "-")
            run_id = secrets.token_hex(16)
            installation, installation_id, storage_id = (secrets.token_hex(16) for _ in range(3))
            driver = fixture.fresh_directory(driver_home / ("n02-" + name),
                                             uid=fixture.ACCOUNTS["driver"][0],
                                             gid=fixture.ACCOUNTS["driver"][0])
            files = fixture.fresh_directory(driver / "files",
                                            uid=fixture.ACCOUNTS["driver"][0],
                                            gid=fixture.ACCOUNTS["driver"][0])
            libraries = fixture.fresh_directory(driver / "lib",
                                                uid=fixture.ACCOUNTS["driver"][0],
                                                gid=fixture.ACCOUNTS["driver"][0])
            fixture.exclusive(libraries / "libwaypipe_bin.so", waypipe,
                              uid=fixture.ACCOUNTS["driver"][0],
                              gid=fixture.ACCOUNTS["driver"][0], mode=0o700)
            descriptor = {
                "schema":1,"run_id":run_id,"installation":installation,
                "installation_id":installation_id,"profile_storage_id":storage_id,
                "profile":{"schema":2,"profile_id":"remote-linux","installation":installation,
                           "revision":"1","display_name":"N02 fixture","backend":"linux_ssh",
                           "host":"127.0.0.1","port":ssh["port"],
                           "username":"eidolon-app","host_key":host_key},
                "configuration":configuration, "files_dir":str(files), "native_library_dir":str(libraries),
            }
            descriptor_path = driver / "descriptor.json"
            output_path = driver / "native.json"
            fixture.exclusive(descriptor_path,evidence.canonical(descriptor),
                              uid=fixture.ACCOUNTS["driver"][0],
                              gid=fixture.ACCOUNTS["driver"][0])
            credential = os.open(fixture_root / "client-key",os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
            diagnostic = {}
            try:
                returncode,_ = fixture.run_capture(
                    [args.runner,"--descriptor",descriptor_path,"--credential-fd",str(credential),
                     "--output",output_path,"--variant",variant],
                    uid=fixture.ACCOUNTS["driver"][0],home=driver_home,seconds=180,
                    pass_fds=(credential,),diagnostic=diagnostic)
            finally:
                os.close(credential)
            # This is independent of the product's Runtime::snapshot; no
            # supervisor process kill can manufacture a success census.
            native = fixture.read_native_receipt(output_path,fixture.ACCOUNTS["driver"][0],
                                                  {"stage":"native_receipt"})
            native_name = "n02-" + variant + "-native.json"
            evidence.write_report(artifacts / native_name, native)
            counts = fixture.settled_census(sshd)
            counts.update(fixture.filesystem_census(files, app_home, installation_id, storage_id))
            _, after_inputs = fixture.export_bundle(build["build_id"], app_home)
            fixture.need(evidence.sha(fixture.read_owned(args.runner,0,limit=128*1024*1024,
                                private=False)) == binary_hash, "I6_SOURCE_MISMATCH")
            environment = {
                "schema":1,"revision":"eidolon-i6-n02-environment-v1",
                "run_id":run_id,"owner_id":native["owner_id"],
                "source_sha256":source["tree_sha256"],
                **accounts,"ssh":ssh,"before":before_inputs,"after":after_inputs,
                "runner":{"source_sha256":source["tree_sha256"],
                          "binary_sha256":binary_hash},
                "isolation":isolation,"final":counts,
            }
            # The oracle reads *saved* evidence bytes, not a status produced
            # by this supervisor. Failure is retained even if rescue follows.
            try:
                measured = evidence.parse(evidence.read_report(artifacts / native_name))
                outcome = verify(measured,environment,source,variant)
            except (ValueError,TypeError,KeyError,RecursionError):
                outcome = invalid()
            if returncode != 0 or diagnostic.get("reason") == "I6_DRIVER_DIED":
                outcome = invalid()
            row.update(status=outcome["status"],reason=outcome["reason"],
                       verified_assertions=outcome["verified_assertions"],
                       environment_disposal="not_yet")
            environment_name = "n02-" + variant + "-environment.json"
            evidence.write_report(artifacts / environment_name, environment)
            if outcome["status"] != "pass":
                # Same C2 account/lease must not be used by the next run until
                # this runtime's actual retirement has been proven.
                break
    finally:
        if sshd is not None:
            fixture.stop_owned(sshd, pidfd)
        for name in ("client-key","host-key"):
            path = fixture_root / name
            if path.is_file() and not path.is_symlink():
                path.unlink()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--disposable-container",action="store_true")
    p.add_argument("--source-root",type=Path,default=ROOT)
    p.add_argument("--fixture-root",type=Path,default=Path("/run/eidolon-i6"))
    p.add_argument("--reviewed-package",type=Path,default=Path("/opt/i6/reviewed-package.json"))
    p.add_argument("--runner",type=Path,default=RUNNER)
    p.add_argument("--artifact-root",type=Path,required=True)
    args = p.parse_args(argv)
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        return run(args)
    finally:
        signal.signal(signal.SIGTERM,previous)


if __name__ == "__main__":
    raise SystemExit(main())
