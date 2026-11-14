import datetime
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ENTRY_COUNT = 1242
SCRIPT = ROOT / "scripts/render-template"
_loader = importlib.machinery.SourceFileLoader("render_template", str(SCRIPT))
template = importlib.util.module_from_spec(
    importlib.util.spec_from_loader(_loader.name, _loader)
)
_loader.exec_module(template)
_browser_loader = importlib.machinery.SourceFileLoader(
    "render_browser_map", str(ROOT / "scripts/render-browser-map")
)
browser_map = importlib.util.module_from_spec(
    importlib.util.spec_from_loader(_browser_loader.name, _browser_loader)
)
_browser_loader.exec_module(browser_map)

FIELDS = {
    "summary",
    "scope",
    "route",
    "values",
    "macos_default",
    "takes_effect",
    "needs",
    "lockout_risk",
    "where",
    "proven_on",
    "not_yet_reason",
}
ROUTE_FIELDS = {"defaults": {"writes"}, "command": {"command"}, "by_hand": {"link"}}
ROUTES = (
    "defaults",
    "command",
    "dock_apps",
    "wallpaper",
    "finder_sidebar",
    "apps",
    "by_hand",
    "not_yet",
)
SCOPES = {"user", "machine"}
TAKES_EFFECT = set(template.TAKES_EFFECT)
PERMISSIONS_FILE = ROOT / template.SETTINGS / "permissions/table.yml"
PERMISSIONS = yaml.safe_load(PERMISSIONS_FILE.read_text())
PERMISSION_FIELDS = {
    "name",
    "test",
    "fallback",
    "only_when_listed",
    "ask",
    "where",
    "link",
    "tick",
    "temporary",
}
SKIPPABLE_ROUTES = ("command", "finder_sidebar")
NEEDS = set(PERMISSIONS) | {"click"}
WRITE_TYPES = {"string", "int", "float", "bool", "array", "dict"}
WRITE_FIELDS = {"domain", "key", "type", "host", "map", "dict_mode"}
VALUE_FIELDS = {
    "bool": {"type"},
    "browser": {"type", "schema"},
    "int": {"type", "min", "max"},
    "float": {"type", "min", "max"},
    "text": {"type", "pattern"},
    "bundle_ids": {"type"},
    "paths": {"type"},
    "list": {"type", "choices"},
    "catalog_ids": {"type"},
    "hotkeys": {"type", "actions"},
}
OPTIONAL_VALUE_FIELDS = {"list": {"min"}}
SETTING_WORD = re.compile(r"[a-z][a-z0-9_]*")
SETTING_ID = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+")
VERSION = re.compile(r"\d+(\.\d+)*")
NOT_FOR_USERS = re.compile(
    r"phase D|prototype|\bmain\b|\bthe mini\b|\bthe VM\b|operator|\.md\b|\.json\b"
    r"|proposal|proposed kind",
    re.I,
)
DOC_COUNTS = {
    "README.md": (
        r"mac-setup knows (?P<total>\d+) macOS settings\. A run sets (?P<run>\d+) of them, "
        r"(?P<by_hand>\d+) need your click, and (?P<not_yet>\d+) it cannot set yet\.",
        r"lists all (?P<total>\d+) settings in four sections",
    ),
    "docs/how-it-works.md": (
        r"The reference holds (?P<total>\d+) entries",
        r"Of the (?P<total>\d+) entries, (?P<run>\d+) are set by a run, (?P<by_hand>\d+) "
        r"are on route `by_hand`, and (?P<not_yet>\d+) are on route `not_yet`",
    ),
}
PROVEN_DOC = "README.md"
PROVEN_COUNT = re.compile(
    r"(?P<count>\d+) settings are proven on macOS (?P<version>\d+(\.\d+)*)"
)


def _line(value):
    return isinstance(value, str) and value.strip() != "" and "\n" not in value


def _argv(value):
    return (
        isinstance(value, list)
        and value != []
        and all(isinstance(arg, str) for arg in value)
    )


def _argv_list(value):
    return isinstance(value, list) and value != [] and all(map(_argv, value))


def _number(value):
    return type(value) in (int, float)


def _values_problems(values):
    if isinstance(values, list):
        if not values or any(isinstance(choice, (list, dict)) for choice in values):
            return ["values: a list must hold one or more plain choices"]
        if len(set(map(repr, values))) != len(values):
            return ["values: a choice is listed twice"]
        return []
    if not isinstance(values, dict) or values.get("type") not in VALUE_FIELDS:
        return [
            "values: must be a list of choices or {type: bool, int, float, text, bundle_ids, paths, list, catalog_ids or hotkeys}"
        ]
    kind = values["type"]
    optional = OPTIONAL_VALUE_FIELDS.get(kind, set())
    if not VALUE_FIELDS[kind] <= set(values) <= VALUE_FIELDS[kind] | optional:
        return [
            f"values: type {kind} takes {sorted(VALUE_FIELDS[kind])}"
            + (f" and may take {sorted(optional)}" if optional else "")
        ]
    if kind == "browser":
        return browser_map.schema_problems(values["schema"])
    if kind == "int" and not (
        type(values["min"]) is int
        and type(values["max"]) is int
        and values["min"] <= values["max"]
    ):
        return ["values: int needs whole numbers with min <= max"]
    if kind == "float" and not (
        _number(values["min"])
        and _number(values["max"])
        and values["min"] <= values["max"]
    ):
        return ["values: float needs numbers with min <= max"]
    if kind == "list":
        choices = values["choices"]
        if (
            not isinstance(choices, list)
            or not choices
            or not all(_line(choice) for choice in choices)
            or len(set(choices)) != len(choices)
        ):
            return ["values: list needs choices, a list of different words"]
        if "min" in values and not (
            type(values["min"]) is int and 1 <= values["min"] <= len(choices)
        ):
            return ["values: list min must be a whole number from 1 to the choices"]
    if kind == "hotkeys":
        actions = values["actions"]
        if (
            not isinstance(actions, dict)
            or not actions
            or not all(SETTING_WORD.fullmatch(str(name)) for name in actions)
            or not all(type(number) is int for number in actions.values())
            or len(set(actions.values())) != len(actions)
        ):
            return [
                "values: hotkeys needs actions, a mapping of words to different numbers"
            ]
    if kind == "text":
        try:
            re.compile(values["pattern"])
        except (re.error, TypeError):
            return ["values: pattern is not a regular expression"]
    return []


