# Recover from an incomplete run

Read the summary before relying on `failed=0` or `changed=0`.
The [README](../README.md#quick-start) gives the local-screen journey.
Every run writes `.local/logs/ansible.log`. Remove personal details before sharing any log.

## A profile mistake

A wrong setting name, an unsupported value, or a setting in the wrong section stops before changes.
The run prints one line per problem and exits with code 2.
Edit the existing section. Use spaces for indentation and quote `"on"`, `"off"`, `"yes"`, and `"no"`.
Preview again:

```sh
.local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K --check
```

## No Mac matched

A missing inventory can warn that no host matched, change nothing, and exit with code 0.
Check the `-i` path. For multiple Macs, also check each name and folder using the [profile guide](profiles.md#use-one-profile-on-several-macs).
A hostname mismatch in a loaded multi-Mac profile stops before writes and prints the hostname it found.

## A setting was refused

A refusal leaves other settings running. The summary prints one `refused` line per setting and exits with code 2.
Correct the named cause and run again.
For a command setting, such as the time zone, mac-setup reads the value again after setting it.
That read decides whether the write worked. If the value stayed unchanged, the setting is refused.
When Full Disk Access is needed, the refusal says so.

Your account's Dock is set before apps install.
If a Dock list names an app being installed in the same run, the entire Dock list is refused until that app exists.
Other settings and app installs continue, but the run ends with exit code 2.
Install apps first, then add their bundle ids to the Dock list on a later run.

## Admin work stopped early

Your own account's settings can apply before the machine play needs admin rights.
If admin access fails, a setting may be refused or machine and other-account work may stop early.
When Ansible reaches the summary, it lists recorded refusals or names the stopped task.
Rerun with `-K` if a password is missing. Use an admin account if your account lacks admin rights.
From an admin account, the apply command is:

```sh
.local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K
```

At `BECOME password:`, type that account's Mac password.

## A permission stayed off

A setting whose value would change is skipped when a needed permission stays off.
The summary names each skipped setting. Skips can leave the command's exit code at 0.
A command setting that already matches stays `ok`, without a write.
For the Finder sidebar, a denied folder permission skips every sidebar setting together.

Only you can grant privacy permissions. mac-setup never grants them or bypasses a consent prompt.
At the screen, the first play asks for missing access before settings change.
After granting access, return to Terminal and press Return when asked.

Full Disk Access lets commands in Terminal read every file.
At the end, turn Terminal off when asked if this run asked you to turn it on.
A task failure still reaches that cleanup step. Access present before the run stays on.
If you stop with Control-C before cleanup, open **System Settings > Privacy & Security > Full Disk Access** and turn Terminal off yourself.

Terminal keeps Desktop, Documents, and Downloads access that you grant.
To remove that access, use **Privacy & Security > Files & Folders**.

## A run over SSH

The example firewall blocks incoming SSH connections.
Before applying remotely, remove `security.firewall_block_all` and `allow_lockout_risk: true` from `machine.yml`.

Over SSH, mac-setup asks for no privacy permission and opens no System Settings page.
It prints `would open` lines for click-only pages and lists the permissions a screen run would request.
Settings needing Full Disk Access, such as the time zone, are skipped while access is off.

Folder tests would show macOS dialogs, so SSH runs skip those tests and still attempt the sidebar step.
If the session lacks folder access, macOS refuses that step. The summary reports the refusal and exits with code 2.
Use a run at the Mac's screen to finish those settings.

## A change does not appear

The summary counts settings that need logout and settings whose timing has not been measured.
Log out and back in, or restart the affected app, as the summary directs.
Other accounts may need their own sign-in and run for wallpaper, sound, and click-only settings.

`changed=0` means the run made no additional writes. Skipped work may still remain.
Click-only pages contain instructions. mac-setup cannot confirm the choices you made there.

## Settings that leave shared keys alone

By-hand changes usually return to the profile's value on the next run.
These trackpad choices leave shared swipe keys alone, so a by-hand change to those keys stays:

1. `off` for `input.mission_control_gesture` and `input.app_expose_gesture`.
2. `off` and `two_finger` for `input.swipe_between_pages`.
3. `off` and `four_finger` for `input.swipe_full_screen_apps`.

`unset` works only for preferences.
The profile check also refuses it for `input.keyboard_shortcuts`, `mission_control.shortcuts`, and `input.input_sources`.
Resetting those settings could remove all shortcuts or leave the account with no input source.
See [Change a setting back](../README.md#change-a-setting-back) for ordinary reversal and Finder sidebar restoration.

## A download or vault failed

For bootstrap failures, follow the [installation retry instructions](installation.md#bootstrap-output-and-retry).
For app checks, read the named check in the refusal and the [download checks](how-it-works.md#apps).
A running app must be quit before an upgrade.
A missing or unusable vault password can stop loading an encrypted profile before settings apply.
Restore the original password using [Secrets](../README.md#secrets). Never replace it with a new password for an existing vault.
