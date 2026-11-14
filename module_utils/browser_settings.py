import configparser
import fcntl
import ipaddress
import json
import math
import os
import platform
import plistlib
import re
import socket
import stat
import struct
import subprocess
import sys
from pathlib import Path
from xml.parsers.expat import ExpatError


class CaptureError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


SOURCE_STATES = {
    "firefox_prefs": "persisted",
    "firefox_user": "startup_override",
    "firefox_policy": "policy_stored",
    "chrome_preferences": "persisted",
    "chrome_local_state": "persisted",
    "chrome_secure_preferences": "persisted",
    "chrome_policy": "policy_stored",
}
TYPE_NAMES = {
    "bool": bool,
    "int": int,
    "float": (int, float),
    "text": str,
    "array": list,
    "object": dict,
}
VERSION = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,6}){1,3}")


def parse_firefox_prefs(text):
    position = 0
    size = len(text)

    def bad():
        raise CaptureError(3, "Could not parse firefox_prefs.")

    def space():
        nonlocal position
        while position < size:
            if text[position].isspace():
                position += 1
            elif text.startswith("//", position):
                end = text.find("\n", position + 2)
                position = size if end < 0 else end + 1
            elif text.startswith("/*", position):
                end = text.find("*/", position + 2)
                if end < 0:
                    bad()
                position = end + 2
            else:
                break

    def literal(expected):
        nonlocal position
        space()
        if not text.startswith(expected, position):
            bad()
        position += len(expected)

    def string():
        nonlocal position
        space()
        if position >= size or text[position] not in "\"'":
            bad()
        quote = text[position]
        position += 1
        result = []
        escapes = {
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "b": "\b",
            "f": "\f",
            "v": "\v",
            "\\": "\\",
            "'": "'",
            '"': '"',
        }
        while position < size:
            char = text[position]
            position += 1
            if char == quote:
                return "".join(result)
            if char in "\n\r" or ord(char) < 32:
                bad()
            if char == "\\":
                if position >= size:
                    bad()
                char = text[position]
                position += 1
                if char in escapes:
                    char = escapes[char]
                elif char in ("x", "u"):
                    count = 2 if char == "x" else 4
                    digits = text[position : position + count]
                    if len(digits) != count or not re.fullmatch(
                        r"[0-9a-fA-F]+", digits
                    ):
                        bad()
                    char = chr(int(digits, 16))
                    position += count
                else:
                    bad()
            result.append(char)
        bad()

    result = {}
    space()
    while position < size:
        match = re.match(r"(?:user_pref|sticky_pref|pref)\b", text[position:])
        if not match:
            bad()
        position += match.end()
        literal("(")
        name = string()
        literal(",")
        space()
        if position < size and text[position] in "\"'":
            value = string()
        else:
            match = re.match(r"(?:true|false|[+-]?[0-9]+)\b", text[position:])
            if not match:
                bad()
            word = match.group()
            position += match.end()
            value = {"true": True, "false": False}.get(word)
            if value is None:
                try:
                    value = int(word)
                except ValueError:
                    bad()
                if not -(2**31) <= value < 2**31:
                    bad()
        literal(")")
        literal(";")
        result[name] = value
        space()
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


_POLICY_MISSING = object()
_POLICY_CONFLICT = object()


def _merge_policy_values(left, right):
    if left is _POLICY_MISSING:
        return right
    if right is _POLICY_MISSING:
        return left
    if isinstance(left, dict) and isinstance(right, dict):
        return {
            key: _merge_policy_values(
                left.get(key, _POLICY_MISSING), right.get(key, _POLICY_MISSING)
            )
            for key in left.keys() | right.keys()
        }
    if isinstance(left, list) and isinstance(right, list):
        return [
            _merge_policy_values(
                left[index] if index < len(left) else _POLICY_MISSING,
                right[index] if index < len(right) else _POLICY_MISSING,
            )
            for index in range(max(len(left), len(right)))
        ]
    if type(left) is type(right) and left == right:
        return left
    return _POLICY_CONFLICT


