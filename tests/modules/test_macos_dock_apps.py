import plistlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_dock_apps/library"))

from macos_dock_apps import DockAppsError, ensure_dock_apps

DOMAIN = "/tmp/scratch/com.example.dock"
INSTALLED = {
    "com.apple.Safari": ["/Applications/Safari.app"],
    "com.apple.systempreferences": ["/System/Applications/System Settings.app"],
    "com.example.Notes": ["/Applications/Notes Example.app"],
    "com.example.Copied": [
        "/Volumes/Backup/Applications/Copied.app",
        "/Applications/Copied.app",
        "/Applications/Copied.app/Contents/PlugIns/Helper.appex",
    ],
}
SAFARI = {
    "GUID": 2774654498,
    "tile-type": "file-tile",
    "tile-data": {
        "bundle-identifier": "com.apple.Safari",
        "book": b"\x00\x01bookmark",
        "file-label": "Safari",
        "file-type": 41,
        "file-data": {
            "_CFURLString": "file:///Applications/Safari.app/",
            "_CFURLStringType": 15,
        },
    },
}
NOTES = {
    "GUID": 2774654513,
    "tile-type": "file-tile",
    "tile-data": {
        "bundle-identifier": "com.example.Notes",
        "file-label": "Notes Example",
    },
}
SPACER = {"GUID": 2774654520, "tile-type": "spacer-tile", "tile-data": {}}
FOLDER = {"GUID": 2774654530, "tile-type": "directory-tile", "tile-data": {}}


class FakeMac:
    def __init__(self, tiles=None, installed=INSTALLED, write_rc=0):
        self.prefs = {"orientation": "left", "persistent-others": [FOLDER]}
        if tiles is not None:
            self.prefs["persistent-apps"] = list(tiles)
        self.installed = installed
        self.write_rc = write_rc
        self.writes = []
        self.searches = []

    def run(self, argv):
        if argv[0] == "/usr/bin/mdfind":
            self.searches.append(argv[1])
            bundle_id = argv[1].split("'")[1]
            return 0, "".join(f"{p}\n" for p in self.installed.get(bundle_id, [])), ""
        assert argv[0] == "/usr/bin/defaults" and argv[2] == DOMAIN, argv
        if argv[1] == "export":
            return 0, plistlib.dumps(self.prefs).decode(), ""
        assert argv[1] == "write" and argv[3:5] == ["persistent-apps", "-array"], argv
        self.writes.append(argv)
        if self.write_rc:
            return self.write_rc, "", "Could not write domain"
        self.prefs["persistent-apps"] = [plistlib.loads(x.encode()) for x in argv[5:]]
        return 0, "", ""

    def ids(self):
        return [
            t["tile-data"].get("bundle-identifier")
            for t in self.prefs["persistent-apps"]
        ]


def run(mac, apps, check_mode=False):
    return ensure_dock_apps(mac, DOMAIN, apps, check_mode)


WANTED = ["com.apple.Safari", "com.apple.systempreferences"]


