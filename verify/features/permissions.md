# Permissions up front

Some settings need a privacy permission for the app that runs mac-setup. `time.zone`, `sharing.remote_login` and `sharing.remote_apple_events` need Full Disk Access. `finder.sidebar_favorites` needs access to each of Desktop, Documents and Downloads that it lists. The first play of `playbooks/site.yml` works out which of these the profile needs, tests each one, and asks a person at the Mac's screen for the missing ones before any change. The last play asks the person to turn Full Disk Access off again when this run turned it on. Over SSH or with `--check`, the run asks for nothing and only lists what it would ask. A setting whose permission is still off is read first and skipped only if it would change: an Ansible skip, exit 0, and one summary line per skipped setting. If a step in a later play fails, the last play still takes Full Disk Access back, and the run ends with exit 2.

## Sub-features

- `permissions-list` prints one `would ask for` line per needed permission, asks nothing, and opens no page.
- `permissions-skip` skips `time.zone` while Full Disk Access is off, with exit 0 and one `skipped time.zone` summary line.
- `permissions-summary` names each needed permission and its state in the summary.
- `permissions-stop` takes back Full Disk Access and ends with exit 2 after a failure in "This account" or "Machine and other accounts". `tests/harness/test_site_permissions.py` proves it with a planted failing role.
- `permissions-ask` (at the screen only) opens the Full Disk Access page, says what to turn on, waits for Return, and tests again. For Desktop, Documents and Downloads it says to click Allow and reads the folder, which shows macOS's own dialog the first time.
- `permissions-take-back` (at the screen only) opens the Full Disk Access page again at the end, says to turn the app off, waits for Return, and tests that it is off.

## How to get to it (user POV)

- Put a setting that needs a permission in the profile, such as `time.zone: Etc/UTC` under `machine_settings`, or `finder.sidebar_favorites: ["~", ~/Desktop]` under `default_settings`.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K --check` to see what the run would ask.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K` in Terminal at the Mac's screen, follow each step, and press Return after each one.

## Driving it with scripts/verify

Preconditions:

- `scripts/bootstrap` has run in this checkout.
- The account that runs the journey has no Full Disk Access in this session. Over SSH that holds unless someone gave SSH Full Disk Access.

- **Setup.** The journey saves `defaults export` of `com.apple.dock` and `com.apple.finder`, and the time zone link from `readlink /etc/localtime`.
- **List.** Run `ansible-playbook playbooks/site.yml --check` with a profile that lists `finder.sidebar_favorites: ["~", ~/Desktop, ~/Downloads]` and `time.zone: Etc/UTC`. Exit 0, recap `failed=0`. The output holds the line saying why the run asks for nothing, one `would ask for` line each for Full Disk Access, Desktop folder access and Downloads folder access, and no line for Documents. The ask for Full Disk Access and the 2 folder reads are skipped, and no page opens. The `time.zone` task is skipped. The summary holds `Full Disk Access: off`, one `skipped time.zone` line, and a `not tested` line for each folder.
- **Proof.** The cleanup exports both domains again and requires each to equal its saved export byte for byte, and requires the same time zone link.

## Gotchas

- A Finder sidebar folder written as a full path into the home folder is not recognised as Desktop, so macOS asks mid-run as before; write `~/Desktop` instead.

- The journey runs list mode only. `pause` waits only when its input is a terminal, and the harness gives every run `/dev/null` as input. The ask and take-back steps need a person at the screen, so a person runs them by hand.
- In list mode the folders are not tested, because a test read is what shows macOS's dialog. So list mode never skips the sidebar.
- The run decides "over SSH" from `SSH_CONNECTION`, `SSH_CLIENT` or `SSH_TTY`, which sshd sets. It ignores `SSH_AUTH_SOCK`, which macOS also sets in a Terminal at the screen.
- If someone gave SSH Full Disk Access, the test reads on and `time.zone` runs, so the `skipped time.zone` patterns fail. The journey then reports the failure.
- The journey needs a Mac whose time zone is not `Etc/UTC`. A zone that already matches is `ok`, not skipped, so the `skipped time.zone` patterns fail.
- Run the hand steps in a fresh Terminal window, not inside tmux or screen. Those run under their own server process, so the permission you turn on for Terminal may not reach the run.
- `permissions_ls` replaces `/bin/ls` for the test reads, and `permissions_open` replaces `/usr/bin/open`. `tests/harness/test_site_permissions.py` uses both to fake a permission that is off or on and to record each page it opens.
