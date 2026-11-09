"""B2's single v3 wire contract; C2 owns live processes and receipt creation.

All parsing is pure. Evidence equality is not installation or launch authority:
the SSH owner must first verify the complete compiled source closure and the
immutable bundle. There is no version negotiation or v2 success fallback here.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

import linux_desktop_v3_contract as C

MAX_FRAME_BYTES = C.MAX_RECORD_BYTES
MAX_CHUNK_BYTES = 65536
MAX_QUEUED_FRAMES = 16
LEASE_MS = 15000
COMMAND_FIELDS = ("schema", "protocol_revision", "context", "scope", "sequence", "command")
EVENT_FIELDS = ("schema", "protocol_revision", "context", "scope", "sequence", "request_sequence", "event")
RECEIPT_FIELDS = ("schema", "protocol_revision", "context", "pins", "build_id", "source_hashes",
                  "expected_boot_id", "origin_boot_id", "boot_id", "status", "process_evidence",
                  "process_complete", "pipes_eof", "remote_cleaned", "receipt_written")


def _require(condition, reason="DESKTOP_V3_PROTOCOL_INVALID"):
    if not condition:
        raise C.ContractError(reason)


def _equal(a, b):
    return C.canonical_json(a) == C.canonical_json(b)


def _fields(v, names):
    _require(type(v) is dict and set(v) == set(names))


def valid_boot(value):
    return C.valid_boot(value) and value not in ("-", "00000000-0000-0000-0000-000000000000")


def valid_sources(value):
    """Validate shape only. The builder/compiled inventory owns the exact set."""
    return (type(value) is dict and 1 <= len(value) <= 64 and all(
        type(name) is str and 1 <= len(name) <= 128 and not name.startswith("/") and
        re.fullmatch(r"[A-Za-z0-9_./-]+", name) is not None and
        all(part not in ("", ".", "..") for part in name.split("/")) and C.hexadecimal(sha, 64)
        for name, sha in value.items()))


def valid_socket(value, nonce):
    tail = "/.eidolon-desktop-v3/s-" + nonce + "/p"
    return (type(value) is str and value.endswith(tail) and len(value.encode()) <= 103 and
            len(value[:-len(tail)]) > 1 and value.startswith("/") and
            not value.startswith("/data/data/com.termux/") and
            not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value) and
            all(part not in ("", ".", "..") for part in value.split("/")[1:]))


@dataclass(frozen=True, repr=False)
class Binding:
    context: C.Context
    pins: C.Pins

    def __post_init__(self):
        _require(type(self.context) is C.Context and type(self.pins) is C.Pins)


@dataclass(frozen=True, repr=False)
class ExpectedEvidence:
    """Expected immutable inputs; constructing this is not source admission."""
    _bytes: bytes

    def __post_init__(self):
        value = C.parse_json(self._bytes)
        _fields(value, ("pins", "app_manifest", "source_hashes", "waypipe_version",
                        "demo_verified", "package_verified", "settings_backend_verified"))
        pins = C.Pins.parse(C.canonical_json(value["pins"]))
        C.Manifest.parse(C.canonical_json(value["app_manifest"])).matches_pins(pins)
        _require(valid_sources(value["source_hashes"]) and value["waypipe_version"] == "0.11.3" and
                 all(value[k] is True for k in ("demo_verified", "package_verified", "settings_backend_verified")))
        object.__setattr__(self, "_bytes", C.canonical_json(value))

    @classmethod
    def capture(cls, pins, manifest, source_hashes):
        _require(type(pins) is C.Pins and type(manifest) is C.Manifest)
        manifest.matches_pins(pins)
        return cls(C.canonical_json({"pins": pins.record(), "app_manifest": manifest.record(),
            "source_hashes": source_hashes, "waypipe_version": "0.11.3", "demo_verified": True,
            "package_verified": True, "settings_backend_verified": True}))

    def record(self):
        return C.parse_json(self._bytes)

    def matches_binding(self, binding):
        _require(type(binding) is Binding and _equal(self.record()["pins"], binding.pins.record()),
                 "DESKTOP_V3_EVIDENCE_STALE")


def catalog_payload(binding, evidence):
    _require(type(binding) is Binding and type(evidence) is ExpectedEvidence)
    evidence.matches_binding(binding)
    return {"descriptor": C.catalog_descriptor(binding.context, binding.pins), "evidence": evidence.record()}


def _envelope(value, binding, fields):
    _fields(value, fields)
    _require(type(binding) is Binding)
    _require(type(value["schema"]) is int and value["schema"] == 1)
    C.DecoderVersion.selected(binding.pins, value["protocol_revision"])
    context = C.Context.parse(C.canonical_json(value["context"]))
    _require(context == binding.context and value["scope"] == binding.context.scope(), "DESKTOP_V3_PROTOCOL_SCOPE")
    _require(C.decimal(value["sequence"]), "DESKTOP_V3_PROTOCOL_SCOPE")


def parse_command(raw, binding):
    value = C.parse_json(raw)
    _envelope(value, binding, COMMAND_FIELDS)
    command = value["command"]
    _fields(command, ("type", "payload"))
    action, payload = command["type"], command["payload"]
    _require(type(action) is str)
    if action == "start":
        _fields(payload, ("selection",))
        C.Selection.parse(C.canonical_json(payload["selection"])).matches(binding.context, binding.pins)
    else:
        _require(action in ("inspect", "heartbeat", "stop"))
        _fields(payload, ())
    return value


def parse_event(raw, binding, evidence):
    value = C.parse_json(raw)
    _envelope(value, binding, EVENT_FIELDS)
    request = value["request_sequence"]
    _require(request == "0" or C.decimal(request), "DESKTOP_V3_PROTOCOL_SCOPE")
    event = value["event"]
    _fields(event, ("type", "payload"))
    kind, payload = event["type"], event["payload"]
    _require(type(kind) is str)
    if kind == "prepared":
        _fields(payload, ("lease_ms", "boot_id", "remote_socket"))
        _require(type(payload["lease_ms"]) is int and payload["lease_ms"] == LEASE_MS and
                 valid_boot(payload["boot_id"]) and valid_socket(payload["remote_socket"], binding.context.record()["nonce"]))
    elif kind == "catalog":
        _fields(payload, ("descriptor", "evidence"))
        C.validate_catalog(C.canonical_json(payload["descriptor"]), binding.context, binding.pins)
        _require(type(evidence) is ExpectedEvidence)
        evidence.matches_binding(binding)
        _require(_equal(payload["evidence"], evidence.record()), "DESKTOP_V3_EVIDENCE_STALE")
    elif kind == "started":
        _fields(payload, ("selection", "mode", "process_identity"))
        C.Selection.parse(C.canonical_json(payload["selection"])).matches(binding.context, binding.pins)
        _require(payload["mode"] == "gui")
        identity = payload["process_identity"]
        _fields(identity, ("pid", "start_time_ticks"))
        _require(C.decimal(identity["pid"], (1 << 31) - 1) and C.decimal(identity["start_time_ticks"]))
    elif kind == "heartbeat":
        _fields(payload, ("phase",))
        _require(payload["phase"] in ("prepared", "catalog", "started"))
    elif kind == "stopped":
        _fields(payload, ("reason", "process_complete", "pipes_eof", "remote_cleaned", "receipt_written"))
        C.parse_reason(C.canonical_json(payload["reason"]), "stop")
        _require(all(payload[k] is True for k in ("process_complete", "pipes_eof", "remote_cleaned", "receipt_written")))
    elif kind == "pending":
        _fields(payload, ("reason", "public_reason", "process_complete", "pipes_eof"))
        C.parse_reason(C.canonical_json(payload["reason"]), "failure")
        _require(C.parse_reason(C.canonical_json(payload["public_reason"])).value != "NONE")
        _require(type(payload["process_complete"]) is bool and type(payload["pipes_eof"]) is bool)
    else:
        raise C.ContractError("DESKTOP_V3_PROTOCOL_INVALID")
    return value


class Protocol:
    """One lease, one in-flight request, no automatic Start retry."""
    def __init__(self, binding, evidence):
        _require(type(binding) is Binding and type(evidence) is ExpectedEvidence)
        evidence.matches_binding(binding)
        self.binding, self.evidence = binding, evidence
        self.phase = "awaiting_prepared"
        self.event_sequence = self.request_sequence = self.acknowledged_sequence = 0
        self.in_flight = None
        self.started_selection = None

    def selection(self, app_id):
        _require(self.phase == "catalog" and self.in_flight is None, "DESKTOP_V3_COMMAND_INVALID")
        value = C.Selection.parse(C.canonical_json({"scope": self.binding.context.scope(),
            "revision": C.catalog_revision(self.binding.context, self.binding.pins), "app_id": app_id}))
        return value

    def command(self, action, selection=None):
        _require(self.in_flight is None and type(action) is str, "DESKTOP_V3_COMMAND_INVALID")
        valid = (action == "inspect" and self.phase == "prepared" and selection is None or
                 action == "start" and self.phase == "catalog" and type(selection) is C.Selection or
                 action in ("heartbeat", "stop") and self.phase in ("prepared", "catalog", "started") and selection is None)
        _require(valid, "DESKTOP_V3_COMMAND_INVALID")
        if selection is not None:
            selection.matches(self.binding.context, self.binding.pins)
        next_sequence = self.request_sequence + 1
        _require(next_sequence <= C.MAX_I64, "DESKTOP_V3_SEQUENCE_EXHAUSTED")
        value = {"schema": 1, "protocol_revision": C.REVISION, "context": self.binding.context.record(),
            "scope": self.binding.context.scope(), "sequence": str(next_sequence),
            "command": {"type": action, "payload": {"selection": selection.record()} if selection else {}}}
        raw = C.canonical_json(value)
        parse_command(raw, self.binding)
        self.request_sequence, self.in_flight = next_sequence, action
        if action == "start":
            self.started_selection = selection
        return raw + b"\n"

    def accept(self, raw):
        try:
            return self._accept(raw)
        except C.ContractError:
            self.phase, self.in_flight = "failed", None
            raise

    def _accept(self, raw):
        _require(self.phase not in ("stopped", "pending", "failed"))
        value = parse_event(raw, self.binding, self.evidence)
        _require(int(value["sequence"]) == self.event_sequence + 1, "DESKTOP_V3_PROTOCOL_SCOPE")
        request = int(value["request_sequence"])
        kind, payload = value["event"]["type"], value["event"]["payload"]
        asynchronous = kind == "pending" or kind == "stopped" and payload["reason"] != "stop"
        _require((self.acknowledged_sequence <= request <= self.request_sequence) if asynchronous else
                 request == self.request_sequence, "DESKTOP_V3_PROTOCOL_SCOPE")
        next_phase = None
        if kind == "prepared" and self.phase == "awaiting_prepared" and self.in_flight is None:
            next_phase = "prepared"
        elif kind == "catalog" and self.phase == "prepared" and self.in_flight == "inspect":
            next_phase = "catalog"
        elif kind == "started" and self.phase == "catalog" and self.in_flight == "start":
            _require(self.started_selection is not None and _equal(payload["selection"], self.started_selection.record()),
                     "DESKTOP_V3_PROTOCOL_SCOPE")
            next_phase = "started"
        elif kind == "heartbeat" and self.in_flight == "heartbeat" and payload["phase"] == self.phase:
            next_phase = self.phase
        elif kind == "stopped" and self.phase != "awaiting_prepared":
            reason = payload["reason"]
            _require(reason != "stop" or self.in_flight == "stop", "DESKTOP_V3_PROTOCOL_TRANSITION")
            _require(reason != "process_exited" or self.phase == "started" or
                     self.in_flight == "start" and request == self.request_sequence,
                     "DESKTOP_V3_PROTOCOL_TRANSITION")
            next_phase = "stopped"
        elif kind == "pending":
            next_phase = "pending"
        _require(next_phase is not None, "DESKTOP_V3_PROTOCOL_TRANSITION")
        self.phase, self.in_flight = next_phase, None
        self.event_sequence += 1
        self.acknowledged_sequence = request
        return value["event"]


class FrameDecoder:
    """LF-only framing, finite input/queue, no lossy UTF-8 or CR tolerance."""
    def __init__(self):
        self.buffer = bytearray()
        self.failed = False

    def feed(self, chunk):
        try:
            _require(not self.failed and type(chunk) is bytes and len(chunk) <= MAX_CHUNK_BYTES)
            frames = []
            for byte in chunk:
                _require(byte != 13)
                if byte == 10:
                    _require(0 < len(self.buffer) <= MAX_FRAME_BYTES and len(frames) < MAX_QUEUED_FRAMES)
                    frames.append(bytes(self.buffer))
                    self.buffer.clear()
                else:
                    _require(len(self.buffer) < MAX_FRAME_BYTES)
                    self.buffer.append(byte)
            return frames
        except C.ContractError:
            self.failed = True
            self.buffer.clear()
            raise

    def finish(self):
        _require(not self.failed and not self.buffer)


def parse_receipt(raw, binding, source_hashes, expected_boot, exit_status):
    _require(type(raw) is bytes and raw.endswith(b"\n") and b"\n" not in raw[:-1] and b"\r" not in raw and
             len(raw) <= MAX_FRAME_BYTES, "DESKTOP_V3_RECOVERY_INVALID")
    value = C.parse_json(raw[:-1])
    _fields(value, RECEIPT_FIELDS)
    _require(type(binding) is Binding and valid_sources(source_hashes))
    _require(type(value["schema"]) is int and value["schema"] == 1 and value["protocol_revision"] == C.REVISION and
             _equal(value["context"], binding.context.record()) and _equal(value["pins"], binding.pins.record()) and
             value["build_id"] == binding.pins.build_id() and _equal(value["source_hashes"], source_hashes) and
             value["expected_boot_id"] == expected_boot and (expected_boot == "-" or valid_boot(expected_boot)) and
             valid_boot(value["boot_id"]), "DESKTOP_V3_RECOVERY_SCOPE")
    origin = value["origin_boot_id"]
    _require(origin is None or valid_boot(origin) and (expected_boot == "-" or origin == expected_boot),
             "DESKTOP_V3_RECOVERY_SCOPE")
    _require(type(exit_status) is int and (value["status"], exit_status) in (("complete", 0), ("pending", 1)),
             "DESKTOP_V3_RECOVERY_SCOPE")
    flags = ("process_complete", "pipes_eof", "remote_cleaned", "receipt_written")
    _require(all(type(value[k]) is bool for k in flags), "DESKTOP_V3_RECOVERY_INVALID")
    proof = value["process_evidence"]
    if proof == "receipt":
        valid = origin is not None and value["process_complete"] and value["pipes_eof"] and value["receipt_written"]
    elif proof == "boot_changed":
        valid = origin is not None and origin != value["boot_id"] and value["process_complete"] and not value["pipes_eof"]
    elif proof == "unknown":
        valid = not any(value[k] for k in flags)
    else:
        valid = False
    _require(valid and (value["status"] != "complete" or
             value["process_complete"] and value["remote_cleaned"] and value["receipt_written"]),
             "DESKTOP_V3_RECOVERY_INVALID")
    return value
