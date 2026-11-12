#!/usr/bin/python
import json
import os
from pathlib import Path
from types import SimpleNamespace

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: macos_wallpaper
short_description: Show one solid colour on every screen of the account that runs it
description:
  - Reads the desktop picture of every screen with the public AppKit call NSWorkspace desktopImageURLForScreen, through osascript.
  - Each screen that does not show the colour's picture gets it with NSWorkspace setDesktopImageURL, and the module reads every screen again to confirm.
  - The picture is one of the solid colour pictures that macOS ships in /System/Library/Desktop Pictures/Solid Colors.
  - macOS records the change in the account's wallpaper store itself. The module never writes that store.
  - It needs a screen visible to the session, so the account must be signed in. With none it fails with no_screen true and changes nothing.
options:
  color: {description: The colour to show., type: str, choices: [black], default: black}
"""

PICTURES = {"black": "/System/Library/Desktop Pictures/Solid Colors/Black.png"}
OSASCRIPT = "/usr/bin/osascript"
PROGRAM = """
ObjC.import("AppKit");
function run(argv) {
  var workspace = $.NSWorkspace.sharedWorkspace, screens = $.NSScreen.screens, out = [];
  for (var i = 0; i < screens.count; i++) {
    var screen = screens.objectAtIndex(i);
    if (argv[0] === "set") {
      out.push(workspace.setDesktopImageURLForScreenOptionsError($.NSURL.fileURLWithPath(argv[1]), screen, $({}), null));
    } else {
      var url = workspace.desktopImageURLForScreen(screen);
      out.push(url.isNil() ? "" : url.absoluteString.js);
    }
  }
  return JSON.stringify(out);
}
"""


class WallpaperError(Exception):
    pass


class NoScreen(WallpaperError):
    pass


def appkit(runner, *args):
    argv = [OSASCRIPT, "-l", "JavaScript", "-e", PROGRAM] + list(args)
    rc, out, err = runner.run(argv)
    try:
        values = json.loads(out) if rc == 0 else None
    except ValueError:
        values = None
    if not isinstance(values, list):
        raise WallpaperError(f"osascript {args[0]} exited {rc}: {out}{err}".strip())
    return values


def read_screens(runner):
    screens = appkit(runner, "read")
    if not screens:
        raise NoScreen(
            "no screen is visible to this session, so the desktop was left as it is; sign in to this account at the screen and run again"
        )
    return screens


def describe(screens):
    return "".join(f"screen {i}: {url or 'none'}\n" for i, url in enumerate(screens))


def ensure_color(runner, picture, check_mode):
    if not os.path.isfile(picture):
        raise WallpaperError(f"{picture} is missing, so the desktop was left as it is")
    want = Path(picture).as_uri()
    before = read_screens(runner)
    if all(url == want for url in before):
        return {"changed": False, "screens": before}
    after = [want] * len(before)
    if not check_mode:
        if not all(v is True for v in appkit(runner, "set", picture)):
            raise WallpaperError(f"macOS refused to show {picture} on every screen")
        after = read_screens(runner)
        if any(url != want for url in after):
            raise WallpaperError(
                f"macOS did not keep {picture} on every screen: {describe(after).strip()}"
            )
    return {
        "changed": True,
        "screens": after,
        "diff": {"before": describe(before), "after": describe(after)},
    }


def main():
    module = AnsibleModule(
        argument_spec={
            "color": {"type": "str", "choices": sorted(PICTURES), "default": "black"},
        },
        supports_check_mode=True,
    )
    runner = SimpleNamespace(run=module.run_command)
    try:
        result = ensure_color(
            runner, PICTURES[module.params["color"]], module.check_mode
        )
    except WallpaperError as error:
        module.fail_json(msg=str(error), no_screen=isinstance(error, NoScreen))
    module.exit_json(**result)


if __name__ == "__main__":
    main()
