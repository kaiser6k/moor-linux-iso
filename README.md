# Moor Linux ISO (work in progress)

Build configuration for the **Moor Linux** live ISO. Moor Linux is a Debian 13
("trixie") based live image that boots into a lean GNOME desktop on Wayland,
themed with WhiteSur. It ships the [Moor](https://github.com/kaiser6k/moor-linux)
web desktop, served locally by nginx on `127.0.0.1:8080` and opened in Chromium
app mode.

- Live image only. **There is no installer yet.**
- Unofficial. Moor Linux is **not affiliated with Debian**, and the image removes
  Debian branding (os-release, boot menus, splash). Debian is a registered
  trademark owned by Software in the Public Interest, Inc.
- The ISO is only a CI **build artifact**. There are no releases yet.

## What's in the image
- Debian trixie packages (kernel 6.12, Mesa 25.0), gnome-core without
  recommends, GDM autologin as `moor`, zram swap, and a broad firmware set
  (AMD, Intel incl. Arc/Battlemage, Wi-Fi).
- The Moor static build in `/opt/moor/www`, the nginx site
  `/etc/nginx/sites-available/moor`, and the launcher `/usr/local/bin/moor-launch`
  (`chromium --app=http://127.0.0.1:8080/`).
- WhiteSur GTK, icons, cursors, and GNOME Shell theme, plus Dash to Dock at the
  bottom. The defaults live in `/etc/dconf/db/local.d/00-moor`.
- GNOME Software, PackageKit, and Tracker are removed or masked. The default
  session also masks evolution-data-server factories, GNOME Online Accounts,
  CUPS, Bluetooth, GeoClue, ModemManager, and other VM-idle units (list in
  `docs/VPS-NESTED-VM.md`). Animations are off. Dash to Dock and WhiteSur stay.
- Boot menu entry **Moor Linux Lite (Xfce)** for a smaller idle set: Xfce on
  X11, WhiteSur, Plank. Same ISO; `moor.session=xfce` selects LightDM.

## Repository layout
| Path | Purpose |
|---|---|
| `live/auto-config.sh` | The exact `lb config` invocation (`live/auto/config` calls it) |
| `live/config/` | Hand-made live-build config: package lists, hooks, apt options, includes |
| `patches/moor-iso-changes.diff` | ISO patch for moor-linux (`MOOR_ISO_STATIC=1` SPA build; the Debian/CheerpX app shows "Not available in this build") |
| `iso-overrides/linux-vm.html` | Static replacement for Moor's CheerpX page |
| `scripts/build-moor.sh` | Clones moor-linux at a pinned commit, applies the patch, builds, and stages it into `includes.chroot` |
| `scripts/stage-themes.sh` | Fetches WhiteSur at pinned commits and installs it into `includes.chroot` |
| `scripts/boot-test.py` | Headless QEMU boot test: menus, fail-safe, session text, app screenshots |
| `.github/workflows/build-iso.yml` | CI build |
| `.github/workflows/boot-test.yml` | Optional QEMU boot test on the built artifact |
| `docs/VPS-NESTED-VM.md` | Nested QEMU/KVM on a Linux VPS, idle-RAM table, morning steps |
| `scripts/vps-setup-nested-vm.sh` | Create or remove one Xfce nested VM on an Ubuntu or Debian VPS |
| `ROADMAP.md` | Later work, including the shared Slatebay dock look |
| `scripts/idle-ram.py` | QEMU idle-RAM probe (`free -m`, `smem -tk`, three-run median) |
| `BUILD-NOTES.md` | Detailed build notes, test results, hardware notes |

Themes and the Moor build are **not committed**. They are fetched and built at
pinned commits on every build.

## Build with GitHub Actions
`build-iso.yml` runs on pushes to `main`, on pull requests, and manually
(`workflow_dispatch`). It runs on `ubuntu-latest` inside a privileged
`debian:trixie` container. It installs live-build, stages Moor and WhiteSur,
runs `lb build`, and uploads these artifacts (kept for 14 days):

- **`moor-linux-iso`**: `moor-linux-amd64.hybrid.iso`, its `.sha256`, and `build-info.txt` (size, sha256, build seconds, commit)
- **`build-log`**: the live-build log

Download them from the workflow run page (Actions → Build ISO → run →
Artifacts). `boot-test.yml` runs after a successful build, on pull requests,
or manually (`run_id` selects which build artifact to boot). It boots the ISO
in QEMU (BIOS and UEFI, KVM if available, otherwise TCG) and uploads the boot
menu, a fail-safe desktop boot, an idle default session (`whoami`, `free -m`,
`/etc/os-release`), and screenshots of Moor, Files, and Terminal.

`scripts/vps-setup-nested-vm.sh` is shellchecked in that workflow and booted
from the same ISO artifact (TCG, and KVM when the runner has `/dev/kvm`).
A build needs about 14 GB of free disk. The standard runner has enough.

## Build locally
You need Debian trixie (a VM or a spare machine, not a busy shared host: the
chroot package install needs several GB of RAM) and root.

```sh
sudo apt-get install live-build debootstrap xorriso squashfs-tools \
  grub-efi-amd64-bin grub-pc-bin mtools dosfstools git ca-certificates \
  curl rsync xz-utils sassc libxml2-utils glib2.0-bin gtk-update-icon-cache
./scripts/build-moor.sh        # Node 22 is downloaded if needed
./scripts/stage-themes.sh
cd live
sudo lb clean --purge
sudo ./auto-config.sh
sudo lb build
# -> live/moor-linux-amd64.hybrid.iso
```

`MOOR_MIRROR=http://your.mirror/debian/ ./auto-config.sh` switches the
bootstrap and chroot mirrors. Scratch files go to `.work/` (set `WORK_DIR` to
change that).

Try it: `qemu-system-x86_64 -m 4096 -smp 2 -machine q35 -device virtio-vga -cdrom live/moor-linux-amd64.hybrid.iso`
(add `-accel kvm -cpu host` if you have KVM). The live user is `moor`,
password `live`.

## Known issues
- The Moor page logs React hydration error **#418** against the prerendered
  SPA shell, then recovers by client-rendering. Cosmetic.
- Stale "Debian" wording is left in parts of the Moor app.
- `/__grok/manifest.webmanifest` returns **404**. The PWA manifest route is a
  server route that the static build doesn't have.
- The Moor page renders slightly shifted to the left.
- evolution-data-server packages stay installed because gnome-shell depends on
  them. The factory units are masked, so the calendar menu can be empty.
- The guest clock shows UTC. No timezone is configured.
- Secure Boot (shim + signed GRUB) is included but untested with an
  SB-enforcing firmware.

### TODO
- **zstd initramfs**: added (`includes.chroot/etc/initramfs-tools/conf.d/compress`), first verified by the CI build.
- The other v2 fixes are in the config but **unverified in a booted image**:
  Chromium `--password-store=basic`, skipping the overview at login (Dash to
  Dock `disable-overview-on-startup`), `username=moor` on the fail-safe boot
  entry, the deb.debian.org binary mirror, and apt `Acquire::Retries "10"`.
- An installer.

## Credits and licenses
This repository (build config and scripts) is MIT licensed; see `LICENSE`.
Components in the image keep their own licenses; see `THIRD_PARTY.md`.

- **Moor** by kaiser6k: https://github.com/kaiser6k/moor-linux (MIT).
- **WhiteSur** themes by **vinceliuice**, installed unmodified:
  - https://github.com/vinceliuice/WhiteSur-gtk-theme: **MIT** (`COPYING`, "Copyright (c) 2021 WhiteSur Developers")
  - https://github.com/vinceliuice/WhiteSur-icon-theme: **GPL-3.0** (`COPYING`)
  - https://github.com/vinceliuice/WhiteSur-cursors: **GPL-3.0** (`LICENSE`)
  - The license texts ship in `/usr/share/doc/whitesur-themes/` in the image.
- **Dash to Dock** by micheleg (https://github.com/micheleg/dash-to-dock),
  installed from the Debian package `gnome-shell-extension-dashtodock`:
  **GPL-2.0-or-later**.
- **Debian** trixie packages under their respective licenses.
