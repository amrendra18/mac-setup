# Dock basics

One run of `playbooks/site.yml` puts the Dock of the account that runs it on the left at icon size 32 with recent apps hidden. The Dock restarts once so the change shows at once. A second run changes nothing, and a broken profile stops the run before anything changes.

## Sub-features

- `dock-preview` reports the 3 settings that would change and writes nothing.
- `dock-apply` writes `orientation`, `tilesize` and `show-recents` in `com.apple.dock` and restarts the Dock once.
- `dock-again` reports `changed=0` when the Dock already matches.
- `dock-bad-profile` stops in the profile check with exit 2 when `dock.position` is `middle`, before any write.

## How to get to it (user POV)

- Put `dock.position`, `dock.tile_size` and `dock.show_recents` under `default_settings` in the profile.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml --check` to preview.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml` to apply, then the same command again.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real Dock.
- `scripts/bootstrap` has run in this checkout.

- **Setup.** The journey deletes the 3 Dock keys with `defaults delete com.apple.dock KEY || true`, so the first run starts as on a fresh Mac.
- **Preview.** Run `ansible-playbook playbooks/site.yml --check`. Exit 0 with recap `changed=3 failed=0`.
- **Apply.** Run `ansible-playbook playbooks/site.yml`. Exit 0 with recap `changed=4 failed=0`: 3 keys and 1 Dock restart. The Dock moves to the left.
- **Apply again.** Run the same command. Exit 0 with recap `changed=0 failed=0`.
- **Proof.** `reads/dock-basics-after.json` holds `orientation` left (string), `tilesize` 32 (float) and `show-recents` false (boolean).
- **Planted failure.** Run `scripts/verify fast --feature dock-basics --profile tests/inputs/bad-position.yml`. Exit 1, the output line names `dock-basics`, the check run's log shows the profile check stopping with exit 2, and `summary.json` has `reads_unchanged: true`.

## Gotchas

- The setup deletes the 3 keys, and the Dock keeps showing the old look until it restarts. After a planted failure the Dock returns to the macOS defaults at its next restart.
- `tilesize` is a float. A stored integer makes `osx_defaults` fail with a type mismatch. The setup's delete avoids that.
- The reads go through `defaults export`, so they see what the preferences system holds, and not only what is on disk.
