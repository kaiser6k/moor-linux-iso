#!/usr/bin/env python3
"""Idle RAM sample for a Moor Linux ISO.

Boots the image in QEMU (KVM if /dev/kvm is usable, otherwise TCG), lets the
graphical session autologin, waits two minutes, then records free -m, smem -tk
when the guest has smem, ps_mem when python3 and the script are available, a
/proc PSS total, and the top 15 processes. The probe is a root shell on tty3
so the sample does not include a GUI terminal. Repeats and prints the median.

Profiles:
  gnome  default boot menu entry (GNOME, or the baseline image)
  xfce   "Moor Linux Lite (Xfce)" menu entry
"""
import argparse
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))

_PLAIN = {
    " ": "spc", "-": "minus", "=": "equal", "[": "bracket_left", "]": "bracket_right",
    ";": "semicolon", "'": "apostrophe", "\\": "backslash", ",": "comma", ".": "dot",
    "/": "slash", "`": "grave_accent", "\n": "ret",
}
_SHIFT = {
    "{": "bracket_left", "}": "bracket_right", "<": "comma", ">": "dot",
    ":": "semicolon", '"': "apostrophe", "|": "backslash", "?": "slash",
    "~": "grave_accent", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
    "^": "6", "&": "7", "*": "8", "(": "9", ")": "0", "_": "minus", "+": "equal",
}


def key_spec(ch):
    if "a" <= ch <= "z" or "0" <= ch <= "9":
        return False, ch
    if ch in _PLAIN:
        return False, _PLAIN[ch]
    if ch in _SHIFT:
        return True, _SHIFT[ch]
    if "A" <= ch <= "Z":
        return True, ch.lower()
    raise ValueError(f"no qcode for {ch!r}")


class QMP:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self.buf = b""
        self._line()
        self.cmd("qmp_capabilities")

    def _line(self):
        while b"\n" not in self.buf:
            d = self.s.recv(65536)
            if not d:
                raise EOFError("qmp closed")
            self.buf += d
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def cmd(self, name, **args):
        msg = {"execute": name}
        if args:
            msg["arguments"] = args
        self.s.sendall((json.dumps(msg) + "\n").encode())
        while True:
            r = self._line()
            if "return" in r or "error" in r:
                return r


def read_ppm(path):
    with open(path, "rb") as f:
        magic = f.readline().strip()
        if magic != b"P6":
            raise ValueError(f"not a P6 ppm: {magic!r}")
        tokens = []
        while len(tokens) < 3:
            line = f.readline()
            if not line:
                break
            if line.startswith(b"#"):
                continue
            tokens.extend(line.split())
        w, h, _maxv = (int(t) for t in tokens[:3])
        data = f.read()
    return w, h, data


def ppm_to_png(ppm_path, png_path):
    w, h, data = read_ppm(ppm_path)
    need = w * h * 3
    if len(data) < need:
        data = data + bytes(need - len(data))
    data = data[:need]

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + data[y * w * 3:(y + 1) * w * 3] for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")
    with open(png_path, "wb") as f:
        f.write(png)


def analyze_ppm(path):
    w, h, data = read_ppm(path)
    nonblack = total = top_bright = bot_bright = bot_n = mid_bright = 0
    bar = 0
    minx, maxx = w, 0
    for y in range(0, h, 2):
        row = y * w * 3
        row_bright = 0
        in_mid = (h * 0.15) <= y <= (h * 0.85)
        for x in range(0, w, 2):
            i = row + x * 3
            if i + 2 >= len(data):
                continue
            s = data[i] + data[i + 1] + data[i + 2]
            total += 1
            if s > 40:
                nonblack += 1
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
            if s > 400:
                row_bright += 1
                if in_mid:
                    mid_bright += 1
            if y < 36 and s > 500:
                top_bright += 1
            if y >= h - 70:
                bot_n += 1
                if s > 500:
                    bot_bright += 1
        if row_bright > bar:
            bar = row_bright
    return {
        "nonblack": nonblack / total if total else 0,
        "width": ((maxx - minx) / w) if nonblack else 0,
        "top_bright": top_bright,
        "bot": bot_bright / bot_n if bot_n else 0,
        "mid_bright": mid_bright,
        "bar": bar,
        "w": w,
        "h": h,
    }


