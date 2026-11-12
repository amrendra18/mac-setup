# mac-setup

mac-setup sets up a Mac from one file that you write, usually right after a fresh macOS install. In that file you list the settings you want for the Mac and for each account on it, and the apps to install. One command makes the Mac match the file, and running the command again changes nothing.

mac-setup is built on Ansible, a free tool that applies a list of settings to a computer. You do not need to know Ansible. The steps below install a private copy of it in the mac-setup folder, and Python in `~/.local/share/uv/python/`.

## What you get after one run

The example file that ships with mac-setup makes these main changes, among others. You can keep, change, or remove each one.

1. **Dock.** It sits on the left at icon size 32, holds only Terminal, and hides the section of recent apps.
2. **Desktop.** A solid black desktop picture.
3. **Finder.** List view, hidden files shown, and a sidebar with Applications, your home folder, Desktop and Downloads. Recents, Shared, AirDrop, Trash and iCloud leave the sidebar.
4. **Look and menu bar.** Dark mode with an orange accent, and a 24-hour clock with seconds.
5. **Machine settings.** Firewall on and blocking all incoming connections, with stealth mode on and no app allowed in automatically, Wi-Fi on, display sleep after 10 minutes, system sleep after 30 minutes, and the time zone you choose.
6. **Accounts.** mac-setup creates each extra account you list, admin or standard, with its own settings. The example lists none.
7. **Apps.** Apps you pick from a short list, such as Firefox or Sublime Text, each checked before it is copied to `/Applications`. The example installs none.
8. **A guided walk through the clicks.** Apple lets only a person grant privacy permissions, such as camera or screen recording. For those settings, mac-setup opens the right System Settings page and tells you what to choose. The example turns Location Services off this way.

mac-setup knows 288 macOS settings. A run sets 218 of them, 40 need your click, and 30 it cannot set yet. A setting counts as proven on a macOS version once a test applied it on a real Mac and read it back. 66 settings are proven on macOS 27.2.1:

1. 17 Dock, hot corner, Mission Control, Stage Manager, widget and window settings.
2. 6 Finder settings.
3. 13 appearance, menu bar, notification, Siri and Spotlight settings.
4. The Dock apps, the black desktop, Wi-Fi power, and 4 power settings: system sleep, display sleep, wake for network and restart after a power failure.
5. 7 keyboard, trackpad and sound settings: keyboard and Mission Control shortcuts, input sources, 2 trackpad gestures, and the output and alert volumes.
6. 15 input settings and 1 privacy setting: text auto-capitalization and double-space period, the Input menu, trackpad and mouse clicks, taps, drags and gestures, and personalized ads.

The code for the other settings has unit tests, but no test has applied them on a real Mac yet.

## Quick start

You need an Apple Silicon Mac, because every pinned download is an arm64 build. Sign in to an admin account. The bootstrap itself needs no admin password.

You also need a GitHub SSH key that can read this repository. Follow GitHub's guides "Generating a new SSH key" and "Adding a new SSH key to your GitHub account". Then test the key with `ssh -T git@github.com`. It works when it prints "You've successfully authenticated", even though it exits 1.

1. Open Terminal and check for Apple's Command Line Tools, which provide `git`. If this prints an error, run `xcode-select --install` and click Install.

   ```sh
   xcode-select -p
   ```

2. Download mac-setup and install its tools.

   ```sh
   git clone git@github.com:amrendra18/mac-setup.git ~/mac-setup
   cd ~/mac-setup
   scripts/bootstrap
   ```

   The bootstrap downloads fixed versions of Python, Ansible and their parts, and checks each download against a known fingerprint (its SHA-256 hash). It prints `OK` and a file name for each of its 11 downloads, and uv prints its own lines for Python. It ends with the Ansible version, `ansible-playbook [core 2.21.5]`, and the commands for steps 3 to 5. If a download does not match, the bootstrap deletes it, names it, and exits with code 1. A network failure also stops it. In both cases, run `scripts/bootstrap` again. A second run downloads nothing and changes nothing. `scripts/bootstrap --help` prints its usage and runs nothing.

