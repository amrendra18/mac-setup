# Accounts

One admin run of `playbooks/site.yml` adopts the accounts that already exist with 0 changes and creates each missing listed account with its home folder. A later run adopts the new account with 0 changes and asks no password. The account's password never shows in a log or in the evidence.

## Sub-features

- `accounts-adopt` adopts the running account with 0 changes and no password prompt.
- `accounts-create` creates `setuptest1` with its home folder on the first run ever. The check run before it reports 1 change and creates nothing.
- `accounts-again` adopts `setuptest1` with 0 changes on every later run.
- `accounts-password` keeps the test password out of `.local/logs/` and the evidence folder.

## How to get to it (user POV)

- List each account under `accounts` in the profile, with `name`, `full_name` and `admin`.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K`. Ansible asks the admin password once, then a hidden prompt asks the password of each account it creates.
- Run the same command again. It changes nothing and asks no account password.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The first run ever creates a real standard account, `setuptest1`.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- `scripts/bootstrap` has run in this checkout.

- **Profile.** The journey lists the running account as `"<running account>"`, which the harness replaces with the name of the account that runs it, and `setuptest1` as a standard account.
- **Password.** The journey lists `passwords: [setuptest1]`. The harness makes a random password for this run, writes it to a mode 600 file in the evidence folder, passes `-e @FILE` to every run, and deletes the file after the runs. The journey never types at the hidden prompt.
- **Case.** Before the runs, the harness reads the record of `setuptest1` with `dscl . -read`. If the account exists, the case is `adopt` and the harness checks the `runs` list. If it is missing, the case is `create` and the harness checks the `create_runs` list. The output line, `summary.json` and `summary.md` name the case.
- **Create case.** Check run: exit 0, `changed=1 failed=0`, nothing created. Apply: exit 0, `changed=1 failed=0`. Apply again: exit 0, `changed=0 failed=0`.
- **Adopt case.** Check run and apply: exit 0, `changed=0 failed=0`.
- **No prompt.** Every run's output holds 0 copies of Ansible's warning "Not waiting for response to prompt", so no task asked for a password.
- **Proof.** After the runs, the record of `setuptest1` reads with `dscl . -read` and `reads/accounts-files.json` shows the home folder `~setuptest1` with owner `setuptest1` and mode 750. `summary.json` lists the test password with 0 copies in `.local/logs/` and the evidence folder.

## Gotchas

- Nothing in this project deletes an account, and deleting one over SSH fails. The maintainer deletes `setuptest1` in System Settings > Users & Groups. The next run is then a `create` case again.
- A new account has no secure token. The summary names it. With FileVault on, it could not unlock the disk at boot.
- The running account must be an admin, because the journey lists it with `admin: true`. A different admin flag is a refused line and the run exits 2.
