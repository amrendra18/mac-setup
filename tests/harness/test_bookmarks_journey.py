import importlib.util
import ast
from contextlib import redirect_stderr
import io
import subprocess
import shutil
import sys
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from jinja2 import Environment
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "bookmarks_journey", ROOT / "verify/helpers/bookmarks.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class BookmarksJourneyTests(unittest.TestCase):
    def test_wrapper_and_probes_select_the_same_isolated_target_for_every_stage(self):
        wrapper = yaml.safe_load((ROOT / "verify/helpers/bookmarks.yml").read_text())
        expression = wrapper[0]["tasks"][1]["ansible.builtin.set_fact"][
            "macos_firefox_bookmarks_root"
        ]
        environment = Environment()
        environment.filters["realpath"] = lambda value: str(Path(value).resolve())
        template = environment.from_string(expression)
        for stage in probe.STAGES:
            with self.subTest(stage=stage):
                rendered = template.render(
                    playbook_dir=str(ROOT / "verify/helpers"),
                    bookmarks_journey_stage=stage,
                )
                self.assertEqual(Path(rendered), probe.target(stage))

    def rendered_target(self, playbook_dir, stage="apply"):
        wrapper = yaml.safe_load((ROOT / "verify/helpers/bookmarks.yml").read_text())
        expression = wrapper[0]["tasks"][1]["ansible.builtin.set_fact"][
            "macos_firefox_bookmarks_root"
        ]
        environment = Environment()
        environment.filters["realpath"] = lambda value: str(Path(value).resolve())
        return environment.from_string(expression).render(
            playbook_dir=str(playbook_dir), bookmarks_journey_stage=stage
        )

    def test_wrapper_keeps_target_symlinks_visible_to_the_production_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "verify/helpers").mkdir(parents=True)
            target = root / ".local/bookmarks-journey/firefox"
            target.parent.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            target.symlink_to(outside, target_is_directory=True)
            rendered = self.rendered_target(root / "verify/helpers")
            self.assertEqual(Path(rendered), target)
            with self.assertRaises(probe.importer.BookmarkError):
                probe.importer.FirefoxSession(rendered)
            self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(
        shutil.which("node"), "Node executes the actual isolated-profile check"
    )
    def test_actual_identity_script_accepts_canonical_wrapper_target_and_rejects_siblings(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "verify/helpers").mkdir(parents=True)
            rendered = self.rendered_target(root / "verify/helpers")
            expected = str(root / ".local/bookmarks-journey/firefox")
            script = r"""
const script = process.argv[1], root = process.argv[2], expected = process.argv[3];
let current;
globalThis.Ci = {nsIFile: {}};
globalThis.Services = {
 dirsvc: {get: () => ({path: current})},
 prefs: {getStringPref: () => '11111111-1111-4111-8111-111111111111'}
};
const run = () => new Function(script)(root);
current = expected + '/app-data/Profiles/synthetic';
if (!run().isolated) throw Error('Canonical target was rejected');
for (const path of [expected + '-other/profile', expected.slice(0, expected.lastIndexOf('/')) + '/outside/profile']) {
 current = path;
 if (run().isolated) throw Error('An outside profile was accepted');
}
process.stdout.write(JSON.stringify({inside: true, outside: false}));
"""
            result = subprocess.run(
                [
                    shutil.which("node"),
                    "--input-type=module",
                    "-e",
                    script,
                    probe.importer.IDENTITY_SCRIPT,
                    rendered,
                    expected,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(
                result.returncode, 0, "Synthetic native profile-isolation check failed."
            )
            self.assertEqual(
                json.loads(result.stdout), {"inside": True, "outside": False}
            )

    def test_setup_refuses_the_real_vault_configuration_before_any_mac_access(self):
        with patch.dict(probe.os.environ, {}, clear=True), patch.object(
            probe.firefox, "closed"
        ) as closed, patch.object(probe, "seed_policy") as seed_policy:
            with self.assertRaises(probe.firefox.ProofError) as raised:
                probe.setup()
        self.assertIn("ANSIBLE_VAULT_PASSWORD_FILE", str(raised.exception))
        closed.assert_not_called()
        seed_policy.assert_not_called()

    def test_cli_names_fixed_checks_without_printing_private_exception_values(self):
        cases = (
            (
                probe.JourneyCheckError(
                    "Test vault environment must select the journey password file."
                ),
                "Test vault environment",
            ),
            (
                probe.firefox.ProofError("SYNTHETIC-PRIVATE"),
                "Firefox browser or policy validation",
            ),
            (
                probe.importer.PermissionDenied("SYNTHETIC-PRIVATE"),
                probe.importer.PERMISSION_MESSAGE,
            ),
            (
                probe.importer.StartupError("SYNTHETIC-PRIVATE", check="gui"),
                probe.importer.GUI_MESSAGE,
            ),
            (
                probe.importer.StartupError("SYNTHETIC-PRIVATE", check="launch"),
                probe.importer.LAUNCH_MESSAGE,
            ),
            (
                probe.importer.StartupError("SYNTHETIC-PRIVATE", check="system_access"),
                probe.importer.SYSTEM_ACCESS_MESSAGE,
            ),
            (
                probe.importer.StartupError("SYNTHETIC-PRIVATE", check="isolation"),
                probe.importer.ISOLATION_MESSAGE,
            ),
            (
                probe.importer.StartupError(
                    "SYNTHETIC-PRIVATE", check="state_directory"
                ),
                probe.importer.STATE_DIRECTORY_MESSAGE,
            ),
            (
                probe.importer.StartupError("SYNTHETIC-PRIVATE"),
                "Firefox startup through LaunchServices",
            ),
            (
                probe.importer.BookmarkError("SYNTHETIC-PRIVATE"),
                "Firefox import operation",
            ),
            (OSError("SYNTHETIC-PRIVATE"), "private file access"),
            (ValueError("SYNTHETIC-PRIVATE"), "private fixture format"),
            (
                subprocess.SubprocessError("SYNTHETIC-PRIVATE"),
                "browser command execution",
            ),
        )
        for error, message in cases:
            with self.subTest(check=message), patch.object(
                probe, "main", side_effect=error
            ):
                output = io.StringIO()
                with redirect_stderr(output):
                    self.assertEqual(probe.cli(), 1)
                self.assertIn(message, output.getvalue())
                self.assertNotIn("SYNTHETIC-PRIVATE", output.getvalue())

    def test_runtime_evidence_keeps_only_safe_setup_observations(self):
        for version, expected in (
            ("160.0", "160.0"),
            ("Private title", "unknown"),
            ("1" * 200, "unknown"),
        ):
            with self.subTest(
                version=version
            ), tempfile.TemporaryDirectory() as directory, patch.object(
                probe, "STATE", Path(directory) / "state"
            ), patch.object(
                probe.firefox, "closed"
            ):
                probe.STATE.mkdir()
                (probe.STATE / "values.json").write_text("{}")
                (probe.STATE / "source.vault").write_bytes(b"synthetic encrypted input")
                (probe.STATE / "startup-control.json").write_text(
                    json.dumps(
                        {
                            "default_port_occupied": True,
                            "selected_default_port": False,
                            "firefox_version": version,
                            "private_extra": "SYNTHETIC-PRIVATE",
                        }
                    )
                )
                evidence = Path(directory) / "evidence"
                inventory = evidence / "inventory/bookmarks/preview"
                inventory.mkdir(parents=True)
                (evidence / "reads").mkdir()
                probe.before("preview", inventory)
                record = evidence / "reads/bookmarks-startup-control.json"
                self.assertEqual(
                    json.loads(record.read_text()),
                    {
                        "default_port_occupied": True,
                        "selected_default_port": False,
                        "firefox_version": expected,
                    },
                )
                self.assertEqual(record.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("SYNTHETIC-PRIVATE", record.read_text())

    def test_invalid_setup_control_has_a_fixed_requirement(self):
        for control in (None, ["SYNTHETIC-PRIVATE"], 1):
            with self.subTest(
                control=control
            ), tempfile.TemporaryDirectory() as directory, patch.object(
                probe, "STATE", Path(directory)
            ), patch.object(
                probe.firefox, "closed"
            ):
                (probe.STATE / "values.json").write_text("{}")
                (probe.STATE / "startup-control.json").write_text(json.dumps(control))
                with self.assertRaises(probe.JourneyCheckError) as raised:
                    probe.before(
                        "preview",
                        Path(directory) / "evidence/inventory/bookmarks/preview",
                    )
                self.assertEqual(
                    str(raised.exception),
                    "The setup startup control is absent or incomplete.",
                )
                self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))

    def test_command_argument_error_never_echoes_the_supplied_private_value(self):
        output = io.StringIO()
        with patch.object(
            sys, "argv", ["bookmarks.py", "SYNTHETIC-PRIVATE"]
        ), redirect_stderr(output):
            self.assertEqual(probe.cli(), 1)
        self.assertIn("Command arguments", output.getvalue())
        self.assertNotIn("SYNTHETIC-PRIVATE", output.getvalue())

    def test_requirement_messages_are_fixed_source_text(self):
        tree = ast.parse((ROOT / "verify/helpers/bookmarks.py").read_text())
        messages = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "require":
                    messages.append(node.args[1])
                elif node.func.id == "JourneyCheckError" and node.args:
                    if (
                        isinstance(node.args[0], ast.Name)
                        and node.args[0].id == "message"
                    ):
                        continue
                    messages.append(node.args[0])
        self.assertGreater(len(messages), 15)
        self.assertTrue(
            all(
                isinstance(message, ast.Constant) and isinstance(message.value, str)
                for message in messages
            )
        )

    def test_private_fixture_is_encrypted_and_never_writes_html(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory)
        ):
            probe.make_fixture()
            source = Path(directory) / "source.vault"
            self.assertTrue(source.read_bytes().startswith(b"$ANSIBLE_VAULT;"))
            values = probe.private_values()
            self.assertEqual(len(values["bookmarks"]), 3)
            for item in values["bookmarks"]:
                self.assertNotIn(item["url"].encode(), source.read_bytes())
            self.assertEqual(source.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                (Path(directory) / "password").stat().st_mode & 0o777, 0o600
            )
            self.assertFalse(list(Path(directory).rglob("*.html")))

    def test_partial_interruption_seeds_only_one_stable_guid_without_completion(self):
        values = {
            "bookmarks": [
                {"title": "one", "url": "https://example.invalid/1"},
                {"title": "two", "url": "https://example.invalid/2"},
            ]
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe.importer, "FirefoxSession"
        ) as session:
            root = Path(directory)
            session.return_value.__enter__.return_value.import_tree.return_value = 1
            session.return_value.__enter__.return_value.profile_key = "a" * 64
            probe.seed_partial(root, values)
            tree = (
                session.return_value.__enter__.return_value.import_tree.call_args.args[
                    0
                ]
            )
            full = probe.importer.parse_html(probe.fixture_html(values["bookmarks"]))

            def identities(nodes):
                result = []
                for node in nodes:
                    result.append((node["type"], node["guid"]))
                    if node["type"] == 2:
                        result.extend(identities(node["children"]))
                return result

            self.assertEqual(
                identities(tree["toolbar"]), identities(full["toolbar"])[:3]
            )
            self.assertEqual(
                sum(kind == 1 for kind, _ in identities(tree["toolbar"])), 1
            )
            self.assertEqual(tree["menu"], [])
            self.assertEqual(tree["unfiled"], [])
            self.assertFalse(probe.importer.completion_path(root, "a" * 64).exists())

    def test_scan_fails_for_planted_title_and_url_without_exposing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {
                "bookmarks": [
                    {
                        "title": "SYNTHETIC-PRIVATE-TITLE",
                        "url": "https://example.invalid/private",
                    }
                ]
            }
            values["_password"] = "SYNTHETIC-VAULT-PASSWORD"
            for value in [*values["bookmarks"][0].values(), values["_password"]]:
                (root / "run.txt").write_text(value)
                with self.assertRaises(probe.firefox.ProofError) as error:
                    probe.scan_private(values, [root])
                self.assertNotIn(value, str(error.exception))
            (root / "run.txt").write_text("counts only")
            self.assertEqual(probe.scan_private(values, [root]), 0)

    def test_probe_checks_tree_preservation_counts_and_active_import_blocking(self):
        values = {
            "bookmarks": [{"title": "made-up", "url": "https://example.invalid/a"}],
            "existing": {"title": "old", "url": "https://example.invalid/old"},
            "extra": {"title": "later", "url": "https://example.invalid/later"},
        }
        client = Mock()
        client.script.return_value = {
            "blocked": True,
            "counts": [1],
            "existing": 1,
            "titles": True,
            "extra": 0,
            "structure": True,
            "preserved": True,
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory)
        ), patch.object(probe.importer, "FirefoxSession") as browser:
            probe.write_private(
                Path(directory) / "existing-tree.json", b'[{"guid":"artificial"}]'
            )
            browser.return_value.__enter__.return_value = client
            self.assertEqual(probe.read_counts(Path("/unused"), values, True), 1)
            for bad in (
                {"blocked": False},
                {"counts": [2]},
                {"existing": 0},
                {"titles": False},
                {"extra": 1},
                {"structure": False},
                {"preserved": False},
            ):
                client.script.return_value = (
                    dict(
                        blocked=True,
                        counts=[1],
                        existing=1,
                        titles=True,
                        extra=0,
                        structure=True,
                        preserved=True,
                    )
                    | bad
                )
                with self.assertRaises(probe.firefox.ProofError):
                    probe.read_counts(Path("/unused"), values, True)

    def test_completed_run_ignores_firefox_housekeeping_and_preserves_completion(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory) / "state"
        ), patch.object(probe.firefox, "closed"), patch.object(
            probe.importer, "FirefoxSession"
        ) as factory, patch.object(
            probe, "read_counts", return_value=3
        ), patch.object(
            probe, "temporary_directories", return_value=[]
        ), patch.object(
            probe, "scan_private", return_value=0
        ):
            probe.STATE.mkdir()
            root = probe.target("again")
            root.mkdir()
            state = probe.importer.state_root(root)
            state.mkdir()
            record = probe.importer.completion_path(root, "a" * 64)
            record.write_text('{"complete": true}')
            factory.return_value.__enter__.return_value.profile_key = "a" * 64
            probe.write_private(probe.STATE / "values.json", b'{"bookmarks": []}')
            probe.write_private(probe.STATE / "temporary-before.json", b"[]")
            probe.write_private(
                probe.STATE / "before.json",
                json.dumps(probe.tree_fingerprint(state)).encode(),
            )
            (root / "firefox-housekeeping").write_text("changed by native Firefox")
            inventory = Path(directory) / "evidence/inventory/example"
            probe.after("again", inventory)
            record.write_text('{"complete": false}')
            with self.assertRaises(probe.JourneyCheckError):
                probe.after("again", inventory)

    def test_cleanup_removes_only_its_decrypted_test_file_after_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory) / "state"
        ), patch.object(probe.firefox, "restore", return_value=True), patch.object(
            probe.firefox, "closed"
        ), patch.object(
            probe, "scan_private", side_effect=probe.firefox.ProofError("test input")
        ):
            probe.STATE.mkdir()
            values = {
                "bookmarks": [{"title": "one", "url": "https://example.invalid/1"}],
                "extra": {"title": "two", "url": "https://example.invalid/2"},
            }
            probe.write_private(
                probe.STATE / "values.json", json.dumps(values).encode()
            )
            probe.write_private(probe.STATE / "temporary-before.json", b"[]")
            decrypted = Path(directory) / "mac-setup-bookmarks-artificial"
            decrypted.mkdir()
            probe.write_private(
                decrypted / "bookmarks.html",
                probe.fixture_html(values["bookmarks"]).encode(),
            )
            with patch.object(
                probe, "temporary_directories", return_value=[str(decrypted)]
            ), self.assertRaises(probe.firefox.ProofError):
                probe.cleanup()
            self.assertFalse(decrypted.exists())
            self.assertFalse(probe.STATE.exists())

    def test_recovered_plaintext_still_fails_the_import_cleanup_proof(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory) / "state"
        ), patch.object(probe.firefox, "closed"), patch.object(
            probe.firefox, "restore", return_value=True
        ), patch.object(
            probe, "scan_private", return_value=0
        ):
            probe.STATE.mkdir()
            values = {
                "bookmarks": [{"title": "one", "url": "https://example.invalid/1"}],
                "extra": {"title": "two", "url": "https://example.invalid/2"},
            }
            probe.write_private(
                probe.STATE / "values.json", json.dumps(values).encode()
            )
            probe.write_private(probe.STATE / "temporary-before.json", b"[]")
            decrypted = Path(directory) / "mac-setup-bookmarks-artificial"
            decrypted.mkdir()
            probe.write_private(
                decrypted / "bookmarks.html", probe.invalid_html(values).encode()
            )
            with patch.object(
                probe,
                "temporary_directories",
                side_effect=[[str(decrypted)], [str(decrypted)], []],
            ):
                with self.assertRaises(probe.firefox.ProofError) as raised:
                    probe.cleanup()
            self.assertIn(
                "Import failure left a decrypted test file", str(raised.exception)
            )
            self.assertFalse(decrypted.exists())
            self.assertFalse(probe.STATE.exists())

    def test_refusal_checks_native_rows_and_external_completion_only_for_existing_target(
        self,
    ):
        values = {
            "bookmarks": [{"title": "one", "url": "https://example.invalid/1"}],
            "existing": {"title": "old", "url": "https://example.invalid/old"},
            "extra": {"title": "later", "url": "https://example.invalid/later"},
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe.importer, "FirefoxSession"
        ) as factory:
            root = Path(directory) / "target"
            probe.check_refusal(root, values)
            factory.assert_not_called()
            self.assertFalse(root.exists())
            root.mkdir()
            session = factory.return_value.__enter__.return_value
            session.profile_key = "a" * 64
            session.script.return_value = True
            probe.check_refusal(root, values)
            session.script.return_value = False
            with self.assertRaises(probe.JourneyCheckError):
                probe.check_refusal(root, values)
            session.script.return_value = True
            record = probe.importer.completion_path(root, session.profile_key)
            record.parent.mkdir()
            record.write_text("{}")
            with self.assertRaises(probe.JourneyCheckError):
                probe.check_refusal(root, values)

    def test_early_failure_cleanup_never_creates_an_unused_refusal_target(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory) / "state"
        ), patch.object(probe.firefox, "closed"), patch.object(
            probe.firefox, "restore", return_value=True
        ), patch.object(
            probe, "scan_private", return_value=0
        ), patch.object(
            probe.importer, "FirefoxSession"
        ) as factory:
            probe.STATE.mkdir()
            probe.write_private(probe.STATE / "values.json", b'{"bookmarks": []}')
            probe.cleanup()
            factory.assert_not_called()
            self.assertFalse(probe.STATE.exists())

    def test_unknown_temporary_directory_preserves_private_state_for_recovery(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory) / "state"
        ), patch.object(probe.firefox, "restore", return_value=True), patch.object(
            probe.firefox, "closed"
        ):
            probe.STATE.mkdir()
            values = {
                "bookmarks": [],
                "extra": {"title": "two", "url": "https://example.invalid/2"},
            }
            probe.write_private(
                probe.STATE / "values.json", json.dumps(values).encode()
            )
            probe.write_private(probe.STATE / "temporary-before.json", b"[]")
            unknown = Path(directory) / "mac-setup-bookmarks-unknown"
            unknown.mkdir()
            (unknown / "unrelated").write_text("untouched")
            with patch.object(
                probe, "temporary_directories", return_value=[str(unknown)]
            ), self.assertRaises(probe.firefox.ProofError):
                probe.cleanup()
            self.assertEqual((unknown / "unrelated").read_text(), "untouched")
            self.assertTrue(probe.STATE.exists())

    def test_policy_restore_failure_keeps_snapshot_after_removing_plaintext(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory) / "state"
        ), patch.object(
            probe.firefox, "restore", side_effect=probe.firefox.ProofError("test input")
        ), patch.object(
            probe.firefox, "closed"
        ):
            probe.STATE.mkdir()
            values = {
                "bookmarks": [{"title": "one", "url": "https://example.invalid/1"}],
                "extra": {"title": "two", "url": "https://example.invalid/2"},
            }
            probe.write_private(
                probe.STATE / "values.json", json.dumps(values).encode()
            )
            probe.write_private(probe.STATE / "temporary-before.json", b"[]")
            saved = probe.STATE / "policy"
            saved.mkdir()
            probe.write_private(saved / "original.json", b"{}")
            decrypted = Path(directory) / "mac-setup-bookmarks-artificial"
            decrypted.mkdir()
            probe.write_private(
                decrypted / "bookmarks.html",
                probe.fixture_html(values["bookmarks"]).encode(),
            )
            with patch.object(
                probe, "temporary_directories", return_value=[str(decrypted)]
            ), self.assertRaises(probe.firefox.ProofError):
                probe.cleanup()
            self.assertFalse(decrypted.exists())
            self.assertTrue((saved / "original.json").is_file())

    def test_cleanup_removes_private_state_after_restoration_and_scanning(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory) / "state"
        ), patch.object(probe, "scan_private", return_value=0), patch.object(
            probe.firefox, "restore", return_value=True
        ), patch.object(
            probe.firefox, "closed"
        ):
            probe.STATE.mkdir()
            probe.write_private(
                probe.STATE / "values.json", json.dumps({"bookmarks": []}).encode()
            )
            probe.write_private(probe.STATE / "password", b"made-up")
            probe.cleanup()
            self.assertFalse(probe.STATE.exists())


if __name__ == "__main__":
    unittest.main()
