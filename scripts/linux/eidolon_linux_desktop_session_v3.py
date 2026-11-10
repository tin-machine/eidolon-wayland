"""C2 v3 wire/session entrypoint loaded only from B2's verified immutable snapshot."""
from __future__ import annotations

import os
import select
import stat
import sys
import time

import linux_desktop_v3_contract as C
import linux_desktop_v3_wire as W
import linux_desktop_v3_bundle as bundle_api
import linux_real_app_inspect as inspect_api
from eidolon_linux_process_owner import ProcessOwner, OwnershipError, identity as process_identity
from eidolon_linux_persistent_profile import PersistentProfileRepository, ProfileError
import eidolon_linux_desktop_v3 as core


def _event(binding, sequence, request_sequence, kind, payload):
    return C.canonical_json({
        "schema": 1,
        "protocol_revision": C.REVISION,
        "context": binding.context.record(),
        "scope": binding.context.scope(),
        "sequence": str(sequence),
        "request_sequence": str(request_sequence),
        "event": {"type": kind, "payload": payload},
    })


def _write_bounded(fd, raw, timeout=2.0):
    """Never let protocol backpressure pin the remote owner indefinitely."""
    core.require(type(fd) is int and fd >= 0 and type(raw) in (bytes, bytearray) and
                 0 < len(raw) <= W.MAX_FRAME_BYTES + 1 and 0 < timeout <= 2.0,
                 "APP_STATE_INVALID", "protocol")
    was_blocking = os.get_blocking(fd)
    deadline = time.monotonic() + timeout
    view = memoryview(raw)
    try:
        if was_blocking:
            os.set_blocking(fd, False)
        while view:
            try:
                written = os.write(fd, view)
            except BlockingIOError:
                written = 0
            if written:
                view = view[written:]
                continue
            remaining = deadline - time.monotonic()
            core.require(remaining > 0, "APP_STATE_INVALID", "protocol")
            _, writable, _ = select.select([], [fd], [], min(.1, remaining))
            core.require(writable or time.monotonic() < deadline,
                         "APP_STATE_INVALID", "protocol")
    finally:
        if was_blocking:
            os.set_blocking(fd, True)


class Emitter:
    def __init__(self, binding):
        self.binding = binding
        self.evidence = None
        self.sequence = 0

    def set_evidence(self, evidence):
        core.require(self.evidence is None and type(evidence) is W.ExpectedEvidence,
                     "APP_STATE_INVALID", "protocol")
        self.evidence = evidence

    def send(self, request_sequence, kind, payload):
        self.sequence += 1
        raw = _event(self.binding, self.sequence, request_sequence, kind, payload)
        W.parse_event(raw, self.binding, self.evidence)
        _write_bounded(sys.stdout.fileno(), raw + b"\n")


def _public(error):
    if isinstance(error, core.HelperError):
        return error.public_reason, error.failure_reason
    if isinstance(error, ProfileError):
        return str(error), "process_ownership"
    if isinstance(error, bundle_api.BundleError):
        token = str(error)
        public = {item.value for item in C.Reason}
        return (token if token in public else "APP_DEPENDENCY_CHANGED"), "tool_inspection"
    if isinstance(error, inspect_api.InspectionError):
        token = str(error)
        public = {item.value for item in C.Reason}
        return (token if token in public else "APP_STATE_INVALID"), "tool_inspection"
    return "APP_STATE_INVALID", "protocol"


def _stop_reason(reason):
    return {"stop": "stop", "lease": "lease_expired",
            "eof": "input_eof", "process": "process_exited"}[reason]


def _cleanup(binding, source_hashes, origin_boot, request_sequence, emitter, owner, profile, drain,
             paths, fds, terminal_reason, failure=None):
    process_complete = False
    pipes_eof = False
    try:
        if owner is not None and profile is not None:
            report, _ = core.cleanup_owner(owner, drain, profile)
            process_complete = report["complete"] is True
            pipes_eof = True
        if profile is not None and not profile.closed:
            raise core.HelperError("APP_STATE_RECOVERY_REQUIRED", "process_ownership")
        if fds is not None:
            home_fd, state_fd = core._remove_session(paths, fds)
            value = core.receipt(binding, source_hashes, "-", origin_boot, core.boot_id(),
                                 "receipt", "complete", True, True, True, True)
            core.write_final_receipt(state_fd, binding.context.record()["nonce"], value,
                                     binding, source_hashes, "-")
            os.close(state_fd)
            os.close(home_fd)
            fds = None
        if failure is None:
            emitter.send(request_sequence, "stopped", {
                "reason": _stop_reason(terminal_reason),
                "process_complete": True,
                "pipes_eof": True,
                "remote_cleaned": True,
                "receipt_written": True,
            })
            return 0
    except BaseException as cleanup_error:
        if failure is None:
            failure = cleanup_error
    public, reason = _public(failure)
    emitter.send(request_sequence, "pending", {
        "reason": reason,
        "public_reason": public,
        "process_complete": bool(process_complete),
        "pipes_eof": bool(pipes_eof),
    })
    return 1


