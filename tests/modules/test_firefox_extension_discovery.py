import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "module_utils" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = load("browser_settings")
data = load("browser_settings_data")
ADDON = "artificial-new-public@example.invalid"
NAME = "Artificial New Public Extension"


def public_record(**changes):
    return {
        "id": ADDON,
        "type": "extension",
        "location": "app-profile",
        "foreignInstall": False,
        "hidden": False,
        "visible": True,
        "signedState": 2,
        "sourceURI": "https://addons.mozilla.org/firefox/downloads/file/123/artificial.xpi",
        "defaultLocale": {"name": NAME},
        "active": True,
        "userDisabled": False,
        "appDisabled": False,
        **changes,
    }


class FirefoxExtensionDiscoveryTests(unittest.TestCase):
    def discover(self, *records):
        return reader.public_extensions({"addons": list(records)})

    def test_new_amo_id_and_saved_name_are_discovered_without_a_catalog(self):
        for state in (2, 4):
            with self.subTest(signedState=state):
                self.assertEqual(
                    self.discover(public_record(signedState=state)),
                    {"public": [{"id": ADDON, "name": NAME}], "manual_count": 0},
                )

    def test_names_are_read_from_firefox_instead_of_replaced_by_a_catalog(self):
        result = self.discover(
            public_record(
                id="FirefoxColor@mozilla.com",
                defaultLocale={"name": "Artificial Firefox Display Name"},
            )
        )
        self.assertEqual(
            result["public"],
            [
                {
                    "id": "FirefoxColor@mozilla.com",
                    "name": "Artificial Firefox Display Name",
                }
            ],
        )

    def test_signed_self_hosted_addon_is_masked_and_counted(self):
        result = self.discover(
            public_record(
                id="PRIVATE_ID@example.invalid",
                sourceURI="https://example.invalid/PRIVATE_DOWNLOAD.xpi",
                defaultLocale={"name": "PRIVATE_NAME"},
            )
        )
        self.assertEqual(result, {"public": [], "manual_count": 1})
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_system_hidden_and_temporary_addons_are_excluded_and_counted(self):
        cases = (
            {"isSystem": True},
            {"hidden": True},
            {"visible": False},
            {"location": "app-system-addons"},
            {"location": "app-builtin-addons"},
            {"location": "app-temporary"},
            {"temporarilyInstalled": True},
            {"signedState": 3},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual(
                    self.discover(public_record(**changes)),
                    {"public": [], "manual_count": 0, "excluded_count": 1},
                )

    def test_missing_or_untrusted_signatures_and_install_metadata_are_masked(self):
        for state in (None, -2, -1, 0, 1, 5, True, "2", 2.0):
            with self.subTest(signedState=state):
                self.assertEqual(
                    self.discover(public_record(signedState=state)),
                    {"public": [], "manual_count": 1},
                )
        for field in ("location", "foreignInstall", "visible", "hidden", "signedState"):
            record = public_record()
            del record[field]
            with self.subTest(missing=field):
                self.assertEqual(
                    self.discover(record), {"public": [], "manual_count": 1}
                )
        for changes in ({"foreignInstall": True}, {"location": "app-global"}):
            self.assertEqual(
                self.discover(public_record(**changes)),
                {"public": [], "manual_count": 1},
            )

    def test_source_uri_requires_exact_https_amo_download_origin(self):
        cases = (
            None,
            "",
            "http://addons.mozilla.org/firefox/downloads/file/123/artificial.xpi",
            "https://addons.mozilla.org.example.invalid/firefox/downloads/file/123/a.xpi",
            "https://user:@addons.mozilla.org/firefox/downloads/file/123/a.xpi",
            "https://addons.mozilla.org:443/firefox/downloads/file/123/a.xpi",
            "https://addons.mozilla.org./firefox/downloads/file/123/a.xpi",
            "https://addons.mozilla.org/firefox/addon/artificial/",
            "https://addons.mozilla.org/firefox/downloads/",
            "https://addons.mozilla.org/firefox/downloads/../PRIVATE",
            "https://addons.mozilla.org/firefox/downloads/file/123/a.xpi\n",
            "file:///PRIVATE_EXTENSION.xpi",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(
                    self.discover(public_record(sourceURI=source)),
                    {"public": [], "manual_count": 1},
                )

    def test_author_update_url_never_makes_a_private_or_unknown_source_public(self):
        for source in (None, "https://example.invalid/PRIVATE_DOWNLOAD.xpi"):
            for update in (
                "https://addons.mozilla.org/",
                "https://addons.mozilla.org/not-an-update-endpoint",
                "https://addons.mozilla.org/api/v5/addons/addon/another-id/",
            ):
                for state in (2, 4):
                    with self.subTest(source=source, update=update, signedState=state):
                        result = self.discover(
                            public_record(
                                id="PRIVATE_ID@example.invalid",
                                sourceURI=source,
                                updateURL=update,
                                signedState=state,
                                defaultLocale={"name": "PRIVATE_NAME"},
                            )
                        )
                        self.assertEqual(result, {"public": [], "manual_count": 1})
                        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_public_download_provenance_is_independent_of_the_author_update_url(self):
        result = self.discover(
            public_record(updateURL="https://example.invalid/PRIVATE_UPDATE")
        )
        self.assertEqual(
            result, {"public": [{"id": ADDON, "name": NAME}], "manual_count": 0}
        )

    def test_invalid_ids_names_and_unrecorded_source_fields_stay_masked(self):
        cases = (
            {"id": "../../PRIVATE"},
            {"defaultLocale": {"name": ""}},
            {"defaultLocale": {"name": "x" * 121}},
            {"defaultLocale": {"name": "PRIVATE\nNAME"}},
            {"defaultLocale": {"name": "PRIVATE\x7fNAME"}},
            {"defaultLocale": {"name": "PRIVATE\u202eNAME"}},
            {"defaultLocale": None},
            {"sourceURI": None, "sourceURL": public_record()["sourceURI"]},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual(
                    self.discover(public_record(**changes)),
                    {"public": [], "manual_count": 1},
                )

    def test_builtin_themes_are_excluded_and_other_addon_types_stay_out_of_scope(self):
        self.assertEqual(
            self.discover(
                public_record(id="default-theme@mozilla.org", type="theme"),
                public_record(type="dictionary"),
                public_record(type="locale"),
            ),
            {"public": [], "manual_count": 0, "excluded_count": 1},
        )

    def test_disabled_public_addon_keeps_its_activation_gap(self):
        result = self.discover(public_record(active=False, userDisabled=True))
        self.assertEqual(result["public"], [{"id": ADDON, "name": NAME}])
        self.assertEqual(result["activation_unverified_count"], 1)

    def test_capture_is_offline_and_uses_discovered_theme_and_toolbar_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            layout = {
                "placements": {
                    "nav-bar": ["artificial-new-public_example_invalid-browser-action"]
                }
            }
            prefs = {
                "browser.uiCustomization.state": json.dumps(layout),
                "extensions.activeThemeID": ADDON,
            }
            (profile / "compatibility.ini").write_text(
                "[Compatibility]\nLastVersion=160.0_20261101010101/20261101010101\n"
            )
            (profile / "prefs.js").write_text(
                "".join(
                    "user_pref(" + json.dumps(key) + ", " + json.dumps(value) + ");\n"
                    for key, value in prefs.items()
                )
            )
            (profile / "extensions.json").write_text(
                json.dumps(
                    {
                        "addons": [
                            public_record(type="theme"),
                            public_record(
                                id="PRIVATE_SYSTEM@example.invalid",
                                location="app-builtin-addons",
                            ),
                        ]
                    }
                )
            )
            with patch(
                "socket.socket", side_effect=AssertionError("network")
            ) as sockets, patch(
                "socket.create_connection", side_effect=AssertionError("network")
            ) as connections, patch(
                "socket.getaddrinfo", side_effect=AssertionError("network")
            ) as dns, patch(
                "urllib.request.urlopen", side_effect=AssertionError("network")
            ) as requests:
                result = reader.read_browser(
                    "firefox",
                    data.BROWSER_SETTINGS,
                    profile,
                    profile,
                    [],
                    process_check=lambda family: None,
                )
            for network in (sockets, connections, dns, requests):
                network.assert_not_called()
            settings = result["settings"]
            self.assertEqual(settings["firefox.addons.install"]["value"], [ADDON])
            self.assertEqual(
                settings["firefox.pref.extensions_active_theme_id"]["value"], ADDON
            )
            self.assertEqual(
                json.loads(
                    settings["firefox.pref.browser_ui_customization_state"]["value"]
                ),
                layout,
            )
            self.assertIn(NAME, reader.candidate_yaml([result]))
            self.assertEqual(result["extensions"]["excluded_count"], 1)
            self.assertNotIn("PRIVATE", reader.candidate_yaml([result]))
            self.assertIn("add-ons excluded: 1", reader.candidate_yaml([result]))

    def test_system_python_cli_captures_new_public_id_and_masks_private_addon(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = root / "profile"
            profile.mkdir()
            (profile / "compatibility.ini").write_text(
                "[Compatibility]\nLastVersion=160.0_20261101010101/20261101010101\n"
            )
            (profile / "prefs.js").write_text(
                'user_pref("browser.newtabpage.enabled", false);\n'
            )
            (profile / "extensions.json").write_text(
                json.dumps(
                    {
                        "addons": [
                            public_record(),
                            public_record(
                                id="PRIVATE_ID@example.invalid",
                                sourceURI="https://example.invalid/PRIVATE.xpi",
                                updateURL="https://addons.mozilla.org/",
                                defaultLocale={"name": "PRIVATE_NAME"},
                            ),
                        ]
                    }
                )
            )
            output = root / "capture.yml"
            command = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    str(ROOT / "scripts/browser-settings"),
                    "capture",
                    "--browser",
                    "firefox",
                    "--firefox-profile",
                    str(profile),
                    "--policy-root",
                    str(root / "missing-policies"),
                    "--output",
                    str(output),
                ],
                cwd=profile,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(command.returncode, 0, command.stderr)
            captured = output.read_text()
            self.assertIn(ADDON, captured)
            self.assertIn(NAME, captured)
            self.assertIn("Add-ons needing manual review: 1", captured)
            self.assertNotIn("PRIVATE", captured + command.stdout + command.stderr)
            readback = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    str(ROOT / "scripts/browser-settings"),
                    "read-back",
                    "--browser",
                    "firefox",
                    "--firefox-profile",
                    str(profile),
                    "--policy-root",
                    str(root / "missing-policies"),
                ],
                cwd=profile,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(readback.returncode, 0, readback.stderr)
            self.assertIn(ADDON, readback.stdout)
            self.assertIn(NAME, readback.stdout)
            self.assertNotIn("PRIVATE", readback.stdout + readback.stderr)


if __name__ == "__main__":
    unittest.main()
