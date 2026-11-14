#!/usr/bin/env python3
import argparse
from contextlib import contextmanager
import errno
import hashlib
from html import escape
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "firefox_journey", Path(__file__).with_name("firefox.py")
)
firefox = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firefox)
IMPORT_SPEC = importlib.util.spec_from_file_location(
    "bookmarks_importer", ROOT / "module_utils/firefox_bookmarks.py"
)
importer = importlib.util.module_from_spec(IMPORT_SPEC)
IMPORT_SPEC.loader.exec_module(importer)
STATE = ROOT / ".local/bookmarks-journey"
STAGES = (
    "preview",
    "apply",
    "again",
    "changed",
    "retry",
    "partial",
    "partial-again",
    "existing",
    "existing-again",
    "refusal",
)
PLACES_TREE_SCRIPT = """
const { PlacesUtils } = ChromeUtils.importESModule('resource://gre/modules/PlacesUtils.sys.mjs');
const bookmarks = PlacesUtils.bookmarks;
async function readTree() {
  const rows = [];
  async function visit(parentGuid) {
    const children = [];
    await bookmarks.fetch({parentGuid}, row => children.push(row));
    children.sort((left, right) => left.index - right.index);
    for (const row of children) {
      rows.push({guid: row.guid, parentGuid: row.parentGuid, index: row.index,
        type: row.type, title: row.title || '', url: row.url ? row.url.href : ''});
      if (row.type === bookmarks.TYPE_FOLDER) await visit(row.guid);
    }
  }
  for (const root of [bookmarks.menuGuid, bookmarks.toolbarGuid, bookmarks.unfiledGuid]) {
    await visit(root);
  }
  return rows;
}
"""


class JourneyCheckError(firefox.ProofError):
    pass


def require(condition, message):
    if not condition:
        raise JourneyCheckError(message)


class JourneyArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise JourneyCheckError(
            "Command arguments must name a supported operation and stage."
        )


def write_private(path, contents):
    with path.open("wb") as output:
        os.chmod(path, 0o600)
        output.write(contents)


def firefox_version(value):
    return (
        value
        if isinstance(value, str)
        and re.fullmatch(r"[0-9]{1,4}(?:\.[0-9]{1,4}){0,3}", value)
        else "unknown"
    )


def private_values():
    values = json.loads((STATE / "values.json").read_text(encoding="utf-8"))
    if (STATE / "password").exists():
        values["_password"] = (STATE / "password").read_text(encoding="utf-8").strip()
    return values


def fixture_tree(entries):
    return [
        {
            "type": 2,
            "title": "Artificial collection",
            "children": [
                {
                    "type": 2,
                    "title": "Artificial nested",
                    "children": [dict(type=1, **item) for item in entries[:2]],
                },
                {
                    "type": 2,
                    "title": "Artificial second",
                    "children": [dict(type=1, **item) for item in entries[2:]],
                },
            ],
        }
    ]


def fixture_html(entries):
    def render(nodes):
        parts = []
        for node in nodes:
            title = escape(node["title"])
            if node["type"] == 2:
                parts.append(
                    f"<DT><H3>{title}</H3>\n<DL><p>\n"
                    + render(node["children"])
                    + "</DL><p>\n"
                )
            else:
                parts.append(
                    f'<DT><A HREF="{escape(node["url"], quote=True)}">{title}</A>\n'
                )
        return "".join(parts)

    return (
        '<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">\n'
        "<TITLE>Artificial input</TITLE>\n<H1>Artificial input</H1>\n<DL><p>\n"
        '<DT><H3 PERSONAL_TOOLBAR_FOLDER="true">Bookmarks Toolbar</H3>\n<DL><p>\n'
        + render(fixture_tree(entries))
        + "</DL><p>\n</DL><p>\n"
    )


def invalid_html(values):
    return fixture_html(values["bookmarks"]).replace(
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>", "<TITLE>Malformed test input</TITLE>"
    )


