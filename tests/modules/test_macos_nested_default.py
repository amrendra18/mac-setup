import plistlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_defaults/library"))

from macos_nested_default import NestedError, ensure_nested

DEFAULTS = "/usr/bin/defaults"
DOMAIN = "com.example.hotkeys"
KEY = "Keys"


def fragment(text):
    return plistlib.loads(f'<plist version="1.0">{text}</plist>'.encode())


class FakeDefaults:
    def __init__(self, domains=None, store_as_text=False):
        self.domains = domains or {}
        self.store_as_text = store_as_text
        self.argvs = []

    def run(self, argv):
        self.argvs.append(list(argv))
        args = argv[1:]
        host = args[0] == "-currentHost"
        verb, domain, *rest = args[1:] if host else args
        values = self.domains.setdefault((host, domain), {})
        if verb == "export":
            return 0, plistlib.dumps(values), b""
        if verb == "delete":
            del values[rest[0]]
            return 0, b"", b""
        key, flag, *items = rest
        parse = str if self.store_as_text else fragment
        if flag == "-array":
            values[key] = [parse(item) for item in items]
        else:
            pairs = dict(zip(items[::2], map(parse, items[1::2])))
            values[key] = {**values[key], **pairs} if flag == "-dict-add" else pairs
        return 0, b"", b""

    def writes(self):
        return [argv for argv in self.argvs if "export" not in argv]


def ensure(tool, value, state="present", dict_mode="add", check_mode=False, host=""):
    return ensure_nested(tool, DOMAIN, host, KEY, value, state, dict_mode, check_mode)


OFF = {"enabled": False}
UP = {
    "enabled": True,
    "value": {"parameters": [65535, 126, 8650752], "type": "standard"},
}


class NestedDefault(unittest.TestCase):
    def test_add_writes_only_the_listed_entries(self):
        tool = FakeDefaults({(False, DOMAIN): {KEY: {"15": OFF, "32": OFF}}})
        result = ensure(tool, {"32": UP})
        self.assertTrue(result["changed"])
        [write] = tool.writes()
        self.assertEqual(write[:6], [DEFAULTS, "write", DOMAIN, KEY, "-dict-add", "32"])
        self.assertEqual(tool.domains[(False, DOMAIN)][KEY], {"15": OFF, "32": UP})
        self.assertFalse(ensure(tool, {"32": UP})["changed"])

    def test_add_to_a_missing_key_writes_a_new_dictionary(self):
        tool = FakeDefaults()
        self.assertTrue(ensure(tool, {"32": UP, "36": OFF})["changed"])
        [write] = tool.writes()
        self.assertEqual(write[4], "-dict")
        self.assertEqual(tool.domains[(False, DOMAIN)][KEY], {"32": UP, "36": OFF})

    def test_an_empty_add_changes_nothing(self):
        tool = FakeDefaults()
        self.assertEqual(ensure(tool, {}), {"changed": False})
        self.assertEqual(tool.writes(), [])

    def test_an_array_is_replaced_in_order(self):
        first = {"InputSourceKind": "Keyboard Layout", "KeyboardLayout ID": 2}
        second = {"Bundle ID": "com.example.palette", "InputSourceKind": "Other"}
        tool = FakeDefaults({(False, DOMAIN): {KEY: [second, first]}})
        self.assertTrue(ensure(tool, [first, second], dict_mode="replace")["changed"])
        self.assertEqual(tool.domains[(False, DOMAIN)][KEY], [first, second])
        self.assertEqual(tool.writes()[0][4], "-array")
        self.assertFalse(ensure(tool, [first, second], dict_mode="replace")["changed"])

    def test_true_and_one_are_different_values(self):
        tool = FakeDefaults({(False, DOMAIN): {KEY: {"32": {"enabled": 1}}}})
        self.assertTrue(ensure(tool, {"32": {"enabled": True}})["changed"])
        self.assertIs(tool.domains[(False, DOMAIN)][KEY]["32"]["enabled"], True)

    def test_check_mode_reports_a_diff_and_writes_nothing(self):
        tool = FakeDefaults({(False, DOMAIN): {KEY: {"32": OFF, "15": OFF}}})
        result = ensure(tool, {"32": UP}, check_mode=True)
        self.assertTrue(result["changed"])
        self.assertEqual(tool.writes(), [])
        self.assertIn('"enabled": false', result["diff"]["before"])
        self.assertIn("8650752", result["diff"]["after"])
        self.assertNotIn('"15"', result["diff"]["before"])

    def test_a_write_that_does_not_read_back_fails(self):
        tool = FakeDefaults(store_as_text=True)
        with self.assertRaisesRegex(NestedError, "after the write it holds"):
            ensure(tool, {"32": UP})

    def test_absent_deletes_the_key_once(self):
        tool = FakeDefaults({(False, DOMAIN): {KEY: [1], "Other": 2}})
        self.assertTrue(ensure(tool, None, state="absent")["changed"])
        self.assertEqual(tool.domains[(False, DOMAIN)], {"Other": 2})
        self.assertFalse(ensure(tool, None, state="absent")["changed"])
        self.assertEqual(tool.writes(), [[DEFAULTS, "delete", DOMAIN, KEY]])

    def test_add_into_a_key_that_is_not_a_dictionary_fails(self):
        tool = FakeDefaults({(False, DOMAIN): {KEY: [1, 2]}})
        with self.assertRaisesRegex(NestedError, "holds a list, not a dictionary"):
            ensure(tool, {"32": UP})
        self.assertEqual(tool.writes(), [])

    def test_add_needs_a_dictionary(self):
        with self.assertRaisesRegex(NestedError, "^value must be a dictionary$"):
            ensure(FakeDefaults(), [UP])

    def test_current_host_reads_and_writes_the_host_domain(self):
        tool = FakeDefaults()
        ensure(tool, {"32": UP}, host="currentHost")
        self.assertEqual(
            [argv[:3] for argv in tool.argvs],
            [
                [DEFAULTS, "-currentHost", "export"],
                [DEFAULTS, "-currentHost", "write"],
                [DEFAULTS, "-currentHost", "export"],
            ],
        )
        self.assertEqual(tool.domains[(True, DOMAIN)][KEY], {"32": UP})


if __name__ == "__main__":
    unittest.main()
