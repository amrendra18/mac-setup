import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_wallpaper/library"))

from macos_wallpaper import PICTURES, NoScreen, WallpaperError, ensure_color

DEFAULT = "file:///System/Library/CoreServices/DefaultDesktop.heic"
SYSTEM_BLACK = "/System/Library/Desktop Pictures/Solid Colors/Black.png"


class FakeMac:
    def __init__(self, screens, set_works=True, keeps=True, read_rc=0):
        self.screens = list(screens)
        self.set_works = set_works
        self.keeps = keeps
        self.read_rc = read_rc
        self.sets = []

    def run(self, argv):
        assert argv[:4] == ["/usr/bin/osascript", "-l", "JavaScript", "-e"], argv
        args = argv[5:]
        if args == ["read"]:
            if self.read_rc:
                return self.read_rc, "", "execution error: AppKit is not available\n"
            return 0, json.dumps(self.screens) + "\n", ""
        assert args[0] == "set" and len(args) == 2, args
        self.sets.append(args[1])
        if self.keeps:
            self.screens = [Path(args[1]).as_uri()] * len(self.screens)
        works = self.set_works
        if isinstance(works, bool):
            works = [works] * len(self.screens)
        return 0, json.dumps(works) + "\n", ""


class Mac(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.picture = Path(folder.name) / "Solid Colors" / "Black.png"
        self.picture.parent.mkdir()
        self.picture.write_bytes(b"png")
        self.black = self.picture.as_uri()

    def run_on(self, mac, check_mode=False, picture=None):
        return ensure_color(mac, str(picture or self.picture), check_mode)


class Apply(Mac):
    def test_one_screen_turns_black(self):
        mac = FakeMac([DEFAULT])
        result = self.run_on(mac)
        self.assertTrue(result["changed"])
        self.assertEqual(mac.sets, [str(self.picture)])
        self.assertEqual(mac.screens, [self.black])
        self.assertEqual(result["screens"], [self.black])

    def test_diff_names_the_old_and_new_picture_per_screen(self):
        result = self.run_on(FakeMac([DEFAULT, self.black]))
        self.assertEqual(
            result["diff"],
            {
                "before": f"screen 0: {DEFAULT}\nscreen 1: {self.black}\n",
                "after": f"screen 0: {self.black}\nscreen 1: {self.black}\n",
            },
        )

    def test_a_screen_with_no_picture_is_a_change(self):
        mac = FakeMac([""])
        result = self.run_on(mac)
        self.assertTrue(result["changed"])
        self.assertEqual(result["diff"]["before"], "screen 0: none\n")
        self.assertEqual(mac.screens, [self.black])

    def test_every_screen_black_changes_nothing(self):
        mac = FakeMac([self.black, self.black])
        result = self.run_on(mac)
        self.assertEqual(result, {"changed": False, "screens": [self.black] * 2})
        self.assertEqual(mac.sets, [])

    def test_second_run_changes_nothing(self):
        mac = FakeMac([DEFAULT])
        self.run_on(mac)
        self.assertFalse(self.run_on(mac)["changed"])
        self.assertEqual(len(mac.sets), 1)

    def test_check_mode_reports_the_change_and_sets_nothing(self):
        mac = FakeMac([DEFAULT])
        result = self.run_on(mac, check_mode=True)
        self.assertTrue(result["changed"])
        self.assertEqual(result["diff"]["after"], f"screen 0: {self.black}\n")
        self.assertEqual(mac.sets, [])
        self.assertEqual(mac.screens, [DEFAULT])


class Refusals(Mac):
    def assert_refused(self, mac, **kwargs):
        with self.assertRaises(WallpaperError) as caught:
            self.run_on(mac, **kwargs)
        return str(caught.exception)

    def test_no_visible_screen_is_refused_without_a_set(self):
        mac = FakeMac([])
        with self.assertRaisesRegex(NoScreen, "no screen is visible"):
            self.run_on(mac)
        self.assertEqual(mac.sets, [])

    def test_other_refusals_are_not_marked_as_no_screen(self):
        with self.assertRaises(WallpaperError) as caught:
            self.run_on(FakeMac([DEFAULT], set_works=False))
        self.assertNotIsInstance(caught.exception, NoScreen)

    def test_failed_read_is_refused_without_a_set(self):
        mac = FakeMac([DEFAULT], read_rc=1)
        self.assertIn("exited 1", self.assert_refused(mac))
        self.assertEqual(mac.sets, [])

    def test_a_set_macos_refuses_is_refused(self):
        self.assert_refused(FakeMac([DEFAULT], set_works=False))

    def test_a_set_refused_on_one_of_two_screens_is_refused(self):
        self.assert_refused(FakeMac([DEFAULT, DEFAULT], set_works=[True, False]))

    def test_a_picture_macos_does_not_keep_is_refused(self):
        mac = FakeMac([DEFAULT], keeps=False)
        self.assertIn(DEFAULT, self.assert_refused(mac))
        self.assertEqual(len(mac.sets), 1)

    def test_missing_picture_is_refused_before_any_command(self):
        mac = FakeMac([DEFAULT])
        self.assert_refused(mac, picture=self.picture.parent / "Gone.png")
        self.assertEqual(mac.sets, [])


@unittest.skipUnless(os.path.isfile(SYSTEM_BLACK), "needs the picture macOS ships")
class ShippedPicture(unittest.TestCase):
    def test_black_is_the_solid_colour_macos_ships(self):
        mac = FakeMac([DEFAULT])
        ensure_color(mac, PICTURES["black"], False)
        self.assertEqual(
            mac.screens,
            ["file:///System/Library/Desktop%20Pictures/Solid%20Colors/Black.png"],
        )


if __name__ == "__main__":
    unittest.main()
