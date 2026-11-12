# Wallpaper

One run of `playbooks/site.yml` makes every screen of the account that runs it show solid black. The change shows at once. A second run changes nothing. The module asks macOS through the public AppKit calls `NSWorkspace desktopImageURLForScreen` and `setDesktopImageURL:forScreen:options:error:`, run by `osascript -l JavaScript`. It sends no Apple Events, so it needs no Automation consent, and it never writes the wallpaper store itself.

## Sub-features

- `wallpaper-preview` reports `desktop.wallpaper` as changed, shows each screen's old and new picture with `--diff`, and sets nothing.
- `wallpaper-apply` sets the black picture that macOS ships, `/System/Library/Desktop Pictures/Solid Colors/Black.png`, on each screen that shows something else, then reads every screen again and fails unless all show it.
- `wallpaper-again` reports `changed=0` when every screen already shows that picture.
- `wallpaper-refused` records one refused line for `desktop.wallpaper` when the running account's session shows no screen, the picture is missing, or macOS refuses or does not keep the picture, and the other settings still run. Only the unit tests in `tests/modules/test_macos_wallpaper.py` cover it; no journey drives it yet.

## How to get to it (user POV)

- Put `desktop.wallpaper: black` under `default_settings` in the profile.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K --check --diff` to preview.
- Run `.local/venv/bin/ansible-playbook playbooks/site.yml -K` to apply, then the same command again.

## Driving it with scripts/verify

Preconditions:

- The Mac is set aside for testing. The run changes the running account's real desktop picture.
- The running account has a graphical session, so `NSScreen` lists its screens.
- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- `scripts/bootstrap` has run in this checkout.

- **Setup.** The journey sets `/System/Library/CoreServices/DefaultDesktop.heic` on every screen with the same AppKit call, so the first run has something to change. It fails when no screen took it.
- **Preview.** Run `ansible-playbook playbooks/site.yml -K --check`. Exit 0 with recap `changed=1 failed=0`.
- **Apply.** Run `ansible-playbook playbooks/site.yml -K`. Exit 0 with recap `changed=1 failed=0`. The desktop turns black.
- **Apply again.** Run the same command. Exit 0 with recap `changed=0 failed=0`.
- **Proof.** The cleanup reads every screen's picture with `desktopImageURLForScreen` and prints it into the journey log. It fails the journey unless at least one screen is listed and every one shows `file:///System/Library/Desktop%20Pictures/Solid%20Colors/Black.png`. The harness's own reads use `defaults export`, which cannot reach this value.

## Gotchas

- For each other listed account, play 2 runs the module as that account through sudo. That session usually sees no screen, so the summary notes "the desktop picture for ACCOUNT is set at its next run while it is signed in". That note is not a refusal, so it does not make the run end with exit 2.
- With no screen visible to the session the module refuses. Whether a `become_user` run for another account sees that account's screens is not measured.
- The call covers the current space. A space created later may show its own picture. Not measured.
- The fill colour behind the picture stays the system's blue. With the default placement `Crop` it is not visible.
