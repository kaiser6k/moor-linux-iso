#!/bin/sh
# Build the Moor web desktop (kaiser6k/moor-linux, MIT) as a static SPA and
# stage it into live/config/includes.chroot/opt/moor/www (served by nginx).
# Needs: git, curl, xz-utils, rsync. Uses Node 22 (downloaded if the system
# node is missing or older than 22).
# Env overrides: WORK_DIR, CHROOT_INC, MOOR_REPO, MOOR_COMMIT, NODE_VERSION.
set -eu
. "$(dirname "$0")/lib.sh"

MOOR_REPO="${MOOR_REPO:-https://github.com/kaiser6k/moor-linux.git}"
MOOR_COMMIT="${MOOR_COMMIT:-025662677527f38ce4d61650058ff32f29365927}"
NODE_VERSION="${NODE_VERSION:-22.23.3}"
SRC="$WORK_DIR/moor-linux"

# --- Node 22 ---
need_node=1
if command -v node >/dev/null 2>&1; then
  major=$(node -p 'process.versions.node.split(".")[0]')
  [ "$major" -ge 22 ] && need_node=0
fi
if [ "$need_node" = 1 ]; then
  ND="$WORK_DIR/node-v$NODE_VERSION-linux-x64"
  if [ ! -x "$ND/bin/node" ]; then
    log "downloading Node v$NODE_VERSION"
    curl -fsSL --retry 5 "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz" \
      | tar -xJ -C "$WORK_DIR"
  fi
  PATH="$ND/bin:$PATH"; export PATH
fi
log "node $(node --version), npm $(npm --version)"

# --- source at the pinned commit + ISO patch ---
fetch_pinned "$MOOR_REPO" "$MOOR_COMMIT" "$SRC"
git -C "$SRC" checkout -q -- . 2>/dev/null || true
git -C "$SRC" apply --whitespace=nowarn "$REPO_ROOT/patches/moor-iso-changes.diff"

# `npm ci` fails upstream at this commit (package-lock.json out of sync with
# package.json: ajv 6 vs 8 and friends), so use `npm install`.
log "npm install"
( cd "$SRC" && npm install --no-audit --no-fund )

log "vite build (MOOR_ISO_STATIC=1)"
( cd "$SRC" && rm -rf dist && \
  MOOR_ISO_STATIC=1 VITE_AUTH_ENABLED=false VITE_MOOR_ISO=true \
  node scripts/with-app-env.mjs npx vite build )

OUT="$SRC/dist/client"
test -f "$OUT/_shell.html"
cp "$REPO_ROOT/iso-overrides/linux-vm.html" "$OUT/linux-vm.html"   # no CheerpX
cp "$OUT/_shell.html" "$OUT/index.html"
if grep -rqi leaningtech "$OUT"; then
  echo "ERROR: CheerpX (leaningtech) reference found in the static build" >&2; exit 1
fi

WWW="$CHROOT_INC/opt/moor/www"
mkdir -p "$WWW"
rsync -a --delete "$OUT/" "$WWW/"

# Launcher icon, wallpaper, license (taken from the same Moor source tree).
install -D -m 644 "$SRC/public/brand/icon-256.png" "$CHROOT_INC/usr/share/icons/hicolor/256x256/apps/moor.png"
install -D -m 644 "$SRC/public/brand/icon-512.png" "$CHROOT_INC/usr/share/icons/hicolor/512x512/apps/moor.png"
install -D -m 644 "$SRC/public/wallpapers/fjord.jpg" "$CHROOT_INC/usr/share/backgrounds/moor/fjord.jpg"
install -D -m 644 "$SRC/LICENSE" "$CHROOT_INC/usr/share/doc/moor-linux/copyright"

du -sh "$WWW"
log "Moor static build staged into $WWW (moor-linux @ $MOOR_COMMIT)"
