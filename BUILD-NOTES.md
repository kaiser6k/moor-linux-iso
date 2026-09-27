# Moor Linux: Phase 1 live ISO build notes

Build 1 was made on Sat Sep 26, 2026 on a shared x86_64 build host (8 cores, 15 GiB RAM, Debian trixie, overlay root; chroot, mount, and loop all worked).
Later local builds were OOM-killed during the chroot package install, so the build moved to GitHub Actions (see "Move to GitHub Actions"). Nothing has been released; the ISO is a CI artifact only.

## Layout (this repository)
| Path | What it is |
|---|---|
| `patches/moor-iso-changes.diff` | Source diff applied to `github.com/kaiser6k/moor-linux` @ `025662677527f38ce4d61650058ff32f29365927` (main, 2026-09-20) |
| `iso-overrides/linux-vm.html` | Replacement for `public/linux-vm.html` in the ISO build (no CheerpX) |
| `scripts/build-moor.sh` | Clone at the pinned commit, patch, `npm install`, static build, stage into `live/config/includes.chroot` |
| `scripts/stage-themes.sh` | Fetch WhiteSur at the pinned commits and install it into `live/config/includes.chroot` |
| `scripts/boot-test.py` | Headless QEMU smoke test (QMP screendump); simplified from the build-1 harness |
| `live/` | live-build tree (`auto-config.sh` = the exact `lb config`, `config/` = package lists, includes, and hooks) |
| `.github/workflows/` | `build-iso.yml` (CI build, artifacts only) and `boot-test.yml` (QEMU screenshots) |

Build 1 used Node v22.23.3 (official tarball) on the host to build Moor; it is not shipped in the image.

## Steps
1. Install `live-build debootstrap xorriso squashfs-tools grub-efi-amd64-bin grub-pc-bin mtools dosfstools sassc libxml2-utils git curl rsync` on a Debian trixie host (plus `qemu-system-x86 ovmf imagemagick` for tests).
2. `scripts/build-moor.sh`: clone Moor at the pinned commit and apply the patch. `npm ci` **fails upstream** because `package-lock.json` is out of sync with `package.json` (ajv 6 vs 8, fast-uri, json-schema-traverse, require-from-string), so the script uses `npm install`, which only changes the local lockfile. The upstream Dockerfile's `npm ci` would fail the same way.
3. The script builds Moor statically:
   `MOOR_ISO_STATIC=1 VITE_AUTH_ENABLED=false VITE_MOOR_ISO=true node scripts/with-app-env.mjs npx vite build`
   → `dist/client/` (5.1 MB in build 1). Then `iso-overrides/linux-vm.html` is copied into `dist/client/`, `_shell.html` is copied to `index.html`, and the result goes to `live/config/includes.chroot/opt/moor/www`. `db:migrate` is skipped (no DB when auth is off).
4. `scripts/stage-themes.sh`: stage the WhiteSur themes (see below) into `live/config/includes.chroot`.
5. `cd live && sudo lb clean --purge; sudo ./auto-config.sh && sudo lb build` (CI does the same in `build-iso.yml`).
6. QEMU smoke tests (see below, and `boot-test.yml`).

## Changes to the Moor source (patch) and why
- `vite.config.ts`: new `MOOR_ISO_STATIC=1` switch. When set, it uses `tanstackStart({ spa: { enabled: true } })` and **omits the Nitro plugin** (preset `vercel`). Result: a plain static site with a prerendered `/_shell.html` that nginx can serve with no Node on the ISO. The default (unset) build is unchanged.
  - SPA caveat: the client logs React hydration error #418 against the prerendered shell, then recovers by client-rendering. The desktop renders fine in Chromium (checked on the host with headless Chrome and inside the ISO). This is the TanStack SPA/Nitro rough edge the plan warned about (TanStack/router#5967-style). Cosmetic for now.
  - Lost in static mode: the Nitro `server/middleware/grok-pwa.ts` routes (`/__grok/manifest.webmanifest` → 404, `?install=1` page). Not needed on the ISO.
- `src/components/apps/linux-app.tsx`: when `VITE_MOOR_ISO=true`, the "Debian" app shows **"Not available in this build"** instead of the CheerpX launcher.
- `public/linux-vm.html` is replaced in the ISO output by a static friendly page with no `cx.esm.js` import. `grep -r leaningtech dist/client` finds nothing, so **no CheerpX code is redistributed**. nginx still sends COOP/COEP on `/linux-vm.html`.
- `package-lock.json`: regenerated locally by `npm install` (see step 2).

