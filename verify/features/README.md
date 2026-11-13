# mac-setup verification map

This folder is the maintained source for proving mac-setup's user-facing features on a real Mac. It is also the guide to the harness, `scripts/verify`. Read this file first, then the feature file that matches your change.

`scripts/verify` changes the real settings of the account that runs it. The `accounts` journey also creates the real standard account `setuptest1`, which only a person can delete, and other journeys write its preferences. Run it only on a Mac set aside for testing, never on a Mac whose settings matter to someone.

The user commands in feature pages select the README's profile with `-i ~/.dot/mac-setup/inventory`.
The harness writes and selects its own test inventory.

## Usage

```text
scripts/verify [fast|full] [--feature NAME] [--profile FILE]
```

1. `fast`, the default, runs every journey whose `level` is `fast`. `full` runs every journey.
2. `--feature NAME` runs only the journey `verify/journeys/NAME.yml`, whatever its level.
3. `--profile FILE` replaces that journey's profile with FILE. Use it to plant a broken test input, such as `tests/inputs/bad-position.yml`. The journey must then fail.
4. `--help` lists the feature names, the exit codes and this map's path.

Exit code 0 means every selected feature was proven. 1 means a feature failed, and its output line names it. 2 means a usage error, including a malformed journey file. 3 means the doctor found the Mac not ready, and no playbook ran.

## Steps of one run

