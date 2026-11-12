# mac-setup

Status: milestone M2. `scripts/bootstrap` installs mac-setup. The settings reference in `settings/macos/` describes 288 settings. The playbooks wire 7 of its routes: preferences (`defaults`), command tools (`command`), Dock apps (`dock_apps`), the desktop picture (`wallpaper`), the Finder sidebar (`finder_sidebar`), app installs (`apps`) and the click-through (`by_hand`). The 36 settings on route `not_yet` have no way to apply them yet. Today a run does these things:

1. It creates the accounts the profile lists.
2. It writes account settings that macOS keeps as preferences, for the account that runs it and for each listed account.
3. It writes machine preferences as root, and sets machine settings through command tools such as `pmset` and `systemsetup`.
4. It installs the apps the profile lists from the catalog in `apps/macos.yml`, after it checks each download.
5. It sets each account's Dock apps, a solid black desktop, and the Finder sidebar's favorites and hidden items.
6. `playbooks/by-hand.yml` walks you through the click-only settings in the profile.

39 settings are proven on macOS 27.2.1: 17 Dock, hot corner, Mission Control, Stage Manager, widget and window settings, 6 Finder settings, 13 appearance, menu bar, notification, Siri and Spotlight settings, the Dock apps, the black desktop and Wi-Fi power. Every other setting on a wired route is built and unit-tested but not yet proven on a Mac. The Plan section names the milestone that proves it.

mac-setup sets up a Mac from one profile file. You write down the settings you want once, for the Mac and for each account on it. One command then applies them, and running it again changes nothing.

Privacy permissions (camera, microphone, screen recording, full disk access and similar) cannot be set by any script. Apple lets only a person grant them. For these, mac-setup opens the exact System Settings page, tells you what to choose, and waits while you click.

## Who it is for

mac-setup is for the person who owns a Mac and sets it up, usually right after a fresh install, when only the first admin account exists. That person may also want other accounts on the same Mac, admin or standard, each with its own look.

Today that person clicks through System Settings by hand, once per Mac and once per account, and has no record of what they chose. mac-setup replaces the clicking with a file they can read, review and reuse.

## What a run looks like

This section describes the first release. Today a run applies every kind of setting the Status line lists, and 39 settings are proven. The Install section below gives the steps that work today.

### First run on a fresh Mac

1. You sign in to the Mac's first admin account and open Terminal.
2. You check that Apple's Command Line Tools are installed with `xcode-select -p`. If they are missing, `xcode-select --install` opens a dialog, and you click Install. The tools provide `git` and `curl`.
3. You clone this repository and run `scripts/bootstrap`. It downloads pinned copies of uv (a Python installer), Python 3.13.15, Ansible and one Ansible collection, checks each against a SHA-256 hash, and installs them inside the checkout and your home folder. It needs no admin password.
4. You copy the example profile to the private folder `.local/inventory/` with `test -d .local/inventory || cp -R inventory/example .local/inventory`, then edit the account names. The `test -d` part skips the copy when the folder exists, so running the line again keeps your edits and adds no nested copy.
5. You run `.local/venv/bin/ansible-playbook playbooks/site.yml -K`.
6. Ansible asks for your admin password once (`BECOME password:`). For each account in the profile that does not exist yet, it asks that account's new password once, with typing hidden.
7. You watch the settings change. The Dock moves, Finder changes, and so on. Each line of output names a setting and whether it changed.
8. The run ends with a short summary. It says how many settings show only after you log out and back in, how many need a click (run `playbooks/by-hand.yml` for those), which settings macOS refused and why, and which new accounts lack a secure token. The recap line shows `failed=0` when everything worked.

### Running it again

Run the same command. Settings that already match print as `ok` and the recap shows `changed=0`. No account password is asked, because the accounts exist. If you changed a setting by hand in System Settings, the profile wins and the run sets it back, reported as `changed`.

