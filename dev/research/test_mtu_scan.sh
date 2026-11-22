#!/usr/bin/env bash
# Test S: po kolei rozne wartosci MTU (nasz sterownik), liczy gesty dla kazdej.
#   sudo bash test_mtu_scan.sh                 -> domyslna lista
#   sudo bash test_mtu_scan.sh 185 293 527     -> wlasna lista
set -u
[ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/../cheerdots/daemon.py" ] && install -m 0644 "$HERE/../cheerdots/daemon.py" /usr/lib/cheerdots/cheerdots/daemon.py
VALS=("$@"); [ ${#VALS[@]} -gt 0 ] || VALS=(23 185 251 293 527)
LOG="$HERE/test_S_$(date +%H%M%S).log"
BASE="--no-reject --mtu 0 --no-led --no-reads --keystore /var/lib/cheerdots/test_keys.json"
trap 'echo "CHEERDOTS_ARGS=" > /etc/default/cheerdots; systemctl restart cheerdots.service; echo "Wyniki: $LOG"' EXIT
echo "=== Test S $(date)" > "$LOG"
for V in "${VALS[@]}"; do
  echo
  echo "################ MTU $V ################"
  echo "CHEERDOTS_ARGS=$BASE --bluez-replay mtu=$V" > /etc/default/cheerdots
  START=$(date '+%Y-%m-%d %H:%M:%S')
  systemctl restart cheerdots.service
  echo "--> lacze (jesli nie polaczy sie w 20 s: wylacz i wlacz myszke; za pierwszym razem moze byc potrzebny TRYB PAROWANIA)"
  OK=""
  for i in $(seq 60); do
    journalctl -u cheerdots --since "$START" -o cat | grep -q "polaczono z" && { OK=1; break; }; sleep 1
  done
  [ -n "$OK" ] || { echo "MTU $V: brak polaczenia" | tee -a "$LOG"; continue; }
  sleep 4
  echo ">>> TERAZ przez 15 s rob gesty: lewo, prawo, gora, dol (na zmiane)"
  for i in 15 10 5; do echo "   ...$i s"; sleep 5; done
  J=$(journalctl -u cheerdots --since "$START" -o cat)
  R=$(echo "$J" | grep -o "test: MTU $V -> [0-9]*" | head -1)
  G=$(echo "$J" | grep -oE "gest (swipe|pinch)_[a-z]+" | sort | uniq -c | tr '\n' ' ')
  echo "MTU $V [$R]: ${G:-BRAK GESTOW}" | tee -a "$LOG"
done
echo; echo "=== PODSUMOWANIE"; cat "$LOG"
