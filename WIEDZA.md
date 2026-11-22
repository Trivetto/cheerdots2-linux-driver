# CheerDots 2 (CHP04) – co wiemy

Zebrane z analizy z 21.11.2026 (nagrania HID/evdev, btmon, Wireshark+BTVS na Windows, aplikacja producenta).

## Urządzenie

| | |
|---|---|
| Model / firmware | `CHP04`, `CHP04.00001`, producent `CheerDots Inc.` |
| Chip | Telink (usługi `00010203-0405-0607-0809-0a0b0c0d19xx`) |
| Połączenie | Bluetooth LE, HID over GATT |
| VID / PID | `05AC:024F` – podszywa się pod klawiaturę Apple (Linux ładuje `hid-apple`) |
| Nazwa | `Cheerdots_1` lub `Cheerdots_2` – zależnie od pozycji suwaka (dwa hosty) |
| Adres | losowy statyczny `D0:xx:08:10:00:ED` – zmienia się przy **nowym parowaniu**, stały przy wyłącz/włącz |

### Uchwyty GATT (stałe dla tego firmware)

| Uchwyt | Co | Uwagi |
|---|---|---|
| `0x001E` / `0x001F` | Bateria (0x2A19) / CCCD | wartość 0–100 % |
| `0x0040` / `0x0041` | Raport klawiatury (ID 1) / CCCD | **tu przychodzą gesty** |
| `0x0044` | Raport wyjściowy LED (ID 1) | Windows zapisuje `00` (Write Command) |
| `0x004A` / `0x004B` | Raport myszy (ID 3) / CCCD | ruch, przyciski, kółko |
| `0x004E` / `0x004F` | Raport multimedialny (ID 2) / CCCD | |
| `0x0052` | Report Map (deskryptor HID) | |
| `…1910` (`2B10` notify, `2B11` write) | kanał aplikacji producenta (SPP) | status, ustawienia DPI itp. |
| `…1911` (`2B18`, `2B19`) | kanał audio (nagrywanie) | |
| `…1912` | aktualizacja firmware (OTA Telink) | **nie dotykać** |

### Raporty

- Mysz (ID 3, 7 B bez ID): `btn(16 bit) | X(12 bit) | Y(12 bit) | wheel(8) | pan(8)`
- Klawiatura (ID 1, 8 B): `mods | 00 | 6 × keycode`
- Multimedia (ID 2, 2 B): bity Power, Home, Back, Menu, Vol+, Vol−, Play/Pause, Zoom, Prev, Next

## Gesty

Gesty rozpoznaje **firmware**. Wysyła je jako skróty klawiaturowe, zależnie od tego, jaki system rozpozna:

| Gest dwoma palcami | Windows | macOS-styl (to dostaje Linux przez nasz sterownik) |
|---|---|---|
| w lewo  | Ctrl+Win+→ | Ctrl+→ |
| w prawo | Ctrl+Win+← | Ctrl+← |
| w górę  | Win+Tab    | F3 |
| w dół   | Win+D      | Cmd(Win)+F3 |

Jeden palec: przewijanie pionowe (kółko). Lewy/prawy brzeg: LPM/PPM.

**Szczypanie (zoom)** – w aplikacji producenta domyślnie wyłączone (`zoom_enabled: 0`, ustawienie
`SendWriteZoomEnable` przez kanał `…1910`). Co dokładnie wysyła myszka – do potwierdzenia
(`cheerdots-ctl debug on`). Sterownik obsługuje warianty: Ctrl/Cmd + kółko oraz Ctrl/Cmd + `=`/`-`.

## Dlaczego na zwykłym Linuksie gesty nie działają

Myszka **nic nie wysyła** przy gestach, gdy jest połączona przez BlueZ – sprawdzone na hidraw, evdev,
wszystkich kanałach GATT i w `btmon`. Firmware wyłącza gesty, gdy uzna host za „nieznany”.

