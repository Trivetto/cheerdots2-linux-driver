#!/usr/bin/env bash
# Test M: zwykly Bluetooth (BlueZ) z ExchangeMTU = 23 -> BlueZ nie powinien wymieniac MTU,
# wiec myszka nie rozpozna Linuksa i powinna wysylac gesty lewo/prawo/gora.
#   sudo bash test_mtu_bluez.sh          -> test
#   sudo bash test_mtu_bluez.sh restore  -> przywraca main.conf i nasz sterownik
set -u
[ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
CONF=/etc/bluetooth/main.conf
BAK=/etc/bluetooth/main.conf.cheerdots-bak

setmtu() {
python3 - "$CONF" "$1" <<'PY'
import sys, re
path, val = sys.argv[1], sys.argv[2]
try:
    lines = open(path).read().split("\n")
except FileNotFoundError:
    lines = []
out, in_gatt, done, has_gatt = [], False, False, False
for l in lines:
    s = l.strip()
    if s.startswith("["):
        if in_gatt and not done:
            out.append(f"ExchangeMTU = {val}"); done = True
        in_gatt = (s == "[GATT]"); has_gatt |= in_gatt
    if in_gatt and re.match(r"#?\s*ExchangeMTU\s*=", s):
        if not done:
            out.append(f"ExchangeMTU = {val}"); done = True
        continue
    out.append(l)
if in_gatt and not done:
    out.append(f"ExchangeMTU = {val}"); done = True
if not has_gatt:
    out += ["", "[GATT]", f"ExchangeMTU = {val}"]
open(path, "w").write("\n".join(out))
PY
}

if [ "${1:-}" = "restore" ]; then
  [ -f "$BAK" ] && cp -p "$BAK" "$CONF" && echo "przywrocono $CONF"
  bash "$HERE/test_bluez.sh" restore
  exit 0
fi

[ -f "$BAK" ] || cp -p "$CONF" "$BAK" 2>/dev/null
setmtu "${1:-23}"
echo "--> $CONF:"; grep -n -A3 "^\[GATT\]" "$CONF" | grep -E "GATT|ExchangeMTU"

systemctl stop cheerdots.service
systemctl unmask --runtime bluetooth >/dev/null 2>&1; systemctl unmask bluetooth >/dev/null 2>&1
for f in /run/systemd/system/bluetooth.service /etc/systemd/system/bluetooth.service; do [ "$(readlink "$f" 2>/dev/null)" = /dev/null ] && rm -f "$f"; done
systemctl daemon-reload; modprobe uhid 2>/dev/null
systemctl restart bluetooth; sleep 3; bluetoothctl power on >/dev/null

# stare parowanie jest nieaktualne (myszka byla potem parowana z naszym sterownikiem) - usun je
bluetoothctl devices 2>/dev/null | grep -i cheerdots | awk '{print $2}' | while read -r A; do
  bluetoothctl remove "$A" >/dev/null 2>&1 && echo "usunieto stare parowanie: $A"; done
sleep 1
ADDR=""
if [ -z "$ADDR" ]; then
  echo
  echo ">>> Myszka nie jest sparowana z systemem. Sparuj ja TERAZ w Ustawieniach KDE -> Bluetooth"
  echo ">>> (tryb parowania w myszce). Czekam do 2 min..."
  for i in $(seq 120); do
    ADDR=$(bluetoothctl devices Paired 2>/dev/null | grep -i cheerdots | head -1 | awk '{print $2}')
    [ -n "$ADDR" ] && break; sleep 1
  done
  [ -n "$ADDR" ] || { echo "!!! Nie sparowano"; exit 1; }
  echo "sparowano: $ADDR"; sleep 5
fi
bash "$HERE/test_bluez.sh" skip
echo
echo "Sprawdzenie MTU w nagraniu:"
LAST=$(ls -t "$HERE"/test_F_*_btmon.txt 2>/dev/null | head -1)
[ -n "$LAST" ] && { grep -c "Exchange MTU Request" "$LAST" | sed 's/^/  wymian MTU: /'; grep -A1 "Exchange MTU Request" "$LAST" | grep "Client RX" | sort | uniq -c; }
echo "Powrot do naszego sterownika: sudo bash $HERE/test_mtu_bluez.sh restore"
