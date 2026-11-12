import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_commands/library"))

from macos_command_setting import SettingError, ensure_setting

PMSET = "/usr/bin/pmset"
SYSTEMSETUP = "/usr/sbin/systemsetup"
LAUNCHCTL = "/bin/launchctl"
SCUTIL = "/usr/sbin/scutil"
ADMIN_REFUSAL = "You need administrator access to run this tool... exiting!"
DISK_REFUSAL = "Turning Remote Login on or off requires Full Disk Access privileges."


class FakeTool:
    def __init__(self, read, state, show, change):
        self.read = read
        self.state = state
        self.show = show
        self.change = change
        self.argvs = []

    def run(self, argv):
        self.argvs.append(list(argv))
        if argv == self.read:
            return self.show(self.state)
        reply = self.change(self, argv)
        if reply is None:
            raise AssertionError(f"unexpected command {argv}")
        return reply

    def sets(self):
        return [argv for argv in self.argvs if argv != self.read]


def display_sleep(minutes):
    def show(state):
        return 0, f"System-wide power settings:\n displaysleep         {state}\n", ""

    def change(tool, argv):
        if argv[:3] == [PMSET, "-a", "displaysleep"] and len(argv) == 4:
            tool.state = argv[3]
            return 0, "", ""

    tool = FakeTool([PMSET, "-g", "custom"], minutes, show, change)
    entry = {
        "read": tool.read,
        "parse": r"displaysleep\s+(?P<value>\d+)",
        "mapping": {},
        "set_spec": [[PMSET, "-a", "displaysleep", "{value}"]],
    }
    return tool, entry


def remote_login(on, refuse_read="", refuse_set="", set_works=True):
    def show(state):
        if refuse_read:
            return 0, f"{refuse_read}\n", ""
        return 0, f"Remote Login: {'On' if state else 'Off'}\n", ""

    def change(tool, argv):
        if argv[0] != SYSTEMSETUP or "-setremotelogin" not in argv:
            return None
        if refuse_set:
            return 0, f"{refuse_set}\n", ""
        if set_works:
            tool.state = argv[-1] == "on"
        return 0, "", ""

    tool = FakeTool([SYSTEMSETUP, "-getremotelogin"], on, show, change)
    entry = {
        "read": tool.read,
        "parse": r"Remote Login: (?P<value>On|Off)",
        "mapping": {"true": "On", "false": "Off"},
        "set_spec": {
            "true": [[SYSTEMSETUP, "-setremotelogin", "on"]],
            "false": [[SYSTEMSETUP, "-f", "-setremotelogin", "off"]],
        },
    }
    return tool, entry


def service(state):
    label = "system/com.example.service"
    plist = "/Library/LaunchDaemons/com.example.service.plist"

    def show(state):
        if state == "loaded":
            return 0, f"{label} = {{\n\tstate = running\n}}\n", ""
        if state == "missing":
            return (
                113,
                "",
                'Could not find service "com.example.service" in domain for system\n',
            )
        return 5, "", "Unexpected failure\n"

    def change(tool, argv):
        if argv == [LAUNCHCTL, "load", "-w", plist]:
            tool.state = "loaded"
            return 0, "", ""
        if argv == [LAUNCHCTL, "unload", "-w", plist]:
            tool.state = "missing"
            return 0, "", ""

    tool = FakeTool([LAUNCHCTL, "print", label], state, show, change)
    entry = {
        "read": tool.read,
        "parse": r"^exit: (?P<value>0|113)$",
        "mapping": {"true": "0", "false": "113"},
        "set_spec": {
            "true": [[LAUNCHCTL, "load", "-w", plist]],
            "false": [[LAUNCHCTL, "unload", "-w", plist]],
        },
    }
    return tool, entry