def _write_problems(write, scope):
    if not isinstance(write, dict):
        return ["writes: each item must be a mapping"]
    problems = [
        f"writes: unknown field {name}" for name in sorted(set(write) - WRITE_FIELDS)
    ]
    if not _line(write.get("domain")) or not _line(write.get("key")):
        problems.append("writes: domain and key must be text")
    elif scope == "machine" and not write["domain"].startswith("/"):
        problems.append(
            f"writes: machine domain {write['domain']} must be a path that starts with /"
        )
    if write.get("type") not in WRITE_TYPES:
        problems.append(f"writes: type must be one of {sorted(WRITE_TYPES)}")
    if write.get("host", "currentHost") != "currentHost":
        problems.append("writes: host must be currentHost")
    if not isinstance(write.get("map", {}), dict):
        problems.append("writes: map must be a mapping")
    if "dict_mode" in write and (
        write["dict_mode"] not in ("add", "replace") or write.get("type") != "dict"
    ):
        problems.append("writes: dict_mode is add or replace, for type dict only")
    return problems


def _command_problems(command, values):
    if not isinstance(command, dict) or not {"read", "parse", "set"} <= set(command):
        return ["command: needs read, parse and set"]
    problems = [
        f"command: unknown field {name}"
        for name in sorted(set(command) - {"read", "parse", "map", "refuse", "set"})
    ]
    if not _argv(command["read"]):
        problems.append("command: read must be one argv list")
    try:
        if set(re.compile(command["parse"]).groupindex) != {"value"}:
            problems.append('command: parse needs exactly one named group "value"')
    except (re.error, TypeError):
        problems.append("command: parse is not a regular expression")
    if not isinstance(command.get("map", {}), dict):
        problems.append("command: map must be a mapping")
    refuse = command.get("refuse", {})
    if not isinstance(refuse, dict) or not all(
        isinstance(reason, str) and reason for reason in refuse.values()
    ):
        problems.append("command: refuse maps each pattern to a reason")
    else:
        try:
            list(map(re.compile, refuse))
        except (re.error, TypeError):
            problems.append("command: a refuse pattern is not a regular expression")
    if isinstance(values, list) or values.get("type") == "bool":
        choices = values if isinstance(values, list) else [True, False]
        steps = command["set"]
        if not isinstance(steps, dict) or set(map(repr, steps)) != set(
            map(repr, choices)
        ):
            problems.append("command: set needs one argv list per allowed value")
        elif not all(map(_argv_list, steps.values())):
            problems.append("command: each set value must be a list of argv lists")
    elif not _argv_list(command["set"]) or not any(
        "{value}" in argv for argv in command["set"]
    ):
        problems.append('command: set must be argv lists with a "{value}" item')
    return problems


