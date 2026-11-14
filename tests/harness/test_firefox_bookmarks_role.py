import os
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = "SYNTHETIC-PRIVATE-BOOKMARK"
STARTUP_MESSAGES = {
    "state_directory": (
        "Firefox bookmark state directory check failed. "
        "State and verification directories must belong to this account, "
        "have mode 0700, and contain no symbolic links."
    ),
    "isolation": (
        "Firefox profile isolation check failed. "
        "Firefox did not confirm the requested verification target."
    ),
    "system_access": (
        "Firefox chrome context and system access check failed. "
        "Firefox refused privileged bookmark automation. "
        "Check the installed Firefox version and Firefox developer tools policies."
    ),
    "gui": (
        "Firefox bookmark import needs this account's graphical login session. "
        "Sign in to this account at the Mac's screen and retry."
    ),
    "launch": (
        "LaunchServices could not start Firefox for bookmark automation. "
        "Keep this account signed in at the Mac's screen, quit Firefox, and retry."
    ),
    "automation": (
        "Firefox startup through LaunchServices failed. "
        "Keep this account signed in at the Mac's screen. "
        "Open Firefox, choose its default profile, and quit before retrying."
    ),
}
MODULE = """from pathlib import Path
from ansible.module_utils.basic import AnsibleModule

module = AnsibleModule(argument_spec={
    "operation": {"choices": ["inspect", "import"], "default": "import"},
    "source": {"type": "path"},
    "firefox_root": {"type": "path"},
    "expected_profile_key": {"type": "str"},
}, supports_check_mode=True)
if not module.check_mode:
    module.fail_json(msg="The synthetic module requires check mode.")
if module.params["operation"] != "inspect" or module.params["source"] is not None:
    module.fail_json(msg="Inspection must precede source handling.")
root = Path(module.params["firefox_root"])
if (root / "permission").exists():
    module.fail_json(msg="macOS denied data access.", permission_denied=True,
                     private="SYNTHETIC-PRIVATE-BOOKMARK")
for startup_check, message in STARTUP_MESSAGES.items():
    if (root / startup_check).exists():
        module.fail_json(msg=message, startup_check=startup_check,
                         private="SYNTHETIC-PRIVATE-BOOKMARK")
if (root / "refuse").exists():
    module.fail_json(msg="Synthetic import refusal.", private="SYNTHETIC-PRIVATE-BOOKMARK")
if (root / "absent").exists():
    module.exit_json(changed=False, imported=0, state="not_installed")
imported = (root / "already").exists()
module.exit_json(changed=not imported, imported=0,
                 state="already_imported" if imported else "would_import")
"""
MODULE = "STARTUP_MESSAGES = " + repr(STARTUP_MESSAGES) + "\n" + MODULE


