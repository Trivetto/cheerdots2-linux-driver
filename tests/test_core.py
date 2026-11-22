"""Testy bez sprzetu: rozpoznawanie gestow, przyciski, podwojne gesty, profile, konfiguracja.
Uruchom:  python3 -m unittest discover -s tests -v
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cheerdots import config as C          # noqa: E402
from cheerdots import daemon as D          # noqa: E402

K, R = D.EV_KEY, D.EV_REL


class FakeUI:
    def __init__(self, *a):
        self.ev = []

    def emit(self, t, c, v):
        self.ev.append((t, c, v))

    def syn(self):
        pass

    def tap(self, names):
        self.ev.append(("TAP", "+".join(names)))

    def release_all(self):
        pass

    def close(self):
        pass

    def taps(self):
        return [e[1] for e in self.ev if e[0] == "TAP"]

    def out(self):
        return [e for e in self.ev if e[0] != "TAP"]


def frames(tr, fr):
    for f in fr:
        for e in f:
            tr.feed(*e)
        tr.feed(0, 0, 0)


MAC_LEFT = [[(K, 29, 1)], [(K, 106, 1)], [(K, 106, 0), (K, 29, 0)]]       # Ctrl+Right -> swipe_left


class TranslatorTest(unittest.TestCase):
    def setUp(self):
        self.ui, self.g = FakeUI(), []
        self.tr = D.Translator(self.ui, lambda g, r, b=frozenset(): self.g.append(g))

    def test_mac_gestures(self):
        frames(self.tr, MAC_LEFT)
        frames(self.tr, [[(K, 125, 1), (K, 120, 1)], [(K, 120, 0), (K, 125, 0)]])   # Meta+KEY_SCALE
        frames(self.tr, [[(K, 204, 1)], [(K, 204, 0)]])                             # KEY_DASHBOARD
        self.assertEqual(self.g, ["swipe_left", "swipe_down", "swipe_up"])
        self.assertEqual(self.ui.out(), [])

    def test_windows_gesture(self):
        frames(self.tr, [[(K, 29, 1), (K, 125, 1)], [(K, 105, 1)], [(K, 105, 0), (K, 125, 0), (K, 29, 0)]])
        self.assertEqual(self.g, ["swipe_right"])

    def test_passthrough(self):
        frames(self.tr, [[(R, 0, 5), (R, 1, -3)], [(K, 272, 1)], [(K, 272, 0)], [(R, 8, 1), (R, 11, 120)]])
        self.assertEqual(self.ui.out(), [(R, 0, 5), (R, 1, -3), (K, 272, 1), (K, 272, 0), (R, 8, 1), (R, 11, 120)])

    def test_pinch_wheel(self):
        frames(self.tr, [[(K, 29, 1)], [(R, 11, 120), (R, 8, 1)], [(R, 11, -120), (R, 8, -1)], [(K, 29, 0)]])
        self.assertEqual(self.g, ["pinch_out", "pinch_in"])

    def test_unknown_combo_passes(self):
        frames(self.tr, [[(K, 29, 1)], [(K, 30, 1)], [(K, 30, 0)], [(K, 29, 0)]])
        self.assertEqual(self.ui.out(), [(K, 29, 1), (K, 30, 1), (K, 30, 0), (K, 29, 0)])

    def test_lone_meta_dropped(self):
        frames(self.tr, [[(K, 125, 1)], [(K, 125, 0)]])
        self.assertEqual(self.ui.out(), [])

    def test_pointer_scale(self):
        tr = D.Translator(self.ui, lambda *a: None, get_scale=lambda: 0.5)
        frames(tr, [[(R, 0, 1)]] * 4 + [[(R, 1, -7)]])
        self.assertEqual(self.ui.out(), [(R, 0, 1), (R, 0, 1), (R, 1, -3)])


class DriverTest(unittest.TestCase):
    def setUp(self):
        D.UInput = FakeUI
        d = tempfile.mkdtemp()

        class A:
            config, socket, raw_log, verbose = d + "/c.json", d + "/s", False, False
        self.A = A

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_double_and_button_and_profile(self):
        async def go():
            drv = D.Driver(self.A())
            ui = drv.ui
            tr = D.Translator(ui, drv.on_gesture, shared=drv.shared, buffer_button=drv.buffer_button)
            frames(tr, MAC_LEFT)
            self.assertEqual(ui.taps(), ["ctrl+meta+right"])
            ui.ev.clear()
            # podwojny
            drv.cfg["gestures"]["swipe_left_x2"] = {"enabled": True, "action": "grid", "custom": ""}
            frames(tr, MAC_LEFT)
            self.assertEqual(ui.taps(), [])
            await asyncio.sleep(0.5)
            self.assertEqual(ui.taps(), ["ctrl+meta+right"])
            ui.ev.clear()
            frames(tr, MAC_LEFT)
            await asyncio.sleep(0.1)
            frames(tr, MAC_LEFT)
            await asyncio.sleep(0.5)
            self.assertEqual(ui.taps(), ["meta+g"])
            ui.ev.clear()
            # przycisk: bez gestow _btn klikniecie przechodzi od razu
            frames(tr, [[(K, 273, 1)], [(K, 273, 0)]])
            self.assertEqual(ui.out(), [(K, 273, 1), (K, 273, 0)])
            ui.ev.clear()
            drv.cfg["gestures"]["swipe_left_btn"] = {"enabled": True, "action": "terminal", "custom": ""}
            frames(tr, [[(K, 273, 1)]])
            self.assertEqual(ui.out(), [])                         # wstrzymany
            frames(tr, [[(K, 273, 0)]])
            self.assertEqual(ui.out(), [(K, 273, 1), (K, 273, 0)])  # zwykle klikniecie przy puszczeniu
            ui.ev.clear()
            frames(tr, [[(K, 273, 1)]] + MAC_LEFT + [[(K, 273, 0)]])
            await asyncio.sleep(0.5)
            self.assertEqual(ui.taps(), ["ctrl+alt+t"])
            self.assertEqual(ui.out(), [])                         # bez klikniecia
            ui.ev.clear()
            frames(tr, [[(K, 273, 1)], [(R, 0, 30)], [(K, 273, 0)]])
            self.assertEqual(ui.out(), [(K, 273, 1), (R, 0, 30), (K, 273, 0)])   # przeciaganie
            ui.ev.clear()
            # profil
            drv.cfg["gestures"]["swipe_left_x2"]["enabled"] = False
            drv.cfg["profiles"][0]["enabled"] = True
            r = await drv.ipc_handle({"cmd": "active_app", "app": "firefox"})
            self.assertEqual(r["profile"], "Przeglądarka")
            frames(tr, MAC_LEFT)
            self.assertEqual(ui.taps(), ["alt+right"])
            ui.ev.clear()
            await drv.ipc_handle({"cmd": "active_app", "app": "org.kde.dolphin"})
            frames(tr, MAC_LEFT)
            self.assertEqual(ui.taps(), ["ctrl+meta+right"])
        self.run_async(go())


class ConfigTest(unittest.TestCase):
    def test_merge_keeps_old_and_adds_new(self):
        c = C.merged({"gestures": {"swipe_down": {"enabled": True, "action": "custom", "custom": "ctrl+alt+t"}},
                      "hci": "auto"})
        self.assertEqual(c["gestures"]["swipe_down"]["custom"], "ctrl+alt+t")
        self.assertIn("swipe_up_x2", c["gestures"])
        self.assertNotIn("hci", c)

    def test_bad_action_sanitized(self):
        c = C.merged({"gestures": {"swipe_up": {"action": "nope"}}})
        self.assertEqual(c["gestures"]["swipe_up"]["action"], "none")

    def test_parse_combo(self):
        self.assertEqual(C.parse_combo("Ctrl+Alt+T"), ["ctrl", "alt", "t"])
        with self.assertRaises(ValueError):
            C.parse_combo("ctrl+zzz")

    def test_bluez_mtu(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("[General]\nExchangeMTU = 5\n[GATT]\n# cheerdots: gesty myszki CheerDots 2\nExchangeMTU = 185\n")
        self.assertEqual(C.bluez_mtu(f.name), 185)
        os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
