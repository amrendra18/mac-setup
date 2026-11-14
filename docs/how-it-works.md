# How mac-setup works

This page explains what happens inside a run, how the settings reference is written, and how mac-setup checks what it downloads. Read the [README](../README.md) first for installing and using mac-setup. [CONTRIBUTING.md](../CONTRIBUTING.md) covers the checks and the developer tools. The [installation reference](installation.md), [profile guide](profiles.md), and [troubleshooting guide](troubleshooting.md) hold the detailed user instructions.

## What runs

Nothing runs in the background. You start `ansible-playbook` in Terminal. It runs on the same Mac through a local connection, and it exits when done. Ansible starts a short-lived Python process for each step. Admin steps run through `sudo`, with the password you typed at the start of the run.

## The parts

1. **The settings reference**, `settings/macos/*.yml`, one file per area. Each entry describes one setting. The section [The settings reference](#the-settings-reference) gives its fields. The permissions table, `settings/macos/permissions/table.yml`, describes each privacy permission a setting can need.
2. **The profile**, your copy of `inventory/example/`. The README keeps it in `~/.dot/mac-setup/inventory/` and selects it with `-i`. The configured default remains `.local/inventory/`. It holds `default_settings` for every account, an `accounts` list where each account may have settings of its own, and `machine_settings`. Account passwords stay out of the profile. An optional encrypted vault holds the VirusTotal API key.
3. **The playbook**, `playbooks/site.yml`, which applies the profile in five plays.
4. **The click-through**, for settings only a person can change. It runs at the end of `playbooks/site.yml`, and `playbooks/by-hand.yml` runs it again on its own.
5. **The template**, `inventory/example/TEMPLATE.yml`. `scripts/render-template` writes it from the reference.
6. **The app catalog**, `apps/macos.yml`, the apps mac-setup can install and the facts each download must match.

## The plays

`playbooks/site.yml` has five plays. They are "Permissions", "This account", "Machine and other accounts", "This account's Firefox bookmarks", and "Take back permissions, click through and summarize". Only "Machine and other accounts" uses admin rights, so every admin step sits in one place. `scripts/check` finds that play by its name.

"Permissions" runs as you without admin rights:

1. When the profile lists more than one Mac, the `profile` role reads this Mac's local hostname with `scutil --get LocalHostName`. It stops with one line when no entry has that name, and ends every other entry, so the later plays skip them too. The compare is case-sensitive.
2. The `profile` role checks the whole profile against the reference and the catalog. A problem stops the run here, before any change.
3. The `profile` role works out the permissions this run needs from your account's settings and the machine settings. A setting's `needs` names rows of the permissions table. A folder row counts only when the setting's value lists that folder or a folder inside it. It also records whether the run sits at the Mac's screen: no `SSH_CONNECTION`, `SSH_CLIENT` or `SSH_TTY` is set, the run is not `--check`, and controller input and output use terminals. The permission asks and the click-through both follow that one record.
4. `macos_permissions` tests each needed permission with `ls` of a protected folder: `~/Library/Mail` for Full Disk Access, and the folder itself for Desktop, Documents and Downloads. "Operation not permitted" means the permission is off. When `~/Library/Mail` does not exist, as on a fresh account, it lists `/Library/Application Support/com.apple.TCC` instead. That folder exists on every Mac, and listing it reads no file inside it.
5. At the Mac's screen, it asks for each permission that is off. For Full Disk Access it opens the page, says what to turn on, waits for Return, and tests again. For a folder it says to click Allow, then reads the folder, which shows macOS's own dialog the first time. Over SSH (`SSH_CONNECTION`, `SSH_CLIENT` or `SSH_TTY` is set) or with `--check`, it asks for nothing and lists what it would ask. It skips the folder tests there, because a test read shows the dialog.
6. "This account" and "Machine and other accounts" may not write a setting whose permission is still off. A command setting is read first. If it already matches, it is `ok`. If it would change, the module returns an Ansible skip. The Finder sidebar is set in one step, so a missing folder skips all three sidebar settings without a read.

"This account" runs as you without admin rights:

1. `macos_defaults` writes your account's preferences.
2. `macos_wallpaper` sets your desktop picture.
3. `macos_commands` sets your account's settings that need a command tool, such as the output and alert volumes through `osascript`. Another listed account gets these only when it runs mac-setup itself while signed in.
4. `macos_finder_sidebar` builds its helper when any account needs it, then sets your Finder sidebar.

"Machine and other accounts" runs with admin rights:

1. `macos_accounts` creates each listed account, or adopts an existing one. Adopting applies settings without changing its password or admin rights. A different admin value in the profile is refused.
2. `macos_defaults` writes the machine preferences as root.
3. `macos_commands` sets the machine settings that need a command tool, such as `pmset` or `systemsetup`.
4. `macos_apps` installs the listed apps.
5. `macos_firefox` applies the selected machine-wide Firefox policies.
6. `macos_firefox_bookmarks` imports each other account's selected bookmarks, running as that account.
7. `macos_defaults`, `macos_dock_apps`, `macos_wallpaper` and `macos_finder_sidebar` set each other account's settings, running as that account.

"This account's Firefox bookmarks" runs `macos_firefox_bookmarks` as you without admin rights.
Both bookmarks steps run after app installation and Firefox policies, so the same run can install Firefox before importing.

"Take back permissions, click through and summarize" runs as you without admin rights:

1. `macos_dock_apps` sets your Dock after the listed apps install. An earlier unexpected stop skips this step.
2. `macos_permissions` takes back each temporary permission that "Permissions" found off and you turned on. Today that is only Full Disk Access. It opens the page, says to turn the app off, waits for Return, and tests that it is off. A permission that was on before the run stays on. This step comes before the click pages, so stopping at a click page never leaves Full Disk Access on.
3. `macos_default_browser` reads web-link handlers and requests macOS confirmation only at your screen, after temporary permissions are removed.
4. `macos_by_hand` walks the click-only settings of your account and of the machine. For each one it opens the System Settings page, says what to choose, and waits for Return. It walks only when "Permissions" would ask, by the same rule: at the Mac's screen and without `--check`. Otherwise it prints one `would open` line per page and opens none.
5. The `profile` role prints the summary, with one line per needed permission and one line per skipped setting. A permission that stayed off gets "every setting that needs it already matches" only when the run read each of those settings and found it matching. The role then fails the run with one `refused` line per refused setting or account. A skipped setting is not a refusal, so it leaves the exit code at 0. The summary counts the click-only settings this run did not walk: your account's and the machine's when it only listed them, and every other account's, which that account walks when it runs mac-setup while signed in.

"This account" and "Machine and other accounts" each wrap their work in one block. The final play also wraps your Dock update. An unexpected error records the failed task instead of ending the run. Later settings stop, while permission cleanup and the click pages still run. The summary names the failed task and its message, and the run exits with code 2. Control-C skips any steps that have not run yet. Permission cleanup comes before the click pages.

The final play first loads `macos_defaults` with no preference writes and your account as the target.
This registers its restart handlers within that play and reads the current screen owner before the Dock changes.

`playbooks/by-hand.yml` runs the `profile` role, then `macos_by_hand` for the click-only settings of your account and of the machine, to walk the pages again without the rest of the run. It walks or lists by the same rule as `site.yml`.

## The roles

| # | Role | What it does | Custom module |
| --- | --- | --- | --- |
| 1 | `profile` | Checks the profile, splits it into work for each role, works out the permissions it needs, and prints the summary | none. It uses a filter plugin, a Python function Ansible calls from the playbook. |
| 2 | `macos_defaults` | Writes plain preferences through `community.general.osx_defaults`, writes dictionaries and arrays of nested values, such as shortcuts and input sources, through its own module, and restarts the Dock, Finder or SystemUIServer when a setting needs it | `macos_nested_default` |
| 3 | `macos_commands` | Reads and sets settings through command tools | `macos_command_setting` |
| 4 | `macos_accounts` | Creates a listed account or adopts an existing one | `macos_account` |
| 5 | `macos_dock_apps` | Sets the Dock's app list | `macos_dock_apps` |
| 6 | `macos_wallpaper` | Sets the desktop picture | `macos_wallpaper` |
| 7 | `macos_finder_sidebar` | Builds a small helper with the Command Line Tools and sets the Finder sidebar | `macos_finder_sidebar` |
| 8 | `macos_apps` | Checks and installs apps from the catalog | `macos_app` |
| 9 | `macos_by_hand` | Opens each click-only System Settings page and waits for you | none |
| 10 | `macos_permissions` | Tests each permission the run needs, asks for the missing ones at the screen, and takes back Full Disk Access at the end | none. It uses a filter plugin. |
| 11 | `macos_firefox` | Applies selected Firefox policies and reports unsupported lock forms | `macos_firefox` |
| 12 | `macos_firefox_bookmarks` | Imports a cleaned bookmarks export once per Firefox profile | `macos_firefox_bookmarks` |
| 13 | `macos_default_browser` | Reads web-link handlers and requests macOS confirmation for the running account | `macos_default_browser` |

A custom module exists only where no existing module does the job. Each one reads before it writes and supports `--check`.

`macos_app` checks arguments before loading installation code.
The `app_install` helper reads installed apps, replaces them, and handles recovery.
The `verified_download` helper downloads and verifies apps.
Both helpers live in `roles/macos_apps/module_utils/`.

## Run details

1. When another account, or the login window, owns the screen, the run skips the Dock restart. A restart then would leave that account's Dock stopped until it logs out and back in. The Dock changes at that account's next sign-in instead.
2. Each new account adds 1 to the run's `changed` count, and each setting written for another account adds 1 more.
3. A preview with `--check` still builds the Finder sidebar helper into `.local/finder-sidebar/` when the profile lists a sidebar setting, because the helper is needed to read the sidebar.
4. A preview prints a `changed` line for each setting it would change, without the value. To see a current value, run `defaults read DOMAIN KEY` with the domain and key from the reference.
5. `dock.applications` runs after app installation for every account. An app installed by the same run can join the Dock immediately. The Dock role still refuses the entire list when an app is missing, and the run ends with exit code 2.

## The settings reference

Each `*.yml` file in `settings/macos/` maps setting ids to entries. An id looks like `area.name`, such as `dock.position`. The reference holds 1264 entries, and `ENTRY_COUNT` in `tests/test_reference.py` pins that number. `tests/test_reference.py` checks every entry against the rules below.

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
| 4 | `values` | A list of plain choices, or `{type: T}`. T is `bool`; `int` or `float` with `min` and `max`; `text` with `pattern`; `bundle_ids`; `paths`; `profile_path`; `catalog_ids`; `list` with `choices` and an optional `min`, the fewest items allowed; or `hotkeys` with `actions`, which maps each shortcut name to its number in macOS. Browser entries use `{type: browser, schema: ...}`. Their schema defines allowed value types, choices, and bounds. |
| 5 | `macos_default` | The measured macOS default, or `not measured` |
| 6 | `takes_effect` | `live`, `dock`, `finder`, `systemuiserver`, `relaunch` (when the app opens again), `logout`, or `not measured` when no test, document or report says when the change appears and the run restarts nothing for it; the other values come from a document, a report, or a guess that makes the run restart the Dock or the menu bar |
| 7 | `needs` | Any of `click` and the rows of the permissions table, each at most once. A permission is allowed only on routes `command` and `finder_sidebar`, where a run can skip the setting. |
| 8 | `lockout_risk` | `true` when the setting could cut a remote connection. The profile must then set `allow_lockout_risk: true`. |
| 9 | `where` | The place in System Settings, in one line |
| 10 | `proven_on` | macOS versions mapped to the date the setting was applied and read back on a real Mac |
| 11 | `not_yet_reason` | Why no route applies the setting yet. Empty outside route `not_yet`. |
| 12 | `notes` | Optional. Facts a user needs. |

### Routes

| # | Route | Extra field | How mac-setup applies it |
| --- | --- | --- | --- |
| 1 | `defaults` | `writes`, each with `domain`, `key` and `type`, and optional `host: currentHost`, `map` and `dict_mode` | Writes a preference. A machine-scope domain must be a path that starts with `/`. |
| 2 | `command` | `command`, with `read`, `parse`, `set`, and an optional `map` and `refuse` | Runs a command tool, then reads again. That read decides whether the set worked, whatever the tool's exit code; a known refusal line still refuses. [The command read](../settings/macos/README.md) explains how `parse` matches the output. |
| 3 | `dock_apps` | none | Sets the Dock's app list. Values are `bundle_ids`. |
| 4 | `wallpaper` | none | Sets the desktop picture. |
| 5 | `finder_sidebar` | none | Sets the Finder sidebar. Values are `paths`, `bool` or a `list`. |
| 6 | `apps` | none | Installs apps from the catalog. Values are `catalog_ids`. |
| 7 | `by_hand` | `link`, an `x-apple.systempreferences:` URL | Opens the System Settings page for a person to click. |
| 8 | `not_yet` | none | Nothing yet. The profile check refuses the setting. |
| 9 | `firefox` | `browser`, the shared map metadata | Applies selected machine-wide Firefox policy values. |
| 10 | `bookmarks` | none | Shares one bookmarks source between browser importers. Values are `profile_path`, a nonempty relative file path without `..` parts or line breaks. |
| 11 | `default_browser` | none | Reads web-link handlers and asks macOS once at the selected account's screen. |

### The permissions table

`settings/macos/permissions/table.yml` maps each permission to one row, such as `full_disk_access` or `desktop_folder`.

| # | Field | Meaning |
| --- | --- | --- |
| 1 | `name` | The name the run prints, such as `Full Disk Access` |
| 2 | `test` | The folder whose `ls` shows whether the permission is on |
| 3 | `fallback` | A folder to list instead when `test` does not exist, or `""` |
| 4 | `only_when_listed` | `true` when the permission counts only if the setting's value lists `test` or a folder inside it |
| 5 | `ask` | `pane` when a person turns the app on in a System Settings page, `dialog` when macOS shows its own dialog at the first read |
| 6 | `where` | The System Settings page, in one line |
| 7 | `link` | The `x-apple.systempreferences:` URL that opens that page |
| 8 | `tick` | What the person does, with `{app}` for the app that runs mac-setup |
| 9 | `temporary` | `true` when the last play asks the person to turn it off again. Only a `pane` row can be temporary. |

`tests/test_reference.py` checks each row, and checks that every `needs` value names a row or `click`.

Three optional fields change a `defaults` write:

1. `host: currentHost` writes this Mac's own copy of the preference, as `defaults -currentHost` does.
2. `map` turns each value from the profile into the value macOS stores. A value missing from the map leaves that key alone.
3. `dict_mode` applies to a `dict` type. `replace`, the default, writes the whole dictionary, and `add` merges only the listed keys.

Of the 1264 entries, 569 are set by a run, 41 are on route `by_hand`, and 654 are on route `not_yet`.
The unsupported entries include 603 Chrome rows, 21 Firefox rows, and 30 macOS settings awaiting support.
The macOS entries cover 26 privacy permissions, text replacements, modifier keys, the number of Spaces, and the password minimum length.

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
5. **VirusTotal.** The run sends only the file's SHA-256 to `www.virustotal.com`, never the file. The report must contain a completed clean scan. A malicious or suspicious result refuses the file. So does a file VirusTotal does not know, a report with no completed scan, and a missing key when no usable cached verdict exists.
6. **Unpack.** A disk image is mounted read-only and hidden from Finder. A zip is unpacked with `ditto`. The app must be inside.
7. **Signature.** `codesign --verify --deep --strict` must pass.
8. **Gatekeeper.** `spctl --assess` must pass, which also confirms that Apple notarized the build.
9. **Team ID.** The signing Team ID must equal the catalog's.
10. **Bundle id and version.** Both must match the catalog, and the version must be at least the catalog's.

An app install fails closed. A refused app is not copied, the other apps still install, and the run ends with one `refused` line per app and exit code 2. A preview with `--check` runs every check, so it also downloads and asks VirusTotal, and it installs nothing.

Google serves only its latest Chrome build, so `google_chrome` has no pinned hash and no age check. The signature, Gatekeeper, Team ID and VirusTotal checks still apply.

VirusTotal's verdict for each file is stored in `.local/downloads/SHA256.virustotal.json`, where SHA256 is the file's hash. A later run with a usable stored verdict asks VirusTotal nothing, so the lookup needs no API key. Loading an encrypted vault still needs its password. A stored `flagged` verdict keeps refusing that file. The run makes at most 4 lookups per minute, the free API key's limit, and waits when it reaches that rate.

An upgrade replaces the old copy whole. The run copies the new app next to the old one, swaps the two, and then deletes the old copy. A running app is refused until you quit it.

## Design rules

1. **The profile is the source of truth.** A run makes the Mac match the profile. There is no hidden state and no receipt file.
2. **Check before writing.** The whole profile is checked before the first change. A bad profile changes nothing.
3. **Admin work sits in one place.** Only the "Machine and other accounts" play of `playbooks/site.yml` uses admin rights.
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
9. Selected Firefox machine policies and a one-time cleaned bookmarks import for each selected account.

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

1. `scripts/check` runs on any Mac and changes no settings. It lints the YAML files, checks playbook syntax, runs the unit tests and every broken test profile in `tests/inputs/`, plus the two-Mac inventory `tests/two-macs/hosts.yml`, whose entries match no Mac. Each must stop in the profile check with exit code 2. It also checks the structure rules (no `sudo` in a role, `become` only in the "Machine and other accounts" play of `playbooks/site.yml`, and no task that touches the privacy database or System Integrity Protection) and scans the files for private words and secrets. `scripts/check full` adds the reference test, a format check and ansible-lint.
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
| 6 | M5 | guided click-through at the end of a run | At the Mac's screen, each click-only page opens at the end of `playbooks/site.yml` with what to choose and 0 preference writes. Over SSH, each page is listed and none opens. |
| 7 | M6 | Dock apps and a black desktop | The Dock holds only the listed apps, and the desktop is black. |
| 8 | M7 | the remaining settings | Every setting has a way to apply it or a written reason why none is safe. |
| 9 | M8 | the new-Mac journey | A fresh admin account follows the README end to end with one command. |
| 10 | M9 | the next items | Dotfiles, FileVault, MDM, undo and SSH runs, each planned and approved first. |
| 11 | M10 | permissions up front | A run asks for the permissions its profile needs before any change, skips the settings of a permission that stays off, and takes back Full Disk Access at the end. |

## Browser capture and shared map

The Firefox and Chrome reference files own the browser map.
They contain 121 Firefox policies, 499 current Chrome policies, and 41 deprecated Chrome policies.
Configurable policy values occupy 319 Firefox rows and 557 Chrome rows.
Structured arrays retain their member schemas. The inventory ledgers name their member paths separately.
There are also 31 Firefox and 45 Chrome settings outside the policy catalogs, plus two lock controls.
Every row has a mapping, a privacy restriction, or a named unsupported reason.
Unsupported updater controls stay visible in the inventory.

`scripts/render-browser-map` validates the browser metadata and writes `module_utils/browser_settings_data.py`.
Its `--check` form rejects stale output in the fast check.
The generated module contains reviewed data and a source fingerprint.
It requires only Python's standard library when loaded.
`module_utils/browser_settings.py` owns parsing, source checks, filtering, and candidate rendering.
The standalone command and the Firefox module load those exact utilities.
The profile validator shares their browser value validation.
`ansible.cfg` declares the shared `module_utils` folder.

Capture reads only approved configuration sources for the selected profile.
Firefox uses `prefs.js`, optional `user.js`, and saved policy plists.
Its non-executing parser accepts preference records, comments, quoted strings, booleans, and signed integers.
Repeated preference records keep the last value within that file.
Saved preferences and startup overrides remain separate observations.
When several descriptors share one source, they form a complete evidence group.
A partially persisted group reports `unverified` and emits no assignment.
Chrome uses mapped leaves in Preferences and, where required by the map, Local State or Secure Preferences.
Saved machine and account policy plists remain separate observations with unknown effective lock state.
Capture does not read installed extensions or personal stores.

Firefox's `compatibility.ini` and Chrome's `Last Version` record the last browser version for that profile.
Capture removes Firefox's build identifiers and never emits its recorded application paths.
Mozilla's [version-record implementation](https://raw.githubusercontent.com/mozilla-firefox/firefox/FIREFOX_158_0_1_RELEASE/toolkit/xre/nsAppRunner.cpp) defines that format.
The report labels this as `saved_profile`, rather than claiming a running browser version.
The `versions.browser` field records that observed version.
The separate `versions.map_tested_on` list names the map's pinned test versions for the selected family.
The map's reference versions are Firefox 158.0.1 and Chrome 158.0.8127.37. They do not establish real-browser verification.
Firefox can update itself after installation. The journey reads the running version from Marionette's NewSession capabilities.
Each disposable launch saves only that sanitized version in `runtime.json`; unknown versions use `unavailable`.
The launch environment grants system access through `MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1` for the test child only.
Firefox 158 through 160 support this environment value. The legacy command-line flag was removed in Firefox 159.
Candidate headers name the tested and observed versions. Newer observed versions still capture mapped values.
Each mapping records minimum known support in `support.min` and its pinned test version in `support.tested`.
An optional `support.max` records only a vendor-documented removal bound, with its source and limitation note.
A major-version maximum includes that major version's point releases.
Unknown versions and versions outside these support bounds report `unavailable`.
The command checks process ids and executable names before and after reading.
It never reads process arguments or account names.
Chrome activity belongs to the selected user data folder, including shared Local State.
Firefox activity belongs to the selected profile.
The command reads only selected-folder lock metadata and never changes locks or signals processes.

Chrome's `SingletonLock` symlink contains a hostname and process id.
Firefox on macOS normally uses a regular `.parentlock` held by a POSIX record lock.
The reader opens that existing file read-only and queries `F_GETLK`; it never acquires a lock.
Firefox's fallback `.parentlock` and older `lock` symlinks contain an address and process id.
A live owner must also match the selected browser family before capture refuses it.
A stale lock or a pid reused by an unrelated program does not prove an active writer.
Changes in lock evidence during a process snapshot refuse capture.

No lock in an isolated selected folder allows capture while another instance uses another folder.
A default selected folder without lock evidence remains unverified while that browser runs.
Unknown formats, unreadable locks, remote hosts, non-loopback Firefox link addresses, and failed process checks refuse capture.
Old regular Chrome locks do not expose a usable owner pid and remain unverified while Chrome runs.
The browser sources describe the formats: [Chromium singleton](https://raw.githubusercontent.com/chromium/chromium/main/chrome/browser/process_singleton_posix.cc),
[Mozilla profile locks](https://raw.githubusercontent.com/mozilla-firefox/firefox/main/toolkit/profile/nsProfileLock.cpp),
and [Apple record-lock queries](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fcntl.2.html).
It compares source identity, size, modification time, and change time to reject a changed snapshot.
These checks reduce races. They do not provide an atomic browser snapshot.

Output names come only from the map. Unknown persisted names become counts.
Firefox counts unique unknown preference names across saved and startup files.
Chrome counts unknown terminal entries in each approved JSON store; arrays count as one entry.
Policy terminal entries have a separate count across inspected policy stores.
Those counts describe persisted entries and do not establish changes from defaults.
Free text and dynamic structures stay masked. Exact reviewed public endpoints are the only URL exception.
A report emits no active value for masked, absent, conflicting, unsupported, or unavailable entries.

There are 349 Firefox rows on route `firefox`. The other 21 remain `not_yet`.
Chrome rows remain `not_yet`. Both lock controls are machine booleans with proposed default `true`.
Every row documents its locked form and its editable form or reason for leaving it unset.
Capture does not infer either global switch from saved browser state.
The capture fixture journey proves extraction and privacy only.
The Firefox journey separately checks policy application and browser behavior. Bookmarks import needs separate work.

## Firefox policies

The profile validator checks types and the full selected Firefox set before any setting writes.
Its `firefox` work dictionary maps setting ids to the exact selected values.
It does not expand proposed baselines. `firefox.lock` defaults to true in the Firefox module.
The profile summary counts selected Firefox settings, excluding the lock switch, and asks you to reopen Firefox.
Role notes describe unsupported lock forms and editable defaults.

`macos_firefox` runs after `macos_apps` in the machine play and inherits its admin rights.
It merges selected keys into `/Library/Preferences/org.mozilla.firefox.plist` and preserves unrelated keys.
The role keeps `/Library/Preferences/org.mac-setup.firefox-owned.plist` as its ownership record.
The record contains setting ids and policy paths, without selected values.
Before importing policies, the module records the union of previous and new owned paths.
An interrupted write or read-back can therefore release those paths on a later run.
An omitted setting preserves its policy. An explicit `unset` releases the setting's owned keys.
Changing `firefox.lock` to false releases previously owned locked forms before installing editable forms.
A shared native `Locked` change refuses if it would change an unowned sibling setting.
An explicit `unset` preserves a shared lock that unrelated siblings still need and reports that limit.
The policy loader `EnterprisePoliciesEnabled` stays true after the final release so unrelated policies continue working.
A matching typed policy domain needs no write. A first matching run can still establish the ownership record.

`defaults import` merges top-level keys. The role explicitly deletes obsolete registered policy keys after a successful import.
It also removes changed flattened keys while preserving unrelated saved fields.
Registered array fields permit flattened indices from 0 to 50, matching Firefox's parser limit.
Read-back refusals name affected public setting ids and never include private keys or values.
If cleanup requires a private flattened field name, the role refuses before importing policies.
Its recovery ownership record can still update before that refusal.

The shared map supplies nested policy paths, allowed preference keys, and the locked and editable forms.
Read-back uses the shared reader's `parse_source` function.
Mapped `Preferences` values use `Value` and the appropriate `Status`.
Policies with mapped sibling `Locked` controls receive the selected lock state automatically.
A selected explicit `Locked` value must agree with `firefox.lock`.
The module rejects conflicting aliases and overlapping parent and child policy selections before writing.
A setting with only an editable native seed can apply in either mode and reports that it cannot lock.
A setting without an editable form stays unset in false mode and reports that limit.
With `firefox.lock: false`, `firefox.homepage.start_page: homepage-locked` compiles to editable `homepage` and reports that conversion.
Both forms open the homepage. Only `homepage-locked` intrinsically locks the startup choice.
`firefox.homepage.start_page: none` requires an absent homepage URL or the exact URL `about:blank`.
The validator refuses another URL because Firefox replaces it with `about:blank`.
The same rule checks retained policies before a write.
These rules follow the pinned [Firefox Homepage handler](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/browser/components/enterprisepolicies/Policies.sys.mjs#L2274-L2352).
A stored policy or its read-back does not prove Firefox accepted it or that a user can change it.

The 16 unsupported Firefox rows have these limits:

1. Seven preferences need profile-file seeds because Firefox rejects their machine policy forms.
2. Four handlers are absent, removed, or ignored in Firefox 158.
3. Three extension or search installation entries can download resources and await approval of the download model.
4. Arbitrary `Preferences` objects bypass the typed map. Only individually mapped preferences can apply.
5. The role owns `EnterprisePoliciesEnabled`; a profile cannot turn off the loader for unrelated policies.

Every baseline remains proposed. The example profile selects no browser setting.
Only real-Mac verification can establish Firefox acceptance, lock behavior, and relaunch effects.

The role holds an exclusive lock at `/Library/Preferences/org.mac-setup.firefox-owned.lock` during each apply.
Another mac-setup Firefox run refuses until the first finishes. Check mode creates no lock file.
The role checks for external domain changes again before import.
An administrator writing through another tool does not share this lock, so avoid concurrent policy edits.
Retained policy aliases join the whole-domain conflict check before any import.

### Firefox policy-source detection

Firefox's native reader checks `EnterprisePoliciesEnabled` through the merged user-defaults view.
The [Firefox 158 native gate](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/xpcom/base/nsMacPreferencesReader.mm#L62-L77)
uses `boolForKey` before reading the merged dictionary.
The [loader calls that gate](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/toolkit/components/enterprisepolicies/EnterprisePoliciesParent.sys.mjs#L911-L920)
before parsing any platform policies.

The shared reader evaluates the switch across the supplied stores, from lower to higher priority.
Live capture supplies machine preferences, account preferences, machine managed preferences, then account managed preferences.
An absent switch does not override another store. A later explicit false disables plist policy observations.
A true switch enables the known policy roots across those stores.
Other roots remain app state, including window positions. Their values and names never enter the report.
The loader switch itself is a control and is excluded from the entry counts.

`policy_entries` counts enabled policy leaves. `unmapped_policy_entries` counts their unmapped leaves.
`app_state_entries` counts saved domain leaves excluded from policy observations.
The capture candidate reports these counts separately.
The role uses the same source classifier for stored-policy read-back and preserves unrelated app-state keys.
Source snapshots do not prove that a running browser accepted policies or that all preference sources were accessible.

Enablement follows Foundation's conversions, rather than requiring a Boolean plist object.
[Apple's UserDefaults reference](https://developer.apple.com/documentation/foundation/userdefaults/bool(forkey:))
documents numeric and string conversion. Nonzero numbers, `YES`, `true`, and `1` enable the source.
String conversion follows [NSString.boolValue](https://developer.apple.com/documentation/foundation/nsstring/boolvalue).

## Firefox bookmarks

`settings/macos/bookmarks.yml` defines the account setting `bookmarks.import_file` on route `bookmarks`.
The generic source can serve Firefox and Chrome importers. This repository currently routes it to Firefox.
This entry has no browser-map metadata because it imports account data rather than a machine policy.
The profile checker accepts a nonempty relative path without `..` parts, null bytes, or line breaks.
It refuses invalid paths without printing their values.
Before decryption, the role resolves links and refuses a source outside the profile root.
The account work dictionary contains `bookmarks_import: {source: VALUE}`, or an empty dictionary when omitted.

The source path starts at the parent of `inventory_dir`, the selected inventory folder.
For the README's profile, `files/firefox-bookmarks.vault` means `~/.dot/mac-setup/files/firefox-bookmarks.vault`.
Each account imports independently, after app installation and Firefox policy application.
The running account imports in an unprivileged play.
Other accounts import through `become_user` inside the machine play.

`macos_firefox_bookmarks` first calls its module with `operation: inspect` and Ansible's actual check-mode value.
Preview reads no private profile or export, starts no Firefox process, and writes nothing.
It reports `would_import` because identifying Firefox's default profile requires starting Firefox.
Apply starts Firefox before decryption and checks the completion record for the profile Firefox selects.
A completed import skips decryption and all temporary files, even when the source changes or disappears.
Firefox still briefly starts on completed apply runs and may update its own files during normal browser housekeeping.
For an import that remains pending, apply creates a private directory with mode `0700`.
Ansible's copy module decrypts the source into `bookmarks.html` with mode `0600` inside that directory.
The importer receives that plaintext path and the inspected profile identity.
It refuses a changed profile identity before bookmark writes. Cleanup removes the directory after the attempt.
Sensitive tasks disable logs and diffs. Refusals use fixed messages without arbitrary module error details.
An access denial preserves the specific instruction to grant the terminal app Full Disk Access or Firefox App Data access.
Startup failures expose only the check category `gui`, `launch`, `system_access`, or `automation` alongside their fixed error message.
The role and journey map those categories to fixed screen-login or startup guidance without echoing arbitrary error text.
The normal permission flow adds no prompt or permission requirement for bookmarks.

The module accepts `operation`, which defaults to `import`, and requires `source` only for import.
Optional `firefox_root` selects an isolated application data container for verification.
Optional `expected_profile_key` binds import to the profile found by inspection.
The module reports `changed`, the imported count, and its state.
Inspection states are `pending`, `already_imported`, `would_import`, and `not_installed`.
Real pending or completed inspections also return the opaque SHA-256 `profile_key`.
Import states are `imported`, `already_imported`, `would_import`, and `not_installed`.
An absent Firefox installation records a visible skip without decrypting the shared source.
A running Firefox instance in any account refuses import. Completed imports never refresh automatically.

The importer starts headless Firefox through LaunchServices without `-profile`, so Firefox chooses its normal default profile.
It uses `/usr/bin/open -n -g -W -a /Applications/Firefox.app` with arguments after `--args`.
The selected account must have a graphical login session. The importer checks `launchctl print gui/UID` by exit status before launching.
An SSH command can use that account's existing graphical session.
On macOS 27, Firefox launched directly from SSH can inherit SSH responsibility and lose protected-profile access.
LaunchServices is macOS's [application launcher](https://developer.apple.com/documentation/coreservices/launch_services).
It never inspects the protected Firefox folder. It communicates through Marionette, Firefox's automation interface.
Firefox creates the UUID preference `mac_setup.bookmarks.profile_id` only when absent.
It reads and saves that identifier through its own [preference service](https://firefox-source-docs.mozilla.org/modules/libpref/index.html).
The identifier is a random value that distinguishes profiles. Its SHA-256 hash names the private completion record.
Completion records live in `~/Library/Application Support/mac-setup/firefox-bookmarks/`, outside the protected Firefox folder.
The importer uses Firefox's Places bookmarks interface.
It reads and inserts through Mozilla's [`Bookmarks.fetch` and `Bookmarks.insert` APIs](https://searchfox.org/firefox-main/source/toolkit/components/places/Bookmarks.sys.mjs).
It never writes the Places database directly.
Mozilla's [`DisableProfileImport` handler](https://searchfox.org/firefox-main/source/browser/components/enterprisepolicies/Policies.sys.mjs)
blocks the profile-import user interface. It does not block these native bookmarks operations.
The importer leaves the configured policies in place.

The headless process uses [Marionette's environment configuration](https://searchfox.org/firefox-main/source/remote/components/Marionette.sys.mjs).
Each automation session sets `MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1` for Firefox's privileged browser context.
This value applies only to the launched session.
Firefox [158.0.1](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/remote/components/RemoteAgent.sys.mjs),
[159.0](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_159_0_RELEASE/remote/components/RemoteAgent.sys.mjs),
and [160.0](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/remote/components/RemoteAgent.sys.mjs) read this variable when creating RemoteAgent.
Releases 159 and 160 remove the older command-line flag handler, so the importer omits `--remote-allow-system-access`.
Firefox 160's [privileged-context check](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/remote/marionette/driver.sys.mjs#L423-L428)
requires that system-access state.
A refusal of privileged context or scripts retains the named `system_access` check through shutdown and cleanup.
The installed `open(1)` manual documents `--env NAME=VALUE`. Each required Mozilla variable is passed through that option.
Standard input, output, and error point to `/dev/null`, including Firefox's streams.
The launcher and Firefox have separate process identities. A random `about:blank` fragment identifies this launch without network access.
Initial process discovery requires the current account, exact executable path, complete fragment argument, and one matching main process.
The executable is independently checked with the system `proc_pidpath` function through Python's built-in `ctypes` module.
Monitoring retains the PID, account, and kernel birth time, including microseconds.
The birth fields come from Apple's [`proc_bsdinfo` structure](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h#L54-L77).
Discovery rechecks the token between matching kernel birth observations, so stale command data cannot bind a replacement process.
Monitoring verifies the kernel executable path on each observation.
Firefox can consume startup arguments without losing its established process identity.
Mozilla's [argument helper](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/toolkit/xre/CmdLineAndEnvUtils.h#L46-L54)
removes entries from its argument array. [Firefox startup](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/toolkit/xre/nsAppRunner.cpp)
also keeps a separate argument array for relaunches. These sources do not establish which argument changed in a particular `ps` observation.
Before profile access, a startup replacement can be discovered only with the original account, executable, and whole fragment argument.
The Marionette PID must match the independently discovered replacement before profile identity or bookmarks are accessed.
After profile access starts, a process exit fails the operation instead of transferring it to another process.
Signals require a fresh matching account, precise birth time, and kernel executable. A detected replacement receives no signal.
An observed PID with incomplete metadata refuses exit verification instead of counting as an exited process.
mac-setup briefly reserves a free loopback port, then passes that known port to Firefox's complete `marionette.port` preference.
It connects directly to that port and never reads a port file from Firefox's folder.
Before any protocol command, a narrow `lsof` query verifies that the bound process owns the exact server-side TCP connection.
The query selects only that PID and established TCP sockets on the chosen loopback port. It reads no browser profile files.
Ownership is polled within the startup deadline because connecting can finish before Firefox accepts the socket.
No protocol bytes are sent while that ownership remains unproved.
The process identity is checked before and after the query. The peer's reported PID is then checked as an additional condition.
A peer claiming Firefox's PID cannot replace the independent connection-owner check.
The importer requires [Marionette's normal shutdown acknowledgement](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/remote/marionette/driver.sys.mjs#L2848-L2921)
and the same Firefox process must exit before completion is recorded.
`open -W` waits for the launched application but does not expose Firefox's exit status.
A crash before acknowledgement or a shutdown timeout refuses completion. A crash after acknowledgement cannot be excluded by the launcher status.
Each imported folder and bookmark receives a stable identifier, so a partial retry skips items already committed by Firefox.
It preserves source duplicates and never joins existing bookmarks by address.
The completion marker has mode `0600` and appears only after that acknowledged shutdown and observed exit.

The bookmarks journey observes `capabilities.browserVersion` from its controlled setup session.
It saves only a numeric version or `unknown`, alongside the two startup-control booleans.
Before preview, the helper copies the safe setup fields to `reads/bookmarks-startup-control.json` in the evidence folder.
The version identifies the setup session. Firefox can update between sessions, so it does not describe every later session.
The Firefox catalog and browser-map baseline remains 158.0.1 until a new version receives native verification.

## Firefox appearance and add-ons

The appearance map uses Firefox 158 and 159 source definitions.
A saved value describes configuration. The disposable Firefox journey proves runtime behavior separately.
The following groups use ordinary Preferences policies where Firefox permits the prefix.

| # | Setting group | Storage and policy behavior |
| --- | --- | --- |
| 1 | Toolbar density and compact choice | `browser.uidensity` uses 0 normal, 1 compact, or 2 touch. `browser.compactmode.show` exposes the compact choice. `browser.touchmode.auto` selects touch spacing automatically. |
| 2 | Tabs and sidebar | `sidebar.verticalTabs`, `sidebar.revamp`, `sidebar.position_start`, `sidebar.expandOnHover`, and `sidebar.visibility` describe the layout. `browser.tabs.inTitlebar` and `browser.tabs.tabmanager.enabled` control the title bar and tab list. |
| 3 | Toolbar buttons | `browser.uiCustomization.state` stores JSON placements. Its policy always uses `Status: user` so Firefox can save rearrangements. A restart reapplies the saved policy layout. Unknown keys, widgets, or areas mask the entire captured layout. |
| 4 | Website colors | `layout.css.prefers-color-scheme.content-override` uses 0 dark, 1 light, or 2 automatic. This affects website color preferences. It does not activate a theme package. |
| 5 | Active theme | `extensions.activeThemeID` identifies light, dark, system, or an approved public theme. `browser.theme.toolbar-theme` and `browser.theme.content-theme` record derived colors. Capture reads these; application remains manual because existing theme installation can skip activation. |
| 6 | Fonts | Existing Western font rows capture reviewed public font names and bounded sizes. `font.default.x-western` captures serif or sans-serif. Firefox's Preferences handler rejects the `font.` prefix. These rows remain unsupported for application. |
| 7 | Default zoom | `browser.content.full-zoom` is a global content preference in `content-prefs.sqlite`. Capture selects only the row with null group id and this exact setting name. It refuses a nonempty SQLite journal or write-ahead log. Default zoom application remains manual. |
| 8 | Owner label | `firefox.owner_label` writes `Preferences.browser.mac-setup.owner`. The run summary includes the selected label. Capture masks the label because it can identify a person. |
| 9 | Public add-ons | `firefox.addons.install` compiles each id into separate `ExtensionSettings` installation mode and URL fields. Locked mode uses `force_installed`; editable mode uses `normal_installed`. Removing an id releases its owned fields and preserves unrelated fields. It does not uninstall the add-on. |

Except toolbar layout, supported appearance preferences follow `firefox.lock` with `locked` or `user` status.
User status sets an editable value each startup. A different manager can still impose a lock.
New preferences have no macOS proof until their real journey runs.

The allowed prefixes and statuses follow Mozilla's [Preferences reference](https://firefox-admin-docs.mozilla.org/reference/policies/preferences/).
The [Firefox policy implementation](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/browser/components/enterprisepolicies/Policies.sys.mjs) defines the accepted prefixes and startup order.
The [Firefox appearance defaults](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/browser/app/profile/firefox.js) define density and sidebar choices.
The [color definitions](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_159_0_RELEASE/modules/libpref/init/StaticPrefList.yaml) define color values.
The [toolbar serializer](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_159_0_RELEASE/browser/components/customizableui/CustomizableUI.sys.mjs) defines the stored layout.
The [theme installation guard](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_159_0_RELEASE/toolkit/components/enterprisepolicies/PoliciesHelpers.sys.mjs) can skip an existing theme.
The [zoom control](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_159_0_RELEASE/browser/components/preferences/config/accessibility.mjs) writes global content preferences.

Capture reads `extensions.json` metadata only. It never opens extension storage or settings.
An email-shaped id or a signature alone does not prove a public source.
Capture requires a public Mozilla signature and a saved HTTPS `addons.mozilla.org` download source.
The add-on must be visible, installed in the profile, and not sideloaded, temporary, hidden, or system-provided.
The public id and Firefox's saved default display name come from that record. No hand-kept catalog gates discovery.
The selected runtime language can show another localized name; capture cannot prove that selection offline.
The capture command performs no network lookup or installation.
An author-supplied update URL never establishes public provenance.
Mozilla signs unlisted extensions too, so a signature alone is insufficient.
Unknown user add-ons produce a manual-review count without ids or names.
Their presence marks add-on comparison as unverified.
System, hidden, and built-in entries have a separate excluded count and never enter the installation list.
They do not prevent capture of eligible public user add-ons.
An eligible add-on also needs explicit `active: true` and `userDisabled: false` metadata without application disablement.
Disabled, missing, malformed, or inconsistent activation evidence marks the install setting as unverified and withholds its profile assignment.
Capture reports a count for that gap. It does not turn a disabled source add-on into a force-install suggestion.
Mozilla's [add-on manager constants](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/toolkit/mozapps/extensions/AddonManager.sys.mjs)
define the accepted signing states.
Firefox's [extension database](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/toolkit/mozapps/extensions/internal/XPIDatabase.sys.mjs)
defines persisted source, location, visibility, signature, and display-name fields.
Mozilla documents [signing for listed and self-distributed extensions](https://extensionworkshop.com/documentation/publish/signing-and-distribution-overview/).
The manifest's [update URL](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/browser_specific_settings)
is controlled by the extension author.
The role installs captured public ids through the existing Mozilla latest-XPI URL.

`browser-settings diff` parses only the generated capture subset, using standard-library JSON parsing for assignment values.
It validates setting ids, scopes, values, duplicate keys, and regular input files before printing ids and comparison states.
Unknown observations remain named gaps even when both files contain the same unknown state.
Comparing two captures with zero observations returns exit 2.
Both parsers normalize toolbar JSON object order and spacing. Widget array order remains significant.
Matching absent states describe saved overrides only. They do not establish effective defaults.

`browser-settings check` accepts resolved browser-only `machine_settings` and `default_settings` JSON.
It uses the same saved-source reader and reports only ids and states.
It cannot resolve inventory or vault data. No automatic normalized export command exists yet.
Explicit locks, masked values, and missing required evidence return exit 3.
Explicit `unset` requests retain a separate release state and report an unverified gap with exit 3.
Saved profile values cannot prove policy ownership or release. `unset` remains invalid for a Boolean lock control.
Neither comparison command proves runtime enforcement or network silence.

### Firefox telemetry controls

`firefox.disable_telemetry: true` selects Mozilla's native DisableTelemetry policy.
It disables health reports, data submission, daily-use reporting, and telemetry archives.
`firefox.disable_firefox_studies: true` disables studies.
`firefox.firefox_suggest.improve_suggest: false` disables suggestion improvement sharing.
Each setting applies only when selected in the profile.

The separate `firefox.pref.browser_crash_reports_unsubmitted_check_auto_submit2: false` disables automatic submission of unsubmitted crash reports.
It follows `firefox.lock` through Preferences status and does not prevent manual submission.
Mozilla's pinned [crash handler](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/browser/modules/ContentCrashHandlers.sys.mjs) reads that preference.
The [default preference](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/browser/app/profile/firefox.js) is false.
Stored-policy tests verify compilation. Effective upload behavior remains unproven until tested in Firefox.

App-specific upload controls remain separate from macOS **Share with app developers**.
The new `privacy.improve_sensitive_content_warning: false` click-through setting opens Privacy & Security.
A person selects Sensitive Content Warning and turns off Improve Sensitive Content Warning.
No automatic switch or zero-telemetry guarantee follows from that click-through entry.

### Default web browser

The account setting `browser.default: firefox` uses route `default_browser`.
The final account play runs `macos_default_browser` after app installation.
It reads the current HTTP and HTTPS handlers through the LaunchServices read API.
An already-default choice changes nothing.
A project callback records controller terminal input and output when the playbook starts.
Task workers read that process-only signal and pass explicit interactive intent to the module.
The callback replaces any inherited signal on every run; a missing signal means unattended.
The module requires that intent and checks SSH, check mode, and ownership of the screen before any request.
It asks macOS once through the public NSWorkspace API and then reads both handlers again.
The run reports a change only after both identify Firefox.
A deferred confirmation stays a manual step.
Other accounts wait for their own session.

Apple documents the [NSWorkspace request](https://developer.apple.com/documentation/appkit/nsworkspace/setdefaultapplication(at:toopenurlswithscheme:completion:)).
Firefox 160 uses the same single HTTP request on macOS 27 in its
[Mac shell service](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/browser/components/shell/nsMacShellService.mm).
Its source reports that macOS also updates HTTPS and HTML handlers.
The module verifies both web-link handlers rather than assuming that result.
No defaults write, private registration database edit, or silent consent workaround is used.
