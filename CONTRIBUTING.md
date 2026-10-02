# Contributing

One program, one repository, one history. Fedora, Arch and everything else
get the **same code**; only the packaging differs, and it lives side by side
in `packaging/`. Please don't keep a distribution's version in a fork or a
long-lived branch — it drifts, and every fix then has to be made twice.

Questions and reviews in English or Ukrainian are both fine.

## Who looks after what

| Area | Who | Where |
|---|---|---|
| The program: daemon, window, curves, importer | [@4bit33](https://github.com/4bit33) | `fancontrol/`, `data/`, `tests/` |
| Fedora package (COPR) | [@4bit33](https://github.com/4bit33) | `packaging/fedora/`, `.copr/`, `tools/copr-build.sh` |
| Arch package (AUR) | [@W1zago](https://github.com/W1zago) | `packaging/arch/`, `tools/aur-publish.sh` |
| `install.sh`, for every other distribution | both | `install.sh`, `tools/test-install-in-containers.sh` |

[`.github/CODEOWNERS`](.github/CODEOWNERS) says the same thing to GitHub, so
the right person is asked to review a pull request automatically.

## How a change gets in

1. **A short branch in this repository** — collaborators don't need a fork.
   Name it after what it does: `fix/sleep-resume`, `feature/identify-fan`,
   `arch/aur-publish`, `docs/readme-install`.
2. **Commit small, signed commits** whose message says *why*, in the
   imperative: "Hand the fans back after a resume", not "fixes".
3. **Open a pull request** into `main`. Say what it changes and how you
   checked it. A draft PR is fine while you are still working.
4. **CI must pass** on Ubuntu, Fedora and Arch.
5. **One approval** from the other person, then merge and delete the branch.

`main` is protected: no direct pushes from collaborators, no force pushes,
no deleting it. Never rewrite history that is already on GitHub.

### Signing commits

Every commit should show as **Verified**. With the SSH key you already use
for GitHub:

```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true
git config --global tag.gpgsign true
```

then add the same key on GitHub once more, under *Settings → SSH and GPG keys
→ New SSH key*, with **Key type: Signing Key**. The e-mail in your commits
(`git config user.email`) must be one of your GitHub account's addresses.

## Before you open a pull request

```bash
python3 -m venv --system-site-packages .venv     # PyGObject from the distribution
.venv/bin/pip install -e '.[dev,gui,daemon]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
```

Then, depending on what you touched:

* **The window:** every visible string goes through `tr()`, and
  `fancontrol/gui/locales/uk.py` gets the Ukrainian — `tests/test_i18n.py`
  fails otherwise.
* **Behaviour users notice:** a line under *Unreleased* in `CHANGELOG.md`,
  and both `README.md` and `README.uk.md` if they describe it.
* **Packaging:** build the package you changed — see the header of
  `packaging/arch/PKGBUILD`, or `.copr/Makefile` for the RPM.
* **`install.sh`:** `./tools/test-install-in-containers.sh` (all five
  distributions, or name one).
* **Anything that drives real fans:** say on which machine you tried it.
  Tests run against simulated hardware; only a real board proves the rest.

## Issues

Labels say what an issue is about: `bug`, `enhancement`, `hardware` (a report
from a real machine — use the template), and `fedora` or `arch` when only one
package is affected.

## Releases

One version, one tag, packages for every distribution built from it. The
steps, and who does which, are in [RELEASING.md](RELEASING.md).
