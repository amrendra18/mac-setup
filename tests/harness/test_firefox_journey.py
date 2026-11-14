import copy
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "firefox_journey", ROOT / "verify/helpers/firefox.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def stored_policy(locked):
    document = {
        "EnterprisePoliciesEnabled": True,
        "DontCheckDefaultBrowser": True,
        "Homepage": {"URL": "https://example.invalid/managed", "Locked": locked},
        "SanitizeOnShutdown": {"History": True, "Locked": locked},
        "ExtensionSettings": {
            "uBlock0@raymondhill.net": {
                "installation_mode": (
                    "force_installed" if locked else "normal_installed"
                ),
                "install_url": "https://addons.mozilla.org/firefox/downloads/latest/uBlock0@raymondhill.net/latest.xpi",
            }
        },
        "Preferences": {
            "browser.mac-setup.owner": {
                "Value": "mac-setup artificial journey",
                "Status": "locked" if locked else "user",
            },
            "privacy.globalprivacycontrol.enabled": {
                "Value": True,
                "Status": "locked" if locked else "user",
            },
        },
    }
    if locked:
        document["DisableProfileImport"] = True
    return plistlib.loads(plistlib.dumps(document))


def active_policy(locked):
    document = stored_policy(locked)
    del document["EnterprisePoliciesEnabled"]
    # Firefox's Mac reader emits integers; Preferences.Value preserves numeric 1.
    document["Preferences"]["privacy.globalprivacycontrol.enabled"]["Value"] = 1
    return document


def extension_state(locked):
    return {
        "id": "uBlock0@raymondhill.net",
        "active": True,
        "canDisable": not locked,
    }


