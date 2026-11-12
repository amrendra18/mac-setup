import re
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
CATALOG = yaml.safe_load((ROOT / "apps/macos.yml").read_text())
TABLE = yaml.safe_load((ROOT / "settings/macos/permissions/table.yml").read_text())
TEST_CATALOG = {"alpha_app": {}, "beta_app": {}}

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
        "needs": [],
        "lockout_risk": False,
        "summary": "A made-up setting.",
        "where": "System Settings > Example",
        "link": "",
    }
    return {**base, **fields}


def counts(**fields):
    return {
        "after_logout": 0,
        "timing_not_measured": 0,
        "need_click": 0,
        "own_session": 0,
        **fields,
    }


TEST_FILES = {
    "test.yml": {
        "test.flag": entry(),
        "test.machine": entry(scope="machine"),
        "test.risky": entry(lockout_risk=True),
        "test.ratio": entry(values={"type": "float", "min": 0.5, "max": 2}),
        "test.label": entry(values={"type": "text", "pattern": "[A-Z][a-z]+"}),
        "test.logout": entry(takes_effect="logout"),
        "test.unmeasured": entry(takes_effect="not measured"),
        "test.click": entry(route="by_hand", writes=[]),
        "test.machine_click": entry(
            scope="machine",
            route="by_hand",
            values=["by_hand"],
            where="System Settings > Bluetooth",
            link="x-apple.systempreferences:com.example.bluetooth",
            writes=[],
        ),
        "test.command": entry(
            scope="machine",
            route="command",
            command={
                "read": ["/usr/bin/example", "-get"],
                "parse": r"^(?P<value>[01])$",
                "map": {True: "1", False: "0"},
                "set": {
                    True: [["/usr/bin/example", "on"]],
                    False: [["/usr/bin/example", "off"]],
                },
            },
            writes=[],
        ),
        "test.switch": entry(
            values=["on", "off"],
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "Switch",
                    "type": "int",
                    "map": {"on": 1, "off": None},
                }
            ],
        ),
        "test.backdrop": entry(route="wallpaper", values=["black"], writes=[]),
        "finder.sidebar_favorites": entry(
            route="finder_sidebar", values={"type": "paths"}, writes=[]
        ),
        "finder.sidebar_favorites_only": entry(route="finder_sidebar", writes=[]),
        "finder.sidebar_hide": entry(
            route="finder_sidebar",
            values={"type": "list", "choices": ["recents", "trash"]},
            writes=[],
        ),
        "test.later": entry(
            route="not_yet", not_yet_reason="the value is a list", writes=[]
        ),
        "test.apps": entry(route="dock_apps", values={"type": "bundle_ids"}, writes=[]),
        "test.software": entry(
            scope="machine", route="apps", values={"type": "catalog_ids"}, writes=[]
        ),
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

    def test_app_ids_come_from_the_catalog(self):
        unknown = "unknown app id {}, use one of alpha_app, beta_app"
        for value, expected in [
            (["alpha_app", "beta_app"], []),
            ([], []),
            (["gamma_app"], [unknown.format("gamma_app")]),
            (
                ["alpha_app", "gamma_app", "alpha_app", "delta_app", "alpha_app"],
                [
                    unknown.format("gamma_app"),
                    unknown.format("delta_app"),
                    "alpha_app listed 3 times",
                ],
            ),
            ("alpha_app", ["'alpha_app' is not allowed"]),
            ([1], ["[1] is not allowed"]),
            ("unset", ["unset works only for settings stored with defaults"]),
        ]:
            with self.subTest(value=value):
                problems = profile_problems(
                    {"machine_settings": {"test.software": value}},
                    TEST_FILES,
                    TEST_CATALOG,
                )
                self.assertEqual(
                    subjects(problems),
                    ["machine_settings, test.software"] * len(expected),
                )
                for line, start in zip(problems, expected):
                    self.assertTrue(
                        line.removeprefix(
                            "machine_settings, test.software: "
                        ).startswith(start),
                        line,
                    )

    def test_apps_install_takes_the_catalog_ids(self):
        good = {"machine_settings": {"apps.install": sorted(CATALOG)}}
        self.assertEqual(profile_problems(good, FILES, CATALOG), [])
        self.assertEqual(profile_work(good, FILES, "runner")["apps"], sorted(CATALOG))
        under_account = {"default_settings": {"apps.install": ["sublime_text"]}}
        self.assertEqual(
            subjects(profile_problems(under_account, FILES, CATALOG)),
            ["apps.install"],
        )

    def test_virustotal_key_is_allowed_and_never_printed(self):
        secret = "Zq7-not-a-real-key"
        profile = {"virustotal_api_key": secret, "default_settng": {}}
        problems = profile_problems(profile, FILES, CATALOG)
        self.assertEqual(subjects(problems), ["default_settng"])
        self.assertFalse(any(secret in line for line in problems))

    def test_internal_profile_names_are_not_profile_keys(self):
        profile = {"profile_local_hostname": {"stdout": "sample-mac"}}
        self.assertEqual(profile_problems(profile, TEST_FILES), [])
        profile = {"profile_settings": {}}
        self.assertEqual(
            subjects(profile_problems(profile, TEST_FILES)), ["profile_settings"]
        )

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

    def test_paths_and_lists(self):
        for setting_id, value, ok in [
            ("finder.sidebar_favorites", ["/Applications", "~", "~/Desktop"], True),
            ("finder.sidebar_favorites", [], True),
            ("finder.sidebar_favorites", ["Desktop"], False),
            ("finder.sidebar_favorites", ["~sam"], False),
            ("finder.sidebar_favorites", ["/"], False),
            ("finder.sidebar_favorites", ["//"], False),
            ("finder.sidebar_favorites", ["/."], False),
            ("finder.sidebar_favorites", ["/Applications", "/Applications"], False),
            ("finder.sidebar_favorites", "/Applications", False),
            ("finder.sidebar_hide", ["recents", "trash"], True),
            ("finder.sidebar_hide", [], True),
            ("finder.sidebar_hide", ["airdrop"], False),
            ("finder.sidebar_hide", ["trash", "trash"], False),
            ("finder.sidebar_hide", "trash", False),
        ]:
            with self.subTest(setting_id=setting_id, value=value):
                problems = profile_problems(
                    {"default_settings": {setting_id: value}}, TEST_FILES
                )
                self.assertEqual(problems == [], ok)

    def test_root_as_a_favorite_is_one_problem_line(self):
        problems = profile_problems(
            {"default_settings": {"finder.sidebar_favorites": ["/Applications", "/"]}},
            TEST_FILES,
        )
        self.assertEqual(
            problems,
            [
                "finder.sidebar_favorites: ['/Applications', '/'] is not allowed, "
                'use a list of different folder paths that start with / or "~", '
                "other than / itself"
            ],
        )

    def test_favorites_only_false_alone_is_fine(self):
        profile = {"default_settings": {"finder.sidebar_favorites_only": False}}
        self.assertEqual(profile_problems(profile, TEST_FILES), [])

    def test_favorites_only_needs_favorites_in_the_same_settings(self):
        alone = {"finder.sidebar_favorites_only": True}
        both = {**alone, "finder.sidebar_favorites": ["~"]}
        for profile, expected in [
            (
                {"default_settings": alone},
                [
                    "finder.sidebar_favorites_only: needs finder.sidebar_favorites "
                    "in the same settings"
                ],
            ),
            ({"default_settings": both}, []),
            (
                {"accounts": [account("setuptest1", settings=alone)]},
                [
                    "account setuptest1, finder.sidebar_favorites_only: needs "
                    "finder.sidebar_favorites in the same settings"
                ],
            ),
            (
                {
                    "default_settings": {"finder.sidebar_favorites": ["~"]},
                    "accounts": [account("setuptest1", settings=alone)],
                },
                [],
            ),
        ]:
            with self.subTest(profile=profile):
                self.assertEqual(profile_problems(profile, TEST_FILES), expected)

    def test_unset_only_for_defaults(self):
        problems = profile_problems(
            {"default_settings": {"test.apps": "unset", "test.flag": "unset"}},
            TEST_FILES,
        )
        self.assertEqual(subjects(problems), ["test.apps"])
        self.assertIn("unset works only for settings stored with defaults", problems[0])

    def test_a_bare_on_or_off_is_told_to_quote(self):
        problems = profile_problems(
            {"default_settings": {"test.switch": False}}, TEST_FILES
        )
        self.assertEqual(
            problems,
            ['test.switch: False is not allowed, use one of "on", "off" (quote them)'],
        )
        problems = profile_problems(
            {"default_settings": {"dock.position": True}}, FILES
        )
        self.assertNotIn("quote", problems[0])

    def test_run_switches_are_allowed(self):
        profile = {
            "by_hand_list": True,
            "by_hand_open": "/usr/bin/true",
            "macos_apps_downloads": "/tmp/downloads",
            "macos_apps_applications": "/tmp/Applications",
            "permissions_ls": "/usr/bin/false",
            "permissions_open": "/usr/bin/true",
        }
        self.assertEqual(profile_problems(profile, FILES), [])

    def test_repeated_reference_id_stops_the_run(self):
        files = {"a.yml": {"test.flag": entry()}, "b.yml": {"test.flag": entry()}}
        self.assertEqual(subjects(profile_problems({}, files)), ["test.flag"])


