"""Oyunun ustunde duran kucuk DPS penceresi (tkinter).

Sol tik + surukle: tasi.  Sag tik: menu.  Bir satira cift tik: "bu benim".
"""

from __future__ import annotations

import ctypes
import tkinter as tk

from .meter import Meter, Snapshot

# Olculer 96 DPI icin; ekran olcegine gore carpilir.
WIDTH = 240
ROW_H = 20
HEADER_H = 74
MAX_ROWS = 6
BG = "#14161b"
FG = "#e8e8e8"
DIM = "#8a8f98"
ACCENT = "#f0b43c"
BAR = "#2f5fb0"
BAR_SELF = "#b8741f"
FONT = "Segoe UI"


def fmt_num(v: float) -> str:
    v = float(v)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= limit:
            return f"{v / limit:.2f}{suffix}" if v < limit * 10 else f"{v / limit:.1f}{suffix}"
    return f"{v:.0f}"


def fmt_time(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


class Overlay:
    def __init__(self, meter: Meter, status_fn, on_quit):
        self.meter = meter
        self.status_fn = status_fn
        self.on_quit = on_quit
        self.show_others = True
        self._rows_hitbox: list[tuple[float, float, int]] = []

        try:  # yuksek DPI ekranlarda bulanik yazi olmasin
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        self.root = tk.Tk()
        self.s = self.root.winfo_fpixels("1i") / 96.0
        self.width = round(WIDTH * self.s)
        self.root.title("Aion2 DPS")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.88)
        self.root.configure(bg=BG)
        self.root.geometry(f"{self.width}x{round(HEADER_H * self.s)}+40+200")

        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Sıfırla", command=self.meter.reset)
        self.menu.add_command(label="Diğer oyuncuları gizle/göster", command=self._toggle_others)
        self.menu.add_separator()
        self.menu.add_command(label="Çıkış", command=self._quit)

        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self._drag = (0, 0)
        self._height = 0

    def run(self) -> None:
        self._tick()
        self.root.mainloop()

    def _quit(self) -> None:
        self.on_quit()
        self.root.destroy()

    def _toggle_others(self) -> None:
        self.show_others = not self.show_others

    def _drag_start(self, e) -> None:
        self._drag = (e.x, e.y)

    def _drag_move(self, e) -> None:
        x = self.root.winfo_x() + e.x - self._drag[0]
        y = self.root.winfo_y() + e.y - self._drag[1]
        self.root.geometry(f"+{x}+{y}")

    def _on_double_click(self, e) -> None:
        for top, bottom, source in self._rows_hitbox:
            if top <= e.y < bottom:
                self.meter.set_self(source)
                return

    def _tick(self) -> None:
        try:
            self._draw(self.meter.snapshot(max_rows=MAX_ROWS))
        finally:
            self.root.after(250, self._tick)

    def _text(self, x, y, text, size, color=FG, anchor="nw", bold=False, width=None):
        s = self.s
        return self.canvas.create_text(
            x * s, y * s, anchor=anchor, text=text, fill=color, width=width and width * s,
            font=(FONT, size, "bold" if bold else "normal"))

    def _draw(self, snap: Snapshot) -> None:
        c, s = self.canvas, self.s
        c.delete("all")

        self._text(8, 5, "AION2 DPS", 8, DIM, bold=True)
        timer = fmt_time(snap.duration) if snap.duration else "--:--"
        self._text(WIDTH - 8, 5, f"{'●' if snap.active else '○'} {timer}", 8,
                   ACCENT if snap.active else DIM, anchor="ne")

        me = snap.self_row
        if me is not None:
            big = self._text(8, 18, fmt_num(me.dps), 20, bold=True)
            x1, _, x2, y2 = c.bbox(big)
            c.create_text(x2 + 4 * s, y2 - 8 * s, anchor="sw", text="DPS", fill=DIM,
                          font=(FONT, 9))
            self._text(8, 54, f"Toplam {fmt_num(me.total)}   •   Krit %{me.crit_rate * 100:.0f}", 8, DIM)
        else:
            status = self.status_fn()
            if not status.startswith("Dinleniyor"):
                hint = status
            elif snap.rows:
                hint = "Karakterin bulunamadı: listede kendi satırına çift tıkla"
            else:
                hint = "Hasar bekleniyor..."
            self._text(8, 24, hint, 9, DIM, width=WIDTH - 16)

        # Karakter bilinmiyorsa liste her zaman gorunur ki cift tikla secilebilsin.
        rows = snap.rows if (self.show_others or snap.self_id is None) else []
        self._rows_hitbox = []
        y = HEADER_H
        top_total = rows[0].total if rows else 1
        for r in rows:
            frac = r.total / top_total if top_total else 0
            c.create_rectangle(4 * s, (y + 2) * s, (4 + (WIDTH - 8) * frac) * s, (y + ROW_H - 2) * s,
                               fill=BAR_SELF if r.is_self else BAR, width=0)
            self._text(9, y + ROW_H / 2, r.name[:16], 9, anchor="w", bold=r.is_self)
            self._text(WIDTH - 9, y + ROW_H / 2, f"{fmt_num(r.dps)}/s   {r.share * 100:.0f}%", 9,
                       anchor="e")
            self._rows_hitbox.append((y * s, (y + ROW_H) * s, r.source))
            y += ROW_H

        height = round((y + (4 if rows else 0)) * s)
        if height != self._height:
            self._height = height
            self.root.geometry(f"{self.width}x{height}")
