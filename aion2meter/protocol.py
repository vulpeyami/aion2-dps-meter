"""AION 2 sunucu paketlerinin cozumu.

Protokol bilgisi Aion2Flow'dan (https://github.com/cloris-chan/Aion2Flow, GPL-3.0)
ogrenildi. Sadece DPS icin gereken paketler cozulur:
  0x0438  dogrudan hasar
  0x0538  periyodik hasar (DoT)
  0x3336  kendi karakterimiz (id + isim)

Cerceve (frame) yapisi:
  [varint uzunluk][2 bayt opcode][govde]
  cerceve_uzunlugu = varint_degeri + varint_bayt_sayisi - 4
Sikistirilmis cerceve:
  [varint][opsiyonel 0xF0..0xFE][FF FF][u32 acik boyut][LZ4 blok]
  Acilan veri yine arka arkaya dizilmis cercevelerden olusur.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import lz4.block

MAX_FRAME_LEN = 1024 * 1024
MAX_DECOMPRESSED_LEN = 4 * 1024 * 1024
MIRROR_WINDOW_SEC = 2.0

NEED, BAD, OK = 0, 1, 2

KNOWN_OPCODES = frozenset({
    (0x29, 0x33),
    (0x00, 0x36), (0x03, 0x36), (0x11, 0x36), (0x15, 0x36), (0x21, 0x36),
    (0x23, 0x36), (0x33, 0x36), (0x40, 0x36), (0x41, 0x36), (0x45, 0x36),
    (0x46, 0x36), (0x49, 0x36), (0x4A, 0x36), (0x4B, 0x36),
    (0x1D, 0x37),
    (0x02, 0x38), (0x03, 0x38), (0x04, 0x38), (0x05, 0x38), (0x06, 0x38),
    (0x22, 0x38), (0x2A, 0x38), (0x2B, 0x38), (0x2C, 0x38), (0x35, 0x38),
    (0x01, 0x40), (0x02, 0x40),
    (0x84, 0x56),
    (0x00, 0x61), (0x01, 0x61),
    (0x00, 0x8D), (0x04, 0x8D), (0x21, 0x8D),
    (0x00, 0x92), (0x0D, 0x92), (0x1B, 0x92), (0x2E, 0x92), (0x2F, 0x92),
    (0x09, 0x94), (0x0B, 0x94),
    (0x02, 0x96), (0x0A, 0x96), (0x1B, 0x96), (0x1D, 0x96), (0x1E, 0x96),
    (0x2B, 0x96),
})

# 0x0538 modlari: 7/8/9/11 durum tohumu, 48 baglanti kaydi (deger alani id tasir).
PERIODIC_NON_DAMAGE_MODES = frozenset({7, 8, 9, 11, 48})


@dataclass(frozen=True, slots=True)
class Hit:
    source: int
    target: int
    amount: int
    skill: int
    crit: bool
    periodic: bool


def _int32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= (1 << 31) else v


def read_varint(buf, off: int):
    """(deger, bayt_sayisi) ya da None."""
    value = 0
    n = len(buf)
    for i in range(5):
        if off + i >= n:
            return None
        b = buf[off + i]
        value |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return _int32(value), i + 1
    return None


def probe_frame(buf, off: int = 0):
    """Cerceve basligini okur: (durum, cerceve_uzunlugu, onek_uzunlugu)."""
    value = 0
    n = len(buf)
    for i in range(5):
        if off + i >= n:
            return NEED, 0, 0
        c = buf[off + i]
        if i == 4 and c & 0xF0:
            return BAD, 0, 0
        value |= (c & 0x7F) << (7 * i)
        if c & 0x80:
            continue
        plen = i + 1
        if plen > 1 and value < (1 << (7 * (plen - 1))):
            return BAD, 0, 0  # minimal olmayan varint
        flen = value + plen - 4
        if flen < plen + 2 or flen > MAX_FRAME_LEN:
            return BAD, 0, 0
        if off + flen > n:
            return NEED, flen, plen
        return OK, flen, plen
    return BAD, 0, 0


def is_recognized_start(buf, off: int, plen: int) -> bool:
    o = off + plen
    if o + 2 > len(buf):
        return False
    a, b = buf[o], buf[o + 1]
    if a == 0xFF and b == 0xFF:
        return True
    if 0xF0 <= a < 0xFF and b == 0xFF and o + 2 < len(buf) and buf[o + 2] == 0xFF:
        return True
    return b == 0x39 or (a, b) in KNOWN_OPCODES


class _Reader:
    __slots__ = ("buf", "off")

    def __init__(self, buf, off: int = 0):
        self.buf = buf
        self.off = off

    @property
    def remaining(self) -> int:
        return len(self.buf) - self.off

    def varint(self):
        r = read_varint(self.buf, self.off)
        if r is None:
            return None
        self.off += r[1]
        return r[0]

    def byte(self):
        if self.off >= len(self.buf):
            return None
        self.off += 1
        return self.buf[self.off - 1]

    def u32(self):
        if self.remaining < 4:
            return None
        v = int.from_bytes(self.buf[self.off:self.off + 4], "little", signed=True)
        self.off += 4
        return v


def parse_damage(p) -> Hit | None:
    """0x0438 govdesi (opcode dahil). Hasar degilse None."""
    if len(p) < 2 or p[0] != 0x04 or p[1] != 0x38:
        return None
    r = _Reader(p, 2)
    target, layout, flag, source = r.varint(), r.varint(), r.varint(), r.varint()
    if None in (target, layout, flag, source):
        return None
    if target <= 0 or source <= 0 or not 0 <= layout <= 255 or not 0 <= flag <= 255:
        return None
    if r.remaining < 5:
        return None
    skill = r.u32()
    marker = r.byte()
    kind = r.varint()
    if marker is None or kind is None or not 0 <= kind <= 255:
        return None

    key = layout & 0x0F
    if not key & 0x04:
        return None
    detail_len = 8 + (4 if key & 0x01 else 0) + (2 if key & 0x02 else 0)
    if r.remaining < detail_len:
        return None
    detail = p[r.off:r.off + detail_len]
    r.off += detail_len

    unknown, damage, loop = r.varint(), r.varint(), r.varint()
    if None in (unknown, damage, loop) or loop < 0:
        return None

    defensive = None
    if key == 0x06 and detail[0] & 0x20 and unknown == 0 and damage == 0 and loop > 0:
        save = r.off
        tail = r.varint()
        if tail is not None and tail > 0:
            defensive = tail
        else:
            r.off = save
    if defensive is not None:
        damage = defensive
    elif unknown == 0 and loop > 0 and key == 0x06:
        damage = loop

    # 8 baytlik detayda 0x57 = can (iyilestirme), 0x58 = mana kaynagi.
    if detail_len == 8 and detail[0] in (0x57, 0x58):
        return None
    if source == target or damage <= 0:
        return None
    return Hit(source, target, damage, max(0, skill), kind == 3, False)


def parse_periodic(p) -> Hit | None:
    """0x0538 govdesi (opcode dahil). Hasar degilse None."""
    if len(p) < 2 or p[0] != 0x05 or p[1] != 0x38:
        return None
    r = _Reader(p, 2)
    target, mode, source = r.varint(), r.varint(), r.varint()
    if None in (target, mode, source) or target <= 0 or source <= 0:
        return None
    if r.varint() is None:
        return None
    if r.u32() is None:
        return None
    damage = r.varint()
    if damage is None:
        return None
    if mode in PERIODIC_NON_DAMAGE_MODES or source == target or damage <= 0:
        return None
    skill = 0
    if r.remaining >= 4:
        skill = max(0, int.from_bytes(p[-4:], "little", signed=True))
    return Hit(source, target, damage, skill, False, True)


def parse_own_nickname(p):
    """0x3336 govdesi -> (oyuncu_id, isim) ya da None."""
    if len(p) < 2 or p[0] != 0x33 or p[1] != 0x36:
        return None
    r = _Reader(p, 2)
    player_id = r.varint()
    if player_id is None or player_id <= 0:
        return None
    prefix = r.byte()
    if prefix == 0x5E:
        if r.byte() is None:
            return None
    elif prefix not in (0x5F, 0xDF):
        return None
    if r.varint() is None or r.byte() not in (0x37, 0x3F):
        return None
    name_len = r.byte()
    if name_len is None or not 1 <= name_len <= 48 or r.remaining < name_len:
        return None
    try:
        name = bytes(p[r.off:r.off + name_len]).decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not name or not all(ch.isalnum() for ch in name):
        return None
    return player_id, name


def _is_embedded_damage_shape(p) -> bool:
    """Bilinmeyen cerceve icinde bulunan 0x0438 icin ek dogrulama."""
    r = _Reader(p, 2)
    target, layout, flag, source = r.varint(), r.varint(), r.varint(), r.varint()
    if None in (target, layout, flag, source) or r.remaining < 6:
        return False
    if r.u32() < 0:
        return False
    r.byte()
    kind = r.varint()
    return (4 <= layout <= 126 and not layout & 1 and layout & 4
            and flag in (0, 4, 16, 20) and kind in (0, 1, 2, 3, 8, 9))


class PacketParser:
    """Cerceveleri cozer, hasar ve kimlik olaylarini geri cagirimlarla bildirir."""

    def __init__(self, on_hit: Callable[[Hit], None],
                 on_self: Callable[[int, str], None],
                 clock: Callable[[], float] = time.monotonic):
        self.on_hit = on_hit
        self.on_self = on_self
        self.clock = clock
        self.stats = {"frames": 0, "compressed": 0, "hits": 0, "mirror_dupes": 0,
                      "lz4_errors": 0}
        self._recent: dict[bytes, tuple[object, float]] = {}

    def handle_frame(self, frame, plen: int, conn, depth: int = 0) -> None:
        self.stats["frames"] += 1
        o = plen
        a, b = frame[o], frame[o + 1]

        if b == 0xFF and (a == 0xFF or (0xF0 <= a < 0xFF and len(frame) > o + 2 and frame[o + 2] == 0xFF)):
            if a != 0xFF:
                o += 1
            self._handle_compressed(frame, o + 2, conn, depth)
            return

        payload = frame[plen:]
        if a == 0x04 and b == 0x38:
            self._emit_hit(parse_damage(payload), frame, conn)
        elif a == 0x05 and b == 0x38:
            self._emit_hit(parse_periodic(payload), frame, conn)
        elif a == 0x33 and b == 0x36:
            me = parse_own_nickname(payload)
            if me:
                self.on_self(*me)
        elif (a, b) not in KNOWN_OPCODES and len(payload) <= 16384:
            self._scan_embedded(payload[2:], conn)

    def walk_batch(self, data, conn, depth: int = 0) -> None:
        """Arka arkaya dizilmis cerceveleri sirayla isler."""
        off, n = 0, len(data)
        while off < n:
            st, flen, plen = probe_frame(data, off)
            if st != OK:
                self._scan_embedded(data[off:], conn)
                return
            self.handle_frame(data[off:off + flen], plen, conn, depth)
            off += flen

    def _handle_compressed(self, frame, o: int, conn, depth: int) -> None:
        if depth > 3 or len(frame) < o + 5:
            return
        size = int.from_bytes(frame[o:o + 4], "little")
        if not 0 < size <= MAX_DECOMPRESSED_LEN:
            return
        try:
            data = lz4.block.decompress(bytes(frame[o + 4:]), uncompressed_size=size)
        except Exception:
            self.stats["lz4_errors"] += 1
            return
        self.stats["compressed"] += 1
        self.walk_batch(data, conn, depth + 1)

    def _scan_embedded(self, body, conn) -> None:
        """Bilinmeyen cercevelerin icine gomulu hasar cercevelerini arar."""
        body = bytes(body)
        for opcode in (b"\x04\x38", b"\x05\x38"):
            pos = body.find(opcode, 1)
            while pos > 0:
                for plen in (1, 2, 3):
                    start = pos - plen
                    if start < 0:
                        break
                    st, flen, got_plen = probe_frame(body, start)
                    if st != OK or got_plen != plen:
                        continue
                    payload = body[pos:start + flen]
                    if opcode == b"\x04\x38":
                        if not _is_embedded_damage_shape(payload):
                            continue
                        hit = parse_damage(payload)
                    else:
                        hit = parse_periodic(payload)
                    if hit:
                        self._emit_hit(hit, body[start:start + flen], conn)
                    break
                pos = body.find(opcode, pos + 2)

    def _emit_hit(self, hit: Hit | None, frame, conn) -> None:
        if hit is None:
            return
        # Ayni cerceve baska bir baglantidan da gelebiliyor (ayna); iki kez sayma.
        now = self.clock()
        key = bytes(frame)
        seen = self._recent.get(key)
        if seen is not None and seen[0] != conn and now - seen[1] < MIRROR_WINDOW_SEC:
            self.stats["mirror_dupes"] += 1
            return
        self._recent[key] = (conn, now)
        if len(self._recent) > 20000:
            self._recent = {k: v for k, v in self._recent.items()
                            if now - v[1] < MIRROR_WINDOW_SEC}
        self.stats["hits"] += 1
        self.on_hit(hit)
