"""suiteの空成功と未登録workspace crateを拒否する共通判定。"""
import re


def rust_test_count(output):
    counts = [int(n) for n in re.findall(r"test result: ok\. (\d+) passed;", output)]
    if not counts or sum(counts) == 0:
        raise ValueError("Rust testが0件です。compile成功をtest成功に置き換えません")
    return sum(counts)


def check_members(actual, registered):
    missing = set(actual) - set(registered)
    if missing:
        raise ValueError("新しいworkspace crateのsuiteを登録してください: " + ", ".join(sorted(missing)))
