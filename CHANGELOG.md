# Changelog

## 0.4.0 – 2026-11-22
- Per-application profiles (active window reported by a small KWin script; built-in “Browser” and
  “Player” profiles, disabled by default).
- Double gestures (same swipe twice within a configurable window, default 400 ms).
- Button gestures (swipe while holding the right or middle button; normal clicks keep working).
- Battery percentage drawn on the tray icon; active profile in the tooltip.
- Settings export / import (JSON).
- 14 new actions (tabs, F5, full screen, Alt+Tab, minimize/maximize, close window, media, terminal…).
- Unit tests and GitHub Actions build.

## 0.3.x – 2026-11-21
- **Uses the system Bluetooth stack (BlueZ)** – the adapter is no longer taken over.
- Key discovery: the mouse enables gestures only for hosts sending ATT MTU 527 (Windows) or 185 (macOS);
  the package sets `[GATT] ExchangeMTU = 185` in `/etc/bluetooth/main.conf`.
- Input device is grabbed (EVIOCGRAB) and re-emitted through uinput; gesture shortcuts mapped to actions.
- Pointer speed scaling, tray menu to stop/start the driver (polkit rule, no password), raw-key debug mode.

## 0.2.0 – 2026-11-21
- Own Bluetooth stack (Google Bumble) imitating Windows; took over the whole adapter.
  Kept for reference in `dev/legacy/`.
