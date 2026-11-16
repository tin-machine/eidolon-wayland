"""I6 N04 temporal proof primitives. Not an acceptance verdict.

Both monotonic clocks and the transport sequence are required. This module
cannot promote a producer status to PASS; a complete GUI/cleanup oracle must
invoke it before claiming any of N04's frozen assertions.
"""
from __future__ import annotations

import linux_real_app_v3_evidence as evidence

ASSERTIONS = (
    "idle_at_least_125s", "multiple_lease_heartbeats",
    "fresh_dialog_before_idle", "fresh_dialog_after_idle",
    "stop_responsive", "no_total_runtime_limit", "product_census_complete",
    "journal_finalized", "origin_retirement_before_runtime_stop",
    "registry_destroy_accepted",
)
MIN_NS = 125_000_000_000
MAX_SAMPLE_GAP_NS = 11_000_000_000  # 5s product cadence; observation allowance only
MAX_ACK_GAP_NS = 15_000_000_000     # original C2 lease; never extend the product
MAX_CLOCK_SKEW_NS = 2_000_000_000


def need(condition, reason="I6_N04_TEMPORAL_INVALID"):
    evidence.require(condition, reason)


def unique(rows, kind):
    selected = [row for row in rows if row.get("kind") == kind]
    need(len(selected) == 1)
    return selected[0]


def natural(value):
    need(type(value) is int and 0 <= value < (1 << 63))
    return value


def verify_temporal(native, external):
    """Reconstruct one capture's real idle heartbeat/ACK progression.

    native: read-only Registry Host acceptance trace and native observation
    watermark; external: separate supervisor timestamps at *stream receipt*.
    A synthetic fixture or an SSH-alive probe cannot satisfy this oracle.
    """
    need(type(native) is dict and type(external) is dict)
    run = native["run_id"]
    need(evidence.identity(run) and external["run_id"] == run)
    observations = native["observations"]
    trace = native["trace"]
    need(type(observations) is list and len(observations) <= 128)
    need(type(trace) is dict and trace["overflow"] is False)
    events = trace["events"]
    need(type(events) is list and 0 < len(events) <= evidence.MAX_TRACE_EVENTS)
    begin = unique(observations, "n04_idle_begin")
    end = unique(observations, "n04_idle_end")
    need(int(begin["sequence"]) < int(end["sequence"]))
    for row in observations:
        need(type(row) is dict and set(row) == {"sequence", "kind", "transport_sequence", "data"})
    a, b = natural(begin["data"]["ns"]), natural(end["data"]["ns"])
    need(b - a >= MIN_NS)
    markers = external["markers"]
    need(type(markers) is list and len(markers) == 2)
    need([m["kind"] for m in markers] == ["begin", "end"])
    need(all(set(m) == {"kind", "run_id", "at_ns"} and m["run_id"] == run
             for m in markers))
    x, y = (natural(m["at_ns"]) for m in markers)
    need(y - x >= MIN_NS and abs(a - x) <= MAX_CLOCK_SKEW_NS
         and abs(b - y) <= MAX_CLOCK_SKEW_NS)
    low, high = int(begin["transport_sequence"]), int(end["transport_sequence"])
    need(0 < low < high <= len(events))
    capture = None
    for n, event in enumerate(events, 1):
        need(event["sequence"] == str(n))
        cap = event["capture"]
        need(type(cap) is dict)
        if capture is None:
            capture = cap
        need(cap == capture and cap.get("capture_id") == native["n04"]["capture_id"],
             "I6_OWNER_MISMATCH")
    heartbeats = []
    pending = None
    for pos, event in enumerate(events, 1):
        if pos <= low or pos > high:
            continue
        if event["kind"] == "control_send_attempt" and event["data"]["action"] == "heartbeat":
            need(pending is None, "I6_N04_HEARTBEAT_UNACKED")
            pending = pos
        elif event["kind"] == "protocol_event":
            record = event["data"]["record"]
            kind = record["event"]["type"]
            if kind == "heartbeat":
                need(pending is not None and pending < pos, "I6_N04_HEARTBEAT_UNACKED")
                need(record["event"]["payload"] == {"phase":"started"})
                seq = record["request_sequence"]
                need(type(seq) is str and seq.isascii() and seq.isdecimal()
                     and int(seq) > 0)
                heartbeats.append((pos, int(seq)))
                pending = None
            elif kind in ("stopped", "pending"):
                need(False, "I6_N04_LEASE_LOST")
    need(pending is None and len(heartbeats) >= 20, "I6_N04_HEARTBEAT_UNACKED")
    seqs = [seq for _, seq in heartbeats]
    need(len(set(seqs)) == len(seqs) and
         all(next_seq == seq + 1 for seq, next_seq in zip(seqs, seqs[1:])),
         "I6_N04_HEARTBEAT_SEQUENCE")
    samples = [row for row in observations if row["kind"] == "n04_lease_sample"]
    need(len(samples) >= 20)
    prior_time, prior_count = a, 0
    for row in samples:
        now = natural(row["data"]["ns"])
        watermark = int(row["transport_sequence"])
        need(prior_time < now <= b and now - prior_time <= MAX_SAMPLE_GAP_NS)
        need(low <= watermark <= high)
        # Every sample must observe an additional, unique wire heartbeat ACK;
        # reusing an earlier successful ACK is not ongoing lease proof.
        count = sum(pos <= watermark for pos, _ in heartbeats)
        need(count > prior_count and count - prior_count <= 2)
        prior_time, prior_count = now, count
    need(b - prior_time <= MAX_SAMPLE_GAP_NS and
         prior_count >= 20)
    return {"capture_id":capture["capture_id"], "duration_ns":b-a,
            "outer_duration_ns":y-x, "heartbeat_acks":len(heartbeats)}
