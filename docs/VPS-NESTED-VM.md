# Nested VM on a Linux VPS

Moor Linux is a live ISO. InterServer does not boot a custom ISO on the VPS
itself, so the ISO runs as a QEMU/KVM guest inside the VPS. Nothing here
listens on a public address. The display stays on `127.0.0.1` and you reach it
through an SSH tunnel.

There is no installer. The guest boots the ISO. A disk is optional and is only
for a live-boot persistence overlay.

## 1. See if the VPS can use KVM

Run these on the VPS, not inside the guest:

```sh
ls -l /dev/kvm
grep -E -c '(vmx|svm)' /proc/cpuinfo
```

`/dev/kvm` must exist and your user must be able to open it (root, or a user in
the `kvm` group). A non-zero `vmx`/`svm` count means the CPU flag is visible in
the VPS.

You usually cannot read the host's nested-virt switch from inside the VPS
(`/sys/module/kvm_intel/parameters/nested` or `kvm_amd` is a host file). If
`/dev/kvm` is missing, nested virtualization is off. Ask InterServer to enable
it, or use the TCG fallback below.

```sh
# Only meaningful on the physical host, not inside the VPS.
cat /sys/module/kvm_intel/parameters/nested 2>/dev/null || true
cat /sys/module/kvm_amd/parameters/nested 2>/dev/null || true
```

## 2. Software emulation when `/dev/kvm` is missing

QEMU's TCG accelerator needs no `/dev/kvm`. It is the fallback, and it is slow.
`BUILD-NOTES.md` recorded about 160 seconds to reach a desktop under TCG on the
original build host. The same style of boot under KVM on a GitHub-hosted runner
reached a desktop in well under a minute (see the boot-test logs). Interactive
use under TCG is sluggish: the whole guest CPU is emulated, so the desktop is
a poor daily driver and a reasonable way to confirm that the ISO boots.

```sh
qemu-system-x86_64 \
  -machine q35,accel=tcg \
  -cpu max \
  -m 4096 -smp 2 \
  ...
```

Add `-accel tcg,thread=multi` if you want QEMU to use more than one host thread
for translation. Do not point this at a public port; the display section below
still applies.

## 3. Boot the ISO

Virtio devices are in the Debian kernel this ISO already ships (`virtio-net`,
`virtio-blk`, `virtio-gpu` / `virtio-vga`, `virtio-scsi`). No extra driver ISO.

Store the ISO on the VPS (about 2 GB). Suggested persistence disk, if you want
one, is 16 GB. The idle-RAM numbers below were taken with no disk and no
balloon device, 4096 MB, BIOS, virtio-vga, user-mode networking.

```sh
qemu-img create -f qcow2 moor-persist.qcow2 16G
```

Persistence is optional. To use it, partition that disk, `mkfs.ext4 -L persistence`,
and put `persistence.conf` on it containing the line `/ union`. Then add
`persistence` to the kernel command line (a copied GRUB entry is the
straightforward way). Without that label, the disk is unused and the live
system keeps its overlay in RAM, which is how the idle samples were taken.

KVM boot, display bound to localhost (pick SPICE or VNC, not both, unless you
mean to):

```sh
qemu-system-x86_64 \
  -name moor \
  -machine q35,accel=kvm \
  -cpu host \
  -m 4096 -smp 2 \
  -device virtio-vga \
  -display none \
  -spice addr=127.0.0.1,port=5900,disable-ticketing=on \
  -device virtio-serial-pci \
  -chardev spicevmc,id=vdagent,name=vdagent \
  -device virtserialport,chardev=vdagent,name=com.redhat.spice.0 \
  -usb -device usb-tablet \
  -nic user,model=virtio-net-pci \
  -drive if=none,id=persist,file=moor-persist.qcow2,format=qcow2 \
  -device virtio-blk-pci,drive=persist \
  -cdrom moor-linux-amd64.hybrid.iso \
  -boot order=d
```

VNC instead of SPICE:

```sh
  -vnc 127.0.0.1:0,to=0
```

`127.0.0.1:0` is port 5900 on localhost only. Do not use `-vnc :0` (that is
`0.0.0.0`) and do not set the SPICE address to `0.0.0.0`.

`virt-install` equivalent (libvirt keeps the same virtio devices):

```sh
virt-install \
  --name moor \
  --memory 4096 \
  --vcpus 2 \
  --cpu host \
  --cdrom /var/lib/libvirt/images/moor-linux-amd64.hybrid.iso \
  --disk path=/var/lib/libvirt/images/moor-persist.qcow2,size=16,bus=virtio \
  --network network=default,model=virtio \
  --graphics spice,listen=127.0.0.1 \
  --video virtio \
  --input tablet,bus=usb \
  --boot cdrom \
  --os-variant debian13 \
  --noautoconsole
```

