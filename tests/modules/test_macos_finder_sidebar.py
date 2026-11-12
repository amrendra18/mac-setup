import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_finder_sidebar/library"))

from macos_finder_sidebar import SidebarError, ensure_sidebar

TOOL = "/tmp/scratch/sidebar"
HOME = "/example/accounts/sam"
OTHER = "/example/accounts/robin"
FOLDERS = {
    "/Applications",
    HOME,
    HOME + "/Desktop",
    HOME + "/Documents",
    HOME + "/Downloads",
    HOME + "/My Work",
    OTHER,
}


def url(path):
    return "file://" + path.replace(" ", "%20") + "/"


RECENTS = "file:///System/Library/CoreServices/Finder.app/Contents/Resources/MyLibraries/myDocuments.cannedSearch/"
AIRDROP = "nwnode://domain-AirDrop"
START = {
    "FavoriteItems": [
        RECENTS,
        AIRDROP,
        "file:///Applications/",
        url(HOME + "/Desktop"),
        url(HOME + "/Documents"),
        url(HOME + "/Downloads"),
    ],
    "FavoriteVolumes": [
        "file:///",
        url(HOME),
        "nwnode://domain-Network",
    ],
    "TopSidebarSection": [],
}


class FakeSidebar:
    def __init__(self, lists=START, refuse=()):
        self.lists = {name: list(urls) for name, urls in lists.items()}
        self.refuse = refuse
        self.changes = []

    def run(self, argv):
        assert argv[0] == TOOL
        command, args = argv[1], argv[2:]
        if command == "read":
            return (
                0,
                "".join(
                    f"{name}\t{item}\n"
                    for name, urls in self.lists.items()
                    for item in urls
                ),
                "",
            )
        self.changes.append(argv[1:])
        if command == "remove":
            name, urls = args[0], set(args[1:])
            self.lists[name] = [u for u in self.lists[name] if u not in urls]
            return 0, "", ""
        if command == "first":
            favorites = self.lists["FavoriteItems"]
            at = 0
            for path in args:
                if path in self.refuse:
                    return 3, "", f"refused {path}\n"
                if url(path) in favorites:
                    favorites.remove(url(path))
                favorites.insert(at, url(path))
                at += 1
            return 0, "", ""
        return 2, "", "usage\n"


def ensure(mac, favorites=None, only=False, hide=(), check=False):
    return ensure_sidebar(
        mac, TOOL, favorites, only, list(hide), check, HOME, FOLDERS.__contains__
    )


