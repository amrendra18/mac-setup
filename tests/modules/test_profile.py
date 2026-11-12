import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/profile/filter_plugins"))

from profile import profile_problems, profile_summary, profile_work

FILES = {
    path.name: yaml.safe_load(path.read_text())
    for path in sorted((ROOT / "settings/macos").glob("*.yml"))
}

VALID = {"dock.position": "left", "dock.tile_size": 32, "dock.show_recents": False}

CASES = [
    ("dock.tile_size", 16, True),
    ("dock.tile_size", 128, True),
    ("dock.tile_size", 15, False),
    ("dock.tile_size", 129, False),
    ("dock.tile_size", 32.0, False),
    ("dock.tile_size", "32", False),
    ("dock.tile_size", True, False),
    ("dock.show_recents", True, True),
    ("dock.show_recents", False, True),
    ("dock.show_recents", 1, False),
    ("dock.show_recents", "false", False),
    ("dock.position", "bottom", True),
    ("dock.position", "middle", False),
    ("dock.position", "unset", True),
    ("dock.tile_size", "unset", True),
    ("dock.postion", "left", False),
]


def entry(**fields):
    base = {
        "scope": "user",
        "route": "defaults",
        "values": {"type": "bool"},
        "writes": [{"domain": "com.example.test", "key": "Flag", "type": "bool"}],
        "takes_effect": "live",
        "lockout_risk": False,
    }
    return {**base, **fields}


TEST_FILES = {
    "test.yml": {
        "test.flag": entry(),
        "test.machine": entry(scope="machine"),
        "test.risky": entry(lockout_risk=True),
        "test.ratio": entry(values={"type": "float", "min": 0.5, "max": 2}),
        "test.label": entry(values={"type": "text", "pattern": "[A-Z][a-z]+"}),
        "test.logout": entry(takes_effect="logout"),
        "test.click": entry(route="by_hand", writes=[]),
        "test.later": entry(
            route="not_yet", not_yet_reason="the value is a list", writes=[]
        ),
        "test.apps": entry(route="dock_apps", values={"type": "bundle_ids"}, writes=[]),
        "test.mapped": entry(
            values=["small", "large"],
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "Size",
                    "type": "int",
                    "map": {"small": 1, "large": 3},
                },
                {
                    "domain": "com.example.test",
                    "key": "Panel",
                    "type": "dict",
                    "host": "currentHost",
                    "dict_mode": "add",
                    "map": {"small": {"Wide": False}, "large": {"Wide": True}},
                },
            ],
        ),
    }
}


def subjects(problems):
    return [line.split(": ", 1)[0] for line in problems]


def account(name, **fields):
    return {"name": name, "full_name": f"{name.title()} Example", **fields}


