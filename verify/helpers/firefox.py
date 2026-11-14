#!/usr/bin/env python3
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import socket
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
POLICY = Path("/Library/Preferences/org.mozilla.firefox.plist")
OWNED = Path("/Library/Preferences/org.mac-setup.firefox-owned.plist")
LOCK = Path("/Library/Preferences/org.mac-setup.firefox-owned.lock")
DOMAIN = "/Library/Preferences/org.mozilla.firefox"
FIREFOX = "/Applications/Firefox.app/Contents/MacOS/firefox"
STATE = ROOT / ".local/firefox-journey"
MANAGED_HOME = "https://example.invalid/managed"
USER_HOME = "https://example.invalid/user-choice"
HISTORY_URL = "https://example.invalid/artificial-history"
BOOKMARK_URL = "https://example.invalid/artificial-bookmark"


class ProofError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ProofError(message)


def receive(stream):
    header = bytearray()
    while True:
        char = stream.read(1)
        require(bool(char), "Marionette closed an incomplete frame.")
        if char == b":":
            break
        require(
            char in b"0123456789" and len(header) < 8,
            "Invalid Marionette frame length.",
        )
        header.extend(char)
    require(bool(header), "Missing Marionette frame length.")
    size = int(header)
    require(0 < size <= 8 * 1024 * 1024, "Marionette frame is too large.")
    payload = bytearray()
    while len(payload) < size:
        block = stream.read(size - len(payload))
        require(bool(block), "Marionette closed an incomplete frame.")
        payload.extend(block)
    try:
        return json.loads(payload)
    except (UnicodeError, ValueError):
        raise ProofError("Invalid Marionette JSON.") from None


def response_value(response, sequence):
    require(
        isinstance(response, list)
        and len(response) == 4
        and response[:2] == [1, sequence],
        "Marionette returned an unexpected response.",
    )
    require(response[2] is None, "Marionette rejected the proof command.")
    return response[3]


class Marionette:
    def __init__(self, connection):
        self.connection = connection
        self.stream = connection.makefile("rb")
        self.sequence = 0
        hello = receive(self.stream)
        require(
            isinstance(hello, dict) and hello.get("marionetteProtocol") == 3,
            "Unsupported Marionette protocol.",
        )
        self.send("WebDriver:NewSession", {"capabilities": {}})

    def send(self, name, parameters):
        self.sequence += 1
        data = json.dumps([0, self.sequence, name, parameters]).encode()
        self.connection.sendall(str(len(data)).encode("ascii") + b":" + data)
        return response_value(receive(self.stream), self.sequence)

    def script(self, script, args=None, asynchronous=False):
        reply = self.send(
            (
                "WebDriver:ExecuteAsyncScript"
                if asynchronous
                else "WebDriver:ExecuteScript"
            ),
            {"script": script, "args": args or [], "scriptTimeout": 30000},
        )
        require(isinstance(reply, dict) and "value" in reply, "Missing script result.")
        return reply["value"]


def assert_policy_page(page, expected_names):
    require(isinstance(page, dict), "Policy page did not load.")
    require(page.get("url") == "about:policies", "Wrong policy page URL.")
    require(page.get("errorRows") == [], "Firefox reports policy errors.")
    rows = page.get("activeRows")
    require(isinstance(rows, list), "Policy page lacks active rows.")
    names = {cell for row in rows for cell in row if isinstance(cell, str)}
    require(expected_names <= names, "Policy page lacks an expected active policy.")


def typed_equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            typed_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            typed_equal(a, b) for a, b in zip(actual, expected)
        )
    return actual == expected


def policy_expectations(locked):
    expected = {
        "DontCheckDefaultBrowser": True,
        "Homepage": {"URL": MANAGED_HOME, "Locked": locked},
        "SanitizeOnShutdown": {"History": True, "Locked": locked},
        "Preferences": {
            "privacy.globalprivacycontrol.enabled": {
                "Value": True,
                "Status": "locked" if locked else "user",
            }
        },
    }
    if locked:
        expected["DisableProfileImport"] = True
    return expected


