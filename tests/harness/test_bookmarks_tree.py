import copy
from contextlib import redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "bookmarks_tree_probe", ROOT / "verify/helpers/bookmarks.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

VALUES = {
    "bookmarks": [
        {"title": f"Artificial {i}", "url": f"https://example.invalid/{i}"}
        for i in range(3)
    ],
    "existing": {"title": "Existing", "url": "https://example.invalid/existing"},
    "extra": {"title": "Later", "url": "https://example.invalid/later"},
}

NODE = r"""
const input = JSON.parse(process.argv[1]);
const rows = input.rows;
globalThis.Services = {policies: {getActivePolicies: () => ({DisableProfileImport: true})}};
globalThis.ChromeUtils = {importESModule: () => ({PlacesUtils: {bookmarks: {
  TYPE_BOOKMARK: 1, TYPE_FOLDER: 2, TYPE_SEPARATOR: 3,
  toolbarGuid: 'toolbar_____', menuGuid: 'menu________', unfiledGuid: 'unfiled_____',
  search: async ({url}) => rows.filter(row => row.url === url),
  insert: async item => {
    const siblings = rows.filter(row => row.parentGuid === item.parentGuid);
    const index = item.index ?? siblings.length;
    for (const row of siblings) if (row.index >= index) row.index++;
    const row = {guid: `seed-${rows.length}`, parentGuid: item.parentGuid, index,
      type: item.type || 1, title: item.title || '', url: item.url || ''};
    rows.push(row);
    return row;
  },
  fetch: async (info, callback) => {
    if (!info.parentGuid || typeof callback !== 'function') throw Error('Unsupported fake API');
    const found = rows.filter(row => row.parentGuid === info.parentGuid);
    for (const row of found) callback({...row, url: row.url ? new URL(row.url) : undefined});
    return found[0] || null;
  }
}}})};
const result = await new Promise(done => new Function(input.script)(...input.args, done));
console.log(JSON.stringify(result));
"""


def rows(existing=False):
    entries = []

    def add(guid, parent, index, kind, title="", url=""):
        entries.append(
            dict(
                guid=guid,
                parentGuid=parent,
                index=index,
                type=kind,
                title=title,
                url=url,
            )
        )

    if existing:
        add("old-folder__", "toolbar_____", 0, 2, "Existing folder")
        add("old-divider_", "old-folder__", 0, 3)
        add("old-bookmark", "old-folder__", 1, 1, **VALUES["existing"])
    add("new-folder__", "toolbar_____", int(existing), 2, "Artificial collection")
    add("new-inner___", "new-folder__", 0, 2, "Artificial nested")
    add("new-first___", "new-inner___", 0, 1, **VALUES["bookmarks"][0])
    add("new-second__", "new-inner___", 1, 1, **VALUES["bookmarks"][1])
    add("new-other___", "new-folder__", 1, 2, "Artificial second")
    add("new-third___", "new-other___", 0, 1, **VALUES["bookmarks"][2])
    return entries