### Previewing a change

Add `--check` to see what would change without changing any setting. When the profile lists a Finder sidebar setting, the preview still builds the sidebar helper into `.local/finder-sidebar/`, because it needs the helper to read the sidebar. Each setting that would change prints as `changed` with its new value. The preview does not print the old value. To see a current value, run `defaults read DOMAIN KEY` with the domain and key from the settings reference.

### Other accounts

Each account in the profile gets its own settings. mac-setup writes them as that account, so they land in that account's own preferences. They take effect the next time that person signs in. mac-setup adopts an account that exists already and keeps its password and admin rights unchanged.

mac-setup never deletes an account. To remove one, use System Settings > Users & Groups in an admin account.

### Click-only settings

List click-only settings in the profile like any other, for example `network.bluetooth_power: false` under `machine_settings`. Then run `.local/venv/bin/ansible-playbook playbooks/by-hand.yml` while you sit at the Mac. For each click-only setting of your account and of the machine, it opens the System Settings page, prints what to choose (for example "Turn Bluetooth on or off: off"), and waits until you press Return. It changes nothing itself. Add `-e by_hand_list=true` to list the pages without opening them. Another account's click-only settings appear when that person runs the playbook while signed in.

### Changing a setting back

Edit the profile and run again. Write the old value, or write `unset` to return the setting to the macOS default. Deleting a line only stops mac-setup managing that setting; it leaves the Mac as it is. The log of every run sits in `.local/logs/`.

### When something goes wrong

1. A mistake in the profile (an unknown setting, a value that is not allowed, a machine setting placed under an account) stops the run before anything changes. Exit code 2 and one line per problem tell you what to fix.
2. A setting macOS refuses at write time, for example because a stored value has another type, does not stop the run. The other settings still apply, the Dock still restarts if needed, and the summary names each refused setting. The run then ends with exit code 2.
3. A run from an account without admin rights, or without `-K`, fails at the machine and other-accounts part. Your own account's settings still apply first. The summary names each setting it could not set there with "needs admin rights: run with -K".
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

Homebrew, dotfiles, turning FileVault on (with secure tokens for the accounts mac-setup creates), MDM profiles, an undo command, and running mac-setup from another machine over SSH.

### Not planned

1. Granting privacy permissions by script, editing the privacy database, or working around a consent prompt.
2. Deleting accounts or apps. The one exception: an app upgrade replaces the old copy of that app.
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
5. **Pinned and checked downloads.** Every download has a fixed version and a SHA-256 hash, and is at least 21 days old when pinned. The one exception is an app whose vendor publishes only a latest download, such as Google Chrome: it has no fixed hash or age, and every other check, the vendor's signature and VirusTotal included, still applies.
6. **Private data stays local.** Real machine names, account names and profiles live in `.local/`, which git ignores. Passwords are typed at a prompt and never written to a file.
7. **Proven on the real Mac.** A setting counts as proven on a macOS version only after it was applied and read back on a Mac running that version.

## How it works

### What runs

Nothing runs in the background. You start `ansible-playbook` in Terminal, it runs on the same Mac (a local connection), and it exits when done. Ansible starts a short-lived Python process for each step. Admin steps run through `sudo`, using the password you typed at the start of the run.

### The parts

1. **The settings reference**, `settings/macos/*.yml`, one file per area. Each entry describes one setting with what it does, its allowed values, whether it belongs to an account or the machine, how it is stored, when it takes effect, and anything a person must do by hand.
2. **The profile**, your copy of `inventory/example/` in `.local/inventory/`. It holds `default_settings` (for every account), an `accounts` list (each with optional settings of its own), and `machine_settings`. It holds no password. A profile may list several Macs, each entry's `hosts.yml` key and `host_vars/` folder named after that Mac's local hostname. A run applies only the entry named after the Mac it runs on, and stops before any change when no entry matches.
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

