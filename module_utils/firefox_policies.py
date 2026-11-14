import copy
import importlib.util
import plistlib
from pathlib import Path

try:
    from ansible.module_utils.browser_settings import addon_id, value_allowed
    from ansible.module_utils.browser_settings_data import BROWSER_SETTINGS
except ImportError:

    def _local(name):
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name(name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    _reader = _local("browser_settings")
    value_allowed, addon_id = _reader.value_allowed, _reader.addon_id
    BROWSER_SETTINGS = _local("browser_settings_data").BROWSER_SETTINGS

ENTRIES = BROWSER_SETTINGS


class FirefoxError(Exception):
    pass


def typed_equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            typed_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            typed_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def lookup(document, path):
    for segment in path:
        if not isinstance(document, dict) or segment not in document:
            return False, None
        document = document[segment]
    return True, document


def remove(document, path):
    if not path or not isinstance(document, dict) or path[0] not in document:
        return
    if len(path) == 1:
        del document[path[0]]
    else:
        remove(document[path[0]], path[1:])
        if document[path[0]] == {}:
            del document[path[0]]


def assign(document, path, value):
    for segment in path[:-1]:
        if segment in document and not isinstance(document[segment], dict):
            raise FirefoxError(
                "Firefox policy shapes overlap. Release the parent setting first."
            )
        document = document.setdefault(segment, {})
    document[path[-1]] = copy.deepcopy(value)


def _add(writes, path, value):
    path = tuple(path)
    for existing, previous in writes.items():
        common = min(len(path), len(existing))
        if path[:common] == existing[:common]:
            if path != existing or not typed_equal(value, previous):
                raise FirefoxError(
                    "Selected Firefox settings have conflicting policy paths."
                )
    writes[path] = value


def _writer(row, lock):
    browser = row["browser"]
    writer = browser["locked" if lock else "unlocked"]
    if lock and "unsupported" in writer:
        writer = browser["unlocked"]
    return writer


def _policy_writes(row, value, lock, entries):
    writer = _writer(row, lock)
    if writer.get("source") != "firefox_policy":
        return {}, writer.get("unsupported", "No machine policy form exists.")
    path = tuple(writer["path"])
    if row is entries.get("firefox.addons.install"):
        if len(value) != len(set(value)) or not all(addon_id(key) for key in value):
            raise FirefoxError("Firefox add-on ids must be valid and different.")
        return {
            ("ExtensionSettings", key, field): item
            for key in value
            for field, item in (
                (
                    "installation_mode",
                    "force_installed" if lock else "normal_installed",
                ),
                (
                    "install_url",
                    "https://addons.mozilla.org/firefox/downloads/latest/"
                    + key
                    + "/latest.xpi",
                ),
            )
        }, None
    if "value" in writer and not typed_equal(value, writer["value"]):
        raise FirefoxError("A selected Firefox Locked field contradicts firefox.lock.")
    if path == ("SanitizeOnShutdown",) and not lock:
        return (
            {},
            "The boolean SanitizeOnShutdown form always locks. Select named fields instead.",
        )
    writes = {path: value}
    if path[:1] == ("Preferences",) and path[-1] == "Value":
        writes[path[:-1] + ("Status",)] = (
            "locked" if lock and writer.get("level") != "user" else "user"
        )
        if type(value) is int:
            writes[path[:-1] + ("Type",)] = "number"
    if path[-1] != "Locked":
        for candidate in entries.values():
            field = candidate["browser"].get("locked", {})
            sibling = tuple(field.get("path", ()))
            if sibling == path[:-1] + ("Locked",):
                writes[sibling] = lock
    return writes, None


def owned_forms(row, entries):
    if row is entries.get("firefox.addons.install"):
        return set()
    result = set()
    for lock in (True, False):
        writer = _writer(row, lock)
        if writer.get("source") == "firefox_policy":
            path = tuple(writer["path"])
            result.add(path)
            if path[:1] == ("Preferences",) and path[-1] == "Value":
                result.add(path[:-1])
                result.update({path[:-1] + ("Status",), path[:-1] + ("Type",)})
            for candidate in entries.values():
                sibling = tuple(candidate["browser"].get("locked", {}).get("path", ()))
                if sibling == path[:-1] + ("Locked",):
                    result.add(sibling)
    return result


def owned_path_allowed(setting_id, path, entries):
    if setting_id == "firefox.addons.install":
        return (
            len(path) == 3
            and path[0] == "ExtensionSettings"
            and addon_id(path[1])
            and path[2] in ("installation_mode", "install_url")
        )
    return tuple(path) in owned_forms(entries[setting_id], entries)


def _validate_shared_preferences(settings, entries):
    observations = {}
    for setting_id, value in settings.items():
        if value == "unset" or setting_id == "firefox.lock":
            continue
        for descriptor in entries[setting_id]["browser"]["read"]:
            if descriptor["source"] != "firefox_prefs":
                continue
            conversion = descriptor["conversion"]
            if conversion not in ("identity", "invert_bool"):
                continue
            wanted = not value if conversion == "invert_bool" else value
            path = tuple(descriptor["path"])
            if path in observations and not typed_equal(observations[path], wanted):
                raise FirefoxError(
                    "Selected Firefox settings disagree on a shared preference."
                )
            observations[path] = wanted


def _validate_combination(document):
    homepage = document.get("Homepage", {})
    if (
        isinstance(homepage, dict)
        and homepage.get("StartPage") == "none"
        and "URL" in homepage
        and homepage["URL"] != "about:blank"
    ):
        raise FirefoxError(
            "firefox.homepage.url conflicts with firefox.homepage.start_page none, "
            "which forces about:blank. Use about:blank or unset one setting."
        )
    if "ClearOnShutdown" in document and "SanitizeOnShutdown" in document:
        raise FirefoxError(
            "ClearOnShutdown and SanitizeOnShutdown cannot be selected together."
        )
    support = document.get("SupportMenu")
    if isinstance(support, dict) and not {"Title", "URL"} <= set(support):
        raise FirefoxError("Firefox SupportMenu needs both Title and URL.")
    dns = document.get("DNSOverHTTPS", {})
    if isinstance(dns, dict) and dns.get("Enabled") is False and "Fallback" in dns:
        raise FirefoxError(
            "Firefox DNSOverHTTPS Enabled false requires Fallback unset."
        )
    tracking = document.get("EnableTrackingProtection", {})
    if (
        isinstance(tracking, dict)
        and "Category" in tracking
        and any(
            key in tracking
            for key in (
                "Value",
                "Cryptomining",
                "Fingerprinting",
                "EmailTracking",
                "SuspectedFingerprinting",
            )
        )
    ):
        raise FirefoxError(
            "Firefox tracking Category and individual protection switches overlap."
        )
    ai = document.get("AIControls", {})
    if (
        isinstance(ai, dict)
        and "TranslateEnabled" in document
        and any(key in ai for key in ("Default", "Translations"))
    ):
        raise FirefoxError("Firefox AIControls and TranslateEnabled overlap.")
    if (
        "PDFjs" in document
        and isinstance(ai, dict)
        and any(key in ai for key in ("Default", "PDFAltText"))
    ):
        raise FirefoxError("Firefox PDFjs and AIControls alt-text controls overlap.")
    try:
        plistlib.dumps(document)
    except (TypeError, ValueError, OverflowError):
        raise FirefoxError(
            "Selected Firefox values cannot be represented as policies."
        ) from None


def validate_policy_aliases(document, entries):
    preferences = {}
    for row in entries.values():
        if row["browser"]["family"] != "firefox":
            continue
        policy_values = []
        for descriptor in row["browser"]["read"]:
            if descriptor["source"] != "firefox_policy":
                continue
            found, value = lookup(document, descriptor["path"])
            if not found or not value_allowed(row["values"], value):
                continue
            if descriptor["conversion"] == "invert_bool":
                value = not value
            policy_values.append(value)
        for descriptor in row["browser"]["read"]:
            if descriptor["source"] != "firefox_prefs":
                continue
            for value in policy_values:
                if descriptor["conversion"] == "invert_bool":
                    value = not value
                key = tuple(descriptor["path"])
                if key in preferences and not typed_equal(preferences[key], value):
                    raise FirefoxError(
                        "Firefox policy aliases disagree on a shared preference. "
                        "Release the conflicting alias explicitly before applying."
                    )
                preferences[key] = value


def plan_policies(settings, entries=None):
    entries = ENTRIES if entries is None else entries
    if not isinstance(settings, dict):
        raise FirefoxError("Firefox settings must map setting ids to values.")
    lock = settings.get("firefox.lock", True)
    if type(lock) is not bool:
        raise FirefoxError("firefox.lock must be true or false.")
    for setting_id, value in settings.items():
        row = entries.get(setting_id)
        if row is None or not setting_id.startswith("firefox."):
            raise FirefoxError("An unknown Firefox setting was selected.")
        if row["route"] != "firefox":
            raise FirefoxError(
                setting_id
                + ": this Firefox setting has no supported application route."
            )
        if (
            setting_id != "firefox.lock"
            and value != "unset"
            and not value_allowed(row["values"], value)
        ):
            raise FirefoxError(setting_id + ": the Firefox value is invalid.")
    _validate_shared_preferences(settings, entries)
    writes, by_id, omitted, released, notes = {}, {}, [], [], []
    for setting_id, value in sorted(settings.items()):
        if setting_id == "firefox.lock":
            continue
        row = entries[setting_id]
        if value == "unset":
            released.append(setting_id)
            continue
        if (
            setting_id == "firefox.homepage.start_page"
            and value == "homepage-locked"
            and not lock
        ):
            value = "homepage"
            notes.append(
                setting_id
                + ": homepage-locked uses editable homepage while firefox.lock is false."
            )
        assignments, reason = _policy_writes(row, value, lock, entries)
        if reason:
            omitted.append({"id": setting_id, "reason": reason})
            continue
        for path, item in assignments.items():
            _add(writes, path, item)
        by_id[setting_id] = assignments
        if setting_id == "firefox.owner_label":
            notes.append("Firefox owner label: " + value)
        if setting_id == "firefox.pref.browser_ui_customization_state":
            notes.append(
                "Firefox toolbar layout stays editable so you can rearrange buttons. The policy reapplies at startup."
            )
        if lock and "unsupported" in row["browser"]["locked"]:
            notes.append(
                setting_id + ": applied as an editable seed; this setting cannot lock."
            )
    document = {}
    for path, value in writes.items():
        assign(document, path, value)
    _validate_combination(document)
    return {
        "lock": lock,
        "writes": writes,
        "by_id": by_id,
        "released": released,
        "omitted": omitted,
        "notes": notes,
    }