class BookmarksReadbackChecksTests(unittest.TestCase):
    def result(self):
        return dict(
            blocked=True,
            counts=[1, 1, 1],
            titles=True,
            existing=0,
            extra=0,
            structure=True,
            preserved=True,
        )

    def output(self, result):
        output = io.StringIO()
        with patch.object(probe.importer, "FirefoxSession") as browser, patch.object(
            probe,
            "main",
            side_effect=lambda: probe.read_counts(Path("/unused"), VALUES),
        ), redirect_stderr(output):
            browser.return_value.__enter__.return_value.script.return_value = result
            code = probe.cli()
        return code, output.getvalue()

    def test_each_failed_readback_check_has_its_own_fixed_message(self):
        for field, value, message in (
            ("blocked", False, "The active DisableProfileImport policy is not true."),
            (
                "counts",
                [2, 1, 1],
                "The bookmark readback found an unexpected copy count for a source address.",
            ),
            ("titles", False, "The bookmark readback titles differ from the source."),
            ("existing", 1, "The bookmark readback existing bookmark count differs."),
            ("extra", 1, "The bookmark readback found unexpected extra bookmarks."),
            ("structure", False, "The bookmark readback ordered folder tree differs."),
            (
                "preserved",
                False,
                "The bookmark readback did not preserve existing entries, folders, and positions.",
            ),
        ):
            with self.subTest(field=field):
                result = self.result()
                result[field] = value
                code, output = self.output(result)
                self.assertEqual(code, 1)
                self.assertEqual(output, f"Bookmarks journey check failed: {message}\n")

    def test_invalid_readback_result_has_a_fixed_message_without_private_values(self):
        private = "SYNTHETIC-PRIVATE-TITLE https://example.invalid/private"
        invalid = [None, private, {"error": True}, dict(self.result(), private=private)]
        missing = self.result()
        del missing["preserved"]
        invalid.append(missing)
        for field, value in (
            ("counts", private),
            ("counts", [True, 1, 1]),
            ("blocked", 1),
            ("titles", 1),
            ("existing", False),
            ("extra", False),
            ("structure", private),
            ("preserved", 1),
        ):
            invalid.append(dict(self.result(), **{field: value}))
        for index, result in enumerate(invalid):
            with self.subTest(case=index):
                code, output = self.output(result)
                self.assertEqual(code, 1)
                self.assertEqual(
                    output,
                    "Bookmarks journey check failed: The bookmark readback result is invalid.\n",
                )
                self.assertNotIn(private, output)