def make_fixture():
    from ansible.parsing.vault import VaultLib, VaultSecret

    token = secrets.token_hex(16)
    values = {
        "bookmarks": [
            {
                "title": f"Artificial {token} {number}",
                "url": f"https://example.invalid/{token}/{number}",
            }
            for number in range(3)
        ],
        "existing": {
            "title": f"Existing {token}",
            "url": f"https://example.invalid/{token}/existing",
        },
        "extra": {
            "title": f"Later {token}",
            "url": f"https://example.invalid/{token}/later",
        },
    }
    password = secrets.token_urlsafe(32).encode()
    vault = VaultLib([("default", VaultSecret(password))])
    for name, entries in (
        ("source", values["bookmarks"]),
        ("changed", values["bookmarks"] + [values["extra"]]),
    ):
        write_private(
            STATE / (name + ".vault"), vault.encrypt(fixture_html(entries).encode())
        )
    write_private(
        STATE / "invalid.vault",
        vault.encrypt(invalid_html(values).encode()),
    )
    write_private(STATE / "password", password + b"\n")
    write_private(STATE / "values.json", json.dumps(values).encode())


@contextmanager
def policy_state():
    previous = firefox.STATE
    firefox.STATE = STATE / "policy"
    try:
        yield
    finally:
        firefox.STATE = previous


def prove_startup_port():
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        raise JourneyCheckError(
            "Default-port startup control needs a loopback socket."
        ) from None
    try:
        try:
            listener.bind(("127.0.0.1", 2828))
            listener.listen(1)
        except OSError as error:
            require(
                error.errno == errno.EADDRINUSE,
                "Default-port startup control must occupy port 2828.",
            )
        try:
            with importer.FirefoxSession(STATE / "startup-control") as session:
                require(
                    type(session.port) is int
                    and 0 < session.port < 65536
                    and session.port != 2828,
                    "Firefox startup must choose a free port while port 2828 is occupied.",
                )
                version = firefox_version(session.version)
        except (importer.PermissionDenied, importer.StartupError):
            raise
        except importer.BookmarkError:
            raise JourneyCheckError(
                "Default-port startup control could not start and stop Firefox through LaunchServices."
            ) from None
        write_private(
            STATE / "startup-control.json",
            json.dumps(
                {
                    "default_port_occupied": True,
                    "selected_default_port": False,
                    "firefox_version": version,
                }
            ).encode(),
        )
    finally:
        listener.close()


def seed_policy():
    require(
        not firefox.STATE.is_symlink(),
        "The policy snapshot folder must not be a symbolic link.",
    )
    firefox.STATE.mkdir(mode=0o700, parents=True)
    original = firefox.fingerprints()
    for name, path in firefox.stores().items():
        if original[name]["exists"]:
            firefox.command(
                [
                    "/usr/bin/sudo",
                    "-n",
                    "/bin/cp",
                    "-p",
                    str(path),
                    str(firefox.STATE / ("original-" + name + ".plist")),
                ]
            )
    firefox.save_json(firefox.STATE / "original.json", original)
    if firefox.OWNED.exists():
        firefox.command(["/usr/bin/sudo", "-n", "/bin/rm", str(firefox.OWNED)])
    firefox.seed_policy()
    firefox.command(
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/defaults",
            "write",
            firefox.DOMAIN,
            "DisableProfileImport",
            "-bool",
            "true",
        ]
    )