3. Copy the example profile to your private folder. The profile is the set of files that hold your settings. Git ignores the `.local/` folder, so your profile stays on your Mac. The `test -d` part skips the copy when the folder exists, so running the line again keeps your edits.

   ```sh
   test -d .local/inventory || cp -R inventory/example .local/inventory
   ```

   Then open `.local/inventory/host_vars/example-mac/machine.yml` with `open -e` (TextEdit). Set `time.zone` to your own zone, such as `Europe/Berlin`. The example uses `Etc/UTC`. To see your Mac's current zone, run `readlink /etc/localtime`. The zone is the part after `zoneinfo/`. macOS changes the zone only for an app with Full Disk Access, so step 5 asks you to turn it on for Terminal.

   The example also turns on the firewall's "Block all incoming connections", which refuses SSH logins too. If you set up or reach this Mac over SSH, delete both the `security.firewall_block_all` line and the `allow_lockout_risk: true` line.

4. Preview the changes. Your settings stay as they are.

   ```sh
   .local/venv/bin/ansible-playbook playbooks/site.yml -K --check
   ```

   `-K` makes Ansible ask once for your admin password, shown as `BECOME password:`. Each setting that would change prints a `changed` line. The preview creates no account and asks no account password. It asks for no permission either, and lists what step 5 would ask for.

5. Apply the changes, then run the same command once more.

   ```sh
   .local/venv/bin/ansible-playbook playbooks/site.yml -K
   ```

   Before any change, the run asks for the permissions your settings need. Only you can turn them on.

   1. **Full Disk Access for Terminal**, which the time zone needs. The run opens the Full Disk Access page, asks you to turn on Terminal, and waits until you press Return. Full Disk Access lets every command in Terminal read every file. So at the end the run opens the page again and asks you to turn Terminal off. If Terminal had it before the run, the run leaves it on. If a step fails, the run still asks you to turn Terminal off before it ends. If you stop the run with Control-C, turn Terminal off there yourself.
   2. **Desktop and Downloads access**, which the Finder sidebar needs. macOS asks whether Terminal may use each folder. Click Allow. Terminal keeps this access.

   If a permission stays off, the run skips each setting that needs it and would change, and names each one in the summary. Over SSH, the run asks for nothing, because nobody at the screen can answer. It skips the settings that need Full Disk Access, such as the time zone, and lists the permissions it would ask for.

   You see the Dock and Finder change. The run ends with a short summary. It says how many settings show only after you log out and back in, and how many need a click in System Settings. It also counts the settings whose timing is not measured, which may need a logout or an app restart to show. The last line, called the recap, shows `failed=0` when everything worked. In the second run, every setting prints `ok` and the recap shows `changed=0`, because the Mac already matches. It asks no account password, because the accounts exist.

If the profile lists accounts that do not exist yet, the run asks each new account's password once, with typing hidden. mac-setup never stores a password.

### If a run stops or refuses a setting

