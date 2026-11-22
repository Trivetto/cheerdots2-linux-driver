#!/usr/bin/env bash
# Test F: myszka przez ZWYKLY Bluetooth Linuksa (BlueZ), bez naszego sterownika.
# Sprawdza, czy gesty docieraja jako klawisze do systemu (wtedy sterownik nie musi zabierac adaptera).
#   sudo bash test_bluez.sh          -> test (Ctrl+C = koniec, log zapisuje sie obok)
#   sudo bash test_bluez.sh restore  -> powrot do naszego sterownika
set -u
[ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"

forget_bluez() {
  bluetoothctl devices 2>/dev/null | grep -i cheerdots | awk '{print $2}' | while read -r A; do
    bluetoothctl remove "$A" >/dev/null 2>&1 && echo "usunieto z BlueZ: $A"; done
}

if [ "${1:-}" = "restore" ]; then
  forget_bluez
  systemctl start cheerdots.service
  echo "Nasz sterownik uruchomiony. Jesli myszka sie nie laczy: GUI -> Zapomnij -> Wybierz urzadzenie."
  exit 0
fi

LOG="$HERE/test_F_$(date +%H%M%S).log"
exec > >(tee "$LOG") 2>&1
echo "=== Test F (BlueZ) $(date)"
echo "--> zatrzymuje sterownik cheerdots, oddaje adapter systemowi"
systemctl stop cheerdots.service
systemctl unmask --runtime bluetooth >/dev/null 2>&1; systemctl unmask bluetooth >/dev/null 2>&1
for f in /run/systemd/system/bluetooth.service /etc/systemd/system/bluetooth.service; do [ "$(readlink "$f" 2>/dev/null)" = /dev/null ] && rm -f "$f"; done; systemctl daemon-reload
systemctl is-enabled bluetooth 2>&1 | sed "s/^/bluetooth: /"
modprobe uhid 2>/dev/null
modprobe -r btusb 2>/dev/null; sleep 1; modprobe btusb; sleep 3
systemctl restart bluetooth; sleep 3
bluetoothctl power on >/dev/null
bluetoothctl show | grep -E "Controller|Powered|Name:" | head -3
btmon -t > "${LOG%.log}_btmon.txt" 2>&1 &  BTMON=$!
trap ':' INT
if [ "${1:-}" = "skip" ]; then
  ADDR=$(bluetoothctl devices | grep -i cheerdots | head -1 | awk '{print $2}')
  echo ">>> WYLACZ i WLACZ myszke (bez trybu parowania). Czekam na polaczenie do 60 s..."
  for i in $(seq 60); do
    bluetoothctl info "$ADDR" 2>/dev/null | grep -q "Connected: yes" && break
    [ $((i % 15)) -eq 0 ] && timeout 10 bluetoothctl connect "$ADDR" >/dev/null 2>&1
    sleep 1
  done
else
forget_bluez

echo
echo ">>> Wlacz w myszce TRYB PAROWANIA (teraz). Szukam przez 30 s..."
ADDR=""
bluetoothctl --timeout 30 scan le >/dev/null 2>&1 &
for i in $(seq 30); do
  sleep 1
  ADDR=$(bluetoothctl devices | grep -i cheerdots | head -1 | awk '{print $2}')
  [ -n "$ADDR" ] && break
done
[ -n "$ADDR" ] || { echo "!!! Nie znaleziono myszki. Sparuj ja recznie w Ustawieniach KDE -> Bluetooth i uruchom skrypt ponownie z opcja: sudo bash test_bluez.sh skip"; exit 1; }
echo "znaleziono: $(bluetoothctl devices | grep "$ADDR")"
sleep 2
echo "--> parowanie"
timeout 25 bluetoothctl --agent NoInputNoOutput pair "$ADDR" | tail -3
bluetoothctl info "$ADDR" | grep -q "Paired: yes" || { echo "!!! Parowanie sie nie udalo. Sparuj myszke w Ustawieniach KDE -> Bluetooth, potem: sudo bash test_bluez.sh skip"; exit 1; }
bluetoothctl trust "$ADDR" | tail -1
timeout 20 bluetoothctl connect "$ADDR" | tail -2
fi
sleep 3
bluetoothctl info "$ADDR" | grep -E "Name|Paired|Bonded|Connected|Battery|Modalias"

echo
echo "--> szukam urzadzen wejscia myszki"
python3 -u - "$ADDR" <<'PY'
import os, sys, struct, select, time, glob
names = {29:"LCtrl",97:"RCtrl",42:"LShift",54:"RShift",56:"LAlt",100:"RAlt",125:"LMeta",126:"RMeta",
         105:"Left",106:"Right",103:"Up",104:"Down",15:"Tab",32:"D",61:"F3",13:"=",12:"-",17:"W"}
addr = sys.argv[1].lower() if len(sys.argv) > 1 else "?"
def rd(path):
    try:
        return open(path).read().strip()
    except OSError:
        return ""
dev = {}
for t in range(40):
    for ev in glob.glob("/sys/class/input/event*"):
        n = rd(ev + "/device/name"); u = rd(ev + "/device/uniq").lower()
        if ("cheerdots" in n.lower() or (addr != "?" and u == addr)) and ev not in dev:
            p = "/dev/input/" + os.path.basename(ev)
            dev[ev] = (os.open(p, os.O_RDONLY | os.O_NONBLOCK), n)
            print(f"   {p}: {n}")
    if dev and t > 3:
        break
    time.sleep(1)
if not dev:
    print("!!! Brak urzadzen wejscia CheerDots - myszka nie podlaczyla sie do systemu jako HID")
    print("--- wszystkie urzadzenia wejscia:")
    for ev in sorted(glob.glob("/sys/class/input/event*")):
        print("  ", os.path.basename(ev), rd(ev + "/device/name"), "|", rd(ev + "/device/uniq"))
    print("--- uhid:", os.path.exists("/dev/uhid"))
    os.system("lsmod | grep -E 'uhid|hid_apple|hid_generic' ; journalctl -u bluetooth -n 25 --no-pager -o cat; dmesg | grep -iE 'hid|uhid|input' | tail -15")
    sys.exit(1)
print("\n>>> TERAZ: kilka razy gesty 2 palcami: lewo, prawo, gora, DOL (po 3-4 razy). Ctrl+C = koniec.")
print(">>> (pulpit moze sie przelaczac / otwierac widoki - to dobrze)\n")
fds = {v[0]: v[1] for v in dev.values()}
fmt = "llHHi"; sz = struct.calcsize(fmt)
try:
    while True:
        r, _, _ = select.select(list(fds), [], [], 1)
        for fd in r:
            try:
                data = os.read(fd, sz * 64)
            except OSError:
                continue
            for i in range(0, len(data) - sz + 1, sz):
                _, _, typ, code, val = struct.unpack_from(fmt, data, i)
                if typ == 1 and val in (0, 1):
                    print(f"{time.strftime('%H:%M:%S')} KLAWISZ {names.get(code, code)} {'dol' if val else 'gora'}   [{fds[fd]}]")
                elif typ == 2 and code in (8, 11, 6, 12):
                    print(f"{time.strftime('%H:%M:%S')} KOLKO kod={code} {val:+d}   [{fds[fd]}]")
except KeyboardInterrupt:
    pass
PY
kill $BTMON 2>/dev/null; chmod a+rw "${LOG%.log}_btmon.txt" 2>/dev/null
echo
echo "Koniec testu. Log: $LOG"
echo "Powrot do naszego sterownika:  sudo bash $HERE/test_bluez.sh restore"
