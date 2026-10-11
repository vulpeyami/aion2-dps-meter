"""Gercek oyun kaydiyla regresyon testi.

kayit.bin oyuncu isimleri icerdigi icin repoya konmaz (.gitignore); dosya yoksa test atlanir.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aion2meter.capture import DUMP_MAGIC, Pipeline, replay_dump
from aion2meter.meter import VIEW_ALL, Meter
from aion2meter.protocol import PacketParser, parse_damage
from test_pipeline import damage_payload, frame, tick

DUMP = os.path.join(ROOT, "kayit.bin")


def replay(path, self_name=None):
    pipeline = None
    meter = Meter(idle_reset=1e9, clock=lambda: pipeline.last_ts, self_name_hint=self_name)
    parser = PacketParser(meter.add_hit, meter.set_self, meter.set_identity, meter.update_group)
    pipeline = Pipeline(parser)
    replay_dump(path, pipeline)
    return meter, parser


@unittest.skipUnless(os.path.exists(DUMP), "kayit.bin yok")
class RealDumpTests(unittest.TestCase):
    def test_first_live_capture_2026_10_12(self):
        meter, parser = replay(DUMP, "Vulpecula")
        self.assertEqual(parser.stats["lz4_errors"], 0)
        self.assertEqual(parser.stats["hits"], 247)
        snap = meter.snapshot(view=VIEW_ALL)
        self.assertEqual((snap.self_id, snap.self_name), (2450, "Vulpecula"))
        self.assertEqual(snap.self_row.total, 134_830)
        self.assertEqual(snap.self_row.cls, "sorcerer")
        self.assertAlmostEqual(snap.duration, 20, delta=1)
        classes = {r.source: r.cls for r in snap.rows}
        self.assertEqual(classes.get(2105), "templar")  # isim paketiyle eslesen baska bir oyuncu


class NameHintTests(unittest.TestCase):
    def test_remembered_name_identifies_self(self):
        m = Meter(self_name_hint="Yami")
        m.set_identity(77, "Baska")
        self.assertIsNone(m.self_id)
        m.set_identity(500, "Yami")
        self.assertEqual((m.self_id, m.self_name), (500, "Yami"))

    def test_truncated_dump_is_tolerated(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "k.bin")
            with open(path, "wb") as f:
                f.write(DUMP_MAGIC)
                p = Pipeline(PacketParser(lambda h: None, lambda *a: None), f)
                p.process((b"s", 1, b"c", 2), 1, False, tick() + frame(damage_payload(5, 9, 100)))
                f.write(b"\x00" * 10)  # yarim kalmis son kayit
            meter, parser = replay(path)
            self.assertEqual(parser.stats["hits"], 1)


if __name__ == "__main__":
    unittest.main()
