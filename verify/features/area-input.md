# Input area

One run of `playbooks/site.yml` writes 15 keyboard, trackpad and mouse settings of the account that runs it. The keyboard settings are automatic capitals, the double-space period and the Input menu in the menu bar. The Magic Mouse settings are right-click, smart zoom, the Mission Control double-tap and the swipe between full-screen apps. The trackpad settings are right-click, tap to click, three-finger drag, double-tap drag, pinch to zoom, rotate, smart zoom and the Notification Center swipe. They take 27 `defaults` keys, because 12 settings write 2 keys each. No process restarts. 3 settings are reported to show after a logout. No test has measured when the other 12 appear. A second run changes nothing.

## Sub-features

- `area-input-preview` reports the 27 keys as changed and writes nothing.
- `area-input-apply` writes the 27 keys, restarts nothing, and its summary prints `log out and back in to see 3 settings` and `12 settings may need a logout or an app restart to show; their timing is not measured`.
- `area-input-again` reports `changed=0` when every key already holds its value.

## How to get to it (user POV)

- Put the settings under `default_settings` in the profile, for example `input.tap_to_click: "off"`, `input.secondary_click: two_finger` and `input.show_input_menu: false`. Quote `"on"` and `"off"`, or YAML reads them as true and false. `inventory/example/TEMPLATE.yml` lists each one with its choices.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K --check` to preview.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K` to apply, then the same command again.
- Log out and back in to see tap to click, three-finger drag and double-tap drag. No test has measured when the other settings appear. Log out and back in, or quit and reopen an app, if you do not see them.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real keyboard, trackpad and mouse settings. The harness puts each saved key back.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- `scripts/bootstrap` has run in this checkout.

- **Setup.** The journey's `saves` lists the 27 keys, so the harness saves the type and value of each, or that it is absent, before setup. A key whose type is not boolean, integer, float or string fails the journey before setup deletes any key. The setup then deletes each key that exists, so the first run changes every key.
- **Preview.** Check run: exit 0, `changed=27 failed=0`. That is 1 per key. 3 settings write 1 key each, and 12 write 2 each (3 + 24 = 27).
- **Apply.** Exit 0, `changed=27 failed=0`. No handler runs, because no setting's `takes_effect` is `dock`, `finder` or `systemuiserver`. The summary counts settings, not keys. 3 settings have `takes_effect: logout` (`tap_to_click`, `three_finger_drag`, `trackpad_tap_drag`), and 12 have `not measured`. So it prints `log out and back in to see 3 settings` and `12 settings may need a logout or an app restart to show; their timing is not measured`.
- **Apply again.** Exit 0, `changed=0 failed=0`.
- **Proof.** After the last run, the harness reads every key with `defaults export DOMAIN -` and checks its type and value. In the table, `AMMouse` stands for `com.apple.AppleMultitouchMouse`, `BTMouse` for `com.apple.driver.AppleBluetoothMultitouch.mouse`, `AMTrackpad` for `com.apple.AppleMultitouchTrackpad`, and `BTTrackpad` for `com.apple.driver.AppleBluetoothMultitouch.trackpad`.

