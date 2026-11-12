import importlib.machinery
import importlib.util
import io
import os
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/app-catalog"
CATALOG = yaml.safe_load((ROOT / "apps/macos.yml").read_text())
NO_NETWORK = {
    name: "http://127.0.0.1:9"
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
}
HAND_WRITTEN_PAGES = {
    "sublime_text": (
        '{"latest_version": 4300, "update_url": "https://www.sublimetext.com/download"}',
        "https://download.sublimetext.com/sublime_text_build_4300_mac.zip",
    ),
    "little_snitch": (
        '<a class="button" href="https://www.obdev.at/downloads/littlesnitch/LittleSnitch-6.6.dmg">'
        "Download</a>\n"
        '<a href="https://www.obdev.at/downloads/littlesnitch/legacy/LittleSnitch-5.7.6.dmg">',
        "https://www.obdev.at/downloads/littlesnitch/LittleSnitch-6.6.dmg",
    ),
    "proton_pass": (
        '{"Releases": [\n  {"CategoryName": "EarlyAccess",\n   "Version": "1.45.0"},\n'
        '  {"CategoryName": "Stable",\n   "Version": "1.44.2"}\n]}',
        "https://proton.me/download/pass/macos/ProtonPass_1.44.2.dmg",
    ),
    "ente_auth": (
        '[{"tag_name": "photos-v1.8.0"}, {"tag_name": "auth-v4.4.30"},'
        ' {"tag_name": "auth-v4.4.26"}]',
        "https://github.com/ente/ente/releases/download/auth-v4.4.30/ente-auth-v4.4.30.dmg",
    ),
    "utm": (
        '{"tag_name": "v5.0.9", "name": "UTM v5.0.9"}',
        "https://github.com/utmapp/UTM/releases/download/v5.0.9/UTM.dmg",
    ),
    "firefox": (
        '{"LATEST_FIREFOX_DEVEL_VERSION": "159.0b4", "LATEST_FIREFOX_VERSION": "158.0.2"}',
        "https://archive.mozilla.org/pub/firefox/releases/158.0.2/mac/en-US/Firefox%20158.0.2.dmg",
    ),
    "micro_snitch": (
        '<a href="https://www.obdev.at/downloads/MicroSnitch/MicroSnitch-1.6.2.dmg">',
        "https://www.obdev.at/downloads/MicroSnitch/MicroSnitch-1.6.2.dmg",
    ),
}


def load_script():
    loader = importlib.machinery.SourceFileLoader("app_catalog_under_test", str(SCRIPT))
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(loader.name, loader)
    )
    loader.exec_module(module)
    return module


class Response(io.BytesIO):
    def __init__(self, body, url):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url


class FakeWeb:
    def __init__(self, pages, redirect=None):
        self.pages = pages
        self.redirect = redirect or {}
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request.full_url)
        final = self.redirect.get(request.full_url, request.full_url)
        return Response(self.pages[final].encode(), final)


class LatestDownload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = load_script()

    def latest(self, item, web):
        return self.script.latest_url(item, CATALOG[item], web)

    def refused(self, item, web):
        with self.assertRaises(self.script.vd.Refused) as caught:
            self.latest(item, web)
        return caught.exception

    def test_each_latest_page_gives_the_current_download(self):
        self.assertEqual(
            set(HAND_WRITTEN_PAGES),
            {item for item, entry in CATALOG.items() if entry.get("latest")},
        )
        for item, (text, expected) in HAND_WRITTEN_PAGES.items():
            with self.subTest(item):
                page = CATALOG[item]["latest"]["page"]
                web = FakeWeb({page: text})
                self.assertEqual(self.latest(item, web), expected)
                self.assertEqual(web.requests, [page])

    def test_entry_without_a_latest_page_keeps_its_url(self):
        web = FakeWeb({})
        self.assertEqual(
            self.latest("google_chrome", web), CATALOG["google_chrome"]["url"]
        )
        self.assertEqual(web.requests, [])

    def test_page_without_a_match_is_refused(self):
        latest = CATALOG["sublime_text"]["latest"]
        web = FakeWeb({latest["page"]: '{"update_url": "https://example.com/"}'})
        error = self.refused("sublime_text", web)
        self.assertEqual(error.check, "latest")
        self.assertIn(latest["page"], str(error))
        self.assertIn(latest["pattern"], str(error))

    def test_page_that_redirects_to_another_host_is_refused(self):
        page = CATALOG["utm"]["latest"]["page"]
        moved = "https://other.example.com/releases/latest"
        text = HAND_WRITTEN_PAGES["utm"][0]
        error = self.refused("utm", FakeWeb({moved: text}, redirect={page: moved}))
        self.assertEqual(error.check, "host")


class Command(unittest.TestCase):
    def run_script(self, *args):
        return subprocess.run(
            [str(SCRIPT), *args],
            env={**os.environ, **NO_NETWORK},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_missing_id_prints_usage_and_exits_2(self):
        for args in ([], ["firefox", "utm"], ["-x"]):
            with self.subTest(args=args):
                out = self.run_script(*args)
                self.assertEqual(out.returncode, 2)
                self.assertEqual(out.stdout, "")
                self.assertTrue(
                    out.stderr.startswith("usage: scripts/app-catalog ID\n")
                )

    def test_unknown_id_is_named_in_one_line(self):
        out = self.run_script("zz_not_an_app")
        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout, "")
        lines = out.stderr.splitlines()
        self.assertEqual(len(lines), 1, out.stderr)
        self.assertTrue(
            lines[0].startswith("app-catalog: zz_not_an_app is not in apps/macos.yml")
        )
        for item in CATALOG:
            self.assertIn(item, lines[0])


if __name__ == "__main__":
    unittest.main()
