# Settings reference

Each `*.yml` file here maps setting ids to entries. `scripts/render-template` turns them into `inventory/example/TEMPLATE.yml`, and `tests/test_reference.py` checks every entry.

## How a command read works

A setting with `route: command` is read in these steps:

1. mac-setup runs the `read` argv.
2. It joins the command's stdout, then its stderr, then one final line `exit: N`, where N is the exit code.
3. It matches `parse` against that text with Python's `re.MULTILINE`, so `^` and `$` match at the start and end of each line.
4. The named group `value` is the current state. The optional `map` gives the text each public value stands for.

Anchor each pattern to the text of its own line, so it cannot match the exit line or another line by accident. Only a pattern that reads the exit code starts with `^exit: `. `tests/test_reference.py` checks each pattern against reads recorded on macOS 27.2.1.

## Rules for writes

1. A `command` setting with `scope: user` runs as the account that runs mac-setup, in play 1. Other listed accounts get a note in the summary instead.
2. A choice that is missing from a write's `map` leaves that key alone.
3. A `{type: list}` value on the `defaults` route needs one array write. Its `map` turns each item into the stored value.
4. A `{type: hotkeys}` value maps each action to `true`, `false`, or keys joined with `+`, such as `control+up`. It merges into one dictionary with `dict_mode: add`, so other shortcuts stay as they are.
5. The profile check refuses two settings that set the same key to different values. Under an account, the line names the account's own setting. Two entries that share a key leave it alone for every choice that does not claim it, so only two claims that disagree are refused.
6. The profile check refuses `unset` for a `{type: hotkeys}` value and for a `{type: list}` value on the `defaults` route, because it would delete the whole dictionary or list.
