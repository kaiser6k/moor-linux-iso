# Nested VM on a Linux VPS

Moor Linux is a live ISO. InterServer does not boot a custom ISO on the VPS
itself, so the ISO runs as a QEMU/KVM guest inside the VPS. Nothing here
listens on a public address. The display stays on `127.0.0.1` and you reach it
through an SSH tunnel.

There is no installer. The guest boots the ISO. A disk is optional and is only
for a live-boot persistence overlay.

The profile to run on the VPS is **Moor Linux Lite (Xfce)**. The idle-RAM
table below is why. `scripts/vps-setup-nested-vm.sh` boots that entry and
nothing else.

## Morning steps

Run these on the VPS, in this order. The script accepts only Ubuntu or Debian,
which means `/etc/os-release` `ID=ubuntu` or `ID=debian`. It refuses everything
else, including derivatives whose `ID_LIKE` mentions debian or ubuntu (Linux
Mint, Pop!_OS, and the like).

### 1. Pre-checks: memory, then nested KVM

```sh
awk '/^MemTotal:/ { printf "%d MiB\n", int($2 / 1024) }' /proc/meminfo
grep -E '(vmx|svm)' /proc/cpuinfo | head
grep -E -c '(vmx|svm)' /proc/cpuinfo
ls -l /dev/kvm
```

Write down the MiB number. The script reads that same `MemTotal`, subtracts
1536 MiB, rounds down to a multiple of 256 MiB, and caps the result at 4096
MiB. It refuses an automatic size below 1024 MiB unless you pass `--ram`.

Worked outputs of that formula. The guest is `(MemTotal - 1536)` rounded
down to a multiple of 256, then capped at 4096. Each band below is 256 MiB
wide. 2048 MiB is only the 3584–3839 band. Lower `MemTotal` keeps stepping
down by 256 MiB until the 1024 MiB floor. Below 2560 MiB the script refuses
to pick a size unless you pass `--ram`.

| Host `MemTotal` | Guest the script chooses |
|---|---|
| below 2560 MiB | refuses (under the 1024 MiB floor) |
| 2560–2815 MiB | 1024 MiB |
| 2816–3071 MiB | 1280 MiB |
| 3072–3327 MiB | 1536 MiB |
| 3328–3583 MiB | 1792 MiB |
| 3584–3839 MiB | 2048 MiB |
| 3840–4095 MiB | 2304 MiB |
| 3891 MiB (a 4 GB VPS) | 2304 MiB |
| 4096–4351 MiB | 2560 MiB |
| 4352–4607 MiB | 2816 MiB |
| 4608–4863 MiB | 3072 MiB |
| 4864–5119 MiB | 3328 MiB |
| 5120–5375 MiB | 3584 MiB |
| 5376–5631 MiB | 3840 MiB |
| 5632 MiB and above, including about 10 GB (10240 MiB) | 4096 MiB, the cap |

A count of zero, or no writable `/dev/kvm`, means the script uses TCG and
prints a warning that the desktop will be slow.

### 2. Get the ISO and the script

