"""Quest試験の対象照合。ADBのpackageフィルターは部分一致である点に注意。"""
import re
import json
from pathlib import Path
import subprocess

PACKAGE = "io.github.tinmachine.eidolon"


def package_uid(output, package=PACKAGE):
    matches = re.findall(r"^package:" + re.escape(package) + r" uid:(\d+)\s*$", output, re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError("対象APKのUIDを一意に取得できませんでした")
    return int(matches[0])


def work_root():
    config = Path.home() / ".config/homecluster-agents/codex-work-root"
    root = Path(config.read_text().strip()) if config.exists() else Path.home() / ".local/state/homecluster-agents/codex-work"
    if not root.is_absolute():
        raise ValueError("作業rootには絶対pathが必要です")
    return root


class QuestDevice:
    def __init__(self, serial, run):
        self.serial, self.run = serial, run

    def adb(self, *command, timeout=30):
        with (self.run / "commands.log").open("a") as log:
            log.write(json.dumps(["adb", "-s", "[ADB_TARGET]", *map(str, command)], ensure_ascii=False) + "\n")
        result = subprocess.run(["adb", "-s", self.serial, *map(str, command)], capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"{command}: {result.stdout}\n{result.stderr}")
        return result.stdout

    def validate(self):
        if self.adb("get-state").strip() != "device":
            raise RuntimeError("対象端末が承認済みdeviceではありません")
        model = self.adb("shell", "getprop", "ro.product.model").strip()
        api = int(self.adb("shell", "getprop", "ro.build.version.sdk").strip())
        if model != "Quest 3" or api < 34:
            raise RuntimeError("対象端末またはAndroid APIが想定外です")
        return {"model": model, "api": api}

    def start_log(self, uid, output):
        started = self.adb("shell", "date", "+%m-%d_%H:%M:%S.000").strip().replace("_", " ")
        return subprocess.Popen(["adb", "-s", self.serial, "logcat", "-v", "threadtime", "-T", started, "--uid=" + str(uid)], stdout=output, stderr=subprocess.STDOUT)