def _date(value):
    if isinstance(value, datetime.date):
        return True
    try:
        datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def entry_problems(entry):
    if not isinstance(entry, dict):
        return ["must be a mapping of fields"]
    route = entry.get("route")
    if route not in ROUTES:
        return [f"route {route!r} must be one of {', '.join(ROUTES)}"]
    route_fields = ROUTE_FIELDS.get(route, set())
    problems = [
        f"missing field {name}" for name in sorted((FIELDS | route_fields) - set(entry))
    ]
    for name in sorted(set(entry) - FIELDS - route_fields - {"notes", "browser"}):
        owner = [r for r, names in ROUTE_FIELDS.items() if name in names]
        problems.append(
            f"field {name} is only for route {owner[0]}"
            if owner
            else f"unknown field {name}"
        )
    if problems:
        return problems
    if not _line(entry["summary"]):
        problems.append("summary must be one line of text")
    if entry["scope"] not in SCOPES:
        problems.append(f"scope {entry['scope']!r} must be user or machine")
    if entry["takes_effect"] not in TAKES_EFFECT:
        problems.append(
            f"takes_effect {entry['takes_effect']!r} must be one of {sorted(TAKES_EFFECT)}"
        )
    needs = entry["needs"]
    if (
        not isinstance(needs, list)
        or not set(needs) <= NEEDS
        or len(set(needs)) != len(needs)
    ):
        problems.append(
            f"needs {needs!r} must name rows of the permissions table or click, "
            "each at most once"
        )
    elif set(needs) - {"click"} and route not in SKIPPABLE_ROUTES:
        problems.append(
            "needs: a permission is only for routes command and finder_sidebar, "
            "where a run skips the setting while the permission is off"
        )
    elif any(
        PERMISSIONS[need]["only_when_listed"] for need in set(needs) - {"click"}
    ) and (entry["values"] != {"type": "paths"}):
        problems.append(
            "needs: a permission needed only when listed is for {type: paths} values"
        )
    if not isinstance(entry["lockout_risk"], bool):
        problems.append("lockout_risk must be true or false")
    if entry["macos_default"] is None:
        problems.append('macos_default must be the measured value or "not measured"')
    if not isinstance(entry["where"], str) or "\n" in entry["where"]:
        problems.append("where must be one line of text")
    if not isinstance(entry.get("notes", ""), str):
        problems.append("notes must be text")
    for name in ("notes", "not_yet_reason"):
        found = NOT_FOR_USERS.search(str(entry.get(name, "")))
        if found:
            problems.append(
                f"{name} must hold only facts a user needs, not {found.group()!r}"
            )
    proven = entry["proven_on"]
    if not isinstance(proven, dict) or not all(
        isinstance(version, str) and VERSION.fullmatch(version) and _date(day)
        for version, day in proven.items()
    ):
        problems.append(
            f"proven_on {proven!r} must map macOS versions such as 27.2.1 to dates"
        )
    reason = entry["not_yet_reason"]
    if route == "not_yet" and not _line(reason):
        problems.append("not_yet_reason must say why the setting is not yet supported")
    if route != "not_yet" and reason != "":
        problems.append('not_yet_reason must be "" outside route not_yet')
    values = entry["values"]
    problems += _values_problems(values)
    kind = values.get("type") if isinstance(values, dict) else None
    if (kind == "bundle_ids") != (route == "dock_apps"):
        problems.append(
            "values: {type: bundle_ids} is for route dock_apps, and only there"
        )
    if (kind == "catalog_ids") != (route == "apps"):
        problems.append("values: {type: catalog_ids} is for route apps, and only there")
    if kind == "paths" and route != "finder_sidebar":
        problems.append("values: {type: paths} is only for route finder_sidebar")
    if kind == "list" and route == "defaults":
        if [(w.get("type"), sorted(w.get("map", {}))) for w in entry["writes"]] != [
            ("array", sorted(values["choices"]))
        ]:
            problems.append(
                "values: {type: list} with route defaults needs one array write "
                "whose map has one item per choice"
            )
    elif kind == "list" and route != "finder_sidebar":
        problems.append(
            "values: {type: list} is only for routes finder_sidebar and defaults"
        )
    if kind == "hotkeys" and (
        route != "defaults"
        or not all(
            (w.get("type"), w.get("dict_mode")) == ("dict", "add") and "map" not in w
            for w in entry["writes"]
        )
    ):
        problems.append(
            "values: {type: hotkeys} needs route defaults with dict writes in "
            "dict_mode add and no map"
        )
    if route == "by_hand":
        if not _line(entry["where"]):
            problems.append("where must name the System Settings page")
        link = entry["link"]
        if not isinstance(link, str) or not link.startswith(
            "x-apple.systempreferences:"
        ):
            problems.append("link must be an x-apple.systempreferences: URL")
    if route == "defaults":
        writes = entry["writes"]
        if not isinstance(writes, list) or not writes:
            problems.append("writes must list one or more writes")
        else:
            for write in writes:
                problems += _write_problems(write, entry["scope"])
                if isinstance(values, list) and isinstance(write.get("map"), dict):
                    problems += [
                        f"writes: map key {choice!r} is not one of the values"
                        for choice in write["map"]
                        if choice not in values
                    ]
    if "browser" in entry:
        problems += browser_map.browser_problems(entry)
    elif kind == "browser":
        problems.append("browser values need browser metadata")
    if route == "command" and not problems:
        problems += _command_problems(entry["command"], values)
    return problems


def reference_problems(entries):
    problems, seen = [], {}
    for file_name, setting_id, entry in entries:
        if not isinstance(setting_id, str) or not SETTING_ID.fullmatch(setting_id):
            problems.append(f"{file_name}: {setting_id!r}: id must look like area.name")
        if setting_id in seen:
            problems.append(f"{file_name}: {setting_id}: also in {seen[setting_id]}")
        seen.setdefault(setting_id, file_name)
        problems += [
            f"{file_name}: {setting_id}: {problem}" for problem in entry_problems(entry)
        ]
    return problems


def doc_count_problems(entries, docs):
    routes = Counter(entry["route"] for _, _, entry in entries)
    counts = {
        "total": len(entries),
        "run": len(entries) - routes["by_hand"] - routes["not_yet"],
        "by_hand": routes["by_hand"],
        "not_yet": routes["not_yet"],
    }
    text = {name: " ".join(docs[name].split()) for name in docs}
    problems = []
    for file_name, patterns in DOC_COUNTS.items():
        for pattern in patterns:
            found = re.search(pattern, text[file_name])
            if not found:
                problems.append(f"{file_name}: no sentence matches {pattern!r}")
                continue
            problems += [
                f'{file_name}: "{found.group()}": {name} is {said}, '
                f"the reference has {counts[name]}"
                for name, said in found.groupdict().items()
                if int(said) != counts[name]
            ]
    proven = Counter(
        version for _, _, entry in entries for version in entry["proven_on"]
    )
    said = {
        found["version"]: found for found in PROVEN_COUNT.finditer(text[PROVEN_DOC])
    }
    for version in sorted(proven.keys() | said.keys()):
        if version not in said:
            problems.append(
                f"{PROVEN_DOC}: no sentence says how many settings are proven on "
                f"macOS {version}; the reference has {proven[version]}"
            )
        elif int(said[version]["count"]) != proven[version]:
            problems.append(
                f'{PROVEN_DOC}: "{said[version].group()}": proven is '
                f'{said[version]["count"]}, the reference has {proven[version]}'
            )
    return problems


def write_reference(folder, files):
    folder.mkdir(parents=True)
    for name, entries in files.items():
        (folder / name).write_text(yaml.safe_dump(entries, sort_keys=False))
    return folder


