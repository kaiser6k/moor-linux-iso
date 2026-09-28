#!/bin/sh
# Collected over the virtio-9p share while the graphical session is already up.
# free -m runs before smem/ps_mem/the PSS walk so those tools are not in "used".
out=/mnt/probe/result.txt
{
  echo '===BEGIN==='
  echo '===WHOAMI==='
  whoami || true
  echo '===SESSION==='
  ps -eo user,pid,comm | grep -E 'gnome-shell|xfce4-session|xfwm4|plank|gdm|lightdm' | grep -v grep || true
  echo '===OS==='
  cat /etc/os-release || true
  echo '===FREE==='
  free -m || true
  echo '===MEMTOTAL==='
  awk '/^MemTotal:/ {print $2}' /proc/meminfo || true
  echo '===TOP==='
  ps -eo rss,pid,user,comm --sort=-rss | head -n 16 || true
  echo '===IP==='
  ip -4 -br addr || true
  echo '===SMEM==='
  if command -v smem >/dev/null 2>&1; then
    smem -tk || true
  else
    echo 'smem not installed'
  fi
  echo '===PS_MEM==='
  if [ -f /mnt/probe/ps_mem.py ] && command -v python3 >/dev/null 2>&1; then
    python3 /mnt/probe/ps_mem.py || true
  else
    echo 'ps_mem unavailable'
  fi
  echo '===PSS==='
  total=0
  list=/tmp/moor-pss-list
  : > "$list"
  for f in /proc/[0-9]*/smaps_rollup; do
    pid=${f#/proc/}
    pid=${pid%%/*}
    pss=$(awk '/^Pss:/ {print $2; exit}' "$f" 2>/dev/null) || continue
    case "$pss" in
      ''|*[!0-9]*) continue ;;
    esac
    comm=$(tr -d '\0' < "/proc/$pid/comm" 2>/dev/null || echo '?')
    printf '%s %s %s\n' "$pss" "$pid" "$comm" >> "$list"
    total=$((total + pss))
  done
  sort -nr "$list" | head -n 20 || true
  echo "PSS_TOTAL_KIB $total"
  echo '===END==='
} 2>&1 | tee "$out"
sync
