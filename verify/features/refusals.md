# Refused settings

When macOS refuses one setting at write time, the run still writes the other settings and restarts the Dock. The summary prints one line for each refused setting, and the run ends with exit code 2.

## Sub-features

- `refusals-preview` reports the refused setting in the check run and still exits 2.
- `refusals-apply` writes the 2 other Dock keys, restarts the Dock once, prints one refused line for `dock.tile_size`, and exits 2.
- `refusals-again` prints the same refused line, changes nothing, and does not restart the Dock.

## How to get to it (user POV)

- A key already holds a value of another type, for example `tilesize` stored as an integer.
- Run `.local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K`. The last lines name the refused setting, and the recap shows `failed=1`.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real Dock.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- The running account owns the screen, so the run restarts its Dock and the restart reports `changed`.

- **Setup.** The journey's `saves` lists the 3 Dock keys, so the harness saves them before setup. The setup deletes `orientation` and `show-recents`, and writes `tilesize` as the integer 48 with `defaults write com.apple.dock tilesize -int 48`. `dock.tile_size` writes a float, so `osx_defaults` refuses it with a type mismatch.
- **Preview.** Check run: exit 2, `changed=2 failed=1`, one refused line naming `dock.tile_size`.
- **Apply.** Exit 2, `changed=3 failed=1`: 2 keys and 1 Dock restart. One refused line naming `dock.tile_size`, and the handler `Restart Dock` reports `changed`.
- **Apply again.** Exit 2, `changed=0 failed=1`, the same refused line, and no Dock restart.
- **Proof.** `reads/refusals-after.json` holds `orientation` left (string), `show-recents` false (boolean) and `tilesize` 48 (integer).
- **Cleanup.** The journey has no cleanup commands. The harness restore puts `orientation`, `tilesize` and `show-recents` back to their saved values and types, or deletes a key that was absent, then restarts the Dock when the running account owns the screen. A save left by an unfinished restore fails the next run before setup, and that run's restore puts it back.

## Gotchas

- A refused line is a line of the run's output that holds the word `refused` and then the setting id. The pattern skips `TASK` and `RUNNING HANDLER` headers, the `[ERROR]` line, and the numbered source lines under `Origin:`. The journey expects exactly 1 such line per run.
- ansible-core 2.21.5 prints a `fail` message twice: after `[ERROR]: Task failed: Action failed:` and under `msg:`. The pattern counts only the second. If the summary also prints the refused lines with `debug`, each line counts twice and the journey fails.
- The rescue records every failed write as refused, whatever the cause, including a failure to act as another account. The refused line's message says which.
- `failed=1` comes from the summary task, which fails on purpose when a setting was refused. The refused write itself is rescued and not counted as failed.