## lb config (from `live/auto-config.sh`)
```
lb config noauto --mode debian --distribution trixie --architectures amd64 \
  --archive-areas "main contrib non-free-firmware" \
  --mirror-bootstrap http://deb.debian.org/debian/ \
  --mirror-chroot http://deb.debian.org/debian/ --parent-mirror-chroot http://deb.debian.org/debian/ \
  --mirror-binary http://deb.debian.org/debian/ --parent-mirror-binary http://deb.debian.org/debian/ \
  --apt-options "--yes -o Acquire::Retries=10" \
  --security true --updates true \
  --binary-images iso-hybrid --bootloaders "grub-efi syslinux" --uefi-secure-boot enable \
  --debian-installer none --apt-recommends false \
  --firmware-chroot false --firmware-binary false \
  --linux-packages "linux-image" --linux-flavours amd64 \
  --iso-application "Moor Linux" --iso-volume "MOOR_LINUX" --iso-publisher "Moor Linux" --iso-preparer "live-build" \
  --image-name moor-linux --memtest none \
  --bootappend-live "boot=live components quiet username=moor hostname=moor locales=en_US.UTF-8 keyboard-layouts=us" \
  --chroot-squashfs-compression-type xz
```
- Mirrors: the local builds used a university mirror for bootstrap/chroot as a workaround for that host's network. On GitHub runners everything uses deb.debian.org (`MOOR_MIRROR` overrides bootstrap/chroot). `config/apt/apt.conf` sets `Acquire::Retries "10"` and `Acquire::http::Timeout "30"` inside the chroot.
 --uefi-secure-boot enable \
  --debian-installer none --apt-recommends false \
  --firmware-chroot false --firmware-binary false \
  --linux-packages "linux-image" --linux-flavours amd64 \
  --iso-application "Moor Linux" --iso-volume "MOOR_LINUX" --iso-publisher "Moor Linux" --iso-preparer "live-build" \
  --image-name moor-linux --memtest none \
  --bootappend-live "boot=live components quiet username=moor hostname=moor locales=en_US.UTF-8 keyboard-layouts=us" \
  --chroot-squashfs-compression-type xz
