# Keep and reuse a private profile

The [README](../README.md#quick-start) uses `~/.dot/mac-setup/inventory/` for the profile, a folder of settings files.
Ansible calls this folder an inventory. The `-i` option chooses it.
These commands run from `~/mac-setup` after bootstrap.

## Choose a profile location

Choose one location before copying or editing the example.

1. The README's outside-checkout location survives deletion of the downloaded project.
   Every run selects it explicitly:

   ```sh
   .local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K --check
   ```

2. The configured default is `.local/inventory/` inside the checkout.
   Git ignores `.local/`, but deleting the checkout deletes this profile too.
   To create that profile without replacing an existing folder, run:

   ```sh
   test -d .local/inventory || cp -R inventory/example .local/inventory
   ```

   That route can omit `-i`:

   ```sh
   .local/venv/bin/ansible-playbook playbooks/site.yml -K --check
   ```

A local folder alone is not a backup. Back up the chosen profile or keep it in a private repository.
Keep real names, profiles, and passwords out of public repositories and reports.
An encrypted vault may travel with a private profile. Its password file stays outside version control.

## Move an existing profile outside the checkout

Copy your edited profile, rather than starting over from the example.
This command stops if the destination already exists, including a symbolic link:

```sh
mkdir -p ~/.dot/mac-setup
if [ -e ~/.dot/mac-setup/inventory ] || [ -L ~/.dot/mac-setup/inventory ]; then
  echo 'The destination exists. Keep it and compare the profiles before continuing.'
else
  cp -R .local/inventory ~/.dot/mac-setup/inventory
fi
```

Review the copied `accounts.yml` and `machine.yml`, then preview with the explicit `-i` command above.
Keep the original until you have checked the copy. Future commands must select the outside-checkout profile.
The vault password stays at `~/.dot/mac-setup/vault-password` throughout this move.

## Use one profile on several Macs

Each Mac runs mac-setup locally. A shared profile does not make one Mac configure another.
Restore the profile on each Mac before previewing.
If it contains a vault, restore the original password separately, following [Secrets](../README.md#secrets).

On each Mac, find its local hostname:

```sh
scutil --get LocalHostName
```

The example below assumes the names are `office-mac` and `studio-mac`.
Use your Macs' actual output in both `hosts.yml` and the folder names.
Names must match exactly, including capital and small letters.
To change the local hostname, use **System Settings > General > Sharing**.

For a profile with the original `example-mac` folder, these commands make the two folders.
Existing destinations keep their files.

```sh
test -e ~/.dot/mac-setup/inventory/host_vars/office-mac || mv ~/.dot/mac-setup/inventory/host_vars/example-mac ~/.dot/mac-setup/inventory/host_vars/office-mac
test -e ~/.dot/mac-setup/inventory/host_vars/studio-mac || cp -R ~/.dot/mac-setup/inventory/host_vars/office-mac ~/.dot/mac-setup/inventory/host_vars/studio-mac
open -e ~/.dot/mac-setup/inventory/hosts.yml
```

Replace the contents of `hosts.yml` with these entries, using your Macs' names:

```yaml
macs:
  hosts:
    office-mac:
      ansible_connection: local
      ansible_python_interpreter: "{{ ansible_playbook_python }}"
    studio-mac:
      ansible_connection: local
      ansible_python_interpreter: "{{ ansible_playbook_python }}"
```

The relevant files now look like this:

```text
inventory/
  hosts.yml
  host_vars/
    office-mac/
      accounts.yml
      machine.yml
    studio-mac/
      accounts.yml
      machine.yml
```

Review both Macs' settings files, especially time zones, accounts, firewall rules, and apps.
When the profile lists multiple Macs, a run applies only the entry matching this Mac's local hostname.
A missing match stops before changes and prints the hostname found.
A profile with one Mac skips the name comparison.
A vault in `group_vars/all/` is shared by both entries.

On each Mac, preview before applying:

```sh
.local/venv/bin/ansible-playbook -i ~/.dot/mac-setup/inventory playbooks/site.yml -K --check
```

After reviewing the summary, run that command without `--check` at that Mac's screen.

## Choose another vault password location

`scripts/vault-password` reads `DOT_HOME/mac-setup/vault-password`.
`DOT_HOME` defaults to `~/.dot`. Changing it selects only the password location.
It does not select the profile.

For example, with both stored under `~/private-dot/mac-setup/`, preview with:

```sh
DOT_HOME="$HOME/private-dot" .local/venv/bin/ansible-playbook -i ~/private-dot/mac-setup/inventory playbooks/site.yml -K --check
```

The password must be a regular file you own with mode 600.
Restore an existing vault's original password. Do not generate a replacement for it.
