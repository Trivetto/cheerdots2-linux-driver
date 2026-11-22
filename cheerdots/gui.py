#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CheerDots 2 - ikona w zasobniku, okno ustawien i kreator wyboru myszki. Rozmawia z demonem przez IPC."""
import json
import os
import socket
import subprocess
import sys
import time

from PyQt6.QtCore import QObject, QProcess, QRectF, Qt, QTimer, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusInterface
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton, QSpinBox,
                             QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cheerdots import config as C                       # noqa: E402

APP = "CheerDots"
PHASES = {
    "start": "Uruchamianie…", "no_bluez": "Bluetooth systemu nie działa",
    "mtu_missing": "Bluetooth nieskonfigurowany dla gestów",
    "no_device": "Nie wybrano myszki", "searching": "Rozłączona – czekam na myszkę",
    "no_input": "Łączenie…", "connected": "Połączona",
}


# --------------------------------------------------------------------------- IPC
def ipc(req, timeout=2.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(C.SOCKET_PATH)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf)
    finally:
        s.close()


def ipc_ok(parent, req):
    """Wywolanie IPC z komunikatem bledu w okienku. Zwraca odpowiedz albo None."""
    try:
        r = ipc(req)
    except OSError as e:
        QMessageBox.critical(parent, APP, f"Sterownik nie odpowiada:\n{e}\n\nUruchom: cheerdots-ctl on")
        return None
    if not r.get("ok"):
        QMessageBox.warning(parent, APP, r.get("error", "Nieznany błąd"))
        return None
    return r


def service(verb):
    """Wlacz/wylacz usluge sterownika. Bez hasla dzieki regule polkit z pakietu; inaczej pyta o haslo."""
    for cmd in (["systemctl", verb, "cheerdots.service"],
                ["pkexec", "systemctl", verb, "cheerdots.service"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            err = str(e)
            continue
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or r.stdout).strip()
    return False, err


# --------------------------------------------------------------------------- ikona
def make_icon(battery, connected):
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    body = QColor("#3daee9") if connected else QColor("#7f8c8d")
    path = QPainterPath()
    path.addRoundedRect(QRectF(14, 4, 36, 56), 18, 18)
    p.setPen(QPen(body.darker(130), 3))
    p.setBrush(QBrush(body))
    p.drawPath(path)
    p.setPen(QPen(QColor(255, 255, 255, 170), 2))
    p.drawLine(32, 8, 32, 24)
    if connected and battery is not None:
        col = QColor("#27ae60") if battery > 40 else QColor("#f39c12") if battery > 15 else QColor("#e74c3c")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 20, 200))
        p.drawRoundedRect(QRectF(2, 34, 60, 28), 7, 7)
        f = QFont()
        f.setPixelSize(24)
        f.setBold(True)
        p.setFont(f)
        p.setPen(col.lighter(130))
        p.drawText(QRectF(2, 34, 60, 28), Qt.AlignmentFlag.AlignCenter, f"{max(0, min(100, int(battery)))}")
    p.end()
    return QIcon(pm)


# --------------------------------------------------------------------------- kreator wyboru myszki
def open_kde_bluetooth():
    for prog, args in (("systemsettings", ["kcm_bluetooth"]), ("kcmshell6", ["kcm_bluetooth"]),
                       ("bluedevil-wizard", []), ("blueman-manager", [])):
        if QProcess.startDetached(prog, args)[0]:
            return True
    return False


