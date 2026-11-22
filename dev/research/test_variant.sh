#!/usr/bin/env bash
# Testy: ktory element "udawania" Windows/Mac jest potrzebny, zeby myszka wysylala gesty.
# Wariant uruchamiamy przez zwykla usluge (te same kroki startu co na co dzien), z dodatkowymi opcjami.
#   sudo bash test_variant.sh A|B|C      -> uruchamia wariant i pokazuje log na zywo (Ctrl+C = koniec)
#   sudo bash test_variant.sh normal     -> powrot do zwyklego dzialania (robi sie tez samo po Ctrl+C)
#   A = AKCEPTUJ prosby o parametry polaczenia (reszta jak Windows)      -> wylacz/wlacz myszke
#   B = bez MTU 527, bez zapisu LED, bez odczytow GATT (reszta jak Windows) -> wylacz/wlacz myszke
#   C = parowanie jak Linux (DisplayYesNo, klucze bez IRK)               -> TRYB PAROWANIA w myszce
#   D = A+B+C naraz (wszystko jak Linux poza parametrami polaczenia)     -> TRYB PAROWANIA w myszce
#   E = D + parametry polaczenia jak BlueZ (30-50 ms, bez zmiany potem)  -> TRYB PAROWANIA w myszce
#   J = I + LE Read Remote Features + Authenticated Payload Timeout (jak BlueZ); K = tylko features; L = tylko ping
#   R = J + dokladnie te same odczyty/zapisy GATT co BlueZ (opcjonalnie lista krokow: mtu,sfeat,disc,gap,pnp,hid,cccd,dis)
#   I = D + od razu parametry, ktore BlueZ ustawia po odczycie z myszki (7.5 ms, latency 60, 6 s)
set -u
[ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo"; exit 1; }
command -v cheerdots-daemon >/dev/null || { echo "Najpierw zainstaluj pakiet cheerdots (.deb)"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"

# najnowszy demon z folderu projektu (jesli nowszy niz w pakiecie)
[ -f "$HERE/../cheerdots/daemon.py" ] && install -m 0644 "$HERE/../cheerdots/daemon.py" /usr/lib/cheerdots/cheerdots/daemon.py
[ -f "$HERE/../packaging/files/bt-prepare" ] && install -m 0755 "$HERE/../packaging/files/bt-prepare" /usr/lib/cheerdots/bt-prepare

normal() { echo 'CHEERDOTS_ARGS=' > /etc/default/cheerdots; systemctl restart cheerdots.service; echo "Zwykle dzialanie przywrocone."; }

case "${1:-}" in
  A) ARGS="--no-reject";                                MSG="WYLACZ myszke, odczekaj 3 s, WLACZ." ;;
  B) ARGS="--mtu 0 --no-led --no-reads";                MSG="WYLACZ myszke, odczekaj 3 s, WLACZ." ;;
  C) ARGS="--io-cap display_yesno --keydist linux --keystore /var/lib/cheerdots/test_keys.json"
     rm -f /var/lib/cheerdots/test_keys.json;           MSG="Wlacz w myszce TRYB PAROWANIA." ;;
  D) ARGS="--no-reject --mtu 0 --no-led --no-reads --io-cap display_yesno --keydist linux --keystore /var/lib/cheerdots/test_keys.json"
     rm -f /var/lib/cheerdots/test_keys.json;           MSG="Wlacz w myszce TRYB PAROWANIA." ;;
  E) ARGS="--no-reject --mtu 0 --no-led --no-reads --io-cap display_yesno --keydist linux --keystore /var/lib/cheerdots/test_keys.json --linux-conn"
     rm -f /var/lib/cheerdots/test_keys.json;           MSG="Wlacz w myszce TRYB PAROWANIA." ;;
  I) ARGS="--no-reject --mtu 0 --no-led --no-reads --io-cap display_yesno --keydist linux --keystore /var/lib/cheerdots/test_keys.json --conn 7.5,7.5,60,6000"
     rm -f /var/lib/cheerdots/test_keys.json;           MSG="Wlacz w myszce TRYB PAROWANIA." ;;
  J) ARGS="--no-reject --mtu 0 --no-led --no-reads --io-cap display_yesno --keydist linux --keystore /var/lib/cheerdots/test_keys.json --conn 7.5,7.5,60,6000 --ll-features --ping"
     rm -f /var/lib/cheerdots/test_keys.json;           MSG="Wlacz w myszce TRYB PAROWANIA." ;;
  K) ARGS="--no-reject --mtu 0 --no-led --no-reads --io-cap display_yesno --keydist linux --keystore /var/lib/cheerdots/test_keys.json --conn 7.5,7.5,60,6000 --ll-features"
     rm -f /var/lib/cheerdots/test_keys.json;           MSG="Wlacz w myszce TRYB PAROWANIA." ;;
  L) ARGS="--no-reject --mtu 0 --no-led --no-reads --io-cap display_yesno --keydist linux --keystore /var/lib/cheerdots/test_keys.json --conn 7.5,7.5,60,6000 --ping"
     rm -f /var/lib/cheerdots/test_keys.json;           MSG="Wlacz w myszce TRYB PAROWANIA." ;;
  R) ARGS="--no-reject --mtu 0 --no-led --no-reads --io-cap display_yesno --keydist linux --keystore /var/lib/cheerdots/test_keys.json --conn 7.5,7.5,60,6000 --ll-features --ping --bluez-replay ${2:-mtu,sfeat,disc,gap,pnp,hid,cccd,dis}"
     rm -f /var/lib/cheerdots/test_keys.json;           MSG="Wlacz w myszce TRYB PAROWANIA." ;;
  normal) normal; exit 0 ;;
  *) echo "uzycie: sudo bash test_variant.sh A|B|C|D|E|I|J|K|L|R [kroki]|normal"; exit 1 ;;
esac
grep -q '"address"' /etc/cheerdots/config.json 2>/dev/null || { echo "Najpierw wybierz myszke w GUI (CheerDots 2)"; exit 1; }

LOG="$HERE/test_$1_$(date +%H%M%S).log"
echo "CHEERDOTS_ARGS=$ARGS" > /etc/default/cheerdots
START=$(date '+%Y-%m-%d %H:%M:%S')
BTM="${LOG%.log}_btmon.txt"
command -v btmon >/dev/null && { btmon -t > "$BTM" 2>&1 & BTMON=$!; }
sleep 1
systemctl restart cheerdots.service
trap 'echo; kill ${BTMON:-0} 2>/dev/null; chmod a+rw "$BTM" 2>/dev/null; journalctl -u cheerdots --since "$START" --no-pager -o cat > "$LOG"; chmod a+rw "$LOG"; normal; echo "Log: $LOG"' EXIT

echo "=== Wariant $1: $ARGS"
echo ">>> Poczekaj na 'adapter ... przejety', potem: $MSG"
echo ">>> Po 'polaczono z ...' odczekaj 10-15 s i zrob gesty dwoma palcami. Patrz na linie 'gest ...'."
echo ">>> Ctrl+C = koniec testu."
echo
journalctl -u cheerdots -f -n 0 -o cat
