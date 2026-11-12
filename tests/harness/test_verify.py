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

    def test_the_tracked_journeys_load(self):
        for name in ("accounts", "per-account", "refusals", "dock-basics"):
            journey = self.verify.load_journey(self.verify.JOURNEYS / f"{name}.yml")
            self.assertEqual(journey.name, name)

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
