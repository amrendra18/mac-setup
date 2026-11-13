#!/usr/bin/python
from pathlib import Path

from ansible.module_utils.basic import AnsibleModule

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
    from ansible.module_utils.app_install import ensure_app
    from ansible.module_utils.verified_download import (
        Clock,
        Refused,
        Runner,
        Setup,
        default_opener,
        parse_app,
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
