import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_accounts/library"))

from macos_account import AccountError, answer_prompt, ensure_account

PASSWORD = "Test-only-7f3a9c2e"


class FakeMac:
    def __init__(self, accounts=None, tokens=(), create_works=True):
        self.accounts = dict(accounts or {})
        self.tokens = set(tokens)
        self.create_works = create_works
        self.argvs = []
        self.secrets = []

    def run(self, argv):
        self.argvs.append(list(argv))
        if argv[0] == "/usr/bin/dscl":
            name = argv[3].removeprefix("/Users/")
            if name in self.accounts:
                return 0, f"RecordName: {name}\n", ""
            return 56, "<dscl_cmd> DS Error: -14136 (eDSRecordNotFound)\n", ""
        if argv[0] == "/usr/bin/dsmemberutil":
            name = argv[argv.index("-U") + 1]
            if name not in self.accounts:
                return 7, "The user cannot be found\n", ""
            if self.accounts[name]:
                return 0, "user is a member of the group\n", ""
            return 0, "user is not a member of the group\n", ""
        if argv[:2] == ["/usr/sbin/sysadminctl", "-secureTokenStatus"]:
            name = argv[2]
            state = "ENABLED" if name in self.tokens else "DISABLED"
            return (
                0,
                "",
                f"2026-11-12 sysadminctl[1:2] Secure token is {state} for user {name}\n",
            )
        raise AssertionError(f"unexpected command {argv}")

    def run_with_secret(self, argv, secret):
        self.argvs.append(list(argv))
        self.secrets.append(secret)
        assert argv[:2] == ["/usr/sbin/sysadminctl", "-addUser"], argv
        if self.create_works:
            self.accounts[argv[2]] = "-admin" in argv
        return (
            0,
            "### Error:-14120 File:DSRecord.m Line:418\nCreating home directory for x\n",
        )

    def wrote(self):
        return bool(self.secrets) or any(
            a[1] in ("-addUser", "-deleteUser") for a in self.argvs if len(a) > 1
        )


def run(mac, name="setuptest1", admin=False, password=PASSWORD, check_mode=False):
    return ensure_account(mac, name, "Setup Test", admin, password, check_mode)


class MissingAccount(unittest.TestCase):
    def test_created_as_standard(self):
        mac = FakeMac()
        result = run(mac)
        self.assertTrue(result["changed"])
        self.assertEqual(
            result["account"],
            {
                "name": "setuptest1",
                "state": "created",
                "refused": "",
                "secure_token": False,
            },
        )
        self.assertEqual(mac.accounts, {"setuptest1": False})
        self.assertEqual(mac.secrets, [PASSWORD])

    def test_created_as_admin(self):
        mac = FakeMac()
        run(mac, admin=True)
        self.assertEqual(mac.accounts, {"setuptest1": True})

    def test_full_name_reaches_sysadminctl(self):
        mac = FakeMac()
        run(mac)
        create = next(a for a in mac.argvs if a[1] == "-addUser")
        self.assertEqual(create[create.index("-fullName") + 1], "Setup Test")

    def test_empty_password_refused(self):
        mac = FakeMac()
        with self.assertRaisesRegex(AccountError, "setuptest1"):
            run(mac, password="")
        self.assertEqual(mac.accounts, {})
        self.assertFalse(mac.wrote())

    def test_check_mode_creates_nothing(self):
        mac = FakeMac()
        result = run(mac, password="", check_mode=True)
        self.assertTrue(result["changed"])
        self.assertEqual(result["account"]["state"], "would_create")
        self.assertEqual(result["account"]["refused"], "")
        self.assertEqual(mac.accounts, {})
        self.assertFalse(mac.wrote())

    def test_exit_zero_without_a_record_fails(self):
        mac = FakeMac(create_works=False)
        with self.assertRaisesRegex(AccountError, "setuptest1"):
            run(mac)


class ExistingAccount(unittest.TestCase):
    def test_adopted_with_no_change(self):
        mac = FakeMac({"setuptest1": False})
        result = run(mac, password="")
        self.assertFalse(result["changed"])
        self.assertEqual(
            result["account"],
            {
                "name": "setuptest1",
                "state": "adopted",
                "refused": "",
                "secure_token": False,
            },
        )
        self.assertFalse(mac.wrote())

    def test_adopted_twice_gives_the_same_answer(self):
        mac = FakeMac()
        run(mac)
        again = run(mac, password="")
        self.assertFalse(again["changed"])
        self.assertEqual(again["account"]["state"], "adopted")
        self.assertEqual(len(mac.secrets), 1)

    def test_admin_mismatch_refused_and_unchanged(self):
        for has_admin, wants_admin in ((False, True), (True, False)):
            with self.subTest(has_admin=has_admin, wants_admin=wants_admin):
                mac = FakeMac({"setuptest1": has_admin})
                result = run(mac, admin=wants_admin, password="")
                self.assertFalse(result["changed"])
                self.assertEqual(result["account"]["state"], "adopted")
                self.assertIn("setuptest1", result["account"]["refused"])
                self.assertEqual(mac.accounts, {"setuptest1": has_admin})
                self.assertFalse(mac.wrote())

    def test_secure_token_reported(self):
        mac = FakeMac({"setuptest1": True}, tokens={"setuptest1"})
        self.assertTrue(run(mac, admin=True)["account"]["secure_token"])


class PasswordHandling(unittest.TestCase):
    def test_password_never_in_an_argv(self):
        for check_mode in (False, True):
            for accounts in ({}, {"setuptest1": False}, {"setuptest1": True}):
                mac = FakeMac(accounts)
                run(mac, check_mode=check_mode)
                with self.subTest(check_mode=check_mode, accounts=accounts):
                    self.assertTrue(mac.argvs)
                    for argv in mac.argvs:
                        self.assertFalse(any(PASSWORD in part for part in argv), argv)


CHILD = r"""
import getpass, hashlib, sys, time
print("No clear text password or interactive option was specified (adduser, change/reset password will not allow user to use FDE) !", flush=True)
time.sleep(0.5)
typed = getpass.getpass("User password:")
print("match" if hashlib.sha256(typed.encode()).hexdigest() == sys.argv[1] else "mismatch", flush=True)
sys.exit(0 if typed else 3)
"""


class PromptAnswer(unittest.TestCase):
    def test_answers_the_prompt_through_a_terminal(self):
        digest = hashlib.sha256(PASSWORD.encode()).hexdigest()
        argv = [sys.executable, "-I", "-c", CHILD, digest]
        rc, output = answer_prompt(argv, PASSWORD)
        self.assertEqual(rc, 0, output)
        self.assertIn("match", output.split())
        self.assertNotIn(PASSWORD, output)

    def test_secret_hidden_in_the_output(self):
        argv = [
            sys.executable,
            "-I",
            "-c",
            "import getpass; print(getpass.getpass('User password:'))",
        ]
        rc, output = answer_prompt(argv, PASSWORD)
        self.assertEqual(rc, 0, output)
        self.assertNotIn(PASSWORD, output)
        self.assertIn("<hidden>", output)

    def test_no_prompt_is_an_error(self):
        with self.assertRaisesRegex(AccountError, "prompt"):
            answer_prompt([sys.executable, "-I", "-c", "print('done')"], PASSWORD)

    def test_a_hung_command_is_stopped(self):
        argv = [sys.executable, "-I", "-c", "import time; time.sleep(30)"]
        with self.assertRaisesRegex(AccountError, "seconds"):
            answer_prompt(argv, PASSWORD, seconds=1)


if __name__ == "__main__":
    unittest.main()
