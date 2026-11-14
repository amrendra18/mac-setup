import json
import io
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_default_browser/library"))
sys.path.insert(0, str(ROOT / "roles/profile/filter_plugins"))
sys.path.insert(0, str(ROOT / "roles/profile/lookup_plugins"))

from profile import profile_mode
from test_macos_interactive import capture_controller

from macos_default_browser import (
    BrowserError,
    Session,
    SubprocessRunner,
    ensure_default,
    firefox_installed,
    read_defaults,
)


class FakeMac:
    def __init__(self, before=None, after=None, read_error=False, request_error=False):
        self.defaults = before or {"http": False, "https": False}
        self.after = after
        self.read_error = read_error
        self.request_error = request_error
        self.requests = 0
        self.calls = []
        self.handlers_path = None

    def read_handler_defaults(self):
        if self.handlers_path is None:
            raise BrowserError("artificial handler source is unavailable")
        return SubprocessRunner(
            handlers_path=self.handlers_path
        ).read_handler_defaults()

    def run(self, argv, timeout):
        self.calls.append((argv, timeout))
        if argv[-1] == "read":
            if self.read_error:
                return 1, "private-output", "private-error"
            return 0, json.dumps(self.defaults), ""
        self.requests += 1
        if self.after is not None:
            self.defaults = self.after
        if self.request_error:
            return 1, "private-output", "private-error"
        return 0, "requested", ""


