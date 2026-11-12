import datetime
import hashlib
import io
import json
import plistlib
import re
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/macos_apps/module_utils"))

import verified_download as vd

KEY = "not-a-real-virustotal-key"
TODAY = datetime.datetime(2026, 11, 12, 12, tzinfo=datetime.timezone.utc).timestamp()
ARCHIVE = b"fake archive bytes"
CLEAN = {"malicious": 0, "suspicious": 0, "undetected": 60, "harmless": 0}


def entry(**changes):
    base = {
        "url": "https://dl.example.com/app-1.2.dmg",
        "host": "dl.example.com",
        "sha256": hashlib.sha256(ARCHIVE).hexdigest(),
        "published": "2026-10-01",
        "team_id": "ABCDE12345",
        "bundle_id": "com.example.app",
        "container": "dmg",
        "app": "Example.app",
        "version": "1.2",
    }
    base.update(changes)
    return base


def app(**changes):
    return vd.parse_app("example", entry(**changes))


class Response(io.BytesIO):
    def __init__(self, body, url):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url


class FakeWeb:
    def __init__(self, files=None, stats=CLEAN, vt_status=200, redirect=None):
        self.files = {"https://dl.example.com/app-1.2.dmg": ARCHIVE, **(files or {})}
        self.stats = stats
        self.vt_status = vt_status
        self.redirect = redirect or {}
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        url = request.full_url
        if url.startswith(vd.VIRUSTOTAL):
            if self.vt_status != 200:
                raise urllib.error.HTTPError(
                    url, self.vt_status, f"echo {KEY}", Message(), None
                )
            body = {"data": {"attributes": {"last_analysis_stats": self.stats}}}
            return Response(json.dumps(body).encode(), url)
        final = self.redirect.get(url, url)
        return Response(self.files[final], final)

    def downloads(self):
        return [r for r in self.requests if not r.full_url.startswith(vd.VIRUSTOTAL)]

    def lookups(self):
        return [r for r in self.requests if r.full_url.startswith(vd.VIRUSTOTAL)]


class FakeMac:
    def __init__(
        self, team="ABCDE12345", bundle_id="com.example.app", version="1.2", fail=()
    ):
        self.team = team
        self.bundle_id = bundle_id
        self.version = version
        self.fail = set(fail)
        self.argvs = []
        self.mounted = set()
        self.cleaned = []

    def make_app(self, folder):
        contents = Path(folder) / "Example.app/Contents"
        contents.mkdir(parents=True)
        info = {
            "CFBundleIdentifier": self.bundle_id,
            "CFBundleShortVersionString": self.version,
        }
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))

    def run(self, argv):
        self.argvs.append(list(argv))
        tool = Path(argv[0]).name
        if argv[:2] == [vd.HDIUTIL, "attach"]:
            if "attach" in self.fail:
                return 1, "", "hdiutil: attach failed\n"
            self.make_app(argv[-2])
            self.mounted.add(argv[-2])
            return 0, "", ""
        if argv[:2] == [vd.HDIUTIL, "detach"]:
            self.mounted.discard(argv[-1])
            return 0, "", ""
        if argv[:3] == [vd.DITTO, "-x", "-k"]:
            self.make_app(argv[-1])
            return 0, "", ""
        if argv[0] == vd.DITTO:
            if "copy" in self.fail:
                return 1, "", "ditto: No space left on device\n"
            shutil.copytree(argv[-2], argv[-1])
            return 0, "", ""
        if tool == "ps":
            return 0, getattr(self, "processes", ""), ""
        if tool == "xattr":
            self.cleaned.append(argv[-1])
            return 0, "", ""
        requirement = next((a for a in argv if a.startswith("-R=")), None)
        if argv[:2] == [vd.CODESIGN, "--verify"] and requirement:
            wanted = re.search(r'subject\.OU\] = "([^"]+)"', requirement).group(1)
            if wanted != self.team:
                return (
                    3,
                    "",
                    "test-requirement: code failed to satisfy specified code requirement(s)\n",
                )
            return 0, "", ""
        if argv[:2] == [vd.CODESIGN, "--verify"]:
            return (
                (1, "", "code object is not signed\n")
                if "codesign" in self.fail
                else (0, "", "")
            )
        if tool == "spctl":
            return (3, "", "rejected\n") if "spctl" in self.fail else (0, "", "")
        raise AssertionError(f"unexpected command {argv}")


