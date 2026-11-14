import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "module_utils" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = load("browser_settings")
planner = load("firefox_policies")
data = load("browser_settings_data")
ADDON = "FirefoxColor@mozilla.com"


class FirefoxAppearanceTests(unittest.TestCase):
    def test_automatic_crash_upload_opt_out_follows_lock(self):
        setting_id = "firefox.pref.browser_crash_reports_unsubmitted_check_auto_submit2"
        for lock in (True, False):
            plan = planner.plan_policies({"firefox.lock": lock, setting_id: False})
            path = ("Preferences", "browser.crashReports.unsubmittedCheck.autoSubmit2")
            self.assertIs(plan["writes"][path + ("Value",)], False)
            self.assertEqual(
                plan["writes"][path + ("Status",)], "locked" if lock else "user"
            )

    def test_owner_label_and_extensions_compile_both_lock_modes(self):
        for locked in (True, False):
            plan = planner.plan_policies(
                {
                    "firefox.lock": locked,
                    "firefox.owner_label": "TEST",
                    "firefox.addons.install": [ADDON],
                }
            )
            self.assertEqual(
                plan["writes"][("Preferences", "browser.mac-setup.owner", "Value")],
                "TEST",
            )
            self.assertEqual(
                plan["writes"][("Preferences", "browser.mac-setup.owner", "Status")],
                "locked" if locked else "user",
            )
            self.assertEqual(
                plan["writes"][("ExtensionSettings", ADDON, "installation_mode")],
                "force_installed" if locked else "normal_installed",
            )
            self.assertEqual(
                plan["writes"][("ExtensionSettings", ADDON, "install_url")],
                "https://addons.mozilla.org/firefox/downloads/latest/FirefoxColor@mozilla.com/latest.xpi",
            )
            self.assertIn("Firefox owner label: TEST", plan["notes"])

    def test_layout_stays_editable_and_supported_preferences_follow_lock(self):
        layout = json.dumps(
            {"placements": {"nav-bar": ["urlbar-container", "back-button"]}}
        )
        plan = planner.plan_policies(
            {
                "firefox.pref.browser_ui_customization_state": layout,
                "firefox.pref.sidebar_vertical_tabs": True,
            }
        )
        self.assertEqual(
            plan["writes"][("Preferences", "browser.uiCustomization.state", "Status")],
            "user",
        )
        self.assertEqual(
            plan["writes"][("Preferences", "sidebar.verticalTabs", "Status")], "locked"
        )
        self.assertTrue(any("rearrange" in note for note in plan["notes"]))

    def test_capture_appearance_addons_and_unknown_layout_privacy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "compatibility.ini").write_text(
                "[Compatibility]\nLastVersion=158.0.1_20261101010101/20261101010101\n"
            )
            layout = {
                "placements": {
                    "nav-bar": [
                        "urlbar-container",
                        "firefoxcolor_mozilla_com-browser-action",
                    ]
                }
            }
            prefs = {
                "browser.uiCustomization.state": json.dumps(layout),
                "sidebar.verticalTabs": True,
                "extensions.activeThemeID": "firefox-compact-dark@mozilla.org",
                "font.name.serif.x-western": "Times",
            }
            (root / "prefs.js").write_text(
                "".join(
                    "user_pref(" + json.dumps(key) + ", " + json.dumps(value) + ");\n"
                    for key, value in prefs.items()
                )
            )
            (root / "extensions.json").write_text(
                json.dumps(
                    {
                        "addons": [
                            {
                                "id": ADDON,
                                "type": "extension",
                                "location": "app-profile",
                                "foreignInstall": False,
                                "hidden": False,
                                "visible": True,
                                "signedState": 4,
                                "sourceURI": "https://addons.mozilla.org/firefox/downloads/file/123/test.xpi",
                                "defaultLocale": {"name": "Firefox Color"},
                                "storage": "PRIVATE_STORAGE",
                            },
                            {
                                "id": "PRIVATE_ID@example.invalid",
                                "type": "extension",
                                "sourceURI": "https://example.invalid/private",
                                "defaultLocale": {"name": "PRIVATE_NAME"},
                            },
                        ]
                    }
                )
            )
            result = reader.read_browser(
                "firefox",
                data.BROWSER_SETTINGS,
                root,
                root,
                [],
                process_check=lambda family: None,
            )
            settings = result["settings"]
            self.assertEqual(settings["firefox.addons.install"]["state"], "unverified")
            self.assertEqual(
                settings["firefox.addons.install"]["observations"][0]["value"], [ADDON]
            )
            self.assertEqual(
                settings["firefox.pref.sidebar_vertical_tabs"]["value"], True
            )
            self.assertEqual(
                json.loads(
                    settings["firefox.pref.browser_ui_customization_state"]["value"]
                ),
                layout,
            )
            self.assertEqual(
                settings["firefox.pref.font_name_serif_x_western"]["value"], "Times"
            )
            self.assertEqual(
                result["extensions"]["public"], [{"id": ADDON, "name": "Firefox Color"}]
            )
            self.assertEqual(result["extensions"]["manual_count"], 1)
            text = reader.candidate_yaml([result]) + json.dumps(result)
            for value in ("PRIVATE_STORAGE", "PRIVATE_ID", "PRIVATE_NAME"):
                self.assertNotIn(value, text)
            prefs["browser.uiCustomization.state"] = json.dumps(
                {"placements": {"nav-bar": ["PRIVATE_WIDGET"]}}
            )
            (root / "prefs.js").write_text(
                "".join(
                    "user_pref(" + json.dumps(key) + ", " + json.dumps(value) + ");\n"
                    for key, value in prefs.items()
                )
            )
            result = reader.read_browser(
                "firefox",
                data.BROWSER_SETTINGS,
                root,
                root,
                [],
                process_check=lambda family: None,
            )
            self.assertEqual(
                result["settings"]["firefox.pref.browser_ui_customization_state"][
                    "state"
                ],
                "masked",
            )
            self.assertNotIn(
                "PRIVATE_WIDGET", reader.candidate_yaml([result]) + json.dumps(result)
            )

    def test_owner_label_is_masked_in_capture(self):
        row = data.BROWSER_SETTINGS["firefox.owner_label"]
        self.assertEqual(reader.filter_value(row, "PRIVATE_OWNER"), (False, None))

    def test_extension_ids_reject_url_paths_and_duplicate_ids(self):
        for ids in (
            ["https://example.invalid/secret"],
            ["../../secret"],
            [ADDON, ADDON],
        ):
            with self.subTest(ids=ids), self.assertRaises(planner.FirefoxError):
                planner.plan_policies({"firefox.addons.install": ids})


if __name__ == "__main__":
    unittest.main()
