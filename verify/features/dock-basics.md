# Dock basics

One run of `playbooks/site.yml` puts the Dock of the account that runs it on the left at icon size 32 with recent apps hidden. The Dock restarts once so the change shows at once. A second run changes nothing, and a broken profile stops the run before anything changes.

## Sub-features

- `dock-preview` reports the 3 settings that would change and writes nothing.
- `dock-apply` writes `orientation`, `tilesize` and `show-recents` in `com.apple.dock` and restarts the Dock once.
- `dock-again` reports `changed=0` when the Dock already matches.
- `dock-bad-profile` stops in the profile check with exit 2 when `dock.position` is `middle`, before any write.

## How to get to it (user POV)

- Put `dock.position`, `dock.tile_size` and `dock.show_recents` under `default_settings` in the profile.
- Run `.local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K --check` to preview.
- Run `.local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K` to apply, then the same command again.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real Dock.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- `scripts/bootstrap` has run in this checkout.

- **Setup.** The journey's `saves` lists the 3 Dock keys, so the harness saves the type and value of each, or that it is absent, before setup. The setup deletes them with `defaults delete com.apple.dock KEY || true`, so the first run starts as on a fresh Mac. A save left by an unfinished restore fails the next run before setup, and that run's restore puts it back.
- **Preview.** Run `ansible-playbook playbooks/site.yml -K --check`. Exit 0 with recap `changed=3 failed=0`.
- **Apply.** Run `ansible-playbook playbooks/site.yml -K`. Exit 0 with recap `changed=4 failed=0`: 3 keys and 1 Dock restart. The Dock moves to the left.
- **Apply again.** Run the same command. Exit 0 with recap `changed=0 failed=0`.
- **Proof.** `reads/dock-basics-after.json` holds `orientation` left (string), `tilesize` 32 (float) and `show-recents` false (boolean).
- **Cleanup.** The journey has no cleanup commands. The harness restore puts the 3 keys back to their saved values and types, or deletes a key that was absent, then restarts the Dock when the running account owns the screen.
- **Planted failure.** Run `scripts/verify fast --feature dock-basics --profile tests/inputs/bad-position.yml`. Exit 1, the output line names `dock-basics`, the check run's log shows the profile check stopping with exit 2, and `summary.json` has `reads_unchanged: true`.

## Gotchas

- The setup deletes the 3 keys, and the Dock keeps showing the old look until it restarts. After a planted failure the harness restores the saved keys and restarts the Dock.
- The apply's `changed=4` needs the running account to own the screen. When another account or the login window owns it, the run skips the Dock restart and the apply shows `changed=3`. A restart then would leave that account's Dock stopped until it logs out and back in.
- `tilesize` is a float. A stored integer makes `osx_defaults` fail with a type mismatch. The setup's delete avoids that.
- The reads go through `defaults export`, so they see what the preferences system holds, and not only what is on disk.