def _normalize_firefox_policy(value, policy_roots=None):
    result = {}
    for key, item in value.items():
        if policy_roots is not None and key.split("__", 1)[0] not in policy_roots:
            result[key] = item
            continue
        branch = item
        parts = key.split("__")
        for index in range(len(parts) - 1, -1, -1):
            part = parts[index]
            if not part:
                raise CaptureError(3, "Could not parse firefox_policy.")
            if index > 0 and part.isascii() and part.isdigit():
                number = int(part)
                if number > 50:
                    raise CaptureError(3, "Could not parse firefox_policy.")
                branch = [_POLICY_MISSING] * number + [branch]
            else:
                branch = {part: branch}
        result = _merge_policy_values(result, branch)
    return result


def _policy_conflict(value):
    if value is _POLICY_CONFLICT or value is _POLICY_MISSING:
        return True
    if isinstance(value, dict):
        return any(_policy_conflict(item) for item in value.values())
    if isinstance(value, list):
        return any(_policy_conflict(item) for item in value)
    return False


def parse_source(source, payload):
    try:
        if source in ("firefox_prefs", "firefox_user"):
            return parse_firefox_prefs(payload.decode("utf-8-sig"))
        if source in ("firefox_policy", "firefox_domain", "chrome_policy"):
            value = plistlib.loads(payload)
        else:
            value = json.loads(
                payload.decode("utf-8-sig"),
                object_pairs_hook=_unique_object,
                parse_constant=lambda word: (_ for _ in ()).throw(
                    ValueError("Non-finite number")
                ),
            )
        if not isinstance(value, dict):
            raise ValueError("Not a mapping")
        return _normalize_firefox_policy(value) if source == "firefox_policy" else value
    except (
        ValueError,
        TypeError,
        UnicodeError,
        OverflowError,
        RecursionError,
        plistlib.InvalidFileException,
        ExpatError,
    ):
        raise CaptureError(3, "Could not parse " + source + ".") from None


def read_bytes(path):
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise CaptureError(3, "A configuration source is not a regular file.")
        return stream.read()


def _signature(path):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise CaptureError(3, "A configuration source is not a regular file.")
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
    except FileNotFoundError:
        return None


class Sources:
    def __init__(self):
        self.signatures = {}

    def load(self, path, required=False):
        path = Path(path)
        try:
            before = _signature(path)
            self.signatures[path] = before
            if before is None:
                if required:
                    raise CaptureError(
                        3, "A required configuration source is unavailable."
                    )
                return None
            payload = read_bytes(path)
            if _signature(path) != before:
                raise CaptureError(3, "A configuration source changed during capture.")
            return payload
        except OSError:
            raise CaptureError(3, "A configuration source could not be read.") from None

    def unchanged(self):
        try:
            if any(
                _signature(path) != before for path, before in self.signatures.items()
            ):
                raise CaptureError(3, "A configuration source changed during capture.")
        except OSError:
            raise CaptureError(3, "A configuration source could not be read.") from None


def _is_browser(family, command):
    command = command.lower()
    name = Path(command).name
    patterns = {
        "firefox": ("firefox", "firefoxcp"),
        "chrome": ("google chrome", "chrome helper", "google chrome helper"),
    }
    bundle = (
        "/firefox.app/contents/"
        if family == "firefox"
        else "/google chrome.app/contents/"
    )
    return bundle in command or any(
        name == word or name.startswith(word + " ") for word in patterns[family]
    )


def _processes():
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "pid=,comm="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode:
            raise ValueError("Process listing unavailable")
        processes = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            pid, command = line.strip().split(maxsplit=1)
            if not pid.isascii() or not pid.isdigit() or int(pid) in processes:
                raise ValueError("Process listing invalid")
            processes[int(pid)] = command
        return processes
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        raise CaptureError(
            3, "Could not check whether the selected browser source is closed."
        ) from None


def _fcntl_lock_owner(path):
    if sys.platform != "darwin":
        raise CaptureError(3, "Could not read the selected browser lock.")
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CaptureError(3, "Could not read the selected browser lock.")
        query = struct.pack("qqihh", 0, 0, 0, fcntl.F_WRLCK, os.SEEK_SET)
        response = fcntl.fcntl(descriptor, fcntl.F_GETLK, query)
        start, length, pid, kind, whence = struct.unpack("qqihh", response)
        if kind == fcntl.F_UNLCK:
            return None
        if kind not in (fcntl.F_RDLCK, fcntl.F_WRLCK) or pid <= 0:
            raise CaptureError(3, "Could not identify the selected browser lock owner.")
        return pid
    finally:
        os.close(descriptor)


