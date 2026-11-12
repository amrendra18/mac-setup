import os
import pty
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/vault-password"
SECRET = "zz-test-vault-password\n"


class VaultPassword(unittest.TestCase):
    def setUp(self):
        dot_home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dot_home)
        folder = dot_home / "mac-setup"
        folder.mkdir(mode=0o700)
        password_file = folder / "vault-password"
        password_file.write_text(SECRET)
        password_file.chmod(0o600)
        self.env = {**os.environ, "DOT_HOME": str(dot_home)}

    def run_script(self, *args, stdout=subprocess.PIPE):
        return subprocess.run(
            [str(SCRIPT), *args],
            env=self.env,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )

    def test_help_prints_one_usage_line(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                out = self.run_script(flag)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertTrue(out.stdout.startswith("usage: scripts/vault-password "))
                self.assertEqual(out.stdout.count("\n"), 1)
                self.assertNotIn(SECRET.strip(), out.stdout)

    def test_bad_argument_exits_2(self):
        out = self.run_script("--vault-id")
        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout, "")
        self.assertTrue(out.stderr.startswith("usage: scripts/vault-password "))

    def test_pipe_gets_the_password(self):
        out = self.run_script()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, SECRET)

    def test_terminal_is_refused(self):
        main, child = pty.openpty()
        self.addCleanup(os.close, main)
        self.addCleanup(os.close, child)
        out = self.run_script(stdout=child)
        os.set_blocking(main, False)
        try:
            shown = os.read(main, 1024)
        except BlockingIOError:
            shown = b""
        self.assertEqual(out.returncode, 2)
        self.assertEqual(
            out.stderr,
            "vault-password: Ansible runs this script; it prints the vault password, so it refuses a terminal\n",
        )
        self.assertEqual(shown, b"")


if __name__ == "__main__":
    unittest.main()
