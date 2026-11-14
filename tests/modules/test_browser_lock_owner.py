import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "browser_lock_reader", ROOT / "module_utils/browser_settings.py"
)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


@unittest.skipUnless(sys.platform == "darwin", "Darwin lock layout")
class BrowserLockOwnerTests(unittest.TestCase):
    def test_read_only_query_reports_owner_and_preserves_fixture_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".parentlock"
            path.write_bytes(b"artificial lock test input")
            before = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
            child = (
                "import fcntl, sys\n"
                "with open(sys.argv[1], 'r+b') as stream:\n"
                "    fcntl.lockf(stream, fcntl.LOCK_EX)\n"
                "    print('ready', flush=True)\n"
                "    sys.stdin.readline()\n"
            )
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", child, str(path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(process.stdout.readline(), "ready\n")
                original = reader.os.open
                with patch.object(reader.os, "open", wraps=original) as opened:
                    self.assertEqual(reader._fcntl_lock_owner(path), process.pid)
                    self.assertEqual(reader._fcntl_lock_owner(path), process.pid)
                for call in opened.call_args_list:
                    self.assertEqual(call.args[1], os.O_RDONLY | os.O_NOFOLLOW)
                self.assertEqual(
                    (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino),
                    before,
                )
            finally:
                process.stdin.close()
                process.wait(timeout=10)
                process.stdout.close()
                process.stderr.close()
            self.assertEqual(process.returncode, 0)
            self.assertIsNone(reader._fcntl_lock_owner(path))
            self.assertEqual(
                (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino),
                before,
            )


if __name__ == "__main__":
    unittest.main()