class FirefoxJourneyTests(unittest.TestCase):
    def packet(self, value):
        data = json.dumps(value).encode()
        return str(len(data)).encode() + b":" + data

    def test_protocol_reads_unicode_and_adjacent_frames(self):
        stream = io.BytesIO(self.packet({"name": "caf\u00e9"}) + self.packet([1]))
        self.assertEqual(probe.receive(stream), {"name": "caf\u00e9"})
        self.assertEqual(probe.receive(stream), [1])

    def test_protocol_refuses_truncated_or_oversized_frames(self):
        for payload in (b"3:{}", b"999999999:", b"x:{}", b"-2:{}", b""):
            with self.subTest(payload=payload), self.assertRaises(probe.ProofError):
                probe.receive(io.BytesIO(payload))

    def test_response_requires_matching_id_and_no_remote_error(self):
        self.assertEqual(
            probe.response_value(
                [1, 2, None, {"value": 4}], 2, "WebDriver:ExecuteScript"
            ),
            {"value": 4},
        )
        for value in ([1, 3, None, {}], [1, 2, {"error": "test"}, None], {}, [1, 2]):
            with self.subTest(value=value), self.assertRaises(probe.ProofError):
                probe.response_value(value, 2, "WebDriver:ExecuteScript")

    def test_rejected_script_names_the_command_and_proof_without_private_values(self):
        connection = Mock()
        connection.makefile.return_value = io.BytesIO(
            self.packet({"marionetteProtocol": 3})
            + self.packet([1, 1, None, {}])
            + self.packet(
                [
                    1,
                    2,
                    {
                        "error": "javascript error",
                        "message": "PRIVATE_MESSAGE",
                        "stacktrace": "PRIVATE_TRACE",
                    },
                    None,
                ]
            )
        )
        client = probe.Marionette(connection)
        with self.assertRaises(probe.ProofError) as caught:
            client.script(
                "PRIVATE_SCRIPT",
                ["PRIVATE_ARGUMENT"],
                asynchronous=True,
                proof="bookmark-import",
            )
        self.assertEqual(
            str(caught.exception),
            "Firefox proof bookmark-import failed: Marionette rejected WebDriver:ExecuteAsyncScript [bookmark-import] (javascript error).",
        )
        self.assertNotIn("PRIVATE_", str(caught.exception))

    def test_rejected_command_keeps_its_name_through_the_transport(self):
        connection = Mock()
        connection.makefile.return_value = io.BytesIO(
            self.packet({"marionetteProtocol": 3})
            + self.packet([1, 1, None, {}])
            + self.packet(
                [
                    1,
                    2,
                    {"error": "unsupported operation", "message": "PRIVATE_REASON"},
                    None,
                ]
            )
        )
        client = probe.Marionette(connection)
        with self.assertRaises(probe.ProofError) as caught:
            client.send("Marionette:SetContext", {"value": "chrome"})
        self.assertEqual(
            str(caught.exception),
            "Firefox proof context-switch failed: Marionette rejected Marionette:SetContext (unsupported operation).",
        )
        self.assertNotIn("PRIVATE_", str(caught.exception))

    def test_refused_system_access_names_the_running_browser_version(self):
        connection = Mock()
        connection.makefile.return_value = io.BytesIO(
            self.packet({"marionetteProtocol": 3})
            + self.packet([1, 1, None, {"capabilities": {"browserVersion": "160.0"}}])
            + self.packet(
                [
                    1,
                    2,
                    {"error": "unsupported operation", "message": "PRIVATE_REASON"},
                    None,
                ]
            )
        )
        client = probe.Marionette(connection)
        with self.assertRaises(probe.ProofError) as caught:
            probe.chrome(client)
        self.assertEqual(
            str(caught.exception),
            "Firefox proof context-switch failed: Marionette rejected Marionette:SetContext on Firefox 160.0 (unsupported operation).",
        )
        self.assertNotIn("PRIVATE_", str(caught.exception))

    def test_browser_version_discards_private_or_malformed_capabilities(self):
        for version in ("PRIVATE_VERSION", "160.0\nPRIVATE_VALUE", {}, None, 160):
            with self.subTest(version=version):
                connection = Mock()
                connection.makefile.return_value = io.BytesIO(
                    self.packet({"marionetteProtocol": 3})
                    + self.packet(
                        [
                            1,
                            1,
                            None,
                            {
                                "capabilities": {
                                    "browserVersion": version,
                                    "profile": "PRIVATE_PROFILE",
                                }
                            },
                        ]
                    )
                )
                client = probe.Marionette(connection)
                self.assertEqual(client.browser_version, "unavailable")

    def test_command_rejections_redact_unknown_labels_and_remote_errors(self):
        for command in (
            "WebDriver:NewSession",
            "Marionette:SetContext",
            "Marionette:Quit",
            "WebDriver:Navigate",
            "WebDriver:ExecuteScript",
        ):
            with self.subTest(command=command), self.assertRaises(
                probe.ProofError
            ) as caught:
                probe.response_value(
                    [
                        1,
                        2,
                        {"error": "unsupported operation", "message": "PRIVATE_POLICY"},
                        None,
                    ],
                    2,
                    command,
                )
            self.assertEqual(
                str(caught.exception),
                "Marionette rejected " + command + " (unsupported operation).",
            )
        with self.assertRaises(probe.ProofError) as caught:
            probe.response_value(
                [1, 2, {"error": "PRIVATE_ERROR", "message": "PRIVATE_POLICY"}, None],
                2,
                "PRIVATE_COMMAND",
                proof="PRIVATE_PROOF",
            )
        self.assertEqual(
            str(caught.exception), "Marionette rejected proof command (unknown error)."
        )
        self.assertNotIn("PRIVATE_", str(caught.exception))

    def test_policy_page_requires_zero_errors_and_expected_policy_rows(self):
        page = {
            "url": "about:policies",
            "activeRows": [["Homepage", "made-up"]],
            "errorRows": [],
        }
        probe.assert_policy_page(page, {"Homepage"})
        for changed in (
            {"errorRows": [["Homepage", "bad test input"]]},
            {"activeRows": []},
            {"url": "about:blank"},
        ):
            with self.subTest(changed=changed), self.assertRaises(probe.ProofError):
                probe.assert_policy_page(dict(page, **changed), {"Homepage"})

    def test_policy_page_follows_preference_value_continuation_rows(self):
        for status in ("locked", "user"):
            for owner_first in (True, False):
                with self.subTest(status=status, owner_first=owner_first):
                    owner_rows = [
                        ["", "browser.mac-setup.owner", "Status", json.dumps(status)],
                        ["", "", "Value", json.dumps(probe.OWNER_LABEL)],
                    ]
                    other_rows = [
                        ["", "privacy.globalprivacycontrol.enabled", "Value", "true"],
                    ]
                    rows = (
                        owner_rows + other_rows
                        if owner_first
                        else other_rows + owner_rows
                    )
                    rows[0][0] = "Preferences"
                    probe.assert_policy_page(
                        {"url": "about:policies", "activeRows": rows, "errorRows": []},
                        {"Preferences"},
                        owner_label=probe.OWNER_LABEL,
                    )

    def test_policy_page_requires_the_owner_label_under_its_own_value(self):
        rows = [
            [
                "Preferences",
                "browser.mac-setup.owner",
                "Value",
                json.dumps(probe.OWNER_LABEL),
            ],
            ["", "", "Status", '"user"'],
        ]
        page = {"url": "about:policies", "activeRows": rows, "errorRows": []}
        probe.assert_policy_page(page, {"Preferences"}, owner_label=probe.OWNER_LABEL)
        for rows in (
            [["Preferences", "browser.mac-setup.owner", "Value", '"wrong test label"']],
            [
                [
                    "Preferences",
                    "unrelated.preference",
                    "Value",
                    json.dumps(probe.OWNER_LABEL),
                ]
            ],
            [
                [
                    "Preferences",
                    "browser.mac-setup.owner",
                    "Status",
                    json.dumps(probe.OWNER_LABEL),
                ]
            ],
            [
                [
                    "Preferences",
                    "browser.mac-setup.owner",
                    "Value",
                    '"wrong test label"',
                ],
                ["", "unrelated.preference", "Value", json.dumps(probe.OWNER_LABEL)],
            ],
            [
                [
                    "Preferences",
                    "browser.mac-setup.owner",
                    "Value",
                    '"wrong test label"',
                ],
                ["Proxy", "", "Value", json.dumps(probe.OWNER_LABEL)],
            ],
        ):
            with self.subTest(rows=rows), self.assertRaises(probe.ProofError):
                probe.assert_policy_page(
                    dict(page, activeRows=rows),
                    {"Preferences"},
                    owner_label=probe.OWNER_LABEL,
                )

    def test_owner_and_extension_policies_require_exact_values_in_both_modes(self):
        for locked in (True, False):
            for factory, assertion in (
                (stored_policy, probe.assert_stored),
                (active_policy, probe.assert_active),
            ):
                for key in ("owner", "owner-status", "extension", "extension-url"):
                    document = factory(locked)
                    if key == "owner":
                        document["Preferences"]["browser.mac-setup.owner"][
                            "Value"
                        ] = "wrong test label"
                    elif key == "owner-status":
                        document["Preferences"]["browser.mac-setup.owner"]["Status"] = (
                            "user" if locked else "locked"
                        )
                    elif key == "extension":
                        document["ExtensionSettings"]["uBlock0@raymondhill.net"][
                            "installation_mode"
                        ] = ("normal_installed" if locked else "force_installed")
                    else:
                        document["ExtensionSettings"]["uBlock0@raymondhill.net"][
                            "install_url"
                        ] = "https://example.invalid/wrong-test-addon.xpi"
                    with self.subTest(locked=locked, key=key), self.assertRaises(
                        probe.ProofError
                    ):
                        assertion(document, locked)

    def test_extension_installation_waits_for_an_active_addon_with_expected_permissions(
        self,
    ):
        for locked in (True, False):
            client = Mock()
            client.script.side_effect = [
                None,
                dict(extension_state(locked), active=False),
                extension_state(locked),
            ]
            with patch.object(probe.time, "sleep"):
                self.assertEqual(
                    probe.extension_proof(client, locked),
                    {
                        "installed_extensions": 1,
                        "extension_user_can_disable": not locked,
                    },
                )

    def test_extension_installation_rejects_wrong_id_and_wrong_permissions(
        self,
    ):
        for locked in (True, False):
            for changed in (
                {"id": "wrong@example.invalid"},
                {"canDisable": locked},
                {"active": 1},
            ):
                client = Mock()
                client.script.return_value = dict(extension_state(locked), **changed)
                with self.subTest(locked=locked, changed=changed), self.assertRaises(
                    probe.ProofError
                ):
                    probe.extension_proof(client, locked)

    def test_extension_installation_times_out_without_an_installed_addon(self):
        client = Mock()
        for state in (None, dict(extension_state(True), active=False)):
            client.script.return_value = state
            with self.subTest(state=state), patch.object(
                probe.time, "monotonic", side_effect=[0, 121]
            ), self.assertRaises(probe.ProofError):
                probe.extension_proof(client, True)

    def test_stored_policy_checks_types_locks_and_unrelated_policy(self):
        locked = stored_policy(locked=True)
        probe.assert_stored(locked, locked=True)
        for path in ("Homepage", "SanitizeOnShutdown"):
            bad = copy.deepcopy(locked)
            bad[path]["Locked"] = 1
            with self.subTest(path=path), self.assertRaises(probe.ProofError):
                probe.assert_stored(bad, locked=True)
        unlocked = stored_policy(locked=False)
        probe.assert_stored(unlocked, locked=False)
        unlocked["Homepage"]["Locked"] = True
        with self.assertRaises(probe.ProofError):
            probe.assert_stored(unlocked, locked=False)
        unlocked["Homepage"]["Locked"] = False
        unlocked["Preferences"]["privacy.globalprivacycontrol.enabled"][
            "Status"
        ] = "locked"
        with self.assertRaises(probe.ProofError):
            probe.assert_stored(unlocked, locked=False)

    def test_stored_and_active_preferences_keep_their_distinct_types(self):
        for locked in (True, False):
            stored = stored_policy(locked)
            active = active_policy(locked)
            with self.subTest(locked=locked):
                probe.assert_stored(stored, locked)
                probe.assert_active(active, locked)
                with self.assertRaisesRegex(probe.ProofError, "Stored.*Preferences"):
                    probe.assert_stored(
                        dict(stored, Preferences=active["Preferences"]), locked
                    )
                with self.assertRaisesRegex(probe.ProofError, "Active.*Preferences"):
                    probe.assert_active(
                        dict(active, Preferences=stored["Preferences"]), locked
                    )

    def test_active_policy_rejects_wrong_values_status_and_native_lock_types(self):
        for locked in (True, False):
            for key in ("Homepage", "SanitizeOnShutdown"):
                document = active_policy(locked)
                document[key]["Locked"] = int(locked)
                with self.subTest(locked=locked, key=key), self.assertRaisesRegex(
                    probe.ProofError, "Active.*" + key
                ):
                    probe.assert_active(document, locked)
            for field, value in (
                ("Value", 0),
                ("Value", False),
                ("Value", 1.0),
                ("Status", "user" if locked else "locked"),
            ):
                document = active_policy(locked)
                document["Preferences"]["privacy.globalprivacycontrol.enabled"][
                    field
                ] = value
                with self.subTest(
                    locked=locked, field=field, value=value
                ), self.assertRaisesRegex(probe.ProofError, "Active.*Preferences"):
                    probe.assert_active(document, locked)
        document = active_policy(locked=False)
        document["DisableProfileImport"] = True
        with self.assertRaisesRegex(probe.ProofError, "Active.*import restriction"):
            probe.assert_active(document, locked=False)

    def test_owner_runtime_rejects_wrong_label_and_lock_state(self):
        state = {
            "homepage": "https://example.invalid/managed",
            "homepageLocked": True,
            "history": True,
            "historyLocked": True,
            "quitLocked": True,
            "gpc": True,
            "gpcLocked": True,
            "owner": "mac-setup artificial journey",
            "ownerLocked": True,
        }
        for changed in ({"owner": "wrong test label"}, {"ownerLocked": False}):
            with self.subTest(changed=changed), self.assertRaises(probe.ProofError):
                probe.assert_runtime(dict(state, **changed), locked=True)

    def test_runtime_requires_exact_effective_values_and_lock_states(self):
        state = {
            "homepage": "https://example.invalid/managed",
            "homepageLocked": True,
            "history": True,
            "historyLocked": True,
            "quitLocked": True,
            "gpc": True,
            "gpcLocked": True,
            "owner": "mac-setup artificial journey",
            "ownerLocked": True,
        }
        probe.assert_runtime(state, locked=True)
        bad = dict(state, historyLocked=False)
        with self.assertRaises(probe.ProofError):
            probe.assert_runtime(bad, locked=True)
        unlocked = dict(
            state,
            homepage="https://example.invalid/user-choice",
            homepageLocked=False,
            history=False,
            historyLocked=False,
            quitLocked=False,
            gpcLocked=False,
            ownerLocked=False,
        )
        probe.assert_runtime(unlocked, locked=False)
        with self.assertRaises(probe.ProofError):
            probe.assert_runtime(
                dict(unlocked, homepage=state["homepage"]), locked=False
            )

    def test_runtime_proof_accepts_mac_active_preferences_without_loader_key(self):
        active = active_policy(locked=True)
        page = {
            "url": "about:policies",
            "activeRows": [
                (
                    [
                        name,
                        "browser.mac-setup.owner",
                        "Value",
                        json.dumps(probe.OWNER_LABEL),
                    ]
                    if name == "Preferences"
                    else [name, "artificial value"]
                )
                for name in active
            ],
            "errorRows": [],
        }
        state = {
            "homepage": "https://example.invalid/managed",
            "homepageLocked": True,
            "history": True,
            "historyLocked": True,
            "quitLocked": True,
            "gpc": True,
            "gpcLocked": True,
            "owner": "mac-setup artificial journey",
            "ownerLocked": True,
        }
        client = Mock()
        client.script.side_effect = [page, active, state, extension_state(True)]
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            probe, "STATE", Path(temporary)
        ), patch.object(probe, "browser") as browser, patch.object(
            probe.subprocess,
            "Popen",
            side_effect=AssertionError("No browser may launch in a unit test"),
        ):
            browser.return_value.__enter__.return_value = (
                client,
                Path(temporary) / "profile",
            )
            proof = probe.runtime_proof("again", locked=True)
            self.assertEqual(
                proof,
                {
                    "policy_errors": 0,
                    "checked_preferences": 5,
                    "owner_label_verified": True,
                    "installed_extensions": 1,
                    "extension_user_can_disable": False,
                    "owned_locked_paths": 5,
                    "user_override_reported": False,
                },
            )
            self.assertEqual(
                json.loads((Path(temporary) / "again-proof.json").read_text()), proof
            )
            self.assertEqual(
                json.loads((Path(temporary) / "again-policies-page.json").read_text()),
                {
                    "url": "about:policies",
                    "policy_errors": 0,
                    "expected_policy_names": sorted(active),
                    "expected_policies_present": True,
                    "active_policies_verified": True,
                    "owner_label_visible": True,
                },
            )

    def test_runtime_proof_keeps_unrelated_private_policies_out_of_evidence(self):
        marker = "SYNTHETIC-PRIVATE-PROXY"
        active = active_policy(locked=True)
        expected_names = sorted(active)
        active["Proxy"] = {"HTTPProxy": marker}
        page = {
            "url": "about:policies",
            "activeRows": [
                (
                    [
                        name,
                        "browser.mac-setup.owner",
                        "Value",
                        json.dumps(probe.OWNER_LABEL),
                    ]
                    if name == "Preferences"
                    else [name, "artificial value"]
                )
                for name in expected_names
            ]
            + [["Proxy", marker]],
            "errorRows": [],
        }
        client = Mock()
        client.script.side_effect = [
            page,
            active,
            {
                "homepage": "https://example.invalid/managed",
                "homepageLocked": True,
                "history": True,
                "historyLocked": True,
                "quitLocked": True,
                "gpc": True,
                "gpcLocked": True,
                "owner": "mac-setup artificial journey",
                "ownerLocked": True,
            },
            extension_state(True),
        ]
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            probe, "STATE", Path(temporary)
        ), patch.object(probe, "browser") as browser, patch.object(
            probe.subprocess,
            "Popen",
            side_effect=AssertionError("No browser may launch in a unit test"),
        ), redirect_stdout(
            output
        ), redirect_stderr(
            output
        ):
            browser.return_value.__enter__.return_value = (
                client,
                Path(temporary) / "profile",
            )
            proof = probe.runtime_proof("again", locked=True)
            evidence = json.loads(
                (Path(temporary) / "again-policies-page.json").read_text()
            )
            self.assertEqual(proof["checked_preferences"], 5)
            self.assertEqual(proof["owned_locked_paths"], 5)
            for path in Path(temporary).rglob("*"):
                if path.is_file():
                    self.assertNotIn(marker.encode(), path.read_bytes())
            self.assertNotIn(marker, output.getvalue())
            self.assertEqual(
                evidence,
                {
                    "url": "about:policies",
                    "policy_errors": 0,
                    "expected_policy_names": expected_names,
                    "expected_policies_present": True,
                    "active_policies_verified": True,
                    "owner_label_visible": True,
                },
            )


class FirefoxRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = self.root / "policy.plist"
        self.owned = self.root / "owned.plist"
        self.lock = self.root / "owned.lock"
        self.state = self.root / "state"
        self.binary = self.root / "unused-firefox"
        self.binary.touch()
        for name, value in (
            ("POLICY", self.policy),
            ("OWNED", self.owned),
            ("LOCK", self.lock),
            ("STATE", self.state),
            ("FIREFOX", str(self.binary)),
            ("closed", lambda: None),
            ("command", self.command),
        ):
            replacement = patch.object(probe, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.launch_guard = patch.object(
            probe.subprocess,
            "Popen",
            side_effect=AssertionError("No browser may launch in a unit test"),
        )
        self.launch_guard.start()
        self.addCleanup(self.launch_guard.stop)

    def command(self, argv):
        if argv[2] == "/bin/cp":
            shutil.copy2(argv[-2], argv[-1])
        elif argv[2] == "/bin/rm":
            Path(argv[-1]).unlink()
        elif argv[2] == "/usr/bin/defaults":
            if argv[3] == "import":
                document = (
                    plistlib.loads(self.policy.read_bytes())
                    if self.policy.exists()
                    else {}
                )
                document.update(plistlib.loads(Path(argv[-1]).read_bytes()))
                self.policy.write_bytes(plistlib.dumps(document))
            elif argv[3] == "export":
                return self.policy.read_bytes()
            elif argv[3] == "delete":
                self.policy.unlink()
            else:
                raise AssertionError("Unexpected defaults command")
        else:
            raise AssertionError("Unexpected command")
        return b""

    def test_setup_isolates_seed_after_snapshot_and_restores_original_stores(self):
        self.policy.write_bytes(
            plistlib.dumps(
                {
                    "DisableProfileImport": True,
                    "Proxy": {"HTTPProxy": "SYNTHETIC-PRIVATE-PROXY"},
                }
            )
        )
        for path in (self.owned, self.lock):
            path.write_bytes(plistlib.dumps({"made-up": path.name}))
        original_bytes = {
            name: path.read_bytes() for name, path in probe.stores().items()
        }
        for path in probe.stores().values():
            path.chmod(0o640)
            os.utime(path, ns=(1000000000, 2000000000))
        before = probe.fingerprints()

        def assert_snapshot():
            self.assertEqual(
                json.loads((self.state / "original.json").read_text()), before
            )
            for name, contents in original_bytes.items():
                saved = self.state / ("original-" + name + ".plist")
                self.assertEqual(saved.read_bytes(), contents)
                self.assertEqual(probe.fingerprint(saved), before[name])

        def command(argv):
            if argv[2:4] == ["/usr/bin/defaults", "delete"]:
                assert_snapshot()
            return self.command(argv)

        def bookmark():
            self.assertEqual(
                plistlib.loads(self.policy.read_bytes()),
                {"EnterprisePoliciesEnabled": True, "DontCheckDefaultBrowser": True},
            )
            assert_snapshot()

        with patch.object(
            probe, "bookmark_feasibility", side_effect=bookmark
        ), patch.object(probe, "command", side_effect=command):
            try:
                probe.setup()
            finally:
                probe.restore()
        self.assertEqual(probe.fingerprints(), before)
        for name, path in probe.stores().items():
            self.assertEqual(path.read_bytes(), original_bytes[name])
        self.assertFalse((self.state / "original.json").exists())
        self.assertFalse(list(self.state.glob("original-*.plist")))

    def test_seed_verification_failure_keeps_snapshot_for_restore(self):
        self.policy.write_bytes(plistlib.dumps({"made-up": True}))
        self.policy.chmod(0o640)
        os.utime(self.policy, ns=(1000000000, 2000000000))
        before = probe.fingerprints()

        def command(argv):
            result = self.command(argv)
            if argv[2:4] == ["/usr/bin/defaults", "export"]:
                return plistlib.dumps(
                    {"EnterprisePoliciesEnabled": 1, "DontCheckDefaultBrowser": True}
                )
            return result

        with patch.object(probe, "command", side_effect=command), patch.object(
            probe, "bookmark_feasibility"
        ) as bookmark:
            try:
                with self.assertRaises(probe.ProofError):
                    probe.setup()
                bookmark.assert_not_called()
                self.assertTrue((self.state / "original.json").is_file())
            finally:
                probe.restore()
        self.assertEqual(probe.fingerprints(), before)
        self.assertFalse((self.state / "original.json").exists())
        self.assertFalse(list(self.state.glob("original-*.plist")))

    def test_failed_bookmark_probe_restores_all_original_files(self):
        for path in (self.policy, self.owned, self.lock):
            path.write_bytes(plistlib.dumps({"made-up": path.name}))
            path.chmod(0o640)
            os.utime(path, ns=(1000000000, 2000000000))
        before = probe.fingerprints()
        with patch.object(
            probe, "bookmark_feasibility", side_effect=probe.ProofError("test input")
        ):
            try:
                with self.assertRaises(probe.ProofError):
                    probe.setup()
                self.assertFalse(self.owned.exists())
            finally:
                probe.restore()
        self.assertEqual(probe.fingerprints(), before)
        self.assertFalse((self.state / "original.json").exists())
        self.assertFalse(list(self.state.glob("original-*.plist")))

    def test_restore_removes_only_the_new_stores_when_originally_absent(self):
        with patch.object(probe, "bookmark_feasibility"):
            probe.setup()
        self.owned.write_bytes(plistlib.dumps({"owned": []}))
        self.lock.touch()
        self.assertTrue(self.policy.exists())
        probe.restore()
        self.assertFalse(self.policy.exists())
        self.assertFalse(self.owned.exists())
        self.assertFalse(self.lock.exists())

    def test_fingerprint_refuses_a_policy_symlink(self):
        target = self.root / "unrelated"
        target.write_text("made-up")
        self.policy.symlink_to(target)
        with self.assertRaises(probe.ProofError):
            probe.fingerprints()
        self.assertEqual(target.read_text(), "made-up")

    def test_restore_refuses_a_replaced_policy_symlink_and_keeps_snapshot(self):
        self.policy.write_bytes(plistlib.dumps({"made-up": True}))
        with patch.object(probe, "bookmark_feasibility"):
            probe.setup()
        self.policy.unlink()
        unrelated = self.root / "unrelated"
        unrelated.write_text("made-up")
        self.policy.symlink_to(unrelated)
        with self.assertRaises(probe.ProofError):
            probe.restore()
        self.assertEqual(unrelated.read_text(), "made-up")
        self.assertTrue((self.state / "original.json").is_file())


if __name__ == "__main__":
    unittest.main()
