# -*- coding: utf-8 -*-
"""Wspolna konfiguracja: gesty, akcje, klawisze, sciezki. Uzywane przez demona i GUI."""
import copy
import json
import os

CONFIG_PATH = os.environ.get("CHEERDOTS_CONFIG", "/etc/cheerdots/config.json")
KEYSTORE_PATH = os.environ.get("CHEERDOTS_KEYS", "/var/lib/cheerdots/keys.json")
SOCKET_PATH = os.environ.get("CHEERDOTS_SOCKET", "/run/cheerdots/cheerdots.sock")

# Gesty, ktore rozpoznaje firmware myszki (dwa palce; 3/4 palce firmware nie wysyla)
BASE_GESTURES = {
    "swipe_left":  "Dwa palce w lewo",
    "swipe_right": "Dwa palce w prawo",
    "swipe_up":    "Dwa palce w górę",
    "swipe_down":  "Dwa palce w dół",
    "pinch_out":   "Rozsunięcie palców (powiększ)",
    "pinch_in":    "Zsunięcie palców (pomniejsz)",
}
SWIPES = ("swipe_left", "swipe_right", "swipe_up", "swipe_down")
# Gesty pochodne: podwojny (dwa razy szybko w te sama strone) i z przytrzymanym przyciskiem myszy
GESTURES = dict(BASE_GESTURES)
GESTURES.update({f"{g}_x2": BASE_GESTURES[g] + " – dwa razy" for g in SWIPES})
GESTURES.update({f"{g}_btn": "Przycisk + " + BASE_GESTURES[g].lower() for g in SWIPES})
GESTURE_GROUPS = {
    "Podstawowe": list(BASE_GESTURES),
    "Podwójne": [f"{g}_x2" for g in SWIPES],
    "Z przyciskiem": [f"{g}_btn" for g in SWIPES],
}
BUTTONS = {"right": ("prawy przycisk", 0x111), "middle": ("środkowy przycisk (kółko)", 0x112)}


# Gotowe akcje (KDE Plasma). Wartosc: (opis, lista klawiszy)
ACTIONS = {
    "none":         ("Brak (wyłączony)",          []),
    "next_desktop": ("Następny pulpit",           ["ctrl", "meta", "right"]),
    "prev_desktop": ("Poprzedni pulpit",          ["ctrl", "meta", "left"]),
    "overview":     ("Przegląd (Overview)",       ["meta", "w"]),
    "show_desktop": ("Pokaż pulpit",              ["meta", "d"]),
    "grid":         ("Siatka pulpitów",           ["meta", "g"]),
    "back":         ("Wstecz (przeglądarka)",     ["alt", "left"]),
    "forward":      ("Dalej (przeglądarka)",      ["alt", "right"]),
    "next_tab":     ("Następna karta",            ["ctrl", "tab"]),
    "prev_tab":     ("Poprzednia karta",          ["ctrl", "shift", "tab"]),
    "vol_up":       ("Głośniej",                  ["volumeup"]),
    "vol_down":     ("Ciszej",                    ["volumedown"]),
    "play_pause":   ("Odtwórz / pauza",           ["playpause"]),
    "screenshot":   ("Zrzut ekranu",              ["print"]),
    "zoom_in":      ("Powiększ (Ctrl + kółko)",   ["ctrl", "wheelup"]),
    "zoom_out":     ("Pomniejsz (Ctrl + kółko)",  ["ctrl", "wheeldown"]),
    "zoom_in_key":  ("Powiększ (Ctrl + +)",       ["ctrl", "equal"]),
    "zoom_out_key": ("Pomniejsz (Ctrl + −)",      ["ctrl", "minus"]),
    "kwin_zoom_in": ("Lupa ekranu: powiększ",     ["meta", "equal"]),
    "kwin_zoom_out":("Lupa ekranu: pomniejsz",    ["meta", "minus"]),
    "zoom_reset":   ("Powiększenie 100% (Ctrl + 0)", ["ctrl", "0"]),
    "new_tab":      ("Nowa karta",                ["ctrl", "t"]),
    "close_tab":    ("Zamknij kartę",             ["ctrl", "w"]),
    "reopen_tab":   ("Przywróć zamkniętą kartę",  ["ctrl", "shift", "t"]),
    "reload":       ("Odśwież (F5)",              ["f5"]),
    "fullscreen":   ("Pełny ekran (F11)",         ["f11"]),
    "switch_window":("Poprzednie okno (Alt+Tab)", ["alt", "tab"]),
    "minimize":     ("Minimalizuj okno",          ["meta", "pagedown"]),
    "maximize":     ("Maksymalizuj okno",         ["meta", "pageup"]),
    "close_window": ("Zamknij okno (Alt+F4)",     ["alt", "f4"]),
    "prev_track":   ("Poprzedni utwór",           ["previoussong"]),
    "next_track":   ("Następny utwór",            ["nextsong"]),
    "mute":         ("Wycisz",                    ["mute"]),
    "terminal":     ("Terminal (Ctrl+Alt+T)",     ["ctrl", "alt", "t"]),
    "custom":       ("Własny skrót…",           None),
}

