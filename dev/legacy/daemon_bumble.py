#!/usr/bin/env python3
"""
CheerDots 2 - demon sterownika (root, usluga systemd 'cheerdots').

- laczy sie z WYBRANA myszka wlasnym stosem Bluetooth (Google Bumble), zachowujac sie jak
  Windows/macOS, dzieki czemu firmware wysyla gesty dwoma palcami,
- przekazuje ruch/klikniecia/kolko przez /dev/uinput, gesty zamienia na akcje z konfiguracji,
- obsluguje bledy: limity czasu, ponawianie, wykrywanie resetu/znikniecia adaptera,
- gniazdo IPC /run/cheerdots/cheerdots.sock (JSON, linia = polecenie) dla GUI:
    status, get_config, set_config, adapters, scan_start, scan_stop, scan_results,
    select_device {address,name}, forget_device, reconnect
"""
import argparse
import asyncio
import fcntl
import json
import logging
import os
import socket
import struct
import time

from bumble import hci, l2cap
from bumble.device import ConnectionParametersPreferences, Device, Peer
from bumble.keys import JsonKeyStore
from bumble.pairing import PairingConfig, PairingDelegate
from bumble.transport import open_transport

try:
    from . import config as C
except ImportError:
    import config as C

log = logging.getLogger("cheerdots")
OWN_ADDR = hci.OwnAddressType.PUBLIC        # jak Windows: komputer laczy sie swoim publicznym adresem

# --------------------------------------------------------------------------- uchwyty GATT (firmware CHP04.00001)
H_BATT, H_BATT_CCCD = 0x001E, 0x001F
H_KBD, H_KBD_CCCD = 0x0040, 0x0041
H_LED = 0x0044
H_MOUSE, H_MOUSE_CCCD = 0x004A, 0x004B
H_CONS, H_CONS_CCCD = 0x004E, 0x004F

# --------------------------------------------------------------------------- rozpoznawanie gestow
HID_RIGHT, HID_LEFT, HID_DOWN, HID_UP, HID_TAB, HID_D, HID_F3 = 0x4F, 0x50, 0x51, 0x52, 0x2B, 0x07, 0x3C
M_CTRL, M_WIN = 0x01, 0x08
GESTURE_REPORTS = {
    (M_CTRL, HID_RIGHT): "swipe_left",  (M_CTRL, HID_LEFT): "swipe_right",       # styl macOS
    (0x00, HID_F3): "swipe_up",         (M_WIN, HID_F3): "swipe_down",
    (M_CTRL | M_WIN, HID_RIGHT): "swipe_left", (M_CTRL | M_WIN, HID_LEFT): "swipe_right",  # styl Windows
    (M_WIN, HID_TAB): "swipe_up",       (M_WIN, HID_D): "swipe_down",
    (M_CTRL, HID_UP): "swipe_up",       (M_CTRL, HID_DOWN): "swipe_down",
    # szczypanie jako skrot klawiszowy (Cmd/Ctrl + '=' / '-')
    (M_WIN, 0x2E): "pinch_out", (M_WIN, 0x2D): "pinch_in", (M_CTRL, 0x2E): "pinch_out", (M_CTRL, 0x2D): "pinch_in",
    (M_WIN | 0x02, 0x2E): "pinch_out", (M_CTRL | 0x02, 0x2E): "pinch_out",          # z Shift ('+')
}
# szczypanie jako kolko z wcisnietym modyfikatorem (Ctrl+kolko = Windows, Cmd+kolko = macOS)
PINCH_MODS = (M_CTRL, M_WIN, 0x10, 0x80)       # LCtrl, LGUI, RCtrl, RGUI

# --------------------------------------------------------------------------- komunikaty dla uzytkownika
HINTS = {
    "no_adapter": "Nie znaleziono adaptera Bluetooth. Sprawdź, czy Bluetooth jest włączony (rfkill) "
                  "albo wybierz inny adapter w ustawieniach.",
    "adapter_busy": "Adapter jest zajęty przez inny program (np. systemowy Bluetooth). "
                    "Sterownik ponawia próbę.",
    "no_device": "Kliknij „Wybierz urządzenie”, włącz w myszce tryb parowania i wskaż ją na liście.",
    "searching": "Włącz myszkę albo nią porusz. Jeśli była parowana z innym komputerem, "
                 "wybierz ją ponownie w ustawieniach.",
    "connect_timeout": "Myszka nie odpowiedziała na połączenie. Upewnij się, że jest włączona i blisko.",
    "pair_failed": "Parowanie nie powiodło się. Włącz w myszce tryb parowania (niebieska dioda miga) "
                   "i wybierz ją ponownie.",
    "setup_failed": "Połączono, ale konfiguracja myszki się nie powiodła. Ponawiam.",
    "adapter_lost": "Adapter Bluetooth zniknął lub się zresetował. Ponowne uruchamianie…",
    "reconnecting": "Myszka rozłączona (wyłączona lub poza zasięgiem). Połączy się sama po włączeniu.",
}

