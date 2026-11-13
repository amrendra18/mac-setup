import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TASK = (
    r"^TASK \[macos_commands : Set time\.zone\] \**\n(?P<result>\w+): \[example-mac\]"
)
OFF = 'echo "ls: $1: Operation not permitted" >&2; exit 1'
PLANTED = """---
- name: Record that this run turned on Full Disk Access
  ansible.builtin.set_fact:
    macos_permissions_results: {full_disk_access: {before: "off", after: "on"}}

- name: Planted failure
  ansible.builtin.fail:
    msg: planted
"""
FAIL_ONLY = PLANTED[PLANTED.index("- name: Planted failure") :]
AT_THE_SCREEN = {"ask": True, "why": "screen", "reason": ""}


def zone_now():
    link = subprocess.run(
        ["/usr/bin/readlink", "/etc/localtime"], capture_output=True, text=True
    ).stdout
    return link.strip().removeprefix("/var/db/timezone/zoneinfo/")


def other_zone():
    return "Europe/London" if zone_now() == "GMT" else "GMT"


def zone(name):
    return {"machine_settings": {"time.zone": name}}


def check_run(
    profile,
    ls_body,
    failing_role="",
    open_body='echo "$@" >>"$0.log"',
    planted=PLANTED,
    play_vars=(),
    playbook="playbooks/site.yml",
):
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        host = work / "inventory/host_vars/example-mac"
        host.mkdir(parents=True)
        (work / "inventory/hosts.yml").write_text(
            (ROOT / "inventory/example/hosts.yml").read_text()
        )
        (host / "profile.yml").write_text(yaml.safe_dump(profile))
        for name, body in [("ls", ls_body), ("open", open_body)]:
            (work / name).write_text(f"#!/bin/sh\n{body}\n")
            (work / name).chmod(0o755)
        switches = {
            "permissions_ls": str(work / "ls"),
            "permissions_open": str(work / "open"),
            "by_hand_open": str(work / "open"),
            "ansible_become": False,
        }
        (work / "sidebar-helper").write_text("#!/bin/sh\nexit 99\n")
        (work / "site.yml").write_text(
            yaml.safe_dump(
                [
                    {
                        "import_playbook": str(ROOT / playbook),
                        "vars": {
                            "macos_finder_sidebar_helper": str(work / "sidebar-helper"),
                            **dict(play_vars),
                        },
                    }
                ]
            )
        )
        if failing_role:
            tasks = work / "roles" / failing_role / "tasks"
            tasks.mkdir(parents=True)
            (tasks / "main.yml").write_text(planted)
        done = subprocess.run(
            [
                str(ROOT / ".local/venv/bin/ansible-playbook"),
                "-i",
                str(work / "inventory"),
                "-e",
                json.dumps(switches),
                str(work / "site.yml"),
                "--check",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"),
                "ANSIBLE_NOCOLOR": "1",
                "ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.default",
                "TERM_PROGRAM": "Apple_Terminal",
                "ANSIBLE_ROLES_PATH": f"{work / 'roles'}:{ROOT / 'roles'}",
            },
        )
        opened = work / "open.log"
        done.opened = opened.read_text().splitlines() if opened.exists() else []
        return done


