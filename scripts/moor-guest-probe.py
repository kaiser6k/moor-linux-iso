#!/usr/bin/env python3
"""Log in on the script-test serial pty and check the Xfce session.

Used by script-test only. The VPS setup script is vps-setup-nested-vm.sh.
"""
import os
import re
import subprocess
import sys
import time

ANSI = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def clean(line):
    return ANSI.sub("", line).strip()


def main():
    if len(sys.argv) != 4:
        sys.exit("usage: moor-guest-probe.py SERIAL_LOG IDLE OUT")
    log_path, idle, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    env = os.environ.copy()
    env["LIBVIRT_DEFAULT_URI"] = "qemu:///system"

    def logtext():
        with open(log_path, "rb") as handle:
            return handle.read().decode("utf-8", "replace")

    def lines():
        return [clean(line) for line in logtext().splitlines()]

    tty = ""
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            tty = subprocess.check_output(
                ["virsh", "ttyconsole", "moor"], text=True, env=env
            ).strip()
        except subprocess.CalledProcessError:
            tty = ""
        if tty.startswith("/dev/"):
            break
        time.sleep(1)
    if not tty.startswith("/dev/"):
        sys.exit("virsh ttyconsole did not return a pty")
    fd = os.open(tty, os.O_RDWR | os.O_NOCTTY)

    def send(line):
        os.write(fd, (line + "\r").encode())

    send("")
    time.sleep(0.3)
    send("moor")
    deadline = time.time() + 30
    while time.time() < deadline and not any("Password:" in line for line in lines()):
        time.sleep(0.5)
    send("live")
    deadline = time.time() + 30
    while time.time() < deadline:
        if any(line.endswith("$") or line.endswith("#") or "@moor" in line for line in lines()):
            break
        time.sleep(0.5)

    xfce = False
    deadline = time.time() + 180
    while time.time() < deadline:
        send("pgrep -x xfce4-session >/dev/null; echo XFCE_STATUS:$?")
        time.sleep(5)
        statuses = [
            line for line in lines()
            if line.startswith("XFCE_STATUS:") and line[12:].isdigit()
        ]
        if statuses and statuses[-1] == "XFCE_STATUS:0":
            xfce = True
            break
    if not xfce:
        sys.exit("xfce4-session is not running")
    print("xfce4-session is running")

    used = ""
    if idle == "1":
        time.sleep(120)
        send("free -m | awk '/^Mem:/ { print \"USED_MIB:\" $3 }'")
        deadline = time.time() + 20
        while time.time() < deadline:
            for line in lines():
                if line.startswith("USED_MIB:") and line[9:].isdigit():
                    used = line.split(":", 1)[1]
            if used:
                break
            time.sleep(1)
        if not used:
            sys.exit("free -m did not report used memory")
        print("MOOR_USED_MIB=%s" % used)

    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("xfce4-session=yes\nused_mib=%s\n" % used)


if __name__ == "__main__":
    main()
