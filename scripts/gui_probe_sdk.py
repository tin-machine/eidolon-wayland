"""開発用GUI probeのsource照合と本文を含まないSDK観測。"""
import hashlib


def digest_sources():
    from native_build import ROOT, source_files
    digest = hashlib.sha256()
    for path in source_files(ROOT):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def sdk_slice(value):
    runtime = value["runtime"]["runtime"]
    app = runtime["app"]
    windows = sorted(row["window"] for row in app.get("windows", []) if row.get("life") == "Mapped")
    targets = []
    for row in runtime.get("render", {}).get("targets", []):
        key = row.get("key", {})
        if key.get("session") == app["session"]:
            targets.append(dict(window=key.get("window"), scene=key.get("scene"), generation=key.get("generation"),
                                frames=row.get("frames"), content_frame=row.get("content_frame")))
    return dict(sequence=value["sequence"], mapped=windows, targets=targets)


def summarize_first_frame(baseline, samples):
    """静止GUIでは最初のcontent/frameだけを確認し、2回の内容前進を要求しない。"""
    extras, first, content = set(), {}, {}
    original = set(baseline["mapped"])
    keys = {(row["window"], row["scene"], row["generation"]) for row in baseline["targets"]}
    preserved = True
    for sample in samples:
        mapped = set(sample["mapped"])
        preserved &= original <= mapped
        extras.update(mapped - original)
        for target in sample["targets"]:
            window = target["window"]
            key = (window, target["scene"], target["generation"])
            number = target.get("content_frame")
            if (window in mapped - original and key not in keys and type(target["frames"]) is int and target["frames"] >= 1
                    and isinstance(number, (str, int)) and len(str(number)) <= 20 and str(number).isascii()
                    and str(number).isdecimal() and int(number) > 0):
                first[window] = max(first.get(window, 0), target["frames"])
                content.setdefault(window, str(number))
    retired = bool(extras) and not extras & set((samples[-1] if samples else baseline)["mapped"])
    observed = len(extras) == 1 and extras <= first.keys() and retired and preserved
    return dict(status="observed" if observed else "not_verified", additional_windows=sorted(extras),
                first_frames=first, first_content_frames=content, all_retired=retired, baseline_windows_preserved=preserved)
