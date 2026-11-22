#!/usr/bin/env bash
# Buduje pakiet .deb:  bash packaging/build-deb.sh   ->  dist/cheerdots_<wersja>_all.deb
# Wymaga: dpkg-deb.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VER=$(python3 -c "import sys; sys.path.insert(0,'$ROOT'); import cheerdots; print(cheerdots.__version__)")
B=$(mktemp -d)/pkg
trap 'rm -rf "$(dirname "$B")"' EXIT

echo "==> wersja $VER"
install -d "$B/DEBIAN" "$B/usr/lib/cheerdots/cheerdots" "$B/usr/bin" "$B/usr/lib/systemd/system" \
           "$B/usr/share/applications" "$B/etc/xdg/autostart" "$B/usr/share/icons/hicolor/scalable/apps" \
           "$B/usr/lib/modules-load.d" "$B/usr/share/doc/cheerdots" "$B/usr/share/polkit-1/rules.d"

install -m 0644 "$ROOT"/cheerdots/*.py "$B/usr/lib/cheerdots/cheerdots/"
install -m 0755 "$ROOT/packaging/files/bluez-mtu" "$B/usr/lib/cheerdots/"
install -m 0755 "$ROOT/packaging/files/cheerdots-daemon" "$ROOT/packaging/files/cheerdots-gui" "$B/usr/bin/"
install -m 0755 "$ROOT/cheerdots-ctl" "$B/usr/bin/cheerdots-ctl"
install -m 0644 "$ROOT/packaging/files/cheerdots.service" "$B/usr/lib/systemd/system/"
install -m 0644 "$ROOT/packaging/files/cheerdots.desktop" "$B/usr/share/applications/"
install -m 0644 "$ROOT/packaging/files/cheerdots-gui-autostart.desktop" "$B/etc/xdg/autostart/cheerdots-gui.desktop"
install -m 0644 "$ROOT/assets/cheerdots.svg" "$B/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$ROOT/packaging/files/50-cheerdots.rules" "$B/usr/share/polkit-1/rules.d/50-cheerdots.rules"
install -m 0644 "$ROOT/packaging/files/modules-load.conf" "$B/usr/lib/modules-load.d/cheerdots.conf"
install -m 0644 "$ROOT/README.md" "$ROOT/WIEDZA.md" "$B/usr/share/doc/cheerdots/"

find "$B" -name "__pycache__" -prune -exec rm -rf {} +

for f in control preinst postinst prerm postrm; do install -m 0755 "$ROOT/packaging/debian/$f" "$B/DEBIAN/$f"; done
chmod 0644 "$B/DEBIAN/control"
echo "/etc/xdg/autostart/cheerdots-gui.desktop" > "$B/DEBIAN/conffiles"
SIZE=$(du -sk "$B" | cut -f1)
sed -i "s/@VERSION@/$VER/; s/@SIZE@/$SIZE/" "$B/DEBIAN/control"

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/cheerdots_${VER}_all.deb"
dpkg-deb --build --root-owner-group -Zxz "$B" "$OUT" >/dev/null
echo "==> $OUT ($(du -h "$OUT" | cut -f1))"
