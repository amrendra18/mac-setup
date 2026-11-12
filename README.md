# mac-setup

Status: milestone M0. `scripts/bootstrap` installs mac-setup, and a run sets 3 Dock settings for the account that runs it (position, tile size and recents). Accounts, machine settings, the click-through and the other settings are planned. The Plan section names the milestone that builds each one.

mac-setup sets up a Mac from one profile file. You write down the settings you want once, for the Mac and for each account on it. One command then applies them, and running it again changes nothing.

Privacy permissions (camera, microphone, screen recording, full disk access and similar) cannot be set by any script. Apple lets only a person grant them. For these, mac-setup opens the exact System Settings page, tells you what to choose, waits while you click, and checks the result afterwards where macOS allows it.

## Who it is for

mac-setup is for the person who owns a Mac and sets it up, usually right after a fresh install, when only the first admin account exists. That person may also want other accounts on the same Mac, admin or standard, each with its own look.

Today that person clicks through System Settings by hand, once per Mac and once per account, and has no record of what they chose. mac-setup replaces the clicking with a file they can read, review and reuse.

## What a run looks like

### First run on a fresh Mac

1. You sign in to the Mac's first admin account and open Terminal.
2. You check that Apple's Command Line Tools are installed with `xcode-select -p`. If they are missing, `xcode-select --install` opens a dialog, and you click Install. The tools provide `git` and `curl`.
3. You clone this repository and run `scripts/bootstrap`. It downloads pinned copies of uv (a Python installer), Python 3.13.15, Ansible and one Ansible collection, checks each against a SHA-256 hash, and installs them inside the checkout and your home folder. It needs no admin password.
4. You copy the example profile to the private folder `.local/inventory/` and edit the account names.
5. You run `.local/venv/bin/ansible-playbook playbooks/site.yml -K`.
6. Ansible asks for your admin password once (`BECOME password:`). For each account in the profile that does not exist yet, it asks that account's new password once, with typing hidden.
7. You watch the settings change. The Dock moves, Finder changes, and so on. Each line of output names a setting and whether it changed.
8. The run ends with a short summary. It says how many settings show only after you log out and back in, how many need a click (run `playbooks/by-hand.yml` for those), which settings macOS refused and why, and which new accounts lack a secure token. The recap line shows `failed=0` when everything worked.

### Running it again

Run the same command. Settings that already match print as `ok` and the recap shows `changed=0`. No account password is asked, because the accounts exist. If you changed a setting by hand in System Settings, the profile wins and the run sets it back, reported as `changed`.

### Previewing a change

Add `--check` to see what would change without changing anything. Each setting that would change prints as `changed` with its new value. The preview does not print the old value. To see a current value, run `defaults read DOMAIN KEY` with the domain and key from the settings reference.

### Other accounts

Each account in the profile gets its own settings. mac-setup writes them as that account, so they land in that account's own preferences. They take effect the next time that person signs in. mac-setup adopts an account that exists already and keeps its password and admin rights unchanged.

mac-setup never deletes an account. To remove one, use System Settings > Users & Groups in an admin account.

### Click-only settings

Run `.local/venv/bin/ansible-playbook playbooks/by-hand.yml` while you sit at the Mac. For each click-only setting in your profile it opens the System Settings page, prints what to choose, and waits until you press Return. It changes nothing itself.

### Changing a setting back

Edit the profile and run again. Write the old value, or write `unset` to return the setting to the macOS default. Deleting a line only stops mac-setup managing that setting; it leaves the Mac as it is. The log of every run sits in `.local/logs/`.

### When something goes wrong

1. A mistake in the profile (an unknown setting, a value that is not allowed, a machine setting placed under an account) stops the run before anything changes. Exit code 2 and one line per problem tell you what to fix.
2. A setting macOS refuses at write time, for example because a stored value has another type, does not stop the run. The other settings still apply, the Dock still restarts if needed, and the summary names each refused setting. The run then ends with exit code 2.
3. A run from an account without admin rights fails at the machine and other-accounts part. Your own account's settings still apply first.
4. A setting that could cut a remote connection, such as turning Remote Login off, is refused unless the profile sets `allow_lockout_risk: true`.

## Scope

### In the first release

1. Machine settings that need admin rights, such as power, firewall, sharing services, time zone, computer name, and the other admin-only preferences.
2. Each account's own settings, such as Dock, Finder, keyboard, trackpad, mouse, menu bar, appearance, and the other per-account preferences.
3. Creating the accounts listed in the profile, admin or standard, with passwords typed at run time and never stored.
4. The Dock's app list. Apple's own apps can leave the Dock; they are never deleted.
5. A solid black desktop, or a click-only step if macOS offers no script path for it.
6. The guided click-through for privacy permissions and click-only System Settings panes.
7. A generated template, `inventory/example/TEMPLATE.yml`, that lists every known setting with its allowed values, whether it needs admin rights, when it takes effect, and whether it is proven on the current macOS.

### Next, right after the first release