The bootstrap prints `OK` and a file name for each of its 11 pinned downloads. They are uv, 9 Python packages and one Ansible collection. uv prints its own lines for Python. The bootstrap then prints `ansible-playbook [core 2.21.5]` and the copy, preview and apply commands from steps 3 and 4 below.

Run `scripts/bootstrap` again at any time. A second run downloads nothing and changes nothing. On a macOS 15.6.1 virtual machine, a first run took 11.2 s and a second run 1.1 s. On a Mac mini with macOS 27.2.1, a first run took 11 s.

If a download does not match its pinned SHA-256 hash, the bootstrap deletes that file, names it, and exits with code 1. A network failure also stops the bootstrap with a non-zero code. In both cases, run `scripts/bootstrap` again.

3. Copy the example profile to the private folder `.local/inventory/`. The `test -d` part skips the copy when the folder exists, so running the line again keeps your edits.

   ```sh
   test -d .local/inventory || cp -R inventory/example .local/inventory
   ```

   You can keep the profile outside the checkout instead, in `~/.dot/mac-setup/inventory/`. Copy the example there, and add `-i ~/.dot/mac-setup/inventory` to every `ansible-playbook` command:

   ```sh
   test -d ~/.dot/mac-setup/inventory || { mkdir -p ~/.dot/mac-setup && cp -R inventory/example ~/.dot/mac-setup/inventory; }
   ```

   The profile is `.local/inventory/host_vars/example-mac/accounts.yml`, or the same path under `~/.dot/mac-setup/inventory/`. Its `default_settings` hold the settings for every account, and `machine.yml` beside it holds the machine settings. Set `time.zone` in `machine.yml` to your own zone first; the example uses `Etc/UTC`. Change a value there if you want another one. For example, `dock.position: bottom` moves the Dock to the bottom. `inventory/example/TEMPLATE.yml` lists every setting with the values it takes. The value `unset` deletes a setting stored as a preference, so macOS uses its own default.

   Quote a value such as `"on"`, `"off"`, `"yes"` or `"no"`. Without quotes, YAML reads it as true or false, and the profile check refuses it with a line that says to quote it.

   Some settings need more than a preference line. Each example below goes under `default_settings` or an account's `settings`, except the last, which goes under `machine_settings`:

   ```yaml
   dock.applications: [com.apple.Terminal, com.apple.Safari]
   desktop.wallpaper: black
   finder.sidebar_favorites: [/Applications, "~", ~/Desktop]
   finder.sidebar_favorites_only: true
   finder.sidebar_hide: [recents, airdrop]
   # under machine_settings:
   power.display_sleep_minutes: 10
   ```

   `dock.applications` keeps only the listed apps, by bundle id and in order. An app that the same run installs joins the Dock on the next run, because the Dock is set before the installs. `desktop.wallpaper` needs the account signed in at the screen. For another account, the summary says its desktop picture is set at its next run while it is signed in. `finder.sidebar_favorites` puts the listed folders first in Finder's sidebar, and `"~"` means each account's home folder. The first run that sets the sidebar compiles a small helper with the Command Line Tools into `.local/finder-sidebar/`. To add Desktop, Documents or Downloads, the helper first lists the folder, so macOS asks once whether Terminal may use it. Click Allow, and the folder is added; later runs do not ask again. If you click Don't Allow, the run names the folder as refused.

   Its `accounts` list is empty. To create a standard account, or to give an existing one its own settings, add an entry:

   ```yaml
   accounts:
     - name: sam
       full_name: Sam Example
       admin: false
       settings:
         dock.position: bottom
   ```

   `settings` is optional. It adds to or overrides `default_settings` for that account only. An account that exists already is adopted: its password and admin rights stay as they are, and a different `admin` value is reported as refused.

