import json
import os
import plistlib
import re
import subprocess
import tempfile
import unittest
from importlib.metadata import version
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".local/venv/bin"
ENV = {
    **os.environ,
    "PATH": f"{BIN}:{os.environ['PATH']}",
    "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"),
    "ANSIBLE_HOME": str(ROOT / ".local/ansible-home"),
    "ANSIBLE_LIBRARY": os.pathsep.join(
        str(p) for p in sorted((ROOT / "roles").glob("*/library"))
    ),
    "ANSIBLE_MODULE_UTILS": str(ROOT / "roles/macos_apps/module_utils"),
    "ANSIBLE_NOCOLOR": "1",
}


class ModuleArguments(unittest.TestCase):
    def test_lint_version_matches_the_pin(self):
        pin = re.search(
            r"^ansible-lint==([^ ]+)", (ROOT / "requirements/dev.txt").read_text(), re.M
        )
        self.assertIsNotNone(pin)
        self.assertEqual(version("ansible-lint"), pin[1])

    def lint(self, *paths, arguments=()):
        done = subprocess.run(
            [
                str(BIN / "ansible-lint"),
                "--offline",
                "--strict",
                "--nocolor",
                *arguments,
                *map(str, paths),
            ],
            cwd=ROOT,
            env=ENV,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertNotIn("Ignored exception", done.stdout + done.stderr)
        return done

    def test_real_roles_receive_every_lint_check(self):
        done = self.lint("playbooks", "roles")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_unknown_arguments_are_refused_for_all_five_calls(self):
        names = {
            "macos_defaults",
            "macos_accounts",
            "macos_finder_sidebar",
            "macos_commands",
            "macos_apps",
        }
        paths = [ROOT / "tests/lint/tasks" / f"{name}.yml" for name in sorted(names)]
        done = self.lint(
            *paths, arguments=("--format", "json", "--tags", "args[module]")
        )
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        findings = json.loads(done.stdout)
        refused = {
            Path(finding["location"]["path"]).stem
            for finding in findings
            if finding["check_name"] == "args[module]"
            and "unexpected_argument_for_lint" in finding["description"]
        }
        self.assertEqual(refused, names, done.stdout + done.stderr)

    def test_app_runtime_is_packaged_and_reads_a_current_scratch_app(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as tmp:
            folder = Path(tmp)
            contents = folder / "Applications/Example.app/Contents"
            contents.mkdir(parents=True)
            (contents / "Info.plist").write_bytes(
                plistlib.dumps(
                    {
                        "CFBundleIdentifier": "com.example.app",
                        "CFBundleShortVersionString": "1.2",
                    }
                )
            )
            playbook = folder / "playbook.yml"
            playbook.write_text(
                yaml.safe_dump(
                    [
                        {
                            "name": "Check app module packaging",
                            "hosts": "localhost",
                            "connection": "local",
                            "gather_facts": False,
                            "tasks": [
                                {
                                    "name": "Read the scratch app",
                                    "macos_app": {
                                        "id": "example",
                                        "downloads": str(folder / "downloads"),
                                        "applications": str(folder / "Applications"),
                                        "app": {
                                            "url": "https://dl.example.com/app.dmg",
                                            "host": "dl.example.com",
                                            "sha256": "a" * 64,
                                            "published": "2026-10-01",
                                            "team_id": "ABCDE12345",
                                            "bundle_id": "com.example.app",
                                            "container": "dmg",
                                            "app": "Example.app",
                                            "version": "1.2",
                                        },
                                    },
                                }
                            ],
                        }
                    ]
                )
            )
            done = subprocess.run(
                [
                    str(BIN / "ansible-playbook"),
                    "--check",
                    "-i",
                    "localhost,",
                    str(playbook),
                ],
                cwd=ROOT,
                env=ENV,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertRegex(done.stdout, r"changed=0\s+unreachable=0\s+failed=0")
            self.assertFalse((folder / "downloads").exists())


if __name__ == "__main__":
    unittest.main()
