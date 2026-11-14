import importlib.util
import errno
import hashlib
import os
import json
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "firefox_bookmarks_test", ROOT / "module_utils/firefox_bookmarks.py"
)
bookmarks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bookmarks)

HTML = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<DL><p><DT><H3 PERSONAL_TOOLBAR_FOLDER="true">Toolbar</H3><DL><p>
<DT><H3>Made-up folder</H3><DL><p>
<DT><A HREF="https://example.invalid/one">SYNTHETIC-PRIVATE-TITLE</A>
<DT><A HREF="https://example.invalid/two">Second</A>
</DL><p></DL><p><DT><A HREF="https://example.invalid/menu">Menu</A></DL><p>
"""


class FakeSession:
    def __init__(self, key="a" * 64):
        self.profile_key = key
        self.identity_created = False
        self.imported = 0
        self.calls = 0
        self.crash = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.crash:
            raise bookmarks.BookmarkError("Synthetic browser crash")
        return False

    def import_tree(self, tree):
        self.calls += 1
        count = 3 - self.imported
        self.imported = 3
        return count


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "isolated"
        self.source = self.base / "bookmarks.html"
        self.source.write_text(HTML)
        self.source.chmod(0o600)
        binary = self.base / "firefox-bin"
        binary.touch()
        self.session = FakeSession()
        for replacement in (
            patch.object(Path, "home", return_value=self.base / "account"),
            patch.object(bookmarks, "FIREFOX", str(binary)),
            patch.object(bookmarks, "check_closed"),
            patch.object(bookmarks, "FirefoxSession", return_value=self.session),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_parse_keeps_toolbar_folders_and_menu(self):
        tree = bookmarks.parse_html(HTML)
        self.assertEqual(tree["toolbar"][0]["title"], "Made-up folder")
        self.assertEqual(len(tree["toolbar"][0]["children"]), 2)
        self.assertEqual(tree["menu"][0]["url"], "https://example.invalid/menu")
        self.assertEqual(tree["unfiled"], [])
        self.assertEqual(len(tree["toolbar"][0]["guid"]), 12)
        self.assertEqual(tree, bookmarks.parse_html(HTML))

    def test_public_sample_is_three_ordered_toolbar_links(self):
        source = (ROOT / "inventory/files/firefox-bookmarks.html").read_text()
        self.assertIn("Test input bookmarks", source)
        tree = bookmarks.parse_html(source)
        self.assertEqual(
            [len(folder["children"]) for folder in tree["toolbar"]], [2, 1]
        )
        self.assertEqual(tree["menu"], [])

    def test_bad_input_errors_contain_no_private_values(self):
        for source in ("SYNTHETIC-PRIVATE-TITLE", HTML.replace("https://", "bad ")):
            with self.assertRaises(bookmarks.BookmarkError) as raised:
                bookmarks.parse_html(source)
            self.assertNotIn("SYNTHETIC-PRIVATE-TITLE", str(raised.exception))
            self.assertNotIn("example.invalid", str(raised.exception))

    def test_check_does_not_read_source_launch_firefox_or_create_state(self):
        self.source.unlink()
        with patch.object(
            bookmarks, "FirefoxSession", side_effect=AssertionError("Browser launched")
        ), patch.object(Path, "home", side_effect=AssertionError("Home read")):
            self.assertEqual(
                bookmarks.inspect_bookmarks(check=True)["state"], "would_import"
            )
            self.assertEqual(
                bookmarks.import_bookmarks(self.source, check=True)["state"],
                "would_import",
            )
        self.assertFalse(self.root.exists())

    def test_isolated_root_is_private_before_state_lock_and_native_session(self):
        def native_boundary(root):
            self.assertEqual(root, self.root)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            return self.session

        with patch.object(bookmarks, "FirefoxSession", side_effect=native_boundary):
            bookmarks.inspect_bookmarks(self.root)
        self.assertEqual((self.root / "mac-setup-state").stat().st_mode & 0o777, 0o700)

    def test_existing_open_isolated_root_is_refused_without_chmod_or_state_creation(
        self,
    ):
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        with self.assertRaises(bookmarks.StartupError) as raised:
            bookmarks.inspect_bookmarks(self.root)
        self.assertEqual(raised.exception.check, "state_directory")
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.root / "mac-setup-state").exists())
        bookmarks.FirefoxSession.assert_not_called()

    def test_symlink_isolated_root_is_refused_without_writing_outside_state(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(bookmarks.StartupError) as raised:
            bookmarks.inspect_bookmarks(self.root)
        self.assertEqual(raised.exception.check, "state_directory")
        self.assertEqual(list(outside.iterdir()), [])
        bookmarks.FirefoxSession.assert_not_called()

    def test_inspect_returns_current_profile_identity_before_decryption(self):
        self.source.unlink()
        result = bookmarks.inspect_bookmarks(self.root)
        self.assertEqual(
            result,
            {
                "changed": False,
                "imported": 0,
                "state": "pending",
                "profile_key": "a" * 64,
            },
        )
        self.assertEqual(self.session.calls, 0)
        self.assertFalse(
            bookmarks.completion_path(self.root, self.session.profile_key).exists()
        )

    def test_completion_lives_outside_firefox_data_and_skips_changed_source(self):
        first = bookmarks.import_bookmarks(self.source, self.root)
        self.assertEqual(first, {"changed": True, "imported": 3, "state": "imported"})
        marker = bookmarks.completion_path(self.root, self.session.profile_key)
        self.assertEqual(marker.parent, self.root / "mac-setup-state")
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        self.assertEqual(marker.parent.stat().st_mode & 0o777, 0o700)
        before = marker.stat().st_mtime_ns
        self.source.write_text("invalid replacement test input")
        self.assertEqual(
            bookmarks.inspect_bookmarks(self.root)["state"], "already_imported"
        )
        self.assertEqual(
            bookmarks.import_bookmarks(self.source, self.root),
            {"changed": False, "imported": 0, "state": "already_imported"},
        )
        self.assertEqual(self.session.calls, 1)
        self.assertEqual(marker.stat().st_mtime_ns, before)
        self.assertNotIn("SYNTHETIC", marker.read_text())

    def test_completion_is_separate_for_each_runtime_profile(self):
        bookmarks.import_bookmarks(self.source, self.root)
        self.session.profile_key = "b" * 64
        self.session.imported = 0
        self.assertEqual(bookmarks.inspect_bookmarks(self.root)["state"], "pending")
        self.assertEqual(
            bookmarks.import_bookmarks(self.source, self.root)["imported"], 3
        )
        self.assertEqual(len(list(bookmarks.state_root(self.root).glob("*.json"))), 2)

    def test_profile_change_is_refused_before_source_read_or_bookmark_write(self):
        self.source.unlink()
        with self.assertRaises(bookmarks.BookmarkError) as raised:
            bookmarks.import_bookmarks(
                self.source, self.root, expected_profile_key="b" * 64
            )
        self.assertIn("different profile", str(raised.exception))
        self.assertEqual(self.session.calls, 0)
        self.assertFalse(
            bookmarks.completion_path(self.root, self.session.profile_key).exists()
        )

    def test_crash_before_record_retries_without_reinsertion(self):
        self.session.crash = True
        with self.assertRaises(bookmarks.BookmarkError):
            bookmarks.import_bookmarks(self.source, self.root)
        marker = bookmarks.completion_path(self.root, self.session.profile_key)
        self.assertFalse(marker.exists())
        self.session.crash = False
        retry = bookmarks.import_bookmarks(self.source, self.root)
        self.assertEqual(retry["imported"], 0)
        self.assertTrue(marker.exists())

    def test_running_firefox_is_refused_before_any_state_write(self):
        with patch.object(
            bookmarks,
            "check_closed",
            side_effect=bookmarks.BookmarkError("Quit Firefox"),
        ):
            with self.assertRaises(bookmarks.BookmarkError):
                bookmarks.import_bookmarks(self.source, self.root)
        self.assertFalse(self.root.exists())

    def test_absent_firefox_skips_inspection_and_source(self):
        with patch.object(bookmarks, "FIREFOX", str(self.base / "not-installed")):
            self.assertEqual(
                bookmarks.inspect_bookmarks(self.root)["state"], "not_installed"
            )
            self.assertEqual(
                bookmarks.import_bookmarks(self.source, self.root)["state"],
                "not_installed",
            )
        self.assertFalse(self.root.exists())

    def test_permission_denial_names_permission_and_screen_without_echoing_path(self):
        for operation in (
            bookmarks.inspect_bookmarks,
            lambda root: bookmarks.import_bookmarks(self.source, root),
        ):
            with patch.object(
                bookmarks,
                "import_lock",
                side_effect=PermissionError(errno.EPERM, "SYNTHETIC-PRIVATE-PATH"),
            ):
                with self.assertRaises(bookmarks.PermissionDenied) as raised:
                    operation(self.root)
            self.assertEqual(str(raised.exception), bookmarks.PERMISSION_MESSAGE)
            self.assertIn("Full Disk Access", str(raised.exception))
            self.assertIn("App Data", str(raised.exception))
            self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))
            self.assertNotIn("Quit Firefox", str(raised.exception))

    def test_default_profile_import_never_inspects_protected_firefox_folder(self):
        home = self.base / "account"
        protected = home / "Library/Application Support/Firefox"
        calls = []

        def guarded(original):
            def run(path, *args, **kwargs):
                if str(path) == str(protected) or str(path).startswith(
                    str(protected) + "/"
                ):
                    calls.append(str(path))
                    raise PermissionError(errno.EPERM, "SYNTHETIC-PRIVATE-PATH")
                return original(path, *args, **kwargs)

            return run

        with patch.object(Path, "home", return_value=home), patch.object(
            Path, "stat", guarded(Path.stat)
        ), patch.object(Path, "open", guarded(Path.open)), patch.object(
            Path, "mkdir", guarded(Path.mkdir)
        ), patch.object(
            Path, "iterdir", guarded(Path.iterdir)
        ):
            self.assertEqual(bookmarks.inspect_bookmarks()["state"], "pending")
            self.assertEqual(bookmarks.import_bookmarks(self.source)["imported"], 3)
            self.assertEqual(bookmarks.inspect_bookmarks()["state"], "already_imported")
            self.assertEqual(
                bookmarks.state_root(),
                home / "Library/Application Support/mac-setup/firefox-bookmarks",
            )
        self.assertEqual(calls, [])

    def test_invalid_completion_types_are_refused(self):
        marker = bookmarks.completion_path(self.root, self.session.profile_key)
        marker.parent.mkdir(mode=0o700, parents=True)
        marker.write_text('{"version": true, "completed": 1}')
        marker.chmod(0o600)
        with self.assertRaises(bookmarks.BookmarkError):
            bookmarks.inspect_bookmarks(self.root)

    def test_source_and_completion_symlinks_are_refused(self):
        target = self.source.with_name("target")
        target.write_text(HTML)
        self.source.unlink()
        self.source.symlink_to(target)
        with self.assertRaises(bookmarks.BookmarkError):
            bookmarks.import_bookmarks(self.source, self.root)
        marker = bookmarks.completion_path(self.root, self.session.profile_key)
        marker.symlink_to(target)
        with self.assertRaises(bookmarks.BookmarkError):
            bookmarks.inspect_bookmarks(self.root)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "isolated"
        environment = patch.dict(os.environ, {"SYNTHETIC_ENV": "true"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.native_verify_peer = bookmarks.verify_peer
        peer = patch.object(bookmarks, "verify_peer")
        peer.start()
        self.addCleanup(peer.stop)

    def capture(self, root=None):
        session = bookmarks.FirefoxSession(root)
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess,
            "Popen",
            side_effect=RuntimeError("Synthetic startup capture"),
        ) as launch:
            with self.assertRaises(RuntimeError):
                session.__enter__()
        return launch.call_args

    def test_default_launch_has_no_profile_argument_or_application_data_override(self):
        with patch.dict(
            os.environ,
            {
                "XRE_PROFILE_PATH": "SYNTHETIC-PRIVATE",
                "MOZ_APP_DATA": "SYNTHETIC-PRIVATE",
                "MOZ_LOCAL_APP_DATA": "SYNTHETIC-PRIVATE",
                "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "SYNTHETIC-PRIVATE",
            },
        ):
            call = self.capture()
        self.assertNotIn("-profile", call.args[0])
        command = call.args[0]
        self.assertEqual(command[:5], ["/usr/bin/open", "-n", "-g", "-W", "-a"])
        self.assertEqual(command[5], "/Applications/Firefox.app")
        self.assertIn("--env", command)
        self.assertIn("MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1", command)
        self.assertNotIn("--remote-allow-system-access", command)
        self.assertIn("--stdout", command)
        self.assertEqual(command[command.index("--stdout") + 1], "/dev/null")
        self.assertEqual(command[command.index("--stderr") + 1], "/dev/null")
        preferences = json.loads(
            next(
                value.split("=", 1)[1]
                for value in command
                if value.startswith("MOZ_MARIONETTE_PREF_STATE_ACROSS_RESTARTS=")
            )
        )
        self.assertEqual(
            preferences, {"marionette.port": 40123, "marionette.log.level": "Fatal"}
        )
        for variable in ("XRE_PROFILE_PATH", "MOZ_APP_DATA", "MOZ_LOCAL_APP_DATA"):
            self.assertNotIn(variable, call.kwargs["env"])
        preferences = json.loads(
            call.kwargs["env"]["MOZ_MARIONETTE_PREF_STATE_ACROSS_RESTARTS"]
        )
        self.assertEqual(
            preferences, {"marionette.port": 40123, "marionette.log.level": "Fatal"}
        )
        self.assertNotIn("SYNTHETIC-PRIVATE", json.dumps(call.kwargs["env"]))

    def test_isolated_launch_overrides_both_data_locations_without_profile_scan(self):
        call = self.capture(self.root)
        self.assertEqual(
            call.kwargs["env"]["MOZ_APP_DATA"], str(self.root / "app-data")
        )
        self.assertEqual(
            call.kwargs["env"]["MOZ_LOCAL_APP_DATA"], str(self.root / "local-data")
        )
        self.assertNotIn("-profile", call.args[0])
        self.assertIn("MOZ_APP_DATA=" + str(self.root / "app-data"), call.args[0])
        self.assertIn(
            "MOZ_LOCAL_APP_DATA=" + str(self.root / "local-data"), call.args[0]
        )
        self.assertEqual(
            sorted(path.name for path in self.root.iterdir()),
            ["app-data", "local-data"],
        )

    def test_identity_is_read_only_after_the_launched_application_pid_is_verified(self):
        process = Mock(pid=1234)
        process.poll.return_value = None
        process.wait.return_value = 0
        connection = Mock()
        session = bookmarks.FirefoxSession()
        identity = {
            "isolated": True,
            "profile_id": "11111111-1111-4111-8111-111111111111",
            "identity_created": False,
        }
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen", return_value=Mock(pid=8000)
        ), patch.object(
            bookmarks.FirefoxProcess, "find", return_value=process
        ), patch.object(
            bookmarks.socket,
            "create_connection",
            side_effect=[ConnectionRefusedError(), connection],
        ) as connect, patch.object(
            bookmarks.time, "sleep"
        ), patch.object(
            bookmarks, "receive", return_value={"marionetteProtocol": 3}
        ), patch.object(
            session,
            "send",
            return_value={"cause": "shutdown", "forced": False, "in_app": True},
        ) as send, patch.object(
            session, "script", side_effect=[1234, identity]
        ) as script:
            with session as opened:
                self.assertEqual(
                    opened.profile_key,
                    hashlib.sha256(identity["profile_id"].encode()).hexdigest(),
                )
            self.assertEqual(
                script.call_args_list[0].args[0], "return Services.appinfo.processID;"
            )
            self.assertEqual(
                script.call_args_list[1].args[0], bookmarks.IDENTITY_SCRIPT
            )
            self.assertEqual(connect.call_count, 2)
            self.assertIn(("127.0.0.1", 40123), connect.call_args.args)
            self.assertTrue(
                any(call.args[0] == "Marionette:Quit" for call in send.call_args_list)
            )

    def test_wrong_selected_target_has_fixed_named_isolation_check(self):
        process = Mock(pid=1234)
        process.poll.return_value = None
        session = bookmarks.FirefoxSession(self.root)
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen"
        ), patch.object(
            bookmarks.FirefoxProcess, "find", return_value=process
        ), patch.object(
            bookmarks.socket, "create_connection"
        ), patch.object(
            bookmarks, "receive", return_value={"marionetteProtocol": 3}
        ), patch.object(
            session, "send"
        ) as send, patch.object(
            session, "script", side_effect=[1234, {"isolated": False}]
        ):
            with self.assertRaises(bookmarks.StartupError) as raised:
                session.__enter__()
        self.assertEqual(raised.exception.check, "isolation")
        self.assertEqual(str(raised.exception), bookmarks.ISOLATION_MESSAGE)
        self.assertFalse(session.verified)
        self.assertFalse(
            any(call.args[0] == "Marionette:Quit" for call in send.call_args_list)
        )

    def test_unverified_peer_receives_no_identity_write_and_no_quit(self):
        process = Mock(pid=1234)
        process.poll.return_value = None
        session = bookmarks.FirefoxSession()
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen", return_value=Mock(pid=8000)
        ), patch.object(
            bookmarks.FirefoxProcess, "find", return_value=process
        ), patch.object(
            bookmarks.socket, "create_connection"
        ), patch.object(
            bookmarks, "receive", return_value={"marionetteProtocol": 3}
        ), patch.object(
            session, "send"
        ) as send, patch.object(
            session, "script", return_value=9999
        ) as script:
            with self.assertRaises(bookmarks.BookmarkError):
                session.__enter__()
        script.assert_called_once_with("return Services.appinfo.processID;")
        self.assertFalse(
            any(call.args[0] == "Marionette:Quit" for call in send.call_args_list)
        )
        process.terminate.assert_called_once()

    def test_browser_crash_is_never_a_verified_normal_quit(self):
        session = bookmarks.FirefoxSession()
        session.process = Mock()
        session.process.poll.return_value = 1
        with self.assertRaises(bookmarks.BookmarkError):
            session.__exit__(None, None, None)

    def test_released_versions_accept_explicit_launchservices_system_access(self):
        for version in ("158.0.1", "159.0", "160.0"):
            with self.subTest(version=version):
                allowed = False
                process = Mock(pid=1234)
                process.poll.return_value = None
                process.wait.side_effect = lambda timeout: setattr(
                    process.poll, "return_value", 1
                )
                session = bookmarks.FirefoxSession()
                identity = {
                    "isolated": True,
                    "profile_id": "11111111-1111-4111-8111-111111111111",
                    "identity_created": False,
                }

                def launch(command, **kwargs):
                    nonlocal allowed
                    explicit = {
                        command[i + 1].split("=", 1)[0]: command[i + 1].split("=", 1)[1]
                        for i, value in enumerate(command)
                        if value == "--env"
                    }
                    allowed = explicit.get("MOZ_REMOTE_ALLOW_SYSTEM_ACCESS") == "1" or (
                        version == "158.0.1"
                        and "--remote-allow-system-access" in command
                    )
                    return Mock(pid=8000)

                calls = 0

                def response(stream):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return {"marionetteProtocol": 3}
                    sequence = calls - 1
                    if sequence == 1:
                        return [
                            1,
                            sequence,
                            None,
                            {
                                "sessionId": "synthetic",
                                "capabilities": {"browserVersion": version},
                            },
                        ]
                    if sequence == 2:
                        error = (
                            None
                            if allowed
                            else {
                                "error": "unsupported operation",
                                "message": "SYNTHETIC-PRIVATE",
                            }
                        )
                        return [1, sequence, error, {}]
                    if sequence == 3:
                        return [1, sequence, None, {"value": 1234}]
                    if sequence == 4:
                        return [1, sequence, None, {"value": identity}]
                    if sequence == 5:
                        return [
                            1,
                            sequence,
                            None,
                            {"cause": "shutdown", "forced": False, "in_app": True},
                        ]
                    raise AssertionError("Unexpected synthetic browser response")

                with patch.object(bookmarks, "check_closed"), patch.object(
                    bookmarks, "require_gui_session"
                ), patch.object(
                    bookmarks, "free_port", return_value=40123
                ), patch.object(
                    bookmarks.subprocess, "Popen", side_effect=launch
                ), patch.object(
                    bookmarks.FirefoxProcess, "find", return_value=process
                ), patch.object(
                    bookmarks.socket, "create_connection"
                ), patch.object(
                    bookmarks, "receive", side_effect=response
                ):
                    with session:
                        self.assertTrue(allowed)
                        self.assertEqual(session.version, version)
                self.assertTrue(session.verified)

    def test_chrome_context_refusal_stops_before_profile_identity(self):
        process = Mock(pid=1234)
        process.poll.return_value = None
        session = bookmarks.FirefoxSession()
        frames = [
            {"marionetteProtocol": 3},
            [1, 1, None, {"capabilities": {"browserVersion": "160.0"}}],
            [
                1,
                2,
                {"error": "unsupported operation", "message": "SYNTHETIC-PRIVATE"},
                None,
            ],
        ]
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen"
        ), patch.object(
            bookmarks.FirefoxProcess, "find", return_value=process
        ), patch.object(
            bookmarks.socket, "create_connection"
        ), patch.object(
            bookmarks, "receive", side_effect=frames
        ), patch.object(
            session, "script"
        ) as script:
            with self.assertRaises(bookmarks.StartupError) as raised:
                session.__enter__()
        self.assertEqual(raised.exception.check, "system_access")
        self.assertEqual(str(raised.exception), bookmarks.SYSTEM_ACCESS_MESSAGE)
        self.assertEqual(session.version, "160.0")
        self.assertIsNone(session.profile_key)
        self.assertFalse(session.verified)
        script.assert_not_called()

    def test_privileged_refusal_is_named_for_every_observed_version(self):
        for version in ("158.0.1", "159.0", "160.0", "999.0", "unknown"):
            for command in (
                "Marionette:SetContext",
                "WebDriver:ExecuteScript",
                "WebDriver:ExecuteAsyncScript",
            ):
                with self.subTest(version=version, command=command):
                    session = bookmarks.FirefoxSession()
                    session.version = version
                    session.connection = Mock()
                    error = {
                        "error": "unsupported operation",
                        "message": "SYNTHETIC-PRIVATE",
                    }
                    with patch.object(
                        bookmarks, "receive", return_value=[1, 1, error, None]
                    ):
                        with self.assertRaises(bookmarks.StartupError) as raised:
                            session.send(command, {"value": "chrome"})
                    self.assertEqual(raised.exception.check, "system_access")
                    self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))

    def test_privileged_body_refusal_survives_real_session_cleanup_failure(self):
        session = bookmarks.FirefoxSession()
        session.process = Mock()
        session.process.poll.side_effect = bookmarks.BookmarkError(
            "Synthetic ownership lookup failure."
        )
        session.launcher = Mock()
        session.connection = Mock()
        session.verified = True
        error = {"error": "unsupported operation", "message": "SYNTHETIC-PRIVATE"}
        with patch.object(
            bookmarks.FirefoxSession, "__enter__", return_value=session
        ), patch.object(bookmarks, "receive", return_value=[1, 1, error, None]):
            with self.assertRaises(bookmarks.StartupError) as raised:
                with session:
                    session.send(
                        "WebDriver:ExecuteAsyncScript", {"script": "return true;"}
                    )
        self.assertEqual(raised.exception.check, "system_access")
        self.assertEqual(str(raised.exception), bookmarks.SYSTEM_ACCESS_MESSAGE)
        session.process.terminate.assert_not_called()
        session.process.kill.assert_not_called()
        session.connection.close.assert_called_once()
        session.launcher.wait.assert_called_once_with(timeout=10)

    def test_runtime_version_accepts_only_safe_observed_capability_values(self):
        for value, expected in (
            ("158.0.1", "158.0.1"),
            ("160.0", "160.0"),
            ("999.0", "999.0"),
            ("SYNTHETIC-PRIVATE", "unknown"),
            ("160.0\nSYNTHETIC-PRIVATE", "unknown"),
            (None, "unknown"),
            ({"private": "SYNTHETIC-PRIVATE"}, "unknown"),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    bookmarks.observed_version(
                        {"capabilities": {"browserVersion": value}}
                    ),
                    expected,
                )
        self.assertEqual(bookmarks.observed_version(None), "unknown")
        self.assertEqual(bookmarks.observed_version({"capabilities": None}), "unknown")

    def test_marionette_permission_error_is_sanitized(self):
        session = bookmarks.FirefoxSession()
        session.connection = Mock()
        error = {"message": "Operation not permitted: SYNTHETIC-PRIVATE-PATH"}
        with patch.object(bookmarks, "receive", return_value=[1, 1, error, None]):
            with self.assertRaises(bookmarks.PermissionDenied) as raised:
                session.send("test", {})
        self.assertEqual(str(raised.exception), bookmarks.PERMISSION_MESSAGE)

    def test_malformed_protocol_error_cannot_escape_as_private_exception_text(self):
        session = bookmarks.FirefoxSession()
        session.connection = Mock()
        with patch.object(
            bookmarks,
            "receive",
            return_value=[1, 1, {"message": {"data": "SYNTHETIC-PRIVATE"}}, None],
        ):
            with self.assertRaises(bookmarks.BookmarkError) as raised:
                session.send("test", {})
        self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))

    def test_shared_process_matcher_refuses_content_and_keeps_unrelated_processes(self):
        for command, refused in (
            ("firefoxcp Web Content", True),
            ("firefox-bin", True),
            ("/usr/bin/python3", False),
        ):
            with self.subTest(command=command), patch.object(
                bookmarks.subprocess,
                "run",
                return_value=Mock(returncode=0, stdout=command + "\n"),
            ):
                if refused:
                    with self.assertRaises(bookmarks.BookmarkError):
                        bookmarks.check_closed()
                else:
                    bookmarks.check_closed()

    def test_missing_gui_session_refuses_before_launch_or_test_directory_creation(self):
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks.subprocess, "run", return_value=Mock(returncode=1)
        ) as run, patch.object(bookmarks.subprocess, "Popen") as launch:
            with self.assertRaises(bookmarks.BookmarkError) as raised:
                bookmarks.FirefoxSession(self.root).__enter__()
        self.assertEqual(str(raised.exception), bookmarks.GUI_MESSAGE)
        self.assertEqual(raised.exception.check, "gui")
        self.assertEqual(
            run.call_args.args[0],
            ["/bin/launchctl", "print", "gui/" + str(os.getuid())],
        )
        launch.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_launchservices_failure_has_fixed_guidance_and_no_unknown_process_signal(
        self,
    ):
        launcher = Mock()
        launcher.poll.return_value = 1
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen", return_value=launcher
        ), patch.object(
            bookmarks.FirefoxProcess, "find", return_value=None
        ), patch.object(
            bookmarks.os, "kill"
        ) as kill:
            with self.assertRaises(bookmarks.BookmarkError) as raised:
                bookmarks.FirefoxSession().__enter__()
        self.assertEqual(str(raised.exception), bookmarks.LAUNCH_MESSAGE)
        self.assertEqual(raised.exception.check, "launch")
        kill.assert_not_called()

    def test_launchservices_discovery_can_wait_for_new_application(self):
        process = Mock(pid=1234)
        process.poll.return_value = None
        session = bookmarks.FirefoxSession()
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen", return_value=Mock(pid=8000)
        ), patch.object(
            bookmarks.FirefoxProcess, "find", side_effect=[None, process]
        ), patch.object(
            bookmarks.time, "sleep"
        ), patch.object(
            bookmarks.socket, "create_connection"
        ), patch.object(
            bookmarks, "receive", return_value={"marionetteProtocol": 3}
        ), patch.object(
            session, "send"
        ), patch.object(
            session, "script", return_value=9999
        ):
            with self.assertRaises(bookmarks.BookmarkError):
                session.__enter__()
        process.terminate.assert_called_once()

    def test_completion_requires_normal_shutdown_acknowledgement_and_process_exit(self):
        for response in (
            None,
            {},
            {"cause": "shutdown", "forced": True, "in_app": True},
            {"cause": "restart", "forced": False, "in_app": True},
        ):
            with self.subTest(response=response):
                session = bookmarks.FirefoxSession()
                session.process = Mock()
                session.process.poll.return_value = None
                session.connection = Mock()
                session.launcher = Mock()
                session.launcher.wait.return_value = 0
                session.verified = True
                with patch.object(session, "send", return_value=response):
                    with self.assertRaises(bookmarks.BookmarkError):
                        session.__exit__(None, None, None)
                session.process.terminate.assert_called_once()

    def test_quit_timeout_cannot_be_hidden_by_successful_open_exit(self):
        session = bookmarks.FirefoxSession()
        session.process = Mock()
        session.process.poll.return_value = None
        session.process.wait.side_effect = [
            subprocess.TimeoutExpired("Firefox", 30),
            None,
        ]
        session.connection = Mock()
        session.launcher = Mock()
        session.launcher.wait.return_value = 0
        session.verified = True
        with patch.object(
            session,
            "send",
            return_value={"cause": "shutdown", "forced": False, "in_app": True},
        ):
            with self.assertRaises(bookmarks.BookmarkError):
                session.__exit__(None, None, None)
        session.process.terminate.assert_called_once()

    def test_startup_follows_proven_restart_before_identity_access(self):
        old = Mock(pid=1234)
        old.poll.return_value = 1
        new = Mock(pid=1235)
        new.poll.return_value = None
        new.wait.side_effect = lambda timeout: setattr(new.poll, "return_value", 1)
        session = bookmarks.FirefoxSession()
        identity = {
            "isolated": True,
            "profile_id": "11111111-1111-4111-8111-111111111111",
            "identity_created": False,
        }
        launcher = Mock(pid=8000)
        launcher.poll.return_value = 0
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen", return_value=launcher
        ), patch.object(
            bookmarks.FirefoxProcess, "find", side_effect=[old, None, new]
        ) as find, patch.object(
            bookmarks.time, "sleep"
        ), patch.object(
            bookmarks.socket, "create_connection"
        ), patch.object(
            bookmarks, "receive", return_value={"marionetteProtocol": 3}
        ), patch.object(
            session,
            "send",
            return_value={"cause": "shutdown", "forced": False, "in_app": True},
        ), patch.object(
            session, "script", side_effect=[1235, identity]
        ) as script:
            with session:
                self.assertEqual(session.process.pid, 1235)
        self.assertEqual(find.call_count, 3)
        self.assertEqual(len({call.args[0] for call in find.call_args_list}), 1)
        self.assertEqual(
            script.call_args_list[0].args[0], "return Services.appinfo.processID;"
        )
        old.terminate.assert_not_called()
        new.wait.assert_called_once_with(timeout=30)

    def test_restart_missing_launch_token_never_reaches_automation(self):
        old = Mock(pid=1234)
        old.poll.return_value = 1
        session = bookmarks.FirefoxSession()
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen"
        ), patch.object(
            bookmarks.FirefoxProcess, "find", side_effect=[old, None]
        ), patch.object(
            bookmarks.time, "monotonic", side_effect=[0, 61]
        ), patch.object(
            session, "script"
        ) as script:
            with self.assertRaises(bookmarks.StartupError):
                session.__enter__()
        script.assert_not_called()
        old.terminate.assert_not_called()

    def test_restart_between_connection_and_pid_check_is_reverified_before_identity(
        self,
    ):
        old = Mock(pid=1234)
        old.poll.side_effect = [None, 1]
        new = Mock(pid=1235)
        new.poll.return_value = None
        new.wait.side_effect = lambda timeout: setattr(new.poll, "return_value", 1)
        session = bookmarks.FirefoxSession()
        identity = {
            "isolated": True,
            "profile_id": "11111111-1111-4111-8111-111111111111",
            "identity_created": False,
        }
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen"
        ), patch.object(
            bookmarks.FirefoxProcess, "find", side_effect=[old, new]
        ) as find, patch.object(
            bookmarks.socket, "create_connection"
        ), patch.object(
            bookmarks, "receive", return_value={"marionetteProtocol": 3}
        ), patch.object(
            session,
            "send",
            return_value={"cause": "shutdown", "forced": False, "in_app": True},
        ), patch.object(
            session, "script", side_effect=[1235, identity]
        ) as script:
            with session:
                self.assertEqual(session.process.pid, 1235)
        self.assertEqual(find.call_count, 2)
        self.assertEqual(script.call_args_list[1].args[0], bookmarks.IDENTITY_SCRIPT)
        old.terminate.assert_not_called()

    def test_impersonating_peer_is_rejected_before_protocol_or_profile_commands(self):
        process = Mock(pid=1234)
        process.poll.return_value = None
        connection = Mock()
        connection.getsockname.return_value = ("127.0.0.1", 45000)
        connection.getpeername.return_value = ("127.0.0.1", 40123)
        session = bookmarks.FirefoxSession()
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen"
        ), patch.object(
            bookmarks.FirefoxProcess, "find", return_value=process
        ), patch.object(
            bookmarks.socket, "create_connection", return_value=connection
        ), patch.object(
            bookmarks, "verify_peer", side_effect=self.native_verify_peer
        ), patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(
                returncode=0, stdout="p9999\nn127.0.0.1:40123->127.0.0.1:45000\n"
            ),
        ), patch.object(
            bookmarks,
            "receive",
            side_effect=AssertionError("Unverified peer reached protocol"),
        ), patch.object(
            session, "send"
        ) as send, patch.object(
            session, "script"
        ) as script, patch.object(
            bookmarks.time, "monotonic", side_effect=[0, 0, 61]
        ), patch.object(
            bookmarks.time, "sleep"
        ):
            with self.assertRaises(bookmarks.StartupError):
                session.__enter__()
        send.assert_not_called()
        script.assert_not_called()
        self.assertFalse(session.verified)

    def test_startup_primary_check_survives_a_cleanup_ownership_failure(self):
        process = Mock(pid=1234)
        process.poll.side_effect = bookmarks.BookmarkError(
            "Synthetic fixed ownership check."
        )
        session = bookmarks.FirefoxSession()
        with patch.object(bookmarks, "check_closed"), patch.object(
            bookmarks, "require_gui_session"
        ), patch.object(bookmarks, "free_port", return_value=40123), patch.object(
            bookmarks.subprocess, "Popen"
        ), patch.object(
            bookmarks.FirefoxProcess, "find", return_value=process
        ):
            with self.assertRaises(bookmarks.StartupError) as raised:
                session.__enter__()
        self.assertEqual(raised.exception.check, "automation")
        process.terminate.assert_not_called()


class LaunchedProcessTests(unittest.TestCase):
    token = "about:blank#mac-setup-synthetic-token"

    def setUp(self):
        executable = patch.object(
            bookmarks, "process_executable", return_value=bookmarks.FIREFOX
        )
        self.executable = executable.start()
        self.addCleanup(executable.stop)
        birth = patch.object(
            bookmarks,
            "process_birth",
            return_value=(os.getuid(), 1000, 123456),
            create=True,
        )
        self.birth = birth.start()
        self.addCleanup(birth.stop)

    def row(
        self,
        pid=1234,
        uid=None,
        started="Sat Nov 14 01:02:03 2026",
        binary=None,
        token=None,
        state="S",
    ):
        return f"{pid} {os.getuid() if uid is None else uid} {started} {state} {binary or bookmarks.FIREFOX} --headless --marionette {self.token if token is None else token}\n"

    def find(self, rows):
        with patch.object(
            bookmarks.subprocess, "run", return_value=Mock(returncode=0, stdout=rows)
        ) as run:
            found = bookmarks.FirefoxProcess.find(self.token)
        self.assertEqual(
            run.call_args.args[0],
            ["/bin/ps", "-ww", "-axo", "pid=,uid=,lstart=,stat=,command="],
        )
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")
        return found

    def test_only_current_account_exact_binary_and_whole_launch_token_match(self):
        for row in (
            self.row(uid=os.getuid() + 1),
            self.row(binary="/synthetic/other/firefox"),
            self.row(token=self.token + "-other"),
            self.row(state="Z"),
            self.row(pid=0),
            "invalid synthetic row\n",
        ):
            with self.subTest(row=row):
                self.assertIsNone(self.find(row))
        found = self.find(self.row())
        self.assertEqual(found.pid, 1234)

    def test_ambiguous_application_match_refuses(self):
        with self.assertRaises(bookmarks.BookmarkError):
            self.find(self.row() + self.row(pid=1235))

    def test_reused_pid_or_changed_application_is_never_signalled(self):
        original = self.find(self.row())
        self.birth.return_value = (os.getuid(), 1001, 123456)
        for replacement in ("", self.row(started="Sat Nov 14 01:02:04 2026")):
            with patch.object(
                bookmarks.subprocess,
                "run",
                return_value=Mock(returncode=0, stdout=replacement),
            ), patch.object(bookmarks.os, "kill") as kill:
                self.assertEqual(original.poll(), 1)
                original.terminate()
                original.kill()
                original.wait(timeout=1)
                kill.assert_not_called()

    def test_owned_application_is_rechecked_immediately_before_each_signal(self):
        process = self.find(self.row())
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(returncode=0, stdout=self.row()),
        ) as read, patch.object(bookmarks.os, "kill") as kill:
            process.terminate()
            process.kill()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(
            [call.args for call in kill.call_args_list],
            [(1234, bookmarks.signal.SIGTERM), (1234, bookmarks.signal.SIGKILL)],
        )

    def test_incomplete_known_pid_metadata_refuses_uncertain_exit(self):
        process = self.find(self.row())
        incomplete = self.row().split(bookmarks.FIREFOX, 1)[0] + "\n"
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(returncode=0, stdout=incomplete),
        ), patch.object(bookmarks.os, "kill") as kill:
            for operation in (
                process.poll,
                process.terminate,
                lambda: process.wait(timeout=1),
            ):
                with self.assertRaises(bookmarks.BookmarkError):
                    operation()
            kill.assert_not_called()

    def test_shutdown_acknowledgement_does_not_override_unknown_process_metadata(self):
        process = self.find(self.row())
        incomplete = self.row().split(bookmarks.FIREFOX, 1)[0] + "\n"
        session = bookmarks.FirefoxSession()
        session.process = process
        session.launcher = Mock()
        session.connection = Mock()
        session.verified = True
        replies = [
            Mock(returncode=0, stdout=self.row()),
            Mock(returncode=0, stdout=incomplete),
            Mock(returncode=0, stdout=incomplete),
        ]
        with patch.object(
            bookmarks.subprocess, "run", side_effect=replies
        ), patch.object(bookmarks.os, "kill") as kill, patch.object(
            session,
            "send",
            return_value={"cause": "shutdown", "forced": False, "in_app": True},
        ) as send:
            with self.assertRaises(bookmarks.BookmarkError):
                session.__exit__(None, None, None)
        send.assert_called_once_with("Marionette:Quit", {"flags": ["eAttemptQuit"]})
        kill.assert_not_called()

    def test_consumed_startup_arguments_do_not_change_bound_process_ownership(self):
        process = self.find(self.row())
        consumed = self.row().replace("--headless --marionette " + self.token, "")
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(returncode=0, stdout=consumed),
        ), patch.object(bookmarks.os, "kill") as kill:
            self.assertIsNone(process.poll())
            process.terminate()
        kill.assert_called_once_with(1234, bookmarks.signal.SIGTERM)

    def test_changed_kernel_executable_is_never_signalled_or_counted_as_exit(self):
        process = self.find(self.row())
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(returncode=0, stdout=self.row(token="different-token")),
        ), patch.object(
            bookmarks, "process_executable", return_value="/synthetic/other"
        ), patch.object(
            bookmarks.os, "kill"
        ) as kill:
            for operation in (
                process.poll,
                process.terminate,
                lambda: process.wait(timeout=1),
            ):
                with self.assertRaises(bookmarks.BookmarkError):
                    operation()
            kill.assert_not_called()

    def test_wait_times_out_if_owned_application_stays_alive(self):
        process = self.find(self.row())
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(returncode=0, stdout=self.row()),
        ), patch.object(bookmarks.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaises(subprocess.TimeoutExpired):
                process.wait(timeout=1)

    def test_spoofed_command_cannot_bind_a_different_kernel_executable(self):
        self.executable.return_value = "/synthetic/other"
        self.assertIsNone(self.find(self.row()))

    def test_stale_launch_token_is_rechecked_within_kernel_birth_observations(self):
        for current in (self.row(token="unrelated-start"), self.row()):
            with self.subTest(current=current):
                self.birth.side_effect = [
                    (os.getuid(), 1000, 1),
                    (os.getuid(), 1000, 2),
                ]
                with patch.object(
                    bookmarks.subprocess,
                    "run",
                    side_effect=[
                        Mock(returncode=0, stdout=self.row()),
                        Mock(returncode=0, stdout=current),
                    ],
                ):
                    self.assertIsNone(bookmarks.FirefoxProcess.find(self.token))

    def test_same_second_pid_reuse_is_rejected_by_precise_kernel_birth(self):
        process = self.find(self.row())
        self.birth.return_value = (os.getuid(), 1000, 123457)
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(returncode=0, stdout=self.row(token="unrelated-start")),
        ), patch.object(bookmarks.os, "kill") as kill:
            self.assertEqual(process.poll(), 1)
            process.terminate()
            process.kill()
        kill.assert_not_called()

    def test_kernel_metadata_failure_never_signals_the_bound_application(self):
        process = self.find(self.row())
        self.executable.side_effect = bookmarks.BookmarkError(
            "Synthetic kernel lookup failure."
        )
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(returncode=0, stdout=self.row()),
        ), patch.object(bookmarks.os, "kill") as kill:
            with self.assertRaises(bookmarks.BookmarkError):
                process.terminate()
        kill.assert_not_called()

    def test_failed_process_read_exposes_no_arbitrary_output(self):
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(returncode=1, stdout="SYNTHETIC-PRIVATE"),
        ):
            with self.assertRaises(bookmarks.BookmarkError) as raised:
                bookmarks.FirefoxProcess.find(self.token)
        self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))


class PeerOwnershipTests(unittest.TestCase):
    def test_claimed_pid_does_not_replace_kernel_endpoint_ownership(self):
        connection = Mock()
        connection.getsockname.return_value = ("127.0.0.1", 45000)
        connection.getpeername.return_value = ("127.0.0.1", 40123)
        process = Mock(pid=1234)
        process.poll.return_value = None
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(
                returncode=0, stdout="p9999\nn127.0.0.1:40123->127.0.0.1:45000\n"
            ),
        ):
            with self.assertRaises(bookmarks.BookmarkError):
                bookmarks.verify_peer(connection, process, 40123)
        connection.sendall.assert_not_called()

    def test_only_exact_server_connection_for_bound_pid_is_accepted(self):
        connection = Mock()
        connection.getsockname.return_value = ("127.0.0.1", 45000)
        connection.getpeername.return_value = ("127.0.0.1", 40123)
        process = Mock(pid=1234)
        process.poll.return_value = None
        for name in (
            "127.0.0.1:40123->127.0.0.1:45000",
            "[::ffff:127.0.0.1]:40123->[::ffff:127.0.0.1]:45000",
        ):
            with patch.object(
                bookmarks.subprocess,
                "run",
                return_value=Mock(returncode=0, stdout="p1234\nn" + name + "\n"),
            ) as run:
                bookmarks.verify_peer(connection, process, 40123)
                self.assertEqual(
                    run.call_args.args[0],
                    [
                        "/usr/sbin/lsof",
                        "-n",
                        "-P",
                        "-a",
                        "-p",
                        "1234",
                        "-iTCP@127.0.0.1:40123",
                        "-sTCP:ESTABLISHED",
                        "-Fpn",
                    ],
                )
        for name in (
            "127.0.0.1:45000->127.0.0.1:40123",
            "127.0.0.1:40123->127.0.0.1:45001",
            "127.0.0.2:40123->127.0.0.1:45000",
            "SYNTHETIC-PRIVATE",
        ):
            with patch.object(
                bookmarks.subprocess,
                "run",
                return_value=Mock(returncode=0, stdout="p1234\nn" + name + "\n"),
            ):
                with self.assertRaises(bookmarks.BookmarkError) as raised:
                    bookmarks.verify_peer(connection, process, 40123)
                self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))

    def test_endpoint_query_failure_or_process_replacement_refuses_connection(self):
        connection = Mock()
        connection.getsockname.return_value = ("127.0.0.1", 45000)
        connection.getpeername.return_value = ("127.0.0.1", 40123)
        process = Mock(pid=1234)
        process.poll.return_value = None
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(returncode=1, stdout="SYNTHETIC-PRIVATE"),
        ):
            with self.assertRaises(bookmarks.BookmarkError):
                bookmarks.verify_peer(connection, process, 40123)
        process.poll.side_effect = [None, 1]
        with patch.object(
            bookmarks.subprocess,
            "run",
            return_value=Mock(
                returncode=0, stdout="p1234\nn127.0.0.1:40123->127.0.0.1:45000\n"
            ),
        ):
            with self.assertRaises(bookmarks.BookmarkError):
                bookmarks.verify_peer(connection, process, 40123)

    def test_delayed_kernel_endpoint_ownership_is_polled_before_protocol(self):
        connection, process = Mock(), Mock()
        with patch.object(
            bookmarks,
            "verify_peer",
            side_effect=[bookmarks.BookmarkError("Synthetic not accepted yet."), None],
        ) as verify, patch.object(
            bookmarks.time, "monotonic", return_value=0
        ), patch.object(
            bookmarks.time, "sleep"
        ) as sleep:
            bookmarks.wait_for_peer(connection, process, 40123, deadline=10)
        self.assertEqual(verify.call_count, 2)
        sleep.assert_called_once_with(0.05)
        connection.sendall.assert_not_called()

    def test_unproved_endpoint_has_a_bounded_fixed_startup_refusal(self):
        connection, process = Mock(), Mock()
        with patch.object(
            bookmarks,
            "verify_peer",
            side_effect=bookmarks.BookmarkError("SYNTHETIC-PRIVATE"),
        ), patch.object(bookmarks.time, "monotonic", side_effect=[0, 11]), patch.object(
            bookmarks.time, "sleep"
        ):
            with self.assertRaises(bookmarks.StartupError) as raised:
                bookmarks.wait_for_peer(connection, process, 40123, deadline=10)
        self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))
        connection.sendall.assert_not_called()


class BirthLookupTests(unittest.TestCase):
    def test_kernel_birth_has_microsecond_precision_and_correct_native_layout(self):
        library = Mock()

        def lookup(pid, flavor, arg, output, size):
            self.assertEqual((pid, flavor, arg, size), (1234, 3, 0, 136))
            info = bookmarks.ctypes.cast(
                output, bookmarks.ctypes.POINTER(bookmarks.ProcessBSDInfo)
            ).contents
            info.pid, info.uid, info.seconds, info.microseconds = (
                1234,
                os.getuid(),
                1000,
                123456,
            )
            return size

        library.proc_pidinfo.side_effect = lookup
        with patch.object(bookmarks.ctypes, "CDLL", return_value=library):
            self.assertEqual(bookmarks.process_birth(1234), (os.getuid(), 1000, 123456))

    def test_incomplete_or_unavailable_kernel_birth_is_not_an_exit(self):
        library = Mock()
        library.proc_pidinfo.return_value = 0
        with patch.object(bookmarks.ctypes, "CDLL", return_value=library):
            for number in (errno.EPERM, errno.EACCES, 0):
                with patch.object(bookmarks.ctypes, "get_errno", return_value=number):
                    with self.assertRaises(bookmarks.BookmarkError):
                        bookmarks.process_birth(1234)
            with patch.object(bookmarks.ctypes, "get_errno", return_value=errno.ESRCH):
                self.assertIsNone(bookmarks.process_birth(1234))
            library.proc_pidinfo.return_value = 136
            with self.assertRaises(bookmarks.BookmarkError):
                bookmarks.process_birth(1234)


class ExecutableLookupTests(unittest.TestCase):
    def test_kernel_executable_uses_bounded_native_lookup(self):
        library = Mock()

        def lookup(pid, output, size):
            self.assertEqual(pid, 1234)
            self.assertEqual(size, 4096)
            name = bookmarks.FIREFOX.encode()
            bookmarks.ctypes.memmove(output, name, len(name))
            return len(name)

        library.proc_pidpath.side_effect = lookup
        with patch.object(bookmarks.ctypes, "CDLL", return_value=library) as load:
            self.assertEqual(bookmarks.process_executable(1234), bookmarks.FIREFOX)
        load.assert_called_once_with("/usr/lib/libproc.dylib", use_errno=True)

    def test_unavailable_kernel_metadata_has_fixed_error_and_only_esrch_means_exit(
        self,
    ):
        library = Mock()
        library.proc_pidpath.return_value = 0
        with patch.object(bookmarks.ctypes, "CDLL", return_value=library):
            for number in (errno.EACCES, errno.EPERM, 0):
                with self.subTest(number=number), patch.object(
                    bookmarks.ctypes, "get_errno", return_value=number
                ):
                    with self.assertRaises(bookmarks.BookmarkError) as raised:
                        bookmarks.process_executable(1234)
                    self.assertNotIn("1234", str(raised.exception))
            with patch.object(bookmarks.ctypes, "get_errno", return_value=errno.ESRCH):
                self.assertIsNone(bookmarks.process_executable(1234))
        with patch.object(
            bookmarks.ctypes, "CDLL", side_effect=OSError("SYNTHETIC-PRIVATE")
        ):
            with self.assertRaises(bookmarks.BookmarkError) as raised:
                bookmarks.process_executable(1234)
        self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))


@unittest.skipUnless(
    shutil.which("node"), "Node is optional for executing the isolated Places API test"
)
class PlacesRetryTests(unittest.TestCase):
    def test_places_keeps_existing_and_retries_partial_commit_without_duplicates(self):
        tree = bookmarks.parse_html(HTML)
        javascript = r"""