4. Preview the change, apply it, and apply it again.

   ```sh
   .local/venv/bin/ansible-playbook playbooks/site.yml -K --check
   .local/venv/bin/ansible-playbook playbooks/site.yml -K
   .local/venv/bin/ansible-playbook playbooks/site.yml -K
   ```

   `-K` makes Ansible ask for your admin password once (`BECOME password:`). The run's second part uses it to create accounts, to write machine settings, and to write other accounts' settings as those accounts. For each listed account that does not exist yet, the apply also asks that account's new password once, with typing hidden. The preview creates nothing and asks no account password.

   With the example profile as copied, the preview lists each setting that differs from your Mac and writes nothing. The first apply sets them and restarts the Dock and Finder once, and its summary says how many settings show only after you log out and back in, and how many need a click in System Settings (run `playbooks/by-hand.yml` for those). The second apply ends with `changed=0`.

   When another account, or the login window, owns the screen, the run skips the Dock restart. A restart then would leave that account's Dock stopped until it logs out and back in. The Dock then changes at that account's next sign-in.

   Without step 3's copy there is no profile. The run then warns that no host matched, changes nothing and still exits 0.

   Each new account adds 1 to the apply's `changed` count, and each setting written for another account adds 1 more. Those settings show at that account's next sign-in. A new account has no secure token, so with FileVault on it cannot unlock the Mac at startup; the run's summary names each such account.

   A setting macOS refuses at write time, such as a stored value of another type, does not stop the others. The run's last task then fails with one `refused` line per setting, and the run ends with exit code 2.

### Apps

mac-setup installs apps from a small catalog, `apps/macos.yml`. Each entry names the vendor's download URL and the only host it may come from. An entry may name one more host that a redirect may go to, such as `release-assets.githubusercontent.com`, GitHub's file host. It also names the pinned SHA-256 hash, the vendor's release date, the vendor's Apple Team ID (the ID of the developer certificate that signs the app), the bundle id and the lowest version accepted. Today the catalog holds `google_chrome`, `sublime_text`, `little_snitch`, `proton_pass`, `ente_auth`, `utm`, `firefox` and `micro_snitch`. `ente_auth` is Ente's two-factor code app. To install some of them, list their ids under `machine_settings`:

```yaml
machine_settings:
  apps.install: [sublime_text, google_chrome]
```

The profile check refuses an id that is not in the catalog, and an id listed twice, with one line each.

An app already in `/Applications` at the catalog's version or newer is left alone, and nothing is downloaded. Otherwise the run checks each app in this order, and the first failed check refuses it:

1. **Catalog entry.** Every field is present and well formed.
2. **Age.** A pinned build must be at least 21 days old by its release date.
3. **HTTPS and host.** The URL uses HTTPS on the entry's host. Every redirect uses HTTPS on that host or on the entry's one redirect host.
4. **SHA-256.** The download must match the pinned hash. Downloads are kept in `.local/downloads/` by hash, so a second run downloads nothing.
5. **VirusTotal.** The run sends only the file's SHA-256 to `www.virustotal.com`, never the file. Any engine that reports the file as malicious or suspicious refuses it. So does a file VirusTotal does not know, a file no engine has scanned, and a missing key.
6. **Unpack.** A disk image is mounted read-only and hidden from Finder. A zip is unpacked with `ditto`. The app must be inside.
7. **Signature.** `codesign --verify --deep --strict` must pass.
8. **Gatekeeper.** `spctl --assess` must pass, which also confirms that Apple notarized the build.
9. **Team ID.** The signing Team ID must equal the catalog's.
10. **Bundle id and version.** Both must match the catalog, and the version must be at least the catalog's.

The install fails closed. A refused app is not copied, the other apps still install, and the run ends with one `refused` line per app and exit code 2. A preview with `--check` runs every check, so it also downloads and asks VirusTotal, and it installs nothing.

