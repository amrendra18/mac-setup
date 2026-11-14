import os
import sys

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = r"""
name: macos_interactive
type: aggregate
short_description: Capture controller terminal intent before task workers start
description:
  - Records whether the controller has terminal input and output at playbook start.
  - Task workers inherit the process-only marker after Ansible detaches their streams.
  - Replaces an inherited marker on every run, including unattended runs.
  - Changes no settings and opens no user interface.
requirements:
  - Enable the callback in the project Ansible configuration.
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "macos_interactive"
    CALLBACK_NEEDS_ENABLED = True

    def v2_playbook_on_start(self, playbook):
        try:
            interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
        except (AttributeError, OSError, ValueError):
            interactive = False
        os.environ["MAC_SETUP_CONTROLLER_INTERACTIVE"] = "1" if interactive else "0"
