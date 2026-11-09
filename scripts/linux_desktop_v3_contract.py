"""B1: pure v3 values, never a helper, builder, dispatcher or admission gate.

All hashes in the shared fixture are synthetic. Parsing this contract proves
neither installed source closure nor application/SDK/Quest acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re

from linux_real_app_manifest import validate_manifest

REVISION = "eidolon-linux-desktop-v3"
MAX_RECORD_BYTES = 16384
MAX_PINS_BYTES = 4096
MAX_DEPTH = 32
MAX_I64 = (1 << 63) - 1
MAX_UID = (1 << 32) - 1
DEMO = "gtk3-desktop"
EDITOR = "mousepad-editor-v1"
PIN_HASH_FIELDS = ("base_bundle_sha256", "desktop_bundle_sha256", "waypipe_sha256",
                   "shm_fixture_sha256", "desktop_binary_sha256", "app_manifest_sha256")
PIN_FIELDS = frozenset(("schema", "backend", "protocol_revision", *PIN_HASH_FIELDS))
CONTEXT_FIELDS = frozenset(("schema", "backend", "protocol_revision", "session", "profile_revision",
                            "profile_binding", "nonce", "uid", "installation_id", "profile_storage_id"))
STORAGE_FIELDS = frozenset(("schema", "legacy_installation", "installation_id", "profile_id",
                            "profile_binding", "profile_storage_id", "host_user_binding"))
CAPTURE_FIELDS = frozenset(("legacy_installation", "profile_id", "profile_binding", "host_user_binding"))
OWNER_FIELDS = frozenset(("schema", "uid", "installation_id", "profile_storage_id", "app_id", "state_policy"))


class ContractError(ValueError):
    """Only a fixed code; no private input in diagnostics."""


def require(condition, reason="CONTRACT_INVALID"):
    if not condition:
        raise ContractError(reason)


def hexadecimal(value, size):
    return type(value) is str and len(value) == size and all(c in "0123456789abcdef" for c in value)


def decimal(value, maximum=MAX_I64):
    return (type(value) is str and 1 <= len(value) <= 19 and value[0] != "0" and
            all("0" <= c <= "9" for c in value) and int(value) <= maximum)


def integer(value, minimum=0, maximum=MAX_I64):
    return type(value) is int and minimum <= value <= maximum


def fields(value, expected):
    require(type(value) is dict and set(value) == set(expected))


def _json_domain(value, depth=0):
    require(depth <= MAX_DEPTH)
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        require(-MAX_I64 - 1 <= value <= MAX_I64)
    elif type(value) is str:
        require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value))
    elif type(value) is list:
        for item in value:
            _json_domain(item, depth + 1)
    elif type(value) is dict:
        for key, item in value.items():
            require(type(key) is str)
            _json_domain(key, depth + 1)
            _json_domain(item, depth + 1)
    else:
        raise ContractError("CONTRACT_INVALID")


def canonical_json(value):
    """ASCII-escaped JSON, UTF-8, recursively sorted keys, no trailing LF.

