#!/usr/bin/env bash
# Publikacja projektu na GitHubie: github.com/Trivetto/cheerdots2-linux-driver
#   bash dev/publish-github.sh          (z katalogu projektu albo skad bądź)
# Robi: porzadki w dev/, git init + pierwszy commit, utworzenie repo (gh), wyslanie, tag v<wersja>
# (tag uruchamia GitHub Actions, ktore buduja .deb i dolaczaja go do Releases).
set -euo pipefail
OWNER=Trivetto
REPO=cheerdots2-linux-driver
DESC="Two-finger gestures, battery & per-app profiles for the CheerDots 2 (CHP04) mouse on Linux / KDE Plasma – via system BlueZ (ExchangeMTU 185)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VER=$(python3 -c "import cheerdots; print(cheerdots.__version__)")
echo "=== $OWNER/$REPO  (wersja $VER)  w $ROOT"

need() { command -v "$1" >/dev/null || { echo "--> instaluje $2"; sudo apt-get install -y "$2"; }; }
need git git
need gh gh

echo "--> porzadki"
mkdir -p dev/research dev/logs dev/archive
for f in dev/test_*.sh; do [ -e "$f" ] && mv -n "$f" dev/research/; done
for f in dev/test_*.log dev/test_*.txt dev/test_*.btsnoop; do [ -e "$f" ] && mv -n "$f" dev/logs/; done
for f in assets/picker.png assets/screenshot.png; do [ -e "$f" ] && mv -n "$f" dev/archive/; done   # zrzuty starej wersji
rm -f packaging/files/bt-prepare packaging/files/bt-restore
rm -rf cheerdots/__pycache__ tests/__pycache__
mkdir -p .github/workflows && cp -f dev/ci-build.yml .github/workflows/build.yml     # budowanie .deb na GitHubie

echo "--> testy"
python3 -m unittest discover -s tests >/dev/null 2>&1 && echo "   testy OK" || { echo "!!! testy nie przeszly"; python3 -m unittest discover -s tests; exit 1; }

echo "--> logowanie do GitHuba"
if ! gh auth status >/dev/null 2>&1; then
  echo "   Wybierz: GitHub.com -> HTTPS -> Yes -> Login with a web browser"
  gh auth login
fi
GHUSER=$(gh api user --jq .login)
GHID=$(gh api user --jq .id)
[ "$GHUSER" = "$OWNER" ] || { echo "!!! Zalogowano jako '$GHUSER', a repo ma byc na koncie '$OWNER'. Przerwij (Ctrl+C) albo Enter, zeby kontynuowac."; read -r; }

echo "--> git"
git config --global --add safe.directory "$ROOT" 2>/dev/null || true
[ -d .git ] || git init -q -b main
git config core.fileMode false          # udzial sieciowy (CIFS) nie przechowuje bitow uprawnien
git config core.autocrlf false
git config user.name "$GHUSER"
git config user.email "${GHID}+${GHUSER}@users.noreply.github.com"    # adres prywatny GitHuba (bez Twojego e-maila)
git add -A
for f in cheerdots-ctl install.sh uninstall.sh packaging/build-deb.sh packaging/debian/preinst packaging/debian/postinst \
         packaging/debian/prerm packaging/debian/postrm packaging/files/bluez-mtu packaging/files/cheerdots-daemon \
         packaging/files/cheerdots-gui dev/publish-github.sh dev/research/*.sh; do
  [ -e "$f" ] && git update-index --chmod=+x "$f"
done
echo "   pliki w repo:"; git status --short | sed 's/^/     /'
if git rev-parse -q --verify HEAD >/dev/null; then
  git diff --cached --quiet || git commit -q -m "Update to $VER"
else
  git commit -q -F - <<EOF
CheerDots 2 Linux driver $VER

Two-finger gestures for the CheerDots 2 (CHP04) mouse on Linux using the
system BlueZ stack: the firmware enables gestures only for hosts requesting
ATT MTU 527 (Windows) or 185 (macOS), so the package sets ExchangeMTU = 185.
Includes daemon (evdev grab + uinput), tray GUI with per-app profiles,
double and button gestures, Debian packaging, tests and CI.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HK1icAMv5c8eZQ3CHz2662
EOF
fi

echo "--> repozytorium na GitHubie"
if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/$REPO.git"
  git push -u origin main
else
  gh repo create "$OWNER/$REPO" --public --description "$DESC" --source=. --remote=origin --push
fi
gh repo edit "$OWNER/$REPO" --add-topic cheerdots,linux,kde-plasma,bluetooth,bluez,mouse,gestures,driver >/dev/null 2>&1 || true

echo "--> tag v$VER (GitHub Actions zbuduje .deb i doda go do Releases)"
if ! git rev-parse -q --verify "refs/tags/v$VER" >/dev/null; then
  git tag -a "v$VER" -m "CheerDots 2 Linux driver $VER"
fi
git push origin "v$VER" 2>/dev/null || echo "   (tag juz byl wyslany)"

echo
echo "Gotowe: https://github.com/$OWNER/$REPO"
echo "Budowanie paczki: https://github.com/$OWNER/$REPO/actions  (ok. 1 min, potem plik w Releases)"