def setup():
    require(
        bool(os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE"))
        and Path(os.environ["ANSIBLE_VAULT_PASSWORD_FILE"]).absolute()
        == STATE / "password",
        "Test vault environment (ANSIBLE_VAULT_PASSWORD_FILE) must select the journey password file.",
    )
    firefox.closed()
    require(
        not STATE.exists(), "Restore the previous bookmarks journey before rerunning."
    )
    STATE.mkdir(mode=0o700, parents=True)
    with policy_state():
        seed_policy()
    make_fixture()
    prove_startup_port()
    write_private(
        STATE / "temporary-before.json", json.dumps(temporary_directories()).encode()
    )


def temporary_directories():
    roots = {Path(tempfile.gettempdir()), Path("/private/tmp")}
    return sorted(
        str(path)
        for root in roots
        for path in root.glob("mac-setup-bookmarks-*")
        if path.is_dir()
    )


def target(stage):
    name = "firefox"
    if stage.startswith("existing"):
        name = "existing-firefox"
    elif stage.startswith("partial"):
        name = "partial-firefox"
    elif stage == "refusal":
        name = "refused-firefox"
    return STATE / name


def seed_existing(root, values):
    with importer.FirefoxSession(root) as session:
        result = session.script(
            """
            const [entry, done] = arguments;
            (async () => {
            """
            + PLACES_TREE_SCRIPT
            + """
              const folder = await bookmarks.insert({parentGuid: bookmarks.toolbarGuid,
                index: 0, type: bookmarks.TYPE_FOLDER, title: 'Existing folder'});
              await bookmarks.insert({parentGuid: folder.guid, index: 0, type: bookmarks.TYPE_SEPARATOR});
              await bookmarks.insert({parentGuid: folder.guid, index: 1, title: entry.title, url: entry.url});
              done({seeded: true, tree: await readTree()});
            })().catch(() => done(false));
            """,
            [values["existing"]],
            asynchronous=True,
        )
        require(
            isinstance(result, dict)
            and result.get("seeded") is True
            and isinstance(result.get("tree"), list)
            and bool(result["tree"]),
            "The artificial existing bookmark could not be seeded.",
        )
        write_private(STATE / "existing-tree.json", json.dumps(result["tree"]).encode())


def seed_partial(root, values):
    tree = importer.parse_html(fixture_html(values["bookmarks"]))

    def first_bookmark(nodes):
        for node in nodes:
            if node["type"] == 1:
                return [node]
            if node["type"] == 2:
                children = first_bookmark(node["children"])
                if children:
                    return [dict(node, children=children)]
        return []

    partial = dict(tree, toolbar=first_bookmark(tree["toolbar"]))
    with importer.FirefoxSession(root) as session:
        require(
            session.import_tree(partial) == 1,
            "The artificial partial import did not commit one bookmark.",
        )
        require(
            not importer.completion_path(root, session.profile_key).exists(),
            "The partial import unexpectedly has a completion record.",
        )


def tree_fingerprint(root):
    return {
        str(path.relative_to(root)): [
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
            path.stat().st_mode,
        ]
        for path in root.rglob("*")
        if path.is_file()
    }


def before(stage, inventory):
    firefox.closed()
    require(
        (STATE / "values.json").is_file(),
        "Run bookmarks through scripts/verify so cleanup can restore policies.",
    )
    root = target(stage)
    if stage == "preview":
        control = json.loads((STATE / "startup-control.json").read_text())
        require(
            isinstance(control, dict)
            and control.get("default_port_occupied") is True
            and control.get("selected_default_port") is False,
            "The setup startup control is absent or incomplete.",
        )
        write_private(
            inventory.parents[2] / "reads/bookmarks-startup-control.json",
            json.dumps(
                {
                    "default_port_occupied": True,
                    "selected_default_port": False,
                    "firefox_version": firefox_version(control.get("firefox_version")),
                }
            ).encode(),
        )
    if stage == "existing":
        seed_existing(root, private_values())
    if stage == "partial":
        seed_partial(root, private_values())
    if stage == "retry":
        with importer.FirefoxSession(root) as session:
            record = importer.completion_path(root, session.profile_key)
            require(
                record.is_file(),
                "The completion record is absent before the interruption test.",
            )
            record.unlink()
    files = inventory.parent / "files"
    files.mkdir(mode=0o700, exist_ok=True)
    source = STATE / (
        "changed.vault"
        if stage == "changed"
        else "invalid.vault" if stage == "refusal" else "source.vault"
    )
    write_private(files / "bookmarks.vault", source.read_bytes())
    write_private(STATE / "evidence-path", str(inventory.parents[2]).encode())
    write_private(
        STATE / "before.json",
        json.dumps(tree_fingerprint(importer.state_root(root))).encode(),
    )


def read_counts(root, values, existing=False):
    baseline = (
        json.loads((STATE / "existing-tree.json").read_text()) if existing else None
    )
    require(
        not existing or isinstance(baseline, list) and bool(baseline),
        "The artificial existing tree snapshot is absent or empty.",
    )
    with importer.FirefoxSession(root) as session:
        result = session.script(
            """
            const [items, old, extra, expected, baseline, done] = arguments;
            (async () => {
            """
            + PLACES_TREE_SCRIPT
            + """
              const rows = await readTree();
              const matches = await Promise.all(items.map(item => bookmarks.search({url: item.url})));
              const previous = await bookmarks.search({url: old.url});
              const later = await bookmarks.search({url: extra.url});
              function layout(nodes, parent) {
                return nodes.flatMap((node, index) => {
                  const item = {parent, index, type: node.type, title: node.title, url: node.url || ''};
                  return [item, ...(node.type === bookmarks.TYPE_FOLDER
                    ? layout(node.children, [...parent, node.title]) : [])];
                });
              }
              function actualLayout(parentGuid, parent) {
                return rows.filter(row => row.parentGuid === parentGuid).flatMap(row => {
                  const item = {parent, index: row.index, type: row.type, title: row.title, url: row.url};
                  return [item, ...(row.type === bookmarks.TYPE_FOLDER
                    ? actualLayout(row.guid, [...parent, row.title]) : [])];
                });
              }
              const toolbar = rows.filter(row => row.parentGuid === bookmarks.toolbarGuid);
              const imported = toolbar.filter(row => row.title === expected[0].title);
              const wanted = layout(expected, ['toolbar']);
              wanted[0].index = toolbar.length - 1;
              const actual = imported.length === 1 ? [
                {parent: ['toolbar'], index: imported[0].index, type: imported[0].type,
                  title: imported[0].title, url: imported[0].url},
                ...actualLayout(imported[0].guid, ['toolbar', imported[0].title])
              ] : [];
              const fields = ['guid', 'parentGuid', 'index', 'type', 'title', 'url'];
              const oldRows = baseline ? rows.filter(row => baseline.some(old => old.guid === row.guid)) : [];
              const preserved = baseline ? oldRows.length === baseline.length &&
                oldRows.every((row, index) => fields.every(field => row[field] === baseline[index][field]))
                : previous.length === 0;
              done({blocked: Services.policies.getActivePolicies().DisableProfileImport === true,
                counts: matches.map(rows => rows.length),
                titles: matches.every((rows, index) => rows.length === 1 && rows[0].title === items[index].title),
                existing: previous.every(row => row.title === old.title) ? previous.length : -1,
                extra: later.length, structure: JSON.stringify(actual) === JSON.stringify(wanted), preserved});
            })().catch(() => done({error: true}));
            """,
            [
                values["bookmarks"],
                values["existing"],
                values["extra"],
                fixture_tree(values["bookmarks"]),
                baseline,
            ],
            asynchronous=True,
        )
        expected = {
            "blocked": True,
            "counts": [1] * len(values["bookmarks"]),
            "titles": True,
            "existing": int(existing),
            "extra": 0,
            "structure": True,
            "preserved": True,
        }
        require(
            isinstance(result, dict)
            and result.keys() == expected.keys()
            and all(type(result[key]) is type(value) for key, value in expected.items())
            and all(type(count) is int for count in result["counts"]),
            "The bookmark readback result is invalid.",
        )
        require(
            result["blocked"] == expected["blocked"],
            "The active DisableProfileImport policy is not true.",
        )
        require(
            result["counts"] == expected["counts"],
            "The bookmark readback found an unexpected copy count for a source address.",
        )
        require(
            result["titles"] == expected["titles"],
            "The bookmark readback titles differ from the source.",
        )
        require(
            result["existing"] == expected["existing"],
            "The bookmark readback existing bookmark count differs.",
        )
        require(
            result["extra"] == expected["extra"],
            "The bookmark readback found unexpected extra bookmarks.",
        )
        require(
            result["structure"] == expected["structure"],
            "The bookmark readback ordered folder tree differs.",
        )
        require(
            result["preserved"] == expected["preserved"],
            "The bookmark readback did not preserve existing entries, folders, and positions.",
        )
    return len(values["bookmarks"])


def scan_private(values, roots):
    entries = values["bookmarks"] + [
        values[key] for key in ("existing", "extra") if key in values
    ]
    needles = [value.encode() for entry in entries for value in entry.values()]
    if "_password" in values:
        needles.append(values["_password"].encode())
    copies = 0
    for root in roots:
        for path in ([root] if root.is_file() else root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                contents = path.read_bytes()
                copies += sum(contents.count(needle) for needle in needles)
    require(
        copies == 0, "Artificial private bookmark content appeared in logs or evidence."
    )
    return copies


def after(stage, inventory):
    firefox.closed()
    root = target(stage)
    if stage == "preview":
        saved = json.loads((STATE / "before.json").read_text())
        require(
            tree_fingerprint(importer.state_root(root)) == saved and not root.exists(),
            "The preview wrote to the fresh Firefox target.",
        )
        print("bookmarks preview target_writes=0")
        return
    if stage in ("again", "changed", "partial-again", "existing-again"):
        saved = json.loads((STATE / "before.json").read_text())
        require(
            tree_fingerprint(importer.state_root(root)) == saved,
            "A completed import changed its completion records.",
        )
    with importer.FirefoxSession(root) as session:
        require(
            importer.completion_path(root, session.profile_key).is_file(),
            "The import completion record is absent.",
        )
    count = read_counts(root, private_values(), stage.startswith("existing"))
    previous = json.loads((STATE / "temporary-before.json").read_text())
    require(
        temporary_directories() == previous,
        "A decrypted bookmarks directory remains after import.",
    )
    scan_private(private_values(), [ROOT / ".local/logs", inventory.parents[2]])
    print(
        f"bookmarks {stage} imported_count={count} existing_count={int(stage.startswith('existing'))} extra_count=0 private_copies=0 plaintext_directories=0"
    )


def remove_plaintext(values):
    record = STATE / "temporary-before.json"
    if not record.exists():
        return
    previous = json.loads(record.read_text())
    expected = [
        fixture_html(values["bookmarks"]).encode(),
        fixture_html(values["bookmarks"] + [values["extra"]]).encode(),
        invalid_html(values).encode(),
    ]
    for name in set(temporary_directories()) - set(previous):
        directory = Path(name)
        source = directory / "bookmarks.html"
        require(
            not directory.is_symlink() and directory.stat().st_uid == os.getuid(),
            "A remaining temporary directory is outside the artificial test.",
        )
        require(
            set(directory.iterdir()) == {source}
            and not source.is_symlink()
            and stat.S_ISREG(source.lstat().st_mode)
            and source.stat().st_uid == os.getuid(),
            "A remaining temporary directory contains unexpected files.",
        )
        require(
            source.read_bytes() in expected,
            "A remaining temporary file differs from the artificial test input.",
        )
        source.unlink()
        directory.rmdir()


def check_refusal(root, values):
    if not root.exists():
        return
    with importer.FirefoxSession(root) as session:
        require(
            not importer.completion_path(root, session.profile_key).exists(),
            "Malformed test input has an import completion record.",
        )
        clean = session.script(
            """
            const [entries, done] = arguments;
            (async () => {
            """
            + PLACES_TREE_SCRIPT
            + """
              const rows = await readTree();
              const titles = [...entries.map(entry => entry.title),
                'Artificial collection', 'Artificial nested', 'Artificial second'];
              done(rows.every(row => !titles.includes(row.title) &&
                !entries.some(entry => row.url === entry.url)));
            })().catch(() => done(false));
            """,
            [values["bookmarks"] + [values["existing"], values["extra"]]],
            asynchronous=True,
        )
        require(
            clean is True, "Malformed test input imported artificial bookmark rows."
        )


def cleanup():
    firefox.closed()
    if not STATE.exists():
        return
    values = private_values() if (STATE / "values.json").exists() else None
    record = STATE / "temporary-before.json"
    leftover_plaintext = record.exists() and temporary_directories() != json.loads(
        record.read_text()
    )
    failure = None
    try:
        if values is not None:
            remove_plaintext(values)
    except (firefox.ProofError, OSError) as error:
        failure = error
    with policy_state():
        firefox.restore()
    if failure is not None:
        raise failure
    try:
        if values is not None:
            check_refusal(target("refusal"), values)
            roots = [ROOT / ".local/logs"]
            if (STATE / "evidence-path").exists():
                roots.append(Path((STATE / "evidence-path").read_text()))
            if (STATE / "temporary-before.json").exists():
                previous = json.loads((STATE / "temporary-before.json").read_text())
                require(
                    temporary_directories() == previous,
                    "A decrypted bookmarks directory remains after cleanup.",
                )
            roots.extend(STATE.rglob("firefox.log"))
            scan_private(values, roots)
    finally:
        shutil.rmtree(STATE)
    require(
        not leftover_plaintext,
        "Import failure left a decrypted test file. Cleanup removed it.",
    )


def main():
    parser = JourneyArgumentParser(
        description="Prove isolated encrypted Firefox bookmark imports."
    )
    parser.add_argument("operation", choices=["setup", "before", "after", "cleanup"])
    parser.add_argument("--stage", choices=STAGES, default="preview")
    parser.add_argument("--inventory", type=Path)
    arguments = parser.parse_args()
    require(
        sys.platform == "darwin" and os.geteuid() != 0,
        "Run the journey as a standard Mac account.",
    )
    if arguments.operation == "setup":
        setup()
    elif arguments.operation == "cleanup":
        cleanup()
        print("bookmarks policies_restored=true private_state_removed=true")
    else:
        require(
            arguments.inventory is not None,
            "The journey needs its generated inventory folder.",
        )
        {"before": before, "after": after}[arguments.operation](
            arguments.stage, arguments.inventory
        )


def cli():
    try:
        main()
    except JourneyCheckError as error:
        print("Bookmarks journey check failed: " + str(error), file=sys.stderr)
        return 1
    except firefox.ProofError:
        print(
            "Bookmarks journey check failed: Firefox browser or policy validation.",
            file=sys.stderr,
        )
        return 1
    except importer.PermissionDenied:
        print(
            "Bookmarks journey check failed: " + importer.PERMISSION_MESSAGE,
            file=sys.stderr,
        )
        return 1
    except importer.StartupError as error:
        message = {
            "gui": importer.GUI_MESSAGE,
            "launch": importer.LAUNCH_MESSAGE,
            "system_access": importer.SYSTEM_ACCESS_MESSAGE,
            "isolation": importer.ISOLATION_MESSAGE,
            "state_directory": importer.STATE_DIRECTORY_MESSAGE,
        }.get(
            error.check,
            "Firefox startup through LaunchServices. "
            "Keep this account signed in at the Mac's screen. "
            "Open Firefox, select its default profile, and quit before retrying.",
        )
        print(
            "Bookmarks journey check failed: " + message,
            file=sys.stderr,
        )
        return 1
    except importer.BookmarkError:
        print(
            "Bookmarks journey check failed: Firefox import operation.", file=sys.stderr
        )
        return 1
    except OSError:
        print("Bookmarks journey check failed: private file access.", file=sys.stderr)
        return 1
    except ValueError:
        print(
            "Bookmarks journey check failed: private fixture format.", file=sys.stderr
        )
        return 1
    except subprocess.SubprocessError:
        print(
            "Bookmarks journey check failed: browser command execution.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