class DefaultBrowser(unittest.TestCase):
    def run_on(self, mac, **kwargs):
        options = {
            "ask": True,
            "check_mode": False,
            "session": Session(501, 501, False),
            "installed": True,
            "interactive": True,
        }
        options.update(kwargs)
        return ensure_default(mac, **options)

    def test_controller_with_null_input_never_requests_for_console_account(self):
        with open(os.devnull) as source, patch("sys.stdin", source), patch(
            "sys.stdout", Mock(isatty=lambda: True)
        ), patch.dict(os.environ):
            self.assertFalse(sys.stdin.isatty())
            interactive = capture_controller()
            mode = profile_mode(["", "", ""], False, interactive)
            mac = FakeMac()
            result = self.run_on(mac, ask=mode["ask"], interactive=interactive)
        self.assertEqual(mode["why"], "unattended")
        self.assertFalse(result["requested"])
        self.assertEqual(mac.requests, 0)

    def test_interactive_controller_requests_once_and_existing_gates_still_hold(self):
        cases = (
            ({}, False, 501, 501, 1),
            ({"SSH_CONNECTION": "artificial"}, False, 501, 501, 0),
            ({"SSH_CONNECTION": ""}, False, 501, 501, 0),
            ({"SSH_CLIENT": ""}, False, 501, 501, 0),
            ({"SSH_TTY": ""}, False, 501, 501, 0),
            ({}, True, 501, 501, 0),
            ({}, False, 502, 501, 0),
            ({}, False, 0, 0, 0),
        )
        for environment, check, uid, console_uid, requests in cases:
            with self.subTest(environment=environment, check=check, uid=uid):
                with patch("sys.stdin", Mock(isatty=lambda: True)), patch(
                    "sys.stdout", Mock(isatty=lambda: True)
                ), patch.dict(os.environ, environment, clear=True), patch(
                    "macos_default_browser.os.geteuid", return_value=uid
                ), patch(
                    "macos_default_browser.os.stat"
                ) as stat:
                    stat.return_value.st_uid = console_uid
                    interactive = capture_controller()
                    mode = profile_mode(
                        [
                            environment.get(key, "")
                            for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
                        ],
                        check,
                        interactive,
                    )
                    mac = FakeMac()
                    result = self.run_on(
                        mac,
                        ask=mode["ask"],
                        check_mode=check,
                        session=Session.current(),
                        interactive=interactive,
                    )
                self.assertEqual(mac.requests, requests)
                self.assertEqual(result["requested"], bool(requests))

    def test_redirected_controller_output_never_requests(self):
        with patch("sys.stdin", Mock(isatty=lambda: True)), patch(
            "sys.stdout", io.StringIO()
        ), patch.dict(os.environ):
            interactive = capture_controller()
            mode = profile_mode(["", "", ""], False, interactive)
            mac = FakeMac()
            result = self.run_on(mac, ask=mode["ask"], interactive=interactive)
        self.assertFalse(result["requested"])
        self.assertEqual(mac.requests, 0)

    def test_missing_interactive_argument_blocks_even_when_ask_is_true(self):
        mac = FakeMac()
        result = ensure_default(mac, True, False, Session(501, 501, False), True)
        self.assertFalse(result["requested"])
        self.assertEqual(mac.requests, 0)

    def test_already_default_never_asks(self):
        mac = FakeMac(before={"http": True, "https": True})
        result = self.run_on(mac)
        self.assertFalse(result["changed"])
        self.assertEqual(result["status"], "already_default")
        self.assertEqual(mac.requests, 0)

    def test_partial_default_needs_confirmation(self):
        mac = FakeMac(before={"http": True, "https": False})
        result = self.run_on(mac, ask=False)
        self.assertEqual(result["status"], "manual")
        self.assertFalse(result["changed"])

    def test_ssh_check_mode_other_account_and_unattended_never_ask(self):
        cases = [
            {"session": Session(501, 501, True)},
            {"check_mode": True},
            {"session": Session(502, 501, False)},
            {"session": Session(0, 0, False)},
            {"session": Session(501, None, False)},
            {"ask": False},
            {"interactive": False},
        ]
        for options in cases:
            with self.subTest(options=options):
                mac = FakeMac()
                result = self.run_on(mac, **options)
                self.assertFalse(result["changed"])
                self.assertEqual(result["status"], "manual")
                self.assertIn("System Settings", " ".join(result["notes"]))
                self.assertEqual(mac.requests, 0)

    def test_confirmed_change_is_reported_and_second_run_changes_nothing(self):
        mac = FakeMac(after={"http": True, "https": True})
        result = self.run_on(mac)
        self.assertTrue(result["changed"])
        self.assertTrue(result["requested"])
        self.assertEqual(result["status"], "confirmed")
        self.assertFalse(self.run_on(mac)["changed"])
        self.assertEqual(mac.requests, 1)

    def test_denied_or_deferred_confirmation_reports_manual_step(self):
        mac = FakeMac()
        result = self.run_on(mac)
        self.assertFalse(result["changed"])
        self.assertTrue(result["requested"])
        self.assertEqual(result["status"], "manual")
        self.assertEqual(mac.requests, 1)
        self.assertLessEqual(len(mac.calls), 3)

    def test_missing_firefox_never_asks(self):
        mac = FakeMac()
        result = self.run_on(mac, installed=False)
        self.assertFalse(result["changed"])
        self.assertIn("Install Firefox", " ".join(result["notes"]))
        self.assertEqual(mac.requests, 0)

    def test_failed_read_redacts_private_command_output(self):
        mac = FakeMac(read_error=True)
        with self.assertRaises(BrowserError) as caught:
            self.run_on(mac)
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(mac.requests, 0)

    def test_failed_request_still_reads_confirmation_and_redacts_output(self):
        mac = FakeMac(request_error=True)
        result = self.run_on(mac)
        self.assertFalse(result["changed"])
        self.assertFalse(result["requested"])
        self.assertEqual(len(mac.calls), 3)
        self.assertNotIn("private", json.dumps(result))

    def test_successful_os_confirmation_is_authoritative_after_request_error(self):
        mac = FakeMac(request_error=True, after={"http": True, "https": True})
        self.assertTrue(self.run_on(mac)["changed"])

    def test_timeout_after_request_still_requires_both_observed_defaults(self):
        mac = FakeMac()
        run = mac.run

        def time_out_request(argv, timeout):
            if argv[-1] == "request":
                mac.requests += 1
                raise BrowserError("request timed out")
            return run(argv, timeout)

        mac.run = time_out_request
        result = self.run_on(mac)
        self.assertFalse(result["changed"])
        self.assertFalse(result["requested"])
        self.assertNotIn("asked macOS", " ".join(result["notes"]))
        self.assertIn("could not confirm delivery", " ".join(result["notes"]))
        self.assertEqual(mac.requests, 1)

    def test_unacknowledged_request_never_claims_macos_was_asked(self):
        for response in ((0, "", ""), (1, "private-output", "private-error")):
            with self.subTest(response=response):
                mac = FakeMac()
                run = mac.run
                mac.run = lambda argv, timeout: (
                    response if argv[-1] == "request" else run(argv, timeout)
                )
                result = self.run_on(mac)
                self.assertFalse(result["requested"])
                self.assertFalse(result["changed"])
                self.assertNotIn("asked macOS", " ".join(result["notes"]))
                self.assertNotIn("private", json.dumps(result))

    def test_request_launch_failure_has_no_acknowledged_request(self):
        mac = FakeMac()
        run = mac.run

        def launch_failure(argv, timeout):
            if argv[-1] == "request":
                with patch(
                    "macos_default_browser.subprocess.run",
                    side_effect=FileNotFoundError("private-missing-osascript"),
                ):
                    return SubprocessRunner().run(argv, timeout)
            return run(argv, timeout)

        mac.run = launch_failure
        result = self.run_on(mac)
        self.assertFalse(result["requested"])
        self.assertFalse(result["changed"])
        self.assertNotIn("asked macOS", " ".join(result["notes"]))
        self.assertNotIn("private", json.dumps(result))

    def test_unsupported_macos_never_reports_a_confirmation_request(self):
        mac = FakeMac()
        run = mac.run
        mac.run = lambda argv, timeout: (
            (0, "unsupported", "") if argv[-1] == "request" else run(argv, timeout)
        )
        result = self.run_on(mac)
        self.assertFalse(result["changed"])
        self.assertFalse(result["requested"])
        self.assertEqual(result["status"], "manual")

    def test_bad_read_shape_is_refused(self):
        for output in ("private-output", "[]", "{}", '{"http": 1, "https": true}'):
            with self.subTest(output=output):
                mac = FakeMac()
                mac.run = lambda argv, timeout: (0, output, "private-error")
                with self.assertRaises(BrowserError) as caught:
                    read_defaults(mac)
                self.assertNotIn("private", str(caught.exception))

    def test_each_subprocess_has_a_timeout_and_uses_only_static_arguments(self):
        mac = FakeMac()
        self.run_on(mac)
        for argv, timeout in mac.calls:
            self.assertEqual(argv[:4], ["/usr/bin/osascript", "-l", "JavaScript", "-e"])
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 40)
            self.assertIn(argv[-1], ["read", "request"])


