#!/bin/bash
# One nested Moor Linux VM on an Ubuntu or Debian VPS.
#
# Boots the recommended profile, Moor Linux Lite (Xfce), from the hybrid ISO.
# The console listens on 127.0.0.1 only. Nothing here opens a firewall port
# or edits sshd.
#
#   sudo ./scripts/vps-setup-nested-vm.sh --sha256 SHA256 /path/or/https://url.iso
#   sudo ./scripts/vps-setup-nested-vm.sh --destroy
#
# See docs/VPS-NESTED-VM.md for the morning steps and the idle-RAM numbers.
set -euo pipefail

STATE_DIR=/var/lib/moor-nested-vm
STATE_FILE=$STATE_DIR/state
IMAGE_DIR=/var/lib/libvirt/images/moor-nested
RESERVE_MIB=1536
MAX_GUEST_MIB=4096
MIN_GUEST_MIB=1024
DEFAULT_DISK_GB=16
DEFAULT_PORT=5900

DRY=0
DESTROY=0
RAM_MB=${MOOR_RAM_MB:-}
DISK_GB=${MOOR_DISK_GB:-$DEFAULT_DISK_GB}
VCPUS=${MOOR_VCPUS:-}
SHA=${MOOR_SHA256:-}
DISPLAY=${MOOR_DISPLAY:-spice}
NAME=${MOOR_NAME:-moor}
ACCEL=${MOOR_ACCEL:-auto}
PORT=${MOOR_PORT:-$DEFAULT_PORT}
SERIAL_LOG=
ISO=${MOOR_ISO:-}

usage() {
  cat <<'EOF'
Usage: vps-setup-nested-vm.sh [options] ISO

ISO is a local path or an http(s) URL. The guest is Moor Linux Lite (Xfce).

Options:
  --dry-run          Print the plan, including the ssh -L line. Change nothing.
  --destroy          Remove only the domain and files this script recorded.
  --ram MB           Guest RAM in MiB (env MOOR_RAM_MB).
  --disk GB          Persistence disk size in GB (env MOOR_DISK_GB). Default 16.
  --vcpus N          Guest vCPUs (env MOOR_VCPUS). Default 2, or 1 if the host has one CPU.
  --sha256 HEX       Require this SHA256 (env MOOR_SHA256).
  --display spice|vnc
                     Console type. Default spice. Always bound to 127.0.0.1.
  --port N           Console port. Default 5900.
  --accel auto|kvm|tcg
                     auto uses KVM when vmx/svm is visible and /dev/kvm is writable.
  --name NAME        Libvirt domain name. Default moor.
  --serial-log PATH  Also log the serial console to PATH (for tests).
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

warn_loud() {
  echo "WARNING: $*" >&2
}

require_debian() {
  if [[ ! -r /etc/os-release ]]; then
    die "This script supports Ubuntu and Debian only. /etc/os-release is missing."
  fi
  # Read ID in a subshell. Sourcing os-release here would set NAME=Ubuntu
  # and overwrite the libvirt domain name.
  local id like
  id=$(
    # shellcheck disable=SC1091
    . /etc/os-release
    printf '%s' "${ID:-}"
  )
  like=$(
    # shellcheck disable=SC1091
    . /etc/os-release
    printf '%s' "${ID_LIKE:-}"
  )
  case "$id" in
    ubuntu|debian) return 0 ;;
  esac
  case " $like " in
    *" debian "*|*" ubuntu "*) return 0 ;;
  esac
  die "This script supports Ubuntu and Debian only (found ID=${id:-unknown})."
}

require_root() {
  if [[ ${EUID} -ne 0 ]]; then
    die "Run this with sudo. Refusing to change the system as a normal user."
  fi
}

