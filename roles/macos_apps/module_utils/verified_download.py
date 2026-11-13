import datetime
import hashlib
import http.client
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

HDIUTIL = "/usr/bin/hdiutil"
DITTO = "/usr/bin/ditto"
CODESIGN = "/usr/bin/codesign"
SPCTL = "/usr/sbin/spctl"
XATTR = "/usr/bin/xattr"
FINDER_DETRITUS = ("com.apple.FinderInfo", "com.apple.ResourceFork")
KEY_HELP = "set virustotal_api_key in your private profile's vault file; see README"
VIRUSTOTAL_HOST = "www.virustotal.com"
VIRUSTOTAL = f"https://{VIRUSTOTAL_HOST}/api/v3/files/"
LOOKUPS_PER_MINUTE = 4
MIN_AGE_DAYS = 21
CONTAINERS = ("dmg", "zip")
CATALOG_KEYS = {
    "name",
    "url",
    "host",
    "redirect_host",
    "sha256",
    "sha256_reason",
    "team_id",
    "bundle_id",
    "container",
    "app",
    "version",
    "published",
    "needs",
    "note",
    "latest",
}
CHUNK = 1 << 20
TIMEOUT = 60


class Refused(Exception):
    def __init__(self, check, item, detail, status=None):
        super().__init__(f"{item}: the {check} check failed: {detail}")
        self.check = check
        self.item = item
        self.detail = detail
        self.status = status


@dataclass(frozen=True)
class App:
    id: str
    url: str
    host: str
    sha256: Optional[str]
    published: Optional[datetime.date]
    team_id: str
    bundle_id: str
    container: str
    app: str
    version: str
    redirect_host: Optional[str] = None

    @property
    def hosts(self):
        return (self.host, self.redirect_host) if self.redirect_host else (self.host,)


@dataclass(frozen=True)
class Verified:
    path: Path
    version: str
    sha256: str


class Clock:
    def time(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(seconds)


@dataclass(frozen=True)
class Setup:
    downloads: Path
    runner: object
    opener: object
    clock: object
    key: str = field(repr=False)


class Runner:
    def run(self, argv):
        done = subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True, text=True
        )
        return done.returncode, done.stdout, done.stderr


def parse_app(item, entry):
    def fail(detail):
        raise Refused("catalog", item, detail)

    if not isinstance(entry, dict):
        fail("the entry is not a mapping")
    unknown = sorted(set(entry) - CATALOG_KEYS)
    if unknown:
        fail(f"unknown keys {unknown}")
    fields = {}
    for key in ("url", "host", "team_id", "bundle_id", "container", "app", "version"):
        value = entry.get(key)
        if not isinstance(value, str) or not value:
            fail(f"{key} must be a non-empty string")
        fields[key] = value
    redirect_host = entry.get("redirect_host")
    if redirect_host is not None and not (
        isinstance(redirect_host, str)
        and redirect_host
        and redirect_host != fields["host"]
    ):
        fail("redirect_host must be a host name other than host")
    sha256, published = entry.get("sha256"), entry.get("published")
    if sha256 is None:
        if not entry.get("sha256_reason"):
            fail("sha256 is null, so sha256_reason must say why")
        if published is not None:
            fail("published is only for an entry with a pinned sha256")
    else:
        if not (isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256)):
            fail("sha256 must be 64 lowercase hex digits or null")
        if "sha256_reason" in entry:
            fail("sha256_reason is only for an entry whose sha256 is null")
        try:
            published = datetime.date.fromisoformat(str(published))
        except ValueError:
            fail("published must be the vendor's release date as YYYY-MM-DD")
    if fields["container"] not in CONTAINERS:
        fail(f"container must be one of {list(CONTAINERS)}")
    if not re.fullmatch(r"[A-Z0-9]{10}", fields["team_id"]):
        fail("team_id must be 10 capital letters or digits")
    if "/" in fields["app"] or not fields["app"].endswith(".app"):
        fail("app must be a bundle name ending in .app")
    if not re.search(r"\d", fields["version"]):
        fail("version must hold a version number")
    return App(
        id=item,
        sha256=sha256,
        published=published,
        redirect_host=redirect_host,
        **fields,
    )


def version_key(item, version):
    numbers = re.findall(r"\d+", version)
    if not numbers:
        raise Refused("version", item, f"{version!r} holds no version number")
    return tuple(int(n) for n in numbers)


def at_least(item, version, minimum):
    return version_key(item, version) >= version_key(item, minimum)


def check_age(app, clock):
    if app.published is None:
        return
    today = datetime.datetime.fromtimestamp(clock.time(), datetime.timezone.utc).date()
    age = (today - app.published).days
    if age < MIN_AGE_DAYS:
        raise Refused(
            "age",
            app.id,
            f"published {app.published}, {age} days ago; builds must be at least {MIN_AGE_DAYS} days old",
        )


