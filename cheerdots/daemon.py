#!/usr/bin/env python3
"""
CheerDots 2 - demon sterownika (root, usluga systemd 'cheerdots'), wersja dla systemowego Bluetooth (BlueZ).

Jak to dziala:
- myszke laczy i paruje zwykly Bluetooth systemu (BlueZ / Ustawienia KDE) - adapter NIE jest zajmowany,
- w /etc/bluetooth/main.conf ustawione jest [GATT] ExchangeMTU = 185 (jak macOS) - dzieki temu firmware
  myszki rozpoznaje komputer i wysyla gesty dwoma palcami (przy domyslnym 517 gestow nie ma),
- demon przejmuje na wylacznosc (EVIOCGRAB) urzadzenie wejscia myszki, przekazuje ruch/klikniecia/kolko
  dalej przez wirtualne urzadzenie /dev/uinput, a skroty gestow zamienia na akcje z konfiguracji,
- bateria i stan polaczenia z BlueZ (D-Bus, przez busctl),
- gniazdo IPC /run/cheerdots/cheerdots.sock (JSON, linia = polecenie) dla GUI:
    status, get_config, set_config, devices, scan_start, scan_stop, scan_results,
    select_device {address,name}, pair {address}, forget_device, reconnect
"""
import argparse
import asyncio
import fcntl
import glob
import json
import logging
import os
import struct
import time

try:
    from . import config as C
except ImportError:
    import config as C

log = logging.getLogger("cheerdots")

# --------------------------------------------------------------------------- evdev / uinput
EV_SYN, EV_KEY, EV_REL, EV_MSC = 0, 1, 2, 4
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 0, 1, 6, 8, 11, 12
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405C5503, 0x5501, 0x5502
EVIOCGRAB = 0x40044590
EVENT = struct.Struct("llHHi")
BTNS = (0x110, 0x111, 0x112, 0x113, 0x114)
OUR_BUS, OUR_VENDOR, OUR_PRODUCT = 0x06, 0x1209, 0xCD02     # wirtualne urzadzenie demona (nie mylic z myszka)

# grupy modyfikatorow
CTRL, SHIFT, ALT, META = "ctrl", "shift", "alt", "meta"
MOD_GROUP = {29: CTRL, 97: CTRL, 42: SHIFT, 54: SHIFT, 56: ALT, 100: ALT, 125: META, 126: META}

# --------------------------------------------------------------------------- rozpoznawanie gestow
K_LEFT, K_RIGHT, K_UP, K_DOWN, K_TAB, K_D, K_EQUAL, K_MINUS = 105, 106, 103, 108, 15, 32, 13, 12
F3_KEYS = (61, 120, 204)                 # F3; hid-apple moze zamienic F3 na KEY_SCALE / KEY_DASHBOARD
GESTURES = {
    # styl macOS (MTU 185)
    (frozenset({CTRL}), K_RIGHT): "swipe_left",
    (frozenset({CTRL}), K_LEFT): "swipe_right",
    **{(frozenset(), k): "swipe_up" for k in F3_KEYS},
    **{(frozenset({META}), k): "swipe_down" for k in F3_KEYS},
    (frozenset({CTRL}), K_UP): "swipe_up",
    (frozenset({CTRL}), K_DOWN): "swipe_down",
    # styl Windows (MTU 527)
    (frozenset({CTRL, META}), K_RIGHT): "swipe_left",
    (frozenset({CTRL, META}), K_LEFT): "swipe_right",
    (frozenset({META}), K_TAB): "swipe_up",
    (frozenset({META}), K_D): "swipe_down",
    # szczypanie jako skrot klawiszowy (Cmd/Ctrl + '=' / '-', ewentualnie z Shift)
    **{(frozenset(m), K_EQUAL): "pinch_out" for m in ({META}, {CTRL}, {META, SHIFT}, {CTRL, SHIFT})},
    **{(frozenset(m), K_MINUS): "pinch_in" for m in ({META}, {CTRL}, {META, SHIFT}, {CTRL, SHIFT})},
}
PINCH_GROUPS = (CTRL, META)              # szczypanie jako kolko z wcisnietym Ctrl/Cmd

