import json
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROOFS = {
    "apply": {
        "artificial_history_after_quit": 0,
        "artificial_history_before_quit": 1,
        "checked_preferences": 4,
        "owned_locked_paths": 4,
        "policy_errors": 0,
        "user_override_reported": False,
    },
    "again": {
        "checked_preferences": 4,
        "owned_locked_paths": 4,
        "policy_errors": 0,
        "user_override_reported": False,
    },
    "unlock": {
        "checked_preferences": 4,
        "owned_locked_paths": 0,
        "policy_errors": 0,
        "user_override_reported": True,
    },
}
REQUIRED = {
    "apply": {
        "artificial_history_after_quit": 0,
        "artificial_history_before_quit": 1,
        "policy_errors": 0,
    },
    "again": {"policy_errors": 0},
    "unlock": {
        "owned_locked_paths": 0,
        "policy_errors": 0,
        "user_override_reported": True,
    },
}
PREVIEW = "keys_written=0 bytes_unchanged=true metadata_unchanged=true"


def ansible_message(stage, proof=None, wrapped=False):
    body = PREVIEW if stage == "preview" else json.dumps(proof, sort_keys=True)
    if wrapped:
        body = body.replace(" ", "\n        ")
        prefix = f"firefox\n        {stage}\n        "
    else:
        prefix = f"firefox {stage} "
    return f"ok: [verify-mac] => \n    msg: '{prefix}{body}'\n"


class FirefoxOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = yaml.safe_load((ROOT / "verify/journeys/firefox.yml").read_text())
        cls.outputs = {run["name"]: run["output"][0] for run in manifest["runs"]}

    def count(self, stage, output):
        return len(re.findall(self.outputs[stage]["pattern"], output, re.M))

    def test_each_stage_accepts_one_unwrapped_or_wrapped_record(self):
        for stage in self.outputs:
            for wrapped in (False, True):
                with self.subTest(stage=stage, wrapped=wrapped):
                    output = ansible_message(stage, PROOFS.get(stage), wrapped)
                    self.assertEqual(self.count(stage, output), 1)

    def test_apply_accepts_the_observed_yaml_wrap_shape(self):
        output = """ok: [verify-mac] =>
    msg: 'firefox apply {"artificial_history_after_quit": 0, "artificial_history_before_quit":
        1, "checked_preferences": 4, "owned_locked_paths": 4, "policy_errors": 0, "user_override_reported":
        false}'
"""
        self.assertEqual(self.count("apply", output), 1)

    def test_required_json_values_reject_wrong_counts_and_types(self):
        for stage, fields in REQUIRED.items():
            for field, expected in fields.items():
                wrong_values = (
                    (False, 1, 10, "true")
                    if expected is True
                    else (
                        expected + 1,
                        expected + 10,
                        expected + 0.5,
                        str(expected),
                        False,
                    )
                )
                for wrong in wrong_values:
                    for wrapped in (False, True):
                        with self.subTest(
                            stage=stage, field=field, wrong=wrong, wrapped=wrapped
                        ):
                            proof = dict(PROOFS[stage], **{field: wrong})
                            output = ansible_message(stage, proof, wrapped)
                            self.assertEqual(self.count(stage, output), 0)

    def test_missing_json_fields_cannot_come_from_a_later_object(self):
        for stage, fields in REQUIRED.items():
            for field in fields:
                proof = dict(PROOFS[stage])
                del proof[field]
                for wrapped in (False, True):
                    with self.subTest(stage=stage, field=field, wrapped=wrapped):
                        output = ansible_message(stage, proof, wrapped)
                        self.assertEqual(self.count(stage, output), 0)
                        output += "unrelated " + json.dumps(PROOFS[stage]) + "\n"
                        self.assertEqual(self.count(stage, output), 0)

    def test_wrong_stage_does_not_match(self):
        for stage in self.outputs:
            for wrapped in (False, True):
                with self.subTest(stage=stage, wrapped=wrapped):
                    output = ansible_message(stage, PROOFS.get(stage), wrapped)
                    output = output.replace(stage, "unrelated-stage")
                    self.assertEqual(self.count(stage, output), 0)

    def test_preview_requires_all_expected_values(self):
        for field in PREVIEW.split():
            for replacement in ("", field.replace("=0", "=1").replace("true", "false")):
                for wrapped in (False, True):
                    with self.subTest(
                        field=field, replacement=replacement, wrapped=wrapped
                    ):
                        output = ansible_message("preview", wrapped=wrapped)
                        self.assertEqual(
                            self.count("preview", output.replace(field, replacement)), 0
                        )

    def test_duplicate_records_retain_two_matches_and_reject_expected_count(self):
        for stage, rule in self.outputs.items():
            for wrapped in (False, True):
                with self.subTest(stage=stage, wrapped=wrapped):
                    output = ansible_message(stage, PROOFS.get(stage), wrapped) * 2
                    count = self.count(stage, output)
                    self.assertEqual(count, 2)
                    self.assertEqual(rule["count"], 1)
                    self.assertNotEqual(count, rule["count"])


if __name__ == "__main__":
    unittest.main()