Apps and Homebrew, dotfiles, turning FileVault on (with secure tokens for the accounts mac-setup creates), MDM profiles, an undo command, and running mac-setup from another machine over SSH.

### Not planned

1. Granting privacy permissions by script, editing the privacy database, or working around a consent prompt.
2. Deleting accounts or apps.
3. macOS updates.
4. Linux and Windows for now. The layout leaves room for them. Each system gets its own settings folder and roles, with the same profile shape.

## Requirements and success checks

| # | Requirement | How it is checked |
| --- | --- | --- |
| 1 | A newcomer can install and run mac-setup from this README alone on a fresh Mac. | A pass from a new clone on a real Mac finds 0 gaps. |
| 2 | A second run changes nothing. | Every journey's second run shows `changed=0 failed=0`. |
| 3 | A profile mistake changes nothing. | Each deliberately broken test profile stops with exit 2 and 0 settings written. |
| 4 | A refused setting does not stop the others. | A test seeds a wrong stored type, and the run ends with exit 2, one line for that setting, and the other settings written. |
| 5 | Each account gets its own settings. | Settings written for a test account read back as that account and leave the running account unchanged. |
| 6 | No password is ever stored or logged. | A test run with a random password finds 0 copies of it in the logs and the evidence folder. |
| 7 | No machine or account name is tracked in git. | The gate scans tracked files against a private word list kept outside git. |
| 8 | Every researched setting is listed. | A test checks every entry in the reference, and checks that the template lists each one. |
| 9 | A full-size profile runs fast enough to iterate. | Measured once on the target Mac, a 170-setting test profile runs its preview and its second apply in under 60 s each. |

## Principles

1. **The profile is the source of truth.** A run makes the Mac match the profile. There is no hidden state and no receipt file.
2. **Check before writing.** The whole profile is checked before the first change. A bad profile changes nothing.
3. **Admin work sits in one place.** The playbook has two plays. Only the second one uses admin rights, so a reader can see every admin step in one place.
4. **Small, data-driven code.** One role writes every simple preference from the reference. A custom module exists only where no existing module can do the job, reads before it writes, and supports `--check`.
5. **Pinned and checked downloads.** Every download has a fixed version and a SHA-256 hash, and is at least 21 days old when pinned.
6. **Private data stays local.** Real machine names, account names and profiles live in `.local/`, which git ignores. Passwords are typed at a prompt and never written to a file.
7. **Proven on the real Mac.** A setting counts as proven on a macOS version only after it was applied and read back on a Mac running that version.

## How it works

### What runs

Nothing runs in the background. You start `ansible-playbook` in Terminal, it runs on the same Mac (a local connection), and it exits when done. Ansible starts a short-lived Python process for each step. Admin steps run through `sudo`, using the password you typed at the start of the run.

### The parts

1. **The settings reference**, `settings/macos/*.yml`, one file per area. Each entry describes one setting with what it does, its allowed values, whether it belongs to an account or the machine, how it is stored, when it takes effect, and anything a person must do by hand.
2. **The profile**, your copy of `inventory/example/` in `.local/inventory/`. It holds `default_settings` (for every account), an `accounts` list (each with optional settings of its own), and `machine_settings`. It holds no password.
3. **The playbook**, `playbooks/site.yml`. Play 1 runs as you, checks the profile, and sets your account's settings. Play 2 runs with admin rights and creates accounts, sets machine settings, and sets every other account's settings as that account.
4. **The click-through**, `playbooks/by-hand.yml`, for settings only a person can change.
5. **The template**, `inventory/example/TEMPLATE.yml`, generated from the reference by `scripts/render-template`.

### Install

You need an Apple Silicon Mac, because every pinned download is an arm64 build. You also need Apple's Command Line Tools and a GitHub SSH key that can read this repository. The install needs no admin password.

1. Check for the Command Line Tools. If this prints an error, run `xcode-select --install` and click Install.

   ```sh
   xcode-select -p
   ```

2. Clone the repository and run the bootstrap.

   ```sh
   git clone git@github.com:amrendra18/mac-setup.git ~/mac-setup
   cd ~/mac-setup
   scripts/bootstrap
   ```

The bootstrap prints `OK` and a file name for each of its 11 pinned downloads. They are uv, 9 Python packages and one Ansible collection. uv prints its own lines for Python. The bootstrap then prints `ansible-playbook [core 2.21.5]` and the next two commands.

Run `scripts/bootstrap` again at any time. A second run downloads nothing and changes nothing. On a macOS 15.6.1 virtual machine, a first run took 11.2 s and a second run 1.1 s.

If a download does not match its pinned SHA-256 hash, the bootstrap deletes that file, names it, and exits with code 1. A network failure also stops the bootstrap with a non-zero code. In both cases, run `scripts/bootstrap` again.

### Hosts the install contacts

