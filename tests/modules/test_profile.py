import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/profile/filter_plugins"))

from profile import profile_defaults_writes, profile_problems

REFERENCE = {}
for path in sorted((ROOT / "settings/macos").glob("*.yml")):
    REFERENCE.update(yaml.safe_load(path.read_text()))

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
    ("dock.postion", "left", False),
]


class ProfileProblems(unittest.TestCase):
    def test_each_value(self):
        for setting_id, value, allowed in CASES:
            with self.subTest(setting_id=setting_id, value=value):
                problems = profile_problems({setting_id: value}, REFERENCE)
                if allowed:
                    self.assertEqual(problems, [])
                else:
                    self.assertEqual(len(problems), 1)
                    self.assertTrue(problems[0].startswith(f"{setting_id}: "))

    def test_one_line_per_problem(self):
        settings = {
            "dock.postion": "left",
            "dock.tile_size": 129,
            "dock.show_recents": "no",
        }
        problems = profile_problems(settings, REFERENCE)
        self.assertEqual([line.split(":")[0] for line in problems], list(settings))

    def test_bool_is_not_a_whole_number(self):
        reference = {"x": {"values": {"type": "int", "min": 0, "max": 2}}}
        self.assertEqual(profile_problems({"x": 1}, reference), [])
        self.assertEqual(len(profile_problems({"x": True}, reference)), 1)

    def test_settings_must_be_a_mapping(self):
        self.assertEqual(len(profile_problems(["dock.position"], REFERENCE)), 1)

    def test_valid_profile(self):
        self.assertEqual(profile_problems(VALID, REFERENCE), [])


class ProfileDefaultsWrites(unittest.TestCase):
    def test_writes_for_valid_profile(self):
        self.assertEqual(
            profile_defaults_writes(VALID, REFERENCE),
            [
                {
                    "id": "dock.position",
                    "domain": "com.apple.dock",
                    "key": "orientation",
                    "type": "string",
                    "value": "left",
                    "takes_effect": "dock",
                },
                {
                    "id": "dock.tile_size",
                    "domain": "com.apple.dock",
                    "key": "tilesize",
                    "type": "float",
                    "value": 32,
                    "takes_effect": "dock",
                },
                {
                    "id": "dock.show_recents",
                    "domain": "com.apple.dock",
                    "key": "show-recents",
                    "type": "bool",
                    "value": False,
                    "takes_effect": "dock",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
