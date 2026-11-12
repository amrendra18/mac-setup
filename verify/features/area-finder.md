# Finder area

One run of `playbooks/site.yml` writes 6 Finder settings of the account that runs it from the profile. They show the sidebar, empty old Trash items after 30 days, use list view, hide Recent Tags, show hidden files, and open new windows in the home folder. Finder restarts once so the change shows at once. A second run changes nothing.

## Sub-features

- `area-finder-preview` reports the 6 settings as changed and writes nothing.
- `area-finder-apply` writes 6 keys in `com.apple.finder` and restarts Finder once.
- `area-finder-again` reports `changed=0` when Finder already matches, and does not restart Finder.

## How to get to it (user POV)

- Put `finder.show_sidebar`, `finder.trash_auto_empty`, `finder.view`, `finder.show_recent_tags`, `finder.show_hidden` and `finder.new_window` under `default_settings` in the profile.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K --check` to preview.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K` to apply, then the same command again.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real Finder settings. The cleanup puts back the saved keys.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- The running account is signed in at the screen, so Finder runs and its restart reports `changed`.
- `scripts/bootstrap` has run in this checkout.

- **Setup.** The journey stops if `.local/area-finder-journey/finder-before.plist` is left from an earlier run whose cleanup did not finish; that run's cleanup restores it. Then the journey saves the whole `com.apple.finder` domain to `.local/area-finder-journey/finder-before.plist`. It then deletes the 6 keys with `defaults delete com.apple.finder KEY || true`, so the first run changes each setting.
- **Preview.** Check run: exit 0, `changed=6 failed=0`.
- **Apply.** Exit 0, `changed=7 failed=0`: 6 keys and 1 Finder restart. The handler `Restart Finder` reports `changed`.
- **Apply again.** Exit 0, `changed=0 failed=0`, and no Finder restart.
- **Proof.** `reads/area-finder-after.json` holds these values in `com.apple.finder`:

| # | Setting | Key | Type | Value |
| --- | --- | --- | --- | --- |
| 1 | `finder.show_sidebar: true` | `ShowSidebar` | boolean | true |
| 2 | `finder.trash_auto_empty: true` | `FXRemoveOldTrashItems` | boolean | true |
| 3 | `finder.view: list` | `FXPreferredViewStyle` | string | `Nlsv` |
| 4 | `finder.show_recent_tags: false` | `ShowRecentTags` | boolean | false |
| 5 | `finder.show_hidden: true` | `AppleShowAllFiles` | boolean | true |
| 6 | `finder.new_window: home` | `NewWindowTarget` | string | `PfHm` |

- **Cleanup.** For each of the 6 keys, the cleanup writes back the saved value with its saved type, or deletes the key when it was absent before. It logs one line per key in `logs/`, deletes `finder-before.plist`, then restarts Finder.

## Gotchas

- The apply's `changed=7` needs the account signed in at the screen. With no Finder running for that account, the restart is skipped and the apply shows `changed=6`.
- The restart closes every Finder window and stops a Finder copy in progress.
- The cleanup restores only the 6 keys. The saved file holds the whole domain, so a person can restore other keys from it by hand.
- The cleanup restores a saved boolean, string, integer or float. A saved key of another type stops the cleanup with exit 1 and names the saved file.
- A key stored with another type than the reference, such as `AppleShowAllFiles` as the string `YES`, makes `osx_defaults` refuse the setting with a type mismatch. The setup's delete avoids that in the journey.
- Guess, not measured: Finder writes `ShowSidebar` itself, because a test Mac had it with no setting made. If Finder writes it back after its restart, the second run shows `changed=1`.
- The reads prove the stored values. They do not prove that Finder shows them. A person checks the screen. Folders with their own saved view ignore `FXPreferredViewStyle`.