class _OffHost(Exception):
    pass


class AllowedHostRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = urlsplit(newurl)
        if new.scheme != "https" or new.hostname not in req.allowed_hosts:
            raise _OffHost(newurl)
        follow = super().redirect_request(req, fp, code, msg, headers, newurl)
        follow.allowed_hosts = req.allowed_hosts
        return follow


def default_opener():
    return urllib.request.build_opener(AllowedHostRedirects)


def _named(hosts):
    return " or ".join(hosts)


def check_url(item, url, hosts):
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise Refused("https", item, f"{url} is not an HTTPS URL")
    if parts.hostname not in hosts:
        raise Refused("host", item, f"{url} is not on the allowed host {_named(hosts)}")


@contextmanager
def open_https(item, url, hosts, opener, headers=None):
    check_url(item, url, hosts[:1])
    request = urllib.request.Request(url, headers=headers or {})
    request.allowed_hosts = hosts
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            check_url(item, response.geturl(), hosts)
            yield response
    except _OffHost as error:
        raise Refused(
            "host",
            item,
            f"{url} redirected to {error}, which is not on https://{_named(hosts)}",
        ) from None
    except urllib.error.HTTPError as error:
        raise Refused(
            "download", item, f"{url}: HTTP {error.code}", error.code
        ) from None
    except (OSError, http.client.HTTPException) as error:
        raise Refused("download", item, f"{url}: {error}") from None


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, data):
    part = path.with_name(f".{path.name}.part")
    part.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(part, path)


def download(app, downloads, opener):
    if app.sha256:
        cached = downloads / f"{app.sha256}.{app.container}"
        if cached.exists() and file_sha256(cached) == app.sha256:
            return cached, app.sha256
    downloads.mkdir(parents=True, exist_ok=True)
    part = tempfile.NamedTemporaryFile(
        dir=downloads, prefix=f".{app.id}.", suffix=".part", delete=False
    )
    try:
        digest = hashlib.sha256()
        with part, open_https(app.id, app.url, app.hosts, opener) as response:
            for chunk in iter(lambda: response.read(CHUNK), b""):
                digest.update(chunk)
                part.write(chunk)
        sha256 = digest.hexdigest()
        if app.sha256 and sha256 != app.sha256:
            raise Refused(
                "sha256", app.id, f"{app.url} has SHA-256 {sha256}, not {app.sha256}"
            )
        target = downloads / f"{sha256}.{app.container}"
        os.chmod(part.name, 0o644)
        os.replace(part.name, target)
        return target, sha256
    finally:
        if os.path.exists(part.name):
            os.unlink(part.name)


def wait_for_lookup_slot(downloads, clock):
    log = downloads / "virustotal-lookups.json"
    try:
        recent = [float(t) for t in json.loads(log.read_text())]
    except (OSError, ValueError, TypeError):
        recent = []
    recent = sorted(t for t in recent if t > clock.time() - 60)
    if len(recent) >= LOOKUPS_PER_MINUTE:
        clock.sleep(recent[-LOOKUPS_PER_MINUTE] + 60 - clock.time())
    write_json(log, (recent + [clock.time()])[-LOOKUPS_PER_MINUTE:])


def scan_verdict(engines):
    categories = {
        "malicious",
        "suspicious",
        "harmless",
        "undetected",
        "timeout",
        "confirmed-timeout",
        "failure",
        "type-unsupported",
    }
    if (
        not isinstance(engines, dict)
        or not engines
        or any(
            kind not in categories or type(count) is not int or count < 0
            for kind, count in engines.items()
        )
    ):
        raise ValueError("invalid engine statistics")
    if engines.get("malicious", 0) or engines.get("suspicious", 0):
        return "flagged"
    if engines.get("harmless", 0) + engines.get("undetected", 0) == 0:
        raise ValueError("no engine completed a clean scan")
    return "clean"


def ask_virustotal(item, sha256, setup):
    key = setup.key
    if not key:
        raise Refused("virustotal", item, KEY_HELP)
    wait_for_lookup_slot(setup.downloads, setup.clock)
    try:
        with open_https(
            item,
            VIRUSTOTAL + sha256,
            (VIRUSTOTAL_HOST,),
            setup.opener,
            {"x-apikey": key},
        ) as response:
            stats = json.load(response)["data"]["attributes"]["last_analysis_stats"]
        verdict = scan_verdict(stats)
    except Refused as error:
        detail = {
            404: f"VirusTotal does not know the file with SHA-256 {sha256}",
            429: "VirusTotal's request limit was reached (HTTP 429)",
        }.get(error.status, error.detail.replace(key, "<hidden>"))
        raise Refused("virustotal", item, detail) from None
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        detail = f"unexpected API response ({type(error).__name__})"
        raise Refused("virustotal", item, detail) from None
    return {
        "sha256": sha256,
        "checked": datetime.datetime.fromtimestamp(
            setup.clock.time(), datetime.timezone.utc
        ).isoformat(timespec="seconds"),
        "engines": stats,
        "verdict": verdict,
    }


