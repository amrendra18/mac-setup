# mac-setup verification map

This folder is the maintained source for proving mac-setup's user-facing features on a real Mac. It is also the guide to the harness, `scripts/verify`. Read this file first, then the feature file that matches your change.

`scripts/verify` changes the real settings of the account that runs it. Run it only on a Mac set aside for testing, never on a Mac whose settings matter to someone.

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

1. **Doctor.** It checks that the Mac runs macOS, that `ansible.cfg` exists (it keeps Ansible's files in `.local/`), and that `.local/venv` holds the ansible-core version pinned in `requirements/ansible.txt` and each collection version pinned in `requirements/collections.txt`. It checks that each selected journey's playbook exists. When a journey reads as another account, it checks that `sudo -n true` works. A failed check prints `not ready` with its name and ends the run with exit 3. Before `scripts/bootstrap` has run, the script exits 3 without making an evidence folder.
2. **Launch.** There is no server. Each journey gets its own inventory in the evidence folder: one host, `verify-mac`, in the group `macs`, with a local connection, and the journey's profile as its only host variables file.
3. **Setup.** The journey's `setup` commands run in order with `/bin/sh -c`. A non-zero exit fails the journey before any playbook runs.
4. **Drive.** Each run calls `.local/venv/bin/ansible-playbook -i INVENTORY PLAYBOOK ARGS` from the checkout. The harness compares the exit code and the recap counts for `verify-mac` with the journey's expectations. The first run that differs fails the journey, and the later runs are skipped.
5. **Measure.** Each run's wall time in seconds goes into the summary.
6. **Reads.** The harness reads each listed key before the first run and after the last run with `defaults export DOMAIN -`, which gives typed values. The reads after the runs must match the journey. The summary says whether the reads before and after are equal, which proves that a failed run wrote nothing.
7. **Cleanup.** The journey's `cleanup` commands always run, also after a failure. A failing cleanup fails the journey. Cleanup never deletes the evidence folder.

## Evidence

Each run writes `.local/verify/UTCTIME/`, for example `.local/verify/20261112T142233Z/`. The last output line is that folder.

| # | Path | What it holds |
| --- | --- | --- |
| 1 | `summary.json` | the level, the macOS version, the commit and whether the tree had uncommitted changes, every doctor check, and per feature its status, reason, runs (exit, recap, seconds, problems) and whether the reads before and after are equal |
| 2 | `summary.md` | the same as tables |
| 3 | `logs/` | per journey, each setup and cleanup command and each playbook command line, each above its full output and exit code |
| 4 | `reads/` | `NAME-before.json` and `NAME-after.json` with every read value and its type |
| 5 | `inventory/` | the inventory and profile each journey ran with |

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
| 5 | `setup` | no | shell commands run before the reads and runs |
| 6 | `runs` | yes | each run's `name`, `exit` code, optional `args` (a list, such as `[--check]`) and optional `recap` counts, such as `{changed: 0, failed: 0}` |
| 7 | `reads` | no | each read's `domain` and `key`, then either `type` and `value` or `absent: true`; optional `account` (read as that account through `sudo -n -u`) and `host: currentHost` |
| 8 | `cleanup` | no | shell commands run at the end, always |

A read's `type` is the name macOS reports: `string`, `integer`, `float`, `boolean`, `array`, `dictionary`, `data` or `date`. The settings reference uses the `osx_defaults` names (`int`, `bool`) instead. A float read matches an integer value of the same size, so `value: 32` matches a stored `32.0`.

## Driving conventions

1. Journeys use made-up account names only. A journey never deletes an account. A journey that creates an account adopts it on every later run.
2. A journey's setup puts the Mac in the state its first run expects, so the journey can run again and again.
3. A failing journey is fixed in the code or reported. Never weaken a journey to make it pass.
4. A precondition the Mac lacks goes in that feature's `Gotchas` section, and the journey reports the failure.

## Features

| # | Feature | Command | Coverage |
| --- | --- | --- | --- |
| 1 | [Dock basics](./dock-basics.md) | `scripts/verify fast --feature dock-basics` | check run, apply, second run, typed reads of 3 Dock keys, and the planted broken profile |
