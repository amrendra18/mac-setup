import io
import json
import socket
import subprocess
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

from test_firefox_journey import probe


def packet(value):
    payload = json.dumps(value).encode()
    return str(len(payload)).encode() + b":" + payload


class FirefoxFailureTests(unittest.TestCase):
    def client(self, error=None):
        connection = Mock()
        stream = io.BytesIO(
            packet({"marionetteProtocol": 3})
            + packet([1, 1, None, {"capabilities": {"browserVersion": "160.0"}}])
            + (packet([1, 2, error, None]) if error else b"")
        )
        connection.makefile.return_value = stream
        return probe.Marionette(connection)

    def test_transport_timeout_names_each_script_check_without_private_values(self):
        for name in probe.SCRIPT_PROOFS:
            with self.subTest(check=name):
                client = self.client()
                client.stream = Mock()
                client.stream.read.side_effect = socket.timeout(
                    "SYNTHETIC_PRIVATE_PATH"
                )
                with self.assertRaises(probe.ProofError) as caught:
                    client.script(
                        "SYNTHETIC_PRIVATE_SCRIPT",
                        ["SYNTHETIC_PRIVATE_ARG"],
                        asynchronous=True,
                        proof=name,
                    )
                self.assertIn(name, str(caught.exception))
                self.assertIn("timeout", str(caught.exception))
                self.assertNotIn("SYNTHETIC_PRIVATE", str(caught.exception))

    def test_legacy_python_socket_timeout_remains_a_named_timeout(self):
        class LegacySocketTimeout(OSError):
            pass

        with patch.object(probe.socket, "timeout", LegacySocketTimeout):
            with self.assertRaises(probe.ProofError) as caught:
                with probe.proof_check("browser-session"):
                    raise LegacySocketTimeout("SYNTHETIC_PRIVATE_ADDRESS")
        self.assertIn("browser-session failed (timeout)", str(caught.exception))
        self.assertNotIn("SYNTHETIC_PRIVATE", str(caught.exception))

    def test_session_owns_script_timeout_and_script_payload_uses_supported_fields(self):
        client = self.client(
            {"error": "script timeout", "message": "SYNTHETIC_PRIVATE_URL"}
        )
        with self.assertRaises(probe.ProofError) as caught:
            client.script("return 1", asynchronous=True, proof="extension-installation")
        frames = [
            json.loads(call.args[0].split(b":", 1)[1])
            for call in client.connection.sendall.call_args_list
        ]
        self.assertEqual(frames[0][3], {"timeouts": {"script": 30000}})
        self.assertEqual(set(frames[1][3]), {"script", "args"})
        self.assertIn("extension-installation", str(caught.exception))
        self.assertIn("script timeout", str(caught.exception))
        self.assertNotIn("SYNTHETIC_PRIVATE", str(caught.exception))

    def test_entry_point_names_unexpected_failures_without_exception_values(self):
        for error in (
            OSError("SYNTHETIC_PRIVATE_PATH"),
            ValueError("SYNTHETIC_PRIVATE_VALUE"),
            KeyError("SYNTHETIC_PRIVATE_KEY"),
            subprocess.TimeoutExpired("SYNTHETIC_PRIVATE_COMMAND", 1),
        ):
            with self.subTest(error=type(error).__name__), patch.object(
                probe, "main", side_effect=error
            ), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(probe.run(), 1)
                self.assertIn("journey-command", stderr.getvalue())
                self.assertNotIn("SYNTHETIC_PRIVATE", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