def is_desktop(st):
    # Same gate as scripts/boot-test.py: GNOME top bar plus the bottom dock.
    return 40 <= st["top_bright"] <= 450 and 0.02 <= st["bot"] <= 0.09


def is_session_frame(st):
    """GNOME gate, or a looser panel-and-wallpaper frame for the Xfce entry."""
    if is_desktop(st):
        return True
    if is_boot_menu(st):
        return False
    # Dark wallpaper with a panel. A text console is much brighter in the middle.
    return st["nonblack"] >= 0.30 and st["top_bright"] >= 15 and st["mid_bright"] < 3000


def is_boot_menu(st):
    if is_desktop(st):
        return False
    # BIOS ISOLINUX. This probe boots BIOS only.
    return 0.015 <= st["nonblack"] <= 0.25 and st["mid_bright"] >= 400 and st["bar"] >= 80


def wait_stable(path, tries=80):
    last = -1
    for _ in range(tries):
        if os.path.exists(path):
            sz = os.path.getsize(path)
            if sz > 64 and sz == last:
                return True
            last = sz
        time.sleep(0.05)
    return os.path.exists(path) and os.path.getsize(path) > 64


class VM:
    def __init__(self, iso, out, mem, smp, accel):
        self.out = out
        os.makedirs(out, exist_ok=True)
        self.qmp_path = os.path.join(out, "qmp.sock")
        if os.path.exists(self.qmp_path):
            os.unlink(self.qmp_path)
        self.probe = os.path.join(out, "probe")
        os.makedirs(self.probe, exist_ok=True)
        kvm_ok = os.access("/dev/kvm", os.R_OK | os.W_OK)
        self.accel = ("kvm" if kvm_ok else "tcg") if accel == "auto" else accel
        cmd = [
            "qemu-system-x86_64", "-name", "moor-idle", "-m", str(mem), "-smp", str(smp),
            "-machine", "q35", "-device", "virtio-vga", "-display", "none",
            "-usb", "-device", "usb-tablet",
            "-nic", "user,model=virtio-net-pci",
            "-cdrom", os.path.abspath(iso), "-boot", "d",
            "-qmp", f"unix:{self.qmp_path},server=on,wait=off",
            "-serial", f"file:{os.path.join(out, 'serial.log')}",
            "-fsdev", f"local,id=probefs,path={self.probe},security_model=mapped",
            "-device", "virtio-9p-pci,fsdev=probefs,mount_tag=probe",
        ]
        cmd += (["-accel", "kvm", "-cpu", "host"] if self.accel == "kvm"
                else ["-accel", "tcg,thread=multi,tb-size=512", "-cpu", "max"])
        self.cmd = cmd
        print("QEMU:", " ".join(cmd), flush=True)
        open(os.path.join(out, "qemu-cmd.txt"), "w").write(" ".join(cmd) + "\n")
        self.t0 = time.time()
        self.proc = subprocess.Popen(
            cmd, stdout=open(os.path.join(out, "qemu.out"), "w"), stderr=subprocess.STDOUT)
        for _ in range(200):
            if os.path.exists(self.qmp_path) or self.proc.poll() is not None:
                break
            time.sleep(0.05)
        if self.proc.poll() is not None:
            print(open(os.path.join(out, "qemu.out")).read())
            raise SystemExit("QEMU exited early")
        self.q = QMP(self.qmp_path)

    def elapsed(self):
        return time.time() - self.t0

    def alive(self):
        return self.proc.poll() is None

    def _dump(self, ppm):
        if os.path.exists(ppm):
            os.unlink(ppm)
        r = self.q.cmd("screendump", filename=os.path.abspath(ppm))
        if not wait_stable(ppm):
            print(f"screendump failed {r.get('error', '')}", flush=True)
            return False
        return True

    def shot(self, name):
        ppm = os.path.join(self.out, name + ".ppm")
        if not self._dump(ppm):
            return None
        png = os.path.join(self.out, name + ".png")
        try:
            ppm_to_png(ppm, png)
        except Exception as e:
            print(f"png encode failed {name}: {e}", flush=True)
            return None
        finally:
            if os.path.exists(ppm):
                os.unlink(ppm)
        print(f"t={self.elapsed():.0f}s shot {png}", flush=True)
        return png

    def frame(self):
        ppm = os.path.join(self.out, "_frame.ppm")
        if not self._dump(ppm):
            return None
        st = analyze_ppm(ppm)
        if os.path.exists(ppm):
            os.unlink(ppm)
        return st

    def tap(self, *qcodes, hold=50):
        keys = [{"type": "qcode", "data": c} for c in qcodes]
        r = self.q.cmd("send-key", **{"keys": keys, "hold-time": hold})
        if r.get("error"):
            print("send-key error", r["error"], qcodes, flush=True)
        time.sleep(hold / 1000.0 + 0.04)

    def type_text(self, text):
        for ch in text:
            shift, code = key_spec(ch)
            if shift:
                self.tap("shift", code)
            else:
                self.tap(code)

    def type_line(self, text):
        print("TYPE", text, flush=True)
        self.type_text(text)
        self.tap("ret")

    def stop(self):
        try:
            self.q.cmd("quit")
        except Exception:
            pass
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        if os.path.exists(self.qmp_path):
            os.unlink(self.qmp_path)


