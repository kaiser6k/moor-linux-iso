# Shared helpers for the staging scripts (sourced, not executed).
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
WORK_DIR="${WORK_DIR:-$REPO_ROOT/.work}"
CHROOT_INC="${CHROOT_INC:-$REPO_ROOT/live/config/includes.chroot}"
mkdir -p "$WORK_DIR"

log() { printf '==> %s\n' "$*"; }

# fetch_pinned <url> <commit> <dir>: shallow-fetch exactly one commit.
fetch_pinned() {
  url=$1; rev=$2; dir=$3
  if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$rev" ]; then
    log "$dir already at $rev"; return 0
  fi
  rm -rf "$dir"; mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$url"
  if ! git -C "$dir" fetch -q --depth 1 origin "$rev"; then
    log "shallow fetch by SHA failed, falling back to full fetch"
    git -C "$dir" fetch -q origin
  fi
  git -C "$dir" -c advice.detachedHead=false checkout -q "$rev"
  test "$(git -C "$dir" rev-parse HEAD)" = "$rev"
  log "$url @ $rev"
}
