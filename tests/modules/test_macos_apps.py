import json
import plistlib
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_apps/module_utils"))

import verified_download

sys.modules["ansible.module_utils.verified_download"] = verified_download

from app_install import ensure_app
import app_install
from test_verified_download import KEY, FakeMac, FakeWeb, app, setup


class InstallingMac(FakeMac):
    def copies(self):
        return [
            a
            for a in self.argvs
            if a[0] == verified_download.DITTO and ".mac-setup-new" in a[-1]
        ]


class Install(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.applications = self.root / "Applications"
        self.applications.mkdir()
        self.target = self.applications / "Example.app"

    def installed(self, version, extra=None):
        contents = self.target / "Contents"
        contents.mkdir(parents=True)
        info = {
            "CFBundleIdentifier": "com.example.app",
            "CFBundleShortVersionString": version,
        }
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        if extra:
            (contents / extra).write_text("old")

    def version_on_disk(self):
        with open(self.target / "Contents/Info.plist", "rb") as file:
            return plistlib.load(file)["CFBundleShortVersionString"]

    def run_app(self, check_mode=False, mac=None):
        self.mac = mac or InstallingMac()
        self.web = FakeWeb()
        return ensure_app(
            app(), setup(self.root, self.mac, self.web), self.applications, check_mode
        )

    def test_a_running_app_is_not_replaced(self):
        self.installed("1.0")
        mac = InstallingMac()
        mac.processes = f"{self.target}/Contents/MacOS/Example\n"
        with self.assertRaises(verified_download.Refused) as caught:
            self.run_app(mac=mac)
        self.assertIn("is running; quit it", str(caught.exception))
        self.assertEqual(self.version_on_disk(), "1.0")
        self.assertEqual(self.mac.copies(), [])

    def test_missing_app_is_installed(self):
        result = self.run_app()
        self.assertTrue(result["changed"])
        self.assertEqual(
            result["app"],
            {
                "id": "example",
                "state": "installed",
                "old_version": None,
                "new_version": "1.2",
            },
        )
        self.assertEqual(self.version_on_disk(), "1.2")
        self.assertEqual(
            result["diff"],
            {"before": "example: not installed\n", "after": "example: 1.2\n"},
        )
        self.assertEqual(
            sorted(p.name for p in self.applications.iterdir()), ["Example.app"]
        )

    def test_older_app_is_replaced_whole(self):
        self.installed("1.1", extra="stale-file")
        result = self.run_app()
        self.assertEqual(result["app"]["state"], "installed")
        self.assertEqual(result["diff"]["before"], "example: 1.1\n")
        self.assertEqual(self.version_on_disk(), "1.2")
        self.assertFalse((self.target / "Contents/stale-file").exists())
        self.assertEqual(
            sorted(p.name for p in self.applications.iterdir()), ["Example.app"]
        )

    def test_failed_promotion_restores_the_old_app(self):
        self.installed("1.1", extra="kept")
        rename = app_install.os.rename

        def fail_promotion(source, target):
            if str(source).endswith(".mac-setup-new"):
                raise OSError("promotion failed")
            return rename(source, target)

        with patch.object(app_install.os, "rename", side_effect=fail_promotion):
            with self.assertRaises((OSError, verified_download.Refused)):
                self.run_app()
        self.assertEqual(self.version_on_disk(), "1.1")
        self.assertTrue((self.target / "Contents/kept").exists())

    def test_an_interrupted_upgrade_recovers_the_backup_before_copying(self):
        self.installed("1.1", extra="kept")
        self.target.rename(self.target.with_name(".Example.app.mac-setup-old"))

        class FailedInstall(InstallingMac):
            def run(self, argv):
                if argv[0] == verified_download.DITTO and argv[-1].endswith(
                    ".mac-setup-new"
                ):
                    return 1, "", "copy failed"
                return super().run(argv)

        mac = FailedInstall()
        with self.assertRaises(verified_download.Refused):
            self.run_app(mac=mac)
        self.assertEqual(self.version_on_disk(), "1.1")
        self.assertTrue((self.target / "Contents/kept").exists())

    def test_an_ambiguous_backup_keeps_both_apps_and_refuses(self):
        self.installed("1.1")
        backup = self.target.with_name(".Example.app.mac-setup-old")
        shutil.copytree(self.target, backup)
        with self.assertRaises(verified_download.Refused):
            self.run_app()
        self.assertTrue((backup / "Contents/Info.plist").is_file())
        self.assertEqual(self.version_on_disk(), "1.1")

    def test_check_mode_leaves_an_existing_backup_untouched(self):
        self.installed("1.1")
        backup = self.target.with_name(".Example.app.mac-setup-old")
        self.target.rename(backup)
        result = self.run_app(check_mode=True)
        self.assertEqual(result["app"]["state"], "would_install")
        self.assertFalse(self.target.exists())
        self.assertTrue((backup / "Contents/Info.plist").is_file())

    def test_recovering_a_current_backup_reports_a_change_without_downloading(self):
        self.installed("1.2")
        backup = self.target.with_name(".Example.app.mac-setup-old")
        self.target.rename(backup)
        result = self.run_app()
        self.assertTrue(result["changed"])
        self.assertEqual(self.version_on_disk(), "1.2")
        self.assertEqual(self.web.requests, [])

    def test_failed_installed_version_check_restores_the_old_app(self):
        self.installed("1.1")
        read_version = app_install.installed_version

        def wrong_version(target):
            version = read_version(target)
            return "0.1" if version == "1.2" else version

        with patch.object(app_install, "installed_version", side_effect=wrong_version):
            with self.assertRaises(verified_download.Refused):
                self.run_app()
        self.assertEqual(self.version_on_disk(), "1.1")

    def test_equal_or_newer_app_is_left_alone_with_no_download_or_lookup(self):
        for version in ("1.2", "1.10", "2.0"):
            with self.subTest(version):
                shutil.rmtree(self.target, ignore_errors=True)
                self.installed(version)
                result = self.run_app()
                self.assertFalse(result["changed"])
                self.assertEqual(result["app"]["state"], "current")
                self.assertEqual(self.version_on_disk(), version)
                self.assertEqual(self.web.requests, [])
                self.assertEqual(self.mac.argvs, [])

    def test_check_mode_verifies_and_installs_nothing(self):
        self.installed("1.1")
        result = self.run_app(check_mode=True)
        self.assertTrue(result["changed"])
        self.assertEqual(result["app"]["state"], "would_install")
        self.assertEqual(
            result["diff"], {"before": "example: 1.1\n", "after": "example: 1.2\n"}
        )
        self.assertEqual(self.mac.copies(), [])
        self.assertEqual(self.version_on_disk(), "1.1")
        self.assertEqual(self.mac.mounted, set())

    def test_second_run_changes_nothing(self):
        self.run_app()
        result = self.run_app()
        self.assertFalse(result["changed"])
        self.assertEqual(self.web.requests, [])

    def test_failed_check_installs_nothing(self):
        mac = InstallingMac(fail={"spctl"})
        with self.assertRaises(verified_download.Refused) as caught:
            self.run_app(mac=mac)
        self.assertEqual(caught.exception.check, "spctl")
        self.assertEqual(mac.copies(), [])
        self.assertEqual(list(self.applications.iterdir()), [])

    def test_key_never_in_the_result(self):
        result = self.run_app()
        self.assertNotIn(KEY, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