def select_menu(vm, downs):
    """Freeze the 5s menu and move `downs` entries, then boot that entry."""
    stable = 0
    chosen = False
    while vm.elapsed() < 40 and vm.alive() and not chosen:
        time.sleep(0.12)
        st = vm.frame()
        if not st:
            continue
        print(f"t={vm.elapsed():.1f}s menu? nonblack={st['nonblack']:.3f} mid={st['mid_bright']} "
              f"bar={st['bar']} top={st['top_bright']} bot={st['bot']:.3f}", flush=True)
        if is_desktop(st):
            print("desktop appeared before the menu was selected", flush=True)
            return False
        if is_boot_menu(st):
            stable += 1
        else:
            stable = 0
            continue
        if stable < 2:
            continue
        print(f"boot menu visible at t={vm.elapsed():.1f}s; selecting entry +{downs}", flush=True)
        for i in range(downs):
            vm.tap("down", hold=100)
            time.sleep(0.35)
        vm.shot("menu-selected")
        vm.tap("ret")
        chosen = True
    return chosen


def wait_desktop(vm, limit):
    found = None
    png = None
    next_save = 20
    while vm.elapsed() < limit and vm.alive():
        time.sleep(4)
        save = vm.elapsed() >= next_save
        st = vm.frame()
        if st is None:
            continue
        print(f"t={vm.elapsed():.0f}s desktop? top={st['top_bright']} bot={st['bot']:.3f} "
              f"mid={st['mid_bright']} nonblack={st['nonblack']:.3f}", flush=True)
        if save:
            next_save += 30
            png = vm.shot(f"boot-t{int(vm.elapsed()):03d}s") or png
        if is_session_frame(st):
            time.sleep(4)
            st2 = vm.frame()
            if not st2 or not is_session_frame(st2):
                print(f"t={vm.elapsed():.0f}s desktop candidate did not hold", flush=True)
                continue
            found = vm.elapsed()
            png = vm.shot("desktop") or png
            break
    return found, png


def listen_for_done(port_box, timeout=120):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 0))
    srv.listen(1)
    srv.settimeout(timeout)
    port_box["port"] = srv.getsockname()[1]
    port_box["ready"] = True

    def accept():
        try:
            conn, _addr = srv.accept()
            conn.settimeout(30)
            data = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            conn.close()
            port_box["data"] = data.decode("utf-8", "replace")
        except Exception as e:
            port_box["error"] = str(e)
        finally:
            srv.close()

    threading.Thread(target=accept, daemon=True).start()
    while not port_box.get("ready"):
        time.sleep(0.05)


