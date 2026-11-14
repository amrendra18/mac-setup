# Browser capture

The journey reads artificial Firefox and Chrome configuration folders through the real system Python command.
It changes no browser setting and opens no real browser profile.

## Run the journey

The journey uses isolated artificial folders. Ordinary Firefox or Chrome instances may remain open when running:

```sh
scripts/verify fast --feature browser-capture
```

The journey uses `tests/browser/capture.yml`, a test-only playbook.
Both runs use Ansible check mode and report `changed=0 failed=0`.
The command still creates filtered fixture reports because reading capture is the behavior under test.
It requires Apple's Python 3.9.6 or later and the existing harness tools.
The capture command itself needs no Ansible.

## What the journey proves

1. Each run captures exactly four expected safe Firefox assignments and three expected safe Chrome assignments.
2. Safe controls sit beside generated private test values in the same configuration sources.
3. Source file fingerprints and modification times remain unchanged.
4. New reports have mode 0600. Existing output files remain protected by command tests.
5. A deliberately leaking test input proves the leak scanner finds its planted value.
6. Reports, command stdout, command stderr, and retained fixture evidence contain zero copies of that private test value.
7. Reports record saved browser versions, map test versions, the running macOS version, and the system Python version.

The harness retains its normal evidence folder under `.local/verify/`.
Filtered artificial reports and proof summaries remain in `.local/browser-capture-journey/`.
The test removes its temporary artificial source folders after each run.
No daily profile path, account, or setting enters the fixtures.
The journey also refuses ambiguous profiles, malformed input, unsafe destinations, and an artificial running-browser result.
Each refusal checks its exit code, zero residual reports, and zero leaked test values.
The running-browser result is injected into the shared reader. Separate process-scanner tests cover its command result.
No real browser starts or stops.
Reader tests separately cover a configuration file changing during capture.

## What still needs the test Mac

This journey proves extraction from made-up files, rather than browser policy acceptance or UI editability.
Create a disposable Firefox profile and a disposable Chrome profile with made-up settings on the test Mac.
Launch and quit each browser, then capture those selected profiles.
Record exact browser versions, command exits, and the harness evidence folder.
Newer profile versions remain eligible for capture. Report headers name the map's tested version and the observed profile version.
Unknown versions and versions outside minimum support or a vendor-documented removal bound remain unavailable.
Browser writes, lock transitions, and bookmarks belong to later journeys.
