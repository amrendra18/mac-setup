#!/usr/bin/python
from ansible.module_utils.basic import AnsibleModule

try:
    from ansible.module_utils.firefox_bookmarks import (
        BookmarkError,
        PermissionDenied,
        StartupError,
        import_bookmarks,
        inspect_bookmarks,
    )
except ImportError:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "firefox_bookmarks",
        Path(__file__).resolve().parents[3] / "module_utils/firefox_bookmarks.py",
    )
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    BookmarkError = importer.BookmarkError
    PermissionDenied = importer.PermissionDenied
    StartupError = importer.StartupError
    import_bookmarks = importer.import_bookmarks
    inspect_bookmarks = importer.inspect_bookmarks

DOCUMENTATION = r"""
module: macos_firefox_bookmarks
short_description: Import editable Firefox bookmarks once per account profile
description:
  - Uses Firefox's selected default profile through a private headless process.
  - Keeps existing bookmarks and records completion outside Firefox's profile directory.
  - Refuses a running Firefox and reports only counts.
options:
  operation:
    description: Inspect the selected profile or import bookmarks into it.
    type: str
    choices: [inspect, import]
    default: import
  source:
    description: Private plaintext Netscape HTML temporary file, required for import and removed by the calling role.
    type: path
  firefox_root:
    description: Optional isolated application data container for verification.
    type: path
  expected_profile_key:
    description: Opaque profile identity from inspection, checked before importing.
    type: str
author:
  - mac-setup maintainers
"""


def main():
    module = AnsibleModule(
        argument_spec={
            "operation": {
                "type": "str",
                "choices": ["inspect", "import"],
                "default": "import",
            },
            "source": {"type": "path", "no_log": True},
            "firefox_root": {"type": "path", "no_log": True},
            "expected_profile_key": {"type": "str", "no_log": True},
        },
        supports_check_mode=True,
    )
    if module.params["operation"] == "import" and not module.params["source"]:
        module.fail_json(msg="The import operation requires source.", changed=False)
    try:
        if module.params["operation"] == "inspect":
            result = inspect_bookmarks(
                firefox_root=module.params["firefox_root"], check=module.check_mode
            )
        else:
            result = import_bookmarks(
                module.params["source"],
                firefox_root=module.params["firefox_root"],
                check=module.check_mode,
                expected_profile_key=module.params["expected_profile_key"],
            )
    except BookmarkError as error:
        module.fail_json(
            msg=str(error),
            changed=False,
            permission_denied=isinstance(error, PermissionDenied),
            startup_check=error.check if isinstance(error, StartupError) else "",
        )
    module.exit_json(**result)


if __name__ == "__main__":
    main()