Google serves only its latest Chrome build, so `google_chrome` has no pinned hash and no age check. The signature, Gatekeeper, Team ID and VirusTotal checks still apply. Chrome updates itself after the install. Little Snitch needs a license, and a one-time approval of its network filter in System Settings, which only a person can give. Micro Snitch needs a license. Proton Pass and Ente Auth each need a sign-in to the vendor's account. A UTM virtual machine may ask a person to allow access the first time it runs. Firefox updates itself after the install.

VirusTotal's verdict for each file is stored in `.local/downloads/SHA256.virustotal.json`, where SHA256 is the file's hash. A later run reads the stored verdict and asks VirusTotal nothing, so it needs no key. A stored `flagged` verdict keeps refusing that file. The run makes at most 4 lookups per minute, the free API key's limit, and waits when it reaches that rate.

An upgrade replaces the old copy whole. The run copies the new app next to the old one, swaps the two, and then deletes the old copy. mac-setup deletes no other app, and never touches Apple's own apps.

An app install contacts the catalog entry's host (`dl.google.com`, `download.sublimetext.com`, `www.obdev.at`, `proton.me`, `github.com` or `archive.mozilla.org`), its redirect host (`release-assets.githubusercontent.com` for `ente_auth` and `utm`), and `www.virustotal.com`. `scripts/app-catalog ID` prints a vendor's current version, URL and SHA-256 for review; it never edits the catalog.

### Secrets

The VirusTotal API key is the one secret mac-setup reads. Keep it in an encrypted Ansible vault file in your private profile, and never in the repository. A free VirusTotal account gives you a key.

1. Make the vault password file. The first line makes the folder, and the second writes a random password readable only by you. Keep a copy of the password in your password manager; without it, nobody can open the vault file.

   ```sh
   mkdir -p ~/.dot/mac-setup && chmod 700 ~/.dot/mac-setup
   (umask 077 && openssl rand -hex 32 > ~/.dot/mac-setup/vault-password)
   ```

2. Create the vault file in your profile's `group_vars/all/` folder, so every machine in the profile can read it. Use the profile folder you chose in Install step 3, here `.local/inventory/`. Its name ends in `.vault.yml`, which marks it as encrypted. The command opens an editor; add one line, `virustotal_api_key: YOUR_KEY`, then save and close.

   ```sh
   mkdir -p .local/inventory/group_vars/all
   .local/venv/bin/ansible-vault create .local/inventory/group_vars/all/secrets.vault.yml
   ```

3. Run the playbook as before. `ansible.cfg` names `scripts/vault-password`, which prints the password from `~/.dot/mac-setup/vault-password`, so nothing is typed at run time. Set `DOT_HOME` to use another folder than `~/.dot`.

`scripts/vault-password` uses the file only when you own it and its mode is 600. Otherwise it prints a warning, and the vault file cannot be opened. Without the password file, a run that reads no vault file works as before, and a run that needs the key stops with `Decryption failed`. Run `.local/venv/bin/ansible-vault edit` with the same path to change the key.

### Hosts the install contacts

| # | Host | What comes from it |
| --- | --- | --- |
| 1 | `github.com` | This repository (`git clone`) and uv 0.13.4 |
| 2 | `release-assets.githubusercontent.com` | GitHub's file host. `github.com` redirects the uv download here. |
| 3 | `releases.astral.sh` | Python 3.13.15 (build 20260929). uv downloads it and checks its hash. |
| 4 | `files.pythonhosted.org` | The 9 Python packages pinned in `requirements/ansible.txt`, from the direct links in `requirements/ansible-urls.txt` |
| 5 | `galaxy.ansible.com` | The community.general 13.5.0 collection pinned in `requirements/collections.txt` |
| 6 | `ansible-galaxy-ng.s3.dualstack.us-east-1.amazonaws.com` | Galaxy's file host. `galaxy.ansible.com` redirects the collection download here. |

Every download uses HTTPS. The bootstrap never reads the package index at `pypi.org`. Running the playbook contacts no host, unless the profile lists apps. The Apps section names the hosts an app install contacts.