class FirefoxBookmarksRoleTests(unittest.TestCase):
    def test_apply_requires_pending_inspection_and_matching_profile(self):
        tasks = yaml.safe_load(
            (ROOT / "roles/macos_firefox_bookmarks/tasks/main.yml").read_text()
        )[0]["block"]
        inspection = tasks[0]
        self.assertEqual(inspection["macos_firefox_bookmarks"]["operation"], "inspect")
        self.assertNotIn("check_mode", inspection)
        self.assertNotIn("source", inspection["macos_firefox_bookmarks"])
        apply = tasks[2]
        self.assertEqual(
            apply["when"],
            [
                "not ansible_check_mode",
                "macos_firefox_bookmarks_inspection.state == 'pending'",
            ],
        )
        importer = apply["block"][-1]["macos_firefox_bookmarks"]
        self.assertEqual(importer["operation"], "import")
        self.assertEqual(
            importer["expected_profile_key"],
            "{{ macos_firefox_bookmarks_inspection.profile_key }}",
        )
        self.assertEqual(apply["always"][0]["ansible.builtin.file"]["state"], "absent")
        environment = Environment(undefined=StrictUndefined)
        for check, state, expected in (
            (False, "pending", True),
            (False, "already_imported", False),
            (False, "not_installed", False),
            (True, "would_import", False),
        ):
            with self.subTest(check=check, state=state):
                context = {
                    "ansible_check_mode": check,
                    "macos_firefox_bookmarks_inspection": {"state": state},
                }
                self.assertEqual(
                    all(
                        environment.compile_expression(condition)(**context)
                        for condition in apply["when"]
                    ),
                    expected,
                )

    def test_rescue_uses_fixed_guidance_without_private_error_text(self):
        task = yaml.safe_load(
            (ROOT / "roles/macos_firefox_bookmarks/tasks/main.yml").read_text()
        )[0]
        message = task["rescue"][0]["vars"]["macos_defaults_refused_message"]
        template = Environment(undefined=StrictUndefined).from_string(message)
        for permission, startup_check in (
            (False, ""),
            (False, "unknown"),
            *((False, category) for category in STARTUP_MESSAGES),
            *((True, category) for category in ("", *STARTUP_MESSAGES)),
        ):
            with self.subTest(permission=permission, startup_check=startup_check):
                result = template.render(
                    ansible_failed_result={
                        "permission_denied": permission,
                        "startup_check": startup_check,
                        "msg": PRIVATE,
                    }
                )
                self.assertNotIn(PRIVATE, result)
                if permission:
                    self.assertEqual(
                        result,
                        "macOS denied data access. Run mac-setup at the Mac's screen. "
                        "Grant Full Disk Access or Firefox App Data access to the terminal app.",
                    )
                elif startup_check in STARTUP_MESSAGES:
                    self.assertEqual(result, STARTUP_MESSAGES[startup_check])
                else:
                    self.assertEqual(
                        result,
                        "Firefox bookmarks import refused. Check the source export, "
                        "vault access, and Firefox availability.",
                    )

    def run_role(self, state="pending", packaged=False):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            role = work / "roles/macos_firefox_bookmarks"
            (role / "tasks").mkdir(parents=True)
            (role / "library").mkdir()
            shutil.copyfile(
                ROOT / "roles/macos_firefox_bookmarks/tasks/main.yml",
                role / "tasks/main.yml",
            )
            refused = work / "roles/macos_defaults/tasks"
            refused.mkdir(parents=True)
            shutil.copyfile(
                ROOT / "roles/macos_defaults/tasks/refused.yml", refused / "refused.yml"
            )
            if packaged:
                shutil.copyfile(
                    ROOT
                    / "roles/macos_firefox_bookmarks/library/macos_firefox_bookmarks.py",
                    role / "library/macos_firefox_bookmarks.py",
                )
            else:
                (role / "library/macos_firefox_bookmarks.py").write_text(MODULE)
            (work / "inventory").mkdir()
            (work / "inventory/hosts.yml").write_text(
                yaml.safe_dump(
                    {
                        "all": {
                            "hosts": {
                                "example-mac": {
                                    "ansible_connection": "local",
                                    "ansible_python_interpreter": sys.executable,
                                }
                            }
                        }
                    }
                )
            )
            (work / state).touch()
            (work / "test-password").write_text("synthetic-test-key")
            (work / "test-password").chmod(0o600)
            play = [
                {
                    "hosts": "all",
                    "gather_facts": False,
                    "become": False,
                    "roles": ["macos_firefox_bookmarks"],
                    "vars": {
                        "macos_firefox_bookmarks_items": {
                            "source": "files/missing.vault"
                        },
                        "macos_firefox_bookmarks_account": "sample",
                        "macos_firefox_bookmarks_root": str(work),
                    },
                    "tasks": [
                        {"ansible.builtin.debug": {"var": "macos_defaults_refused"}}
                    ],
                }
            ]
            (work / "play.yml").write_text(yaml.safe_dump(play))
            done = subprocess.run(
                [
                    str(ROOT / ".local/venv/bin/ansible-playbook"),
                    "-i",
                    str(work / "inventory"),
                    str(work / "play.yml"),
                    "--check",
                    "--diff",
                ],
                cwd=work,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"),
                    "ANSIBLE_HOME": str(work / "ansible-home"),
                    "ANSIBLE_LOCAL_TEMP": str(work / "local-tmp"),
                    "ANSIBLE_REMOTE_TEMP": str(work / "remote-tmp"),
                    "ANSIBLE_LOG_PATH": str(work / "ansible.log"),
                    "ANSIBLE_VAULT_PASSWORD_FILE": str(work / "test-password"),
                    "ANSIBLE_ROLES_PATH": str(work / "roles"),
                    "ANSIBLE_MODULE_UTILS": str(ROOT / "module_utils"),
                    "ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.default",
                    "ANSIBLE_NOCOLOR": "1",
                },
            )
            output = done.stdout + done.stderr + (work / "ansible.log").read_text()
            self.assertEqual(done.returncode, 0, output)
            self.assertNotIn(PRIVATE, output)
            self.assertFalse(list(work.rglob("*.html")))
            if state in ("pending", "already", "absent"):
                for name in (
                    "Create a private bookmarks directory",
                    "Decrypt the selected bookmarks into the private directory",
                    "Import the private bookmarks file",
                ):
                    result = done.stdout.split(
                        f"TASK [macos_firefox_bookmarks : {name}]", 1
                    )[1].split("TASK [", 1)[0]
                    self.assertIn("skipping:", result)
            return output

    def test_packaged_real_module_previews_without_creating_a_profile(self):
        self.run_role(packaged=True)

    def test_check_never_reads_missing_source_or_creates_plaintext(self):
        self.run_role()

    def test_completed_import_ignores_missing_source_without_plaintext(self):
        self.run_role("already")

    def test_preview_failure_records_generic_refusal_without_private_result_fields(
        self,
    ):
        output = self.run_role("refuse")
        self.assertIn("Firefox bookmarks import refused.", output)
        self.assertIn("bookmarks.import_file", output)

    def test_absent_firefox_reports_the_skip(self):
        output = self.run_role("absent")
        self.assertIn(
            "Firefox bookmarks import skipped because Firefox is not installed.", output
        )

    def test_permission_refusal_keeps_safe_specific_guidance(self):
        output = self.run_role("permission")
        self.assertIn("macOS denied data access", output)
        self.assertIn(
            "Grant Full Disk Access or Firefox App Data access",
            " ".join(output.split()),
        )
        self.assertNotIn("Synthetic private failure.", output)

    def test_startup_refusals_keep_safe_specific_guidance(self):
        for category, message in STARTUP_MESSAGES.items():
            with self.subTest(category=category):
                output = self.run_role(category)
                self.assertIn(message, " ".join(output.split()))
                self.assertIn("bookmarks.import_file", output)


