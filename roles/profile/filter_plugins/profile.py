import re
from collections import Counter

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
]
ANSIBLE_NAMES = {
    "group_names",
    "groups",
    "inventory_dir",
    "inventory_file",
    "inventory_hostname",
    "inventory_hostname_short",
    "playbook_dir",
}
SCOPE_HINTS = {
    "machine": "a machine setting, move it to machine_settings",
    "user": "an account setting, move it to default_settings or an account",
}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
}


def _allowed(spec, value):
    if isinstance(spec, list):
        return value in spec
    return VALUE_KINDS[spec["type"]][0](spec, value)


def _describe(spec):
    if isinstance(spec, list):
        return "one of " + ", ".join(str(choice) for choice in spec)
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


def _setting_problems(prefix, settings, scope, reference, allow_lockout_risk):
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
        elif value != UNSET and not _allowed(entry["values"], value):
            problems.append(
                f"{subject}: {value!r} is not allowed, "
                f"use {_describe(entry['values'])}"
            )
        elif entry["lockout_risk"] and not allow_lockout_risk:
            problems.append(
                f"{subject}: can lock you out of this Mac, "
                "set allow_lockout_risk: true to allow it"
            )
    return problems


def _account_problems(accounts, reference, allow_lockout_risk):
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
                f"{subject}, ", settings, "user", reference, allow_lockout_risk
            )
        else:
            problems.append(f"{subject}: settings must map setting ids to values")
    problems += [
        f"account {name}: listed {count} times"
        for name, count in names.items()
        if count > 1
    ]
    return problems


def profile_problems(profile, files):
    reference, problems = _reference(files)
    problems += [
        f"{key}: unknown profile key, use {', '.join(PROFILE_KEYS)}"
        for key in sorted(profile)
        if key not in PROFILE_KEYS
        and key not in ANSIBLE_NAMES
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
                prefix, settings, scope, reference, allow_lockout_risk
            )
        else:
            problems.append(f"{name}: must map setting ids to values")
    accounts = _section(profile, "accounts", [])
    if isinstance(accounts, list):
        problems += _account_problems(accounts, reference, allow_lockout_risk)
    else:
        problems.append("accounts: must be a list of accounts")
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


def _write(setting_id, value, entry, write):
    unset = value == UNSET
    return {
        "id": setting_id,
        "domain": write["domain"],
        "key": write["key"],
        "type": write["type"],
        "value": None if unset else write["map"][value] if "map" in write else value,
        "state": "absent" if unset else "present",
        "host": write.get("host", ""),
        "dict_mode": write.get("dict_mode", "replace"),
        "takes_effect": entry["takes_effect"],
    }


def _writes(settings, reference):
    return [
        _write(setting_id, value, reference[setting_id], write)
        for setting_id, value in settings.items()
        if reference[setting_id]["route"] == "defaults"
        for write in reference[setting_id]["writes"]
    ]


def _ids(writes):
    return len({write["id"] for write in writes})


def profile_work(profile, files, running_account):
    reference, _ = _reference(files)
    defaults = _section(profile, "default_settings", {})
    accounts = _section(profile, "accounts", [])
    settings = {
        account["name"]: {**defaults, **_section(account, "settings", {})}
        for account in accounts
    }
    own_writes = _writes(settings.get(running_account, defaults), reference)
    others = [
        {"account": name, "writes": _writes(account_settings, reference)}
        for name, account_settings in settings.items()
        if name != running_account
    ]
    groups = [*settings.values(), _section(profile, "machine_settings", {})]
    if running_account not in settings:
        groups.append(defaults)
    return {
        "defaults_writes": own_writes,
        "accounts": [
            {
                "name": account["name"],
                "full_name": account["full_name"],
                "admin": account.get("admin", False),
            }
            for account in accounts
        ],
        "other_account_writes": others,
        "counts": {
            "after_logout": _ids(
                [write for write in own_writes if write["takes_effect"] == "logout"]
            )
            + sum(_ids(other["writes"]) for other in others),
            "need_click": sum(
                reference[setting_id]["route"] == "by_hand"
                or "click" in reference[setting_id].get("needs", [])
                for group in groups
                for setting_id in group
            ),
        },
    }


def _settings(count):
    return f"{count} setting" if count == 1 else f"{count} settings"


def profile_summary(counts, refused_settings, account_results):
    refused = [
        f"refused {item['id']} for {item['account']}: {item['message']}"
        for item in refused_settings
    ] + [
        f"refused account {item['name']}: {item['refused']}"
        for item in account_results
        if item["refused"]
    ]
    notes = [
        f"account {item['name']} does not exist yet, so this check run skipped its settings"
        for item in account_results
        if item["state"] == "would_create"
    ] + [
        f"account {item['name']} has no secure token, "
        "so it cannot unlock a FileVault disk at startup"
        for item in account_results
        if item["state"] != "would_create" and not item["secure_token"]
    ]
    after_logout, click = counts["after_logout"], counts["need_click"]
    if after_logout:
        notes.append(f"log out and back in to see {_settings(after_logout)}")
    if click:
        notes.append(
            f"{_settings(click)} {'needs' if click == 1 else 'need'} a click in System Settings"
        )
    return {"notes": notes, "refused": refused}


class FilterModule:
    def filters(self):
        return {
            "profile_problems": profile_problems,
            "profile_work": profile_work,
            "profile_summary": profile_summary,
        }
