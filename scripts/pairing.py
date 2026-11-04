"""公開情報だけを照合するpairing契約。Termux側の固定authorized_keys更新にも同じ実装を使う。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile


class PairError(ValueError):
    pass


def canonical_key(value):
    if not isinstance(value, str) or len(value) > 4096 or "\n" in value.strip() or "\r" in value.strip():
        raise PairError("PUBLIC_KEY_INVALID")
    parts = value.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise PairError("PUBLIC_KEY_INVALID")
    try:
        wire = base64.b64decode(parts[1], validate=True)
        if len(wire) != 51 or struct.unpack(">I", wire[:4])[0] != 11 or wire[4:15] != b"ssh-ed25519" or struct.unpack(">I", wire[15:19])[0] != 32:
            raise ValueError()
    except (ValueError, struct.error):
        raise PairError("PUBLIC_KEY_INVALID") from None
    return "ssh-ed25519 " + base64.b64encode(wire).decode("ascii")


def verify_device(physical, known_hosts, host_key):
    if (not isinstance(physical, str) or physical.lower() in {"unknown", "null"}
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", physical)):
        raise PairError("DEVICE_UNKNOWN")
    alias = "eidolon-quest-" + hashlib.sha256(physical.encode()).hexdigest()[:12]
    entries = [line.split() for line in known_hosts.splitlines() if line.split() and not line.lstrip().startswith("#")
               and alias in line.split()[0].split(",")]
    if len(entries) != 1 or len(entries[0]) < 3:
        raise PairError("DEVICE_UNKNOWN")
    if canonical_key(" ".join(entries[0][1:3])) != canonical_key(host_key):
        raise PairError("HOST_KEY_MISMATCH")


def profile(public, username, host_key, previous=None):
    if (not isinstance(public, dict) or set(public) != {"schema", "installation", "public_key"}
            or type(public.get("schema")) is not int or public["schema"] != 1
            or not isinstance(public.get("installation"), str) or not re.fullmatch(r"[0-9a-f]{32}", public["installation"])
            or not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", username)):
        raise PairError("PROFILE_INVALID")
    canonical_key(public["public_key"])
    value = {"schema": 1, "profile_id": "termux-local", "installation": public["installation"],
             "username": username, "host_key": canonical_key(host_key)}
    if previous is not None:
        if (not isinstance(previous, dict) or set(previous) != set(value) or previous.get("schema") != 1
                or previous.get("profile_id") != "termux-local" or previous.get("installation") != value["installation"]):
            raise PairError("PROFILE_INSTALLATION_MISMATCH")
        if canonical_key(previous.get("host_key")) != value["host_key"]:
            raise PairError("HOST_KEY_MISMATCH")
    return value


def append_authorized(existing, public):
    key = canonical_key(public)
    pattern = re.compile(r"(?:^|\s)" + re.escape(key) + r"(?:\s|$)")
    if any(pattern.search(line) for line in existing.splitlines() if not line.lstrip().startswith("#")):
        return existing, False
    return existing + ("\n" if existing and not existing.endswith("\n") else "") + key + " eidolon-apk\n", True


def fresh_runtime(snapshot, pid, previous_pid, build_info):
    state = snapshot.get("runtime", {}).get("runtime", {})
    return (isinstance(pid, str) and pid.isdecimal() and int(pid) > 0 and pid != previous_pid
            and str(snapshot.get("process_id")) == pid and snapshot.get("build_info") == build_info
            and state.get("state") == "Ready" and state.get("activity_visible") is True)


def update_authorized(path, public):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise PairError("AUTHORIZED_KEYS_UNSAFE")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_uid != os.getuid() or path.parent.stat().st_mode & 0o077:
        raise PairError("AUTHORIZED_KEYS_UNSAFE")
    if path.exists() and (not path.is_file() or path.stat().st_size > 1024 * 1024 or path.stat().st_nlink != 1):
        raise PairError("AUTHORIZED_KEYS_UNSAFE")
    before = path.read_bytes().decode("utf-8") if path.exists() else ""
    after, changed = append_authorized(before, public)
    if not changed:
        path.chmod(0o600)
        return False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".eidolon-authorized-", delete=False) as output:
            temporary = Path(output.name)
            output.write(after.encode("utf-8")); output.flush(); os.fsync(output.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        return True
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    # ADBから受け取るpacketは公開鍵だけ。更新先は固定し、外部path/shellを受けない。
    try:
        packet = json.loads(sys.stdin.read(8193))
        if set(packet) != {"schema", "public_key"} or type(packet["schema"]) is not int or packet["schema"] != 1:
            raise PairError("PUBLIC_KEY_INVALID")
        changed = update_authorized(Path("/data/data/com.termux/files/home/.ssh/authorized_keys"), packet["public_key"])
        print(json.dumps({"schema": 1, "changed": changed, "existing_entries_preserved": True}))
    except PairError as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