class ProfileProblems(unittest.TestCase):
    def test_each_value(self):
        for setting_id, value, allowed in CASES:
            with self.subTest(setting_id=setting_id, value=value):
                problems = profile_problems(
                    {"default_settings": {setting_id: value}}, FILES
                )
                self.assertEqual(subjects(problems), [] if allowed else [setting_id])

    def test_float_and_text_values(self):
        for setting_id, value, allowed in [
            ("test.ratio", 0.5, True),
            ("test.ratio", 2, True),
            ("test.ratio", 2.5, False),
            ("test.ratio", True, False),
            ("test.label", "Small", True),
            ("test.label", "small", False),
            ("test.label", 3, False),
        ]:
            with self.subTest(setting_id=setting_id, value=value):
                problems = profile_problems(
                    {"default_settings": {setting_id: value}}, TEST_FILES
                )
                self.assertEqual(subjects(problems), [] if allowed else [setting_id])

    def test_one_line_per_problem(self):
        profile = {
            "default_settings": {
                "dock.postion": "left",
                "dock.tile_size": 129,
                "dock.show_recents": "no",
            }
        }
        self.assertEqual(
            subjects(profile_problems(profile, FILES)),
            ["dock.postion", "dock.tile_size", "dock.show_recents"],
        )

    def test_valid_profile(self):
        profile = {
            "default_settings": VALID,
            "accounts": [
                account("setuptest1"),
                account("setuptest2", admin=True, settings={"dock.position": "unset"}),
            ],
            "machine_settings": {},
            "allow_lockout_risk": False,
            "account_passwords": {"setuptest1": "x"},
        }
        self.assertEqual(profile_problems(profile, FILES), [])

    def test_missing_and_empty_sections_are_empty(self):
        self.assertEqual(profile_problems({}, FILES), [])
        empty = dict.fromkeys(
            ["default_settings", "accounts", "machine_settings", "account_passwords"]
        )
        self.assertEqual(profile_problems(empty, FILES), [])

    def test_sections_of_the_wrong_kind(self):
        profile = {
            "default_settings": ["dock.position"],
            "accounts": {"name": "setuptest1"},
            "machine_settings": "none",
            "allow_lockout_risk": "yes",
            "account_passwords": ["x"],
        }
        self.assertEqual(
            subjects(profile_problems(profile, FILES)),
            [
                "allow_lockout_risk",
                "default_settings",
                "machine_settings",
                "accounts",
                "account_passwords",
            ],
        )

    def test_settings_under_an_account_are_checked(self):
        profile = {
            "accounts": [
                account(
                    "setuptest1",
                    settings={"dock.postion": "left", "dock.tile_size": 200},
                )
            ]
        }
        self.assertEqual(
            subjects(profile_problems(profile, FILES)),
            ["account setuptest1, dock.postion", "account setuptest1, dock.tile_size"],
        )

    def test_scope_must_match_the_section(self):
        profile = {
            "default_settings": {"test.machine": True},
            "accounts": [account("setuptest1", settings={"test.machine": True})],
            "machine_settings": {"test.flag": True, "test.machine": True},
        }
        self.assertEqual(
            subjects(profile_problems(profile, TEST_FILES)),
            [
                "test.machine",
                "machine_settings, test.flag",
                "account setuptest1, test.machine",
            ],
        )

    def test_unknown_id_under_machine_settings(self):
        problems = profile_problems({"machine_settings": {"test.nope": 1}}, TEST_FILES)
        self.assertEqual(subjects(problems), ["machine_settings, test.nope"])

    def test_repeated_account_name_is_one_line(self):
        profile = {
            "accounts": [
                account("setuptest1"),
                account("setuptest1"),
                account("setuptest1"),
            ]
        }
        self.assertEqual(
            subjects(profile_problems(profile, FILES)), ["account setuptest1"]
        )

    def test_account_fields(self):
        profile = {
            "accounts": [
                {"full_name": "No Name"},
                {"name": "Bad Name", "full_name": "Bad"},
                {"name": "setuptest1"},
                account("setuptest2", full_name="  "),
                account("setuptest3", admin="no"),
                account("setuptest4", settings=["dock.position"]),
                account("setuptest5", admn=True),
                "setuptest6",
            ]
        }
        self.assertEqual(
            subjects(profile_problems(profile, FILES)),
            [
                "account item 1",
                "account item 2",
                "account setuptest1",
                "account setuptest2",
                "account setuptest3",
                "account setuptest4",
                "account setuptest5",
                "account item 8",
            ],
        )

    def test_empty_password(self):
        profile = {
            "accounts": [account("setuptest1"), account("setuptest2")],
            "account_passwords": {"setuptest1": "", "setuptest2": None},
        }
        problems = profile_problems(profile, FILES)
        self.assertEqual(
            subjects(problems), ["account setuptest1", "account setuptest2"]
        )

    def test_password_never_in_a_problem_line(self):
        secret = "Zq7-secret-value"
        profile = {
            "accounts": [{"name": "setuptest1"}],
            "account_passwords": {"setuptest1": secret, "setuptest2": ""},
        }
        problems = profile_problems(profile, FILES)
        self.assertTrue(problems)
        self.assertFalse(any(secret in line for line in problems))

    def test_lockout_risk_needs_the_opt_in(self):
        risky = {"default_settings": {"test.risky": True}}
        self.assertEqual(subjects(profile_problems(risky, TEST_FILES)), ["test.risky"])
        allowed = {**risky, "allow_lockout_risk": True}
        self.assertEqual(profile_problems(allowed, TEST_FILES), [])

    def test_a_setting_that_needs_a_click_is_counted(self):
        files = {"t.yml": {"t.siri": entry(needs=["click"])}}
        work = profile_work({"default_settings": {"t.siri": True}}, files, "runner")
        self.assertEqual(work["counts"]["need_click"], 1)

    def test_not_yet_setting_is_refused_with_its_reason(self):
        problems = profile_problems(
            {"default_settings": {"test.later": True}}, TEST_FILES
        )
        self.assertEqual(subjects(problems), ["test.later"])
        self.assertIn("the value is a list", problems[0])

    def test_bundle_ids(self):
        for value, ok in [
            (["com.apple.Safari", "com.apple.systempreferences"], True),
            ([], True),
            (["com.apple.Safari", "com.apple.Safari"], False),
            (["Safari"], False),
            ("com.apple.Safari", False),
            ([1], False),
            ([["com.apple.Safari"]], False),
            ([{"app": "com.apple.Safari"}], False),
        ]:
            with self.subTest(value=value):
                problems = profile_problems(
                    {"default_settings": {"test.apps": value}}, TEST_FILES
                )
                self.assertEqual(problems == [], ok)

    def test_unknown_profile_key(self):
        profile = {
            "default_setting": {"dock.position": "left"},
            "Accounts": [],
            "ansible_connection": "local",
            "ansible_python_interpreter": "/usr/bin/python3",
            "inventory_hostname": "example-mac",
            "groups": {},
            "playbook_dir": "/tmp",
        }
        self.assertEqual(
            subjects(profile_problems(profile, FILES)), ["Accounts", "default_setting"]
        )

    def test_repeated_reference_id_stops_the_run(self):
        files = {"a.yml": {"test.flag": entry()}, "b.yml": {"test.flag": entry()}}
        self.assertEqual(subjects(profile_problems({}, files)), ["test.flag"])