def computer_name(name):
    def show(state):
        return 0, f"{state}\n", ""

    def change(tool, argv):
        if argv[:3] == [SCUTIL, "--set", "ComputerName"] and len(argv) == 4:
            tool.state = argv[3]
            return 0, "", ""

    tool = FakeTool([SCUTIL, "--get", "ComputerName"], name, show, change)
    entry = {
        "read": tool.read,
        "parse": r"\A(?P<value>.+)$",
        "mapping": {},
        "set_spec": [[SCUTIL, "--set", "ComputerName", "{value}"]],
    }
    return tool, entry


def ensure(tool, entry, value, check_mode=False):
    return ensure_setting(
        tool, value=value, check_mode=check_mode, **{"refuse": {}, **entry}
    )


class EqualValue(unittest.TestCase):
    def test_int_equal_runs_no_set(self):
        tool, entry = display_sleep("10")
        self.assertEqual(ensure(tool, entry, 10), {"changed": False})
        self.assertEqual(tool.sets(), [])

    def test_mapped_bool_equal_runs_no_set(self):
        tool, entry = remote_login(on=True)
        self.assertEqual(ensure(tool, entry, True), {"changed": False})
        self.assertEqual(tool.sets(), [])


class DifferentValue(unittest.TestCase):
    def test_int_value_is_one_argument(self):
        tool, entry = display_sleep("10")
        result = ensure(tool, entry, 15)
        self.assertTrue(result["changed"])
        self.assertEqual(tool.sets(), [[PMSET, "-a", "displaysleep", "15"]])
        self.assertEqual(tool.state, "15")

    def test_text_value_is_one_argument_and_never_shell(self):
        name = "Example Mac; touch /tmp/never"
        tool, entry = computer_name("Old Name")
        self.assertTrue(ensure(tool, entry, name)["changed"])
        self.assertEqual(tool.sets(), [[SCUTIL, "--set", "ComputerName", name]])
        self.assertEqual(tool.state, name)

    def test_bool_runs_the_set_list_for_that_value(self):
        tool, entry = remote_login(on=True)
        self.assertTrue(ensure(tool, entry, False)["changed"])
        self.assertEqual(tool.sets(), [[SYSTEMSETUP, "-f", "-setremotelogin", "off"]])
        self.assertFalse(tool.state)

    def test_second_run_changes_nothing(self):
        tool, entry = remote_login(on=False)
        ensure(tool, entry, True)
        self.assertEqual(ensure(tool, entry, True), {"changed": False})

    def test_set_that_does_not_hold_fails(self):
        tool, entry = remote_login(on=False, set_works=False)
        with self.assertRaisesRegex(SettingError, "shows false, not true"):
            ensure(tool, entry, True)

    def test_set_that_exits_nonzero_fails(self):
        tool, entry = display_sleep("10")
        tool.change = lambda tool, argv: (1, "", "pmset: bad argument\n")
        with self.assertRaisesRegex(SettingError, "exited 1: pmset: bad argument"):
            ensure(tool, entry, 15)

    def test_value_without_a_set_command_fails(self):
        tool, entry = remote_login(on=True)
        del entry["set_spec"]["false"]
        with self.assertRaisesRegex(SettingError, "no set command for the value false"):
            ensure(tool, entry, False)


class CheckAndDiff(unittest.TestCase):
    def test_check_mode_runs_no_set(self):
        tool, entry = display_sleep("10")
        result = ensure(tool, entry, 15, check_mode=True)
        self.assertTrue(result["changed"])
        self.assertEqual(tool.sets(), [])
        self.assertEqual(tool.state, "10")

    def test_diff_shows_public_values_before_and_after(self):
        tool, entry = remote_login(on=False)
        for check_mode in (True, False):
            result = ensure(tool, entry, True, check_mode=check_mode)
            self.assertEqual(result["diff"], {"before": "false\n", "after": "true\n"})

    def test_diff_shows_int_values(self):
        tool, entry = display_sleep("10")
        result = ensure(tool, entry, 15, check_mode=True)
        self.assertEqual(result["diff"], {"before": "10\n", "after": "15\n"})


