#!/usr/bin/python
import json
import os
import plistlib
import pwd
import subprocess
from dataclasses import dataclass
from typing import Optional
from xml.parsers.expat import ExpatError

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: macos_default_browser
short_description: Ask macOS once to make Firefox the running account's default browser
description:
  - Reads the HTTP and HTTPS handlers through the public LaunchServices API.
  - If the API cannot read, reads only HTTP and HTTPS from the account's LSHandlers preferences.
  - At the screen, asks macOS through the public NSWorkspace API for HTTP once.
  - Keeps the request process alive for up to 30 seconds while waiting for confirmation.
  - Reports a change only after both schemes identify Firefox.
  - Leaves SSH, other accounts, check mode and unattended runs as manual steps.
  - Never starts or closes Firefox, or writes its profile or the handler database.
options:
  browser:
    description: The browser to request.
    type: str
    choices: [firefox]
    default: firefox
  ask:
    description: Allow one macOS confirmation for the account at the screen.
    type: bool
    default: false
  interactive:
    description: The controller has terminal input and output for this run.
    type: bool
    default: false
"""

FIREFOX = "/Applications/Firefox.app"
MANUAL = (
    "browser.default: choose Firefox in System Settings > Desktop & Dock > "
    "Default web browser."
)
PROGRAM = """
ObjC.import("AppKit");
ObjC.import("CoreServices");
function defaults() {
  var out = {};
  ["http", "https"].forEach(function(scheme) {
    var handler = $.LSCopyDefaultHandlerForURLScheme($(scheme));
    out[scheme] = !handler.isNil() && ObjC.unwrap(handler) === "org.mozilla.firefox";
  });
  return out;
}
function run(argv) {
  if (argv[0] === "read") {
    return JSON.stringify(defaults());
  }
  if (argv[0] !== "request") {
    throw new Error("unsupported operation");
  }
  var workspace = $.NSWorkspace.sharedWorkspace;
  if (!workspace.respondsToSelector("setDefaultApplicationAtURL:toOpenURLsWithScheme:completionHandler:")) {
    return "unsupported";
  }
  workspace.setDefaultApplicationAtURLToOpenURLsWithSchemeCompletionHandler(
    $.NSURL.fileURLWithPath("/Applications/Firefox.app"), "http", null);
  var deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    var current = defaults();
    if (current.http && current.https) {
      break;
    }
    $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.1));
    $.NSThread.sleepForTimeInterval(0.1);
  }
  return "requested";
}
"""


class BrowserError(Exception):
    pass


@dataclass(frozen=True)
class Session:
    uid: int
    console_uid: Optional[int]
    ssh: bool

    @property
    def can_prompt(self):
        return self.uid > 0 and self.uid == self.console_uid and not self.ssh

    @classmethod
    def current(cls):
        try:
            console_uid = os.stat("/dev/console").st_uid
        except OSError:
            console_uid = None
        ssh = any(
            key in os.environ for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
        )
        return cls(os.geteuid(), console_uid, ssh)


class SubprocessRunner:
    def __init__(self, handlers_path=None):
        self.handlers_path = handlers_path

    def run(self, argv, timeout):
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            raise BrowserError(
                "browser.default: the macOS request or read did not finish"
            ) from None
        return result.returncode, result.stdout, result.stderr

    def read_handler_defaults(self):
        try:
            path = self.handlers_path
            if path is None:
                path = os.path.join(
                    pwd.getpwuid(os.geteuid()).pw_dir,
                    "Library/Preferences/com.apple.LaunchServices/"
                    "com.apple.launchservices.secure.plist",
                )
            with open(path, "rb") as source:
                preferences = plistlib.load(source)
        except (OSError, ValueError, KeyError, ExpatError):
            raise BrowserError(
                "browser.default: could not read the HTTP and HTTPS defaults"
            ) from None
        entries = (
            preferences.get("LSHandlers") if isinstance(preferences, dict) else None
        )
        if not isinstance(entries, list):
            raise BrowserError(
                "browser.default: could not read the HTTP and HTTPS defaults"
            )
        handlers = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise BrowserError(
                    "browser.default: could not read the HTTP and HTTPS defaults"
                )
            scheme = entry.get("LSHandlerURLScheme")
            if scheme not in ("http", "https"):
                continue
            handler = entry.get("LSHandlerRoleAll")
            if scheme in handlers or not isinstance(handler, str) or not handler:
                raise BrowserError(
                    "browser.default: could not read the HTTP and HTTPS defaults"
                )
            handlers[scheme] = handler == "org.mozilla.firefox"
        if set(handlers) != {"http", "https"}:
            raise BrowserError(
                "browser.default: could not read the HTTP and HTTPS defaults"
            )
        return all(handlers.values())


def command(runner, operation):
    return runner.run(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", PROGRAM, operation],
        timeout=40 if operation == "request" else 10,
    )


def read_defaults(runner):
    try:
        rc, out, _ = command(runner, "read")
        handlers = json.loads(out) if rc == 0 else None
    except (BrowserError, ValueError):
        handlers = None
    if (
        not isinstance(handlers, dict)
        or set(handlers) != {"http", "https"}
        or any(type(value) is not bool for value in handlers.values())
    ):
        return runner.read_handler_defaults()
    return all(handlers.values())


def firefox_installed():
    try:
        with open(f"{FIREFOX}/Contents/Info.plist", "rb") as source:
            info = plistlib.load(source)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    return (
        isinstance(info, dict)
        and info.get("CFBundleIdentifier") == "org.mozilla.firefox"
    )


def ensure_default(runner, ask, check_mode, session, installed, interactive=False):
    result = {"changed": False, "requested": False, "status": "manual", "notes": []}
    if read_defaults(runner):
        return dict(
            result,
            status="already_default",
            notes=[
                "browser.default: Firefox is already the default for HTTP and HTTPS."
            ],
        )
    if not installed:
        result["notes"] = [
            "browser.default: Install Firefox before choosing the default browser.",
            MANUAL,
        ]
        return result
    if check_mode or not ask or not interactive or not session.can_prompt:
        result["notes"] = [MANUAL]
        return result

    request_note = "browser.default: could not confirm delivery of the macOS request."
    try:
        rc, out, _ = command(runner, "request")
        if rc == 0 and out.strip() == "requested":
            result["requested"] = True
            request_note = (
                "browser.default: asked macOS once to make Firefox the default browser."
            )
        elif rc == 0 and out.strip() == "unsupported":
            request_note = "browser.default: this macOS version did not offer the default browser request API."
    except BrowserError:
        pass
    if read_defaults(runner):
        result.update(
            changed=True,
            status="confirmed",
            notes=["browser.default: macOS confirmed Firefox for HTTP and HTTPS."],
            diff={
                "before": "Firefox was not the default for both HTTP and HTTPS.\n",
                "after": "Firefox is the default for HTTP and HTTPS.\n",
            },
        )
    else:
        result["notes"] = [
            "browser.default: macOS has not confirmed Firefox for both HTTP and HTTPS.",
            MANUAL,
        ]
    result["notes"].insert(0, request_note)
    return result


def main():
    module = AnsibleModule(
        argument_spec={
            "browser": {"type": "str", "choices": ["firefox"], "default": "firefox"},
            "ask": {"type": "bool", "default": False},
            "interactive": {"type": "bool", "default": False},
        },
        supports_check_mode=True,
    )
    try:
        result = ensure_default(
            SubprocessRunner(),
            module.params["ask"],
            module.check_mode,
            Session.current(),
            firefox_installed(),
            interactive=module.params["interactive"],
        )
    except BrowserError as error:
        module.fail_json(msg=str(error))
    module.exit_json(**result)


if __name__ == "__main__":
    main()
