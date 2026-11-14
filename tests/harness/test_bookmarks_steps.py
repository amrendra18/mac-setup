import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_verify():
    loader = importlib.machinery.SourceFileLoader(
        "bookmarks_step_verify", str(ROOT / "scripts/verify")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


FIREFOX = """from pathlib import Path
import builtins
import errno
import io
import json
import os
import socket
protected = os.environ.get("SYNTHETIC_PROTECTED_FIREFOX")
if protected:
    def guard(function):
        def checked(path, *args, **kwargs):
            for value in (path, *args):
                if isinstance(value, (str, bytes, os.PathLike)):
                    name = os.path.abspath(os.fsdecode(value))
                    if name == protected or name.startswith(protected + os.sep):
                        with original_open(os.environ["SYNTHETIC_PROTECTED_LOG"], "a") as output:
                            output.write(function.__name__ + "\\n")
                        raise PermissionError(errno.EPERM, "Synthetic protected Firefox directory")
            return function(path, *args, **kwargs)
        return checked
    original_open = builtins.open
    for name in ("open", "stat", "lstat", "scandir", "listdir", "mkdir", "rmdir", "unlink", "remove", "readlink", "chmod", "utime", "rename", "replace", "link", "symlink", "access"):
        setattr(os, name, guard(getattr(os, name)))
    builtins.open = guard(original_open)
    io.open = guard(io.open)
class SyntheticSocket:
    def __init__(self, *args): pass
    def bind(self, address): pass
    def listen(self, count): pass
    def close(self): pass
socket.socket = SyntheticSocket
STATE = Path("unused")
OWNED = Path("unused-ownership")
DOMAIN = "synthetic"
class ProofError(Exception): pass
def require(condition, message):
    if not condition: raise ProofError(message)
def closed():
    expected = Path(__file__).resolve().parents[2] / ".local/bookmarks-journey/password"
    require(Path(os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE", "")).absolute() == expected,
            "Synthetic browser step lacks the journey environment.")
def setup():
    raise AssertionError("Bookmarks must not reuse legacy browser setup")
def browser(*args, **kwargs):
    raise AssertionError("Bookmarks must not launch the legacy browser")
def fingerprints(): return {}
def stores(): return {}
def save_json(path, value): path.write_text(json.dumps(value))
def seed_policy(): pass
def restore():
    if (STATE / "original.json").exists():
        (STATE / "original.json").unlink()
    return True
def bookmark_feasibility():
    raise AssertionError("Bookmarks must not run the legacy profile probe")
def command(argv): pass
"""
IMPORTER = """from pathlib import Path
import json
import os
class BookmarkError(Exception): pass
class PermissionDenied(BookmarkError): pass
class StartupError(BookmarkError):
    def __init__(self, message, check="automation"):
        super().__init__(message)
        self.check = check
GUI_MESSAGE = "Synthetic graphical login check."
LAUNCH_MESSAGE = "Synthetic LaunchServices startup check."
SYSTEM_ACCESS_MESSAGE = "Synthetic Firefox system access check."
ISOLATION_MESSAGE = "Synthetic Firefox profile isolation check."
STATE_DIRECTORY_MESSAGE = "Synthetic Firefox state directory check."
PERMISSION_MESSAGE = "Synthetic permission fallback."
def state_root(root=None):
    if root is None: raise AssertionError("Synthetic sessions need an isolated root")
    return root / "mac-setup-state"
def completion_path(root, profile_key):
    return state_root(root) / (profile_key + ".json")
def parse_html(source):
    return {"menu": [], "toolbar": [{"type": 2, "guid": "folder", "children": [
        {"type": 2, "guid": "nested", "children": [{"type": 1, "guid": str(i)} for i in range(3)]}]}], "unfiled": []}
class FirefoxSession:
    def __init__(self, root):
        self.root, self.port, self.profile_key = root, 40123, "a" * 64
        self.version = "160.0"
    def __enter__(self):
        with open(os.environ["SYNTHETIC_SESSION_LOG"], "a") as output:
            output.write(json.dumps({"root": str(self.root)}) + "\\n")
        for folder in ("app-data", "local-data"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        return self
    def script(self, script, args=None, asynchronous=False):
        if "titles.includes" in script: return True
        if len(args) == 1:
            entry = args[0]
            return {"seeded": True, "tree": [{"guid": "synthetic-old", "parentGuid": "synthetic-folder",
                "index": 1, "type": 1, "title": entry["title"], "url": entry["url"]}]}
        return {"blocked": True, "counts": [1] * len(args[0]), "titles": True,
                "existing": int(os.environ["SYNTHETIC_STAGE"].startswith("existing")), "extra": 0,
                "structure": True, "preserved": True}
    def send(self, *args): return None
    def import_tree(self, tree):
        def count(nodes):
            return sum(1 if node["type"] == 1 else count(node.get("children", [])) for node in nodes)
        return count(tree["toolbar"])
    def __exit__(self, *args): return False
"""
ANSIBLE = """#!PYTHON -I
import importlib.util, json, os, runpy, subprocess, sys
from pathlib import Path
from jinja2 import Environment
import yaml
args = sys.argv[1:]
inventory = Path(args[args.index("-i") + 1])
stage = next(value.split("=", 1)[1] for value in args if value.startswith("bookmarks_journey_stage="))
os.environ["SYNTHETIC_STAGE"] = stage
root = Path.cwd()
runpy.run_path(str(root / "verify/helpers/firefox.py"))
spec = importlib.util.spec_from_file_location("synthetic_importer", root / "module_utils/firefox_bookmarks.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)
expected = root / ".local/bookmarks-journey/password"
if Path(os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE", "")).absolute() != expected:
    sys.exit("Synthetic Ansible did not receive the test vault environment.")
wrapper = yaml.safe_load((root / "verify/helpers/bookmarks.yml").read_text())
values = {"ansible_playbook_python": sys.executable, "playbook_dir": str(root / "verify/helpers"),
          "bookmarks_journey_stage": stage, "inventory_dir": str(inventory)}
def command(task):
    argv = [Environment().from_string(arg).render(**values) for arg in task["ansible.builtin.command"]["argv"]]
    env = dict(os.environ, **task.get("environment", {}))
    done = subprocess.run(argv, env=env, capture_output=True, text=True)
    sys.stdout.write(done.stdout + done.stderr)
    if done.returncode: sys.exit(done.returncode)
command(wrapper[0]["tasks"][0])
expression = wrapper[0]["tasks"][1]["ansible.builtin.set_fact"]["macos_firefox_bookmarks_root"]
environment = Environment()
environment.filters["realpath"] = os.path.realpath
target = Path(environment.from_string(expression).render(**values))
if stage != "preview":
    with importer.FirefoxSession(target) as session:
        if stage != "refusal":
            record = importer.completion_path(target, session.profile_key)
            record.parent.mkdir(exist_ok=True)
            if not record.exists(): record.write_text('{"complete": true}')
        (target / "app-data/housekeeping").write_text(stage)
if stage != "refusal": command(wrapper[2]["tasks"][0])
else: print("    msg: 'refused bookmarks.import_file for synthetic: Firefox bookmarks import refused.'")
with open(os.environ["SYNTHETIC_STEP_LOG"], "a") as log:
    log.write(json.dumps({"stage":stage,"password":os.environ["ANSIBLE_VAULT_PASSWORD_FILE"]}) + "\\n")
print("PLAY RECAP")
print("verify-mac : ok=1 changed=0 unreachable=0 failed=" + ("1" if stage == "refusal" else "0"))
sys.exit(2 if stage == "refusal" else 0)
"""


class BookmarksDeclaredSteps(unittest.TestCase):
    def test_environment_schema_rejects_invalid_values_without_echoing_them(self):
        verify = load_verify()
        for value in (
            [],
            {"BAD=NAME": "SYNTHETIC-PRIVATE"},
            {"VALID": 3},
            {"VALID": "SYNTHETIC-PRIVATE\x00"},
        ):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(verify.JourneyError) as raised:
                    verify.parse_journey(
                        "test",
                        {"summary": "test", "runs": [], "environment": value},
                        "test",
                    )
                self.assertNotIn("SYNTHETIC-PRIVATE", str(raised.exception))

    @unittest.skipUnless(
        sys.platform == "darwin" and os.geteuid() != 0,
        "The actual helper CLI requires a standard Mac account",
    )
    def test_real_helper_cli_names_the_vault_check_without_echoing_its_value(self):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(ROOT / "verify/helpers/bookmarks.py"),
                "setup",
            ],
            cwd=ROOT,
            env=dict(os.environ, ANSIBLE_VAULT_PASSWORD_FILE="SYNTHETIC-PRIVATE-VALUE"),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "Test vault environment (ANSIBLE_VAULT_PASSWORD_FILE)", result.stderr
        )
        self.assertNotIn("SYNTHETIC-PRIVATE-VALUE", result.stdout + result.stderr)

    @unittest.skipUnless(
        sys.platform == "darwin" and os.geteuid() != 0,
        "The actual helper CLI requires a standard Mac account",
    )
    def test_declared_setup_all_runs_and_cleanup_inherit_the_test_environment(self):
        verify = load_verify()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            helpers = root / "verify/helpers"
            helpers.mkdir(parents=True)
            (root / "verify/journeys").mkdir()
            (root / "module_utils").mkdir()
            (root / ".local").mkdir()
            (root / ".local/venv").symlink_to(
                ROOT / ".local/venv", target_is_directory=True
            )
            for name in ("bookmarks.py", "bookmarks.yml"):
                shutil.copyfile(ROOT / "verify/helpers" / name, helpers / name)
            shutil.copyfile(
                ROOT / "verify/journeys/bookmarks.yml",
                root / "verify/journeys/bookmarks.yml",
            )
            (helpers / "firefox.py").write_text(FIREFOX)
            (root / "module_utils/firefox_bookmarks.py").write_text(IMPORTER)
            binary = root / "bin"
            binary.mkdir()
            fake = binary / "ansible-playbook"
            fake.write_text(ANSIBLE.replace("PYTHON", sys.executable, 1))
            fake.chmod(0o700)
            evidence = root / "evidence"
            for folder in ("logs", "reads", "inventory"):
                (evidence / folder).mkdir(parents=True, exist_ok=True)
            log = root / "steps.jsonl"
            sessions = root / "sessions.jsonl"
            protected = root / "home/Library/Application Support/Firefox"
            protected.mkdir(parents=True)
            sentinel = protected / "profiles.ini"
            sentinel.write_text("synthetic protected profile")
            protected_log = root / "protected-attempts.txt"
            environment = dict(
                os.environ,
                ANSIBLE_VAULT_PASSWORD_FILE="SYNTHETIC-WRONG-VAULT",
                SYNTHETIC_STEP_LOG=str(log),
                SYNTHETIC_SESSION_LOG=str(sessions),
                SYNTHETIC_PROTECTED_FIREFOX=str(protected),
                SYNTHETIC_PROTECTED_LOG=str(protected_log),
                HOME=str(root / "home"),
                ANSIBLE_LOCAL_TEMP=str(root / ".local/ansible/tmp"),
                ANSIBLE_HOME=str(root / ".local/ansible"),
            )
            data = yaml.safe_load((root / "verify/journeys/bookmarks.yml").read_text())
            journey = verify.parse_journey("bookmarks", data, "bookmarks.yml")
            with patch.object(verify, "ROOT", root), patch.object(
                verify, "BIN", binary
            ), patch.object(verify, "ENV", environment), patch.object(
                verify, "SAVES", root / ".local"
            ), patch.object(
                verify, "LOGS", root / ".local/logs"
            ):
                result = verify.run_journey(journey, evidence, None)
                self.assertEqual(result["reason"], "", result)
                self.assertEqual(len(result["runs"]), len(journey.runs))
                self.assertEqual(
                    verify.ENV["ANSIBLE_VAULT_PASSWORD_FILE"], "SYNTHETIC-WRONG-VAULT"
                )
            self.assertEqual(
                json.loads(
                    (evidence / "reads/bookmarks-startup-control.json").read_text()
                ),
                {
                    "default_port_occupied": True,
                    "selected_default_port": False,
                    "firefox_version": "160.0",
                },
            )
            self.assertFalse((root / ".local/bookmarks-journey").exists())
            self.assertEqual(len(journey.runs), 10)
            session_roots = [
                Path(json.loads(line)["root"])
                for line in sessions.read_text().splitlines()
            ]
            self.assertEqual(
                session_roots[0], root / ".local/bookmarks-journey/startup-control"
            )
            self.assertTrue(
                all(
                    target.parent == root / ".local/bookmarks-journey"
                    for target in session_roots
                )
            )
            self.assertFalse(protected_log.exists())
            self.assertEqual(sentinel.read_text(), "synthetic protected profile")
            control = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    (
                        "import os, runpy; from pathlib import Path; "
                        "runpy.run_path(os.environ['SYNTHETIC_GUARD_MODULE']); "
                        "Path(os.environ['SYNTHETIC_PROTECTED_FIREFOX']).iterdir().__next__()"
                    ),
                ],
                env=dict(
                    environment, SYNTHETIC_GUARD_MODULE=str(helpers / "firefox.py")
                ),
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertNotEqual(control.returncode, 0)
            self.assertIn("PermissionError", control.stderr)
            self.assertEqual(protected_log.read_text().splitlines(), ["scandir"])
            records = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(
                [item["stage"] for item in records], [run.name for run in journey.runs]
            )
            self.assertEqual(
                {item["password"] for item in records},
                {".local/bookmarks-journey/password"},
            )


if __name__ == "__main__":
    unittest.main()
