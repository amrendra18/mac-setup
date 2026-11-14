import importlib.util
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "compare_reader", ROOT / "module_utils/browser_settings.py"
)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)
SPEC = importlib.util.spec_from_file_location(
    "compare_catalog", ROOT / "module_utils/browser_settings_data.py"
)
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)
SPEC = importlib.util.spec_from_file_location(
    "compare_logic", ROOT / "module_utils/browser_compare.py"
)
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)
SETTING = "firefox.new_tab_page"


class BrowserCompareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = self.root / "first.yml"
        self.second = self.root / "second.yml"

    def cli(self, *args):
        return subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                str(ROOT / "scripts/browser-settings"),
                *map(str, args),
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

    def candidate(self, path, value=True, state="captured", setting=SETTING):
        path.write_text(
            reader.candidate_yaml(
                [
                    {
                        "browser": "firefox",
                        "versions": {},
                        "counts": {},
                        "settings": {
                            setting: {
                                "scope": "machine",
                                "state": state,
                                "value": value,
                                "observations": [],
                            }
                        },
                    }
                ]
            )
        )

    def check_fixture(self, settings, preferences, policies=None):
        profile = self.root / "profile"
        profile.mkdir(exist_ok=True)
        (profile / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=158.0.1_20261101010101/20261101010101\n"
        )
        (profile / "prefs.js").write_text(
            "\n".join(
                "user_pref(" + json.dumps(key) + ", " + json.dumps(value) + ");"
                for key, value in preferences.items()
            )
        )
        policy_root = self.root / "policies"
        policy_root.mkdir(exist_ok=True)
        (policy_root / "org.mozilla.firefox.plist").write_bytes(
            plistlib.dumps(policies or {})
        )
        export = self.root / "profile.json"
        export.write_text(
            json.dumps({"machine_settings": settings, "default_settings": {}})
        )
        return self.cli(
            "check",
            "--browser",
            "firefox",
            "--profile-json",
            export,
            "--firefox-profile",
            profile,
            "--policy-root",
            policy_root,
        )

    def test_equal_and_different_captures(self):
        self.candidate(self.first)
        self.candidate(self.second)
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, SETTING + " same\n")
        self.candidate(self.second, False)
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, SETTING + " different\n")

    def test_present_in_only_one_and_known_absence(self):
        self.candidate(self.first)
        self.second.write_text("---\nmachine_settings: {}\ndefault_settings: {}\n")
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, SETTING + " only-in-one\n")
        for path in (self.first, self.second):
            self.candidate(path, state="absent")
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, SETTING + " same\n")

    def test_two_empty_captures_cannot_prove_equality(self):
        for path in (self.first, self.second):
            path.write_text("---\nmachine_settings: {}\ndefault_settings: {}\n")
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn(str(self.root), result.stderr)

    def test_toolbar_capture_diff_ignores_json_object_order_and_spacing(self):
        setting = "firefox.pref.browser_ui_customization_state"
        first = '{ "placements": { "nav-bar": ["urlbar-container", "back-button"] }, "currentVersion": 20 }'
        second = '{"currentVersion":20,"placements":{"nav-bar":["urlbar-container","back-button"]}}'
        self.candidate(self.first, first, setting=setting)
        self.candidate(self.second, second, setting=setting)
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, setting + " same\n")
        reordered = '{"currentVersion":20,"placements":{"nav-bar":["back-button","urlbar-container"]}}'
        self.candidate(self.second, reordered, setting=setting)
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, setting + " different\n")
        for value in (first, second, reordered):
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_toolbar_check_compares_saved_and_desired_json_consistently(self):
        setting = "firefox.pref.browser_ui_customization_state"
        exact = '{ "placements": { "nav-bar": ["urlbar-container"] } }'
        result = self.check_fixture(
            {setting: exact}, {"browser.uiCustomization.state": exact}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, setting + " same\n")
        saved = '{ "placements": { "nav-bar": ["urlbar-container", "back-button"] }, "currentVersion": 20 }'
        desired = '{"currentVersion":20,"placements":{"nav-bar":["urlbar-container","back-button"]}}'
        result = self.check_fixture(
            {setting: desired}, {"browser.uiCustomization.state": saved}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, setting + " same\n")
        reordered = '{"currentVersion":20,"placements":{"nav-bar":["back-button","urlbar-container"]}}'
        result = self.check_fixture(
            {setting: reordered}, {"browser.uiCustomization.state": saved}
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, setting + " different\n")
        for value in (exact, saved, desired, reordered):
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_check_reports_every_explicit_release_as_unverified(self):
        telemetry = "firefox.disable_telemetry"
        active_policy = {
            "EnterprisePoliciesEnabled": True,
            "DisableTelemetry": True,
        }
        cases = (
            (
                {telemetry: "unset", SETTING: True},
                active_policy,
                telemetry + " gap unverified\n" + SETTING + " same\n",
            ),
            ({telemetry: "unset"}, active_policy, telemetry + " gap unverified\n"),
            ({telemetry: "unset"}, {}, telemetry + " gap unverified\n"),
            ({SETTING: "unset"}, {}, SETTING + " gap unverified\n"),
            (
                {telemetry: "unset", SETTING: "unset"},
                active_policy,
                telemetry + " gap unverified\n" + SETTING + " gap unverified\n",
            ),
        )
        for settings, policies, expected in cases:
            with self.subTest(settings=settings, policies=policies):
                result = self.check_fixture(
                    settings, {"browser.newtabpage.enabled": True}, policies
                )
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertEqual(result.stdout, expected)
                self.assertNotIn(str(self.root), result.stdout + result.stderr)

    def test_check_rejects_unset_for_boolean_lock_control(self):
        result = self.check_fixture({"firefox.lock": "unset"}, {})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn(str(self.root), result.stderr)

    def test_unknown_states_never_prove_equality(self):
        for state in ("masked", "conflict", "unverified", "unsupported", "unavailable"):
            with self.subTest(state=state):
                for path in (self.first, self.second):
                    self.candidate(path, state=state)
                result = self.cli("diff", self.first, self.second)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, SETTING + " gap " + state + "\n")
                self.assertNotIn("same", result.stdout)

    def test_values_and_private_keys_never_appear(self):
        canary = "artificial-private-" + os.urandom(8).hex()
        setting = "firefox.homepage.url"
        self.assertIn(setting, catalog.BROWSER_SETTINGS)
        for path, value in ((self.first, canary), (self.second, canary + "-other")):
            self.candidate(path, value=value, setting=setting)
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, setting + " different\n")
        self.assertNotIn(canary, result.stdout + result.stderr)
        self.first.write_text(
            "---\nmachine_settings:\n  " + canary + ": true\ndefault_settings: {}\n"
        )
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(canary, result.stdout + result.stderr)
        self.assertNotIn(str(self.first), result.stdout + result.stderr)

    def test_bad_input_is_rejected_before_any_comparison_output(self):
        self.candidate(self.second)
        inputs = (
            "machine_settings: {}\ndefault_settings: {}\n",
            "---\nmachine_settings:\n  "
            + SETTING
            + ": !!bool true\ndefault_settings: {}\n",
            "---\nmachine_settings:\n  "
            + SETTING
            + ": &secret true\ndefault_settings: {}\n",
            "---\nmachine_settings:\n  "
            + SETTING
            + ": true\n  "
            + SETTING
            + ": false\ndefault_settings: {}\n",
            "---\nmachine_settings: {}\ndefault_settings:\n  " + SETTING + ": true\n",
            "---\nmachine_settings:\n  " + SETTING + ": 1\ndefault_settings: {}\n",
            "---\nmachine_settings:\n  # "
            + SETTING
            + ": masked\n  "
            + SETTING
            + ": true\ndefault_settings: {}\n",
            "---\nmachine_settings: {}\ndefault_settings: {}\nextra: true\n",
        )
        for text in inputs:
            with self.subTest(text=text):
                self.first.write_text(text)
                result = self.cli("diff", self.first, self.second)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
        result = self.cli("diff", self.root / "missing", self.second)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(str(self.root), result.stderr)

    def test_objects_use_json_without_exposing_members(self):
        canary = "artificial-private-" + os.urandom(8).hex()
        setting = "firefox.3rdparty.extensions"
        value = {canary: {"enabled": True}}
        self.candidate(self.first, value, setting=setting)
        self.candidate(self.second, value, setting=setting)
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, setting + " same\n")
        self.assertNotIn(canary, result.stdout + result.stderr)
        self.candidate(self.second, {canary: {"enabled": False}}, setting=setting)
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, setting + " different\n")
        self.first.write_text(
            "---\nmachine_settings:\n  "
            + setting
            + ': {"duplicate": {}, "duplicate": {}}\ndefault_settings: {}\n'
        )
        result = self.cli("diff", self.first, self.second)
        self.assertEqual(result.returncode, 2)

    def test_gaps_take_precedence_over_proven_check_drift(self):
        desired = {
            SETTING: ("captured", True),
            "firefox.homepage.url": ("captured", "https://example.invalid"),
        }
        observed = {
            SETTING: ("captured", False),
            "firefox.homepage.url": ("unavailable", None),
        }
        lines, code = comparison.compare(desired, observed, required=True)
        self.assertEqual(code, 3)
        self.assertEqual(
            lines, ["firefox.homepage.url gap unavailable", SETTING + " different"]
        )

    def test_normalized_profile_rejects_unrelated_or_duplicate_keys(self):
        inputs = (
            '{"machine_settings": {}, "machine_settings": {}, "default_settings": {}}',
            '{"machine_settings": {"unmapped-private": true}, "default_settings": {}}',
            '{"machine_settings": {}, "default_settings": {}, "secret": true}',
            '{"machine_settings": {}, "default_settings": {}}',
        )
        for text in inputs:
            with self.subTest(text=text):
                with self.assertRaises(comparison.CompareError):
                    comparison.parse_profile(
                        text,
                        catalog.BROWSER_SETTINGS,
                        reader.value_allowed,
                        ["firefox"],
                    )

    def test_nonregular_comparison_input_does_not_block(self):
        fifo = self.root / "input-fifo"
        os.mkfifo(fifo)
        with self.assertRaises(comparison.CompareError):
            comparison.read_text(fifo)

    def test_check_normalized_profile_against_fixture_sources(self):
        profile = self.root / "profile"
        profile.mkdir()
        (profile / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=158.0.1_20261101010101/20261101010101\n"
        )
        (profile / "prefs.js").write_text(
            'user_pref("browser.newtabpage.enabled", true);'
        )
        policies = self.root / "policies"
        policies.mkdir()
        export = self.root / "profile.json"

        def check(value):
            export.write_text(
                json.dumps(
                    {"machine_settings": {SETTING: value}, "default_settings": {}}
                )
            )
            return self.cli(
                "check",
                "--browser",
                "firefox",
                "--profile-json",
                export,
                "--firefox-profile",
                profile,
                "--policy-root",
                policies,
            )

        result = check(True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, SETTING + " same\n")
        export.write_text(
            json.dumps(
                {"machine_settings": {"firefox.lock": True}, "default_settings": {}}
            )
        )
        result = self.cli(
            "check",
            "--browser",
            "firefox",
            "--profile-json",
            export,
            "--firefox-profile",
            profile,
            "--policy-root",
            policies,
        )
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "firefox.lock gap unverified\n")
        result = check(False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, SETTING + " different\n")
        (profile / "prefs.js").write_text("// Artificial absent input\n")
        result = check(True)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, SETTING + " gap absent\n")


if __name__ == "__main__":
    unittest.main()
