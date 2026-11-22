# dev/

Materials from reverse-engineering the CheerDots 2 firmware behaviour (November 2026).

- `research/` – test scripts used to find out why the mouse sends no gestures to Linux.
  They drove the **legacy** Bumble-based daemon (`legacy/daemon_bumble.py`, version 0.2) with switches
  that turned single “Windows-like” behaviours off, recorded HCI traffic with `btmon` and finally
  scanned ATT MTU values. Result: the firmware recognises the host by the MTU in *Exchange MTU Request*
  (527 = Windows, 185 = macOS → gestures; 517 = Linux default → no gestures). Details: `../WIEDZA.md`.
- `legacy/daemon_bumble.py` – the previous driver that used its own Bluetooth stack and occupied the
  whole adapter. Not used by the package any more.

Logs and HCI captures are not committed (they contain device addresses).