class Apply(unittest.TestCase):
    def test_keeps_only_the_listed_apps_in_order(self):
        mac = FakeMac([NOTES, SPACER, SAFARI])
        result = run(mac, WANTED)
        self.assertTrue(result["changed"])
        self.assertEqual(mac.ids(), WANTED)

    def test_a_listed_app_keeps_its_tile(self):
        mac = FakeMac([NOTES, SAFARI])
        run(mac, WANTED)
        self.assertEqual(mac.prefs["persistent-apps"][0], SAFARI)

    def test_a_new_app_gets_a_tile_for_its_installed_path(self):
        mac = FakeMac([SAFARI])
        run(mac, WANTED)
        self.assertEqual(
            mac.prefs["persistent-apps"][1],
            {
                "tile-type": "file-tile",
                "tile-data": {
                    "bundle-identifier": "com.apple.systempreferences",
                    "file-label": "System Settings",
                    "file-type": 41,
                    "file-data": {
                        "_CFURLString": "file:///System/Applications/System%20Settings.app/",
                        "_CFURLStringType": 15,
                    },
                },
            },
        )

    def test_an_app_in_the_applications_folder_wins_over_a_copy(self):
        mac = FakeMac([])
        run(mac, ["com.example.Copied"])
        url = mac.prefs["persistent-apps"][0]["tile-data"]["file-data"]["_CFURLString"]
        self.assertEqual(url, "file:///Applications/Copied.app/")

    def test_reordering_is_a_change(self):
        mac = FakeMac([NOTES, SAFARI])
        result = run(mac, ["com.apple.Safari", "com.example.Notes"])
        self.assertTrue(result["changed"])
        self.assertEqual(mac.prefs["persistent-apps"], [SAFARI, NOTES])

    def test_folders_and_other_keys_stay(self):
        mac = FakeMac([NOTES])
        run(mac, WANTED)
        self.assertEqual(mac.prefs["persistent-others"], [FOLDER])
        self.assertEqual(mac.prefs["orientation"], "left")

    def test_a_missing_key_starts_from_an_empty_dock(self):
        mac = FakeMac()
        run(mac, WANTED)
        self.assertEqual(mac.ids(), WANTED)

    def test_an_empty_list_empties_the_dock(self):
        mac = FakeMac([SAFARI])
        run(mac, [])
        self.assertEqual(mac.prefs["persistent-apps"], [])

    def test_diff_shows_the_old_and_new_ids(self):
        mac = FakeMac([NOTES, SPACER])
        result = run(mac, WANTED)
        self.assertEqual(
            result["diff"],
            {
                "before": "com.example.Notes\nspacer-tile\n",
                "after": "com.apple.Safari\ncom.apple.systempreferences\n",
            },
        )

    def test_a_failed_write_is_an_error(self):
        mac = FakeMac([NOTES], write_rc=1)
        with self.assertRaisesRegex(DockAppsError, "defaults write failed"):
            run(mac, WANTED)


class NoChange(unittest.TestCase):
    def test_a_matching_dock_is_not_written(self):
        mac = FakeMac([SAFARI, NOTES])
        result = run(mac, ["com.apple.Safari", "com.example.Notes"])
        self.assertEqual(
            result,
            {"changed": False, "apps": ["com.apple.Safari", "com.example.Notes"]},
        )
        self.assertEqual(mac.writes, [])

    def test_check_mode_reports_and_writes_nothing(self):
        mac = FakeMac([NOTES])
        result = run(mac, WANTED, check_mode=True)
        self.assertTrue(result["changed"])
        self.assertEqual(
            result["diff"]["after"], "com.apple.Safari\ncom.apple.systempreferences\n"
        )
        self.assertEqual(mac.writes, [])
        self.assertEqual(mac.prefs["persistent-apps"], [NOTES])


class Refused(unittest.TestCase):
    def test_an_unknown_bundle_id_changes_nothing(self):
        mac = FakeMac([NOTES])
        with self.assertRaisesRegex(
            DockAppsError, r"^com\.example\.missing: no installed app"
        ):
            run(mac, ["com.apple.Safari", "com.example.missing"])
        self.assertEqual(mac.writes, [])
        self.assertEqual(mac.prefs["persistent-apps"], [NOTES])

    def test_a_plug_in_is_not_an_app(self):
        mac = FakeMac(
            [],
            installed={
                "com.example.Helper": [
                    "/Applications/Copied.app/Contents/PlugIns/Helper.appex"
                ]
            },
        )
        with self.assertRaisesRegex(DockAppsError, "com.example.Helper"):
            run(mac, ["com.example.Helper"])
        self.assertEqual(mac.writes, [])

    def test_a_malformed_bundle_id_is_not_searched(self):
        mac = FakeMac([NOTES])
        with self.assertRaisesRegex(DockAppsError, "no installed app"):
            run(mac, ["com.apple.Safari' || true || '"])
        self.assertEqual(mac.searches, [])
        self.assertEqual(mac.writes, [])

    def test_a_repeated_bundle_id_changes_nothing(self):
        mac = FakeMac([NOTES])
        with self.assertRaisesRegex(
            DockAppsError, "com.apple.Safari: listed more than once"
        ):
            run(mac, ["com.apple.Safari", "com.apple.Safari"])
        self.assertEqual(mac.writes, [])


class SecondRun(unittest.TestCase):
    def test_the_written_dock_matches_on_the_next_run(self):
        mac = FakeMac([NOTES, SPACER])
        run(mac, WANTED)
        self.assertFalse(run(mac, WANTED)["changed"])
        self.assertEqual(len(mac.writes), 1)


if __name__ == "__main__":
    unittest.main()
