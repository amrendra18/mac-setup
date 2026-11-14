#!/usr/bin/python
import copy
import fcntl
import os
import plistlib
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from ansible.module_utils.basic import AnsibleModule

try:
    from ansible.module_utils import browser_settings as reader
    from ansible.module_utils.firefox_policies import (
        ENTRIES,
        FirefoxError,
        assign,
        lookup,
        owned_forms,
        plan_policies,
        remove,
        typed_equal,
        _validate_combination,
        validate_policy_aliases,
    )
except ImportError:
    import importlib.util

    def _local(name):
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parents[3] / "module_utils" / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    reader = _local("browser_settings")
    planner = _local("firefox_policies")
    ENTRIES, FirefoxError = planner.ENTRIES, planner.FirefoxError
    assign, lookup, owned_forms, plan_policies = (
        planner.assign,
        planner.lookup,
        planner.owned_forms,
        planner.plan_policies,
    )
    remove, typed_equal, _validate_combination = (
        planner.remove,
        planner.typed_equal,
        planner._validate_combination,
    )
    validate_policy_aliases = planner.validate_policy_aliases

DOCUMENTATION = r"""
module: macos_firefox
short_description: Merge selected Firefox machine policies and release owned settings
description:
  - Validates all settings, refuses running Firefox, and preserves unrelated policies.
  - Checks stored typed policies. Firefox acceptance requires a separate browser journey.
options:
  settings:
    description: Selected Firefox setting ids and values, including the optional lock switch.
    type: dict
    required: true
"""

DOMAIN = Path("/Library/Preferences/org.mozilla.firefox")
OWNED = Path("/Library/Preferences/org.mac-setup.firefox-owned.plist")
DEFAULTS = "/usr/bin/defaults"
POLICY_ROOTS = {
    descriptor["path"][0]
    for row in ENTRIES.values()
    if row["browser"]["family"] == "firefox"
    for descriptor in row["browser"]["read"]
    if descriptor["source"] == "firefox_policy"
}
PUBLIC_POLICY_KEYS = {
    "__".join(path[:length])
    for row in ENTRIES.values()
    if row["browser"]["family"] == "firefox"
    for path in owned_forms(row, ENTRIES)
    for length in range(1, len(path) + 1)
} | POLICY_ROOTS
PUBLIC_ARRAY_POLICY_PATHS = {
    "__".join(descriptor["path"])
    for row in ENTRIES.values()
    if row["browser"]["family"] == "firefox"
    for descriptor in row["browser"]["read"]
    if descriptor["source"] == "firefox_policy" and descriptor["type"] == "array"
}


def _contains_policy(fragment, document):
    if fragment is reader._POLICY_MISSING:
        return True
    if type(fragment) is not type(document):
        return False
    if isinstance(fragment, dict):
        return all(
            key in document and _contains_policy(value, document[key])
            for key, value in fragment.items()
        )
    if isinstance(fragment, list):
        return len(fragment) <= len(document) and all(
            _contains_policy(value, document[index])
            for index, value in enumerate(fragment)
        )
    return typed_equal(fragment, document)


def _policy_error(message, wanted, after, identifiers):
    def matches(path):
        wanted_found, wanted_value = lookup(wanted, path)
        after_found, after_value = lookup(after, path)
        return wanted_found == after_found and typed_equal(wanted_value, after_value)

    different = {
        key
        for key, paths in identifiers.items()
        if any(not matches(path) for path in paths)
    }
    if not matches(("EnterprisePoliciesEnabled",)):
        different.add("firefox.enterprise_policies_enabled")
    detail = " for " + ", ".join(sorted(different)) if different else ""
    return FirefoxError(message + detail + ". Firefox acceptance remains unverified.")