def fixture(**fields):
    base = {
        "summary": "A made-up setting for the reference test.",
        "scope": "user",
        "route": "defaults",
        "values": {"type": "bool"},
        "macos_default": "not measured",
        "writes": [{"domain": "com.example.test", "key": "Flag", "type": "bool"}],
        "takes_effect": "live",
        "needs": [],
        "lockout_risk": False,
        "where": "System Settings > Example > Flag",
        "proven_on": {},
        "not_yet_reason": "",
    }
    entry = {**base, **fields}
    if entry["route"] != "defaults" and "writes" not in fields:
        del entry["writes"]
    return entry


COMMAND_BOOL = {
    "read": ["/usr/bin/example", "-get"],
    "parse": r"state:\s+(?P<value>\w+)",
    "map": {True: "on", False: "off"},
    "set": {
        True: [["/usr/bin/example", "-set", "on"]],
        False: [["/usr/bin/example", "-set", "off"]],
    },
}
COMMAND_INT = {
    "read": ["/usr/bin/example", "-g"],
    "parse": r"sleep\s+(?P<value>\d+)",
    "set": [["/usr/bin/example", "-a", "sleep", "{value}"]],
}

FIXTURES = {
    "beta.yml": {
        "beta.flag": fixture(macos_default=True, proven_on={"27.2.1": "2026-11-12"}),
        "beta.edge": fixture(
            values=["left", "bottom", "off"],
            macos_default="bottom",
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "Edge",
                    "type": "string",
                    "host": "currentHost",
                }
            ],
        ),
        "beta.size": fixture(
            values={"type": "int", "min": 16, "max": 128}, takes_effect="dock"
        ),
        "beta.ratio": fixture(
            values={"type": "float", "min": 0.5, "max": 2.0}, macos_default=1.5
        ),
        "beta.apps": fixture(
            route="dock_apps", values={"type": "bundle_ids"}, takes_effect="dock"
        ),
        "beta.backdrop": fixture(route="wallpaper", values=["black"], needs=["click"]),
        "beta.places": fixture(route="finder_sidebar", values={"type": "paths"}),
        "beta.hidden": fixture(
            route="finder_sidebar",
            values={"type": "list", "choices": ["recents", "trash"]},
        ),
        "beta.switch": fixture(
            values=["on", "off"],
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "Switch",
                    "type": "int",
                    "map": {"on": 1, "off": None},
                }
            ],
        ),
        "beta.keys": fixture(
            values={"type": "hotkeys", "actions": {"open_panel": 7}},
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "Keys",
                    "type": "dict",
                    "dict_mode": "add",
                }
            ],
            takes_effect="logout",
        ),
        "beta.layouts": fixture(
            values={"type": "list", "choices": ["first", "second"], "min": 1},
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "Layouts",
                    "type": "array",
                    "map": {"first": {"Id": 1}, "second": {"Id": 2}},
                }
            ],
        ),
        "beta.pair": fixture(
            values=["off", "left", "right"],
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "Side",
                    "type": "int",
                    "map": {"left": 1, "right": 2},
                }
            ],
        ),
        "beta.privacy_page": fixture(
            route="by_hand",
            where="System Settings > Privacy & Security > Screen Recording",
            link="x-apple.systempreferences:com.example.screen",
            needs=["click"],
        ),
    },
    "alpha.yml": {
        "alpha.remote": fixture(
            scope="machine",
            route="command",
            command=COMMAND_BOOL,
            lockout_risk=True,
            takes_effect="not measured",
            notes="Turning it off can end a remote session.",
        ),
        "alpha.software": fixture(
            scope="machine", route="apps", values={"type": "catalog_ids"}
        ),
        "alpha.sleep": fixture(
            scope="machine",
            route="command",
            values={"type": "int", "min": 0, "max": 180},
            command=COMMAND_INT,
            needs=["full_disk_access"],
        ),
        "alpha.label": fixture(
            scope="machine",
            values={"type": "text", "pattern": "[A-Za-z0-9-]{1,63}"},
            writes=[
                {
                    "domain": "/Library/Preferences/com.example.test",
                    "key": "Label",
                    "type": "string",
                }
            ],
        ),
        "alpha.message": fixture(
            scope="machine",
            values={"type": "text", "pattern": "^.{0,200}$"},
            writes=[
                {
                    "domain": "/Library/Preferences/com.example.test",
                    "key": "Message",
                    "type": "string",
                }
            ],
        ),
        "alpha.gesture": fixture(
            scope="machine",
            route="not_yet",
            not_yet_reason="The research found no store that reads back.",
        ),
    },
}

