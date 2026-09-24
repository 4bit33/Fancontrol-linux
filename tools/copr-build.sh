#!/usr/bin/env bash
# Build a tagged release in COPR, so `dnf upgrade` picks it up.
#
#   tools/copr-build.sh v1.2.0            # the project is <you>/fancontrol-linux
#   COPR_PROJECT=someone/fancontrol-linux tools/copr-build.sh v1.2.0
#
# Needs copr-cli and an API token in ~/.config/copr
# (https://copr.fedorainfracloud.org/api/). COPR clones the tag from GitHub
# and runs .copr/Makefile, so push the tag first.
set -euo pipefail

tag="${1:?usage: $0 <tag, e.g. v1.2.0>}"
repo="https://github.com/4bit33/Fancontrol-linux.git"

if [[ -z "${COPR_PROJECT:-}" ]]; then
    user="$(copr-cli whoami)"
    COPR_PROJECT="$user/fancontrol-linux"
fi

git ls-remote --exit-code --tags "$repo" "refs/tags/$tag" >/dev/null \
    || { echo "tag $tag is not on GitHub yet - push it first" >&2; exit 1; }

exec copr-cli buildscm "$COPR_PROJECT" \
    --clone-url "$repo" --commit "$tag" \
    --method make_srpm --spec fancontrol-linux.spec
