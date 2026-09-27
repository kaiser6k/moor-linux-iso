#!/bin/sh
# Fetch WhiteSur (vinceliuice) at pinned commits and install it system-wide
# into live/config/includes.chroot. Needs: git, sassc, libxml2-utils, glib2.0-bin
# (the GTK install.sh checks for sassc/xmllint).
# Env overrides: WORK_DIR, CHROOT_INC, WHITESUR_{GTK,ICON,CURSORS}_COMMIT.
set -eu
. "$(dirname "$0")/lib.sh"

GTK_COMMIT="${WHITESUR_GTK_COMMIT:-d5782652d412137e26fb8ff55b55a5572e4c6995}"
ICON_COMMIT="${WHITESUR_ICON_COMMIT:-73d8040da51a9ed74e47c7366e7e9ff437601a5c}"
CURSORS_COMMIT="${WHITESUR_CURSORS_COMMIT:-e190baf618ed95ee217d2fd45589bd309b37672b}"

SRC="$WORK_DIR/themes-src"
mkdir -p "$SRC"
fetch_pinned https://github.com/vinceliuice/WhiteSur-gtk-theme.git  "$GTK_COMMIT"     "$SRC/WhiteSur-gtk-theme"
fetch_pinned https://github.com/vinceliuice/WhiteSur-icon-theme.git "$ICON_COMMIT"    "$SRC/WhiteSur-icon-theme"
fetch_pinned https://github.com/vinceliuice/WhiteSur-cursors.git    "$CURSORS_COMMIT" "$SRC/WhiteSur-cursors"

THEMES="$CHROOT_INC/usr/share/themes"
ICONS="$CHROOT_INC/usr/share/icons"
SKEL_GTK4="$CHROOT_INC/etc/skel/.config/gtk-4.0"
DOC="$CHROOT_INC/usr/share/doc/whitesur-themes"
rm -rf "$THEMES"/WhiteSur-* "$ICONS"/WhiteSur "$ICONS"/WhiteSur-dark "$ICONS"/WhiteSur-light \
       "$ICONS"/WhiteSur-cursors "$SKEL_GTK4" "$DOC"
mkdir -p "$THEMES" "$ICONS" "$DOC" "$(dirname "$SKEL_GTK4")"

# install.sh enables `set -e` and calls `setterm` for a spinner. With no tty
# (the CI container) setterm fails and aborts the install. A no-op is enough.
mkdir -p "$WORK_DIR/bin"
printf '#!/bin/sh\nexit 0\n' > "$WORK_DIR/bin/setterm"
chmod +x "$WORK_DIR/bin/setterm"
PATH="$WORK_DIR/bin:$PATH"
export PATH

# GTK 2/3/4 + GNOME Shell: dark+light, solid (no transparency). With no
# gnome-shell on the build host the script targets GNOME 48 (trixie ships 48).
# -l installs the libadwaita (GTK4) CSS into $HOME/.config/gtk-4.0, so use a
# throwaway HOME and copy the result into /etc/skel afterwards.
#
# WhiteSur's install.sh refuses -l when UID is 0 (it prints an error and skips
# the CSS). The CI container is root, so drop privileges for that one install.
# The links it writes are absolute paths under $HOME; rewrite them to relative
# names so they still resolve after the tree is copied into the image.
FAKEHOME="$WORK_DIR/fakehome"
rm -rf "$FAKEHOME"; mkdir -p "$FAKEHOME/.config"
log "installing WhiteSur GTK theme"
# `su` resets PATH, so pass the no-op setterm directory explicitly.
gtk_cmd="cd '$SRC/WhiteSur-gtk-theme' && PATH='$PATH' HOME='$FAKEHOME' ./install.sh -d '$THEMES' -c dark -c light -o solid -l"
if [ "$(id -u)" -eq 0 ]; then
  if ! id moorbuild >/dev/null 2>&1; then
    useradd --create-home --user-group --shell /bin/sh moorbuild
  fi
  chown -R moorbuild:moorbuild "$THEMES" "$FAKEHOME" "$SRC/WhiteSur-gtk-theme"
  su -s /bin/sh moorbuild -c "$gtk_cmd"
  chown -R root:root "$THEMES" "$FAKEHOME" "$SRC/WhiteSur-gtk-theme"
else
  ( eval "$gtk_cmd" )
fi
if [ -d "$FAKEHOME/.config/gtk-4.0" ]; then
  rm -rf "$SKEL_GTK4"
  cp -a "$FAKEHOME/.config/gtk-4.0" "$SKEL_GTK4"
  for link in "$SKEL_GTK4"/*; do
    [ -L "$link" ] || continue
    ln -sfn "$(basename "$(readlink "$link")")" "$link"
  done
else
  echo "ERROR: libadwaita gtk-4.0 CSS not produced" >&2
  exit 1
fi

log "installing WhiteSur icon theme"
( cd "$SRC/WhiteSur-icon-theme" && HOME="$FAKEHOME" ./install.sh -d "$ICONS" )

log "installing WhiteSur cursors (prebuilt dist/)"
cp -a "$SRC/WhiteSur-cursors/dist" "$ICONS/WhiteSur-cursors"

cp "$SRC/WhiteSur-gtk-theme/COPYING"  "$DOC/WhiteSur-gtk-theme.COPYING"
cp "$SRC/WhiteSur-icon-theme/COPYING" "$DOC/WhiteSur-icon-theme.COPYING"
cp "$SRC/WhiteSur-cursors/LICENSE"    "$DOC/WhiteSur-cursors.LICENSE"
cat > "$DOC/SOURCES" <<INFO
WhiteSur themes by vinceliuice, installed unmodified from:
https://github.com/vinceliuice/WhiteSur-gtk-theme  $GTK_COMMIT  (MIT)
https://github.com/vinceliuice/WhiteSur-icon-theme $ICON_COMMIT  (GPL-3.0)
https://github.com/vinceliuice/WhiteSur-cursors    $CURSORS_COMMIT  (GPL-3.0)
INFO

ls "$THEMES" "$ICONS"
log "themes staged into $CHROOT_INC"
