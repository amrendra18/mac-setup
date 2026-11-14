import importlib.util
import json
import plistlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = load_module("browser_map_reader", "module_utils/browser_settings.py")
entries = load_module(
    "browser_map_data", "module_utils/browser_settings_data.py"
).BROWSER_SETTINGS


class BrowserMapRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        (self.profile / "prefs.js").write_text("")
        (self.profile / "Preferences").write_text("{}")
        (self.profile / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=158.0.1/fixture\n"
        )
        (self.root / "Last Version").write_text("158.0.8127.37")
        self.policy = self.root / "fixture-policy.plist"

    def capture(self, family, rows, policies=()):
        return reader.read_browser(
            family,
            rows,
            self.profile,
            self.root,
            policies,
            process_check=lambda family: None,
        )

    def test_every_firefox_preference_policy_pair_accepts_both_encodings(self):
        pairs = {}
        for key, row in entries.items():
            descriptors = [
                descriptor
                for descriptor in row["browser"]["read"]
                if descriptor["source"] == "firefox_policy"
                and descriptor["path"][:1] == ["Preferences"]
            ]
            if len(descriptors) == 2:
                pairs[key] = (row, descriptors)
        self.assertEqual(len(pairs), 24)
        for key, (row, descriptors) in pairs.items():
            preference = next(
                descriptor["path"][1]
                for descriptor in descriptors
                if len(descriptor["path"]) == 2
            )
            private = row["browser"]["capture"]["class"] == "mask"
            kind = descriptors[0]["type"]
            value = {
                "bool": True,
                "int": 1,
                "text": "fixture-private-dictionary" if private else "9.9.9.9",
            }[kind]
            for encoding in ("scalar", "object"):
                with self.subTest(setting=key, encoding=encoding):
                    stored = (
                        value
                        if encoding == "scalar"
                        else {"Value": value, "Status": "locked"}
                    )
                    self.policy.write_bytes(
                        plistlib.dumps({"Preferences": {preference: stored}})
                    )
                    result = self.capture("firefox", {key: row}, [self.policy])
                    self.assertEqual(set(result["settings"]), {key})
                    item = result["settings"][key]
                    expected_state = "masked" if private else "captured"
                    self.assertEqual(item["state"], expected_state)
                    observation = {
                        "source": "firefox_policy",
                        "state": "masked" if private else "policy_stored",
                        "lock": "unknown",
                    }
                    if private:
                        self.assertNotIn("value", item)
                        self.assertNotIn(value, json.dumps(result))
                    else:
                        self.assertEqual(item["value"], value)
                        self.assertIs(type(item["value"]), type(value))
                        observation["value"] = value
                    self.assertEqual(item["observations"], [observation])
                    self.assertEqual(result["counts"][expected_state], 1)
                    self.assertEqual(result["counts"]["unverified"], 0)

    def test_chrome_quit_aliases_read_local_state(self):
        ids = ("chrome.confirm_to_quit", "chrome.warn_before_quitting_enabled")
        rows = {key: entries[key] for key in ids}
        for value in (True, False):
            with self.subTest(value=value):
                (self.root / "Local State").write_text(
                    json.dumps({"browser": {"confirm_to_quit": value}})
                )
                result = self.capture("chrome", rows)
                self.assertEqual(result["counts"]["captured"], 2)
                for key in ids:
                    item = result["settings"][key]
                    self.assertEqual(item["state"], "captured")
                    self.assertIs(item["value"], value)
                    self.assertEqual(
                        item["observations"],
                        [
                            {
                                "source": "chrome_local_state",
                                "state": "persisted",
                                "lock": "unknown",
                                "value": value,
                            }
                        ],
                    )
        self.assertEqual(result["settings"][ids[0]]["scope"], "user")
        self.assertEqual(result["settings"][ids[1]]["scope"], "machine")

    def test_chrome_quit_aliases_ignore_profile_preferences(self):
        ids = ("chrome.confirm_to_quit", "chrome.warn_before_quitting_enabled")
        (self.profile / "Preferences").write_text(
            json.dumps({"browser": {"confirm_to_quit": True}})
        )
        result = self.capture("chrome", {key: entries[key] for key in ids})
        for key in ids:
            self.assertEqual(result["settings"][key]["state"], "absent")
            self.assertEqual(result["settings"][key]["observations"], [])
            self.assertNotIn("value", result["settings"][key])
        self.assertEqual(result["counts"]["captured"], 0)

    def test_chrome_quit_policy_is_distinct_from_personal_alias(self):
        ids = ("chrome.confirm_to_quit", "chrome.warn_before_quitting_enabled")
        self.policy.write_bytes(plistlib.dumps({"WarnBeforeQuittingEnabled": True}))
        result = self.capture(
            "chrome", {key: entries[key] for key in ids}, [self.policy]
        )
        personal, policy = (result["settings"][key] for key in ids)
        self.assertEqual(personal["state"], "absent")
        self.assertEqual(policy["state"], "captured")
        self.assertIs(policy["value"], True)
        self.assertEqual(
            policy["observations"],
            [
                {
                    "source": "chrome_policy",
                    "state": "policy_stored",
                    "lock": "unknown",
                    "value": True,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
