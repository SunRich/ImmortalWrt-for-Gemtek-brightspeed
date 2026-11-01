#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
./scripts/feeds update -a
./scripts/feeds install -a
# These unselected packages have recursive Kconfig dependencies in the pinned
# feeds. Exclude their metadata, not any selected firmware component.
if grep -Eq '^CONFIG_PACKAGE_(fwknop|fwknopd|fwknop-client|squeezelite|luci-app-fwknopd)=y$' 1710.config; then
	echo 'The metadata exclusion now affects a selected package; resolve its dependency instead.' >&2
	exit 1
fi
./scripts/feeds uninstall fwknop squeezelite luci-app-fwknopd
bash scripts/fix-stale-golang-host.sh
FEED_PATCHES_STRICT=1 bash scripts/apply-feed-patches.sh
python3 scripts/configure-xr1710g-n6.py
BUILD_DATE="$(TZ=Asia/Shanghai date +%Y%m%d)" \
REPO_COMMIT="$(git rev-parse --short=10 HEAD)" \
UPSTREAM_COMMIT=2248ed7546 \
VERSION_NUMBER="$(TZ=Asia/Shanghai date +%Y%m%d)-n6-rc-$(git rev-parse --short=8 HEAD)" \
bash scripts/set-build-version.sh .config
make defconfig
bash scripts/check-gemtek-profile-isolation.sh --config .config
python3 scripts/verify-xr1710g-n6.py source
{
	printf 'Source commit: %s\n' "$(git rev-parse HEAD)"
	printf 'Project baseline: 8f92c5427fd902eb5c32006d1bc1ed9954055378\n'
	printf 'Upstream ancestry baseline: 2248ed75465327b4eed0ffcc3ece361dc1e62d75\n'
	printf 'Component update reference: 32bec3df4163228f00dd1cd054a28e7053f4d2ac\n'
	for feed in feeds/*; do
		if [[ -d "$feed/.git" ]]; then printf '%s %s\n' "${feed##*/}" "$(git -C "$feed" rev-parse HEAD)"; fi
	done
} > build-provenance.txt