class ProfileWork(unittest.TestCase):
    def test_writes_for_the_running_account(self):
        work = profile_work({"default_settings": VALID}, FILES, "runner")
        self.assertEqual(
            work["own"]["writes"],
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
        self.assertEqual(work["others"], [])
        self.assertEqual(work["machine_writes"], [])
        self.assertEqual(work["commands"], [])
        self.assertEqual(work["by_hand"], [])

    def test_unset_deletes_the_key(self):
        work = profile_work(
            {"default_settings": {"dock.show_recents": "unset"}}, FILES, "runner"
        )
        [write] = work["own"]["writes"]
        self.assertEqual((write["state"], write["value"]), ("absent", None))

    def test_maps_host_and_several_writes(self):
        work = profile_work(
            {"default_settings": {"test.mapped": "large"}}, TEST_FILES, "runner"
        )
        self.assertEqual(
            [
                (w["id"], w["key"], w["type"], w["value"], w["host"], w["dict_mode"])
                for w in work["own"]["writes"]
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
            values(work["own"]["writes"]),
            {"dock.position": "left", "dock.tile_size": 48},
        )
        self.assertEqual(
            [(o["account"], values(o["writes"])) for o in work["others"]],
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

    def test_a_null_in_the_map_deletes_the_key(self):
        work = profile_work(
            {"default_settings": {"test.switch": "off"}}, TEST_FILES, "runner"
        )
        [write] = work["own"]["writes"]
        self.assertEqual((write["state"], write["value"]), ("absent", None))

    def test_routes_build_their_work_items(self):
        profile = {
            "default_settings": {
                "test.apps": ["com.apple.Terminal"],
                "test.backdrop": "black",
                "finder.sidebar_favorites": ["~"],
                "test.click": True,
            },
            "accounts": [
                account("setuptest1", settings={"finder.sidebar_hide": ["trash"]}),
            ],
            "machine_settings": {
                "test.machine": True,
                "test.command": False,
                "test.machine_click": "by_hand",
                "test.software": ["beta_app", "alpha_app"],
            },
        }
        work = profile_work(profile, TEST_FILES, "runner")
        self.assertEqual(work["apps"], ["beta_app", "alpha_app"])
        own = work["own"]
        self.assertEqual(own["dock_apps"], {"apps": ["com.apple.Terminal"]})
        self.assertEqual(own["wallpaper"], {"color": "black"})
        self.assertEqual(
            own["finder_sidebar"],
            {"ids": ["finder.sidebar_favorites"], "favorites": ["~"]},
        )
        [other] = work["others"]
        self.assertEqual(other["account"], "setuptest1")
        self.assertEqual(
            other["finder_sidebar"],
            {
                "ids": ["finder.sidebar_favorites", "finder.sidebar_hide"],
                "favorites": ["~"],
                "hide": ["trash"],
            },
        )
        self.assertEqual(
            [(w["id"], w["domain"]) for w in work["machine_writes"]],
            [("test.machine", "com.example.test")],
        )
        self.assertEqual(
            work["commands"],
            [
                {
                    "id": "test.command",
                    "read": ["/usr/bin/example", "-get"],
                    "parse": r"^(?P<value>[01])$",
                    "map": {True: "1", False: "0"},
                    "refuse": {},
                    "set": {
                        True: [["/usr/bin/example", "on"]],
                        False: [["/usr/bin/example", "off"]],
                    },
                    "value": False,
                }
            ],
        )
        self.assertEqual(
            [(i["account"], i["id"], i["choose"]) for i in work["by_hand"]],
            [
                ("setuptest1", "test.click", "A made-up setting: on"),
                ("runner", "test.click", "A made-up setting: on"),
                (
                    "machine",
                    "test.machine_click",
                    "A made-up setting: the way you want",
                ),
            ],
        )
        self.assertEqual(
            work["by_hand"][2]["link"],
            "x-apple.systempreferences:com.example.bluetooth",
        )
        self.assertEqual(work["counts"]["need_click"], 3)

    def test_unmeasured_timing_is_not_counted_as_a_logout(self):
        profile = {"default_settings": {"test.logout": True, "test.unmeasured": True}}
        self.assertEqual(
            profile_work(profile, TEST_FILES, "runner")["counts"],
            counts(after_logout=1, timing_not_measured=1),
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
        self.assertEqual(
            work["counts"],
            counts(after_logout=4, need_click=2),
        )


class ProfileSummary(unittest.TestCase):
    def test_notes_and_refused_lines(self):
        summary = profile_summary(
            counts(after_logout=3, need_click=1),
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
            [],
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
        self.assertEqual(
            summary["notes"][3], "1 setting needs a click: run playbooks/by-hand.yml"
        )

    def test_check_run_names_each_account_it_skipped(self):
        summary = profile_summary(
            counts(),
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
            [],
        )
        self.assertEqual(summary["refused"], [])
        self.assertEqual(len(summary["notes"]), 1)
        self.assertIn("setuptest1", summary["notes"][0])
        self.assertIn("skipped its settings", summary["notes"][0])

    def test_clean_run_has_no_refused_lines(self):
        summary = profile_summary(
            counts(),
            [],
            [],
            [],
        )
        self.assertEqual(summary, {"notes": [], "refused": []})

    def test_a_missing_admin_password_is_named_as_admin_rights(self):
        summary = profile_summary(
            counts(),
            [
                {
                    "account": "machine",
                    "id": "power.display_sleep_minutes",
                    "message": "Task failed: Premature end of stream waiting for "
                    "become success.\n>>> Standard Error\nsudo: a password is required",
                }
            ],
            [],
            [],
        )
        self.assertEqual(
            summary["refused"],
            [
                "refused power.display_sleep_minutes for machine: "
                "needs admin rights: run with -K"
            ],
        )

    def test_a_wallpaper_waiting_for_another_account_is_a_note(self):
        summary = profile_summary(
            counts(need_click=2),
            [],
            [],
            ["setuptest1"],
        )
        self.assertEqual(
            summary,
            {
                "notes": [
                    "the desktop picture for setuptest1 is set at its next run "
                    "while it is signed in",
                    "2 settings need a click: run playbooks/by-hand.yml",
                ],
                "refused": [],
            },
        )

    def test_unmeasured_timing_has_its_own_line(self):
        for unmeasured, line in [
            (
                1,
                "1 setting may need a logout or an app restart to show; "
                "its timing is not measured",
            ),
            (
                3,
                "3 settings may need a logout or an app restart to show; "
                "their timing is not measured",
            ),
        ]:
            with self.subTest(unmeasured):
                summary = profile_summary(
                    counts(after_logout=2, timing_not_measured=unmeasured), [], [], []
                )
                self.assertEqual(
                    summary["notes"],
                    ["log out and back in to see 2 settings", line],
                )

    def test_permission_notes_come_first(self):
        summary = profile_summary(
            counts(need_click=1),
            [],
            [],
            [],
            ["Full Disk Access: off", "skipped time.zone: needs Full Disk Access"],
        )
        self.assertEqual(
            summary["notes"],
            [
                "Full Disk Access: off",
                "skipped time.zone: needs Full Disk Access",
                "1 setting needs a click: run playbooks/by-hand.yml",
            ],
        )

    def test_a_stop_in_play_1_or_2_ends_the_run_with_its_line(self):
        summary = profile_summary(
            counts(),
            [
                {
                    "account": "runner",
                    "id": "dock.tile_size",
                    "message": "Type mismatch.",
                }
            ],
            [],
            [],
            [],
            "Set each command setting: Invalid data passed to 'loop'",
        )
        self.assertEqual(
            summary["refused"],
            [
                "the run stopped early at Set each command setting: "
                "Invalid data passed to 'loop'",
                "refused dock.tile_size for runner: Type mismatch.",
            ],
        )

    def test_settings_for_another_session_are_a_note(self):
        summary = profile_summary(
            counts(own_session=2),
            [],
            [],
            [],
        )
        self.assertEqual(
            summary["notes"],
            [
                "2 settings for other accounts run in the account's own session: "
                "sign in as each account and run mac-setup there"
            ],
        )


def needed(profile, running_account="runner"):
    return [
        (permission["id"], permission["settings"])
        for permission in profile_work(profile, FILES, running_account, TABLE)[
            "permissions"
        ]
    ]


class ProfilePermissions(unittest.TestCase):
    def test_full_disk_access_comes_from_needs(self):
        profile = {
            "machine_settings": {
                "time.zone": "Etc/UTC",
                "power.system_sleep_minutes": 30,
                "sharing.remote_apple_events": False,
            }
        }
        self.assertEqual(
            needed(profile),
            [("full_disk_access", ["time.zone", "sharing.remote_apple_events"])],
        )

    def test_folders_come_from_the_favorites_listed(self):
        profile = {
            "default_settings": {
                "finder.sidebar_favorites": [
                    "/Applications",
                    "~",
                    "~/Downloads/Projects",
                    "~/Desktop/",
                ]
            }
        }
        self.assertEqual(
            needed(profile),
            [
                ("desktop_folder", ["finder.sidebar_favorites"]),
                ("downloads_folder", ["finder.sidebar_favorites"]),
            ],
        )

    def test_a_favorite_outside_the_three_folders_needs_nothing(self):
        profile = {
            "default_settings": {
                "finder.sidebar_favorites": ["~", "/Applications", "~/Desktopper"]
            }
        }
        self.assertEqual(needed(profile), [])

    def test_only_the_running_account_and_the_machine_count(self):
        profile = {
            "accounts": [
                account(
                    "setuptest1",
                    settings={"finder.sidebar_favorites": ["~/Documents"]},
                ),
                account("runner", settings={"finder.sidebar_favorites": ["~/Desktop"]}),
            ]
        }
        self.assertEqual(
            needed(profile), [("desktop_folder", ["finder.sidebar_favorites"])]
        )
        self.assertEqual(
            needed(profile, "setuptest1"),
            [("documents_folder", ["finder.sidebar_favorites"])],
        )

    def test_each_permission_carries_its_table_row(self):
        permission = profile_work(
            {"machine_settings": {"time.zone": "Etc/UTC"}}, FILES, "runner", TABLE
        )["permissions"][0]
        self.assertEqual(
            permission,
            {
                "id": "full_disk_access",
                **TABLE["full_disk_access"],
                "settings": ["time.zone"],
                "skips": ["time.zone"],
            },
        )

    def test_a_missing_folder_skips_the_whole_sidebar_step(self):
        profile = {
            "default_settings": {
                "finder.sidebar_hide": ["recents"],
                "finder.sidebar_favorites": ["~/Desktop", "~/Downloads"],
                "finder.sidebar_favorites_only": True,
            }
        }
        permissions = profile_work(profile, FILES, "runner", TABLE)["permissions"]
        self.assertEqual(
            [(p["id"], p["settings"], p["skips"]) for p in permissions],
            [
                (
                    folder,
                    ["finder.sidebar_favorites"],
                    [
                        "finder.sidebar_favorites",
                        "finder.sidebar_favorites_only",
                        "finder.sidebar_hide",
                    ],
                )
                for folder in ("desktop_folder", "downloads_folder")
            ],
        )

    def test_a_click_is_no_permission(self):
        self.assertEqual(
            needed({"machine_settings": {"network.bluetooth_power": False}}), []
        )


def hotkey(parameters):
    return {"enabled": True, "value": {"parameters": parameters, "type": "standard"}}


class RemainingStores(unittest.TestCase):
    def problems(self, settings, **profile):
        return profile_problems({"default_settings": settings, **profile}, FILES)

    def test_hotkey_values(self):
        good = {
            "mission_control": "control+up",
            "show_desktop": "f11",
            "switch_to_desktop_1": "control+1",
            "move_left_a_space": True,
            "application_windows": False,
        }
        self.assertEqual(self.problems({"mission_control.shortcuts": good}), [])
        for bad in [
            {"open_the_pod_bay_doors": True},
            {"mission_control": "space"},
            {"mission_control": "a"},
            {"mission_control": "control+control+up"},
            {"mission_control": "hyper+up"},
            {"mission_control": "control+"},
            {"mission_control": "Control+Up"},
            {"mission_control": 5},
            {"mission_control": "on"},
            ["mission_control"],
            "control+up",
        ]:
            with self.subTest(bad):
                [line] = self.problems({"mission_control.shortcuts": bad})
                self.assertTrue(line.startswith("mission_control.shortcuts: "))
                self.assertIn("is not allowed, use a mapping from actions (", line)

    def test_unset_is_refused_for_hotkeys(self):
        [line] = self.problems({"input.keyboard_shortcuts": "unset"})
        self.assertTrue(
            line.startswith(
                "input.keyboard_shortcuts: unset would reset every keyboard shortcut"
            )
        )

    def test_hotkeys_are_encoded_into_numbered_entries(self):
        profile = {
            "default_settings": {
                "input.keyboard_shortcuts": {
                    "spotlight_search": "command+option+space",
                    "screenshot_options": False,
                    "finder_search_window": True,
                    "previous_input_source": "control+space",
                },
                "mission_control.shortcuts": {
                    "mission_control": "control+up",
                    "show_desktop": "f11",
                    "switch_to_desktop_2": "control+shift+2",
                    "move_right_a_space": "command+l",
                },
            }
        }
        writes = profile_work(profile, FILES, "runner")["own"]["writes"]
        self.assertEqual(
            [
                (w["id"], w["domain"], w["key"], w["type"], w["dict_mode"])
                for w in writes
            ],
            [
                (
                    setting_id,
                    "com.apple.symbolichotkeys",
                    "AppleSymbolicHotKeys",
                    "dict",
                    "add",
                )
                for setting_id in profile["default_settings"]
            ],
        )
        self.assertEqual(
            writes[0]["value"],
            {
                "64": hotkey([32, 49, 1572864]),
                "184": {"enabled": False},
                "65": {"enabled": True},
                "60": hotkey([32, 49, 262144]),
            },
        )
        self.assertEqual(
            writes[1]["value"],
            {
                "32": hotkey([65535, 126, 8650752]),
                "36": hotkey([65535, 103, 8388608]),
                "119": hotkey([50, 19, 393216]),
                "81": hotkey([108, 37, 1048576]),
            },
        )
        self.assertEqual({w["takes_effect"] for w in writes}, {"not measured"})

    def test_input_sources(self):
        self.assertEqual(
            self.problems({"input.input_sources": ["british", "emoji_and_symbols"]}),
            [],
        )
        for bad in [[], ["us", "us"], ["klingon"], "us"]:
            with self.subTest(bad):
                [line] = self.problems({"input.input_sources": bad})
                self.assertIn("use a list of 1 or more different items from us,", line)
        [write] = profile_work(
            {
                "default_settings": {
                    "input.input_sources": ["canadian", "emoji_and_symbols"]
                }
            },
            FILES,
            "runner",
        )["own"]["writes"]
        self.assertEqual(
            (write["domain"], write["key"], write["type"], write["value"]),
            (
                "com.apple.HIToolbox",
                "AppleEnabledInputSources",
                "array",
                [
                    {
                        "InputSourceKind": "Keyboard Layout",
                        "KeyboardLayout ID": 29,
                        "KeyboardLayout Name": "Canadian",
                    },
                    {
                        "Bundle ID": "com.apple.CharacterPaletteIM",
                        "InputSourceKind": "Non Keyboard Input Method",
                    },
                ],
            ),
        )

    def test_off_leaves_the_shared_finger_keys_alone(self):
        work = profile_work(
            {
                "default_settings": {
                    "input.mission_control_gesture": "off",
                    "input.app_expose_gesture": "four_finger",
                }
            },
            FILES,
            "runner",
        )
        self.assertEqual(
            [(w["id"], w["key"], w["value"]) for w in work["own"]["writes"]],
            [
                (
                    "input.mission_control_gesture",
                    "showMissionControlGestureEnabled",
                    False,
                ),
                ("input.app_expose_gesture", "TrackpadThreeFingerVertSwipeGesture", 0),
                ("input.app_expose_gesture", "TrackpadFourFingerVertSwipeGesture", 2),
                ("input.app_expose_gesture", "showAppExposeGestureEnabled", True),
            ],
        )

    def test_gestures_that_share_keys_must_agree(self):
        mc, ae = "input.mission_control_gesture", "input.app_expose_gesture"
        for agree in [
            {mc: "three_finger", ae: "three_finger"},
            {mc: "three_finger", ae: "off"},
            {mc: "off", ae: "four_finger"},
            {mc: "off", ae: "off"},
            {mc: "unset", ae: "unset"},
        ]:
            with self.subTest(agree):
                self.assertEqual(self.problems(agree), [])
        for disagree in [
            {mc: "three_finger", ae: "four_finger"},
            {mc: "three_finger", ae: "unset"},
        ]:
            with self.subTest(disagree):
                self.assertEqual(
                    self.problems(disagree),
                    [
                        f"{ae}: {mc} sets the same key TrackpadThreeFingerVertSwipeGesture "
                        "to another value, so choose values that agree"
                    ],
                )

    def test_a_shared_key_is_checked_once_per_place(self):
        mc, ae = "input.mission_control_gesture", "input.app_expose_gesture"
        problems = self.problems(
            {mc: "three_finger", ae: "four_finger"},
            accounts=[
                account("setuptest1"),
                account("setuptest2", settings={ae: "three_finger"}),
            ],
        )
        self.assertEqual(subjects(problems), [ae])
        problems = self.problems(
            {mc: "three_finger"},
            accounts=[account("setuptest1", settings={ae: "four_finger"})],
        )
        self.assertEqual(subjects(problems), [f"account setuptest1, {ae}"])

    def test_swipes_that_share_the_three_finger_key(self):
        pages, apps = "input.swipe_between_pages", "input.swipe_full_screen_apps"
        for agree in [
            {pages: "two_finger", apps: "three_finger"},
            {pages: "off", apps: "three_finger"},
            {pages: "three_finger", apps: "four_finger"},
            {pages: "two_or_three_finger", apps: "off"},
        ]:
            with self.subTest(agree):
                self.assertEqual(self.problems(agree), [])
        for disagree in [
            {pages: "three_finger", apps: "three_finger"},
            {pages: "two_or_three_finger", apps: "three_finger"},
        ]:
            with self.subTest(disagree):
                self.assertEqual(
                    self.problems(disagree),
                    [
                        f"{apps}: {pages} sets the same key TrackpadThreeFingerHorizSwipeGesture "
                        "to another value, so choose values that agree"
                    ],
                )
        work = profile_work(
            {"default_settings": {pages: "two_finger", apps: "three_finger"}},
            FILES,
            "runner",
        )
        self.assertEqual(
            [(w["id"], w["key"], w["value"]) for w in work["own"]["writes"]],
            [
                (pages, "AppleEnableSwipeNavigateWithScrolls", True),
                (apps, "TrackpadThreeFingerHorizSwipeGesture", 2),
                (apps, "TrackpadFourFingerHorizSwipeGesture", 0),
            ],
        )

    def test_the_conflict_names_the_account_own_setting(self):
        mc, ae = "input.mission_control_gesture", "input.app_expose_gesture"
        problems = self.problems(
            {mc: "three_finger", ae: "three_finger"},
            accounts=[account("setuptest1", settings={mc: "four_finger"})],
        )
        self.assertEqual(
            problems,
            [
                f"account setuptest1, {mc}: {ae} sets the same key "
                "TrackpadThreeFingerVertSwipeGesture to another value, "
                "so choose values that agree"
            ],
        )

    def test_unset_is_refused_for_input_sources(self):
        [line] = self.problems({"input.input_sources": "unset"})
        self.assertTrue(
            line.startswith(
                "input.input_sources: unset would delete the whole list, use "
            )
        )

    def test_volume_is_an_account_setting(self):
        [line] = profile_problems(
            {"machine_settings": {"sound.output_volume": 50}}, FILES
        )
        self.assertEqual(
            line,
            "machine_settings, sound.output_volume: an account setting, "
            "move it to default_settings or an account",
        )
        [line] = self.problems({"sound.alert_volume": 101})
        self.assertIn("use a whole number from 0 to 100", line)

    def test_volume_runs_only_for_the_running_account(self):
        work = profile_work(
            {
                "default_settings": {"sound.output_volume": 50},
                "accounts": [
                    account("runner"),
                    account("setuptest1", settings={"sound.alert_volume": 75}),
                ],
            },
            FILES,
            "runner",
        )
        [own] = work["own"]["commands"]
        self.assertEqual((own["id"], own["value"]), ("sound.output_volume", 50))
        self.assertEqual(own["set"][0][-1], "{value}")
        [other] = work["others"]
        self.assertEqual(
            [(c["id"], c["value"]) for c in other["commands"]],
            [("sound.output_volume", 50), ("sound.alert_volume", 75)],
        )
        self.assertEqual(work["commands"], [])
        self.assertEqual(work["counts"]["own_session"], 2)

    def test_the_volume_parse_reads_each_part(self):
        read = "output volume:65, input volume:missing value, alert volume:100, output muted:false\nexit: 0\n"
        for setting_id, expected in [
            ("sound.output_volume", "65"),
            ("sound.alert_volume", "100"),
        ]:
            with self.subTest(setting_id):
                parse = FILES["systemui.yml"][setting_id]["command"]["parse"]
                self.assertEqual(re.search(parse, read, re.M).group("value"), expected)


if __name__ == "__main__":
    unittest.main()
