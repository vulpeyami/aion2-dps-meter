import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aion2meter.meter import VIEW_ALL, VIEW_PARTY, VIEW_SELF, Meter
from aion2meter.protocol import (PacketParser, class_from_code, class_from_skill, parse_damage,
                                 parse_group, parse_nickname_048d, parse_nickname_0994,
                                 parse_pc_metadata_4536, probe_frame)
from test_pipeline import Collector, damage_payload, frame, varint

UUID = b"01234567-89ab-cdef-0123-456789abcdef"


def name_bytes(name: str) -> bytes:
    raw = name.encode()
    return bytes([len(raw)]) + raw


def le16(v):
    return v.to_bytes(2, "little")


def le32(v):
    return v.to_bytes(4, "little")


def party_list_row(entity, slot, server, name):
    """Aion2Flow BuildProfileListRow ile ayni yerlesim."""
    row = bytearray(56)
    row[2] = slot
    row[3:7] = le32(entity)
    row[7:9] = le16(server)
    row[11] = 36
    row[12:48] = UUID
    row[54:56] = le16(server)
    return bytes(row) + name_bytes(name)


def direct_party_row(entity, slot, server, name):
    """Aion2Flow BuildPartyIdentityRow ile ayni yerlesim (satir basi 0x03)."""
    row = bytearray(55)
    row[0] = 0x03
    row[1] = slot
    row[2:6] = le32(entity)
    row[6:8] = le16(server)
    row[8:10] = le16(server)
    row[10] = 36
    row[11:47] = UUID
    row[53:55] = le16(server)
    return bytes(row) + name_bytes(name)


class RealPacketTests(unittest.TestCase):
    def test_real_damage_frame_from_aion2flow(self):
        # Aion2Flow testlerindeki gercek oyun cercevesi.
        f = bytes.fromhex("2704388BE00106048377581AAE000D030400026B4A024401000000A29F01AF200100DF06")
        st, flen, plen = probe_frame(f)
        self.assertEqual((flen, plen), (len(f), 1))
        hit = parse_damage(f[plen:])
        self.assertEqual((hit.source, hit.target, hit.amount, hit.crit), (15235, 28683, 4143, True))
        self.assertEqual(class_from_skill(hit.skill), "gladiator")


class ClassTests(unittest.TestCase):
    def test_skill_buckets(self):
        self.assertEqual(class_from_skill(19010047), "brawler")
        self.assertEqual(class_from_skill(13050000), "assassin")
        self.assertIsNone(class_from_skill(4900509))  # canavar / esya

    def test_class_codes(self):
        self.assertEqual(class_from_code(6), "gladiator")
        self.assertEqual(class_from_code(14), "ranger")
        self.assertEqual(class_from_code(46), "brawler")
        self.assertIsNone(class_from_code(40))


class NameParserTests(unittest.TestCase):
    def test_048d_with_server_id(self):
        p = b"\x04\x8d" + b"\x00" * 7 + varint(4242) + le16(1005) + name_bytes("Kırmızı") + b"\x01" * 8
        self.assertEqual(parse_nickname_048d(p), (4242, "Kırmızı"))

    def test_048d_without_server_id(self):
        p = b"\x04\x8d" + b"\x00" * 7 + varint(4242) + name_bytes("아이온") + b"\x00" * 4
        self.assertEqual(parse_nickname_048d(p), (4242, "아이온"))

    def test_4536_name_and_class(self):
        p = b"\x45\x36" + varint(777) + varint(3) + b"\x07" + name_bytes("Okcu") + le32(14) + b"\x01"
        self.assertEqual(parse_pc_metadata_4536(p), (777, "Okcu", "ranger"))

    def test_0b94(self):
        p = b"\x0b\x94" + le32(901) + b"\x02" + le32(901) + b"\x00" * 4 + name_bytes("Rahip")
        self.assertEqual(parse_nickname_0994(p), (901, "Rahip"))

    def test_rejects_garbage_name(self):
        p = b"\x04\x8d" + b"\x00" * 7 + varint(4242) + b"\x03\xff\xfe\x00"
        self.assertIsNone(parse_nickname_048d(p))


class GroupParserTests(unittest.TestCase):
    def test_party_list_skips_malformed_row(self):
        # Aion2Flow testi: 25. baytta bozuk satir basi, gecerli satir 90'da.
        body = bytearray(90)
        body[25] = 0x05
        body += party_list_row(77, 3, 1001, "Later") + b"\x00" * 5
        kind, rows, full = parse_group(b"\x00\x92" + bytes(body))
        self.assertEqual((kind, rows, full), ("party", [(77, "Later")], True))

    def test_party_list_multiple(self):
        body = bytearray(25)
        for i, name in enumerate(["Ali", "Veli", "Ayse"]):
            body += party_list_row(100 + i, i + 1, 1001, name) + b"\x00" * 60
        kind, rows, full = parse_group(b"\x00\x92" + bytes(body))
        self.assertEqual(rows, [(100, "Ali"), (101, "Veli"), (102, "Ayse")])

    def test_direct_party_member(self):
        kind, rows, full = parse_group(b"\x0d\x92" + direct_party_row(42, 2, 1001, "Member"))
        self.assertEqual((kind, rows, full), ("party", [(42, "Member")], False))

    def test_party_status(self):
        body = varint(55) + varint(900) + varint(1000) + b"\x00" * 24 + b"\x01"
        self.assertEqual(parse_group(b"\x1b\x92" + body), ("party", [(55, None)], False))


class MeterViewTests(unittest.TestCase):
    def setUp(self):
        self.t = [0.0]
        self.m = Meter(clock=lambda: self.t[0])
        self.m.set_self(500, "Yami")
        self.m.update_group("party", [500, 501], True)
        self.m.set_identity(502, "Yabanci")
        for i in range(5):
            self.t[0] = i
            self.m.add_hit(parse_damage(damage_payload(500, 9000, 1000, skill=11020000)))
            self.m.add_hit(parse_damage(damage_payload(501, 9000, 800, skill=17010000)))
            self.m.add_hit(parse_damage(damage_payload(502, 9001, 600, skill=14010000)))
            self.m.add_hit(parse_damage(damage_payload(9000, 500, 300, skill=4900509)))  # canavar

    def names(self, view):
        return [r.name for r in self.m.snapshot(view=view).rows]

    def test_party_view(self):
        self.assertEqual(self.names(VIEW_PARTY), ["Yami", "#501"])

    def test_all_view_includes_identified_players_but_not_monster(self):
        self.assertEqual(self.names(VIEW_ALL), ["Yami", "#501", "Yabanci"])

    def test_self_view(self):
        self.assertEqual(self.names(VIEW_SELF), ["Yami"])

    def test_classes_inferred_from_skills(self):
        rows = {r.source: r.cls for r in self.m.snapshot(view=VIEW_ALL).rows}
        self.assertEqual(rows, {500: "gladiator", 501: "cleric", 502: "ranger"})

    def test_party_list_names_reach_meter_through_parser(self):
        c = Collector()
        m = Meter()
        parser = PacketParser(m.add_hit, m.set_self, m.set_identity, m.update_group)
        body = bytes(25) + party_list_row(501, 2, 1001, "Rahibe") + bytes(60)
        parser.handle_frame(frame(b"\x00\x92" + body), 2 if len(body) + 6 > 127 else 1, "c")
        self.assertEqual(m.names.get(501), "Rahibe")
        self.assertEqual(m.groups["party"], {501})


if __name__ == "__main__":
    unittest.main()
