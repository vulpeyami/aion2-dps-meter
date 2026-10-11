"""Hakari (秤): oyunun ustunde duran kucuk DPS penceresi (tkinter).

Sol tik + surukle: tasi.  Sag tik: menu.  Bir satira cift tik: "bu benim".
"""

from __future__ import annotations

import ctypes
import json
import tkinter as tk
import tkinter.font as tkfont

from .meter import VIEW_ALL, VIEW_PARTY, VIEW_SELF, Meter, Snapshot

# Olculer 96 DPI icin; ekran olcegine gore carpilir.
WIDTH = 290
ROW_H = 20
HEADER_H = 74
COLUMNS_H = 14
# Satir sutunlarinin sag kenarlari: toplam hasar, DPS, yuzde.
TOTAL_X = WIDTH - 112
DPS_X = WIDTH - 50
SHARE_X = WIDTH - 9
MAX_ROWS = 8
BG = "#14161b"
FG = "#e8e8e8"
DIM = "#8a8f98"
ACCENT = "#f0b43c"
FONT = "Segoe UI"

# Cubuk renkleri; ustlerindeki beyaz yazi okunacak kadar koyu.
CLASS_COLORS = {
    "gladiator": "#c0662a",
    "templar": "#3a62b8",
    "assassin": "#7d4bbf",
    "ranger": "#2f8a4f",
    "sorcerer": "#b8323f",
    "elementalist": "#278a99",
    "cleric": "#a88b22",
    "chanter": "#6f9a2a",
    "brawler": "#b5448a",
}
UNKNOWN_COLOR = "#4a5160"
CLASS_NAMES = {
    "gladiator": "Gladiator", "templar": "Templar", "assassin": "Assassin",
    "ranger": "Ranger", "sorcerer": "Sorcerer", "elementalist": "Elementalist",
    "cleric": "Cleric", "chanter": "Chanter", "brawler": "Brawler",
}
VIEW_LABELS = {VIEW_SELF: "Sadece ben", VIEW_PARTY: "Parti", VIEW_ALL: "Herkes"}


def fmt_num(v: float) -> str:
    v = float(v)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= limit:
            return f"{v / limit:.2f}{suffix}" if v < limit * 10 else f"{v / limit:.1f}{suffix}"
    return f"{v:.0f}"