### Folders the install writes

| # | Place | What it holds | Size measured |
| --- | --- | --- | --- |
| 1 | `.local/uv/` | uv 0.13.4 | 27,232 KiB |
| 2 | `.local/downloads/` | The uv and collection archives. A run that installs apps adds each app's download and its VirusTotal verdict here, owned by root. | 16,840 KiB before any app |
| 3 | `.local/wheels/` | The 9 Python package files | 6,920 KiB |
| 4 | `.local/venv/` | The Python virtual environment with Ansible | 49,636 KiB |
| 5 | `.local/collections/` | The community.general collection | 26,292 KiB |
| 6 | `.local/ansible-home/` | Ansible's own settings and temporary files | 4 KiB |
| 7 | `.local/logs/` | `ansible.log`, the log of every run | 4 KiB |
| 8 | `.local/finder-sidebar/` | The compiled Finder sidebar helper, written by the first run or preview that includes a sidebar setting | not measured |
| 9 | `~/.local/share/uv/python/` | Python 3.13.15 in `cpython-3.13.15-macos-aarch64-none`, the link `cpython-3.13-macos-aarch64-none`, and uv's files `.gitignore`, `.lock` and `.temp` | 72,520 KiB |

Rows 1 to 8 sit inside the checkout, and git ignores `.local/`. Your profile goes in `.local/inventory/` and test evidence in `.local/verify/`, in the same ignored folder.

Nothing else in your home folder changes. uv adds no link in `~/.local/bin` and keeps no cache or settings. The checkout's `ansible.cfg` keeps Ansible's files in `.local/`, so no `~/.ansible` folder appears.

### Removing mac-setup

Removing mac-setup leaves your Mac's settings and the apps it installed as they are. Deleting the checkout also deletes your profile in `.local/inventory/`, so copy it elsewhere first if you want to keep it.

```sh
cd ~/mac-setup
.local/uv/uv python uninstall 3.13.15 --no-config
cd ~ && rm -rf ~/mac-setup
```

The second line removes Python 3.13.15 and its `cpython-3.13` link from `~/.local/share/uv/python`. If another tool on your Mac uses that same Python, skip that line. uv leaves its three small files in that folder. If nothing else on your Mac uses uv, `rm -rf ~/.local/share/uv` removes them as well.

### Checks for contributors

`scripts/check fast` runs the quick checks (YAML lint, playbook syntax, unit tests, the profile checks against broken test profiles, the structure rules, the private-word scan and a secret scan). `scripts/check full` adds the reference test, which also checks that the template matches the reference, a `black --check` format check of the Python files, and ansible-lint. `scripts/verify` proves features on a real Mac and keeps evidence in `.local/verify/`. The maintainer refreshes the example profile from their own profile with `scripts/sync-example`, which keeps personal values out and stops on a free-text setting it has no neutral value for.

Besides `scripts/bootstrap`, the checks need three things:

1. The dev tools pinned in `requirements/dev.txt` (yamllint, ansible-lint, black and their dependencies) in `.local/venv`. These two commands download each pinned file from its direct link in `requirements/dev-urls.txt`, check its SHA-256 hash, and install it without the PyPI index:

   ```sh
   mkdir -p .local/wheels && (cd .local/wheels && xargs -n 1 curl --fail --silent --show-error --location --remote-name <../../requirements/dev-urls.txt)
   .local/venv/bin/python -I -m pip install --isolated --no-index --find-links .local/wheels --no-deps --require-hashes --only-binary :all: -r requirements/dev.txt
   ```

2. The Gitleaks secret scanner, in `.local/bin/gitleaks` or on your PATH. The `first-commit-setup` command below puts it in `.local/bin/`.
3. The file `.local/private-words.txt`, with one private word per line, such as your machine and account names. Git ignores it. The private-word scan fails when a file in the repository contains one of these words.

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
