"""Gercek oyun kaydiyla regresyon testi.

Kayitlar oyuncu isimleri icerdigi icin repoya konmaz (.gitignore); dosya yoksa test atlanir.
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

# Kayitlar oyuncu isimleri icerdigi icin repoda yok; dosya yoksa ilgili test atlanir.
DUMP = os.path.join(ROOT, "kayit_2026-10-12_ilk.bin")
DUNGEON = os.path.join(ROOT, "kayit_2026-10-12_zindan.bin")


def replay(path, self_name=None):
    pipeline = None
    meter = Meter(idle_reset=1e9, clock=lambda: pipeline.last_ts, self_name_hint=self_name)
    parser = PacketParser(meter.add_hit, meter.set_self, meter.set_identity, meter.update_group,
                          meter.set_npc, meter.set_self_entity)
    pipeline = Pipeline(parser)
    replay_dump(path, pipeline)
    return meter, parser


@unittest.skipUnless(os.path.exists(DUNGEON), "zindan kaydi yok")
class RealDungeonTests(unittest.TestCase):
    def test_self_state_packet_finds_us_long_before_own_nickname(self):
        # 0x3336 ancak 298. saniyede (isinlanmada) geliyor; 0x4A36 ilk saniyeden kim oldugumuzu
        # soyluyor ve ikisi ayni id'yi veriyor.
        pipeline, first = None, {}
        parser = PacketParser(lambda h: None, lambda i, n: first.setdefault("nick", (pipeline.last_ts, i)),
                              on_self_entity=lambda e: first.setdefault("state", (pipeline.last_ts, e)))
        pipeline = Pipeline(parser)
        replay_dump(DUNGEON, pipeline)
        self.assertEqual(first["state"][1], first["nick"][1])
        self.assertGreater(first["nick"][0] - first["state"][0], 200)

    def test_self_found_with_remembered_name(self):
        meter, parser = replay(DUNGEON, "Vulpecula")
        snap = meter.snapshot(view=VIEW_ALL)
        self.assertEqual((snap.self_id, snap.self_name), (2753, "Vulpecula"))
        self.assertEqual(snap.self_row.cls, "sorcerer")
        names = {r.name for r in snap.rows}
        self.assertTrue({"Vulpecula", "Lausan", "Cenk", "MESSINT0", "Cleri"} <= names, names)

    def test_party_view_lists_the_whole_party(self):
        meter, _ = replay(DUNGEON, "Vulpecula")
        names = [r.name for r in meter.snapshot(view="parti").rows]
        self.assertEqual(sorted(names), ["Cenk", "Cleri", "Lausan", "MESSINT0", "Vulpecula"])

    def test_boss_recognized(self):
        meter, _ = replay(DUNGEON, "Vulpecula")
        self.assertIn("Divine Auldor", [f.label for f in meter.snapshot().fights])


@unittest.skipUnless(os.path.exists(DUMP), "ilk kayit yok")
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