def check_virustotal(item, sha256, setup):
    cache = setup.downloads / f"{sha256}.virustotal.json"
    try:
        verdict = json.loads(cache.read_text())
        if (
            verdict["sha256"] != sha256
            or verdict["verdict"] not in ("clean", "flagged")
            or not isinstance(verdict["engines"], dict)
            or not isinstance(verdict["checked"], str)
        ):
            raise ValueError(cache)
        verdict["verdict"] = scan_verdict(verdict["engines"])
    except (OSError, ValueError, KeyError, TypeError):
        verdict = ask_virustotal(item, sha256, setup)
        write_json(cache, verdict)
    if verdict["verdict"] != "clean":
        engines = verdict["engines"]
        raise Refused(
            "virustotal",
            item,
            f"{engines.get('malicious', 0)} engines report malicious and "
            f"{engines.get('suspicious', 0)} suspicious for SHA-256 {sha256} "
            f"(checked {verdict['checked']})",
        )


def run_check(check, item, runner, argv):
    rc, out, err = runner.run(argv)
    if rc != 0:
        raise Refused(
            check, item, f"{' '.join(argv)} exited {rc}: {(out + err).strip()}"
        )
    return out + err


def has_bundle(app, bundle, archive):
    if not (bundle / "Contents/Info.plist").is_file():
        raise Refused("unpack", app.id, f"{archive.name} holds no {app.app}")


def copy_from_dmg(app, archive, work, bundle, runner):
    mount = work / "mount"
    mount.mkdir()
    attach = [HDIUTIL, "attach", "-nobrowse", "-readonly", "-mountpoint"]
    run_check("unpack", app.id, runner, attach + [str(mount), str(archive)])
    try:
        has_bundle(app, mount / app.app, archive)
        run_check("unpack", app.id, runner, [DITTO, str(mount / app.app), str(bundle)])
    finally:
        if not detach(runner, mount):
            raise Refused("unpack", app.id, f"{HDIUTIL} could not detach {mount}")


@contextmanager
def unpacked(app, archive, runner):
    work = Path(tempfile.mkdtemp(prefix="mac-setup-app-"))
    bundle = work / "app" / app.app
    try:
        if app.container == "dmg":
            copy_from_dmg(app, archive, work, bundle, runner)
        else:
            unzip = [DITTO, "-x", "-k", str(archive), str(bundle.parent)]
            run_check("unpack", app.id, runner, unzip)
        has_bundle(app, bundle, archive)
        for detritus in FINDER_DETRITUS:
            run_check(
                "unpack", app.id, runner, [XATTR, "-r", "-d", detritus, str(bundle)]
            )
        yield bundle
    finally:
        shutil.rmtree(work, ignore_errors=True)


def detach(runner, mount):
    for flags in ([], ["-force"]):
        if runner.run([HDIUTIL, "detach", *flags, str(mount)])[0] == 0:
            return True
    return False


def bundle_info(bundle):
    with open(bundle / "Contents/Info.plist", "rb") as file:
        info = plistlib.load(file)
    return info.get("CFBundleIdentifier"), str(
        info.get("CFBundleShortVersionString", "")
    )


def check_app(app, bundle, runner, sha256):
    path = str(bundle)
    run_check(
        "codesign", app.id, runner, [CODESIGN, "--verify", "--deep", "--strict", path]
    )
    run_check("spctl", app.id, runner, [SPCTL, "--assess", "--type", "execute", path])
    requirement = (
        f'anchor apple generic and certificate leaf[subject.OU] = "{app.team_id}"'
    )
    rc, out, err = runner.run(
        [CODESIGN, "--verify", "--deep", "--strict", f"-R={requirement}", path]
    )
    if rc != 0:
        raise Refused(
            "team_id",
            app.id,
            f"not signed by Team ID {app.team_id}: {(err or out).strip()}",
        )
    bundle_id, version = bundle_info(bundle)
    if bundle_id != app.bundle_id:
        raise Refused(
            "bundle_id", app.id, f"bundle id is {bundle_id}, not {app.bundle_id}"
        )
    if not at_least(app.id, version, app.version):
        raise Refused(
            "version", app.id, f"version {version} is older than {app.version}"
        )
    return Verified(path=bundle, version=version, sha256=sha256)


@contextmanager
def verified_app(app, setup):
    check_age(app, setup.clock)
    archive, sha256 = download(app, setup.downloads, setup.opener)
    check_virustotal(app.id, sha256, setup)
    with unpacked(app, archive, setup.runner) as bundle:
        yield check_app(app, bundle, setup.runner, sha256)