1. A mistake in the profile, such as an unknown setting or a value that is not allowed, stops the run before anything changes. The run prints one line per problem and ends with exit code 2.
2. If macOS refuses one setting, the other settings still apply. The run ends with one `refused` line per setting and exit code 2.
3. Without `-K`, or from an account without admin rights, your own account's settings still apply first. The summary then names each machine or other-account setting it could not set, with "needs admin rights: run with -K".
4. A setting whose permission is off, and whose value would change, is skipped, as Quick start step 5 says. The summary names it in one `skipped` line, and the run still ends with exit code 0.
5. If you skipped step 3, there is no profile. The run warns that no host (Ansible's word for a Mac) matched, changes nothing, and exits 0.

Every run writes its log to `.local/logs/ansible.log`.

## Choose your settings

Your settings live in two files in `.local/inventory/host_vars/example-mac/`.

1. `accounts.yml` holds `default_settings`, the settings every account gets, and the `accounts` list.
2. `machine.yml` holds `machine_settings`, the settings for the whole Mac. These need your admin password.

The template, `inventory/example/TEMPLATE.yml`, lists all 288 settings in four sections: account settings, machine settings, "Click-only settings" and "Not yet supported". Each setting shows whether it belongs to an account or to the machine. The first three sections also give the allowed values, and the first two say when a setting takes effect. To use a setting, copy its line from the template into the same section of your profile, and remove the `# ` in front.

For example, this line in `default_settings` moves the Dock to the bottom of the screen:

```yaml
dock.position: bottom
```

Put quotes around the words `"on"`, `"off"`, `"yes"` and `"no"`. Without quotes, the file format (YAML) reads them as true or false, and the profile check asks you to quote them.

### Dock apps, desktop and Finder sidebar

These settings take a list or a word instead of a single preference:

```yaml
dock.applications: [com.apple.Terminal, com.apple.Safari]
desktop.wallpaper: black
finder.sidebar_favorites: [/Applications, "~", ~/Desktop]
finder.sidebar_favorites_only: true
finder.sidebar_hide: [recents, airdrop]
```

1. `dock.applications` keeps only the listed apps in the Dock, in that order. You name each app by its bundle id, the app's unique name such as `com.apple.Safari`. To find one, run `osascript -e 'id of app "Safari"'` with the app's name. An app that the same run installs joins the Dock on the next run.
2. `desktop.wallpaper` needs the account signed in at the screen. For another account, the summary says its picture is set at its next run while that account is signed in.
3. `finder.sidebar_favorites` puts the listed folders first in Finder's sidebar. `"~"` means each account's home folder. `finder.sidebar_favorites_only: true` also removes every favorite the list does not name. For each of Desktop, Documents and Downloads in the list, the run first asks you to click Allow, as Quick start step 5 says. Terminal keeps that right, so later runs do not ask again. You can take it back in System Settings > Privacy & Security > Files & Folders. If you click Don't Allow, the run skips every Finder sidebar setting and names each one in the summary.

### Other accounts

To create an account, or to give an existing account its own settings, add an entry to the `accounts` list in `accounts.yml`:

```yaml
accounts:
  - name: sam
    full_name: Sam Example
    admin: false
    settings:
      dock.position: bottom
```

`settings` is optional. It adds to or replaces `default_settings` for that account only. Those settings take effect the next time that person signs in. The volumes (`sound.output_volume` and `sound.alert_volume`) run only in an account's own session, so for another account the summary says that person must sign in and run mac-setup there.

If the account exists already, mac-setup keeps its password and admin rights as they are. A different `admin` value in the profile is reported as refused and changes nothing. mac-setup never deletes an account. To remove one, use System Settings > Users & Groups.

A new account has no secure token, the macOS right that lets an account unlock an encrypted disk at startup. So with FileVault on, that account cannot unlock the Mac. The run's summary names each such account.

### Settings only a person can click

The template lists these settings under "Click-only settings". List one in the profile like any other, for example `network.bluetooth_power: false` under `machine_settings`. Then, while you sit at the Mac, run:

```sh
.local/venv/bin/ansible-playbook playbooks/by-hand.yml
```

For each click-only setting of your account and of the machine, it opens the System Settings page, prints what to choose (such as "Turn Bluetooth on or off: off"), and waits until you press Return. It changes nothing itself. Add `-e by_hand_list=true` to list the pages without opening them. Another account's click-only settings appear when that person runs the playbook while signed in.

### Change a setting back

Write the old value in the profile and run again. For a setting stored as a preference, write `unset` to return it to the macOS default. The profile check refuses `unset` for any other setting. It also refuses `unset` for `input.keyboard_shortcuts` and `mission_control.shortcuts`, because it would reset every shortcut, and for `input.input_sources`, because it would leave the account with no input source. Deleting a line only stops mac-setup managing that setting, and leaves the Mac as it is.

If you change a setting by hand in System Settings, the next run sets it back to the profile's value. The exception is a trackpad choice that leaves shared swipe keys alone: `off` for `input.mission_control_gesture` and `input.app_expose_gesture`, `off` and `two_finger` for `input.swipe_between_pages`, and `off` and `four_finger` for `input.swipe_full_screen_apps`. A by-hand change to those keys stays.

A setting that could cut a remote connection, such as turning Remote Login off, is refused unless you allow it. To allow it, add the line `allow_lockout_risk: true` at the top of `machine.yml`, outside `machine_settings`.

### Keep the profile outside the checkout

You can keep the profile in `~/.dot/mac-setup/inventory/` instead of `.local/inventory/`. Copy the example there, then add `-i ~/.dot/mac-setup/inventory` to every `ansible-playbook` command.

```sh
test -d ~/.dot/mac-setup/inventory || { mkdir -p ~/.dot/mac-setup && cp -R inventory/example ~/.dot/mac-setup/inventory; }
```

### Several Macs in one profile

One profile can hold several Macs. Each Mac gets its own entry in `hosts.yml` and its own folder in `host_vars/`, both named after the Mac's local hostname, such as `office-mac`. A run reads this Mac's local hostname with `scutil --get LocalHostName` and applies only the entry with that name. If no entry matches, the run stops before any change and prints the hostname it found.

```yaml
macs:
  hosts:
    office-mac:
      ansible_connection: local
      ansible_python_interpreter: "{{ ansible_playbook_python }}"
    studio-mac:
      ansible_connection: local
      ansible_python_interpreter: "{{ ansible_playbook_python }}"
```

To see a Mac's local hostname, run `scutil --get LocalHostName`. To change it, use System Settings > General > Sharing, then rename that Mac's entry and folder to match. The names must match in capital and small letters too. A profile with one Mac skips this check.

## Apps

mac-setup installs apps from a short list in `apps/macos.yml`, called the catalog. To install some, list their ids under `machine_settings`:

```yaml
machine_settings:
  apps.install: [sublime_text, google_chrome]
```

The profile check refuses an id that is not in the catalog, and an id listed twice. Before the first app install, set up the VirusTotal key as [Secrets](#secrets) says.

| # | Id | App | Download host | What it needs once installed |
| --- | --- | --- | --- | --- |
| 1 | `google_chrome` | Google Chrome | `dl.google.com` | Nothing. Chrome updates itself. |
| 2 | `firefox` | Firefox | `archive.mozilla.org` | Nothing. Firefox updates itself. |
| 3 | `sublime_text` | Sublime Text | `download.sublimetext.com` | Nothing. |
| 4 | `little_snitch` | Little Snitch | `www.obdev.at` | A license, and your approval of its network filter in System Settings. |
| 5 | `micro_snitch` | Micro Snitch | `www.obdev.at` | A license. |
| 6 | `proton_pass` | Proton Pass | `proton.me` | A sign-in to your Proton account. |
| 7 | `ente_auth` | Ente Auth, Ente's two-factor code app | `github.com`, then `release-assets.githubusercontent.com` | A sign-in to your Ente account. |
| 8 | `utm` | UTM, an app that runs virtual machines | `github.com`, then `release-assets.githubusercontent.com` | A virtual machine may ask you to allow access the first time it runs. |

Before mac-setup copies an app, it checks the download. The download must come over HTTPS from the host the catalog names, be at least 21 days old, and match its pinned fingerprint. VirusTotal, a free malware scanning service, must know the file and report it clean. Apple's signature and notarization (Apple's malware scan of the build) checks must pass. The developer's Team ID (Apple's id for the company that signed the app) must match the catalog. If any check fails, that app is not installed, the other apps still install, and the run ends with exit code 2.