def _symlink_lock_pid(family, target):
    if family == "chrome":
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)-([1-9][0-9]{0,9})", target)
        if not match or match[1] != socket.gethostname():
            raise CaptureError(3, "Could not identify the selected browser lock owner.")
    else:
        match = re.fullmatch(r"([^:]+):\+?([1-9][0-9]{0,9})", target)
        try:
            local = match and ipaddress.IPv4Address(match[1]).is_loopback
        except ipaddress.AddressValueError:
            local = False
        if not local:
            raise CaptureError(3, "Could not identify the selected browser lock owner.")
    pid = int(match[2])
    if pid > 2**31 - 1:
        raise CaptureError(3, "Could not identify the selected browser lock owner.")
    return pid


def _browser_locks(family, profile, user_data):
    paths = (
        [user_data / "SingletonLock"]
        if family == "chrome"
        else [profile / ".parentlock", profile / "lock"]
    )
    records = []
    try:
        for path in paths:
            try:
                info = path.lstat()
            except FileNotFoundError:
                records.append((path.name, None, "missing", None))
                continue
            signature = (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
            if stat.S_ISLNK(info.st_mode):
                pid = _symlink_lock_pid(family, str(path.readlink()))
                kind = "owner"
            elif (
                stat.S_ISREG(info.st_mode)
                and family == "firefox"
                and path.name == ".parentlock"
            ):
                pid = _fcntl_lock_owner(path)
                kind = "owner"
            elif stat.S_ISREG(info.st_mode) and family == "chrome":
                pid, kind = None, "opaque"
            else:
                raise CaptureError(3, "Could not read the selected browser lock.")
            records.append((path.name, signature, kind, pid))
        return records
    except (OSError, ValueError, struct.error):
        raise CaptureError(3, "Could not read the selected browser lock.") from None


def check_browser_closed(family, profile, user_data):
    try:
        profile, user_data = Path(profile).resolve(), Path(user_data).resolve()
        support = Path.home() / "Library/Application Support"
        default = (
            support / ("Google/Chrome" if family == "chrome" else "Firefox")
        ).resolve()
    except (OSError, RuntimeError):
        raise CaptureError(
            3, "Could not resolve the selected browser source."
        ) from None
    if family == "chrome" and profile.parent != user_data:
        raise CaptureError(
            3,
            "The selected Chrome profile must belong to its selected user data folder.",
        )
    before = _browser_locks(family, profile, user_data)
    processes = _processes()
    after = _browser_locks(family, profile, user_data)
    if before != after:
        raise CaptureError(3, "The selected browser lock changed during capture.")
    active = {pid for pid, command in processes.items() if _is_browser(family, command)}
    if any(kind == "owner" and pid in active for name, signature, kind, pid in after):
        raise CaptureError(
            3, "Quit the browser instance using the selected source with Command-Q."
        )
    proven = any(kind == "owner" for name, signature, kind, pid in after)
    opaque = any(kind == "opaque" for name, signature, kind, pid in after)
    default_selected = (
        user_data == default
        if family == "chrome"
        else profile == default or default in profile.parents
    )
    if active and (opaque or (default_selected and not proven)):
        raise CaptureError(3, "Could not prove the selected browser source is closed.")


def select_firefox_profile(root):
    sources = Sources()
    payload = sources.load(Path(root) / "profiles.ini", required=True)
    try:
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(payload.decode("utf-8-sig"))
        choices = []
        for section in config.sections():
            if section.startswith("Profile") and config.has_option(section, "Path"):
                path = Path(config.get(section, "Path"))
                if config.get(section, "IsRelative", fallback="1") == "1":
                    path = Path(root) / path
                choices.append(path)
    except (ValueError, UnicodeError, configparser.Error):
        raise CaptureError(3, "Could not parse the Firefox profile registry.") from None
    sources.unchanged()
    if len(choices) != 1:
        raise CaptureError(
            3,
            "Select one Firefox profile explicitly. Find its root in about:support before quitting.",
        )
    return choices[0]


def select_chrome_profile(root):
    try:
        choices = [
            path
            for path in Path(root).iterdir()
            if path.is_dir() and (path / "Preferences").is_file()
        ]
    except OSError:
        raise CaptureError(3, "A Chrome profile is unavailable.") from None
    if len(choices) != 1:
        raise CaptureError(
            3,
            "Select one Chrome profile explicitly. Find its root in chrome://version before quitting.",
        )
    return choices[0]


def _version(text):
    match = VERSION.fullmatch(text)
    return text if match else "unavailable"


def _browser_version(family, profile, user_data, sources):
    if family == "firefox":
        payload = sources.load(profile / "compatibility.ini")
        if payload is None:
            return "unavailable"
        try:
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(payload.decode("utf-8-sig"))
            return _version(
                config.get("Compatibility", "LastVersion")
                .split("/", 1)[0]
                .split("_", 1)[0]
            )
        except (ValueError, UnicodeError, configparser.Error):
            raise CaptureError(
                3, "Could not parse the Firefox version record."
            ) from None
    payload = sources.load(user_data / "Last Version")
    if payload is None:
        return "unavailable"
    try:
        return _version(payload.decode("ascii").strip())
    except UnicodeError:
        raise CaptureError(3, "Could not parse the Chrome version record.") from None


def schema_accepts(schema, value):
    kinds = {
        "boolean": bool,
        "integer": int,
        "number": (int, float),
        "string": str,
        "array": list,
        "object": dict,
    }
    kind = schema.get("type")
    if kind is not None and (
        kind not in kinds
        or not isinstance(value, kinds[kind])
        or kind in ("integer", "number")
        and isinstance(value, bool)
    ):
        return False
    if type(value) is float and not math.isfinite(value):
        return False
    if "enum" in schema and not any(
        type(value) is type(choice) and value == choice for choice in schema["enum"]
    ):
        return False
    if "anyOf" in schema and not any(
        schema_accepts(choice, value) for choice in schema["anyOf"]
    ):
        return False
    if type(value) in (int, float) and (
        value < schema.get("minimum", -math.inf)
        or value > schema.get("maximum", math.inf)
    ):
        return False
    if isinstance(value, str) and "pattern" in schema:
        try:
            if re.search(schema["pattern"], value) is None:
                return False
        except re.error:
            return False
    if isinstance(value, list):
        return all(schema_accepts(schema.get("items", {}), item) for item in value)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            return False
        properties = schema.get("properties", {})
        if not set(schema.get("required", [])) <= set(value):
            return False
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                if not schema_accepts(properties[key], item):
                    return False
            elif extra is False or not schema_accepts(
                extra if isinstance(extra, dict) else {}, item
            ):
                return False
    return True


def value_allowed(values, value):
    if isinstance(values, list):
        return schema_accepts({"enum": values}, value)
    kind = values.get("type")
    if kind == "browser":
        return schema_accepts(values["schema"], value)
    if kind == "bool":
        return schema_accepts({"type": "boolean"}, value)
    if kind in ("int", "float"):
        return schema_accepts(
            {
                "type": "integer" if kind == "int" else "number",
                "minimum": values["min"],
                "maximum": values["max"],
            },
            value,
        )
    if kind == "text":
        return (
            isinstance(value, str)
            and re.fullmatch(values["pattern"], value) is not None
        )
    if kind == "list":
        return schema_accepts(
            {"type": "array", "items": {"enum": values["choices"]}}, value
        ) and len(value) >= values.get("min", 0)
    return False


def filter_value(entry, value):
    capture = entry["browser"]["capture"]
    kind = capture["class"]
    if kind == "mask" or not value_allowed(entry["values"], value):
        return False, None
    if kind in ("enum", "public_endpoint"):
        if any(
            type(value) is type(choice) and value == choice
            for choice in capture.get("choices", [])
        ):
            if isinstance(value, str) and any(ord(char) < 32 for char in value):
                return False, None
            return True, value
        return False, None
    if type(value) is bool:
        return True, value
    if type(value) in (int, float) and (type(value) is int or math.isfinite(value)):
        shape = entry["values"]
        if (
            isinstance(shape, list)
            or (
                shape.get("type") in ("int", "float")
                and "min" in shape
                and "max" in shape
            )
            or (
                shape.get("type") == "browser"
                and "minimum" in shape["schema"]
                and "maximum" in shape["schema"]
            )
        ):
            return True, value
    return False, None


def _lookup(value, path):
    for segment in path:
        if value is _POLICY_CONFLICT:
            return True, value
        if not isinstance(value, dict) or segment not in value:
            return False, None
        value = value[segment]
    return True, value


def _terminals(value, prefix=()):
    if not prefix and isinstance(value, dict) and not value:
        return
    if isinstance(value, dict) and value:
        for key, item in value.items():
            yield from _terminals(item, prefix + (key,))
    else:
        yield prefix


def _defaults_bool(value):
    if type(value) in (bool, int, float):
        return value != 0
    return (
        isinstance(value, str)
        and re.match(
            r"[\t \u00a0\u1680\u2000-\u200b\u202f\u205f\u3000]*(?:[YyTt]|[+-]?0*[1-9])",
            value,
        )
        is not None
    )


def firefox_policy_sources(documents, policy_roots):
    enabled = False
    for document in documents:
        if "EnterprisePoliciesEnabled" in document:
            enabled = _defaults_bool(document["EnterprisePoliciesEnabled"])
    policies, app_state_entries = [], 0
    for document in documents:
        selected = {}
        for key, value in document.items():
            if key == "EnterprisePoliciesEnabled":
                continue
            if enabled and key.split("__", 1)[0] in policy_roots:
                selected[key] = value
            else:
                app_state_entries += sum(1 for _ in _terminals({key: value}))
        if selected:
            policies.append(_normalize_firefox_policy(selected))
    return {
        "enabled": enabled,
        "policies": policies,
        "app_state_entries": app_state_entries,
    }


def _supported(entry, version):
    if version == "unavailable":
        return False
    support = entry["browser"]["support"]
    observed = tuple(map(int, version.split(".")))
    minimum = tuple(map(int, support["min"].split(".")))
    width = max(len(observed), len(minimum))
    if observed + (0,) * (width - len(observed)) < minimum + (0,) * (
        width - len(minimum)
    ):
        return False
    if "max" in support:
        maximum = tuple(map(int, support["max"].split(".")))
        return (
            observed[: len(maximum)] + (0,) * max(0, len(maximum) - len(observed))
            <= maximum
        )
    return True


def read_browser(
    family,
    entries,
    profile,
    user_data,
    policy_paths,
    process_check=None,
):
    profile, user_data = Path(profile), Path(user_data)
    if process_check is None:
        process_check = lambda selected: check_browser_closed(
            selected, profile, user_data
        )
    process_check(family)
    rows = {
        key: row
        for key, row in entries.items()
        if row["browser"]["family"] == family
        and row["browser"]["kind"] != "lock_control"
    }
    sources = Sources()
    version = _browser_version(family, profile, user_data, sources)
    needed = {
        item["source"] for row in rows.values() for item in row["browser"]["read"]
    }
    stores = {}
    paths = {
        "firefox_prefs": profile / "prefs.js",
        "firefox_user": profile / "user.js",
        "chrome_preferences": profile / "Preferences",
        "chrome_local_state": user_data / "Local State",
        "chrome_secure_preferences": profile / "Secure Preferences",
    }
    required = "firefox_prefs" if family == "firefox" else "chrome_preferences"
    for kind in needed | {required}:
        if kind in paths:
            payload = sources.load(paths[kind], required=(kind == required))
            if payload is not None:
                stores[kind] = [parse_source(kind, payload)]
    policy_kind = family + "_policy"
    stores[policy_kind] = []
    for path in policy_paths:
        payload = sources.load(path)
        if payload is not None:
            stores[policy_kind].append(
                parse_source(
                    "firefox_domain" if family == "firefox" else policy_kind, payload
                )
            )
    app_state_entries = 0
    if family == "firefox":
        policy_roots = {
            descriptor["path"][0]
            for row in rows.values()
            for descriptor in row["browser"]["read"]
            if descriptor["source"] == "firefox_policy"
        }
        policy_sources = firefox_policy_sources(stores[policy_kind], policy_roots)
        stores[policy_kind] = policy_sources["policies"]
        app_state_entries = policy_sources["app_state_entries"]
    settings = {}
    known = {source: set() for source in SOURCE_STATES}
    for setting_id, row in sorted(rows.items()):
        observations = []
        groups = {}
        for descriptor in row["browser"]["read"]:
            source, path = descriptor["source"], descriptor["path"]
            known[source].add(tuple(path))
            group = descriptor.get("alternative", tuple(path))
            groups.setdefault(source, {}).setdefault(group, []).append(descriptor)
        incomplete, account_layer = False, False
        for source, source_groups in groups.items():
            for document in stores.get(source, []):
                layers = [("saved", document)]
                if source == "chrome_preferences" and "account_values" in document:
                    if not isinstance(document["account_values"], dict):
                        raise CaptureError(3, "Could not parse chrome_preferences.")
                    layers.append(("account", document["account_values"]))
                for layer, values in layers:
                    found_groups = 0
                    for alternatives in source_groups.values():
                        accepted, found_any = [], False
                        for descriptor in alternatives:
                            path = descriptor["path"]
                            found, value = _lookup(values, path)
                            if layer == "account":
                                known[source].add(("account_values",) + tuple(path))
                                known[source].add(("account_values", ".".join(path)))
                                if not found and ".".join(path) in values:
                                    found, value = True, values[".".join(path)]
                            if not found:
                                continue
                            found_any = True
                            if source == "firefox_policy" and _policy_conflict(value):
                                incomplete = True
                                accepted.append(
                                    {
                                        "source": source,
                                        "state": "unverified",
                                        "lock": "unknown",
                                    }
                                )
                                continue
                            expected = TYPE_NAMES[descriptor["type"]]
                            valid = isinstance(value, expected) and not (
                                descriptor["type"] in ("int", "float")
                                and isinstance(value, bool)
                            )
                            if not valid:
                                continue
                            if descriptor["conversion"] == "invert_bool":
                                if type(value) is not bool:
                                    continue
                                value = not value
                            allowed, filtered = filter_value(row, value)
                            observation = {
                                "source": source,
                                "state": SOURCE_STATES[source],
                                "lock": "unknown",
                            }
                            if layer == "account":
                                observation["layer"] = "account"
                            if allowed:
                                observation["value"] = filtered
                            else:
                                observation["state"] = "masked"
                            accepted.append(observation)
                        if found_any:
                            found_groups += 1
                            if layer == "account":
                                account_layer = True
                            observations.extend(
                                accepted
                                or [
                                    {
                                        "source": source,
                                        "state": "masked",
                                        "lock": "unknown",
                                    }
                                ]
                            )
                    if 0 < found_groups < len(source_groups):
                        incomplete = True
        item = {"scope": row["scope"], "observations": observations, "lock": "unknown"}
        if not _supported(row, version):
            item["state"] = "unavailable"
            item["observations"] = [
                {key: value for key, value in observation.items() if key != "value"}
                for observation in observations
            ]
        elif not row["browser"]["read"]:
            item["state"] = "unsupported"
        elif not observations:
            item["state"] = "absent"
        elif incomplete or account_layer:
            item["state"] = "unverified"
        elif any(observation["state"] == "masked" for observation in observations):
            item["state"] = "masked"
        elif all(
            type(observation["value"]) is type(observations[0]["value"])
            and observation["value"] == observations[0]["value"]
            for observation in observations
        ):
            item["state"] = "captured"
            item["value"] = observations[0]["value"]
        else:
            item["state"] = "conflict"
        settings[setting_id] = item
    unknown_persisted, unknown_policy = set(), 0
    for source, documents in stores.items():
        for document in documents:
            for path in _terminals(document):
                if not any(path[: len(mapped)] == mapped for mapped in known[source]):
                    if source == policy_kind:
                        unknown_policy += 1
                    elif family == "firefox":
                        unknown_persisted.add(path)
                    else:
                        unknown_persisted.add((source,) + path)
    process_check(family)
    sources.unchanged()
    counts = {
        state: sum(item["state"] == state for item in settings.values())
        for state in (
            "captured",
            "masked",
            "unavailable",
            "unsupported",
            "absent",
            "conflict",
            "unverified",
        )
    }
    counts.update(
        unmapped_persisted_entries=len(unknown_persisted),
        unmapped_policy_entries=unknown_policy,
        policy_entries=sum(
            1 for document in stores[policy_kind] for _ in _terminals(document)
        ),
        app_state_entries=app_state_entries,
    )
    return {
        "browser": family,
        "versions": {
            "browser": version,
            "browser_version_source": "saved_profile",
            "map_tested_on": sorted(
                {row["browser"]["support"]["tested"] for row in rows.values()}
            ),
            "macos": platform.mac_ver()[0] or "unavailable",
            "python": platform.python_version(),
        },
        "settings": settings,
        "counts": counts,
    }


def leaked_test_values(canaries, texts):
    return [
        index
        for index, canary in enumerate(canaries)
        if any(canary in text for text in texts)
    ]


def candidate_yaml(results):
    lines = [
        "---",
        "# Review candidate. The profile check refuses unsupported settings.",
        "# Saved configuration does not prove effective browser enforcement.",
        "# Review locally before sharing. Keep this file outside public repositories.",
    ]
    for result in results:
        family = result["browser"]
        versions = result["versions"]
        if versions.get("map_tested_on"):
            lines.append(
                "# "
                + family
                + " map tested on "
                + ", ".join(versions["map_tested_on"])
                + "; observed "
                + versions["browser"]
            )
        lines.append(
            "# " + family + " versions: " + json.dumps(versions, sort_keys=True)
        )
        lines.append(
            "# " + family + " counts: " + json.dumps(result["counts"], sort_keys=True)
        )
        lines.append(
            "# "
            + family
            + " policy sources: "
            + result.get("policy_sources", "saved_configuration")
        )
        lines.append(
            "# Unmapped persisted entries count saved names or terminal entries, not changes from defaults."
        )
        if family == "firefox":
            lines.append(
                "# firefox policy entries: "
                + str(result["counts"].get("policy_entries", 0))
                + "; app-state entries: "
                + str(result["counts"].get("app_state_entries", 0))
            )
    for section, scope in (
        ("machine_settings", "machine"),
        ("default_settings", "user"),
    ):
        assignments, comments = [], []
        for result in results:
            if scope == "machine":
                comments.append(
                    "  # "
                    + result["browser"]
                    + ".lock: true (recommendation only; choose after review)"
                )
            for setting_id, item in sorted(result["settings"].items()):
                if item["scope"] != scope:
                    continue
                observations = ", ".join(
                    observation["source"] + ":" + observation["state"]
                    for observation in item["observations"]
                )
                if item["state"] == "captured":
                    if scope == "machine" and any(
                        observation["state"] in ("persisted", "startup_override")
                        for observation in item["observations"]
                    ):
                        comments.append(
                            "  # "
                            + setting_id
                            + ": saved profile value suggests a machine policy; review its target scope."
                        )
                    assignments.append(
                        "  "
                        + setting_id
                        + ": "
                        + json.dumps(item["value"], ensure_ascii=True, allow_nan=False)
                    )
                else:
                    comments.append("  # " + setting_id + ": " + item["state"])
                if observations:
                    comments.append(
                        "  # "
                        + setting_id
                        + " sources: "
                        + observations
                        + "; lock unknown"
                    )
        lines.append(section + (":" if assignments else ": {}"))
        lines.extend(comments)
        lines.extend(sorted(assignments))
    return "\n".join(lines) + "\n"


def _open_directory(path):
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def write_candidate(path, text, forbidden_roots):
    path = Path(path)
    directory = None
    try:
        parent = path.parent.resolve(strict=True)
        destination = parent / path.name
        if any(part.suffix.lower() == ".app" for part in destination.parents):
            raise CaptureError(2, "Choose an output outside application bundles.")
        if any(
            destination == root.resolve() or root.resolve() in destination.parents
            for root in map(Path, forbidden_roots)
        ):
            raise CaptureError(
                2, "Choose an output outside the checkout and browser stores."
            )
        directory = _open_directory(parent)
        descriptor = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
    except OSError:
        if directory is not None:
            os.close(directory)
        raise CaptureError(2, "Could not create a new private output file.") from None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
            stream.flush()
    except BaseException as error:
        os.unlink(path.name, dir_fd=directory)
        if isinstance(error, (OSError, UnicodeError)):
            raise CaptureError(3, "Could not finish the private output file.") from None
        raise
    finally:
        os.close(directory)
