import json
import os
import re
import stat

SECTIONS = {"machine_settings": "machine", "default_settings": "user"}
GAPS = frozenset({"masked", "conflict", "unverified", "unsupported", "unavailable"})
STATES = GAPS | {"absent"}
MAX_BYTES = 16 * 1024 * 1024


class CompareError(Exception):
    pass


def read_text(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CompareError()
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise CompareError()
        return raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise CompareError() from error


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CompareError()
        result[key] = value
    return result


def parse_json(text):
    def bad_constant(value):
        raise CompareError()

    try:
        return json.loads(
            text, object_pairs_hook=unique_object, parse_constant=bad_constant
        )
    except (ValueError, RecursionError) as error:
        raise CompareError() from error


def mapped_entry(setting_id, section, entries):
    row = entries.get(setting_id)
    if row is None or row["scope"] != SECTIONS[section]:
        raise CompareError()
    return row


def normalized_value(row, value):
    if row["browser"].get("capture", {}).get("class") == "toolbar_layout":
        return json.dumps(parse_json(value), sort_keys=True, separators=(",", ":"))
    return value


def parse_candidate(text, entries, value_allowed):
    result = {}
    sections = []
    section = None
    empty = False
    started = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if not started:
            if line != "---":
                raise CompareError()
            started = True
            continue
        if line.startswith("#"):
            continue
        if line in (
            "machine_settings:",
            "machine_settings: {}",
            "default_settings:",
            "default_settings: {}",
        ):
            section = line.split(":", 1)[0]
            if section in sections:
                raise CompareError()
            sections.append(section)
            empty = line.endswith("{}")
            continue
        if line.startswith("  # "):
            if section is None:
                raise CompareError()
            comment = line[4:]
            state_match = re.fullmatch(r"([^ :]+): ([a-z]+)", comment)
            if state_match:
                setting_id, state = state_match.groups()
                row = mapped_entry(setting_id, section, entries)
                if (
                    state not in STATES
                    or row["browser"]["kind"] == "lock_control"
                    or setting_id in result
                ):
                    raise CompareError()
                result[setting_id] = (state, None)
            continue
        if section is None or empty:
            raise CompareError()
        assignment = re.fullmatch(r"  ([^ :]+): (.+)", line)
        if assignment is None:
            raise CompareError()
        setting_id, raw_value = assignment.groups()
        row = mapped_entry(setting_id, section, entries)
        if setting_id in result or row["browser"]["kind"] == "lock_control":
            raise CompareError()
        value = parse_json(raw_value)
        if not value_allowed(row["values"], value):
            raise CompareError()
        result[setting_id] = ("captured", normalized_value(row, value))
    if sections != list(SECTIONS):
        raise CompareError()
    return result


def parse_profile(text, entries, value_allowed, families):
    profile = parse_json(text)
    if not isinstance(profile, dict) or set(profile) != set(SECTIONS):
        raise CompareError()
    desired = {}
    for section, settings in profile.items():
        if not isinstance(settings, dict):
            raise CompareError()
        for setting_id, value in settings.items():
            row = mapped_entry(setting_id, section, entries)
            release = value == "unset"
            if release and row["browser"]["kind"] == "lock_control":
                raise CompareError()
            if not release and not value_allowed(row["values"], value):
                raise CompareError()
            if row["browser"]["family"] in families:
                desired[setting_id] = (
                    ("release", None)
                    if release
                    else ("captured", normalized_value(row, value))
                )
    if not desired:
        raise CompareError()
    return desired


def equal_values(first, second):
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(
            equal_values(first[key], second[key]) for key in first
        )
    if isinstance(first, list):
        return len(first) == len(second) and all(
            equal_values(a, b) for a, b in zip(first, second)
        )
    return first == second


def compare(first, second, required=False):
    if not first and not second:
        raise CompareError()
    lines = []
    different = False
    unknown = False
    for setting_id in sorted(first.keys() | second.keys()):
        left = first.get(setting_id)
        right = second.get(setting_id)
        if left and left[0] == "release":
            lines.append(setting_id + " gap unverified")
            unknown = True
            continue
        gaps = {item[0] for item in (left, right) if item and item[0] in GAPS}
        if required and (right is None or right[0] == "absent"):
            gaps.add("absent")
        if gaps:
            lines.append(setting_id + " gap " + ",".join(sorted(gaps)))
            unknown = True
        elif left is None or right is None:
            lines.append(setting_id + " only-in-one")
            different = True
        elif left[0] == right[0] and equal_values(left[1], right[1]):
            lines.append(setting_id + " same")
        else:
            lines.append(setting_id + " different")
            different = True
    return lines, (3 if required and unknown else int(different or unknown))