| # | Host | What comes from it |
| --- | --- | --- |
| 1 | `github.com` | This repository (`git clone`) and uv 0.13.4 |
| 2 | `release-assets.githubusercontent.com` | GitHub's file host. `github.com` redirects the uv download here. |
| 3 | `releases.astral.sh` | Python 3.13.15 (build 20260929). uv downloads it and checks its hash. |
| 4 | `files.pythonhosted.org` | The 9 Python packages pinned in `requirements/ansible.txt`, from the direct links in `requirements/ansible-urls.txt` |
| 5 | `galaxy.ansible.com` | The community.general 13.5.0 collection pinned in `requirements/collections.txt` |
| 6 | `ansible-galaxy-ng.s3.dualstack.us-east-1.amazonaws.com` | Galaxy's file host. `galaxy.ansible.com` redirects the collection download here. |

Every download uses HTTPS. The bootstrap never reads the package index at `pypi.org`. Running the playbook contacts no host.

### Folders the install writes

| # | Place | What it holds | Size measured |
| --- | --- | --- | --- |
| 1 | `.local/uv/` | uv 0.13.4 | 27,232 KiB |
| 2 | `.local/downloads/` | The uv and collection archives | 16,840 KiB |
| 3 | `.local/wheels/` | The 9 Python package files | 6,920 KiB |
| 4 | `.local/venv/` | The Python virtual environment with Ansible | 49,636 KiB |
| 5 | `.local/collections/` | The community.general collection | 26,292 KiB |
| 6 | `.local/ansible-home/` | Ansible's own settings and temporary files | 4 KiB |
| 7 | `.local/logs/` | `ansible.log`, the log of every run | 4 KiB |
| 8 | `~/.local/share/uv/python/` | Python 3.13.15 in `cpython-3.13.15-macos-aarch64-none`, the link `cpython-3.13-macos-aarch64-none`, and uv's files `.gitignore`, `.lock` and `.temp` | 72,520 KiB |

Rows 1 to 7 sit inside the checkout, and git ignores `.local/`. Your profile goes in `.local/inventory/` and test evidence in `.local/verify/`, in the same ignored folder.

Nothing else in your home folder changes. uv adds no link in `~/.local/bin` and keeps no cache or settings. The checkout's `ansible.cfg` keeps Ansible's files in `.local/`, so no `~/.ansible` folder appears.

### Removing mac-setup

Removing mac-setup leaves your Mac's settings as they are. Deleting the checkout also deletes your profile in `.local/inventory/`, so copy it elsewhere first if you want to keep it.

```sh
cd ~/mac-setup
.local/uv/uv python uninstall 3.13.15 --no-config
cd ~ && rm -rf ~/mac-setup
```

The second line removes Python 3.13.15 and its `cpython-3.13` link from `~/.local/share/uv/python`. If another tool on your Mac uses that same Python, skip that line. uv leaves its three small files in that folder. If nothing else on your Mac uses uv, `rm -rf ~/.local/share/uv` removes them as well.

### Checks for contributors

`scripts/check fast` runs the quick checks (YAML lint, playbook syntax, unit tests, the profile checks against broken test profiles, the structure rules, the private-word scan and a secret scan). `scripts/check full` adds the reference test, which also checks that the template matches the reference. `scripts/verify` proves features on a real Mac and keeps evidence in `.local/verify/`.

Commits and pushes run pre-commit hooks from [first-commit](https://github.com/amrendra18/first-commit), pinned in `.pre-commit-config.yaml`. They check signing, secrets and private values. In a new clone, install pre-commit (`pipx install --python python3.13 pre-commit` or `brew install pre-commit`), then run:

```sh
pre-commit install
pre-commit run first-commit-setup --hook-stage manual
```

The second command sets the clone's Git identity and SSH signing and installs the Gitleaks scanner. Check a clone with `pre-commit run first-commit-check --hook-stage manual --all-files`.

## Plan

| # | Milestone | Done when |
| --- | --- | --- |
| 1 | M0: install from this README and one visible Dock change | A fresh clone installs, one run puts the Dock on the left at size 32 with recents hidden, and a second run shows `changed=0`. |
| 2 | M1: each account gets its own settings | A test account is created and gets its own settings; existing accounts are adopted with 0 changes; a refused setting no longer stops the run; a 170-setting profile is timed. |
| 3 | M2: the full settings reference and the template | All researched settings load, and the template lists each one. |
| 4 | M3: account settings by area | Dock, Finder, input, system UI and privacy preferences apply and read back. |
| 5 | M4: machine settings | Admin settings and command-line tools apply and read back. |
| 6 | M5: guided click-through | Each click-only page opens with what to choose and 0 preference writes. |
| 7 | M6: Dock apps and a black desktop | The Dock holds only the listed apps, and the desktop is black. |
| 8 | M7: the remaining settings | Every setting has a way to apply it or a written reason why none is safe. |
| 9 | M8: the new-Mac journey | A fresh admin account follows this README end to end with one command. |
| 10 | M9: the next items | Apps, dotfiles, FileVault, MDM, undo and SSH runs, each planned and approved first. |

## License

MIT, see `LICENSE`.
