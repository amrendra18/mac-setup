import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted(
    ROOT / name
    for name in subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "scripts"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
)
INSTALL_TOOLS = ("curl", "tar", "uname")


def tree(folder):
    found = {}
    for top, folders, files in os.walk(folder):
        folders[:] = [
            name
            for name in folders
            if name not in (".git", "__pycache__")
            and not (top == str(folder) and name == ".local")
        ]
        for name in folders:
            found[os.path.join(top, name)] = "folder"
        for name in files:
            stat = os.lstat(os.path.join(top, name))
            found[os.path.join(top, name)] = (stat.st_mtime_ns, stat.st_size)
    return found


class ScriptHelp(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch)
        fake_bin = self.scratch / "bin"
        fake_bin.mkdir()
        self.calls = self.scratch / "calls.log"
        for tool in INSTALL_TOOLS:
            fake = fake_bin / tool
            fake.write_text(f'#!/bin/sh\necho {tool} "$@" >>"{self.calls}"\nexit 1\n')
            fake.chmod(0o755)
        for name in ("home", "dot", "tmp"):
            (self.scratch / name).mkdir()
        self.env = {
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(self.scratch / "home"),
            "DOT_HOME": str(self.scratch / "dot"),
            "TMPDIR": str(self.scratch / "tmp"),
            **{
                name: "http://127.0.0.1:9"
                for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")
            },
        }

    def test_every_script_answers_help_and_writes_nothing(self):
        self.assertGreater(len(SCRIPTS), 0)
        for script in SCRIPTS:
            with self.subTest(script.name):
                before = tree(ROOT)
                out = subprocess.run(
                    [str(script), "--help"],
                    cwd=self.scratch,
                    env=self.env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                after = tree(ROOT)
                self.assertEqual(out.returncode, 0, out.stderr)
                lines = out.stdout.splitlines() or [""]
                self.assertRegex(
                    lines[0], rf"^usage: scripts/{re.escape(script.name)}( |$)"
                )
                self.assertTrue(
                    any(line.startswith("exit codes:") for line in lines),
                    f"no line starts with 'exit codes:'\n{out.stdout}",
                )
                changed = sorted(
                    path
                    for path in before.keys() | after.keys()
                    if before.get(path) != after.get(path)
                )
                self.assertEqual(changed, [], "--help wrote in the checkout")
                ran = self.calls.read_text() if self.calls.exists() else ""
                self.calls.unlink(missing_ok=True)
                self.assertEqual(ran, "", "--help ran an install tool")


if __name__ == "__main__":
    unittest.main()
