import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "bookmarks_policy_restore", ROOT / "verify/helpers/bookmarks.py"
)
bookmarks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bookmarks)


class BookmarksPolicyRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.state = self.root / "journey"
        self.policy_state = self.state / "policy"
        self.stores = {
            "policy": self.root / "firefox.plist",
            "ownership": self.root / "owned.plist",
            "lock": self.root / "owned.lock",
        }
        self.original = {}
        for index, (name, path) in enumerate(self.stores.items()):
            path.write_bytes(plistlib.dumps({"synthetic-original": name}))
            path.chmod((0o600, 0o640, 0o604)[index])
            os.utime(path, ns=(1_000_000_000, 2_000_000_000 + index * 1_000_000_000))
            self.original[name] = self.file_state(path)
        self.fail_after_policy_write = False
        self.fail_restore_ownership = False
        self.mutations = 0
        self.browser = MagicMock()
        self.browser.return_value.__enter__.return_value.port = 40123
        self.browser.return_value.__enter__.return_value.version = "160.0"
        replacements = [
            patch.object(bookmarks, "STATE", self.state),
            patch.object(bookmarks, "make_fixture", return_value=None),
            patch.object(bookmarks.socket, "socket", return_value=Mock()),
            patch.object(bookmarks.importer, "FirefoxSession", self.browser),
            patch.object(bookmarks, "temporary_directories", return_value=[]),
            patch.object(bookmarks.firefox, "POLICY", self.stores["policy"]),
            patch.object(bookmarks.firefox, "OWNED", self.stores["ownership"]),
            patch.object(bookmarks.firefox, "LOCK", self.stores["lock"]),
            patch.object(bookmarks.firefox, "closed", return_value=None),
            patch.object(bookmarks.firefox, "command", side_effect=self.command),
            patch.object(
                bookmarks.firefox.subprocess,
                "Popen",
                side_effect=AssertionError("A synthetic test must not launch Firefox"),
            ),
            patch.object(
                bookmarks.firefox.subprocess,
                "run",
                side_effect=AssertionError("A synthetic test must not run OS commands"),
            ),
            patch.dict(
                os.environ,
                {"ANSIBLE_VAULT_PASSWORD_FILE": str(self.state / "password")},
            ),
        ]
        for replacement in replacements:
            replacement.start()
            self.addCleanup(replacement.stop)

    def file_state(self, path):
        info = path.stat()
        return {
            "contents": path.read_bytes(),
            "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid,
            "gid": info.st_gid,
            "mtime_ns": info.st_mtime_ns,
        }

    def assert_snapshots(self):
        record = json.loads((self.policy_state / "original.json").read_text())
        self.assertEqual(set(record), set(self.original))
        for name, expected in self.original.items():
            self.assertTrue(record[name]["exists"])
            self.assertEqual(
                {key: record[name][key] for key in expected if key != "contents"},
                {key: value for key, value in expected.items() if key != "contents"},
            )
            saved = self.policy_state / ("original-" + name + ".plist")
            self.assertEqual(self.file_state(saved), expected)

    def command(self, argv):
        self.assertEqual(argv[:2], ["/usr/bin/sudo", "-n"])
        operation = argv[2]
        if operation == "/bin/cp":
            source, destination = map(Path, argv[-2:])
            if source.parent == self.policy_state:
                if (
                    self.fail_restore_ownership
                    and destination == self.stores["ownership"]
                ):
                    raise OSError("Synthetic restoration refusal")
            shutil.copy2(source, destination)
        elif operation == "/bin/rm":
            path = Path(argv[-1])
            if path == self.stores["ownership"]:
                self.assert_snapshots()
                self.mutations += 1
            path.unlink()
        elif operation == "/usr/bin/defaults":
            action = argv[3]
            policy = self.stores["policy"]
            if action == "export":
                return policy.read_bytes()
            self.assert_snapshots()
            self.mutations += 1
            if action == "delete":
                policy.unlink()
            elif action == "import":
                contents = (
                    plistlib.loads(policy.read_bytes()) if policy.exists() else {}
                )
                contents.update(plistlib.loads(Path(argv[-1]).read_bytes()))
                policy.write_bytes(plistlib.dumps(contents))
            elif action == "write":
                self.assertEqual(argv[-3:], ["DisableProfileImport", "-bool", "true"])
                contents = plistlib.loads(policy.read_bytes())
                contents["DisableProfileImport"] = True
                policy.write_bytes(plistlib.dumps(contents))
                if self.fail_after_policy_write:
                    raise bookmarks.firefox.ProofError("Synthetic setup interruption")
            else:
                self.fail("Unexpected defaults action")
        else:
            self.fail("Unexpected system command")
        return b""

    def assert_restored(self):
        for name, path in self.stores.items():
            self.assertEqual(self.file_state(path), self.original[name])
        self.assertFalse(self.state.exists())

    def test_cleanup_restores_all_store_bytes_and_metadata_after_setup(self):
        bookmarks.setup()
        self.assertGreater(self.mutations, 0)
        self.assert_snapshots()
        self.assertTrue(
            plistlib.loads(self.stores["policy"].read_bytes())["DisableProfileImport"]
        )
        self.assertFalse(self.stores["ownership"].exists())
        for path in self.stores.values():
            path.write_bytes(plistlib.dumps({"synthetic-journey": path.name}))
            path.chmod(0o600)
            os.utime(path, ns=(9_000_000_000, 9_000_000_000))
        bookmarks.cleanup()
        self.assert_restored()

    def test_setup_uses_only_the_production_session_for_browser_startup(self):
        with patch.object(
            bookmarks.firefox,
            "setup",
            side_effect=AssertionError("Legacy setup must not select a browser"),
        ), patch.object(
            bookmarks.firefox,
            "browser",
            side_effect=AssertionError("Legacy direct browser launch is forbidden"),
        ), patch.object(
            bookmarks.firefox,
            "bookmark_feasibility",
            side_effect=AssertionError("Legacy profile probe is forbidden"),
        ):
            bookmarks.setup()
        self.browser.assert_called_once_with(self.state / "startup-control")
        self.browser.return_value.__exit__.assert_called_once()
        self.assertEqual(
            json.loads((self.state / "startup-control.json").read_text()),
            {
                "default_port_occupied": True,
                "selected_default_port": False,
                "firefox_version": "160.0",
            },
        )
        self.assert_snapshots()
        bookmarks.cleanup()
        self.assert_restored()

    def test_cleanup_restores_stores_after_setup_interrupts_a_policy_write(self):
        self.fail_after_policy_write = True
        with self.assertRaises(bookmarks.firefox.ProofError):
            bookmarks.setup()
        self.assertTrue(
            plistlib.loads(self.stores["policy"].read_bytes())["DisableProfileImport"]
        )
        self.assertFalse(self.stores["ownership"].exists())
        self.assert_snapshots()
        bookmarks.cleanup()
        self.assert_restored()

    def test_failed_cleanup_retains_all_snapshots_for_a_retry(self):
        bookmarks.setup()
        self.fail_restore_ownership = True
        with self.assertRaises(bookmarks.firefox.ProofError) as caught:
            bookmarks.cleanup()
        self.assertIn("restore failed (system-error)", str(caught.exception))
        self.assertNotIn("Synthetic restoration refusal", str(caught.exception))
        self.assert_snapshots()
        self.assertTrue(self.state.is_dir())
        self.fail_restore_ownership = False
        bookmarks.cleanup()
        self.assert_restored()


if __name__ == "__main__":
    unittest.main()
