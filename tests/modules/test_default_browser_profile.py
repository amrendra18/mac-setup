import unittest

import test_profile as profile


class DefaultBrowserProfileTests(unittest.TestCase):
    def test_default_browser_is_an_account_choice_with_a_separate_work_item(self):
        selection = {"default_settings": {"browser.default": "firefox"}}
        self.assertEqual(profile.profile_problems(selection, profile.FILES), [])
        work = profile.profile_work(selection, profile.FILES, "runner", profile.TABLE)
        self.assertEqual(work["own"]["default_browser"], "firefox")
        self.assertEqual(work["own"]["writes"], [])
        self.assertEqual(work["own"]["commands"], [])
        self.assertEqual(work["firefox"], {})
        self.assertEqual(work["by_hand"], [])

    def test_bookmarks_and_default_browser_share_accounts_with_firefox_policies(self):
        source = "files/example-bookmarks.vault"
        policies = {
            "firefox.owner_label": "HOME",
            "firefox.pref.sidebar_vertical_tabs": True,
            "firefox.addons.install": ["FirefoxColor@mozilla.com"],
        }
        selection = {
            "default_settings": {
                "browser.default": "firefox",
                "bookmarks.import_file": source,
            },
            "machine_settings": policies,
            "accounts": [
                profile.account(
                    "setuptest1",
                    settings={"bookmarks.import_file": "files/other-bookmarks.vault"},
                )
            ],
        }
        self.assertEqual(profile.profile_problems(selection, profile.FILES), [])
        work = profile.profile_work(selection, profile.FILES, "runner", profile.TABLE)
        self.assertEqual(work["own"]["default_browser"], "firefox")
        self.assertEqual(work["own"]["bookmarks_import"], {"source": source})
        self.assertEqual(work["others"][0]["default_browser"], "firefox")
        self.assertEqual(
            work["others"][0]["bookmarks_import"],
            {"source": "files/other-bookmarks.vault"},
        )
        self.assertEqual(work["firefox"], policies)
        self.assertEqual(work["counts"]["own_session"], 1)

    def test_other_account_choice_waits_for_that_accounts_session(self):
        selection = {
            "accounts": [
                profile.account("setuptest1", settings={"browser.default": "firefox"})
            ]
        }
        work = profile.profile_work(selection, profile.FILES, "runner", profile.TABLE)
        self.assertEqual(work["own"]["default_browser"], "")
        self.assertEqual(work["others"][0]["default_browser"], "firefox")
        self.assertEqual(work["counts"]["own_session"], 1)

    def test_invalid_browser_and_machine_scope_stop_in_profile_validation(self):
        for selection in (
            {"default_settings": {"browser.default": "unknown-test-browser"}},
            {"default_settings": {"browser.default": "unset"}},
            {"machine_settings": {"browser.default": "firefox"}},
        ):
            with self.subTest(selection=selection):
                self.assertTrue(profile.profile_problems(selection, profile.FILES))

    def test_request_result_notes_reach_the_run_summary(self):
        result = profile.profile_summary(
            profile.counts(), [], [], [], default_browser_notes=["artificial note"]
        )
        self.assertIn("artificial note", result["notes"])
