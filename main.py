"""Aion2 DPS meter.

Kullanim:
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
from aion2meter.meter import Meter
from aion2meter.protocol import PacketParser

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def build(meter: Meter, dump_file=None):
    def on_self(player_id: int, name: str) -> None:
        log(f"Karakter bulundu: {name} (id {player_id})")
        meter.set_self(player_id, name)

    def on_group(kind: str, members: list[int], full_list: bool) -> None:
        if full_list:
            log(f"{'Parti' if kind == 'party' else 'Ittifak'} listesi: {len(members)} uye")
        meter.update_group(kind, members, full_list)

    parser = PacketParser(meter.add_hit, on_self, meter.set_identity, on_group)
    return parser, Pipeline(parser, dump_file)


def run_replay(path: str) -> None:
    meter = Meter(idle_reset=1e9)
    parser, pipeline = build(meter)
    n = replay_dump(path, pipeline)
    print(f"{n} segment, istatistik: {parser.stats}")
    snap = meter.snapshot(max_rows=20, view="herkes")
    print(f"Sure {snap.duration:.0f} sn, karakter: {snap.self_name} ({snap.self_id}), "
          f"bilinen isim {len(meter.names)}, parti {sorted(meter.groups['party'])}")
    for r in snap.rows:
        print(f"  {'*' if r.is_self else ' '} {r.name:>14} {r.cls or '?':>12}  toplam {r.total:>12,}  "
              f"dps {r.dps:>10,.0f}  krit %{r.crit_rate * 100:.0f}")


def run_selftest() -> None:
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
        print(f"Ham soket acilamadi: {e}")
        return
    print(f"{ip} dinleniyor. 10 saniye boyunca tarayicida bir sayfa ac/yenile...")
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
        print(f"BASARILI: {count} gelen TCP paketi goruldu. Ham soket bu bilgisayarda calisiyor.")
    else:
        print("Hic gelen TCP paketi gorulmedi. Guvenlik duvari engelliyor olabilir (README).")


def run_live(dump_path: str | None, mode: str) -> None:
    if not ctypes.windll.shell32.IsUserAnAdmin():
        print("Yonetici olarak calistirilmali (Baslat.bat kullanin).")
        input("Kapatmak icin Enter...")
        sys.exit(1)

    from aion2meter.overlay import Overlay

    dump_file = None
    if dump_path:
        dump_file = open(dump_path, "wb")
        dump_file.write(DUMP_MAGIC)
        log(f"Ham trafik kaydediliyor: {dump_path}")

    meter = Meter()
    parser, pipeline = build(meter, dump_file)
    capture = LiveCapture(pipeline, mode, os.path.join(HERE, "windivert"), log)
    log(f"Yontem: {capture.backend.name}")
    capture.start()

    def report() -> None:
        while True:
            time.sleep(15)
            log(f"segment={pipeline.segments} {parser.stats} durum={capture.status}")

    threading.Thread(target=report, daemon=True).start()

    def quit_all() -> None:
        capture.stop()
        if dump_file:
            dump_file.close()

    Overlay(meter, lambda: capture.status, quit_all, os.path.join(HERE, "ayarlar.json")).run()


def main() -> None:
    ap = argparse.ArgumentParser(description="Aion2 DPS meter")
    ap.add_argument("--dump", metavar="DOSYA", help="ham trafigi dosyaya kaydet")
    ap.add_argument("--replay", metavar="DOSYA", help="kaydedilmis trafigi isle")
    ap.add_argument("--mode", choices=("raw", "windivert"), default="raw",
                    help="raw: surucusuz ham soket (varsayilan), windivert: WinDivert surucusu")
    ap.add_argument("--selftest", action="store_true",
                    help="ham soketin bu bilgisayarda calistigini oyun olmadan dene")
    args = ap.parse_args()
    if args.replay:
        run_replay(args.replay)
    elif args.selftest:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            print("Yonetici olarak calistirilmali (Baslat.bat --selftest).")
        else:
            run_selftest()
        input("Kapatmak icin Enter...")
    else:
        run_live(args.dump, args.mode)


if __name__ == "__main__":
    main()
