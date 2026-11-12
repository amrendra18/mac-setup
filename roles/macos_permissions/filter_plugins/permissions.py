APPS = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm"}
NOT_PERMITTED = "Operation not permitted"
SKIP_REASONS = {
    "ssh": "which a run over SSH does not have; run mac-setup at the Mac's screen to set it",
    "check": "which is off; a run at the Mac's screen without --check asks for it",
    "screen": "which is still off; turn on {app} in {where} and run again",
}


def _joined(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _needed_by(permission):
    settings = permission["settings"]
    verb = "needs" if len(settings) == 1 else "need"
    return f"{permission['name']}, which {_joined(settings)} {verb}"


def _tick(permission, app):
    return permission["tick"].format(app=app)


def _sentence(text):
    return text[0].upper() + text[1:] + "."


def macos_permissions_mode(ssh_values, check_mode):
    if any(ssh_values):
        return {
            "ask": False,
            "why": "ssh",
            "reason": "this run asks for no permission, because it runs over SSH, "
            "where nobody at the Mac's screen can answer",
        }
    if check_mode:
        return {
            "ask": False,
            "why": "check",
            "reason": "this run asks for no permission, because it is a check run (--check)",
        }
    return {"ask": True, "why": "screen", "reason": ""}


def macos_permissions_app(term_program):
    return APPS.get(term_program, "the app you run mac-setup in")


def macos_permissions_state(result):
    if "rc" not in result:
        return None
    if result["rc"] == 0:
        return "on"
    return "off" if NOT_PERMITTED in result.get("stderr", "") else None


def macos_permissions_ask_lines(permission, app):
    if permission["ask"] == "dialog":
        return [
            _needed_by(permission),
            _sentence(_tick(permission, app))
            + " If you answered before, macOS asks nothing now.",
        ]
    lines = [
        _needed_by(permission),
        f"In {permission['where']}, which opens now, {_tick(permission, app)}.",
    ]
    if permission["temporary"]:
        lines.append(
            "This run asks you to turn it off again at the end. "
            "If you stop the run with Control-C, turn it off there yourself."
        )
    return lines


def macos_permissions_list_line(permission, state, app):
    if state == "on":
        return f"{permission['name']} is on, so a run would not ask for it"
    if state is None and permission["ask"] == "pane":
        return (
            f"could not test {permission['name']}, so a run tries "
            f"{_joined(permission['settings'])} anyway"
        )
    where = "" if permission["ask"] == "dialog" else f"in {permission['where']}, "
    return f"would ask for {_needed_by(permission)}: {where}{_tick(permission, app)}"


def macos_permissions_take_back_lines(permission, app):
    return [
        f"{permission['name']} was on only for this run",
        f"In {permission['where']}, which opens now, turn off {app}.",
    ]


def _result(results, permission):
    return results.get(permission["id"], {})


def macos_permissions_blocked(permissions, results):
    return list(
        dict.fromkeys(
            setting_id
            for permission in permissions
            if _result(results, permission).get("after") == "off"
            for setting_id in permission["skips"]
        )
    )


def macos_permissions_take_back(permissions, results):
    return [
        permission
        for permission in permissions
        if permission["temporary"]
        and _result(results, permission).get("before") == "off"
        and _result(results, permission).get("after") == "on"
    ]


def _state_line(permission, result, mode, app, skipped):
    name, where = permission["name"], permission["where"]
    after = result.get("after")
    if after == "off" and not set(permission["skips"]) & set(skipped):
        return f"{name}: off, but every setting that needs it already matches"
    if after == "off":
        return f"{name}: off"
    if after is None and permission["ask"] == "dialog" and not mode["ask"]:
        return (
            f"{name}: not tested, because the test shows macOS's dialog; "
            "a run at the Mac's screen asks for it"
        )
    if after is None:
        return (
            f"{name}: its test failed for another reason, so this run tried "
            f"{_joined(permission['settings'])} anyway"
        )
    if permission["temporary"] and result.get("before") == "off":
        if result.get("end") == "off":
            return f"{name}: on for this run, then off again"
        return f"{name}: still on; turn off {app} in {where}"
    if result.get("before") == "on":
        return f"{name}: on before this run, and left on"
    return f"{name}: on, and it stays on"


def _skip_line(setting_id, permission, mode, app):
    if setting_id not in permission["settings"]:
        return f"skipped {setting_id}: set in one step with {_joined(permission['settings'])}"
    reason = SKIP_REASONS[mode["why"]].format(app=app, where=permission["where"])
    return f"skipped {setting_id}: needs {permission['name']}, {reason}"


def macos_permissions_notes(permissions, results, skipped, mode, app):
    lines, named = [], set()
    for permission in permissions:
        result = _result(results, permission)
        lines.append(_state_line(permission, result, mode, app, skipped))
        lines += [
            _skip_line(setting_id, permission, mode, app)
            for setting_id in permission["skips"]
            if result.get("after") == "off"
            and setting_id in skipped
            and setting_id not in named
        ]
        if result.get("after") == "off":
            named.update(permission["skips"])
    return lines


class FilterModule:
    def filters(self):
        return {
            "macos_permissions_mode": macos_permissions_mode,
            "macos_permissions_app": macos_permissions_app,
            "macos_permissions_state": macos_permissions_state,
            "macos_permissions_ask_lines": macos_permissions_ask_lines,
            "macos_permissions_list_line": macos_permissions_list_line,
            "macos_permissions_take_back_lines": macos_permissions_take_back_lines,
            "macos_permissions_blocked": macos_permissions_blocked,
            "macos_permissions_take_back": macos_permissions_take_back,
            "macos_permissions_notes": macos_permissions_notes,
        }
