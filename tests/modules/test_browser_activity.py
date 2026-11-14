import importlib.util
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "browser_activity_reader", ROOT / "module_utils/browser_settings.py"
)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)

BROWSERS = {
    "chrome": {
        "command": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "lock": "SingletonLock",
        "target": "fixture-host-123",
    },
    "firefox": {
        "command": "/Applications/Firefox.app/Contents/MacOS/firefox",
        "lock": "lock",
        "target": "127.0.0.1:+123",
    },
}


class BrowserActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.home_patch = patch.object(reader.Path, "home", return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.hostname_patch = patch.object(
            socket, "gethostname", return_value="fixture-host"
        )
        self.hostname_patch.start()
        self.addCleanup(self.hostname_patch.stop)

    def folders(self, family, default=False, name="selected"):
        if default:
            support = self.home / "Library/Application Support"
            user_data = support / ("Google/Chrome" if family == "chrome" else "Firefox")
        else:
            user_data = self.root / family / name
        profile = user_data / (
            "Default" if family == "chrome" else "Profiles/fixture.default"
        )
        profile.mkdir(parents=True, exist_ok=True)
        return profile, user_data

    def lock(self, family, folders, target=None):
        profile, user_data = folders
        directory = user_data if family == "chrome" else profile
        path = directory / BROWSERS[family]["lock"]
        path.symlink_to(target or BROWSERS[family]["target"])
        return path

    def check(self, family, folders, processes="", returncode=0):
        result = subprocess.CompletedProcess([], returncode, processes)
        with patch.object(reader.subprocess, "run", return_value=result):
            return reader.check_browser_closed(family, *folders)

    def assert_refused(self, family, folders, processes="", returncode=0):
        with self.assertRaises(reader.CaptureError) as error:
            self.check(family, folders, processes, returncode)
        self.assertEqual(error.exception.code, 3)
        return str(error.exception)

    def test_selected_live_lock_refuses_capture(self):
        for family, browser in BROWSERS.items():
            with self.subTest(family=family):
                folders = self.folders(family)
                self.lock(family, folders)
                self.assert_refused(family, folders, "123 " + browser["command"])

    def test_browser_in_another_isolated_folder_allows_capture(self):
        for family, browser in BROWSERS.items():
            with self.subTest(family=family):
                selected = self.folders(family)
                other = self.folders(family, name="other")
                self.lock(family, other)
                self.check(family, selected, "123 " + browser["command"])

    def test_other_browser_family_allows_capture(self):
        for family, other in (("firefox", "chrome"), ("chrome", "firefox")):
            with self.subTest(family=family):
                folders = self.folders(family, default=True)
                self.check(family, folders, "123 " + BROWSERS[other]["command"])

    def test_stale_selected_lock_allows_capture(self):
        for family, browser in BROWSERS.items():
            with self.subTest(family=family):
                folders = self.folders(family)
                self.lock(family, folders)
                self.check(family, folders, "456 " + browser["command"])

    def test_firefox_selected_parentlock_owner_refuses_capture(self):
        folders = self.folders("firefox", default=True)
        (folders[0] / ".parentlock").touch()
        with patch.object(reader, "_fcntl_lock_owner", return_value=123, create=True):
            self.assert_refused(
                "firefox", folders, "123 " + BROWSERS["firefox"]["command"]
            )

    def test_firefox_free_parentlock_allows_another_running_profile(self):
        folders = self.folders("firefox", default=True)
        (folders[0] / ".parentlock").touch()
        with patch.object(reader, "_fcntl_lock_owner", return_value=None, create=True):
            self.check("firefox", folders, "123 " + BROWSERS["firefox"]["command"])

    def test_firefox_parentlock_owner_must_match_browser_process(self):
        folders = self.folders("firefox", default=True)
        (folders[0] / ".parentlock").touch()
        processes = (
            "123 /bin/artificial-test-process\n456 " + BROWSERS["firefox"]["command"]
        )
        with patch.object(reader, "_fcntl_lock_owner", return_value=123, create=True):
            self.check("firefox", folders, processes)

    def test_firefox_parentlock_query_failure_refuses_without_disclosure(self):
        folders = self.folders("firefox", default=True)
        lock = folders[0] / ".parentlock"
        lock.touch()
        with patch.object(
            reader,
            "_fcntl_lock_owner",
            side_effect=PermissionError("artificial-sensitive-parentlock-error"),
            create=True,
        ):
            message = self.assert_refused("firefox", folders)
        self.assertNotIn(str(lock), message)
        self.assertNotIn("artificial-sensitive-parentlock-error", message)

    def test_reused_lock_pid_with_unrelated_process_allows_capture(self):
        for family in BROWSERS:
            with self.subTest(family=family):
                folders = self.folders(family)
                self.lock(family, folders)
                self.check(family, folders, "123 /bin/artificial-test-process")

    def test_default_folder_without_lock_refuses_running_browser(self):
        for family, browser in BROWSERS.items():
            with self.subTest(family=family):
                folders = self.folders(family, default=True)
                self.assert_refused(family, folders, "123 " + browser["command"])

    def test_default_folder_without_lock_allows_closed_browser(self):
        for family in BROWSERS:
            with self.subTest(family=family):
                self.check(family, self.folders(family, default=True))

    def test_unreadable_default_lock_refuses_running_browser(self):
        original = Path.readlink
        for family, browser in BROWSERS.items():
            with self.subTest(family=family):
                folders = self.folders(family, default=True)
                lock = self.lock(family, folders)
                attempted = []

                def readlink(path):
                    if path == lock:
                        attempted.append(path)
                        raise PermissionError("artificial-sensitive-lock-target")
                    return original(path)

                with patch.object(reader.Path, "readlink", readlink):
                    message = self.assert_refused(
                        family, folders, "123 " + browser["command"]
                    )
                self.assertEqual(attempted, [lock])
                self.assertNotIn(str(lock), message)
                self.assertNotIn("artificial-sensitive-lock-target", message)

    def test_unknown_lock_format_refuses_capture(self):
        for family in BROWSERS:
            with self.subTest(family=family):
                folders = self.folders(family)
                self.lock(family, folders, "artificial-sensitive-unknown-lock")
                message = self.assert_refused(family, folders)
                self.assertNotIn("artificial-sensitive-unknown-lock", message)

    def test_failed_process_listing_refuses_capture(self):
        for family in BROWSERS:
            with self.subTest(family=family):
                self.assert_refused(family, self.folders(family), returncode=1)

    def test_process_listing_requests_no_arguments_or_account_names(self):
        result = subprocess.CompletedProcess([], 0, "")
        with patch.object(reader.subprocess, "run", return_value=result) as run:
            reader.check_browser_closed("chrome", *self.folders("chrome"))
        self.assertEqual(run.call_args.args[0], ["/bin/ps", "-axo", "pid=,comm="])

    def test_unavailable_process_listing_refuses_without_disclosure(self):
        for family in BROWSERS:
            with self.subTest(family=family):
                folders = self.folders(family)
                with patch.object(
                    reader.subprocess,
                    "run",
                    side_effect=OSError("artificial-sensitive-process-error"),
                ):
                    with self.assertRaises(reader.CaptureError) as error:
                        reader.check_browser_closed(family, *folders)
                self.assertEqual(error.exception.code, 3)
                self.assertNotIn(
                    "artificial-sensitive-process-error", str(error.exception)
                )

    def test_lock_lstat_failure_refuses_without_disclosure(self):
        original = Path.lstat
        for family in BROWSERS:
            with self.subTest(family=family):
                folders = self.folders(family)
                lock = self.lock(family, folders)
                attempted = []

                def lstat(path, *args, **kwargs):
                    if path == lock:
                        attempted.append(path)
                        raise PermissionError("artificial-sensitive-lock-error")
                    return original(path, *args, **kwargs)

                with patch.object(reader.Path, "lstat", lstat):
                    message = self.assert_refused(family, folders)
                self.assertEqual(attempted, [lock])
                self.assertNotIn(str(lock), message)
                self.assertNotIn("artificial-sensitive-lock-error", message)

    def test_lock_changing_during_process_listing_refuses_capture(self):
        for family in BROWSERS:
            with self.subTest(family=family):
                folders = self.folders(family)
                lock = self.lock(family, folders)

                def changed_lock(*args, **kwargs):
                    lock.unlink()
                    lock.symlink_to(BROWSERS[family]["target"].replace("123", "456"))
                    return subprocess.CompletedProcess([], 0, "")

                with patch.object(reader.subprocess, "run", side_effect=changed_lock):
                    with self.assertRaises(reader.CaptureError) as error:
                        reader.check_browser_closed(family, *folders)
                self.assertEqual(error.exception.code, 3)

    def test_lock_and_source_metadata_stay_unchanged(self):
        for family, browser in BROWSERS.items():
            for active in (False, True):
                with self.subTest(family=family, active=active):
                    folders = self.folders(
                        family, name="active" if active else "closed"
                    )
                    lock = self.lock(family, folders)
                    source = folders[0] / "artificial-source"
                    source.write_text("artificial saved browser data")
                    watched = (lock, source, folders[0], folders[1])
                    before = {
                        path: (
                            path.lstat().st_ino,
                            path.lstat().st_size,
                            path.lstat().st_mtime_ns,
                            path.lstat().st_ctime_ns,
                        )
                        for path in watched
                    }
                    processes = ("123 " + browser["command"]) if active else ""
                    if active:
                        self.assert_refused(family, folders, processes)
                    else:
                        self.check(family, folders, processes)
                    self.assertEqual(
                        before,
                        {
                            path: (
                                path.lstat().st_ino,
                                path.lstat().st_size,
                                path.lstat().st_mtime_ns,
                                path.lstat().st_ctime_ns,
                            )
                            for path in watched
                        },
                    )
                    self.assertEqual(lock.readlink(), Path(browser["target"]))
                    self.assertEqual(
                        source.read_text(), "artificial saved browser data"
                    )


if __name__ == "__main__":
    unittest.main()
