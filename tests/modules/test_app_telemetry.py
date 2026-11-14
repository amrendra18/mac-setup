import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/profile/filter_plugins"))

from profile import profile_problems, profile_work

FILES = {
    path.name: yaml.safe_load(path.read_text())
    for path in sorted((ROOT / "settings/macos").glob("*.yml"))
}
TABLE = yaml.safe_load((ROOT / "settings/macos/permissions/table.yml").read_text())
SETTING = "privacy.improve_sensitive_content_warning"


class AppTelemetryTests(unittest.TestCase):
    def test_sensitive_content_analytics_opt_out_needs_a_person(self):
        profile = {"default_settings": {SETTING: False}}
        self.assertEqual(profile_problems(profile, FILES), [])
        work = profile_work(profile, FILES, "runner", TABLE)
        [item] = work["by_hand"]
        self.assertEqual(item["id"], SETTING)
        self.assertEqual(
            item["where"],
            "System Settings > Privacy & Security > Sensitive Content Warning",
        )
        self.assertTrue(item["link"].startswith("x-apple.systempreferences:"))
        self.assertIn("off", item["choose"])
        self.assertEqual(work["own"]["writes"], [])
        self.assertEqual(work["commands"], [])
        self.assertEqual(work["machine_writes"], [])

    def test_omission_leaves_sensitive_content_analytics_alone(self):
        profile = {"default_settings": {}}
        self.assertEqual(profile_problems(profile, FILES), [])
        work = profile_work(profile, FILES, "runner", TABLE)
        self.assertEqual(work["by_hand"], [])


if __name__ == "__main__":
    unittest.main()
