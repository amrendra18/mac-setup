import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_permissions/filter_plugins"))

from permissions import (
    macos_permissions_app,
    macos_permissions_ask_lines,
    macos_permissions_list_line,
    macos_permissions_mode,
    macos_permissions_notes,
    macos_permissions_blocked,
    macos_permissions_state,
    macos_permissions_take_back,
    macos_permissions_take_back_lines,
)

TABLE = yaml.safe_load((ROOT / "settings/macos/permissions/table.yml").read_text())
DISK = {
    "id": "full_disk_access",
    **TABLE["full_disk_access"],
    "settings": ["time.zone"],
    "skips": ["time.zone"],
}
DESKTOP = {
    "id": "desktop_folder",
    **TABLE["desktop_folder"],
    "settings": ["finder.sidebar_favorites"],
    "skips": ["finder.sidebar_favorites", "finder.sidebar_hide"],
}
SCREEN = macos_permissions_mode(["", "", ""], False)
SSH = macos_permissions_mode(["192.0.2.1 50000 192.0.2.2 22", "", ""], False)
CHECK = macos_permissions_mode(["", "", ""], True)
NOT_PERMITTED = "ls: Library/Mail: Operation not permitted\n"


def ls(rc, stderr=""):
    return {"rc": rc, "stdout": "", "stderr": stderr, "changed": False}


class Mode(unittest.TestCase):
    def test_only_a_run_at_the_screen_asks(self):
        self.assertTrue(SCREEN["ask"])
        self.assertFalse(SSH["ask"])
        self.assertFalse(CHECK["ask"])
        self.assertFalse(macos_permissions_mode(["", "", "/dev/ttys001"], False)["ask"])

    def test_the_reason_names_ssh_before_check(self):
        self.assertIn("over SSH", macos_permissions_mode(["x", "", ""], True)["reason"])
        self.assertIn("--check", CHECK["reason"])
        self.assertEqual(SCREEN["reason"], "")

    def test_the_app_is_named_from_term_program(self):
        self.assertEqual(macos_permissions_app("Apple_Terminal"), "Terminal")
        self.assertEqual(macos_permissions_app("iTerm.app"), "iTerm")
        self.assertEqual(macos_permissions_app(""), "the app you run mac-setup in")


class State(unittest.TestCase):
    def test_a_read_that_works_is_on(self):
        self.assertEqual(macos_permissions_state(ls(0)), "on")

    def test_operation_not_permitted_is_off(self):
        self.assertEqual(macos_permissions_state(ls(1, NOT_PERMITTED)), "off")

    def test_another_failure_or_a_skipped_test_is_unknown(self):
        self.assertIsNone(macos_permissions_state(ls(1, "ls: x: No such file")))
        self.assertIsNone(macos_permissions_state({"skipped": True, "changed": False}))


class Lines(unittest.TestCase):
    def test_the_full_disk_access_ask_names_the_pane_and_the_take_back(self):
        lines = macos_permissions_ask_lines(
            {**DISK, "settings": ["time.zone", "sharing.remote_login"]}, "Terminal"
        )
        self.assertEqual(
            lines,
            [
                "Full Disk Access, which time.zone and sharing.remote_login need",
                "In System Settings > Privacy & Security > Full Disk Access, "
                "which opens now, turn on Terminal.",
                "This run asks you to turn it off again at the end. "
                "If you stop the run with Control-C, turn it off there yourself.",
            ],
        )

    def test_the_folder_ask_says_to_click_allow(self):
        self.assertEqual(
            macos_permissions_ask_lines(DESKTOP, "Terminal"),
            [
                "Desktop folder access, which finder.sidebar_favorites needs",
                "Click Allow when macOS asks whether Terminal may use your "
                "Desktop folder. If you answered before, macOS asks nothing now.",
            ],
        )

    def test_list_lines(self):
        app = "the app you run mac-setup in"
        self.assertEqual(
            macos_permissions_list_line(DISK, "off", app),
            "would ask for Full Disk Access, which time.zone needs: in System "
            "Settings > Privacy & Security > Full Disk Access, turn on the app "
            "you run mac-setup in",
        )
        self.assertEqual(
            macos_permissions_list_line(DISK, "on", app),
            "Full Disk Access is on, so a run would not ask for it",
        )
        self.assertEqual(
            macos_permissions_list_line(DESKTOP, None, app),
            "would ask for Desktop folder access, which finder.sidebar_favorites "
            "needs: click Allow when macOS asks whether the app you run mac-setup "
            "in may use your Desktop folder",
        )
        self.assertEqual(
            macos_permissions_list_line(DISK, None, app),
            "could not test Full Disk Access, so a run tries time.zone anyway",
        )

    def test_the_take_back_says_to_turn_it_off(self):
        self.assertEqual(
            macos_permissions_take_back_lines(DISK, "Terminal"),
            [
                "Full Disk Access was on only for this run",
                "In System Settings > Privacy & Security > Full Disk Access, "
                "which opens now, turn off Terminal.",
            ],
        )