const tree = JSON.parse(process.argv[1]);
const script = process.argv[2];
const data = new Map([['existing____', {guid:'existing____', type:1, parentGuid:'toolbar_____', title:'Existing', url:'https://example.invalid/existing'}]]);
let budget = Infinity;
const bookmarks = {
  TYPE_BOOKMARK: 1, TYPE_FOLDER: 2, TYPE_SEPARATOR: 3,
  menuGuid: 'menu________', toolbarGuid: 'toolbar_____', unfiledGuid: 'unfiled_____',
  async fetch(guid) { return data.get(guid) ?? null; },
  async eraseEverything() { data.clear(); },
  async insert(item) {
    if (budget-- === 0) throw new Error('Synthetic interrupted import');
    if (data.has(item.guid)) throw new Error('Duplicate guid');
    data.set(item.guid, {...item});
    return {...item};
  }
};
globalThis.ChromeUtils = {importESModule: () => ({PlacesUtils:{bookmarks}})};
const run = () => new Promise(resolve => new Function(script)(tree, resolve));
budget = 2;
const interrupted = await run();
if (!interrupted.error || data.size !== 3) throw new Error('Interruption control failed');
budget = Infinity;
const retry = await run();
if (retry.imported !== 2 || data.size !== 5) throw new Error('Retry duplicated or omitted a bookmark');
const again = await run();
if (again.imported !== 0 || data.size !== 5) throw new Error('Second import changed Places');
if (!data.has('existing____')) throw new Error('Existing bookmark was removed');
const counts = {bookmarks:[...data.values()].filter(item=>item.type===1).length, folders:[...data.values()].filter(item=>item.type===2).length};
process.stdout.write(JSON.stringify(counts));
"""
        result = subprocess.run(
            [
                shutil.which("node"),
                "--input-type=module",
                "-e",
                javascript,
                json.dumps(tree),
                bookmarks.IMPORT_SCRIPT,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"bookmarks": 4, "folders": 1})

    def test_firefox_creates_a_stable_identity_without_returning_its_path(self):
        javascript = r"""
