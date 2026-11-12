VALUE_KINDS = {
    "bool": (
        lambda spec, value: isinstance(value, bool),
        lambda spec: "true or false",
    ),
    "int": (
        lambda spec, value: (
            isinstance(value, int)
            and not isinstance(value, bool)
            and spec["min"] <= value <= spec["max"]
        ),
        lambda spec: f"a whole number from {spec['min']} to {spec['max']}",
    ),
}


def _allowed(spec, value):
    if isinstance(spec, list):
        return value in spec
    return VALUE_KINDS[spec["type"]][0](spec, value)


def _describe(spec):
    if isinstance(spec, list):
        return "one of " + ", ".join(str(choice) for choice in spec)
    return VALUE_KINDS[spec["type"]][1](spec)


def profile_problems(settings, reference):
    if not isinstance(settings, dict):
        return ["default_settings: must map setting ids to values"]
    problems = []
    for setting_id, value in settings.items():
        entry = reference.get(setting_id)
        if entry is None:
            problems.append(f"{setting_id}: unknown setting id")
        elif not _allowed(entry["values"], value):
            problems.append(
                f"{setting_id}: {value!r} is not allowed, "
                f"use {_describe(entry['values'])}"
            )
    return problems


def profile_defaults_writes(settings, reference):
    return [
        {
            "id": setting_id,
            "domain": write["domain"],
            "key": write["key"],
            "type": write["type"],
            "value": value,
            "takes_effect": reference[setting_id]["takes_effect"],
        }
        for setting_id, value in settings.items()
        if reference[setting_id]["route"] == "defaults"
        for write in reference[setting_id]["writes"]
    ]


class FilterModule:
    def filters(self):
        return {
            "profile_problems": profile_problems,
            "profile_defaults_writes": profile_defaults_writes,
        }