class HandlerFallbackTests(unittest.TestCase):
    run_on = DefaultBrowser.run_on

    def fixture(self, folder, http="org.mozilla.firefox", https="org.mozilla.firefox"):
        path = Path(folder) / "com.apple.launchservices.secure.plist"
        value = {
            "LSHandlers": [
                {"LSHandlerURLScheme": "http", "LSHandlerRoleAll": http},
                {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": https},
                {
                    "LSHandlerURLScheme": "private-scheme",
                    "LSHandlerRoleAll": "private-application",
                },
                {"LSHandlerContentType": "private-type", "private-key": "private-data"},
            ],
            "private-metadata": "private-value",
        }
        path.write_bytes(plistlib.dumps(value))
        mac = FakeMac(read_error=True)
        mac.handlers_path = path
        return mac, path, value

    def test_xml_and_binary_sources_report_only_both_firefox(self):
        for format in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            for http, https, expected in (
                ("org.mozilla.firefox", "org.mozilla.firefox", True),
                ("com.google.chrome", "com.google.chrome", False),
                ("org.mozilla.firefox", "com.google.chrome", False),
                ("com.google.chrome", "org.mozilla.firefox", False),
            ):
                with self.subTest(format=format, http=http, https=https):
                    with tempfile.TemporaryDirectory() as folder:
                        mac, path, value = self.fixture(folder, http, https)
                        path.write_bytes(plistlib.dumps(value, fmt=format))
                        self.assertIs(read_defaults(mac), expected)
                        self.assertEqual(mac.requests, 0)

    def test_valid_api_result_does_not_read_fallback(self):
        for expected in (False, True):
            with self.subTest(expected=expected):
                mac = FakeMac(before={"http": expected, "https": expected})
                self.assertIs(read_defaults(mac), expected)

    def test_malformed_api_result_or_api_exception_uses_fallback(self):
        for response in (
            (1, "private-output", "private-error"),
            (0, "private-output", "private-error"),
            (0, "[]", ""),
            (0, "{}", ""),
            (0, '{"http": 1, "https": true}', ""),
            BrowserError("private-command-failure"),
        ):
            with self.subTest(response=response):
                with tempfile.TemporaryDirectory() as folder:
                    mac, _, _ = self.fixture(folder)

                    def api_failure(argv, timeout):
                        if isinstance(response, BrowserError):
                            raise response
                        return response

                    mac.run = api_failure
                    self.assertIs(read_defaults(mac), True)

    def test_ssh_and_check_mode_never_request_with_fallback(self):
        for http, https, expected_status in (
            ("org.mozilla.firefox", "org.mozilla.firefox", "already_default"),
            ("com.google.chrome", "com.google.chrome", "manual"),
            ("org.mozilla.firefox", "com.google.chrome", "manual"),
        ):
            for options in (
                {"session": Session(501, 501, True)},
                {"check_mode": True},
                {"session": Session(502, 501, False)},
                {"ask": False},
            ):
                with self.subTest(http=http, https=https, options=options):
                    with tempfile.TemporaryDirectory() as folder:
                        mac, _, _ = self.fixture(folder, http, https)
                        result = self.run_on(mac, **options)
                        self.assertEqual(result["status"], expected_status)
                        self.assertFalse(result["changed"])
                        self.assertFalse(result["requested"])
                        self.assertEqual(mac.requests, 0)
                        self.assertNotIn("private", json.dumps(result))

    def test_fallback_confirmation_requires_both_handlers_after_one_request(self):
        for https, expected in (
            ("org.mozilla.firefox", True),
            ("com.google.chrome", False),
        ):
            with self.subTest(https=https):
                with tempfile.TemporaryDirectory() as folder:
                    mac, path, value = self.fixture(
                        folder, "com.google.chrome", "com.google.chrome"
                    )
                    run = mac.run

                    def request(argv, timeout):
                        if argv[-1] == "request":
                            value["LSHandlers"][0][
                                "LSHandlerRoleAll"
                            ] = "org.mozilla.firefox"
                            value["LSHandlers"][1]["LSHandlerRoleAll"] = https
                            path.write_bytes(plistlib.dumps(value))
                        return run(argv, timeout)

                    mac.run = request
                    result = self.run_on(mac)
                    self.assertIs(result["changed"], expected)
                    self.assertTrue(result["requested"])
                    self.assertEqual(mac.requests, 1)
                    self.assertNotIn("private", json.dumps(result))

    def test_missing_malformed_incomplete_and_duplicate_sources_fail_closed(self):
        values = (
            [],
            {},
            {"LSHandlers": "private-data"},
            {"LSHandlers": []},
            {"LSHandlers": ["private-data"]},
            {"LSHandlers": [{"LSHandlerURLScheme": "http"}]},
            {
                "LSHandlers": [
                    {"LSHandlerURLScheme": "http", "LSHandlerRoleAll": True},
                    {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "private-app"},
                ]
            },
        )
        with tempfile.TemporaryDirectory() as folder:
            mac, path, value = self.fixture(folder)
            values += (
                {"LSHandlers": value["LSHandlers"][:1]},
                {"LSHandlers": value["LSHandlers"] + value["LSHandlers"][:1]},
            )
            for value in values:
                with self.subTest(value=value):
                    path.write_bytes(plistlib.dumps(value))
                    with self.assertRaises(BrowserError) as caught:
                        self.run_on(mac)
                    self.assertNotIn("private", str(caught.exception))
                    self.assertEqual(mac.requests, 0)
            for malformed in (b"private-invalid-plist", b"<?xml private-malformed"):
                path.write_bytes(malformed)
                with self.assertRaises(BrowserError) as caught:
                    self.run_on(mac)
                self.assertNotIn("private", str(caught.exception))
            path.unlink()
            with self.assertRaises(BrowserError) as caught:
                self.run_on(mac)
            self.assertNotIn(str(path), str(caught.exception))
            self.assertEqual(mac.requests, 0)


class SessionTests(unittest.TestCase):
    def test_empty_ssh_variable_also_blocks_prompt(self):
        with patch.dict(os.environ, {"SSH_CONNECTION": ""}, clear=True), patch(
            "macos_default_browser.os.geteuid", return_value=501
        ), patch("macos_default_browser.os.stat") as stat:
            stat.return_value.st_uid = 501
            self.assertFalse(Session.current().can_prompt)

    def test_no_console_owner_blocks_prompt(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "macos_default_browser.os.geteuid", return_value=501
        ), patch("macos_default_browser.os.stat", side_effect=OSError):
            self.assertFalse(Session.current().can_prompt)


class RunnerTests(unittest.TestCase):
    def test_fallback_uses_running_accounts_home_with_read_only_file_access(self):
        with tempfile.TemporaryDirectory() as folder:
            path = (
                Path(folder)
                / "Library/Preferences/com.apple.LaunchServices"
                / "com.apple.launchservices.secure.plist"
            )
            path.parent.mkdir(parents=True)
            path.write_bytes(
                plistlib.dumps(
                    {
                        "LSHandlers": [
                            {
                                "LSHandlerURLScheme": scheme,
                                "LSHandlerRoleAll": "org.mozilla.firefox",
                            }
                            for scheme in ("http", "https")
                        ]
                    }
                )
            )
            before = path.read_bytes()
            with patch("macos_default_browser.pwd.getpwuid") as account, patch.dict(
                os.environ, {"HOME": "/private-artificial-home"}
            ):
                account.return_value.pw_dir = folder
                self.assertIs(SubprocessRunner().read_handler_defaults(), True)
            self.assertEqual(path.read_bytes(), before)

    def test_unreadable_fallback_redacts_error_details(self):
        with patch("builtins.open", side_effect=PermissionError("private-path")):
            with self.assertRaises(BrowserError) as caught:
                SubprocessRunner(
                    handlers_path="artificial.plist"
                ).read_handler_defaults()
        self.assertNotIn("private", str(caught.exception))

    def test_invalid_command_output_is_redacted(self):
        failure = UnicodeDecodeError("utf-8", b"private-output\xff", 14, 15, "bad byte")
        with patch("macos_default_browser.subprocess.run", side_effect=failure):
            with self.assertRaises(BrowserError) as caught:
                SubprocessRunner().run(["static-command"], timeout=1)
            self.assertNotIn("private", str(caught.exception))

    def test_timeout_kills_the_child_and_redacts_its_output(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "wait.py"
            executable.write_text(
                "import time\nprint('private-output', flush=True)\ntime.sleep(5)\n"
            )
            with self.assertRaises(BrowserError) as caught:
                SubprocessRunner().run([sys.executable, str(executable)], timeout=0.05)
            self.assertNotIn("private", str(caught.exception))


class InstallationTests(unittest.TestCase):
    def test_only_the_release_firefox_bundle_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / "Firefox.app"
            contents = app / "Contents"
            contents.mkdir(parents=True)
            info = contents / "Info.plist"
            with patch("macos_default_browser.FIREFOX", str(app)):
                self.assertFalse(firefox_installed())
                for value in (
                    {"CFBundleIdentifier": "org.mozilla.firefox"},
                    {"CFBundleIdentifier": "org.mozilla.nightly"},
                    [],
                ):
                    info.write_bytes(plistlib.dumps(value))
                    self.assertEqual(
                        firefox_installed(),
                        value == {"CFBundleIdentifier": "org.mozilla.firefox"},
                    )
                info.write_bytes(b"private-output")
                self.assertFalse(firefox_installed())


if __name__ == "__main__":
    unittest.main()
