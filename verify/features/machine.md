# Machine settings

One run of `playbooks/site.yml` sets the machine settings listed under `machine_settings` that a command tool stores. Play 2 runs as admin and sets each one with the tool its reference entry names, then reads it back with the same tool. This journey covers 4 power settings. The time zone is not in it: without Full Disk Access, `systemsetup -settimezone` prints Error -99 and changes nothing, over SSH and in a local Terminal (measured on macOS 27.2.1). A second run changes nothing.

## Sub-features

- `machine-preview` reports each of the 4 settings as changed and writes nothing.
- `machine-apply` sets the 4 power settings with `pmset`, and each set is confirmed by a second read.
- `machine-again` reports `changed=0` when the Mac already holds the 4 values.

## How to get to it (user POV)

- Put the settings under `machine_settings` in the profile, for example:

  ```yaml
  machine_settings:
    power.system_sleep_minutes: 0
    power.display_sleep_minutes: 0
    power.wake_for_network: true
    power.restart_after_power_failure: true
  ```

- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K --check --diff` to preview.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K` to apply, then the same command again.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the Mac's real power settings: the setup saves them and sets other values, the apply sets the profile's values, and the cleanup puts the saved values back.
- The running account is an admin with password-free sudo, because the harness passes no `-K`, and the setup and cleanup run `pmset` through `sudo -n`.
- `pmset -g custom` lists `sleep`, `displaysleep`, `womp` and `autorestart` for every power source.

- **Setup.** The journey makes `.local/machine-journey/` and saves the output of `pmset -g custom` to `pmset-before.txt`. It stops before any change when a value is missing. It then runs `sudo -n pmset -a sleep 30 displaysleep 10 womp 0 autorestart 0`, so each of the 4 settings differs from the profile.
- **Preview.** Check run: exit 0, `changed=4 failed=0`, and 4 `Set` tasks of `macos_commands` report `changed`. The 4 come from 1 module task per setting. No other task in either play changes anything for this profile: there are no accounts, apps, Dock or Finder settings, and the remaining tasks read or are skipped.
- **Apply.** Exit 0, `changed=4 failed=0`, 4 `Set` tasks report `changed`. After each set, the module reads again and fails unless the read shows the profile's value.
- **Apply again.** Exit 0, `changed=0 failed=0`, and the 4 `Set` tasks report `ok`.
- **Proof.** The cleanup step reads `pmset -g custom`, the command the module reads with, and prints it to `logs/machine-cleanup.txt`. It restores each saved power value per power source (`-b` battery, `-c` charger, `-u` UPS), checks that the read matches the saved values, and removes `.local/machine-journey/`. Last, it fails the journey unless every power source showed `sleep 0`, `displaysleep 0`, `womp 1` and `autorestart 1`.

## Gotchas

- The harness's `reads` read only `defaults export`, and these settings live outside the preferences system. So the proof reads in the cleanup step, which runs after the last run, as in the Dock apps journey.
- If `.local/machine-journey/` already exists, a run stopped before its cleanup. The setup then fails at its first step. The cleanup restores the values saved by that earlier run and removes the folder, so the next run starts clean. A change made by hand between the two runs is overwritten with those saved values.
- The module sets power values with `pmset -a`, for every power source, and reads the first source that `pmset -g custom` lists. On a laptop, the cleanup's proof also checks the other sources.
- A Mac that does not list `womp` or `autorestart` in `pmset -g custom`, such as some virtual machines, fails at setup step 2 before any change.
