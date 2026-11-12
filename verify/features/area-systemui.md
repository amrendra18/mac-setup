# System UI area

One run of `playbooks/site.yml` writes 13 system UI settings of the account that runs it: the appearance theme, accent and highlight colors, the menu bar clock, the Sound and Fast User Switching menu bar items, notification previews, sounds and summaries, Siri, and Spotlight search sharing. They take 14 `defaults` keys, because the theme writes 2. No process restarts. The theme shows after a logout. No test has measured when the other settings appear; a logout shows them. A second run changes nothing.

## Sub-features

- `area-systemui-preview` reports the 14 keys as changed and writes nothing.
- `area-systemui-apply` writes the 14 keys, restarts nothing, and says to log out and back in to see 1 setting, and that 12 settings may need a logout or an app restart because their timing is not measured.
- `area-systemui-again` reports `changed=0` when every key already matches.

## How to get to it (user POV)

- Put the settings under `default_settings` in the profile, for example `appearance.theme: dark`, `menu_bar.clock_24_hour: true` and `spotlight.improve_search: "off"`. Quote `"on"` and `"off"`, or YAML reads them as true and false.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K --check` to preview.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K` to apply, then the same command again.
- Log out and back in to see the theme. No test has measured when the other settings appear. Log out and back in if you do not see them.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real appearance, menu bar, notification, Siri and Spotlight settings. The harness puts each saved key back.
- The running account is signed in at the screen, so the menu bar and notification processes that read these keys run.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- `scripts/bootstrap` has run in this checkout.

- **Setup.** The journey's `saves` lists the 14 keys, 2 of them with `host: currentHost`, so the harness saves the type and value of each, or that it is absent, before setup. The setup then deletes each key that exists, one by one. It never deletes or imports a whole domain. Last, it writes `AppleInterfaceStyleSwitchesAutomatically` false and `AppleHighlightColor` to the blue string, because the profile's values delete those 2 keys, and a delete of a missing key changes nothing.
- **Preview.** Check run: exit 0, `changed=14 failed=0`.
- **Apply.** Exit 0, `changed=14 failed=0`: 12 keys written and 2 keys deleted. No handler runs, because every entry's `takes_effect` is `logout` or `not measured`. The summary prints `log out and back in to see 1 setting`, for the theme, and `12 settings may need a logout or an app restart to show; their timing is not measured`.
- **Apply again.** Exit 0, `changed=0 failed=0`.
- **Proof.** `reads/area-systemui-after.json` holds these values:

| # | Setting | Domain | Key | Type | Value |
| --- | --- | --- | --- | --- | --- |
| 1 | `appearance.theme` | `NSGlobalDomain` | `AppleInterfaceStyle` | string | `Dark` |
| 2 | `appearance.theme` | `NSGlobalDomain` | `AppleInterfaceStyleSwitchesAutomatically` | absent | |
| 3 | `appearance.accent_color` | `NSGlobalDomain` | `AppleAccentColor` | integer | 1 (orange) |
| 4 | `appearance.highlight_color` | `NSGlobalDomain` | `AppleHighlightColor` | absent (accent) | |
| 5 | `menu_bar.clock_24_hour` | `com.apple.menuextra.clock` | `Show24Hour` | boolean | true |
| 6 | `menu_bar.clock_seconds` | `com.apple.menuextra.clock` | `ShowSeconds` | boolean | true |
| 7 | `menu_bar.clock_flash_separators` | `com.apple.menuextra.clock` | `FlashDateSeparators` | boolean | true |
| 8 | `menu_bar.sound` | `com.apple.controlcenter`, current host | `Sound` | integer | 18 (always) |
| 9 | `menu_bar.user_switcher` | `com.apple.controlcenter`, current host | `UserSwitcher` | integer | 18 (show) |
| 10 | `notifications.iphone_sounds` | `com.apple.ncprefs` | `play_forwarded_notifications_sounds` | boolean | true |
| 11 | `notifications.show_previews` | `com.apple.ncprefs` | `content_visibility` | integer | 2 (when unlocked) |
| 12 | `notifications.summarize` | `com.apple.ncprefs` | `summarize_previews` | boolean | false |
| 13 | `siri.enabled` | `com.apple.assistant.support` | `Assistant Enabled` | boolean | false |
| 14 | `spotlight.improve_search` | `com.apple.assistant.support` | `Search Queries Data Sharing Status` | integer | 2 (off) |

- **Cleanup.** The journey has no cleanup commands. The harness restore writes each saved key back with its saved type and value, and deletes each key that was absent. It restarts nothing.

## Gotchas

- The recap counts keys, so the theme counts 2. A count of 13 on the apply means one key did not change.
- The numbers for accent orange, Sound always, Fast User Switching show, previews when unlocked and Spotlight off come from community sources and one test Mac. The reads prove what is stored. Only a look at System Settings proves that macOS shows the matching choice.
- Nothing restarts, so the menu bar clock and items may keep their old look until the account logs out and in.
- The setup deletes `AppleInterfaceStyle`, so new windows open in light mode until the apply writes `Dark`.
- `siri.enabled` lists `needs: [click]`, because turning Siri on needs the pane. The summary therefore says 1 setting needs a click, even when the profile turns Siri off.
- The restore puts back only the 14 keys. A change made by hand to one of them during the journey is lost.