def _g(action="none", enabled=None):
    return {"enabled": action != "none" if enabled is None else enabled, "action": action, "custom": ""}


DEFAULT_CONFIG = {
    "version": 2,
    "gestures": {
        "swipe_left":  _g("next_desktop"),
        "swipe_right": _g("prev_desktop"),
        "swipe_up":    _g("overview"),
        "swipe_down":  _g("show_desktop"),
        "pinch_out":   _g("zoom_in"),
        "pinch_in":    _g("zoom_out"),
        **{f"{g}_x2": _g() for g in SWIPES},
        **{f"{g}_btn": _g() for g in SWIPES},
    },
    "double_ms": 400,              # okno na gest podwojny (ms); pojedynczy gest czeka tyle, gdy podwojny jest ustawiony
    "gesture_button": "right",     # przycisk dla gestow "z przyciskiem": right / middle
    "notify_battery_below": 15,    # GUI ostrzega ponizej tylu procent (0 = nie)
    "pointer_scale": 100,          # predkosc wskaznika w % (sterownik skaluje ruch przed KDE; 100 = bez zmian)
    "device": None,                # wybrana myszka: {"address": "D0:..:ED", "name": "Cheerdots_2"}
    # Profile aplikacji: pierwszy aktywny profil, ktorego "match" pasuje do klasy aktywnego okna (fragment nazwy),
    # nadpisuje gesty; gest z akcja "inherit" (albo nieobecny) dziala jak w ustawieniach domyslnych.
    "profiles": [
        {"name": "Przeglądarka", "enabled": False,
         "match": ["firefox", "chrom", "brave", "vivaldi", "msedge", "microsoft-edge", "opera", "librewolf", "falkon"],
         "gestures": {"swipe_left": _g("forward"), "swipe_right": _g("back"),
                      "swipe_up": _g("new_tab"), "swipe_down": _g("close_tab"),
                      "pinch_out": _g("zoom_in_key"), "pinch_in": _g("zoom_out_key")}},
        {"name": "Odtwarzacz", "enabled": False,
         "match": ["vlc", "mpv", "spotify", "elisa", "smplayer", "audacious", "haruna", "rhythmbox", "strawberry"],
         "gestures": {"swipe_left": _g("next_track"), "swipe_right": _g("prev_track"),
                      "swipe_up": _g("vol_up"), "swipe_down": _g("vol_down")}},
    ],
}

# Nazwy klawiszy -> kody Linux (input-event-codes.h)
KEYCODES = {
    "esc": 1, "backspace": 14, "tab": 15, "enter": 28, "space": 57, "minus": 12, "equal": 13,
    "ctrl": 29, "shift": 42, "alt": 56, "meta": 125, "rctrl": 97, "rshift": 54, "ralt": 100, "rmeta": 126,
    "up": 103, "down": 108, "left": 105, "right": 106, "home": 102, "end": 107,
    "pageup": 104, "pagedown": 109, "insert": 110, "delete": 111, "print": 99,
    "mute": 113, "volumedown": 114, "volumeup": 115, "playpause": 164, "nextsong": 163,
    "previoussong": 165, "stopcd": 166,
    "wheelup": -1, "wheeldown": -2,            # pseudo-klawisze: obrot kolka (np. ctrl+wheelup = zoom)
    **{f"f{i}": c for i, c in zip(range(1, 11), range(59, 69))}, "f11": 87, "f12": 88,
    **dict(zip("1234567890", range(2, 12))),
    **dict(zip("qwertyuiop", range(16, 26))), **dict(zip("asdfghjkl", range(30, 39))),
    **dict(zip("zxcvbnm", range(44, 51))),
}


def parse_combo(text):
    """'ctrl+alt+t' -> ['ctrl','alt','t'] (rzuca ValueError przy nieznanym klawiszu)."""
    keys = [k.strip().lower() for k in text.replace(" ", "").split("+") if k.strip()]
    bad = [k for k in keys if k not in KEYCODES]
    if bad:
        raise ValueError("Nieznane klawisze: " + ", ".join(bad))
    return keys


def action_keys(gesture_cfg):
    """Zwraca liste nazw klawiszy dla gestu albo [] gdy wylaczony/brak."""
    if not gesture_cfg.get("enabled", True):
        return []
    action = gesture_cfg.get("action", "none")
    if action == "custom":
        try:
            return parse_combo(gesture_cfg.get("custom", ""))
        except ValueError:
            return []
    return list(ACTIONS.get(action, ("", []))[1] or [])


def _clean_gesture(gc, allow_inherit=False):
    out = {"enabled": bool(gc.get("enabled", True)), "action": str(gc.get("action", "none")),
           "custom": str(gc.get("custom", ""))}
    if out["action"] not in ACTIONS and not (allow_inherit and out["action"] == "inherit"):
        out["action"] = "none"
    return out


