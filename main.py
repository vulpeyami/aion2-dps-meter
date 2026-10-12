"""Hakari (秤, "terazi") - Aion 2 DPS meter.

Kullanim (exe icin "py main.py" yerine "Hakari.exe"):
  py main.py                  canli, surucusuz ham soket (yonetici gerekir)
  py main.py --mode windivert canli, WinDivert surucusu ile
  py main.py --dump kayit.bin canli + ham trafigi dosyaya kaydet (hata ayiklama icin)
  py main.py --replay kayit.bin   kaydi isleyip ozet yazdir (yonetici gerekmez)
  py main.py --selftest       ham soketin calistigini oyun olmadan dene (yonetici gerekir)
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time

from aion2meter.capture import DUMP_MAGIC, LiveCapture, Pipeline, replay_dump
from aion2meter.lag import LagMonitor
from aion2meter.meter import SCOPE_TOTAL, Meter
from aion2meter.protocol import PacketParser

APP_NAME = "Hakari"
FROZEN = getattr(sys, "frozen", False)  # PyInstaller exe'si olarak mi calisiyoruz
# Ayarlar, kayitlar ve windivert klasoru: exe'nin ya da main.py'nin yaninda.
APP_DIR = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
LOG_PATH = os.path.join(APP_DIR, "hakari.log")
_log_file = None


def say(msg: str) -> None:
    """Konsol varsa konsola, yoksa (pencereli exe) hakari.log'a yazar."""
    global _log_file
    if sys.stdout is not None:
        print(msg, flush=True)
        return
    if _log_file is None:
        _log_file = open(LOG_PATH, "w", encoding="utf-8")
    _log_file.write(msg + "\n")
    _log_file.flush()


def log(msg: str) -> None:
    say(f"{time.strftime('%H:%M:%S')} {msg}")


def notify(title: str, msg: str, error: bool = False) -> None:
    """Konsolsuz exe'de kullaniciya mesaj kutusuyla, konsolda yaziyla bildirir."""
    if sys.stdout is not None:
        print(msg, flush=True)
        if sys.stdin is not None:
            input("Kapatmak icin Enter...")
        return
    from tkinter import Tk, messagebox
    root = Tk()
    root.withdraw()
    (messagebox.showerror if error else messagebox.showinfo)(f"{APP_NAME} 秤 - {title}", msg)
    root.destroy()


def build(meter: Meter, dump_file=None):
    def on_self(player_id: int, name: str) -> None:
        log(f"Karakter bulundu: {name} (id {player_id})")
        meter.set_self(player_id, name)

    def on_group(kind: str, members: list[int], full_list: bool) -> None:
        if full_list:
            log(f"{'Parti' if kind == 'party' else 'Ittifak'} listesi: {len(members)} uye")
        meter.update_group(kind, members, full_list)

    def on_self_entity(entity: int) -> None:
        if meter.set_self_entity(entity):
            log(f"Karakter id'si: {entity} ({meter.self_name or 'isim bilinmiyor'})")

    parser = PacketParser(meter.add_hit, on_self, meter.set_identity, on_group, meter.set_npc,
                          on_self_entity)
    return parser, Pipeline(parser, dump_file, LagMonitor(log))


def run_replay(path: str, self_name: str | None) -> None:
    # Sure hesabi icin kayittaki paket zamanlari kullanilir, bilgisayarin saati degil.
    pipeline = None
    meter = Meter(idle_reset=1e9, clock=lambda: pipeline.last_ts, self_name_hint=self_name)
    parser, pipeline = build(meter)
    n = replay_dump(path, pipeline)
    say(f"{n} segment, istatistik: {parser.stats}")
    say(f"Baglanti: {pipeline.lag.stats()}")
    for e in pipeline.lag.recent(20):
        say(f"  takilma {e.describe()}")
    snap = meter.snapshot(max_rows=20, view="herkes")
    say(f"Sure {snap.duration:.0f} sn, karakter: {snap.self_name} ({snap.self_id}), "
          f"bilinen isim {len(meter.names)}, parti {sorted(meter.groups['party'])}")
    for r in snap.rows:
        say(f"  {'*' if r.is_self else ' '} {r.name:>14} {r.cls or '?':>12}  toplam {r.total:>12,}  "
              f"dps {r.dps:>10,.0f}  krit %{r.crit_rate * 100:.0f}")
    say(f"NPC: {len(meter.npc_of)} tanindi.  Dovusler:")
    for f in snap.fights:
        say(f"  {'*' if f.boss_name else ' '} {f.label:<30} {f.duration:6.0f} sn")
    if snap.self_id is not None:
        me, skills = meter.skill_breakdown(snap.self_id, SCOPE_TOTAL)
        say(f"{me.name} skilleri:")
        for sk in skills[:15]:
            say(f"    {sk.name + (' (DoT)' if sk.periodic else ''):<32} {sk.total:>10,}  "
                f"%{sk.share * 100:4.1f}  {sk.hits:>4} vurus  krit %{sk.crit_rate * 100:.0f}")