# --------------------------------------------------------------------------- uinput
EV_SYN, EV_KEY, EV_REL = 0, 1, 2
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0, 1, 6, 8
BTNS = (0x110, 0x111, 0x112, 0x113, 0x114)
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405C5503, 0x5501, 0x5502
MODS = [29, 42, 56, 125, 97, 54, 100, 126]
HID2KEY = {
    **dict(zip(range(0x04, 0x1E), [30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25,
                                     16, 19, 31, 20, 22, 47, 17, 45, 21, 44])),
    **dict(zip(range(0x1E, 0x28), range(2, 12))),
    0x28: 28, 0x29: 1, 0x2A: 14, 0x2B: 15, 0x2C: 57, 0x2D: 12, 0x2E: 13,
    0x4F: 106, 0x50: 105, 0x51: 108, 0x52: 103, 0x4A: 102, 0x4D: 107, 0x4B: 104, 0x4E: 109,
    **dict(zip(range(0x3A, 0x44), range(59, 69))), 0x44: 87, 0x45: 88,
}
CONSUMER = {0: 116, 1: 172, 2: 158, 3: 139, 4: 115, 5: 114, 6: 164, 7: 0x174, 8: 165, 9: 163}


class UInput:
    def __init__(self, name="CheerDots 2"):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
        for k in set(MODS) | set(HID2KEY.values()) | set(CONSUMER.values()) | {v for v in C.KEYCODES.values() if v > 0} | set(BTNS):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
        for r in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL):
            fcntl.ioctl(self.fd, UI_SET_RELBIT, r)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x05, 0x05AC, 0x024F, 1, name.encode()[:79], 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.btn_state = 0
        self.consumer_down = set()

    def emit(self, t, c, v):
        now = time.time()
        try:
            os.write(self.fd, struct.pack("llHHi", int(now), int((now % 1) * 1e6), t, c, v))
        except OSError as e:
            log.debug("uinput: %s", e)

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
            self.syn()
        for c in reversed(keys):
            self.emit(EV_KEY, c, 0)
        self.syn()

    def tap(self, names):
        self.tap_codes([C.KEYCODES[n] for n in names if n in C.KEYCODES])

    def release_all(self):
        """Po rozlaczeniu: zwolnij przyciski, zeby nic nie 'zawislo' wcisniete."""
        for i, code in enumerate(BTNS):
            if self.btn_state & (1 << i):
                self.emit(EV_KEY, code, 0)
        for k in self.consumer_down:
            self.emit(EV_KEY, k, 0)
        self.btn_state, self.consumer_down = 0, set()
        self.syn()

    def mouse(self, r: bytes):
        if len(r) < 7:
            return
        btn = r[0] | (r[1] << 8)
        x = r[2] | ((r[3] & 0x0F) << 8); x -= 4096 if x & 0x800 else 0
        y = (r[3] >> 4) | (r[4] << 4);  y -= 4096 if y & 0x800 else 0
        wheel = r[5] - 256 if r[5] & 0x80 else r[5]
        pan = r[6] - 256 if r[6] & 0x80 else r[6]
        for i, code in enumerate(BTNS):
            if (btn ^ self.btn_state) & (1 << i):
                self.emit(EV_KEY, code, 1 if btn & (1 << i) else 0)
        self.btn_state = btn
        if x: self.emit(EV_REL, REL_X, x)
        if y: self.emit(EV_REL, REL_Y, y)
        if wheel: self.emit(EV_REL, REL_WHEEL, wheel)
        if pan: self.emit(EV_REL, REL_HWHEEL, pan)
        self.syn()

    def consumer(self, r: bytes):
        bits = int.from_bytes(bytes(r[:2]).ljust(2, b"\0"), "little")
        new = {CONSUMER[b] for b in CONSUMER if bits & (1 << b)}
        for k in self.consumer_down - new: self.emit(EV_KEY, k, 0)
        for k in new - self.consumer_down: self.emit(EV_KEY, k, 1)
        self.consumer_down = new
        self.syn()

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            os.close(self.fd)
        except OSError:
            pass


class StepError(Exception):
    """Blad etapu polaczenia z kodem podpowiedzi (HINTS)."""
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