class PolicyStore:
    def __init__(self, runner, domain=DOMAIN, owned=OWNED, uid=0, managed_paths=None):
        self.runner = runner
        self.domain = Path(domain)
        self.path = self.domain.with_suffix(self.domain.suffix + ".plist")
        self.owned = Path(owned)
        self.lock = self.owned.with_suffix(".lock")
        self.uid = uid
        self.managed_paths = managed_paths

    def _safe(self, path):
        for parent in [path, *path.parents]:
            try:
                info = parent.lstat()
            except FileNotFoundError:
                if parent == path:
                    continue
                raise FirefoxError("The Firefox policy directory is missing.") from None
            if stat.S_ISLNK(info.st_mode):
                raise FirefoxError(
                    "Firefox policy paths must not contain symbolic links."
                )
            if parent == path:
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != self.uid
                    or info.st_mode & 0o022
                    or info.st_nlink != 1
                ):
                    raise FirefoxError(
                        "Firefox policy file ownership or permissions are unsafe."
                    )
            elif info.st_uid not in (0, self.uid) or (
                info.st_mode & 0o022 and not (info.st_mode & stat.S_ISVTX)
            ):
                raise FirefoxError("Firefox policy directory permissions are unsafe.")

    def guard(self):
        self._safe(self.path)
        self._safe(self.owned)
        managed = self.managed_paths
        if managed is None:
            root = Path("/Library/Managed Preferences")
            managed = [
                root / "org.mozilla.firefox.plist",
                *root.glob("*/org.mozilla.firefox.plist"),
            ]
        if any(path.exists() or path.is_symlink() for path in managed):
            raise FirefoxError(
                "Managed Firefox policies exist. Resolve that policy source before applying this profile."
            )

    def closed(self):
        rc, out, _ = self.runner.run(["/bin/ps", "-axo", "pid=,comm="])
        if rc:
            raise FirefoxError("Could not check Firefox processes in every account.")
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.strip().split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                raise FirefoxError(
                    "Could not check Firefox processes in every account."
                )
            if reader._is_browser("firefox", parts[1]):
                raise FirefoxError(
                    "Quit Firefox in every account before applying Firefox settings."
                )

    @contextmanager
    def transaction(self, check_mode):
        if check_mode:
            yield
            return
        self.guard()
        self._safe(self.lock)
        descriptor = os.open(self.lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if (
                info.st_uid != self.uid
                or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o022
                or info.st_nlink != 1
            ):
                raise FirefoxError("The Firefox policy lock file is unsafe.")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise FirefoxError(
                    "Another Firefox policy run is active. Retry after it finishes."
                ) from None
            yield
        finally:
            os.close(descriptor)

    def _raw(self):
        rc, out, _ = self.runner.run([DEFAULTS, "export", str(self.domain), "-"])
        if rc:
            if self.path.exists():
                raise FirefoxError("Could not export the Firefox policy domain.")
            return {}
        try:
            return reader.parse_source("firefox_domain", out.encode())
        except reader.CaptureError:
            raise FirefoxError("Could not parse the Firefox policy domain.") from None

    def read(self, allow_conflicts=False):
        try:
            value = reader._normalize_firefox_policy(self._raw(), POLICY_ROOTS)
        except reader.CaptureError:
            raise FirefoxError("Could not parse the Firefox policy domain.") from None
        if not allow_conflicts and reader._policy_conflict(value):
            raise FirefoxError("Nested and flattened Firefox policies conflict.")
        return value

    def ownership(self):
        self._safe(self.owned)
        if not self.owned.exists():
            return {"version": 1, "lock": True, "entries": {}}
        try:
            document = plistlib.loads(self.owned.read_bytes())
            if (
                set(document) != {"version", "lock", "entries"}
                or document["version"] != 1
                or type(document["lock"]) is not bool
                or not isinstance(document["entries"], dict)
            ):
                raise ValueError()
            for key, paths in document["entries"].items():
                if (
                    key not in ENTRIES
                    or not key.startswith("firefox.")
                    or not isinstance(paths, list)
                ):
                    raise ValueError()
                allowed = owned_forms(ENTRIES[key], ENTRIES)
                if any(
                    not isinstance(path, list)
                    or not all(isinstance(part, str) for part in path)
                    or tuple(path) not in allowed
                    for path in paths
                ):
                    raise ValueError()
            return document
        except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
            raise FirefoxError(
                "The Firefox ownership record is invalid. No policy was written."
            ) from None

    def write(self, before, document, identifiers):
        raw = self._raw()
        try:
            normalized = reader._normalize_firefox_policy(raw, POLICY_ROOTS)
        except reader.CaptureError:
            raise FirefoxError("Could not parse the Firefox policy domain.") from None
        if not typed_equal(before, normalized):
            raise FirefoxError(
                "Firefox policies changed before import. No policy was written."
            )
        stale = []
        for key, value in raw.items():
            if key in document:
                continue
            fragment = reader._normalize_firefox_policy({key: value}, POLICY_ROOTS)
            if _contains_policy(fragment, document):
                continue
            array_path, _, index = key.rpartition("__")
            if key not in PUBLIC_POLICY_KEYS and not (
                array_path in PUBLIC_ARRAY_POLICY_PATHS
                and index.isascii()
                and index.isdigit()
                and int(index) <= 50
            ):
                raise _policy_error(
                    "Firefox policy cleanup requires an unsupported stored key",
                    document,
                    normalized,
                    identifiers,
                )
            stale.append(key)
        descriptor, filename = tempfile.mkstemp(
            prefix=".mac-setup-firefox-", suffix=".plist", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                plistlib.dump(document, stream)
            rc, _, _ = self.runner.run([DEFAULTS, "import", str(self.domain), filename])
            if rc:
                raise FirefoxError(
                    "The Firefox policy import failed. Inspect the machine policy domain before retrying."
                )
            for key in stale:
                rc, _, _ = self.runner.run([DEFAULTS, "delete", str(self.domain), key])
                if rc:
                    raise _policy_error(
                        "Firefox policy key removal failed",
                        document,
                        normalized,
                        identifiers,
                    )
        finally:
            Path(filename).unlink(missing_ok=True)

    def record(self, document):
        if self.owned.exists() and typed_equal(
            plistlib.loads(self.owned.read_bytes()), document
        ):
            return
        descriptor, filename = tempfile.mkstemp(
            prefix=".mac-setup-firefox-owned-", dir=self.owned.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                plistlib.dump(document, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, self.owned)
        finally:
            Path(filename).unlink(missing_ok=True)


def ensure_firefox(store, settings, check_mode=False):
    plan = plan_policies(settings)
    if not settings:
        return _ensure_firefox(store, settings, plan, check_mode)
    with store.transaction(check_mode):
        return _ensure_firefox(store, settings, plan, check_mode)


def _ensure_firefox(store, settings, plan, check_mode):
    result = {
        "changed": False,
        "stored_verified": False,
        "pending_browser_start": False,
        "applied": sorted(plan["by_id"]),
        "released": plan["released"],
        "omitted": plan["omitted"],
        "notes": list(plan["notes"]),
    }
    if not settings:
        return result
    store.guard()
    store.closed()
    before = store.read()
    ownership = store.ownership()
    manifest_before = store.owned.read_bytes() if store.owned.exists() else None
    wanted = copy.deepcopy(before)
    previous = ownership["entries"]
    selected = set(settings) - {"firefox.lock"}
    identifiers = {
        key: owned_forms(ENTRIES[key], ENTRIES)
        for key in set(previous) | selected | set(plan["by_id"])
    }
    cleanup = set(selected)
    if ownership["lock"] and not plan["lock"]:
        cleanup.update(previous)
        result["released"] = sorted(
            set(result["released"]) | (set(previous) - selected)
        )
    kept = {key: paths for key, paths in previous.items() if key not in cleanup}
    cleaning = set()
    for key in cleanup:
        cleaning.update(tuple(path) for path in previous.get(key, []))
        if key in selected:
            cleaning.update(owned_forms(ENTRIES[key], ENTRIES))
    shared = {tuple(path) for paths in kept.values() for path in paths}
    for path in plan["writes"]:
        if any(
            path != retained
            and path[: min(len(path), len(retained))]
            == retained[: min(len(path), len(retained))]
            for retained in shared
        ):
            raise FirefoxError(
                "A selected Firefox policy overlaps a retained owned setting. "
                "Release the conflicting setting explicitly first."
            )
    for path in sorted(cleaning - shared, key=len, reverse=True):
        if any(retained[: len(path)] == path for retained in shared):
            continue
        remove(wanted, path)
    for path, value in plan["writes"].items():
        assign(wanted, path, value)
    coverage = {tuple(path) for paths in previous.values() for path in paths} | set(
        plan["writes"]
    )
    for path in cleaning | set(plan["writes"]):
        if path[-1] != "Locked":
            continue
        old_found, old_value = lookup(before, path)
        new_found, new_value = lookup(wanted, path)
        if old_found == new_found and typed_equal(old_value, new_value):
            continue
        _, siblings = lookup(wanted, path[:-1])
        unrelated = isinstance(siblings, dict) and any(
            key != "Locked"
            and not any(owned[: len(path)] == path[:-1] + (key,) for owned in coverage)
            for key in siblings
        )
        if unrelated:
            if path not in plan["writes"] and old_found:
                assign(wanted, path, old_value)
                result["notes"].append(
                    "An unrelated Firefox policy shares a lock. Its lock was preserved."
                )
            else:
                raise FirefoxError(
                    "A Firefox lock change would affect unrelated policy fields. "
                    "Resolve the shared policy before changing its lock."
                )
    if plan["writes"] or kept:
        wanted["EnterprisePoliciesEnabled"] = True
    _validate_combination(wanted)
    validate_policy_aliases(wanted, ENTRIES)
    result["changed"] = not typed_equal(before, wanted)
    if check_mode:
        return result
    store.guard()
    store.closed()
    if not typed_equal(before, store.read()) or manifest_before != (
        store.owned.read_bytes() if store.owned.exists() else None
    ):
        raise FirefoxError(
            "Firefox policies changed during planning. No policy was written. Retry after the other writer finishes."
        )
    if result["changed"]:
        recovery = copy.deepcopy(previous)
        for key, writes in plan["by_id"].items():
            recovery[key] = [
                list(path)
                for path in sorted(
                    {tuple(path) for path in recovery.get(key, [])} | set(writes)
                )
            ]
        store.record(
            {
                "version": 1,
                "lock": ownership["lock"] or plan["lock"],
                "entries": recovery,
            }
        )
        store.write(before, wanted, identifiers)
    after = store.read(allow_conflicts=True)
    if not typed_equal(wanted, after):
        raise _policy_error(
            "Firefox stored-policy read-back did not match", wanted, after, identifiers
        )
    evidence = reader.firefox_policy_sources([after], POLICY_ROOTS)
    if (plan["writes"] or kept) and not evidence["enabled"]:
        raise FirefoxError(
            "Firefox stored policies are not enabled in the read-back domain."
        )
    for key, writes in plan["by_id"].items():
        kept[key] = [list(path) for path in sorted(writes)]
    store.record({"version": 1, "lock": plan["lock"], "entries": kept})
    result["stored_verified"] = True
    result["pending_browser_start"] = bool(kept)
    if not plan["lock"] and plan["writes"]:
        result["notes"].append(
            "Firefox editable defaults may be overridden by existing user choices. Read-back of user overrides and browser acceptance remains pending."
        )
    result["notes"].append(
        "Firefox stored policies were checked. Relaunch Firefox to check policy errors and effective locks."
    )
    return result


def main():
    module = AnsibleModule(
        argument_spec={"settings": {"type": "dict", "required": True, "no_log": True}},
        supports_check_mode=True,
    )
    try:
        result = ensure_firefox(
            PolicyStore(SimpleNamespace(run=module.run_command)),
            module.params["settings"],
            module.check_mode,
        )
    except (FirefoxError, OSError, ValueError, TypeError, OverflowError) as error:
        module.fail_json(
            msg=(
                str(error)
                if isinstance(error, FirefoxError)
                else "Could not safely read or write Firefox policies."
            )
        )
    module.exit_json(**result)


if __name__ == "__main__":
    main()
