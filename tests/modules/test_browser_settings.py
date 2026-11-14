import contextlib
import importlib.util
import json
import plistlib
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "browser_reader", ROOT / "module_utils/browser_settings.py"
)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


def row(
    family="firefox", source="firefox_prefs", key="safe.enabled", kind="bool", **capture
):
    return {
        "scope": "machine",
        "values": {"type": "bool"},
        "browser": {
            "family": family,
            "kind": "setting",
            "read": [
                {
                    "source": source,
                    "path": key.split("|"),
                    "type": kind,
                    "conversion": "identity",
                }
            ],
            "capture": {"class": "scalar", **capture},
            "support": {"min": "158.0.1", "max": "158.0.1", "tested": "158.0.1"},
        },
    }


class BrowserReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.policies = self.root / "policies"
        self.policies.mkdir()
        (self.profile / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=158.0.1_20261101010101/20261101010101\n"
        )
        (self.profile / "prefs.js").write_text('user_pref("safe.enabled", true);\n')
        self.rows = {"firefox.safe": row()}

    def capture(self, rows=None, **kwargs):
        return reader.read_browser(
            "firefox",
            rows or self.rows,
            self.profile,
            self.root,
            [self.policies / "org.mozilla.firefox.plist"],
            process_check=lambda family: None,
            **kwargs
        )

    def test_firefox_pref_grammar(self):
        values = reader.parse_firefox_prefs(
            "/* test input */ user_pref('safe.enabled', true); // comment\n"
            'user_pref("number", -12); user_pref("text", "a\\n\\u0041\\x42\\"\\\\");'
            'user_pref("number", +3);'
        )
        self.assertEqual(
            values, {"safe.enabled": True, "number": 3, "text": 'a\nAB"\\'}
        )

    def test_firefox_never_executes_pref_input(self):
        sentinel = self.root / "never-execute-test-input"
        command = "touch " + shlex.quote(str(sentinel))
        # Deliberately executable test inputs use the exact watched temporary path.
        payloads = (
            'user_pref("x", __import__("os").system(' + json.dumps(command) + "));",
            'user_pref("x", __import__("subprocess").run(["touch", '
            + json.dumps(str(sentinel))
            + "]));",
            'user_pref("x", __import__("pathlib").Path('
            + json.dumps(str(sentinel))
            + ').write_text("executed"));',
        )
        process_functions = {
            reader.subprocess: ("run", "Popen", "call", "check_call", "check_output"),
            reader.os: (
                "system",
                "popen",
                "posix_spawn",
                "posix_spawnp",
                "spawnv",
                "spawnve",
                "spawnvp",
                "spawnvpe",
                "spawnl",
                "spawnle",
                "spawnlp",
                "spawnlpe",
                "execl",
                "execle",
                "execlp",
                "execlpe",
                "execv",
                "execve",
                "execvp",
                "execvpe",
            ),
        }
        for payload in payloads:
            with self.subTest(payload=payload):
                sentinel.unlink(missing_ok=True)
                with contextlib.ExitStack() as stack:
                    calls = [
                        stack.enter_context(patch.object(module, name))
                        for module, names in process_functions.items()
                        for name in names
                        if hasattr(module, name)
                    ]
                    with self.assertRaises(reader.CaptureError) as error:
                        reader.parse_firefox_prefs(payload)
                    self.assertEqual(
                        str(error.exception), "Could not parse firefox_prefs."
                    )
                    for call in calls:
                        call.assert_not_called()
                self.assertFalse(sentinel.exists())

    def test_firefox_saved_and_startup_values_stay_distinct(self):
        self.rows["firefox.safe"]["browser"]["read"].append(
            {
                "source": "firefox_user",
                "path": ["safe.enabled"],
                "type": "bool",
                "conversion": "identity",
            }
        )
        (self.profile / "user.js").write_text('user_pref("safe.enabled", false);')
        result = self.capture()
        observations = result["settings"]["firefox.safe"]["observations"]
        self.assertEqual(
            [item["state"] for item in observations], ["persisted", "startup_override"]
        )
        self.assertEqual([item["value"] for item in observations], [True, False])
        self.assertEqual(result["settings"]["firefox.safe"]["state"], "conflict")
        self.assertNotIn("value", result["settings"]["firefox.safe"])

    def test_capture_never_opens_personal_stores(self):
        opened = []
        original = reader.read_bytes

        def record(path):
            opened.append(Path(path).name)
            return original(path)

        for name in (
            "places.sqlite",
            "cookies.sqlite",
            "logins.json",
            "sessionstore.jsonlz4",
        ):
            (self.profile / name).write_text("Deliberately personal test input")
        with patch.object(reader, "read_bytes", side_effect=record):
            result = self.capture()
        self.assertEqual(result["settings"]["firefox.safe"]["value"], True)
        self.assertEqual(set(opened), {"prefs.js", "compatibility.ini"})

    def test_only_mapped_ids_leave_the_reader(self):
        (self.profile / "prefs.js").write_text(
            'user_pref("safe.enabled", true); user_pref("unknown@example.invalid", "private");'
        )
        result = self.capture()
        self.assertEqual(result["counts"]["unmapped_persisted_entries"], 1)
        self.assertNotIn("example.invalid", json.dumps(result))
        self.assertEqual(result["counts"]["captured"], 1)

    def test_personal_url_email_path_and_account_id_are_masked(self):
        # Deliberately personal test inputs, each beside the positive control.
        for value in (
            "https://private.invalid/a",
            "person@example.invalid",
            "/private/fixture",
            "account-123456",
        ):
            with self.subTest(value=value):
                rows = dict(self.rows)
                rows["firefox.private"] = row(
                    key="private",
                    kind="text",
                    **{"class": "mask", "reason": "Private text."}
                )
                (self.profile / "prefs.js").write_text(
                    'user_pref("safe.enabled", true); user_pref("private", '
                    + json.dumps(value)
                    + ");"
                )
                result = self.capture(rows)
                self.assertEqual(result["counts"]["captured"], 1)
                self.assertEqual(result["counts"]["masked"], 1)
                self.assertEqual(result["settings"]["firefox.safe"]["value"], True)
                self.assertNotIn(value, json.dumps(result))

    def test_public_endpoint_exception_is_exact(self):
        entry = row(
            kind="text",
            **{
                "class": "public_endpoint",
                "choices": ["https://dns.quad9.net/dns-query"],
            }
        )
        entry["values"] = {"type": "text", "pattern": ".*"}
        for value, expected in (
            ("https://dns.quad9.net/dns-query", True),
            ("https://dns.quad9.net/dns-query?token=x", False),
            ("https://user:@dns.quad9.net/dns-query", False),
        ):
            self.assertEqual(reader.filter_value(entry, value)[0], expected)

    def test_nested_dynamic_keys_and_values_are_masked(self):
        entry = row(kind="object", **{"class": "mask", "reason": "Private site rules."})
        for value in (
            {"person@example.invalid": True},
            {"safe": "person@example.invalid"},
        ):
            self.assertEqual(reader.filter_value(entry, value), (False, None))

    def test_malformed_input_errors_do_not_echo_data(self):
        for source, payload in (
            ("firefox_prefs", b'user_pref("personal@example.invalid", ???);'),
            ("chrome_preferences", b'{"personal@example.invalid":'),
            ("chrome_policy", b"<personal@example.invalid>"),
        ):
            with self.subTest(source=source):
                with self.assertRaises(reader.CaptureError) as error:
                    reader.parse_source(source, payload)
                self.assertNotIn("example.invalid", str(error.exception))
                self.assertEqual(error.exception.code, 3)

    def test_policy_plist_xml_and_binary(self):
        entry = row(source="firefox_policy", key="Safe")
        rows = {"firefox.safe": entry}
        results = []
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            (self.policies / "org.mozilla.firefox.plist").write_bytes(
                plistlib.dumps(
                    {"EnterprisePoliciesEnabled": True, "Safe": True}, fmt=fmt
                )
            )
            results.append(self.capture(rows)["settings"])
        self.assertEqual(results[0], results[1])
        self.assertEqual(
            results[0]["firefox.safe"]["observations"][0]["state"], "policy_stored"
        )
        self.assertEqual(
            results[0]["firefox.safe"]["observations"][0]["lock"], "unknown"
        )

    def test_running_or_changed_browser_refuses_capture(self):
        def running(family):
            raise reader.CaptureError(3, "Quit the selected browser with Command-Q.")

        with self.assertRaises(reader.CaptureError):
            reader.read_browser(
                "firefox", self.rows, self.profile, self.root, [], process_check=running
            )
        original = reader.read_bytes

        def mutate(path):
            data = original(path)
            if Path(path).name == "prefs.js":
                Path(path).write_bytes(data + b"\n")
            return data

        with patch.object(reader, "read_bytes", side_effect=mutate):
            with self.assertRaises(reader.CaptureError) as error:
                self.capture()
        self.assertEqual(
            str(error.exception), "A configuration source changed during capture."
        )

    def test_known_removed_versions_are_unavailable(self):
        (self.profile / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=159.0/fixture\n"
        )
        result = self.capture()
        self.assertEqual(result["settings"]["firefox.safe"]["state"], "unavailable")
        self.assertNotIn("value", result["settings"]["firefox.safe"])

    def test_profile_selection_requires_an_explicit_choice(self):
        root = self.root / "Firefox"
        root.mkdir()
        (root / "profiles.ini").write_text(
            "[Profile0]\nPath=fixture-personal-a\nIsRelative=1\n[Profile1]\nPath=fixture-personal-b\nIsRelative=1\n"
        )
        with self.assertRaises(reader.CaptureError) as error:
            reader.select_firefox_profile(root)
        self.assertEqual(error.exception.code, 3)
        self.assertNotIn("fixture-personal", str(error.exception))

    def test_compound_preferences_require_complete_source_evidence(self):
        entry = row()
        entry["browser"]["read"].append(
            {
                "source": "firefox_prefs",
                "path": ["other.enabled"],
                "type": "bool",
                "conversion": "identity",
            }
        )
        rows = {"firefox.compound": entry}
        result = self.capture(rows)
        self.assertEqual(result["settings"]["firefox.compound"]["state"], "unverified")
        self.assertNotIn("value", result["settings"]["firefox.compound"])
        (self.profile / "prefs.js").write_text(
            'user_pref("safe.enabled", true); user_pref("other.enabled", true);'
        )
        result = self.capture(rows)
        self.assertIs(result["settings"]["firefox.compound"]["value"], True)
        self.assertEqual(result["counts"]["captured"], 1)

    def browser_map(self):
        spec = importlib.util.spec_from_file_location(
            "test_browser_data", ROOT / "module_utils/browser_settings_data.py"
        )
        data = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(data)
        return data.BROWSER_SETTINGS

    def test_firefox_preference_policy_encodings_are_alternatives(self):
        entries = self.browser_map()
        key = "firefox.pref.privacy_globalprivacycontrol_enabled"
        rows = {key: entries[key]}
        for value in (True, {"Value": True, "Status": "locked"}):
            with self.subTest(value=value):
                (self.policies / "org.mozilla.firefox.plist").write_bytes(
                    plistlib.dumps(
                        {
                            "EnterprisePoliciesEnabled": True,
                            "Preferences": {
                                "privacy.globalprivacycontrol.enabled": value
                            },
                        }
                    )
                )
                result = self.capture(rows)
                self.assertIs(result["settings"][key]["value"], True)
                self.assertEqual(result["counts"]["captured"], 1)

    def test_newer_browser_profiles_capture_shipped_safe_values(self):
        entries = self.browser_map()
        cases = (
            ("firefox", "160.0", "158.0.1", "firefox.new_tab_page", False),
            (
                "chrome",
                "160.0.0.0",
                "158.0.8127.37",
                "chrome.bookmark_bar_enabled",
                True,
            ),
        )
        for family, observed, tested, key, expected in cases:
            with self.subTest(browser=family):
                (self.profile / "compatibility.ini").write_text(
                    "[Compatibility]\nLastVersion="
                    + observed
                    + "_20261101010101/20261101010101\n"
                )
                (self.profile / "prefs.js").write_text(
                    'user_pref("browser.newtabpage.enabled", false);'
                )
                (self.root / "Last Version").write_text(observed)
                (self.profile / "Preferences").write_text(
                    json.dumps({"bookmark_bar": {"show_on_all_tabs": True}})
                )
                result = reader.read_browser(
                    family,
                    entries,
                    self.profile,
                    self.root,
                    [],
                    process_check=lambda family: None,
                )
                self.assertEqual(result["counts"]["captured"], 1)
                self.assertIs(result["settings"][key]["value"], expected)
                self.assertEqual(result["versions"]["browser"], observed)
                self.assertEqual(result["versions"]["map_tested_on"], [tested])
                self.assertIn(
                    "# "
                    + family
                    + " map tested on "
                    + tested
                    + "; observed "
                    + observed,
                    reader.candidate_yaml([result]),
                )

    def test_shipped_firefox_nested_and_flattened_policy_keys_agree(self):
        entries = self.browser_map()
        key = "firefox.dns_over_https.enabled"
        for document in (
            {"DNSOverHTTPS": {"Enabled": True}},
            {"DNSOverHTTPS__Enabled": True},
            {"DNSOverHTTPS": {"Enabled": True}, "DNSOverHTTPS__Enabled": True},
        ):
            with self.subTest(document=document):
                policy = self.policies / "org.mozilla.firefox.plist"
                policy.write_bytes(
                    plistlib.dumps(dict(document, EnterprisePoliciesEnabled=True))
                )
                result = self.capture(entries)
                self.assertEqual(result["settings"][key]["state"], "captured")
                self.assertIs(result["settings"][key]["value"], True)
                self.assertEqual(
                    result["settings"][key]["observations"][0]["state"], "policy_stored"
                )
                self.assertEqual(result["counts"]["unmapped_policy_entries"], 0)

    def test_firefox_nested_policy_payloads_keep_delimiters(self):
        # Deliberately personal test inputs remain opaque inside extension data.
        extensions = {
            "fixture-addon@example.invalid": {
                "cache__51": "artificial-private",
                "cache____name": {"setting__52": "artificial-private"},
                "nested": [{"cache____name": True}],
            }
        }
        expected = {"3rdparty": {"Extensions": extensions}}
        for document in (expected, {"3rdparty__Extensions": extensions}):
            with self.subTest(root_keys=list(document)):
                self.assertEqual(
                    reader.parse_source("firefox_policy", plistlib.dumps(document)),
                    expected,
                )

    def test_firefox_flattened_policy_arrays_have_bounded_indices(self):
        private_url = "https://artificial-private.invalid/test-input"
        document = {"Cookies__Allow__0": private_url}
        normalized = reader.parse_source("firefox_policy", plistlib.dumps(document))
        self.assertEqual(normalized, {"Cookies": {"Allow": [private_url]}})
        policy = self.policies / "org.mozilla.firefox.plist"
        policy.write_bytes(
            plistlib.dumps(dict(document, EnterprisePoliciesEnabled=True))
        )
        result = self.capture(self.browser_map())
        self.assertEqual(result["settings"]["firefox.cookies.allow"]["state"], "masked")
        self.assertNotIn(private_url, json.dumps(result))
        with self.assertRaises(reader.CaptureError) as error:
            reader.parse_source(
                "firefox_policy", plistlib.dumps({"Cookies__Allow__51": private_url})
            )
        self.assertEqual(error.exception.code, 3)
        self.assertNotIn(private_url, str(error.exception))

    def test_shipped_firefox_conflicting_policy_encodings_are_unverified(self):
        entries = self.browser_map()
        key = "firefox.dns_over_https.enabled"
        for nested in ({"Enabled": False}, False):
            document = {"DNSOverHTTPS": nested, "DNSOverHTTPS__Enabled": True}
            policy = self.policies / "org.mozilla.firefox.plist"
            policy.write_bytes(
                plistlib.dumps(dict(document, EnterprisePoliciesEnabled=True))
            )
            result = self.capture(entries)
            self.assertEqual(result["settings"][key]["state"], "unverified")
            self.assertNotIn("value", result["settings"][key])
            self.assertNotIn("  " + key + ":", reader.candidate_yaml([result]))

    def test_chrome_side_effect_aliases_do_not_create_policy_assignments(self):
        rows = self.browser_map()
        (self.root / "Last Version").write_text("158.0.8127.37")
        (self.profile / "Preferences").write_text(
            json.dumps(
                {"download": {"prompt_for_download": False}, "sync": {"apps": False}}
            )
        )
        result = reader.read_browser(
            "chrome",
            rows,
            self.profile,
            self.root,
            [],
            process_check=lambda family: None,
        )
        for key in (
            "chrome.allow_file_selection_dialogs",
            "chrome.web_app_install_by_user_enabled",
        ):
            self.assertEqual(result["settings"][key]["state"], "absent")
            self.assertNotIn("value", result["settings"][key])

    def test_chrome_item_enums_reject_unlisted_integers(self):
        rows = self.browser_map()
        for key in (
            "chrome.browser_switcher_parsing_mode",
            "chrome.profile_reauth_prompt",
        ):
            self.assertEqual(reader.filter_value(rows[key], 2), (False, None))
            self.assertEqual(reader.filter_value(rows[key], 999), (False, None))
            self.assertEqual(reader.filter_value(rows[key], 1), (True, 1))

    def test_account_layer_requires_effective_precedence_evidence(self):
        key = "chrome.saved"
        rows = {
            key: row(family="chrome", source="chrome_preferences", key="nested|safe")
        }
        (self.root / "Last Version").write_text("158.0.1")
        for account in ({"nested": {"safe": False}}, {"nested.safe": False}):
            (self.profile / "Preferences").write_text(
                json.dumps({"nested": {"safe": True}, "account_values": account})
            )
            result = reader.read_browser(
                "chrome",
                rows,
                self.profile,
                self.root,
                [],
                process_check=lambda family: None,
            )
            self.assertEqual(result["settings"][key]["state"], "unverified")
            self.assertNotIn("value", result["settings"][key])
            self.assertEqual(
                result["settings"][key]["observations"][1]["layer"], "account"
            )
            self.assertEqual(result["counts"]["unmapped_persisted_entries"], 0)

    def test_truncated_xml_and_extreme_numbers_are_safe(self):
        with self.assertRaises(reader.CaptureError) as error:
            reader.parse_source(
                "chrome_policy",
                b'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>PrivateTest',
            )
        self.assertEqual(error.exception.code, 3)
        self.assertNotIn("PrivateTest", str(error.exception))
        entry = row(kind="int")
        entry["values"] = {"type": "int", "min": 0, "max": 100}
        self.assertEqual(reader.filter_value(entry, 10**400), (False, None))

    def test_chrome_nested_values_and_policy_sources(self):
        (self.profile / "Preferences").write_text(
            json.dumps({"nested": {"safe": True}})
        )
        (self.root / "Last Version").write_text("158.0.1")
        policy = self.policies / "com.google.Chrome.plist"
        policy.write_bytes(plistlib.dumps({"Safe": False}))
        rows = {
            "chrome.saved": row(
                family="chrome", source="chrome_preferences", key="nested|safe"
            ),
            "chrome.policy": row(family="chrome", source="chrome_policy", key="Safe"),
        }
        result = reader.read_browser(
            "chrome",
            rows,
            self.profile,
            self.root,
            [policy],
            process_check=lambda family: None,
        )
        self.assertTrue(result["settings"]["chrome.saved"]["value"])
        self.assertFalse(result["settings"]["chrome.policy"]["value"])
        self.assertEqual(result["counts"]["captured"], 2)
        self.assertEqual(
            result["settings"]["chrome.policy"]["observations"][0]["state"],
            "policy_stored",
        )
        self.assertEqual(result["settings"]["chrome.policy"]["lock"], "unknown")

    def test_privacy_fixture_positive_controls_all_sources(self):
        # Deliberately personal test input beside a safe value in every source.
        canary = "person-" + reader.os.urandom(8).hex() + "@example.invalid"
        kinds = {
            "firefox_prefs": "prefs.js",
            "firefox_user": "user.js",
            "firefox_policy": "org.mozilla.firefox.plist",
            "chrome_preferences": "Preferences",
            "chrome_local_state": "Local State",
            "chrome_secure_preferences": "Secure Preferences",
            "chrome_policy": "com.google.Chrome.plist",
        }
        for source, filename in kinds.items():
            with self.subTest(source=source):
                family = source.split("_")[0]
                if family == "chrome":
                    (self.profile / "Preferences").write_text("{}")
                    (self.root / "Last Version").write_text("158.0.1")
                else:
                    (self.profile / "prefs.js").write_text("")
                payload = {"Safe": True, "Private": canary}
                if source in ("firefox_prefs", "firefox_user"):
                    path = self.profile / filename
                    path.write_text(
                        'user_pref("Safe", true); user_pref("Private", '
                        + json.dumps(canary)
                        + ");"
                    )
                elif source.endswith("policy"):
                    path = self.policies / filename
                    path.write_bytes(
                        plistlib.dumps(
                            dict(payload, EnterprisePoliciesEnabled=True)
                            if source == "firefox_policy"
                            else payload
                        )
                    )
                else:
                    path = (
                        self.root if source == "chrome_local_state" else self.profile
                    ) / filename
                    path.write_text(json.dumps(payload))
                rows = {
                    family + ".safe": row(family=family, source=source, key="Safe"),
                    family
                    + ".private": row(
                        family=family,
                        source=source,
                        key="Private",
                        kind="text",
                        **{"class": "mask", "reason": "Private text."}
                    ),
                }
                result = reader.read_browser(
                    family,
                    rows,
                    self.profile,
                    self.root,
                    [
                        self.policies
                        / (
                            "org.mozilla.firefox.plist"
                            if family == "firefox"
                            else "com.google.Chrome.plist"
                        )
                    ],
                    process_check=lambda family: None,
                )
                self.assertEqual(result["counts"]["captured"], 1)
                self.assertEqual(result["counts"]["masked"], 1)
                self.assertTrue(result["settings"][family + ".safe"]["value"])
                observation = result["settings"][family + ".safe"]["observations"][0]
                self.assertEqual(observation["source"], source)
                self.assertEqual(observation["state"], reader.SOURCE_STATES[source])
                report = json.dumps(result) + reader.candidate_yaml([result])
                self.assertEqual(reader.leaked_test_values([canary], [report]), [])
                path.unlink()

    def test_firefox_version_record_keeps_build_ids_and_paths_private(self):
        # Deliberately personal test input in an unrelated version-record field.
        (self.profile / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=158.0.1_20261101010101/20261101010101\nLastPlatformDir=/private/artificial-sensitive-path\n"
        )
        result = self.capture()
        self.assertEqual(result["versions"]["browser"], "158.0.1")
        self.assertIs(result["settings"]["firefox.safe"]["value"], True)
        self.assertNotIn("20261101010101", json.dumps(result))
        self.assertNotIn("artificial-sensitive-path", json.dumps(result))

    def test_process_scan_refuses_browser_helpers_and_unknown_results(self):
        default = self.root / "Library/Application Support/Firefox"
        profile = default / "Profiles/fixture.default"
        profile.mkdir(parents=True)
        home_patch = patch.object(reader.Path, "home", return_value=self.root)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        for result in (
            reader.subprocess.CompletedProcess(
                [], 0, "123 /Applications/Firefox.app/Contents/MacOS/plugin-container\n"
            ),
            reader.subprocess.CompletedProcess([], 1, ""),
        ):
            with patch.object(reader.subprocess, "run", return_value=result):
                with self.assertRaises(reader.CaptureError) as error:
                    reader.check_browser_closed("firefox", profile, default)
                self.assertEqual(error.exception.code, 3)
        with patch.object(
            reader.subprocess,
            "run",
            return_value=reader.subprocess.CompletedProcess(
                [], 0, "123 /bin/artificial-test-process\n"
            ),
        ):
            reader.check_browser_closed("firefox", profile, default)

    def test_empty_stores_have_zero_unmapped_entries(self):
        (self.profile / "prefs.js").write_text("")
        result = self.capture()
        self.assertEqual(result["counts"]["unmapped_persisted_entries"], 0)
        self.assertEqual(result["counts"]["unmapped_policy_entries"], 0)
        self.assertEqual(result["counts"]["captured"], 0)

    def test_leak_scanner_positive_control(self):
        canary = "artificial-private-canary"
        self.assertEqual(
            reader.leaked_test_values([canary], ["test input: " + canary]), [0]
        )
        self.assertEqual(
            reader.leaked_test_values([canary], [json.dumps(self.capture())]), []
        )


if __name__ == "__main__":
    unittest.main()