# --------------------------------------------------------------------------- sterownik
class Driver:
    def __init__(self, args):
        self.args = args
        self.cfg = C.load(args.config)
        self.ui = UInput()
        self.hci_idx = None
        self.dev = None
        self.conn = None
        self.wake = asyncio.Event()
        self.target_future = None
        self.gui_scan_until = 0.0           # GUI chce wyniki skanowania do tego czasu
        self.scan_results = {}              # adres -> {...}
        self.scanning = False
        self.force_pair = False
        self.device_mods = 0
        self.raw_log = getattr(args, "raw_log", False)
        self.state = {"connected": False, "battery": None, "address": None, "name": None,
                      "phase": "start", "error": None, "hint": None, "last_gesture": None,
                      "hci": None, "device": self.cfg.get("device"), "since": None, "scanning": False}

    # ---------------------------------------------------------------- stan
    def set_phase(self, phase, error=None, hint_code=None):
        self.state["phase"] = phase
        self.state["error"] = error
        code = hint_code or (phase if phase in HINTS else None)
        self.state["hint"] = HINTS.get(code) if code else None

    # ---------------------------------------------------------------- gesty / dane
    def fire_gesture(self, gesture, raw=""):
        gcfg = self.cfg["gestures"].get(gesture, {})
        names = C.action_keys(gcfg)
        self.state["last_gesture"] = {"gesture": gesture, "time": time.time(),
                                      "action": gcfg.get("action") if gcfg.get("enabled", True) else "disabled"}
        log.info("gest %s %s-> %s", gesture, f"[{raw}] " if raw else "", "+".join(names) or "(wylaczony)")
        if names:
            self.ui.tap(names)

    def on_keyboard(self, v: bytes):
        if len(v) < 8:
            return
        mods, keys = v[0], [k for k in v[2:8] if k]
        # trzymany sam modyfikator (bez klawisza) = poczatek szczypania jako Ctrl/Cmd+kolko
        self.device_mods = mods if not keys else 0
        if self.raw_log:
            log.info("RAW klawiatura: %s", v.hex(" "))
        if not keys:
            return
        gesture = GESTURE_REPORTS.get((mods, keys[0]))
        if gesture:
            self.fire_gesture(gesture, v.hex(" "))
            return
        log.info("nieznany skrot z myszki: %s (przekazuje dalej)", v.hex(" "))
        codes = [MODS[i] for i in range(8) if mods & (1 << i)] + [HID2KEY[k] for k in keys if k in HID2KEY]
        self.ui.tap_codes(codes)

    def on_mouse(self, v: bytes):
        if self.raw_log and (len(v) < 7 or v[0] or v[1] or v[5] or v[6] or self.device_mods):
            log.info("RAW mysz: %s (mods=%02x)", v.hex(" "), self.device_mods)
        if len(v) >= 7 and v[5] and any(self.device_mods & m for m in PINCH_MODS):
            wheel = v[5] - 256 if v[5] & 0x80 else v[5]
            self.fire_gesture("pinch_out" if wheel > 0 else "pinch_in", f"mods={self.device_mods:02x} wheel={wheel}")
            v = bytes(v[:5]) + b"\x00" + bytes(v[6:])  # kolko zuzyte przez gest
        self.ui.mouse(v)

    def on_battery(self, v: bytes):
        if v:
            self.state["battery"] = v[0]

    # ---------------------------------------------------------------- skanowanie
    def on_advertisement(self, adv):
        try:
            name = adv.data.get(0x09) or adv.data.get(0x08) or ""
            if isinstance(name, bytes):
                name = name.decode(errors="ignore")
            name = str(name)
            addr = C.norm_addr(adv.address)
            if time.time() < self.gui_scan_until:
                prev = self.scan_results.get(addr, {})
                self.scan_results[addr] = {
                    "address": addr, "name": name or prev.get("name", ""), "rssi": adv.rssi,
                    "last_seen": time.time(), "cheerdots": "cheerdots" in (name or prev.get("name", "")).lower(),
                    "connectable": bool(getattr(adv, "is_connectable", True)),
                }
            dev = self.cfg.get("device")
            f = self.target_future
            if dev and f and not f.done() and addr == C.norm_addr(dev.get("address")):
                f.set_result((adv.address, name or dev.get("name", "")))
        except Exception as e:
            log.debug("adv: %s", e)

    async def update_scanning(self):
        want = time.time() < self.gui_scan_until or (self.target_future is not None and not self.target_future.done())
        if self.dev is None or want == self.scanning:
            return
        try:
            if want:
                await self.dev.start_scanning(own_address_type=OWN_ADDR, filter_duplicates=False)
            else:
                await self.dev.stop_scanning()
            self.scanning = want
        except Exception as e:
            log.info("skanowanie: %s", e)
        self.state["scanning"] = self.scanning

    async def scan_keeper(self):
        while True:
            await self.update_scanning()
            await asyncio.sleep(1)

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

    async def ipc_handle(self, req):
        cmd = req.get("cmd")
        if cmd == "status":
            self.state["device"] = self.cfg.get("device")
            return {"ok": True, "status": self.state}
        if cmd == "get_config":
            return {"ok": True, "config": self.cfg, "actions": {k: v[0] for k, v in C.ACTIONS.items()},
                    "gestures": C.GESTURES}
        if cmd == "set_config":
            new = C.merged(req.get("config", {}))
            new["device"] = self.cfg.get("device")          # urzadzenie zmienia tylko select/forget
            for g in new["gestures"].values():
                if g["enabled"] and g["action"] == "custom":
                    C.parse_combo(g["custom"])
            adapter_changed = str(new["hci"]) != str(self.cfg["hci"])
            C.save(new, self.args.config)
            self.cfg = new
            if adapter_changed:
                self.restart_controller = True
                self.wake.set()
            return {"ok": True}
        if cmd == "adapters":
            return {"ok": True, "adapters": [C.adapter_info(i) for i in C.list_adapters()], "current": self.hci_idx}
        if cmd == "scan_start":
            self.gui_scan_until = time.time() + float(req.get("seconds", 30))
            if req.get("clear", True):
                self.scan_results = {}
            await self.update_scanning()
            return {"ok": True, "scanning": self.scanning, "adapter": self.dev is not None}
        if cmd == "scan_stop":
            self.gui_scan_until = 0
            await self.update_scanning()
            return {"ok": True}
        if cmd == "scan_results":
            now = time.time()
            res = sorted((r for r in self.scan_results.values() if now - r["last_seen"] < 20),
                         key=lambda r: (not r["cheerdots"], not r["name"], -(r["rssi"] or -127)))
            return {"ok": True, "results": res, "scanning": self.scanning}
        if cmd == "select_device":
            addr = C.norm_addr(req.get("address"))
            if not addr:
                raise ValueError("brak adresu")
            self.cfg["device"] = {"address": addr, "name": req.get("name") or ""}
            C.save(self.cfg, self.args.config)
            await self._forget_keys()
            self.force_pair = True
            self.gui_scan_until = 0
            log.info("wybrano urzadzenie %s (%s)", addr, req.get("name"))
            await self._drop_connection()
            self.state.update(connected=False, name=req.get("name"), address=addr, battery=None)
            self.set_phase("searching")
            return {"ok": True}
        if cmd == "forget_device":
            self.cfg["device"] = None
            C.save(self.cfg, self.args.config)
            await self._forget_keys()
            await self._drop_connection()
            self.state.update(connected=False, name=None, address=None, battery=None)
            self.set_phase("no_device")
            return {"ok": True}
        if cmd == "reconnect":
            await self._drop_connection()
            return {"ok": True}
        return {"ok": False, "error": f"nieznane polecenie {cmd}"}

    async def _forget_keys(self):
        try:
            if self.dev and self.dev.keystore:
                await self.dev.keystore.delete_all()
            elif os.path.exists(self.args.keystore):
                os.remove(self.args.keystore)
        except Exception as e:
            log.info("usuwanie kluczy: %s", e)

    async def _drop_connection(self):
        self.wake.set()
        if self.target_future and not self.target_future.done():
            self.target_future.cancel()
        if self.conn:
            try:
                await asyncio.wait_for(self.conn.disconnect(), 5)
            except Exception:
                pass

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

    # ---------------------------------------------------------------- adapter
    @staticmethod
    def hci_is_up(idx):
        """HCIGETDEVINFO -> flaga HCI_UP. None gdy nie da sie sprawdzic."""
        try:
            s = socket.socket(31, socket.SOCK_RAW, 1)
            buf = bytearray(struct.pack("H", idx) + bytes(126))
            fcntl.ioctl(s.fileno(), 0x800448D3, buf)
            s.close()
            return bool(struct.unpack_from("I", buf, 16)[0] & 1)
        except OSError:
            return None

    @classmethod
    def hci_down(cls, idx):
        """Wylacz adapter tylko gdy jest wlaczony (zbedne wylaczanie zawiesza niektore adaptery Intel)."""
        if cls.hci_is_up(idx) is False:
            return
        try:
            s = socket.socket(31, socket.SOCK_RAW, 1)
            fcntl.ioctl(s.fileno(), 0x400448CA, idx)
            s.close()
        except OSError:
            pass

    @staticmethod
    def adapter_recover(idx):
        """Twardy reset adaptera USB (jak odlaczenie i podlaczenie) - gdy przestal odpowiadac."""
        try:
            intf = os.path.realpath(f"/sys/class/bluetooth/hci{idx}/device")
            usbdev = os.path.dirname(intf)
            auth = os.path.join(usbdev, "authorized")
            if not os.path.exists(auth):
                log.warning("reset adaptera: brak %s", auth)
                return False
            log.warning("adapter hci%d nie odpowiada - reset urzadzenia USB %s", idx, os.path.basename(usbdev))
            with open(auth, "w") as f:
                f.write("0")
            time.sleep(1.5)
            with open(auth, "w") as f:
                f.write("1")
            return True
        except OSError as e:
            log.warning("reset adaptera nie powiodl sie: %s", e)
            return False

    async def open_controller(self):
        while True:
            want = self.args.hci if self.args.hci is not None else self.cfg.get("hci", "auto")
            idx = C.resolve_hci(want)
            if idx is None:
                self.set_phase("no_adapter", f"brak adaptera ({want})")
                await asyncio.sleep(3)
                continue
            for attempt in range(10):
                self.hci_down(idx)
                await asyncio.sleep(0.5)
                try:
                    t = await open_transport(f"hci-socket:{idx}")
                    self.hci_idx = idx
                    self.state["hci"] = idx
                    return t
                except OSError as e:
                    self.set_phase("adapter_busy", f"hci{idx}: {e.strerror or e}")
                    if attempt in (0, 4, 9):
                        log.info("adapter hci%d zajety (%s), proba %d/10", idx, e.strerror or e, attempt + 1)
                    await asyncio.sleep(1)
            await asyncio.sleep(3)

    async def adapter_watch(self):
        """Konczy sie, gdy adapter zniknie albo przestanie odpowiadac."""
        fails = 0
        while True:
            await asyncio.sleep(3)
            if not os.path.exists(f"/sys/class/bluetooth/hci{self.hci_idx}"):
                log.warning("adapter hci%s zniknal", self.hci_idx)
                return
            if getattr(self, "restart_controller", False):
                self.restart_controller = False
                log.info("zmiana adaptera - restart")
                return
            try:
                await asyncio.wait_for(self.dev.host.send_command(hci.HCI_Read_BD_ADDR_Command()), 3)
                fails = 0
            except Exception as e:
                fails += 1
                log.warning("adapter nie odpowiada (%s) %d/3", e, fails)
                if fails >= 3:
                    return

    # ---------------------------------------------------------------- glowna petla
    async def run(self):
        await self.start_ipc()
        fails = 0
        while True:
            ok_run = False
            try:
                await self.run_controller()
                ok_run = True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("kontroler: %s", e)
                fails = 0 if self.dev is not None and self.state.get("connected") else fails + 1
            self.dev, self.conn, self.scanning = None, None, False
            if fails >= 1 and self.hci_idx is not None and not ok_run:
                # adapter sie zawiesil - twardy reset USB i chwila na wczytanie firmware
                if await asyncio.get_running_loop().run_in_executor(None, self.adapter_recover, self.hci_idx):
                    self.set_phase("adapter_lost", "reset adaptera USB")
                    await asyncio.sleep(5)
                    fails = 0
            log.info("ponowne uruchomienie adaptera za 3 s")
            self.ui.release_all()
            self.state.update(connected=False, scanning=False)
            self.set_phase("adapter_lost")
            await asyncio.sleep(3)

    async def run_controller(self):
        self.set_phase("adapter")
        transport = await self.open_controller()
        async with transport as (src, sink):
            dev = Device.with_hci("DESKTOP", hci.Address("F0:F1:F2:F3:F4:F5"), src, sink)
            dev.keystore = JsonKeyStore(namespace=None, filename=self.args.keystore)
            dev.pairing_config_factory = self.pairing_config
            try:
                await asyncio.wait_for(dev.power_on(), 25)
            except asyncio.TimeoutError:
                raise RuntimeError(f"adapter hci{self.hci_idx} nie odpowiada przy uruchamianiu (reset sterownika BT?)")
            dev.on("advertisement", self.on_advertisement)
            self.dev = dev
            log.info("adapter hci%d (%s) przejety", self.hci_idx, dev.public_address)
            tasks = [asyncio.ensure_future(x) for x in (self.device_loop(), self.adapter_watch(), self.scan_keeper())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    if t.exception():
                        raise t.exception()
            finally:
                for t in tasks:
                    t.cancel()
                await self.close_controller(dev)

    async def close_controller(self, dev):
        """Porzadne zamkniecie: rozlacz myszke i zresetuj kontroler, zanim oddamy go systemowi."""
        try:
            if self.conn:
                await asyncio.wait_for(self.conn.disconnect(), 2)
        except Exception:
            pass
        try:
            await asyncio.wait_for(dev.host.send_command(hci.HCI_Reset_Command()), 2)
        except Exception:
            pass
        self.ui.release_all()

    def pairing_config(self, conn):
        KD = PairingDelegate.KeyDistribution
        IO = PairingDelegate.IoCapability
        io = {"kbd_display": IO.DISPLAY_OUTPUT_AND_KEYBOARD_INPUT, "display_yesno": IO.DISPLAY_OUTPUT_AND_YES_NO_INPUT,
              "noio": IO.NO_OUTPUT_NO_INPUT}[self.args.io_cap]
        ini = (KD.DISTRIBUTE_ENCRYPTION_KEY if self.args.keydist == "linux" else KD.DISTRIBUTE_IDENTITY_KEY) \
            | KD.DISTRIBUTE_SIGNING_KEY | KD.DISTRIBUTE_LINK_KEY
        delegate = PairingDelegate(io_capability=io, local_initiator_key_distribution=ini,
                                   local_responder_key_distribution=KD.DISTRIBUTE_ENCRYPTION_KEY
                                   | KD.DISTRIBUTE_IDENTITY_KEY | KD.DISTRIBUTE_SIGNING_KEY | KD.DISTRIBUTE_LINK_KEY)
        return PairingConfig(sc=True, mitm=True, bonding=True, delegate=delegate,
                             identity_address_type=hci.AddressType.PUBLIC_DEVICE)

    async def device_loop(self):
        backoff = 2
        while True:
            self.wake.clear()
            dev = self.cfg.get("device")
            if not dev:
                self.state.update(connected=False, name=None, address=None)
                self.set_phase("no_device")
                await self.wake.wait()
                continue
            self.set_phase("searching")
            self.state.update(name=dev.get("name"), address=dev.get("address"))
            self.target_future = asyncio.get_running_loop().create_future()
            await self.update_scanning()
            waker = asyncio.ensure_future(self.wake.wait())
            done, _ = await asyncio.wait({self.target_future, waker}, return_when=asyncio.FIRST_COMPLETED)
            waker.cancel()
            if self.target_future.cancelled() or not self.target_future.done():
                self.target_future = None
                continue                                   # zmiana urzadzenia/konfiguracji
            address, name = self.target_future.result()
            self.target_future = None
            await self.update_scanning()
            try:
                await self.session(address, name)
                backoff = 2
            except asyncio.CancelledError:
                raise
            except StepError as e:
                log.warning("polaczenie: %s", e)
                self.set_phase("error", e.detail or e.code, e.code)
            except Exception as e:
                log.warning("polaczenie: %r", e)
                self.set_phase("error", str(e), "setup_failed")
            self.conn = None
            self.device_mods = 0
            self.ui.release_all()
            self.state.update(connected=False, since=None)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15)

    async def session(self, address, name):
        dev = self.dev
        self.set_phase("connecting")
        if self.args.conn:         # test: wlasne parametry polaczenia "min_ms,max_ms,latency,timeout_ms"
            mn, mx, lat, to = (float(x) for x in self.args.conn.split(","))
            prefs = {hci.Phy.LE_1M: ConnectionParametersPreferences(
                connection_interval_min=mn, connection_interval_max=mx, max_latency=int(lat), supervision_timeout=int(to))}
        elif self.args.linux_conn:   # domyslne parametry BlueZ dla LE HID
            prefs = {hci.Phy.LE_1M: ConnectionParametersPreferences(
                connection_interval_min=30, connection_interval_max=50, max_latency=0, supervision_timeout=420)}
        else:
            prefs = {hci.Phy.LE_1M: ConnectionParametersPreferences(
                connection_interval_min=7.5, connection_interval_max=7.5, max_latency=0, supervision_timeout=3000)}
        try:
            conn = await dev.connect(address, own_address_type=OWN_ADDR, timeout=15,
                                     connection_parameters_preferences=prefs)
        except Exception as e:
            raise StepError("connect_timeout", str(e) or type(e).__name__)
        self.conn = conn
        if self.args.ll_features:   # test: jak BlueZ - LE Read Remote Features zaraz po polaczeniu
            try:
                await asyncio.wait_for(dev.host.send_command(
                    hci.HCI_LE_Read_Remote_Features_Command(connection_handle=conn.handle)), 3)
                await asyncio.sleep(0.08)
                log.info("test: LE Read Remote Features wyslane")
            except Exception as e:
                log.info("test: read remote features: %s", e)
        done = asyncio.Event()
        conn.on("disconnection", lambda *a: done.set())

        self.set_phase("pairing")
        known = (not self.force_pair) and await dev.keystore.get(str(conn.peer_address))
        try:
            if known:
                try:
                    await asyncio.wait_for(conn.encrypt(), 15)
                except Exception as e:
                    log.info("zapisany klucz nie pasuje (%s) - paruje od nowa", e)
                    await asyncio.wait_for(conn.pair(), 40)
            else:
                await asyncio.wait_for(conn.pair(), 40)
        except Exception as e:
            raise StepError("pair_failed", str(e) or type(e).__name__)
        self.force_pair = False
        if self.args.ping:          # test: jak BlueZ - Write Authenticated Payload Timeout 30 s po szyfrowaniu
            try:
                await asyncio.wait_for(dev.host.send_command(hci.HCI_Write_Authenticated_Payload_Timeout_Command(
                    connection_handle=conn.handle, authenticated_payload_timeout=3000)), 3)
                log.info("test: Authenticated Payload Timeout ustawiony")
            except Exception as e:
                log.info("test: auth payload timeout: %s", e)

        try:
            await asyncio.wait_for(self.setup_gatt(conn), 30)
        except Exception as e:
            raise StepError("setup_failed", str(e) or type(e).__name__)

        if not (self.args.linux_conn or self.args.conn):
            try:
                await dev.update_connection_parameters(conn, 7.5, 7.5, self.args.latency, 3000)
            except Exception as e:
                log.info("parametry polaczenia: %s", e)
        self.state.update(connected=True, address=C.norm_addr(conn.peer_address), name=name, since=time.time())
        self.set_phase("connected")
        log.info("polaczono z %s (%s)", name, C.norm_addr(conn.peer_address))
        await done.wait()
        log.info("myszka rozlaczona")
        self.ui.release_all()
        self.state.update(connected=False, since=None)
        if self.cfg.get("device") and not self.force_pair:
            self.set_phase("reconnecting")

    async def bluez_replay(self, peer):
        """Test: powtorz dokladnie sekwencje ATT, ktora BlueZ wysyla przy pierwszym polaczeniu."""
        from bumble.core import UUID
        gc = peer.gatt_client
        async def rd(h):
            try:
                await asyncio.wait_for(gc.read_value(h), 3)
            except Exception:
                pass
        steps = self.args.bluez_replay
        for st in steps.split(","):
            if st.startswith("mtu"):
                val = int(st.split("=")[1]) if "=" in st else 517
                try:
                    got = await peer.request_mtu(val)
                    log.info("test: MTU %d -> %s", val, got)
                except Exception as e:
                    log.info("test: MTU %d: %s", val, e)
        if "sfeat" in steps:
            try:
                await asyncio.wait_for(gc.read_characteristics_by_uuid(UUID.from_16_bits(0x2B3A)), 3)
            except Exception:
                pass
        if "disc" in steps:
            try:
                await peer.discover_services()
                for sv in peer.services:
                    await sv.discover_characteristics()
                    for ch in sv.characteristics:
                        await ch.discover_descriptors()
            except Exception as e:
                log.info("discovery: %s", e)
        if "gap" in steps:
            for h in (0x0003, 0x0007):
                await rd(h)
        if "pnp" in steps:
            for h in (0x001B, 0x001E, 0x001B):
                await rd(h)
        if "hid" in steps:
            for h in (0x0039, 0x0040, 0x0042, 0x0044, 0x0045, 0x004A, 0x004C, 0x004E, 0x0050, 0x0054, 0x000D):
                await rd(h)
        if "cccd" in steps:
            await peer.write_value(H_BATT_CCCD, b"\x01\x00", with_response=True)
            for h in (H_KBD_CCCD, H_MOUSE_CCCD, H_CONS_CCCD):
                await rd(h)
            await rd(0x000B)
        if "dis" in steps:
            for h in (0x000F, 0x0052, 0x0013):
                await rd(h)
        log.info("test: sekwencja BlueZ (%s) wykonana", steps)

    async def setup_gatt(self, conn):
        peer = Peer(conn)
        a = self.args
        if a.bluez_replay:
            await self.bluez_replay(peer)
        if a.mtu:
            await peer.request_mtu(a.mtu)
        if a.led:
            await peer.write_value(H_LED, b"\x00", with_response=False)
        if a.reads:                                     # odczyty jak Windows
            try:
                await peer.discover_services()
                for s in peer.services:
                    await s.discover_characteristics()
            except Exception as e:
                log.info("GATT discovery: %s", e)
            for h in (0x0007, 0x0005, 0x0003, 0x0054, 0x0052, H_CONS, 0x0050, H_CONS_CCCD, H_MOUSE, 0x004C,
                      H_MOUSE_CCCD, 0x0047, H_LED, 0x0045, H_KBD, 0x0042, H_KBD_CCCD, 0x003E, 0x003B, 0x0039):
                try:
                    await asyncio.wait_for(peer.read_value(h), 3)
                except Exception:
                    pass
        gc = peer.gatt_client
        gc.notification_subscribers.setdefault(H_KBD, set()).add(lambda v: self.on_keyboard(bytes(v)))
        gc.notification_subscribers.setdefault(H_MOUSE, set()).add(lambda v: self.on_mouse(bytes(v)))
        gc.notification_subscribers.setdefault(H_CONS, set()).add(lambda v: self.ui.consumer(bytes(v)))
        gc.notification_subscribers.setdefault(H_BATT, set()).add(lambda v: self.on_battery(bytes(v)))
        for h in (H_BATT_CCCD, H_KBD_CCCD, H_CONS_CCCD, H_MOUSE_CCCD):
            await peer.write_value(h, b"\x01\x00", with_response=True)   # bez tego mysz nie dziala - blad = ponow
        if a.led:
            await peer.write_value(H_LED, b"\x00", with_response=False)
        try:
            self.on_battery(bytes(await asyncio.wait_for(peer.read_value(H_BATT), 3)))
        except Exception:
            pass


