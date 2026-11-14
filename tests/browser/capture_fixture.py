#!/usr/bin/python3

import argparse
import hashlib
import importlib.util
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "browser_reader", ROOT / "module_utils/browser_settings.py"
)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


def hashes(folder):
    return {
        str(path.relative_to(folder)): [
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        ]
        for path in folder.rglob("*")
        if path.is_file()
    }


def active_values(text):
    return {
        line.strip().split(": ", 1)[0]: json.loads(line.strip().split(": ", 1)[1])
        for line in text.splitlines()
        if line.startswith("  ") and not line.lstrip().startswith("#")
    }


def prove_refusals(work, profile, policies, output, canary):
    base = [
        "/usr/bin/python3",
        "-I",
        "-B",
        str(ROOT / "scripts/browser-settings"),
        "capture",
        "--browser",
        "firefox",
        "--policy-root",
        str(policies),
    ]
    results = {}

    def expect(name, args, destination, code, env=None):
        result = subprocess.run(
            args + ["--output", str(destination)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if result.returncode != code or destination.exists():
            raise AssertionError(
                "Fixture refusal did not meet its expected exit and absent report: "
                + name
            )
        if reader.leaked_test_values([canary], [result.stdout, result.stderr]):
            raise AssertionError("A planted private test value reached refusal output.")
        results[name] = {"exit": code, "residual_reports": 0, "leaked_test_values": 0}

    explicit = base + ["--firefox-profile", str(profile)]
    expect("unsafe_output", explicit, profile / "refused.yml", 2)
    saved = (profile / "prefs.js").read_bytes()
    try:
        (profile / "prefs.js").write_text("user_pref(" + json.dumps(canary) + ", ???);")
        expect("malformed_source", explicit, output / "malformed.yml", 3)
    finally:
        (profile / "prefs.js").write_bytes(saved)
    fake_home = work / "fake-home"
    registry = fake_home / "Library/Application Support/Firefox"
    registry.mkdir(parents=True)
    (registry / "profiles.ini").write_text(
        "[Profile0]\nPath=artificial-a\nIsRelative=1\n[Profile1]\nPath=artificial-b\nIsRelative=1\n"
    )
    expect(
        "ambiguous_profiles",
        base,
        output / "ambiguous.yml",
        3,
        dict(os.environ, HOME=str(fake_home)),
    )

    def artificial_running(family):
        raise reader.CaptureError(3, "Quit the selected browser with Command-Q.")

    try:
        reader.read_browser(
            "firefox", {}, profile, profile.parent, [], process_check=artificial_running
        )
    except reader.CaptureError as error:
        if error.code != 3:
            raise AssertionError("Running-browser simulation returned the wrong exit.")
    else:
        raise AssertionError("Running-browser simulation was not refused.")
    results["running_browser_simulation"] = {
        "exit": 3,
        "residual_reports": 0,
        "leaked_test_values": 0,
        "surface": "shared_reader",
    }
    return results


def prove(families, evidence, label):
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="browser-capture-test-") as folder:
        work = Path(folder)
        sources, output = work / "sources", work / "reports"
        sources.mkdir()
        output.mkdir()
        policies = sources / "policies"
        policies.mkdir()
        firefox = sources / "firefox"
        firefox.mkdir()
        chrome_data = sources / "chrome"
        chrome_data.mkdir()
        chrome = chrome_data / "fixture-profile"
        chrome.mkdir()
        # Deliberately personal test values are generated and never retained.
        canary = "test-person-" + os.urandom(12).hex() + "@example.invalid"
        (firefox / "compatibility.ini").write_text(
            "[Compatibility]\nLastVersion=158.0.1_20261101010101/20261101010101\n"
        )
        (firefox / "prefs.js").write_text(
            'user_pref("privacy.globalprivacycontrol.enabled", true);\nuser_pref("browser.newtabpage.enabled", false);\nuser_pref("browser.startup.homepage", '
            + json.dumps("https://" + canary + "/private")
            + ");\n"
        )
        (firefox / "user.js").write_text(
            'user_pref("browser.newtabpage.enabled", false);\nuser_pref('
            + json.dumps(canary)
            + ', "test input");\n'
        )
        (policies / "org.mozilla.firefox.plist").write_bytes(
            plistlib.dumps(
                {
                    "OfferToSaveLogins": False,
                    "Homepage": {"URL": "https://" + canary + "/private"},
                    "DNSOverHTTPS": {"ProviderURL": "https://dns.quad9.net/dns-query"},
                }
            )
        )
        (chrome_data / "Last Version").write_text("158.0.8127.37")
        (chrome / "Preferences").write_text(
            json.dumps(
                {
                    "bookmark_bar": {"show_on_all_tabs": True},
                    "homepage": "https://" + canary + "/private",
                }
            )
        )
        (chrome_data / "Local State").write_text(
            json.dumps({"async_dns": {"enabled": True}, canary: "test input"})
        )
        (chrome / "Secure Preferences").write_text(
            json.dumps({"extensions": {"settings": {canary: "test input"}}})
        )
        (policies / "com.google.Chrome.plist").write_bytes(
            plistlib.dumps(
                {
                    "HttpsOnlyMode": "force_enabled",
                    "HomepageLocation": "https://" + canary + "/private",
                }
            )
        )
        expected = {
            "firefox": {
                "firefox.pref.privacy_globalprivacycontrol_enabled": True,
                "firefox.new_tab_page": False,
                "firefox.offer_to_save_logins": False,
                "firefox.dns_over_https.provider_url": "https://dns.quad9.net/dns-query",
            },
            "chrome": {
                "chrome.bookmark_bar_enabled": True,
                "chrome.built_in_dns_client_enabled": True,
                "chrome.https_only_mode": "force_enabled",
            },
        }
        before = hashes(sources)
        proof = {
            "test_inputs": "artificial browser folders",
            "runs": {},
            "leak_scanner_positive_control": False,
        }
        if reader.leaked_test_values(
            [canary], ["Deliberately leaking test input " + canary]
        ) != [0]:
            raise AssertionError(
                "Leak scanner failed its deliberately leaking test input."
            )
        proof["leak_scanner_positive_control"] = True
        for family in families:
            destination = output / (family + ".yml")
            args = [
                "/usr/bin/python3",
                "-I",
                "-B",
                str(ROOT / "scripts/browser-settings"),
                "capture",
                "--browser",
                family,
                "--policy-root",
                str(policies),
                "--output",
                str(destination),
            ]
            args += (
                ["--firefox-profile", str(firefox)]
                if family == "firefox"
                else [
                    "--chrome-profile",
                    str(chrome),
                    "--chrome-user-data-dir",
                    str(chrome_data),
                ]
            )
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise AssertionError(
                    "Fixture capture exit "
                    + str(result.returncode)
                    + ": "
                    + result.stderr.strip()
                )
            text = destination.read_text()
            actual = active_values(text)
            if actual != expected[family]:
                raise AssertionError(
                    "Safe assignment ids or values differ from fixture expectations: "
                    + json.dumps(
                        {"expected": expected[family], "observed": actual},
                        sort_keys=True,
                    )
                )
            if stat.S_IMODE(destination.stat().st_mode) != 0o600:
                raise AssertionError("Fixture report mode differs from 0600.")
            if reader.leaked_test_values(
                [canary], [text, result.stdout, result.stderr]
            ):
                raise AssertionError("A planted private test value leaked.")
            if hashes(sources) != before:
                raise AssertionError("Capture changed an artificial source.")
            proof["runs"][family] = {
                "exit": result.returncode,
                "expected_assignments": len(expected[family]),
                "observed_assignments": len(actual),
                "source_changes": 0,
                "leaked_test_values": 0,
                "report_mode": "0600",
                "versions": [
                    line
                    for line in text.splitlines()
                    if line.startswith("# " + family + " versions:")
                ],
            }
            shutil.copyfile(destination, evidence / (label + "-" + family + ".yml"))
            (evidence / (label + "-" + family + ".yml")).chmod(0o600)
        if "firefox" in families:
            proof["refusals"] = prove_refusals(work, firefox, policies, output, canary)
        retained = [path.read_text() for path in evidence.iterdir() if path.is_file()]
        if reader.leaked_test_values([canary], retained):
            raise AssertionError(
                "A planted private test value reached retained evidence."
            )
        summary = json.dumps(proof, sort_keys=True)
        (evidence / (label + "-proof.json")).write_text(summary + "\n")
        print(summary)


def main():
    parser = argparse.ArgumentParser(
        description="Prove capture against artificial browser files, keeping only filtered evidence."
    )
    parser.add_argument(
        "--browser", choices=("firefox", "chrome", "both"), default="both"
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--label", choices=("first", "second"), required=True)
    args = parser.parse_args()
    try:
        prove(
            ["firefox", "chrome"] if args.browser == "both" else [args.browser],
            args.evidence,
            args.label,
        )
    except (AssertionError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