def tty_probe(vm, port):
    vm.tap("ctrl", "alt", "f3", hold=120)
    time.sleep(4)
    vm.tap("ret")
    time.sleep(1.2)
    vm.type_line("moor")
    time.sleep(1.4)
    vm.type_line("live")
    time.sleep(2.0)
    cmd = (
        "sudo -n modprobe 9pnet_virtio; sudo -n mkdir -p /mnt/probe; "
        "sudo -n mount -t 9p -o trans=virtio,version=9p2000.L probe /mnt/probe && "
        "sudo -n sh /mnt/probe/collect.sh; "
        f"bash -c 'echo PROBE_DONE > /dev/tcp/10.0.2.2/{port}'"
    )
    vm.type_line(cmd)
    vm.shot("tty-typed")


def parse_size_token(tok):
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)([KkMmGg])?$", tok)
    if not m:
        return None
    n = float(m.group(1))
    u = (m.group(2) or "").upper()
    if u == "":
        return n / 1024.0  # smem's unsuffixed unit is KiB
    if u == "K":
        return n / 1024.0
    if u == "M":
        return n
    if u == "G":
        return n * 1024.0
    return None


def section(text, name):
    m = re.search(rf"^==={re.escape(name)}===\s*$([\s\S]*?)(?=^===|\Z)", text, re.M)
    return m.group(1) if m else ""


def parse_free(text):
    blob = section(text, "FREE") or text
    for line in blob.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) < 7:
                return None
            return {
                "total_mib": int(parts[1]),
                "used_mib": int(parts[2]),
                "free_mib": int(parts[3]),
                "shared_mib": int(parts[4]),
                "buff_cache_mib": int(parts[5]),
                "available_mib": int(parts[6]),
            }
    return None


def parse_smem_pss_mib(text):
    blob = section(text, "SMEM")
    if not blob or "smem not installed" in blob:
        return None
    last = None
    for line in blob.splitlines():
        sizes = []
        for tok in line.split():
            sz = parse_size_token(tok)
            if sz is not None:
                sizes.append(sz)
        if len(sizes) >= 4:
            last = sizes[-2]  # Swap USS PSS RSS -> PSS
    return None if last is None else round(last, 1)


def parse_ps_mem_mib(text):
    """Last line of ps_mem that ends in KiB/MiB/GiB is the total."""
    blob = section(text, "PS_MEM")
    if not blob or "ps_mem unavailable" in blob:
        return None
    last = None
    for line in blob.splitlines():
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s+(KiB|MiB|GiB)\s*$", line.strip())
        if not m:
            continue
        n = float(m.group(1))
        mult = {"KiB": 1 / 1024.0, "MiB": 1.0, "GiB": 1024.0}[m.group(2)]
        last = n * mult
    return None if last is None else round(last, 1)


def parse_pss_kib(text):
    m = re.search(r"PSS_TOTAL_KIB\s+([0-9]+)", text)
    return int(m.group(1)) if m else None


def parse_top(text, n=15):
    blob = section(text, "TOP")
    rows = []
    for line in blob.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("RSS"):
            continue
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        rows.append({
            "rss_kib": int(parts[0]),
            "pid": int(parts[1]),
            "user": parts[2],
            "comm": parts[3],
        })
        if len(rows) >= n:
            break
    return rows


def parse_memtotal_kib(text):
    m = re.search(r"^===MEMTOTAL===\s*([0-9]+)", text, re.M)
    return int(m.group(1)) if m else None


def session_ok(text, profile):
    blob = section(text, "SESSION")
    user_ok = re.search(r"(?m)^moor\s", blob) is not None
    if profile == "xfce":
        return "xfce4-session" in blob and user_ok
    return "gnome-shell" in blob and user_ok


