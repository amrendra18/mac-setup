# Remaining stores

One run of `playbooks/site.yml` sets 6 settings that need more than one plain key, for the account that runs it. They are the system keyboard shortcuts, the Mission Control shortcuts, the list of input sources, the App Exposé gesture, the speaker volume and the alert volume. The journey also sets the Mission Control gesture, because it shares two keys with the App Exposé gesture. The Dock restarts once, so the gestures work at once. The shortcuts and input sources show after a logout. The volumes change at once. A second run changes nothing.

## Sub-features

- `remaining-preview` reports 9 changes and writes nothing.
- `remaining-apply` writes 2 nested values, 1 array and 4 trackpad and Dock keys, sets 2 volumes, and restarts the Dock once.
- `remaining-again` reports `changed=0` when every value already holds.

## How to get to it (user POV)

- Put the settings under `default_settings` in the profile. `inventory/example/TEMPLATE.yml` lists each one with its choices. For example:

```yaml
default_settings:
  input.keyboard_shortcuts:
    spotlight_search: command+option+space
    screenshot_options: false
  mission_control.shortcuts:
    mission_control: control+up
  input.input_sources: [abc, emoji_and_symbols]
  input.app_expose_gesture: three_finger
  sound.alert_volume: 50
```

- A shortcut value is `true` (on with its standard keys), `false` (off), or keys joined with `+`, such as `control+up`. A letter, digit or space needs at least one of shift, control, option or command.
- The profile check refuses the two trackpad gestures when both are on with different fingers, and refuses `unset` for the shortcuts and the input sources.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K --check --diff` to preview.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K` to apply, then the same command again.
- Log out and back in to see the shortcuts and the input sources.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real settings. The harness and the cleanup put each value back as it was.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- The running account is signed in at the screen, so the Dock runs and its restart reports `changed`, and `osascript` reaches the sound settings.
- The sound output has a volume control. An output that reads `missing value`, such as some displays, stops the setup.

- **Setup.** The journey's `saves` lists 4 trackpad and Dock keys and the whole `com.apple.symbolichotkeys` and `com.apple.HIToolbox` domains, so the harness saves them before setup. The setup stops if `.local/remaining-journey/volume.txt` is left from an earlier run whose cleanup did not finish. It saves the output volume, the alert volume and the mute state to `volume.txt`. Only then does it delete the 4 keys, `AppleSymbolicHotKeys` and `AppleEnabledInputSources`, and set both volumes to 0, so the first run changes every setting.
- **Preview.** Check run: exit 0, `changed=9 failed=0`. That is 1 nested write for each shortcut setting (2), 1 array write for the input sources (1), 1 Dock key for the Mission Control gesture set to off (1), 3 keys for the App Exposé gesture (3), and 1 command for each volume (2): 2 + 1 + 1 + 3 + 2 = 9.
- **Apply.** Exit 0, `changed=10 failed=0`. That is the 9 changes and 1 Dock restart. Both gestures ask for the Dock restart, and it runs once. The shortcuts and input sources take effect at logout and restart nothing. Each volume command reads the volume again after the set and fails unless it reads 50 and 75.
- **Apply again.** Exit 0, `changed=0 failed=0`, and no Dock restart. The volume reads show 50 and 75, so no command runs.
- **Proof.** After the last run, the harness reads every key with `defaults export DOMAIN -` and checks its type and value:

| # | Setting | Domain | Key | Type | Value |
| --- | --- | --- | --- | --- | --- |
| 1 | `input.keyboard_shortcuts` and `mission_control.shortcuts` | `com.apple.symbolichotkeys` | `AppleSymbolicHotKeys` | dictionary | 5 entries: `184` and `65` hold only `enabled` (false, then true); `32`, `36` and `118` hold the keys 65535 126 8650752, 65535 103 8388608 and 49 18 262144 |
| 2 | `input.input_sources: [british, canadian, emoji_and_symbols]` | `com.apple.HIToolbox` | `AppleEnabledInputSources` | array | British (layout 2), Canadian (layout 29), then the Emoji & Symbols viewer |
| 3 | `input.mission_control_gesture: off` | `com.apple.dock` | `showMissionControlGestureEnabled` | boolean | `false` |
| 4 | `input.app_expose_gesture: four_finger` | `com.apple.AppleMultitouchTrackpad` | `TrackpadThreeFingerVertSwipeGesture` | integer | `0` |
| 5 | `input.app_expose_gesture: four_finger` | `com.apple.AppleMultitouchTrackpad` | `TrackpadFourFingerVertSwipeGesture` | integer | `2` |
| 6 | `input.app_expose_gesture: four_finger` | `com.apple.dock` | `showAppExposeGestureEnabled` | boolean | `true` |

- **Cleanup.** The cleanup sets the saved volumes and mute state again and deletes `volume.txt`. It runs also after a failed run, and it skips the volume when the setup did not save it. Then the harness restore writes each saved domain back with `defaults import`, writes each saved trackpad and Dock key back or deletes it if it was absent, and restarts the Dock when the running account owns the screen. Whether a key macOS added during the run is removed, and whether a running app keeps its own copy until it reloads, is not tested.

## Gotchas

- Row 1 compares the whole dictionary, so it holds only the 5 journey entries. If macOS adds a shortcut entry by itself during the run, row 1 fails, and the journey reports it.
- `screenshot_options: false` turns off Command-Shift-5 until the restore. The harness restores the keys, but the old shortcuts may show only after the next logout.
- Removing `AppleEnabledInputSources` at setup leaves the account with no listed input source until the apply. macOS may add one back by itself, and then the preview may show the array as changed from that value. The count stays 9.
- The off value for each gesture writes only its own Dock key and leaves the finger keys alone. That the Dock key alone turns the gesture off is inferred, not measured.
- The character codes for a letter, digit or space follow what a test Mac with macOS 15 stores for Control-Space. The digit 1 is stored as 49 here, while community lists show 65535 for Desktop 1. If macOS rewrites entry 118, row 1 fails.
- A set of the output volume may also unmute the output. The cleanup restores the saved mute state either way.
- The volume is not a `defaults` key the harness can read, so the volume commands prove themselves: each set reads the volume again, and the second run reads both volumes as wanted.
- A `saved.plist` or `volume.txt` left in `.local/remaining-journey/` by an unfinished restore or cleanup holds the account's own values. The next run refuses to overwrite it and fails the journey. That run's restore and cleanup put the old values back and delete the files, so the run after it works.
