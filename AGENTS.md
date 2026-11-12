# Agent rules for mac-setup

This repository is an Ansible project that sets up a Mac from one profile. `README.md` owns the product, the journeys, the scope, the principles and the plan. Read the section that matches your change. Milestone M0 built `scripts/bootstrap`, `scripts/check`, `scripts/verify` and 3 Dock settings. A command or test marked "planned" arrives with the milestone it names.

## Where to look

1. For a change to what a user sees or runs, read the `README.md` sections "What a run looks like" and "Scope".
2. For a new or changed setting, read the entry shape in `settings/macos/` and the README section "How it works".
3. For a test or a check, read `scripts/check`, `scripts/verify`, `verify/features/` and `verify/journeys/`.
4. The current milestone and its "Done when" line sit in the maintainer's private plan. Ask the maintainer when you need them.

## How a change is made

1. State the outcome and the check that proves it.
2. Write the failing test or journey first. It is a unit test in `tests/`, a broken test profile in `tests/inputs/`, or a journey in `verify/journeys/`.
3. Make the smallest change that passes it.
4. Run `scripts/check fast`. Before a hand-off, also run `scripts/check full`.
5. Only `scripts/verify fast --feature NAME` on a real Mac proves a change to settings or roles. Report the evidence folder it prints.

## Authority

1. Builders never commit, push, change the remote, or rewrite history. Only the maintainer's main agent commits, after its own review and an independent review.
2. Nobody makes this repository public except the maintainer.
3. No agent adds or upgrades a dependency without the maintainer's yes. Every pin carries a SHA-256 hash and is at least 21 days old. Test: `scripts/collection-age` checks the Ansible collection pins in `requirements/collections.txt` against Galaxy's publish dates. Run it before a collection pin changes; it needs the network.
4. Only the maintainer's main agent touches a real Mac or GitHub. Builders work in their own worktree with no network.
5. A step that needs a person at the Mac (deleting an account, granting a secure token, a privacy permission, signing in) belongs to the maintainer.
6. Do not edit files outside your assigned list, and do not tidy unrelated code.
7. Stage only the approved paths by name; never use broad staging commands. Never commit secrets, private keys or personal data. Git configuration and hook installation belong to the maintainer.

## Invariants

Each rule names the test that enforces it. A test marked "planned" does not exist yet; its milestone adds it.

1. No machine name, account name, host name or address appears in a tracked file. Test: the private-word scan in `scripts/check fast`, which reads its word list from the ignored file `.local/private-words.txt`.
2. No password or secret appears in a tracked file, a log, or an evidence folder. Every task that handles a password sets `no_log: true`, or passes it only to a module parameter declared `no_log`. Test: gitleaks in `scripts/check fast`, and the `accounts` journey, which finds 0 copies of its random test password.
3. Admin rights appear only in play 2 of `playbooks/site.yml`. No role contains `become:` or `sudo`. Test: the structure check in `scripts/check fast`.
4. No task writes the privacy database, runs `tccutil reset`, turns off System Integrity Protection, or works around a consent prompt. Test: the structure check in `scripts/check fast`.
5. A profile error stops the run before any write, with exit code 2 and one line per problem. Test: the broken test inputs in `scripts/check fast`, `tests/inputs/bad-position.yml` in the `dock-basics` journey, and the broken profiles in `tests/inputs/profile/`.
6. A setting macOS refuses at write time does not stop the other settings, and the run ends with exit code 2. Test: the `refusals` journey.
7. A second run changes nothing. Test: every journey's second run expects `changed=0`.
8. No run deletes an account or changes an existing account's password or admin rights. Test: the unit tests in `tests/modules/test_macos_account.py`.
9. A machine-scope preference writes only a domain path that starts with `/`. Test: `tests/test_reference.py`.
10. `inventory/example/TEMPLATE.yml` matches the reference. Test: `tests/test_reference.py` compares it with the output of `scripts/render-template`.
11. A setting with `lockout_risk: true` is refused unless the profile sets `allow_lockout_risk: true`. Test: `tests/inputs/lockout-no-optin.yml` (planned, M4).

## Conventions

1. yamllint checks every YAML file against the repository's `.yamllint`. Python files follow black's default format, checked by `black --check` in `scripts/check full`. Python modules have unit tests that use a fake command runner, so tests never touch the Mac.
2. A custom module reads first, writes only on a difference, and supports `--check`. A module that changes a value also supports `--diff`.
3. macOS-only roles carry the `macos_` prefix.
4. Examples, test profiles and journeys use made-up names only.
5. Keep a code comment only for a reason the code cannot show.

## Hand-off

Report what changed, the commands you ran with their exit codes, the evidence folder of any `scripts/verify` run, what you skipped and why, and what stays unproven.
