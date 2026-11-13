#!/usr/bin/python
import os
import pwd
from pathlib import Path
from types import SimpleNamespace

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: macos_finder_sidebar
short_description: Order the Finder sidebar's favorite folders and remove sidebar items
description:
  - Reads the three sidebar lists FavoriteItems, FavoriteVolumes and TopSidebarSection with the compiled helper, which uses LSSharedFileList.
  - Changes the lists only when they differ from the wanted state, then reads them again to confirm.
  - favorites go first in FavoriteItems, in order. Other favorite folders stay after them unless favorites_only is true.
  - Each hide name removes its items from every list. Finder ignores the hidden flag of an item, so the module removes the item.
  - A "~" in a path means the home folder of the account that runs the module.
  - The helper lists each Desktop, Documents or Downloads folder before it adds the folder. For the account that runs mac-setup, the Permissions play asks for Files & Folders access before this module runs.
  - Three empty lists mean the program has no access to the sidebar, so the module refuses and changes nothing.
options:
  helper: {description: Path of the compiled sidebar helper., type: path, required: true}
  favorites: {description: Folders that come first in Favorites, in order., type: list, elements: str}
  favorites_only: {description: Remove every other favorite folder., type: bool, default: false}
  hide: {description: Sidebar items to remove., type: list, elements: str, default: [], choices: [recents, shared, airdrop, trash, icloud, home_in_locations]}
"""

LISTS = ("FavoriteItems", "FavoriteVolumes", "TopSidebarSection")
HIDE = {
    "recents": lambda name, url, home: "/myDocuments.cannedSearch" in url,
    "shared": lambda name, url, home: "/SharedDocuments.cannedSearch" in url,
    "airdrop": lambda name, url, home: url.startswith("nwnode://domain-AirDrop"),
    "trash": lambda name, url, home: url == "x-finder:Trash",
    "icloud": lambda name, url, home: url.startswith("x-apple-finder:icloud"),
    "home_in_locations": lambda name, url, home: name == "FavoriteVolumes"
    and url == home,
}
SPECIAL = [name for name in HIDE if name != "home_in_locations"]


REFUSED = (
    "macOS refused to add {} to the sidebar; Desktop, Documents and Downloads "
    "need Files & Folders access for the app that runs mac-setup"
)


class SidebarError(Exception):
    pass


def folder_url(path):
    return Path(path).as_uri().rstrip("/") + "/"


def home_folder():
    return pwd.getpwuid(os.getuid()).pw_dir


def wanted_paths(favorites, home, is_dir):
    paths = []
    for given in favorites:
        path = os.path.realpath(home + given[1:] if given[:1] == "~" else given)
        if path == "/":
            raise SidebarError(f"{given}: Finder refuses / as a favorite")
        if not is_dir(path):
            raise SidebarError(f"{given}: no folder at {path}")
        paths.append(path)
    return paths


def helper(runner, argv, given=None):
    rc, out, err = runner.run(argv)
    if rc == 3 and given:
        refused = err.strip().removeprefix("refused ")
        raise SidebarError(REFUSED.format(given.get(refused, refused)))
    if rc != 0:
        raise SidebarError(f"{' '.join(argv[1:3])} exited {rc}: {(out + err).strip()}")
    return out


def read_lists(runner, tool):
    lists = {name: [] for name in LISTS}
    for line in helper(runner, [tool, "read"]).splitlines():
        name, _, url = line.partition("\t")
        if name not in lists:
            raise SidebarError(f"read printed an unknown line: {line!r}")
        lists[name].append(url)
    return lists


def plan(lists, home_url, wanted, favorites_only, hide):
    removed = {
        name: [
            url for url in urls if any(HIDE[item](name, url, home_url) for item in hide)
        ]
        for name, urls in lists.items()
    }
    if favorites_only and wanted is not None:
        removed["FavoriteItems"] += [
            url
            for url in lists["FavoriteItems"]
            if url.startswith("file://")
            and url not in wanted
            and url not in removed["FavoriteItems"]
            and not any(HIDE[item]("FavoriteItems", url, home_url) for item in SPECIAL)
        ]
    after = {
        name: [url for url in urls if url not in removed[name]]
        for name, urls in lists.items()
    }
    if wanted is not None:
        favorites = after["FavoriteItems"]
        after["FavoriteItems"] = wanted + [
            url for url in favorites if url not in wanted
        ]
    return {name: urls for name, urls in removed.items() if urls}, after


def show(lists):
    return "".join(f"{name}: {', '.join(urls)}\n" for name, urls in lists.items())


def ensure_sidebar(
    runner, tool, favorites, favorites_only, hide, check_mode, home, is_dir
):
    paths = None if favorites is None else wanted_paths(favorites, home, is_dir)
    wanted = None if paths is None else [folder_url(path) for path in paths]
    home_url = folder_url(home)
    before = read_lists(runner, tool)
    if not any(before.values()):
        raise SidebarError(
            "the Finder sidebar lists read back empty, so this program has no access "
            "to them; run mac-setup from Terminal at the Mac"
        )
    removed, after = plan(before, home_url, wanted, favorites_only, hide)
    if after == before:
        return {"changed": False, "lists": before}
    if not check_mode:
        kept = [
            url
            for url in before["FavoriteItems"]
            if url not in removed.get("FavoriteItems", [])
        ]
        if paths and after["FavoriteItems"] != kept:
            helper(runner, [tool, "first", *paths], dict(zip(paths, favorites)))
        for name, urls in removed.items():
            helper(runner, [tool, "remove", name, *urls])
        now = read_lists(runner, tool)
        if now != after:
            raise SidebarError(
                f"after the change the sidebar shows {show(now)!r}, not {show(after)!r}"
            )
    return {
        "changed": True,
        "lists": after,
        "diff": {"before": show(before), "after": show(after)},
    }


def main():
    module = AnsibleModule(
        argument_spec={
            "helper": {"type": "path", "required": True},
            "favorites": {"type": "list", "elements": "str"},
            "favorites_only": {"type": "bool", "default": False},
            "hide": {
                "type": "list",
                "elements": "str",
                "default": [],
                "choices": list(HIDE),
            },
        },
        supports_check_mode=True,
    )
    params = module.params
    try:
        result = ensure_sidebar(
            SimpleNamespace(run=module.run_command),
            params["helper"],
            params["favorites"],
            params["favorites_only"],
            params["hide"],
            module.check_mode,
            home_folder(),
            os.path.isdir,
        )
    except SidebarError as error:
        module.fail_json(msg=str(error))
    module.exit_json(**result)


if __name__ == "__main__":
    main()