If `osinfo-query os` has no `debian13`, use the newest `debian` variant it lists.
Confirm the SPICE or VNC listen address is `127.0.0.1` (`virsh dumpxml moor`).

The boot menu:

| Entry | Session |
|---|---|
| Moor Linux Live | GNOME-lean (default) |
| Moor Linux Lite (Xfce) | Xfce on X11, WhiteSur, Plank dock |

Live user `moor`, password `live`. Both entries autologin. GNOME keeps Dash to
Dock and the WhiteSur theme. Xfce uses the Xfce top panel, a WhiteSur Plank
dock at the bottom, the WhiteSur GTK/icon/cursor/xfwm theme, and the fjord
wallpaper. The compositor is off on Xfce.

Why a menu entry rather than a second ISO: this repo's live-build produces one
hybrid image. A second flavor would build and upload another full ISO. The
cmdline flag `moor.session=xfce` is enough; `moor-display-manager` starts
LightDM for that entry and GDM otherwise.

## 4. Reach the desktop

On your laptop, tunnel to the VPS. The guest port is only on the VPS loopback.

SPICE (port 5900 in the example):

```sh
ssh -L 5900:127.0.0.1:5900 user@your-vps
remote-viewer spice://127.0.0.1:5900
```

VNC:

```sh
ssh -L 5900:127.0.0.1:5900 user@your-vps
vncviewer 127.0.0.1:5900
```

RDP is not installed (an RDP server would sit in the idle set). If you want it:

```sh
# inside the guest, then keep it on localhost
sudo apt-get install xrdp
sudo sed -i 's/^port=3389/port=127.0.0.1:3389/' /etc/xrdp/xrdp.ini
sudo systemctl restart xrdp
```

And from the laptop, `ssh -L 3389:127.0.0.1:3389 user@your-vps`, then connect
to `127.0.0.1`. Do not set xrdp to `0.0.0.0`.

`spice-vdagent` and `qemu-guest-agent` are in the image so SPICE can resize
the desktop and a host can talk to the guest agent. The idle samples below
were taken without a SPICE client attached, so they do not include an active
SPICE session.

## 5. Firewall

Binding to `127.0.0.1` is the actual control. A firewall is the backstop for
the day someone changes that bind. Allow SSH. Do not allow 5900, 5901, or 3389
from the network.

nftables sketch (adjust the chain name to the one your VPS already uses):

```sh
nft add rule inet filter input iifname != lo tcp dport { 5900, 5901, 3389 } drop
```

ufw:

```sh
ufw default deny incoming
ufw allow OpenSSH
ufw enable
```

Do not `ufw allow 5900` or `ufw allow 3389`.

## GNOME-lean: what was turned off

The default session is still GNOME on Wayland with Dash to Dock and WhiteSur.
Animations are off (`org.gnome.desktop.interface enable-animations=false`).
Networking (NetworkManager and wpa_supplicant), GDM login, the display, nginx,
and zram stay. There is no installer in this image.

| Unit or autostart | Why a VM does not need it |
|---|---|
| tracker-miner / localsearch user units | File indexing on a live squashfs wastes RAM and I/O. Already masked; crawling is off in dconf. |
| gnome-software, packagekit | Removed. No package-manager UI on the idle desktop. |
| evolution-source-registry, evolution-calendar-factory, evolution-addressbook-factory, evolution-alarm-notify | D-Bus factories. gnome-shell depends on the libraries, so the packages stay; the processes do not. The calendar menu can be empty. |
| goa-daemon, goa-identity-service | Online accounts. None are configured. |
| cups.service, cups.socket, cups-browsed | No printers. CUPS was a known loopback listener. |
| bluetooth.service | No Bluetooth controller. |
| geoclue.service | No useful location. `org.gnome.system.location enabled=false` as well. |
| ModemManager.service | No modem. |
| fwupd.service and fwupd-refresh.timer | Host firmware updates do not apply to a VM. |
| power-profiles-daemon.service | No laptop power profiles. |
| switcheroo-control.service | No dual-GPU switching. virtio-vga is one device. |
| bolt.service | No Thunderbolt. |
| avahi-daemon.service | No mDNS. The guest is reached by the tunnel, not by discovery. |
| iio-sensor-proxy.service | No accelerometer or light sensor. |
| pcscd.service | No smartcard reader. |
| thermald.service | No thermal zones to manage. |
| colord.service | No color-managed devices. |
| apt-daily.timer, apt-daily-upgrade.timer | Unattended apt during a live session. Manual apt still works. |
| GNOME settings daemons for print notifications, color, housekeeping, rfkill, sharing, smartcard, USB protection, Wacom, WWAN | Same hardware gaps. XSettings, keyboard, media keys, power, and sound are still running so the theme and session work. |
| Autostart files for Evolution alarms, Software, Tracker, Blueman, Geoclue demo, print applet, Deja Dup, update-notifier | `Hidden=true`, same reasons. |