def network_ok(text):
    blob = section(text, "IP")
    for match in re.finditer(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", blob):
        if not match.group(1).startswith("127."):
            return True
    return False


def median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def prepare_probe(dest):
    os.makedirs(dest, exist_ok=True)
    src = os.path.join(HERE, "guest-idle-ram.sh")
    shutil.copy(src, os.path.join(dest, "collect.sh"))
    os.chmod(os.path.join(dest, "collect.sh"), 0o755)
    ps_mem = os.path.join(HERE, "ps_mem.py")
    if os.path.exists(ps_mem):
        shutil.copy(ps_mem, os.path.join(dest, "ps_mem.py"))
    # Drop a previous result so a retry cannot read a stale file.
    for name in ("result.txt",):
        p = os.path.join(dest, name)
        if os.path.exists(p):
            os.unlink(p)


# pixelb/ps_mem @ 8dbf7f9df7979773bc135f61d40447b41dc484fd (release 3.14). GPL-2.0.
# Fetched at probe time; not vendored.
PS_MEM_URL_PIN = (
    "https://raw.githubusercontent.com/pixelb/ps_mem/"
    "8dbf7f9df7979773bc135f61d40447b41dc484fd/ps_mem.py"
)


def ensure_ps_mem(probe_dir):
    target = os.path.join(probe_dir, "ps_mem.py")
    vendored = os.path.join(HERE, "ps_mem.py")
    if os.path.exists(vendored):
        shutil.copy(vendored, target)
        return True
    url = os.environ.get("PS_MEM_URL", PS_MEM_URL_PIN)
    try:
        import urllib.request
        urllib.request.urlretrieve(url, target)
        if os.path.getsize(target) > 1000 and b"def " in open(target, "rb").read(8000):
            return True
    except Exception as e:
        print(f"ps_mem download failed: {e}", flush=True)
    if os.path.exists(target):
        os.unlink(target)
    return False


def one_run(iso, out, profile, mem, smp, accel, settle_s):
    vm = VM(iso, out, mem, smp, accel)
    # The 9p directory is the one QEMU was given. Seed it after VM() creates it.
    seed = os.path.join(out, "probe")
    prepare_probe(seed)
    ps_mem_ok = ensure_ps_mem(seed)
    try:
        menu_ok = True
        if profile == "xfce":
            # Live, serial, fail-safe, then Lite. Three Downs.
            menu_ok = select_menu(vm, 3)
        desk_at, desk_png = wait_desktop(vm, 240)
        if desk_at is None:
            # A missed heuristic must not skip the idle wait. The search already
            # ran; this is the same two-minute settle the detected path uses.
            print("desktop was not detected; settling anyway before the probe", flush=True)
            vm.shot("no-desktop")
        else:
            print(f"desktop at t={desk_at:.0f}s; settling {settle_s}s", flush=True)
        end = time.time() + settle_s
        while time.time() < end and vm.alive():
            time.sleep(5)
        vm.shot("idle-before-probe")
        port_box = {}
        listen_for_done(port_box)
        tty_probe(vm, port_box["port"])
        result_path = os.path.join(seed, "result.txt")
        deadline = time.time() + 90
        text = ""
        while time.time() < deadline and vm.alive():
            if os.path.exists(result_path):
                text = open(result_path, errors="replace").read()
                if "===END===" in text:
                    break
            if port_box.get("data") and "PROBE_DONE" in port_box.get("data", ""):
                time.sleep(1)
                if os.path.exists(result_path):
                    text = open(result_path, errors="replace").read()
                if "===END===" in text:
                    break
            time.sleep(0.5)
        vm.shot("tty-after")
        if "===END===" not in text:
            print("9p probe did not finish; collecting over TCP instead", flush=True)
            port_box = {}
            listen_for_done(port_box, timeout=150)
            # sudo so smem and smaps_rollup see every process. The redirect is
            # the login shell's, so the whole sudo stdout lands on the host.
            # No '$' in this string: key_spec cannot type it. PSS stays on the 9p path.
            vm.type_line(
                "sudo -n bash -c 'echo ===FREE===; free -m; echo ===TOP===; "
                "ps -eo rss,pid,user,comm --sort=-rss | head -n 16; echo ===IP===; "
                "ip -4 -br addr; echo ===SESSION===; ps -eo user,pid,comm; "
                "echo ===SMEM===; if command -v smem >/dev/null; then smem -tk; "
                "else echo smem not installed; fi; echo ===END===' "
                f"> /dev/tcp/10.0.2.2/{port_box['port']}"
            )
            deadline = time.time() + 120
            while time.time() < deadline:
                if "===END===" in port_box.get("data", ""):
                    text = port_box["data"]
                    break
                time.sleep(0.4)
            vm.shot("tty-fallback")
        open(os.path.join(out, "probe.txt"), "w").write(text)
        free = parse_free(text)
        pss_kib = parse_pss_kib(text)
        return {
            "accel": vm.accel,
            "menu_selected": menu_ok,
            "desktop_detected": desk_at is not None,
            "desktop_at_s": None if desk_at is None else round(desk_at, 1),
            "settle_s": settle_s,
            "settled": True,
            "ps_mem_staged": ps_mem_ok,
            "probe_tcp": "PROBE_DONE" in (port_box.get("data") or ""),
            "free": free,
            "smem_pss_mib": parse_smem_pss_mib(text),
            "smem_installed": "smem not installed" not in text and "===SMEM===" in text and parse_smem_pss_mib(text) is not None,
            "ps_mem_mib": parse_ps_mem_mib(text),
            "pss_total_kib": pss_kib,
            "pss_total_mib": None if pss_kib is None else round(pss_kib / 1024.0, 1),
            "top": parse_top(text),
            "memtotal_kib": parse_memtotal_kib(text),
            "session_ok": session_ok(text, profile),
            "network_ok": network_ok(text),
            "probe_complete": "===END===" in text,
        }
    finally:
        vm.stop()


def run_profile(iso, out, profile, label, runs, mem, smp, accel, settle_s):
    os.makedirs(out, exist_ok=True)
    results = []
    for i in range(1, runs + 1):
        print(f"\n===== {label} run {i}/{runs} =====", flush=True)
        run_out = os.path.join(out, f"run-{i}")
        try:
            row = one_run(iso, run_out, profile, mem, smp, accel, settle_s)
        except Exception as e:
            row = {"error": str(e), "probe_complete": False}
            print(f"run {i} failed: {e}", flush=True)
        row["run"] = i
        results.append(row)
        print("RUN", json.dumps(row, indent=1), flush=True)
    used = [r["free"]["used_mib"] for r in results if r.get("free") and r.get("probe_complete")]
    avail = [r["free"]["available_mib"] for r in results if r.get("free") and r.get("probe_complete")]
    smem = [r["smem_pss_mib"] for r in results if r.get("smem_pss_mib") is not None and r.get("probe_complete")]
    ps_mem = [r["ps_mem_mib"] for r in results if r.get("ps_mem_mib") is not None and r.get("probe_complete")]
    pss = [r["pss_total_mib"] for r in results if r.get("pss_total_mib") is not None and r.get("probe_complete")]
    ok = [r for r in results if r.get("probe_complete") and r.get("free") and r.get("session_ok")]
    summary = {
        "label": label,
        "profile": profile,
        "iso": os.path.abspath(iso),
        "runs_requested": runs,
        "runs": results,
        "successful_runs": len(ok),
        "median_used_mib": median(used),
        "median_available_mib": median(avail),
        "median_smem_pss_mib": median(smem),
        "median_ps_mem_mib": median(ps_mem),
        "median_proc_pss_mib": median(pss),
        "smem_runs": len(smem),
        "ps_mem_runs": len(ps_mem),
        "all_sessions_ok": len(ok) == runs and all(r.get("network_ok") for r in ok),
        "network_ok_runs": sum(1 for r in results if r.get("network_ok")),
        "desktop_detected_runs": sum(1 for r in results if r.get("desktop_detected")),
    }
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    write_summary_md(summary, os.path.join(out, "summary.md"))
    print("SUMMARY", json.dumps({k: summary[k] for k in summary if k != "runs"}, indent=1), flush=True)
    return summary


def write_summary_md(summary, path):
    lines = [
        f"# Idle RAM: {summary['label']}",
        "",
        f"- profile: `{summary['profile']}`",
        f"- successful session samples: {summary['successful_runs']} / {summary['runs_requested']}",
        f"- desktop detected: {summary['desktop_detected_runs']} / {summary['runs_requested']}",
        f"- network (non-loopback IPv4): {summary['network_ok_runs']} / {summary['runs_requested']}",
        f"- median used (free -m): {summary['median_used_mib']}",
        f"- median available (free -m): {summary['median_available_mib']}",
        f"- median smem PSS MiB: {summary['median_smem_pss_mib']} ({summary['smem_runs']} runs had smem)",
        f"- median ps_mem MiB: {summary['median_ps_mem_mib']} ({summary['ps_mem_runs']} runs had ps_mem)",
        f"- median /proc PSS MiB: {summary['median_proc_pss_mib']}",
        "",
        "| run | used | available | smem PSS | ps_mem | proc PSS | desktop | session | network |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for r in summary["runs"]:
        free = r.get("free") or {}
        lines.append(
            f"| {r.get('run')} | {free.get('used_mib', '')} | {free.get('available_mib', '')} | "
            f"{'' if r.get('smem_pss_mib') is None else r.get('smem_pss_mib')} | "
            f"{'' if r.get('ps_mem_mib') is None else r.get('ps_mem_mib')} | "
            f"{'' if r.get('pss_total_mib') is None else r.get('pss_total_mib')} | "
            f"{r.get('desktop_detected')} | {r.get('session_ok')} | {r.get('network_ok')} |"
        )
    lines.append("")
    if summary["runs"] and summary["runs"][0].get("top"):
        lines.append("Top processes from run 1 (RSS KiB, before smem):")
        lines.append("")
        for row in summary["runs"][0]["top"]:
            lines.append(f"- {row['rss_kib']} KiB pid {row['pid']} {row['user']} {row['comm']}")
        lines.append("")
    open(path, "w").write("\n".join(lines) + "\n")


def self_test():
    sample = """===FREE===
               total        used        free      shared  buff/cache   available
Mem:            3911        1077        1823          82        1200        2700
Swap:           1955           0        1955
===SMEM===
  PID User     Command                         Swap      USS      PSS      RSS
1234 moor     gnome-shell                        0     180.0M   220.5M   340.0M
-------------------------------------------------------------------------------
  128                                          0     845.2M     1.0G     1.4G
===PSS===
PSS_TOTAL_KIB 1048576
===TOP===
  RSS   PID USER     COMMAND
812000  900 moor     gnome-shell
===SESSION===
moor      900 gnome-shell
===IP===
eth0             UP             10.0.2.15/24
"""
    free = parse_free(sample)
    assert free["used_mib"] == 1077, free
    assert free["available_mib"] == 2700, free
    smem = parse_smem_pss_mib(sample)
    assert smem == 1024.0, smem
    assert parse_pss_kib(sample) == 1048576
    assert session_ok(sample, "gnome")
    assert network_ok(sample)
    assert median([3, 1, 2]) == 2
    assert median([10, 30]) == 20
    assert parse_size_token("1.0G") == 1024.0
    assert abs(parse_size_token("512K") - 0.5) < 0.01
    ps = "===PS_MEM===\n  1.0 MiB +  1.0 MiB =   2.0 MiB\tfoo\n---------------------------------\n                        614.5 MiB\n=================================\n"
    assert parse_ps_mem_mib(ps) == 614.5
    assert session_ok(sample, "gnome") is True
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True)
    ap.add_argument("--profile", choices=["gnome", "xfce"], required=True)
    ap.add_argument("--label", default=None)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default="idle-ram")
    ap.add_argument("--mem", type=int, default=4096)
    ap.add_argument("--smp", type=int, default=min(4, os.cpu_count() or 2))
    ap.add_argument("--accel", choices=["auto", "kvm", "tcg"], default="auto")
    ap.add_argument("--settle", type=int, default=120)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
        return
    label = a.label or a.profile
    summary = run_profile(a.iso, os.path.abspath(a.out), a.profile, label, a.runs,
                          a.mem, a.smp, a.accel, a.settle)
    sys.exit(0 if summary["all_sessions_ok"] else 1)


if __name__ == "__main__":
    main()
