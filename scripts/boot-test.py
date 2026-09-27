#!/usr/bin/env python3
"""Boot checks for the Moor Linux ISO (BIOS and UEFI).

Two QEMU runs per firmware:

1. Menu + fail-safe. Screenshot the boot menu about three seconds after it
   appears (inside the 5s timeout), select "Moor Linux Live (fail-safe mode)"
   with QMP, wait for the GNOME desktop, then read /proc/cmdline and whoami.
2. Default entry. Let the menu time out, sit on the desktop for two minutes,
   record whoami, free -m and /etc/os-release, then launch Moor, Files and
   Terminal.

Text is typed into the graphical terminal (kgx). The same command is also
sent to 10.0.2.2, the QEMU user-net host, so the output is saved as text
even when OCR is fuzzy. A tty3 login is the fallback. KVM is used when
/dev/kvm is usable, TCG otherwise.
"""
import argparse, hashlib, json, os, re, shutil, socket, struct, subprocess, sys, threading, time, zlib

ap = argparse.ArgumentParser()
ap.add_argument("--iso", required=True)
ap.add_argument("--firmware", choices=["bios", "uefi"], required=True)
ap.add_argument("--out", default="screenshots")
ap.add_argument("--mem", default="4096")
ap.add_argument("--smp", default=str(min(4, os.cpu_count() or 2)))
ap.add_argument("--accel", choices=["auto", "kvm", "tcg"], default="auto")
a = ap.parse_args()

OUT = os.path.abspath(a.out)
os.makedirs(OUT, exist_ok=True)
FW = a.firmware