def recap(out):
    line = re.search(r"^example-mac +: (.*)$", out, re.M).group(1)
    return {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", line)}


class TimeZoneWithoutFullDiskAccess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.done = check_run(zone(other_zone()), OFF)
        cls.out = cls.done.stdout

    def test_the_run_ends_with_exit_0_and_nothing_failed(self):
        self.assertEqual(self.done.returncode, 0, self.out + self.done.stderr)
        self.assertEqual(recap(self.out)["failed"], 0)

    def test_the_time_zone_task_is_an_ansible_skip(self):
        self.assertEqual(re.search(TASK, self.out, re.M)["result"], "skipping")
        self.assertGreaterEqual(recap(self.out)["skipped"], 1)

    def test_the_summary_names_the_skip_once(self):
        self.assertEqual(
            len(re.findall(r"skipped time\.zone: needs Full Disk Access, ", self.out)),
            1,
        )
        self.assertIn("Full Disk Access: off'", self.out)

    def test_it_lists_the_ask_and_asks_nothing(self):
        self.assertIn("would ask for Full Disk Access, which time.zone needs", self.out)
        self.assertRegex(
            self.out,
            r"(?m)^TASK \[macos_permissions : Ask for Full Disk Access\] \**\n"
            r"skipping: ",
        )
        self.assertNotIn("TASK [macos_permissions : Open the page", self.out)
        self.assertEqual(self.done.opened, [])


class MailFolderMissing(unittest.TestCase):
    def test_the_fallback_folder_decides(self):
        missing_mail = (
            'case "$1" in */Library/Mail) echo "ls: $1: No such file or directory" >&2;; '
            '"/Library/Application Support/com.apple.TCC") '
            'echo "ls: $1: Operation not permitted" >&2;; esac; exit 1'
        )
        done = check_run(zone(other_zone()), missing_mail)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(re.search(TASK, done.stdout, re.M)["result"], "skipping")
        self.assertIn("Full Disk Access: off'", done.stdout)


class TimeZoneThatAlreadyMatches(unittest.TestCase):
    def test_a_matching_value_is_ok_and_not_named_as_skipped(self):
        done = check_run(zone(zone_now()), OFF)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(re.search(TASK, done.stdout, re.M)["result"], "ok")
        self.assertNotIn("skipped time.zone", done.stdout)
        self.assertIn(
            "Full Disk Access: off, but every setting that needs it already matches",
            done.stdout,
        )


class TimeZoneWithFullDiskAccess(unittest.TestCase):
    def test_the_time_zone_task_runs(self):
        done = check_run(zone(other_zone()), "exit 0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(re.search(TASK, done.stdout, re.M)["result"], "changed")
        self.assertIn("Full Disk Access: on before this run, and left on", done.stdout)
        self.assertNotIn("skipped time.zone", done.stdout)


class StopInThisAccountOrMachinePlay(unittest.TestCase):
    def assert_take_back_ran(self, done, stopped_task):
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(
            done.opened,
            [
                "x-apple.systempreferences:com.apple.settings.PrivacySecurity"
                ".extension?Privacy_AllFiles"
            ],
        )
        self.assertIn(
            "TASK [macos_permissions : Wait while you change Full Disk Access]",
            done.stdout,
        )
        self.assertIn("Full Disk Access was on only for this run", done.stdout)
        self.assertRegex(done.stdout, f"the run stopped early at {stopped_task}: ")

    def test_a_failure_in_the_this_account_play_still_takes_back_and_ends_with_exit_2(
        self,
    ):
        done = check_run(zone(other_zone()), OFF, "macos_wallpaper")
        self.assert_take_back_ran(done, "Planted failure")
        self.assertNotIn(
            "TASK [Set the machine settings that need a command]", done.stdout
        )

    def test_a_failure_in_the_machine_play_still_takes_back_and_ends_with_exit_2(
        self,
    ):
        done = check_run(zone(other_zone()), OFF, "macos_apps")
        self.assert_take_back_ran(done, "Planted failure")
        self.assertIn(
            "TASK [Set the machine settings that need a command]", done.stdout
        )

    def test_a_failing_open_still_says_to_turn_it_off(self):
        done = check_run(
            zone(other_zone()),
            OFF,
            "macos_apps",
            open_body='echo "$@" >>"$0.log"; exit 1',
        )
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn(
            "TASK [macos_permissions : Say what to change for Full Disk Access]",
            done.stdout,
        )
        self.assertIn("Full Disk Access was on only for this run", done.stdout)
        self.assertRegex(done.stdout, "the run stopped early at Planted failure: ")
        self.assertIn(
            "msg: could not open the page; open System Settings > "
            "Privacy & Security > Full Disk Access yourself",
            " ".join(done.stdout.split()),
        )


class StopBeforeTheSettingRan(unittest.TestCase):
    def test_the_summary_makes_no_claim_about_a_setting_nobody_read(self):
        done = check_run(zone(zone_now()), OFF, "macos_wallpaper", planted=FAIL_ONLY)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertNotIn("TASK [macos_commands : Set time.zone]", done.stdout)
        self.assertRegex(done.stdout, "the run stopped early at Planted failure: ")
        self.assertIn("- 'Full Disk Access: off'", done.stdout)
        self.assertNotIn("already matches", done.stdout)


class FinderSidebarWithoutFolderAccess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.done = check_run(
            {"default_settings": {"finder.sidebar_favorites": ["~", "~/Desktop"]}},
            OFF,
            play_vars={"macos_permissions_run_mode": AT_THE_SCREEN},
        )
        cls.out = cls.done.stdout

    def test_the_run_ends_with_exit_0_and_nothing_failed(self):
        self.assertEqual(self.done.returncode, 0, self.out + self.done.stderr)
        self.assertEqual(recap(self.out)["failed"], 0)

    def test_the_sidebar_step_is_skipped_without_running_the_helper(self):
        self.assertRegex(
            self.out,
            r"(?m)^TASK \[macos_finder_sidebar : Set the Finder sidebar\] \**\n"
            r"skipping: ",
        )

    def test_the_summary_names_the_skip_once(self):
        lines = " ".join(self.out.split())
        self.assertIn("- 'Desktop folder access: off'", lines)
        self.assertEqual(
            lines.count(
                "skipped finder.sidebar_favorites: needs Desktop folder access, "
                "which is still off; turn on Terminal in System Settings > "
                "Privacy & Security > Files & Folders and run again"
            ),
            1,
        )


CLICKS = {
    "default_settings": {"appearance.icon_style": "dark"},
    "machine_settings": {"network.bluetooth_power": False},
}
TAKE_BACK = (
    "TASK [macos_permissions : Take back each permission this run turned on for itself]"
)
SUMMARY = "TASK [profile : Show what happens next]"
LISTED_NOTE = (
    "2 settings need a click: at the Mac's screen, "
    "run playbooks/site.yml again or playbooks/by-hand.yml"
)


WALK_OPENS = [
    "x-apple.systempreferences:com.apple.Appearance-Settings.extension",
    "x-apple.systempreferences:com.apple.BluetoothSettings",
]


def between_take_back_and_summary(out):
    return out[out.index(TAKE_BACK) : out.index(SUMMARY)]


class ClickPagesAtTheEndOfACheckRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.done = check_run(CLICKS, OFF)
        cls.out = cls.done.stdout

    def test_the_run_ends_with_exit_0_and_opens_nothing(self):
        self.assertEqual(self.done.returncode, 0, self.out + self.done.stderr)
        self.assertEqual(self.done.opened, [])

    def test_each_click_page_is_listed_after_the_take_back_and_before_the_summary(
        self,
    ):
        listed = re.findall(r"(?m)^ +msg: '?would open ", self.out)
        self.assertEqual(len(listed), 2)
        self.assertEqual(
            len(
                re.findall(
                    r"(?m)^ +msg: '?would open ",
                    between_take_back_and_summary(self.out),
                )
            ),
            2,
        )

    def test_the_summary_counts_the_listed_pages(self):
        self.assertIn(LISTED_NOTE, " ".join(self.out.split()).replace("''", "'"))


class ClickPagesAtTheScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.done = check_run(
            CLICKS,
            OFF,
            "macos_wallpaper",
            planted=FAIL_ONLY,
            play_vars={"macos_permissions_run_mode": AT_THE_SCREEN},
        )
        cls.out = cls.done.stdout

    def test_each_page_opens_once(self):
        self.assertEqual(self.done.opened, WALK_OPENS)
        self.assertNotIn("could not open the page", self.out)

    def test_each_page_is_walked_after_a_stop_and_before_the_summary(self):
        walked = between_take_back_and_summary(self.out)
        for setting_id in ("appearance.icon_style", "network.bluetooth_power"):
            self.assertIn(
                f"TASK [macos_by_hand : Wait while you choose {setting_id}]", walked
            )
        self.assertNotRegex(self.out, r"(?m)^ +msg: '?would open ")

    def test_the_summary_reports_the_stop_and_no_click_left(self):
        self.assertEqual(self.done.returncode, 2, self.out + self.done.stderr)
        self.assertRegex(self.out, "the run stopped early at Planted failure: ")
        self.assertNotIn("need a click", self.out)


class ByHandCheckRun(unittest.TestCase):
    def test_it_lists_each_page_and_opens_none(self):
        done = check_run(CLICKS, OFF, playbook="playbooks/by-hand.yml")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(re.findall(r"(?m)^ +msg: '?would open ", done.stdout)), 2)
        self.assertNotIn("TASK [macos_by_hand : Wait while you choose", done.stdout)
        self.assertEqual(done.opened, [])


class AFailingOpenAtAClickPage(unittest.TestCase):
    def test_the_walk_goes_on_and_the_summary_still_runs(self):
        done = check_run(
            CLICKS,
            OFF,
            open_body='echo "$@" >>"$0.log"; exit 1',
            play_vars={"macos_permissions_run_mode": AT_THE_SCREEN},
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.opened, WALK_OPENS)
        self.assertIn(SUMMARY, done.stdout)
        self.assertEqual(recap(done.stdout)["failed"], 0)
        for where in ("Appearance", "Bluetooth"):
            self.assertIn(
                f"msg: could not open the page; go to System Settings > {where} yourself",
                " ".join(done.stdout.split()),
            )


if __name__ == "__main__":
    unittest.main()