class DevicePicker(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wybierz myszkę CheerDots")
        self.setMinimumSize(560, 440)
        lay = QVBoxLayout(self)
        info = QLabel("Myszka łączy się przez <b>systemowy Bluetooth</b> – inne urządzenia działają normalnie.<br>"
                      "<b>Nowa myszka:</b> włącz w niej <b>tryb parowania</b> (niebieska dioda miga), kliknij "
                      "<b>Szukaj nowych</b>, wybierz ją i <b>Sparuj i użyj</b>. Jeśli parowanie się nie uda, "
                      "sparuj ją w <b>Ustawieniach Bluetooth</b> i wróć tutaj.<br>"
                      "<span style='color:gray'>Myszki CheerDots są na górze listy (★).</span>")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._sel)
        self.list.itemDoubleClicked.connect(lambda _: self.accept_selected())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.only_named = QCheckBox("Tylko urządzenia z nazwą")
        self.only_named.setChecked(True)
        self.only_named.toggled.connect(self.refresh)
        self.l_state = QLabel("")
        self.l_state.setStyleSheet("color: gray")
        row.addWidget(self.only_named)
        row.addStretch(1)
        row.addWidget(self.l_state)
        lay.addLayout(row)
        bb = QDialogButtonBox()
        self.b_connect = bb.addButton("Użyj", QDialogButtonBox.ButtonRole.AcceptRole)
        b_scan = bb.addButton("Szukaj nowych", QDialogButtonBox.ButtonRole.ActionRole)
        b_kde = bb.addButton("Ustawienia Bluetooth…", QDialogButtonBox.ButtonRole.ActionRole)
        bb.addButton("Anuluj", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(self.reject)
        self.b_connect.setEnabled(False)
        self.b_connect.clicked.connect(self.accept_selected)
        b_scan.clicked.connect(self.start_scan)
        b_kde.clicked.connect(lambda: open_kde_bluetooth() or QMessageBox.information(
            self, APP, "Otwórz Ustawienia systemowe → Bluetooth i sparuj tam myszkę."))
        lay.addWidget(bb)
        self.selected = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

    def start_scan(self):
        try:
            ipc({"cmd": "scan_start", "seconds": 40})
            self.l_state.setText("Szukam nowych urządzeń…")
        except OSError as e:
            self.l_state.setText(f"Sterownik nie odpowiada: {e}")

    def exec(self):
        self.refresh()
        self.timer.start(1500)
        try:
            return super().exec()
        finally:
            self.timer.stop()
            try:
                ipc({"cmd": "scan_stop"})
            except OSError:
                pass

    def refresh(self):
        try:
            r = ipc({"cmd": "devices"})
        except OSError as e:
            self.l_state.setText(f"Sterownik nie odpowiada: {e}")
            return
        if not r.get("bluez", True):
            self.l_state.setText("Bluetooth systemu nie działa – włącz Bluetooth.")
        results = [x for x in r.get("results", []) if x.get("name") or not self.only_named.isChecked()]
        keep = self.selected
        self.list.blockSignals(True)
        self.list.clear()
        for x in results:
            star = "★ " if x.get("cheerdots") else "    "
            st = "połączona" if x.get("connected") else "sparowana" if x.get("paired") else "nowa – do sparowania"
            text = f"{star}{x.get('name') or '(bez nazwy)'}    {x['address']}    [{st}]"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, x)
            if x.get("cheerdots"):
                f = it.font(); f.setBold(True); it.setFont(f)
            self.list.addItem(it)
            if keep and x["address"] == keep["address"]:
                it.setSelected(True)
        self.list.blockSignals(False)
        if r.get("bluez", True):
            self.l_state.setText(("Szukam… " if r.get("scanning") else "") + f"Urządzeń: {len(results)}")
        self._sel()

    def _sel(self):
        items = self.list.selectedItems()
        self.selected = items[0].data(Qt.ItemDataRole.UserRole) if items else self.selected
        x = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        self.b_connect.setEnabled(bool(items))
        self.b_connect.setText("Użyj" if not x or x.get("paired") else "Sparuj i użyj")

    def accept_selected(self):
        items = self.list.selectedItems()
        if not items:
            return
        x = items[0].data(Qt.ItemDataRole.UserRole)
        if not x.get("cheerdots"):
            if QMessageBox.question(self, APP, f"„{x.get('name') or x['address']}” nie wygląda na myszkę CheerDots.\n"
                                               "Na pewno ją wybrać?") != QMessageBox.StandardButton.Yes:
                return
        self.timer.stop()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.l_state.setText("Parowanie… (do 30 s)" if not x.get("paired") else "Zapisywanie…")
        QApplication.processEvents()
        try:
            r = ipc({"cmd": "select_device", "address": x["address"], "name": x.get("name", "")}, timeout=60)
        except OSError as e:
            r = {"ok": False, "error": f"Sterownik nie odpowiada: {e}"}
        finally:
            QApplication.restoreOverrideCursor()
        if r.get("ok"):
            self.accept()
            return
        self.timer.start(1500)
        if QMessageBox.warning(self, APP, r.get("error", "Nieznany błąd") + "\n\nOtworzyć Ustawienia Bluetooth?",
                               QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                == QMessageBox.StandardButton.Yes:
            open_kde_bluetooth()


# --------------------------------------------------------------------------- okno ustawien
INHERIT = "inherit"


class SettingsWindow(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.work = C.merged(C.DEFAULT_CONFIG)     # edytowana kopia konfiguracji
        self.cur_profile = -1                      # -1 = ustawienia domyslne, inaczej indeks profilu
        self.setWindowTitle("CheerDots 2 – ustawienia")
        self.setWindowIcon(make_icon(80, True))
        self.setMinimumWidth(680)
        lay = QVBoxLayout(self)

        # --- stan i urzadzenie
        g = QGroupBox("Myszka")
        f = QFormLayout(g)
        self.l_status = QLabel("–")
        self.l_status.setFont(QFont(self.font().family(), self.font().pointSize() + 1, QFont.Weight.Bold))
        self.l_hint = QLabel("")
        self.l_hint.setWordWrap(True)
        self.l_hint.setStyleSheet("color: #c0392b")
        dev_row = QHBoxLayout()
        self.l_device = QLabel("–")
        self.b_pick = QPushButton("Wybierz urządzenie…")
        self.b_forget = QPushButton("Zapomnij")
        self.b_pick.clicked.connect(self.pick_device)
        self.b_forget.clicked.connect(self.forget_device)
        dev_row.addWidget(self.l_device, 1)
        dev_row.addWidget(self.b_pick)
        dev_row.addWidget(self.b_forget)
        self.battery = QProgressBar()
        self.battery.setRange(0, 100)
        self.battery.setFormat("%p %")
        self.l_gesture = QLabel("–")
        self.l_gesture.setWordWrap(True)
        f.addRow("Stan:", self.l_status)
        f.addRow("", self.l_hint)
        f.addRow("Urządzenie:", dev_row)
        f.addRow("Bateria:", self.battery)
        f.addRow("Ostatni gest:", self.l_gesture)
        lay.addWidget(g)

        # --- gesty + profile aplikacji
        g = QGroupBox("Gesty")
        gl = QVBoxLayout(g)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Profil:"))
        self.profile = QComboBox()
        self.profile.setMinimumWidth(200)
        self.profile.currentIndexChanged.connect(self.switch_profile)
        prow.addWidget(self.profile, 1)
        self.b_padd = QPushButton("Dodaj profil…")
        self.b_pdel = QPushButton("Usuń")
        self.b_padd.clicked.connect(self.add_profile)
        self.b_pdel.clicked.connect(self.del_profile)
        prow.addWidget(self.b_padd)
        prow.addWidget(self.b_pdel)
        gl.addLayout(prow)
        self.pbox = QWidget()
        pf = QFormLayout(self.pbox)
        pf.setContentsMargins(0, 0, 0, 0)
        self.p_enabled = QCheckBox("Profil aktywny")
        self.p_name = QLineEdit()
        self.p_match = QLineEdit()
        self.p_match.setPlaceholderText("np. firefox, chrom, org.kde.dolphin")
        mrow = QHBoxLayout()
        mrow.addWidget(self.p_match, 1)
        self.b_last = QPushButton("Dodaj ostatnie okno")
        self.b_last.setToolTip("Dopisuje aplikację, która była aktywna przed otwarciem tego okna")
        self.b_last.clicked.connect(self.add_last_app)
        mrow.addWidget(self.b_last)
        pf.addRow("", self.p_enabled)
        pf.addRow("Nazwa:", self.p_name)
        pf.addRow("Aplikacje:", mrow)
        note = QLabel("Fragmenty nazwy klasy okna, po przecinku. Gest ustawiony na „jak w domyślnych” "
                      "działa tak, jak w ustawieniach domyślnych.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 9pt")
        pf.addRow("", note)
        self.p_name.editingFinished.connect(self._rename_profile)
        gl.addWidget(self.pbox)

        self.tabs = QTabWidget()
        self.rows = {}
        self.row_labels = {}
        for title, gids in C.GESTURE_GROUPS.items():
            w = QWidget()
            grid = QGridLayout(w)
            grid.addWidget(QLabel("<b>Gest</b>"), 0, 0)
            grid.addWidget(QLabel("<b>Akcja</b>"), 0, 1)
            grid.addWidget(QLabel("<b>Własny skrót</b>"), 0, 2)
            for i, gid in enumerate(gids, start=1):
                cb = QCheckBox(C.GESTURES[gid])
                combo = QComboBox()
                edit = QLineEdit()
                edit.setPlaceholderText("np. ctrl+alt+t")
                combo.currentIndexChanged.connect(lambda _=0, c=combo, e=edit, b=cb: self._sync_row(b, c, e))
                cb.toggled.connect(lambda _=0, c=combo, e=edit, b=cb: self._sync_row(b, c, e))
                grid.addWidget(cb, i, 0)
                grid.addWidget(combo, i, 1)
                grid.addWidget(edit, i, 2)
                self.rows[gid] = (cb, combo, edit)
            info = {"Podwójne": "Dwa takie same gesty szybko po sobie. Gdy ustawiony jest gest podwójny, "
                                "pojedynczy gest w tę stronę wykonuje się z małym opóźnieniem (patrz „Inne”).",
                    "Z przyciskiem": "Gest wykonany z wciśniętym przyciskiem myszy (wybór przycisku w „Inne”). "
                                     "Zwykłe kliknięcie tym przyciskiem dalej działa."}.get(title)
            if info:
                lab = QLabel(info)
                lab.setWordWrap(True)
                lab.setStyleSheet("color: gray; font-size: 9pt")
                grid.addWidget(lab, len(gids) + 1, 0, 1, 3)
            grid.setRowStretch(len(gids) + 2, 1)
            self.tabs.addTab(w, title)
        gl.addWidget(self.tabs)
        hint = QLabel("Klawisze własnego skrótu: ctrl, alt, shift, meta, litery, cyfry, f1–f12, left/right/up/down, "
                      "tab, enter, esc, space, volumeup, volumedown, playpause, print, wheelup/wheeldown (obrót kółka)…")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 9pt")
        gl.addWidget(hint)
        lay.addWidget(g)

        # --- inne
        g = QGroupBox("Inne")
        f = QFormLayout(g)
        self.speed = QSpinBox()
        self.speed.setRange(10, 200)
        self.speed.setSingleStep(5)
        self.speed.setSuffix(" %")
        self.speed.setToolTip("Zmniejsza/zwiększa ruch myszki, zanim trafi do KDE. "
                              "Przydatne, gdy najniższa prędkość w ustawieniach KDE to wciąż za szybko.")
        f.addRow("Prędkość wskaźnika:", self.speed)
        self.double_ms = QSpinBox()
        self.double_ms.setRange(200, 800)
        self.double_ms.setSingleStep(50)
        self.double_ms.setSuffix(" ms")
        f.addRow("Czas na gest podwójny:", self.double_ms)
        self.button = QComboBox()
        for bid, (bname, _) in C.BUTTONS.items():
            self.button.addItem(bname.capitalize(), bid)
        self.button.currentIndexChanged.connect(self._update_btn_labels)
        f.addRow("Przycisk gestów „z przyciskiem”:", self.button)
        self.low_batt = QSpinBox()
        self.low_batt.setRange(0, 50)
        self.low_batt.setSuffix(" %")
        self.low_batt.setSpecialValueText("nie ostrzegaj")
        f.addRow("Ostrzegaj, gdy bateria poniżej:", self.low_batt)
        self.l_bt = QLabel("–")
        self.l_bt.setWordWrap(True)
        f.addRow("Bluetooth:", self.l_bt)
        self.l_apps = QLabel("–")
        self.l_apps.setWordWrap(True)
        f.addRow("Wykrywanie aplikacji:", self.l_apps)
        lay.addWidget(g)

        bb = QDialogButtonBox()
        b_save = bb.addButton("Zapisz", QDialogButtonBox.ButtonRole.AcceptRole)
        b_def = bb.addButton("Domyślne gesty", QDialogButtonBox.ButtonRole.ResetRole)
        b_exp = bb.addButton("Eksportuj…", QDialogButtonBox.ButtonRole.ActionRole)
        b_imp = bb.addButton("Importuj…", QDialogButtonBox.ButtonRole.ActionRole)
        b_close = bb.addButton("Zamknij", QDialogButtonBox.ButtonRole.RejectRole)
        b_save.clicked.connect(self.save)
        b_def.clicked.connect(self.reset_gestures)
        b_exp.clicked.connect(self.export_cfg)
        b_imp.clicked.connect(self.import_cfg)
        b_close.clicked.connect(self.hide)
        lay.addWidget(bb)

    # -------------------------------------------------------------- urzadzenie
    def pick_device(self):
        if DevicePicker(self).exec():
            self.app.tray_message("Wybrano myszkę – trwa łączenie…")
            self.app.refresh()

    def forget_device(self):
        if QMessageBox.question(self, APP, "Zapomnieć myszkę i usunąć jej parowanie z systemowego Bluetooth?") \
                == QMessageBox.StandardButton.Yes:
            ipc_ok(self, {"cmd": "forget_device"})
            self.app.refresh()

    # -------------------------------------------------------------- wiersze gestow
    def _sync_row(self, cb, combo, edit):
        on = cb.isChecked()
        combo.setEnabled(on)
        edit.setEnabled(on and combo.currentData() == "custom")

    def _update_btn_labels(self):
        bname = C.BUTTONS.get(self.button.currentData() or "right", C.BUTTONS["right"])[0]
        for gid in C.GESTURE_GROUPS["Z przyciskiem"]:
            base = gid[:-4]
            self.rows[gid][0].setText(f"{bname.split(' (')[0].capitalize()} + {C.BASE_GESTURES[base].lower()}")

    def _fill_combo(self, combo, with_inherit):
        combo.blockSignals(True)
        combo.clear()
        if with_inherit:
            combo.addItem("— jak w ustawieniach domyślnych —", INHERIT)
        for aid, (adesc, _) in C.ACTIONS.items():
            if aid != "none":
                combo.addItem(adesc, aid)
        combo.blockSignals(False)

    def _gestures_of(self, idx):
        return self.work["gestures"] if idx < 0 else self.work["profiles"][idx]["gestures"]

    def load_rows(self, idx):
        prof = idx >= 0
        gs = self._gestures_of(idx)
        for gid, (cb, combo, edit) in self.rows.items():
            self._fill_combo(combo, prof)
            gc = gs.get(gid) or ({"enabled": True, "action": INHERIT, "custom": ""} if prof
                                 else {"enabled": False, "action": "none", "custom": ""})
            action = gc.get("action", "none")
            if not prof:
                cb.setChecked(gc.get("enabled", True) and action != "none")
                d = C.DEFAULT_CONFIG["gestures"][gid]["action"]
                combo.setCurrentIndex(max(0, combo.findData(action if action != "none" else (d if d != "none" else "next_desktop"))))
            else:
                cb.setChecked(gc.get("enabled", True))
                combo.setCurrentIndex(max(0, combo.findData(action if action != "none" else INHERIT)))
            edit.setText(gc.get("custom", ""))
            self._sync_row(cb, combo, edit)

    def store_rows(self, idx):
        if idx >= len(self.work["profiles"]):
            return
        prof = idx >= 0
        out = {}
        for gid, (cb, combo, edit) in self.rows.items():
            action = combo.currentData()
            if prof and action == INHERIT:
                continue                            # dziedziczony - nie zapisuj
            out[gid] = {"enabled": cb.isChecked(), "action": action if (cb.isChecked() or prof) else "none",
                        "custom": edit.text().strip()}
            if not prof and not cb.isChecked():
                out[gid]["action"] = "none"
        if prof:
            p = self.work["profiles"][idx]
            p["gestures"] = out
            p["enabled"] = self.p_enabled.isChecked()
            p["name"] = self.p_name.text().strip() or p["name"]
            p["match"] = [m.strip().lower() for m in self.p_match.text().split(",") if m.strip()]
        else:
            self.work["gestures"].update(out)

    # -------------------------------------------------------------- profile
    def fill_profiles(self, select=-1):
        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItem("Ustawienia domyślne (wszystkie aplikacje)", -1)
        for i, p in enumerate(self.work["profiles"]):
            self.profile.addItem(f"{p['name']}{'' if p['enabled'] else '  (wyłączony)'}", i)
        self.profile.setCurrentIndex(max(0, self.profile.findData(select)))
        self.profile.blockSignals(False)
        self.cur_profile = select
        self.show_profile(select)

    def show_profile(self, idx):
        prof = idx >= 0
        self.pbox.setVisible(prof)
        self.b_pdel.setEnabled(prof)
        if prof:
            p = self.work["profiles"][idx]
            self.p_enabled.setChecked(p["enabled"])
            self.p_name.setText(p["name"])
            self.p_match.setText(", ".join(p["match"]))
        self.load_rows(idx)

    def switch_profile(self):
        self.store_rows(self.cur_profile)
        idx = self.profile.currentData()
        self.cur_profile = -1 if idx is None else idx
        self.show_profile(self.cur_profile)
        self.fill_profiles(self.cur_profile)        # odswiez etykiety (nazwa / wylaczony)

    def _rename_profile(self):
        if self.cur_profile >= 0:
            self.store_rows(self.cur_profile)
            self.fill_profiles(self.cur_profile)

    def add_profile(self):
        self.store_rows(self.cur_profile)
        last = self.app.last_app or ""
        self.work["profiles"].append({"name": last.split(".")[-1].capitalize() if last else "Nowy profil",
                                      "enabled": True, "match": [last.lower()] if last else [], "gestures": {}})
        self.fill_profiles(len(self.work["profiles"]) - 1)
        self.p_name.setFocus()

    def del_profile(self):
        if self.cur_profile < 0:
            return
        p = self.work["profiles"][self.cur_profile]
        if QMessageBox.question(self, APP, f"Usunąć profil „{p['name']}”?") == QMessageBox.StandardButton.Yes:
            del self.work["profiles"][self.cur_profile]
            self.fill_profiles(-1)

    def add_last_app(self):
        last = (self.app.last_app or "").lower()
        if not last:
            QMessageBox.information(self, APP, "Nie znam jeszcze ostatniego okna. Przełącz się na aplikację, "
                                               "wróć tutaj i kliknij ponownie.\n\n(Działa w KDE Plasma.)")
            return
        items = [m.strip() for m in self.p_match.text().split(",") if m.strip()]
        if last not in items:
            items.append(last)
        self.p_match.setText(", ".join(items))

    # -------------------------------------------------------------- formularz
    def fill(self, cfg):
        self.work = C.merged(cfg)
        self.speed.setValue(int(self.work.get("pointer_scale", 100)))
        self.double_ms.setValue(int(self.work.get("double_ms", 400)))
        self.button.setCurrentIndex(max(0, self.button.findData(self.work.get("gesture_button", "right"))))
        self.low_batt.setValue(int(self.work.get("notify_battery_below", 15)))
        self._update_btn_labels()
        self.fill_profiles(-1 if self.cur_profile >= len(self.work["profiles"]) else self.cur_profile)

    def collect(self):
        self.store_rows(self.cur_profile)
        cfg = C.merged(self.work)
        cfg["notify_battery_below"] = self.low_batt.value()
        cfg["pointer_scale"] = self.speed.value()
        cfg["double_ms"] = self.double_ms.value()
        cfg["gesture_button"] = self.button.currentData()
        cfg["device"] = (self.app.config or {}).get("device")
        return cfg

    def reset_gestures(self):
        if self.cur_profile >= 0:
            self.work["profiles"][self.cur_profile]["gestures"] = {}
        else:
            self.work["gestures"] = C.merged({})["gestures"]
        self.load_rows(self.cur_profile)

    def validate(self, cfg):
        for scope, gs in [("", cfg["gestures"])] + [(p["name"] + ": ", p["gestures"]) for p in cfg["profiles"]]:
            for gid, g in gs.items():
                if g["enabled"] and g["action"] == "custom":
                    try:
                        C.parse_combo(g["custom"])
                    except ValueError as e:
                        QMessageBox.warning(self, APP, f"{scope}{C.GESTURES[gid]}: {e}")
                        return False
        return True

    def save(self):
        cfg = self.collect()
        if not self.validate(cfg):
            return
        if ipc_ok(self, {"cmd": "set_config", "config": cfg}):
            self.app.config = cfg
            self.fill_profiles(self.cur_profile)
            self.app.tray_message("Zapisano ustawienia.")

    def export_cfg(self):
        path, _ = QFileDialog.getSaveFileName(self, "Eksportuj ustawienia", os.path.expanduser("~/cheerdots-ustawienia.json"),
                                              "Ustawienia CheerDots (*.json)")
        if not path:
            return
        cfg = self.collect()
        cfg.pop("device", None)
        try:
            with open(path, "w") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            self.app.tray_message(f"Zapisano: {path}")
        except OSError as e:
            QMessageBox.warning(self, APP, f"Nie udało się zapisać pliku:\n{e}")

    def import_cfg(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importuj ustawienia", os.path.expanduser("~"),
                                              "Ustawienia CheerDots (*.json);;Wszystkie pliki (*)")
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, dict) or "gestures" not in data:
                raise ValueError("to nie jest plik ustawień CheerDots")
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, APP, f"Nie udało się wczytać pliku:\n{e}")
            return
        data["device"] = (self.app.config or {}).get("device")
        self.cur_profile = -1
        self.fill(data)
        QMessageBox.information(self, APP, "Wczytano ustawienia. Sprawdź je i kliknij „Zapisz”.")

    def show_status(self, st):
        if st is None:
            self.l_status.setText("<span style='color:#c0392b'>Sterownik nie działa</span>")
            self.l_hint.setText("Włącz go z menu ikony w zasobniku (prawy przycisk → Włącz sterownik).")
            self.l_hint.setVisible(True)
            self.battery.setValue(0)
            self.battery.setEnabled(False)
            self.b_pick.setEnabled(False)
            self.b_forget.setEnabled(False)
            return
        self.b_pick.setEnabled(True)
        phase = st.get("phase", "")
        txt = PHASES.get(phase, phase)
        color = "#27ae60" if st.get("connected") else "#e67e22"
        self.l_status.setText(f"<span style='color:{color}'>{txt}</span>")
        hint = st.get("hint") or ""
        if st.get("error") and not st.get("connected"):
            hint = (hint + "\n" if hint else "") + f"Szczegóły: {st['error']}"
        self.l_hint.setText(hint)
        self.l_hint.setVisible(bool(hint))
        tr = getattr(self.app, "tracker", None)
        if tr is not None and tr.ok:
            cur = self.app.last_app or "–"
            self.l_apps.setText(f"działa (KWin) · ostatnia aplikacja: <b>{cur}</b>"
                                + (f" · profil „{st['profile']}”" if st.get("profile") else ""))
        else:
            self.l_apps.setText("<span style='color:gray'>niedostępne (wymaga KDE Plasma) – "
                                "działają tylko ustawienia domyślne</span>")
        mtu = st.get("mtu")
        self.l_bt.setText("systemowy (BlueZ), inne urządzenia działają normalnie – "
                          + ("<span style='color:#27ae60'>skonfigurowany dla gestów (MTU 185)</span>" if st.get("mtu_ok")
                             else f"<span style='color:#c0392b'>brak konfiguracji gestów (MTU {mtu or 517}) – "
                                  "uruchom: sudo cheerdots-ctl setup</span>"))
        dev = st.get("device")
        self.b_forget.setEnabled(bool(dev))
        if dev:
            auto = "  <span style='color:gray'>(wykryta automatycznie)</span>" if dev.get("auto") else ""
            self.l_device.setText(f"{dev.get('name') or 'CheerDots'}   {dev.get('address')}{auto}")
            self.b_pick.setText("Zmień urządzenie…")
        else:
            self.l_device.setText("<i>nie wybrano</i>")
            self.b_pick.setText("Wybierz urządzenie…")
        b = st.get("battery")
        self.battery.setEnabled(b is not None and bool(st.get("connected")))
        self.battery.setValue(b or 0)
        lg = st.get("last_gesture")
        if lg:
            ago = int(time.time() - lg.get("time", 0))
            act = C.ACTIONS.get(lg.get("action"), (lg.get("action"),))[0] if lg.get("action") != "disabled" else "wyłączony"
            prof = f" · profil „{lg['profile']}”" if lg.get("profile") else ""
            self.l_gesture.setText(f"{C.GESTURES.get(lg['gesture'], lg['gesture'])} → {act}  ({ago} s temu{prof})")