```
- I set `--firmware-chroot false` and list firmware explicitly. `true` would pull *every* non-free-firmware package.
- Package list: `config/package-lists/moor.list.chroot` covers gnome-core, gnome-console, gdm3, xwayland, xdg-desktop-portal-gnome, gnome-shell-extension-dashtodock (100-2), gnome-shell-extension-user-theme (48.2-1), dconf-cli, dbus-user-session, libpam-systemd, network-manager, fonts, locales, sudo, user-setup, zram-tools, zstd, nginx, and chromium. For Mesa: libgl1-mesa-dri, libglx-mesa0, libegl-mesa0, libgbm1, mesa-vulkan-drivers, mesa-va-drivers. For firmware: firmware-linux-free, -misc-nonfree, -amd-graphics, -intel-graphics, -intel-sound, sof-signed, -iwlwifi, -realtek, -atheros, -brcm80211, -mediatek, intel-microcode, and amd64-microcode.
- Secure Boot path: `--uefi-secure-boot enable` installs shim-signed + grub-efi-amd64-signed into the EFI image. That wasn't separately tested with an SB-enforcing OVMF (see "Unverified").

### Includes and hooks
- `includes.chroot/opt/moor/www`: the static Moor build. `etc/nginx/sites-available/moor`: `listen 127.0.0.1:8080`, SPA fallback `try_files $uri $uri/ /_shell.html`, COOP/COEP on `/linux-vm.html`. The default nginx site is removed.
- `usr/share/applications/moor.desktop` + `usr/local/bin/moor-launch` → `chromium --app=http://127.0.0.1:8080/ --password-store=basic`. The icon is `public/brand/icon-256/512.png` installed as `hicolor/.../moor.png`.
- `etc/chromium.d/moor-flags`: `--password-store=basic`. Without it Chromium pops a "create Default Keyring password" dialog in the autologin session, as seen in the first test.
- `etc/gdm3/daemon.conf`: `WaylandEnable=true`, `AutomaticLoginEnable=true`, `AutomaticLogin=moor`. The live user `moor` (password `live`) is created by live-config (`username=moor`).
- `etc/dconf/profile/user` + `etc/dconf/db/local.d/00-moor`: the system dconf defaults (users can override them). These set the WhiteSur GTK, icon, and cursor themes, shell theme `WhiteSur-Dark-solid`, `enabled-extensions` = user-theme + dash-to-dock only, dock at the bottom (fixed, 48 px, no trash or mounts, `disable-overview-on-startup`), favorites (Moor, Files, Console, Chromium, Settings), a macOS-style `close,minimize,maximize:` button layout, the Moor fjord wallpaper, no screen lock or idle blank, Tracker crawling off, and GNOME Software updates off.
- `etc/default/zramswap`: `ALGO=zstd PERCENT=50`.
- `etc/skel/.config/gtk-4.0/`: WhiteSur libadwaita CSS (dark), so GTK4/libadwaita apps (Files, Console) pick it up.
- Hook `0500-moor.hook.chroot`:
  - os-release branding (`/usr/lib/os-release` ← `NAME="Moor Linux"`, `ID=moor`, `ID_LIKE=debian`), `/etc/issue`.
  - Purges gnome-software and PackageKit (packagekit, gstreamer1.0-packagekit, gnome-software-plugin-deb). The gnome-core metapackage goes with them. Everything is `apt-mark manual`'d first so nothing gets autoremoved.
  - Masks the Tracker/LocalSearch user units (tracker-miner-fs isn't even installed with no-recommends).
  - Removes the Evolution alarm-notify autostart.
  - Enables nginx and zramswap, sets `graphical.target`, runs `glib-compile-schemas` and `dconf update`, and updates the icon caches.
- Hook `0600-moor-bootmenu.hook.binary` (runs on the binary tree):
  - Renames the GRUB and ISOLINUX entries to "Moor Linux Live", "Moor Linux Live (serial console)" (adds `console=ttyS0,115200 console=tty0` for debugging and CI), and "Moor Linux Live (fail-safe mode)". Menu title "Moor Linux".
  - Sets timeouts: GRUB 5 s, ISOLINUX 5 s.
- `includes.binary/{isolinux,boot/grub}/splash.png`: a plain "Moor Linux" text splash. No Debian logo.

### Evolution data server
It **stays**. gnome-core and gnome-shell depend on evolution-data-server, and gnome-shell's clock/calendar menu D-Bus-activates `evolution-source-registry`, `evolution-calendar-factory`, and `evolution-addressbook-factory` (seen running in the test, about 60 MB RSS for the source registry). Only the alarm-notify autostart was removed. Masking the factories would risk calendar-menu errors, and I didn't test that.

## WhiteSur (vinceliuice), installed system-wide at build time
| Repo | Commit | Date | License | Installed as |
|---|---|---|---|---|
| WhiteSur-gtk-theme | `d5782652d412137e26fb8ff55b55a5572e4c6995` | 2026-09-11 | MIT | `/usr/share/themes/WhiteSur-{Dark,Light}-solid[-hdpi,-xhdpi]` (GTK2/3/4 + gnome-shell), `/etc/skel/.config/gtk-4.0` |
| WhiteSur-icon-theme | `73d8040da51a9ed74e47c7366e7e9ff437601a5c` | 2026-09-10 | GPL-3.0 | `/usr/share/icons/WhiteSur{,-dark,-light}` |
| WhiteSur-cursors | `e190baf618ed95ee217d2fd45589bd309b37672b` | 2025-04-05 | GPL-3.0 | `/usr/share/icons/WhiteSur-cursors` (prebuilt `dist/`) |

Licenses verified from each repo's top-level file at the pinned commit: gtk `COPYING` (MIT, "Copyright (c) 2021 WhiteSur Developers"), icons `COPYING` (GNU GPL v3), cursors `LICENSE` (GNU GPL v3). In CI all three are fetched by `scripts/stage-themes.sh`; nothing is vendored.

- GTK install command: `./install.sh -d <stage>/usr/share/themes -c dark -c light -o solid -l` with a fake `$HOME`. `-o solid` means no transparency variant, and it builds for GNOME 48, the script's default when gnome-shell isn't on the host.
- Icon install command: `./install.sh -d <stage>/usr/share/icons`.
- License files are shipped in `/usr/share/doc/whitesur-themes/`. Moor's MIT LICENSE is at `/usr/share/doc/moor-linux/copyright`, and `/usr/share/doc/moor-linux/README` has the "based on Debian" line and the trademark disclaimer:
  > Moor Linux is based on Debian 13 ("trixie") packages. Moor Linux is not affiliated with Debian. Debian is a registered trademark owned by Software in the Public Interest, Inc.

## Build 1 QEMU results
Headless QEMU, **TCG** (no KVM), 4 GB RAM, q35 + virtio-vga, 1280×800:
| Firmware | Desktop visible |
|---|---|
| UEFI (OVMF) | ~158 s |
| BIOS (SeaBIOS) | ~162 s |

- Idle RAM after login: **1077 MB used** (`free -m`).
- Moor (Chromium app window), Files, and Terminal (Console) all opened from the dock.
- KVM crashed on vCPU creation on the original build host (`kvm_arch_vcpu_create` oops, broken nested virtualization), so all tests used TCG. Timings on real hardware or with KVM will be much shorter.
- A first test showed the Chromium "create keyring password" dialog in the autologin session; `--password-store=basic` was added for v2 (see below).

## Known issues (build 1)
- React hydration error #418 against the prerendered SPA shell; the page recovers by client-rendering.
- Stale "Debian" wording in parts of the Moor app.
- PWA manifest (`/__grok/manifest.webmanifest`) → 404 (Nitro route not present in the static build).
- The Moor page is slightly left-shifted.
- CUPS listening on loopback.
- evolution-data-server running (needed by gnome-shell's calendar; see below).
- Guest clock shows UTC (no timezone configured).

## v2 fixes: queued, not yet verified in a booted image
| Fix | Where | Status |
|---|---|---|
| Chromium `--password-store=basic` | `includes.chroot/etc/chromium.d/moor-flags`, `usr/local/bin/moor-launch` | in config, unverified |
| Skip the overview at login | Dash to Dock `disable-overview-on-startup=true` in `etc/dconf/db/local.d/00-moor` | in config, unverified |
| `username=moor` on the fail-safe boot entry | `hooks/live/0600-moor-bootmenu.hook.binary` | in config, unverified |
| zstd initramfs | `includes.chroot/etc/initramfs-tools/conf.d/compress` (`COMPRESS=zstd`) | added, unverified until first CI build |
| deb.debian.org binary mirror | `auto-config.sh` | in config |
| apt retries 10 | `config/apt/apt.conf`, `--apt-options` | in config |

## Move to GitHub Actions
The v2 local builds were OOM-killed during the chroot package install on the shared host (and destabilized it). The build now runs in `.github/workflows/build-iso.yml`: `ubuntu-latest`, privileged `debian:trixie` container, `timeout-minutes: 120`. The ~14 GB free disk on a standard runner is enough for the ~1.4 GB ISO (build 1 needed well under that); host disk-cleanup actions can't run inside the container. Artifacts: ISO, `.sha256`, `build-info.txt`, and the live-build log, kept 14 days. No release or publish steps. `boot-test.yml` boots the artifact in QEMU (BIOS and UEFI, KVM if `/dev/kvm` is usable, otherwise TCG) for 240 s each and uploads screenshots at 60/120/180/240 s.

## Hardware target 1: primary test PC (Ryzen 7 PRO 6850H + Radeon 680M, Intel Arc B580)
**Checked for this build:**
- `firmware-intel-graphics` (trixie 20250410-2) ships `xe/bmg_guc_70.bin`, `xe/bmg_huc.bin`, and `i915/bmg_dmc.bin`, per the trixie non-free-firmware `Contents-all`.
- `firmware-amd-graphics` ships the Rembrandt/680M blobs (`amdgpu/yellow_carp_*`, `psp_13_0_5`, `dcn_3_1_6`, `gc_10_3_6`, ...).
- Both packages, plus firmware-misc-nonfree, -realtek, -iwlwifi, amd64-microcode, and intel-microcode, are in the image.
- The full Mesa 25.0.7 stack is included: radeonsi/RADV for the 680M and iris/ANV for Arc, via libgl1-mesa-dri, mesa-vulkan-drivers, and mesa-va-drivers.

**Versions (deb.debian.org Packages indices, 2026-09-26):**
| | trixie (this ISO) | trixie-backports |
|---|---|---|
| linux-image-amd64 | 6.12.107-1 | 7.1.8-1~bpo13+1 |
| Mesa (libgl1-mesa-dri, mesa-vulkan-drivers) | 25.0.7-2+deb13u1 | 26.1.6-1~bpo13+1 |
| firmware-intel-graphics / -amd-graphics / -misc-nonfree | 20250410-2 | 20260810-1~bpo13+1 |

**Assessment:**
- **6.12 is the minimum, not the sweet spot.** Linux 6.12 is the first kernel where Xe2/Battlemage is enabled by default without `xe.force_probe`. 6.11 had the IDs behind force_probe. Sources: Phoronix, "Intel Arc B580 Graphics Open-Source Driver Linux Gaming Performance Review" (https://www.phoronix.com/review/intel-arc-b580-graphics-linux); Intel's Xe KMD supported-GPU table, which lists E20B B580 as initial 6.11, full 6.12 (https://dgpu-docs.intel.com/overview/supported-hardware/xe-driver-gpus.html).
- Later kernels fixed or added things that matter on a desktop B580:
  - 6.15: GPU and VRAM temperatures via hwmon, survivability mode (https://www.phoronix.com/news/Intel-Xe-Linux-6.15-First).
  - 6.16: fan-speed reporting, PCIe link-downgrade controls. BMG D3cold was disabled by default because of D3cold→D0 instability (https://www.phoronix.com/news/Intel-Xe-Linux-6.16-Fan-Speeds, https://www.phoronix.com/news/Linux-6.16-Graphics-Drivers).
  - 6.17: Battlemage SR-IOV and fan-control/VR sysfs (Phoronix Battlemage tag page, https://www.phoronix.com/search/Battlemage).
  - Debian's 6.12 LTS gets stable fixes, but those features aren't backported **[whether the D3cold-disable fix reached 6.12.y is UNVERIFIED]**.
- **Mesa:** Battlemage was enabled by default in Mesa 24.3, with 24.2 backports (https://www.phoronix.com/news/Intel-Battlemage-Mesa-Default). Mesa 25.0 added Xe2 tuning (RR_STRICT, re-enabled the iris BO cache for Xe2; https://www.phoronix.com/news/Intel-RR-Strict-Xe2-Optimize, https://docs.mesa3d.org/relnotes/25.0.0.html). 25.0.x point releases fixed Xe2 MOCS and blit issues (mesa 25.0.5 announce). So trixie's 25.0.7 is "fine for a desktop". Backports' 26.1 brings about 18 months of ANV/iris performance work, mostly relevant for gaming and Vulkan **[magnitude UNVERIFIED]**.
- **680M (Rembrandt)** has been well supported since about 5.17 and Mesa 22. Trixie's stock stack is fine for it.

**Recommendation:**
- Keep stock trixie for Phase 1, as built. It should drive both GPUs for a GNOME desktop.
- For Phase 2 hardware testing on this PC, boot the ISO and check `dmesg | grep -iE 'xe|amdgpu'`, which GPU GNOME renders on (`glxinfo -B` or Settings → About), and multi-monitor/HDMI/DP on the B580.
- If you see any B580 instability (hangs on runtime-PM resume, missing sensors, display glitches), make a **backports variant**: `--backports true` plus apt pinning of `linux-image-amd64`, `firmware-intel-graphics`, `firmware-misc-nonfree`, and the Mesa packages from trixie-backports. That tracks kernel 7.1 and Mesa 26.1 and is still Debian-signed, so the Secure Boot chain keeps working.
- Reason not to default to it now: backports kernels move fast and get less testing, and a newer kernel isn't needed to reach a working desktop.
- A dual-GPU caveat: with both the 680M and the B580 active, mutter picks the GPU driving the primary display as the render device. If the monitor is on the B580, GNOME renders there.

## Fallback design (NOT built): Xfce variant with the same WhiteSur look
- **Packages:** `xfce4 xfce4-terminal thunar xfce4-whiskermenu-plugin xfce4-pulseaudio-plugin network-manager-gnome lightdm lightdm-gtk-greeter plank xserver-xorg-core xserver-xorg-input-libinput` + the same Mesa, firmware, chromium, and nginx sets. This is X11: Xfce 4.20 on Wayland is still experimental.
- **Dock:** Plank (`plank`, WhiteSur ships a `plank` theme in `themes/WhiteSur-Dark-solid/plank`). You can also use a second `xfce4-panel` at the bottom with the docklike plugin (`xfce4-docklike-plugin`) for a more integrated look. The top xfce4-panel stays as the menu bar.
- **Theme:** the same system-wide WhiteSur install. `/etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml` sets Net/ThemeName=WhiteSur-Dark-solid, Net/IconThemeName=WhiteSur-dark, and Gtk/CursorThemeName=WhiteSur-cursors. Put xfwm4 `theme=WhiteSur-Dark-solid` in `xfwm4.xml` (WhiteSur ships an `xfwm4` dir) and the Plank autostart in `/etc/xdg/autostart/plank.desktop`.
- **Autologin:** `/etc/lightdm/lightdm.conf.d/50-moor.conf` with `[Seat:*] autologin-user=moor autologin-session=xfce`. live-config's lightdm component also handles this.
- **Expected RAM savings (estimate, unmeasured):** Xfce + Plank on X11 idles around 450–650 MB used, compared with the ~1.0–1.1 GB measured here for GNOME. That's roughly 400–600 MB saved. It also avoids evolution-data-server, gnome-shell's JS heap, and llvmpipe compositing cost on weak GPUs, which is the bigger win for the Presario. The trade-off: X11, less polish, and no Wayland-native Chromium.
