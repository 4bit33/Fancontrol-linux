# COPR's "make_srpm" method runs this in a checkout of the repository:
#   make -f .copr/Makefile srpm outdir=<where the SRPM goes> spec=<ignored>
# It packs the checked-out commit exactly as GitHub's tag archive is laid out,
# so the spec's Source0 and %autosetup work for both.

top     := $(shell cd "$(dir $(lastword $(MAKEFILE_LIST)))/.." && pwd)
version := $(shell sed -n 's/^Version: *//p' $(top)/fancontrol-linux.spec)
outdir  ?= $(top)/build/srpm

srpm:
	command -v git >/dev/null || dnf -y install git-core
	command -v rpmbuild >/dev/null || dnf -y install rpm-build
	git config --global --add safe.directory '*'
	mkdir -p $(outdir)
	git -C $(top) archive --prefix=Fancontrol-linux-$(version)/ \
	    -o $(outdir)/fancontrol-linux-$(version).tar.gz HEAD
	rpmbuild -bs --define "_sourcedir $(outdir)" --define "_srcrpmdir $(outdir)" \
	    $(top)/fancontrol-linux.spec

.PHONY: srpm
