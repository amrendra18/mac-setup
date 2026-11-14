import copy
import importlib.util
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from test_macos_firefox import FakeMac, PolicyStore, ensure_firefox

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "appearance_reader", ROOT / "module_utils/browser_settings.py"
)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)
SPEC = importlib.util.spec_from_file_location(
    "appearance_data", ROOT / "module_utils/browser_settings_data.py"
)
data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data)

ADDONS = "firefox.addons.install"
LAYOUT = "firefox.pref.browser_ui_customization_state"
ZOOM = "firefox.pref.browser_content_full_zoom"
ADDON = "FirefoxColor@mozilla.com"
SECOND_ADDON = "artificial-public@example.invalid"
PRIVATE_ID = "PRIVATE_EXTENSION@example.invalid"


class FirefoxExtensionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.unrelated = {
            "*": {"installation_mode": "allowed"},
            "unrelated@example.invalid": {
                "installation_mode": "blocked",
                "blocked_install_message": "Artificial administrator policy",
            },
        }
        self.mac = FakeMac(
            {
                "ExtensionSettings": {
                    **copy.deepcopy(self.unrelated),
                    ADDON: {"private_browsing": True},
                },
                "UnrelatedPolicy": {"keep": True},
            }
        )
        self.store = PolicyStore(
            self.mac,
            self.root / "org.mozilla.firefox",
            self.root / "owned.plist",
            uid=os.getuid(),
            managed_paths=(),
        )

    def apply(self, settings):
        return ensure_firefox(self.store, settings)

    def assert_unrelated_preserved(self):
        self.assertEqual(self.mac.policies["UnrelatedPolicy"], {"keep": True})
        for key, expected in self.unrelated.items():
            self.assertEqual(self.mac.policies["ExtensionSettings"][key], expected)
        self.assertIs(
            self.mac.policies["ExtensionSettings"][ADDON]["private_browsing"], True
        )

    def test_extension_apply_preserves_other_ids_and_fields(self):
        result = self.apply({ADDONS: [ADDON]})
        self.assertTrue(result["changed"])
        self.assertTrue(result["stored_verified"])
        self.assertEqual(
            self.mac.policies["ExtensionSettings"][ADDON],
            {
                "private_browsing": True,
                "installation_mode": "force_installed",
                "install_url": "https://addons.mozilla.org/firefox/downloads/latest/"
                + ADDON
                + "/latest.xpi",
            },
        )
        self.assert_unrelated_preserved()

    def test_reapplying_extensions_changes_no_policies_or_owned_file(self):
        settings = {ADDONS: [ADDON]}
        self.apply(settings)
        policies = copy.deepcopy(self.mac.policies)
        owned = self.store.owned.read_bytes()
        modified = self.store.owned.stat().st_mtime_ns
        imports = len(self.mac.imports)
        result = self.apply(settings)
        self.assertFalse(result["changed"])
        self.assertEqual(self.mac.policies, policies)
        self.assertEqual(self.store.owned.read_bytes(), owned)
        self.assertEqual(self.store.owned.stat().st_mtime_ns, modified)
        self.assertEqual(len(self.mac.imports), imports)

    def test_lock_false_keeps_extension_installed_and_editable(self):
        self.apply({ADDONS: [ADDON]})
        result = self.apply({"firefox.lock": False, ADDONS: [ADDON]})
        self.assertTrue(result["stored_verified"])
        self.assertEqual(
            self.mac.policies["ExtensionSettings"][ADDON]["installation_mode"],
            "normal_installed",
        )
        self.assert_unrelated_preserved()
        self.assertFalse(
            self.apply({"firefox.lock": False, ADDONS: [ADDON]})["changed"]
        )

    def test_empty_and_unset_release_only_owned_extension_fields(self):
        for value in ([], "unset"):
            with self.subTest(value=value):
                self.apply({ADDONS: [ADDON, SECOND_ADDON]})
                result = self.apply({ADDONS: value})
                self.assertTrue(result["stored_verified"])
                self.assertEqual(
                    self.mac.policies["ExtensionSettings"][ADDON],
                    {"private_browsing": True},
                )
                self.assertNotIn(SECOND_ADDON, self.mac.policies["ExtensionSettings"])
                self.assert_unrelated_preserved()
                self.assertFalse(self.apply({ADDONS: value})["changed"])

    def test_replacement_releases_prior_id_and_preserves_its_other_fields(self):
        self.apply({ADDONS: [ADDON]})
        self.apply({ADDONS: [SECOND_ADDON]})
        self.assertEqual(
            self.mac.policies["ExtensionSettings"][ADDON], {"private_browsing": True}
        )
        self.assertEqual(
            self.mac.policies["ExtensionSettings"][SECOND_ADDON]["installation_mode"],
            "force_installed",
        )
        self.assert_unrelated_preserved()


class FirefoxCaptureStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=158.0.1_20261101010101/20261101010101\n"
        )
        self.write_prefs({})

    def write_prefs(self, values):
        (self.root / "prefs.js").write_text(
            "".join(
                "user_pref(" + json.dumps(key) + ", " + json.dumps(value) + ");\n"
                for key, value in values.items()
            )
        )

    def capture(self, *keys):
        return reader.read_browser(
            "firefox",
            {key: data.BROWSER_SETTINGS[key] for key in keys},
            self.root,
            self.root,
            [],
            process_check=lambda family: None,
        )

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.root.iterdir()}

    def create_zoom_database(self, global_values=(1.25,)):
        path = self.root / "content-prefs.sqlite"
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(
                "CREATE TABLE groups (id INTEGER PRIMARY KEY, name TEXT);"
                "CREATE TABLE settings (id INTEGER PRIMARY KEY, name TEXT);"
                "CREATE TABLE prefs (groupID INTEGER, settingID INTEGER, value);"
                "INSERT INTO groups VALUES (1, 'PRIVATE_SITE.example.invalid');"
                "INSERT INTO settings VALUES (1, 'browser.content.full-zoom');"
                "INSERT INTO settings VALUES (2, 'PRIVATE_SETTING');"
                "INSERT INTO prefs VALUES (1, 1, 4.25);"
                "INSERT INTO prefs VALUES (NULL, 2, 'PRIVATE_VALUE');"
            )
            connection.executemany(
                "INSERT INTO prefs VALUES (NULL, 1, ?)",
                [(value,) for value in global_values],
            )
            connection.commit()
        return path

    def test_zoom_captures_only_global_value_without_modifying_sources(self):
        self.create_zoom_database()
        before = self.snapshot()
        result = self.capture(ZOOM)
        self.assertEqual(result["settings"][ZOOM]["state"], "captured")
        self.assertEqual(result["settings"][ZOOM]["value"], 1.25)
        output = reader.candidate_yaml([result]) + json.dumps(result)
        for private in ("PRIVATE_SITE", "PRIVATE_SETTING", "PRIVATE_VALUE", "4.25"):
            self.assertNotIn(private, output)
        self.assertEqual(self.snapshot(), before)

    def test_site_zoom_is_not_used_when_global_zoom_is_absent(self):
        self.create_zoom_database(())
        before = self.snapshot()
        self.assertEqual(reader.global_zoom(self.root, reader.Sources()), {})
        result = self.capture(ZOOM)
        self.assertNotIn("value", result["settings"][ZOOM])
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_global_zoom_is_refused_without_modifying_sources(self):
        self.create_zoom_database((1.25, 1.5))
        before = self.snapshot()
        with self.assertRaises(reader.CaptureError):
            self.capture(ZOOM)
        self.assertEqual(self.snapshot(), before)

    def test_corrupt_zoom_database_is_refused_without_modifying_sources(self):
        (self.root / "content-prefs.sqlite").write_bytes(b"PRIVATE_CORRUPT_DATABASE")
        before = self.snapshot()
        with self.assertRaises(reader.CaptureError) as caught:
            self.capture(ZOOM)
        self.assertNotIn("PRIVATE_CORRUPT_DATABASE", str(caught.exception))
        self.assertEqual(self.snapshot(), before)

    def test_active_zoom_wal_is_refused_without_modifying_sources(self):
        path = self.create_zoom_database()
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("UPDATE prefs SET value=1.5 WHERE groupID IS NULL")
        connection.commit()
        self.assertGreater(Path(str(path) + "-wal").stat().st_size, 0)
        before = self.snapshot()
        with self.assertRaises(reader.CaptureError):
            self.capture(ZOOM)
        self.assertEqual(self.snapshot(), before)

    def test_unsigned_extension_stays_private_with_an_amo_source_uri(self):
        (self.root / "extensions.json").write_text(
            json.dumps(
                {
                    "addons": [
                        {
                            "id": PRIVATE_ID,
                            "type": "extension",
                            "sourceURI": "https://addons.mozilla.org/firefox/downloads/file/123/artificial.xpi",
                            "defaultLocale": {"name": "PRIVATE_EXTENSION_NAME"},
                        }
                    ]
                }
            )
        )
        self.write_prefs(
            {
                "browser.uiCustomization.state": json.dumps(
                    {
                        "placements": {
                            "nav-bar": [
                                "private_extension_example_invalid-browser-action"
                            ]
                        }
                    }
                )
            }
        )
        result = self.capture(ADDONS, LAYOUT)
        self.assertEqual(result["extensions"], {"public": [], "manual_count": 1})
        self.assertEqual(result["settings"][LAYOUT]["state"], "masked")
        output = reader.candidate_yaml([result]) + json.dumps(result)
        for private in (PRIVATE_ID, "PRIVATE_EXTENSION_NAME", "private_extension"):
            self.assertNotIn(private, output)

    def write_public_addon(self, **activation):
        (self.root / "extensions.json").write_text(
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
                            "sourceURI": "https://addons.mozilla.org/firefox/downloads/file/123/artificial.xpi",
                            "defaultLocale": {"name": "Firefox Color"},
                            **activation,
                        }
                    ]
                }
            )
        )

    def test_disabled_addon_is_a_gap_and_has_no_install_assignment(self):
        self.write_public_addon(active=True, userDisabled=False)
        enabled = self.capture(ADDONS)
        self.assertEqual(enabled["settings"][ADDONS]["state"], "captured")
        self.assertEqual(enabled["settings"][ADDONS]["value"], [ADDON])
        self.write_public_addon(active=False, userDisabled=True)
        disabled = self.capture(ADDONS)
        self.assertEqual(disabled["settings"][ADDONS]["state"], "unverified")
        self.assertNotIn("value", disabled["settings"][ADDONS])
        self.assertNotIn("  " + ADDONS + ": ", reader.candidate_yaml([disabled]))
        first = self.root / "enabled.yml"
        second = self.root / "disabled.yml"
        first.write_text(reader.candidate_yaml([enabled]))
        second.write_text(reader.candidate_yaml([disabled]))
        for source in (first, second):
            with self.subTest(source=source.name):
                result = subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-B",
                        str(ROOT / "scripts/browser-settings"),
                        "diff",
                        str(source),
                        str(second),
                    ],
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, ADDONS + " gap unverified\n")

    def test_missing_malformed_or_inconsistent_addon_activation_is_a_gap(self):
        cases = (
            {},
            {"active": True},
            {"active": 1, "userDisabled": False},
            {"active": True, "userDisabled": 0},
            {"active": True, "userDisabled": True},
            {"active": False, "userDisabled": False},
            {"active": True, "userDisabled": False, "appDisabled": True},
        )
        for activation in cases:
            with self.subTest(activation=activation):
                self.write_public_addon(**activation)
                result = self.capture(ADDONS)
                self.assertEqual(result["settings"][ADDONS]["state"], "unverified")
                self.assertNotIn("value", result["settings"][ADDONS])

    def test_unknown_toolbar_fields_and_values_are_masked(self):
        layouts = (
            {"placements": {"nav-bar": ["urlbar-container"]}, "PRIVATE_FIELD": True},
            {"placements": {"PRIVATE_AREA": ["urlbar-container"]}},
            {"placements": {"nav-bar": ["PRIVATE_WIDGET"]}},
            {"placements": {"nav-bar": []}, "seen": ["PRIVATE_SEEN"]},
            {"placements": {"nav-bar": []}, "dirtyAreaCache": ["PRIVATE_CACHE"]},
            {"placements": {"nav-bar": []}, "currentVersion": "PRIVATE_VERSION"},
            {"placements": {"nav-bar": []}, "newElementCount": "PRIVATE_COUNT"},
        )
        for layout in layouts:
            with self.subTest(layout=layout):
                self.write_prefs({"browser.uiCustomization.state": json.dumps(layout)})
                result = self.capture(LAYOUT)
                self.assertEqual(result["settings"][LAYOUT]["state"], "masked")
                self.assertNotIn(
                    "PRIVATE_", reader.candidate_yaml([result]) + json.dumps(result)
                )


if __name__ == "__main__":
    unittest.main()
