"""Quest試験の対象照合。ADBのpackageフィルターは部分一致である点に注意。"""
import re

PACKAGE = "io.github.tinmachine.eidolon"


def package_uid(output, package=PACKAGE):
    matches = re.findall(r"^package:" + re.escape(package) + r" uid:(\d+)\s*$", output, re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError("対象APKのUIDを一意に取得できませんでした")
    return int(matches[0])