| # | Setting | Domain | Key | Type | Value |
| --- | --- | --- | --- | --- | --- |
| 1 | `input.auto_capitalize: true` | `NSGlobalDomain` | `NSAutomaticCapitalizationEnabled` | boolean | `true` |
| 2 | `input.double_space_period: true` | `NSGlobalDomain` | `NSAutomaticPeriodSubstitutionEnabled` | boolean | `true` |
| 3 | `input.mouse_mission_control: "on"` | `AMMouse` | `MouseTwoFingerDoubleTapGesture` | integer | `3` |
| 4 | `input.mouse_mission_control: "on"` | `BTMouse` | `MouseTwoFingerDoubleTapGesture` | integer | `3` |
| 5 | `input.mouse_secondary_click: "off"` | `AMMouse` | `MouseButtonMode` | string | `OneButton` |
| 6 | `input.mouse_secondary_click: "off"` | `BTMouse` | `MouseButtonMode` | string | `OneButton` |
| 7 | `input.mouse_smart_zoom: "off"` | `AMMouse` | `MouseOneFingerDoubleTapGesture` | integer | `0` |
| 8 | `input.mouse_smart_zoom: "off"` | `BTMouse` | `MouseOneFingerDoubleTapGesture` | integer | `0` |
| 9 | `input.mouse_swipe_full_screen_apps: "on"` | `AMMouse` | `MouseTwoFingerHorizSwipeGesture` | integer | `2` |
| 10 | `input.mouse_swipe_full_screen_apps: "on"` | `BTMouse` | `MouseTwoFingerHorizSwipeGesture` | integer | `2` |
| 11 | `input.notification_center_gesture: "on"` | `AMTrackpad` | `TrackpadTwoFingerFromRightEdgeSwipeGesture` | integer | `3` |
| 12 | `input.notification_center_gesture: "on"` | `BTTrackpad` | `TrackpadTwoFingerFromRightEdgeSwipeGesture` | integer | `3` |
| 13 | `input.pinch_zoom: "on"` | `AMTrackpad` | `TrackpadPinch` | integer | `1` |
| 14 | `input.pinch_zoom: "on"` | `BTTrackpad` | `TrackpadPinch` | integer | `1` |
| 15 | `input.rotate: "on"` | `AMTrackpad` | `TrackpadRotate` | integer | `1` |
| 16 | `input.rotate: "on"` | `BTTrackpad` | `TrackpadRotate` | integer | `1` |
| 17 | `input.secondary_click: two_finger` | `AMTrackpad` | `TrackpadRightClick` | boolean | `true` |
| 18 | `input.secondary_click: two_finger` | `AMTrackpad` | `TrackpadCornerSecondaryClick` | integer | `0` |
| 19 | `input.show_input_menu: false` | `com.apple.TextInputMenu` | `visible` | boolean | `false` |
| 20 | `input.smart_zoom: "on"` | `AMTrackpad` | `TrackpadTwoFingerDoubleTapGesture` | integer | `1` |
| 21 | `input.smart_zoom: "on"` | `BTTrackpad` | `TrackpadTwoFingerDoubleTapGesture` | integer | `1` |
| 22 | `input.tap_to_click: "off"` | `AMTrackpad` | `Clicking` | integer | `0` |
| 23 | `input.tap_to_click: "off"` | `BTTrackpad` | `Clicking` | integer | `0` |
| 24 | `input.three_finger_drag: false` | `AMTrackpad` | `TrackpadThreeFingerDrag` | boolean | `false` |
| 25 | `input.three_finger_drag: false` | `BTTrackpad` | `TrackpadThreeFingerDrag` | boolean | `false` |
| 26 | `input.trackpad_tap_drag: "off"` | `AMTrackpad` | `Dragging` | integer | `0` |
| 27 | `input.trackpad_tap_drag: "off"` | `AMTrackpad` | `DragLock` | integer | `0` |

- **Cleanup.** The journey has no cleanup commands. The harness restore writes each saved key back with its saved type and value, and deletes each key that was absent. It restarts nothing. It changes only these 27 keys, so the rest of each domain stays as it is.

## Gotchas

- The recap counts keys and the summary counts settings. A count of 26 on the apply means one key did not change.
- The reads prove what is stored. Only a look at System Settings proves that macOS shows the matching choice. The map numbers for right-click, tap to click, double-tap drag and the trackpad and Magic Mouse gestures come from community sources and one test Mac.
- Trackpad and Magic Mouse keys sit in 2 domains, one for the built-in device and one for the Bluetooth device. A Mac with no trackpad or Magic Mouse stores the keys but shows no change.
- `input.secondary_click` and `input.trackpad_tap_drag` write only the built-in trackpad domain. The Bluetooth trackpad domain may hold its own copies of their keys, and the run leaves those as they are.
- macOS also keeps `-currentHost` copies of some trackpad keys in `NSGlobalDomain`, such as `com.apple.trackpad.enableSecondaryClick`. The run does not write them, so they may disagree with the keys it writes.
- Nothing restarts, so a trackpad change may not take effect until the account logs out and in. The cleanup cannot log out.
- The restore puts back only the 27 keys. A change made by hand to one of them during the journey is lost.
- A `.local/area-input-journey/saved.plist` left by an unfinished restore holds the account's own values. The next run refuses to overwrite it and fails the journey before setup. That run's restore puts the old values back and deletes the file, so the run after it works.