class FinderSidebar(unittest.TestCase):
    def test_listed_folders_come_first_and_the_rest_stay_after(self):
        mac = FakeSidebar()
        result = ensure(mac, ["/Applications", "~", "~/Desktop"])
        self.assertTrue(result["changed"])
        self.assertEqual(
            mac.lists["FavoriteItems"],
            [
                "file:///Applications/",
                url(HOME),
                url(HOME + "/Desktop"),
                RECENTS,
                AIRDROP,
                url(HOME + "/Documents"),
                url(HOME + "/Downloads"),
            ],
        )
        self.assertEqual(
            mac.changes, [["first", "/Applications", HOME, HOME + "/Desktop"]]
        )

    def test_a_second_run_changes_nothing(self):
        mac = FakeSidebar()
        ensure(mac, ["/Applications", "~"], only=True, hide=["recents", "airdrop"])
        mac.changes.clear()
        result = ensure(
            mac, ["/Applications", "~"], only=True, hide=["recents", "airdrop"]
        )
        self.assertFalse(result["changed"])
        self.assertNotIn("diff", result)
        self.assertEqual(mac.changes, [])

    def test_favorites_only_removes_other_folders_and_keeps_special_items(self):
        mac = FakeSidebar()
        ensure(mac, ["~/Desktop", OTHER], only=True)
        self.assertEqual(
            mac.lists["FavoriteItems"],
            [
                url(HOME + "/Desktop"),
                url(OTHER),
                RECENTS,
                AIRDROP,
            ],
        )

    def test_listed_favorites_only_and_every_hide_name(self):
        mac = FakeSidebar()
        ensure(
            mac,
            ["/Applications", "~", OTHER, "~/Desktop"],
            only=True,
            hide=[
                "recents",
                "shared",
                "airdrop",
                "trash",
                "icloud",
                "home_in_locations",
            ],
        )
        self.assertEqual(
            mac.lists,
            {
                "FavoriteItems": [
                    "file:///Applications/",
                    url(HOME),
                    url(OTHER),
                    url(HOME + "/Desktop"),
                ],
                "FavoriteVolumes": ["file:///", "nwnode://domain-Network"],
                "TopSidebarSection": [],
            },
        )

    def test_hide_removes_only_the_named_items(self):
        mac = FakeSidebar()
        ensure(mac, hide=["airdrop", "home_in_locations"])
        self.assertEqual(
            mac.lists["FavoriteItems"], [RECENTS] + START["FavoriteItems"][2:]
        )
        self.assertEqual(
            mac.lists["FavoriteVolumes"], ["file:///", "nwnode://domain-Network"]
        )
        self.assertEqual(
            mac.changes,
            [
                ["remove", "FavoriteItems", AIRDROP],
                ["remove", "FavoriteVolumes", url(HOME)],
            ],
        )

    def test_check_mode_reports_the_change_and_writes_nothing(self):
        mac = FakeSidebar()
        result = ensure(mac, ["~/My Work"], hide=["recents"], check=True)
        self.assertTrue(result["changed"])
        self.assertEqual(mac.changes, [])
        self.assertIn(RECENTS, result["diff"]["before"])
        self.assertNotIn(RECENTS, result["diff"]["after"])
        self.assertIn(
            "FavoriteItems: " + url(HOME + "/My Work") + ", ", result["diff"]["after"]
        )

    def test_root_is_refused_before_any_read(self):
        mac = FakeSidebar()
        with self.assertRaisesRegex(SidebarError, "Finder refuses / as a favorite"):
            ensure(mac, ["/Applications", "/"])
        self.assertEqual(mac.changes, [])

    def test_a_missing_folder_is_refused(self):
        mac = FakeSidebar()
        with self.assertRaisesRegex(SidebarError, "~/Pictures: no folder at"):
            ensure(mac, ["~/Pictures"])
        self.assertEqual(mac.changes, [])

    def test_a_folder_is_moved_by_insert_and_never_removed_to_reorder(self):
        mac = FakeSidebar()
        ensure(mac, ["~/Desktop", "/Applications"], only=True, hide=["airdrop"])
        self.assertEqual(mac.changes[0], ["first", HOME + "/Desktop", "/Applications"])
        removed = [u for change in mac.changes[1:] for u in change[2:]]
        self.assertNotIn(url(HOME + "/Desktop"), removed)
        self.assertNotIn("file:///Applications/", removed)
        self.assertEqual(
            mac.lists["FavoriteItems"],
            [url(HOME + "/Desktop"), "file:///Applications/", RECENTS],
        )

    def test_a_refused_insert_is_named_and_nothing_else_changes(self):
        mac = FakeSidebar(refuse={HOME + "/Downloads"})
        with self.assertRaises(SidebarError) as raised:
            ensure(mac, ["/Applications", "~/Downloads"], only=True, hide=["recents"])
        self.assertEqual(
            str(raised.exception),
            "macOS refused to add ~/Downloads to the sidebar; Desktop, Documents and "
            "Downloads need Files and Folders access for the program that runs mac-setup",
        )
        self.assertEqual(mac.changes, [["first", "/Applications", HOME + "/Downloads"]])
        self.assertEqual(mac.lists["FavoriteVolumes"], START["FavoriteVolumes"])
        self.assertIn(RECENTS, mac.lists["FavoriteItems"])
        self.assertIn(url(HOME + "/Documents"), mac.lists["FavoriteItems"])

    def test_a_read_back_that_differs_fails(self):
        class Stubborn(FakeSidebar):
            def run(self, argv):
                if argv[1] == "remove":
                    self.changes.append(argv[1:])
                    return 0, "", ""
                return super().run(argv)

        with self.assertRaisesRegex(SidebarError, "after the change"):
            ensure(Stubborn(), hide=["airdrop"])

    def test_empty_lists_are_refused_as_no_access(self):
        mac = FakeSidebar(lists={name: [] for name in START})
        with self.assertRaisesRegex(
            SidebarError,
            "^the Finder sidebar lists read back empty, so this program has no access "
            "to them; run mac-setup from Terminal at the Mac$",
        ):
            ensure(mac, ["/Applications"], hide=["recents"])
        self.assertEqual(mac.changes, [])

    def test_a_failed_read_fails(self):
        class Broken(FakeSidebar):
            def run(self, argv):
                return 1, "", "cannot open the list FavoriteItems\n"

        with self.assertRaisesRegex(SidebarError, "read exited 1: cannot open"):
            ensure(Broken())


if __name__ == "__main__":
    unittest.main()
