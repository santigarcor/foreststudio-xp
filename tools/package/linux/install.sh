#!/bin/sh
# Add ForestStudio XP to your applications menu (current user only, no root).
#   ./install.sh            add the menu entry and icon
#   ./install.sh --remove   remove them again
# The program stays in this folder: move the folder first if you want it elsewhere, then run this.
# Your data folder (~/.foreststudio) is never touched.
set -e
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP="$DATA/applications/foreststudio-xp.desktop"
ICON="$DATA/icons/hicolor/256x256/apps/foreststudio-xp.png"

if [ "$1" = "--remove" ]; then
  rm -f "$DESKTOP" "$ICON"
  echo "Removed the menu entry. Delete this folder to remove the program."
  exit 0
fi

mkdir -p "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cp "$HERE/app/forest/app/icons/foreststudio-xp-256.png" "$ICON"
cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=ForestStudio XP
Comment=X-Plane 12 forest scenery builder
Exec="$HERE/foreststudio-xp"
Icon=foreststudio-xp
Terminal=false
Categories=Utility;Graphics;
StartupWMClass=foreststudio-xp
DESK
chmod +x "$DESKTOP"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" || true
echo "ForestStudio XP added to your applications menu."