def fmt_time(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def load_settings(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class Overlay:
    def __init__(self, meter: Meter, status_fn, on_quit, settings_path: str | None = None):
        self.meter = meter
        self.status_fn = status_fn
        self.on_quit = on_quit
        self.settings_path = settings_path
        settings = load_settings(settings_path)
        self._rows_hitbox: list[tuple[float, float, int]] = []

        try:  # yuksek DPI ekranlarda bulanik yazi olmasin
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        self.root = tk.Tk()
        self.s = self.root.winfo_fpixels("1i") / 96.0
        self._fonts = {bold: tkfont.Font(family=FONT, size=9, weight="bold" if bold else "normal")
                       for bold in (False, True)}
        self.width = round(WIDTH * self.s)
        self.root.title("Hakari 秤")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.88)
        self.root.configure(bg=BG)
        x, y = settings.get("x", 40), settings.get("y", 200)
        self.root.geometry(f"{self.width}x{round(HEADER_H * self.s)}+{int(x)}+{int(y)}")

        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        view = settings.get("view", VIEW_PARTY)
        self.view = tk.StringVar(value=view if view in VIEW_LABELS else VIEW_PARTY)
        self.menu = tk.Menu(self.root, tearoff=0)
        for key, label in VIEW_LABELS.items():
            self.menu.add_radiobutton(label=label, value=key, variable=self.view,
                                      command=self._save_settings)
        self.menu.add_separator()
        self.menu.add_command(label="Sıfırla", command=self.meter.reset)
        self.menu.add_command(label="Çıkış", command=self._quit)

        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._save_settings())
        self.canvas.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self._drag = (0, 0)
        self._height = 0

    def run(self) -> None:
        self._tick()
        self.root.mainloop()

    def _quit(self) -> None:
        self._save_settings()
        self.on_quit()
        self.root.destroy()

    def _save_settings(self) -> None:
        if not self.settings_path:
            return
        data = {"view": self.view.get(), "x": self.root.winfo_x(), "y": self.root.winfo_y()}
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

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
            self._draw(self.meter.snapshot(max_rows=MAX_ROWS, view=self.view.get()))
        finally:
            self.root.after(250, self._tick)

    def _text(self, x, y, text, size, color=FG, anchor="nw", bold=False, width=None):
        s = self.s
        return self.canvas.create_text(
            x * s, y * s, anchor=anchor, text=text, fill=color, width=width and width * s,
            font=(FONT, size, "bold" if bold else "normal"))

    def _fit(self, text: str, max_width: float, bold: bool) -> str:
        """Ismi piksel genisligine gore kisaltir (Korece/Japonca karakterler daha genis)."""
        font = self._fonts[bold]
        limit = max_width * self.s
        if font.measure(text) <= limit:
            return text
        while text and font.measure(text + "…") > limit:
            text = text[:-1]
        return text + "…"

    def _draw(self, snap: Snapshot) -> None:
        c, s = self.canvas, self.s
        c.delete("all")
        view = self.view.get()

        title = f"秤 HAKARI  ·  {VIEW_LABELS[view]}"
        if view == VIEW_PARTY and not snap.in_group:
            title += " (parti yok)"
        self._text(8, 5, title, 8, DIM, bold=True)
        timer = fmt_time(snap.duration) if snap.duration else "--:--"
        self._text(WIDTH - 8, 5, f"{'●' if snap.active else '○'} {timer}", 8,
                   ACCENT if snap.active else DIM, anchor="ne")

        me = snap.self_row
        if me is not None:
            big = self._text(8, 18, fmt_num(me.dps), 20, bold=True)
            _, _, x2, y2 = c.bbox(big)
            c.create_text(x2 + 4 * s, y2 - 8 * s, anchor="sw", text="DPS", fill=DIM,
                          font=(FONT, 9))
            cls = f"   •   {CLASS_NAMES[me.cls]}" if me.cls else ""
            self._text(8, 54, f"Toplam {fmt_num(me.total)}   •   Krit %{me.crit_rate * 100:.0f}{cls}",
                       8, DIM)
        else:
            status = self.status_fn()
            if not status.startswith("Dinleniyor"):
                hint = status
            elif snap.rows:
                hint = "Karakterin bulunamadı: listede kendi satırına çift tıkla"
            else:
                hint = "Hasar bekleniyor..."
            self._text(8, 24, hint, 9, DIM, width=WIDTH - 16)

        # Sadece ben gorunumunde liste yok; buyuk rakam yeterli.
        rows = snap.rows if (view != VIEW_SELF or snap.self_id is None) else []
        self._rows_hitbox = []
        y = HEADER_H
        if rows:
            for x, label in ((TOTAL_X, "Toplam"), (DPS_X, "DPS"), (SHARE_X, "%")):
                self._text(x, y + COLUMNS_H / 2, label, 7, DIM, anchor="e")
            y += COLUMNS_H
        top_total = rows[0].total if rows else 1
        for r in rows:
            frac = r.total / top_total if top_total else 0
            color = CLASS_COLORS.get(r.cls, UNKNOWN_COLOR)
            c.create_rectangle(4 * s, (y + 2) * s, (4 + (WIDTH - 8) * frac) * s, (y + ROW_H - 2) * s,
                               fill=color, width=0)
            if r.is_self:  # kendi satirimizi kenarlikla belirt
                c.create_rectangle(4 * s, (y + 2) * s, (WIDTH - 4) * s, (y + ROW_H - 2) * s,
                                   outline=ACCENT, width=max(1, round(s)))
            mid = y + ROW_H / 2
            self._text(9, mid, self._fit(r.name, TOTAL_X - 52, r.is_self), 9, anchor="w",
                       bold=r.is_self)
            self._text(TOTAL_X, mid, fmt_num(r.total), 9, anchor="e")
            self._text(DPS_X, mid, fmt_num(r.dps), 9, anchor="e")
            self._text(SHARE_X, mid, f"{r.share * 100:.0f}%", 9, anchor="e")
            self._rows_hitbox.append((y * s, (y + ROW_H) * s, r.source))
            y += ROW_H

        height = round((y + (4 if rows else 0)) * s)
        if height != self._height:
            self._height = height
            self.root.geometry(f"{self.width}x{height}")
