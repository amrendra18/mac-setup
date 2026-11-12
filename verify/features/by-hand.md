# Guided click-through

Some settings have no script path: privacy permissions and panes that only take a click. `playbooks/by-hand.yml` walks the person at the Mac through them. The profile lists these settings like any other, under `default_settings`, an account's `settings` or `machine_settings`. For each one of the running account and the machine, the playbook opens the System Settings page, says what to choose, and waits for Return. It needs no admin rights and writes nothing.

## Sub-features

- `by-hand-list` prints one `would open` line per item and opens no page.
- `by-hand-walk` opens each item's page with `/usr/bin/open`, prints the page and what to choose, for example "Turn Bluetooth on or off: off", and waits for Return.
- `by-hand-settings-home` opens System Settings itself with `open -b com.apple.systempreferences` when an item's `link` is `""`.

## How to get to it (user POV)

- Put click-only settings in the profile, for example `network.bluetooth_power: false` under `machine_settings`. `inventory/example/TEMPLATE.yml` lists them under "Click-only settings". A setting whose only value is `by_hand` means "choose what you want there".
- Settings of another account appear when that account runs the playbook while signed in.
- Run `.local/venv/bin/ansible-playbook playbooks/by-hand.yml -e by_hand_list=true` to see the pages it would open.
- Run `.local/venv/bin/ansible-playbook playbooks/by-hand.yml` at the Mac's screen, make each choice, and press Return after each one.

## Driving it with scripts/verify

Preconditions:

- `scripts/bootstrap` has run in this checkout.
- The profile check accepts the run-time switches `by_hand_list` and `by_hand_open`.

- **Setup.** The journey writes a stand-in for `open` at `.local/by-hand-journey/open`, which records each call in `open.log` next to it. It saves `defaults export` of `com.apple.dock` and `com.apple.finder` as a sample of domains a stray write would touch.
- **List.** Run `ansible-playbook playbooks/by-hand.yml -e by_hand_list=true` with the stand-in as `by_hand_open`. The profile lists `notifications.per_app` and `appearance.icon_style` for the account and `network.bluetooth_power` for the machine. Exit 0, recap `changed=0 failed=0`, 3 `would open` lines, one per setting, and the line `would open System Settings for network.bluetooth_power (Turn Bluetooth on or off: off)`, because that setting has no known page.
- **Proof.** The cleanup finds no `open.log`, so list mode called `open` 0 times. It exports both domains again and requires each to equal its saved export byte for byte.

## Gotchas

- The `debug` output wraps a long line, so the journey counts the lines that start a `would open` message.
- `pause` waits only when its input is a terminal. The harness gives every run `/dev/null` as input, so a walk run under the harness would not wait. That is one reason the journey runs list mode only.
- The playbook reads nothing back. Privacy permissions cannot be read back by script on macOS 27 (`tccutil` has no `list`), and the click-only entries name no stored key.
- A domain can change while the journey runs if someone uses the Dock or Finder at the screen. The cleanup then fails with "DOMAIN changed during the runs".
