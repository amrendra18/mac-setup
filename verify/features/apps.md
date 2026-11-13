# Apps

One run of `playbooks/site.yml` installs each app listed in `apps.install` under `machine_settings`, from the catalog in `apps/macos.yml`. Each download passes every check before anything is copied, and a failed check refuses that app with one line and leaves the others. Without a VirusTotal key and without a stored verdict for the file, the VirusTotal check refuses the app, so nothing is installed.

## Sub-features

- `apps-check-no-key` downloads Sublime Text into a scratch cache, refuses it at the VirusTotal check with one line, and exits 2.
- `apps-apply-no-key` reuses the cached download, refuses it the same way, installs nothing into the scratch Applications folder, and exits 2.

## How to get to it (user POV)

- Put `apps.install: [sublime_text]` under `machine_settings` in the profile.
- Store `virustotal_api_key` in a vault file in the private profile, as the README's Secrets section says.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K --check` to preview, then the same command without `--check` to install.

## Driving it with scripts/verify

Preconditions:

- The Mac can reach `download.sublimetext.com`. The journey downloads the pinned Sublime Text build, so its level is `full`.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.

- **Setup.** The journey makes an empty scratch cache `.local/apps-journey/downloads` and an empty scratch install folder `.local/apps-journey/Applications`. Each run points the role at them with `-e macos_apps_downloads=...` and `-e macos_apps_applications=...`, so a real copy in `/Applications` and a stored verdict in `.local/downloads` cannot change the result.
- **Check, no key.** Check run: exit 2, `changed=0 failed=1 rescued=1`, and one refused line for `sublime_text` that names the VirusTotal check.
- **Apply, no key.** Exit 2, `changed=0 failed=1 rescued=1`, and the same refused line.
- **Proof.** The cleanup prints what the scratch Applications folder holds, deletes the scratch folder, and fails the journey when anything was installed.

## Gotchas

- The harness writes its own inventory, so the private profile's vault file and its key never reach the journey. This journey therefore proves the fail-closed path only. A real install needs the key, or a verdict stored by an earlier run, and is not yet proven by a journey.
- The "Machine and other accounts" play runs as root, so the download in the scratch cache belongs to root. The setup makes the folder as the running account, so the cleanup can still delete the file.
- `failed=1` comes from the summary task, which fails on purpose when an app was refused. The refused install itself is rescued and not counted as failed.
