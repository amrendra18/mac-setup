import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class FirefoxModulePackaging(unittest.TestCase):
    def test_packaged_module_previews_empty_and_refuses_invalid_without_reading_mac(
        self,
    ):
        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as folder:
            root = Path(folder)
            playbook = root / "firefox-probe.yml"
            playbook.write_text("""---
- name: Check the packaged Firefox module
  hosts: all
  gather_facts: false
  tasks:
    - name: Preview an empty selection
      macos_firefox:
        settings: {}
      register: empty
    - name: Refuse an invalid selection before any platform read
      macos_firefox:
        settings:
          firefox.pref.privacy_globalprivacycontrol_enabled: PRIVATE-INVALID-VALUE
      register: invalid
      ignore_errors: true
    - name: Check the public results
      ansible.builtin.assert:
        that:
          - not empty.changed
          - not empty.stored_verified
          - invalid is failed
          - not invalid.changed
          - "'Firefox value is invalid' in invalid.msg"
""")
            environment = dict(
                os.environ,
                ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                ANSIBLE_LIBRARY=str(ROOT / "roles/macos_firefox/library"),
                ANSIBLE_LOCAL_TEMP=str(root / "local-tmp"),
                ANSIBLE_REMOTE_TEMP=str(root / "remote-tmp"),
                ANSIBLE_LOG_PATH=str(root / "probe.log"),
            )
            result = subprocess.run(
                [
                    str(ROOT / ".local/venv/bin/ansible-playbook"),
                    "-i",
                    "localhost,",
                    "-c",
                    "local",
                    "-e",
                    "ansible_python_interpreter="
                    + str(ROOT / ".local/venv/bin/python"),
                    str(playbook),
                    "--check",
                ],
                env=environment,
                cwd=root,
                text=True,
                capture_output=True,
                timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("changed=0", result.stdout)
            self.assertIn("failed=0", result.stdout)
            self.assertNotIn("PRIVATE-INVALID-VALUE", result.stdout + result.stderr)

    def test_imported_site_accepts_journey_stage_then_refuses_policy_conflict(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as folder:
            root = Path(folder)
            wrapper = root / "wrapper.yml"
            wrapper.write_text(
                "---\n- name: Import the production site\n  ansible.builtin.import_playbook: "
                + str(ROOT / "playbooks/site.yml")
                + "\n"
            )
            environment = dict(
                os.environ,
                ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                ANSIBLE_LOCAL_TEMP=str(root / "local-tmp"),
                ANSIBLE_REMOTE_TEMP=str(root / "remote-tmp"),
                ANSIBLE_LOG_PATH=str(root / "probe.log"),
            )
            result = subprocess.run(
                [
                    str(ROOT / ".local/venv/bin/ansible-playbook"),
                    "-i",
                    str(ROOT / "inventory/example"),
                    str(wrapper),
                    "--check",
                    "-e",
                    "firefox_journey_stage=preview",
                    "-e",
                    '{"machine_settings":{"firefox.homepage.locked":false}}',
                ],
                env=environment,
                cwd=root,
                text=True,
                capture_output=True,
                timeout=90,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("contradicts firefox.lock", result.stdout)
            self.assertNotIn("unknown profile key", result.stdout)
            self.assertIn("changed=0", result.stdout)
            self.assertIn("TASK [profile : Check the profile]", result.stdout)


if __name__ == "__main__":
    unittest.main()
