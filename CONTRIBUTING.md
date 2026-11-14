# Contributing to mac-setup

This page sets up a clone for development and lists the checks a change must pass. [AGENTS.md](AGENTS.md) holds the rules for making a change, the invariants and their tests. [How mac-setup works](docs/how-it-works.md) explains the code.

## Set up a clone

Clone over HTTPS, then install the runtime tools:

```sh
git clone https://github.com/amrendra18/mac-setup.git ~/mac-setup
cd ~/mac-setup
scripts/bootstrap
```

Contributors with an existing GitHub SSH key can instead clone with `git clone git@github.com:amrendra18/mac-setup.git ~/mac-setup`.
The checks then need three more things.

1. The dev tools yamllint, ansible-lint and black, pinned in `requirements/dev.txt`. These two commands download each pinned file from its direct link in `requirements/dev-urls.txt`, check its SHA-256 hash, and install it into `.local/venv` without the PyPI index:

   ```sh
   mkdir -p .local/wheels && (cd .local/wheels && xargs -n 1 curl --fail --silent --show-error --location --remote-name <../../requirements/dev-urls.txt)
   .local/venv/bin/python -I -m pip install --isolated --no-index --find-links .local/wheels --no-deps --require-hashes --only-binary :all: -r requirements/dev.txt
   ```

2. The Gitleaks secret scanner, in `.local/bin/gitleaks` or on your PATH. The `first-commit-setup` command below puts it in `.local/bin/`.
3. The file `.local/private-words.txt`, with one private word per line. Put in at least your Mac's name from `scutil --get LocalHostName` and your account name from `whoami`. Git ignores the file. The private-word scan fails when the file is missing or empty, or when a file in the repository contains one of its words.

## Install the commit hooks

Commits and pushes run pre-commit hooks from [first-commit](https://github.com/amrendra18/first-commit), pinned in `.pre-commit-config.yaml`. They check signing, secrets and private values. The bootstrap does not install pre-commit. Install it with Homebrew (brew.sh) as `brew install pre-commit`, then run:

```sh
pre-commit install
pre-commit run first-commit-setup --hook-stage manual
```

The second command sets the clone's Git identity and SSH signing, and installs the Gitleaks scanner. To check a clone later, run:

```sh
pre-commit run first-commit-check --hook-stage manual --all-files
```

## Run the checks

`scripts/check` is the gate every change must pass. It changes no settings, so it runs on any Mac.

```sh
scripts/check fast
scripts/check full
```

`fast` runs these checks, in this order:

1. yamllint on every YAML file and `scripts/render-browser-map --check` for stale browser data.
2. The playbook syntax check.
3. The unit tests in `tests/modules/` and `tests/harness/`.
4. Every broken test profile in `tests/inputs/`, and the two-Mac inventory `tests/two-macs/hosts.yml`. Each one must stop in the profile check with exit code 2 and `changed=0`.
5. The structure rules forbid `sudo` in roles and allow `become` only in the "Machine and other accounts" play of `playbooks/site.yml`. They also forbid changes to the privacy database or System Integrity Protection.
6. The private-word scan.
7. The Gitleaks secret scan.

`full` runs `fast`, then `tests/test_reference.py`, a `black --check` format check of the Python files, and ansible-lint. `tests/test_reference.py` checks every entry of the settings reference (`settings/macos/*.yml`), and checks that the template matches it.

The full check also tests ansible-lint itself.
It verifies the installed version against its pin and fails if lint prints an ignored exception for the real roles.
Lint must reject an unknown argument in each of five deliberately broken module calls.
A temporary test app in Ansible check mode proves the helpers load without downloading or installing anything.

The last line says `every check held` and the script exits 0 when every check held. It exits 1 when a check failed, and the `FAIL` line names it.

## Change one setting

1. Edit the entry in `settings/macos/AREA.yml`. [The settings reference](docs/how-it-works.md#the-settings-reference) gives its fields.
2. For a new entry, raise `ENTRY_COUNT` in `tests/test_reference.py`. Then update the counts in README.md ("Before you start") and in docs/how-it-works.md ("The settings reference"). `tests/test_reference.py` fails on a stale count and names the sentence. A reworded count sentence also fails; then update its pattern in `DOC_COUNTS` in that file.
3. For a browser entry, run `scripts/render-browser-map`.
4. Run `scripts/render-template`.
5. Run `scripts/check full`.
6. On a Mac set aside for testing, run `scripts/verify fast --feature NAME`. When it proves the setting, add the macOS version and date to `proven_on`, and update the proven count in README.md.

## Prove a change on a real Mac

`scripts/verify` proves features on a real Mac and keeps the evidence in `.local/verify/UTCTIME/`. It changes the real settings of the account that runs it. The `accounts` journey (the test script for one feature) also creates the real standard account `setuptest1`, which only a person can delete. Run it only on a Mac set aside for testing.

```sh
scripts/verify fast --feature dock-basics
```

The [verification map](verify/features/README.md) lists every feature, the journey file format and the evidence each run keeps.

## Maintainer scripts

1. `scripts/render-template` writes `inventory/example/TEMPLATE.yml` from `settings/macos/`. Run it after a change to the settings reference.
2. `scripts/sync-example` refreshes the example profile in `inventory/example/`, and the profile block of `verify/journeys/example-check.yml`, from the maintainer's private profile, default `.local/inventory`. It keeps personal values out, and stops with exit code 1 on a free-text setting it has no neutral value for.
3. `scripts/app-catalog ID` prints a vendor's current version, URL and SHA-256 for one app, for review. It never edits the catalog.
4. `scripts/collection-age` checks the Ansible collection pins in `requirements/collections.txt` against Galaxy's publish dates, and fails on a pin younger than 21 days. It needs the network.

Every script in `scripts/` answers `--help` with its usage and exit codes.

Browser capture tests use temporary artificial folders and selected policy copies.
They never use the running account's browser data.
The command tests need read-only process and lock checks. Ordinary browsers may remain open because fixtures use isolated folders.
