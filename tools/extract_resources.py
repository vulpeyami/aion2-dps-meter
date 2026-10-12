"""Aion2Flow'un kaynak paketlerinden skill ve NPC adlarini cikarir.

Kaynak: https://github.com/cloris-chan/Aion2Flow (GPL-3.0), src/Aion2Flow.Resources/Packs.
Bicim ResourcePackReader.cs'den: LZ4 sikistirilmis bolumler, metinler 0x07 ile XOR'lu.
Cikti: aion2meter/data/oyun_verisi.json.gz
  skills: {kod: ad}, skill_base: {kod: temel_kod}, npcs: {kod: [ad, tur]} (tur 2 = boss)

Kullanim: py tools/extract_resources.py PAKET_KLASORU   (shared.bin ve en-US.bin icermeli)
"""

from __future__ import annotations

import gzip
import json
import os
import struct
import sys

import lz4.block

PACK_MAGIC, PAYLOAD_MAGIC, VERSION = 0x50523241, 0x44523241, 14
STRING_MASK = 0x07
SKILL_BASE, NPC_DEFS, SKILL_NAMES, NPC_NAMES = 2, 8, 101, 102


def fnv1a64(data: bytes) -> int:
    h = 14695981039346656037
    for b in data:
        h = ((h ^ b) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def load_pack(path: str) -> dict[int, tuple[int, bytes]]:
    with open(path, "rb") as f:
        raw = f.read()
    magic, version, _kind, _, size, checksum, clen = struct.unpack_from("<IHBBiQi", raw)
    if magic != PACK_MAGIC or version != VERSION:
        raise ValueError(f"{path}: beklenmeyen paket basligi")
    payload = lz4.block.decompress(raw[24:24 + clen], uncompressed_size=size)
    if fnv1a64(payload) != checksum:
        raise ValueError(f"{path}: saglama toplami tutmuyor")
    magic, version, count = struct.unpack_from("<IHi", payload)
    if magic != PAYLOAD_MAGIC or version != VERSION:
        raise ValueError(f"{path}: beklenmeyen veri basligi")
    off, headers = 10, []
    for _ in range(count):
        headers.append(struct.unpack_from("<HiiQ", payload, off))
        off += 18
    sections = {}
    for sid, n, length, _sum in headers:
        sections[sid] = (n, payload[off:off + length])
        off += length
    return sections


def read_string(buf: bytes, off: int) -> tuple[str, int]:
    n = struct.unpack_from("<i", buf, off)[0]
    off += 4
    text = bytes(b ^ STRING_MASK for b in buf[off:off + n]).decode("utf-8")
    return text, off + n


def read_int_strings(section) -> dict[int, str]:
    n, buf = section
    out, off = {}, 0
    for _ in range(n):
        key = struct.unpack_from("<i", buf, off)[0]
        out[key], off = read_string(buf, off + 4)
    return out


def main(pack_dir: str) -> None:
    shared = load_pack(os.path.join(pack_dir, "shared.bin"))
    locale = load_pack(os.path.join(pack_dir, "en-US.bin"))

    n, buf = shared[SKILL_BASE]
    skill_base = {}
    for i in range(n):
        code, base = struct.unpack_from("<ii", buf, i * 8)
        if base > 0 and base != code:
            skill_base[code] = base

    n, buf = shared[NPC_DEFS]
    npc_kind = {}
    for i in range(n):
        code, kind, _scale = struct.unpack_from("<iBB", buf, i * 6)
        npc_kind[code] = kind

    skills = {k: v for k, v in read_int_strings(locale[SKILL_NAMES]).items() if v}
    npc_names = read_int_strings(locale[NPC_NAMES])
    npcs = {code: [npc_names.get(code, ""), kind] for code, kind in npc_kind.items()
            if npc_names.get(code) or kind == 2}

    out = os.path.join(os.path.dirname(__file__), "..", "aion2meter", "data", "oyun_verisi.json.gz")
    data = {"skills": skills, "skill_base": skill_base, "npcs": npcs}
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    bosses = sum(1 for _, k in npcs.values() if k == 2)
    print(f"{len(skills)} skill adi, {len(skill_base)} temel skill eslemesi, "
          f"{len(npcs)} NPC ({bosses} boss) -> {os.path.normpath(out)} ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
