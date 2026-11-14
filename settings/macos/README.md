# Settings reference

Each `*.yml` file here maps setting ids to entries. `scripts/render-template` turns them into `inventory/example/TEMPLATE.yml`, and `tests/test_reference.py` checks every entry.

## How a command read works

A setting with `route: command` is read in these steps:

1. mac-setup runs the `read` argv.
2. It joins the command's stdout, then its stderr, then one final line `exit: N`, where N is the exit code.
3. It matches `parse` against that text with Python's `re.MULTILINE`, so `^` and `$` match at the start and end of each line.
4. The named group `value` is the current state. The optional `map` gives the text each public value stands for.
5. The optional `refuse` maps a pattern to a plain reason. When the read text matches the pattern, the run refuses the setting with that reason before `parse` runs.

Anchor each pattern to the text of its own line, so it cannot match the exit line or another line by accident. Only a pattern that reads the exit code starts with `^exit: `. `tests/test_reference.py` checks each pattern against reads recorded on macOS 27.2.1.

## Rules for writes

1. A `command` setting with `scope: user` runs as the account that runs mac-setup, in the "This account" play. Other listed accounts get a note in the summary instead.
2. A choice that is missing from a write's `map` leaves that key alone.
3. A `{type: list}` value on the `defaults` route needs one array write. Its `map` turns each item into the stored value.
4. A `{type: hotkeys}` value maps each action to `true`, `false`, or keys joined with `+`, such as `control+up`. It merges into one dictionary with `dict_mode: add`, so other shortcuts stay as they are.
5. The profile check refuses two settings that set the same key to different values. Under an account, the line names the account's own setting. Two entries that share a key leave it alone for every choice that does not claim it, so only two claims that disagree are refused.
6. The profile check refuses `unset` for a `{type: hotkeys}` value and for a `{type: list}` value on the `defaults` route, because it would delete the whole dictionary or list.

## Browser rows

`firefox.yml` and `chrome.yml` are the shared source for capture and later browser application.
Every browser row remains on `route: not_yet`, so normal profiles still refuse those settings.
The `browser` field holds the family, read descriptors, capture rule, lock forms, proposed baseline, support metadata, and inventory disposition.
The metadata validator rejects unknown fields and executable descriptors.

1. `read` names exact keys in approved sources and an identity or boolean inversion conversion.
2. `capture` allows a scalar, a finite enum, an exact reviewed public endpoint, or a masked value.
3. `locked` and `unlocked` describe supported forms. An unsupported form names why it stays unset.
4. `baseline.status` remains `proposed` until the settings review. It is separate from observed browser values.
5. `support` requires `min`, `tested`, `source`, and `note`. Only a vendor-documented removal has an optional `max`.
6. `inventory` links each row to a policy leaf, deprecated policy, non-policy setting, or lock control.

`support.min` records minimum known support from the vendor, or the pinned test version when earlier support is unknown.
`support.tested` names the map's pinned test version. It stays separate from the observed profile version.
Newer profile versions remain eligible for capture unless they exceed a documented `max`.
A major-version `max` includes that major version's point releases.
The report header names both the tested and observed versions.

Structured values use `{type: browser, schema: ...}` with a validated subset of JSON Schema.
Schemas state types, enum choices, numeric bounds, object members, array members, and required fields.
Dynamic keys and private structures remain masked during capture.
The ledgers in `tests/browser/` list policy names, configurable leaves, structured members, and non-policy settings independently.
The coverage test requires one disposition per listed item, with no duplicates.

After editing browser rows, run these commands:

```sh
scripts/render-browser-map
scripts/render-template
scripts/check full
```

Capture loads the generated Python map without PyYAML or Ansible.
Do not edit the generated file by hand.
`scripts/sync-example` refuses private or structured browser values instead of copying them into an example.
