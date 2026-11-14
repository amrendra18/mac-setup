import copy
import importlib.machinery
import importlib.util
import json
import math
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


reader = load_module("value_reader", "module_utils/browser_settings.py")
generator = load_module("value_generator", "scripts/render-browser-map")
exporter = load_module("value_exporter", "scripts/sync-example")
catalog = load_module("value_catalog", "module_utils/browser_settings_data.py")

VALUE_BANK = (
    None,
    False,
    True,
    -2147483649,
    -1,
    0,
    1,
    6,
    2147483648,
    0.0,
    1.5,
    math.inf,
    -math.inf,
    math.nan,
    "",
    "fixture",
    "prefix safe suffix",
    [],
    [True],
    [1],
    ["fixture"],
    [{}],
    {},
    {"fixture": True},
    {"fixture": {"nested": "fixture"}},
    {"fixture": [{"nested": "fixture"}]},
    {1: "fixture"},
)


def schema_nodes(schema, path=()):
    yield path, schema
    for key, child in schema.get("properties", {}).items():
        yield from schema_nodes(child, path + ("properties", key))
    if "items" in schema:
        yield from schema_nodes(schema["items"], path + ("items",))
    extra = schema.get("additionalProperties")
    if isinstance(extra, dict):
        yield from schema_nodes(extra, path + ("additionalProperties",))
    for index, child in enumerate(schema.get("anyOf", [])):
        yield from schema_nodes(child, path + ("anyOf", index))


def specimen(schema):
    if "enum" in schema:
        return copy.deepcopy(schema["enum"][0])
    if "anyOf" in schema:
        return specimen(schema["anyOf"][0])
    kind = schema.get("type")
    if kind == "boolean":
        return True
    if kind in ("integer", "number"):
        return schema.get("minimum", schema.get("maximum", 0))
    if kind == "string":
        pattern = schema.get("pattern", ".*")
        return next(
            value
            for value in (
                "fixture",
                "",
                "https://example.invalid/%20",
                "safe",
                "123",
                "FirefoxColor@mozilla.com",
            )
            if re.search(pattern, value) is not None
        )
    if kind == "array":
        return [specimen(schema["items"])]
    if kind == "object":
        result = {
            name: specimen(child)
            for name, child in schema.get("properties", {}).items()
        }
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            result["fixture_extra"] = specimen(extra)
        return result
    return None


def candidates(values):
    result = list(VALUE_BANK)
    if isinstance(values, list):
        result.extend(values)
    elif values["type"] == "browser":
        schema = values["schema"]
        result.append(specimen(schema))
        result.extend(schema.get("enum", []))
        for field in ("minimum", "maximum"):
            if field in schema:
                bound = schema[field]
                result.extend((bound - 1, bound, bound + 1))
        if schema.get("type") == "object":
            value = specimen(schema)
            result.append({**value, "fixture_extra": "fixture"})
            for name in schema.get("required", []):
                result.append({key: item for key, item in value.items() if key != name})
    elif values["type"] in ("int", "float"):
        for field in ("min", "max"):
            bound = values[field]
            result.extend((bound - 1, bound, bound + 1))
    return result


