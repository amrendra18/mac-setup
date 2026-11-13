# Per-account settings

An admin run writes the settings of each other listed account as that account, into that account's own preferences. The admin's own Dock stays as it is. The settings show at that account's next login. A second run changes nothing, and the value `unset` deletes a key so macOS uses its own default.

## Sub-features

- `per-account-preview` reports the 3 Dock settings of `setuptest1` that would change and writes nothing.
- `per-account-apply` writes `orientation`, `tilesize` and `show-recents` into `setuptest1`'s own `com.apple.dock` preferences, with no Dock restart.
- `per-account-again` reports `changed=0` when the account already matches.
- `per-account-unset` deletes `show-recents` for `setuptest1` when the profile says `unset`.

## How to get to it (user POV)

- Add a `settings` map to an account under `accounts` in the profile.
- Run `.local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K`.
- Sign in to that account and look at its Dock.

## Driving it with scripts/verify

Preconditions:

- `setuptest1` exists. Run `scripts/verify fast --feature accounts` first; `scripts/verify fast` runs it first by name order.
- The running account is an admin with password-free sudo.

- **Setup.** The journey stops with a message when `setuptest1` is missing. It then deletes the 3 Dock keys as `setuptest1` with `sudo -n -u setuptest1 defaults delete`, so the first run starts as on a fresh account.
- **Preview.** Check run: exit 0, `changed=3 failed=0`.
- **Apply.** Exit 0, `changed=3 failed=0`. `reads/per-account-apply.json`, read as `setuptest1`, holds `orientation` left (string), `tilesize` 32 (float) and `show-recents` false (boolean).
- **Apply again.** Exit 0, `changed=0 failed=0`.
- **Unset.** The run's own profile sets `dock.show_recents: unset`. Exit 0, `changed=1 failed=0`. The same profile again: exit 0, `changed=0 failed=0`.
- **Proof.** `reads/per-account-after.json`, read as `setuptest1`, holds `orientation` left, `tilesize` 32 and no `show-recents` key. `reads/per-account-files.json` shows `~setuptest1/Library/Preferences/com.apple.dock.plist` with owner `setuptest1` and mode 600.

## Gotchas

- The settings show only after `setuptest1` logs in. The run restarts no Dock for another account.
- An absent key reads as `absent` in the harness, which `defaults export` reports. `defaults read-type com.apple.dock show-recents` as that account exits 1 for the same state.
