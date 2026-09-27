# Third-party components

This repository contains only build configuration and scripts (MIT, see
`LICENSE`). The ISO it produces bundles or fetches components that keep their
own licenses. Nothing below is vendored in this repository; everything is
fetched at build time.

| Component | Source (pinned) | License | Where it ends up |
|---|---|---|---|
| Moor web desktop | https://github.com/kaiser6k/moor-linux @ `025662677527f38ce4d61650058ff32f29365927`, plus `patches/moor-iso-changes.diff` | MIT | `/opt/moor/www`, license at `/usr/share/doc/moor-linux/copyright` |
| WhiteSur GTK theme (vinceliuice) | https://github.com/vinceliuice/WhiteSur-gtk-theme @ `d5782652d412137e26fb8ff55b55a5572e4c6995` | MIT (`COPYING`, "Copyright (c) 2021 WhiteSur Developers") | `/usr/share/themes/WhiteSur-*`, `/etc/skel/.config/gtk-4.0` |
| WhiteSur icon theme (vinceliuice) | https://github.com/vinceliuice/WhiteSur-icon-theme @ `73d8040da51a9ed74e47c7366e7e9ff437601a5c` | GPL-3.0 (`COPYING`) | `/usr/share/icons/WhiteSur{,-dark,-light}` |
| WhiteSur cursors (vinceliuice) | https://github.com/vinceliuice/WhiteSur-cursors @ `e190baf618ed95ee217d2fd45589bd309b37672b` | GPL-3.0 (`LICENSE`) | `/usr/share/icons/WhiteSur-cursors` |
| Dash to Dock | Debian package `gnome-shell-extension-dashtodock` (100-2) | GPL-2.0-or-later (Debian copyright file) | installed from Debian |
| Debian 13 "trixie" packages | deb.debian.org | Various (see `/usr/share/doc/*/copyright` in the image) | the whole base system |

WhiteSur license texts are copied into `/usr/share/doc/whitesur-themes/` in
the image, with a `SOURCES` file naming the exact commits.

The in-browser CheerpX Linux VM used by the hosted Moor app is **not**
included: its license does not allow redistribution. `scripts/build-moor.sh`
fails the build if a `leaningtech` reference shows up in the static output.

Moor Linux is not affiliated with Debian. Debian is a registered trademark
owned by Software in the Public Interest, Inc.
