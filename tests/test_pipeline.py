import os
import random
import sys
import unittest

import lz4.block

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aion2meter.capture import Pipeline, parse_ip_tcp
from aion2meter.meter import SCOPE_TOTAL, Meter
from aion2meter.protocol import PacketParser, parse_damage, parse_own_nickname, parse_periodic


def varint(v: int) -> bytes:
    out = bytearray()
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def frame(payload: bytes) -> bytes:
    """[varint][payload] ve cerceve_uzunlugu = deger + onek - 4."""
    for plen in range(1, 6):
        value = len(payload) + 4
        if len(varint(value)) == plen:
            return varint(value) + payload
    raise AssertionError


def damage_payload(src, tgt, amount, crit=False, skill=11020000, detail0=0x00):
    return (b"\x04\x38" + varint(tgt) + varint(0x04) + varint(0) + varint(src)
            + skill.to_bytes(4, "little") + b"\x01" + varint(3 if crit else 1)
            + bytes([detail0]) + b"\x00" * 7 + varint(1) + varint(amount) + varint(0))


def periodic_payload(src, tgt, amount, mode=2):
    return (b"\x05\x38" + varint(tgt) + varint(mode) + varint(src) + varint(7)
            + (1234).to_bytes(4, "little") + varint(amount) + (11020000).to_bytes(4, "little"))


def tick() -> bytes:
    return frame(b"\x00\x36" + b"\x11" * 8)


def other() -> bytes:
    return frame(b"\x21\x8d" + b"\x22" * 5)


def compressed(inner: bytes) -> bytes:
    body = lz4.block.compress(inner, store_size=False)
    return frame(b"\xff\xff" + len(inner).to_bytes(4, "little") + body)


def nickname_payload(pid: int, name: str) -> bytes:
    n = name.encode()
    return b"\x33\x36" + varint(pid) + b"\x5f" + varint(1) + b"\x37" + bytes([len(n)]) + n + b"\x00" * 8


class Collector:
    def __init__(self):
        self.hits = []
        self.me = None
        self.parser = PacketParser(self.hits.append, lambda i, n: setattr(self, "me", (i, n)))
        self.pipeline = Pipeline(self.parser)

    def send(self, data: bytes, conn=("s", 1, "c", 2), start_seq=1000, chunk=None, shuffle=False):
        segs, seq, i = [], start_seq, 0
        while i < len(data):
            size = chunk or len(data)
            segs.append((seq, data[i:i + size]))
            seq += len(data[i:i + size])
            i += size
        if shuffle:
            first, rest = segs[0], segs[1:]
            random.Random(1).shuffle(rest)
            segs = [first] + rest
        for s, d in segs:
            self.pipeline.process(conn, s, False, d)
        return seq


class ProtocolTests(unittest.TestCase):
    def test_damage(self):
        h = parse_damage(damage_payload(500, 9000, 12345, crit=True))
        self.assertEqual((h.source, h.target, h.amount, h.crit), (500, 9000, 12345, True))

    def test_heal_resource_is_not_damage(self):
        self.assertIsNone(parse_damage(damage_payload(500, 501, 800, detail0=0x57)))

    def test_self_target_is_not_damage(self):
        self.assertIsNone(parse_damage(damage_payload(500, 500, 800)))

    def test_periodic(self):
        h = parse_periodic(periodic_payload(500, 9000, 777))
        self.assertEqual((h.amount, h.periodic), (777, True))
        self.assertIsNone(parse_periodic(periodic_payload(500, 9000, 777, mode=48)))

    def test_nickname(self):
        self.assertEqual(parse_own_nickname(nickname_payload(500, "Yami")), (500, "Yami"))


class StreamTests(unittest.TestCase):
    def stream(self):
        parts = [tick(), other(), frame(nickname_payload(500, "Yami"))]
        for i in range(20):
            parts.append(frame(damage_payload(500, 9000, 100 + i)))
            parts.append(other())
            parts.append(tick())
        parts.append(compressed(frame(damage_payload(501, 9000, 1000)) + tick()
                                + frame(damage_payload(500, 9000, 5000))))
        parts.append(tick())
        return b"".join(parts)

    def expected_total(self):
        return sum(100 + i for i in range(20)) + 5000

    def test_direct_small_segments(self):
        c = Collector()
        c.send(self.stream(), chunk=7)
        self.assertEqual(c.me, (500, "Yami"))
        self.assertEqual(sum(h.amount for h in c.hits if h.source == 500), self.expected_total())
        self.assertEqual(sum(h.amount for h in c.hits if h.source == 501), 1000)

    def test_out_of_order(self):
        c = Collector()
        c.send(self.stream(), chunk=50, shuffle=True)
        self.assertEqual(sum(h.amount for h in c.hits if h.source == 500), self.expected_total())

    def test_midstream_join(self):
        c = Collector()
        data = bytes(range(37)) * 3 + self.stream()
        c.send(data, chunk=200)
        self.assertEqual(sum(h.amount for h in c.hits if h.source == 500), self.expected_total())

    def test_length_prefixed(self):
        data = self.stream()
        out, i = bytearray(), 0
        rnd = random.Random(2)
        while i < len(data):
            n = rnd.randint(20, 120)
            body = data[i:i + n]
            out += len(body).to_bytes(4, "little") + body
            i += n
        c = Collector()
        c.send(bytes(out), chunk=33)
        self.assertEqual(sum(h.amount for h in c.hits if h.source == 500), self.expected_total())

    def test_mirror_connection_not_double_counted(self):
        c = Collector()
        data = self.stream()
        c.send(data, conn=("s", 1, "c", 2), chunk=64)
        c.send(data, conn=("s", 1, "c", 3), chunk=64)
        self.assertEqual(sum(h.amount for h in c.hits if h.source == 500), self.expected_total())

    def test_ip_tcp_parse(self):
        payload = b"hello"
        tcp = (1234).to_bytes(2, "big") + (5555).to_bytes(2, "big") + (42).to_bytes(4, "big") \
            + b"\x00" * 4 + bytes([5 << 4, 0x18]) + b"\x00" * 6
        ip = bytes([0x45, 0]) + (20 + len(tcp) + len(payload)).to_bytes(2, "big") + b"\x00" * 5 \
            + b"\x06" + b"\x00" * 2 + bytes([1, 2, 3, 4, 10, 0, 0, 2])
        key, seq, syn, data = parse_ip_tcp(ip + tcp + payload)
        self.assertEqual((key[1], key[3], seq, syn, data), (1234, 5555, 42, False, payload))


