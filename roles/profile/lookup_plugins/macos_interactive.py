import os

from ansible.plugins.lookup import LookupBase

DOCUMENTATION = r"""
name: macos_interactive
short_description: Read the captured controller terminal intent
description:
  - Reads the process-only marker captured by the project callback before workers start.
  - Returns false when the marker is missing or unrecognized.
  - Does not inspect detached worker streams or piped module input.
author: mac-setup contributors
"""

EXAMPLES = r"""
profile_interactive: "{{ lookup('macos_interactive') }}"
"""

RETURN = r"""
_raw:
  description: Whether both controller streams are connected to a terminal.
  type: list
  elements: bool
"""


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        return [os.environ.get("MAC_SETUP_CONTROLLER_INTERACTIVE") == "1"]
