"""AION 2 sunucu paketlerinin cozumu.

Protokol bilgisi Aion2Flow'dan (https://github.com/cloris-chan/Aion2Flow, GPL-3.0)
ogrenildi. Sadece DPS icin gereken paketler cozulur:
  0x0438  dogrudan hasar
  0x0538  periyodik hasar (DoT)
  0x3336  kendi karakterimiz (id + isim)
  0x4036/0x4136  NPC dogusu/durumu (varlik id -> NPC kodu; boss adi icin)
  0x4A36/0x4137/0x4237  sadece kendi karakterimiz hakkinda gelen durum paketleri: ilk varint
          bizim varlik id'miz. 0x3336 her bolge degisiminde gelmedigi icin kim oldugumuzu
          bunlardan anlariz (iki gercek kayitta da %100 tutarli; Aion2Flow'da yok).

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

from .gamedata import is_npc_code

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


NPC_OPCODES = frozenset({(0x40, 0x36), (0x41, 0x36)})
SELF_STATE_OPCODES = frozenset({(0x4A, 0x36), (0x41, 0x37), (0x42, 0x37)})


def parse_self_state(p) -> int | None:
    """Kendi karakterimize ait durum paketinden varlik id'si."""
    if len(p) < 4 or (p[0], p[1]) not in SELF_STATE_OPCODES:
        return None
    entity = _Reader(p, 2).varint()
    return entity if entity and entity > 0 else None


def parse_npc_state(p):
    """0x4036/0x4136 govdesi -> (varlik_id, npc_kodu) ya da None.

    Gercek kayitta kod, varlik id'sinden sonraki 3 mod baytinin hemen arkasinda;
    Aion2Flow bazi duzenlerde 1-2 bayt ileride okuyor, onlari da dener.
    """
    if len(p) < 2 or (p[0], p[1]) not in NPC_OPCODES:
        return None
    r = _Reader(p, 2)
    entity = r.varint()
    if entity is None or entity <= 0:
        return None
    for off in (3, 4, 5):
        start = r.off + off
        if start + 4 > len(p):
            break
        code = int.from_bytes(p[start:start + 4], "little")
        if is_npc_code(code):
            return entity, code
    return None


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
    name = read_name(p, r.off)
    return (player_id, name[0]) if name else None


# --- Sinif bilgisi ------------------------------------------------------------

CLASSES = ("gladiator", "templar", "assassin", "ranger", "sorcerer",
           "elementalist", "cleric", "chanter", "brawler")

# Oyuncu skill kodlarinin milyonlar basamagi sinifi verir (11410008 -> 11 -> gladiator).
_SKILL_CLASS = {11: "gladiator", 12: "templar", 13: "assassin", 14: "ranger", 15: "sorcerer",
                16: "elementalist", 17: "cleric", 18: "chanter", 19: "brawler"}

# 0x4536 icindeki sinif kodu araliklari.
_CODE_CLASS = ((5, 8, "gladiator"), (9, 12, "templar"), (13, 16, "ranger"), (17, 20, "assassin"),
               (21, 24, "elementalist"), (25, 28, "sorcerer"), (29, 32, "cleric"),
               (33, 36, "chanter"), (45, 48, "brawler"))


