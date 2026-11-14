import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = "org.example.NewApp"
NOOP = [
    {"name": "Leave unrelated settings alone", "ansible.builtin.debug": {"msg": "test"}}
]


def check_run(*, twice=False, install_failure=False, dock_failure=False):
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        roles = work / "roles"

        def tasks(role, body, name="main"):
            target = roles / role / "tasks" / f"{name}.yml"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(yaml.safe_dump(body, sort_keys=False))

        tasks(
            "profile",
            [
                {
                    "name": "Prepare a profile for a newly installed Dock app",
                    "ansible.builtin.set_fact": {
                        "profile_running_account": "example-user",
                        "profile_own_work": {
                            "default_browser": "",
                            "bookmarks_import": {},
                            "writes": [],
                            "dock_apps": {"apps": [APP]},
                            "wallpaper": [],
                            "commands": [],
                            "finder_sidebar": [],
                        },
                        "profile_other_work": [],
                        "profile_machine_writes": [],
                        "profile_commands": [],
                        "profile_apps": [APP],
                        "profile_firefox": {},
                        "profile_permissions": [],
                        "profile_by_hand": [],
                    },
                }
            ],
        )
        tasks(
            "profile",
            [
                {
                    "name": "Report an unexpected stop",
                    "ansible.builtin.fail": {"msg": "{{ site_stopped }}"},
                    "when": "site_stopped is defined",
                }
            ],
            "summary",
        )
        tasks(
            "macos_permissions",
            [
                {
                    "name": "Use no real permissions",
                    "ansible.builtin.set_fact": {
                        "macos_permissions_blocked": [],
                        "macos_permissions_summary": [],
                        "macos_permissions_run_mode": {"ask": False},
                    },
                }
            ],
        )
        tasks(
            "macos_permissions",
            [
                {
                    "name": "Clean up test permissions",
                    "ansible.builtin.debug": {"msg": "cleaned up"},
                }
            ],
            "take_back",
        )
        tasks(
            "macos_apps",
            [
                {
                    "name": "Reject a broken test installer",
                    "ansible.builtin.fail": {"msg": "test installer failed"},
                    "when": "test_install_failure",
                },
                {
                    "name": "Read the test installation state",
                    "ansible.builtin.set_fact": {
                        "test_install_needed": "{{ macos_apps_items != test_installed_apps | default([]) }}"
                    },
                },
                {
                    "name": "Install apps into test state",
                    "ansible.builtin.set_fact": {
                        "test_installed_apps": "{{ macos_apps_items }}"
                    },
                    "changed_when": "test_install_needed",
                },
            ],
        )
        tasks(
            "macos_dock_apps",
            [
                {
                    "name": "Require the Dock apps to be installed",
                    "ansible.builtin.assert": {
                        "that": [
                            "not test_dock_failure",
                            "macos_dock_apps_account == profile_running_account",
                            "macos_dock_apps_items.apps | difference(test_installed_apps | default([])) | length == 0",
                        ]
                    },
                },
                {
                    "name": "Read the test Dock state",
                    "ansible.builtin.set_fact": {
                        "test_dock_needed": "{{ macos_dock_apps_items.apps != test_dock_apps | default([]) }}"
                    },
                },
                {
                    "name": "Write the Dock into test state",
                    "ansible.builtin.set_fact": {
                        "test_dock_apps": "{{ macos_dock_apps_items.apps }}"
                    },
                    "changed_when": "test_dock_needed",
                    "notify": "Restart Dock",
                },
            ],
        )
        tasks(
            "macos_defaults",
            [
                {
                    "name": "Read the test screen owner",
                    "ansible.builtin.set_fact": {
                        "macos_defaults_screen_owner": "{{ {'stdout': profile_running_account} if macos_defaults_restart else {'skipped': true} }}"
                    },
                }
            ],
        )
        defaults = roles / "macos_defaults" / "defaults" / "main.yml"
        defaults.parent.mkdir(parents=True, exist_ok=True)
        defaults.write_text(yaml.safe_dump({"macos_defaults_restart": True}))
        handler = yaml.safe_load(
            (ROOT / "roles/macos_defaults/handlers/main.yml").read_text()
        )[0]
        tasks(
            "macos_defaults",
            [
                {
                    "name": handler["name"],
                    "ansible.builtin.set_fact": {
                        "test_dock_restarts": "{{ test_dock_restarts | default(0) | int + 1 }}",
                        "test_dock_restart_account": "{{ macos_defaults_account }}",
                    },
                    "changed_when": True,
                    "when": handler["when"],
                }
            ],
            "../handlers/main",
        )
        for role in (
            "macos_wallpaper",
            "macos_commands",
            "macos_finder_sidebar",
            "macos_accounts",
            "macos_firefox",
            "macos_firefox_bookmarks",
            "macos_by_hand",
        ):
            tasks(role, NOOP)
        playbook = [{"import_playbook": str(ROOT / "playbooks/site.yml")}]
        snapshot = {
            "name": "Check the requested Dock state",
            "hosts": "macs",
            "gather_facts": False,
            "tasks": [
                {
                    "name": "Check the installed app is in the Dock",
                    "ansible.builtin.assert": {
                        "that": [
                            f"test_dock_apps == ['{APP}']",
                            "test_dock_restarts | int == 1",
                            "test_dock_restart_account == profile_running_account",
                        ]
                    },
                }
            ],
        }
        if twice:
            playbook += [
                snapshot,
                {
                    "name": "Start the second run",
                    "hosts": "macs",
                    "gather_facts": False,
                    "tasks": [],
                },
                {"import_playbook": str(ROOT / "playbooks/site.yml")},
                snapshot,
            ]
        (work / "site.yml").write_text(yaml.safe_dump(playbook, sort_keys=False))
        (work / "hosts.yml").write_text(
            yaml.safe_dump(
                {"macs": {"hosts": {"example-mac": {"ansible_connection": "local"}}}}
            )
        )
        return subprocess.run(
            [
                str(ROOT / ".local/venv/bin/ansible-playbook"),
                "-i",
                str(work / "hosts.yml"),
                "-e",
                json.dumps(
                    {
                        "ansible_become": False,
                        "test_install_failure": install_failure,
                        "test_dock_failure": dock_failure,
                    }
                ),
                str(work / "site.yml"),
                "--check",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"),
                "ANSIBLE_HOME": str(work / "ansible-home"),
                "ANSIBLE_LOCAL_TEMP": str(work / "local-tmp"),
                "ANSIBLE_REMOTE_TEMP": str(work / "remote-tmp"),
                "ANSIBLE_LOG_PATH": str(work / "ansible.log"),
                "ANSIBLE_NOCOLOR": "1",
                "ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.default",
                "ANSIBLE_ROLES_PATH": str(roles),
            },
        )