Sprawdzone i **bez wpływu**: kanał 1/2, stan diod NumLock/CapsLock (także zaraz po połączeniu),
wysłanie klucza IRK przy parowaniu (Privacy), ponowne parowanie, adapter (Intel vs Realtek).

**Działa**: połączenie własnym stosem (Google Bumble), który zachowuje się jak Windows:
- odrzuca `L2CAP Connection Parameter Update Request` od myszki (Windows odrzuca 4×, Linux akceptuje),
- parowanie: IO capability KeyboardDisplay, rozdzielanie kluczy IdKey|Sign|LinkKey,
- MTU 527, zapis LED jako Write Command, odczyty GATT w kolejności Windowsa.

Moduł jądra, który **tylko** odrzuca prośby o parametry (BlueZ zostaje), w pierwszym teście gestów
nie odblokował – albo nie zadziałał przy tym połączeniu, albo potrzebny jest jeszcze inny element.
Do sprawdzenia później (`dev/kernel-module`).

## Konsekwencja dla sterownika

Sterownik oparty o Bumble **zajmuje cały adapter Bluetooth**. Przy jednym adapterze inne urządzenia BT
nie działają, gdy sterownik jest włączony → zalecany osobny dongle USB dla myszki.

## Testy wariantow (21.11.2026, Bumble, adapter Intel 8265)
| wariant | co wylaczone z "udawania Windows" | lewo/prawo/gora | dol |
|---|---|---|---|
| A | odrzucanie prosb o parametry polaczenia | tak | tak |
| B | MTU 527, zapis LED, odczyty GATT | tak | NIE |
| C | parowanie (Linux: DisplayYesNo, bez IRK) | tak | tak |
| D | A+B+C | tak | NIE |
| E | D + parametry polaczenia jak BlueZ (30-50 ms, latency 0) | tak | NIE |
Wnioski: zaden element nie jest potrzebny do lewo/prawo/gora; do gestu w dol potrzebne cos z grupy B.
Myszka wysylala gesty w stylu Windows: Ctrl+Win+strzalka, Win+Tab (gora), Win+D (dol).
Nastepny krok: test F - zwykly BlueZ + odczyt /dev/input (dev/test_bluez.sh).

## PRZELOM: rozpoznawanie systemu po MTU (21.11.2026, 23:00)
Test G (btmon pod BlueZ): myszka nie wysyla ZADNEGO raportu klawiatury (0x40), tylko mysz (0x4A).
Bisekcja (Bumble odtwarza sekwencje BlueZ, wariant R):
- odczyty HID/CCCD/DIS, PnP ID, Server Supported Features, discovery, GAP, LE features, ping, parametry polaczenia 7.5/60/6000 -> gesty DZIALAJA
- tylko Exchange MTU 517 (wartosc BlueZ) -> gesty NIE dzialaja
- MTU 527 (Windows) -> wszystkie 4 gesty (A, C); brak wymiany MTU -> lewo/prawo/gora (B, D, E, I, J)
Wniosek: firmware rozpoznaje host po wartosci MTU w Exchange MTU Request.
BlueZ: [GATT] ExchangeMTU w /etc/bluetooth/main.conf (zakres 23..517).

## Rozwiazanie (0.3.0): BlueZ + ExchangeMTU = 185
Skan MTU (nasz sterownik, test S): 23 / 251 / 293 -> brak gestow, 185 -> wszystkie 4 (styl macOS).
Pod zwyklym BlueZ z [GATT] ExchangeMTU = 185 (test F 23:20): raporty klawiatury 0x40 przychodza:
 01 00 4f = Ctrl+Right (swipe_left), 01 00 50 = Ctrl+Left (swipe_right), 00 00 3c = F3 (up), 08 00 3c = Meta+F3 (down).
Po zmianie MTU myszke trzeba sparowac ponownie (stare klucze -> "PIN or Key Missing").
BlueZ (GKeyFile) NIE obsluguje komentarzy na koncu linii - znacznik pakietu jest w osobnej linii.
