import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "firefox_startup", ROOT / "verify/helpers/firefox.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def packet(value):
    data = json.dumps(value).encode()
    return str(len(data)).encode("ascii") + b":" + data


class FirefoxConnection:
    def __init__(self, process, browser_version, refusal):
        self.process = process
        self.browser_version = browser_version
        self.refusal = refusal
        self.commands = []
        self.stream = io.BytesIO(packet({"marionetteProtocol": 3}))

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.stream.close()

    def makefile(self, mode):
        if mode != "rb":
            raise AssertionError("The protocol must read bytes")
        return self.stream

    def sendall(self, frame):
        length, payload = frame.split(b":", 1)
        if len(payload) != int(length):
            raise AssertionError("The command frame must contain its whole payload")
        kind, sequence, name, parameters = json.loads(payload)
        if kind != 0:
            raise AssertionError("The client must send a Marionette command")
        self.commands.append((name, parameters))
        error = None
        result = {}
        if name == "WebDriver:NewSession":
            result = {"capabilities": {"browserVersion": self.browser_version}}
        elif name == "Marionette:SetContext":
            if parameters != {"value": "chrome"}:
                raise AssertionError("This fixture expects a chrome context request")
            if self.refusal or not self.process.system_access:
                error = {
                    "error": "unsupported operation",
                    "message": "SYNTHETIC_PRIVATE_REMOTE_MESSAGE",
                    "stacktrace": "SYNTHETIC_PRIVATE_REMOTE_TRACE",
                    "data": {"path": "SYNTHETIC_PRIVATE_REMOTE_PATH"},
                }
        elif name == "Marionette:Quit":
            if parameters != {"flags": ["eAttemptQuit"]}:
                raise AssertionError("The browser must request a clean quit")
        else:
            raise AssertionError("Unexpected Marionette command")
        position = self.stream.tell()
        self.stream.seek(0, io.SEEK_END)
        self.stream.write(packet([1, sequence, error, result]))
        self.stream.seek(position)


