#!/usr/bin/env bash
set -euo pipefail
cd "${GITHUB_WORKSPACE:?}"
patch_file=041-fix-cmm_data-header-translated-port-security.patch
echo "b8be9e00bef41d8321c511a0b499239615afb9ffb5e65afe2764528c9fc1c3da  patches/$patch_file" | sha256sum -c -
cp feeds.conf.default openwrt/feeds.conf.default
cp .config openwrt/.config
cp "patches/$patch_file" "openwrt/package/mtk/drivers/mt_wifi/patches-7673/$patch_file"
# Use a higher package release so opkg can distinguish this fix from release 1.
sed -i '/^PKG_NAME:=mt_wifi$/a PKG_RELEASE:=2' openwrt/package/mtk/drivers/mt_wifi/Makefile
cd openwrt
./scripts/feeds update -a
./scripts/feeds install -a
make defconfig
grep -qx 'CONFIG_TARGET_mediatek_mt7981_DEVICE_konka_komi-a31=y' .config
grep -qx 'CONFIG_MTK_MT_WIFI_DRIVER_VERSION_7673=y' .config
grep -qx 'CONFIG_PACKAGE_kmod-mt_wifi=y' .config
grep -qx 'CONFIG_PACKAGE_kmod-crypto-user=y' .config
# Record the actual feed revisions and all local source changes.
./scripts/feeds list -s > ../diagnostics/resolved.feeds
git diff > ../diagnostics/source-changes.patch
cp package/mtk/drivers/mt_wifi/patches-7673/041* ../diagnostics/
git rev-parse HEAD > ../diagnostics/source-commit.txt
