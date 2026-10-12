"""Hakari (秤): oyunun ustunde duran kucuk DPS penceresi (tkinter).

Sol tik + surukle: tasi.  Satira tik: skill dokumu.  Sag tik: menu (satirda: "bu benim").
Ctrl + fare tekerlegi: boyut.  Ctrl+Shift+H gizle, L kilitle (tiklama gecer), C ozeti kopyala.
"""

from __future__ import annotations

import ctypes
import json
import os
import time
import tkinter as tk
import tkinter.font as tkfont

from .meter import (MODE_DEALT, MODE_TAKEN, SCOPE_FIGHT, SCOPE_TOTAL, VIEW_ALL, VIEW_PARTY,
                    VIEW_SELF, Meter, Snapshot)
from .records import MIN_RECORD_SEC, Records

# Olculer 96 DPI ve %100 boyut icin; ekran olcegi ve kullanicinin boyut secimiyle carpilir.
WIDTH = 290
ROW_H = 20
TAB_H = 17
GRAPH_H = 20
COLUMNS_H = 14
# Satir sutunlarinin sag kenarlari: toplam hasar, DPS, yuzde.
TOTAL_X = WIDTH - 112
DPS_X = WIDTH - 50
SHARE_X = WIDTH - 9
MAX_ROWS = 8
MAX_SKILL_ROWS = 12
MAX_BOSS_TABS = 3
FG = "#e8e8e8"
DIM = "#8a8f98"
ACCENT = "#f0b43c"
# Arka plan temalari: ad, arka plan, sekme/baslik zemini, grafik dolgusu.
# Hepsi koyu; beyaz yazi ve sinif renkleri her birinde okunur kalir.
THEMES = {
    "koyu": ("Koyu gri", "#14161b", "#262a33", "#3a3222"),
    "siyah": ("Siyah", "#000000", "#1c1c1f", "#2e2a1e"),
    "antrasit": ("Antrasit", "#2a2c31", "#3c3f46", "#4a4232"),
    "lacivert": ("Lacivert", "#0d1626", "#1e2b42", "#22324a"),
    "mor": ("Mor", "#1a1226", "#2e2242", "#3b2b4c"),
    "yesil": ("Koyu yeşil", "#0e1a14", "#1d2f25", "#23392b"),
    "bordo": ("Bordo", "#230f10", "#3b1d1e", "#47291f"),
}
FONT = "Segoe UI"
SCALES = (0.8, 0.9, 1.0, 1.15, 1.3, 1.5, 1.75)
OPACITIES = (1.0, 0.9, 0.8, 0.7, 0.6)
IDLE_FADE = 0.5  # dovus disinda saydamlik bu oranda azalir
HOTKEYS = {"H": "gizle", "L": "kilit", "C": "kopyala"}

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
MODE_LABELS = {MODE_DEALT: "Verilen hasar", MODE_TAKEN: "Alınan hasar"}


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