def assert_stored(document, locked):
    expected = dict(policy_expectations(locked), EnterprisePoliciesEnabled=True)
    if not locked:
        require(
            "DisableProfileImport" not in document,
            "Stored owned import restriction remains.",
        )
    for key, value in expected.items():
        require(
            key in document and typed_equal(document[key], value),
            "Stored policy type or value differs: " + key + ".",
        )


def assert_active(document, locked):
    expected = policy_expectations(locked)
    # Firefox's Mac reader leaves Preferences.Value numeric after schema validation.
    expected["Preferences"]["privacy.globalprivacycontrol.enabled"]["Value"] = 1
    if not locked:
        require(
            "DisableProfileImport" not in document,
            "Active owned import restriction remains.",
        )
    for key, value in expected.items():
        require(
            key in document and typed_equal(document[key], value),
            "Active policy type or value differs: " + key + ".",
        )


def assert_runtime(state, locked):
    expected = {
        "homepage": MANAGED_HOME if locked else USER_HOME,
        "homepageLocked": locked,
        "history": locked,
        "historyLocked": locked,
        "quitLocked": locked,
        "gpc": True,
        "gpcLocked": locked,
    }
    require(typed_equal(state, expected), "Effective preference or lock state differs.")


def command(argv):
    result = subprocess.run(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False
    )
    require(result.returncode == 0, "Firefox journey system command failed.")
    return result.stdout


def closed():
    spec = importlib.util.spec_from_file_location(
        "firefox_journey_reader", ROOT / "module_utils/browser_settings.py"
    )
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    require(
        not any(
            reader._is_browser("firefox", name) for name in reader._processes().values()
        ),
        "Close Firefox in every account before running this journey.",
    )


def fingerprint(path):
    require(not path.is_symlink(), "A policy store must not be a symbolic link.")
    if not path.exists():
        return {"exists": False}
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), "The policy plist must be a regular file.")
    try:
        contents = path.read_bytes()
    except PermissionError:
        contents = command(["/usr/bin/sudo", "-n", "/bin/cat", str(path)])
    return {
        "exists": True,
        "sha256": hashlib.sha256(contents).hexdigest(),
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mtime_ns": info.st_mtime_ns,
    }


def stores():
    return {"policy": POLICY, "ownership": OWNED, "lock": LOCK}


def fingerprints():
    return {name: fingerprint(path) for name, path in stores().items()}


def save_json(path, value):
    with path.open("w", encoding="utf-8") as output:
        os.chmod(path, 0o600)
        json.dump(value, output, indent=2)
        output.write("\n")


def seed_policy():
    expected = {"EnterprisePoliciesEnabled": True, "DontCheckDefaultBrowser": True}
    with tempfile.NamedTemporaryFile(dir=STATE, suffix=".plist") as output:
        output.write(plistlib.dumps(expected))
        output.flush()
        if POLICY.exists():
            command(["/usr/bin/sudo", "-n", "/usr/bin/defaults", "delete", DOMAIN])
        command(
            ["/usr/bin/sudo", "-n", "/usr/bin/defaults", "import", DOMAIN, output.name]
        )
    actual = plistlib.loads(
        command(["/usr/bin/sudo", "-n", "/usr/bin/defaults", "export", DOMAIN, "-"])
    )
    require(typed_equal(actual, expected), "Firefox journey seed differs.")