const script = process.argv[1];
const prefs = new Map();
let saves = 0;
globalThis.Ci = {nsIFile: {}};
globalThis.Services = {
  dirsvc: {get: () => ({path: '/synthetic/protected/Firefox/profile'})},
  uuid: {generateUUID: () => ({toString: () => '{11111111-1111-4111-8111-111111111111}'})},
  prefs: {
    getStringPref: (key, fallback) => prefs.get(key) ?? fallback,
    setStringPref: (key, value) => prefs.set(key, value),
    savePrefFile: () => {saves++;}
  }
};
const execute = new Function(script);
const first = execute(null);
const again = execute(null);
const outside = execute('/different-isolation');
if (!first.identity_created || again.identity_created || first.profile_id !== again.profile_id)
  throw Error('Profile identity was not persistent');
if (saves !== 1 || outside.isolated !== false) throw Error('Identity control failed');
if (JSON.stringify(first).includes('/synthetic/')) throw Error('Profile path escaped Firefox');
process.stdout.write(JSON.stringify({created_once: true, stable_identity: true, path_revealed: false}));
"""
        run = subprocess.run(
            [
                shutil.which("node"),
                "--input-type=module",
                "-e",
                javascript,
                bookmarks.IDENTITY_SCRIPT,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(
            run.returncode, 0, "Synthetic Firefox identity control failed."
        )
        self.assertEqual(
            json.loads(run.stdout),
            {"created_once": True, "stable_identity": True, "path_revealed": False},
        )

    def test_places_preserves_repeated_source_entries_and_separators(self):
        tree = bookmarks.parse_html(
            HTML.replace(
                "</DL><p></DL>",
                '<HR><DT><A HREF="https://example.invalid/one">SYNTHETIC-PRIVATE-TITLE</A></DL><p></DL>',
            )
        )
        children = tree["toolbar"][0]["children"]
        self.assertEqual([node["type"] for node in children], [1, 1, 3, 1])
        self.assertEqual(len({node["guid"] for node in children}), 4)


if __name__ == "__main__":
    unittest.main()
