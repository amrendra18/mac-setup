# How mac-setup works

This page explains what happens inside a run, how the settings reference is written, and how mac-setup checks what it downloads. Read the [README](../README.md) first for installing and using mac-setup. [CONTRIBUTING.md](../CONTRIBUTING.md) covers the checks and the developer tools.

## What runs

Nothing runs in the background. You start `ansible-playbook` in Terminal. It runs on the same Mac through a local connection, and it exits when done. Ansible starts a short-lived Python process for each step. Admin steps run through `sudo`, with the password you typed at the start of the run.

## The parts

1. **The settings reference**, `settings/macos/*.yml`, one file per area. Each entry describes one setting. The section [The settings reference](#the-settings-reference) gives its fields.
2. **The profile**, your copy of `inventory/example/` in `.local/inventory/`. It holds `default_settings` for every account, an `accounts` list where each account may have settings of its own, and `machine_settings`. It holds no password.
3. **The playbook**, `playbooks/site.yml`, which applies the profile in two plays.
4. **The click-through**, `playbooks/by-hand.yml`, for settings only a person can change.
5. **The template**, `inventory/example/TEMPLATE.yml`. `scripts/render-template` writes it from the reference.
6. **The app catalog**, `apps/macos.yml`, the apps mac-setup can install and the facts each download must match.

## The two plays

`playbooks/site.yml` has two plays. Only the second uses admin rights, so every admin step sits in one place.

Play 1, "This account", runs as you without admin rights:

1. When the profile lists more than one Mac, the `profile` role reads this Mac's local hostname with `scutil --get LocalHostName`. It stops with one line when no entry has that name, and ends every other entry, so play 2 skips them too. The compare is case-sensitive.
2. The `profile` role checks the whole profile against the reference and the catalog. A problem stops the run here, before any change.
3. `macos_defaults` writes your account's preferences.
4. `macos_dock_apps` sets your Dock's app list.
5. `macos_wallpaper` sets your desktop picture.
6. `macos_commands` sets your account's settings that need a command tool, such as the output and alert volumes through `osascript`. Another listed account gets these only when it runs mac-setup itself while signed in.
7. `macos_finder_sidebar` builds its helper when any account needs it, then sets your Finder sidebar.

Play 2, "Machine and other accounts", runs with admin rights:

1. `macos_accounts` creates each listed account, or adopts an existing one. Adopting takes the account over without changing its password or admin rights.
2. `macos_defaults` writes the machine preferences as root.
3. `macos_commands` sets the machine settings that need a command tool, such as `pmset` or `systemsetup`.
4. `macos_apps` installs the listed apps.
5. `macos_defaults`, `macos_dock_apps`, `macos_wallpaper` and `macos_finder_sidebar` set each other account's settings, running as that account.
6. The `profile` role prints the summary, then fails the run with one `refused` line per refused setting or account.

`playbooks/by-hand.yml` runs the `profile` role, then `macos_by_hand` for the click-only settings of your account and of the machine.

## The roles

| # | Role | What it does | Custom module |
| --- | --- | --- | --- |
| 1 | `profile` | Checks the profile, splits it into work for each role, and prints the summary | none. It uses a filter plugin, a Python function Ansible calls from the playbook. |
| 2 | `macos_defaults` | Writes plain preferences through `community.general.osx_defaults`, writes dictionaries and arrays of nested values, such as shortcuts and input sources, through its own module, and restarts the Dock, Finder or SystemUIServer when a setting needs it | `macos_nested_default` |
| 3 | `macos_commands` | Reads and sets settings through command tools | `macos_command_setting` |
| 4 | `macos_accounts` | Creates a listed account or adopts an existing one | `macos_account` |
| 5 | `macos_dock_apps` | Sets the Dock's app list | `macos_dock_apps` |
| 6 | `macos_wallpaper` | Sets the desktop picture | `macos_wallpaper` |
| 7 | `macos_finder_sidebar` | Builds a small helper with the Command Line Tools and sets the Finder sidebar | `macos_finder_sidebar` |
| 8 | `macos_apps` | Checks and installs apps from the catalog | `macos_app` |
| 9 | `macos_by_hand` | Opens each click-only System Settings page and waits for you | none |

A custom module exists only where no existing module does the job. Each one reads before it writes and supports `--check`.

## Run details

1. When another account, or the login window, owns the screen, the run skips the Dock restart. A restart then would leave that account's Dock stopped until it logs out and back in. The Dock changes at that account's next sign-in instead.
2. Each new account adds 1 to the run's `changed` count, and each setting written for another account adds 1 more.
3. A preview with `--check` still builds the Finder sidebar helper into `.local/finder-sidebar/` when the profile lists a sidebar setting, because the helper is needed to read the sidebar.
4. A preview prints a `changed` line for each setting it would change, without the value. To see a current value, run `defaults read DOMAIN KEY` with the domain and key from the reference.
5. `dock.applications` joins a newly installed app on the next run, because play 1 sets the Dock before play 2 installs apps.

## The settings reference

Each `*.yml` file in `settings/macos/` maps setting ids to entries. An id looks like `area.name`, such as `dock.position`. The reference holds 288 entries, and `ENTRY_COUNT` in `tests/test_reference.py` pins that number. `tests/test_reference.py` checks every entry against the rules below.

This is one entry:

```yaml
dock.position:
  summary: Which edge of the screen the Dock sits on.
  scope: user
  route: defaults
  values: [left, bottom, right]
  macos_default: not measured
  writes:
    - {domain: com.apple.dock, key: orientation, type: string}
  takes_effect: dock
  needs: []
  lockout_risk: false
  where: System Settings > Desktop & Dock > Position on screen
  proven_on: {"27.2.1": "2026-11-12"}
  not_yet_reason: ""
```

### Fields

| # | Field | Meaning |
| --- | --- | --- |
| 1 | `summary` | One line on what the setting does |
| 2 | `scope` | `user` for an account setting, `machine` for a setting of the whole Mac |
| 3 | `route` | How mac-setup applies it. See the routes table below. |
| 4 | `values` | A list of plain choices, or `{type: T}`. T is `bool`; `int` or `float` with `min` and `max`; `text` with `pattern`; `bundle_ids`; `paths`; `catalog_ids`; `list` with `choices` and an optional `min`, the fewest items allowed; or `hotkeys` with `actions`, which maps each shortcut name to its number in macOS. |
| 5 | `macos_default` | The measured macOS default, or `not measured` |
| 6 | `takes_effect` | `live`, `dock`, `finder`, `systemuiserver`, `relaunch` (when the app opens again), `logout`, or `not measured` when no test has shown when the change appears |
| 7 | `needs` | Any of `full_disk_access` and `click`, each at most once |
| 8 | `lockout_risk` | `true` when the setting could cut a remote connection. The profile must then set `allow_lockout_risk: true`. |
| 9 | `where` | The place in System Settings, in one line |
| 10 | `proven_on` | macOS versions mapped to the date the setting was applied and read back on a real Mac |
| 11 | `not_yet_reason` | Why no route applies the setting yet. Empty outside route `not_yet`. |
| 12 | `notes` | Optional. Facts a user needs. |

### Routes

| # | Route | Extra field | How mac-setup applies it |
| --- | --- | --- | --- |
| 1 | `defaults` | `writes`, each with `domain`, `key` and `type`, and optional `host: currentHost`, `map` and `dict_mode` | Writes a preference. A machine-scope domain must be a path that starts with `/`. |
| 2 | `command` | `command`, with `read`, `parse`, `set`, and an optional `map` and `refuse` | Runs a command tool. [The command read](../settings/macos/README.md) explains how `parse` matches the output. |
| 3 | `dock_apps` | none | Sets the Dock's app list. Values are `bundle_ids`. |
| 4 | `wallpaper` | none | Sets the desktop picture. |
| 5 | `finder_sidebar` | none | Sets the Finder sidebar. Values are `paths`, `bool` or a `list`. |
| 6 | `apps` | none | Installs apps from the catalog. Values are `catalog_ids`. |
| 7 | `by_hand` | `link`, an `x-apple.systempreferences:` URL or `""` | Opens the System Settings page for a person to click. |
| 8 | `not_yet` | none | Nothing yet. The profile check refuses the setting. |

Three optional fields change a `defaults` write:

1. `host: currentHost` writes this Mac's own copy of the preference, as `defaults -currentHost` does.
2. `map` turns each value from the profile into the value macOS stores. A value missing from the map leaves that key alone.
3. `dict_mode` applies to a `dict` type. `replace`, the default, writes the whole dictionary, and `add` merges only the listed keys.

Of the 288 entries, 218 are set by a run, 40 are on route `by_hand`, and 30 are on route `not_yet`: 26 privacy permissions that only a person can grant, text replacements, modifier keys, the number of Spaces, and the password minimum length.

## How downloads are checked

Every download has a fixed version and a SHA-256 hash, and was at least 21 days old when it was pinned. The one exception is an app whose vendor publishes only a latest download, such as Google Chrome.

### The bootstrap

`scripts/bootstrap` checks each of its 11 downloads (uv, 9 Python packages and one Ansible collection) against the hash pinned in `scripts/bootstrap` or in `requirements/`. uv then downloads Python and checks its hash. A file that does not match is deleted, and the bootstrap exits with code 1. A second run downloads nothing and changes nothing. On a macOS 15.6.1 virtual machine, a first run took 11.2 s and a second run 1.1 s. On a Mac mini with macOS 27.2.1, a first run took 11 s.

### Apps

Each catalog entry names the vendor's download URL and the only host it may come from. An entry may name one more host that a redirect may go to, such as `release-assets.githubusercontent.com`, GitHub's file host. It also names the pinned SHA-256 hash, the vendor's release date, the vendor's Apple Team ID (the ID of the developer certificate that signs the app), the bundle id and the lowest version accepted.

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

An app install fails closed. A refused app is not copied, the other apps still install, and the run ends with one `refused` line per app and exit code 2. A preview with `--check` runs every check, so it also downloads and asks VirusTotal, and it installs nothing.

Google serves only its latest Chrome build, so `google_chrome` has no pinned hash and no age check. The signature, Gatekeeper, Team ID and VirusTotal checks still apply.

VirusTotal's verdict for each file is stored in `.local/downloads/SHA256.virustotal.json`, where SHA256 is the file's hash. A later run reads the stored verdict and asks VirusTotal nothing, so it needs no key. A stored `flagged` verdict keeps refusing that file. The run makes at most 4 lookups per minute, the free API key's limit, and waits when it reaches that rate.

An upgrade replaces the old copy whole. The run copies the new app next to the old one, swaps the two, and then deletes the old copy.

## Design rules

1. **The profile is the source of truth.** A run makes the Mac match the profile. There is no hidden state and no receipt file.
2. **Check before writing.** The whole profile is checked before the first change. A bad profile changes nothing.
3. **Admin work sits in one place.** Only play 2 of `playbooks/site.yml` uses admin rights.
4. **Small, data-driven code.** One role writes every simple preference from the reference. A custom module exists only where no existing module can do the job.
5. **Pinned and checked downloads.** See [How downloads are checked](#how-downloads-are-checked).
6. **Private data stays local.** Real machine names, account names and profiles live in `.local/` or `~/.dot/`, outside the tracked files. Account passwords are typed at a prompt and never written to a file. The only secret stored in plain text is the vault password, in a file only you can read. The VirusTotal key sits in an encrypted vault that this password opens.
7. **Proven on the real Mac.** A setting counts as proven on a macOS version only after it was applied and read back on a Mac running that version.

## Scope

### In the first release

1. Machine settings that need admin rights, such as power, firewall, sharing services, time zone, computer name, and the other admin-only preferences.
2. Each account's own settings, such as Dock, Finder, keyboard, trackpad, mouse, menu bar, appearance, and the other per-account preferences.
3. Creating the accounts listed in the profile, admin or standard, with passwords typed at run time and never stored.
4. The Dock's app list. Apple's own apps can leave the Dock. They are never deleted.
5. A solid black desktop, or a click-only step if macOS offers no script path for it.
6. The guided click-through for privacy permissions and click-only System Settings panes.
7. A generated template, `inventory/example/TEMPLATE.yml`, that lists every known setting with its allowed values, whether it needs admin rights, when it takes effect, and whether it is proven on the current macOS.
8. Apps from the checked catalog in `apps/macos.yml`, and one profile for several Macs.

### Next, right after the first release

Homebrew, dotfiles, turning FileVault on with a secure token (the right to unlock the encrypted disk at startup) for each account mac-setup creates, MDM profiles, an undo command, and running mac-setup from another machine over SSH.

### Not planned

1. Granting privacy permissions by script, editing the privacy database, or working around a consent prompt.
2. Deleting accounts or apps. The one exception is an app upgrade, which replaces the old copy of that app.
3. macOS updates.
4. Linux and Windows for now. The layout leaves room for them. Each system gets its own settings folder and roles, with the same profile shape.

## Requirements and success checks

| # | Requirement | How it is checked |
| --- | --- | --- |
| 1 | A newcomer can install and run mac-setup from the README alone on a fresh Mac. | A pass from a new clone on a real Mac finds 0 gaps. |
| 2 | A second run changes nothing. | Every journey's second run shows `changed=0 failed=0`. |
| 3 | A profile mistake changes nothing. | Each deliberately broken test profile stops with exit 2 and 0 settings written. |
| 4 | A refused setting does not stop the others. | A test seeds a wrong stored type, and the run ends with exit 2, one line for that setting, and the other settings written. |
| 5 | Each account gets its own settings. | Settings written for a test account read back as that account and leave the running account unchanged. |
| 6 | No password is ever stored or logged. | A test run with a random password finds 0 copies of it in the logs and the evidence folder. |
| 7 | No machine or account name is tracked in git. | The gate scans tracked files against a private word list kept outside git. |
| 8 | Every researched setting is listed. | A test checks every entry in the reference, and checks that the template lists each one. |
| 9 | A full-size profile runs fast enough to iterate. | Measured once on the target Mac, a 170-setting test profile runs its preview and its second apply in under 60 s each. |

## How mac-setup is tested

Two scripts test mac-setup. [CONTRIBUTING.md](../CONTRIBUTING.md) says when to run each.

1. `scripts/check` runs on any Mac and changes no settings. It lints the YAML files, checks playbook syntax, runs the unit tests and every broken test profile in `tests/inputs/`, plus the two-Mac inventory `tests/two-macs/hosts.yml`, whose entries match no Mac. Each must stop in the profile check with exit code 2. It also checks the structure rules (no `sudo` in a role, `become` only in play 2 of `playbooks/site.yml`, and no task that touches the privacy database or System Integrity Protection) and scans the files for private words and secrets. `scripts/check full` adds the reference test, a format check and ansible-lint.
2. `scripts/verify` is the harness that proves features on a real Mac. Each feature has a journey in `verify/journeys/`, a YAML file that lists playbook runs and the values to read back afterwards. The harness runs the real playbook, reads the stored values with `defaults export`, and keeps the evidence in `.local/verify/UTCTIME/`. It changes the real settings of the account that runs it, and the `accounts` journey creates the standard account `setuptest1`.

The [verification map](../verify/features/README.md) describes the harness in full. It gives the harness's steps, its evidence, the journey file format and one page per feature.

## Milestones

| # | Milestone | Goal | Done when |
| --- | --- | --- | --- |
| 1 | M0 | install from the README and one visible Dock change | A fresh clone installs, one run puts the Dock on the left at size 32 with recents hidden, and a second run shows `changed=0`. |
| 2 | M1 | each account gets its own settings | A test account is created and gets its own settings. Existing accounts are adopted with 0 changes. A refused setting no longer stops the run. A 170-setting profile is timed. |
| 3 | M2 | the full settings reference and the template | All researched settings load, and the template lists each one. |
| 4 | M3 | account settings by area | Dock, Finder, input, system UI and privacy preferences apply and read back. |
| 5 | M4 | machine settings | Admin settings and command-line tools apply and read back. |
| 6 | M5 | guided click-through | Each click-only page opens with what to choose and 0 preference writes. |
| 7 | M6 | Dock apps and a black desktop | The Dock holds only the listed apps, and the desktop is black. |
| 8 | M7 | the remaining settings | Every setting has a way to apply it or a written reason why none is safe. |
| 9 | M8 | the new-Mac journey | A fresh admin account follows the README end to end with one command. |
| 10 | M9 | the next items | Dotfiles, FileVault, MDM, undo and SSH runs, each planned and approved first. |
