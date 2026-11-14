import copy
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    loader = importlib.machinery.SourceFileLoader(
        name.replace("-", "_"), str(ROOT / "scripts" / name)
    )
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(loader.name, loader)
    )
    loader.exec_module(module)
    return module


def fixture():
    return {
        "scope": "machine",
        "route": "not_yet",
        "values": {"type": "bool"},
        "needs": [],
        "proven_on": {},
        "takes_effect": "relaunch",
        "browser": {
            "family": "firefox",
            "kind": "setting",
            "read": [
                {
                    "source": "firefox_policy",
                    "path": ["Example"],
                    "type": "bool",
                    "conversion": "identity",
                }
            ],
            "capture": {"class": "scalar"},
            "locked": {
                "source": "firefox_policy",
                "path": ["Example"],
                "level": "mandatory",
            },
            "unlocked": {"unsupported": "The policy has no editable form."},
            "baseline": {"status": "proposed", "value": True},
            "support": {
                "min": "158.0.1",
                "tested": "158.0.1",
                "source": "https://example.org/policies",
                "note": "Disk values only.",
            },
            "inventory": {
                "category": "policy",
                "policy": "Example",
                "leaf": "Example",
                "disposition": "mapped",
            },
        },
    }


def schema_members(schema, prefix):
    paths = set()
    for variant in schema.get("anyOf", []):
        paths.update(schema_members(variant, prefix))
    for name, child in schema.get("properties", {}).items():
        paths.update(schema_members(child, prefix + "." + name))
    if "items" in schema:
        paths.update(schema_members(schema["items"], prefix))
    extra = schema.get("additionalProperties", False)
    if isinstance(extra, dict):
        paths.update(schema_members(extra, prefix + ".*"))
    elif extra:
        paths.add(prefix + ".*")
    if not paths:
        paths.add(prefix)
    return {
        path
        for path in paths
        if not any(other.startswith(path + ".") for other in paths)
    }


class BrowserMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_script("render-browser-map")

    def test_generated_map_matches_reference(self):
        run = subprocess.run(
            [sys.executable, "-I", str(ROOT / "scripts/render-browser-map"), "--check"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_browser_reference_contract(self):
        self.assertEqual(self.tool.browser_problems(fixture()), [])
        changes = [
            lambda b: b.update(unknown=True),
            lambda b: b["read"][0].update(command=["anything"]),
            lambda b: b["read"][0].update(path=[]),
            lambda b: b["read"][0].update(type="secret"),
            lambda b: b["read"][0].update(type="text"),
            lambda b: b["read"][0].update(conversion="execute"),
            lambda b: b["read"][0].update(source="chrome_policy"),
            lambda b: b["capture"].update(**{"class": "enum"}),
            lambda b: b["locked"].update(script="anything"),
            lambda b: b["baseline"].update(status="accepted"),
            lambda b: b["baseline"].update(value="private"),
            lambda b: b["support"].update(max="1.0"),
            lambda b: b["inventory"].update(disposition="forgotten"),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                row = fixture()
                change(row["browser"])
                self.assertTrue(self.tool.browser_problems(row))
        row = fixture()
        row["values"] = {"type": "text", "pattern": ".*"}
        row["browser"]["read"][0]["type"] = "text"
        row["browser"]["baseline"].pop("value")
        self.assertTrue(self.tool.browser_problems(row))

    def test_support_accepts_open_ended_and_documented_removed_versions(self):
        row = fixture()
        self.assertEqual(self.tool.browser_problems(row), [])
        row["browser"]["support"].update(min="100.0", max="120.0", tested="158.0.1")
        self.assertEqual(self.tool.browser_problems(row), [])
        row["browser"]["support"].update(min="100.1.0", max="100", tested="158")
        self.assertEqual(self.tool.browser_problems(row), [])
        row["browser"]["support"].pop("max")
        row["browser"]["support"].update(min="160.0", tested="158.0.1")
        self.assertEqual(self.tool.browser_problems(row), [])

    def test_support_rejects_invalid_versions_bounds_and_fields(self):
        for field in ("min", "tested", "max"):
            for invalid in (None, True, 158, "", "158..1", "158.0-beta", "-1.0"):
                with self.subTest(field=field, invalid=invalid):
                    row = fixture()
                    row["browser"]["support"][field] = invalid
                    self.assertTrue(self.tool.browser_problems(row))
        for change in (
            lambda support: support.update(max="100.0"),
            lambda support: support.update(unknown="158.0.1"),
            lambda support: support.pop("tested"),
            lambda support: support.update(source="http://example.org/policies"),
            lambda support: support.update(note=""),
        ):
            with self.subTest(change=change):
                row = fixture()
                change(row["browser"]["support"])
                self.assertTrue(self.tool.browser_problems(row))

    def test_public_endpoints_require_reviewed_public_addresses(self):
        row = fixture()
        row["values"] = {"type": "text", "pattern": ".*"}
        row["browser"]["read"][0]["type"] = "text"
        row["browser"]["baseline"].pop("value")
        for endpoint in ("https://example.org/", "9.9.9.9"):
            row["browser"]["capture"] = {
                "class": "public_endpoint",
                "choices": [endpoint],
            }
            self.assertEqual(self.tool.browser_problems(row), [])
        for endpoint in (
            "https://",
            "https://localhost/",
            "https://127.0.0.1/",
            "192.168.1.1",
            "https://name:password@example.org/",
        ):
            row["browser"]["capture"] = {
                "class": "public_endpoint",
                "choices": [endpoint],
            }
            self.assertTrue(self.tool.browser_problems(row), endpoint)

    def test_read_alternatives_use_named_groups(self):
        row = fixture()
        first = row["browser"]["read"][0]
        first.update(
            path=["Preferences", "example.enabled"], alternative="preference_value"
        )
        second = copy.deepcopy(first)
        second["path"].append("Value")
        row["browser"]["read"].append(second)
        self.assertEqual(self.tool.browser_problems(row), [])
        for invalid in (
            "",
            "PreferenceValue",
            "preference-value",
            "two words",
            1,
            None,
        ):
            with self.subTest(invalid=invalid):
                first["alternative"] = invalid
                self.assertTrue(self.tool.browser_problems(row))
        first["alternative"] = "preference_value"
        first["unknown_alias"] = True
        self.assertTrue(self.tool.browser_problems(row))

    def test_structured_schemas_reject_unknown_keys_and_invalid_bounds(self):
        good = {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 0}},
            "additionalProperties": False,
        }
        self.assertEqual(self.tool.schema_problems(good), [])
        for schema in [
            dict(good, executable="bad"),
            {"type": "number", "minimum": 9, "maximum": 2},
            {"type": "array"},
            {"type": "object", "required": ["missing"], "properties": {}},
            {"type": "string", "pattern": "["},
        ]:
            self.assertTrue(self.tool.schema_problems(schema), schema)

    def test_browser_inventory_dispositions_complete(self):
        for family, policy_count, deprecated_count in [
            ("firefox", 121, 0),
            ("chrome", 499, 41),
        ]:
            with self.subTest(family=family):
                rows = yaml.safe_load(
                    (ROOT / "settings/macos" / f"{family}.yml").read_text()
                )
                inventory = json.loads(
                    (ROOT / "tests/browser" / f"{family}-inventory.json").read_text()
                )
                self.assertEqual(len(inventory["policies"]), policy_count)
                self.assertEqual(len(inventory["deprecated"]), deprecated_count)
                for name in ("policies", "deprecated", "leaves", "non_policy"):
                    self.assertEqual(
                        len(inventory[name]), len(set(inventory[name])), name
                    )
                ledgers = [
                    row["browser"]["inventory"]
                    for row in rows.values()
                    if row["browser"]["kind"] == "setting"
                ]
                self.assertEqual(
                    {row["policy"] for row in ledgers if row["category"] == "policy"},
                    set(inventory["policies"]),
                )
                self.assertEqual(
                    {
                        row["policy"]
                        for row in ledgers
                        if row["category"] == "deprecated"
                    },
                    set(inventory["deprecated"]),
                )
                leaves = [
                    row["leaf"]
                    for row in ledgers
                    if row["category"] in ("policy", "deprecated")
                ]
                self.assertEqual(Counter(leaves), Counter(inventory["leaves"]))
                self.assertEqual(
                    Counter(
                        row["leaf"]
                        for row in ledgers
                        if row["category"] == "non_policy"
                    ),
                    Counter(inventory["non_policy"]),
                )
                represented = {}
                for row in rows.values():
                    values = row["values"]
                    if isinstance(values, dict) and values.get("type") == "browser":
                        leaf = row["browser"]["inventory"]["leaf"]
                        members = schema_members(values["schema"], leaf) - {leaf}
                        if members:
                            represented[leaf] = sorted(members)
                self.assertEqual(represented, inventory["structured_members"])
                for members in inventory["structured_members"].values():
                    self.assertEqual(len(members), len(set(members)))
                for row in rows.values():
                    self.assertEqual(
                        self.tool.browser_problems(row), [], row["browser"]["inventory"]
                    )

    def test_lock_controls_default_true_without_fabricated_capture(self):
        for family in ("firefox", "chrome"):
            rows = yaml.safe_load(
                (ROOT / "settings/macos" / f"{family}.yml").read_text()
            )
            row = rows[f"{family}.lock"]
            self.assertEqual(row["scope"], "machine")
            self.assertEqual(row["values"], {"type": "bool"})
            self.assertEqual(row["browser"]["kind"], "lock_control")
            self.assertIs(row["browser"]["baseline"]["value"], True)
            self.assertEqual(row["browser"]["read"], [])

    def test_lock_false_selects_editable_or_unset(self):
        for family in ("firefox", "chrome"):
            rows = yaml.safe_load(
                (ROOT / "settings/macos" / f"{family}.yml").read_text()
            )
            for row in rows.values():
                plan = row["browser"]["unlocked"]
                self.assertTrue(
                    "unsupported" in plan
                    or plan["source"]
                    in ("none", "firefox_preferences", "chrome_preferences")
                    or plan.get("level") in ("recommended", "user"),
                    plan,
                )


if __name__ == "__main__":
    unittest.main()