BROKEN = [
    ("missing summary", lambda e: e.pop("summary"), "missing field summary"),
    (
        "unknown field",
        lambda e: e.update(take_effect="dock"),
        "unknown field take_effect",
    ),
    ("scope word", lambda e: e.update(scope="system"), "scope 'system'"),
    ("route word", lambda e: e.update(route="osx_defaults"), "route 'osx_defaults'"),
    (
        "takes_effect word",
        lambda e: e.update(takes_effect="unknown"),
        "takes_effect 'unknown'",
    ),
    (
        "takes_effect not measured spelling",
        lambda e: e.update(takes_effect="not_measured"),
        "takes_effect 'not_measured'",
    ),
    (
        "needs names no permission",
        lambda e: e.update(needs=["screen_recording"]),
        "needs ['screen_recording'] must name rows of the permissions table",
    ),
    (
        "permission on a route that cannot skip it",
        lambda e: e.update(needs=["full_disk_access"]),
        "a permission is only for routes command and finder_sidebar",
    ),
    (
        "folder permission on a value that lists no folder",
        lambda e: (
            e.pop("writes"),
            e.update(route="finder_sidebar", needs=["desktop_folder"]),
        ),
        "needed only when listed is for {type: paths} values",
    ),
    (
        "lockout_risk text",
        lambda e: e.update(lockout_risk="no"),
        "lockout_risk must be",
    ),
    ("empty choices", lambda e: e.update(values=[]), "values: a list must"),
    (
        "int range",
        lambda e: e.update(values={"type": "int", "min": 5, "max": 1}),
        "values: int needs",
    ),
    (
        "old value shape",
        lambda e: e.update(values={"type": "boolean"}),
        "values: must be",
    ),
    (
        "list without choices",
        lambda e: (
            e.pop("writes"),
            e.update(route="finder_sidebar", values={"type": "list", "choices": []}),
        ),
        "values: list needs choices",
    ),
    (
        "bundle ids off dock_apps",
        lambda e: e.update(values={"type": "bundle_ids"}),
        "bundle_ids} is for route dock_apps",
    ),
    (
        "catalog ids off apps",
        lambda e: e.update(values={"type": "catalog_ids"}),
        "catalog_ids} is for route apps",
    ),
    (
        "paths off finder_sidebar",
        lambda e: e.update(values={"type": "paths"}),
        "{type: paths} is only for route finder_sidebar",
    ),
    (
        "list on defaults without an array write",
        lambda e: e.update(values={"type": "list", "choices": ["trash"]}),
        "{type: list} with route defaults needs one array write",
    ),
    (
        "list off its routes",
        lambda e: (
            e.pop("writes"),
            e.update(route="wallpaper", values={"type": "list", "choices": ["trash"]}),
        ),
        "{type: list} is only for routes finder_sidebar and defaults",
    ),
    (
        "list min",
        lambda e: e.update(
            values={"type": "list", "choices": ["a"], "min": 2},
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "L",
                    "type": "array",
                    "map": {"a": 1},
                }
            ],
        ),
        "values: list min must be",
    ),
    (
        "hotkeys without actions",
        lambda e: e.update(
            values={"type": "hotkeys", "actions": {}},
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "K",
                    "type": "dict",
                    "dict_mode": "add",
                }
            ],
        ),
        "values: hotkeys needs actions",
    ),
    (
        "hotkeys with a repeated number",
        lambda e: e.update(
            values={"type": "hotkeys", "actions": {"one": 7, "two": 7}},
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "K",
                    "type": "dict",
                    "dict_mode": "add",
                }
            ],
        ),
        "values: hotkeys needs actions",
    ),
    (
        "hotkeys on a plain write",
        lambda e: e.update(values={"type": "hotkeys", "actions": {"one": 7}}),
        "{type: hotkeys} needs route defaults with dict writes in dict_mode add",
    ),
    (
        "map key outside the values",
        lambda e: e.update(
            values=["on", "off"],
            writes=[
                {
                    "domain": "com.example.test",
                    "key": "Switch",
                    "type": "int",
                    "map": {"on": 1, "of": 0},
                }
            ],
        ),
        "writes: map key 'of' is not one of the values",
    ),
    (
        "writes on a command",
        lambda e: e.update(route="command", command=COMMAND_BOOL),
        "field writes is only for route defaults",
    ),
    (
        "command on defaults",
        lambda e: e.update(command=COMMAND_BOOL),
        "field command is only for route command",
    ),
    ("no writes", lambda e: e.update(writes=[]), "writes must list"),
    (
        "write type",
        lambda e: e["writes"][0].update(type="integer"),
        "writes: type must",
    ),
    (
        "machine domain",
        lambda e: e.update(scope="machine"),
        "machine domain com.example.test must be a path",
    ),
    (
        "not_yet with writes",
        lambda e: e.update(route="not_yet", not_yet_reason="Later."),
        "field writes is only for route defaults",
    ),
    (
        "not_yet without reason",
        lambda e: (e.pop("writes"), e.update(route="not_yet")),
        "not_yet_reason must say why",
    ),
    (
        "reason outside not_yet",
        lambda e: e.update(not_yet_reason="Later."),
        'not_yet_reason must be ""',
    ),
    (
        "proven_on key",
        lambda e: e.update(proven_on={"latest": "2026-11-12"}),
        "proven_on",
    ),
    (
        "proven_on day",
        lambda e: e.update(proven_on={"27.2.1": "yesterday"}),
        "proven_on",
    ),
    (
        "by_hand without link",
        lambda e: (e.pop("writes"), e.update(route="by_hand")),
        "missing field link",
    ),
    (
        "by_hand with an empty link",
        lambda e: (e.pop("writes"), e.update(route="by_hand", link="")),
        "link must be an x-apple.systempreferences: URL",
    ),
    ("no default", lambda e: e.update(macos_default=None), "macos_default must be"),
    (
        "note cites a research file",
        lambda e: e.update(notes="A defaults write is not the route (admin.md B28)."),
        "notes must hold only facts a user needs, not '.md'",
    ),
    (
        "reason names a person",
        lambda e: (
            e.pop("writes"),
            e.update(route="not_yet", not_yet_reason="The operator adds Spaces."),
        ),
        "not_yet_reason must hold only facts a user needs, not 'operator'",
    ),
    (
        "parse group",
        lambda e: (
            e.pop("writes"),
            e.update(route="command", command={**COMMAND_BOOL, "parse": r"(\w+)"}),
        ),
        'parse needs exactly one named group "value"',
    ),
    (
        "set per value",
        lambda e: (
            e.pop("writes"),
            e.update(
                route="command", command={**COMMAND_BOOL, "set": {True: [["/bin/x"]]}}
            ),
        ),
        "set needs one argv list per allowed value",
    ),
    (
        "set without value",
        lambda e: (
            e.pop("writes"),
            e.update(
                route="command",
                values={"type": "int", "min": 0, "max": 9},
                command=COMMAND_BOOL,
            ),
        ),
        'set must be argv lists with a "{value}" item',
    ),
    (
        "refuse without reason",
        lambda e: (
            e.pop("writes"),
            e.update(route="command", command={**COMMAND_BOOL, "refuse": {"^x": ""}}),
        ),
        "refuse maps each pattern to a reason",
    ),
]