def ip_tcp_packet(src_ip, dst_ip, sport, dport, payload=b"x", seq=1):
    tcp = sport.to_bytes(2, "big") + dport.to_bytes(2, "big") + seq.to_bytes(4, "big") \
        + b"\x00" * 4 + bytes([5 << 4, 0x18]) + b"\x00" * 6
    total = 20 + len(tcp) + len(payload)
    ip = bytes([0x45, 0]) + total.to_bytes(2, "big") + b"\x00" * 5 + b"\x06" + b"\x00" * 2 \
        + bytes(src_ip) + bytes(dst_ip)
    return ip + tcp + payload


class RawSocketFilterTests(unittest.TestCase):
    def test_only_inbound_game_packets_are_queued(self):
        import queue
        import threading
        from aion2meter.capture import _RawSocketBackend

        me, server, other_ip = [192, 168, 1, 5], [20, 0, 0, 1], [192, 168, 1, 9]
        packets = [
            ip_tcp_packet(server, me, 7777, 50000, b"game"),    # oyuna gelen: alinmali
            ip_tcp_packet(server, me, 443, 50001, b"browser"),  # baska port
            ip_tcp_packet(server, other_ip, 7777, 50000),       # baska IP
            ip_tcp_packet(me, server, 50000, 7777, b"out"),     # oyundan giden
        ]

        class FakeSock:
            def recv_into(self, buf):
                if not packets:
                    raise OSError("bitti")
                p = packets.pop(0)
                buf[:len(p)] = p
                return len(p)

            def ioctl(self, *a):
                pass

            def close(self):
                pass

        out = queue.Queue()
        backend = _RawSocketBackend(out, lambda m: None)
        backend.ports = frozenset({50000})
        backend._loop("192.168.1.5", FakeSock(), threading.Event())
        got = [parse_ip_tcp(out.get_nowait()[1])[3] for _ in range(out.qsize())]
        self.assertEqual(got, [b"game"])

    def test_ipv6_reports_error(self):
        import queue
        from aion2meter.capture import _RawSocketBackend
        backend = _RawSocketBackend(queue.Queue(), lambda m: None)
        self.assertIn("IPv6", backend.update({("2001:db8::1", 50000)}))


class ProcessListTests(unittest.TestCase):
    def test_find_pids_sees_own_process_without_handles(self):
        import psutil
        from aion2meter.capture import find_pids
        own = psutil.Process().name().lower()
        self.assertIn(os.getpid(), find_pids({own}))
        self.assertEqual(find_pids({"boyle-bir-program-yok.exe"}), set())


class MeterTests(unittest.TestCase):
    def test_monster_damage_excluded_when_self_known(self):
        t = [0.0]
        m = Meter(clock=lambda: t[0])
        p = PacketParser(m.add_hit, lambda i, n: m.set_self(i, n))
        p.on_self(500, "Yami")
        for i in range(10):
            t[0] = i
            m.add_hit(parse_damage(damage_payload(500, 9000, 1000)))
            m.add_hit(parse_damage(damage_payload(501, 9000, 500)))
            m.add_hit(parse_damage(damage_payload(9000, 500, 300, skill=2000000)))  # canavar bize vuruyor
        snap = m.snapshot(view="herkes")
        self.assertEqual([r.source for r in snap.rows], [500, 501])
        self.assertEqual(snap.self_row.total, 10000)
        self.assertAlmostEqual(snap.self_row.dps, 10000 / 9)

    def test_idle_starts_new_encounter(self):
        t = [0.0]
        m = Meter(idle_reset=15, clock=lambda: t[0])
        m.set_self(500)
        m.add_hit(parse_damage(damage_payload(500, 9000, 1000)))
        t[0] = 30
        m.add_hit(parse_damage(damage_payload(500, 9001, 50)))
        self.assertEqual(m.snapshot().self_row.total, 50)

    def test_total_scope_survives_idle_and_counts_only_fight_time(self):
        t = [0.0]
        m = Meter(idle_reset=15, clock=lambda: t[0])
        m.set_self(500)
        for t[0] in (0, 5, 10):  # 1. boss: 10 sn
            m.add_hit(parse_damage(damage_payload(500, 9000, 1000)))
        for t[0] in (100, 110, 120):  # 90 sn yuruyus, 2. boss: 20 sn
            m.add_hit(parse_damage(damage_payload(500, 9001, 2000)))
        total = m.snapshot(scope=SCOPE_TOTAL)
        self.assertEqual(total.self_row.total, 9000)
        self.assertEqual(total.duration, 30)
        self.assertAlmostEqual(total.self_row.dps, 300)
        self.assertEqual(m.snapshot().self_row.total, 6000)
        m.reset()
        self.assertIsNone(m.snapshot(scope=SCOPE_TOTAL).self_row)


if __name__ == "__main__":
    unittest.main()