class Outcome(unittest.TestCase):
    def test_a_permission_still_off_skips_its_settings(self):
        results = {
            "full_disk_access": {"before": "off", "after": "off"},
            "desktop_folder": {"after": "on"},
        }
        self.assertEqual(
            macos_permissions_blocked([DISK, DESKTOP], results), ["time.zone"]
        )

    def test_a_folder_still_off_skips_the_whole_sidebar_step(self):
        self.assertEqual(
            macos_permissions_blocked([DESKTOP], {"desktop_folder": {"after": "off"}}),
            ["finder.sidebar_favorites", "finder.sidebar_hide"],
        )

    def test_an_untested_permission_skips_nothing(self):
        self.assertEqual(
            macos_permissions_blocked(
                [DISK, DESKTOP], {"desktop_folder": {"after": None}}
            ),
            [],
        )

    def test_only_a_temporary_permission_this_run_turned_on_is_taken_back(self):
        disk_granted = {"full_disk_access": {"before": "off", "after": "on"}}
        self.assertEqual(macos_permissions_take_back([DISK], disk_granted), [DISK])
        for results in [
            {"full_disk_access": {"before": "on", "after": "on"}},
            {"full_disk_access": {"before": "off", "after": "off"}},
            {},
        ]:
            with self.subTest(results):
                self.assertEqual(macos_permissions_take_back([DISK], results), [])
        desktop_granted = {"desktop_folder": {"before": "off", "after": "on"}}
        self.assertEqual(macos_permissions_take_back([DESKTOP], desktop_granted), [])


class Notes(unittest.TestCase):
    SKIPPED = dict.fromkeys(
        ["time.zone", "finder.sidebar_favorites", "finder.sidebar_hide"], "skipped"
    )
    OFF = {"full_disk_access": {"before": "off", "after": "off"}}

    def notes(self, results, mode=SCREEN, permissions=(DISK,), outcomes=SKIPPED):
        return macos_permissions_notes(
            list(permissions), results, outcomes, mode, "Terminal"
        )

    def test_only_a_setting_the_run_read_and_found_matching_says_it_matches(self):
        self.assertEqual(
            self.notes(self.OFF, outcomes={"time.zone": "matched"}),
            ["Full Disk Access: off, but every setting that needs it already matches"],
        )

    def test_a_setting_the_run_never_read_makes_no_claim(self):
        self.assertEqual(self.notes(self.OFF, outcomes={}), ["Full Disk Access: off"])

    def test_one_setting_not_read_makes_no_claim_for_the_others(self):
        both = {**DISK, "settings": ["time.zone", "sharing.remote_login"]}
        both["skips"] = both["settings"]
        self.assertEqual(
            self.notes(self.OFF, permissions=[both], outcomes={"time.zone": "matched"}),
            ["Full Disk Access: off"],
        )

    def test_over_ssh_the_skip_says_to_run_at_the_screen(self):
        self.assertEqual(
            self.notes({"full_disk_access": {"before": "off", "after": "off"}}, SSH),
            [
                "Full Disk Access: off",
                "skipped time.zone: needs Full Disk Access, which a run over SSH "
                "does not have; run mac-setup at the Mac's screen to set it",
            ],
        )

    def test_a_check_run_says_a_run_at_the_screen_asks(self):
        self.assertEqual(
            self.notes({"full_disk_access": {"before": "off", "after": "off"}}, CHECK)[
                1
            ],
            "skipped time.zone: needs Full Disk Access, which is off; a run at "
            "the Mac's screen without --check asks for it",
        )

    def test_still_off_at_the_screen_says_where_to_turn_it_on(self):
        self.assertEqual(
            self.notes({"full_disk_access": {"before": "off", "after": "off"}})[1],
            "skipped time.zone: needs Full Disk Access, which is still off; turn "
            "on Terminal in System Settings > Privacy & Security > Full Disk "
            "Access and run again",
        )

    def test_each_state_has_one_line(self):
        for results, line in [
            (
                {"before": "off", "after": "on", "end": "off"},
                "Full Disk Access: on for this run, then off again",
            ),
            (
                {"before": "off", "after": "on", "end": "on"},
                "Full Disk Access: still on; turn off Terminal in System Settings "
                "> Privacy & Security > Full Disk Access",
            ),
            (
                {"before": "on", "after": "on"},
                "Full Disk Access: on before this run, and left on",
            ),
            (
                {"before": None, "after": None},
                "Full Disk Access: its test failed for another reason, so this "
                "run tried time.zone anyway",
            ),
        ]:
            with self.subTest(line):
                self.assertEqual(self.notes({"full_disk_access": results}), [line])

    def test_a_setting_skipped_with_the_sidebar_step_says_why(self):
        self.assertEqual(
            self.notes({"desktop_folder": {"after": "off"}}, permissions=[DESKTOP]),
            [
                "Desktop folder access: off",
                "skipped finder.sidebar_favorites: needs Desktop folder access, which "
                "is still off; turn on Terminal in System Settings > Privacy & "
                "Security > Files & Folders and run again",
                "skipped finder.sidebar_hide: set in one step with "
                "finder.sidebar_favorites",
            ],
        )

    def test_a_folder_that_is_on_does_not_hide_the_skip_of_another(self):
        downloads = {
            **DESKTOP,
            "id": "downloads_folder",
            "name": "Downloads folder access",
        }
        lines = self.notes(
            {"desktop_folder": {"after": "on"}, "downloads_folder": {"after": "off"}},
            permissions=[DESKTOP, downloads],
        )
        self.assertIn(
            "skipped finder.sidebar_favorites: needs Downloads folder access, which "
            "is still off; turn on Terminal in System Settings > Privacy & "
            "Security > Files & Folders and run again",
            lines,
        )

    def test_folder_lines(self):
        self.assertEqual(
            self.notes({"desktop_folder": {"after": "on"}}, permissions=[DESKTOP]),
            ["Desktop folder access: on, and it stays on"],
        )
        self.assertEqual(
            self.notes({"desktop_folder": {"after": None}}, SSH, [DESKTOP]),
            [
                "Desktop folder access: not tested, because the test shows "
                "macOS's dialog; a run at the Mac's screen asks for it"
            ],
        )


if __name__ == "__main__":
    unittest.main()