PMSET = """AC Power:
 Sleep On Power Button 1
 powernap             1
 displaysleep         0
 womp                 1
 sleep                0
 autorestart          1
 disksleep            10
exit: 0
"""
RECORDED_READS = {
    "power.display_sleep_minutes": (PMSET, "0"),
    "power.system_sleep_minutes": (PMSET, "0"),
    "power.disk_sleep_minutes": (PMSET, "10"),
    "power.wake_for_network": (PMSET, "1"),
    "power.power_nap": (PMSET, "1"),
    "power.restart_after_power_failure": (PMSET, "1"),
    "power.low_power_mode": (PMSET, None),
    "sharing.screen_sharing": (
        'Bad request.\nCould not find service "com.apple.screensharing" in domain for system\nexit: 113\n',
        "113",
    ),
    "sharing.remote_apple_events": (
        'Bad request.\nCould not find service "com.apple.AEServer" in domain for system\nexit: 113\n',
        "113",
    ),
    "sharing.remote_login": (
        "system/com.openssh.sshd = {\n\tactive count = 0\n\tstate = not running\n}\nexit: 0\n",
        "0",
    ),
    "sharing.printer_sharing": (
        "_debug_logging=0\n_remote_admin=0\n_share_printers=0\n_user_cancel_any=0\nexit: 0\n",
        "0",
    ),
    "sharing.content_caching": ("0\nexit: 0\n", "0"),
    "computer.name": ("Example Mac mini\nexit: 0\n", "Example Mac mini"),
    "computer.local_hostname": ("example\nexit: 0\n", "example"),
    "time.zone": (
        "/var/db/timezone/zoneinfo/America/Example\nexit: 0\n",
        "America/Example",
    ),
    "security.firewall": ("Firewall is enabled. (State = 1)\nexit: 0\n", "1"),
    "security.firewall_block_all": (
        "Firewall has block all state set to disabled.\nexit: 0\n",
        "disabled",
    ),
    "security.stealth": ("Firewall stealth mode is off\nexit: 0\n", "off"),
    "security.firewall_allow_builtin_signed": (
        "Automatically allow built-in signed software ENABLED.\nAutomatically allow downloaded signed software ENABLED.\nexit: 0\n",
        "ENABLED",
    ),
    "security.firewall_allow_downloaded_signed": (
        "Automatically allow built-in signed software DISABLED.\nAutomatically allow downloaded signed software ENABLED.\nexit: 0\n",
        "ENABLED",
    ),
    "security.guest_enabled": ("0\nexit: 0\n", "0"),
    "security.guest_shared_folders": (
        "2026-11-11 14:38:25.200 sysadminctl[4583:109108] SMB guest access disabled.\nexit: 0\n",
        "disabled",
    ),
    "sound.output_volume": (
        "output volume:0, input volume:missing value, alert volume:100, output muted:true\nexit: 0\n",
        "0",
    ),
    "sound.alert_volume": (
        "output volume:63, input volume:missing value, alert volume:100, output muted:false\nexit: 0\n",
        "100",
    ),
    "sound.startup_sound": (
        "nvram: Error getting variable - 'StartupMute': (iokit/common) data was not found\nexit: 1\n",
        None,
    ),
}


def _listed(part):
    return re.findall(r"^ *# ([a-z_]+\.[a-z_]+)(?:: (.*))?$", part, re.M)


