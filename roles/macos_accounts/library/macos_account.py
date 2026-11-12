#!/usr/bin/python
import os
import pty
import re
import select
import signal
import time
from types import SimpleNamespace

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: macos_account
short_description: Create a local macOS account, or adopt one that exists
description:
  - Reads the account with dscl. A missing account is created with sysadminctl -addUser, which also makes its home folder.
  - The password reaches sysadminctl through a pseudo-terminal, so it never sits on a command line.
  - An existing account is adopted unchanged. A different admin flag is reported in refused and changes nothing.
  - It never deletes an account or changes an existing account's password or admin rights.
options:
  name: {description: Short account name., type: str, required: true}
  full_name: {description: Full name for a new account., type: str, required: true}
  admin: {description: Whether the account is an admin., type: bool, default: false}
  password: {description: Password for a new account. Required to create one outside check mode., type: str, default: ""}
"""

DSCL = "/usr/bin/dscl"
DSMEMBERUTIL = "/usr/bin/dsmemberutil"
SYSADMINCTL = "/usr/sbin/sysadminctl"
PROMPT = re.compile(rb"password:\s*\Z", re.IGNORECASE)


class AccountError(Exception):
    pass


def answer_prompt(argv, secret, seconds=60):
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execv(argv[0], argv)
        finally:
            os._exit(127)
    output = b""
    answered = timed_out = False
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
            if not answered and PROMPT.search(output):
                os.write(fd, secret.encode() + b"\n")
                answered = True
        else:
            timed_out = True
            os.kill(pid, signal.SIGKILL)
    finally:
        os.close(fd)
        _, status = os.waitpid(pid, 0)
    text = output.decode(errors="replace").replace(secret, "<hidden>")
    if timed_out:
        raise AccountError(f"{argv[0]} did not finish within {seconds} seconds: {text}")
    if not answered:
        raise AccountError(f"{argv[0]} exited without a password prompt: {text}")
    return os.waitstatus_to_exitcode(status), text


def exists(runner, name):
    rc, out, err = runner.run([DSCL, ".", "-read", f"/Users/{name}", "RecordName"])
    if rc == 0:
        return True
    if "eDSRecordNotFound" in out + err:
        return False
    raise AccountError(f"{name}: dscl read failed with exit {rc}: {out}{err}")


def is_admin(runner, name):
    rc, out, err = runner.run(
        [DSMEMBERUTIL, "checkmembership", "-U", name, "-G", "admin"]
    )
    if "is a member" in out:
        return True
    if "is not a member" in out:
        return False
    raise AccountError(f"{name}: dsmemberutil failed with exit {rc}: {out}{err}")


def has_secure_token(runner, name):
    rc, out, err = runner.run([SYSADMINCTL, "-secureTokenStatus", name])
    for state, token in (("ENABLED", True), ("DISABLED", False)):
        if f"Secure token is {state}" in out + err:
            return token
    raise AccountError(
        f"{name}: sysadminctl -secureTokenStatus failed with exit {rc}: {out}{err}"
    )


def account(name, state, refused, secure_token):
    return {
        "name": name,
        "state": state,
        "refused": refused,
        "secure_token": secure_token,
    }


def ensure_account(runner, name, full_name, admin, password, check_mode):
    if exists(runner, name):
        current = is_admin(runner, name)
        refused = ""
        if current != admin:
            kind = "an admin" if current else "a standard"
            refused = f"{name}: the profile says admin: {str(admin).lower()}, but it is {kind} account; nothing changed"
        return {
            "changed": False,
            "account": account(
                name, "adopted", refused, has_secure_token(runner, name)
            ),
        }
    if check_mode:
        return {"changed": True, "account": account(name, "would_create", "", False)}
    if not password:
        raise AccountError(
            f"{name}: the password is empty, so the account was not created"
        )
    argv = [SYSADMINCTL, "-addUser", name, "-fullName", full_name, "-password", "-"]
    rc, out = runner.run_with_secret(argv + ["-admin"] if admin else argv, password)
    if rc != 0 or not exists(runner, name):
        raise AccountError(
            f"{name}: sysadminctl -addUser exited {rc} and the account does not exist: {out}"
        )
    return {
        "changed": True,
        "account": account(name, "created", "", has_secure_token(runner, name)),
    }


def main():
    module = AnsibleModule(
        argument_spec={
            "name": {"type": "str", "required": True},
            "full_name": {"type": "str", "required": True},
            "admin": {"type": "bool", "default": False},
            "password": {"type": "str", "default": "", "no_log": True},
        },
        supports_check_mode=True,
    )
    runner = SimpleNamespace(run=module.run_command, run_with_secret=answer_prompt)
    params = module.params
    try:
        result = ensure_account(
            runner,
            params["name"],
            params["full_name"],
            params["admin"],
            params["password"],
            module.check_mode,
        )
    except AccountError as error:
        module.fail_json(msg=str(error))
    module.exit_json(**result)


if __name__ == "__main__":
    main()