# US QEMU qcodes. Shifted symbols are (True, base_key).
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
    sig = hashlib.md5(data[::48]).hexdigest()
    return {
        "sig": sig,
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
    # GNOME top bar plus the bright bottom dock.
    # A real frame of this image is about top_bright 220 and bot 0.044.
    # A text console full of kernel lines is brighter in both bands
    # (top_bright ~570, bot ~0.11) and must not count.
    return 40 <= st["top_bright"] <= 450 and 0.02 <= st["bot"] <= 0.09


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
    def __init__(self, tag):
        self.tag = tag
        self.run = os.path.join(OUT, f"run-{FW}-{tag}")
        os.makedirs(self.run, exist_ok=True)
        self.qmp_path = os.path.join(self.run, "qmp.sock")
        if os.path.exists(self.qmp_path):
            os.unlink(self.qmp_path)
        kvm_ok = os.access("/dev/kvm", os.R_OK | os.W_OK)
        self.accel = ("kvm" if kvm_ok else "tcg") if a.accel == "auto" else a.accel
        cmd = ["qemu-system-x86_64", "-name", f"moor-{FW}-{tag}", "-m", a.mem, "-smp", a.smp,
               "-machine", "q35", "-device", "virtio-vga", "-display", "none",
               "-usb", "-device", "usb-tablet", "-nic", "user,model=virtio-net-pci",
               "-cdrom", os.path.abspath(a.iso), "-boot", "d",
               "-qmp", f"unix:{self.qmp_path},server=on,wait=off",
               "-serial", f"file:{os.path.join(self.run, 'serial.log')}"]
        cmd += (["-accel", "kvm", "-cpu", "host"] if self.accel == "kvm"
                else ["-accel", "tcg,thread=multi,tb-size=512", "-cpu", "max"])
        if FW == "uefi":
            code = next((p for p in ["/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_CODE.fd"]
                         if os.path.exists(p)), None)
            vars_src = next((p for p in ["/usr/share/OVMF/OVMF_VARS_4M.fd", "/usr/share/OVMF/OVMF_VARS.fd"]
                             if os.path.exists(p)), None)
            if not code or not vars_src:
                sys.exit("OVMF firmware not found (install the ovmf package)")
            vars_copy = os.path.join(self.run, "OVMF_VARS.fd")
            shutil.copy(vars_src, vars_copy)
            cmd += ["-drive", f"if=pflash,format=raw,readonly=on,file={code}",
                    "-drive", f"if=pflash,format=raw,file={vars_copy}"]
        self.cmd = cmd
        print("QEMU:", " ".join(cmd), flush=True)
        open(os.path.join(self.run, "qemu-cmd.txt"), "w").write(" ".join(cmd) + "\n")
        self.t0 = time.time()
        self.proc = subprocess.Popen(cmd, stdout=open(os.path.join(self.run, "qemu.out"), "w"),
                                     stderr=subprocess.STDOUT)
        for _ in range(200):
            if os.path.exists(self.qmp_path) or self.proc.poll() is not None:
                break
            time.sleep(0.05)
        if self.proc.poll() is not None:
            print(open(os.path.join(self.run, "qemu.out")).read())
            sys.exit("QEMU exited early")
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

    def _publish(self, ppm, name):
        png = os.path.join(OUT, f"{FW}-{name}.png")
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

    def shot(self, name):
        ppm = os.path.join(self.run, name + ".ppm")
        if not self._dump(ppm):
            return None
        return self._publish(ppm, name)

    def shot_stats(self, name):
        ppm = os.path.join(self.run, "_frame.ppm")
        if not self._dump(ppm):
            return None
        st = analyze_ppm(ppm)
        if name:
            png = self._publish(ppm, name)
            st["png"] = png
        else:
            if os.path.exists(ppm):
                os.unlink(ppm)
            st["png"] = None
        return st

    def frame(self):
        """Screendump into the run directory and return stats plus a PNG path."""
        ppm = os.path.join(self.run, "_frame.ppm")
        if not self._dump(ppm):
            return None
        st = analyze_ppm(ppm)
        png = os.path.join(self.run, "_frame.png")
        try:
            ppm_to_png(ppm, png)
        except Exception as e:
            print(f"png encode failed: {e}", flush=True)
            st["png"] = None
        else:
            st["png"] = png
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


def listen_for_probe(port_box, timeout=180):
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
            conn.settimeout(20)
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


def ocr(png):
    if not png or not shutil.which("tesseract") or not os.path.exists(png):
        return ""
    r = subprocess.run(["tesseract", png, "stdout", "--psm", "6"],
                       capture_output=True, text=True, timeout=30)
    return r.stdout or ""


def await_probe(port_box, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if "data" in port_box or "error" in port_box:
            return
        time.sleep(0.4)


def tcp_cmd(payload, port):
    return f"bash -c '{payload}' > /dev/tcp/10.0.2.2/{port}"


def tty_login(vm, slow):
    vm.tap("ctrl", "alt", "f3", hold=120)
    time.sleep(6 if slow else 3)
    vm.tap("ret")
    time.sleep(1.5)
    vm.type_line("moor")
    time.sleep(2.0 if slow else 1.2)
    vm.type_line("live")
    time.sleep(3.0 if slow else 1.6)


def collect_text(vm, visible, slow, stem, terminal_name=None):
    """Type `visible` into kgx (then tty3 if that produced no marker).

    Returns (text, ocr_text, png, how).
    """
    port_box = {}
    listen_for_probe(port_box)
    tcp = tcp_cmd(visible, port_box["port"])

    vm.tap("esc")
    time.sleep(0.3)
    vm.tap("alt", "f2", hold=120)
    time.sleep(1.2)
    vm.type_line("kgx")
    time.sleep(8 if slow else 3.5)
    vm.type_line(visible)
    time.sleep(6 if slow else 3.0)
    png = vm.shot(stem + "-console")
    if terminal_name:
        term = vm.shot(terminal_name)
        if term:
            png = term
    vm.type_line(tcp)
    await_probe(port_box, 30 if slow else 20)
    text = port_box.get("data") or ""
    ocr_text = ocr(png)
    blob = text + "\n" + ocr_text
    if "WHOAMI" in blob or re.search(r"(?m)^moor\s*$", blob):
        return text, ocr_text, png, "terminal"

    print("graphical terminal probe had no marker; trying tty3", flush=True)
    port_box = {}
    listen_for_probe(port_box)
    tcp = tcp_cmd(visible, port_box["port"])
    tty_login(vm, slow)
    vm.type_line(visible)
    time.sleep(5 if slow else 2.5)
    tty_png = vm.shot(stem + "-tty")
    vm.type_line(tcp)
    await_probe(port_box, 30 if slow else 20)
    text = port_box.get("data") or ""
    ocr_text = (ocr_text or "") + "\n" + ocr(tty_png)
    return text, ocr_text, tty_png or png, "tty"


def wait_desktop(vm, limit, prefix):
    found_at = None
    png = None
    next_save = 30
    tried_vt = False
    while vm.elapsed() < limit and vm.alive():
        # Fail-safe uses vga=788, so the kernel log can stay on the first VT
        # while GDM is on another. Look there once the log has had time to finish.
        if prefix.startswith("failsafe") and not tried_vt and vm.elapsed() > 90:
            tried_vt = True
            for key in ("f2", "f1"):
                vm.tap("ctrl", "alt", key, hold=120)
                time.sleep(2.5)
                st = vm.shot_stats(f"{prefix}-vt-{key}")
                if st and is_desktop(st):
                    print(f"t={vm.elapsed():.0f}s desktop on {key}", flush=True)
                    time.sleep(4)
                    st2 = vm.shot_stats(f"{prefix}-desktop")
                    if st2 and is_desktop(st2):
                        return vm.elapsed(), st2.get("png")
        time.sleep(5)
        save = vm.elapsed() >= next_save
        st = vm.shot_stats(f"{prefix}-t{int(vm.elapsed()):03d}s" if save else None)
        if st is None:
            continue
        if save:
            next_save += 30
            png = st.get("png") or png
        print(f"t={vm.elapsed():.0f}s desktop? top={st['top_bright']} bot={st['bot']:.3f} "
              f"mid={st['mid_bright']}", flush=True)
        if is_desktop(st):
            # Require the same signature on the next sample so a
            # one-frame console flash cannot end the wait.
            time.sleep(4)
            st2 = vm.shot_stats(f"{prefix}-desktop")
            if not st2 or not is_desktop(st2):
                print(f"t={vm.elapsed():.0f}s desktop candidate did not hold", flush=True)
                continue
            found_at = vm.elapsed()
            png = st2.get("png") or png
            break
    return found_at, png


def grab_back_to_desktop(vm):
    for key in ("f2", "f1", "f7"):
        vm.tap("ctrl", "alt", key, hold=120)
        time.sleep(2.5)
        st = vm.shot_stats(f"vt-{key}")
        if st and is_desktop(st):
            print(f"graphical session on {key}", flush=True)
            return True
    print("could not find the graphical VT; continuing on the current screen", flush=True)
    return False


def launch(vm, command, stem, wait_s):
    vm.tap("esc")
    time.sleep(0.2)
    vm.tap("alt", "f2", hold=120)
    time.sleep(1.0)
    vm.type_line(command)
    time.sleep(wait_s)
    return vm.shot(stem)


def is_boot_menu(st):
    """Menu frame, without OCR. OCR of the GRUB screen costs several seconds,
    which is long enough for the 5s timeout to boot the default entry.
    """
    if is_desktop(st):
        return False
    if FW == "bios":
        # ISOLINUX: black splash, a highlight bar, a modest amount of text.
        return 0.015 <= st["nonblack"] <= 0.25 and st["mid_bright"] >= 400 and st["bar"] >= 80
    # UEFI GRUB gfxterm only. The TianoCore logo is sparse (nonblack ~0.02)
    # and was being mistaken for the isolinux menu.
    # mid_bright drops to 0 once GRUB has already started an entry.
    return (st["nonblack"] >= 0.45 and st["mid_bright"] >= 300 and st["bar"] >= 150
            and st["bot"] >= 0.08 and st["top_bright"] < 40)


def capture_menu(vm):
    """Screenshot the menu and select fail-safe before the 5s timeout.

    The first Down is sent as soon as the menu is visible, which freezes the
    countdown. Later shots (around 3s on BIOS) are taken after that.
    """
    saved = set()
    series_at = {2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 22}
    menu_png = None
    chosen = False
    stable = 0
    while vm.elapsed() < 40 and vm.alive() and not chosen:
        time.sleep(0.12)
        mark = int(vm.elapsed())
        save_name = f"bootmenu-series-{mark:02d}s" if mark in series_at and mark not in saved else None
        st = vm.frame()
        if not st:
            continue
        if save_name and st.get("png"):
            dest = os.path.join(OUT, f"{FW}-{save_name}.png")
            shutil.copy(st["png"], dest)
            saved.add(mark)
            print(f"t={vm.elapsed():.0f}s shot {dest}", flush=True)
        print(f"t={vm.elapsed():.1f}s frame nonblack={st['nonblack']:.3f} mid={st['mid_bright']} "
              f"bar={st['bar']} top={st['top_bright']} bot={st['bot']:.3f}", flush=True)
        if is_desktop(st):
            print("desktop appeared before the menu was selected", flush=True)
            break
        if is_boot_menu(st):
            stable += 1
        else:
            stable = 0
            continue
        if stable < 2 or not st.get("png"):
            continue
        seen = os.path.join(OUT, f"{FW}-bootmenu-seen.png")
        shutil.copy(st["png"], seen)
        print(f"boot menu visible at t={vm.elapsed():.1f}s; stopping the timeout", flush=True)
        # Down freezes the 5s countdown and moves to the serial entry.
        vm.tap("down", hold=100)
        time.sleep(0.35)
        vm.shot("bootmenu-down1")
        hold_until = vm.elapsed() + 1.6
        while vm.elapsed() < hold_until and vm.alive():
            time.sleep(0.25)
            mark = int(vm.elapsed())
            if mark in series_at and mark not in saved:
                snap = vm.frame()
                if snap and snap.get("png"):
                    dest = os.path.join(OUT, f"{FW}-bootmenu-series-{mark:02d}s.png")
                    shutil.copy(snap["png"], dest)
                    saved.add(mark)
                    print(f"t={vm.elapsed():.0f}s shot {dest}", flush=True)
        vm.tap("down", hold=100)
        time.sleep(0.4)
        menu_png = vm.shot("bootmenu-failsafe")
        # Also keep a copy under the name the report looks for.
        if menu_png:
            shutil.copy(menu_png, os.path.join(OUT, f"{FW}-bootmenu.png"))
        elif os.path.exists(seen):
            shutil.copy(seen, os.path.join(OUT, f"{FW}-bootmenu.png"))
            menu_png = os.path.join(OUT, f"{FW}-bootmenu.png")
        vm.tap("ret")
        try:
            open(os.path.join(OUT, f"{FW}-bootmenu.txt"), "w").write(ocr(seen))
        except Exception as e:
            print("menu ocr failed", e, flush=True)
        chosen = True
    return chosen, menu_png


def xorriso_find(iso, name):
    r = subprocess.run(["xorriso", "-indev", iso, "-find", "/", "-name", name],
                       capture_output=True, text=True)
    paths = []
    blob = (r.stdout or "") + "\n" + (r.stderr or "")
    for line in blob.splitlines():
        line = line.strip().strip("'").strip('"')
        if line.startswith("/") and line.endswith(name):
            paths.append(line)
    return paths, blob[-800:]


def check_boot_configs():
    dest = os.path.join(OUT, "iso-config")
    os.makedirs(dest, exist_ok=True)
    report = os.path.join(OUT, f"{FW}-boot-config-check.txt")
    iso = os.path.abspath(a.iso)
    lines = []
    if not shutil.which("xorriso"):
        open(report, "w").write("xorriso is not installed\n")
        return False, report
    wanted = {
        "grub.cfg": ["/boot/grub/grub.cfg"],
        "live.cfg": ["/isolinux/live.cfg"],
        "isolinux.cfg": ["/isolinux/isolinux.cfg"],
    }
    for name, guesses in wanted.items():
        found, raw = xorriso_find(iso, name)
        lines.append(f"found {name}: {found or '(none)'}")
        if not found:
            lines.append(f"find raw {name}: {raw or '(empty)'}")
        srcs = []
        for guess in guesses + found:
            if guess not in srcs:
                srcs.append(guess)
        for src in srcs:
            out = os.path.join(dest, name)
            r = subprocess.run(["xorriso", "-osirrox", "on", "-indev", iso, "-extract", src, out],
                               capture_output=True, text=True)
            if os.path.exists(out) and os.path.getsize(out) > 0 and r.returncode == 0:
                lines.append(f"extracted {src}")
                break
            lines.append(f"extract {src} failed rc={r.returncode}: {((r.stderr or '') + (r.stdout or ''))[-300:]}")
            if os.path.exists(out):
                os.unlink(out)
    grub_line = live_line = ""
    grub = os.path.join(dest, "grub.cfg")
    live = os.path.join(dest, "live.cfg")
    if os.path.exists(grub):
        text = open(grub, errors="replace").read()
        idx = text.find('menuentry "Moor Linux Live (fail-safe mode)"')
        chunk = text[idx:idx + 900] if idx >= 0 else ""
        lm = re.search(r"(?m)^\s*linux\s+(.+)$", chunk) if chunk else None
        grub_line = lm.group(1).strip() if lm else ""
    if os.path.exists(live):
        text = open(live, errors="replace").read()
        m = re.search(r"(?m)^label \S*failsafe\S*\n(?:.*\n){0,8}?\s*append ([^\n]+)", text)
        live_line = m.group(1).strip() if m else ""
    grub_ok = "username=moor" in grub_line
    live_ok = "username=moor" in live_line
    lines.append(f"grub_failsafe: {grub_line or '(not found)'}")
    lines.append(f"grub_failsafe_username_moor: {'yes' if grub_ok else 'no'}")
    lines.append(f"isolinux_failsafe: {live_line or '(not found)'}")
    lines.append(f"isolinux_failsafe_username_moor: {'yes' if live_ok else 'no'}")
    open(report, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    return grub_ok and live_ok, report


def whoami_moor(blob):
    return bool(re.search(r"WHOAMI---\s*moor\b", blob)) or bool(re.search(r"(?m)^moor\s*$", blob))


def run_failsafe():
    vm = VM("failsafe")
    try:
        chosen, menu_png = capture_menu(vm)
        if not chosen:
            print("boot menu was not detected; fail-safe was not selected", flush=True)
        desk_at, desk_png = wait_desktop(vm, 720 if chosen else 90, "failsafe")
        visible = "echo ---CMDLINE---; cat /proc/cmdline; echo ---NPROC---; nproc; echo ---WHOAMI---; whoami"
        text, ocr_text, _png, how = collect_text(vm, visible, slow=True, stem="failsafe")
        open(os.path.join(OUT, f"{FW}-failsafe-probe.txt"), "w").write(text)
        open(os.path.join(OUT, f"{FW}-failsafe-console-ocr.txt"), "w").write(ocr_text)
        blob = text + "\n" + ocr_text
        return {
            "menu_captured": bool(menu_png) or os.path.exists(os.path.join(OUT, f"{FW}-bootmenu-seen.png")),
            "menu_png": os.path.basename(menu_png) if menu_png else None,
            "failsafe_selected": chosen,
            "failsafe_desktop": desk_at is not None,
            "failsafe_desktop_at_s": None if desk_at is None else round(desk_at, 1),
            "failsafe_desktop_png": os.path.basename(desk_png) if desk_png else None,
            "failsafe_cmdline_nosmp": "nosmp" in blob and "memtest" in blob,
            "failsafe_username_moor": ("username=moor" in blob) and whoami_moor(blob),
            "failsafe_probe_how": how,
            "failsafe_probe_tcp": bool(text.strip()),
        }
    finally:
        vm.stop()


def run_default():
    vm = VM("default")
    try:
        desk_at, _desk = wait_desktop(vm, 240, "default-boot")
        if desk_at is None:
            print("default boot did not reach a desktop before the idle wait", flush=True)
            idle_from = vm.elapsed()
        else:
            idle_from = desk_at
        while vm.elapsed() < idle_from + 120 and vm.alive():
            time.sleep(5)
        idle_png = vm.shot("default-idle-2min")
        visible = "echo ---WHOAMI---; whoami; echo ---FREE---; free -m; echo ---OS---; cat /etc/os-release"
        text, ocr_text, _png, how = collect_text(
            vm, visible, slow=False, stem="default", terminal_name="terminal")
        open(os.path.join(OUT, f"{FW}-session-probe.txt"), "w").write(text)
        open(os.path.join(OUT, f"{FW}-session-console-ocr.txt"), "w").write(ocr_text)
        blob = text + "\n" + ocr_text
        if how == "tty":
            grab_back_to_desktop(vm)
            launch(vm, "kgx", "terminal", 6)
        vm.tap("alt", "f2", hold=120)
        time.sleep(1.0)
        vm.type_line("moor-launch")
        time.sleep(8)
        moor8 = vm.shot("moor-8s")
        time.sleep(12)
        moor20 = vm.shot("moor-20s")
        files_png = launch(vm, "nautilus", "files", 8)
        moor_ocr = (ocr(moor8) + "\n" + ocr(moor20)).lower()
        keyring = any(w in moor_ocr for w in ("keyring", "password", "unlock"))
        return {
            "default_desktop": desk_at is not None,
            "default_desktop_at_s": None if desk_at is None else round(desk_at, 1),
            "idle_png": os.path.basename(idle_png) if idle_png else None,
            "whoami_moor": whoami_moor(blob),
            "free_recorded": "Mem:" in blob or "Swap:" in blob,
            "os_release_moor": "Moor Linux" in blob,
            "session_probe_how": how,
            "session_probe_tcp": bool(text.strip()),
            "moor_8s": os.path.basename(moor8) if moor8 else None,
            "moor_20s": os.path.basename(moor20) if moor20 else None,
            "files_png": os.path.basename(files_png) if files_png else None,
            "terminal_png": f"{FW}-terminal.png" if os.path.exists(os.path.join(OUT, f"{FW}-terminal.png")) else None,
            "keyring_dialog_ocr": keyring,
        }
    finally:
        vm.stop()


def main():
    cfg_ok, cfg_report = check_boot_configs()
    failsafe = run_failsafe()
    default = run_default()
    res = {
        "firmware": FW,
        "accel": "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg",
        "config_check": os.path.basename(cfg_report),
        "failsafe_config_username_moor": cfg_ok,
    }
    res.update(failsafe)
    res.update(default)
    required = [
        "failsafe_config_username_moor",
        "menu_captured",
        "failsafe_desktop",
        "failsafe_cmdline_nosmp",
        "failsafe_username_moor",
        "default_desktop",
        "whoami_moor",
        "free_recorded",
        "os_release_moor",
        "moor_20s",
        "files_png",
        "terminal_png",
    ]
    res["required_ok"] = {k: bool(res.get(k)) for k in required}
    res["all_required_ok"] = all(res["required_ok"].values())
    out_json = os.path.join(OUT, f"result-{FW}.json")
    json.dump(res, open(out_json, "w"), indent=1)
    print("RESULT", json.dumps(res, indent=1), flush=True)
    sys.exit(0 if res["all_required_ok"] else 1)


if __name__ == "__main__":
    main()