def class_from_skill(skill: int) -> str | None:
    return _SKILL_CLASS.get(skill // 1_000_000)


def class_from_code(code: int) -> str | None:
    for low, high, name in _CODE_CLASS:
        if low <= code <= high:
            return name
    return None


# --- Isimler -----------------------------------------------------------------

MAX_NAME_BYTES = 72


def read_name(p, off: int):
    """[1 bayt uzunluk][UTF-8 isim] -> (isim, bitis_ofseti) ya da None."""
    if off >= len(p):
        return None
    n = p[off]
    if not 1 <= n <= MAX_NAME_BYTES or off + 1 + n > len(p):
        return None
    try:
        name = bytes(p[off + 1:off + 1 + n]).decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(name) < 2 or not all(ch.isalnum() for ch in name):
        return None
    return name, off + 1 + n


def _u16(p, off: int) -> int:
    return p[off] | (p[off + 1] << 8)


def _i32(p, off: int) -> int:
    return int.from_bytes(p[off:off + 4], "little", signed=True)


def _is_server_id(v: int) -> bool:
    return 1000 <= v <= 2999


def parse_nickname_048d(p):
    """0x048D: gorus alanindaki oyuncu -> (id, isim)."""
    if len(p) < 11 or p[0] != 0x04 or p[1] != 0x8D:
        return None
    r = read_varint(p, 9)
    if r is None or r[0] <= 0:
        return None
    pid, off = r[0], 9 + r[1]
    if off + 2 <= len(p) and _is_server_id(_u16(p, off)):
        name = read_name(p, off + 2)
        if name:
            return pid, name[0]
    name = read_name(p, off)
    return (pid, name[0]) if name else None


def parse_pc_metadata_4536(p):
    """0x4536: oyuncu bilgisi -> (id, isim, sinif) ya da None."""
    if len(p) < 4 or p[0] != 0x45 or p[1] != 0x36:
        return None
    r = _Reader(p, 2)
    entity = r.varint()
    if entity is None or entity <= 0:
        return None
    while r.remaining > 0:
        if p[r.off] in (0x07, 0x17):
            name = read_name(p, r.off + 1)
            if name and name[1] + 4 <= len(p):
                cls = class_from_code(_i32(p, name[1]))
                if cls:
                    return entity, name[0], cls
        if r.varint() is None:
            return None
    return None


def parse_nickname_0994(p):
    """0x0994 / 0x0B94 -> (id, isim)."""
    if len(p) < 4 or p[1] != 0x94:
        return None
    if p[0] == 0x0B:
        if len(p) < 2 + 17 or p[6] > 0x10:
            return None
        pid = _i32(p, 2)
        if pid <= 0 or _i32(p, 7) != pid:
            return None
        name = read_name(p, 15)
        return (pid, name[0]) if name else None
    if p[0] != 0x09:
        return None
    for kind in range(6, min(len(p) - 1, 18)):
        if p[kind] > 0x10:
            continue
        pid = _i32(p, kind - 4)
        if pid <= 0:
            continue
        r = read_varint(p, kind + 1)
        if r is None or r[0] != pid:
            continue
        name = read_name(p, kind + 1 + r[1])
        if name:
            return pid, name[0]
    return None


def _is_uuid(b) -> bool:
    if len(b) != 36:
        return False
    for i, c in enumerate(b):
        if i in (8, 13, 18, 23):
            if c != 0x2D:
                return False
        elif not (0x30 <= c <= 0x39 or 0x41 <= c <= 0x46 or 0x61 <= c <= 0x66):
            return False
    return True


def _party_list_row(b, o):
    """0x0092 parti listesi satiri -> (id, isim)."""
    if o + 57 > len(b) or b[o] > 5 or b[o + 1] > 7 or not 1 <= b[o + 2] <= 6:
        return None
    entity, server = _i32(b, o + 3), _u16(b, o + 7)
    if (entity <= 0 or not _is_server_id(server) or b[o + 11] != 36
            or not _is_uuid(b[o + 12:o + 48]) or _u16(b, o + 54) != server):
        return None
    name = read_name(b, o + 56)
    return (entity, name[0]) if name else None


def _direct_party_row(b, o):
    """0x0D92 / 0x1E96 dogrudan parti uyesi satiri -> (id, isim|None)."""
    if o + 11 > len(b) or b[o] != 0x03 or not 1 <= b[o + 1] <= 5:
        return None
    entity, server = _i32(b, o + 2), _u16(b, o + 6)
    if entity <= 0 or not _is_server_id(server) or b[o + 10] != 36:
        return None
    if o + 55 > len(b) or _u16(b, o + 53) != server:
        return None
    name = read_name(b, o + 55)
    return entity, name[0] if name else None


def _force_member_row(b, o):
    """0x1E96 / 0x1D96 ittifak uyesi satiri -> (id, isim|None)."""
    if (o + 25 > len(b) or not 1 <= b[o + 8] <= 4 or b[o + 9] != 0x05
            or b[o + 10] or b[o + 11] or b[o + 12] or b[o + 13] not in (1, 2)
            or b[o + 14] != 0x03 or not 1 <= b[o + 15] <= 6):
        return None
    entity, server = _i32(b, o + 16), _u16(b, o + 20)
    if entity <= 0 or not _is_server_id(server) or _u16(b, o + 22) != server or b[o + 24] != 36:
        return None
    name = read_name(b, o + 25 + 36 + 8)
    return entity, name[0] if name else None


def _compact_force_row(b, o):
    """0x1B96 / 0x0296 kompakt ittifak satiri -> (id, isim)."""
    if o + 56 > len(b) or b[o] > 4 or not 1 <= b[o + 1] <= 6:
        return None
    entity, server = _i32(b, o + 2), _u16(b, o + 6)
    if (entity <= 0 or not _is_server_id(server) or b[o + 10] != 36
            or not _is_uuid(b[o + 11:o + 47]) or _u16(b, o + 53) != server):
        return None
    name = read_name(b, o + 55)
    return (entity, name[0]) if name else None


def _group_status(b):
    """0x1B92 / 0x2B96 uye durum (can) guncellemesi -> id."""
    r = _Reader(b)
    entity, cur, top = r.varint(), r.varint(), r.varint()
    if None in (entity, cur, top) or entity <= 0 or top <= 0 or not 0 <= cur <= top:
        return None
    if r.remaining != 25 or b[-1] > 1:
        return None
    return entity


def _scan_rows(b, row_fn, start: int = 0, limit: int = 32):
    found = []
    o = start
    while o < len(b) and len(found) < limit:
        row = row_fn(b, o)
        if row:
            found.append(row)
            o += 56
        else:
            o += 1
    return found


def parse_group(p):
    """Parti/ittifak paketleri -> (tur, [(id, isim|None)], tam_liste_mi) ya da None."""
    op = (p[0], p[1])
    b = p[2:]
    if op == (0x00, 0x92):
        start = 34 if len(b) > 34 and b[0] == 0x01 else 25
        rows = _scan_rows(b, _party_list_row, start, 6)
        return ("party", rows, True) if rows else None
    if op == (0x0D, 0x92):
        row = _direct_party_row(b, 0)
        return ("party", [row], False) if row else None
    if op in ((0x1B, 0x92), (0x2B, 0x96)):
        entity = _group_status(b)
        kind = "party" if op[1] == 0x92 else "force"
        return (kind, [(entity, None)], False) if entity else None
    if op == (0x1E, 0x96):
        rows = _scan_rows(b, lambda bb, o: _force_member_row(bb, o) or _direct_party_row(bb, o), 1)
        return ("force", rows, True) if rows else None
    if op == (0x1D, 0x96):
        row = _force_member_row(b, 0)
        return ("force", [row], False) if row else None
    if op == (0x1B, 0x96):
        row = _compact_force_row(b, 0)
        return ("force", [row], False) if row else None
    if op == (0x02, 0x96):
        rows = _scan_rows(b, _compact_force_row, 22)
        return ("force", rows, True) if rows else None
    return None


GROUP_OPCODES = frozenset({(0x00, 0x92), (0x0D, 0x92), (0x1B, 0x92), (0x2B, 0x96),
                           (0x1E, 0x96), (0x1D, 0x96), (0x1B, 0x96), (0x02, 0x96)})


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
                 on_identity: Callable[[int, str | None, str | None], None] | None = None,
                 on_group: Callable[[str, list, bool], None] | None = None,
                 on_npc: Callable[[int, int], None] | None = None,
                 on_self_entity: Callable[[int], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.on_hit = on_hit
        self.on_self = on_self
        self.on_identity = on_identity or (lambda *a: None)
        self.on_group = on_group or (lambda *a: None)
        self.on_npc = on_npc or (lambda *a: None)
        self.on_self_entity = on_self_entity or (lambda *a: None)
        self.clock = clock
        self.stats = {"frames": 0, "compressed": 0, "hits": 0, "mirror_dupes": 0,
                      "lz4_errors": 0, "names": 0, "group": 0, "npcs": 0}
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
        elif a == 0x45 and b == 0x36:
            self._emit_identity(parse_pc_metadata_4536(payload))
        elif a == 0x04 and b == 0x8D:
            self._emit_identity(parse_nickname_048d(payload))
        elif b == 0x94 and a in (0x09, 0x0B):
            self._emit_identity(parse_nickname_0994(payload))
        elif (a, b) in NPC_OPCODES:
            npc = parse_npc_state(payload)
            if npc:
                self.stats["npcs"] += 1
                self.on_npc(*npc)
        elif (a, b) in SELF_STATE_OPCODES:
            entity = parse_self_state(payload)
            if entity:
                self.on_self_entity(entity)
        elif (a, b) in GROUP_OPCODES:
            group = parse_group(payload)
            if group:
                self.stats["group"] += 1
                kind, rows, full = group
                for entity, name in rows:
                    if name:
                        self._emit_identity((entity, name))
                self.on_group(kind, [entity for entity, _ in rows], full)
        elif (a, b) not in KNOWN_OPCODES and len(payload) <= 16384:
            self._scan_embedded(payload[2:], conn)
            self._scan_embedded_identity(payload[2:])

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

    def _scan_embedded_identity(self, body) -> None:
        """Bilinmeyen cercevelerin icindeki isim kayitlari (uzunluk oneki olmadan gelir)."""
        body = bytes(body)
        for opcode, parse in ((b"\x45\x36", parse_pc_metadata_4536),
                              (b"\x04\x8d", parse_nickname_048d),
                              (b"\x33\x36", parse_own_nickname)):
            pos = body.find(opcode)
            while pos >= 0:
                found = parse(body[pos:])
                if found and opcode == b"\x33\x36":
                    self.on_self(*found)
                elif found:
                    self._emit_identity(found)
                pos = body.find(opcode, pos + 2)

    def _emit_identity(self, found) -> None:
        if not found:
            return
        self.stats["names"] += 1
        entity, name = found[0], found[1]
        self.on_identity(entity, name, found[2] if len(found) > 2 else None)

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