def run_selftest() -> tuple[bool, str]:
    """Ham soketin bu bilgisayarda paket alabildigini oyun olmadan dener."""
    import queue
    import socket

    from aion2meter.capture import _RawSocketBackend

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.connect(("1.1.1.1", 53))  # UDP connect paket gondermez, sadece arayuzu secer
    ip = probe.getsockname()[0]
    probe.close()

    backend = _RawSocketBackend(queue.Queue(), log)
    try:
        sock = backend._open(ip)
    except OSError as e:
        return False, f"Ham soket açılamadı: {e}"
    log(f"{ip} dinleniyor, 10 saniye...")
    sock.settimeout(0.5)
    local, count, end = socket.inet_aton(ip), 0, time.monotonic() + 10
    while time.monotonic() < end:
        try:
            pkt = sock.recv(0xFFFF)
        except socket.timeout:
            continue
        if len(pkt) >= 40 and pkt[9] == 6 and pkt[16:20] == local:
            count += 1
    sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
    sock.close()
    if count:
        return True, f"BAŞARILI: {count} gelen TCP paketi görüldü. Ham soket bu bilgisayarda çalışıyor."
    return False, "Hiç gelen TCP paketi görülmedi. Güvenlik duvarı engelliyor olabilir (README)."


def ensure_firewall_rule() -> None:
    """Hakari.exe icin guvenlik duvari kurali yoksa kullaniciya sorup ekler."""
    from tkinter import Tk, messagebox

    from aion2meter import firewall

    program = sys.executable
    if firewall.has_rule(program):
        return
    root = Tk()
    root.withdraw()
    ok = messagebox.askyesno(
        f"{APP_NAME} 秤 - Güvenlik duvarı",
        "Windows Güvenlik Duvarı, oyundan gelen paketlerin Hakari'ye ulaşmasını engelliyor.\n\n"
        "Sadece bu exe için \"gelen trafiğe izin ver\" kuralı eklensin mi?\n"
        "(Hakari hiçbir portu dinlemez; kural başka bir kapı açmaz.)")
    root.destroy()
    if not ok:
        log("Guvenlik duvari kurali eklenmedi; paket gelmeyebilir")
        return
    if firewall.add_rule(program):
        log(f"Guvenlik duvari kurali eklendi: {firewall.rule_name(program)}")
    else:
        notify("Güvenlik duvarı", "Kural eklenemedi. README'deki elle izin verme adımlarına bakın.", True)


def run_live(dump_path: str | None, mode: str) -> None:
    if not ctypes.windll.shell32.IsUserAnAdmin():
        notify("Yönetici izni", "Yönetici olarak çalıştırılmalı (Baslat.bat ya da Hakari.exe).", True)
        sys.exit(1)
    if mode == "raw" and FROZEN:
        ensure_firewall_rule()
    windivert_dir = os.path.join(APP_DIR, "windivert")
    if mode == "windivert" and not os.path.exists(os.path.join(windivert_dir, "WinDivert.dll")):
        notify("WinDivert yok", f"--mode windivert için {windivert_dir} klasörü gerekli.", True)
        sys.exit(1)

    from aion2meter.overlay import Overlay

    dump_file = None
    if dump_path:
        dump_file = open(os.path.join(APP_DIR, dump_path), "wb")
        dump_file.write(DUMP_MAGIC)
        log(f"Ham trafik kaydediliyor: {dump_path}")

    from aion2meter.overlay import load_settings

    settings_path = os.path.join(APP_DIR, "ayarlar.json")
    meter = Meter(self_name_hint=load_settings(settings_path).get("self_name"))
    parser, pipeline = build(meter, dump_file)
    capture = LiveCapture(pipeline, mode, windivert_dir, log)
    log(f"Yontem: {capture.backend.name}")
    capture.start()

    def report() -> None:
        while True:
            time.sleep(15)
            log(f"segment={pipeline.segments} {parser.stats} baglanti={pipeline.lag.stats()} "
                f"durum={capture.status}")

    threading.Thread(target=report, daemon=True).start()

    def quit_all() -> None:
        capture.stop()
        if dump_file:
            dump_file.close()

    from aion2meter.records import Records

    records = Records(os.path.join(APP_DIR, "kayitlar"), log)
    Overlay(meter, lambda: capture.status, quit_all, settings_path, records, log,
            pipeline.lag).run()


def main() -> None:
    ap = argparse.ArgumentParser(description="Hakari (秤) - Aion 2 DPS meter")
    ap.add_argument("--dump", metavar="DOSYA", help="ham trafigi dosyaya kaydet")
    ap.add_argument("--replay", metavar="DOSYA", help="kaydedilmis trafigi isle")
    ap.add_argument("--self", metavar="ISIM", help="--replay icin: karakter adi")
    ap.add_argument("--mode", choices=("raw", "windivert"), default="raw",
                    help="raw: surucusuz ham soket (varsayilan), windivert: WinDivert surucusu")
    ap.add_argument("--selftest", action="store_true",
                    help="ham soketin bu bilgisayarda calistigini oyun olmadan dene")
    args = ap.parse_args()
    if args.replay:
        run_replay(args.replay, args.self)
    elif args.selftest:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            notify("Bağlantı testi", "Yönetici olarak çalıştırılmalı.", True)
            return
        if sys.stdout is None:
            notify("Bağlantı testi", "Tamam'a bastıktan sonra 10 saniye içinde tarayıcıda bir sayfa aç.")
        else:
            print("10 saniye icinde tarayicida bir sayfa ac/yenile...")
        ok, msg = run_selftest()
        notify("Bağlantı testi", msg, not ok)
    else:
        run_live(args.dump, args.mode)


if __name__ == "__main__":
    main()
