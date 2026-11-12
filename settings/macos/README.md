# Settings reference

Each `*.yml` file here maps setting ids to entries. `scripts/render-template` turns them into `inventory/example/TEMPLATE.yml`, and `tests/test_reference.py` checks every entry.

## How a command read works

A setting with `route: command` is read in these steps:

1. mac-setup runs the `read` argv.
2. It joins the command's stdout, then its stderr, then one final line `exit: N`, where N is the exit code.
3. It matches `parse` against that text with Python's `re.MULTILINE`, so `^` and `$` match at the start and end of each line.
4. The named group `value` is the current state. The optional `map` gives the text each public value stands for.

Anchor each pattern to the text of its own line, so it cannot match the exit line or another line by accident. Only a pattern that reads the exit code starts with `^exit: `. `tests/test_reference.py` checks each pattern against reads recorded on macOS 27.2.1.
