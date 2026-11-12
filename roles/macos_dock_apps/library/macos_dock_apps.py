#!/usr/bin/python
import plistlib
import re
from pathlib import Path
from types import SimpleNamespace

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: macos_dock_apps
short_description: Keep only the listed apps in the Dock, by bundle id and in order
description:
  - Reads persistent-apps in the domain with defaults export, and writes it with defaults write only when its bundle ids differ from apps.
  - A listed app already in the Dock keeps its tile. A new one gets a tile for the installed app that mdfind finds for its bundle id.
  - Every other tile in persistent-apps is removed. persistent-others, the folders side of the Dock, stays as it is.
  - A bundle id with no installed app is refused before anything is read or written.
options:
  apps: {description: Bundle ids of the apps the Dock keeps, in order., type: list, elements: str, required: true}
  domain: {description: The preferences domain, or the path of a plist without its .plist suffix., type: str, default: com.apple.dock}
"""

DEFAULTS = "/usr/bin/defaults"
MDFIND = "/usr/bin/mdfind"
KEY = "persistent-apps"
BUNDLE_ID = re.compile(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
APP_FOLDERS = ("/Applications/", "/System/Applications/")


class DockAppsError(Exception):
    pass


def find_app(runner, bundle_id):
    if not BUNDLE_ID.fullmatch(bundle_id):
        return None
    rc, out, err = runner.run([MDFIND, f"kMDItemCFBundleIdentifier == '{bundle_id}'"])
    if rc != 0:
        raise DockAppsError(f"{bundle_id}: mdfind failed with exit {rc}: {out}{err}")
    paths = [line for line in out.splitlines() if line.endswith(".app")]
    if not paths:
        return None
    return min(paths, key=lambda path: (not path.startswith(APP_FOLDERS), path))


def label(tile):
    return tile.get("tile-data", {}).get("bundle-identifier") or tile.get("tile-type")


def new_tile(bundle_id, path):
    return {
        "tile-type": "file-tile",
        "tile-data": {
            "bundle-identifier": bundle_id,
            "file-label": Path(path).stem,
            "file-type": 41,
            "file-data": {
                "_CFURLString": Path(path).as_uri() + "/",
                "_CFURLStringType": 15,
            },
        },
    }


def read_tiles(runner, domain):
    rc, out, err = runner.run([DEFAULTS, "export", domain, "-"])
    if rc != 0:
        raise DockAppsError(
            f"{domain}: defaults export failed with exit {rc}: {out}{err}"
        )
    return plistlib.loads(out.encode()).get(KEY, [])


def lines(ids):
    return "".join(f"{bundle_id}\n" for bundle_id in ids)


def ensure_dock_apps(runner, domain, apps, check_mode):
    repeated = sorted({bundle_id for bundle_id in apps if apps.count(bundle_id) > 1})
    if repeated:
        raise DockAppsError(
            f"{', '.join(repeated)}: listed more than once, so the Dock is unchanged"
        )
    paths = {bundle_id: find_app(runner, bundle_id) for bundle_id in apps}
    missing = [bundle_id for bundle_id, path in paths.items() if path is None]
    if missing:
        raise DockAppsError(
            f"{', '.join(missing)}: no installed app has this bundle id, so the Dock is unchanged"
        )
    tiles = read_tiles(runner, domain)
    before = [label(tile) for tile in tiles]
    if before == apps:
        return {"changed": False, "apps": apps}
    kept = {}
    for tile in tiles:
        kept.setdefault(label(tile), tile)
    wanted = [
        kept.get(bundle_id) or new_tile(bundle_id, paths[bundle_id])
        for bundle_id in apps
    ]
    if not check_mode:
        xml = [plistlib.dumps(tile).decode() for tile in wanted]
        rc, out, err = runner.run([DEFAULTS, "write", domain, KEY, "-array", *xml])
        if rc != 0:
            raise DockAppsError(
                f"{domain}: defaults write failed with exit {rc}: {out}{err}"
            )
    return {
        "changed": True,
        "apps": apps,
        "diff": {"before": lines(before), "after": lines(apps)},
    }


def main():
    module = AnsibleModule(
        argument_spec={
            "apps": {"type": "list", "elements": "str", "required": True},
            "domain": {"type": "str", "default": "com.apple.dock"},
        },
        supports_check_mode=True,
    )
    try:
        result = ensure_dock_apps(
            SimpleNamespace(run=module.run_command),
            module.params["domain"],
            module.params["apps"],
            module.check_mode,
        )
    except DockAppsError as error:
        module.fail_json(msg=str(error))
    module.exit_json(**result)


if __name__ == "__main__":
    main()