class NewlyInstalledDockApp(unittest.TestCase):
    def test_the_app_reaches_the_dock_in_one_run_and_the_second_changes_nothing(self):
        done = check_run(twice=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        first, second = done.stdout.split("PLAY [Start the second run]", 1)
        self.assertEqual(len(re.findall(r"(?m)^changed: \[example-mac\]", first)), 3)
        self.assertEqual(len(re.findall(r"(?m)^changed: \[example-mac\]", second)), 0)
        self.assertEqual(
            first.count("RUNNING HANDLER [macos_defaults : Restart Dock]"), 1
        )
        self.assertNotIn("RUNNING HANDLER", second)

    def test_a_failed_installer_skips_the_dock_and_still_cleans_up(self):
        done = check_run(install_failure=True)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertNotIn("TASK [macos_dock_apps : Require the Dock apps", done.stdout)
        self.assertIn(
            "TASK [macos_permissions : Clean up test permissions]", done.stdout
        )
        self.assertIn("test installer failed", done.stdout)

    def test_a_dock_failure_still_cleans_up_and_reports_the_stop(self):
        done = check_run(dock_failure=True)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn(
            "TASK [macos_permissions : Clean up test permissions]", done.stdout
        )
        self.assertIn("TASK [Record where this account's Dock stopped]", done.stdout)


if __name__ == "__main__":
    unittest.main()
