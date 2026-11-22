# CheerDots 2 – Linux gesture driver (KDE Plasma)

**[English](#english) · [Polski](#polski)**

Two-finger gestures, battery status and per-app settings for the **CheerDots 2 (CHP04)** touch mouse on Linux.
The vendor supports only Windows and macOS – on Linux the mouse works as a plain mouse and sends no gestures.
This project makes the gestures work **using the normal system Bluetooth stack** – no extra dongle, no kernel module.

---

## English

### Why gestures don't work on Linux (and the fix)

The mouse firmware enables gestures only for hosts it recognises, and it recognises them by the
**ATT MTU** value the computer requests right after connecting:

| MTU requested by the host | gestures |
|---|---|
| 527 (Windows) | all |
| 185 (macOS) | all |
| no MTU exchange | left / right / up |
| 517 (Linux/BlueZ default) and anything else | none |

The package sets `ExchangeMTU = 185` in the `[GATT]` section of `/etc/bluetooth/main.conf`, so the mouse treats
the computer as a Mac and sends its gestures (macOS-style shortcuts: Ctrl+←/→, Mission Control, Meta+Mission Control).
A small daemon catches those shortcuts and runs the actions you choose. Found by recording and replaying HCI
traffic – see [`WIEDZA.md`](WIEDZA.md) (Polish) and [`dev/`](dev/).

### Features

- 4 two-finger swipes (left, right, up, down) + pinch – each mapped to a ready action or your own shortcut
- **Double gestures** – the same swipe twice quickly (e.g. 2× up = desktop grid)
- **Button gestures** – swipe while holding the right (or middle) button; normal clicks keep working
- **Per-application profiles** – e.g. Back/Forward in the browser, previous/next track in a player
  (active window detected by a tiny KWin script – KDE Plasma only)
- Pointer speed scaling independent of KDE, battery percentage on the tray icon, low-battery warning
- Settings export/import, tray menu to stop/start the driver, raw-key debug mode
- Other Bluetooth devices keep working normally

The mouse does not send three- or four-finger gestures (verified).

### Install (Debian / Ubuntu / Kubuntu)

1. Download the latest `cheerdots_*_all.deb` from **[Releases](https://github.com/Trivetto/cheerdots2-linux-driver/releases)**
   and install it:
   ```
   sudo apt install ./cheerdots_*_all.deb
   ```
2. **Pair (or re-pair) the mouse** in *System Settings → Bluetooth* (pairing mode on the mouse).
   If it was paired before installing, remove it there and pair again.
3. Start **CheerDots 2** from the application menu. The mouse is detected automatically; the tray icon starts at login.

Requirements: Python ≥ 3.10, PyQt6, BlueZ, systemd. Tested on Kubuntu with KDE Plasma 6 and BlueZ 5.85.
Build the package yourself with `bash packaging/build-deb.sh` (needs only `dpkg-deb`).

Uninstall: `sudo apt remove cheerdots` (also reverts the `main.conf` change), `sudo apt purge cheerdots` – with settings.

### Notes

- In KDE mouse settings you will see two entries: `Cheerdots_2` (the real mouse, grabbed by the driver – its
  settings have no effect) and `CheerDots 2` (the driver's virtual mouse – **set pointer speed here**).
- `ExchangeMTU = 185` applies to all Bluetooth LE devices (smaller maximum GATT packet). Mice, keyboards and
  classic-Bluetooth audio are not affected in practice.
- The UI is in Polish for now. Translations are welcome.

Not affiliated with CheerDots Inc. Use at your own risk (MIT license).

---

## Polski

### Dlaczego gesty nie działają na Linuksie (i rozwiązanie)

Firmware myszki włącza gesty tylko dla systemów, które rozpozna – a rozpoznaje je po wartości **MTU**, o którą
komputer prosi zaraz po połączeniu: 527 (Windows) i 185 (macOS) = gesty, 517 (domyślnie Linux/BlueZ) = brak gestów.
Pakiet ustawia w systemowym Bluetooth (`/etc/bluetooth/main.conf`, sekcja `[GATT]`) `ExchangeMTU = 185` –
myszka bierze komputer za Maca. **Bluetooth zostaje przy systemie**, inne urządzenia działają normalnie.

### Funkcje

- 4 gesty dwoma palcami + szczypanie, każdy z wyborem akcji albo własnym skrótem
- **gesty podwójne** (dwa razy szybko w tę samą stronę), **gesty z przyciskiem** (z wciśniętym prawym/środkowym)
- **profile aplikacji** (np. w przeglądarce Wstecz/Dalej, w odtwarzaczu utwory) – aktywne okno zgłasza skrypt KWin
- prędkość wskaźnika niezależna od KDE, bateria na ikonie w zasobniku, eksport/import ustawień,
  włączanie/wyłączanie sterownika z menu ikony

### Instalacja

1. Pobierz `cheerdots_*_all.deb` z **[Releases](https://github.com/Trivetto/cheerdots2-linux-driver/releases)**:
   `sudo apt install ./cheerdots_*_all.deb`
2. Sparuj myszkę w **Ustawieniach systemowych → Bluetooth** (jeśli była sparowana wcześniej – usuń ją i sparuj ponownie).
3. Uruchom **CheerDots 2** z menu – myszka wykryje się sama.

W ustawieniach myszy KDE prędkość wskaźnika ustawiaj dla wpisu **„CheerDots 2”** (ze spacją) – to wirtualna
mysz sterownika; „Cheerdots_2” to prawdziwa myszka przejęta przez sterownik.

### Polecenia

| polecenie | działanie |
|---|---|
| `cheerdots-ctl status` | stan usługi, MTU w BlueZ, stan myszki |
| `cheerdots-ctl gui` | okno ustawień |
| `cheerdots-ctl setup` | (ponownie) ustaw `ExchangeMTU = 185` w BlueZ |
| `cheerdots-ctl pair` | usuń parowanie i otwórz ustawienia Bluetooth |
| `cheerdots-ctl log` | dziennik na żywo |
| `cheerdots-ctl debug on/off` | loguj surowe klawisze z myszki |
| `cheerdots-ctl on/off/restart` | usługa (to samo z menu ikony: PPM → Wyłącz/Włącz sterownik) |

### Jak to działa

- `cheerdots-daemon` (usługa systemd): przejmuje na wyłączność (EVIOCGRAB) urządzenie wejścia myszki utworzone
  przez BlueZ, ruch/kliknięcia przekazuje przez `/dev/uinput`, skróty gestów zamienia na akcje
  (z uwzględnieniem profilu aktywnej aplikacji). Bateria i stan z BlueZ (D-Bus). IPC: `/run/cheerdots/cheerdots.sock`.
- `cheerdots-gui`: ikona w zasobniku, ustawienia, profile, wybór/parowanie myszki, skrypt KWin aktywnego okna.
- `/usr/lib/cheerdots/bluez-mtu set|unset|status`: zmiana w `main.conf` (kopia: `main.conf.cheerdots-orig`).
- Testy: `python3 -m unittest discover -s tests -v`.

Historia badań: [`WIEDZA.md`](WIEDZA.md), zmiany: [`CHANGELOG.md`](CHANGELOG.md). Licencja MIT.