@contextmanager
def browser(root, profile=None, override=False):
    closed()
    root = Path(tempfile.mkdtemp(prefix=root.name + "-", dir=root.parent))
    profile = profile or root / "profile"
    profile.mkdir(mode=0o700, exist_ok=True)
    for name in ("app-data", "local-data"):
        (root / name).mkdir(mode=0o700)
    prefs = 'user_pref("marionette.port", 0);\n'
    if override:
        prefs += (
            'user_pref("browser.startup.homepage", ' + json.dumps(USER_HOME) + ");\n"
        )
        prefs += 'user_pref("privacy.clearOnShutdown.history", false);\n'
    (profile / "user.js").write_text(prefs, encoding="utf-8")
    port_file = profile / "MarionetteActivePort"
    if port_file.exists():
        port_file.unlink()
    environment = os.environ.copy()
    for key in ("XRE_PROFILE_PATH", "XRE_PROFILE_LOCAL_PATH"):
        environment.pop(key, None)
    environment["MOZ_APP_DATA"] = str(root / "app-data")
    environment["MOZ_LOCAL_APP_DATA"] = str(root / "local-data")
    with (root / "firefox.log").open("wb") as log:
        process = subprocess.Popen(
            [
                FIREFOX,
                "-headless",
                "-no-remote",
                "-profile",
                str(profile),
                "--marionette",
                "--remote-allow-system-access",
                "about:blank",
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            while not port_file.is_file():
                require(
                    process.poll() is None, "Firefox exited before Marionette started."
                )
                require(
                    time.monotonic() < deadline,
                    "Marionette did not start in 60 seconds.",
                )
                time.sleep(0.1)
            port = int(port_file.read_text(encoding="ascii").strip())
            require(0 < port < 65536, "Invalid Marionette port.")
            with socket.create_connection(
                ("127.0.0.1", port), timeout=30
            ) as connection:
                client = Marionette(connection)
                yield client, profile
                client.send("Marionette:Quit", {"flags": ["eAttemptQuit"]})
            require(
                process.wait(timeout=30) == 0, "Firefox quit with a nonzero exit code."
            )
            require(
                (profile / "prefs.js").is_file(),
                "Firefox did not save its disposable profile.",
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def chrome(client):
    client.send("Marionette:SetContext", {"value": "chrome"})


def content(client):
    client.send("Marionette:SetContext", {"value": "content"})


def policy_page(client, expected_names):
    content(client)
    client.send("WebDriver:Navigate", {"url": "about:policies"})
    deadline = time.monotonic() + 30
    while True:
        page = client.script("""
            const active = document.getElementById('activeContent');
            const errors = document.getElementById('errorsContent');
            if (!active?.classList.contains('active-policies') ||
                !errors?.classList.contains('errors')) return null;
            return {
              url: location.href,
              activeRows: Array.from(active.rows, row =>
                Array.from(row.cells, cell => cell.textContent.trim())),
              errorRows: Array.from(errors.rows, row =>
                Array.from(row.cells, cell => cell.textContent.trim()))
            };
            """)
        if page is not None:
            break
        require(time.monotonic() < deadline, "Policy page did not load in 30 seconds.")
        time.sleep(0.1)
    assert_policy_page(page, expected_names)
    chrome(client)
    return page


def bookmark_feasibility():
    html = STATE / "artificial-bookmarks.html"
    html.write_text(
        '<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<META HTTP-EQUIV="Content-Type" '
        'CONTENT="text/html; charset=UTF-8">\n<TITLE>Made-up bookmarks</TITLE>\n'
        "<H1>Made-up bookmarks</H1>\n<DL><p>\n"
        '<DT><A HREF="' + BOOKMARK_URL + '">Made-up bookmark</A>\n</DL><p>\n',
        encoding="utf-8",
    )
    with browser(STATE / "bookmarks") as (client, _):
        chrome(client)
        result = client.script(
            """
            const [path, url, done] = arguments;
            (async () => {
              const { BookmarkHTMLUtils } = ChromeUtils.importESModule(
                'resource://gre/modules/BookmarkHTMLUtils.sys.mjs');
              const { PlacesUtils } = ChromeUtils.importESModule(
                'resource://gre/modules/PlacesUtils.sys.mjs');
              if (Services.policies.getActivePolicies().DisableProfileImport)
                throw new Error('Import blocking already active');
              await BookmarkHTMLUtils.importFromFile(path, { replace: false });
              const matches = await PlacesUtils.bookmarks.search({ url });
              done({ imported: matches.length, title: matches[0]?.title });
            })().catch(() => done({ error: true }));
            """,
            [str(html), BOOKMARK_URL],
            asynchronous=True,
        )
        require(
            result == {"imported": 1, "title": "Made-up bookmark"},
            "Artificial bookmark import did not produce exactly one bookmark.",
        )
    save_json(
        STATE / "bookmark-proof.json", {"imported": 1, "before_import_blocking": True}
    )


def runtime_proof(stage, locked):
    root = STATE / (stage + "-browser")
    with browser(root, override=not locked) as (client, profile):
        expected = {
            "Homepage",
            "SanitizeOnShutdown",
            "DontCheckDefaultBrowser",
            "Preferences",
        }
        if locked:
            expected.add("DisableProfileImport")
        page = policy_page(client, expected)
        active = client.script(
            "return JSON.parse(JSON.stringify(Services.policies.getActivePolicies()));"
        )
        assert_active(active, locked)
        state = client.script("""
            return {
              homepage: Services.prefs.getStringPref('browser.startup.homepage'),
              homepageLocked: Services.prefs.prefIsLocked('browser.startup.homepage'),
              history: Services.prefs.getBoolPref('privacy.clearOnShutdown.history'),
              historyLocked: Services.prefs.prefIsLocked('privacy.clearOnShutdown.history'),
              quitLocked: Services.prefs.prefIsLocked('privacy.sanitize.sanitizeOnShutdown'),
              gpc: Services.prefs.getBoolPref('privacy.globalprivacycontrol.enabled'),
              gpcLocked: Services.prefs.prefIsLocked('privacy.globalprivacycontrol.enabled')
            };
            """)
        assert_runtime(state, locked)
        if stage == "apply":
            inserted = client.script(
                """
                const [url, done] = arguments;
                (async () => {
                  const { PlacesUtils } = ChromeUtils.importESModule(
                    'resource://gre/modules/PlacesUtils.sys.mjs');
                  await PlacesUtils.history.insert({ url, visits: [{ date: new Date() }] });
                  done(await PlacesUtils.history.hasVisits(url));
                })().catch(() => done(false));
                """,
                [HISTORY_URL],
                asynchronous=True,
            )
            require(inserted is True, "Artificial history entry was not created.")
    proof = {
        "policy_errors": len(page["errorRows"]),
        "checked_preferences": 4,
        "owned_locked_paths": sum(
            state[key]
            for key in ("homepageLocked", "historyLocked", "quitLocked", "gpcLocked")
        ),
        "user_override_reported": False,
    }
    if stage == "apply":
        with browser(STATE / "history-reopen", profile=profile) as (client, _):
            chrome(client)
            remains = client.script(
                """
                const [url, done] = arguments;
                const { PlacesUtils } = ChromeUtils.importESModule(
                  'resource://gre/modules/PlacesUtils.sys.mjs');
                PlacesUtils.history.hasVisits(url).then(done, () => done('error'));
                """,
                [HISTORY_URL],
                asynchronous=True,
            )
            require(remains is False, "Artificial history remained after normal quit.")
        proof["artificial_history_before_quit"] = 1
        proof["artificial_history_after_quit"] = 0
    if not locked:
        spec = importlib.util.spec_from_file_location(
            "firefox_journey_reader", ROOT / "module_utils/browser_settings.py"
        )
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        map_spec = importlib.util.spec_from_file_location(
            "firefox_journey_map", ROOT / "module_utils/browser_settings_data.py"
        )
        browser_map = importlib.util.module_from_spec(map_spec)
        map_spec.loader.exec_module(browser_map)
        setting_id = "firefox.sanitize_on_shutdown.history"
        result = reader.read_browser(
            "firefox",
            {setting_id: browser_map.BROWSER_SETTINGS[setting_id]},
            profile,
            profile.parent,
            [POLICY],
        )["settings"][setting_id]
        require(
            result["state"] == "conflict"
            and any(
                item["source"] == "firefox_user"
                and item["state"] == "startup_override"
                and item.get("value") is False
                for item in result["observations"]
            ),
            "Shared reader did not report the artificial user override.",
        )
        proof["user_override_reported"] = True
    save_json(
        STATE / (stage + "-policies-page.json"),
        {
            "url": "about:policies",
            "policy_errors": 0,
            "expected_policy_names": sorted(expected),
            "expected_policies_present": True,
            "active_policies_verified": True,
        },
    )
    save_json(STATE / (stage + "-proof.json"), proof)
    return proof


def setup():
    closed()
    require(Path(FIREFOX).is_file(), "Install Firefox before running this journey.")
    require(not STATE.is_symlink(), "The journey folder must not be a symbolic link.")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    require(
        not (STATE / "original.json").exists(),
        "Restore the saved Firefox policy before rerunning.",
    )
    original = fingerprints()
    for name, path in stores().items():
        if original[name]["exists"]:
            command(
                [
                    "/usr/bin/sudo",
                    "-n",
                    "/bin/cp",
                    "-p",
                    str(path),
                    str(STATE / ("original-" + name + ".plist")),
                ]
            )
    save_json(STATE / "original.json", original)
    if OWNED.exists():
        command(["/usr/bin/sudo", "-n", "/bin/rm", str(OWNED)])
    seed_policy()
    bookmark_feasibility()


def restore():
    metadata = STATE / "original.json"
    if not metadata.exists():
        return False
    require(
        not metadata.is_symlink(),
        "The saved snapshot record must not be a symbolic link.",
    )
    original = json.loads(metadata.read_text(encoding="utf-8"))
    for name, path in stores().items():
        saved = STATE / ("original-" + name + ".plist")
        require(
            not path.is_symlink() and not saved.is_symlink(),
            "Restore refuses a symbolic link in a policy store or snapshot.",
        )
        if original[name]["exists"]:
            if name == "policy":
                command(
                    [
                        "/usr/bin/sudo",
                        "-n",
                        "/usr/bin/defaults",
                        "import",
                        DOMAIN,
                        str(saved),
                    ]
                )
            command(["/usr/bin/sudo", "-n", "/bin/cp", "-p", str(saved), str(path)])
        elif path.exists():
            if name == "policy":
                command(["/usr/bin/sudo", "-n", "/usr/bin/defaults", "delete", DOMAIN])
            if path.exists():
                command(["/usr/bin/sudo", "-n", "/bin/rm", str(path)])
    require(
        fingerprints() == original,
        "Original Firefox policy or metadata was not restored.",
    )
    metadata.unlink()
    for name in stores():
        saved = STATE / ("original-" + name + ".plist")
        if saved.exists():
            command(["/usr/bin/sudo", "-n", "/bin/rm", str(saved)])
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Prove Firefox policies using disposable profiles."
    )
    parser.add_argument("operation", choices=["setup", "before", "after", "restore"])
    parser.add_argument(
        "--stage", choices=["preview", "apply", "again", "unlock"], default="preview"
    )
    arguments = parser.parse_args()
    if arguments.operation == "restore":
        restored = restore()
        print(
            "firefox original stores restored=true"
            if restored
            else "firefox restore snapshot_present=false writes=0"
        )
        return
    require(
        sys.platform == "darwin" and os.geteuid() != 0,
        "Run this journey as a standard Mac account.",
    )
    if arguments.operation == "setup":
        setup()
        print("firefox artificial bookmark imported=1 before_import_blocking=true")
    elif arguments.operation == "before":
        closed()
        require(
            (STATE / "original.json").is_file(),
            "Run this journey through scripts/verify so cleanup can restore it.",
        )
        save_json(STATE / "before.json", fingerprints())
        print("firefox precondition browsers_closed=true")
    else:
        closed()
        before = json.loads((STATE / "before.json").read_text(encoding="utf-8"))
        if arguments.stage in ("preview", "again"):
            require(
                fingerprints() == before,
                "Preview or second apply changed the policy plist.",
            )
        if arguments.stage == "preview":
            print(
                "firefox preview keys_written=0 bytes_unchanged=true metadata_unchanged=true"
            )
        else:
            assert_stored(
                plistlib.loads(POLICY.read_bytes()), locked=arguments.stage != "unlock"
            )
            proof = runtime_proof(arguments.stage, locked=arguments.stage != "unlock")
            print(
                "firefox " + arguments.stage + " " + json.dumps(proof, sort_keys=True)
            )


if __name__ == "__main__":
    try:
        main()
    except ProofError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, subprocess.SubprocessError):
        print(
            "Firefox journey proof failed. Original policies remain saved for cleanup.",
            file=sys.stderr,
        )
        sys.exit(1)