# --------------------------------------------------------------------------- aktywne okno (KDE Plasma / KWin)
KWIN_SCRIPT = """
function report(w) {
    var c = "";
    try { c = w ? String(w.resourceClass || "") : ""; } catch (e) {}
    callDBus("org.cheerdots.Gui", "/Tracker", "org.cheerdots.Gui", "activeWindow", c);
}
if (workspace.windowActivated) {            // KWin 6
    workspace.windowActivated.connect(report);
    report(workspace.activeWindow);
} else {                                     // KWin 5
    workspace.clientActivated.connect(report);
    report(workspace.activeClient);
}
"""
KWIN_SCRIPT_NAME = "cheerdots-active-window"


@pyqtClassInfo("D-Bus Interface", "org.cheerdots.Gui")
class ActiveWindowTracker(QObject):
    """Skrypt KWin zglasza klase aktywnego okna przez D-Bus; przekazujemy ja demonowi (profile aplikacji)."""

    def __init__(self, on_change):
        super().__init__()
        self.on_change = on_change
        self.ok = False
        try:
            bus = QDBusConnection.sessionBus()
            if not bus.isConnected():
                return
            if not bus.registerService("org.cheerdots.Gui"):
                return
            bus.registerObject("/Tracker", self, QDBusConnection.RegisterOption.ExportAllSlots)
            self.ok = self.load_kwin_script()
        except Exception as e:                      # brak KWin / D-Bus - profile po prostu nie dzialaja
            print("tracker:", e, file=sys.stderr)

    @pyqtSlot(str)
    def activeWindow(self, cls):                     # noqa: N802 (nazwa metody D-Bus)
        self.on_change(cls or "")

    @staticmethod
    def load_kwin_script():
        d = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or os.path.expanduser("~/.cache"), "cheerdots")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "kwin-active-window.js")
        with open(path, "w") as f:
            f.write(KWIN_SCRIPT)
        scr = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", QDBusConnection.sessionBus())
        if not scr.isValid():
            return False
        scr.call("unloadScript", KWIN_SCRIPT_NAME)
        reply = scr.call("loadScript", path, KWIN_SCRIPT_NAME)
        args = reply.arguments()
        if not args or int(args[0]) < 0:
            return False
        sid = int(args[0])
        for obj in (f"/Scripting/Script{sid}", f"/{sid}"):     # KWin 6 / KWin 5
            sc = QDBusInterface("org.kde.KWin", obj, "org.kde.kwin.Script", QDBusConnection.sessionBus())
            if sc.isValid():
                sc.call("run")
                return True
        scr.call("start")
        return True

    @staticmethod
    def unload():
        try:
            QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                           QDBusConnection.sessionBus()).call("unloadScript", KWIN_SCRIPT_NAME)
        except Exception:
            pass