def _before_prepared_reclaim(binding, source_hashes, origin_boot, owner, profile, drain, paths, fds):
    if profile is None or paths is None or fds is None:
        return
    try:
        if drain is None:
            drain = core.Drain()
        core.cleanup_owner(owner, drain, profile)
        home_fd, state_fd = core._remove_session(paths, fds)
        value = core.receipt(binding, source_hashes, "-", origin_boot, core.boot_id(),
                             "receipt", "complete", True, True, True, True)
        core.write_final_receipt(state_fd, binding.context.record()["nonce"], value,
                                 binding, source_hashes, "-")
        os.close(state_fd)
        os.close(home_fd)
    except BaseException:
        pass


def _start_demo(owner, profile, drain, env, paths, verified_bundle, context):
    try:
        forwarded = paths.wayland_socket.lstat()
    except FileNotFoundError:
        raise core.HelperError("APP_STATE_INVALID", "start") from None
    core.require(stat.S_ISSOCK(forwarded.st_mode) and forwarded.st_uid == os.getuid(),
                 "APP_STATE_INVALID", "start")
    waypipe = paths.home / ".local/lib/eidolon-linux/waypipe"
    command = [str(waypipe), *core.FIXED_WAYPIPE_ARGS, "--socket", str(paths.wayland_socket),
               "--display", str(paths.runtime / "w"), "server", "--",
               str(verified_bundle.directory / "eidolon-gtk3-desktop"),
               "--case", "input", "--run-id", context.record()["nonce"] * 2]
    return core.spawn_tracked(owner, profile, drain, command, env)


