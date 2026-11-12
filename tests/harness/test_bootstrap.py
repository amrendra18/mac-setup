import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class BootstrapUsage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "checkout/scripts").mkdir(parents=True)
        self.script = self.tmp / "checkout/scripts/bootstrap"
        shutil.copy2(ROOT / "scripts/bootstrap", self.script)
        fake_bin = self.tmp / "bin"
        fake_bin.mkdir()
        self.calls = self.tmp / "calls.log"
        for tool in ("curl", "tar", "uname"):
            fake = fake_bin / tool
            fake.write_text(f'#!/bin/sh\necho {tool} "$@" >>"{self.calls}"\nexit 1\n')
            fake.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{fake_bin}:/usr/bin:/bin"}

    def run_bootstrap(self, *args):
        return subprocess.run(
            [str(self.script), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def assert_nothing_ran(self):
        self.assertFalse(self.calls.exists(), "bootstrap called a tool")
        self.assertFalse(
            (self.tmp / "checkout/.local").exists(), "bootstrap wrote .local/"
        )

    def test_help_prints_usage_and_downloads_nothing(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                out = self.run_bootstrap(flag)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertTrue(out.stdout.startswith("usage: scripts/bootstrap\n"))
                self.assertIn("exit codes:", out.stdout)
                self.assertEqual(out.stderr, "")
                self.assert_nothing_ran()

    def test_bad_argument_prints_usage_to_stderr_and_exits_2(self):
        for args in (["--check"], ["--help", "extra"], [""]):
            with self.subTest(args=args):
                out = self.run_bootstrap(*args)
                self.assertEqual(out.returncode, 2)
                self.assertEqual(out.stdout, "")
                self.assertTrue(out.stderr.startswith("usage: scripts/bootstrap\n"))
                self.assert_nothing_ran()


if __name__ == "__main__":
    unittest.main()
