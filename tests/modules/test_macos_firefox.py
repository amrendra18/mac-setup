import copy
import importlib.util
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_firefox/library"))
from macos_firefox import FirefoxError, PolicyStore, ensure_firefox, plan_policies

HOME = "firefox.homepage.url"
START = "firefox.homepage.start_page"
GPC = "firefox.pref.privacy_globalprivacycontrol_enabled"
HISTORY = "firefox.sanitize_on_shutdown.history"
TELEMETRY = "firefox.disable_telemetry"
IMPORT = "firefox.disable_profile_import"


class FakeMac:
    def __init__(self, policies=None):
        self.policies = copy.deepcopy(policies or {})
        self.calls = []
        self.processes = ""
        self.process_rc = 0
        self.import_rc = 0
        self.keep = True
        self.export_count = 0
        self.race = False
        self.delete_rc = 0
        self.after_import = None

    def run(self, argv):
        self.calls.append(argv)
        if argv[0] == "/bin/ps":
            return self.process_rc, self.processes, ""
        if argv[1] == "export":
            self.export_count += 1
            if self.race and self.export_count == 2:
                self.policies["Unrelated"] = "concurrent"
            if self.after_import and len(self.imports) >= self.inject_after:
                change, self.after_import = self.after_import, None
                change(self.policies)
            return 0, plistlib.dumps(self.policies).decode(), ""
        if argv[1] == "import":
            if not self.import_rc and self.keep:
                self.policies.update(plistlib.loads(Path(argv[3]).read_bytes()))
            return self.import_rc, "PRIVATE-FAILURE", "PRIVATE-FAILURE"
        if argv[1] == "delete":
            if not self.delete_rc:
                self.policies.pop(argv[3], None)
            return self.delete_rc, "PRIVATE-FAILURE", "PRIVATE-FAILURE"
        raise AssertionError(argv)

    @property
    def imports(self):
        return [call for call in self.calls if len(call) > 1 and call[1] == "import"]

    @property
    def after_import(self):
        return self._after_import

    @after_import.setter
    def after_import(self, value):
        self._after_import = value
        self.inject_after = len(self.imports) + 1

    @property
    def deletes(self):
        return [call for call in self.calls if len(call) > 1 and call[1] == "delete"]


class FirefoxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.mac = FakeMac()
        self.store = PolicyStore(
            self.mac,
            self.root / "org.mozilla.firefox",
            self.root / "owned.plist",
            uid=os.getuid(),
            managed_paths=(),
        )

    def apply(self, settings, check=False):
        return ensure_firefox(self.store, settings, check)

    def test_check_writes_no_file_or_policy(self):
        result = self.apply({HOME: "https://example.invalid/"}, True)
        self.assertTrue(result["changed"])
        self.assertEqual(self.mac.imports, [])
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse(result["stored_verified"])

    def test_apply_keeps_unrelated_keys_and_verifies(self):
        self.mac.policies = {
            "Unrelated": {"Keep": 42},
            "Homepage": {"StartPage": "previous-session", "Locked": True},
        }
        result = self.apply({HOME: "https://example.invalid/"})
        self.assertTrue(result["stored_verified"])
        self.assertTrue(self.mac.policies["EnterprisePoliciesEnabled"])
        self.assertEqual(self.mac.policies["Unrelated"], {"Keep": 42})
        self.assertEqual(
            self.mac.policies["Homepage"],
            {
                "StartPage": "previous-session",
                "URL": "https://example.invalid/",
                "Locked": True,
            },
        )

    def test_second_run_preserves_files_and_reports_unchanged(self):
        settings = {GPC: True, HOME: "https://example.invalid/"}
        self.apply(settings)
        signature = self.store.owned.stat().st_mtime_ns
        self.assertFalse(self.apply(settings)["changed"])
        self.assertEqual(len(self.mac.imports), 1)
        self.assertEqual(self.store.owned.stat().st_mtime_ns, signature)

    def test_matching_typed_domain_is_never_imported(self):
        self.mac.policies = {
            "EnterprisePoliciesEnabled": True,
            "DisableTelemetry": True,
        }
        self.assertFalse(self.apply({TELEMETRY: True})["changed"])
        self.assertEqual(self.mac.imports, [])

    def test_bool_does_not_match_integer(self):
        self.mac.policies = {"EnterprisePoliciesEnabled": True, "DisableTelemetry": 1}
        self.assertTrue(self.apply({TELEMETRY: True})["changed"])
        self.assertIs(self.mac.policies["DisableTelemetry"], True)

    def test_lock_false_removes_owned_native_and_preference_locks(self):
        self.apply({HOME: "https://example.invalid/", GPC: True, TELEMETRY: True})
        self.mac.policies["UnrelatedPolicy"] = True
        result = self.apply(
            {
                "firefox.lock": False,
                HOME: "https://example.invalid/",
                GPC: True,
                TELEMETRY: True,
            }
        )
        self.assertIs(self.mac.policies["Homepage"]["Locked"], False)
        self.assertEqual(
            self.mac.policies["Preferences"]["privacy.globalprivacycontrol.enabled"][
                "Status"
            ],
            "user",
        )
        self.assertNotIn("DisableTelemetry", self.mac.policies)
        self.assertTrue(self.mac.policies["UnrelatedPolicy"])
        self.assertIn(TELEMETRY, [item["id"] for item in result["omitted"]])

    def test_mini3_apply_again_and_unlock_releases_import_policy(self):
        settings = {HOME: "https://example.invalid/", GPC: True, IMPORT: True}
        self.assertTrue(self.apply(settings)["stored_verified"])
        self.assertFalse(self.apply(settings)["changed"])
        result = self.apply({"firefox.lock": False, **settings})
        self.assertTrue(result["stored_verified"])
        self.assertNotIn("DisableProfileImport", self.mac.policies)
        self.assertIs(self.mac.policies["Homepage"]["Locked"], False)
        self.assertEqual(
            self.mac.policies["Preferences"]["privacy.globalprivacycontrol.enabled"][
                "Status"
            ],
            "user",
        )
        self.assertFalse(self.apply({"firefox.lock": False, **settings})["changed"])

    def test_unlocked_homepage_start_is_editable_and_releases(self):
        settings = {"firefox.lock": False, START: "homepage-locked"}
        result = self.apply(settings)
        self.assertTrue(result["stored_verified"])
        self.assertEqual(
            self.mac.policies["Homepage"], {"StartPage": "homepage", "Locked": False}
        )
        note = "\n".join(result["notes"])
        self.assertIn(START, note)
        self.assertIn("homepage-locked", note)
        self.assertIn("homepage", note)
        self.assertIn("editable", note)
        self.assertFalse(self.apply(settings)["changed"])
        self.assertTrue(self.apply({START: "unset"})["stored_verified"])
        self.assertNotIn("Homepage", self.mac.policies)
        self.assertFalse(self.apply({START: "unset"})["changed"])

    def test_locked_homepage_start_becomes_editable_on_lock_transition(self):
        selected = {START: "homepage-locked"}
        self.apply(selected)
        self.assertEqual(
            self.mac.policies["Homepage"],
            {"StartPage": "homepage-locked", "Locked": True},
        )
        settings = {"firefox.lock": False, **selected}
        result = self.apply(settings)
        self.assertTrue(result["stored_verified"])
        self.assertEqual(
            self.mac.policies["Homepage"], {"StartPage": "homepage", "Locked": False}
        )
        self.assertTrue(any(START in note for note in result["notes"]))
        self.assertFalse(self.apply(settings)["changed"])
        self.apply({"firefox.lock": True, **selected})
        self.apply({"firefox.lock": False})
        self.assertNotIn("Homepage", self.mac.policies)

    def test_selected_nonblank_homepage_and_no_start_refuse_before_any_read(self):
        with self.assertRaises(FirefoxError) as error:
            self.apply(
                {HOME: "https://example.invalid/PRIVATE-HOMEPAGE", START: "none"}
            )
        self.assertIn(HOME, str(error.exception))
        self.assertIn(START, str(error.exception))
        self.assertNotIn("PRIVATE-HOMEPAGE", str(error.exception))
        self.assertEqual(self.mac.calls, [])

    def test_retained_no_start_refuses_new_nonblank_homepage_without_writes(self):
        self.apply({START: "none"})
        before = copy.deepcopy(self.mac.policies)
        manifest = self.store.owned.read_bytes()
        self.mac.calls.clear()
        with self.assertRaises(FirefoxError) as error:
            self.apply({HOME: "https://example.invalid/PRIVATE-HOMEPAGE"})
        self.assertIn(HOME, str(error.exception))
        self.assertIn(START, str(error.exception))
        self.assertNotIn("PRIVATE-HOMEPAGE", str(error.exception))
        self.assertEqual(self.mac.policies, before)
        self.assertEqual(self.store.owned.read_bytes(), manifest)
        self.assertEqual(self.mac.imports, [])
        self.assertEqual(self.mac.deletes, [])

    def test_retained_nonblank_homepage_refuses_no_start_without_writes(self):
        self.apply({HOME: "https://example.invalid/PRIVATE-HOMEPAGE"})
        before = copy.deepcopy(self.mac.policies)
        manifest = self.store.owned.read_bytes()
        self.mac.calls.clear()
        with self.assertRaises(FirefoxError) as error:
            self.apply({START: "none"})
        self.assertIn(HOME, str(error.exception))
        self.assertIn(START, str(error.exception))
        self.assertNotIn("PRIVATE-HOMEPAGE", str(error.exception))
        self.assertEqual(self.mac.policies, before)
        self.assertEqual(self.store.owned.read_bytes(), manifest)
        self.assertEqual(self.mac.imports, [])
        self.assertEqual(self.mac.deletes, [])

    def test_blank_homepage_agrees_with_no_start_and_is_unchanged_again(self):
        selected = {HOME: "about:blank", START: "none"}
        self.assertTrue(self.apply(selected)["stored_verified"])
        self.assertEqual(
            self.mac.policies["Homepage"],
            {"URL": "about:blank", "StartPage": "none", "Locked": True},
        )
        self.assertFalse(self.apply(selected)["changed"])
        self.assertFalse(self.apply({HOME: "about:blank"})["changed"])
        self.assertFalse(self.apply({START: "none"})["changed"])

    def test_unset_no_start_releases_preexisting_homepage_conflict(self):
        self.mac.policies = {
            "Homepage": {
                "URL": "https://example.invalid/PRIVATE-HOMEPAGE",
                "StartPage": "none",
                "Locked": True,
            }
        }
        self.assertTrue(self.apply({START: "unset"})["stored_verified"])
        self.assertEqual(
            self.mac.policies["Homepage"],
            {"URL": "https://example.invalid/PRIVATE-HOMEPAGE", "Locked": True},
        )
        self.assertFalse(self.apply({START: "unset"})["changed"])

    def test_unset_removes_last_owned_top_level_policy(self):
        for key, policy in (
            (HOME, "Homepage"),
            (GPC, "Preferences"),
            (IMPORT, "DisableProfileImport"),
        ):
            with self.subTest(key=key):
                value = "https://example.invalid/" if key == HOME else True
                self.apply({key: value})
                self.assertTrue(self.apply({key: "unset"})["stored_verified"])
                self.assertNotIn(policy, self.mac.policies)

    def test_flattened_changed_and_released_policy_keys_are_removed(self):
        self.mac.policies = {
            "EnterprisePoliciesEnabled": True,
            "Homepage__URL": "https://example.invalid/",
            "Homepage__Locked": True,
            "Preferences__privacy.globalprivacycontrol.enabled__Value": True,
            "Preferences__privacy.globalprivacycontrol.enabled__Status": "locked",
            "DisableProfileImport": True,
            "DNSOverHTTPS__ProviderURL": "https://example.invalid/dns",
            "Artificial____Window": "PRIVATE-STATE",
        }
        self.apply({HOME: "https://example.invalid/", GPC: True, IMPORT: True})
        self.apply(
            {
                "firefox.lock": False,
                HOME: "https://example.invalid/",
                GPC: True,
                IMPORT: True,
            }
        )
        self.assertNotIn("Homepage__Locked", self.mac.policies)
        self.assertNotIn(
            "Preferences__privacy.globalprivacycontrol.enabled__Status",
            self.mac.policies,
        )
        self.assertNotIn("DisableProfileImport", self.mac.policies)
        self.assertEqual(
            self.mac.policies["DNSOverHTTPS__ProviderURL"],
            "https://example.invalid/dns",
        )
        self.assertEqual(self.mac.policies["Artificial____Window"], "PRIVATE-STATE")
        self.apply({HOME: "unset", GPC: "unset"})
        self.assertNotIn("Homepage__URL", self.mac.policies)
        self.assertNotIn(
            "Preferences__privacy.globalprivacycontrol.enabled__Value",
            self.mac.policies,
        )

    def test_failed_import_never_deletes_existing_keys(self):
        self.apply({IMPORT: True})
        before = copy.deepcopy(self.mac.policies)
        self.mac.import_rc = 1
        with self.assertRaises(FirefoxError):
            self.apply({IMPORT: "unset"})
        self.assertEqual(self.mac.policies, before)
        self.assertEqual(self.mac.deletes, [])

    def test_failed_deletion_keeps_recovery_ownership_and_hides_private_output(self):
        self.apply({IMPORT: True})
        self.mac.delete_rc = 1
        with self.assertRaises(FirefoxError) as error:
            self.apply({IMPORT: "unset"})
        self.assertIn(IMPORT, str(error.exception))
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertIn(IMPORT, self.store.ownership()["entries"])
        self.mac.delete_rc = 0
        self.assertTrue(self.apply({IMPORT: "unset"})["stored_verified"])

    def test_check_release_writes_no_policy_or_files(self):
        self.apply({IMPORT: True})
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.iterdir()
        }
        calls = len(self.mac.calls)
        self.assertTrue(self.apply({IMPORT: "unset"}, True)["changed"])
        self.assertEqual(
            before,
            {
                path: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.iterdir()
            },
        )
        self.assertFalse(
            any(call[1] in ("import", "delete") for call in self.mac.calls[calls:])
        )

    def test_private_flattened_field_removal_refuses_before_import(self):
        private_key = "Preferences__privacy.globalprivacycontrol.enabled__PRIVATE-KEY"
        self.mac.policies = {
            "Preferences__privacy.globalprivacycontrol.enabled__Value": True,
            "Preferences__privacy.globalprivacycontrol.enabled__Status": "locked",
            private_key: "PRIVATE-VALUE",
        }
        before = copy.deepcopy(self.mac.policies)
        with self.assertRaises(FirefoxError) as error:
            self.apply({GPC: False})
        self.assertIn(GPC, str(error.exception))
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertNotIn("PRIVATE", str(self.mac.calls))
        self.assertEqual(self.mac.imports, [])
        self.assertEqual(self.mac.deletes, [])
        self.assertEqual(self.mac.policies, before)

    def test_flattened_list_gaps_preserve_unrelated_saved_values(self):
        self.mac.policies = {
            "Homepage__Additional__0": "https://example.invalid/first",
            "Homepage__Additional__1": "https://example.invalid/second",
            "Homepage__Locked": True,
        }
        self.apply({HOME: "https://example.invalid/"})
        self.assertEqual(self.mac.deletes, [])
        self.assertEqual(
            self.mac.policies["Homepage"]["Additional"],
            ["https://example.invalid/first", "https://example.invalid/second"],
        )

    def test_flattened_array_update_removes_old_index_keys(self):
        self.mac.policies = {
            "Homepage__Additional__0": "https://example.invalid/first",
            "Homepage__Additional__1": "https://example.invalid/second",
            "Homepage__Locked": True,
            "Preferences__PRIVATE-KEY__Value": "PRIVATE-VALUE",
        }
        result = self.apply(
            {"firefox.homepage.additional": ["https://example.invalid/replacement"]}
        )
        self.assertTrue(result["stored_verified"])
        self.assertEqual(
            self.mac.policies["Homepage"]["Additional"],
            ["https://example.invalid/replacement"],
        )
        self.assertNotIn("Homepage__Additional__0", self.mac.policies)
        self.assertNotIn("Homepage__Additional__1", self.mac.policies)
        self.assertEqual(
            self.mac.policies["Preferences__PRIVATE-KEY__Value"], "PRIVATE-VALUE"
        )
        self.assertNotIn("PRIVATE", str(self.mac.calls))
        self.assertFalse(
            self.apply(
                {"firefox.homepage.additional": ["https://example.invalid/replacement"]}
            )["changed"]
        )

    def test_flattened_array_release_removes_indices_through_reader_limit(self):
        self.mac.policies = {
            f"Homepage__Additional__{index}": f"https://example.invalid/{index}"
            for index in range(51)
        }
        self.mac.policies["Homepage__Locked"] = True
        result = self.apply({"firefox.homepage.additional": "unset"})
        self.assertTrue(result["stored_verified"])
        self.assertEqual(self.mac.policies, {})
        self.assertFalse(
            self.apply({"firefox.homepage.additional": "unset"})["changed"]
        )

    def test_flattened_array_index_above_reader_limit_refuses_before_write(self):
        self.mac.policies = {"Homepage__Additional__51": "PRIVATE-VALUE"}
        with self.assertRaises(FirefoxError) as error:
            self.apply({"firefox.homepage.additional": "unset"})
        self.assertIn("Could not parse the Firefox policy domain", str(error.exception))
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(self.mac.imports, [])
        self.assertEqual(self.mac.deletes, [])

    def test_numeric_field_under_dictionary_policy_refuses_before_write(self):
        self.mac.policies = {"Preferences__0": {"PRIVATE-KEY": "PRIVATE-VALUE"}}
        with self.assertRaises(FirefoxError) as error:
            self.store.write(self.store.read(), {}, {})
        self.assertIn("unsupported stored key", str(error.exception))
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertNotIn("PRIVATE", str(self.mac.calls))
        self.assertEqual(self.mac.imports, [])
        self.assertEqual(self.mac.deletes, [])

    def test_unrelated_private_flattened_preference_is_preserved_without_argv(self):
        self.mac.policies = {"Preferences__PRIVATE-KEY__Value": "PRIVATE-VALUE"}
        result = self.apply({GPC: True})
        self.assertTrue(result["stored_verified"])
        self.assertEqual(
            self.mac.policies["Preferences__PRIVATE-KEY__Value"], "PRIVATE-VALUE"
        )
        self.assertEqual(self.mac.deletes, [])
        self.assertNotIn("PRIVATE", str(self.mac.calls))
        self.assertNotIn("PRIVATE", str(result))

    def test_change_immediately_before_import_refuses_without_policy_write(self):
        original = self.mac.run

        def concurrent(argv):
            if argv[1] == "export" and self.mac.export_count == 2:
                self.mac.policies["Unrelated"] = "concurrent"
            return original(argv)

        self.mac.run = concurrent
        with self.assertRaises(FirefoxError):
            self.apply({IMPORT: True})
        self.assertEqual(self.mac.imports, [])
        self.assertEqual(self.mac.deletes, [])
        self.assertEqual(self.mac.policies, {"Unrelated": "concurrent"})

    def test_serialization_failure_never_imports_or_deletes(self):
        self.apply({IMPORT: True})
        imports = len(self.mac.imports)
        before = copy.deepcopy(self.mac.policies)
        with self.assertRaises(TypeError):
            self.store.write(self.store.read(), {"Unrepresentable": object()}, {})
        self.assertEqual(self.mac.policies, before)
        self.assertEqual(len(self.mac.imports), imports)
        self.assertEqual(self.mac.deletes, [])

    def test_readback_mismatch_names_selected_public_id_and_hides_private_data(self):
        self.mac.after_import = lambda policies: policies.update(
            {"DisableProfileImport": 1, "PRIVATE-KEY": "PRIVATE-VALUE"}
        )
        with self.assertRaises(FirefoxError) as error:
            self.apply({IMPORT: True})
        self.assertEqual(
            str(error.exception),
            "Firefox stored-policy read-back did not match for firefox.disable_profile_import. Firefox acceptance remains unverified.",
        )
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_readback_mismatch_names_omitted_and_released_public_ids(self):
        for selection in (
            {"firefox.lock": False, IMPORT: True},
            {IMPORT: "unset"},
            {"firefox.lock": False},
        ):
            with self.subTest(selection=selection):
                self.apply({IMPORT: True})
                self.mac.after_import = lambda policies: policies.update(
                    {"DisableProfileImport": True}
                )
                with self.assertRaises(FirefoxError) as error:
                    self.apply(selection)
                self.assertEqual(
                    str(error.exception),
                    "Firefox stored-policy read-back did not match for firefox.disable_profile_import. Firefox acceptance remains unverified.",
                )
                self.mac.policies.pop("DisableProfileImport")
                self.apply({IMPORT: "unset"})

    def test_readback_mismatch_names_retained_public_id(self):
        self.apply({IMPORT: True})
        self.mac.after_import = lambda policies: policies.update(
            {"DisableProfileImport": False}
        )
        with self.assertRaises(FirefoxError) as error:
            self.apply({GPC: True})
        self.assertEqual(
            str(error.exception),
            "Firefox stored-policy read-back did not match for firefox.disable_profile_import. Firefox acceptance remains unverified.",
        )

    def test_readback_mismatch_names_loader_public_id(self):
        self.mac.after_import = lambda policies: policies.update(
            {"EnterprisePoliciesEnabled": 1}
        )
        with self.assertRaises(FirefoxError) as error:
            self.apply({IMPORT: True})
        self.assertEqual(
            str(error.exception),
            "Firefox stored-policy read-back did not match for firefox.enterprise_policies_enabled. Firefox acceptance remains unverified.",
        )

    def test_readback_mismatch_uses_safe_fallback_for_private_only_changes(self):
        self.mac.after_import = lambda policies: policies.update(
            {"PRIVATE-KEY": "PRIVATE-VALUE"}
        )
        with self.assertRaises(FirefoxError) as error:
            self.apply({IMPORT: True})
        self.assertEqual(
            str(error.exception),
            "Firefox stored-policy read-back did not match. Firefox acceptance remains unverified.",
        )

    def test_conflicting_flattened_readback_names_public_id(self):
        self.mac.after_import = lambda policies: policies.update(
            {"Homepage__URL": "PRIVATE-VALUE"}
        )
        with self.assertRaises(FirefoxError) as error:
            self.apply({HOME: "https://example.invalid/"})
        self.assertEqual(
            str(error.exception),
            "Firefox stored-policy read-back did not match for firefox.homepage.url. Firefox acceptance remains unverified.",
        )

    def test_readback_reports_sorted_public_ids(self):
        def corrupt(policies):
            policies["DisableProfileImport"] = False
            policies["Homepage"]["URL"] = "PRIVATE-VALUE"

        self.mac.after_import = corrupt
        with self.assertRaises(FirefoxError) as error:
            self.apply({HOME: "https://example.invalid/", IMPORT: True})
        self.assertEqual(
            str(error.exception),
            "Firefox stored-policy read-back did not match for firefox.disable_profile_import, firefox.homepage.url. Firefox acceptance remains unverified.",
        )

    def test_lock_false_releases_previously_owned_omitted_settings(self):
        self.apply({HOME: "https://example.invalid/", GPC: True})
        self.apply({"firefox.lock": False})
        self.assertNotIn("Homepage", self.mac.policies)
        self.assertNotIn("Preferences", self.mac.policies)

    def test_unset_releases_both_forms_and_keeps_unrelated_siblings(self):
        self.apply({GPC: True, HOME: "https://example.invalid/"})
        self.mac.policies["Homepage"]["StartPage"] = "none"
        self.apply({HOME: "unset"})
        self.assertEqual(
            self.mac.policies["Homepage"], {"StartPage": "none", "Locked": True}
        )
        self.assertIn("Preferences", self.mac.policies)

    def test_omission_preserves_management_without_lock_transition(self):
        self.apply({HOME: "https://example.invalid/"})
        self.assertTrue(self.apply({GPC: True})["changed"])
        self.assertEqual(
            self.mac.policies["Homepage"]["URL"], "https://example.invalid/"
        )

    def test_selected_unset_releases_preexisting_policy_without_manifest(self):
        self.mac.policies = {
            "Homepage": {
                "URL": "https://example.invalid/",
                "Locked": True,
                "StartPage": "none",
            }
        }
        self.apply({HOME: "unset"})
        self.assertEqual(
            self.mac.policies["Homepage"], {"StartPage": "none", "Locked": True}
        )

    def test_whole_set_invalid_value_refuses_before_export(self):
        with self.assertRaises(FirefoxError):
            self.apply({HOME: "PRIVATE-URL", GPC: "PRIVATE-INVALID"})
        self.assertEqual(self.mac.calls, [])

    def test_lock_requires_boolean_even_with_unset(self):
        for value in ("unset", 1, None):
            with self.subTest(value=value), self.assertRaises(FirefoxError):
                self.apply({"firefox.lock": value})
        self.assertEqual(self.mac.calls, [])

    def test_unknown_setting_error_hides_value_and_identifier(self):
        with self.assertRaises(FirefoxError) as error:
            self.apply({"PRIVATE-ID": "PRIVATE-VALUE"})
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_any_account_running_browser_refuses_without_import(self):
        for command in (
            "123 /Applications/Firefox.app/Contents/MacOS/firefox",
            "456 /Applications/Firefox.app/Contents/MacOS/plugin-container",
        ):
            with self.subTest(command=command):
                self.mac.processes = command
                with self.assertRaises(FirefoxError):
                    self.apply({GPC: True})
        self.assertEqual(self.mac.imports, [])

    def test_failed_process_query_refuses(self):
        self.mac.process_rc = 1
        with self.assertRaises(FirefoxError):
            self.apply({GPC: True})
        self.assertEqual(self.mac.imports, [])

    def test_symlink_domain_refuses(self):
        target = self.root / "target"
        target.write_bytes(plistlib.dumps({}))
        self.store.path.symlink_to(target)
        with self.assertRaises(FirefoxError):
            self.apply({GPC: True})
        self.assertEqual(self.mac.imports, [])

    def test_unsafe_domain_permissions_refuse(self):
        self.store.path.touch(mode=0o666)
        self.store.path.chmod(0o666)
        with self.assertRaises(FirefoxError):
            self.apply({GPC: True})

    def test_managed_domain_refuses(self):
        managed = self.root / "managed.plist"
        managed.write_bytes(plistlib.dumps({"DisableTelemetry": True}))
        self.store.managed_paths = [managed]
        with self.assertRaises(FirefoxError):
            self.apply({GPC: True})
        self.assertEqual(self.mac.imports, [])

    def test_concurrent_change_refuses_before_import(self):
        self.mac.race = True
        with self.assertRaises(FirefoxError):
            self.apply({GPC: True})
        self.assertEqual(self.mac.imports, [])

    def test_import_error_never_exposes_output(self):
        self.mac.import_rc = 1
        with self.assertRaises(FirefoxError) as error:
            self.apply({HOME: "PRIVATE-URL"})
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertNotIn(b"PRIVATE", self.store.owned.read_bytes())

    def test_failed_readback_is_not_reported_verified(self):
        self.mac.keep = False
        with self.assertRaises(FirefoxError):
            self.apply({GPC: True})

    def test_results_and_manifest_never_contain_values(self):
        result = self.apply({HOME: "https://private-fixture.invalid/"})
        self.assertNotIn("private-fixture", str(result))
        self.assertNotIn(b"private-fixture", self.store.owned.read_bytes())

    def test_conflicting_flat_and_nested_policies_refuse(self):
        self.mac.policies = {"Homepage": {"URL": "a"}, "Homepage__URL": "b"}
        with self.assertRaises(FirefoxError):
            self.apply({GPC: True})
        self.assertEqual(self.mac.imports, [])

    def test_alias_conflict_refuses(self):
        from macos_firefox import ENTRIES

        aliases = [
            key
            for key, row in ENTRIES.items()
            if row["browser"]["family"] == "firefox"
            and row["browser"]["locked"].get("path")
            in (["DisableAccounts"], ["DisableFirefoxAccounts"])
        ]
        self.assertEqual(len(aliases), 2)
        with self.assertRaises(FirefoxError):
            self.apply({aliases[0]: True, aliases[1]: False})
        self.assertEqual(self.mac.calls, [])

    def test_clear_on_shutdown_conflicts_with_sanitize(self):
        from macos_firefox import ENTRIES

        key = next(
            key
            for key, row in ENTRIES.items()
            if row["browser"]["locked"].get("path")
            == ["ClearOnShutdown", "BrowsingHistoryAndDownloads"]
        )
        with self.assertRaises(FirefoxError):
            self.apply({key: True, HISTORY: True})
        self.assertEqual(self.mac.calls, [])

    def test_empty_selection_writes_nothing(self):
        self.assertFalse(self.apply({})["changed"])
        self.assertEqual(self.mac.calls, [])

    def test_shared_native_lock_does_not_change_unowned_sibling(self):
        self.mac.policies = {
            "Homepage": {
                "Additional": ["https://example.invalid/unrelated"],
                "Locked": False,
            }
        }
        with self.assertRaises(FirefoxError):
            self.apply({HOME: "https://example.invalid/"})
        self.assertEqual(self.mac.imports, [])

    def test_shared_owned_lock_survives_one_sibling_release(self):
        from macos_firefox import ENTRIES

        additional = next(
            key
            for key, row in ENTRIES.items()
            if row["browser"]["locked"].get("path") == ["Homepage", "Additional"]
        )
        self.apply(
            {
                HOME: "https://example.invalid/",
                additional: ["https://example.invalid/extra"],
            }
        )
        self.apply({HOME: "unset"})
        self.assertTrue(self.mac.policies["Homepage"]["Locked"])
        self.assertNotIn("URL", self.mac.policies["Homepage"])

    def test_lock_transition_recompiles_without_keeping_locked_sibling_flag(self):
        self.apply({HOME: "https://example.invalid/", "firefox.homepage.locked": True})
        self.apply({"firefox.lock": False, HOME: "https://example.invalid/"})
        self.assertIs(self.mac.policies["Homepage"]["Locked"], False)

    def test_corrupt_ownership_refuses_without_private_detail(self):
        self.store.owned.write_text("PRIVATE-CORRUPTION")
        with self.assertRaises(FirefoxError) as error:
            self.apply({GPC: True})
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(self.mac.imports, [])

    def test_forged_ownership_cannot_release_unrelated_path(self):
        self.store.owned.write_bytes(
            plistlib.dumps(
                {"version": 1, "lock": True, "entries": {HOME: [["Unrelated"]]}}
            )
        )
        with self.assertRaises(FirefoxError):
            self.apply({"firefox.lock": False})
        self.assertEqual(self.mac.imports, [])

    def test_unlocked_boolean_sanitize_is_omitted(self):
        from macos_firefox import ENTRIES

        key = next(
            key
            for key, row in ENTRIES.items()
            if row["browser"]["locked"].get("path") == ["SanitizeOnShutdown"]
        )
        result = self.apply({"firefox.lock": False, key: True})
        self.assertNotIn("SanitizeOnShutdown", self.mac.policies)
        self.assertEqual([item["id"] for item in result["omitted"]], [key])

    def test_fixed_lock_field_cannot_override_global_control(self):
        with self.assertRaises(FirefoxError):
            self.apply({"firefox.lock": False, "firefox.homepage.locked": True})
        self.assertEqual(self.mac.calls, [])

    def test_support_menu_needs_required_siblings_before_any_read(self):
        from macos_firefox import ENTRIES

        key = next(
            key
            for key, row in ENTRIES.items()
            if row["browser"]["locked"].get("path") == ["SupportMenu", "Title"]
        )
        with self.assertRaises(FirefoxError):
            self.apply({key: "Artificial title"})
        self.assertEqual(self.mac.calls, [])

    def test_dns_false_and_fallback_refused_before_any_read(self):
        with self.assertRaises(FirefoxError):
            self.apply(
                {
                    "firefox.dns_over_https.enabled": False,
                    "firefox.dns_over_https.fallback": False,
                }
            )
        self.assertEqual(self.mac.calls, [])

    def test_tracking_category_and_individual_value_refused(self):
        with self.assertRaises(FirefoxError):
            self.apply(
                {
                    "firefox.enable_tracking_protection.category": "strict",
                    "firefox.enable_tracking_protection.value": False,
                }
            )
        self.assertEqual(self.mac.calls, [])

    def test_manifest_survives_interrupted_readback_for_later_release(self):
        self.apply({HOME: "https://example.invalid/"})
        original = self.store.read
        calls = 0

        def broken_read(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise FirefoxError("Artificial interruption after import.")
            return original(*args, **kwargs)

        self.store.read = broken_read
        with self.assertRaises(FirefoxError):
            self.apply({GPC: True})
        self.store.read = original
        self.apply({"firefox.lock": False})
        self.assertNotIn("Preferences", self.mac.policies)
        self.assertNotIn("Homepage", self.mac.policies)

    def test_flattened_matching_policy_is_not_rewritten(self):
        self.mac.policies = {
            "EnterprisePoliciesEnabled": True,
            "Homepage__URL": "https://example.invalid/",
            "Homepage__Locked": True,
        }
        self.assertFalse(self.apply({HOME: "https://example.invalid/"})["changed"])
        self.assertEqual(self.mac.imports, [])

    def test_integer_preference_emits_explicit_number_type(self):
        self.apply({"firefox.pref.browser_uidensity": 1})
        self.assertEqual(
            self.mac.policies["Preferences"]["browser.uidensity"],
            {"Value": 1, "Status": "locked", "Type": "number"},
        )

    def test_retained_alias_conflict_refuses_before_import(self):
        self.apply({"firefox.disable_accounts": True})
        imports = len(self.mac.imports)
        with self.assertRaises(FirefoxError):
            self.apply({"firefox.disable_firefox_accounts": False})
        self.assertEqual(len(self.mac.imports), imports)
        self.assertNotIn("DisableFirefoxAccounts", self.mac.policies)

    def test_overlapping_runs_refuse_without_losing_successful_policy(self):
        second = PolicyStore(
            self.mac,
            self.store.domain,
            self.store.owned,
            uid=os.getuid(),
            managed_paths=(),
        )
        original = self.store.write
        concurrent = []

        def overlap(before, document, identifiers):
            try:
                ensure_firefox(second, {TELEMETRY: True})
            except FirefoxError:
                concurrent.append("refused")
            else:
                concurrent.append("wrote")
            original(before, document, identifiers)

        self.store.write = overlap
        self.apply({GPC: True})
        self.assertEqual(concurrent, ["refused"])
        self.assertIn("Preferences", self.mac.policies)
        self.assertNotIn("DisableTelemetry", self.mac.policies)

    def test_scalar_preference_converts_to_structured_lock_form(self):
        self.mac.policies = {
            "Preferences": {
                "privacy.globalprivacycontrol.enabled": True,
                "unrelated.pref": "keep",
            }
        }
        self.apply({GPC: False})
        self.assertEqual(
            self.mac.policies["Preferences"]["privacy.globalprivacycontrol.enabled"],
            {"Value": False, "Status": "locked"},
        )
        self.assertEqual(self.mac.policies["Preferences"]["unrelated.pref"], "keep")

    def test_unset_releases_scalar_preference(self):
        self.mac.policies = {
            "Preferences": {
                "privacy.globalprivacycontrol.enabled": True,
                "unrelated.pref": "keep",
            }
        }
        self.apply({GPC: "unset"})
        self.assertEqual(self.mac.policies["Preferences"], {"unrelated.pref": "keep"})

    def test_parent_selection_cannot_delete_retained_owned_child(self):
        from macos_firefox import ENTRIES

        self.apply({HISTORY: True})
        parent = next(
            key
            for key, row in ENTRIES.items()
            if row["browser"]["locked"].get("path") == ["SanitizeOnShutdown"]
        )
        with self.assertRaises(FirefoxError):
            self.apply({parent: False})
        self.assertEqual(self.mac.policies["SanitizeOnShutdown"]["History"], True)

    def test_role_preserves_app_state_keys_with_policy_delimiters(self):
        self.mac.policies = {"Artificial____Window": "PRIVATE-STATE"}
        result = self.apply({GPC: True})
        self.assertTrue(result["stored_verified"])
        self.assertEqual(self.mac.policies["Artificial____Window"], "PRIVATE-STATE")
        self.assertNotIn("PRIVATE-STATE", str(result))

    def test_retained_owned_policies_reenable_the_loader(self):
        self.apply({GPC: True})
        self.mac.policies["EnterprisePoliciesEnabled"] = False
        result = self.apply({"firefox.lock": True})
        self.assertTrue(result["changed"])
        self.assertTrue(result["stored_verified"])
        self.assertIs(self.mac.policies["EnterprisePoliciesEnabled"], True)


if __name__ == "__main__":
    unittest.main()