class FixtureReference(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)

    def entries(self, files, name="settings"):
        return template.load(write_reference(self.work / name, files))

    def test_good_fixtures_have_no_problems(self):
        self.assertEqual(reference_problems(self.entries(FIXTURES)), [])

    def test_each_broken_entry_is_named(self):
        for number, (name, mutate, expected) in enumerate(BROKEN):
            with self.subTest(name):
                entry = fixture()
                mutate(entry)
                files = {"one.yml": {"one.case": entry}}
                problems = reference_problems(self.entries(files, f"case{number}"))
                self.assertEqual(len(problems), 1, problems)
                self.assertRegex(
                    problems[0], "^one.yml: one.case: .*" + re.escape(expected)
                )

    def test_an_id_in_two_files_is_named(self):
        files = {"a.yml": {"x.same": fixture()}, "b.yml": {"x.same": fixture()}}
        self.assertEqual(
            reference_problems(self.entries(files)), ["b.yml: x.same: also in a.yml"]
        )

    def test_a_repeated_id_in_one_file_stops_the_load(self):
        folder = self.work / "settings"
        folder.mkdir()
        text = yaml.safe_dump({"x.same": fixture()})
        (folder / "a.yml").write_text(text + text)
        with self.assertRaisesRegex(yaml.YAMLError, "repeated key x.same"):
            template.load(folder)

    def test_template_lists_each_setting_in_its_section(self):
        text = template.render(write_reference(self.work / "settings", FIXTURES))
        self.assertTrue(text.startswith("# Generated by scripts/render-template"))
        self.assertIn("Do not edit this file.", text.splitlines()[0])
        self.assertEqual(
            yaml.safe_load(text), {"default_settings": None, "machine_settings": None}
        )
        account, rest = text.split("\nmachine_settings:\n")
        machine, rest = rest.split("\n# Click-only settings.")
        click_only, not_yet = rest.split("\n# Not yet supported.")
        self.assertEqual(
            _listed(account.split("\ndefault_settings:\n")[1]),
            [
                ("beta.apps", "[]"),
                ("beta.backdrop", "black"),
                ("beta.edge", "bottom"),
                ("beta.flag", "true"),
                ("beta.hidden", "[]"),
                ("beta.keys", "{}"),
                ("beta.layouts", "[first]"),
                ("beta.pair", "'off'"),
                ("beta.places", "[]"),
                ("beta.ratio", "1.5"),
                ("beta.size", "16"),
                ("beta.switch", "'on'"),
            ],
        )
        self.assertEqual(
            _listed(machine),
            [
                ("alpha.label", "unset"),
                ("alpha.message", "unset"),
                ("alpha.remote", "false"),
                ("alpha.sleep", "0"),
                ("alpha.software", "[]"),
            ],
        )
        self.assertEqual(_listed(click_only), [("beta.privacy_page", "")])
        self.assertEqual(_listed(not_yet), [("alpha.gesture", "")])
        for expected in [
            '  #   Values: left, bottom, "off"',
            '  #   Values: "on", "off"',
            "  #   Values: a list of different items from recents, trash",
            "  #   Values: a list of 1 or more different items from first, second",
            "  #   Values: a list of app ids from apps/macos.yml",
            '  #   Values: a list of folder paths that start with / or "~", other than / itself, in order',
            "  #   macOS default: bottom",
            "  #   Takes effect: when the Dock restarts",
            "  #   Needs: Full Disk Access",
            "  #   Proven on: macOS 27.2.1 (2026-11-12)",
            "  #   Proven on: not yet",
            "  #   Warning: can lock you out of this Mac; it needs allow_lockout_risk: true",
            "  #   Takes effect: not measured",
            "  #   Note: Turning it off can end a remote session.",
            "#   Where: System Settings > Privacy & Security > Screen Recording",
            "#   Not yet because: The research found no store that reads back.",
            "#   Scope: machine setting, needs admin",
        ]:
            self.assertIn(expected + "\n", text)
        self.assertIn(
            "  #   Values: a mapping from actions (open_panel) to true (on), false (off)",
            text,
        )

    def test_uncommented_examples_load_as_a_profile(self):
        text = template.render(write_reference(self.work / "settings", FIXTURES))
        profile = yaml.safe_load(
            re.sub(r"^  # ([a-z_]+\.[a-z_]+: )", r"  \1", text, flags=re.M)
        )
        self.assertEqual(profile["default_settings"]["beta.edge"], "bottom")
        self.assertIs(profile["machine_settings"]["alpha.remote"], False)
        self.assertEqual(
            len(profile["default_settings"]) + len(profile["machine_settings"]), 17
        )


class DocCounts(unittest.TestCase):
    ENTRIES = [
        ("a.yml", "a.one", {"route": "defaults", "proven_on": {"27.1": "2026-11-01"}}),
        ("a.yml", "a.two", {"route": "command", "proven_on": {}}),
        ("a.yml", "a.three", {"route": "by_hand", "proven_on": {}}),
        ("a.yml", "a.four", {"route": "not_yet", "proven_on": {}}),
    ]
    README = (
        "mac-setup knows 4 macOS settings. A run sets 2 of them, 1 need your\n"
        "click, and 1 it cannot set yet. 1 settings are proven on macOS 27.1:\n\n"
        "The template lists all 4 settings in four sections."
    )
    HOW = (
        "The reference holds 4 entries. Of the 4 entries, 2 are set by a run,\n"
        "1 are on route `by_hand`, and 1 are on route `not_yet`."
    )

    def problems(self, readme=README, how=HOW, entries=ENTRIES):
        return doc_count_problems(
            entries, {"README.md": readme, "docs/how-it-works.md": how}
        )

    def test_matching_counts_have_no_problems(self):
        self.assertEqual(self.problems(), [])

    def test_a_stale_count_names_the_file_sentence_and_both_numbers(self):
        readme = self.README.replace("1 need your", "3 need your")
        self.assertEqual(
            self.problems(readme=readme),
            [
                'README.md: "mac-setup knows 4 macOS settings. A run sets 2 of them, '
                '3 need your click, and 1 it cannot set yet.": by_hand is 3, '
                "the reference has 1"
            ],
        )

    def test_a_missing_sentence_is_named(self):
        how = self.HOW.replace("The reference holds 4 entries.", "")
        problems = self.problems(how=how)
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(
            problems[0].startswith("docs/how-it-works.md: no sentence matches ")
        )

    def test_a_stale_or_missing_proven_count_is_named(self):
        entries = [(name, key, dict(entry)) for name, key, entry in self.ENTRIES]
        entries[1][2]["proven_on"] = {"27.2": "2026-11-02"}
        readme = self.README.replace("1 settings are proven", "2 settings are proven")
        self.assertEqual(
            self.problems(readme=readme, entries=entries),
            [
                'README.md: "2 settings are proven on macOS 27.1": proven is 2, '
                "the reference has 1",
                "README.md: no sentence says how many settings are proven on "
                "macOS 27.2; the reference has 1",
            ],
        )