class ProfileWork(unittest.TestCase):
    def test_writes_for_the_running_account(self):
        work = profile_work({"default_settings": VALID}, FILES, "runner")
        self.assertEqual(
            work["defaults_writes"],
            [
                {
                    "id": "dock.position",
                    "domain": "com.apple.dock",
                    "key": "orientation",
                    "type": "string",
                    "value": "left",
                    "state": "present",
                    "host": "",
                    "dict_mode": "replace",
                    "takes_effect": "dock",
                },
                {
                    "id": "dock.tile_size",
                    "domain": "com.apple.dock",
                    "key": "tilesize",
                    "type": "float",
                    "value": 32,
                    "state": "present",
                    "host": "",
                    "dict_mode": "replace",
                    "takes_effect": "dock",
                },
                {
                    "id": "dock.show_recents",
                    "domain": "com.apple.dock",
                    "key": "show-recents",
                    "type": "bool",
                    "value": False,
                    "state": "present",
                    "host": "",
                    "dict_mode": "replace",
                    "takes_effect": "dock",
                },
            ],
        )
        self.assertEqual(work["accounts"], [])
        self.assertEqual(work["other_account_writes"], [])

    def test_unset_deletes_the_key(self):
        work = profile_work(
            {"default_settings": {"dock.show_recents": "unset"}}, FILES, "runner"
        )
        [write] = work["defaults_writes"]
        self.assertEqual((write["state"], write["value"]), ("absent", None))

    def test_maps_host_and_several_writes(self):
        work = profile_work(
            {"default_settings": {"test.mapped": "large"}}, TEST_FILES, "runner"
        )
        self.assertEqual(
            [
                (w["id"], w["key"], w["type"], w["value"], w["host"], w["dict_mode"])
                for w in work["defaults_writes"]
            ],
            [
                ("test.mapped", "Size", "int", 3, "", "replace"),
                ("test.mapped", "Panel", "dict", {"Wide": True}, "currentHost", "add"),
            ],
        )

    def test_each_account_gets_defaults_plus_its_own(self):
        profile = {
            "default_settings": {"dock.position": "left", "dock.tile_size": 32},
            "accounts": [
                account("runner", settings={"dock.tile_size": 48}),
                account("setuptest1", admin=True, settings={"dock.position": "bottom"}),
                account("setuptest2"),
            ],
        }
        work = profile_work(profile, FILES, "runner")

        def values(writes):
            return {w["id"]: w["value"] for w in writes}

        self.assertEqual(
            values(work["defaults_writes"]),
            {"dock.position": "left", "dock.tile_size": 48},
        )
        self.assertEqual(
            [(o["account"], values(o["writes"])) for o in work["other_account_writes"]],
            [
                ("setuptest1", {"dock.position": "bottom", "dock.tile_size": 32}),
                ("setuptest2", {"dock.position": "left", "dock.tile_size": 32}),
            ],
        )
        self.assertEqual(
            work["accounts"],
            [
                {"name": "runner", "full_name": "Runner Example", "admin": False},
                {
                    "name": "setuptest1",
                    "full_name": "Setuptest1 Example",
                    "admin": True,
                },
                {
                    "name": "setuptest2",
                    "full_name": "Setuptest2 Example",
                    "admin": False,
                },
            ],
        )

    def test_counts(self):
        profile = {
            "default_settings": {
                "test.flag": True,
                "test.logout": True,
                "test.click": True,
            },
            "accounts": [account("setuptest1", settings={"test.mapped": "small"})],
        }
        work = profile_work(profile, TEST_FILES, "runner")
        self.assertEqual(work["counts"], {"after_logout": 4, "need_click": 2})


