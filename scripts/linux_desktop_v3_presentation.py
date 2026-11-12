"""B1 synthetic evidence model. No listener, timer, SDK callback or wire trust.

Only the future native owner may supply these observations. JSON exists here
for language-neutral fixtures, not to authorize remote declarations of origin.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from linux_desktop_v3_contract import (
    _Record, Context, Selection, Pins, REVISION, DEMO, EDITOR, MAX_I64,
    Reason, StopReason, canonical_json, decimal, fields, hexadecimal, integer,
    parse_json, require,
)

MAIN_WAIT_MS = 20000
SDK_WAIT_MS = 10000
_STATE_CAPABILITY = object()


class NativeOrigin(_Record):
    @classmethod
    def _validate(cls, raw):
        v = parse_json(raw)
        fields(v, ("runtime", "session", "request", "nonce", "scope", "app_id"))
        require(all(decimal(v[k]) for k in ("runtime", "session", "request")) and
                hexadecimal(v["nonce"], 16) and hexadecimal(v["scope"], 64) and v["app_id"] in (DEMO, EDITOR))
        return v

    def matches(self, context: Context, selection: Selection, pins: Pins):
        selection.matches(context, pins)
        v, c, s = self.record(), context.record(), selection.record()
        require(v["session"] == c["session"] and v["nonce"] == c["nonce"] and
                v["scope"] == context.scope() and v["app_id"] == s["app_id"], "APP_ORIGIN_UNKNOWN")


def validate_parent(v):
    require(type(v) is dict and type(v.get("kind")) is str)
    require(v["kind"] in ("none", "resolved", "unresolved", "invalid"))
    fields(v, ("kind", "window") if v["kind"] == "resolved" else ("kind",))
    require(v["kind"] != "resolved" or decimal(v["window"]))


def validate_target(v):
    fields(v, ("session", "window", "scene", "generation"))
    require(all(decimal(item) for item in v.values()))


def validate_create(v):
    fields(v, ("runtime", "target", "command_seq", "action"))
    require(decimal(v["runtime"]) and decimal(v["command_seq"]) and v["action"] == "create")
    validate_target(v["target"])


def validate_window(v):
    fields(v, ("origin", "window", "role", "life", "app_id", "parent"))
    NativeOrigin.parse(canonical_json(v["origin"]))
    require(decimal(v["window"]) and v["role"] in ("main", "dialog") and
            v["life"] in ("mapped", "unmapped", "destroyed") and
            (v["app_id"] is None or (type(v["app_id"]) is str and len(v["app_id"]) <= 128)))
    validate_parent(v["parent"])


def validate_evidence(v):
    fields(v, ("panel", "ack", "sdk", "frame"))
    for name in ("panel", "ack"):
        item = v[name]
        if item is None:
            continue
        flags = ("applied", "error") if name == "panel" else ("success",)
        fields(item, ("create", *flags))
        validate_create(item["create"])
        require(all(type(item[k]) is bool for k in flags))
    if v["sdk"] is not None:
        item = v["sdk"]
        fields(item, ("runtime", "target", "bound", "diagnostic"))
        validate_target(item["target"])
        require(decimal(item["runtime"]) and type(item["bound"]) is bool and type(item["diagnostic"]) is bool)
    if v["frame"] is not None:
        item = v["frame"]
        fields(item, ("runtime", "target", "frames", "paused", "presentation_paused"))
        validate_target(item["target"])
        require(decimal(item["runtime"]) and integer(item["frames"]) and type(item["paused"]) is bool and
                type(item["presentation_paused"]) is bool)


class Observation(_Record):
    @classmethod
    def _validate(cls, raw):
        v = parse_json(raw)
        fields(v, ("now_ms", "origin", "started", "terminal", "cancelled", "cleanup_pending", "activity_visible",
                   "scene", "xr_focused", "global_window_count", "windows", "current_create", "evidence", "fallback"))
        require(integer(v["now_ms"]) and (v["scene"] is None or decimal(v["scene"])) and
                all(type(v[k]) is bool for k in ("cancelled", "cleanup_pending", "activity_visible", "xr_focused", "fallback")))
        NativeOrigin.parse(canonical_json(v["origin"]))
        if v["started"] is not None:
            NativeOrigin.parse(canonical_json(v["started"]))
        if v["terminal"] is not None:
            fields(v["terminal"], ("origin", "reason"))
            NativeOrigin.parse(canonical_json(v["terminal"]["origin"]))
            require(v["terminal"]["reason"] in tuple(x.value for x in StopReason))
        require(type(v["windows"]) is list and integer(v["global_window_count"], 0, 9) and
                len(v["windows"]) <= v["global_window_count"])
        for w in v["windows"]:
            validate_window(w)
        ids = [w["window"] for w in v["windows"]]
        require(len(ids) == len(set(ids)))
        if v["current_create"] is not None:
            validate_create(v["current_create"])
        validate_evidence(v["evidence"])
        return v


def _main_app_id(origin):
    return "mousepad" if origin["app_id"] == EDITOR else "io.github.tinmachine.eidolon.linuxdesktop.r" + origin["nonce"] * 2


def _topology(windows, main, origin):
    """Live dialog chains reach the adopted main, without cycles or promotion."""
    nodes = {w["window"]: w for w in windows}
    for w in windows:
        if w["life"] == "destroyed":
            continue
        if w["role"] == "main":
            if w["window"] != main or w["parent"]["kind"] != "none" or w["app_id"] != _main_app_id(origin):
                return False
            continue
        if origin["app_id"] == DEMO and w["app_id"] != _main_app_id(origin) + ".dialog":
            return False
        current, seen = w, {w["window"]}
        while True:
            parent = current["parent"]
            if parent["kind"] != "resolved" or parent["window"] in seen:
                return False
            current = nodes.get(parent["window"])
            if current is None or current["life"] == "destroyed" or current["origin"] != origin:
                return False
            if current["window"] == main:
                break
            if current["role"] != "dialog":
                return False
            seen.add(current["window"])
    return True


@dataclass(frozen=True, repr=False)
class Startup:
    origin: NativeOrigin
    t0_ms: int
    before: tuple = ()
    last_ms: int | None = None
    phase: str = "awaiting_main"
    main: str | None = None
    first_adopted_ms: int | None = None
    started: bool = False
    roles: tuple = ()
    current_create_bytes: bytes | None = None
    current_retired: bool = False
    startup_completed: bool = False
    first_failure: str | None = None
    cleanup_pending: bool = False
    fallback_used: bool = False
    _capability: object = None

    def __post_init__(self):
        require(self._capability is _STATE_CAPABILITY, "CONTRACT_INVALID")

    @classmethod
    def begin(cls, origin, t0_ms, before=()):
        require(type(origin) is NativeOrigin and integer(t0_ms, 0, MAX_I64 - MAIN_WAIT_MS - SDK_WAIT_MS) and
                type(before) is tuple and all(decimal(x) for x in before) and len(before) == len(set(before)) and len(before) <= 9)
        return cls(origin, t0_ms, before, t0_ms, _capability=_STATE_CAPABILITY)

    def summary(self):
        return {"state": self.phase, "main": self.main, "first_adopted_ms": self.first_adopted_ms,
                "startup_completed": self.startup_completed, "first_failure": self.first_failure,
                "cleanup_pending": self.cleanup_pending, "fallback_used": self.fallback_used}

    def advance(self, observation):
        require(type(observation) is Observation)
        v, owner = observation.record(), self.origin.record()
        require(v["now_ms"] >= self.last_ms, "CLOCK_STALE")
        require(v["origin"] == owner and (v["started"] is None or v["started"] == owner) and
                (v["terminal"] is None or v["terminal"]["origin"] == owner) and
                all(w["origin"] == owner for w in v["windows"]), "APP_ORIGIN_UNKNOWN")
        s = replace(self, last_ms=v["now_ms"], cleanup_pending=v["cleanup_pending"])

        def stop(reason=None):
            return replace(s, phase="pending" if s.cleanup_pending else "stopping", first_failure=s.first_failure or reason)

        if self.phase in ("stopping", "pending"):
            return stop()
        # Scope checks above precede terminal priority: a foreign owner cannot cancel us.
        if v["terminal"] is not None:
            return stop(None if self.startup_completed else Reason.APP_STARTUP_CANCELLED.value)
        if v["cancelled"] or (not self.startup_completed and
                (not v["activity_visible"] or v["scene"] is None or not v["xr_focused"])):
            return stop(None if self.startup_completed else Reason.APP_STARTUP_CANCELLED.value)
        if not self.startup_completed:
            deadline = self.t0_ms + MAIN_WAIT_MS if self.main is None else self.first_adopted_ms + SDK_WAIT_MS
            if v["now_ms"] >= deadline:
                return stop(Reason.APP_LAUNCH_TIMEOUT.value if self.main is None else Reason.APP_PRESENTATION_TIMEOUT.value)
        if v["cleanup_pending"]:
            return stop(Reason.APP_PRESENTATION_FAILED.value)
        roles = dict(self.roles)
        for w in v["windows"]:
            require(w["window"] not in roles or roles[w["window"]] == w["role"], "APP_WINDOW_UNSUPPORTED")
            roles[w["window"]] = w["role"]
        current = v["current_create"]
        evidence = v["evidence"]
        if current is None:
            require(all(x is None for x in evidence.values()) and not v["fallback"], "PRESENTATION_STALE")
        else:
            require(current["runtime"] == owner["runtime"] and current["target"]["session"] == owner["session"] and
                    current["target"]["scene"] == v["scene"], "PRESENTATION_STALE")
            if self.current_create_bytes is not None:
                old = parse_json(self.current_create_bytes)
                if current != old:
                    require(current["target"]["window"] == old["target"]["window"] and
                            (int(current["target"]["scene"]), int(current["target"]["generation"])) >
                            (int(old["target"]["scene"]), int(old["target"]["generation"])) and
                            int(current["command_seq"]) > int(old["command_seq"]), "PRESENTATION_STALE")
            for key in ("panel", "ack"):
                require(evidence[key] is None or evidence[key]["create"] == current, "PRESENTATION_STALE")
            for key in ("sdk", "frame"):
                require(evidence[key] is None or (evidence[key]["runtime"] == current["runtime"] and
                        evidence[key]["target"] == current["target"]), "PRESENTATION_STALE")
        s = replace(s, roles=tuple(sorted(roles.items())))
        s = replace(s, started=self.started or v["started"] is not None)
        roots = [w for w in v["windows"] if w["role"] == "main" and w["life"] != "destroyed"]
        if any(w["parent"]["kind"] != "none" or w["app_id"] != _main_app_id(owner) or
               w["window"] in self.before for w in roots) or len(roots) > 1:
            return stop(Reason.APP_WINDOW_UNSUPPORTED.value)
        if s.main is None:
            if not s.started or not roots or roots[0]["life"] != "mapped":
                require(current is None, "PRESENTATION_STALE")
                return s
            s = replace(s, main=roots[0]["window"], first_adopted_ms=v["now_ms"], phase="awaiting_sdk")
        nodes = {w["window"]: w for w in v["windows"]}
        main = nodes.get(s.main)
        if main is None or main["life"] == "destroyed" or not _topology(v["windows"], s.main, owner):
            return stop(Reason.APP_WINDOW_UNSUPPORTED.value)
        if current is not None:
            require(current["target"]["window"] == s.main, "PRESENTATION_STALE")
            same = canonical_json(current) == self.current_create_bytes
            require(not (self.current_retired and same and main["life"] == "mapped"), "PRESENTATION_STALE")
            s = replace(s, current_create_bytes=canonical_json(current), current_retired=self.current_retired if same else False)
        if (current is None or main["life"] != "mapped") and s.current_create_bytes is not None:
            s = replace(s, current_retired=True)
        panel, ack, sdk, frame = (evidence[k] for k in ("panel", "ack", "sdk", "frame"))
        failed = (panel is not None and panel["error"]) or (ack is not None and not ack["success"])
        if v["fallback"]:
            # Native authorizes the one decoration retry at the failing Create.
            # The failed target is retired; a strictly newer Create must follow.
            if not failed or s.fallback_used or self.startup_completed:
                return stop(Reason.APP_PRESENTATION_FAILED.value)
            return replace(s, phase="awaiting_sdk", fallback_used=True, current_retired=True)
        if failed:
            return stop(Reason.APP_PRESENTATION_FAILED.value)
        ready = (main["life"] == "mapped" and current is not None and panel is not None and panel["applied"] and
                 ack is not None and ack["success"] and sdk is not None and sdk["bound"] and not sdk["diagnostic"] and
                 frame is not None and frame["frames"] > 0 and not frame["paused"] and not frame["presentation_paused"] and
                 v["activity_visible"] and v["scene"] is not None and v["xr_focused"])
        return replace(s, phase="ready" if ready else "awaiting_sdk", startup_completed=s.startup_completed or ready)
