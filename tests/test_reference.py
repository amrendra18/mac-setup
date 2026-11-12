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
ENTRY_COUNT = 288
SCRIPT = ROOT / "scripts/render-template"
_loader = importlib.machinery.SourceFileLoader("render_template", str(SCRIPT))
template = importlib.util.module_from_spec(
    importlib.util.spec_from_loader(_loader.name, _loader)
)
_loader.exec_module(template)

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
TAKES_EFFECT = {"live", "dock", "finder", "systemuiserver", "relaunch", "logout"}
NEEDS = {"full_disk_access", "click"}
WRITE_TYPES = {"string", "int", "float", "bool", "array", "dict"}
WRITE_FIELDS = {"domain", "key", "type", "host", "map", "dict_mode"}
VALUE_FIELDS = {
    "bool": {"type"},
    "int": {"type", "min", "max"},
    "float": {"type", "min", "max"},
    "text": {"type", "pattern"},
    "bundle_ids": {"type"},
    "paths": {"type"},
    "list": {"type", "choices"},
    "catalog_ids": {"type"},
}
SETTING_ID = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+")
VERSION = re.compile(r"\d+(\.\d+)*")
NOT_FOR_USERS = re.compile(
    r"phase D|prototype|\bmain\b|\bthe mini\b|\bthe VM\b|operator|\.md\b|\.json\b"
    r"|proposal|proposed kind",
    re.I,
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
            "values: must be a list of choices or {type: bool, int, float, text, bundle_ids, paths, list or catalog_ids}"
        ]
    kind = values["type"]
    if set(values) != VALUE_FIELDS[kind]:
        return [f"values: type {kind} takes exactly {sorted(VALUE_FIELDS[kind])}"]
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
        for name in sorted(set(command) - {"read", "parse", "map", "set"})
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
    for name in sorted(set(entry) - FIELDS - route_fields - {"notes"}):
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
            f"needs {needs!r} must list each of {sorted(NEEDS)} at most once"
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
    if kind in ("paths", "list") and route != "finder_sidebar":
        problems.append(f"values: {{type: {kind}}} is only for route finder_sidebar")
    if route == "by_hand":
        if not _line(entry["where"]):
            problems.append("where must name the System Settings page")
        link = entry["link"]
        if not isinstance(link, str) or not (
            link == "" or link.startswith("x-apple.systempreferences:")
        ):
            problems.append('link must be an x-apple.systempreferences: URL or ""')
    if route == "defaults":
        writes = entry["writes"]
        if not isinstance(writes, list) or not writes:
            problems.append("writes must list one or more writes")
        else:
            for write in writes:
                problems += _write_problems(write, entry["scope"])
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
            notes="takes_effect not measured",
        ),
        "alpha.software": fixture(
            scope="machine", route="apps", values={"type": "catalog_ids"}
        ),
        "alpha.sleep": fixture(
            scope="machine",
            route="command",
            values={"type": "int", "min": 0, "max": 180},
            command=COMMAND_INT,
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
            needs=["full_disk_access"],
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
    ("needs word", lambda e: e.update(needs=["admin"]), "needs ['admin']"),
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
        "list off finder_sidebar",
        lambda e: e.update(values={"type": "list", "choices": ["trash"]}),
        "{type: list} is only for route finder_sidebar",
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
            "  #   Values: a list of any of recents, trash",
            "  #   Values: a list of app ids from apps/macos.yml",
            '  #   Values: a list of folder paths that start with / or "~", other than / itself, in order',
            "  #   macOS default: bottom",
            "  #   Takes effect: when the Dock restarts",
            "  #   Needs: Full Disk Access for the terminal app",
            "  #   Proven on: macOS 27.2.1 (2026-11-12)",
            "  #   Proven on: not yet",
            "  #   Warning: can lock you out of this Mac; it needs allow_lockout_risk: true",
            "  #   Note: takes_effect not measured",
            "#   Where: System Settings > Privacy & Security > Screen Recording",
            "#   Not yet because: The research found no store that reads back.",
            "#   Scope: machine setting, needs admin",
        ]:
            self.assertIn(expected + "\n", text)

    def test_uncommented_examples_load_as_a_profile(self):
        text = template.render(write_reference(self.work / "settings", FIXTURES))
        profile = yaml.safe_load(
            re.sub(r"^  # ([a-z_]+\.[a-z_]+: )", r"  \1", text, flags=re.M)
        )
        self.assertEqual(profile["default_settings"]["beta.edge"], "bottom")
        self.assertIs(profile["machine_settings"]["alpha.remote"], False)
        self.assertEqual(
            len(profile["default_settings"]) + len(profile["machine_settings"]), 14
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

    def test_no_parse_matches_an_exit_line_alone(self):
        for _, setting_id, entry in self.entries:
            if entry["route"] == "command" and not entry["command"]["parse"].startswith(
                "^exit"
            ):
                with self.subTest(setting_id):
                    self.assertIsNone(
                        re.search(entry["command"]["parse"], "exit: 0\n", re.MULTILINE)
                    )

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

    def test_reference_has_every_researched_setting(self):
        routes = Counter(entry.get("route") for _, _, entry in self.entries)
        print(
            "\nreference entries per route: "
            + ", ".join(f"{route} {routes[route]}" for route in ROUTES)
            + f"; total {len(self.entries)}"
        )
        self.assertEqual(len(self.entries), ENTRY_COUNT)

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
