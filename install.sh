#!/usr/bin/env bash
# Instaluje gotowy pakiet .deb z katalogu dist/ (to samo, co dwuklik na pliku .deb w Discover).
set -e
DEB=$(ls -t "$(dirname "$0")"/dist/cheerdots_*_all.deb 2>/dev/null | head -1)
[ -n "$DEB" ] || { echo "Brak pakietu w dist/. Zbuduj go: bash packaging/build-deb.sh"; exit 1; }
sudo apt-get install -y "$(realpath "$DEB")"
