from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
COUNTS = {
    "imported_count": 3,
    "existing_count": 0,
    "extra_count": 0,
    "private_copies": 0,
    "plaintext_directories": 0,
}


def fields(stage):
    if stage == "preview":
        return {"target_writes": 0}
    return dict(COUNTS, existing_count=int(stage.startswith("existing")))


def message(stage, values=None, wrapped=False):
    if stage == "refusal":
        body = "refused bookmarks.import_file for verify-mac: Firefox bookmarks import refused."
        if wrapped:
            body = body.replace(" ", "\n        ")
        return (
            "[ERROR]: Task failed: Action failed: refused bookmarks.import_file\n"
            + f"fatal: [verify-mac]: FAILED! =>\n    msg: '{body}'\n"
        )
    values = fields(stage) if values is None else values
    body = (
        "bookmarks "
        + stage
        + " "
        + " ".join(f"{key}={value}" for key, value in values.items())
    )
    if wrapped:
        body = body.replace(" ", "\n        ")
    return f"ok: [verify-mac] =>\n    msg: '{body}'\n"


class BookmarksOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = yaml.safe_load((ROOT / "verify/journeys/bookmarks.yml").read_text())
        cls.outputs = {run["name"]: run["output"][0] for run in manifest["runs"]}

    def count(self, stage, output):
        return len(re.findall(self.outputs[stage]["pattern"], output, re.M))

    def test_every_stage_accepts_one_wrapped_or_unwrapped_message(self):
        for stage in self.outputs:
            for wrapped in (False, True):
                with self.subTest(stage=stage, wrapped=wrapped):
                    self.assertEqual(
                        self.count(stage, message(stage, wrapped=wrapped)), 1
                    )

    def test_apply_accepts_the_observed_yaml_wrap(self):
        output = """ok: [verify-mac] =>
    msg: bookmarks apply imported_count=3 existing_count=0 extra_count=0 private_copies=0
        plaintext_directories=0
"""
        self.assertEqual(self.count("apply", output), 1)

    def test_wrong_counts_and_leaks_are_rejected_even_when_wrapped(self):
        for stage in self.outputs.keys() - {"refusal"}:
            for key, expected in fields(stage).items():
                for wrong in (expected + 1, str(expected) + "0", str(expected) + ".5"):
                    for wrapped in (False, True):
                        with self.subTest(
                            stage=stage, key=key, wrong=wrong, wrapped=wrapped
                        ):
                            values = dict(fields(stage), **{key: wrong})
                            self.assertEqual(
                                self.count(stage, message(stage, values, wrapped)), 0
                            )

    def test_missing_fields_cannot_be_borrowed_from_another_record(self):
        for stage in self.outputs.keys() - {"refusal"}:
            for key in fields(stage):
                for wrapped in (False, True):
                    with self.subTest(stage=stage, key=key, wrapped=wrapped):
                        values = dict(fields(stage))
                        del values[key]
                        output = message(stage, values, wrapped)
                        output += message("unrelated-stage", fields(stage), wrapped)
                        self.assertEqual(self.count(stage, output), 0)

    def test_stage_names_are_required(self):
        for stage in self.outputs.keys() - {"refusal"}:
            for wrapped in (False, True):
                with self.subTest(stage=stage, wrapped=wrapped):
                    output = message(stage, wrapped=wrapped).replace(
                        stage, "unrelated-stage"
                    )
                    self.assertEqual(self.count(stage, output), 0)

    def test_duplicate_messages_remain_two_matches(self):
        for stage, rule in self.outputs.items():
            for wrapped in (False, True):
                with self.subTest(stage=stage, wrapped=wrapped):
                    self.assertEqual(
                        self.count(stage, message(stage, wrapped=wrapped) * 2), 2
                    )
                    self.assertEqual(rule["count"], 1)

    def test_refusal_requires_its_message_and_exact_setting(self):
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                output = message("refusal", wrapped=wrapped)
                self.assertEqual(self.count("refusal", output.split("fatal:")[0]), 0)
                self.assertEqual(
                    self.count(
                        "refusal",
                        output.replace(
                            "bookmarks.import_file", "bookmarks.import_file_extra"
                        ),
                    ),
                    0,
                )


if __name__ == "__main__":
    unittest.main()
