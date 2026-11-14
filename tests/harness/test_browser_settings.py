import hashlib
import importlib.util
import json
import os
import plistlib
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "browser_reader", ROOT / "module_utils/browser_settings.py"
)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


class BrowserCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "Firefox" / "fixture-profile"
        self.profile.mkdir(parents=True)
        self.policies = self.root / "policies"
        self.policies.mkdir()
        self.output = self.root / "review.yml"
        (self.profile / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=158.0.1_20261101010101/20261101010101\n"
        )
        (self.profile / "prefs.js").write_text(
            'user_pref("privacy.globalprivacycontrol.enabled", true);\n'
        )

    def cli(self, *extra, command="capture"):
        args = [
            "/usr/bin/python3",
            "-I",
            "-B",
            str(ROOT / "scripts/browser-settings"),
            command,
            "--browser",
            "firefox",
            "--firefox-profile",
            str(self.profile),
            "--policy-root",
            str(self.policies),
        ]
        if command == "capture":
            args += ["--output", str(self.output)]
        return subprocess.run(
            args + list(extra),
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_capture_runs_with_system_python_without_site_packages(self):
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = yaml.safe_load(self.output.read_text())
        self.assertEqual(
            data["machine_settings"][
                "firefox.pref.privacy_globalprivacycontrol_enabled"
            ],
            True,
        )
        self.assertIn("1 safe assignments", result.stdout)

    def test_capture_keeps_nested_extension_payload_keys_opaque(self):
        (self.profile / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=160.0_20261101010101/20261101010101\n"
        )
        (self.profile / "prefs.js").write_text(
            'user_pref("browser.newtabpage.enabled", false);'
        )
        # Deliberately personal test input beside one mapped safe preference.
        extension_id = "fixture-addon-" + os.urandom(6).hex() + "@example.invalid"
        private_value = "artificial-private-" + os.urandom(6).hex()
        for index, payload_key in enumerate(
            ("cache__51", "cache____name", "cache__name")
        ):
            with self.subTest(payload_key=payload_key):
                self.output = self.root / ("review-" + str(index) + ".yml")
                policy = {
                    "EnterprisePoliciesEnabled": True,
                    "3rdparty": {
                        "Extensions": {extension_id: {payload_key: private_value}}
                    },
                }
                (self.policies / "org.mozilla.firefox.plist").write_bytes(
                    plistlib.dumps(policy)
                )
                result = self.cli()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                text = self.output.read_text()
                self.assertEqual(
                    yaml.safe_load(text),
                    {
                        "machine_settings": {"firefox.new_tab_page": False},
                        "default_settings": {},
                    },
                )
                self.assertIn("1 safe assignments; 1 masked settings", result.stdout)
                self.assertIn("# firefox.3rdparty.extensions: masked", text)
                for planted in (extension_id, private_value, payload_key):
                    self.assertNotIn(planted, text + result.stdout + result.stderr)

    def test_yaml_candidate_round_trips_and_pastes(self):
        item = {
            "browser": "firefox",
            "versions": {"browser": "158.0.1"},
            "counts": {"captured": 1},
            "settings": {
                "firefox.safe": {
                    "scope": "machine",
                    "state": "captured",
                    "value": False,
                    "observations": [{"source": "firefox_prefs", "state": "persisted"}],
                }
            },
        }
        text = reader.candidate_yaml([item])
        data = yaml.safe_load(text)
        self.assertEqual(
            data, {"machine_settings": {"firefox.safe": False}, "default_settings": {}}
        )
        line = next(
            line for line in text.splitlines() if line.startswith("  firefox.safe:")
        )
        self.assertEqual(
            yaml.safe_load("machine_settings:\n" + line),
            {"machine_settings": {"firefox.safe": False}},
        )
        self.assertIn("suggests a machine policy", text)

    def test_masked_absent_and_unverified_values_are_not_assignments(self):
        states = ("masked", "absent", "unavailable", "conflict", "unsupported")
        item = {
            "browser": "firefox",
            "versions": {},
            "counts": {},
            "settings": {
                "firefox."
                + state: {"scope": "machine", "state": state, "observations": []}
                for state in states
            },
        }
        text = reader.candidate_yaml([item])
        self.assertEqual(
            yaml.safe_load(text), {"machine_settings": {}, "default_settings": {}}
        )
        self.assertNotIn("<redacted>", text)
        self.assertNotIn("firefox.lock:", json.dumps(yaml.safe_load(text)))

    def test_output_is_exclusive_private_and_outside_browser_stores(self):
        reader.write_candidate(self.output, "review\n", [self.profile])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        with self.assertRaises(reader.CaptureError):
            reader.write_candidate(self.output, "replacement", [self.profile])
        self.assertEqual(self.output.read_text(), "review\n")
        target = self.profile / "new.yml"
        with self.assertRaises(reader.CaptureError):
            reader.write_candidate(target, "review", [self.profile])
        link = self.root / "link.yml"
        link.symlink_to(self.output)
        with self.assertRaises(reader.CaptureError):
            reader.write_candidate(link, "replacement", [])
        self.assertEqual(self.output.read_text(), "review\n")
        failing = self.root / "partial.yml"
        with patch.object(
            reader.os, "fchmod", side_effect=OSError("Deliberately broken test input")
        ):
            with self.assertRaises(reader.CaptureError):
                reader.write_candidate(failing, "review", [])
        self.assertFalse(failing.exists())

    def test_output_parent_swap_and_interruption_leave_no_unsafe_report(self):
        approved = self.root / "approved"
        approved.mkdir()
        link = self.root / "parent"
        link.symlink_to(approved, target_is_directory=True)
        original = reader._open_directory

        def swap(path):
            descriptor = original(path)
            link.unlink()
            link.symlink_to(self.profile, target_is_directory=True)
            return descriptor

        with patch.object(reader, "_open_directory", side_effect=swap):
            reader.write_candidate(link / "safe.yml", "review", [self.profile])
        self.assertEqual((approved / "safe.yml").read_text(), "review")
        self.assertFalse((self.profile / "safe.yml").exists())
        bundle = self.root / "RenamedBrowser.app" / "Contents"
        bundle.mkdir(parents=True)
        with self.assertRaises(reader.CaptureError):
            reader.write_candidate(bundle / "review.yml", "review", [])
        self.assertFalse((bundle / "review.yml").exists())
        interrupted = self.root / "interrupted.yml"
        with patch.object(reader.os, "fchmod", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                reader.write_candidate(interrupted, "review", [])
        self.assertFalse(interrupted.exists())

    def test_capture_changes_no_source_and_creates_no_bytecode(self):
        before = {
            path: (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_mtime_ns,
            )
            for path in self.profile.iterdir()
        }
        bytecode_before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (ROOT / "module_utils").rglob("*.pyc")
        }
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        bytecode_after = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (ROOT / "module_utils").rglob("*.pyc")
        }
        self.assertEqual(bytecode_after, bytecode_before)
        after = {
            path: (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_mtime_ns,
            )
            for path in self.profile.iterdir()
        }
        self.assertEqual(after, before)
        self.assertEqual(list(self.root.rglob("__pycache__")), [])

    def test_empty_capture_is_refused(self):
        (self.profile / "prefs.js").write_text("// Artificial empty test input\n")
        result = self.cli()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("No safe mapped value", result.stderr)
        self.assertFalse(self.output.exists())

    def test_unmapped_names_with_domains_or_extension_ids_are_hidden(self):
        # Deliberately personal test input, generated beside a mapped safe value.
        canary = "private-" + os.urandom(8).hex() + "@example.invalid"
        with (self.profile / "prefs.js").open("a") as stream:
            stream.write("user_pref(" + json.dumps(canary) + ', "personal");')
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(
            canary, result.stdout + result.stderr + self.output.read_text()
        )
        self.assertIn('"unmapped_persisted_entries": 1', self.output.read_text())
        self.assertEqual(
            len(yaml.safe_load(self.output.read_text())["machine_settings"]), 1
        )

    def test_safe_errors_hide_invalid_arguments(self):
        result = self.cli("--unknown-personal@example.invalid")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("example.invalid", result.stderr)
        result = self.cli("--setting", "personal@example.invalid", command="read-back")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("example.invalid", result.stderr)

    def test_read_back_exposes_saved_state_without_claiming_enforcement(self):
        result = self.cli(command="read-back")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        item = report["browsers"][0]["settings"][
            "firefox.pref.privacy_globalprivacycontrol_enabled"
        ]
        self.assertTrue(item["value"])
        self.assertEqual(item["observations"][0]["state"], "persisted")
        self.assertEqual(item["lock"], "unknown")
        self.assertEqual(report["browsers"][0]["policy_sources"], "selected_copy")

    def test_capture_newer_firefox_names_observed_and_tested_versions(self):
        (self.profile / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=160.0_20261101010101/20261101010101\n"
        )
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.output.read_text()
        self.assertIn("# firefox map tested on 158.0.1; observed 160.0", text)
        self.assertEqual(
            yaml.safe_load(text)["machine_settings"][
                "firefox.pref.privacy_globalprivacycontrol_enabled"
            ],
            True,
        )

    def test_read_back_selection_keeps_catalog_unmapped_counts(self):
        with (self.profile / "prefs.js").open("a") as stream:
            stream.write('user_pref("browser.newtabpage.enabled", false);')
        result = self.cli(
            "--setting",
            "firefox.pref.privacy_globalprivacycontrol_enabled",
            command="read-back",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        browser = json.loads(result.stdout)["browsers"][0]
        self.assertEqual(
            list(browser["settings"]),
            ["firefox.pref.privacy_globalprivacycontrol_enabled"],
        )
        self.assertEqual(browser["counts"]["unmapped_persisted_entries"], 0)
        self.assertEqual(browser["counts"]["captured"], 1)

    def test_ansible_packages_shared_browser_utilities(self):
        library = self.root / "library"
        library.mkdir()
        (library / "browser_probe.py").write_text(
            "from ansible.module_utils.basic import AnsibleModule\n"
            "from ansible.module_utils.browser_settings import parse_firefox_prefs\n"
            "from ansible.module_utils.browser_settings_data import BROWSER_SETTINGS\n"
            "m = AnsibleModule(argument_spec={}, supports_check_mode=True)\n"
            'm.exit_json(changed=False, count=len(BROWSER_SETTINGS), parsed=parse_firefox_prefs(\'user_pref("safe", true);\')["safe"])\n'
        )
        playbook = self.root / "probe.yml"
        playbook.write_text(
            "- hosts: all\n  gather_facts: false\n  tasks:\n    - name: Read packaged browser utilities\n      browser_probe:\n      register: probe\n    - name: Check packaged result\n      ansible.builtin.assert:\n        that:\n          - probe.count > 0\n          - probe.parsed\n          - not probe.changed\n"
        )
        environment = dict(
            os.environ,
            ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
            ANSIBLE_LIBRARY=str(library),
            ANSIBLE_LOCAL_TEMP=str(self.root / "local-tmp"),
            ANSIBLE_REMOTE_TEMP=str(self.root / "remote-tmp"),
            ANSIBLE_LOG_PATH=str(self.root / "probe.log"),
        )
        result = subprocess.run(
            [
                str(ROOT / ".local/venv/bin/ansible-playbook"),
                "-i",
                "localhost,",
                "-c",
                "local",
                "-e",
                "ansible_python_interpreter=" + str(ROOT / ".local/venv/bin/python"),
                str(playbook),
                "--check",
            ],
            env=environment,
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed=0", result.stdout)
        self.assertIn("failed=0", result.stdout)


if __name__ == "__main__":
    unittest.main()
