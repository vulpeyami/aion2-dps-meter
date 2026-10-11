"""Aion2 DPS meter.

Kullanim:
  py main.py                  canli (yonetici gerekir)
  py main.py --dump kayit.bin canli + ham trafigi dosyaya kaydet (hata ayiklama icin)
  py main.py --replay kayit.bin   kaydi isleyip ozet yazdir (yonetici gerekmez)
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

    parser = PacketParser(meter.add_hit, on_self)
    return parser, Pipeline(parser, dump_file)


def run_replay(path: str) -> None:
    meter = Meter(idle_reset=1e9)
    parser, pipeline = build(meter)
    n = replay_dump(path, pipeline)
    print(f"{n} segment, istatistik: {parser.stats}")
    snap = meter.snapshot(max_rows=20)
    print(f"Sure {snap.duration:.0f} sn, karakter: {snap.self_name} ({snap.self_id})")
    for r in snap.rows:
        print(f"  {'*' if r.is_self else ' '} {r.name:>14}  toplam {r.total:>12,}  dps {r.dps:>10,.0f}  "
              f"krit %{r.crit_rate * 100:.0f}")


def run_live(dump_path: str | None) -> None:
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
    capture = LiveCapture(pipeline, os.path.join(HERE, "windivert"), log)
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

    Overlay(meter, lambda: capture.status, quit_all).run()


def main() -> None:
    ap = argparse.ArgumentParser(description="Aion2 DPS meter")
    ap.add_argument("--dump", metavar="DOSYA", help="ham trafigi dosyaya kaydet")
    ap.add_argument("--replay", metavar="DOSYA", help="kaydedilmis trafigi isle")
    args = ap.parse_args()
    if args.replay:
        run_replay(args.replay)
    else:
        run_live(args.dump)


if __name__ == "__main__":
    main()