def serve(bootstrap, verified_bundle, source_hashes):
    context = bootstrap.context
    pins = bootstrap.pins
    binding = W.Binding(context, pins)
    emitter = Emitter(binding)
    owner = None
    profile = None
    drain = None
    paths = None
    fds = None
    request_sequence = 0
    phase = "prepared"
    inspected = False
    started = None
    bus = None
    origin_boot = core.boot_id()
    try:
        owner = ProcessOwner()
        paths, barrier, home_fd, state_fd, session_fd = core.prepare_session(context)
        fds = (home_fd, state_fd, session_fd)
        profile = PersistentProfileRepository().acquire(
            context, process_owner=owner, barrier=barrier)
        drain = core.Drain()
        emitter.send(0, "prepared", {
            "lease_ms": W.LEASE_MS,
            "boot_id": origin_boot,
            "remote_socket": str(paths.wayland_socket),
        })
        decoder = W.FrameDecoder()
        os.set_blocking(sys.stdin.fileno(), False)
        last_input = time.monotonic()
        next_request = 1

        while True:
            now = time.monotonic()
            drain.pump(0)
            if bus is not None and bus.poll() is not None:
                return _cleanup(binding, source_hashes, origin_boot, request_sequence, emitter,
                                owner, profile, drain, paths, fds, "process",
                                core.HelperError("APP_STATE_INVALID", "start"))
            if started is not None and started.poll() is not None:
                return _cleanup(binding, source_hashes, origin_boot, request_sequence, emitter,
                                owner, profile, drain, paths, fds, "process")
            if now - last_input >= core.LEASE_SECONDS:
                return _cleanup(binding, source_hashes, origin_boot, request_sequence, emitter,
                                owner, profile, drain, paths, fds, "lease")

            try:
                chunk = os.read(sys.stdin.fileno(), 65536)
            except BlockingIOError:
                chunk = None
            if chunk == b"":
                decoder.finish()
                return _cleanup(binding, source_hashes, origin_boot, request_sequence, emitter,
                                owner, profile, drain, paths, fds, "eof")
            if chunk:
                for raw in decoder.feed(chunk):
                    command = W.parse_command(raw, binding)
                    sequence = int(command["sequence"])
                    core.require(sequence == next_request, "APP_STATE_INVALID", "protocol")
                    next_request += 1
                    request_sequence = sequence
                    last_input = time.monotonic()
                    action = command["command"]["type"]

                    if action == "heartbeat":
                        emitter.send(sequence, "heartbeat", {"phase": phase})
                        continue
                    if action == "stop":
                        return _cleanup(binding, source_hashes, origin_boot, request_sequence,
                                        emitter, owner, profile, drain, paths, fds, "stop")
                    if action == "inspect":
                        core.require(phase == "prepared" and not inspected,
                                     "CAPABILITY_STALE", "protocol")
                        bundle_api.validate_launch_package(verified_bundle)
                        recovery = inspect_api.guard_recovery_state(
                            profile.paths.root, uid=context.record()["uid"])
                        core.require(recovery.get("clear") is True,
                                     recovery.get("reason", "APP_STATE_RECOVERY_REQUIRED"),
                                     "tool_inspection")
                        core.render_bus_config(verified_bundle, paths, session_fd,
                                               context.record()["uid"])
                        env = core.launch_environment(profile, paths, verified_bundle)
                        core.run_settings_probe(owner, profile, drain, env, verified_bundle, True)
                        evidence = W.ExpectedEvidence.capture(
                            pins, verified_bundle.manifest, source_hashes)
                        emitter.set_evidence(evidence)
                        emitter.send(sequence, "catalog", W.catalog_payload(binding, evidence))
                        inspected = True
                        phase = "catalog"
                        continue
                    if action == "start":
                        core.require(phase == "catalog" and inspected and started is None,
                                     "CAPABILITY_STALE", "start")
                        selection = C.Selection.parse(
                            C.canonical_json(command["command"]["payload"]["selection"]))
                        bundle_api.validate_launch_package(verified_bundle)
                        recovery = inspect_api.guard_recovery_state(
                            profile.paths.root, uid=context.record()["uid"])
                        core.require(recovery.get("clear") is True,
                                     recovery.get("reason", "APP_STATE_RECOVERY_REQUIRED"), "start")
                        base_env = core.launch_environment(profile, paths, verified_bundle)
                        core.run_settings_probe(owner, profile, drain, base_env,
                                                verified_bundle, False)
                        recovery = inspect_api.guard_recovery_state(
                            profile.paths.root, uid=context.record()["uid"])
                        core.require(recovery.get("clear") is True,
                                     recovery.get("reason", "APP_STATE_RECOVERY_REQUIRED"), "start")
                        bus, address = core.start_private_bus(
                            owner, profile, drain, base_env, paths.bus_config, paths.bus_socket)
                        env = core.launch_environment(profile, paths, verified_bundle, address)
                        app_id = selection.record()["app_id"]
                        if app_id == C.EDITOR:
                            started = core.launch_mousepad(owner, profile, drain, env, paths)
                        else:
                            core.require(app_id == C.DEMO, "APP_STATE_INVALID", "start")
                            started = _start_demo(owner, profile, drain, env, paths,
                                                  verified_bundle, context)
                        row = process_identity(started.pid)
                        core.require(row is not None, "APP_STATE_INVALID", "start")
                        emitter.send(sequence, "started", {
                            "selection": selection.record(),
                            "mode": "gui",
                            "process_identity": {
                                "pid": str(row["pid"]),
                                "start_time_ticks": str(row["start"]),
                            },
                        })
                        phase = "started"
                        continue
                    raise core.HelperError("APP_STATE_INVALID", "protocol")
            time.sleep(.01)
    except BaseException as error:
        if profile is None and isinstance(error, ProfileError) and error.handle is not None:
            profile = error.handle
        if emitter.sequence == 0:
            _before_prepared_reclaim(binding, source_hashes, origin_boot, owner,
                                     profile, drain, paths, fds)
            return 1
        if drain is None:
            drain = core.Drain()
        return _cleanup(binding, source_hashes, origin_boot, request_sequence, emitter,
                        owner, profile, drain, paths, fds, "stop", error)
    finally:
        if drain is not None:
            drain.close()


def main(argv, verified_bundle=None, source_hashes=None):
    os.umask(0o077)
    try:
        bootstrap = C.Bootstrap.parse(C.canonical_json(argv))
        core.require(type(verified_bundle) is bundle_api.VerifiedBundle and
                     type(source_hashes) is dict, "APP_DEPENDENCY_CHANGED", "protocol")
        binding = W.Binding(bootstrap.context, bootstrap.pins)
        if bootstrap.operation == "recover":
            raw, status = core.recovery_receipt(
                binding, source_hashes, bootstrap.expected_boot_id)
            _write_bounded(sys.stdout.fileno(), raw)
            return status
        core.require(bootstrap.operation == "serve")
        return serve(bootstrap, verified_bundle, source_hashes)
    except (C.ContractError, bundle_api.BundleError, core.HelperError, ProfileError,
            OwnershipError, inspect_api.InspectionError, OSError, ValueError,
            KeyError, TypeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(1)