def merged(cfg):
    """Uzupelnia brakujace pola wartosciami domyslnymi (i porzadkuje dane z pliku/GUI)."""
    out = copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(cfg, dict):
        return out
    for k, v in cfg.items():
        if k == "gestures" and isinstance(v, dict):
            for g, gc in v.items():
                if g in out["gestures"] and isinstance(gc, dict):
                    out["gestures"][g] = _clean_gesture({**out["gestures"][g], **gc})
        elif k == "profiles" and isinstance(v, list):
            profs = []
            for p in v:
                if not isinstance(p, dict):
                    continue
                match = p.get("match", [])
                if isinstance(match, str):
                    match = [m.strip() for m in match.split(",")]
                profs.append({
                    "name": str(p.get("name") or "Profil"), "enabled": bool(p.get("enabled", True)),
                    "match": [str(m).strip().lower() for m in match if str(m).strip()],
                    "gestures": {g: _clean_gesture(gc, True) for g, gc in (p.get("gestures") or {}).items()
                                 if g in GESTURES and isinstance(gc, dict)},
                })
            out["profiles"] = profs
        elif k in out and k != "version":
            out[k] = v
    return out


def find_profile(cfg, app):
    """Pierwszy aktywny profil pasujacy do klasy okna (np. 'firefox', 'org.kde.dolphin') albo None."""
    app = (app or "").lower()
    if not app:
        return None
    for p in cfg.get("profiles", []):
        if p.get("enabled") and any(m and m in app for m in p.get("match", [])):
            return p
    return None


def effective_gesture(cfg, gesture, app=None):
    """-> (konfiguracja gestu, nazwa profilu albo None) dla aktywnej aplikacji."""
    p = find_profile(cfg, app)
    if p:
        gc = p["gestures"].get(gesture)
        if gc and gc.get("action") != "inherit":
            return gc, p["name"]
    return cfg["gestures"].get(gesture, {"enabled": False, "action": "none", "custom": ""}), None


def gesture_active(cfg, gesture, app=None):
    gc, _ = effective_gesture(cfg, gesture, app)
    return bool(action_keys(gc))


def load(path=CONFIG_PATH):
    try:
        with open(path) as f:
            return merged(json.load(f))
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULT_CONFIG)


def save(cfg, path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(merged(cfg), f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def list_adapters():
    """Numery adapterow Bluetooth obecnych w systemie (hci0, hci1, ...)."""
    try:
        names = os.listdir("/sys/class/bluetooth")
    except OSError:
        return []
    return sorted(int(n[3:]) for n in names if n.startswith("hci") and n[3:].isdigit())


def resolve_hci(value):
    """'auto' -> pierwszy dostepny adapter; liczba -> ta liczba (jesli istnieje). None gdy brak."""
    present = list_adapters()
    if value in (None, "", "auto"):
        return present[0] if present else None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return present[0] if present else None
    return n if n in present else None


USB_VENDORS = {"8087": "Intel", "0bda": "Realtek", "2b89": "Realtek (UGREEN)", "0a12": "CSR", "0a5c": "Broadcom",
               "13d3": "IMC/Realtek", "04ca": "Lite-On", "0cf3": "Qualcomm Atheros", "2357": "TP-Link", "7392": "Edimax"}


def adapter_info(idx):
    """Opis adaptera hciN: {'index', 'usb', 'label'}."""
    info = {"index": idx, "usb": None, "label": f"hci{idx}"}
    try:
        d = os.path.realpath(f"/sys/class/bluetooth/hci{idx}/device")
        for up in (d, os.path.dirname(d)):
            vid = os.path.join(up, "idVendor")
            if os.path.exists(vid):
                v = open(vid).read().strip()
                pr = open(os.path.join(up, "idProduct")).read().strip()
                info["usb"] = f"{v}:{pr}"
                info["label"] = f"hci{idx} – {USB_VENDORS.get(v, 'USB')} ({v}:{pr})"
                break
    except OSError:
        pass
    return info


def norm_addr(a):
    """'D0:CE:08:10:00:ED/P' -> 'D0:CE:08:10:00:ED'"""
    return str(a or "").split("/")[0].upper()


# --------------------------------------------------------------------------- BlueZ
# Firmware CheerDots wysyla gesty tylko hostom, ktore rozpoznaje (Windows: MTU 527, macOS: MTU 185).
# BlueZ domyslnie prosi o MTU 517 -> myszka uznaje komputer za "obcy" i wylacza gesty.
# Ustawiamy w BlueZ ExchangeMTU = 185 (jak macOS) - zakres BlueZ to 23..517, wiec 527 nie wchodzi w gre.
BLUEZ_CONF = os.environ.get("CHEERDOTS_BLUEZ_CONF", "/etc/bluetooth/main.conf")
BLUEZ_MTU = 185


def bluez_mtu(path=BLUEZ_CONF):
    """Aktualna wartosc [GATT] ExchangeMTU z main.conf (None = nie ustawiona -> BlueZ uzywa 517)."""
    try:
        lines = open(path).read().splitlines()
    except OSError:
        return None
    section = None
    for line in lines:
        s = line.strip()
        if s.startswith("["):
            section = s
        elif section == "[GATT]" and s.split("=")[0].strip() == "ExchangeMTU" and "=" in s:
            try:
                return int(s.split("=", 1)[1].split("#")[0].strip())
            except ValueError:
                return None
    return None