require_name() {
  if [[ ! $NAME =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
    die "Domain name must be letters, digits, dot, underscore, or hyphen."
  fi
}

require_number() {
  local label=$1
  local value=$2
  if [[ ! $value =~ ^[0-9]+$ ]] || [[ $value -lt 1 ]]; then
    die "$label must be a positive integer (got ${value})."
  fi
}

host_mem_mib() {
  awk '/^MemTotal:/ { print int($2 / 1024); exit }' /proc/meminfo
}

choose_guest_mib() {
  local host raw
  host=$(host_mem_mib)
  if [[ -z $host || $host -lt 1 ]]; then
    die "Could not read MemTotal from /proc/meminfo."
  fi
  if [[ -n $RAM_MB ]]; then
    require_number "RAM" "$RAM_MB"
    if [[ $RAM_MB -lt $MIN_GUEST_MIB ]]; then
      warn_loud "Guest RAM ${RAM_MB} MiB is below ${MIN_GUEST_MIB} MiB. The measured Xfce-lite idle set used 581 MiB."
    fi
    echo "$RAM_MB"
    return
  fi
  raw=$((host - RESERVE_MIB))
  if [[ $raw -lt 0 ]]; then
    raw=0
  fi
  raw=$((raw / 256 * 256))
  if [[ $raw -gt $MAX_GUEST_MIB ]]; then
    raw=$MAX_GUEST_MIB
  fi
  if [[ $raw -lt $MIN_GUEST_MIB ]]; then
    die "Host MemTotal is ${host} MiB. Leaving ${RESERVE_MIB} MiB for the host leaves ${raw} MiB, below the ${MIN_GUEST_MIB} MiB minimum. Free host RAM or pass --ram."
  fi
  echo "$raw"
}

choose_vcpus() {
  local n
  if [[ -n $VCPUS ]]; then
    require_number "vCPUs" "$VCPUS"
    echo "$VCPUS"
    return
  fi
  n=$(nproc)
  if [[ $n -lt 2 ]]; then
    echo 1
  else
    echo 2
  fi
}

kvm_usable() {
  if ! grep -E -q '(vmx|svm)' /proc/cpuinfo; then
    return 1
  fi
  if [[ ! -e /dev/kvm ]]; then
    return 1
  fi
  if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
    return 1
  fi
  return 0
}

choose_accel() {
  case "$ACCEL" in
    kvm)
      if ! kvm_usable; then
        die "KVM was requested, but vmx/svm is missing or /dev/kvm is not writable."
      fi
      echo kvm
      ;;
    tcg)
      warn_loud "TCG software emulation was requested. The desktop will be slow."
      echo tcg
      ;;
    auto)
      if kvm_usable; then
        echo kvm
      else
        warn_loud "No usable KVM. Need a vmx or svm flag in /proc/cpuinfo and a writable /dev/kvm."
        warn_loud "Falling back to TCG software emulation. The desktop will be slow."
        echo tcg
      fi
      ;;
    *)
      die "Unknown accel ${ACCEL}. Use auto, kvm, or tcg."
      ;;
  esac
}

ssh_forward_line() {
  echo "ssh -L ${PORT}:127.0.0.1:${PORT} USER@YOUR_VPS"
}

print_windows_steps() {
  local viewer
  echo
  echo "From Windows, in PowerShell or Command Prompt (OpenSSH):"
  echo
  ssh_forward_line
  echo
  echo "Replace USER and YOUR_VPS with the SSH login you already use for this VPS."
  if [[ $DISPLAY == spice ]]; then
    viewer="remote-viewer spice://127.0.0.1:${PORT}"
  else
    viewer="A VNC client pointed at 127.0.0.1:${PORT}"
  fi
  echo "Then connect the viewer to localhost only:"
  echo
  echo "  ${viewer}"
  echo
  echo "The console is bound to 127.0.0.1. Do not publish port ${PORT}."
}

