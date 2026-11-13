#!/usr/bin/python
import re
from types import SimpleNamespace

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: macos_command_setting
short_description: Read and set one macOS setting through command-line tools
description:
  - Runs the read argv, then searches its output with the parse pattern. The pattern sees stdout, then stderr, then a last line "exit: N" with the read's exit code, so a read whose exit code is the state (launchctl print exits 0 or 113) parses that line.
  - A read that exits nonzero is an error unless the pattern matches.
  - The value found is compared with the wanted value, after map turns the wanted public value into the text the read prints.
  - Only on a difference it runs the set argv lists in order, stopping at the first that exits nonzero, then reads again. The read decides. The set succeeded when the read shows the wanted value, whatever the set's exit code; a known refusal line still refuses.
  - When the read after the set still shows the old value and needs names a permission, the failure says in plain words that the setting needs that permission for the app that runs mac-setup.
  - A known refusal line in any command's output, such as systemsetup's "You need administrator access", is a failure, whatever the exit code. The failure gives the entry's reason, or the line itself when the entry has no reason.
  - A read whose text matches a pattern in refuse is a failure with that pattern's reason, before parse runs.
  - Arguments go to the tool as a list, never through a shell.
  - With write_allowed false, a value that differs is skipped without any set, and a value that matches is ok.
options:
  read: {description: Argv that prints the current state., type: list, elements: str, required: true}
  parse: {description: Regular expression with one named group "value"., type: str, required: true}
  map: {description: Public value to the text the read prints., type: dict, default: {}}
  refuse: {description: Regular expression to the plain reason the read refuses the setting., type: dict, default: {}}
  set: {description: Public value to a list of argv lists, or a list of argv lists where the item "{value}" becomes the value., type: raw, required: true}
  value: {description: The wanted public value., type: raw, required: true}
  write_allowed: {description: Whether this run may run the set commands., type: bool, default: true}
  needs: {description: Plain names of the permissions the setting needs, such as Full Disk Access., type: list, elements: str, default: []}
"""

REFUSALS = {
    "You need administrator access": "",
    "requires Full Disk Access": "",
}


class SettingError(Exception):
    pass


def text(value):
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def run(runner, argv):
    rc, out, err = runner.run(argv)
    for line in (out + err).splitlines():
        for refusal, reason in REFUSALS.items():
            if refusal in line:
                raise SettingError(f"{argv[0]} refused: {reason or line.strip()}")
    return rc, out, err


def read_state(runner, argv, pattern, refusals):
    rc, out, err = run(runner, argv)
    shown = f"{out}{err}"
    if shown and not shown.endswith("\n"):
        shown += "\n"
    shown += f"exit: {rc}\n"
    for refusal, reason in refusals.items():
        if refusal.search(shown):
            raise SettingError(reason)
    found = pattern.search(shown)
    if not found:
        raise SettingError(
            f"{' '.join(argv)} printed nothing that matches {pattern.pattern!r}: {shown!r}"
        )
    return found.group("value")


def joined(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def set_commands(set_spec, value):
    if isinstance(set_spec, dict):
        commands = {text(key): argvs for key, argvs in set_spec.items()}
        if text(value) not in commands:
            raise SettingError(
                f"no set command for the value {text(value)}; known values: {', '.join(commands)}"
            )
        return commands[text(value)]
    return [
        [text(value) if arg == "{value}" else arg for arg in argv] for argv in set_spec
    ]


def ensure_setting(
    runner,
    read,
    parse,
    mapping,
    refuse,
    set_spec,
    value,
    check_mode,
    write_allowed=True,
    needs=(),
):
    try:
        pattern = re.compile(parse, re.MULTILINE)
        refusals = {
            re.compile(refusal, re.MULTILINE): reason
            for refusal, reason in refuse.items()
        }
    except re.error as error:
        raise SettingError(f"{error.pattern!r} is not a valid pattern: {error}")
    if "value" not in pattern.groupindex:
        raise SettingError(f"parse {parse!r} has no group named value")
    shows = {text(key): text(shown) for key, shown in mapping.items()}
    public = {shown: key for key, shown in shows.items()}
    wanted = shows.get(text(value), text(value))
    current = read_state(runner, read, pattern, refusals)
    if current == wanted:
        return {"changed": False}
    if not write_allowed:
        return {
            "changed": False,
            "skipped": True,
            "msg": "the value differs, and this run may not write it",
        }
    commands = set_commands(set_spec, value)
    diff = {
        "before": f"{public.get(current, current)}\n",
        "after": f"{text(value)}\n",
    }
    if not check_mode:
        failure = ""
        for argv in commands:
            rc, out, err = run(runner, argv)
            if rc != 0:
                failure = f"{' '.join(argv)} exited {rc}: {(out + err).strip()}"
                break
        after = read_state(runner, read, pattern, refusals)
        if after != wanted:
            problem = (
                failure
                or f"after the set, {' '.join(read)} shows {public.get(after, after)}, not {text(value)}"
            )
            if after == current and needs:
                problem = (
                    f"the value did not change ({problem}); this setting needs "
                    f"{joined(needs)} for the app that runs mac-setup, so if that is off, "
                    f"it is the likely reason"
                )
            raise SettingError(problem)
    return {"changed": True, "diff": diff}


def main():
    module = AnsibleModule(
        argument_spec={
            "read": {"type": "list", "elements": "str", "required": True},
            "parse": {"type": "str", "required": True},
            "map": {"type": "dict", "default": {}},
            "refuse": {"type": "dict", "default": {}},
            "set": {"type": "raw", "required": True},
            "value": {"type": "raw", "required": True},
            "write_allowed": {"type": "bool", "default": True},
            "needs": {"type": "list", "elements": "str", "default": []},
        },
        supports_check_mode=True,
    )
    params = module.params
    try:
        result = ensure_setting(
            SimpleNamespace(run=module.run_command),
            params["read"],
            params["parse"],
            params["map"],
            params["refuse"],
            params["set"],
            params["value"],
            module.check_mode,
            params["write_allowed"],
            params["needs"],
        )
    except SettingError as error:
        module.fail_json(msg=str(error))
    module.exit_json(**result)


if __name__ == "__main__":
    main()
