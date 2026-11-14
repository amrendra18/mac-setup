import errno
import os
import pty
import select
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = r"""
import os
import sys
from ansible.plugins.action import ActionBase
sys.path.insert(0, ROOT + '/tests/modules')
from test_macos_default_browser import FakeMac
from macos_default_browser import Session, ensure_default

class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        mac = FakeMac()
        args = self._task.args
        session = Session(501, 501, any(key in os.environ for key in (
            'SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY')))
        result = ensure_default(mac, args['ask'], self._task.check_mode,
                                session, True, args.get('interactive', False))
        result['requests'] = mac.requests
        return result
"""


class DefaultBrowserControllerPlumbing(unittest.TestCase):
    def run_controller(self, terminal, environment=None, check=False, inherited=False):
        profile_tasks = yaml.safe_load(
            (ROOT / "roles/profile/tasks/main.yml").read_text()
        )
        terminal_task = next(
            task
            for task in profile_tasks
            if "profile_interactive" in task.get("ansible.builtin.set_fact", {})
        )
        mode = next(
            task["ansible.builtin.set_fact"]["profile_run_mode"]
            for task in profile_tasks
            if "profile_run_mode" in task.get("ansible.builtin.set_fact", {})
        )
        site = yaml.safe_load((ROOT / "playbooks/site.yml").read_text())
        browser_task = next(
            task
            for play in site
            for task in play.get("tasks", [])
            if task.get("ansible.builtin.include_role", {}).get("name")
            == "macos_default_browser"
        )
        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as folder:
            work = Path(folder)
            action = work / "action_plugins"
            action.mkdir()
            (action / "macos_default_browser.py").write_text(
                "ROOT = " + repr(str(ROOT)) + "\n" + ACTION
            )
            tasks = [
                terminal_task,
                {
                    "name": "Choose the run mode",
                    "ansible.builtin.set_fact": {"profile_run_mode": mode},
                },
                browser_task,
                {
                    "name": "Check the artificial request count",
                    "ansible.builtin.assert": {
                        "that": [
                            "macos_default_browser_result.requests == expected_requests",
                            "macos_default_browser_result.requested == (expected_requests == 1)",
                        ]
                    },
                },
            ]
            expected = int(terminal and not environment and not check)
            playbook = work / "probe.yml"
            playbook.write_text(
                yaml.safe_dump(
                    [
                        {
                            "name": "Check controller terminal plumbing",
                            "hosts": "all",
                            "gather_facts": False,
                            "vars": {
                                "profile_running_account": "runner",
                                "profile_own_work": {"default_browser": "firefox"},
                                "expected_requests": expected,
                            },
                            "tasks": tasks,
                        }
                    ]
                )
            )
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
            }
            env.update(environment or {})
            if inherited:
                env["MAC_SETUP_CONTROLLER_INTERACTIVE"] = "1"
            env.update(
                {
                    "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"),
                    "ANSIBLE_ACTION_PLUGINS": str(action),
                    "ANSIBLE_LOOKUP_PLUGINS": str(
                        ROOT / "roles/profile/lookup_plugins"
                    ),
                    "ANSIBLE_FILTER_PLUGINS": str(
                        ROOT / "roles/profile/filter_plugins"
                    ),
                    "ANSIBLE_LOCAL_TEMP": str(work / "local-tmp"),
                    "ANSIBLE_REMOTE_TEMP": str(work / "remote-tmp"),
                    "ANSIBLE_LOG_PATH": str(work / "probe.log"),
                    "ANSIBLE_NOCOLOR": "1",
                }
            )
            command = [
                str(ROOT / ".local/venv/bin/ansible-playbook"),
                "-i",
                "localhost,",
                "-c",
                "local",
                str(playbook),
            ]
            if check:
                command.append("--check")
            if terminal:
                master, slave = pty.openpty()
                process = None
                try:
                    try:
                        process = subprocess.Popen(
                            command,
                            cwd=ROOT,
                            env=env,
                            stdin=slave,
                            stdout=slave,
                            stderr=subprocess.PIPE,
                        )
                    finally:
                        os.close(slave)
                    output = bytearray()
                    deadline = time.monotonic() + 90
                    while True:
                        remaining = deadline - time.monotonic()
                        if (
                            remaining <= 0
                            or not select.select([master], [], [], remaining)[0]
                        ):
                            raise subprocess.TimeoutExpired(command, 90)
                        try:
                            block = os.read(master, 65536)
                        except OSError as error:
                            if error.errno != errno.EIO:
                                raise
                            break
                        if not block:
                            break
                        output.extend(block)
                    _, errors = process.communicate(timeout=90)
                    code, output = process.returncode, output.decode() + errors.decode()
                finally:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)
                    os.close(master)
            else:
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                code, output = result.returncode, result.stdout + result.stderr
            self.assertEqual(code, 0, output)
            self.assertIn("failed=0", output)

    def test_terminal_controller_allows_one_artificial_request(self):
        self.run_controller(True)

    def test_redirected_controller_requests_nothing(self):
        self.run_controller(False)

    def test_redirected_controller_overwrites_inherited_interactive_marker(self):
        self.run_controller(False, inherited=True)

    def test_terminal_ssh_empty_ssh_and_check_mode_request_nothing(self):
        for environment, check in (
            ({"SSH_CONNECTION": "artificial"}, False),
            ({"SSH_CONNECTION": ""}, False),
            ({}, True),
        ):
            with self.subTest(environment=environment, check=check):
                self.run_controller(True, environment, check)


if __name__ == "__main__":
    unittest.main()