class Refusals(unittest.TestCase):
    def test_admin_refusal_on_read_with_exit_0_fails_with_that_line(self):
        tool, entry = remote_login(on=False, refuse_read=ADMIN_REFUSAL)
        with self.assertRaises(SettingError) as raised:
            ensure(tool, entry, True)
        self.assertIn(ADMIN_REFUSAL, str(raised.exception))
        self.assertEqual(tool.sets(), [])

    def test_disk_access_refusal_on_set_with_exit_0_fails_with_that_line(self):
        tool, entry = remote_login(on=False, refuse_set=DISK_REFUSAL)
        with self.assertRaises(SettingError) as raised:
            ensure(tool, entry, True)
        self.assertIn(DISK_REFUSAL, str(raised.exception))
        self.assertFalse(tool.state)

    def test_error_99_is_refused_in_plain_words(self):
        tool, entry = remote_login(
            on=False,
            refuse_set="systemsetup[1:2] ### Error:-99 File:/x/InternetServices.m Line:395",
        )
        with self.assertRaises(SettingError) as raised:
            ensure(tool, entry, True)
        self.assertIn("no Full Disk Access", str(raised.exception))
        self.assertNotIn("InternetServices", str(raised.exception))


class ParseFailure(unittest.TestCase):
    def test_no_match_names_the_output(self):
        tool, entry = display_sleep("10")
        tool.show = lambda state: (0, "No such setting\n", "")
        with self.assertRaises(SettingError) as raised:
            ensure(tool, entry, 15)
        self.assertIn("No such setting", str(raised.exception))
        self.assertEqual(tool.sets(), [])

    def test_output_with_no_volume_control_is_refused(self):
        reference = yaml.safe_load((ROOT / "settings/macos/systemui.yml").read_text())
        command = reference["sound.output_volume"]["command"]
        shown = "output volume:missing value, input volume:missing value, alert volume:100, output muted:missing value\n"
        tool = FakeTool(
            command["read"], shown, lambda state: (0, state, ""), lambda *_: None
        )
        entry = {
            "read": command["read"],
            "parse": command["parse"],
            "mapping": {},
            "refuse": command["refuse"],
            "set_spec": command["set"],
        }
        with self.assertRaises(SettingError) as raised:
            ensure(tool, entry, 50)
        self.assertEqual(str(raised.exception), "this output has no volume control")
        self.assertEqual(tool.sets(), [])

    def test_pattern_without_value_group_fails(self):
        tool, entry = display_sleep("10")
        entry["parse"] = r"displaysleep\s+\d+"
        with self.assertRaisesRegex(SettingError, "no group named value"):
            ensure(tool, entry, 15)


class ExitCodeState(unittest.TestCase):
    def test_exit_113_reads_as_not_loaded(self):
        tool, entry = service("missing")
        self.assertEqual(ensure(tool, entry, False), {"changed": False})

    def test_exit_113_to_loaded_runs_load_and_confirms(self):
        tool, entry = service("missing")
        result = ensure(tool, entry, True)
        self.assertEqual(result["diff"], {"before": "false\n", "after": "true\n"})
        self.assertEqual(tool.sets(), [entry["set_spec"]["true"][0]])
        self.assertEqual(tool.state, "loaded")

    def test_exit_0_reads_as_loaded(self):
        tool, entry = service("loaded")
        self.assertEqual(ensure(tool, entry, True), {"changed": False})

    def test_other_exit_code_fails_naming_the_output(self):
        tool, entry = service("broken")
        with self.assertRaises(SettingError) as raised:
            ensure(tool, entry, True)
        self.assertIn("Unexpected failure", str(raised.exception))
        self.assertIn("exit: 5", str(raised.exception))
        self.assertEqual(tool.sets(), [])


if __name__ == "__main__":
    unittest.main()