1. **Doctor.** It checks that the Mac runs macOS, that `ansible.cfg` exists (it keeps Ansible's files in `.local/`), and that `.local/venv` holds the ansible-core version pinned in `requirements/ansible.txt` and each collection version pinned in `requirements/collections.txt`. It checks, in one check per selected journey, that every playbook the journey runs exists, and that `sudo -n true` works: the "Machine and other accounts" play of `playbooks/site.yml` and reads as another account need password-free sudo, because the harness passes no `-K`. A failed check prints `not ready` with its name and ends the run with exit 3. Before `scripts/bootstrap` has run, the script exits 3 without making an evidence folder.
2. **Launch.** There is no server. Each run gets its own inventory in the evidence folder: one host, `verify-mac`, in the group `macs`, with a local connection, and the run's profile as its only host variables file. The run's profile is its own `profile`, else the journey's. The harness replaces each value `"<running account>"` with the name of the account that runs it.
3. **Save.** The harness saves each key in the journey's `saves` to `.local/NAME-journey/saved.plist`: its type and value, or that it is absent. An entry without a key saves its whole domain. It reads each domain once with `defaults export`. A key that is not a boolean, integer, float or string fails the journey before setup, and the harness writes no save. A `saved.plist` left by an earlier run whose restore did not finish also fails the journey before setup; step 11 then restores that file.
4. **Setup.** The journey's `setup` commands run in order with `/bin/sh -c`. A non-zero exit fails the journey before any playbook runs, and the reason ends with the command's last output line.
5. **Case.** A journey with `creates: NAME` reads that account's record with `dscl . -read`. If the account exists, the case is `adopt` and the `runs` list applies. If it is missing, the case is `create` and the `create_runs` list applies. Only one list runs, so no expectation accepts both outcomes. The output line and the summary name the case. After the runs, the account must exist.
6. **Passwords.** For each name in `passwords`, the harness makes a random password for this run and writes `account_passwords` to the mode 600 file `NAME-passwords.json` in the evidence folder. Every run of the journey gets `-e @FILE`. The harness deletes the file after the runs, also after a failure, and never prints a password.
7. **Drive.** Each run calls `.local/venv/bin/ansible-playbook -i INVENTORY PLAYBOOK ARGS` from the checkout, with its input from `/dev/null`, so a prompt cannot wait for an answer. The harness compares the exit code, the recap counts for `verify-mac`, each `output` pattern's count, and the run's own `reads` with the journey's expectations. A run whose output holds `<< error`, Ansible's mark for a template it could not fill, fails as well. The first run that differs fails the journey, and the later runs are skipped.
8. **Measure.** Each run's wall time in seconds goes into the summary.
9. **Reads.** The harness reads each journey-level key before the first run and after the last run with `defaults export DOMAIN -`, which gives typed values. A run's own `reads` are read after that run. The reads after the runs must match the journey. Each `files` entry is checked after the last run with `stat`, run as its owner through `sudo -n -u` when the owner is another account. The summary says whether the reads before and after are equal, which proves that a failed run wrote nothing.
10. **Cleanup.** The journey's `cleanup` commands always run, also after a failure. A failing cleanup fails the journey. Cleanup never deletes the evidence folder.
11. **Restore.** When `saved.plist` exists, the harness writes each saved key back with its saved type, deletes each key that was absent, and imports each saved domain with `defaults import`. It then reads each saved key again. A key that differs, or a failed import, fails the journey and keeps `saved.plist`, so the next run refuses to start and restores it. Otherwise it deletes `saved.plist`. Last, it restarts the Dock when the save holds a `com.apple.dock` entry and Finder when it holds a `com.apple.finder` entry, only when the account that runs the journey owns the screen, as `roles/macos_defaults` does.
12. **Password search.** Last, the harness searches every file in `.local/logs/` (Ansible's own log) and the whole evidence folder for each test password. Any copy fails the journey, and the summary records the count per account.

## Evidence

Each run writes `.local/verify/UTCTIME/`, for example `.local/verify/20261112T142233Z/`. The last output line is that folder.

| # | Path | What it holds |
| --- | --- | --- |
| 1 | `summary.json` | the level, the macOS version, the commit and whether the tree had uncommitted changes, every doctor check, and per feature its status, reason, case (`create` or `adopt`, only with `creates`), runs (exit, recap, seconds, problems), test password copies per account, and whether the reads before and after are equal |
| 2 | `summary.md` | the same as tables |
| 3 | `logs/` | per journey, each setup, cleanup and restore command and each playbook command line, each above its full output and exit code |
| 4 | `reads/` | `NAME-before.json` and `NAME-after.json` with every read value and its type, `NAME-RUN.json` for a run's own reads, and `NAME-files.json` with each checked file's owner and mode |
| 5 | `inventory/` | `NAME/RUN/`, the inventory and profile each run ran with |

Proof standards:

1. A feature is proven only by a journey that runs the real playbook on a real Mac and reads the stored values back.
2. A check run (`--check`) proves what would change. Only an apply followed by reads proves a setting.
3. A second run with `changed=0` proves that a run changes nothing when the Mac already matches.
4. A planted broken input proves the failure path only when the run exits 1, names the feature, and the reads before and after are equal.

## Journey files

One YAML file per journey in `verify/journeys/`. The file name without `.yml` is the feature name, and `verify/features/NAME.md` must exist. Adding a journey needs no change to `scripts/verify`.

| # | Field | Required | Meaning |
| --- | --- | --- | --- |
| 1 | `summary` | yes | one sentence on what the journey proves |
| 2 | `level` | no | `fast` (default) or `full` |
| 3 | `playbook` | no | the playbook to run, default `playbooks/site.yml` |
| 4 | `profile` | no | the profile, written as the host variables of `verify-mac`; made-up account names only |
| 5 | `saves` | no | the preference keys the journey changes, each a `domain` with an optional `key` and optional `host: currentHost`; an entry without `key` saves the whole domain. Saved before setup, restored after cleanup. List a key once, and a whole domain without its keys |
| 6 | `setup` | no | shell commands run after the save, before the reads and runs |
| 7 | `runs` | yes | each run's `name` (unique in the list), `exit` code, optional `args` (a list, such as `[--check]`), optional `recap` counts, such as `{changed: 0, failed: 0}`, optional `profile` (replaces the journey's profile for this run only), optional `playbook` (replaces the journey's playbook for this run only), optional `reads` (checked after this run), and optional `output`, a list of `{pattern, count}`: the run's output must hold exactly `count` matches of the Python regular expression `pattern`, searched in the whole output with `^` and `$` matching at each line, so `\n` can join 2 lines |
| 8 | `reads` | no | each read's `domain` and `key`, then either `type` and `value` or `absent: true`; optional `account` (read as that account through `sudo -n -u`) and `host: currentHost` |
| 9 | `passwords` | no | account names that get a random password for this run, passed as `account_passwords` with `-e @FILE` |
| 10 | `creates` | no | the one account this journey may create; needs `create_runs` |
| 11 | `create_runs` | with `creates` | the runs, in the `runs` shape, that apply instead of `runs` when that account is missing before the runs |
| 12 | `files` | no | each file's `path` (`~NAME` allowed), `owner` and `mode` as quoted text, such as `"600"` |
| 13 | `cleanup` | no | shell commands run after the reads and before the restore, always |

A read's `type` is the name macOS reports: `string`, `integer`, `float`, `boolean`, `array`, `dictionary`, `data` or `date`. The settings reference uses the `osx_defaults` names (`int`, `bool`) instead. A float read matches an integer value of the same size, so `value: 32` matches a stored `32.0`.

## Driving conventions

1. Journeys use made-up account names only. The running account appears only as `"<running account>"`. A journey never deletes an account. A journey that creates an account names it in `creates` and adopts it on every later run.
2. A journey's setup puts the Mac in the state its first run expects, so the journey can run again and again.
3. A failing journey is fixed in the code or reported. Never weaken a journey to make it pass.
4. A precondition the Mac lacks goes in that feature's `Gotchas` section, and the journey reports the failure.
5. A journey lists in `saves` each preference key of the running account that its setup or runs change, so the harness puts it back. Only a value that is not a preference, such as the volume, keeps its own save and restore in `setup` and `cleanup`.

## Features

| # | Feature | Command | Coverage |
| --- | --- | --- | --- |
| 1 | [Accounts](./accounts.md) | `scripts/verify fast --feature accounts` | adopt the running account, create or adopt `setuptest1`, its home folder, no password prompt, 0 test password copies |
| 2 | [Dock basics](./dock-basics.md) | `scripts/verify fast --feature dock-basics` | check run, apply, second run, typed reads of 3 Dock keys, and the planted broken profile, each key restored |
| 3 | [Per-account settings](./per-account.md) | `scripts/verify fast --feature per-account` | 3 Dock keys written and read as `setuptest1`, the plist owner and mode, second run, `unset` read as absent |
| 4 | [Refused settings](./refusals.md) | `scripts/verify fast --feature refusals` | a type mismatch refused with exit 2 and one line, the 2 other keys written, the Dock restarted, each key restored |
| 5 | [Example profile check](./example-check.md) | `scripts/verify fast --feature example-check` | the example profile and every reference entry load, and a check run fails nothing and writes nothing |
| 6 | [Guided click-through](./by-hand.md) | `scripts/verify fast --feature by-hand` | `by-hand.yml` and `site.yml` with `--check` each list 3 items from click-only settings in the profile, `site.yml` after its take-back and before its summary, open no page, and change no Dock or Finder key |
| 7 | [Dock apps](./dock-apps.md) | `scripts/verify fast --feature dock-apps` | check run, apply with 1 Dock restart, second run, a missing app refused with exit 2, the bundle ids read back, and the saved Dock restored |
| 8 | [Wallpaper](./wallpaper.md) | `scripts/verify fast --feature wallpaper` | check run, apply, second run, and every screen read back as the black picture |
| 9 | [Apps](./apps.md) | `scripts/verify full --feature apps` | without a VirusTotal key, a check run and an apply each refuse Sublime Text with one line and exit 2, and the scratch Applications folder stays empty |
| 10 | [Dock area](./area-dock.md) | `scripts/verify fast --feature area-dock` | check run, apply with 1 Dock restart, second run, typed reads of 17 keys, each key restored |
| 11 | [Finder area](./area-finder.md) | `scripts/verify fast --feature area-finder` | check run, apply with 1 Finder restart, second run, typed reads of 6 Finder keys, each key restored |
| 12 | [System UI area](./area-systemui.md) | `scripts/verify fast --feature area-systemui` | check run, apply, second run, typed reads of 14 keys for 13 settings, each key restored |
| 13 | [Remaining stores](./remaining.md) | `scripts/verify fast --feature remaining` | check run, apply with 1 Dock restart, second run, keyboard and Mission Control shortcuts, input sources and 2 trackpad gestures read back, both volumes read again by the command after each set, each restored |
| 14 | [Machine settings](./machine.md) | `scripts/verify fast --feature machine` | check run, apply, second run, the 4 power settings read back with `pmset`, each saved value restored |
| 15 | [Input area](./area-input.md) | `scripts/verify fast --feature area-input` | check run, apply, second run, typed reads of 27 keys for 15 settings, each key restored |
| 16 | [Privacy area](./area-privacy.md) | `scripts/verify fast --feature area-privacy` | check run, apply, second run, the typed read of 1 advertising key, the key restored |
| 17 | [Permissions up front](./permissions.md) | `scripts/verify fast --feature permissions` | a check run lists the asks for Full Disk Access and the Desktop and Downloads folders, asks nothing, skips `time.zone` while Full Disk Access is off, and changes no Dock, Finder or time zone value |