class ProfileSummary(unittest.TestCase):
    def test_notes_and_refused_lines(self):
        summary = profile_summary(
            {"after_logout": 3, "need_click": 1},
            [
                {
                    "account": "runner",
                    "id": "dock.tile_size",
                    "message": "Type mismatch.",
                }
            ],
            [
                {
                    "name": "runner",
                    "state": "adopted",
                    "refused": "",
                    "secure_token": True,
                },
                {
                    "name": "setuptest1",
                    "state": "adopted",
                    "refused": "admin is false here, the profile says true",
                    "secure_token": False,
                },
                {
                    "name": "setuptest2",
                    "state": "created",
                    "refused": "",
                    "secure_token": False,
                },
            ],
        )
        self.assertEqual(
            summary["refused"],
            [
                "refused dock.tile_size for runner: Type mismatch.",
                "refused account setuptest1: admin is false here, the profile says true",
            ],
        )
        self.assertEqual(len(summary["notes"]), 4)
        self.assertIn("setuptest1", summary["notes"][0])
        self.assertIn("setuptest2", summary["notes"][1])
        self.assertIn(" 3 settings", summary["notes"][2])
        self.assertIn("1 setting ", summary["notes"][3])

    def test_check_run_names_each_account_it_skipped(self):
        summary = profile_summary(
            {"after_logout": 0, "need_click": 0},
            [],
            [
                {
                    "name": "setuptest1",
                    "state": "would_create",
                    "refused": "",
                    "secure_token": False,
                },
                {
                    "name": "setuptest2",
                    "state": "adopted",
                    "refused": "",
                    "secure_token": True,
                },
            ],
        )
        self.assertEqual(summary["refused"], [])
        self.assertEqual(len(summary["notes"]), 1)
        self.assertIn("setuptest1", summary["notes"][0])
        self.assertIn("skipped its settings", summary["notes"][0])

    def test_clean_run_has_no_refused_lines(self):
        summary = profile_summary({"after_logout": 0, "need_click": 0}, [], [])
        self.assertEqual(summary, {"notes": [], "refused": []})


if __name__ == "__main__":
    unittest.main()
