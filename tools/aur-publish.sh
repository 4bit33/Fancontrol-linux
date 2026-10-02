#!/usr/bin/env bash
# Publish a tagged release to the AUR, so `yay -Syu` picks it up.
#
#   tools/aur-publish.sh v1.2.0            # the package is fancontrol-linux
#   AUR_SSH=aurora tools/aur-publish.sh v1.2.0
#
# Needs an AUR account with your SSH key uploaded
# (https://wiki.archlinux.org/title/AUR#Authenticate).
# Clones the AUR repo in a scratch directory, fills in the release's
# checksum, regenerates .SRCINFO (needs makepkg, so run it on Arch), and
# pushes. Push the tag to GitHub first; pkgver in packaging/arch/PKGBUILD must
# already match it.
set -euo pipefail

tag="${1:?usage: $0 <tag, e.g. v1.2.0>}"
repo="https://github.com/4bit33/Fancontrol-linux.git"
aur_host="${AUR_SSH:-aur.archlinux.org}"
pkg="fancontrol-linux"
ver="${tag#v}"

git ls-remote --exit-code --tags "$repo" "refs/tags/$tag" >/dev/null \
    || { echo "tag $tag is not on GitHub yet - push it first" >&2; exit 1; }

top="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pkgbuild="$top/packaging/arch/PKGBUILD"
if ! grep -q "^pkgver=$ver$" "$pkgbuild"; then
    echo "PKGBUILD still says $(grep '^pkgver=' "$pkgbuild") - bump it to $ver first" >&2
    exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

git clone "ssh://aur@$aur_host/$pkg.git" "$work/$pkg"
cp "$pkgbuild" "$work/$pkg/"

# Checksum the very URL the PKGBUILD's source= downloads.
tarball="$work/$pkg-$ver.tar.gz"
curl -fsSL -o "$tarball" "https://github.com/4bit33/Fancontrol-linux/archive/v$ver/$pkg-$ver.tar.gz"
sum="$(sha256sum "$tarball" | cut -d' ' -f1)"
rm "$tarball"
sed -i "s/^sha256sums=.*/sha256sums=('$sum')/" "$work/$pkg/PKGBUILD"

# The .SRCINFO the AUR parses must match PKGBUILD exactly; regenerate it.
(cd "$work/$pkg" && makepkg --printsrcinfo > .SRCINFO)

(cd "$work/$pkg" && git add PKGBUILD .SRCINFO \
    && git -c user.name="${GIT_AUTHOR_NAME:-$(git config user.name)}" \
           -c user.email="${GIT_AUTHOR_EMAIL:-$(git config user.email)}" \
           commit -m "Release $tag" \
    && git push)
