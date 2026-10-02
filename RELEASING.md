# Releasing

One version number for the program and every package. A release is a signed
tag on `main`; the Fedora and Arch packages are built from that tag, never
from anything else.

## 1. Prepare — pull request `release/X.Y.Z` (@4bit33)

Set the version in all four places; `tests/test_packaging.py` fails if they
disagree:

| File | What |
|---|---|
| `pyproject.toml` | `version = "X.Y.Z"` |
| `fancontrol/__init__.py` | `__version__ = "X.Y.Z"` |
| `packaging/fedora/fancontrol-linux.spec` | `Version:` and a new `%changelog` entry on top |
| `packaging/arch/PKGBUILD` | `pkgver=X.Y.Z`, `pkgrel=1` |

In `CHANGELOG.md`, turn *Unreleased* into `## X.Y.Z — YYYY-MM-DD`.

Open the pull request, wait for CI on Ubuntu, Fedora and Arch, get the
approval, merge.

## 2. Tag and publish on GitHub (@4bit33)

```bash
git switch main && git pull
git tag -s vX.Y.Z -m "fancontrol-linux X.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --title "X.Y.Z — <one line>" --notes-file notes.md
```

Release notes: what changed for users, how to update, and a short Ukrainian
paragraph at the end. Windows that check for updates will show the new
version within a day.

## 3. Fedora — COPR (@4bit33)

```bash
tools/copr-build.sh vX.Y.Z
```

COPR clones the tag and builds for every enabled Fedora release (about three
minutes). Check https://copr.fedorainfracloud.org/coprs/4bit33/fancontrol-linux/
then update a real machine: `sudo dnf upgrade --refresh fancontrol-linux`.

## 4. Arch — AUR (@W1zago)

On an Arch machine, from an up-to-date `main`:

```bash
tools/aur-publish.sh vX.Y.Z
```

It downloads the tag's archive, writes its checksum into the PKGBUILD,
regenerates `.SRCINFO` and pushes to the AUR. Then `yay -Syu` on a real
machine.

## 5. Check

* The release page shows the tag as **Verified**.
* `fanctl --version` says X.Y.Z on Fedora and on Arch.
* `systemctl status fancontrold` is running the new version — both packages
  restart it on upgrade.

## If something is wrong after the tag

Don't move or delete a published tag. Fix it in a pull request and release
X.Y.Z+1. A packaging-only fix can instead raise `Release:` in the spec or
`pkgrel` in the PKGBUILD and rebuild that one package from the same tag.

## One-time setup

* **@4bit33:** `copr-cli` with an API token in `~/.config/copr` (from
  https://copr.fedorainfracloud.org/api/ — it expires after about six months).
* **@W1zago:** an account on https://aur.archlinux.org with an SSH key, and
  `base-devel` installed for `makepkg`.
* **Both:** commit and tag signing, see [CONTRIBUTING.md](CONTRIBUTING.md).