Google serves only its latest Chrome build, so `google_chrome` has no pinned fingerprint and no age check. Every other check still applies to it.

A preview with `--check` runs every check too. So it downloads each listed app and asks VirusTotal, but installs nothing.

An app that is already in `/Applications` at the catalog's version or newer is left alone. An upgrade replaces the old copy of that app. mac-setup deletes no other app and never touches Apple's own apps. [How it works](docs/how-it-works.md#how-downloads-are-checked) lists every check in order.

## Secrets

The app checks need a VirusTotal API key, a password-like code that a free VirusTotal account shows on its API key page, under the account menu. It is the only secret mac-setup reads. You keep it in an Ansible vault, an encrypted file that only your vault password opens. The key never goes in the repository.

1. Make the vault password file. The first line makes the folder. The second writes a random password that only you can read. Keep a copy of the password in your password manager, because without it nobody can open the vault. Show it with `cat ~/.dot/mac-setup/vault-password`.

   ```sh
   mkdir -p ~/.dot/mac-setup && chmod 700 ~/.dot/mac-setup
   (umask 077 && openssl rand -hex 32 > ~/.dot/mac-setup/vault-password)
   ```

2. Create the vault file in your profile's `group_vars/all/` folder. If you keep the profile in `~/.dot/mac-setup/inventory/`, use that path instead of `.local/inventory/`. The command opens the nano editor. Add one line, `virustotal_api_key: YOUR_KEY`. Press Control-O and Return to save, then Control-X to close.

   ```sh
   mkdir -p .local/inventory/group_vars/all
   EDITOR=nano .local/venv/bin/ansible-vault create .local/inventory/group_vars/all/secrets.vault.yml
   ```

3. Run the playbook as before. `ansible.cfg` points Ansible at `scripts/vault-password`, which reads the password from `~/.dot/mac-setup/vault-password`, so you type nothing extra. Set `DOT_HOME` to use a folder other than `~/.dot`. Run by hand in Terminal, the script refuses to print the password.

The password file must be yours and readable only by you (mode 600). Otherwise mac-setup prints a warning, and the vault cannot be opened. Without a usable password file, a run that needs the key stops with `Decryption failed`. A run with no vault works as before. To change the key later, run `EDITOR=nano .local/venv/bin/ansible-vault edit` with the same path.

mac-setup sends VirusTotal only the file's fingerprint, never the file. It stores each verdict in `.local/downloads/`, so a later run asks VirusTotal nothing and needs no key.

## What mac-setup changes on your Mac

Nothing runs in the background. You start each run in Terminal, it works on the same Mac, and it exits when done.

mac-setup changes the settings in your profile, creates the accounts you list (each with a home folder in `/Users/`), and installs the apps you list. A run restarts the Dock, Finder or the menu bar when a setting needs it, so open Finder windows may close. It never does these things:

1. It never grants privacy permissions, edits the privacy database, or works around a consent prompt.
2. It never deletes an account or an app. The one exception is an app upgrade, which replaces the old copy of that app.
3. It never installs macOS updates.

### Hosts the clone and the bootstrap contact

| # | Host | What comes from it |
| --- | --- | --- |
| 1 | `github.com` | This repository (`git clone`) and uv 0.13.4 |
| 2 | `release-assets.githubusercontent.com` | GitHub's file host. `github.com` redirects the uv download here. |
| 3 | `releases.astral.sh` | Python 3.13.15 (build 20260929). uv downloads it and checks its hash. |
| 4 | `files.pythonhosted.org` | The 9 Python packages pinned in `requirements/ansible.txt`, from the direct links in `requirements/ansible-urls.txt` |
| 5 | `galaxy.ansible.com` | The community.general 13.5.0 collection pinned in `requirements/collections.txt` |
| 6 | `ansible-galaxy-ng.s3.dualstack.us-east-1.amazonaws.com` | Galaxy's file host. `galaxy.ansible.com` redirects the collection download here. |

uv is a tool that installs Python. Every download uses HTTPS. The bootstrap never reads the package index at `pypi.org`. A run contacts no host unless the profile lists apps. An app install contacts the app's host (see the [Apps](#apps) table) and `www.virustotal.com`.

### Folders mac-setup writes

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
| 10 | `~/.dot/mac-setup/` | Your vault password file and, if you choose, your profile. You write them in the Secrets steps. | not measured |
| 11 | `/Applications/` | Each app in `apps.install`. An upgrade replaces the old copy. | not measured |

Rows 1 to 8 sit inside the checkout, and git ignores `.local/`. Your profile in `.local/inventory/` sits in the same ignored folder. Step 1 may install Apple's Command Line Tools in `/Library/Developer/CommandLineTools`. mac-setup never removes them.

The bootstrap changes nothing else in your home folder. uv adds no link in `~/.local/bin` and keeps no cache or settings. Ansible keeps its files in `.local/`, so no `~/.ansible` folder appears.

### Remove mac-setup

Removing mac-setup leaves your Mac's settings and the apps it installed as they are. Deleting the checkout also deletes your profile in `.local/inventory/`, so copy the profile elsewhere first if you want to keep it.

```sh
cd ~/mac-setup
.local/uv/uv python uninstall 3.13.15 --no-config
cd ~ && rm -rf ~/mac-setup
```

The second line removes Python 3.13.15 and its `cpython-3.13` link from `~/.local/share/uv/python`. If another tool on your Mac uses that same Python, skip that line. uv leaves its three small files in that folder. If nothing else on your Mac uses uv, `rm -rf ~/.local/share/uv` removes them as well.

## Learn more

1. [How mac-setup works](docs/how-it-works.md) explains the parts, the settings reference, the download checks, the design rules and the tests.
2. [Contributing](CONTRIBUTING.md) covers the checks, the developer tools, the commit hooks and the test runs on a real Mac.

## License

MIT, see `LICENSE`.