No floats or lone surrogates. This explicitly preserves A/A0's ensure_ascii
choice, including the Japanese fixed demo label (it is not RFC 8785/JCS).
"""
    _json_domain(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _parse_int(token):
    require(token != "-0")
    return int(token)


def parse_json(raw, limit=MAX_RECORD_BYTES):
    require(type(raw) is bytes and 0 < len(raw) <= limit)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_int=_parse_int,
                           parse_constant=lambda _: require(False))
        _json_domain(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ContractError("CONTRACT_INVALID") from error


@dataclass(frozen=True, repr=False)
class _Record:
    _bytes: bytes

    def __post_init__(self):
        # The constructor is checked too; nominal types cannot wrap forged bytes.
        value = type(self)._validate(self._bytes)
        object.__setattr__(self, "_bytes", canonical_json(value))

    @classmethod
    def parse(cls, raw):
        return cls(raw)

    def record(self):
        return json.loads(self._bytes)

    def canonical(self):
        return self._bytes

    def sha256(self):
        return hashlib.sha256(self._bytes).hexdigest()

    def __repr__(self):
        return type(self).__name__ + "(<private>)"


class Pins(_Record):
    @classmethod
    def _validate(cls, raw):
        v = parse_json(raw, MAX_PINS_BYTES)
        fields(v, PIN_FIELDS)
        require(type(v["schema"]) is int and v["schema"] == 2 and v["backend"] == "linux_desktop" and
                v["protocol_revision"] == REVISION and all(hexadecimal(v[k], 64) for k in PIN_HASH_FIELDS))
        return v

    def build_input(self):
        v = self.record()
        return {k: v[k] for k in ("protocol_revision", "base_bundle_sha256", "desktop_bundle_sha256", "app_manifest_sha256")}

    def build_id(self):
        return digest(self.build_input())


class Manifest(_Record):
    @classmethod
    def _validate(cls, raw):
        try:
            v = parse_json(raw)
            validate_manifest(v)
        except ValueError as error:
            raise ContractError("APP_PACKAGE_UNSUPPORTED") from error
        return v

    def matches_pins(self, pins):
        require(type(pins) is Pins)
        require(self.sha256() == pins.record()["app_manifest_sha256"], "APP_DEPENDENCY_CHANGED")


@dataclass(frozen=True, repr=False)
class StorageInstallationId:
    value: str

    def __post_init__(self):
        require(hexadecimal(self.value, 32))


@dataclass(frozen=True, repr=False)
class ProfileStorageId:
    value: str

    def __post_init__(self):
        require(hexadecimal(self.value, 32))


class Context(_Record):
    @classmethod
    def _validate(cls, raw):
        v = parse_json(raw)
        fields(v, CONTEXT_FIELDS)
        require(type(v["schema"]) is int and v["schema"] == 1 and v["backend"] == "linux_desktop" and
                v["protocol_revision"] == REVISION and decimal(v["session"]) and decimal(v["profile_revision"]) and
                hexadecimal(v["profile_binding"], 64) and hexadecimal(v["nonce"], 16) and
                integer(v["uid"], 1, MAX_UID))
        StorageInstallationId(v["installation_id"])
        ProfileStorageId(v["profile_storage_id"])
        return v

    def scope(self):
        return self.sha256()


def host_user_input(host_key_sha256, username):
    require(hexadecimal(host_key_sha256, 64) and type(username) is str and
            re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}", username) is not None)
    return {"domain": "eidolon-linux-storage-host-user-v1", "host_key_sha256": host_key_sha256, "username": username}


def host_user_binding(host_key_sha256, username):
    """Key hash is SHA256(UTF8(canonical existing ConnectionProfileV2 host_key))."""
    return digest(host_user_input(host_key_sha256, username))


class StorageCapture(_Record):
    @classmethod
    def _validate(cls, raw):
        v = parse_json(raw)
        fields(v, CAPTURE_FIELDS)
        require(hexadecimal(v["legacy_installation"], 32) and v["profile_id"] == "remote-linux" and
                hexadecimal(v["profile_binding"], 64) and hexadecimal(v["host_user_binding"], 64))
        return v


class StorageRecord(_Record):
    @classmethod
    def _validate(cls, raw):
        v = parse_json(raw)
        fields(v, STORAGE_FIELDS)
        require(type(v["schema"]) is int and v["schema"] == 1)
        StorageCapture.parse(canonical_json({k: v[k] for k in CAPTURE_FIELDS}))
        StorageInstallationId(v["installation_id"])
        ProfileStorageId(v["profile_storage_id"])
        return v

    def matches_capture(self, capture):
        require(type(capture) is StorageCapture)
        v = self.record()
        require(all(v[k] == item for k, item in capture.record().items()), "STORAGE_STALE")

    def matches_context(self, context):
        require(type(context) is Context)
        v, c = self.record(), context.record()
        require(all(v[k] == c[k] for k in ("profile_binding", "installation_id", "profile_storage_id")), "STORAGE_STALE")


def validate_storage_transition(old, new, *, expected_old_sha256, capture, explicit_save, fully_reclaimed):
    """Pure comparison only; no ID creation, CAS, write, migration or cleanup."""
    require(type(old) is StorageRecord and type(new) is StorageRecord and type(capture) is StorageCapture)
    require(explicit_save is True and fully_reclaimed is True, "RECOVERY_REQUIRED")
    require(hexadecimal(expected_old_sha256, 64) and old.sha256() == expected_old_sha256, "STORAGE_STALE")
    new.matches_capture(capture)
    a, b = old.record(), new.record()
    require(all(a[k] == b[k] for k in ("legacy_installation", "installation_id", "profile_id")), "STORAGE_STALE")
    retained = a["host_user_binding"] == b["host_user_binding"]
    require((a["profile_storage_id"] == b["profile_storage_id"]) == retained, "STORAGE_STALE")
    return "retain" if retained else "replace"


class StorageOwner(_Record):
    @classmethod
    def _validate(cls, raw):
        v = parse_json(raw)
        fields(v, OWNER_FIELDS)
        require(type(v["schema"]) is int and v["schema"] == 1 and integer(v["uid"], 1, MAX_UID) and
                v["app_id"] == EDITOR and v["state_policy"] == "persistent-keyfile-locked-v1")
        StorageInstallationId(v["installation_id"])
        ProfileStorageId(v["profile_storage_id"])
        return v

    def matches_context(self, context):
        require(type(context) is Context)
        v, c = self.record(), context.record()
        require(all(v[k] == c[k] for k in ("uid", "installation_id", "profile_storage_id")), "APP_STATE_INVALID")


def catalog_entries():
    # Descriptor only: availability/source attestation is B2/C, never inferred.
    return [{"id": DEMO, "label": "Linux アプリ", "mode": "gui", "icon": "builtin:script"},
            {"id": EDITOR, "label": "Mousepad", "mode": "gui", "icon": "builtin:editor"}]


def catalog_revision_input(context, pins):
    require(type(context) is Context and type(pins) is Pins)
    return {"protocol_revision": REVISION, "scope": context.scope(), "pins": pins.record(), "entries": catalog_entries()}


def catalog_revision(context, pins):
    return digest(catalog_revision_input(context, pins))


def catalog_descriptor(context, pins):
    return {"schema": 1, "namespace": "linux-desktop", "protocol_revision": REVISION,
            "scope": context.scope(), "revision": catalog_revision(context, pins), "entries": catalog_entries(),
            "arbitrary_exec": False, "terminal": False, "instance_limit": 1}


def validate_catalog(raw, context, pins):
    value = parse_json(raw)
    # Comparing canonical bytes also rejects bool-vs-int equality in Python.
    require(canonical_json(value) == canonical_json(catalog_descriptor(context, pins)), "CAPABILITY_STALE")
    return value


class Selection(_Record):
    @classmethod
    def _validate(cls, raw):
        v = parse_json(raw)
        fields(v, ("scope", "revision", "app_id"))
        require(hexadecimal(v["scope"], 64) and hexadecimal(v["revision"], 64) and v["app_id"] in (DEMO, EDITOR))
        return v

    def matches(self, context, pins):
        require(type(context) is Context and type(pins) is Pins)
        v = self.record()
        require(v["scope"] == context.scope() and v["revision"] == catalog_revision(context, pins), "CAPABILITY_STALE")


class DecoderVersion(Enum):
    V3 = REVISION

    @classmethod
    def selected(cls, pins, remote_revision):
        require(type(pins) is Pins and remote_revision == REVISION, "PROTOCOL_VERSION")
        return cls.V3


class Reason(str, Enum):
    NONE = "NONE"
    APP_NOT_INSTALLED = "APP_NOT_INSTALLED"
    APP_PACKAGE_UNSUPPORTED = "APP_PACKAGE_UNSUPPORTED"
    APP_DEPENDENCY_CHANGED = "APP_DEPENDENCY_CHANGED"
    APP_BACKEND_UNSUPPORTED = "APP_BACKEND_UNSUPPORTED"
    APP_SETTINGS_UNVERIFIED = "APP_SETTINGS_UNVERIFIED"
    APP_ORIGIN_UNKNOWN = "APP_ORIGIN_UNKNOWN"
    APP_WINDOW_UNSUPPORTED = "APP_WINDOW_UNSUPPORTED"
    APP_STATE_BUSY = "APP_STATE_BUSY"
    APP_STATE_INVALID = "APP_STATE_INVALID"
    APP_STATE_RECOVERY_REQUIRED = "APP_STATE_RECOVERY_REQUIRED"
    APP_LAUNCH_TIMEOUT = "APP_LAUNCH_TIMEOUT"
    APP_PRESENTATION_TIMEOUT = "APP_PRESENTATION_TIMEOUT"
    APP_PRESENTATION_FAILED = "APP_PRESENTATION_FAILED"
    APP_STARTUP_CANCELLED = "APP_STARTUP_CANCELLED"
    APP_OUTPUT_RATE_LIMIT = "APP_OUTPUT_RATE_LIMIT"
    APP_V3_NOT_IMPLEMENTED = "APP_V3_NOT_IMPLEMENTED"
    STATE_LIMIT = "STATE_LIMIT"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    CAPABILITY_STALE = "CAPABILITY_STALE"


class StopReason(str, Enum):
    STOP = "stop"
    LEASE_EXPIRED = "lease_expired"
    INPUT_EOF = "input_eof"
    PROCESS_EXITED = "process_exited"


class FailureReason(str, Enum):
    PROCESS_OWNERSHIP = "process_ownership"
    PIPE_EOF = "pipe_eof"
    DIRECTORY_CLEANUP = "directory_cleanup"
    RECEIPT_WRITE = "receipt_write"
    PROTOCOL = "protocol"
    TOOL_INSPECTION = "tool_inspection"
    START = "start"


def parse_reason(raw, kind="public"):
    enum = {"public": Reason, "stop": StopReason, "failure": FailureReason}.get(kind)
    value = parse_json(raw)
    require(enum is not None and type(value) is str, "PROTOCOL_REASON")
    try:
        return enum(value)
    except ValueError as error:
        raise ContractError("PROTOCOL_REASON") from error


def valid_boot(value):
    return type(value) is str and (value == "-" or re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value) is not None)


@dataclass(frozen=True, repr=False)
class Bootstrap:
    operation: str
    context: Context
    pins: Pins
    expected_boot_id: str | None = None

    def __post_init__(self):
        require(self.operation in ("serve", "recover") and type(self.context) is Context and type(self.pins) is Pins)
        require((self.operation == "serve" and self.expected_boot_id is None) or
                (self.operation == "recover" and valid_boot(self.expected_boot_id)))

    def vector(self):
        c, p = self.context.record(), self.pins.record()
        result = [self.operation, c["session"], c["profile_revision"], c["profile_binding"], c["nonce"], str(c["uid"]),
                  *(p[k] for k in PIN_HASH_FIELDS), c["installation_id"], c["profile_storage_id"]]
        if self.operation == "recover":
            result.append(self.expected_boot_id)
        return tuple(result)

    @classmethod
    def parse(cls, raw):
        a = parse_json(raw)
        require(type(a) is list and len(a) in (14, 15) and all(type(x) is str for x in a))
        require((a[0] == "serve" and len(a) == 14) or (a[0] == "recover" and len(a) == 15))
        require(decimal(a[5], MAX_UID))
        context = Context.parse(canonical_json({"schema": 1, "backend": "linux_desktop", "protocol_revision": REVISION,
            "session": a[1], "profile_revision": a[2], "profile_binding": a[3], "nonce": a[4], "uid": int(a[5]),
            "installation_id": a[12], "profile_storage_id": a[13]}))
        pins = Pins.parse(canonical_json(dict(zip(PIN_HASH_FIELDS, a[6:12]), schema=2,
                                              backend="linux_desktop", protocol_revision=REVISION)))
        return cls(a[0], context, pins, a[14] if len(a) == 15 else None)

    def matches(self, context, pins):
        require(type(context) is Context and type(pins) is Pins)
        require(self.context == context and self.pins == pins, "CONTEXT_STALE")
