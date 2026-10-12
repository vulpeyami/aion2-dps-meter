"""Oyun verisi: skill adlari, NPC adlari ve hangi NPC'nin boss oldugu.

data/oyun_verisi.json.gz, Aion2Flow'un kaynak paketlerinden tools/extract_resources.py ile
uretilir (https://github.com/cloris-chan/Aion2Flow, GPL-3.0). Adlar Ingilizce istemciden.
"""

from __future__ import annotations

import gzip
import json
import os
from functools import lru_cache

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "oyun_verisi.json.gz")
NPC_CODE_MIN, NPC_CODE_MAX = 2_000_000, 2_999_999
NPC_KIND_BOSS = 2


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        with gzip.open(DATA_PATH, "rt", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {"skills": {}, "skill_base": {}, "npcs": {}}
    return {
        "skills": {int(k): v for k, v in raw["skills"].items()},
        "skill_base": {int(k): v for k, v in raw["skill_base"].items()},
        "npcs": {int(k): (v[0], v[1]) for k, v in raw["npcs"].items()},
    }


def skill_name(code: int) -> str | None:
    """Skill kodu -> ad. Varyant kodlari (seviye/sarj) temel skill'e indirgenir."""
    d = _data()
    names = d["skills"]
    for c in (code, d["skill_base"].get(code), code // 10000 * 10000):
        if c and c in names:
            return names[c]
    return None


def is_npc_code(value: int) -> bool:
    return NPC_CODE_MIN <= value <= NPC_CODE_MAX


def npc_info(code: int) -> tuple[str, bool] | None:
    """NPC kodu -> (ad, boss mu). Tabloda yoksa None."""
    entry = _data()["npcs"].get(code)
    if entry is None:
        return None
    name, kind = entry
    return name, kind == NPC_KIND_BOSS