print_plan() {
  local host guest vcpus accel
  host=$(host_mem_mib)
  guest=$(choose_guest_mib)
  vcpus=$(choose_vcpus)
  accel=$(choose_accel)
  echo "Host MemTotal: ${host} MiB"
  echo "Guest RAM: ${guest} MiB (reserve ${RESERVE_MIB} MiB for the host, cap ${MAX_GUEST_MIB} MiB)"
  echo "vCPUs: ${vcpus}"
  echo "Disk: ${DISK_GB}G sparse qcow2"
  echo "Profile: Moor Linux Lite (Xfce)"
  echo "Domain: ${NAME}"
  echo "Display: ${DISPLAY} on 127.0.0.1:${PORT}"
  echo "MOOR_ACCEL=${accel}"
  if [[ -n $ISO ]]; then
    echo "ISO: ${ISO}"
  fi
  if [[ -n $SHA ]]; then
    echo "SHA256: ${SHA}"
  else
    echo "SHA256: not requested"
  fi
  echo
  ssh_forward_line
  print_windows_steps
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY=1; shift ;;
      --destroy) DESTROY=1; shift ;;
      --ram)
        [[ $# -ge 2 ]] || die "--ram needs a value"
        RAM_MB=$2
        shift 2
        ;;
      --ram=*) RAM_MB=${1#*=}; shift ;;
      --disk)
        [[ $# -ge 2 ]] || die "--disk needs a value"
        DISK_GB=$2
        shift 2
        ;;
      --disk=*) DISK_GB=${1#*=}; shift ;;
      --vcpus)
        [[ $# -ge 2 ]] || die "--vcpus needs a value"
        VCPUS=$2
        shift 2
        ;;
      --vcpus=*) VCPUS=${1#*=}; shift ;;
      --sha256)
        [[ $# -ge 2 ]] || die "--sha256 needs a value"
        SHA=$2
        shift 2
        ;;
      --sha256=*) SHA=${1#*=}; shift ;;
      --display)
        [[ $# -ge 2 ]] || die "--display needs spice or vnc"
        DISPLAY=$2
        shift 2
        ;;
      --display=*) DISPLAY=${1#*=}; shift ;;
      --port)
        [[ $# -ge 2 ]] || die "--port needs a value"
        PORT=$2
        shift 2
        ;;
      --port=*) PORT=${1#*=}; shift ;;
      --accel)
        [[ $# -ge 2 ]] || die "--accel needs auto, kvm, or tcg"
        ACCEL=$2
        shift 2
        ;;
      --accel=*) ACCEL=${1#*=}; shift ;;
      --name)
        [[ $# -ge 2 ]] || die "--name needs a value"
        NAME=$2
        shift 2
        ;;
      --name=*) NAME=${1#*=}; shift ;;
      --serial-log)
        [[ $# -ge 2 ]] || die "--serial-log needs a path"
        SERIAL_LOG=$2
        shift 2
        ;;
      --serial-log=*) SERIAL_LOG=${1#*=}; shift ;;
      --iso)
        [[ $# -ge 2 ]] || die "--iso needs a path or URL"
        ISO=$2
        shift 2
        ;;
      --iso=*) ISO=${1#*=}; shift ;;
      -h|--help) usage; exit 0 ;;
      --) shift; break ;;
      -*) die "Unknown option $1" ;;
      *)
        if [[ -n $ISO ]]; then
          die "Unexpected argument $1"
        fi
        ISO=$1
        shift
        ;;
    esac
  done
  case "$DISPLAY" in
    spice|vnc) ;;
    *) die "Display must be spice or vnc." ;;
  esac
  require_name
  require_number "port" "$PORT"
  require_number "disk" "$DISK_GB"
  if [[ -n $SHA ]]; then
    SHA=$(printf '%s' "$SHA" | tr '[:upper:]' '[:lower:]')
    if [[ ! $SHA =~ ^[0-9a-f]{64}$ ]]; then
      die "SHA256 must be 64 hex characters."
    fi
  fi
}

state_get() {
  local key=$1
  local line
  [[ -f $STATE_FILE ]] || return 1
  while IFS= read -r line || [[ -n $line ]]; do
    if [[ ${line%%=*} == "$key" ]]; then
      printf '%s' "${line#*=}"
      return 0
    fi
  done <"$STATE_FILE"
  return 1
}

write_state() {
  local iso_path=$1
  local iso_owned=$2
  local disk=$3
  local kernel=$4
  local initrd=$5
  local accel=$6
  install -d -m 0755 "$STATE_DIR"
  local tmp
  tmp=$(mktemp "$STATE_DIR/state.XXXXXX")
  cat >"$tmp" <<EOF
name=${NAME}
disk=${disk}
iso=${iso_path}
iso_owned=${iso_owned}
kernel=${kernel}
initrd=${initrd}
serial_log=${SERIAL_LOG}
xml=${IMAGE_DIR}/${NAME}.xml
accel=${accel}
port=${PORT}
display=${DISPLAY}
EOF
  chmod 0600 "$tmp"
  mv "$tmp" "$STATE_FILE"
}

remove_recorded_file() {
  local path=$1
  if [[ -z $path || $path == / || $path == /var || $path == /var/lib ]]; then
    return 0
  fi
  case "$path" in
    "$IMAGE_DIR"/*|"$STATE_DIR"/*)
      if [[ -f $path ]]; then
        rm -f "$path"
      fi
      ;;
    *)
      echo "Refusing to delete unrecorded path outside the script directories: ${path}" >&2
      ;;
  esac
}

do_destroy() {
  require_debian
  if [[ ! -f $STATE_FILE ]]; then
    echo "No state file at ${STATE_FILE}. Nothing created by this script to remove."
    exit 0
  fi
  require_root
  export LIBVIRT_DEFAULT_URI=qemu:///system
  local name disk iso iso_owned kernel initrd xml
  name=$(state_get name || true)
  disk=$(state_get disk || true)
  iso=$(state_get iso || true)
  iso_owned=$(state_get iso_owned || true)
  kernel=$(state_get kernel || true)
  initrd=$(state_get initrd || true)
  xml=$(state_get xml || true)
  if [[ -z $name ]]; then
    die "State file ${STATE_FILE} has no domain name. Not deleting anything."
  fi
  if command -v virsh >/dev/null 2>&1 && virsh dominfo "$name" >/dev/null 2>&1; then
    if virsh domstate "$name" 2>/dev/null | grep -q '^running'; then
      virsh destroy "$name"
    fi
    virsh undefine "$name" --managed-save >/dev/null 2>&1 || virsh undefine "$name"
  fi
  remove_recorded_file "$disk"
  remove_recorded_file "$kernel"
  remove_recorded_file "$initrd"
  remove_recorded_file "$xml"
  if [[ $iso_owned == 1 ]]; then
    remove_recorded_file "$iso"
  fi
  rm -f "$STATE_FILE"
  rmdir "$IMAGE_DIR" 2>/dev/null || true
  rmdir "$STATE_DIR" 2>/dev/null || true
  echo "Removed the nested VM and files this script created (${name})."
}

verify_sha() {
  local file=$1
  local got
  if [[ -z $SHA ]]; then
    echo "SHA256 was not given. The ISO was not checksum-verified." >&2
    return 0
  fi
  got=$(sha256sum "$file" | awk '{ print $1 }')
  got=$(printf '%s' "$got" | tr '[:upper:]' '[:lower:]')
  if [[ $got != "$SHA" ]]; then
    die "SHA256 mismatch for ${file}. Expected ${SHA}, got ${got}."
  fi
  echo "SHA256 matches ${SHA}." >&2
}

install_packages() {
  local pkgs
  export DEBIAN_FRONTEND=noninteractive
  export NEEDRESTART_MODE=a
  apt-get update
  pkgs=(
    qemu-system-x86 qemu-utils
    libvirt-daemon-system libvirt-clients virtinst
    xorriso curl ca-certificates iproute2
  )
  # Ubuntu splits SPICE out of qemu-system-x86. Debian may already include it.
  if apt-cache show qemu-system-modules-spice >/dev/null 2>&1; then
    pkgs+=(qemu-system-modules-spice)
  fi
  apt-get install -y --no-install-recommends "${pkgs[@]}"
  if ! systemctl enable --now libvirtd; then
    systemctl enable --now libvirtd.socket
  fi
  export LIBVIRT_DEFAULT_URI=qemu:///system
}

prepare_iso() {
  local src=$1
  local dest owned
  install -d -m 0755 "$IMAGE_DIR"
  if [[ $src == http://* || $src == https://* ]]; then
    dest=$IMAGE_DIR/moor-linux.iso
    curl -fL --retry 5 --retry-delay 2 -o "${dest}.partial" "$src"
    mv "${dest}.partial" "$dest"
    owned=1
  else
    if [[ ! -f $src ]]; then
      die "ISO not found: ${src}"
    fi
    dest=$IMAGE_DIR/$(basename "$src")
    if [[ $(realpath "$src") != $(realpath -m "$dest") ]]; then
      cp -f "$src" "$dest"
      owned=1
    else
      owned=0
    fi
  fi
  chmod 0644 "$dest"
  verify_sha "$dest"
  printf '%s\n%s\n' "$dest" "$owned"
}

extract_xfce_boot() {
  local iso=$1
  local work kernel initrd cmdline linux_path initrd_path
  work=$(mktemp -d)
  xorriso -osirrox on -indev "$iso" -extract /isolinux/live.cfg "$work/live.cfg" >/dev/null
  linux_path=$(awk '
    $1 == "label" && $2 == "live-xfce" { grab=1; next }
    grab && $1 == "label" { exit }
    grab && $1 == "linux" { print $2; exit }
  ' "$work/live.cfg")
  initrd_path=$(awk '
    $1 == "label" && $2 == "live-xfce" { grab=1; next }
    grab && $1 == "label" { exit }
    grab && $1 == "initrd" { print $2; exit }
  ' "$work/live.cfg")
  cmdline=$(awk '
    $1 == "label" && $2 == "live-xfce" { grab=1; next }
    grab && $1 == "label" { exit }
    grab && $1 == "append" {
      sub(/^[[:space:]]*append[[:space:]]+/, "")
      print
      exit
    }
  ' "$work/live.cfg")
  if [[ -z $linux_path || -z $initrd_path || -z $cmdline ]]; then
    die "This ISO has no Moor Linux Lite (Xfce) boot entry."
  fi
  case "$cmdline" in
    *moor.session=xfce*) ;;
    *) die "The Xfce boot entry is missing moor.session=xfce." ;;
  esac
  kernel=$IMAGE_DIR/vmlinuz
  initrd=$IMAGE_DIR/initrd.img
  xorriso -osirrox on -indev "$iso" \
    -extract "$linux_path" "$kernel" \
    -extract "$initrd_path" "$initrd" >/dev/null
  chmod 0644 "$kernel" "$initrd"
  rm -rf "$work"
  printf '%s\n%s\n%s\n' "$kernel" "$initrd" "$cmdline"
}

pin_spice_graphics() {
  local xml=$1
  [[ $DISPLAY == spice ]] || return 0
  python3 - "$xml" "$PORT" <<'PY'
import re
import sys

path, port = sys.argv[1], sys.argv[2]
if not port.isdigit():
    sys.exit("spice port is not numeric")
text = open(path, encoding="utf-8").read()
pattern = re.compile(r"<graphics\b[^>]*\btype=(['\"])spice\1[^>]*/?>", re.I)
matches = list(pattern.finditer(text))
if len(matches) != 1:
    sys.exit("expected one spice graphics element, found %d" % len(matches))
tag = matches[0].group(0)
if tag.endswith("/>"):
    sys.exit("spice graphics element has no listen child")
# Omit tlsPort. libvirt treats tlsPort='-1' as "allocate a TLS port",
# which fails when qemu.conf has spice TLS disabled. With the attribute
# absent and autoport='no', TLS stays off.
new = (
    "<graphics type='spice' port='%s' autoport='no' "
    "listen='127.0.0.1' defaultMode='insecure'>"
    % port
)
text = text[: matches[0].start()] + new + text[matches[0].end() :]
open(path, "w", encoding="utf-8").write(text)
PY
}

listen_is_local() {
  local xml=$1
  local graphics
  graphics=$(awk '/<graphics /,/<\/graphics>/' "$xml")
  if [[ -z $graphics ]]; then
    return 1
  fi
  if printf '%s\n' "$graphics" | grep -E -q "0\\.0\\.0\\.0|listen='::'|address='::'|listen='\\*'|address='\\*'"; then
    return 1
  fi
  printf '%s\n' "$graphics" | grep -q "127.0.0.1"
}

console_is_local() {
  local public=0
  local line
  if ! command -v ss >/dev/null 2>&1; then
    die "ss is missing; iproute2 should have been installed."
  fi
  while IFS= read -r line; do
    case "$line" in
      *"127.0.0.1:${PORT}"*|*"127.0.0.1:${PORT} "*) ;;
      *":${PORT}"*)
        public=1
        echo "$line" >&2
        ;;
    esac
  done < <(ss -H -ltn "sport = :${PORT}" || true)
  if [[ $public -ne 0 ]]; then
    die "Console port ${PORT} is listening outside 127.0.0.1."
  fi
  if ! ss -H -ltn "sport = :${PORT}" | grep -q "127.0.0.1:${PORT}"; then
    die "Console is not listening on 127.0.0.1:${PORT}."
  fi
}

start_domain() {
  local iso=$1 kernel=$2 initrd=$3 cmdline=$4 disk=$5 guest=$6 vcpus=$7 accel=$8
  local xml cpu_model virt_type
  xml=$IMAGE_DIR/${NAME}.xml
  if [[ -n $SERIAL_LOG ]]; then
    cmdline="${cmdline} console=tty0 console=ttyS0,115200"
    : >"$SERIAL_LOG"
    chmod 0644 "$SERIAL_LOG"
  fi
  if [[ $accel == kvm ]]; then
    cpu_model=host
    virt_type=kvm
  else
    cpu_model=qemu64
    virt_type=qemu
  fi
  local -a args=(
    --name "$NAME"
    --memory "$guest"
    --vcpus "$vcpus"
    --virt-type "$virt_type"
    --cpu "$cpu_model"
    --os-variant "$(os_variant)"
    --import
    --disk "path=${disk},format=qcow2,bus=virtio"
    --disk "path=${iso},device=cdrom,bus=sata,readonly=on"
    --boot "kernel=${kernel},initrd=${initrd},kernel_args=${cmdline}"
    --network "user,model=virtio"
    --video virtio
    --input "tablet,bus=usb"
    --noautoconsole
  )
  if [[ $DISPLAY == spice ]]; then
    # defaultMode=insecure avoids a TLS channel. pin_spice_graphics then
    # drops tlsPort, because libvirt reads tlsPort='-1' as an autoport
    # request and Ubuntu's qemu.conf has spice TLS disabled.
    args+=(--graphics "spice,listen=127.0.0.1,port=${PORT},defaultMode=insecure")
    args+=(--channel "spicevmc,target_type=virtio,name=com.redhat.spice.0")
  else
    args+=(--graphics "vnc,listen=127.0.0.1,port=${PORT}")
  fi
  if [[ -n $SERIAL_LOG ]]; then
    args+=(--serial "file,path=${SERIAL_LOG}")
  fi
  virt-install "${args[@]}" --print-xml >"$xml"
  pin_spice_graphics "$xml"
  if ! listen_is_local "$xml"; then
    die "Refusing to define a domain whose console is not limited to 127.0.0.1. See ${xml}."
  fi
  virsh define "$xml"
  virsh start "$NAME"
  local _
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if ss -H -ltn "sport = :${PORT}" | grep -q "127.0.0.1:${PORT}"; then
      break
    fi
    sleep 1
  done
  console_is_local
}

os_variant() {
  if ! command -v osinfo-query >/dev/null 2>&1; then
    echo linux2022
    return
  fi
  if osinfo-query os 2>/dev/null | awk '{ print $1 }' | grep -qx debian13; then
    echo debian13
  elif osinfo-query os 2>/dev/null | awk '{ print $1 }' | grep -qx debian12; then
    echo debian12
  else
    echo linux2022
  fi
}

domain_exists() {
  command -v virsh >/dev/null 2>&1 && virsh dominfo "$NAME" >/dev/null 2>&1
}

create_vm() {
  require_root
  export LIBVIRT_DEFAULT_URI=qemu:///system
  if [[ -z $ISO ]]; then
    die "Pass the ISO path or URL."
  fi
  install_packages
  if domain_exists; then
    if [[ -f $STATE_FILE ]] && [[ $(state_get name || true) == "$NAME" ]]; then
      if ! virsh domstate "$NAME" | grep -q '^running'; then
        virsh start "$NAME"
      fi
      console_is_local
      echo "VM ${NAME} is already defined. Leaving it in place."
      echo "MOOR_ACCEL=$(state_get accel || echo unknown)"
      print_windows_steps
      exit 0
    fi
    die "Libvirt already has a domain named ${NAME}, and this script did not record it. Refusing to replace it."
  fi
  local guest vcpus accel prepared iso_path iso_owned
  guest=$(choose_guest_mib)
  vcpus=$(choose_vcpus)
  accel=$(choose_accel)
  mapfile -t prepared < <(prepare_iso "$ISO")
  iso_path=${prepared[0]:-}
  iso_owned=${prepared[1]:-}
  if [[ ! -f $iso_path || ( $iso_owned != 0 && $iso_owned != 1 ) ]]; then
    die "Could not prepare the ISO."
  fi
  local boot_lines kernel initrd cmdline
  mapfile -t boot_lines < <(extract_xfce_boot "$iso_path")
  kernel=${boot_lines[0]:-}
  initrd=${boot_lines[1]:-}
  cmdline=${boot_lines[2]:-}
  if [[ ! -f $kernel || ! -f $initrd || -z $cmdline ]]; then
    die "Could not read the Moor Linux Lite (Xfce) boot entry from the ISO."
  fi
  local disk=$IMAGE_DIR/${NAME}.qcow2
  if [[ ! -f $disk ]]; then
    qemu-img create -f qcow2 "$disk" "${DISK_GB}G"
  fi
  chmod 0644 "$disk"
  write_state "$iso_path" "$iso_owned" "$disk" "$kernel" "$initrd" "$accel"
  echo "MOOR_ACCEL=${accel}"
  start_domain "$iso_path" "$kernel" "$initrd" "$cmdline" "$disk" "$guest" "$vcpus" "$accel"
  echo "MOOR_ACCEL=${accel}"
  echo "Guest ${NAME} is running: ${guest} MiB, ${vcpus} vCPU, ${accel}, ${DISPLAY} on 127.0.0.1:${PORT}."
  print_windows_steps
}

main() {
  parse_args "$@"
  require_debian
  if [[ $DESTROY -eq 1 ]]; then
    if [[ $DRY -eq 1 ]]; then
      echo "Would remove only the domain and files recorded in ${STATE_FILE}."
      exit 0
    fi
    do_destroy
    exit 0
  fi
  if [[ -z $ISO ]]; then
    die "Pass the ISO path or URL. Use --help for options."
  fi
  require_number "disk" "$DISK_GB"
  if [[ $DRY -eq 1 ]]; then
    if [[ $ISO != http://* && $ISO != https://* ]]; then
      if [[ ! -f $ISO ]]; then
        die "ISO not found: ${ISO}"
      fi
      verify_sha "$ISO"
    else
      echo "Would download ${ISO}"
    fi
    print_plan
    exit 0
  fi
  create_vm
}

main "$@"
