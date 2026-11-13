import os
import shutil
from pathlib import Path

from ansible.module_utils.verified_download import (
    DITTO,
    Refused,
    at_least,
    bundle_info,
    run_check,
    verified_app,
)

PS = "/bin/ps"


def running(target, runner):
    rc, out, err = runner.run([PS, "-axo", "comm="])
    prefix = f"{target}/"
    return rc == 0 and any(line.startswith(prefix) for line in out.splitlines())


def installed_version(target):
    if not (target / "Contents/Info.plist").is_file():
        return None
    return bundle_info(target)[1]


def recover_app(item, target):
    old = target.with_name(f".{target.name}.mac-setup-old")
    if old.exists():
        if target.exists():
            raise Refused(
                "install",
                item,
                f"both {target} and {old} exist; keep the wanted copy before retrying",
            )
        os.rename(old, target)
        return True
    return False


def replace_app(item, source, target, runner, version):
    staging = target.with_name(f".{target.name}.mac-setup-new")
    old = target.with_name(f".{target.name}.mac-setup-old")
    promoted = False
    try:
        shutil.rmtree(staging, ignore_errors=True)
        run_check("install", item, runner, [DITTO, str(source), str(staging)])
        if target.exists():
            os.rename(target, old)
        os.rename(staging, target)
        promoted = True
        if installed_version(target) != version:
            raise Refused(
                "install",
                item,
                f"{target} does not hold version {version} after the copy",
            )
    except BaseException:
        if promoted:
            shutil.rmtree(target)
        if old.exists():
            os.rename(old, target)
        raise
    else:
        if old.exists():
            shutil.rmtree(old)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def ensure_app(app, setup, applications, check_mode):
    target = Path(applications) / app.app
    recovered = not check_mode and recover_app(app.id, target)
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
        return result(recovered, "current", old)
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
        replace_app(app.id, new.path, target, setup.runner, new.version)
    return result(True, "installed", new.version)
