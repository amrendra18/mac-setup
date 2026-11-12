#!/usr/bin/python
import json
import plistlib
from types import SimpleNamespace

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: macos_nested_default
short_description: Read and write one defaults key that holds a dictionary or an array of nested values
description:
  - Reads the key from C(defaults export DOMAIN -), so booleans, integers and nested dictionaries keep their types.
  - With dict_mode add, only the listed entries of the dictionary are compared and written, and the other entries stay as they are.
  - With dict_mode replace, the whole value is compared and written.
  - Only on a difference it runs C(defaults write) with each entry as an XML property list, then reads again and fails unless the key holds the wanted value.
  - A boolean never equals an integer here, so true and 1 are different values.
options:
  domain: {description: The defaults domain., type: str, required: true}
  host: {description: currentHost for a per-host key, or empty., type: str, default: ""}
  key: {description: The key in the domain., type: str, required: true}
  value: {description: A dictionary or a list. Ignored when state is absent., type: raw}
  state: {description: present writes the value, absent deletes the key., type: str, choices: [present, absent], default: present}
  dict_mode: {description: add merges the listed entries into a dictionary, replace writes the whole value., type: str, choices: [add, replace], default: replace}
"""


class NestedError(Exception):
    pass


def same(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(map(same, a, b))
    return a == b


def xml(value):
    text = plistlib.dumps(value, fmt=plistlib.FMT_XML).decode()
    return text.split('<plist version="1.0">\n', 1)[1].rsplit("</plist>", 1)[0].strip()


def base(host):
    return ["/usr/bin/defaults"] + (["-currentHost"] if host else [])


def read_key(runner, host, domain, key):
    argv = base(host) + ["export", domain, "-"]
    rc, out, err = runner.run(argv)
    if rc != 0:
        raise NestedError(
            f"{' '.join(argv)} exited {rc}: {err.decode(errors='replace')}"
        )
    return plistlib.loads(out).get(key)


def compared(current, value, dict_mode):
    if dict_mode == "add":
        if current is not None and not isinstance(current, dict):
            raise NestedError(f"holds a {type(current).__name__}, not a dictionary")
        held = current or {}
        return {k: held[k] for k in value if k in held}, value
    return current, value


def write_argv(host, domain, key, current, value, dict_mode):
    head = base(host) + ["write", domain, key]
    if isinstance(value, list):
        return head + ["-array"] + [xml(item) for item in value]
    flag = "-dict-add" if dict_mode == "add" and current is not None else "-dict"
    return head + [flag] + [part for k, v in value.items() for part in (str(k), xml(v))]


def shown(value, indent=None):
    return json.dumps(value, indent=indent, sort_keys=True, default=str)


def ensure_nested(runner, domain, host, key, value, state, dict_mode, check_mode):
    current = read_key(runner, host, domain, key)
    if state == "absent":
        if current is None:
            return {"changed": False}
        before, after, argv = current, None, base(host) + ["delete", domain, key]
    else:
        if not isinstance(value, (dict, list)) or (
            dict_mode == "add" and not isinstance(value, dict)
        ):
            raise NestedError(
                "value must be a dictionary"
                + ("" if dict_mode == "add" else " or a list")
            )
        before, after = compared(current, value, dict_mode)
        if same(before, after):
            return {"changed": False}
        argv = write_argv(host, domain, key, current, value, dict_mode)
    diff = {
        "before": shown(before, 1) + "\n",
        "after": shown(after, 1) + "\n",
    }
    if not check_mode:
        rc, out, err = runner.run(argv)
        if rc != 0:
            raise NestedError(
                f"defaults exited {rc}: {(out + err).decode(errors='replace')}"
            )
        held = read_key(runner, host, domain, key)
        got = held if after is None else compared(held, value, dict_mode)[0]
        if not same(got, after):
            raise NestedError(
                f"after the write it holds {shown(got)}, not {shown(after)}"
            )
    return {"changed": True, "diff": diff}


def main():
    module = AnsibleModule(
        argument_spec={
            "domain": {"type": "str", "required": True},
            "host": {"type": "str", "default": ""},
            "key": {"type": "str", "required": True},
            "value": {"type": "raw"},
            "state": {
                "type": "str",
                "choices": ["present", "absent"],
                "default": "present",
            },
            "dict_mode": {
                "type": "str",
                "choices": ["add", "replace"],
                "default": "replace",
            },
        },
        supports_check_mode=True,
    )
    params = module.params
    try:
        result = ensure_nested(
            SimpleNamespace(run=lambda argv: module.run_command(argv, encoding=None)),
            params["domain"],
            params["host"],
            params["key"],
            params["value"],
            params["state"],
            params["dict_mode"],
            module.check_mode,
        )
    except NestedError as error:
        module.fail_json(msg=f"{params['domain']} {params['key']}: {error}")
    module.exit_json(**result)


if __name__ == "__main__":
    main()