# --------------------------------------------------------------------------- aplikacja w zasobniku
class TrayApp:
    def __init__(self, qapp):
        self.qapp = qapp
        self.config = None
        self.status = None
        self.low_warned = False
        self.last_connected = None
        self.active_app = ""          # klasa aktywnego okna
        self.last_app = ""            # ostatnie okno inne niz CheerDots (do „Dodaj ostatnie okno”)
        self.win = SettingsWindow(self)
        self.tracker = ActiveWindowTracker(self.on_active_window)
        qapp.aboutToQuit.connect(ActiveWindowTracker.unload)
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(make_icon(None, False))
            menu = QMenu()
            self.a_status = QAction("–")
            self.a_status.setEnabled(False)
            menu.addAction(self.a_status)
            menu.addSeparator()
            menu.addAction("Ustawienia…", self.open_settings)
            menu.addAction("Wybierz urządzenie…", self.win.pick_device)
            menu.addAction("Połącz ponownie", lambda: self.cmd("reconnect"))
            menu.addSeparator()
            self.a_service = menu.addAction("Wyłącz sterownik", self.toggle_service)
            menu.addAction("Zakończ sterownik i ikonę", self.quit_all)
            menu.addAction("Zakończ tylko ikonę", qapp.quit)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda r: self.open_settings()
                                        if r == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.show()
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()
        if self.tray is None or "--show" in sys.argv:
            self.open_settings()
        if self.status is not None and not self.status.get("device"):
            QTimer.singleShot(500, self.first_run)

    def on_active_window(self, cls):
        self.active_app = cls
        if cls and not any(x in cls.lower() for x in ("cheerdots", "python")):
            self.last_app = cls
        try:
            ipc({"cmd": "active_app", "app": cls}, timeout=1.0)
        except (OSError, ValueError):
            pass

    def toggle_service(self):
        running = self.status is not None
        verb = "stop" if running else "start"
        ok, err = service(verb)
        if not ok:
            QMessageBox.warning(self.win, APP, f"Nie udało się {'wyłączyć' if running else 'włączyć'} sterownika:\n{err}")
            return
        self.tray_message("Sterownik wyłączony – myszka działa jak zwykła mysz, bez gestów.\n"
                          "Włącz go ponownie z tego menu (włączy się też sam po restarcie komputera)."
                          if running else "Sterownik włączony.")
        QTimer.singleShot(1500, self.refresh)

    def quit_all(self):
        if self.status is not None:
            ok, err = service("stop")
            if not ok:
                QMessageBox.warning(self.win, APP, f"Nie udało się wyłączyć sterownika:\n{err}")
                return
        self.qapp.quit()

    def first_run(self):
        self.open_settings()
        if QMessageBox.question(self.win, APP, "Nie znaleziono sparowanej myszki CheerDots.\nDodać ją teraz?") \
                == QMessageBox.StandardButton.Yes:
            self.win.pick_device()

    def cmd(self, name):
        try:
            ipc({"cmd": name})
        except OSError as e:
            self.tray_message(f"Sterownik nie odpowiada: {e}")

    def tray_message(self, text, icon=None):
        if self.tray:
            self.tray.showMessage(APP, text, icon or make_icon(80, True), 4000)

    def open_settings(self):
        self.load_config()
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()

    def load_config(self):
        try:
            self.config = ipc({"cmd": "get_config"})["config"]
        except (OSError, KeyError, ValueError):
            self.config = C.load()
        self.win.fill(self.config)

    def refresh(self):
        try:
            st = ipc({"cmd": "status"}).get("status")
        except (OSError, ValueError):
            st = None
        if st is not None and self.active_app and st.get("active_app") != self.active_app:
            self.on_active_window(self.active_app)          # demon uruchomiony ponownie - przypomnij okno
        self.status = st
        self.win.show_status(st)
        if not self.tray:
            return
        connected = bool(st and st.get("connected"))
        batt = st.get("battery") if st else None
        self.tray.setIcon(make_icon(batt, connected))
        if st is None:
            tip = "CheerDots: sterownik wyłączony"
        elif connected:
            tip = f"CheerDots: połączona, bateria {batt if batt is not None else '?'} %"
            if st.get("profile"):
                tip += f" · profil „{st['profile']}”"
        else:
            tip = "CheerDots: " + PHASES.get(st.get("phase"), st.get("phase", ""))
        self.tray.setToolTip(tip)
        self.a_status.setText(tip)
        self.a_service.setText("Wyłącz sterownik" if st is not None else "Włącz sterownik")
        if self.last_connected is not None and connected != self.last_connected:
            self.tray_message("Myszka połączona." if connected else "Myszka rozłączona.")
        self.last_connected = connected
        limit = (self.config or C.DEFAULT_CONFIG).get("notify_battery_below", 15)
        if connected and batt is not None and limit and batt < limit and not self.low_warned:
            self.low_warned = True
            self.tray.showMessage(APP, f"Niski poziom baterii myszki: {batt} %",
                                  QSystemTrayIcon.MessageIcon.Warning, 8000)
        if batt is not None and batt >= (limit or 0) + 5:
            self.low_warned = False


def main():
    qapp = QApplication(sys.argv)
    qapp.setApplicationName(APP)
    qapp.setDesktopFileName("cheerdots")
    qapp.setQuitOnLastWindowClosed(False)
    app = TrayApp(qapp)          # noqa: F841
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