HINTS = {
    "no_bluez": "Systemowa usługa Bluetooth nie działa. Włącz Bluetooth (ikona w zasobniku) "
                "albo uruchom: sudo systemctl start bluetooth",
    "mtu_missing": "Bluetooth nie jest skonfigurowany dla gestów (brak ExchangeMTU = 185 w /etc/bluetooth/main.conf). "
                   "Przeinstaluj pakiet albo uruchom: sudo cheerdots-ctl setup",
    "no_device": "Sparuj myszkę w Ustawieniach systemowych → Bluetooth (tryb parowania w myszce), "
                 "potem wybierz ją tutaj.",
    "searching": "Myszka rozłączona. Włącz ją albo nią porusz – połączy się sama.",
    "no_input": "Myszka połączona, ale system nie utworzył jeszcze jej urządzenia wejścia. Chwilę…",
}


class UInput:
    """Wirtualna mysz+klawiatura, przez ktora idzie wszystko z myszki i akcje gestow."""

    def __init__(self, name="CheerDots 2"):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
        for k in range(1, 0x2FF):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
        for r in (REL_X, REL_Y, REL_HWHEEL, REL_WHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES):
            fcntl.ioctl(self.fd, UI_SET_RELBIT, r)
        fcntl.ioctl(self.fd, UI_DEV_SETUP,
                    struct.pack("HHHH80sI", OUR_BUS, OUR_VENDOR, OUR_PRODUCT, 3, name.encode()[:79], 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.down = set()

    def emit(self, t, c, v):
        now = time.time()
        try:
            os.write(self.fd, EVENT.pack(int(now), int((now % 1) * 1e6), t, c, v))
        except OSError as e:
            log.debug("uinput: %s", e)
        if t == EV_KEY:
            if v:
                self.down.add(c)
            else:
                self.down.discard(c)

    def syn(self):
        self.emit(EV_SYN, 0, 0)

    def tap_codes(self, codes):
        keys = [c for c in codes if c > 0]
        wheel = [c for c in codes if c < 0]          # -1 = kolko w gore, -2 = w dol
        for c in keys:
            self.emit(EV_KEY, c, 1)
        self.syn()
        for w in wheel:
            self.emit(EV_REL, REL_WHEEL, 1 if w == -1 else -1)
            self.emit(EV_REL, REL_WHEEL_HI_RES, 120 if w == -1 else -120)
            self.syn()
        for c in reversed(keys):
            self.emit(EV_KEY, c, 0)
        self.syn()

    def tap(self, names):
        self.tap_codes([C.KEYCODES[n] for n in names if n in C.KEYCODES])

    def release_all(self):
        for c in list(self.down):
            self.emit(EV_KEY, c, 0)
        self.syn()

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            os.close(self.fd)
        except OSError:
            pass


class Translator:
    """Stan jednego urzadzenia wejscia myszki: wykrywa skroty gestow, reszte przepuszcza."""

    def __init__(self, ui, on_gesture, raw_log=False, get_scale=lambda: 1.0, shared=None,
                 buffer_button=lambda: None):
        self.ui, self.on_gesture, self.raw_log, self.get_scale = ui, on_gesture, raw_log, get_scale
        # stan wspolny dla wszystkich urzadzen wejscia tej myszki: wcisniete przyciski i wstrzymany przycisk gestow
        self.shared = shared if shared is not None else {"buttons": set(), "bbuf": None}
        self.buffer_button = buffer_button     # -> kod przycisku do wstrzymywania (gesty z przyciskiem) albo None
        self.rest = {REL_X: 0.0, REL_Y: 0.0}   # reszta z przeskalowanego ruchu (zeby wolny ruch nie ginal)
        self.held = {}             # kod modyfikatora -> czy juz przekazany dalej
        self.swallowed = set()     # klawisze zjedzone przez gest (ich puszczenie tez zjadamy)
        self.pinch_frame = False   # w tej ramce kolko zostalo zuzyte przez szczypanie
        self.pending = []          # zdarzenia biezacej ramki do przekazania

    def groups(self):
        return frozenset(MOD_GROUP[k] for k in self.held)

    def flush_mods(self):
        for k, sent in self.held.items():
            if not sent:
                self.pending.append((EV_KEY, k, 1))
                self.held[k] = True

    BTN_HOLD_MAX = 1.0     # s - dluzej trzymany przycisk bez gestu = zwykle przytrzymanie (przekaz dalej)
    BTN_MOVE_MAX = 25      # px - ruch z wcisnietym przyciskiem = przeciaganie (przekaz dalej)

    def flush_button(self):
        st = self.shared.get("bbuf")
        if st and not st["flushed"]:
            self.pending.insert(0, (EV_KEY, st["code"], 1))
            st["flushed"] = True

    def gesture(self, name, raw):
        st = self.shared.get("bbuf")
        if st:
            st["used"] = True
        self.on_gesture(name, raw, frozenset(self.shared["buttons"]))

    def feed(self, t, c, v):
        st = self.shared.get("bbuf")
        if st and not st["flushed"] and time.monotonic() - st["t0"] > self.BTN_HOLD_MAX:
            self.flush_button()
        if t == EV_SYN:
            if self.pending:
                for e in self.pending:
                    if e is None:
                        self.ui.syn()
                    else:
                        self.ui.emit(*e)
                self.ui.syn()
                self.pending = []
            self.pinch_frame = False
            return
        if t == EV_MSC:
            return
        if t == EV_KEY and c in BTNS and v in (0, 1):
            if v == 1:
                self.shared["buttons"].add(c)
                if c == self.buffer_button() and not st:
                    self.shared["bbuf"] = {"code": c, "t0": time.monotonic(), "flushed": False,
                                           "used": False, "moved": 0}
                    return                                    # wstrzymaj - moze to gest z przyciskiem
            else:
                self.shared["buttons"].discard(c)
                if st and st["code"] == c:
                    self.shared["bbuf"] = None
                    if st["flushed"]:
                        self.pending.append((t, c, v))
                    elif not st["used"]:
                        self.pending += [(t, c, 1), None, (t, c, 0)]   # zwykle klikniecie
                    return                                    # uzyty do gestu - nie klikaj
            self.pending.append((t, c, v))
            return
        if t == EV_KEY:
            if self.raw_log and v != 2 and c not in BTNS:
                log.info("RAW klawisz %d %s (modyfikatory: %s)", c, v, "+".join(sorted(self.groups())) or "-")
            if c in MOD_GROUP:
                if v == 1:
                    self.held.setdefault(c, False)        # wstrzymaj - moze to poczatek gestu
                elif v == 0:
                    if self.held.pop(c, False):
                        self.pending.append((EV_KEY, c, 0))
                return
            if c in self.swallowed:
                if v == 0:
                    self.swallowed.discard(c)
                return
            if v == 1:
                gesture = GESTURES.get((self.groups(), c))
                if gesture:
                    self.swallowed.add(c)
                    self.gesture(gesture, f"{'+'.join(sorted(self.groups())) or '-'} key={c}")
                    return
                if self.held and not all(self.held.values()):
                    log.info("nieznany skrot z myszki: %s + %d (przekazuje dalej)",
                             "+".join(sorted(self.groups())), c)
                self.flush_mods()
            self.pending.append((t, c, v))
            return
        if t == EV_REL:
            if self.raw_log and c not in (REL_X, REL_Y):
                log.info("RAW kolko/przewijanie kod=%d %+d (modyfikatory: %s)", c, v, "+".join(sorted(self.groups())) or "-")
            if c in (REL_X, REL_Y):
                if st and not st["flushed"]:
                    st["moved"] += abs(v)
                    if st["moved"] > self.BTN_MOVE_MAX:
                        self.flush_button()
                k = self.get_scale()
                if k != 1.0:
                    f = v * k + self.rest[c]
                    v = int(f)
                    self.rest[c] = f - v
                    if not v:
                        return
            if c in (REL_WHEEL, REL_WHEEL_HI_RES):
                unsent = {MOD_GROUP[k] for k, s in self.held.items() if not s}
                if self.pinch_frame or any(g in unsent for g in PINCH_GROUPS):
                    if not self.pinch_frame:
                        self.gesture("pinch_out" if v > 0 else "pinch_in",
                                        f"{'+'.join(sorted(unsent))}+kolko {v:+d}")
                    self.pinch_frame = True
                    return
            self.pending.append((t, c, v))
            return
        self.pending.append((t, c, v))

    def reset(self):
        self.shared["bbuf"] = None
        self.held.clear()
        self.swallowed.clear()
        self.pending = []


class InputDev:
    def __init__(self, path, name, uniq):
        self.path, self.name, self.uniq = path, name, uniq
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, EVIOCGRAB, 1)
        self.tr = None

    def close(self):
        try:
            fcntl.ioctl(self.fd, EVIOCGRAB, 0)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def read_sys(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def find_inputs(target_addr=None):
    """Urzadzenia wejscia myszki CheerDots utworzone przez BlueZ (uhid). -> [(path, name, uniq)]"""
    out = []
    for ev in sorted(glob.glob("/sys/class/input/event*")):
        d = ev + "/device"
        name = read_sys(d + "/name")
        bus, ven, prod = (read_sys(f"{d}/id/{x}").lower() for x in ("bustype", "vendor", "product"))
        uniq = read_sys(d + "/uniq").upper()
        if (bus, ven) == (f"{OUR_BUS:04x}", f"{OUR_VENDOR:04x}"):
            continue                                            # nasze wlasne wirtualne urzadzenie
        is_bt = bus == "0005"
        looks = "cheerdots" in name.lower() or (ven, prod) == ("05ac", "024f")
        if not (is_bt and looks):
            continue
        if target_addr and uniq and C.norm_addr(uniq) != C.norm_addr(target_addr):
            continue                                            # inna myszka CheerDots
        out.append(("/dev/input/" + os.path.basename(ev), name, uniq))
    return out


# --------------------------------------------------------------------------- BlueZ przez busctl
async def run(*cmd, timeout=10):
    try:
        p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
    except FileNotFoundError as e:
        return 127, "", str(e)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            p.kill()
        except ProcessLookupError:
            pass
        await p.wait()
        return -1, "", "timeout"
    return p.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def _v(x):
    return x.get("data") if isinstance(x, dict) else x


async def bluez_objects():
    """-> (dziala?, {adres: {...}}) z org.freedesktop.DBus.ObjectManager.GetManagedObjects"""
    rc, out, err = await run("busctl", "--system", "--json=short", "call", "org.bluez", "/",
                             "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", timeout=5)
    if rc != 0:
        return False, {}
    try:
        data = json.loads(out)["data"][0]
    except (ValueError, KeyError, IndexError):
        return False, {}
    devs = {}
    for path, ifaces in data.items():
        d = ifaces.get("org.bluez.Device1")
        if not d:
            continue
        addr = C.norm_addr(_v(d.get("Address")))
        name = _v(d.get("Alias")) or _v(d.get("Name")) or ""
        batt = ifaces.get("org.bluez.Battery1", {})
        devs[addr] = {
            "address": addr, "name": name, "path": path,
            "paired": bool(_v(d.get("Paired"))), "bonded": bool(_v(d.get("Bonded"))),
            "trusted": bool(_v(d.get("Trusted"))), "connected": bool(_v(d.get("Connected"))),
            "rssi": _v(d.get("RSSI")), "battery": _v(batt.get("Percentage")) if batt else None,
            "cheerdots": "cheerdots" in name.lower(),
        }
    return True, devs


# --------------------------------------------------------------------------- sterownik
class Driver:
    def __init__(self, args):
        self.args = args
        self.cfg = C.load(args.config)
        self.ui = UInput()
        self.inputs = {}            # path -> InputDev
        self.bluez_ok = False
        self.devices = {}
        self.scan_proc = None
        self.scan_until = 0.0
        self.raw_log = args.raw_log
        self.shared = {"buttons": set(), "bbuf": None}     # wspolny stan przyciskow wszystkich wejsc myszki
        self.active_app = ""                                # klasa aktywnego okna (od GUI / skryptu KWin)
        self.pending_gesture = None                         # (gest, uchwyt timera, raw) czekajacy na podwojny
        self.state = {"connected": False, "battery": None, "address": None, "name": None,
                      "phase": "start", "error": None, "hint": None, "last_gesture": None,
                      "device": self.cfg.get("device"), "since": None, "scanning": False,
                      "mtu": None, "mtu_ok": False, "inputs": [], "active_app": "", "profile": None}

    def pointer_scale(self):
        try:
            return max(5, min(300, int(self.cfg.get("pointer_scale", 100)))) / 100.0
        except (TypeError, ValueError):
            return 1.0

    # ---------------------------------------------------------------- gesty
    def buffer_button(self):
        """Przycisk do wstrzymywania tylko wtedy, gdy jakis gest 'z przyciskiem' jest ustawiony (dla tej aplikacji)."""
        code = C.BUTTONS.get(self.cfg.get("gesture_button", "right"), C.BUTTONS["right"])[1]
        if any(C.gesture_active(self.cfg, f"{g}_btn", self.active_app) for g in C.SWIPES):
            return code
        return None

    def on_gesture(self, base, raw="", buttons=frozenset()):
        """Gest z myszki -> wariant (z przyciskiem / podwojny / zwykly) -> akcja."""
        app = self.active_app
        btn = C.BUTTONS.get(self.cfg.get("gesture_button", "right"), C.BUTTONS["right"])[1]
        if base in C.SWIPES and btn in buttons and C.gesture_active(self.cfg, f"{base}_btn", app):
            self.cancel_pending()
            return self.fire(f"{base}_btn", raw)
        if base in C.SWIPES and C.gesture_active(self.cfg, f"{base}_x2", app):
            p = self.pending_gesture
            if p and p[0] == base:                      # drugi taki sam gest w oknie czasu -> podwojny
                p[1].cancel()
                self.pending_gesture = None
                return self.fire(f"{base}_x2", raw)
            self.cancel_pending(fire=True)
            delay = max(150, min(1000, int(self.cfg.get("double_ms", 400)))) / 1000
            h = asyncio.get_running_loop().call_later(delay, self._fire_pending)
            self.pending_gesture = (base, h, raw)
            return
        self.cancel_pending(fire=True)
        self.fire(base, raw)

    def _fire_pending(self):
        p, self.pending_gesture = self.pending_gesture, None
        if p:
            self.fire(p[0], p[2])

    def cancel_pending(self, fire=False):
        p, self.pending_gesture = self.pending_gesture, None
        if p:
            p[1].cancel()
            if fire:
                self.fire(p[0], p[2])

    def fire(self, gesture, raw=""):
        gcfg, profile = C.effective_gesture(self.cfg, gesture, self.active_app)
        names = C.action_keys(gcfg)
        self.state["last_gesture"] = {"gesture": gesture, "time": time.time(), "profile": profile,
                                      "app": self.active_app,
                                      "action": gcfg.get("action") if gcfg.get("enabled", True) else "disabled"}
        log.info("gest %s %s%s-> %s", gesture, f"[{raw}] " if raw else "",
                 f"(profil {profile}) " if profile else "", "+".join(names) or "(wylaczony)")
        if names:
            self.ui.tap(names)

    fire_gesture = fire          # zgodnosc wstecz

    # ---------------------------------------------------------------- urzadzenia wejscia
    def target(self):
        """Wybrana myszka (adres) albo automatycznie pierwsza sparowana CheerDots."""
        dev = self.cfg.get("device")
        if dev and dev.get("address"):
            return dev["address"], dev.get("name") or "CheerDots"
        cands = sorted((d for d in self.devices.values() if d["cheerdots"] and d["paired"]),
                       key=lambda d: not d["connected"])
        if cands:
            return cands[0]["address"], cands[0]["name"]
        return None, None

    def update_inputs(self):
        addr, _ = self.target()
        found = {p: (n, u) for p, n, u in find_inputs(addr)}
        for p in list(self.inputs):
            if p not in found:
                self.drop_input(p, "zniknelo")
        for p, (n, u) in found.items():
            if p in self.inputs:
                continue
            try:
                d = InputDev(p, n, u)
            except OSError as e:
                log.warning("nie moge przejac %s (%s): %s", p, n, e)
                continue
            d.tr = Translator(self.ui, self.on_gesture, self.raw_log, self.pointer_scale,
                              shared=self.shared, buffer_button=self.buffer_button)
            asyncio.get_running_loop().add_reader(d.fd, self.on_readable, p)
            self.inputs[p] = d
            log.info("przejeto urzadzenie wejscia %s (%s %s)", p, n, u)
        self.state["inputs"] = [f"{d.path} {d.name}" for d in self.inputs.values()]

    def drop_input(self, p, why=""):
        d = self.inputs.pop(p, None)
        if not d:
            return
        try:
            asyncio.get_running_loop().remove_reader(d.fd)
        except Exception:
            pass
        d.close()
        self.ui.release_all()
        log.info("zwolniono %s %s", p, why)

    def on_readable(self, p):
        d = self.inputs.get(p)
        if not d:
            return
        try:
            data = os.read(d.fd, EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:
            self.drop_input(p, "(rozlaczona)")
            return
        if not data:
            self.drop_input(p, "(koniec)")
            return
        for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _, _, t, c, v = EVENT.unpack_from(data, i)
            try:
                d.tr.feed(t, c, v)
            except Exception as e:
                log.warning("blad przetwarzania zdarzenia: %r", e)

    # ---------------------------------------------------------------- stan
    def set_phase(self, phase, error=None):
        self.state["phase"] = phase
        self.state["error"] = error
        self.state["hint"] = HINTS.get(phase)

    async def poll(self):
        while True:
            try:
                await self.poll_once()
            except Exception as e:
                log.warning("odswiezanie stanu: %r", e)
            await asyncio.sleep(1.5)

    async def poll_once(self):
        mtu = C.bluez_mtu()
        self.state["mtu"], self.state["mtu_ok"] = mtu, mtu == C.BLUEZ_MTU
        self.bluez_ok, self.devices = await bluez_objects()
        self.update_inputs()
        addr, name = self.target()
        d = self.devices.get(addr) if addr else None
        connected = bool(d and d["connected"])
        was = self.state["connected"]
        self.state.update(address=addr, name=(d or {}).get("name") or name,
                          battery=(d or {}).get("battery") if connected else None,
                          device=self.cfg.get("device") or ({"address": addr, "name": name, "auto": True} if addr else None),
                          connected=connected and bool(self.inputs))
        if self.state["connected"] and not was:
            self.state["since"] = time.time()
            log.info("myszka gotowa: %s (%s), bateria %s%%", self.state["name"], addr, self.state["battery"])
        elif was and not self.state["connected"]:
            log.info("myszka rozlaczona")
        if not self.bluez_ok:
            self.set_phase("no_bluez")
        elif not self.state["mtu_ok"]:
            self.set_phase("mtu_missing", f"ExchangeMTU = {mtu if mtu else 'domyślne (517)'}")
        elif not addr:
            self.set_phase("no_device")
        elif not connected:
            self.set_phase("searching")
        elif not self.inputs:
            self.set_phase("no_input")
        else:
            self.set_phase("connected")
        if self.scan_proc and (time.time() > self.scan_until or self.scan_proc.returncode is not None):
            await self.stop_scan()

    # ---------------------------------------------------------------- skanowanie / parowanie (bluetoothctl)
    async def start_scan(self, seconds):
        self.scan_until = time.time() + seconds
        if self.scan_proc and self.scan_proc.returncode is None:
            return
        try:
            self.scan_proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "--timeout", str(int(seconds) + 5), "scan", "le",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL)
            self.state["scanning"] = True
        except FileNotFoundError:
            self.scan_proc = None

    async def stop_scan(self):
        p, self.scan_proc = self.scan_proc, None
        self.state["scanning"] = False
        if p and p.returncode is None:
            try:
                p.terminate()
                await asyncio.wait_for(p.wait(), 3)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass
            await run("bluetoothctl", "scan", "off", timeout=5)

    async def pair(self, addr):
        await self.stop_scan()
        log.info("parowanie %s", addr)
        rc, out, err = await run("bluetoothctl", "--timeout", "25", "--agent", "NoInputNoOutput", "pair", addr, timeout=35)
        ok, devs = await bluez_objects()
        d = devs.get(addr, {})
        if not d.get("paired"):
            msg = (out + err).strip().splitlines()[-1:] or ["brak odpowiedzi"]
            raise RuntimeError(f"Parowanie nie powiodło się ({msg[0]}). Sparuj myszkę w Ustawieniach "
                               "systemowych → Bluetooth i wybierz ją ponownie.")
        await run("bluetoothctl", "trust", addr, timeout=10)
        await run("bluetoothctl", "connect", addr, timeout=20)
        return d

    # ---------------------------------------------------------------- IPC
    async def ipc_client(self, reader, writer):
        try:
            while line := await reader.readline():
                try:
                    resp = await self.ipc_handle(json.loads(line))
                except Exception as e:
                    log.info("IPC: %r", e)
                    resp = {"ok": False, "error": str(e)}
                writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    def device_list(self):
        now = time.time()
        res = [d for d in self.devices.values() if d["paired"] or d.get("rssi") is not None or now < self.scan_until]
        return sorted(res, key=lambda d: (not d["cheerdots"], not d["paired"], not d["name"], d["address"]))

    async def ipc_handle(self, req):
        cmd = req.get("cmd")
        if cmd == "status":
            return {"ok": True, "status": self.state}
        if cmd == "active_app":
            app = str(req.get("app") or "")[:200]
            if app != self.active_app:
                self.active_app = app
                p = C.find_profile(self.cfg, app)
                self.state.update(active_app=app, profile=p["name"] if p else None)
                log.debug("aktywne okno: %s (profil %s)", app, self.state["profile"])
            return {"ok": True, "profile": self.state["profile"]}
        if cmd == "get_config":
            return {"ok": True, "config": self.cfg, "actions": {k: v[0] for k, v in C.ACTIONS.items()},
                    "gestures": C.GESTURES}
        if cmd == "set_config":
            new = C.merged(req.get("config", {}))
            new["device"] = self.cfg.get("device")          # urzadzenie zmienia tylko select/forget
            for scope, gs in [("", new["gestures"])] + [(p["name"] + ": ", p["gestures"]) for p in new["profiles"]]:
                for gid, g in gs.items():
                    if g["enabled"] and g["action"] == "custom":
                        try:
                            C.parse_combo(g["custom"])
                        except ValueError as e:
                            raise ValueError(f"{scope}{C.GESTURES.get(gid, gid)}: {e}")
            C.save(new, self.args.config)
            self.cfg = new
            p = C.find_profile(self.cfg, self.active_app)
            self.state["profile"] = p["name"] if p else None
            return {"ok": True}
        if cmd in ("devices", "scan_results"):
            return {"ok": True, "results": self.device_list(), "scanning": self.state["scanning"],
                    "bluez": self.bluez_ok}
        if cmd == "scan_start":
            await self.start_scan(float(req.get("seconds", 30)))
            return {"ok": True, "scanning": True, "adapter": self.bluez_ok}
        if cmd == "scan_stop":
            await self.stop_scan()
            return {"ok": True}
        if cmd == "pair":
            addr = C.norm_addr(req.get("address"))
            d = await self.pair(addr)
            self.cfg["device"] = {"address": addr, "name": d.get("name", "")}
            C.save(self.cfg, self.args.config)
            await self.poll_once()
            return {"ok": True}
        if cmd == "select_device":
            addr = C.norm_addr(req.get("address"))
            if not addr:
                raise ValueError("brak adresu")
            d = self.devices.get(addr, {})
            if d and not d.get("paired"):
                d = await self.pair(addr)
            self.cfg["device"] = {"address": addr, "name": req.get("name") or d.get("name", "")}
            C.save(self.cfg, self.args.config)
            log.info("wybrano urzadzenie %s (%s)", addr, self.cfg["device"]["name"])
            for p in list(self.inputs):
                self.drop_input(p, "(zmiana urzadzenia)")
            await self.poll_once()
            return {"ok": True}
        if cmd == "forget_device":
            addr, _ = self.target()
            self.cfg["device"] = None
            C.save(self.cfg, self.args.config)
            for p in list(self.inputs):
                self.drop_input(p, "(zapomniano)")
            if addr and req.get("unpair", True):
                await run("bluetoothctl", "remove", addr, timeout=10)
                log.info("usunieto parowanie %s", addr)
            await self.poll_once()
            return {"ok": True}
        if cmd == "reconnect":
            addr, _ = self.target()
            if addr:
                await run("bluetoothctl", "disconnect", addr, timeout=10)
                await asyncio.sleep(1)
                await run("bluetoothctl", "connect", addr, timeout=20)
            return {"ok": True}
        return {"ok": False, "error": f"nieznane polecenie {cmd}"}

    async def start_ipc(self):
        path = self.args.socket
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        server = await asyncio.start_unix_server(self.ipc_client, path=path)
        os.chmod(path, 0o666)
        log.info("gniazdo IPC: %s", path)
        return server

    async def run(self):
        await self.start_ipc()
        mtu = C.bluez_mtu()
        if mtu != C.BLUEZ_MTU:
            log.warning("BlueZ: ExchangeMTU = %s (potrzebne %d) - myszka nie bedzie wysylac gestow. "
                        "Napraw: sudo cheerdots-ctl setup", mtu or "domyslne 517", C.BLUEZ_MTU)
        await self.poll()

    def close(self):
        for p in list(self.inputs):
            self.drop_input(p, "(koniec pracy)")
        self.ui.close()


def main():
    ap = argparse.ArgumentParser(description="CheerDots 2 - demon sterownika (BlueZ)")
    ap.add_argument("--config", default=C.CONFIG_PATH)
    ap.add_argument("--socket", default=C.SOCKET_PATH)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--raw-log", action="store_true", help="loguj surowe klawisze z myszki (diagnostyka)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    async def amain():
        import signal
        drv = Driver(a)
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await drv.run()
        except asyncio.CancelledError:
            log.info("zatrzymywanie sterownika")
        finally:
            await drv.stop_scan()
            drv.close()

    asyncio.run(amain())


if __name__ == "__main__":
    main()
