#!/usr/bin/env python3
"""Headless QEMU boot smoke test for the Moor Linux ISO.

Boots the ISO (BIOS or UEFI) with no display, takes QMP `screendump`s at the
given offsets, converts them to PNG (ImageMagick `convert`), and keeps the
serial + QEMU logs. Uses KVM when /dev/kvm is usable, TCG otherwise.
Derived from the local test harness used for build 1 (QMP screendump).
No input is sent: the image autologins into GNOME.
"""
import argparse, json, os, shutil, socket, subprocess, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("--iso", required=True)
ap.add_argument("--firmware", choices=["bios", "uefi"], required=True)
ap.add_argument("--out", default="screenshots")
ap.add_argument("--duration", type=int, default=240)
ap.add_argument("--shots", default="60,120,180,240")
ap.add_argument("--mem", default="4096")
ap.add_argument("--smp", default=str(min(4, os.cpu_count() or 2)))
ap.add_argument("--accel", choices=["auto", "kvm", "tcg"], default="auto")
a = ap.parse_args()

out = os.path.abspath(a.out); os.makedirs(out, exist_ok=True)
run = os.path.join(out, f"run-{a.firmware}"); os.makedirs(run, exist_ok=True)
qmp_path = os.path.join(run, "qmp.sock")
if os.path.exists(qmp_path): os.unlink(qmp_path)

kvm_ok = os.access("/dev/kvm", os.R_OK | os.W_OK)
accel = ("kvm" if kvm_ok else "tcg") if a.accel == "auto" else a.accel
cmd = ["qemu-system-x86_64", "-name", f"moor-{a.firmware}", "-m", a.mem, "-smp", a.smp,
       "-machine", "q35", "-device", "virtio-vga", "-display", "none",
       "-usb", "-device", "usb-tablet", "-nic", "user,model=virtio-net-pci",
       "-cdrom", os.path.abspath(a.iso), "-boot", "d",
       "-qmp", f"unix:{qmp_path},server=on,wait=off",
       "-serial", f"file:{os.path.join(run, 'serial.log')}"]
cmd += (["-accel", "kvm", "-cpu", "host"] if accel == "kvm"
        else ["-accel", "tcg,thread=multi,tb-size=512", "-cpu", "max"])

if a.firmware == "uefi":
    code = next((p for p in ["/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_CODE.fd"]
                 if os.path.exists(p)), None)
    vars_src = next((p for p in ["/usr/share/OVMF/OVMF_VARS_4M.fd", "/usr/share/OVMF/OVMF_VARS.fd"]
                     if os.path.exists(p)), None)
    if not code or not vars_src:
        sys.exit("OVMF firmware not found (install the ovmf package)")
    vars_copy = os.path.join(run, "OVMF_VARS.fd"); shutil.copy(vars_src, vars_copy)
    cmd += ["-drive", f"if=pflash,format=raw,readonly=on,file={code}",
            "-drive", f"if=pflash,format=raw,file={vars_copy}"]

print("QEMU:", " ".join(cmd), flush=True)
open(os.path.join(run, "qemu-cmd.txt"), "w").write(" ".join(cmd) + "\n")
t0 = time.time()
proc = subprocess.Popen(cmd, stdout=open(os.path.join(run, "qemu.out"), "w"), stderr=subprocess.STDOUT)

for _ in range(200):
    if os.path.exists(qmp_path) or proc.poll() is not None: break
    time.sleep(0.05)
if proc.poll() is not None:
    print(open(os.path.join(run, "qemu.out")).read()); sys.exit("QEMU exited early")

class QMP:
    def __init__(s, path):
        s.s = socket.socket(socket.AF_UNIX); s.s.connect(path); s.buf = b""
        s._line(); s.cmd("qmp_capabilities")
    def _line(s):
        while b"\n" not in s.buf:
            d = s.s.recv(65536)
            if not d: raise EOFError("qmp closed")
            s.buf += d
        line, s.buf = s.buf.split(b"\n", 1)
        return json.loads(line)
    def cmd(s, name, **args):
        msg = {"execute": name}
        if args: msg["arguments"] = args
        s.s.sendall((json.dumps(msg) + "\n").encode())
        while True:
            r = s._line()
            if "return" in r or "error" in r: return r

q = QMP(qmp_path)
shots = sorted(int(x) for x in a.shots.split(",") if x.strip())
taken = []
for t in shots:
    delay = t0 + t - time.time()
    if delay > 0: time.sleep(delay)
    if proc.poll() is not None:
        print(f"QEMU exited before t={t}s"); break
    ppm = os.path.join(run, f"{a.firmware}-{t:03d}s.ppm")
    png = os.path.join(out, f"{a.firmware}-{accel}-{t:03d}s.png")
    r = q.cmd("screendump", filename=ppm)
    for _ in range(100):
        if os.path.exists(ppm) and os.path.getsize(ppm) > 0: break
        time.sleep(0.05)
    time.sleep(0.2)
    if shutil.which("convert") and subprocess.run(["convert", ppm, png]).returncode == 0:
        os.unlink(ppm); taken.append(png)
    else:
        taken.append(ppm)
    print(f"t={int(time.time() - t0)}s screendump -> {taken[-1]} {r.get('error', '')}", flush=True)

remaining = t0 + a.duration - time.time()
if remaining > 0 and proc.poll() is None: time.sleep(remaining)
try:
    q.cmd("quit")
except Exception:
    pass
try:
    proc.wait(15)
except subprocess.TimeoutExpired:
    proc.kill()
if os.path.exists(qmp_path): os.unlink(qmp_path)

res = {"firmware": a.firmware, "accel": accel, "mem_mb": int(a.mem), "screenshots": [os.path.basename(p) for p in taken]}
json.dump(res, open(os.path.join(out, f"result-{a.firmware}.json"), "w"), indent=1)
print("RESULT", json.dumps(res))
sys.exit(0 if taken else 1)