class Reference(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = template.load(ROOT / template.SETTINGS)

    def test_entries_follow_the_contract(self):
        problems = reference_problems(self.entries)
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_each_parse_finds_the_value_in_its_recorded_read(self):
        commands = {
            setting_id: entry["command"]
            for _, setting_id, entry in self.entries
            if entry["route"] == "command"
        }
        self.assertLessEqual(set(RECORDED_READS), set(commands))
        for setting_id, (text, expected) in RECORDED_READS.items():
            with self.subTest(setting_id):
                found = re.search(commands[setting_id]["parse"], text, re.MULTILINE)
                self.assertEqual(found and found.group("value"), expected)
                for refusal in commands[setting_id].get("refuse", {}):
                    self.assertIsNone(re.search(refusal, text, re.MULTILINE))

    def test_no_parse_or_refuse_matches_an_exit_line_alone(self):
        for _, setting_id, entry in self.entries:
            if entry["route"] != "command":
                continue
            command = entry["command"]
            if not command["parse"].startswith("^exit"):
                with self.subTest(setting_id):
                    self.assertIsNone(
                        re.search(command["parse"], "exit: 0\n", re.MULTILINE)
                    )
            for pattern in command.get("refuse", {}):
                with self.subTest(setting_id, refuse=pattern):
                    self.assertIsNone(re.search(pattern, "exit: 0\n", re.MULTILINE))

    def test_hotkey_numbers_are_in_one_entry_each(self):
        owners = Counter(
            (write["domain"], write["key"], number)
            for _, _, entry in self.entries
            if isinstance(entry["values"], dict)
            and entry["values"]["type"] == "hotkeys"
            for write in entry["writes"]
            for number in entry["values"]["actions"].values()
        )
        self.assertEqual([place for place, n in owners.items() if n > 1], [])
        self.assertGreater(len(owners), 0)

    def test_sidebar_ids_name_the_module_options(self):
        loader = importlib.machinery.SourceFileLoader(
            "macos_finder_sidebar",
            str(ROOT / "roles/macos_finder_sidebar/library/macos_finder_sidebar.py"),
        )
        module = importlib.util.module_from_spec(
            importlib.util.spec_from_loader(loader.name, loader)
        )
        loader.exec_module(module)
        options = yaml.safe_load(module.DOCUMENTATION)["options"]
        sidebar = {
            setting_id: entry
            for _, setting_id, entry in self.entries
            if entry["route"] == "finder_sidebar"
        }
        self.assertEqual(
            {setting_id.removeprefix("finder.sidebar_") for setting_id in sidebar},
            set(options) - {"helper"},
        )
        self.assertEqual(
            sidebar["finder.sidebar_hide"]["values"]["choices"], list(module.HIDE)
        )

    def test_each_permission_row_has_a_test_a_page_and_what_to_tick(self):
        problems = []
        for need, row in PERMISSIONS.items():
            if not (isinstance(row, dict) and set(row) == PERMISSION_FIELDS):
                problems.append(f"{need}: fields must be {sorted(PERMISSION_FIELDS)}")
                continue
            if not SETTING_WORD.fullmatch(need) or need == "click":
                problems.append(f"{need}: the id must be a word other than click")
            if row["ask"] not in ("pane", "dialog"):
                problems.append(f"{need}: ask must be pane or dialog")
            if not (_line(row["test"]) and row["test"].startswith("~/")):
                problems.append(
                    f"{need}: test must be a folder path that starts with ~/"
                )
            if row["fallback"] != "" and not (
                _line(row["fallback"]) and row["fallback"].startswith("/")
            ):
                problems.append(
                    f'{need}: fallback must be an absolute folder path or ""'
                )
            if not row["link"].startswith(
                "x-apple.systempreferences:com.apple.settings.PrivacySecurity"
            ):
                problems.append(f"{need}: link must open a Privacy & Security page")
            if not row["where"].startswith("System Settings > Privacy & Security > "):
                problems.append(f"{need}: where must name the Privacy & Security page")
            if "{app}" not in row["tick"] or not row["tick"][:1].islower():
                problems.append(f"{need}: tick must be a lowercase phrase with {{app}}")
            if not isinstance(row["only_when_listed"], bool):
                problems.append(f"{need}: only_when_listed must be true or false")
            if row["temporary"] not in (True, False) or (
                row["temporary"] and row["ask"] != "pane"
            ):
                problems.append(
                    f"{need}: temporary must be true or false, and true only for ask pane"
                )
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_reference_has_every_researched_setting(self):
        routes = Counter(entry.get("route") for _, _, entry in self.entries)
        print(
            "\nreference entries per route: "
            + ", ".join(f"{route} {routes[route]}" for route in ROUTES)
            + f"; total {len(self.entries)}"
        )
        self.assertEqual(len(self.entries), ENTRY_COUNT)

    def test_doc_counts_match_the_reference(self):
        docs = {name: (ROOT / name).read_text() for name in DOC_COUNTS}
        problems = doc_count_problems(self.entries, docs)
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_tracked_template_matches_the_reference(self):
        tracked = ROOT / template.TEMPLATE
        self.assertTrue(
            tracked.is_file(),
            f"{template.TEMPLATE} is missing; run scripts/render-template",
        )
        self.assertEqual(
            tracked.read_text(),
            template.render(ROOT / template.SETTINGS),
            f"{template.TEMPLATE} differs from the reference; run scripts/render-template",
        )


def ansible_inventory(inventory):
    return subprocess.run(
        [
            str(ROOT / ".local/venv/bin/ansible-inventory"),
            "-i",
            str(inventory),
            "--list",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg")},
    )


class Inventory(unittest.TestCase):
    def test_example_inventory_has_only_the_macs_group(self):
        run = ansible_inventory(ROOT / "inventory/example")
        self.assertEqual(run.returncode, 0, run.stderr)
        listed = json.loads(run.stdout)
        groups = set(listed) - {"_meta"} | set(listed["all"]["children"])
        self.assertEqual(groups, {"all", "ungrouped", "macs"})

    def test_a_repeated_key_stops_ansible(self):
        with tempfile.TemporaryDirectory() as folder:
            host = Path(folder) / "host_vars/example-mac"
            host.mkdir(parents=True)
            (Path(folder) / "hosts.yml").write_text(
                "macs:\n  hosts:\n    example-mac:\n"
            )
            (host / "profile.yml").write_text(
                "dock.position: left\ndock.position: right\n"
            )
            run = ansible_inventory(folder)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("duplicate mapping key 'dock.position'", run.stderr)


if __name__ == "__main__":
    unittest.main()
