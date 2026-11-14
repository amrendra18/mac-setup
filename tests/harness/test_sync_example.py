import importlib.machinery
import shutil
import tempfile
import importlib.util
import sys
import unittest
from unittest.mock import patch
import contextlib
import io
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/profile/filter_plugins"))

from profile import profile_problems


def load_script():
    loader = importlib.machinery.SourceFileLoader(
        "sync_example_under_test", str(ROOT / "scripts" / "sync-example")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def reference_files():
    return {
        path.name: yaml.safe_load(path.read_text())
        for path in sorted((ROOT / "settings/macos").glob("*.yml"))
    }


class SyncExample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = load_script()
        cls.entries = cls.script.reference()

    def neutral(self, settings):
        refused = []
        return self.script.neutral_settings(settings, self.entries, refused), refused

    def test_personal_values_stay_out(self):
        result, refused = self.neutral(
            {
                "time.zone": "Zz/Nowhere",
                "network.wifi_power": False,
                "computer.name": "zzPersonalMac",
                "login_window.message": "Property of Zz Person",
                "sharing.remote_login": False,
                "finder.sidebar_favorites": [
                    "/Applications",
                    "~",
                    str(Path("/Users") / "zzother"),
                    "/Volumes/zzdrive",
                    "~/Documents/zzclient",
                    "~/Downloads",
                ],
                "dock.position": "left",
                "dock.applications": ["com.google.Chrome", "com.apple.Terminal"],
                "power.display_sleep_minutes": 3,
                "power.system_sleep_minutes": 0,
                "apps.install": ["sublime_text", "google_chrome"],
            }
        )
        self.assertEqual(sorted(refused), ["computer.name", "login_window.message"])
        self.assertEqual(result["time.zone"], "GMT")
        self.assertIs(result["network.wifi_power"], True)
        self.assertNotIn("sharing.remote_login", result)
        self.assertEqual(
            result["finder.sidebar_favorites"], ["/Applications", "~", "~/Downloads"]
        )
        self.assertEqual(result["dock.position"], "left")
        self.assertEqual(result["dock.applications"], ["com.apple.Terminal"])
        self.assertEqual(result["power.display_sleep_minutes"], 10)
        self.assertEqual(result["power.system_sleep_minutes"], 30)
        self.assertEqual(result["apps.install"], [])
        self.assertNotIn("zz", yaml.safe_dump(result).lower())

    def test_the_example_blocks_all_incoming_connections(self):
        for private in [
            {"time.zone": "America/Vancouver"},
            {
                "security.firewall": False,
                "security.firewall_block_all": False,
                "security.stealth": False,
                "security.firewall_allow_builtin_signed": True,
                "security.firewall_allow_downloaded_signed": True,
                "computer.local_hostname": "zzPersonalMac",
            },
        ]:
            refused = []
            machine = self.script.neutral_machine(private, self.entries, refused)
            settings = machine["machine_settings"]
            self.assertEqual(refused, [])
            self.assertIs(machine["allow_lockout_risk"], True)
            self.assertIs(settings["security.firewall"], True)
            self.assertIs(settings["security.firewall_block_all"], True)
            self.assertIs(settings["security.stealth"], True)
            self.assertIs(settings["security.firewall_allow_builtin_signed"], False)
            self.assertIs(settings["security.firewall_allow_downloaded_signed"], False)
            self.assertNotIn("computer.local_hostname", settings)
            self.assertEqual(profile_problems(machine, reference_files()), [])

    def test_an_encrypted_vault_file_in_the_profile_is_skipped(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        host = folder / "host_vars" / "mac"
        host.mkdir(parents=True)
        (host / "accounts.yml").write_text("default_settings:\n  dock.position: left\n")
        (host / "secrets.vault.yml").write_text("$ANSIBLE_VAULT;1.1;AES256\n6162\n")
        self.assertEqual(
            self.script.load(folder), {"default_settings": {"dock.position": "left"}}
        )

    def test_multiple_hosts_stop_before_any_output_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for host, text in (
                ("office-mac", "default_settings:\n  dock.position: left\n"),
                ("studio-mac", "machine_settings:\n  power.system_sleep_minutes: 20\n"),
            ):
                path = folder / "host_vars" / host / "settings.yml"
                path.parent.mkdir(parents=True)
                path.write_text(text)
            example = folder / "example"
            example.mkdir()
            journey = folder / "journey.yml"
            outputs = [example / "accounts.yml", example / "machine.yml", journey]
            for output in outputs:
                output.write_text("profile:\n  original: true\nruns:\n")
            before = [p.read_bytes() for p in outputs]
            with patch.object(self.script, "EXAMPLE", example), patch.object(
                self.script, "JOURNEY", journey
            ):
                with patch.object(sys, "argv", ["sync-example", str(folder)]):
                    with contextlib.redirect_stderr(io.StringIO()) as stderr:
                        with self.assertRaises(SystemExit) as caught:
                            self.script.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("one host_vars directory", stderr.getvalue())
            self.assertEqual([p.read_bytes() for p in outputs], before)

    def test_sync_example_refuses_private_browser_values(self):
        entries = {
            "firefox.private": {
                "values": {"type": "text", "pattern": ".*"},
                "browser": {"capture": {"class": "mask"}},
            },
            "chrome.structured": {
                "values": {"type": "browser", "schema": {"type": "object"}},
                "browser": {"capture": {"class": "mask"}},
            },
            "chrome.endpoint": {
                "values": {"type": "text", "pattern": ".*"},
                "browser": {
                    "capture": {
                        "class": "public_endpoint",
                        "choices": ["https://example.org/"],
                    }
                },
            },
            "firefox.safe": {
                "values": {"type": "bool"},
                "browser": {"capture": {"class": "scalar"}},
            },
            "chrome.enum": {
                "values": ["system", "light", "dark"],
                "browser": {
                    "capture": {"class": "enum", "choices": ["system", "light", "dark"]}
                },
            },
        }
        settings = {
            "firefox.private": "test-input-identifying-name",
            "chrome.structured": {"name": "test-input-identifying-name"},
            "chrome.endpoint": "https://example.org/",
            "firefox.safe": True,
            "chrome.enum": "system",
        }
        refused = []
        result = self.script.neutral_settings(settings, entries, refused)
        self.assertEqual(sorted(refused), ["chrome.structured", "firefox.private"])
        self.assertEqual(
            result,
            {
                "chrome.endpoint": "https://example.org/",
                "firefox.safe": True,
                "chrome.enum": "system",
            },
        )
        for key, value in [
            ("firefox.safe", "test-input-identifying-name"),
            ("chrome.enum", "test-input-identifying-name"),
            ("chrome.endpoint", "https://example.org/private"),
        ]:
            refused = []
            self.assertEqual(
                self.script.neutral_settings({key: value}, entries, refused), {}
            )
            self.assertEqual(refused, [key])

    def test_accounts_get_made_up_names(self):
        refused = []
        accounts = self.script.neutral_accounts(
            [
                {
                    "name": "zzreal",
                    "full_name": "Zz Real",
                    "admin": True,
                    "password": "x",
                }
            ],
            self.entries,
            refused,
        )
        self.assertEqual(
            accounts, [{"name": "sam", "full_name": "Sam Example", "admin": True}]
        )

    def test_the_tracked_example_passes_the_profile_check(self):
        example = {}
        for path in sorted(
            (ROOT / "inventory/example/host_vars/example-mac").glob("*.yml")
        ):
            example.update(yaml.safe_load(path.read_text()))
        self.assertEqual(profile_problems(example, reference_files()), [])


if __name__ == "__main__":
    unittest.main()
