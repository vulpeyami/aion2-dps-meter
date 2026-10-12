"""Pencere icin Windows yardimcilari: tiklama gecirgenligi ve genel kisayol tuslari.

Ikisi de sadece Hakari'nin kendi penceresini ilgilendirir; oyuna dokunmaz. Kisayollar
RegisterHotKey ile alinir (klavye kancasi degil): Windows tusa basildiginda bize haber verir.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0002, 0x0004, 0x4000
WM_HOTKEY = 0x0312

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.GetParent.argtypes = (wintypes.HWND,)
_user32.GetParent.restype = wintypes.HWND
_user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
_user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                wintypes.UINT)


def toplevel_hwnd(tk_root) -> int:
    """tkinter'in ic penceresinin sahibi olan asil pencere."""
    return _user32.GetParent(tk_root.winfo_id()) or tk_root.winfo_id()


def set_click_through(hwnd: int, enabled: bool) -> None:
    """Acikken fare tiklamalari pencerenin arkasindaki oyuna gecer."""
    style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED
    style = style | WS_EX_TRANSPARENT if enabled else style & ~WS_EX_TRANSPARENT
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


class Hotkeys:
    """Ctrl+Shift+<harf> kisayollarini arka planda dinler; basilanlar queue'ya duser."""

    def __init__(self, letters: dict[str, str], log=lambda msg: None):
        self.events: queue.Queue[str] = queue.Queue()
        self.failed: list[str] = []
        self._letters = letters  # harf -> olay adi
        self._log = log
        self._ready = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="hotkeys").start()
        self._ready.wait(2)

    def _loop(self) -> None:
        ids = {}
        for i, (letter, event) in enumerate(self._letters.items(), start=1):
            if _user32.RegisterHotKey(None, i, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord(letter)):
                ids[i] = event
            else:
                self.failed.append(letter)
                self._log(f"Kisayol alinamadi (baska program kullaniyor): Ctrl+Shift+{letter}")
        self._ready.set()
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                self.events.put(ids[msg.wParam])
