# Dock area

One run of `playbooks/site.yml` writes 17 settings of the Desktop & Dock pane for the account that runs it. They are the Dock's position, size, recent apps, hiding, minimizing and open-app dots, the 4 hot corners, 2 Mission Control settings, Stage Manager's recent apps, 3 widget settings, and what happens to windows when an app quits. The Dock restarts once, so the Dock and hot corner changes show at once. No test has measured when the other settings appear; a logout shows them. A second run changes nothing.

## Sub-features

- `area-dock-preview` reports 17 settings as changed and writes nothing.
- `area-dock-apply` writes 17 keys in 5 domains and restarts the Dock once.
- `area-dock-again` reports `changed=0` when every key already holds its value.

## How to get to it (user POV)

- Put the settings under `default_settings` in the profile, for example `dock.position: left`, `hot_corners.bottom_right: quick_note` and `windows.on_quit: keep`. `inventory/example/TEMPLATE.yml` lists each one with its choices.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K --check` to preview.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K` to apply, then the same command again.
- No test has measured when the Mission Control, Stage Manager and widget settings appear. Log out and back in if you do not see them.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real settings. The harness puts each key back as it was.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- The running account is signed in at the screen, so the Dock runs and its restart reports `changed`.

- **Setup.** The journey's `saves` lists the 17 keys, so the harness saves the type and value of each, or that it is absent, before setup. A key whose type is not boolean, integer, float or string fails the journey before setup deletes any key. The setup then deletes each key that exists, so the first run changes every setting.
- **Preview.** Check run: exit 0, `changed=17 failed=0`. That is 1 per setting.
- **Apply.** Exit 0, `changed=18 failed=0`. That is the 17 settings and 1 Dock restart. The handler `Restart Dock` reports `changed`. 11 settings ask for the Dock restart, and it runs once. The other 6 restart nothing: 1 shows at logout, and the timing of 5 is not measured.
- **Apply again.** Exit 0, `changed=0 failed=0`, and no Dock restart.
- **Proof.** After the last run, the harness reads every key with `defaults export DOMAIN -` and checks its type and value:

| # | Setting | Domain | Key | Type | Value |
| --- | --- | --- | --- | --- | --- |
| 1 | `dock.position: left` | `com.apple.dock` | `orientation` | string | `left` |
| 2 | `dock.tile_size: 32` | `com.apple.dock` | `tilesize` | float | `32` |
| 3 | `dock.show_recents: false` | `com.apple.dock` | `show-recents` | boolean | `false` |
| 4 | `dock.autohide: false` | `com.apple.dock` | `autohide` | boolean | `false` |
| 5 | `dock.minimize_to_application: true` | `com.apple.dock` | `minimize-to-application` | boolean | `true` |
| 6 | `dock.show_open_indicators: true` | `com.apple.dock` | `show-process-indicators` | boolean | `true` |
| 7 | `hot_corners.top_left: none` | `com.apple.dock` | `wvous-tl-corner` | integer | `1` |
| 8 | `hot_corners.top_right: none` | `com.apple.dock` | `wvous-tr-corner` | integer | `1` |
| 9 | `hot_corners.bottom_left: none` | `com.apple.dock` | `wvous-bl-corner` | integer | `1` |
| 10 | `hot_corners.bottom_right: quick_note` | `com.apple.dock` | `wvous-br-corner` | integer | `14` |
| 11 | `mission_control.group_by_app: true` | `com.apple.dock` | `expose-group-apps` | boolean | `true` |
| 12 | `mission_control.display_spaces: separate` | `com.apple.spaces` | `spans-displays` | boolean | `false` |
| 13 | `stage_manager.recent_apps: hide` | `com.apple.WindowManager` | `AutoHide` | boolean | `true` |
| 14 | `widgets.on_desktop: hide` | `com.apple.WindowManager` | `StandardHideWidgets` | boolean | `true` |
| 15 | `widgets.in_stage_manager: hide` | `com.apple.WindowManager` | `StageManagerHideWidgets` | boolean | `true` |
| 16 | `widgets.iphone_widgets: false` | `com.apple.chronod` | `remoteWidgetsEnabled` | boolean | `false` |
| 17 | `windows.on_quit: keep` | `NSGlobalDomain` | `NSQuitAlwaysKeepsWindows` | boolean | `true` |

- **Cleanup.** The journey has no cleanup commands. The harness restore writes each saved key back with its saved type and value, deletes each key that was absent, and restarts the Dock when the running account owns the screen. It runs also after a failed run. It changes only these 17 keys, so the rest of each domain stays as it is.

## Gotchas

- The apply's `changed=18` needs the account signed in at the screen. With no Dock running, the restart has nothing to do and the apply shows `changed=17`.
- 4 settings store the opposite of the words on screen. `mission_control.display_spaces: separate` stores `spans-displays` false. `hide` for Stage Manager's recent apps and for both widget settings stores true.
- Hot corner code 14 (Quick Note) was measured on a test Mac. Code 1 (none) comes from community lists.
- `chronod` also keeps `effectiveRemoteWidgetsEnabled` and a migration flag. It may overwrite a direct write of `remoteWidgetsEnabled`. Then the read of row 16 fails, and the journey reports it.
- The Dock owns `com.apple.spaces`. If its restart writes `spans-displays` back, the read of row 12 or the second run fails, and the journey reports it.
- The cleanup cannot log out. Until the next logout, the Mission Control, Stage Manager and widget settings may still look as the apply left them, though the keys hold the saved values.
- A `.local/area-dock-journey/saved.plist` left by an unfinished restore holds the account's own values. The next run refuses to overwrite it and fails the journey before setup. That run's restore puts the old values back and deletes the file, so the run after it works.
