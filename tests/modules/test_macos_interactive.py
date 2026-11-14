import io
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/profile/lookup_plugins"))

from macos_interactive import LookupModule

SPEC = importlib.util.spec_from_file_location(
    "controller_interactive_callback", ROOT / "plugins/callback/macos_interactive.py"
)
CALLBACK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CALLBACK)


def capture_controller():
    CALLBACK.CallbackModule().v2_playbook_on_start(None)
    return LookupModule().run([])[0]


class ControllerTerminal(unittest.TestCase):
    def read(self, source, output):
        with patch("sys.stdin", source), patch("sys.stdout", output):
            with patch.dict(os.environ, {}, clear=True):
                return capture_controller()

    def test_both_terminal_streams_allow_interaction(self):
        self.assertIs(
            self.read(Mock(isatty=lambda: True), Mock(isatty=lambda: True)), True
        )

    def test_either_redirected_stream_disallows_interaction(self):
        with open(os.devnull) as source:
            self.assertIs(self.read(source, Mock(isatty=lambda: True)), False)
        self.assertIs(self.read(Mock(isatty=lambda: True), io.StringIO()), False)

    def test_missing_or_broken_streams_disallow_interaction(self):
        broken = (
            None,
            object(),
            Mock(isatty=Mock(side_effect=OSError)),
            Mock(isatty=Mock(side_effect=ValueError)),
        )
        for stream in broken:
            with self.subTest(stream=stream):
                self.assertIs(self.read(stream, Mock(isatty=lambda: True)), False)
                self.assertIs(self.read(Mock(isatty=lambda: True), stream), False)

    def test_missing_or_unrecognized_capture_disallows_interaction(self):
        for marker in (None, "", "true", "0", "unexpected"):
            environment = (
                {} if marker is None else {"MAC_SETUP_CONTROLLER_INTERACTIVE": marker}
            )
            with self.subTest(marker=marker), patch.dict(
                os.environ, environment, clear=True
            ):
                self.assertIs(LookupModule().run([])[0], False)

    def test_unattended_capture_overwrites_inherited_interactive_marker(self):
        with patch.dict(os.environ, {"MAC_SETUP_CONTROLLER_INTERACTIVE": "1"}), patch(
            "sys.stdin", io.StringIO()
        ), patch("sys.stdout", Mock(isatty=lambda: True)):
            self.assertIs(capture_controller(), False)
            self.assertEqual(os.environ["MAC_SETUP_CONTROLLER_INTERACTIVE"], "0")


if __name__ == "__main__":
    unittest.main()
