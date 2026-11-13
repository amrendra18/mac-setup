# Example profile check

The example profile in `inventory/example/` loads together with every reference entry. A check run of it fails nothing and changes nothing on the Mac.

## Sub-features

- `example-check-load` loads every file in `settings/macos/` through the profile check, which stops on a repeated id or a broken entry.
- `example-check-preview` runs the example profile with `--check` and ends with `failed=0`.
- `example-check-no-writes` finds the Dock's preferences byte-for-byte equal before and after the check run.

## How to get to it (user POV)

- Copy the example profile, as step 3 of the README's Quick start says.
- Run `.local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K --check`.

## Driving it with scripts/verify

Preconditions:

- The running account is an admin with password-free sudo, because the harness passes no `-K`.
- `scripts/bootstrap` has run in this checkout.

- **Setup.** The journey saves `defaults export com.apple.dock` to `.local/example-check/dock-before.plist`.
- **Preview.** Check run: exit 0, `failed=0`.
- **Proof.** The cleanup exports the Dock's preferences again and compares the two files with `cmp`. Any difference fails the journey.

## Gotchas

- The journey's profile repeats the example profile's values; `scripts/sync-example` writes both, and a harness test keeps them equal.
- A person who uses the Dock during the run can change its preferences and fail the comparison.
