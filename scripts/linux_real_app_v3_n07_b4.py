"""Disposable B4 two-session collection with independent, live C1 observations.

One Host process retains one Registry and creates two real Runtime owners. The
root supervisor owns provisioning, the retained read-only C1 root, the fixed
prelaunch document, per-process fingerprint observers and bounded IPC barriers.
It never changes Mousepad settings or supplies product cleanup evidence.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import socket
import subprocess
import time

import linux_desktop_v3_contract as contract
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n01 as fixture
from linux_real_app_v3_n07_b4_storage import TrustedStorage, storage_record, need
from linux_real_app_v3_n07_fingerprint import LiveFingerprintObserver, prepare_fixture
from linux_real_app_v3_n07_settings import validate_file_sequence

ROOT = Path(__file__).resolve().parents[1]
PHASES = ("first_before_launch", "first_after_toggle", "first_retired",
          "second_before_launch", "second_after_observation", "second_retired")
MAX_CONTEXT = 4 * 1024 * 1024


def fixed_reason(error, default="N07_B4_OBSERVATION_FAILED"):
    value = str(error)
    return value if re.fullmatch(r"(?:I6|N07)_[A-Z0-9_]{1,100}", value) else default


def process_identity(pid, uid):
    directory = Path("/proc") / str(pid)
    raw = (directory / "stat").read_bytes()
    status = (directory / "status").read_bytes()
    values = [line.split()[1:] for line in status.splitlines() if line.startswith(b"Uid:")]
    need(len(values) == 1 and tuple(map(int, values[0])) == (uid,) * 4,
         "N07_B4_DRIVER_IDENTITY")
    return {"pid": pid, "start_ticks": int(raw.rsplit(b")", 1)[1].split()[19]), "uid": uid}


def clean_census(counts):
    need(type(counts) is dict and set(counts) == {
        "app_process_count", "driver_child_count", "sshd_child_count",
        "journal_entries", "journal_lock_held", "profile_lock_held"}, "N07_B4_CENSUS_SHAPE")
    need(all(type(counts[k]) is int and counts[k] == 0 for k in (
        "app_process_count", "driver_child_count", "sshd_child_count", "journal_entries"))
        and counts["journal_lock_held"] is False and counts["profile_lock_held"] is False,
        "N07_B4_CLEANUP_INCOMPLETE")


class BarrierSupervisor:
    def __init__(self, *, descriptor, accounts, source, artifacts, output_dir,
                 files, sshd, ssh, isolation, before, build_id, runner_hash, policy):
        self.descriptor, self.accounts, self.source = descriptor, accounts, source
        self.artifacts, self.output_dir, self.files = artifacts, output_dir, files
        self.sshd, self.ssh, self.isolation = sshd, ssh, isolation
        self.before, self.build_id, self.runner_hash, self.policy = before, build_id, runner_hash, policy
        self.storage = TrustedStorage(storage_record(descriptor), accounts["app"])
        self.child = None
        self.child_identity = None
        self.observers, self.observer_active = {}, set()
        self.natives, self.contexts, self.environments, self.fingerprints = {}, {}, {}, {}
        self.index = 0
        self.identities = {}
        self.record = {
            "schema": 1, "revision": "eidolon-i6-n07-b4-supervisor-v1",
            "run_id": descriptor["run_id"], "source": source,
            "trusted_storage": None, "document": None, "keyfile": {},
            "supplemental_keyfile": {}, "barriers": [], "fingerprint_observers": {},
            "failures": [], "external_rescue": False, "final_census": None,
        }

    def failure(self, error):
        reason = fixed_reason(error)
        if reason not in self.record["failures"]:
            self.record["failures"].append(reason)
        return reason

    def save(self):
        self.record["trusted_storage"] = self.storage.metadata
        self.record["document"] = self.storage.document
        # Root-private, fixed-name checkpoint: retain the cumulative barrier
        # history even when a later session fails. Never use the ordinary
        # evidence writer's exclusive-create API as an overwrite operation.
        raw = evidence.canonical(self.record) + b"\n"
        need(len(raw) <= evidence.MAX_REPORT_BYTES, "N07_B4_SUPERVISOR_BOUNDS")
        temporary = self.artifacts / ".n07-b4-supervisor.tmp"
        fixture.exclusive(temporary, raw)
        os.replace(temporary, self.artifacts / "n07-b4-supervisor.json")
        fd = os.open(self.artifacts, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _request(self, request):
        need(type(request) is dict and set(request) == {
            "schema", "run_id", "session", "phase", "runtime", "capture_id",
            "capture", "context", "transport_watermark", "monotonic_ns"}, "N07_B4_BARRIER_SHAPE")
        phase, session = request["phase"], request["session"]
        need(type(request["schema"]) is int and request["schema"] == 1
             and request["run_id"] == self.descriptor["run_id"]
             and session in ("first", "second") and phase in PHASES
             and phase.startswith(session + "_") and contract.decimal(request["runtime"])
             and contract.hexadecimal(request["capture_id"], 64)
             and type(request["transport_watermark"]) is int and request["transport_watermark"] > 0
             and type(request["monotonic_ns"]) is int and 0 < request["monotonic_ns"] <= time.monotonic_ns(),
             "N07_B4_BARRIER_IDENTITY")
        if not self.record["failures"]:
            need(self.index < len(PHASES) and phase == PHASES[self.index], "N07_B4_BARRIER_ORDER")
        else:
            need(phase == session + "_retired" and session in self.identities,
                 "N07_B4_FAILURE_LAUNCH_REFUSED")
        context = contract.Context.parse(contract.canonical_json(request["context"]))
        self.storage.record.matches_context(context)
        cap = request["capture"]
        need(context.record()["uid"] == self.accounts["app"]["uid"]
             and context.record()["nonce"] == cap["nonce"]
             and context.record()["session"] == cap["session"]
             and context.scope() == cap["scope"] == cap["context_digest"]
             and request["capture_id"] == cap["capture_id"], "N07_B4_CONTEXT_MISMATCH")
        identity = {k: request[k] for k in ("runtime", "capture_id", "capture", "context")}
        if session not in self.identities:
            need(phase == session + "_before_launch", "N07_B4_BARRIER_ORDER")
            if session == "second":
                first = self.identities["first"]
                need(all(first[k] != identity[k] for k in ("runtime", "capture_id"))
                     and first["capture"]["nonce"] != cap["nonce"], "N07_B4_IDENTITY_REUSED")
                need(self.record["barriers"][-1]["request"]["phase"] == "first_retired"
                     and self.record["barriers"][-1]["response"]["allow"] is True
                     and self.record["barriers"][-1]["observation"]["product_cleanup_verified"] is True,
                     "N07_B4_SECOND_BEFORE_CLEANUP")
            self.identities[session] = copy.deepcopy(identity)
        need(evidence.canonical(identity) == evidence.canonical(self.identities[session]),
             "N07_B4_SESSION_CHANGED")
        self.index += 1

    def _start_observer(self, session):
        need(session not in self.observers, "N07_B4_OBSERVER_REUSED")
        observer = LiveFingerprintObserver(app_uid=self.accounts["app"]["uid"],
            run_id=self.descriptor["run_id"], source=self.source, policy=self.policy)
        observer.__enter__()
        self.observers[session] = observer
        self.observer_active.add(session)
        self.record["fingerprint_observers"][session] = {
            "started_at_ns": time.monotonic_ns(), "stopped_at_ns": None}

    def _stop_observer(self, session):
        if session in self.observer_active:
            self.observers[session].__exit__(None, None, None)
            self.observer_active.remove(session)
            self.record["fingerprint_observers"][session]["stopped_at_ns"] = time.monotonic_ns()

    def _snapshot_true(self):
        # Only observe the asynchronous normal GSettings flush; never write it.
        deadline = time.monotonic() + 3
        while True:
            proof = self.storage.snapshot()
            if proof["key_present"] and proof["value"] is True:
                return proof
            need(time.monotonic() < deadline, "N07_B4_KEY_NOT_ENABLED")
            time.sleep(0.025)

    def export_session(self, session):
        for role, limit in (("native", fixture.MAX_OUTPUT), ("context", MAX_CONTEXT)):
            name = session + "-" + role + ".json"
            path = self.output_dir / name
            if not path.exists():
                continue
            raw = fixture.read_owned(path, self.accounts["driver"]["uid"], limit=limit)
            value = evidence.parse(raw, limit=limit)
            output = self.artifacts / name
            if output.exists():
                need(output.read_bytes() == raw, "N07_B4_RECEIPT_CHANGED")
            else:
                fixture.exclusive(output, raw)
            if role == "native":
                self.natives[session] = value
            else:
                self.contexts[session] = raw

    def _census(self):
        # The retained Host control process is still at the barrier after all
        # Runtime owners retire. Exclude only its live pidfd-owned identity;
        # expose the raw count as well. Any other driver-UID process is a leak.
        need(self.child is not None and self.child.poll() is None, "N07_B4_DRIVER_DIED")
        need(process_identity(self.child.pid, self.accounts["driver"]["uid"])
             == self.child_identity, "N07_B4_DRIVER_IDENTITY")
        deadline = time.monotonic() + 3
        while True:
            counts = fixture.process_census(self.sshd.pid)
            raw = counts["driver_child_count"]
            need(raw >= 1, "N07_B4_DRIVER_IDENTITY")
            counts["driver_child_count"] = raw - 1
            if not any(counts.values()) or time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        counts.update(fixture.filesystem_census(self.files, self.storage.home,
            self.descriptor["installation_id"], self.descriptor["profile_storage_id"]))
        control = {**self.child_identity, "raw_driver_process_count": raw, "excluded_live_control": 1}
        return counts, control

    def _retired(self, session, observation):
        from linux_real_app_v3_n07_b4_probe import validate_session
        self._stop_observer(session)
        self.export_session(session)
        counts, control = self._census()
        observation.update(census=counts, driver_control=control)
        _, after = fixture.export_bundle(self.build_id, self.storage.home)
        native = self.natives.get(session)
        need(native is not None, "N07_B4_NATIVE_MISSING")
        environment = {
            "schema": 1, "revision": "eidolon-i6-n07-b4-environment-v1",
            "run_id": self.descriptor["run_id"], "owner_id": native["owner_id"],
            "source_sha256": self.source["tree_sha256"], **self.accounts,
            "ssh": self.ssh, "before": self.before, "after": after,
            "runner": {"source_sha256": self.source["tree_sha256"], "binary_sha256": self.runner_hash},
            "isolation": self.isolation, "final": counts, "driver_control": control,
        }
        self.environments[session] = environment
        evidence.write_report(self.artifacts / (session + "-environment.json"), environment)
        # Preserve observations even when exercise or product retirement fails.
        if session in self.observers:
            fingerprint = self.observers[session].finish(native, environment)
            self.fingerprints[session] = fingerprint
            evidence.write_report(self.artifacts / (session + "-fingerprint.json"), fingerprint)
        clean_census(counts)
        validate_session(native, environment, self.source, session,
                         context_raw=self.contexts.get(session))
        observation["product_cleanup_verified"] = True
        need(session in self.fingerprints, "N07_B4_FINGERPRINT_MISSING")
        fp = self.fingerprints[session]
        need(fp["missing"] in ([], ["theme"])
             and fp["evidence"]["observation_errors"] == [], "N07_B4_FINGERPRINT_INCOMPLETE")
        if session == "second":
            need(evidence.canonical(self.fingerprints["first"]["values"]) ==
                 evidence.canonical(fp["values"]), "N07_B4_FINGERPRINT_MISMATCH")

    def preserve_partial_session(self, session):
        """Save actual failed-run observations without making a retired gate."""
        native = self.natives.get(session)
        counts = self.record["final_census"]
        if native is not None and session not in self.environments and counts is not None:
            _, after = fixture.export_bundle(self.build_id, self.storage.home)
            # This is measured after the child has exited, not at its missing
            # retirement barrier. It must never satisfy the live-control gate.
            control = {**(self.child_identity or {}),
                       "raw_driver_process_count": counts["driver_child_count"],
                       "excluded_live_control": 0}
            env = {"schema": 1, "revision": "eidolon-i6-n07-b4-partial-environment-v1",
                "observation_phase": "after_driver_exit", "run_id": self.descriptor["run_id"],
                "owner_id": native.get("owner_id"), "source_sha256": self.source["tree_sha256"],
                **self.accounts, "ssh": self.ssh, "before": self.before, "after": after,
                "runner": {"source_sha256": self.source["tree_sha256"], "binary_sha256": self.runner_hash},
                "isolation": self.isolation, "final": counts, "driver_control": control}
            self.environments[session] = env
            evidence.write_report(self.artifacts / (session + "-environment.json"), env)
        observer = self.observers.get(session)
        if observer is None or session in self.fingerprints:
            return
        try:
            need(native is not None and session in self.environments, "N07_B4_FINGERPRINT_BINDING_MISSING")
            record = observer.finish(native, self.environments[session])
            self.fingerprints[session] = record
            evidence.write_report(self.artifacts / (session + "-fingerprint.json"), record)
        except (OSError, ValueError, TypeError, KeyError) as error:
            reason = self.failure(error)
            # Explicitly not a fingerprint receipt. Only allowlisted metadata
            # already gathered by the bounded same-UID observer is retained.
            evidence.write_report(self.artifacts / (session + "-fingerprint-diagnostic.json"), {
                "schema": 1, "revision": "eidolon-i6-n07-b4-partial-fingerprint-v1",
                "session": session, "run_id": self.descriptor["run_id"],
                "reason": reason, "process": observer.process,
                "fonts": list(observer.fonts.values()), "libraries": observer.libraries,
                "observation_errors": sorted(observer.errors), "reviewed_golden": False,
            })

    def respond(self, request_raw):
        request = evidence.parse(request_raw, limit=16384)
        received = time.monotonic_ns()
        digest = evidence.sha(request_raw)
        phase = request.get("phase") if type(request) is dict else None
        session = request.get("session") if type(request) is dict else None
        observation = {"phase": phase, "session": session, "observed_at_ns": received,
                       "keyfile": None, "census": None, "driver_control": None,
                       "product_cleanup_verified": False, "document_sha256": None}
        allowed = False
        try:
            self._request(request)
            if phase == "first_before_launch":
                self.storage.open_after_inspect(request["context"])
                proof = self.storage.snapshot()
                self.record["keyfile"]["before_first_launch"] = proof
                observation["keyfile"] = proof
                need(proof["key_present"] is False and proof["value"] is None,
                     "N07_INITIAL_KEY_PRESENT")
                self.storage.create_document_before_launch()
                self._start_observer(session)
            elif phase == "second_before_launch":
                self.storage.assert_current()
                proof = self.storage.snapshot()
                self.record["supplemental_keyfile"][phase] = proof
                observation["keyfile"] = proof
                need(proof["key_present"] is True and proof["value"] is True,
                     "N07_B4_KEY_NOT_PERSISTED")
                self._start_observer(session)
            elif phase in ("first_after_toggle", "second_after_observation"):
                proof = self._snapshot_true()
                name = "after_first_toggle" if session == "first" else "second_session"
                self.record["keyfile"][name] = proof
                observation["keyfile"] = proof
                values = self.record["keyfile"]
                validate_file_sequence(values["before_first_launch"],
                    values["after_first_toggle"], values.get("second_session", proof))
                self._stop_observer(session)
            else:
                self._retired(session, observation)
            if self.storage.document is not None:
                observation["document_sha256"] = self.storage.verify_document()["sha256"]
            observation["observed_at_ns"] = time.monotonic_ns()
            allowed = not self.record["failures"]
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
            self.failure(error)
        observation_hash = evidence.sha(evidence.canonical(observation))
        response = {"schema": 1, "phase": phase, "request_sha256": digest,
                    "allow": allowed, "observation_sha256": observation_hash}
        event = {"request": request, "request_sha256": digest, "received_at_ns": received,
                 "observation": observation, "observation_sha256": observation_hash,
                 "response": response, "sent_at_ns": None}
        self.record["barriers"].append(event)
        return evidence.canonical(response) + b"\n", event

    def close(self):
        for session in tuple(self.observer_active):
            self._stop_observer(session)
        self.storage.close()


def run_host(argv, supervisor, *, uid, home, credential, channel):
    """Own one Host child by pidfd, bound all output, keep failed evidence."""
    parent, child_socket = channel
    child = subprocess.Popen([str(v) for v in argv], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT,
        env=fixture.clean_environment(home), pass_fds=(credential, child_socket.fileno()),
        user=uid, group=uid, extra_groups=[], start_new_session=True)
    child_socket.close()
    supervisor.child = child
    pidfd = None
    selector, fragment, stderr, total = selectors.DefaultSelector(), bytearray(), bytearray(), 0
    started, deadline = time.monotonic(), time.monotonic() + 300
    try:
        pidfd = os.pidfd_open(child.pid)
        supervisor.child_identity = process_identity(child.pid, uid)
        for name, stream in (("stdout", child.stdout), ("stderr", child.stderr), ("barrier", parent)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map() or child.poll() is None:
            need(time.monotonic() < deadline, "N07_B4_DRIVER_TIMEOUT")
            for key, _ in selector.select(0.05):
                data = os.read(key.fd, 16384)
                if not data:
                    selector.unregister(key.fd)
                    if key.data == "barrier":
                        need(not fragment, "N07_B4_BARRIER_TRUNCATED")
                    continue
                total += len(data)
                need(total <= fixture.MAX_OUTPUT, "N07_B4_OUTPUT_BOUNDS")
                if key.data == "stderr":
                    stderr.extend(data)
                    del stderr[:-2048]
                elif key.data == "barrier":
                    fragment.extend(data)
                    need(len(fragment) <= 16384, "N07_B4_BARRIER_BOUNDS")
                    if b"\n" in fragment:
                        line, remaining = bytes(fragment).split(b"\n", 1)
                        need(not remaining, "N07_B4_BARRIER_PIPELINED")
                        fragment.clear()
                        answer, event = supervisor.respond(line)
                        # Responses are <1KiB. A blocked/broken consumer fails;
                        # there is no unbounded write or external fake ACK.
                        event["sent_at_ns"] = time.monotonic_ns()
                        need(parent.send(answer) == len(answer), "N07_B4_BARRIER_WRITE")
                        supervisor.save()
        return child.wait(timeout=1), fixture.runner_diagnostics(
            child.returncode, int((time.monotonic() - started) * 1000), stderr)
    finally:
        if child.poll() is None:
            supervisor.record["external_rescue"] = True
            if pidfd is None:
                child.terminate()  # Exact owned, unreaped Popen child only.
            else:
                signal.pidfd_send_signal(pidfd, signal.SIGTERM)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if pidfd is None:
                    child.kill()
                else:
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                child.wait(timeout=3)
        selector.close()
        if pidfd is not None:
            os.close(pidfd)
        parent.close()
        child.stdout.close()
        child.stderr.close()


def prepare_and_run(args, artifacts, source, report):
    report["stage"] = "disposable_preflight"
    isolation = fixture.disposable_preflight(args.disposable_container)
    fixture.need(args.source_root == ROOT and source["dirty"] is False, "I6_SOURCE_DIRTY")
    fixture_root = fixture.fresh_directory(args.fixture_root)
    accounts = fixture.prepare_accounts()
    policy = prepare_fixture(disposable=True)
    app_home, driver_home = (Path(accounts[k]["passwd_home"]) for k in ("app", "driver"))
    report["stage"] = "prepare_bundle"
    waypipe = fixture.prepare_base_bundle(app_home)
    bundle = evidence.parse(fixture.command([
        "/usr/bin/python3", ROOT / "scripts/build-linux-desktop-v3-bundle",
        "--reviewed-package", args.reviewed_package],
        uid=accounts["app"]["uid"], home=app_home, seconds=180))
    fixture.need(bundle.get("status") == "built")
    configuration, before = fixture.export_bundle(bundle["build_id"], app_home)
    runner_hash = evidence.sha(fixture.read_owned(args.runner, 0,
        limit=128 * 1024 * 1024, private=False))
    sshd, supervisor, output_path = None, None, None
    try:
        report["stage"] = "start_sshd"
        sshd, sshd_pidfd, host_key, ssh = fixture.prepare_sshd(
            fixture_root, app_home, n02_remote_streamlocal=True)
        run_id, installation, installation_id, storage_id = (secrets.token_hex(16) for _ in range(4))
        driver = fixture.fresh_directory(driver_home / ("n07-b4-" + run_id),
            uid=accounts["driver"]["uid"], gid=accounts["driver"]["uid"])
        dirs = {name: fixture.fresh_directory(driver / name,
            uid=accounts["driver"]["uid"], gid=accounts["driver"]["uid"])
            for name in ("files", "lib", "output")}
        fixture.exclusive(dirs["lib"] / "libwaypipe_bin.so", waypipe,
            uid=accounts["driver"]["uid"], gid=accounts["driver"]["uid"], mode=0o700)
        descriptor = {
            "schema": 1, "run_id": run_id, "installation": installation,
            "installation_id": installation_id, "profile_storage_id": storage_id,
            "profile": {"schema": 2, "profile_id": "remote-linux",
                "installation": installation, "revision": "1", "display_name": "N07-B4 fixture",
                "backend": "linux_ssh", "host": "127.0.0.1", "port": ssh["port"],
                "username": "eidolon-app", "host_key": host_key},
            "configuration": configuration, "files_dir": str(dirs["files"]),
            "native_library_dir": str(dirs["lib"]),
        }
        descriptor_path, output_path = driver / "descriptor.json", driver / "native.json"
        fixture.exclusive(descriptor_path, evidence.canonical(descriptor),
            uid=accounts["driver"]["uid"], gid=accounts["driver"]["uid"])
        supervisor = BarrierSupervisor(descriptor=descriptor, accounts=accounts, source=source,
            artifacts=artifacts, output_dir=dirs["output"], files=dirs["files"], sshd=sshd,
            ssh=ssh, isolation=isolation, before=before, build_id=bundle["build_id"],
            runner_hash=runner_hash, policy=policy)
        channel = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        credential = os.open(fixture_root / "client-key", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        report["stage"] = "two_session_host"
        try:
            code, diagnostic = run_host([
                args.runner, "--descriptor", descriptor_path, "--credential-fd", credential,
                "--output", output_path, "--n07-b4-fd", channel[1].fileno(),
                "--n07-b4-document", supervisor.storage.document_path,
                "--n07-b4-output-dir", dirs["output"]], supervisor,
                uid=accounts["driver"]["uid"], home=driver_home,
                credential=credential, channel=channel)
            report["runner_diagnostic"] = diagnostic
        finally:
            os.close(credential)
            for endpoint in channel:
                endpoint.close()
        report["stage"] = "saved_evidence"
        for session in ("first", "second"):
            supervisor.export_session(session)
        native = fixture.read_native_receipt(output_path, accounts["driver"]["uid"], {})
        evidence.write_report(artifacts / "n07-b4-native.json", native)
        counts = fixture.settled_census(sshd)
        counts.update(fixture.filesystem_census(dirs["files"], app_home, installation_id, storage_id))
        supervisor.record["final_census"] = counts
        supervisor.storage.verify_document()
        supervisor.save()
        need(evidence.sha(fixture.read_owned(args.runner, 0,
            limit=128 * 1024 * 1024, private=False)) == runner_hash, "I6_SOURCE_MISMATCH")
        need(code == 0 and not supervisor.record["failures"]
             and supervisor.record["external_rescue"] is False, "N07_B4_RUN_INCOMPLETE")
        clean_census(counts)
        from linux_real_app_v3_n07_b4_probe import validate_collection
        result = validate_collection(native, supervisor.record,
            supervisor.natives.get("first"), supervisor.natives.get("second"),
            supervisor.environments, supervisor.fingerprints, supervisor.contexts, source,
            native_raws={s: (artifacts / (s + "-native.json")).read_bytes()
                         for s in ("first", "second")})
        report.update(status="collected", reason="N07_B4_UNAPPROVED_COLLECTION",
                      collection=result)
    finally:
        if supervisor is not None:
            # Stop observer children before taking any final OS census, even
            # if the Host failed before it could reach a retired barrier.
            for session in tuple(supervisor.observer_active):
                supervisor._stop_observer(session)
            for session in ("first", "second"):
                try:
                    supervisor.export_session(session)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    supervisor.failure(error)
            if output_path is not None and output_path.exists() and not (artifacts / "n07-b4-native.json").exists():
                try:
                    raw = fixture.read_owned(output_path, accounts["driver"]["uid"])
                    evidence.parse(raw)
                    fixture.exclusive(artifacts / "n07-b4-native.json", raw)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    supervisor.failure(error)
            if supervisor.record["final_census"] is None and sshd is not None:
                try:
                    counts = fixture.settled_census(sshd)
                    counts.update(fixture.filesystem_census(dirs["files"], app_home, installation_id, storage_id))
                    supervisor.record["final_census"] = counts
                except (OSError, ValueError, TypeError, KeyError) as error:
                    supervisor.failure(error)
            for session in ("first", "second"):
                try:
                    supervisor.preserve_partial_session(session)
                except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
                    supervisor.failure(error)
            supervisor.save()
            supervisor.close()
        if sshd is not None:
            fixture.stop_owned(sshd, sshd_pidfd)
        for name in ("client-key", "host-key"):
            path = fixture_root / name
            if path.is_file() and not path.is_symlink():
                path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disposable-container", action="store_true")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--fixture-root", type=Path, default=Path("/run/eidolon-i6"))
    parser.add_argument("--reviewed-package", type=Path, default=Path("/opt/i6/reviewed-package.json"))
    parser.add_argument("--runner", type=Path, default=Path("/opt/i6/eidolon-i6-host"))
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args(argv)
    from linux_real_app_check import private_artifact_root
    os.umask(0o077)
    artifacts = private_artifact_root(args.artifact_root)
    source = evidence.source_snapshot(ROOT)
    report = {"schema": 1, "revision": "eidolon-i6-n07-b4-collection-v1",
              "source": source, "status": "not_run", "reason": "I6_NOT_RUN",
              "reviewed_golden": False, "menu_identity_verified": False,
              "verified_n07_assertions": [], "host_v3_acceptance_passed": False}
    old = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        prepare_and_run(args, artifacts, source, report)
    except KeyboardInterrupt:
        report.update(status="cancelled", reason="I6_OPERATOR_CANCELLED")
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        report.update(status="fail", reason=fixed_reason(error))
    finally:
        signal.signal(signal.SIGTERM, old)
    report["exit_code"] = 0 if report["status"] == "collected" else 2
    evidence.write_report(artifacts / "n07-b4-validation.json", report)
    print(json.dumps({k: report[k] for k in ("status", "reason", "verified_n07_assertions",
                                          "host_v3_acceptance_passed")}, sort_keys=True))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