@unittest.skipUnless(shutil.which("node"), "Node is needed for the actual Places probe")
class BookmarksTreeTests(unittest.TestCase):
    def execute(self, script, args, entries):
        run = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                NODE,
                json.dumps(dict(script=script, args=args, rows=entries)),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(run.returncode, 0, "The synthetic Places runner failed.")
        return json.loads(run.stdout)

    def read(self, entries, existing=False):
        client = Mock()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory)
        ), patch.object(probe.importer, "FirefoxSession") as browser:
            if existing:
                old = [
                    entry for entry in rows(True) if entry["guid"].startswith("old-")
                ]
                probe.write_private(
                    Path(directory) / "existing-tree.json", json.dumps(old).encode()
                )
            browser.return_value.__enter__.return_value = client

            def execute(script, args, asynchronous=False):
                result = self.execute(script, args, entries)
                self.assertTrue(
                    all(
                        isinstance(value, (bool, int))
                        or isinstance(value, list)
                        and all(isinstance(item, int) for item in value)
                        for value in result.values()
                    ),
                    "The Places probe returned private bookmark data.",
                )
                return result

            client.script.side_effect = execute
            return probe.read_counts(Path(directory), copy.deepcopy(VALUES), existing)

    def test_correct_nested_tree_is_accepted_in_both_profile_states(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.assertEqual(self.read(rows(existing), existing), 3)

    def test_fixture_has_three_links_in_order_inside_nested_toolbar_folders(self):
        tree = probe.importer.parse_html(probe.fixture_html(VALUES["bookmarks"]))
        self.assertEqual(tree["menu"], [])
        self.assertEqual(tree["unfiled"], [])
        self.assertEqual(len(tree["toolbar"]), 1)
        collection = tree["toolbar"][0]
        self.assertEqual(collection["title"], "Artificial collection")
        nested, second = collection["children"]
        self.assertEqual(nested["title"], "Artificial nested")
        self.assertEqual(second["title"], "Artificial second")
        self.assertEqual(
            [(row["title"], row["url"]) for row in nested["children"]],
            [(row["title"], row["url"]) for row in VALUES["bookmarks"][:2]],
        )
        self.assertEqual(
            [(row["title"], row["url"]) for row in second["children"]],
            [(row["title"], row["url"]) for row in VALUES["bookmarks"][2:]],
        )

    def test_existing_seed_records_the_actual_tree_with_known_folder_and_position(self):
        initial = [
            dict(
                guid="default-item",
                parentGuid="toolbar_____",
                index=0,
                type=1,
                title="Default item",
                url="https://example.invalid/default",
            )
        ]
        client = Mock()
        client.script.side_effect = (
            lambda script, args, asynchronous=False: self.execute(script, args, initial)
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            probe, "STATE", Path(directory)
        ), patch.object(probe.importer, "FirefoxSession") as browser:
            browser.return_value.__enter__.return_value = client
            probe.seed_existing(Path(directory) / "target", VALUES)
            snapshot = Path(directory) / "existing-tree.json"
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
            recorded = json.loads(snapshot.read_text())
        self.assertEqual(len(recorded), 4)
        folder, separator, bookmark, default = recorded
        self.assertEqual(
            (folder["title"], folder["parentGuid"], folder["index"]),
            ("Existing folder", "toolbar_____", 0),
        )
        self.assertEqual(
            (separator["type"], separator["parentGuid"], separator["index"]),
            (3, folder["guid"], 0),
        )
        self.assertEqual(
            (
                bookmark["title"],
                bookmark["url"],
                bookmark["parentGuid"],
                bookmark["index"],
            ),
            (VALUES["existing"]["title"], VALUES["existing"]["url"], folder["guid"], 1),
        )
        self.assertEqual((default["guid"], default["index"]), ("default-item", 1))

    def test_refusal_native_probe_rejects_synthetic_rows_and_accepts_unrelated_rows(
        self,
    ):
        unrelated = dict(
            guid="native-item_",
            parentGuid="toolbar_____",
            index=0,
            type=1,
            title="Native default",
            url="https://example.invalid/default",
        )
        for entries, accepted in (([unrelated], True), (rows(), False)):
            with self.subTest(
                accepted=accepted
            ), tempfile.TemporaryDirectory() as directory, patch.object(
                probe.importer, "FirefoxSession"
            ) as factory:
                session = factory.return_value.__enter__.return_value
                session.profile_key = "a" * 64
                session.script.side_effect = (
                    lambda script, args, asynchronous=False: self.execute(
                        script, args, entries
                    )
                )
                if accepted:
                    probe.check_refusal(Path(directory), VALUES)
                else:
                    with self.assertRaises(probe.JourneyCheckError):
                        probe.check_refusal(Path(directory), VALUES)

    def test_moved_and_reordered_imports_are_rejected_in_both_profile_states(self):
        for existing in (False, True):
            for defect in (
                "root-move",
                "bookmark-move",
                "bookmark-order",
                "folder-order",
                "folder-name",
            ):
                with self.subTest(existing=existing, defect=defect):
                    entries = rows(existing)
                    by_guid = {row["guid"]: row for row in entries}
                    if defect == "root-move":
                        by_guid["new-folder__"]["parentGuid"] = "menu________"
                    elif defect == "bookmark-move":
                        by_guid["new-first___"]["parentGuid"] = "new-other___"
                    elif defect == "bookmark-order":
                        by_guid["new-first___"]["index"] = 1
                        by_guid["new-second__"]["index"] = 0
                    elif defect == "folder-order":
                        by_guid["new-inner___"]["index"] = 1
                        by_guid["new-other___"]["index"] = 0
                    else:
                        by_guid["new-inner___"]["title"] = "Different folder"
                    with self.assertRaises(probe.JourneyCheckError):
                        self.read(entries, existing)

    def test_moved_and_reordered_existing_entries_are_rejected(self):
        for defect in (
            "bookmark-move",
            "bookmark-order",
            "folder-move",
            "folder-order",
        ):
            with self.subTest(defect=defect):
                entries = rows(True)
                by_guid = {row["guid"]: row for row in entries}
                if defect == "bookmark-move":
                    by_guid["old-bookmark"]["parentGuid"] = "unfiled_____"
                elif defect == "bookmark-order":
                    by_guid["old-bookmark"]["index"] = 0
                    by_guid["old-divider_"]["index"] = 1
                elif defect == "folder-move":
                    by_guid["old-folder__"]["parentGuid"] = "menu________"
                else:
                    by_guid["old-folder__"]["index"] = 1
                    by_guid["new-folder__"]["index"] = 0
                with self.assertRaises(probe.JourneyCheckError):
                    self.read(entries, True)


if __name__ == "__main__":
    unittest.main()