Kept on purpose: NetworkManager, wpa_supplicant, accounts-daemon (GDM), udisks2,
upower, rtkit, polkit, pipewire, nginx, zram, spice-vdagent, qemu-guest-agent.
Plank's autostart is `OnlyShowIn=XFCE` so it does not also run under GNOME.

## Measured idle RAM

Method, in GitHub Actions (`baseline-ram` and `idle-ram` in
`.github/workflows/build-iso.yml`):

- BIOS QEMU, 4096 MB,  up to 4 vCPUs, virtio-vga, KVM when `/dev/kvm` works.
- Autologin, then 120 seconds of idle, then a root shell on tty3 (no GUI terminal).
- `free -m` first, then `ps` top 15, then `smem -tk` if `smem` is installed, then `ps_mem` when Python is available, then a `/proc/*/smaps_rollup` PSS sum.
- Three runs. The median is the middle value. Raw `probe.txt` files are the `idle-ram-*` artifacts.

`smem` is not in the previous main image. When the probe prints `smem not installed`, the smem cell is "n/a" and the ps_mem total is the stand-in the job actually ran. The proc PSS column is the same walk on every profile. Do not treat a missing cell as zero.

The numbers in this table are copied from CI artifacts on Build ISO run
36360884431 (head `b77c1f016782f0be29f4f07b2ad11185cde7c296`). They are not
estimates. Each profile finished 3/3 boots under KVM. `free -m` reported
3921 MiB total on every run. The baseline probe printed `smem not installed`
on all three runs, so that smem cell is n/a. No measurement job failed.

| Profile | used MiB (median) | available MiB (median) | smem PSS MiB (median) | ps_mem MiB (median) | proc PSS MiB (median) | Artifact |
|---|---:|---:|---:|---:|---:|---|
| baseline (main ISO, GNOME) | 778 | 3142 | n/a | 606.4 | 606.4 | `idle-ram-baseline` (10945308092) |
| GNOME-lean | 739 | 3182 | 562.0 | 544.0 | 544.0 | `idle-ram-gnome-lean` (10946107161) |
| Xfce-lite | 585 | 3336 | 382.0 | 365.9 | 366.0 | `idle-ram-xfce-lite` (10946142062) |

`used` / `available` by run, in MiB: baseline 778/3142, 782/3138, 772/3148;
GNOME-lean 739/3182, 741/3180, 737/3183; Xfce-lite 584/3336, 585/3336, 585/3335.

### Recommended guest RAM

The VPS plan in view is 10 GB of host RAM and one guest. Give that guest
4096 MB (`-m 4096` in the command above). That is the only size this method
boots. It leaves about 6 GB of the 10 GB plan for the host OS, `sshd`, and
QEMU's own overhead on top of the guest allocation (the 4096 MB sits inside
the QEMU process). Do not start a second VM on that host. If `free -m` on the
VPS, with this guest running, shows the host with almost nothing available,
the host is out of room; stop other host processes before raising the guest
above 4096 MB.

No run booted at 3072 MB. The idle samples above are the 4096 MB boots.
Median `used` is 778 MiB on the baseline image, 739 MiB on GNOME-lean, and
585 MiB on Xfce-lite. Each of those is below 3072, so a 3072 MB guest is
larger than the idle set that was measured, for every profile. A guest smaller
than the measured `used` value cannot hold that idle set. Chromium and a long
live session were not part of the sample. The one-guest plan stays at 4096 MB.

### Swap and zram in the guest

The image enables `zramswap` with `ALGO=zstd` and `PERCENT=50`
(`/etc/default/zramswap`). On every 4096 MB sample, `free -m` reported
1960 MiB of swap and 0 MiB used. A 3072 MB guest was not booted; at
`PERCENT=50` its zram device would be about 1.5 GB. Zram compresses anonymous pages
into RAM. It does not add memory the host did not give the guest, and it does
not make a 3 GB guest equal to a 4 GB one. It can absorb a short spike that
would otherwise be an OOM. Swapped pages still occupy host RAM, just smaller.

The idle samples use no persistence disk, so the live overlay is also in guest
RAM and competes with zram. There is no disk swap in those samples. On the
VPS, do not count on the host swapping the QEMU process; that stalls the whole
guest. Host swap is a backstop, not the sizing plan. The ~6 GB left after the
4 GB guest is what should keep the host out of swap.
