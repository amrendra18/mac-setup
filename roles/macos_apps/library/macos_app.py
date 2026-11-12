#!/usr/bin/python
import os
import shutil
from pathlib import Path

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.verified_download import (
    DITTO,
    Clock,
    Refused,
    Runner,
    Setup,
    at_least,
    bundle_info,
    default_opener,
    parse_app,
    run_check,
    verified_app,
)

DOCUMENTATION = r"""
module: macos_app
short_description: Install one catalog app into /Applications after verification
description:
  - Reads the installed app's CFBundleShortVersionString. At the catalog version or newer, it downloads nothing and changes nothing.
  - Otherwise verified_download downloads and checks the app, and ditto copies it into place. An older copy is replaced whole.
  - Check mode runs every check and installs nothing. Diff mode shows the old and new version.
  - The VirusTotal key arrives in virustotal_api_key, declared no_log, so Ansible hides it in output and logs. It is used only in the lookup's x-apikey header.
options:
  id: {description: Catalog id of the app., type: str, required: true}
  app: {description: The app's catalog entry from apps/macos.yml., type: dict, required: true}
  downloads: {description: Cache folder for downloads and VirusTotal verdicts., type: path, required: true}
  virustotal_api_key: {description: VirusTotal API key. Needed only when a file has no stored verdict., type: str, default: ""}
  applications: {description: Folder the app is installed into., type: path, default: /Applications}
"""


PS = "/bin/ps"


def running(target, runner):
    rc, out, err = runner.run([PS, "-axo", "comm="])
    prefix = f"{target}/"
    return rc == 0 and any(line.startswith(prefix) for line in out.splitlines())


def installed_version(target):
    if not (target / "Contents/Info.plist").is_file():
        return None
    return bundle_info(target)[1]


def replace_app(item, source, target, runner):
    staging = target.with_name(f".{target.name}.mac-setup-new")
    old = target.with_name(f".{target.name}.mac-setup-old")
    for leftover in (staging, old):
        shutil.rmtree(leftover, ignore_errors=True)
    run_check("install", item, runner, [DITTO, str(source), str(staging)])
    if target.exists():
        os.rename(target, old)
    os.rename(staging, target)
    shutil.rmtree(old, ignore_errors=True)


def ensure_app(app, setup, applications, check_mode):
    target = Path(applications) / app.app
    old = installed_version(target)

    def result(changed, state, new):
        return {
            "changed": changed,
            "app": {
                "id": app.id,
                "state": state,
                "old_version": old,
                "new_version": new,
            },
            "diff": {
                "before": f"{app.id}: {old or 'not installed'}\n",
                "after": f"{app.id}: {new}\n",
            },
        }

    if old is not None and at_least(app.id, old, app.version):
        return result(False, "current", old)
    with verified_app(app, setup) as new:
        if check_mode:
            return result(True, "would_install", new.version)
        if old is not None and running(target, setup.runner):
            raise Refused(
                "install",
                app.id,
                f"{app.app} is running; quit it and run again to upgrade "
                f"from {old} to {new.version}",
            )
        replace_app(app.id, new.path, target, setup.runner)
    if installed_version(target) != new.version:
        raise Refused(
            "install",
            app.id,
            f"{target} does not hold version {new.version} after the copy",
        )
    return result(True, "installed", new.version)


def main():
    module = AnsibleModule(
        argument_spec={
            "id": {"type": "str", "required": True},
            "app": {"type": "dict", "required": True},
            "downloads": {"type": "path", "required": True},
            "virustotal_api_key": {"type": "str", "default": "", "no_log": True},
            "applications": {"type": "path", "default": "/Applications"},
        },
        supports_check_mode=True,
    )
    params = module.params
    setup = Setup(
        downloads=Path(params["downloads"]),
        key=params["virustotal_api_key"],
        runner=Runner(),
        opener=default_opener(),
        clock=Clock(),
    )
    try:
        result = ensure_app(
            parse_app(params["id"], params["app"]),
            setup,
            params["applications"],
            module.check_mode,
        )
    except Refused as error:
        module.fail_json(msg=str(error), check=error.check)
    if not module._diff:
        del result["diff"]
    module.exit_json(**result)


if __name__ == "__main__":
    main()
