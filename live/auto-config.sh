#!/bin/sh
# Moor Linux Phase 1: the exact `lb config` invocation.
# Run from the live/ directory (as root): ./auto-config.sh && lb build
# Mirrors: deb.debian.org everywhere (CDN; works on GitHub-hosted runners).
# Override with MOOR_MIRROR=http://your.mirror/debian/ if needed.
set -e
MIRROR="${MOOR_MIRROR:-http://deb.debian.org/debian/}"
lb config noauto \
  --mode debian \
  --distribution trixie \
  --architectures amd64 \
  --archive-areas "main contrib non-free-firmware" \
  --mirror-bootstrap "$MIRROR" \
  --mirror-chroot "$MIRROR" --parent-mirror-chroot "$MIRROR" \
  --mirror-binary http://deb.debian.org/debian/ --parent-mirror-binary http://deb.debian.org/debian/ \
  --apt-options "--yes -o Acquire::Retries=10" \
  --security true --updates true \
  --binary-images iso-hybrid \
  --bootloaders "grub-efi syslinux" \
  --uefi-secure-boot enable \
  --debian-installer none \
  --apt-recommends false \
  --firmware-chroot false --firmware-binary false \
  --linux-packages "linux-image" --linux-flavours amd64 \
  --iso-application "Moor Linux" --iso-volume "MOOR_LINUX" \
  --iso-publisher "Moor Linux" --iso-preparer "live-build" \
  --image-name moor-linux \
  --memtest none \
  --bootappend-live "boot=live components quiet username=moor hostname=moor locales=en_US.UTF-8 keyboard-layouts=us" \
  --chroot-squashfs-compression-type xz \
  "$@"
