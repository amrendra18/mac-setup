import posixpath
import re
from collections import Counter

import yaml

UNSET = "unset"
ACCOUNT_NAME = re.compile(r"[a-z_][a-z0-9_.-]*")
BUNDLE_ID = re.compile(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
ACCOUNT_FIELDS = {"name", "full_name", "admin", "settings"}
PROFILE_KEYS = [
    "default_settings",
    "accounts",
    "machine_settings",
    "allow_lockout_risk",
    "account_passwords",
    "virustotal_api_key",
]
RUN_SWITCHES = {
    "by_hand_list",
    "by_hand_open",
    "macos_apps_downloads",
    "macos_apps_applications",
    "permissions_ls",
    "permissions_open",
}
SIDEBAR_PREFIX = "finder.sidebar_"
NEEDS_SETTING = {"finder.sidebar_favorites_only": "finder.sidebar_favorites"}
ANSIBLE_NAMES = {
    "group_names",
    "groups",
    "inventory_dir",
    "inventory_file",
    "inventory_hostname",
    "inventory_hostname_short",
    "playbook_dir",
    "profile_local_hostname",
}
HOTKEY_MODIFIERS = {
    "shift": 1 << 17,
    "control": 1 << 18,
    "option": 1 << 19,
    "command": 1 << 20,
}
NO_CHARACTER = 65535
FUNCTION_KEY = 1 << 23
PRINTABLE_KEY_CODES = {
    "a": 0,
    "b": 11,
    "c": 8,
    "d": 2,
    "e": 14,
    "f": 3,
    "g": 5,
    "h": 4,
    "i": 34,
    "j": 38,
    "k": 40,
    "l": 37,
    "m": 46,
    "n": 45,
    "o": 31,
    "p": 35,
    "q": 12,
    "r": 15,
    "s": 1,
    "t": 17,
    "u": 32,
    "v": 9,
    "w": 13,
    "x": 7,
    "y": 16,
    "z": 6,
    "0": 29,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "5": 23,
    "6": 22,
    "7": 26,
    "8": 28,
    "9": 25,
}
NAMED_KEY_CODES = {
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
}
HOTKEY_KEYS = {
    **{key: (ord(key), code, 0) for key, code in PRINTABLE_KEY_CODES.items()},
    "space": (ord(" "), 49, 0),
    **{
        key: (NO_CHARACTER, code, FUNCTION_KEY) for key, code in NAMED_KEY_CODES.items()
    },
}
UNSET_REFUSALS = {
    "hotkeys": "would reset every keyboard shortcut",
    "list": "would delete the whole list",
}
SCOPE_HINTS = {
    "machine": "a machine setting, move it to machine_settings",
    "user": "an account setting, move it to default_settings or an account",
}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _hotkey(choice):
    if isinstance(choice, bool):
        return True
    if not isinstance(choice, str):
        return False
    *modifiers, key = choice.split("+")
    return (
        key in HOTKEY_KEYS
        and set(modifiers) <= set(HOTKEY_MODIFIERS)
        and len(set(modifiers)) == len(modifiers)
        and (modifiers != [] or HOTKEY_KEYS[key][0] == NO_CHARACTER)
    )


def _hotkey_entry(choice):
    if isinstance(choice, bool):
        return {"enabled": choice}
    *modifiers, key = choice.split("+")
    character, code, mask = HOTKEY_KEYS[key]
    mask += sum(HOTKEY_MODIFIERS[modifier] for modifier in modifiers)
    return {
        "enabled": True,
        "value": {"parameters": [character, code, mask], "type": "standard"},
    }


VALUE_KINDS = {
    "bool": (
        lambda spec, value: isinstance(value, bool),
        lambda spec: "true or false",
    ),
    "int": (
        lambda spec, value: (
            isinstance(value, int)
            and not isinstance(value, bool)
            and spec["min"] <= value <= spec["max"]
        ),
        lambda spec: f"a whole number from {spec['min']} to {spec['max']}",
    ),
    "float": (
        lambda spec, value: _number(value) and spec["min"] <= value <= spec["max"],
        lambda spec: f"a number from {spec['min']} to {spec['max']}",
    ),
    "text": (
        lambda spec, value: (
            isinstance(value, str) and re.fullmatch(spec["pattern"], value) is not None
        ),
        lambda spec: f"text that matches {spec['pattern']}",
    ),
    "bundle_ids": (
        lambda spec, value: (
            isinstance(value, list)
            and all(isinstance(i, str) and BUNDLE_ID.fullmatch(i) for i in value)
            and len(set(value)) == len(value)
        ),
        lambda spec: "a list of different app bundle ids, such as [com.apple.Safari]",
    ),
    "paths": (
        lambda spec, value: (
            isinstance(value, list)
            and all(
                isinstance(i, str)
                and (i == "~" or i[:2] == "~/" or i[:1] == "/")
                and posixpath.normpath(i).strip("/") != ""
                for i in value
            )
            and len(set(value)) == len(value)
        ),
        lambda spec: 'a list of different folder paths that start with / or "~", '
        "other than / itself",
    ),
    "list": (
        lambda spec, value: (
            isinstance(value, list)
            and all(i in spec["choices"] for i in value)
            and len(set(map(repr, value))) == len(value)
            and len(value) >= spec.get("min", 0)
        ),
        lambda spec: (
            f"a list of {spec['min']} or more different items from "
            if "min" in spec
            else "a list of different items from "
        )
        + ", ".join(map(str, spec["choices"])),
    ),
    "hotkeys": (
        lambda spec, value: (
            isinstance(value, dict)
            and all(
                action in spec["actions"] and _hotkey(choice)
                for action, choice in value.items()
            )
        ),
        lambda spec: "a mapping from actions ("
        + ", ".join(spec["actions"])
        + ") to true (on), false (off) or keys joined with +, such as control+up; "
        "a letter, digit or space needs shift, control, option or command",
    ),
    "catalog_ids": (
        lambda spec, value: (
            isinstance(value, list) and all(isinstance(i, str) for i in value)
        ),
        lambda spec: "a list of app ids from apps/macos.yml",
    ),
}


def _misread(choice):
    return isinstance(choice, str) and not isinstance(yaml.safe_load(choice), str)


def choice_text(choice):
    if _misread(choice):
        return f'"{choice}"'
    return str(choice).lower() if isinstance(choice, bool) else str(choice)


def _kind(spec):
    return spec["type"] if isinstance(spec, dict) else None


def allowed(spec, value):
    if isinstance(spec, list):
        return value in spec
    return VALUE_KINDS[spec["type"]][0](spec, value)


def describe(spec):
    if isinstance(spec, list):
        return "one of " + ", ".join(map(choice_text, spec))
    return VALUE_KINDS[spec["type"]][1](spec)


def _reference(files):
    reference, source, problems = {}, {}, []
    for file_name, entries in files.items():
        for setting_id, entry in (entries or {}).items():
            if setting_id in source:
                problems.append(
                    f"{setting_id}: in the settings reference twice, "
                    f"in {source[setting_id]} and {file_name}"
                )
                continue
            source[setting_id] = file_name
            reference[setting_id] = entry
    return reference, problems


def _section(mapping, name, empty):
    value = mapping.get(name)
    return empty if value is None else value


def _app_id_problems(app_ids, catalog):
    known = ", ".join(sorted(catalog))
    return [
        f"unknown app id {app_id}, use one of {known}"
        for app_id in dict.fromkeys(app_ids)
        if app_id not in catalog
    ] + [
        f"{app_id} listed {count} times"
        for app_id, count in Counter(app_ids).items()
        if count > 1
    ]


def _setting_problems(prefix, settings, scope, reference, allow_lockout_risk, catalog):
    problems = []
    for setting_id, value in settings.items():
        subject = f"{prefix}{setting_id}"
        entry = reference.get(setting_id)
        if entry is None:
            problems.append(f"{subject}: unknown setting id")
        elif entry["scope"] != scope:
            problems.append(f"{subject}: {SCOPE_HINTS[entry['scope']]}")
        elif entry["route"] == "not_yet":
            problems.append(
                f"{subject}: mac-setup cannot set this yet, {entry['not_yet_reason']}"
            )
        elif value == UNSET and entry["route"] != "defaults":
            problems.append(
                f"{subject}: unset works only for settings stored with defaults, "
                f"use {describe(entry['values'])}"
            )
        elif value == UNSET and _kind(entry["values"]) in UNSET_REFUSALS:
            problems.append(
                f"{subject}: unset {UNSET_REFUSALS[_kind(entry['values'])]}, "
                f"use {describe(entry['values'])}"
            )
        elif value != UNSET and not allowed(entry["values"], value):
            quote = isinstance(entry["values"], list) and any(
                _misread(choice) and yaml.safe_load(choice) == value
                for choice in entry["values"]
            )
            problems.append(
                f"{subject}: {value!r} is not allowed, "
                f"use {describe(entry['values'])}" + (" (quote them)" if quote else "")
            )
        elif entry["values"] == {"type": "catalog_ids"} and (
            app_problems := _app_id_problems(value, catalog)
        ):
            problems += [f"{subject}: {line}" for line in app_problems]
        elif entry["lockout_risk"] and not allow_lockout_risk:
            problems.append(
                f"{subject}: can lock you out of this Mac, "
                "set allow_lockout_risk: true to allow it"
            )
    return problems


def _pair_problems(prefix, settings, together):
    return [
        f"{prefix}{setting_id}: needs {needed} in the same settings"
        for setting_id, needed in NEEDS_SETTING.items()
        if settings.get(setting_id) is True and needed not in together
    ]


def _conflict_problems(prefix, together, own, reference):
    first, pairs = {}, {}
    for write in _writes(together, reference):
        if write["dict_mode"] == "add":
            continue
        earlier = first.setdefault(
            (write["domain"], write["host"], write["key"]), write
        )
        pair = (
            (write["id"], earlier["id"])
            if write["id"] in own
            else (earlier["id"], write["id"])
        )
        if earlier["value"] != write["value"] and own & set(pair):
            pairs.setdefault(pair, write["key"])
    return [
        f"{prefix}{named}: {other} sets the same key {key} to another value, "
        "so choose values that agree"
        for (named, other), key in pairs.items()
    ]


def _account_problems(accounts, reference, allow_lockout_risk, defaults, catalog):
    problems, names = [], Counter()
    for number, account in enumerate(accounts, 1):
        if not isinstance(account, dict):
            problems.append(
                f"account item {number}: must be a mapping with name and full_name"
            )
            continue
        name = account.get("name")
        valid_name = isinstance(name, str) and ACCOUNT_NAME.fullmatch(name)
        subject = f"account {name if valid_name else f'item {number}'}"
        if "name" not in account:
            problems.append(f"{subject}: name is missing")
        elif not valid_name:
            problems.append(
                f"{subject}: name {name!r} must start with a lowercase letter or _ "
                "and hold only lowercase letters, digits, '.', '_' and '-'"
            )
        else:
            names[name] += 1
        full_name = account.get("full_name")
        if not (isinstance(full_name, str) and full_name.strip()):
            problems.append(f"{subject}: full_name is missing or empty")
        if not isinstance(account.get("admin", False), bool):
            problems.append(f"{subject}: admin must be true or false")
        for field in sorted(set(account) - ACCOUNT_FIELDS):
            problems.append(f"{subject}: unknown field {field}")
        settings = _section(account, "settings", {})
        if isinstance(settings, dict):
            problems += _setting_problems(
                f"{subject}, ", settings, "user", reference, allow_lockout_risk, catalog
            )
            problems += _pair_problems(
                f"{subject}, ", settings, {**defaults, **settings}
            )
        else:
            problems.append(f"{subject}: settings must map setting ids to values")
    problems += [
        f"account {name}: listed {count} times"
        for name, count in names.items()
        if count > 1
    ]
    return problems


def profile_problems(profile, files, catalog=()):
    reference, problems = _reference(files)
    problems += [
        f"{key}: unknown profile key, use {', '.join(PROFILE_KEYS)}"
        for key in sorted(profile)
        if key not in PROFILE_KEYS
        and key not in ANSIBLE_NAMES
        and key not in RUN_SWITCHES
        and not key.startswith("ansible_")
    ]
    allow_lockout_risk = _section(profile, "allow_lockout_risk", False)
    if not isinstance(allow_lockout_risk, bool):
        problems.append("allow_lockout_risk: must be true or false")
        allow_lockout_risk = False
    for name, scope, prefix in [
        ("default_settings", "user", ""),
        ("machine_settings", "machine", "machine_settings, "),
    ]:
        settings = _section(profile, name, {})
        if isinstance(settings, dict):
            problems += _setting_problems(
                prefix, settings, scope, reference, allow_lockout_risk, catalog
            )
            if scope == "user":
                problems += _pair_problems(prefix, settings, settings)
        else:
            problems.append(f"{name}: must map setting ids to values")
    defaults = _section(profile, "default_settings", {})
    accounts = _section(profile, "accounts", [])
    if isinstance(accounts, list):
        problems += _account_problems(
            accounts,
            reference,
            allow_lockout_risk,
            defaults if isinstance(defaults, dict) else {},
            catalog,
        )
    else:
        problems.append("accounts: must be a list of accounts")
    if not problems:
        machine = _section(profile, "machine_settings", {})
        problems += _conflict_problems("", defaults, set(defaults), reference)
        problems += _conflict_problems(
            "machine_settings, ", machine, set(machine), reference
        )
        for account in accounts:
            settings = _section(account, "settings", {})
            problems += _conflict_problems(
                f"account {account['name']}, ",
                {**defaults, **settings},
                set(settings),
                reference,
            )
    passwords = _section(profile, "account_passwords", {})
    if isinstance(passwords, dict):
        problems += [
            f"account {name}: its password in account_passwords is empty"
            for name, password in passwords.items()
            if not (isinstance(password, str) and password)
        ]
    else:
        problems.append("account_passwords: must map account names to passwords")
    return problems


def _stored(value, spec, write):
    if value == UNSET:
        return None
    if _kind(spec) == "hotkeys":
        return {
            str(spec["actions"][action]): _hotkey_entry(choice)
            for action, choice in value.items()
        }
    if _kind(spec) == "list":
        return [write["map"][item] for item in value]
    return write["map"][value] if "map" in write else value


def _write(setting_id, value, entry, write):
    stored = _stored(value, entry["values"], write)
    return {
        "id": setting_id,
        "domain": write["domain"],
        "key": write["key"],
        "type": write["type"],
        "value": stored,
        "state": "absent" if stored is None else "present",
        "host": write.get("host", ""),
        "dict_mode": write.get("dict_mode", "replace"),
        "takes_effect": entry["takes_effect"],
    }


def _routed(settings, reference, route):
    return {
        setting_id: value
        for setting_id, value in settings.items()
        if reference[setting_id]["route"] == route
    }


def _leaves_alone(value, spec, write):
    return (
        isinstance(spec, list)
        and value != UNSET
        and "map" in write
        and value not in write["map"]
    )


def _writes(settings, reference):
    return [
        _write(setting_id, value, reference[setting_id], write)
        for setting_id, value in _routed(settings, reference, "defaults").items()
        for write in reference[setting_id]["writes"]
        if not _leaves_alone(value, reference[setting_id]["values"], write)
    ]


def _item(settings, reference, route, key):
    return {key: value for value in _routed(settings, reference, route).values()}


def _commands(settings, reference):
    return [
        _command(setting_id, value, reference[setting_id]["command"])
        for setting_id, value in _routed(settings, reference, "command").items()
    ]


def _account_work(settings, reference):
    sidebar = _routed(settings, reference, "finder_sidebar")
    return {
        "writes": _writes(settings, reference),
        "commands": _commands(settings, reference),
        "dock_apps": _item(settings, reference, "dock_apps", "apps"),
        "wallpaper": _item(settings, reference, "wallpaper", "color"),
        "finder_sidebar": (
            {"ids": sorted(sidebar)}
            | {
                setting_id.removeprefix(SIDEBAR_PREFIX): value
                for setting_id, value in sidebar.items()
            }
            if sidebar
            else {}
        ),
    }


def _shown(value):
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _choose(entry, value):
    setting = entry["summary"].split(". ")[0].removesuffix(".")
    return f"{setting}: {'the way you want' if value == 'by_hand' else _shown(value)}"


def _by_hand(account, settings, reference):
    return [
        {
            "account": account,
            "id": setting_id,
            "where": reference[setting_id]["where"],
            "link": reference[setting_id]["link"],
            "choose": _choose(reference[setting_id], value),
        }
        for setting_id, value in _routed(settings, reference, "by_hand").items()
    ]


def _command(setting_id, value, command):
    return {
        "id": setting_id,
        "read": command["read"],
        "parse": command["parse"],
        "map": command.get("map", {}),
        "refuse": command.get("refuse", {}),
        "set": command["set"],
        "value": value,
    }


def _written(work):
    return (
        {write["id"] for write in work["writes"]}
        | ({"dock.applications"} if work["dock_apps"] else set())
        | ({"desktop.wallpaper"} if work["wallpaper"] else set())
        | set(work["finder_sidebar"].get("ids", []))
    )


def _lists(paths, folder):
    return any(
        posixpath.normpath(path) == folder
        or posixpath.normpath(path).startswith(folder + "/")
        for path in paths
    )


def _step(setting_id, settings, reference):
    if reference[setting_id]["route"] == "finder_sidebar":
        return sorted(_routed(settings, reference, "finder_sidebar"))
    return [setting_id]


def _permissions(groups, reference, table):
    needed = {}
    for settings in groups:
        for setting_id, value in settings.items():
            for need in reference[setting_id]["needs"]:
                row = table.get(need)
                if row and (not row["only_when_listed"] or _lists(value, row["test"])):
                    found = needed.setdefault(need, {"settings": [], "skips": []})
                    found["settings"].append(setting_id)
                    found["skips"] += _step(setting_id, settings, reference)
    return [
        {"id": need, **row, **needed[need]}
        for need, row in table.items()
        if need in needed
    ]


def profile_work(profile, files, running_account, permission_table=None):
    reference, _ = _reference(files)
    defaults = _section(profile, "default_settings", {})
    accounts = _section(profile, "accounts", [])
    machine = _section(profile, "machine_settings", {})
    settings = {
        account["name"]: {**defaults, **_section(account, "settings", {})}
        for account in accounts
    }
    settings.setdefault(running_account, defaults)
    own = _account_work(settings[running_account], reference)
    others = [
        {"account": name, **_account_work(account_settings, reference)}
        for name, account_settings in settings.items()
        if name != running_account
    ]
    by_hand = [
        item
        for name, account_settings in [*settings.items(), ("machine", machine)]
        for item in _by_hand(name, account_settings, reference)
    ]
    clicks_elsewhere = sum(
        reference[setting_id]["route"] != "by_hand"
        and "click" in reference[setting_id]["needs"]
        for group in [*settings.values(), machine]
        for setting_id in group
    )
    own_timing = {write["id"]: write["takes_effect"] for write in own["writes"]}
    return {
        "own": own,
        "others": others,
        "machine_writes": _writes(machine, reference),
        "commands": _commands(machine, reference),
        "apps": [
            app_id
            for app_ids in _routed(machine, reference, "apps").values()
            for app_id in app_ids
        ],
        "by_hand": by_hand,
        "permissions": _permissions(
            [settings[running_account], machine], reference, permission_table or {}
        ),
        "accounts": [
            {
                "name": account["name"],
                "full_name": account["full_name"],
                "admin": account.get("admin", False),
            }
            for account in accounts
        ],
        "counts": {
            "after_logout": list(own_timing.values()).count("logout")
            + sum(len(_written(other)) for other in others),
            "timing_not_measured": list(own_timing.values()).count("not measured"),
            "need_click": len(by_hand) + clicks_elsewhere,
            "own_session": sum(len(other["commands"]) for other in others),
        },
    }


def _settings(count):
    return f"{count} setting" if count == 1 else f"{count} settings"


def _reason(message):
    if "a password is required" in message:
        return "needs admin rights: run with -K"
    return message


def profile_summary(
    counts,
    refused_settings,
    account_results,
    waiting_wallpaper,
    permission_notes=(),
    stopped="",
):
    refused = (
        ([f"the run stopped early at {stopped}"] if stopped else [])
        + [
            f"refused {item['id']} for {item['account']}: {_reason(item['message'])}"
            for item in refused_settings
        ]
        + [
            f"refused account {item['name']}: {item['refused']}"
            for item in account_results
            if item["refused"]
        ]
    )
    notes = (
        list(permission_notes)
        + [
            f"account {item['name']} does not exist yet, so this check run skipped its settings"
            for item in account_results
            if item["state"] == "would_create"
        ]
        + [
            f"account {item['name']} has no secure token, "
            "so it cannot unlock a FileVault disk at startup"
            for item in account_results
            if item["state"] != "would_create" and not item["secure_token"]
        ]
        + [
            f"the desktop picture for {name} is set at its next run while it is signed in"
            for name in waiting_wallpaper
        ]
    )
    after_logout, click = counts["after_logout"], counts["need_click"]
    if after_logout:
        notes.append(f"log out and back in to see {_settings(after_logout)}")
    unmeasured = counts["timing_not_measured"]
    if unmeasured:
        notes.append(
            f"{_settings(unmeasured)} may need a logout or an app restart to show; "
            f"{'its' if unmeasured == 1 else 'their'} timing is not measured"
        )
    if counts["own_session"]:
        notes.append(
            f"{_settings(counts['own_session'])} for other accounts "
            f"{'runs' if counts['own_session'] == 1 else 'run'} in the account's "
            "own session: sign in as each account and run mac-setup there"
        )
    if click:
        notes.append(
            f"{_settings(click)} {'needs' if click == 1 else 'need'} a click: "
            "run playbooks/by-hand.yml"
        )
    return {"notes": notes, "refused": refused}


class FilterModule:
    def filters(self):
        return {
            "profile_problems": profile_problems,
            "profile_work": profile_work,
            "profile_summary": profile_summary,
        }