class FakeClock:
    def __init__(self, now=TODAY):
        self.now = now
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def setup(root, mac, web, clock=None, key=KEY):
    return vd.Setup(
        downloads=root / "downloads",
        runner=mac,
        opener=web,
        clock=clock or FakeClock(),
        key=key,
    )


class Case(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.downloads = self.root / "downloads"

    def verify(self, item=None, mac=None, web=None, clock=None, key=KEY):
        self.mac = mac or FakeMac()
        self.web = web or FakeWeb()
        with vd.verified_app(
            item or app(), setup(self.root, self.mac, self.web, clock, key)
        ) as verified:
            self.assertTrue((verified.path / "Contents/Info.plist").is_file())
            return verified

    def refused(self, check, **kwargs):
        with self.assertRaises(vd.Refused) as caught:
            self.verify(**kwargs)
        self.assertEqual(caught.exception.check, check)
        self.assertIn("example", str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(self.mac.mounted, set(), "the DMG stays attached")
        return caught.exception


class Verifies(Case):
    def test_good_app_passes_every_check(self):
        verified = self.verify()
        self.assertEqual(verified.version, "1.2")
        self.assertEqual(verified.sha256, hashlib.sha256(ARCHIVE).hexdigest())
        self.assertEqual(self.mac.mounted, set())
        lookup = self.web.lookups()[0]
        self.assertEqual(lookup.full_url, vd.VIRUSTOTAL + verified.sha256)
        self.assertEqual(lookup.get_header("X-apikey"), KEY)

    def test_newer_version_passes(self):
        self.assertEqual(
            self.verify(mac=FakeMac(version="Build 1.10")).version, "Build 1.10"
        )

    def test_zip_unpacks_with_ditto(self):
        self.verify(item=app(container="zip"))
        self.assertIn([vd.DITTO, "-x", "-k"], [a[:3] for a in self.mac.argvs])

    def test_second_run_downloads_nothing_and_asks_virustotal_nothing(self):
        self.verify()
        web = FakeWeb()
        self.verify(web=web)
        self.assertEqual(web.requests, [])

    def test_unpinned_item_downloads_and_caches_by_hash(self):
        item = app(sha256=None, published=None, sha256_reason="latest only")
        verified = self.verify(item=item)
        self.assertTrue((self.downloads / f"{verified.sha256}.dmg").is_file())
        self.assertEqual([p.name for p in self.downloads.glob("*.part")], [])

    def test_cached_file_with_other_bytes_is_downloaded_again(self):
        self.verify()
        cached = self.downloads / f"{app().sha256}.dmg"
        cached.write_bytes(b"tampered")
        web = FakeWeb()
        self.verify(web=web)
        self.assertEqual(len(web.downloads()), 1)
        self.assertEqual(cached.read_bytes(), ARCHIVE)

    def test_same_host_redirect_is_followed(self):
        final = "https://dl.example.com/mirror/app-1.2.dmg"
        web = FakeWeb(files={final: ARCHIVE}, redirect={app().url: final})
        self.verify(web=web)


class RefusesTheDownload(Case):
    def test_http_url(self):
        self.refused("https", item=app(url="http://dl.example.com/app-1.2.dmg"))
        self.assertEqual(self.web.requests, [])

    def test_other_host(self):
        self.refused("host", item=app(url="https://evil.example.net/app-1.2.dmg"))
        self.assertEqual(self.web.requests, [])

    def test_redirect_to_other_host(self):
        final = "https://evil.example.net/app-1.2.dmg"
        self.refused(
            "host", web=FakeWeb(files={final: ARCHIVE}, redirect={app().url: final})
        )
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_redirect_to_http(self):
        final = "http://dl.example.com/app-1.2.dmg"
        self.refused(
            "https", web=FakeWeb(files={final: ARCHIVE}, redirect={app().url: final})
        )

    def test_hash_mismatch(self):
        self.refused("sha256", item=app(sha256="0" * 64))
        self.assertEqual(list(self.downloads.iterdir()), [])
        self.assertEqual(self.web.lookups(), [])

    def test_too_young(self):
        self.refused("age", item=app(published="2026-11-01"))
        self.assertEqual(self.web.requests, [])


class RefusesOnVirusTotal(Case):
    def test_malicious(self):
        error = self.refused("virustotal", web=FakeWeb(stats={**CLEAN, "malicious": 1}))
        self.assertIn("1 engines report malicious", str(error))

    def test_suspicious(self):
        self.refused("virustotal", web=FakeWeb(stats={**CLEAN, "suspicious": 2}))

    def test_unknown_file(self):
        error = self.refused("virustotal", web=FakeWeb(vt_status=404))
        self.assertIn("does not know", str(error))

    def test_no_engine_scanned(self):
        self.refused("virustotal", web=FakeWeb(stats={"malicious": 0, "undetected": 0}))

    def test_missing_key(self):
        for key in ("", None):
            with self.subTest(key=key):
                error = self.refused("virustotal", key=key)
                self.assertIn(
                    "set virustotal_api_key in your private profile's vault file; see README",
                    str(error),
                )
                self.assertEqual(self.web.lookups(), [])

    def test_stored_verdict_needs_no_key(self):
        self.verify()
        self.verify(web=FakeWeb(), key="")

    def test_api_error(self):
        self.refused("virustotal", web=FakeWeb(vt_status=500))

    def test_rate_limit_stops_the_app(self):
        error = self.refused("virustotal", web=FakeWeb(vt_status=429))
        self.assertIn("429", str(error))

    def test_errors_are_not_cached(self):
        self.refused("virustotal", web=FakeWeb(vt_status=429))
        self.verify()
        self.assertEqual(len(self.web.lookups()), 1)

    def test_cached_bad_verdict_keeps_refusing_without_a_lookup(self):
        self.refused("virustotal", web=FakeWeb(stats={**CLEAN, "malicious": 1}))
        web = FakeWeb()
        self.refused("virustotal", web=web)
        self.assertEqual(web.requests, [])

    def test_verdict_is_stored_next_to_the_download(self):
        verified = self.verify()
        stored = json.loads(
            (self.downloads / f"{verified.sha256}.virustotal.json").read_text()
        )
        self.assertEqual(stored["verdict"], "clean")
        self.assertEqual(stored["engines"], CLEAN)
        self.assertEqual(stored["checked"], "2026-11-12T12:00:00+00:00")

    def test_fifth_lookup_in_a_minute_waits(self):
        clock = FakeClock()
        for n in range(5):
            item = app(sha256=None, published=None, sha256_reason="latest only")
            web = FakeWeb(files={item.url: ARCHIVE + bytes([n])})
            self.verify(item=item, web=web, clock=clock)
        self.assertEqual(clock.slept, [60])


class RefusesTheApp(Case):
    def test_wrong_team_id(self):
        self.refused("team_id", mac=FakeMac(team="ZZZZZ99999"))

    def test_the_team_is_checked_by_code_requirement_not_display_text(self):
        self.verify()
        requirement = [
            a for a in self.mac.argvs if a[0] == vd.CODESIGN and "-R=" in a[-2]
        ]
        self.assertEqual(len(requirement), 1)
        self.assertIn('certificate leaf[subject.OU] = "ABCDE12345"', requirement[0][-2])
        self.assertFalse(any(a[:2] == [vd.CODESIGN, "-dv"] for a in self.mac.argvs))

    def test_wrong_bundle_id(self):
        self.refused("bundle_id", mac=FakeMac(bundle_id="com.example.other"))

    def test_older_version(self):
        self.refused("version", mac=FakeMac(version="1.1.9"))

    def test_codesign_failure(self):
        self.refused("codesign", mac=FakeMac(fail={"codesign"}))

    def test_spctl_failure(self):
        self.refused("spctl", mac=FakeMac(fail={"spctl"}))

    def test_copy_failure_still_detaches(self):
        self.refused("unpack", mac=FakeMac(fail={"copy"}))
        self.assertEqual(
            sum(a[:2] == [vd.HDIUTIL, "detach"] for a in self.mac.argvs), 1
        )

    def test_checks_run_on_the_copy_with_finder_detritus_removed(self):
        verified = self.verify()
        self.assertNotIn("mount", verified.path.parts)
        self.assertEqual(self.mac.cleaned, [str(verified.path)] * 2)
        codesign = next(a for a in self.mac.argvs if a[:2] == [vd.CODESIGN, "--verify"])
        self.assertEqual(codesign[-1], str(verified.path))

    def test_attach_failure(self):
        self.refused("unpack", mac=FakeMac(fail={"attach"}))

    def test_missing_bundle(self):
        self.refused("unpack", item=app(app="Other.app"))


class ParsesTheCatalog(unittest.TestCase):
    def test_every_catalog_entry_parses(self):
        import yaml

        catalog = yaml.safe_load((ROOT / "apps/macos.yml").read_text())
        self.assertEqual(
            sorted(catalog), ["google_chrome", "little_snitch", "sublime_text"]
        )
        for item, value in catalog.items():
            vd.parse_app(item, value)

    def test_bad_entries(self):
        for changes in (
            {"sha256": None},
            {"sha256": "ABC"},
            {"sha256_reason": "why"},
            {"published": None},
            {"container": "pkg"},
            {"team_id": "short"},
            {"app": "../Example.app"},
            {"version": "latest"},
            {"colour": "red"},
        ):
            with self.subTest(changes), self.assertRaises(vd.Refused) as caught:
                app(**changes)
            self.assertEqual(caught.exception.check, "catalog")


class KeepsTheKeySecret(Case):
    def test_key_never_in_argv_errors_repr_or_stored_files(self):
        verified = self.verify()
        self.assertNotIn(KEY, repr(setup(self.root, self.mac, self.web)))
        self.assertNotIn(KEY, repr(verified))
        self.assertEqual(self.web.lookups()[0].get_header("X-apikey"), KEY)
        self.assertFalse(any(KEY in " ".join(argv) for argv in self.mac.argvs))
        self.assertFalse(any(KEY in r.full_url for r in self.web.requests))
        stored = list(self.downloads.iterdir())
        self.assertEqual(len(stored), 3)
        for path in stored:
            self.assertNotIn(KEY.encode(), path.read_bytes())


class Redirects(unittest.TestCase):
    def opener(self, location):
        class Canned(urllib.request.BaseHandler):
            handler_order = 100

            def https_open(self, request):
                if request.full_url == "https://dl.example.com/a":
                    headers = Message()
                    headers["Location"] = location
                    response = urllib.request.addinfourl(
                        io.BytesIO(b""), headers, request.full_url, 302
                    )
                    response.msg = "Found"
                    return response
                response = urllib.request.addinfourl(
                    io.BytesIO(b"ok"), Message(), request.full_url, 200
                )
                response.msg = "OK"
                return response

        return urllib.request.build_opener(Canned, vd.SameHostRedirects)

    def test_redirect_handler_refuses_another_host(self):
        with self.assertRaises(vd.Refused) as caught:
            with vd.open_https(
                "example",
                "https://dl.example.com/a",
                "dl.example.com",
                self.opener("https://evil.example.net/b"),
            ):
                pass
        self.assertEqual(caught.exception.check, "host")

    def test_redirect_handler_follows_the_same_host(self):
        with vd.open_https(
            "example",
            "https://dl.example.com/a",
            "dl.example.com",
            self.opener("https://dl.example.com/b"),
        ) as response:
            self.assertEqual(response.read(), b"ok")


if __name__ == "__main__":
    unittest.main()
