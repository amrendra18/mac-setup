import importlib.util
import plistlib
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "module_utils" / (name + ".py")
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


reader = load("browser_settings")
entries = load("browser_settings_data").BROWSER_SETTINGS
ID = "firefox.disable_telemetry"


class FirefoxPolicySources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        (self.profile / "prefs.js").write_text("")
        (self.profile / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=158.0.1/fixture\n"
        )

    def capture(self, documents):
        paths = []
        for index, document in enumerate(documents):
            path = self.root / (str(index) + ".plist")
            path.write_bytes(plistlib.dumps(document, fmt=plistlib.FMT_BINARY))
            paths.append(path)
        return reader.read_browser(
            "firefox",
            {ID: entries[ID]},
            self.profile,
            self.root,
            paths,
            process_check=lambda _: None,
        )

    def test_twelve_app_state_keys_without_enablement_are_not_policies(self):
        state = {"NSWindow Frame Artificial" + str(i): str(i) for i in range(12)}
        result = self.capture([state])
        self.assertEqual(result["counts"]["unmapped_policy_entries"], 0)
        self.assertEqual(result["counts"]["policy_entries"], 0)
        self.assertEqual(result["counts"]["app_state_entries"], 12)
        self.assertEqual(result["settings"][ID]["state"], "absent")

    def test_enabled_policies_are_read_beside_app_state(self):
        result = self.capture(
            [
                {
                    "EnterprisePoliciesEnabled": True,
                    "DisableTelemetry": True,
                    "NSWindow Frame Artificial": "PRIVATE-APP-STATE",
                }
            ]
        )
        self.assertIs(result["settings"][ID]["value"], True)
        self.assertEqual(result["counts"]["policy_entries"], 1)
        self.assertEqual(result["counts"]["unmapped_policy_entries"], 0)
        self.assertEqual(result["counts"]["app_state_entries"], 1)
        self.assertNotIn("PRIVATE-APP-STATE", str(result))

    def test_merged_enablement_applies_to_other_domain(self):
        result = self.capture(
            [{"EnterprisePoliciesEnabled": True}, {"DisableTelemetry": True}]
        )
        self.assertIs(result["settings"][ID]["value"], True)
        self.assertEqual(result["counts"]["policy_entries"], 1)

    def test_later_false_overrides_lower_enablement(self):
        result = self.capture(
            [
                {"EnterprisePoliciesEnabled": True, "DisableTelemetry": True},
                {"EnterprisePoliciesEnabled": False},
            ]
        )
        self.assertEqual(result["settings"][ID]["state"], "absent")
        self.assertEqual(result["counts"]["unmapped_policy_entries"], 0)
        self.assertEqual(result["counts"]["app_state_entries"], 1)

    def test_policy_shaped_value_without_enablement_is_app_state(self):
        result = self.capture([{"DisableTelemetry": True}])
        self.assertEqual(result["settings"][ID]["state"], "absent")
        self.assertEqual(result["counts"]["unmapped_policy_entries"], 0)
        self.assertEqual(result["counts"]["app_state_entries"], 1)

    def test_app_state_delimiters_are_never_parsed_as_policy_paths(self):
        result = self.capture([{"Artificial____Window": True}])
        self.assertEqual(result["counts"]["app_state_entries"], 1)
        self.assertEqual(result["counts"]["unmapped_policy_entries"], 0)

    def test_native_boolean_conversions_enable_policy_sources(self):
        for value in (
            True,
            1,
            1.0,
            -2,
            "YES",
            "true",
            "1",
            " -0002tail",
            "\tTrue",
            "\u200bYES",
        ):
            with self.subTest(value=value):
                result = self.capture(
                    [{"EnterprisePoliciesEnabled": value, "DisableTelemetry": True}]
                )
                self.assertIs(result["settings"][ID].get("value"), True)
                self.assertEqual(result["counts"]["policy_entries"], 1)

    def test_native_false_conversions_leave_app_state(self):
        for value in (
            False,
            0,
            0.0,
            "NO",
            "false",
            "0",
            "unrelated",
            "0.5",
            "+YES",
            "\nYES",
            "\rYES",
            "\vYES",
            "\fYES",
            "\u001cYES",
            [],
            {},
        ):
            with self.subTest(value=value):
                result = self.capture(
                    [{"EnterprisePoliciesEnabled": value, "DisableTelemetry": True}]
                )
                self.assertEqual(result["settings"][ID]["state"], "absent")
                self.assertEqual(result["counts"]["policy_entries"], 0)


if __name__ == "__main__":
    unittest.main()