def smooth_series(values: list[int], width: int) -> list[float]:
    """Saniyelik hasari kayan ortalamayla yumusatip en fazla `width` noktaya indirir."""
    n = len(values)
    k = max(5, n // 60)
    acc, out = 0, []
    for i, v in enumerate(values):
        acc += v
        if i >= k:
            acc -= values[i - k]
        out.append(acc / min(i + 1, k))
    if n <= width:
        return out
    step = n / width
    return [max(out[int(i * step):max(int(i * step) + 1, int((i + 1) * step))]) for i in range(width)]


def summary_text(snap: Snapshot, mode: str, limit: int = 6) -> str:
    """Sohbete yapistirmak icin tek satirlik ozet."""
    head = f"[Hakari] {snap.label} {fmt_time(snap.duration)}"
    if mode == MODE_TAKEN:
        head += " · alınan hasar"
        parts = [f"{r.name} {fmt_num(r.total)} ({r.share * 100:.0f}%)" for r in snap.rows[:limit]]
    else:
        parts = [f"{r.name} {fmt_num(r.dps)} DPS ({r.share * 100:.0f}%)" for r in snap.rows[:limit]]
        if not parts and snap.self_row:
            r = snap.self_row
            parts = [f"{r.name} {fmt_num(r.dps)} DPS, toplam {fmt_num(r.total)}"]
    return head + (" | " + " · ".join(parts) if parts else "")


class Overlay:
    def __init__(self, meter: Meter, status_fn, on_quit, settings_path: str | None = None,
                 records: Records | None = None, log=lambda msg: None):
        self.meter = meter
        self.status_fn = status_fn
        self.on_quit = on_quit
        self.settings_path = settings_path
        self.records = records
        self.log = log
        settings = load_settings(settings_path)

        try:  # yuksek DPI ekranlarda bulanik yazi olmasin
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        self.root = tk.Tk()
        self.dpi = self.root.winfo_fpixels("1i") / 96.0
        self.root.title("Hakari 秤")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # Ayarlar
        view = settings.get("view", VIEW_PARTY)
        self.view = view if view in VIEW_LABELS else VIEW_PARTY
        mode = settings.get("mode", MODE_DEALT)
        self.mode = mode if mode in MODE_LABELS else MODE_DEALT
        scope = settings.get("scope", SCOPE_FIGHT)
        self.scope: str | int = scope if scope in (SCOPE_FIGHT, SCOPE_TOTAL) else SCOPE_FIGHT
        scale = settings.get("scale", 1.0)
        self.scale = scale if scale in SCALES else 1.0
        opacity = settings.get("opacity", 0.9)
        self.opacity = opacity if opacity in OPACITIES else 0.9
        self.locked = bool(settings.get("locked", False))
        theme = settings.get("theme", "koyu")
        self.theme = theme if theme in THEMES else "koyu"
        self._saved_self_name = settings.get("self_name")

        self.detail: int | None = None  # skill dokumu acik olan oyuncu
        self.hidden = False
        self.hover = False
        self._alpha = None
        self._flash: tuple[str, float] | None = None
        self._hitboxes: list[tuple[float, float, float, float, object]] = []
        self._row_at: list[tuple[float, float, int, str]] = []
        self._press = None
        self._height = 0
        self._font_cache: dict[tuple[int, bool], tkfont.Font] = {}
        self._snap: Snapshot | None = None
        self._saved_changes = meter.changes
        self._apply_scale()
        x, y = settings.get("x", 40), settings.get("y", 200)
        if not (isinstance(x, int) and isinstance(y, int)):  # bozuk ayar dosyasi
            x, y = 40, 200
        self.root.geometry(f"{self.width}x{round(80 * self.s)}+{x}+{y}")

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self._apply_theme()
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.root.bind("<Enter>", lambda e: self._set_hover(True))
        self.root.bind("<Leave>", lambda e: self._set_hover(False))
        self.menu: tk.Menu | None = None

        self.hotkeys = None
        try:
            from .winutil import Hotkeys
            self.hotkeys = Hotkeys(HOTKEYS, log)
        except (OSError, AttributeError) as e:
            log(f"Kisayollar kullanilamiyor: {e}")
        self.root.after(200, self._apply_lock)

    # --- calisma dongusu ---

    def run(self) -> None:
        self._tick()
        self.root.mainloop()

    def _quit(self) -> None:
        self._save_settings()
        if self.records:
            self.records.finish(self.meter.export_run())
        self.on_quit()
        self.root.destroy()

    def _tick(self) -> None:
        try:
            self._handle_hotkeys()
            snap = self.meter.snapshot(max_rows=MAX_ROWS, view=self.view, scope=self.scope,
                                       mode=self.mode)
            self._snap = snap
            if snap.scope != self.scope:  # secili dovus artik yok (sifirlandi)
                self.scope = SCOPE_FIGHT
            if snap.self_name and snap.self_name != self._saved_self_name:
                # Karakter adini hatirla: sonraki acilista teleport gerekmeden taninir.
                self._saved_self_name = snap.self_name
                self._save_settings()
            if (self.records and self.meter.changes != self._saved_changes
                    and not self.meter.in_combat()):
                # Dovus bitti: zindan kaydini guncelle.
                self._saved_changes = self.meter.changes
                self.records.save(self.meter.export_run())
            self._update_alpha(snap)
            if not self.hidden:
                self._draw(snap)
        finally:
            self.root.after(250, self._tick)

    def _handle_hotkeys(self) -> None:
        if self.hotkeys is None:
            return
        while not self.hotkeys.events.empty():
            event = self.hotkeys.events.get_nowait()
            if event == "gizle":
                self._toggle_hidden()
            elif event == "kilit":
                self._toggle_lock()
            elif event == "kopyala":
                self._copy_summary()

    # --- ayarlar ---

    def _save_settings(self) -> None:
        if not self.settings_path:
            return
        data = {"view": self.view, "mode": self.mode,
                "scope": self.scope if isinstance(self.scope, str) else SCOPE_FIGHT,
                "scale": self.scale, "opacity": self.opacity, "locked": self.locked,
                "theme": self.theme,
                "x": self.root.winfo_x(), "y": self.root.winfo_y(),
                "self_name": self._saved_self_name}
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _set(self, attr: str, value) -> None:
        setattr(self, attr, value)
        if attr == "scale":
            self._apply_scale()
            self.root.geometry(f"{self.width}x{max(self._height, 1)}")
        elif attr == "theme":
            self._apply_theme()
        self._save_settings()
        if self._snap is not None:
            self._draw(self.meter.snapshot(max_rows=MAX_ROWS, view=self.view, scope=self.scope,
                                           mode=self.mode))

    def _apply_scale(self) -> None:
        self.s = self.dpi * self.scale
        self.width = round(WIDTH * self.s)
        self._font_cache.clear()

    def _apply_theme(self) -> None:
        _, self.bg, self.tab_bg, self.graph_fill = THEMES[self.theme]
        self.root.configure(bg=self.bg)
        self.canvas.configure(bg=self.bg)

    def _on_ctrl_wheel(self, e) -> None:
        i = SCALES.index(self.scale) + (1 if e.delta > 0 else -1)
        if 0 <= i < len(SCALES):
            self._set("scale", SCALES[i])

    # --- pencere durumu ---

    def _hwnd(self) -> int | None:
        try:
            from .winutil import toplevel_hwnd
            return toplevel_hwnd(self.root)
        except (OSError, AttributeError):
            return None

    def _apply_lock(self) -> None:
        hwnd = self._hwnd()
        if hwnd:
            from .winutil import set_click_through
            set_click_through(hwnd, self.locked)

    def _toggle_lock(self) -> None:
        self.locked = not self.locked
        self._apply_lock()
        self._save_settings()
        self._flash_msg("Kilitlendi: tıklamalar oyuna geçer" if self.locked else "Kilit açıldı")

    def _toggle_hidden(self) -> None:
        self.hidden = not self.hidden
        if self.hidden:
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self._alpha = None
            self.root.after(50, self._apply_lock)

    def _set_hover(self, on: bool) -> None:
        self.hover = on

    def _update_alpha(self, snap: Snapshot) -> None:
        busy = self.meter.in_combat() or self.hover or self.detail is not None or not snap.rows
        alpha = self.opacity if busy else max(0.25, self.opacity * IDLE_FADE)
        if alpha != self._alpha:
            self._alpha = alpha
            self.root.attributes("-alpha", alpha)

    def _flash_msg(self, text: str) -> None:
        self._flash = (text, time.monotonic() + 2.0)

    # --- islemler ---

    def _copy_summary(self) -> None:
        snap = self.meter.snapshot(max_rows=MAX_ROWS, view=self.view, scope=self.scope,
                                   mode=self.mode)
        if not snap.duration:
            self._flash_msg("Kopyalanacak veri yok")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(summary_text(snap, self.mode))
        self._flash_msg("Özet kopyalandı ✓")

    def _reset(self) -> None:
        if self.records:
            self.records.finish(self.meter.export_run())
        self.meter.reset()
        self._saved_changes = self.meter.changes
        self.detail = None
        if isinstance(self.scope, int):
            self.scope = SCOPE_FIGHT

    def _open_records(self) -> None:
        if not self.records:
            return
        os.makedirs(self.records.folder, exist_ok=True)
        os.startfile(self.records.folder)

    # --- fare ---

    def _on_press(self, e) -> None:
        self._press = (e.x, e.y, e.x_root, e.y_root, False)

    def _on_motion(self, e) -> None:
        if self._press is None or self.locked:
            return
        x0, y0, xr, yr, moved = self._press
        if not moved and abs(e.x_root - xr) + abs(e.y_root - yr) < 4:
            return
        self._press = (x0, y0, xr, yr, True)
        self.root.geometry(f"+{int(e.x_root - x0)}+{int(e.y_root - y0)}")

    def _on_release(self, e) -> None:
        press, self._press = self._press, None
        if press is None:
            return
        if press[4]:
            self._save_settings()
            return
        for x1, y1, x2, y2, action in self._hitboxes:
            if x1 <= e.x < x2 and y1 <= e.y < y2:
                action()
                return

    def _row_under(self, y: float) -> tuple[int, str] | None:
        for top, bottom, source, name in self._row_at:
            if top <= y < bottom:
                return source, name
        return None

    def _on_right_click(self, e) -> None:
        if self.menu is not None:
            self.menu.destroy()
        m = self.menu = tk.Menu(self.root, tearoff=0)
        row = self._row_under(e.y)
        if row:
            source, name = row
            m.add_command(label=f"Bu benim: {name}", command=lambda: self.meter.set_self(source))
            m.add_command(label=f"Skill dökümü: {name}", command=lambda: self._open_detail(source))
            m.add_separator()
        if self.detail is not None:
            m.add_command(label="◀ Listeye dön", command=lambda: self._open_detail(None))
            m.add_separator()

        def radio(menu, label, attr, value):
            var = tk.BooleanVar(master=m, value=getattr(self, attr) == value)
            menu.add_checkbutton(label=label, variable=var, command=lambda: self._set(attr, value))

        for key, label in VIEW_LABELS.items():
            radio(m, label, "view", key)
        m.add_separator()
        for key, label in MODE_LABELS.items():
            radio(m, label, "mode", key)
        m.add_separator()
        radio(m, "Canlı dövüş", "scope", SCOPE_FIGHT)
        radio(m, "Zindan toplamı", "scope", SCOPE_TOTAL)
        fights = self._snap.fights if self._snap else []
        if fights:
            history = tk.Menu(m, tearoff=0)
            for f in reversed(fights[-25:]):
                dps = f" · {fmt_num(f.self_dps)} DPS" if f.self_dps else ""
                star = "★ " if f.boss_name else ""
                radio(history, f"{star}{f.label} · {fmt_time(f.duration)}{dps}", "scope", f.serial)
            m.add_cascade(label="Geçmiş dövüşler", menu=history)
        m.add_separator()
        sizes = tk.Menu(m, tearoff=0)
        for sc in SCALES:
            radio(sizes, f"%{sc * 100:.0f}", "scale", sc)
        m.add_cascade(label="Boyut  (Ctrl + tekerlek)", menu=sizes)
        alphas = tk.Menu(m, tearoff=0)
        for op in OPACITIES:
            radio(alphas, f"%{op * 100:.0f}", "opacity", op)
        m.add_cascade(label="Saydamlık", menu=alphas)
        themes = tk.Menu(m, tearoff=0)
        for key, (label, *_) in THEMES.items():
            radio(themes, label, "theme", key)
        m.add_cascade(label="Arka plan rengi", menu=themes)
        m.add_command(label="Kilitle (tıklama oyuna geçer)", accelerator="Ctrl+Shift+L",
                      command=self._toggle_lock)
        m.add_command(label="Özeti kopyala", accelerator="Ctrl+Shift+C", command=self._copy_summary)
        m.add_command(label="Gizle", accelerator="Ctrl+Shift+H", command=self._toggle_hidden)
        m.add_separator()
        m.add_command(label="Sıfırla (yeni zindan)", command=self._reset)
        if self.records:
            m.add_command(label="Kayıt klasörünü aç", command=self._open_records)
        m.add_command(label="Çıkış", command=self._quit)
        m.tk_popup(e.x_root, e.y_root)

    def _open_detail(self, source: int | None) -> None:
        self.detail = source

    # --- cizim yardimcilari ---

    def _font(self, size: float, bold: bool = False):
        return (FONT, -max(6, round(size * 4 / 3 * self.s)), "bold" if bold else "normal")

    def _measure(self, text: str, size: float, bold: bool = False) -> float:
        """Metnin genisligi (olcek oncesi birimde)."""
        key = (round(size * 4 / 3 * self.s), bold)
        font = self._font_cache.get(key)
        if font is None:
            font = self._font_cache[key] = tkfont.Font(
                root=self.root, family=FONT, size=-max(6, key[0]), weight="bold" if bold else "normal")
        return font.measure(text) / self.s

    def _fit(self, text: str, max_width: float, size: float = 9, bold: bool = False) -> str:
        """Metni genislige gore kisaltir (Korece/Japonca karakterler daha genis)."""
        if self._measure(text, size, bold) <= max_width:
            return text
        while text and self._measure(text + "…", size, bold) > max_width:
            text = text[:-1]
        return text + "…"

    def _text(self, x, y, text, size, color=FG, anchor="nw", bold=False, width=None):
        s = self.s
        return self.canvas.create_text(x * s, y * s, anchor=anchor, text=text, fill=color,
                                       width=width and width * s, font=self._font(size, bold))

    def _rect(self, x1, y1, x2, y2, **kw):
        s = self.s
        return self.canvas.create_rectangle(x1 * s, y1 * s, x2 * s, y2 * s, **kw)

    def _hit(self, x1, y1, x2, y2, action) -> None:
        s = self.s
        self._hitboxes.append((x1 * s, y1 * s, x2 * s, y2 * s, action))

    # --- cizim ---

    def _draw(self, snap: Snapshot) -> None:
        self.canvas.delete("all")
        self._hitboxes, self._row_at = [], []
        y = self._draw_header(snap)
        if self.detail is not None:
            y = self._draw_detail(y, snap)
        else:
            if snap.fights:
                y = self._draw_tabs(y, snap)
            y = self._draw_rows(y, snap)
        height = round((y + 4) * self.s)
        if height != self._height:
            self._height = height
            self.root.geometry(f"{self.width}x{height}")

    def _draw_header(self, snap: Snapshot) -> float:
        title = f"秤 HAKARI  ·  {VIEW_LABELS[self.view]}"
        if self.view == VIEW_PARTY and not snap.in_group:
            title += " (parti yok)"
        if self.locked:
            title += "  🔒"
        if self._flash and time.monotonic() < self._flash[1]:
            self._text(8, 5, self._flash[0], 8, ACCENT, bold=True)
        else:
            self._flash = None
            self._text(8, 5, self._fit(title, WIDTH - 70, 8, True), 8, DIM, bold=True)
        timer = fmt_time(snap.duration) if snap.duration else "--:--"
        self._text(WIDTH - 8, 5, f"{'●' if snap.active else '○'} {timer}", 8,
                   ACCENT if snap.active else DIM, anchor="ne")

        me = snap.self_row
        if me is None:
            status = self.status_fn()
            if not status.startswith("Dinleniyor"):
                hint = status
            elif snap.rows:
                hint = "Karakterin bulunamadı: listede kendi satırına sağ tıkla → Bu benim"
            else:
                hint = "Hasar bekleniyor..."
            self._text(8, 24, hint, 9, DIM, width=WIDTH - 16)
            return 58

        big = self._text(8, 18, fmt_num(me.dps), 20, bold=True)
        _, _, x2, y2 = self.canvas.bbox(big)
        self.canvas.create_text(x2 + 4 * self.s, y2 - 8 * self.s, anchor="sw", text="DPS",
                                fill=DIM, font=self._font(9))
        self._hit(0, 16, WIDTH / 2, 52, lambda: self._open_detail(me.source))
        self._draw_record(snap, me)
        cls = f"   •   {CLASS_NAMES[me.cls]}" if me.cls else ""
        summary = f"Toplam {fmt_num(me.total)}   •   Krit %{me.crit_rate * 100:.0f}{cls}"
        self._text(8, 54, summary, 8, DIM)
        y = 70
        if len(snap.timeline) >= 2:
            peak = self._draw_graph(y, snap.timeline, CLASS_COLORS.get(me.cls, ACCENT))
            label = f"zirve {fmt_num(peak)}/sn"
            if self._measure(summary, 8) + self._measure(label, 7) + 24 <= WIDTH:
                self._text(WIDTH - 8, 55, label, 7, DIM, anchor="ne")
            y += GRAPH_H + 4
        return y

    def _draw_record(self, snap: Snapshot, me) -> None:
        if not (self.records and snap.boss_name and snap.duration >= MIN_RECORD_SEC):
            return
        pb = self.records.personal_best(snap.self_name, snap.boss_name)
        if pb is None:
            self._text(WIDTH - 8, 24, "İlk kayıt", 8, DIM, anchor="ne")
        elif me.dps > pb[0]:
            self._text(WIDTH - 8, 24, f"▲ Yeni rekor! (+%{(me.dps / pb[0] - 1) * 100:.0f})", 8,
                       ACCENT, anchor="ne", bold=True)
        else:
            self._text(WIDTH - 8, 24, f"Rekor {fmt_num(pb[0])}", 8, DIM, anchor="ne")
            self._text(WIDTH - 8, 37, f"%{me.dps / pb[0] * 100:.0f}", 8, DIM, anchor="ne")

    def _draw_graph(self, y: float, timeline: list[int], color: str) -> float:
        """Grafigi cizer, en yuksek (yumusatilmis) saniyelik hasari dondurur."""
        s, w = self.s, WIDTH - 16
        pts = smooth_series(timeline, int(w))
        peak = max(pts) or 1
        step = w / (len(pts) - 1)
        line = []
        for i, v in enumerate(pts):
            line += [(8 + i * step) * s, (y + GRAPH_H - v / peak * GRAPH_H) * s]
        self.canvas.create_polygon(8 * s, (y + GRAPH_H) * s, *line, (8 + w) * s, (y + GRAPH_H) * s,
                                   fill=self.graph_fill, outline="")
        self.canvas.create_line(*line, fill=color if color != UNKNOWN_COLOR else ACCENT,
                                width=max(1, round(1.5 * s)))
        return peak

    def _draw_tabs(self, y: float, snap: Snapshot) -> float:
        tabs: list[tuple[str, object]] = [("Canlı", SCOPE_FIGHT)]
        bosses = [f for f in snap.fights if f.boss_name][-MAX_BOSS_TABS:]
        tabs += [(f.label, f.serial) for f in bosses]
        if isinstance(self.scope, int) and all(key != self.scope for _, key in tabs):
            picked = next((f for f in snap.fights if f.serial == self.scope), None)
            if picked:
                tabs.append((picked.label, picked.serial))
        tabs.append(("Toplam", SCOPE_TOTAL))

        pad, gap = 6, 3
        fixed = sum(self._measure(t, 8) + 2 * pad for t, k in tabs if isinstance(k, str))
        flexible = [t for t, k in tabs if not isinstance(k, str)]
        room = (WIDTH - 8 - fixed - gap * (len(tabs) - 1)) / max(1, len(flexible)) - 2 * pad
        x = 4
        for text, key in tabs:
            label = text if isinstance(key, str) else self._fit(text, max(20, room), 8)
            tw = self._measure(label, 8) + 2 * pad
            selected = key == self.scope
            if selected:
                self._rect(x, y, x + tw, y + TAB_H, fill=self.tab_bg, width=0)
                self._rect(x, y + TAB_H - 2, x + tw, y + TAB_H, fill=ACCENT, width=0)
            self._text(x + tw / 2, y + TAB_H / 2 - 1, label, 8, ACCENT if selected else DIM,
                       anchor="center", bold=selected)
            self._hit(x, y, x + tw, y + TAB_H, lambda k=key: self._set("scope", k))
            x += tw + gap
        return y + TAB_H + 3

    def _draw_rows(self, y: float, snap: Snapshot) -> float:
        # Sadece ben gorunumunde liste yok; buyuk rakam yeterli (alinan hasar haric).
        rows = snap.rows
        if self.view == VIEW_SELF and snap.self_id is not None and self.mode == MODE_DEALT:
            rows = []
        if not rows:
            return y
        taken = self.mode == MODE_TAKEN
        if taken:
            self._text(9, y + COLUMNS_H / 2, "Alınan hasar", 7, ACCENT, anchor="w", bold=True)
        for x, label in ((TOTAL_X, "Toplam"), (DPS_X, "/sn" if taken else "DPS"), (SHARE_X, "%")):
            self._text(x, y + COLUMNS_H / 2, label, 7, DIM, anchor="e")
        y += COLUMNS_H
        s = self.s
        top_total = rows[0].total or 1
        for r in rows:
            color = CLASS_COLORS.get(r.cls, UNKNOWN_COLOR)
            self._rect(4, y + 2, 4 + (WIDTH - 8) * r.total / top_total, y + ROW_H - 2,
                       fill=color, width=0)
            if r.is_self:  # kendi satirimizi kenarlikla belirt
                self._rect(4, y + 2, WIDTH - 4, y + ROW_H - 2, outline=ACCENT, width=max(1, round(s)))
            mid = y + ROW_H / 2
            self._text(9, mid, self._fit(r.name, TOTAL_X - 52, 9, r.is_self), 9, anchor="w",
                       bold=r.is_self)
            self._text(TOTAL_X, mid, fmt_num(r.total), 9, anchor="e")
            self._text(DPS_X, mid, fmt_num(r.dps), 9, anchor="e")
            self._text(SHARE_X, mid, f"{r.share * 100:.0f}%", 9, anchor="e")
            self._hit(0, y, WIDTH, y + ROW_H, lambda sid=r.source: self._open_detail(sid))
            self._row_at.append((y * s, (y + ROW_H) * s, r.source, r.name))
            y += ROW_H
        return y

    def _draw_detail(self, y: float, snap: Snapshot) -> float:
        player, skills = self.meter.skill_breakdown(self.detail, self.scope)
        back = lambda: self._open_detail(None)  # noqa: E731
        self._rect(4, y, WIDTH - 4, y + TAB_H, fill=self.tab_bg, width=0)
        self._hit(0, y, WIDTH, y + TAB_H, back)
        if player is None:
            self._text(8, y + TAB_H / 2, "◀  Bu dövüşte hasarı yok", 8, DIM, anchor="w", bold=True)
            return y + TAB_H + 3
        head = f"◀  {player.name}  ·  {snap.label}"
        self._text(8, y + TAB_H / 2, self._fit(head, WIDTH - 90, 8, True), 8, FG, anchor="w", bold=True)
        self._text(WIDTH - 8, y + TAB_H / 2, f"{fmt_num(player.dps)} DPS", 8, ACCENT, anchor="e",
                   bold=True)
        y += TAB_H + 3
        self._text(9, y + COLUMNS_H / 2, "Skill", 7, DIM, anchor="w")
        for x, label in ((TOTAL_X, "Toplam"), (DPS_X, "Krit"), (SHARE_X, "%")):
            self._text(x, y + COLUMNS_H / 2, label, 7, DIM, anchor="e")
        y += COLUMNS_H
        color = CLASS_COLORS.get(player.cls, UNKNOWN_COLOR)
        top = skills[0].total if skills else 1
        for sk in skills[:MAX_SKILL_ROWS]:
            self._rect(4, y + 2, 4 + (WIDTH - 8) * sk.total / top, y + ROW_H - 2, fill=color, width=0)
            name = sk.name + (" (DoT)" if sk.periodic else "")
            mid = y + ROW_H / 2
            self._text(9, mid, self._fit(name, TOTAL_X - 52, 9), 9, anchor="w")
            self._text(TOTAL_X, mid, fmt_num(sk.total), 9, anchor="e")
            self._text(DPS_X, mid, f"%{sk.crit_rate * 100:.0f}", 9, anchor="e")
            self._text(SHARE_X, mid, f"{sk.share * 100:.0f}%", 9, anchor="e")
            y += ROW_H
        if len(skills) > MAX_SKILL_ROWS:
            rest = sum(sk.total for sk in skills[MAX_SKILL_ROWS:])
            self._text(9, y + 8, f"+{len(skills) - MAX_SKILL_ROWS} skill daha ({fmt_num(rest)})", 7, DIM,
                       anchor="w")
            y += 16
        if skills:
            best = max(skills, key=lambda sk: sk.max_hit)
            self._text(9, y + 8, self._fit(f"En büyük vuruş {fmt_num(best.max_hit)} · {best.name}   •   "
                                           f"{player.hits} vuruş, krit %{player.crit_rate * 100:.0f}",
                                           WIDTH - 16, 7), 7, DIM, anchor="w")
            y += 16
        return y