class FirefoxBookmarksModuleTests(unittest.TestCase):
    def run_module(self, operation="inspect", check=True, source=None, failure=None):
        class BookmarkError(Exception):
            pass

        class PermissionDenied(BookmarkError):
            pass

        class StartupError(BookmarkError):
            def __init__(self, message, check="automation"):
                super().__init__(message)
                self.check = check

        backend = ModuleType("ansible.module_utils.firefox_bookmarks")
        backend.BookmarkError = BookmarkError
        backend.PermissionDenied = PermissionDenied
        backend.StartupError = StartupError
        backend.inspect_bookmarks = Mock(
            return_value={"changed": True, "imported": 0, "state": "would_import"}
        )
        backend.import_bookmarks = Mock(
            return_value={"changed": False, "imported": 0, "state": "already_imported"}
        )
        if failure:
            if failure == "permission":
                error = PermissionDenied("Safe permission guidance.")
            elif failure in STARTUP_MESSAGES:
                error = StartupError(STARTUP_MESSAGES[failure], check=failure)
            else:
                error = BookmarkError("Safe refusal.")
            backend.inspect_bookmarks.side_effect = error
        module = SimpleNamespace(
            params={
                "operation": operation,
                "source": source,
                "firefox_root": "/synthetic/app-data",
                "expected_profile_key": "a" * 64,
            },
            check_mode=check,
            exit_json=Mock(),
            fail_json=Mock(side_effect=SystemExit),
        )
        factory = Mock(return_value=module)
        basic = ModuleType("ansible.module_utils.basic")
        basic.AnsibleModule = factory
        spec = importlib.util.spec_from_file_location(
            "test_bookmarks_module",
            ROOT / "roles/macos_firefox_bookmarks/library/macos_firefox_bookmarks.py",
        )
        imported = importlib.util.module_from_spec(spec)
        with patch.dict(
            sys.modules,
            {
                "ansible.module_utils.basic": basic,
                "ansible.module_utils.firefox_bookmarks": backend,
            },
        ):
            spec.loader.exec_module(imported)
            try:
                imported.main()
            except SystemExit:
                pass
        return module, backend, factory.call_args.kwargs

    def test_inspection_check_uses_no_source(self):
        module, backend, arguments = self.run_module()
        backend.inspect_bookmarks.assert_called_once_with(
            firefox_root="/synthetic/app-data", check=True
        )
        backend.import_bookmarks.assert_not_called()
        self.assertEqual(arguments["argument_spec"]["operation"]["default"], "import")
        self.assertFalse(arguments["argument_spec"]["source"].get("required", False))
        self.assertTrue(arguments["argument_spec"]["expected_profile_key"]["no_log"])
        module.fail_json.assert_not_called()

    def test_import_binds_expected_profile_and_preserves_check_mode(self):
        module, backend, _ = self.run_module(
            operation="import", source="/synthetic/export"
        )
        backend.import_bookmarks.assert_called_once_with(
            "/synthetic/export",
            firefox_root="/synthetic/app-data",
            check=True,
            expected_profile_key="a" * 64,
        )
        backend.inspect_bookmarks.assert_not_called()
        module.fail_json.assert_not_called()

    def test_apply_inspection_reaches_backend_without_forcing_check(self):
        module, backend, _ = self.run_module(check=False)
        backend.inspect_bookmarks.assert_called_once_with(
            firefox_root="/synthetic/app-data", check=False
        )
        module.fail_json.assert_not_called()

    def test_permission_failure_is_structured(self):
        module, _, _ = self.run_module(failure="permission")
        module.fail_json.assert_called_once_with(
            msg="Safe permission guidance.",
            changed=False,
            permission_denied=True,
            startup_check="",
        )

    def test_other_refusal_is_not_reported_as_permission_denied(self):
        module, _, _ = self.run_module(failure="other")
        module.fail_json.assert_called_once_with(
            msg="Safe refusal.",
            changed=False,
            permission_denied=False,
            startup_check="",
        )

    def test_startup_failures_expose_the_structured_category(self):
        for category, message in STARTUP_MESSAGES.items():
            with self.subTest(category=category):
                module, _, _ = self.run_module(failure=category)
                module.fail_json.assert_called_once_with(
                    msg=message,
                    changed=False,
                    permission_denied=False,
                    startup_check=category,
                )

    def test_import_requires_source_before_calling_backend(self):
        module, backend, _ = self.run_module(operation="import")
        module.fail_json.assert_called_once()
        backend.import_bookmarks.assert_not_called()


if __name__ == "__main__":
    unittest.main()