class FirefoxProcess:
    def __init__(self, argv, environment, engine_version):
        self.argv = argv
        self.environment = environment
        self.returncode = None
        self.waits = []
        self.terminated = False
        self.killed = False
        self.profile = Path(argv[argv.index("-profile") + 1])
        (self.profile / "MarionetteActivePort").write_text("28281\n")
        (self.profile / "prefs.js").write_text('user_pref("artificial.test", true);\n')
        self.system_access = environment.get("MOZ_REMOTE_ALLOW_SYSTEM_ACCESS") == "1"
        if engine_version == "158.0.1":
            self.system_access |= "--remote-allow-system-access" in argv

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.waits.append(timeout)
        self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class FirefoxStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.processes = []
        self.connections = []
        self.engine_version = "160.0"
        self.browser_version = "160.0"
        self.refusal = False
        for replacement in (
            patch.object(probe, "closed"),
            patch.object(probe.subprocess, "Popen", side_effect=self.launch),
            patch.object(
                probe.subprocess,
                "run",
                side_effect=AssertionError("No system command may run in this test"),
            ),
            patch.object(probe.socket, "create_connection", side_effect=self.connect),
            patch.dict(
                os.environ,
                {
                    "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "0",
                    "XRE_PROFILE_PATH": "SYNTHETIC_PRIVATE_PROFILE",
                    "XRE_PROFILE_LOCAL_PATH": "SYNTHETIC_PRIVATE_LOCAL_PROFILE",
                    "FIREFOX_STARTUP_TEST_INHERITED": "keep-test-value",
                },
            ),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def launch(self, argv, *, env, stdout, stderr):
        self.assertEqual(stderr, probe.subprocess.STDOUT)
        self.assertFalse(stdout.closed)
        process = FirefoxProcess(list(argv), dict(env), self.engine_version)
        self.processes.append(process)
        return process

    def connect(self, address, timeout):
        self.assertEqual(address, ("127.0.0.1", 28281))
        self.assertGreater(timeout, 30000 / 1000)
        connection = FirefoxConnection(
            self.processes[-1], self.browser_version, self.refusal
        )
        self.connections.append(connection)
        return connection

    def test_chrome_access_across_the_command_line_flag_removal(self):
        for version in ("158.0.1", "159.0", "160.0"):
            with self.subTest(version=version):
                self.engine_version = self.browser_version = version
                with probe.browser(self.root / "chrome") as (client, profile):
                    probe.chrome(client)
                    self.assertTrue((profile / "user.js").is_file())
                process = self.processes[-1]
                self.assertEqual(process.waits, [30])
                self.assertFalse(process.terminated)
                self.assertFalse(process.killed)
                self.assertEqual(
                    self.connections[-1].commands,
                    [
                        ("WebDriver:NewSession", {"timeouts": {"script": 30000}}),
                        ("Marionette:SetContext", {"value": "chrome"}),
                        ("Marionette:Quit", {"flags": ["eAttemptQuit"]}),
                    ],
                )

    def test_observed_version_is_saved_for_each_disposable_child(self):
        for version in ("158.0.1", "159.0", "160.0"):
            with self.subTest(version=version):
                self.engine_version = self.browser_version = version
                with probe.browser(self.root / "version") as (client, profile):
                    self.assertEqual(client.browser_version, version)
                    runtime = profile.parent / "runtime.json"
                    self.assertEqual(
                        json.loads(runtime.read_text()), {"browser_version": version}
                    )

    def test_child_environment_and_data_roots_are_isolated(self):
        roots = []
        for _ in range(2):
            with probe.browser(self.root / "isolated", override=True) as (_, profile):
                roots.append(profile.parent)
                process = self.processes[-1]
                self.assertEqual(
                    process.argv,
                    [
                        probe.FIREFOX,
                        "-headless",
                        "-no-remote",
                        "-profile",
                        str(profile),
                        "--marionette",
                        "about:blank",
                    ],
                )
                environment = process.environment
                self.assertEqual(environment["MOZ_REMOTE_ALLOW_SYSTEM_ACCESS"], "1")
                self.assertEqual(
                    environment["MOZ_APP_DATA"], str(profile.parent / "app-data")
                )
                self.assertEqual(
                    environment["MOZ_LOCAL_APP_DATA"],
                    str(profile.parent / "local-data"),
                )
                self.assertTrue(Path(environment["MOZ_APP_DATA"]).is_dir())
                self.assertTrue(Path(environment["MOZ_LOCAL_APP_DATA"]).is_dir())
                self.assertNotIn("XRE_PROFILE_PATH", environment)
                self.assertNotIn("XRE_PROFILE_LOCAL_PATH", environment)
                self.assertEqual(
                    environment["FIREFOX_STARTUP_TEST_INHERITED"], "keep-test-value"
                )
                prefs = (profile / "user.js").read_text()
                self.assertIn('user_pref("marionette.port", 0);', prefs)
                self.assertIn(json.dumps(probe.USER_HOME), prefs)
                self.assertIn(
                    'user_pref("privacy.clearOnShutdown.history", false);', prefs
                )
        self.assertNotEqual(roots[0], roots[1])
        self.assertEqual(os.environ["MOZ_REMOTE_ALLOW_SYSTEM_ACCESS"], "0")
        self.assertEqual(os.environ["XRE_PROFILE_PATH"], "SYNTHETIC_PRIVATE_PROFILE")
        self.assertEqual(
            os.environ["XRE_PROFILE_LOCAL_PATH"], "SYNTHETIC_PRIVATE_LOCAL_PROFILE"
        )

    def test_refused_context_names_actual_version_without_remote_private_data(self):
        self.refusal = True
        for version in ("160.0", "161.2"):
            with self.subTest(version=version):
                self.engine_version = self.browser_version = version
                with self.assertRaises(probe.ProofError) as caught:
                    with probe.browser(self.root / "refused") as (client, _):
                        probe.chrome(client)
                message = str(caught.exception)
                self.assertIn("Marionette:SetContext", message)
                self.assertIn("on Firefox " + version, message)
                self.assertIn("unsupported operation", message)
                self.assertNotIn("SYNTHETIC_PRIVATE", message)

    def test_malformed_version_never_enters_diagnostics_or_runtime_evidence(self):
        self.refusal = True
        for malformed in (
            "160.0 SYNTHETIC_PRIVATE_VERSION",
            {"SYNTHETIC_PRIVATE_VERSION": "160.0"},
            "160.0\nSYNTHETIC_PRIVATE_VERSION",
            None,
            160,
        ):
            with self.subTest(version=malformed):
                self.browser_version = malformed
                with self.assertRaises(probe.ProofError) as caught:
                    with probe.browser(self.root / "malformed") as (client, profile):
                        self.assertEqual(client.browser_version, "unavailable")
                        probe.chrome(client)
                self.assertIn("Marionette:SetContext", str(caught.exception))
                self.assertIn("unsupported operation", str(caught.exception))
                self.assertNotIn("SYNTHETIC_PRIVATE", str(caught.exception))
                runtime = profile.parent / "runtime.json"
                self.assertEqual(
                    json.loads(runtime.read_text()), {"browser_version": "unavailable"}
                )
                self.assertNotIn("SYNTHETIC_PRIVATE", runtime.read_text())


if __name__ == "__main__":
    unittest.main()
