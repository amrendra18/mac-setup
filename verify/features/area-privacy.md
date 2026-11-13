# Privacy area

One run of `playbooks/site.yml` writes one privacy setting of the account that runs it: whether Apple uses account data to choose ads in the App Store, News and Stocks. It takes 1 key. No process restarts. No test has measured when the change appears. A second run changes nothing.

The app permissions in Privacy & Security, such as Camera, Microphone and Full Disk Access, are `not_yet`. macOS lets only a person grant them in System Settings, so this journey does not cover them.

## Sub-features

- `area-privacy-preview` reports the 1 key as changed and writes nothing.
- `area-privacy-apply` writes the key, restarts nothing, and its summary prints `1 setting may need a logout or an app restart to show; its timing is not measured`.
- `area-privacy-again` reports `changed=0` when the key already holds its value.

## How to get to it (user POV)

- Put the setting under `default_settings` in the profile, as `privacy.personalized_ads: false`. `inventory/example/TEMPLATE.yml` lists it with its choices.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K --check` to preview.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K` to apply, then the same command again.
- No test has measured when System Settings > Privacy & Security > Apple Advertising shows the change. Log out and back in if you do not see it.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real advertising setting. The harness puts the saved key back.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- `scripts/bootstrap` has run in this checkout.

- **Setup.** The journey's `saves` lists the 1 key, so the harness saves its type and value, or that it is absent, before setup. The setup then deletes the key if it exists, so the first run changes it.
- **Preview.** Check run: exit 0, `changed=1 failed=0`.
- **Apply.** Exit 0, `changed=1 failed=0`. No handler runs, because the setting's `takes_effect` is `not measured`. The summary prints `1 setting may need a logout or an app restart to show; its timing is not measured` and no logout line.
- **Apply again.** Exit 0, `changed=0 failed=0`.
- **Proof.** After the last run, the harness reads the key with `defaults export DOMAIN -` and checks its type and value:

| # | Setting | Domain | Key | Type | Value |
| --- | --- | --- | --- | --- | --- |
| 1 | `privacy.personalized_ads: false` | `com.apple.AdLib` | `allowApplePersonalizedAdvertising` | boolean | `false` |

- **Cleanup.** The journey has no cleanup commands. The harness restore writes the saved key back with its saved type and value, or deletes it if it was absent. It restarts nothing. It changes only this key, so the rest of `com.apple.AdLib` stays as it is.

## Gotchas

- The read proves what is stored. Only a look at System Settings proves that macOS shows the matching choice.
- `com.apple.AdLib` also keeps sync and migration keys, such as `personalizedAdsMigrated`. A process that owns the domain may write `allowApplePersonalizedAdvertising` back (a guess; not tested). Then the read or the second run fails, and the journey reports it.
- The other privacy settings stored with `defaults` stay out of this journey. `privacy.improve_siri_dictation` is not in the profile. `privacy.location_menu_icon`, `privacy.share_mac_analytics` and `privacy.share_with_app_developers` are machine settings, which the "Machine and other accounts" play writes with admin rights.
- A `.local/area-privacy-journey/saved.plist` left by an unfinished restore holds the account's own value. The next run refuses to overwrite it and fails the journey before setup. That run's restore puts the old value back and deletes the file, so the run after it works.
