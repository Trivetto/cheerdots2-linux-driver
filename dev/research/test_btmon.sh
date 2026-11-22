#!/usr/bin/env bash
# Test G: nagranie tego, co BlueZ wysyla do myszki (btmon), zeby porownac z Bumble.
#   sudo bash test_btmon.sh          -> nagrywanie (Ctrl+C = koniec, pliki zapisuja sie obok)
#   sudo bash test_bluez.sh restore  -> potem powrot do naszego sterownika
set -u
[ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo"; exit 1; }
command -v btmon >/dev/null || { echo "Brak btmon: sudo apt install bluez"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
T=$(date +%H%M%S)
TXT="$HERE/test_G_$T.txt"; SNOOP="$HERE/test_G_$T.btsnoop"

echo "--> zatrzymuje sterownik cheerdots, oddaje adapter systemowi"
systemctl stop cheerdots.service
systemctl unmask --runtime bluetooth >/dev/null 2>&1; systemctl unmask bluetooth >/dev/null 2>&1
for f in /run/systemd/system/bluetooth.service /etc/systemd/system/bluetooth.service; do [ "$(readlink "$f" 2>/dev/null)" = /dev/null ] && rm -f "$f"; done
systemctl daemon-reload
modprobe uhid 2>/dev/null
systemctl restart bluetooth; sleep 3
bluetoothctl power on >/dev/null

btmon -w "$SNOOP" >/dev/null 2>&1 &  BT1=$!
btmon -t > "$TXT" 2>&1 &             BT2=$!
trap 'kill $BT1 $BT2 2>/dev/null; sleep 1; chmod a+rw "$TXT" "$SNOOP" 2>/dev/null; echo; echo "Zapisano: $TXT"; echo "Powrot: sudo bash $HERE/test_bluez.sh restore"' EXIT
sleep 1

ADDR=$(bluetoothctl devices Paired 2>/dev/null | grep -i cheerdots | head -1 | awk '{print $2}')
if [ -n "$ADDR" ]; then
  echo ">>> Myszka jest sparowana ($ADDR). WYLACZ ja teraz."
  bluetoothctl disconnect "$ADDR" >/dev/null 2>&1
  for i in $(seq 60); do
    bluetoothctl info "$ADDR" | grep -q "Connected: yes" || break; sleep 1
  done
  sleep 3
  echo ">>> Rozlaczona. Teraz WLACZ myszke."
else
  echo ">>> Myszka nie jest sparowana. Sparuj ja TERAZ w Ustawieniach KDE -> Bluetooth (tryb parowania w myszce)."
fi
echo ">>> Czekam na polaczenie (do 2 min)..."
for i in $(seq 120); do
  sleep 1
  ADDR=$(bluetoothctl devices Connected 2>/dev/null | grep -i cheerdots | head -1 | awk '{print $2}')
  [ -n "$ADDR" ] && break
done
[ -n "$ADDR" ] || { echo "!!! Myszka sie nie polaczyla"; exit 1; }
echo "polaczono: $ADDR - czekam 15 s az BlueZ skonczy konfiguracje..."
sleep 15
echo
echo ">>> TERAZ: gesty 2 palcami po 3 razy: lewo, prawo, gora, dol. Potem Ctrl+C."
while true; do sleep 1; done