The ISO is about 1.4 GB (`moor-linux-amd64.hybrid.iso`, 1403781120 bytes).
There is one artifact to download, id `10946214543`, from
[Build ISO run 36363113863](https://github.com/kaiser6k/moor-linux-iso/actions/runs/36363113863):

[moor-linux-iso artifact 10946214543](https://github.com/kaiser6k/moor-linux-iso/actions/runs/36363113863/artifacts/10946214543)

That page needs a GitHub login. The browser saves `moor-linux-iso.zip`.
Inside are `moor-linux-amd64.hybrid.iso`, `moor-linux-amd64.hybrid.iso.sha256`,
and `build-info.txt`.

The SHA256 of `moor-linux-amd64.hybrid.iso` is:

```text
2b0e0b05fff1e145a737f47b02e19004ba40aa574a73bce79b9a9fa9ed8538e6
```

On Windows, in PowerShell, from the folder where the zip was saved. This
checks the hash before the copy. Replace `USER` and `YOUR_VPS`. The ISO lands
on the VPS as `~/moor-linux-amd64.hybrid.iso`.

```powershell
Expand-Archive -Path .\moor-linux-iso.zip -DestinationPath .\moor-iso
$hash = (Get-FileHash -Algorithm SHA256 .\moor-iso\moor-linux-amd64.hybrid.iso).Hash.ToLower()
if ($hash -ne '2b0e0b05fff1e145a737f47b02e19004ba40aa574a73bce79b9a9fa9ed8538e6') { throw "SHA256 mismatch: $hash" }
$hash
scp .\moor-iso\moor-linux-amd64.hybrid.iso USER@YOUR_VPS:~/
```

On a machine with `unzip` instead of PowerShell:

```sh
unzip moor-linux-iso.zip -d moor-iso
cd moor-iso
sha256sum -c moor-linux-amd64.hybrid.iso.sha256
scp moor-linux-amd64.hybrid.iso USER@YOUR_VPS:~/
```

From a machine that already has `gh` logged in, `gh` unpacks the zip itself:

```sh
gh run download 36363113863 --repo kaiser6k/moor-linux-iso --name moor-linux-iso --dir moor-iso
cd moor-iso
sha256sum -c moor-linux-amd64.hybrid.iso.sha256
scp moor-linux-amd64.hybrid.iso USER@YOUR_VPS:~/
```

On the VPS, from your home directory, fetch the setup script from branch
`cursor/vps-nested-vm-ram-6ee6`. The later steps call this file as
`./vps-setup-nested-vm.sh`, and the ISO as `~/moor-linux-amd64.hybrid.iso`.

```sh
cd
curl -fL -o vps-setup-nested-vm.sh \
  https://raw.githubusercontent.com/kaiser6k/moor-linux-iso/cursor/vps-nested-vm-ram-6ee6/scripts/vps-setup-nested-vm.sh
chmod +x vps-setup-nested-vm.sh
```

On Windows, install Virt Viewer so you have `remote-viewer`. In PowerShell:

```powershell
winget install --id RedHat.VirtViewer -e
```

The MSI builds are also listed at https://releases.pagure.org/virt-viewer/ .
After it installs, open Remote Viewer from the Start menu, or run
`remote-viewer` if that directory is on your PATH.

### 3. Dry run, then create the VM

```sh
cd
sudo ./vps-setup-nested-vm.sh --dry-run \
  --sha256 2b0e0b05fff1e145a737f47b02e19004ba40aa574a73bce79b9a9fa9ed8538e6 \
  ~/moor-linux-amd64.hybrid.iso
```

```sh
cd
sudo ./vps-setup-nested-vm.sh \
  --sha256 2b0e0b05fff1e145a737f47b02e19004ba40aa574a73bce79b9a9fa9ed8538e6 \
  ~/moor-linux-amd64.hybrid.iso
```

Read any `WARNING` lines. The script installs QEMU and libvirt if they are
missing, sizes the guest from the host's `MemTotal`, and prints:

```text
ssh -L 5900:127.0.0.1:5900 USER@YOUR_VPS
```

Overrides, if you need them: `--ram` MiB, `--disk` GB, `--vcpus` N, or the
environment variables `MOOR_RAM_MB`, `MOOR_DISK_GB`, and `MOOR_VCPUS`.
`--accel tcg` forces software emulation. The default disk is a 16 GB sparse
qcow2. It is attached and not formatted as a persistence volume, so the live
overlay stays in guest RAM, which is how the idle samples were taken.

Sizing is the table in the pre-checks. A 4 GB VPS whose `MemTotal` is 3891
MiB gets a 2304 MiB guest. 2048 MiB is only when `MemTotal` is 3584–3839.
Below that the guest drops in 256 MiB steps down to the 1024 MiB floor.
A host at 5632 MiB or more, including one near 10 GB, hits the 4096 MiB cap.
The script does not turn on host swap and does not install host zram. Guest zram is already
in the image (`PERCENT=50`). Do not count on the host swapping the QEMU
process.

Installing `libvirt-daemon-system` enables libvirt's `default` NAT network.
That network starts `dnsmasq` and adds firewall rules for the `virbr0`
bridge. Those rules do not publish the guest or the console on the VPS
public address. The VM this script creates does not use that bridge. Its NIC
is libvirt `user` networking (SLIRP). SPICE is started with no password.
That is safe only because the listen address is `127.0.0.1` and you reach it
through the SSH tunnel. Do not change the listen address and do not open
port 5900.

### 4. Open the console from Windows

In PowerShell or Command Prompt:

```text
ssh -L 5900:127.0.0.1:5900 USER@YOUR_VPS
```

Replace `USER` and `YOUR_VPS` with the SSH login you already use. Then:

```text
remote-viewer spice://127.0.0.1:5900
```

The script binds SPICE to `127.0.0.1` only. It does not change `sshd` or the
firewall, and it does not listen on a public address. Do not open port 5900.

### 5. Remove the VM

```sh
cd
sudo ./vps-setup-nested-vm.sh --destroy
```

That deletes the libvirt domain and the disk, kernel, initrd, and ISO copy
recorded under `/var/lib/moor-nested-vm` and
`/var/lib/libvirt/images/moor-nested`. It does not remove an ISO you passed by
path, and it does not touch other domains.

## Nested KVM on the VPS

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

## Software emulation when `/dev/kvm` is missing

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

## Boot the ISO by hand

Virtio devices are in the Debian kernel this ISO already ships (`virtio-net`,
`virtio-blk`, `virtio-gpu` / `virtio-vga`, `virtio-scsi`). No extra driver ISO.

Store the ISO on the VPS (about 1.4 GB). Suggested persistence disk, if you want
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

This CD boot stops at the menu. The default entry is GNOME-lean. Choose
**Moor Linux Lite (Xfce)** before the timeout. On the isolinux menu that entry
is after Live, serial, and fail-safe: press Down three times, then Enter.
The setup script does not use this menu. It boots that same Xfce command line
directly.

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

`--network network=default` attaches the guest to libvirt's NAT network. That
network auto-starts `dnsmasq` and host firewall rules. Nothing in those rules
listens on the VPS public address; the guest is still behind NAT. The setup
script uses `--network user` instead, which is SLIRP and does not add the
guest to `virbr0`.

This CD boot also stops at the menu. Choose **Moor Linux Lite (Xfce)** (Down
three times, then Enter on the isolinux menu) or the default GNOME-lean
session starts. SPICE here has no password. Leave the listen address at
`127.0.0.1` and use the SSH tunnel.

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

## Reach the desktop

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

## Firewall

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

`ufw allow OpenSSH` opens port 22 only. If `sshd` is listening on another
port, allow that port before `ufw enable` (for example `ufw allow 2222/tcp`).
Otherwise enabling the firewall drops the SSH session you are using.
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

The numbers in this table are copied from
[Build ISO run 36363113863](https://github.com/kaiser6k/moor-linux-iso/actions/runs/36363113863)
on head `0cc4b80819cc3a3d640adc104bb96071a492bae2`. They are not estimates.
Each profile finished 3/3 boots under KVM. `free -m` reported 3921 MiB total
on every run. The baseline probe printed `smem not installed` on all three
runs, so that smem cell is n/a. No measurement job failed.

| Profile | used MiB (median) | available MiB (median) | smem PSS MiB (median) | ps_mem MiB (median) | proc PSS MiB (median) | Artifact |
|---|---:|---:|---:|---:|---:|---|
| baseline (main ISO, GNOME) | 783 | 3138 | n/a | 608.1 | 608.1 | [idle-ram-baseline](https://github.com/kaiser6k/moor-linux-iso/actions/runs/36363113863/artifacts/10946736528) |
| GNOME-lean | 740 | 3180 | 564.5 | 546.5 | 546.5 | [idle-ram-gnome-lean](https://github.com/kaiser6k/moor-linux-iso/actions/runs/36363113863/artifacts/10946483866) |
| Xfce-lite | 581 | 3339 | 381.3 | 365.2 | 365.3 | [idle-ram-xfce-lite](https://github.com/kaiser6k/moor-linux-iso/actions/runs/36363113863/artifacts/10946489018) |

`used` / `available` by run, in MiB: baseline 783/3138, 785/3135, 780/3140;
GNOME-lean 740/3180, 747/3173, 740/3181; Xfce-lite 573/3347, 595/3325, 581/3339.

### Recommended profile

Use **Moor Linux Lite (Xfce)** for the one nested guest.

Median `used` is 581 MiB on Xfce-lite, 740 MiB on GNOME-lean, and 783 MiB on
the baseline image. Median proc PSS is 365.3 MiB, 546.5 MiB, and 608.1 MiB.
Xfce-lite is the smallest idle set of the three. GNOME-lean is 43 MiB of
`used` under the baseline (783 to 740). That gap does not change how a 4 GB
host is split. The Xfce gap does: 202 MiB less `used` than the baseline, and
159 MiB less than GNOME-lean.

The samples above booted at 4096 MB only. No run in that table booted at
3072 MB or at 1024 MB, and Chromium was not in the sample. Those 581 MiB do
not show that a 1024 MiB guest can hold the desktop. A guest smaller than the
measured `used` value cannot hold that idle set.

On a 4 GB VPS the script does not ask for 4096 MB. The formula in the morning
steps gives 2304 MiB when `MemTotal` is 3891. 2048 MiB is only when
`MemTotal` is 3584–3839; lower values step down by 256 MiB to the 1024 MiB
floor. On a host at 5632 MiB or more the cap is one 4096 MiB guest. Do not
start a second VM.

### Script-path idle RAM at 2304 and 2048

These boots use `scripts/vps-setup-nested-vm.sh` (SPICE on `127.0.0.1`,
virtio disk and video, the Xfce kernel command line). Each size is one
sample, not a median of three. The probe waits until `xfce4-session` is
running, idles 120 seconds, then reads `free -m` used.

<!-- VPS-SIZE-IDLE-START -->
Copied from script-test on
[Build ISO run 36385649159](https://github.com/kaiser6k/moor-linux-iso/actions/runs/36385649159)
(head `47841b6e846129fc4353b95980f4e25e29246d7c`). The runner had KVM, so
both boots used `--accel kvm`. One sample each.

| Guest RAM | accel | `free -m` used | samples |
|---|---|---:|---:|
| 2304 MiB | kvm | 515 | 1 |
| 2048 MiB | kvm | 496 | 1 |

Both reached `xfce4-session` before the idle wait. The same job's TCG boot
(`qemu64`, SPICE, virtio) also reached `xfce4-session`. These are not the
4096 MiB medians above, and they are not a median of three.
<!-- VPS-SIZE-IDLE-END -->

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
