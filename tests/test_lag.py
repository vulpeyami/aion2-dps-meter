"""Takilma sensoru: sunucu sessizligi ve paket kaybi ayrimi."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aion2meter.lag import WARMUP_SEGMENTS, LagMonitor
from aion2meter.stream import TcpReassembler

FLOW = ("sunucu", 13328, "ben", 50000)


class ReassemblerLossTests(unittest.TestCase):
    def test_counts_hole_and_retransmit(self):
        r = TcpReassembler()
        r.feed(1000, b"a" * 10)
        r.feed(1020, b"c" * 10)  # 1010 kayboldu: delik
        r.feed(1030, b"d" * 10)  # ayni delik, tekrar sayilmaz
        self.assertEqual(r.holes, 1)
        out = r.feed(1010, b"b" * 10)  # yeniden gonderim deligi kapatir
        self.assertEqual(b"".join(d for d, _ in out), b"b" * 10 + b"c" * 10 + b"d" * 10)
        r.feed(1000, b"a" * 10)  # zaten gelmis veri
        self.assertEqual(r.retransmits, 1)

    def test_in_order_stream_has_no_losses(self):
        r = TcpReassembler()
        for i in range(100):
            r.feed(i * 10, b"x" * 10)
        self.assertEqual((r.holes, r.retransmits), (0, 0))


class LagMonitorTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.m = LagMonitor(self.logs.append)

    def steady(self, start: float, count: int, step: float = 0.06) -> float:
        t = start
        for _ in range(count):
            self.m.feed(FLOW, t)
            t += step
        return t

    def test_normal_traffic_is_quiet(self):
        self.steady(0.0, 1000)
        self.assertEqual(self.m.events, [])

    def test_silent_gap_without_loss(self):
        t = self.steady(0.0, WARMUP_SEGMENTS + 10)
        t = self.steady(t + 1.8, 200)  # 1.8 sn hic paket yok, sonra her sey normal
        [e] = self.m.events
        self.assertAlmostEqual(e.duration, 1.86, places=2)
        self.assertEqual((e.losses, e.acks, e.kind), (0, 0, "hat sessiz"))
        self.assertTrue(e.final)
        self.assertIn("hat sessiz", self.logs[-1])

    def test_acks_during_gap_mean_server_stall(self):
        t = self.steady(0.0, WARMUP_SEGMENTS + 10)
        for _ in range(20):
            self.m.ack(FLOW)  # sunucu baglantida, hareketlerimizi onayliyor
        self.steady(t + 2.0, 100)
        [e] = self.m.events
        self.assertEqual((e.acks, e.kind), (20, "sunucu takıldı"))
        self.assertIn("sunucu takildi", self.logs[-1])

    def test_acks_between_normal_data_do_not_accumulate(self):
        t = 0.0
        for _ in range(WARMUP_SEGMENTS + 10):
            self.m.ack(FLOW)
            self.m.feed(FLOW, t)
            t += 0.06
        self.steady(t + 1.0, 100)
        self.assertEqual(self.m.events[0].acks, 0)

    def test_gap_followed_by_retransmit_is_packet_loss(self):
        t = self.steady(0.0, WARMUP_SEGMENTS + 10)
        t += 1.2
        self.m.feed(FLOW, t, retransmit=True)  # kesintiyi bitiren yeniden gonderim
        self.m.feed(FLOW, t + 0.01, retransmit=True)
        self.steady(t + 0.06, 100)
        [e] = self.m.events
        self.assertEqual(e.losses, 2)
        self.assertEqual(e.kind, "paket kaybı")
        self.assertIn("paket kaybi (2", self.logs[-1])

    def test_new_connection_gaps_are_ignored(self):
        # Yukleme ekrani: baglanti yeni acildi, ilk segmentler arasinda uzun bosluk normal.
        self.m.feed(FLOW, 0.0)
        self.m.feed(FLOW, 5.0)
        self.steady(5.06, 50)
        self.assertEqual(self.m.events, [])

    def test_flows_are_independent(self):
        t = self.steady(0.0, WARMUP_SEGMENTS + 10)
        other = ("baska", 13328, "ben", 50001)
        self.m.feed(other, t + 3.0)  # diger baglantinin ilk paketi takilma sayilmaz
        self.assertEqual(self.m.events, [])

    def test_stats(self):
        t = self.steady(0.0, WARMUP_SEGMENTS + 10)
        self.steady(t + 0.9, 100)
        self.assertEqual(self.m.stats()["takilma"], 1)
        self.assertEqual(self.m.stats()["en_uzun"], 1.0)


class RealDumpTests(unittest.TestCase):
    def test_recorded_dungeon_server_stall(self):
        # 23:54:33'te 4.8 sn oyun verisi gelmedi, sunucu bu arada 88 bos ACK gonderdi.
        from aion2meter.capture import Pipeline, replay_dump
        from aion2meter.protocol import PacketParser

        path = os.path.join(os.path.dirname(__file__), "..", "kayit_2026-10-12_zindan.bin")
        if not os.path.exists(path):
            self.skipTest("kayit yok")
        lag = LagMonitor()
        pipeline = Pipeline(PacketParser(lambda h: None, lambda *a: None), lag=lag)
        replay_dump(path, pipeline)
        self.assertEqual(lag.stats(), {"takilma": 1, "en_uzun": 4.8, "yeniden_gonderim": 0,
                                       "delik": 0})
        [e] = lag.recent()
        self.assertEqual((e.acks, e.kind), (88, "sunucu takıldı"))


if __name__ == "__main__":
    unittest.main()
