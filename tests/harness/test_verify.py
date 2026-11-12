import getpass
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_verify():
    loader = importlib.machinery.SourceFileLoader(
        "verify_under_test", str(ROOT / "scripts" / "verify")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


FAKE = """#!{python} -I
import json, os, stat, sys
args = sys.argv[1:]
log = os.environ["FAKE_LOG"]
calls = open(log).read().splitlines() if os.path.exists(log) else []
plan = json.load(open(os.environ["FAKE_PLAN"]))[len(calls)]
record = {{"args": args, "stdin_is_null": os.fstat(0).st_rdev == os.stat("/dev/null").st_rdev}}
if "-e" in args:
    path = args[args.index("-e") + 1][1:]
    record["password_file_mode"] = stat.S_IMODE(os.stat(path).st_mode)
    passwords = json.load(open(path))["account_passwords"]
    record["password_names"] = sorted(passwords)
    record["password_lengths"] = [len(p) for p in passwords.values()]
    if plan.get("leak"):
        print("leaked", next(iter(passwords.values())))
    if plan.get("leak_log"):
        with open(plan["leak_log"], "a") as out:
            out.write(next(iter(passwords.values())) + "\\n")
inventory = args[args.index("-i") + 1]
record["profile"] = open(os.path.join(inventory, "host_vars", "verify-mac", "profile.yml")).read()
for domain, key, kind, value in plan.get("defaults", []):
    os.system(f"defaults write {{domain}} {{key}} -{{kind}} {{value}}")
for line in plan.get("print", []):
    print(line)
print("PLAY RECAP")
print(f"verify-mac                 : ok=1    changed={{plan.get('changed', 0)}}    unreachable=0    failed=0")
with open(log, "a") as out:
    out.write(json.dumps(record) + "\\n")
sys.exit(plan.get("exit", 0))
"""


FAKE_DEFAULTS = """#!{python} -I
import json, os, plistlib, struct, sys
args = sys.argv[1:]
host = "currentHost" if args[0] == "-currentHost" else ""
verb, domain, *rest = args[1:] if host else args
path = os.environ["FAKE_DEFAULTS_STORE"]
store = json.load(open(path))
values = store.setdefault(host + "|" + domain, {{}})
if verb == "export":
    sys.stdout.buffer.write(plistlib.dumps(values))
    sys.exit(0)
fail = os.environ["FAKE_DEFAULTS_FAIL"]
if verb in ("write", "delete") and os.path.exists(fail) and domain + "/" + rest[0] in open(fail).read().split():
    sys.exit("Could not write domain " + domain)
if verb == "import":
    store[host + "|" + domain] = plistlib.loads(sys.stdin.buffer.read())
elif verb == "delete":
    if values.pop(rest[0], None) is None:
        sys.exit("The domain/default pair does not exist")
elif verb == "write":
    kinds = {{"-bool": lambda t: t in ("true", "yes", "1"), "-int": int, "-float": lambda t: struct.unpack("f", struct.pack("f", float(t)))[0], "-string": str}}
    if rest[1].startswith("<real>"):
        values[rest[0]] = float(rest[1][len("<real>"):-len("</real>")])
    else:
        values[rest[0]] = kinds[rest[1]](rest[2])
json.dump(store, open(path, "w"))
"""
FAKE_KILLALL = """#!/bin/sh
echo "$@" >> "$FAKE_KILLALL_LOG"
"""


class Harness(unittest.TestCase):
    def setUp(self):
        self.verify = load_verify()
        self.tmp = Path(tempfile.mkdtemp(dir=ROOT / ".local"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "ansible-playbook"
        fake.write_text(FAKE.format(python=sys.executable))
        fake.chmod(0o755)
        self.verify.BIN = self.bin
        self.verify.LOGS = self.tmp / "ansible-logs"
        self.verify.LOGS.mkdir()
        self.evidence = self.tmp / "evidence"
        for sub in ("logs", "reads", "inventory"):
            (self.evidence / sub).mkdir(parents=True)
        self.calls = self.tmp / "calls.jsonl"
        self.plan = self.tmp / "plan.json"
        self.scratch = str(self.tmp / "scratch-domain")
        self.verify.SAVES = self.tmp
        self.saved = self.tmp / "test-journey" / "saved.plist"

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)])

    def journey(self, data, plan):
        self.plan.write_text(json.dumps(plan))
        self.verify.ENV = {
            **self.verify.ENV,
            "FAKE_PLAN": str(self.plan),
            "FAKE_LOG": str(self.calls),
        }
        journey = self.verify.parse_journey("test", data, "test journey")
        return self.verify.run_journey(journey, self.evidence, None)

    def recorded(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def evidence_bytes(self):
        return b"".join(p.read_bytes() for p in self.evidence.rglob("*") if p.is_file())

    def test_password_file_is_private_passed_to_every_run_and_deleted(self):
        result = self.journey(
            {
                "summary": "s",
                "passwords": ["setuptest1"],
                "runs": [{"name": "a", "exit": 0}, {"name": "b", "exit": 0}],
            },
            [{}, {}],
        )
        self.assertEqual(result["status"], "proven", result["reason"])
        calls = self.recorded()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call["password_file_mode"], 0o600)
            self.assertEqual(call["password_names"], ["setuptest1"])
            self.assertGreaterEqual(call["password_lengths"][0], 20)
        self.assertEqual(list(self.evidence.glob("*password*")), [])
        self.assertEqual(result["passwords"], [{"account": "setuptest1", "copies": 0}])

    def test_each_run_gets_a_new_password(self):
        passwords = []
        for i in range(2):
            self.calls.unlink(missing_ok=True)
            self.evidence = self.tmp / f"evidence-{i}"
            for sub in ("logs", "reads", "inventory"):
                (self.evidence / sub).mkdir(parents=True)
            self.journey(
                {
                    "summary": "s",
                    "passwords": ["setuptest1"],
                    "runs": [{"name": "a", "exit": 0}],
                },
                [{"leak": True}],
            )
            log = (self.evidence / "logs" / "test-a.txt").read_text()
            passwords.append(log.split("leaked ")[1].split()[0])
        self.assertNotEqual(passwords[0], passwords[1])

    def test_a_password_copy_in_the_run_output_fails_without_printing_it(self):
        result = self.journey(
            {
                "summary": "s",
                "passwords": ["setuptest1"],
                "runs": [{"name": "a", "exit": 0}],
            },
            [{"leak": True}],
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("setuptest1", result["reason"])
        log = (self.evidence / "logs" / "test-a.txt").read_text()
        password = log.split("leaked ")[1].split()[0]
        self.assertNotIn(password, json.dumps(result))
        self.assertEqual(result["passwords"], [{"account": "setuptest1", "copies": 1}])

    def test_a_password_copy_in_the_ansible_log_fails(self):
        result = self.journey(
            {
                "summary": "s",
                "passwords": ["setuptest1"],
                "runs": [{"name": "a", "exit": 0}],
            },
            [{"leak_log": str(self.verify.LOGS / "ansible.log")}],
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["passwords"], [{"account": "setuptest1", "copies": 1}])

    def test_no_passwords_means_no_extra_vars(self):
        self.journey({"summary": "s", "runs": [{"name": "a", "exit": 0}]}, [{}])
        self.assertNotIn("-e", self.recorded()[0]["args"])

    def test_playbook_stdin_is_null_so_a_prompt_cannot_wait(self):
        self.journey({"summary": "s", "runs": [{"name": "a", "exit": 0}]}, [{}])
        self.assertTrue(self.recorded()[0]["stdin_is_null"])

    def test_running_account_placeholder_is_filled(self):
        self.journey(
            {
                "summary": "s",
                "profile": {"accounts": [{"name": "<running account>", "admin": True}]},
                "runs": [{"name": "a", "exit": 0}],
            },
            [{}],
        )
        profile = self.recorded()[0]["profile"]
        self.assertIn(f"name: {getpass.getuser()}", profile)
        self.assertNotIn("<running account>", profile)

    def creates(self, name):
        return self.journey(
            {
                "summary": "s",
                "creates": name,
                "runs": [{"name": "adopt", "exit": 0, "recap": {"changed": 0}}],
                "create_runs": [
                    {"name": "create", "exit": 0, "recap": {"changed": 1}},
                    {"name": "create-again", "exit": 0, "recap": {"changed": 0}},
                ],
            },
            [{"changed": 0}, {"changed": 0}],
        )

    def test_an_existing_account_runs_only_the_adopt_expectations(self):
        result = self.creates(getpass.getuser())
        self.assertEqual(result["case"], "adopt")
        self.assertEqual([r["run"] for r in result["runs"]], ["adopt"])
        self.assertEqual(result["status"], "proven", result["reason"])

    def test_a_missing_account_runs_only_the_create_expectations(self):
        result = self.creates("setuptest-missing")
        self.assertEqual(result["case"], "create")
        self.assertEqual(result["runs"][0]["run"], "create")
        self.assertEqual(result["status"], "failed")
        self.assertIn("changed=0, expected 1", result["reason"])

    def test_a_missing_account_after_the_runs_fails(self):
        result = self.journey(
            {
                "summary": "s",
                "creates": "setuptest-missing",
                "runs": [{"name": "adopt", "exit": 0}],
                "create_runs": [{"name": "create", "exit": 0}],
            },
            [{}],
        )
        self.assertEqual(result["case"], "create")
        self.assertEqual(result["status"], "failed")
        self.assertIn("setuptest-missing", result["reason"])

    def test_creates_needs_create_runs(self):
        with self.assertRaises(self.verify.JourneyError):
            self.verify.parse_journey(
                "x",
                {"summary": "s", "creates": "a", "runs": [{"name": "a", "exit": 0}]},
                "x",
            )
        with self.assertRaises(self.verify.JourneyError):
            self.verify.parse_journey(
                "x",
                {
                    "summary": "s",
                    "create_runs": [{"name": "a", "exit": 0}],
                    "runs": [{"name": "a", "exit": 0}],
                },
                "x",
            )

    def test_a_run_profile_replaces_the_journey_profile_for_that_run_only(self):
        self.journey(
            {
                "summary": "s",
                "profile": {"default_settings": {"dock.show_recents": False}},
                "runs": [
                    {"name": "a", "exit": 0},
                    {
                        "name": "b",
                        "exit": 0,
                        "profile": {"default_settings": {"dock.show_recents": "unset"}},
                    },
                    {"name": "c", "exit": 0},
                ],
            },
            [{}, {}, {}],
        )
        profiles = [c["profile"] for c in self.recorded()]
        self.assertIn("dock.show_recents: false", profiles[0])
        self.assertIn("dock.show_recents: unset", profiles[1])
        self.assertIn("dock.show_recents: false", profiles[2])

    def test_run_reads_are_checked_after_that_run(self):
        result = self.journey(
            {
                "summary": "s",
                "runs": [
                    {
                        "name": "a",
                        "exit": 0,
                        "reads": [
                            {
                                "domain": self.scratch,
                                "key": "orientation",
                                "type": "string",
                                "value": "left",
                            },
                            {
                                "domain": self.scratch,
                                "key": "show-recents",
                                "absent": True,
                            },
                        ],
                    },
                    {
                        "name": "b",
                        "exit": 0,
                        "reads": [
                            {
                                "domain": self.scratch,
                                "key": "orientation",
                                "type": "string",
                                "value": "left",
                            },
                        ],
                    },
                ],
            },
            [
                {"defaults": [[self.scratch, "orientation", "string", "left"]]},
                {"defaults": [[self.scratch, "orientation", "string", "right"]]},
            ],
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["runs"][0]["problems"], [])
        self.assertIn("orientation", result["reason"])
        self.assertTrue(result["reason"].startswith("b run"))
        self.assertTrue((self.evidence / "reads" / "test-a.json").is_file())

    def test_output_patterns_are_counted(self):
        result = self.journey(
            {
                "summary": "s",
                "runs": [
                    {
                        "name": "a",
                        "exit": 2,
                        "output": [
                            {"pattern": "refused.*dock\\.tile_size", "count": 1},
                            {"pattern": "Not waiting for response", "count": 0},
                        ],
                    },
                    {
                        "name": "b",
                        "exit": 2,
                        "output": [
                            {"pattern": "refused.*dock\\.tile_size", "count": 1}
                        ],
                    },
                ],
            },
            [
                {"exit": 2, "print": ["  refused dock.tile_size: Type mismatch"]},
                {
                    "exit": 2,
                    "print": ["refused dock.tile_size", "refused dock.tile_size"],
                },
            ],
        )
        self.assertEqual(result["runs"][0]["problems"], [])
        self.assertEqual(result["status"], "failed")
        self.assertIn("found 2 times, expected 1", result["reason"])

    def test_a_template_error_in_the_output_fails_the_run(self):
        result = self.journey(
            {"summary": "s", "runs": [{"name": "a", "exit": 0}]},
            [
                {
                    "print": [
                        "TASK [macos_finder_sidebar : Set << error 1 - object of "
                        "type 'dict' has no attribute 'ids' >>]"
                    ]
                }
            ],
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["runs"][0]["problems"],
            ["the output holds a template error (<< error)"],
        )

    def test_file_checks_compare_owner_and_mode(self):
        good = self.tmp / "good.plist"
        good.write_text("x")
        good.chmod(0o600)
        bad = self.tmp / "bad.plist"
        bad.write_text("x")
        bad.chmod(0o644)
        me = getpass.getuser()
        result = self.journey(
            {
                "summary": "s",
                "runs": [{"name": "a", "exit": 0}],
                "files": [
                    {"path": str(good), "owner": me, "mode": "600"},
                    {"path": str(bad), "owner": me, "mode": "600"},
                ],
            },
            [{}],
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("bad.plist", result["reason"])
        self.assertNotIn("good.plist", result["reason"])

    def fake_defaults(self, store):
        for name, text in (("defaults", FAKE_DEFAULTS), ("killall", FAKE_KILLALL)):
            tool = self.bin / name
            tool.write_text(text.format(python=sys.executable))
            tool.chmod(0o755)
        self.store = self.tmp / "store.json"
        self.store.write_text(json.dumps(store))
        self.killed = self.tmp / "killall.txt"
        self.fail_file = self.tmp / "fail.txt"
        self.verify.DEFAULTS = str(self.bin / "defaults")
        self.verify.KILLALL = str(self.bin / "killall")
        self.verify.CONSOLE = self.store
        self.verify.ENV = {
            **self.verify.ENV,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_DEFAULTS_STORE": str(self.store),
            "FAKE_DEFAULTS_FAIL": str(self.fail_file),
            "FAKE_KILLALL_LOG": str(self.killed),
        }

    def stored(self):
        return json.loads(self.store.read_text())

    def restarts(self):
        return self.killed.read_text().split() if self.killed.exists() else []

    SCALARS = {
        "flag": True,
        "count": 3,
        "size": 32.0,
        "name": "left",
    }

    def scalar_journey(self, **extra):
        return {
            "summary": "s",
            "saves": [{"domain": "com.apple.dock", "key": k} for k in self.SCALARS]
            + [{"domain": "com.apple.dock", "key": "gone"}],
            "setup": [
                "defaults write com.apple.dock flag -bool false",
                "defaults write com.apple.dock count -float 9.5",
                "defaults delete com.apple.dock size",
                "defaults write com.apple.dock name -int 7",
            ],
            "runs": [{"name": "a", "exit": 0}],
            **extra,
        }

    def test_each_saved_type_is_restored_with_its_type_and_absent_keys_deleted(self):
        self.fake_defaults({"|com.apple.dock": dict(self.SCALARS, other="kept")})
        result = self.journey(
            self.scalar_journey(),
            [{"defaults": [["com.apple.dock", "gone", "string", "new"]]}],
        )
        self.assertEqual(result["status"], "proven", result["reason"])
        dock = self.stored()["|com.apple.dock"]
        self.assertEqual(dock, dict(self.SCALARS, other="kept"))
        self.assertEqual(
            {k: type(v) for k, v in dock.items()},
            {"flag": bool, "count": int, "size": float, "name": str, "other": str},
        )
        self.assertFalse(self.saved.exists())

    def test_a_float_is_restored_exactly(self):
        self.fake_defaults({"|com.apple.dock": {"size": 0.1}})
        result = self.journey(
            {
                "summary": "s",
                "saves": [{"domain": "com.apple.dock", "key": "size"}],
                "setup": ["defaults delete com.apple.dock size"],
                "runs": [{"name": "a", "exit": 0}],
            },
            [{"defaults": []}],
        )
        self.assertEqual(result["status"], "proven", result["reason"])
        self.assertEqual(self.stored()["|com.apple.dock"]["size"], 0.1)
        self.assertFalse(self.saved.exists())

    def test_reads_see_the_run_and_cleanup_runs_before_the_restore(self):
        self.fake_defaults({"|com.apple.dock": {"name": "left"}})
        result = self.journey(
            {
                "summary": "s",
                "saves": [{"domain": "com.apple.dock", "key": "name"}],
                "runs": [{"name": "a", "exit": 0}],
                "reads": [
                    {
                        "domain": "com.apple.dock",
                        "key": "name",
                        "type": "string",
                        "value": "right",
                    }
                ],
                "cleanup": ["defaults export com.apple.dock - | grep -q right"],
            },
            [{"defaults": [["com.apple.dock", "name", "string", "right"]]}],
        )
        self.assertEqual(result["status"], "proven", result["reason"])
        self.assertEqual(self.stored()["|com.apple.dock"], {"name": "left"})

    def test_each_saved_domain_is_read_once(self):
        calls = []
        real = self.verify.export
        self.addCleanup(setattr, self.verify, "export", real)
        self.verify.export = lambda domain, host="": calls.append((host, domain)) or {}
        saves = self.verify.parse_saves(
            [
                {"domain": "com.apple.dock", "key": "a"},
                {"domain": "com.apple.dock", "key": "b"},
                {"domain": "com.apple.finder", "key": "c"},
            ],
            "test",
        )
        self.assertEqual(
            self.verify.save(saves, self.tmp / "s-journey" / "saved.plist"), ""
        )
        self.assertEqual(
            sorted(calls), [("", "com.apple.dock"), ("", "com.apple.finder")]
        )

    def test_a_journey_with_no_saves_writes_no_save(self):
        self.fake_defaults({})
        result = self.journey(
            {"summary": "s", "runs": [{"name": "a", "exit": 0}]},
            [{"defaults": []}],
        )
        self.assertEqual(result["status"], "proven", result["reason"])
        self.assertEqual(sorted(self.tmp.glob("*-journey")), [])

    def test_current_host_keys_are_saved_and_restored_on_their_own_host(self):
        self.fake_defaults(
            {
                "|com.apple.controlcenter": {"Sound": 8},
                "currentHost|com.apple.controlcenter": {"Sound": 18},
            }
        )
        result = self.journey(
            {
                "summary": "s",
                "saves": [
                    {
                        "domain": "com.apple.controlcenter",
                        "key": "Sound",
                        "host": "currentHost",
                    }
                ],
                "setup": [
                    "defaults -currentHost write com.apple.controlcenter Sound -int 2"
                ],
                "runs": [{"name": "a", "exit": 0}],
            },
            [{}],
        )
        self.assertEqual(result["status"], "proven", result["reason"])
        self.assertEqual(
            self.stored(),
            {
                "|com.apple.controlcenter": {"Sound": 8},
                "currentHost|com.apple.controlcenter": {"Sound": 18},
            },
        )

    def test_a_whole_domain_save_restores_every_key_of_any_type(self):
        before = {
            "persistent-apps": [{"tile-data": {"bundle-identifier": "a"}}],
            "x": 1,
        }
        self.fake_defaults({"|com.apple.dock": before})
        result = self.journey(
            {
                "summary": "s",
                "saves": [{"domain": "com.apple.dock"}],
                "setup": ["defaults write com.apple.dock added -bool true"],
                "runs": [{"name": "a", "exit": 0}],
            },
            [{}],
        )
        self.assertEqual(result["status"], "proven", result["reason"])
        self.assertEqual(self.stored()["|com.apple.dock"], before)
        self.assertEqual(self.restarts(), ["Dock"])

    def test_a_leftover_save_stops_the_setup_and_is_restored(self):
        self.fake_defaults({"|com.apple.dock": {"name": "now"}})
        marker = self.tmp / "setup-ran"
        self.saved.parent.mkdir()
        self.saved.write_bytes(
            self.verify.plistlib.dumps(
                [
                    {
                        "domain": "com.apple.dock",
                        "host": "",
                        "key": "name",
                        "type": "string",
                        "value": "old",
                    }
                ]
            )
        )
        result = self.journey(
            {
                "summary": "s",
                "saves": [{"domain": "com.apple.dock", "key": "name"}],
                "setup": [f"touch {marker}"],
                "runs": [{"name": "a", "exit": 0}],
            },
            [{}],
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("earlier restore did not finish", result["reason"])
        self.assertFalse(marker.exists())
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.stored()["|com.apple.dock"], {"name": "old"})
        self.assertFalse(self.saved.exists())

    def test_a_failed_restore_keeps_the_save_and_restores_the_other_keys(self):
        self.fake_defaults({"|com.apple.dock": dict(self.SCALARS)})
        result = self.journey(
            self.scalar_journey(
                cleanup=[f"echo com.apple.dock/count > {self.fail_file}"]
            ),
            [{}],
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("com.apple.dock count reads", result["reason"])
        self.assertIn("the save stays in", result["reason"])
        self.assertTrue(self.saved.exists())
        dock = self.stored()["|com.apple.dock"]
        self.assertEqual(dock["count"], 9.5)
        self.assertEqual(
            {k: v for k, v in dock.items() if k != "count"},
            {k: v for k, v in self.SCALARS.items() if k != "count"},
        )

    def test_a_failed_delete_of_an_absent_key_keeps_the_save(self):
        self.fake_defaults({"|com.apple.dock": {}})
        result = self.journey(
            {
                "summary": "s",
                "saves": [{"domain": "com.apple.dock", "key": "gone"}],
                "runs": [{"name": "a", "exit": 0}],
                "cleanup": [f"echo com.apple.dock/gone > {self.fail_file}"],
            },
            [{"defaults": [["com.apple.dock", "gone", "string", "new"]]}],
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("com.apple.dock gone reads", result["reason"])
        self.assertTrue(self.saved.exists())

    def test_a_key_type_the_harness_cannot_save_stops_before_setup(self):
        before = {"|com.apple.dock": {"name": "left", "tiles": [1, 2]}}
        self.fake_defaults(before)
        marker = self.tmp / "setup-ran"
        result = self.journey(
            {
                "summary": "s",
                "saves": [
                    {"domain": "com.apple.dock", "key": "name"},
                    {"domain": "com.apple.dock", "key": "tiles"},
                ],
                "setup": [
                    f"touch {marker}",
                    "defaults write com.apple.dock name -string x",
                ],
                "runs": [{"name": "a", "exit": 0}],
            },
            [{}],
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "cannot save com.apple.dock tiles of type array", result["reason"]
        )
        self.assertFalse(marker.exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.saved.exists())
        self.assertEqual(self.stored(), before)

    def restarted(self, console):
        self.fake_defaults({})
        self.verify.CONSOLE = console or self.store
        result = self.journey(
            {
                "summary": "s",
                "saves": [
                    {"domain": "com.apple.dock", "key": "a"},
                    {"domain": "com.apple.finder", "key": "b"},
                    {"domain": "NSGlobalDomain", "key": "c"},
                ],
                "runs": [{"name": "a", "exit": 0}],
            },
            [{}],
        )
        self.assertEqual(result["status"], "proven", result["reason"])
        return self.restarts()

    def test_restore_restarts_dock_and_finder_when_the_running_account_owns_the_screen(
        self,
    ):
        self.assertEqual(self.restarted(None), ["Dock", "Finder"])

    def test_restore_restarts_nothing_when_another_account_owns_the_screen(self):
        self.assertEqual(self.restarted(Path("/")), [])
        log = (self.evidence / "logs" / "test-restore.txt").read_text()
        self.assertIn("no restart of Dock, Finder", log)

    def test_saves_reject_a_bad_host_a_repeated_key_and_a_key_inside_a_saved_domain(
        self,
    ):
        for saves in (
            [{"domain": "d", "key": "k", "host": "localhost"}],
            [{"domain": "d", "key": "k"}, {"domain": "d", "key": "k"}],
            [{"domain": "d"}, {"domain": "d", "key": "k"}],
            [{"key": "k"}],
        ):
            with self.assertRaises(self.verify.JourneyError, msg=saves):
                self.verify.parse_journey(
                    "x",
                    {
                        "summary": "s",
                        "saves": saves,
                        "runs": [{"name": "a", "exit": 0}],
                    },
                    "x",
                )

    def test_the_tracked_journeys_load(self):
        paths = sorted(self.verify.JOURNEYS.glob("*.yml"))
        self.assertLessEqual(
            {
                "accounts",
                "by-hand",
                "dock-apps",
                "dock-basics",
                "example-check",
                "per-account",
                "refusals",
                "wallpaper",
            },
            {path.stem for path in paths},
        )
        for path in paths:
            journey = self.verify.load_journey(path)
            self.assertEqual(journey.name, path.stem)

    def test_example_check_uses_the_example_profile(self):
        journey = self.verify.load_journey(self.verify.JOURNEYS / "example-check.yml")
        example = {}
        for path in sorted(
            (ROOT / "inventory/example/host_vars/example-mac").glob("*.yml")
        ):
            example.update(self.verify.yaml.safe_load(path.read_text()))
        self.assertEqual(journey.profile, example)

    def test_journeys_use_no_real_account_name(self):
        me = getpass.getuser()
        for path in self.verify.JOURNEYS.glob("*.yml"):
            self.assertNotIn(me, path.read_text(), path.name)

    def test_help_lists_the_new_features(self):
        out = subprocess.run(
            [str(ROOT / "scripts" / "verify"), "--help"], capture_output=True, text=True
        )
        self.assertEqual(out.returncode, 0)
        for name in ("accounts", "per-account", "refusals"):
            self.assertIn(name, out.stdout)


if __name__ == "__main__":
    unittest.main()
