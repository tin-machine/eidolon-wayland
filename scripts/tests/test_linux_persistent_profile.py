"""C1 ownership checks against real files, processes, locks and pipe EOF.

Only a dedicated temporary passwd-HOME is used. The authenticated nonroot
checks are never disabled merely to run this suite in a root-only sandbox.
"""
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import pwd
import select
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts/linux"), str(ROOT / "scripts")]
import eidolon_linux_persistent_profile as profile
import linux_desktop_v3_contract as contract

FIXTURE_PATH = Path(__file__).parent / "fixtures/linux-desktop-v3-contract.json"
FIXTURE = json.loads(FIXTURE_PATH.read_bytes())
RECORDS = {row["id"]: row for row in FIXTURE["records"]}
WORKER = Path(__file__).parent / "fixtures/linux_persistent_profile_worker.py"


def fixed_context():
    return contract.Context.parse(RECORDS["context"]["raw"].encode())


class PureBoundaryTests(unittest.TestCase):
    def test_existing_golden_owner_is_consumed_without_extra_fields(self):
        owner = profile.storage_owner(fixed_context())
        self.assertEqual(owner.canonical(), RECORDS["owner"]["canonical_utf8"].encode())
        self.assertEqual(owner.sha256(), RECORDS["owner"]["sha256"])
        self.assertEqual(set(owner.record()), set(contract.OWNER_FIELDS))
        for forbidden in ("session", "nonce", "profile_revision", "profile_binding", "legacy_installation", "secret"):
            self.assertNotIn(forbidden, owner.record())

    def test_existing_owner_rejection_vectors_remain_strict(self):
        cases = [row for row in FIXTURE["invalid"] if row["kind"] == "owner"]
        self.assertEqual(len(cases), 28)
        for row in cases:
            with self.subTest(vector=row["id"]), self.assertRaises(contract.ContractError):
                raw = bytes.fromhex(row["raw_hex"]) if "raw_hex" in row else row["raw"].encode()
                contract.StorageOwner.parse(raw)

    def test_no_default_or_boolean_recovery_barrier(self):
        context = fixed_context()
        with self.assertRaises(TypeError):
            profile.JournalBarrier(context, profile.storage_owner(context))
        with self.assertRaises(TypeError):
            profile.PersistentProfileRepository().acquire(context, process_owner=None)

    def test_authenticated_uid_mismatch_rejects_before_passwd_or_writes(self):
        value = fixed_context().record()
        value["uid"] = os.getuid() + 1
        context = contract.Context.parse(contract.canonical_json(value))
        with mock.patch.object(profile.pwd, "getpwuid", side_effect=AssertionError("unauthenticated passwd lookup")), \
                mock.patch.object(profile.os, "mkdir", side_effect=AssertionError("unauthenticated write")):
            with self.assertRaisesRegex(profile.ProfileError, "^APP_STATE_INVALID$"):
                profile.PersistentProfileRepository().acquire(context, process_owner=None, barrier=None)

    def test_filesystem_identity_is_immutable(self):
        identity = profile.FileIdentity(1, 2)
        with self.assertRaises(FrozenInstanceError):
            identity.inode = 3

    def test_filesystem_identity_rejects_bool_zero_and_negative_inodes(self):
        for device, inode in ((True, 2), (1, False), (-1, 2), (1, 0), (1, -1), ("1", 2)):
            with self.subTest(device=device, inode=inode), self.assertRaises(profile.ProfileError):
                profile.FileIdentity(device, inode)


class RealPersistentProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "linux" or os.getuid() == 0:
            reason = "dedicated Linux nonroot process required; UID checks are not mocked"
            if os.environ.get("EIDOLON_REQUIRE_C1_TESTS") == "1":
                raise AssertionError(reason)
            raise unittest.SkipTest(reason)
        cls.account_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        if not cls.account_home.is_absolute() or not cls.account_home.is_dir():
            raise AssertionError("test account passwd HOME unavailable")

    def private_home(self):
        directory = tempfile.TemporaryDirectory(prefix="eidolon-c1-test-", dir=self.account_home)
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        path.chmod(0o700)
        return path

    def command(self, case, home, *arguments):
        return [sys.executable, "-I", "-S", "-B", str(WORKER), case, str(home), *arguments]

    def run_case(self, case, *, home=None):
        home = home or self.private_home()
        result = subprocess.run(self.command(case, home), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(lines[-1]["case"], case)
        self.assertEqual(lines[-1]["result"], "passed")
        return lines[-1]

    def start_worker(self, case, home, journal):
        process = subprocess.Popen(self.command(case, home, journal), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.addCleanup(self.stop_worker, process)
        return process

    @staticmethod
    def stop_worker(process):
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=3)

    def event(self, process, timeout=7):
        if not select.select([process.stdout], [], [], timeout)[0]:
            self.fail("C1 worker event timeout")
        raw = process.stdout.readline(8193)
        if not raw:
            self.fail("C1 worker exited: " + process.stderr.read())
        self.assertLessEqual(len(raw), 8192)
        return json.loads(raw)

    def send(self, process, message):
        process.stdin.write(message + "\n")
        process.stdin.flush()

    def test_fixed_path_modes_owner_environment_and_real_lock(self):
        self.run_case("basic")

    def test_reopen_revision_nonce_and_id_change_preserve_existing_data(self):
        self.run_case("reopen")

    def test_real_owner_records_use_the_same_golden_rejections(self):
        value = self.run_case("invalid_owner")
        self.assertEqual(value["details"], {"raw_rejections": 28, "owner_mismatches": 3})

    def test_concurrent_process_creation_has_one_owner_and_stable_lock(self):
        home = self.private_home()
        first = self.start_worker("compete", home, "creator-a")
        second = self.start_worker("compete", home, "creator-b")
        self.assertEqual(self.event(first), {"event": "prepared"})
        self.assertEqual(self.event(second), {"event": "prepared"})
        self.send(first, "go")
        self.send(second, "go")
        one, two = self.event(first), self.event(second)
        self.assertEqual(sorted([one["event"], two["event"]]), ["acquired", "denied"])
        winner, accepted, denied = (first, one, two) if one["event"] == "acquired" else (second, two, one)
        self.assertEqual(denied["reason"], "APP_STATE_BUSY")
        self.send(winner, "release")
        self.assertEqual(self.event(winner), {"event": "released"})
        for process in (first, second):
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
        reopened = self.start_worker("hold", home, "after-creators")
        current = self.event(reopened)
        self.assertEqual(current["event"], "acquired")
        self.assertEqual(current["identity"], accepted["identity"])
        self.send(reopened, "release")
        self.assertEqual(self.event(reopened), {"event": "released"})
        _, stderr = reopened.communicate(timeout=5)
        self.assertEqual(reopened.returncode, 0, stderr)

    def test_os_unlock_after_helper_death_does_not_clear_unknown_journal(self):
        home = self.private_home()
        first = self.start_worker("hold", home, "crash-journal")
        acquired = self.event(first)
        self.assertEqual(acquired["event"], "acquired")
        first.kill()
        first.communicate(timeout=3)
        lock = Path(acquired["root"]) / "profile.lock"
        code = "import os,fcntl,sys;f=os.open(sys.argv[1],os.O_RDWR);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
        probe = subprocess.run([sys.executable, "-I", "-c", code, str(lock)], capture_output=True, timeout=3)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        second = self.start_worker("hold", home, "crash-journal")
        self.assertEqual(self.event(second), {"event": "denied", "reason": "APP_STATE_RECOVERY_REQUIRED"})
        _, stderr = second.communicate(timeout=3)
        self.assertEqual(second.returncode, 0, stderr)

    def test_cross_process_directory_owner_and_lock_replacement_remain_pending(self):
        for name in ("root", "profile.lock", "owner.json", "home", "config", "data", "state", "Documents"):
            with self.subTest(replaced=name):
                self.run_case("replace:" + name)

    def test_replacing_nested_passwd_home_cannot_split_the_live_lock(self):
        home = self.private_home()
        detached = home.with_name(home.name + ".detached")
        self.addCleanup(lambda: shutil.rmtree(detached) if detached.exists() else None)
        first = self.start_worker("hold", home, "home-owner")
        acquired = self.event(first)
        self.assertEqual(acquired["event"], "acquired")
        relative_root = Path(acquired["root"]).relative_to(home)
        # This controller is a different real process from both repositories.
        home.rename(detached)
        shutil.copytree(detached, home)
        second = self.start_worker("hold", home, "home-contender")
        self.assertEqual(self.event(second), {"event": "denied", "reason": "APP_STATE_BUSY"})
        _, stderr = second.communicate(timeout=3)
        self.assertEqual(second.returncode, 0, stderr)
        code = """import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDWR)
try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError: sys.exit(0)
sys.exit(7)
"""
        probe = subprocess.run([sys.executable, "-I", "-c", code,
                                str(detached / relative_root / "profile.lock")], capture_output=True, timeout=3)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.send(first, "pending")
        self.assertEqual(self.event(first), {"event": "pending"})
        _, stderr = first.communicate(timeout=5)
        self.assertEqual(first.returncode, 0, stderr)

    def test_managed_symlinks_are_never_followed(self):
        for name in (".local", ".local/share", ".local/share/eidolon", "owner.json", "profile.lock", "home",
                     "config", "data", "state", "Documents"):
            with self.subTest(link=name):
                self.run_case("tamper:symlink:" + name)

    def test_hardlinks_permissions_nonregular_and_nonempty_locks_reject(self):
        cases = ("hardlink:owner.json", "hardlink:profile.lock", "mode:owner.json", "mode:profile.lock", "mode:config",
                 "missing", "fifo", "lock_bytes")
        for case in cases:
            with self.subTest(tamper=case):
                self.run_case("tamper:" + case)

    def test_foreign_uid_record_is_rejected_without_adoption(self):
        if not shutil.which("sudo"):
            self.skipTest("sudo unavailable for isolated foreign-UID inode fixture")
        result = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=3)
        if result.returncode:
            self.skipTest("passwordless sudo unavailable for isolated foreign-UID inode fixture")
        self.run_case("tamper:foreign_uid")

    def test_unknown_and_partially_written_profile_are_not_repaired(self):
        for case in ("preexisting", "mkdir_failure", "lock_creation_failure", "fsync_before_owner", "write_failure"):
            with self.subTest(failure=case):
                self.run_case("partial:" + case)

    def test_known_missing_root_is_not_recreated(self):
        self.run_case("missing_known")

    def test_no_acquisition_without_real_journal_barrier(self):
        for case in ("unknown", "boolean", "after"):
            with self.subTest(barrier=case):
                self.run_case("barrier:" + case)

    def test_release_needs_completed_owner_all_descendants_and_actual_pipe_eof(self):
        for case in ("unfinished", "plain", "descendant", "pipe_writer", "untracked"):
            with self.subTest(release=case):
                self.run_case("release:" + case)

    def test_release_barrier_denial_retains_the_same_handle_for_verified_retry(self):
        self.run_case("release:barrier")
        value = self.run_case("release:posthook")
        self.assertEqual(value["details"], {"release_denied": True, "journal": "release_authorized"})

    def test_forked_process_cannot_use_borrowed_owner_handle(self):
        self.run_case("borrowed")


if __name__ == "__main__":
    unittest.main()
