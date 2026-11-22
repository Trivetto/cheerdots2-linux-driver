#!/usr/bin/env bash
# Test H: parujemy myszke NASZYM sterownikiem (wtedy gesty dzialaja), a potem przekazujemy klucze
# do zwyklego Bluetooth (BlueZ) i sprawdzamy, czy gesty dalej przychodza.
# Jesli TAK -> myszka "zapamietuje system" przy parowaniu i wystarczy parowac naszym narzedziem,
#              a na co dzien adapter zostaje przy systemie.
#   sudo bash test_handover.sh
#   sudo bash test_bluez.sh restore   -> powrot do naszego sterownika
set -u
[ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
SOCK=/run/cheerdots/cheerdots.sock
LOG="$HERE/test_H_$(date +%H%M%S).log"
exec > >(tee "$LOG") 2>&1
ipc() { python3 -c "import socket,sys;s=socket.socket(socket.AF_UNIX);s.settimeout(5);s.connect('$SOCK');s.sendall(sys.argv[1].encode()+b'\n');print(s.makefile().readline().strip())" "$1" 2>/dev/null; }

echo "=== Test H $(date)"
[ -f "$HERE/../cheerdots/daemon.py" ] && install -m 0644 "$HERE/../cheerdots/daemon.py" /usr/lib/cheerdots/cheerdots/daemon.py
echo 'CHEERDOTS_ARGS=' > /etc/default/cheerdots
START=$(date '+%Y-%m-%d %H:%M:%S')
systemctl restart cheerdots.service
echo "--> czekam az nasz sterownik przejmie adapter..."
for i in $(seq 60); do
  journalctl -u cheerdots --since "$START" -o cat | grep -q "przejety" && break; sleep 1
done
sleep 2
ipc '{"cmd":"forget_device"}' >/dev/null
ipc '{"cmd":"scan_start","seconds":90}' >/dev/null
echo
echo ">>> 1) Wlacz w myszce TRYB PAROWANIA (teraz)."
ADDR=""
for i in $(seq 90); do
  sleep 1
  ADDR=$(ipc '{"cmd":"scan_results"}' | python3 -c "
import json,sys
try: r=json.load(sys.stdin)
except Exception: sys.exit()
for d in r.get('devices', r.get('results', [])):
    if d.get('cheerdots'): print(d['address'], d.get('name','')); break")
  [ -n "$ADDR" ] && break
done
[ -n "$ADDR" ] || { echo "!!! Nie znaleziono myszki"; exit 1; }
A=${ADDR%% *}; N=${ADDR#* }
echo "znaleziono: $A ($N) - paruje naszym sterownikiem"
ipc "{\"cmd\":\"select_device\",\"address\":\"$A\",\"name\":\"$N\"}" >/dev/null
for i in $(seq 90); do
  journalctl -u cheerdots --since "$START" -o cat | grep -q "polaczono z" && break; sleep 1
done
journalctl -u cheerdots --since "$START" -o cat | grep -q "polaczono z" || { echo "!!! Nie polaczyl sie"; journalctl -u cheerdots --since "$START" -o cat | tail -15; exit 1; }
echo "polaczono (nasz sterownik). Odczekaj chwile..."
sleep 12
echo
echo ">>> 2) Zrob 2-3 gesty (lewo/prawo) - sprawdzam, czy dzialaja z naszym sterownikiem (20 s)."
sleep 20
G=$(journalctl -u cheerdots --since "$START" -o cat | grep -c " gest ")
echo "gestow z naszym sterownikiem: $G"

echo
echo "--> przekazuje klucze do BlueZ"
systemctl stop cheerdots.service
sleep 2
systemctl stop bluetooth
python3 - "$A" "$N" <<'PY'
import json, os, sys, glob, re
addr, name = sys.argv[1].upper(), sys.argv[2] or "Cheerdots_2"
ks = json.load(open("/var/lib/cheerdots/keys.json"))
keys = None
for ns, d in ks.items():
    for a, k in d.items():
        if a.split("/")[0].upper() == addr:
            keys = k
if not keys:
    print("!!! brak kluczy dla", addr, "w keys.json:", list(ks)); sys.exit(1)
sc = "ltk" in keys and "ltk_central" not in keys
lk = keys.get("ltk_central") or keys.get("ltk")
auth = 1 if lk.get("authenticated") else 0
if sc: auth += 2
rand = int.from_bytes(bytes.fromhex(lk.get("rand") or "00" * 8), "little")
ediv = lk.get("ediv") or 0
out = ["[General]", f"Name={name}", "AddressType=static", "SupportedTechnologies=LE;", "Trusted=true",
       "Blocked=false", "WakeAllowed=true", "",
       "[LongTermKey]", f"Key={lk['value'].upper()}", f"Authenticated={auth}", "EncSize=16",
       f"EDiv={ediv}", f"Rand={rand}", ""]
if sc:
    out += ["[PeripheralLongTermKey]", f"Key={lk['value'].upper()}", f"Authenticated={auth}",
            "EncSize=16", "EDiv=0", "Rand=0", ""]
if keys.get("irk"):
    out += ["[IdentityResolvingKey]", f"Key={keys['irk']['value'].upper()}", ""]
out += ["[ConnectionParameters]", "MinInterval=6", "MaxInterval=6", "Latency=60", "Timeout=600", ""]
mac = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
dirs = [d for d in glob.glob("/var/lib/bluetooth/*") if mac.match(os.path.basename(d))]
if not dirs:
    print("!!! brak katalogu adaptera w /var/lib/bluetooth"); sys.exit(1)
for d in dirs:
    for old in glob.glob(d + "/*/info"):          # usun stare wpisy CheerDots
        if "cheerdots" in open(old, errors="ignore").read().lower():
            import shutil; shutil.rmtree(os.path.dirname(old), ignore_errors=True)
            shutil.rmtree(d + "/cache/" + os.path.basename(os.path.dirname(old)), ignore_errors=True)
    os.makedirs(f"{d}/{addr}", mode=0o700, exist_ok=True)
    open(f"{d}/{addr}/info", "w").write("\n".join(out))
    os.chmod(f"{d}/{addr}/info", 0o600)
    print("zapisano", f"{d}/{addr}/info", "(legacy)" if not sc else "(SC)")
PY
[ $? -eq 0 ] || exit 1
echo
echo ">>> 3) Za chwile: jesli myszka sie nie polaczy sama, WYLACZ i WLACZ ja (BEZ trybu parowania)."
echo
bash "$HERE/test_bluez.sh" skip
