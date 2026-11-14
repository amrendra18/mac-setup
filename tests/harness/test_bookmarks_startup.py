import errno
from contextlib import contextmanager, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "bookmarks_startup_probe", ROOT / "verify/helpers/bookmarks.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class BookmarksStartupControl(unittest.TestCase):
    def setup_failure_output(self, error, phase):
        class Session:
            version = "160.0"

            @property
            def port(self):
                if phase == "body":
                    raise error
                return 40123

        @contextmanager
        def browser(root):
            self.assertEqual(root, probe.STATE / "startup-control")
            if phase == "entry":
                raise error
            try:
                yield Session()
            finally:
                if phase == "exit":
                    raise error

        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory) / "state"
        ), patch.dict(
            probe.os.environ,
            {"ANSIBLE_VAULT_PASSWORD_FILE": str(Path(directory) / "state/password")},
        ), patch.object(
            probe.firefox, "closed"
        ), patch.object(
            probe, "seed_policy"
        ), patch.object(
            probe, "make_fixture"
        ), patch.object(
            probe.socket, "socket", return_value=Mock()
        ) as socket_factory, patch.object(
            probe.importer, "FirefoxSession", side_effect=browser
        ), patch.object(
            probe, "main", side_effect=probe.setup
        ):
            output = io.StringIO()
            with redirect_stderr(output):
                self.assertEqual(probe.cli(), 1)
            socket_factory.return_value.close.assert_called_once()
            self.assertFalse((probe.STATE / "startup-control.json").exists())
            return output.getvalue()

    def test_setup_names_startup_control_errors_from_session_entry_body_and_exit(self):
        for phase in ("entry", "body", "exit"):
            with self.subTest(phase=phase):
                output = self.setup_failure_output(
                    probe.importer.BookmarkError("SYNTHETIC-PRIVATE"), phase
                )
                self.assertIn("Default-port startup control", output)
                self.assertNotIn("Firefox import operation", output)
                self.assertNotIn("SYNTHETIC-PRIVATE", output)

    def test_setup_preserves_specific_startup_and_permission_guidance(self):
        cases = (
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
        )
        for error, message in cases:
            for phase in ("entry", "body", "exit"):
                with self.subTest(error=type(error).__name__, phase=phase):
                    output = self.setup_failure_output(error, phase)
                    self.assertIn(message, output)
                    self.assertNotIn("SYNTHETIC-PRIVATE", output)

    def test_cleanup_preserves_system_access_check_from_entry_body_and_exit(self):
        for phase in ("entry", "body", "exit"):
            with self.subTest(phase=phase):
                error = probe.importer.StartupError(
                    "SYNTHETIC-PRIVATE", check="system_access"
                )

                class Session:
                    profile_key = "a" * 64
                    version = "160.0"

                    def script(self, *args, **kwargs):
                        if phase == "body":
                            raise error
                        return True

                @contextmanager
                def browser(root):
                    if phase == "entry":
                        raise error
                    try:
                        yield Session()
                    finally:
                        if phase == "exit":
                            raise error

                with tempfile.TemporaryDirectory() as directory, patch.object(
                    probe, "STATE", Path(directory) / "state"
                ), patch.object(probe.firefox, "closed"), patch.object(
                    probe.firefox, "restore"
                ) as restore, patch.object(
                    probe.importer, "FirefoxSession", side_effect=browser
                ), patch.object(
                    probe, "main", side_effect=probe.cleanup
                ):
                    probe.target("refusal").mkdir(parents=True)
                    (probe.STATE / "values.json").write_text(
                        json.dumps({"bookmarks": [], "existing": {}, "extra": {}})
                    )
                    output = io.StringIO()
                    with redirect_stderr(output):
                        self.assertEqual(probe.cli(), 1)
                    restore.assert_called_once()
                    self.assertIn(
                        probe.importer.SYSTEM_ACCESS_MESSAGE, output.getvalue()
                    )
                    self.assertNotIn("SYNTHETIC-PRIVATE", output.getvalue())
                    self.assertFalse(probe.STATE.exists())

    def run_control(self, port=40123, bind_error=None, version="160.0"):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory)
        ):
            listener = Mock()
            if bind_error:
                listener.bind.side_effect = bind_error
            session = Mock(port=port, version=version)
            factory = MagicMock()
            factory.return_value.__enter__.return_value = session

            def started(root):
                listener.bind.assert_called_once_with(("127.0.0.1", 2828))
                self.assertFalse(listener.close.called)
                return factory.return_value

            factory.side_effect = started
            with patch.object(
                probe.socket, "socket", return_value=listener
            ), patch.object(probe.importer, "FirefoxSession", factory):
                try:
                    probe.prove_startup_port()
                finally:
                    listener.close.assert_called_once()
            record = json.loads((probe.STATE / "startup-control.json").read_text())
            return listener, factory, record

    def test_startup_evidence_records_the_observed_session_version(self):
        for version in ("158.0.1", "159.0", "160.0", "171.2.3", "unknown"):
            with self.subTest(version=version):
                _, _, record = self.run_control(version=version)
                self.assertEqual(
                    record,
                    {
                        "default_port_occupied": True,
                        "selected_default_port": False,
                        "firefox_version": version,
                    },
                )

    def test_startup_evidence_replaces_unsafe_version_values_with_unknown(self):
        for version in (
            None,
            Mock(),
            "/private/synthetic/160.0",
            "Private title",
            "160.0\n",
        ):
            with self.subTest(version_type=type(version).__name__):
                _, _, record = self.run_control(version=version)
                self.assertEqual(record["firefox_version"], "unknown")
                self.assertEqual(
                    set(record),
                    {
                        "default_port_occupied",
                        "selected_default_port",
                        "firefox_version",
                    },
                )

    def test_setup_preserves_system_access_check_from_entry_body_and_exit(self):
        for phase in ("entry", "body", "exit"):
            with self.subTest(phase=phase):
                output = self.setup_failure_output(
                    probe.importer.StartupError(
                        "SYNTHETIC-PRIVATE", check="system_access"
                    ),
                    phase,
                )
                self.assertIn(probe.importer.SYSTEM_ACCESS_MESSAGE, output)
                self.assertNotIn("SYNTHETIC-PRIVATE", output)

    def test_default_port_is_occupied_until_random_port_startup_finishes(self):
        listener, factory, _ = self.run_control()
        listener.listen.assert_called_once()
        factory.assert_called_once()

    def test_default_port_selection_is_rejected(self):
        with self.assertRaises(probe.firefox.ProofError):
            self.run_control(port=2828)

    def test_an_existing_service_can_occupy_the_default_port(self):
        listener, factory, _ = self.run_control(
            bind_error=OSError(errno.EADDRINUSE, "synthetic busy port")
        )
        listener.listen.assert_not_called()
        factory.assert_called_once()

    def test_socket_creation_failure_names_the_failed_control(self):
        with patch.object(
            probe.socket, "socket", side_effect=OSError("SYNTHETIC-PRIVATE")
        ):
            with self.assertRaises(probe.JourneyCheckError) as raised:
                probe.prove_startup_port()
        self.assertIn("Default-port startup control", str(raised.exception))
        self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))

    def test_unavailable_socket_permission_names_the_failed_control(self):
        with self.assertRaises(probe.firefox.ProofError) as raised:
            self.run_control(bind_error=OSError(errno.EACCES, "SYNTHETIC-PRIVATE"))
        self.assertIn("Default-port startup control", str(raised.exception))
        self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
