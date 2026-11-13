# Dock apps

One run of `playbooks/site.yml` keeps only the listed apps in the Dock of the account that runs it and of every listed account, by bundle id and in the listed order, and removes every other app tile. The folders side of the Dock stays as it is. For the account that runs it, the Dock restarts once so the change shows at once. Other accounts see the change at their next login. A second run changes nothing, and a bundle id with no installed app changes nothing.

## Sub-features

- `dock-apps-preview` reports the Dock apps as changed and writes nothing.
- `dock-apps-apply` writes `persistent-apps` in `com.apple.dock` and restarts the Dock once.
- `dock-apps-again` reports `changed=0` when the Dock already holds the listed apps in order.
- `dock-apps-not-installed` prints one refused line for `dock.applications`, leaves the Dock as it is, and exits 2.

## How to get to it (user POV)

- Put `dock.applications` under `default_settings` in the profile, or under one account's `settings`, as a list of bundle ids, for example `[com.apple.Terminal, com.apple.Safari]`.
- Run `.local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K --check --diff` to preview. The diff lists the bundle ids in the Dock now and after the run.
- Run `.local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K` to apply, then the same command again.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real Dock: the harness saves the Dock, the setup empties its app tiles, the apply leaves only Terminal and Safari, and the harness puts the saved Dock back.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- The running account is signed in at the screen, so the Dock runs and its restart reports `changed`.
- The module finds each app with `mdfind`, and scans `/Applications`, `/System/Applications` and `/System/Applications/Utilities` when Spotlight has not indexed it yet.

- **Setup.** The journey's `saves` holds the whole `com.apple.dock` domain, so the harness saves it before setup. The setup then empties the Dock's app tiles with `defaults write com.apple.dock persistent-apps -array`, so the first run has something to change.
- **Preview.** Check run: exit 0, `changed=1 failed=0`.
- **Apply.** Exit 0, `changed=2 failed=0`: the Dock apps and 1 Dock restart. The handler `Restart Dock` reports `changed`.
- **Apply again.** Exit 0, `changed=0 failed=0`, and no Dock restart.
- **Not installed.** The profile lists `com.example.notinstalled` after Terminal. Exit 2, `changed=0 failed=1 rescued=1`, and one refused line naming `dock.applications`.
- **Proof.** The cleanup step reads `persistent-apps` with `defaults export com.apple.dock -` and `plutil` and prints `Dock bundle ids: com.apple.Terminal com.apple.Safari`. It fails the journey when the ids it read differ. After it, the harness imports the saved domain with `defaults import` and restarts the Dock when the running account owns the screen. It runs after the not-installed run, so it also proves that run changed nothing.

## Gotchas

- The running account's Dock is set before apps install. Install an app first, then add its bundle id on a later run. A missing app refuses the whole Dock list and ends the run with exit code 2, while other settings and app installs continue.

- The harness's `reads` compare a whole key, and each Dock tile holds values macOS sets itself, such as `GUID` and `book`. So the proof reads the bundle ids in the cleanup step, which logs the ids it found in `logs/dock-apps-cleanup.txt`.
- The setup empties the Dock's app tiles, and the Dock shows the old tiles until it restarts. The harness always restores the saved domain, also after a failed run. The restore writes the saved Dock domain back with `defaults import`, so a Dock change made by hand during the journey is lost. Whether a key macOS added during the run is removed is not tested.
- The apply's `changed=2` needs the account signed in at the screen. With no Dock running, the restart has nothing to do and the apply shows `changed=1`.
- A listed app already in the Dock keeps its tile. A new one gets a tile with its bundle id, name and file URL, and the Dock may add its own fields, such as `GUID` and `book`, later. The module compares bundle ids only, so such fields never count as a change.
- The module searches for each app with `mdfind`, then the three app folders above. When several copies exist, it prefers one in `/Applications/` or `/System/Applications/`. A bundle that is not an `.app`, such as a plug-in, is not found, and neither is an app on a disk Spotlight does not index.
