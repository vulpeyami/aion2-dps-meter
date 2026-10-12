"""Dovus gecmisi, boss tanima, skill dokumu, alinan hasar, kayitlar ve ozet metni."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aion2meter.gamedata import npc_info, skill_name
from aion2meter.meter import (MODE_TAKEN, SCOPE_FIGHT, SCOPE_TOTAL, VIEW_ALL, VIEW_PARTY,
                              Meter)
from aion2meter.overlay import smooth_series, summary_text
from aion2meter.protocol import Hit, PacketParser, parse_npc_state
from aion2meter.records import Records
from test_pipeline import varint

BOSS = 2090175  # Fediv Wraith (oyun verisinde boss)
MOB = 2000000   # Punching Bag (siradan)
ME, ALLY = 500, 501


def npc_payload(entity: int, code: int, opcode=b"\x41\x36") -> bytes:
    return opcode + varint(entity) + b"\x04\x20\x00" + code.to_bytes(4, "little") + b"\x00" * 30


class GameDataTests(unittest.TestCase):
    def test_skill_variants_resolve_to_base_name(self):
        self.assertEqual(skill_name(15200012), "Cold Storm")
        self.assertEqual(skill_name(15200022), "Cold Storm")
        self.assertIsNone(skill_name(987654321))

    def test_boss_flag(self):
        self.assertEqual(npc_info(BOSS), ("Fediv Wraith", True))
        self.assertFalse(npc_info(MOB)[1])


class NpcPacketTests(unittest.TestCase):
    def test_parse_4136_and_4036(self):
        self.assertEqual(parse_npc_state(npc_payload(70001, BOSS)), (70001, BOSS))
        self.assertEqual(parse_npc_state(npc_payload(70002, MOB, b"\x40\x36")), (70002, MOB))

    def test_rejects_non_npc(self):
        self.assertIsNone(parse_npc_state(b"\x41\x36" + varint(5) + b"\x5f\x00\x00" + b"\x01" * 20))

    def test_parser_reports_npc(self):
        seen = []
        p = PacketParser(lambda h: None, lambda *a: None, on_npc=lambda e, c: seen.append((e, c)))
        payload = npc_payload(70001, BOSS)
        frame = varint(len(payload) + 4 - 1) + payload  # [varint][govde]; uzunluk kurali onemsiz
        p.handle_frame(frame, 1, None)
        self.assertEqual(seen, [(70001, BOSS)])


class FightTests(unittest.TestCase):
    def setUp(self):
        self.t = [0.0]
        self.m = Meter(idle_reset=15, clock=lambda: self.t[0])
        self.m.set_self(ME, "Ben")
        self.m.update_group("party", [ALLY], True)
        self.m.set_npc(9000, MOB)
        self.m.set_npc(9100, BOSS)

    def hit(self, at, src, tgt, amount, skill=15200012, crit=False, periodic=False):
        self.t[0] = at
        self.m.add_hit(Hit(src, tgt, amount, skill, crit, periodic))

    def test_boss_fight_is_named_and_trash_is_numbered(self):
        for at in (0, 5):
            self.hit(at, ME, 9000, 100)
        for at in (60, 70, 80):
            self.hit(at, ME, 9100, 1000)
        snap = self.m.snapshot()
        self.assertEqual([f.label for f in snap.fights], ["Dövüş 1", "Fediv Wraith"])
        self.assertEqual(snap.boss_name, "Fediv Wraith")
        self.assertEqual(snap.fights[1].self_dps, 3000 / 20)

    def test_boss_phase_gap_continues_same_fight(self):
        self.hit(0, ME, 9100, 1000)
        self.hit(10, ME, 9100, 1000)
        self.hit(50, ALLY, 9100, 1000)  # 40 sn dokunulmazlik, ayni boss
        self.assertEqual(len(self.m.snapshot().fights), 1)
        self.hit(200, ME, 9100, 1000)   # cok uzun ara: yeni dovus
        self.assertEqual(len(self.m.snapshot().fights), 2)

    def test_past_fight_tab_and_total(self):
        self.hit(0, ME, 9100, 1000)
        self.hit(10, ME, 9100, 1000)
        self.hit(100, ME, 9000, 50)
        boss = self.m.snapshot().fights[0].serial
        past = self.m.snapshot(scope=boss)
        self.assertEqual(past.self_row.total, 2000)
        self.assertFalse(past.active)
        self.assertEqual(self.m.snapshot(scope=SCOPE_TOTAL).self_row.total, 2050)
        self.assertEqual(self.m.snapshot(scope=SCOPE_FIGHT).self_row.total, 50)
        self.m.reset()
        self.assertEqual(self.m.snapshot(scope=boss).scope, SCOPE_FIGHT)

    def test_skill_breakdown_merges_variants_and_splits_dots(self):
        self.hit(0, ME, 9000, 300, skill=15200012, crit=True)
        self.hit(1, ME, 9000, 100, skill=15200022)
        self.hit(2, ME, 9000, 100, skill=13730007, periodic=True)
        row, skills = self.m.skill_breakdown(ME)
        self.assertEqual(row.total, 500)
        self.assertEqual([(s.name, s.periodic, s.total, s.hits) for s in skills],
                         [("Cold Storm", False, 400, 2), ("Poison", True, 100, 1)])
        self.assertEqual(skills[0].crit_rate, 0.5)
        self.assertEqual(skills[0].max_hit, 300)

    def test_damage_taken_only_lists_players(self):
        self.hit(0, 9000, ME, 400, skill=0)
        self.hit(1, 9000, ALLY, 100, skill=0)
        self.hit(2, ME, 9000, 1000)  # canavarin aldigi hasar listede olmamali
        rows = self.m.snapshot(mode=MODE_TAKEN, view=VIEW_ALL).rows
        self.assertEqual([(r.source, r.total) for r in rows], [(ME, 400), (ALLY, 100)])
        self.assertAlmostEqual(rows[0].share, 0.8)

    def test_monster_never_listed_as_player(self):
        self.hit(0, ME, 9000, 100)
        self.hit(1, 9000, ME, 50, skill=11020000)  # sinif skill kodu gibi gorunse de NPC
        self.hit(2, 9000, ME, 50, skill=11020000)
        self.hit(3, 9000, ME, 50, skill=11020000)
        self.assertEqual([r.source for r in self.m.snapshot(view=VIEW_ALL).rows], [ME])

    def test_timeline_concatenates_fights_for_total(self):
        self.hit(0, ME, 9000, 10)
        self.hit(2, ME, 9000, 20)
        self.hit(100, ME, 9000, 30)
        self.assertEqual(self.m.snapshot().timeline, [30])
        self.assertEqual(self.m.snapshot(scope=SCOPE_TOTAL).timeline, [10, 0, 20, 30])

    def test_export_run(self):
        self.hit(0, ME, 9100, 1000)
        self.hit(20, ME, 9100, 1000)
        run = self.m.export_run()
        self.assertEqual(run["character"], "Ben")
        self.assertEqual(run["fights"][0]["boss"], "Fediv Wraith")
        self.assertEqual(run["fights"][0]["self_dps"], 100)
        json.dumps(run)  # yazilabilir olmali


class RecordTests(unittest.TestCase):
    def run_with(self, dps, duration=60.0, boss="Fediv Wraith"):
        return {"started": "2026-10-12T21:00:00", "character": "Ben", "fights": [
            {"boss": boss, "self_dps": dps, "duration": duration, "players": []}], "total": {}}

    def test_personal_best_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            r = Records(d)
            r.finish(self.run_with(5000))
            self.assertEqual(r.personal_best("Ben", "Fediv Wraith")[0], 5000)
            r.finish(self.run_with(4000) | {"started": "2026-10-13T21:00:00"})
            r.finish(self.run_with(9000, duration=3))  # cok kisa: sayilmaz
            again = Records(d)
            self.assertEqual(again.personal_best("Ben", "Fediv Wraith")[0], 5000)
            self.assertIsNone(again.personal_best("Ben", "Baska Boss"))
            self.assertEqual(len(os.listdir(d)), 3)  # ayni saniyede baslasa da ayri dosya

    def test_current_run_does_not_count_until_finished(self):
        with tempfile.TemporaryDirectory() as d:
            r = Records(d)
            r.save(self.run_with(5000))
            self.assertIsNone(r.personal_best("Ben", "Fediv Wraith"))


class OverlayHelperTests(unittest.TestCase):
    def test_smooth_series_length(self):
        self.assertEqual(len(smooth_series([1] * 50, 200)), 50)
        self.assertEqual(len(smooth_series(list(range(1000)), 200)), 200)
        self.assertEqual(smooth_series([0, 10, 0, 0, 0, 0], 100)[-1], 2.0)

    def test_summary_text(self):
        t = [0.0]
        m = Meter(clock=lambda: t[0])
        m.set_self(ME, "Ben")
        m.set_identity(ALLY, "Dost")
        m.update_group("party", [ALLY], True)
        for t[0] in range(11):
            m.add_hit(Hit(ME, 9000, 300, 15200012, False, False))
            m.add_hit(Hit(ALLY, 9000, 100, 15200012, False, False))
        text = summary_text(m.snapshot(view=VIEW_PARTY), "verilen")
        self.assertEqual(text, "[Hakari] Dövüş 1 00:10 | Ben 330 DPS (75%) · Dost 110 DPS (25%)")


if __name__ == "__main__":
    unittest.main()