class BrowserValueValidationTests(unittest.TestCase):
    def assert_consumers(self, values, value, expected):
        for name, consumer in (
            ("reader", reader),
            ("generator", generator),
            ("exporter", exporter),
        ):
            with self.subTest(consumer=name):
                self.assertEqual(consumer.value_allowed(values, value), expected)

    def test_anyof_keeps_containing_constraints(self):
        cases = (
            ({"type": "integer", "anyOf": [{"type": "number"}]}, 1.5, False),
            (
                {"type": "integer", "minimum": 5, "anyOf": [{"type": "integer"}]},
                1,
                False,
            ),
            (
                {"type": "integer", "maximum": 10, "anyOf": [{"type": "integer"}]},
                11,
                False,
            ),
            (
                {"type": "integer", "enum": [6], "anyOf": [{"type": "integer"}]},
                5,
                False,
            ),
            (
                {"type": "string", "pattern": "safe", "anyOf": [{"type": "string"}]},
                "prefix safe suffix",
                True,
            ),
            (
                {"type": "string", "pattern": "safe", "anyOf": [{"type": "string"}]},
                "other",
                False,
            ),
            (
                {
                    "type": "object",
                    "properties": {"enabled": {"type": "boolean"}},
                    "required": ["enabled"],
                    "additionalProperties": False,
                    "anyOf": [{"type": "object"}],
                },
                {},
                False,
            ),
            (
                {
                    "type": "object",
                    "properties": {"enabled": {"type": "boolean"}},
                    "additionalProperties": False,
                    "anyOf": [{"type": "object"}],
                },
                {"enabled": 1},
                False,
            ),
            (
                {
                    "type": "object",
                    "additionalProperties": False,
                    "anyOf": [{"type": "object"}],
                },
                {"extra": True},
                False,
            ),
            (
                {
                    "type": "array",
                    "items": {"type": "boolean"},
                    "anyOf": [{"type": "array"}],
                },
                [1],
                False,
            ),
        )
        for schema, value, expected in cases:
            with self.subTest(schema=schema, value=value):
                self.assert_consumers(
                    {"type": "browser", "schema": schema}, value, expected
                )

    def test_bounded_anyof_metadata_and_capture(self):
        row = copy.deepcopy(catalog.BROWSER_SETTINGS["chrome.ai_mode_settings"])
        row["values"] = {
            "type": "browser",
            "schema": {
                "type": "integer",
                "minimum": 5,
                "maximum": 10,
                "anyOf": [{"type": "integer"}],
            },
        }
        row["browser"]["capture"] = {"class": "scalar"}
        row["browser"]["baseline"] = {"status": "proposed", "value": 6}
        self.assertEqual(generator.browser_problems(row), [])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = root / "profile"
            profile.mkdir()
            (root / "Last Version").write_text("158.0.8127.37")
            for value, expected in ((1, False), (6, True)):
                with self.subTest(value=value):
                    self.assert_consumers(row["values"], value, expected)
                    self.assertEqual(
                        reader.filter_value(row, value),
                        (expected, value if expected else None),
                    )
                    self.assertEqual(
                        exporter.browser_value_is_public(row, value), expected
                    )
                    (profile / "Preferences").write_text(
                        json.dumps({"omnibox": {"ai_mode_settings": value}})
                    )
                    result = reader.read_browser(
                        "chrome",
                        {"chrome.ai_mode_settings": row},
                        profile,
                        root,
                        [],
                        process_check=lambda family: None,
                    )
                    setting = result["settings"]["chrome.ai_mode_settings"]
                    self.assertEqual(
                        setting["state"], "captured" if expected else "masked"
                    )
                    self.assertEqual(setting.get("value"), value if expected else None)

    def test_schema_patterns_search_and_text_patterns_match_whole_value(self):
        for value, expected in (
            ("safe", True),
            ("prefix safe suffix", True),
            ("other", False),
        ):
            with self.subTest(value=value):
                self.assert_consumers(
                    {
                        "type": "browser",
                        "schema": {"type": "string", "pattern": "safe"},
                    },
                    value,
                    expected,
                )
                self.assert_consumers(
                    {"type": "text", "pattern": "safe"}, value, value == "safe"
                )

    def test_additional_properties_default_and_explicit_rules(self):
        base = {"type": "object", "properties": {"enabled": {"type": "boolean"}}}
        cases = (
            (base, {"enabled": True, "extra": "fixture"}, True),
            ({**base, "additionalProperties": True}, {"extra": [1]}, True),
            ({**base, "additionalProperties": False}, {"extra": "fixture"}, False),
            ({**base, "additionalProperties": {"type": "integer"}}, {"extra": 1}, True),
            (
                {**base, "additionalProperties": {"type": "integer"}},
                {"extra": True},
                False,
            ),
            (base, {"enabled": 1}, False),
            (base, {1: "fixture"}, False),
        )
        for schema, value, expected in cases:
            with self.subTest(schema=schema, value=value):
                self.assert_consumers(
                    {"type": "browser", "schema": schema}, value, expected
                )

    def test_strict_types_and_finite_numbers(self):
        cases = (
            ({"type": "boolean"}, 1, False),
            ({"type": "integer"}, True, False),
            ({"type": "integer"}, 1.0, False),
            ({"type": "number"}, True, False),
            ({"type": "number"}, 1.5, True),
            ({"type": "integer", "enum": [1]}, True, False),
            ({"type": "number", "enum": [1]}, 1.0, False),
        )
        for schema, value, expected in cases:
            with self.subTest(schema=schema, value=value):
                self.assert_consumers(
                    {"type": "browser", "schema": schema}, value, expected
                )
        for value in (math.inf, -math.inf, math.nan):
            for schema in (
                {"type": "number"},
                {"enum": [value]},
                {"anyOf": [{"type": "number"}]},
            ):
                with self.subTest(schema=schema, value=value):
                    self.assert_consumers(
                        {"type": "browser", "schema": schema}, value, False
                    )

    def test_every_shipped_value_shape_and_recursive_schema_node_agrees(self):
        shapes = {}
        edges = set()
        for setting_id, row in catalog.BROWSER_SETTINGS.items():
            values = row["values"]
            shapes.setdefault(
                json.dumps(values, sort_keys=True), (setting_id, (), values)
            )
            if isinstance(values, dict) and values.get("type") == "browser":
                for path, schema in schema_nodes(values["schema"]):
                    edges.update(part for part in path if isinstance(part, str))
                    shape = {"type": "browser", "schema": schema}
                    shapes.setdefault(
                        json.dumps(shape, sort_keys=True), (setting_id, path, shape)
                    )
        self.assertTrue(
            {"properties", "items", "additionalProperties", "anyOf"} <= edges
        )
        for setting_id, path, values in shapes.values():
            for value in candidates(values):
                with self.subTest(
                    setting=setting_id, path=path, values=values, value=value
                ):
                    self.assertEqual(
                        reader.value_allowed(values, value),
                        generator.value_allowed(values, value),
                    )
                    self.assertEqual(
                        reader.value_allowed(values, value),
                        exporter.value_allowed(values, value),
                    )

    def test_privacy_filtering_stays_separate_from_shape_validation(self):
        for setting_id, row in catalog.BROWSER_SETTINGS.items():
            for value in candidates(row["values"]):
                with self.subTest(setting=setting_id, value=value):
                    allowed, public_value = reader.filter_value(row, value)
                    self.assertEqual(
                        exporter.browser_value_is_public(row, value),
                        allowed and not isinstance(value, (dict, list)),
                    )
                    if (
                        isinstance(value, (dict, list))
                        and not (
                            row["browser"]["capture"]["class"] == "addon_ids"
                            and value == []
                        )
                        or row["browser"]["capture"]["class"] == "mask"
                    ):
                        self.assertEqual((allowed, public_value), (False, None))

    def test_valid_structured_values_stay_masked(self):
        cases = (
            (
                "chrome.auto_launch_protocols_from_origins",
                [
                    {
                        "protocol": "fixture",
                        "allowed_origins": ["https://example.invalid"],
                    }
                ],
            ),
            ("firefox.preferences", {"fixture.preference": True}),
        )
        for setting_id, value in cases:
            with self.subTest(setting=setting_id):
                row = catalog.BROWSER_SETTINGS[setting_id]
                self.assert_consumers(row["values"], value, True)
                self.assertEqual(reader.filter_value(row, value), (False, None))
                self.assertFalse(exporter.browser_value_is_public(row, value))


if __name__ == "__main__":
    unittest.main()
