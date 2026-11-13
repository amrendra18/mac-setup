# Installation reference

The [README](../README.md#quick-start) gives the first-run steps.
These tables list installation destinations and network hosts.
Sizes below are historical measurements before app downloads, not storage limits or promises for every Mac.

## Bootstrap output and retry

`scripts/bootstrap` checks 11 downloads, uv, 9 Python packages, and one Ansible collection, against pinned SHA-256 hashes.
It prints `OK` and a file name for each. uv downloads Python and checks its hash separately.
The last version line is `ansible-playbook [core 2.21.5]`.
A fingerprint mismatch deletes that download and exits with code 1.
A network failure stops the bootstrap with the download tool's exit code.
Run `scripts/bootstrap` again after either failure.
A second complete run downloads nothing and changes nothing.
`scripts/bootstrap --help` prints usage without installing anything.

The bootstrap prints examples using `.local/inventory/`, Ansible's configured default.
The README selects the outside-checkout profile explicitly with `-i ~/.dot/mac-setup/inventory`.

## Hosts the clone and bootstrap contact

| # | Host | What comes from it |
| --- | --- | --- |
| 1 | `github.com` | This repository (`git clone`) and uv 0.13.4 |
| 2 | `release-assets.githubusercontent.com` | GitHub's file host. `github.com` redirects the uv download here. |
| 3 | `releases.astral.sh` | Python 3.13.15 (build 20260929). uv downloads it and checks its hash. |
| 4 | `files.pythonhosted.org` | The 9 Python packages pinned in `requirements/ansible.txt`, from the direct links in `requirements/ansible-urls.txt` |
| 5 | `galaxy.ansible.com` | The community.general 13.5.0 collection pinned in `requirements/collections.txt` |
| 6 | `ansible-galaxy-ng.s3.dualstack.us-east-1.amazonaws.com` | Galaxy's file host. `galaxy.ansible.com` redirects the collection download here. |

uv is a tool that installs Python. Every download uses HTTPS. The bootstrap never reads the package index at `pypi.org`. A run contacts no host unless the profile lists apps. An app install contacts the app's host (see the [app download hosts](#app-download-hosts) table) and `www.virustotal.com`.

## Folders mac-setup writes

| # | Place | What it holds | Size measured |
| --- | --- | --- | --- |
| 1 | `.local/uv/` | uv 0.13.4 | 27,232 KiB |
| 2 | `.local/downloads/` | The uv and collection archives. A run that installs apps adds each app's download and its VirusTotal verdict here, owned by root. | 16,840 KiB before any app |
| 3 | `.local/wheels/` | The 9 Python package files | 6,920 KiB |
| 4 | `.local/venv/` | The Python virtual environment with Ansible | 49,636 KiB |
| 5 | `.local/collections/` | The community.general collection | 26,292 KiB |
| 6 | `.local/ansible-home/` | Ansible's own settings and temporary files | 4 KiB |
| 7 | `.local/logs/` | `ansible.log`, the log of every run | 4 KiB |
| 8 | `.local/finder-sidebar/` | The compiled Finder sidebar helper, written by the first run or preview that includes a sidebar setting | not measured |
| 9 | `~/.local/share/uv/python/` | Python 3.13.15 in `cpython-3.13.15-macos-aarch64-none`, the link `cpython-3.13-macos-aarch64-none`, and uv's files `.gitignore`, `.lock` and `.temp` | 72,520 KiB |
| 10 | `~/.dot/mac-setup/` | Your private profile and vault password. The README uses this profile location. | not measured |
| 11 | `/Applications/` | Each app in `apps.install`. An upgrade replaces the old copy. | not measured |

Rows 1 to 8 sit inside the checkout, and git ignores `.local/`. The alternative profile in `.local/inventory/` sits in the same ignored folder. Apple's command tools step may install Apple's Command Line Tools in `/Library/Developer/CommandLineTools`. mac-setup never removes them.

The bootstrap changes nothing else in your home folder. uv adds no link in `~/.local/bin` and keeps no cache or settings. Ansible keeps its files in `.local/`, so no `~/.ansible` folder appears.


## App download hosts

| # | Id | App | Download host | What it needs once installed |
| --- | --- | --- | --- | --- |
| 1 | `google_chrome` | Google Chrome | `dl.google.com` | Nothing. Chrome updates itself. |
| 2 | `firefox` | Firefox | `archive.mozilla.org` | Nothing. Firefox updates itself. |
| 3 | `sublime_text` | Sublime Text | `download.sublimetext.com` | Nothing. |
| 4 | `little_snitch` | Little Snitch | `www.obdev.at` | A license, and your approval of its network filter in System Settings. |
| 5 | `micro_snitch` | Micro Snitch | `www.obdev.at` | A license. |
| 6 | `proton_pass` | Proton Pass | `proton.me` | A sign-in to your Proton account. |
| 7 | `ente_auth` | Ente Auth, Ente's two-factor code app | `github.com`, then `release-assets.githubusercontent.com` | A sign-in to your Ente account. |
| 8 | `utm` | UTM, an app that runs virtual machines | `github.com`, then `release-assets.githubusercontent.com` | A virtual machine may ask you to allow access the first time it runs. |


A run without apps contacts no network host.
App previews and installs contact these hosts and `www.virustotal.com` when a lookup is needed.
[Download checks](how-it-works.md#how-downloads-are-checked) explain the checks and cached verdicts.