def patch_reject_conn_params():
    """Odrzucaj L2CAP Connection Parameter Update Request - jak Windows. Kluczowe dla gestow."""
    def handler(self, connection, cid, request):
        self.send_control_frame(connection, cid, l2cap.L2CAP_Connection_Parameter_Update_Response(
            identifier=request.identifier, result=l2cap.L2CAP_CONNECTION_PARAMETERS_REJECTED_RESULT))
    l2cap.ChannelManager.on_l2cap_connection_parameter_update_request = handler


def main():
    ap = argparse.ArgumentParser(description="CheerDots 2 - demon sterownika")
    ap.add_argument("--config", default=C.CONFIG_PATH)
    ap.add_argument("--keystore", default=C.KEYSTORE_PATH)
    ap.add_argument("--socket", default=C.SOCKET_PATH)
    ap.add_argument("--hci", type=int, default=None, help="wymus adapter hciN (domyslnie z konfiguracji)")
    ap.add_argument("-v", "--verbose", action="store_true")
    # przelaczniki diagnostyczne
    ap.add_argument("--no-reject", dest="reject", action="store_false", help="akceptuj prosby o parametry polaczenia")
    ap.add_argument("--io-cap", default="kbd_display", choices=["kbd_display", "display_yesno", "noio"])
    ap.add_argument("--keydist", default="windows", choices=["windows", "linux"])
    ap.add_argument("--mtu", type=int, default=527, help="0 = nie wymieniaj MTU")
    ap.add_argument("--no-led", dest="led", action="store_false")
    ap.add_argument("--no-reads", dest="reads", action="store_false")
    ap.add_argument("--linux-conn", action="store_true", help="test: parametry polaczenia jak BlueZ, bez pozniejszej zmiany")
    ap.add_argument("--conn", default=None, help="test: parametry polaczenia min_ms,max_ms,latency,timeout_ms (bez pozniejszej zmiany)")
    ap.add_argument("--ll-features", action="store_true", help="test: LE Read Remote Features po polaczeniu (jak BlueZ)")
    ap.add_argument("--ping", action="store_true", help="test: Write Authenticated Payload Timeout (jak BlueZ)")
    ap.add_argument("--bluez-replay", default="", help="test: kroki BlueZ np. mtu,sfeat,disc,gap,pnp,hid,cccd,dis")
    ap.add_argument("--latency", type=int, default=99, help="peripheral latency po polaczeniu")
    ap.add_argument("--raw-log", action="store_true", help="loguj surowe raporty (do rozpoznawania nowych gestow)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("bumble").setLevel(logging.WARNING if not a.verbose else logging.INFO)
    os.makedirs(os.path.dirname(a.keystore), exist_ok=True)
    if a.reject:
        patch_reject_conn_params()

    async def amain():
        import signal
        drv = Driver(a)
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)       # porzadne zamkniecie adaptera przy stop
        try:
            await drv.run()
        except asyncio.CancelledError:
            log.info("zatrzymywanie sterownika")
        finally:
            drv.ui.close()
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
