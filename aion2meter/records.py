"""Zindan kayitlari (kayitlar/*.json) ve boss basina kisisel rekor.

Her zindan (Sifirla'ya kadar gecen sure) tek bir dosyaya yazilir; dovus bittikce ayni dosya
guncellenir, boylece oyun kapansa da kayit kaybolmaz. Rekorlar onceki zindanlardan hesaplanir:
devam eden zindan kendi rekorunu kirmaya calistigi icin karsilastirmaya katilmaz.
"""

from __future__ import annotations

import glob
import json
import os
import time

MIN_RECORD_SEC = 10.0  # cok kisa (yarida kalan) dovusler rekor sayilmaz


class Records:
    def __init__(self, folder: str, log=lambda msg: None):
        self.folder = folder
        self.log = log
        self.current_path: str | None = None
        # (karakter, boss adi) -> (dps, tarih)
        self.best: dict[tuple[str, str], tuple[float, str]] = {}
        for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
            self._learn(self._read(path))

    @staticmethod
    def _read(path: str) -> dict | None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _learn(self, run: dict | None) -> None:
        if not run or not run.get("character"):
            return
        char, date = run["character"], str(run.get("started", ""))[:10]
        for fight in run.get("fights", []):
            boss, dps = fight.get("boss"), fight.get("self_dps")
            if not boss or not dps or fight.get("duration", 0) < MIN_RECORD_SEC:
                continue
            key = (char, boss)
            if key not in self.best or dps > self.best[key][0]:
                self.best[key] = (float(dps), date)

    def personal_best(self, character: str | None, boss: str | None) -> tuple[float, str] | None:
        if not character or not boss:
            return None
        return self.best.get((character, boss))

    def save(self, run: dict | None) -> None:
        """Devam eden zindani yazar (ayni dosyanin uzerine)."""
        if not run:
            return
        if self.current_path is None:
            os.makedirs(self.folder, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d_%H%M%S", time.strptime(run["started"], "%Y-%m-%dT%H:%M:%S"))
            path, n = os.path.join(self.folder, f"{stamp}.json"), 1
            while os.path.exists(path):  # ayni saniyede baslayan onceki kaydi ezme
                n += 1
                path = os.path.join(self.folder, f"{stamp}_{n}.json")
            self.current_path = path
        tmp = self.current_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(run, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.current_path)
        except OSError as e:
            self.log(f"Kayit yazilamadi: {e}")

    def finish(self, run: dict | None) -> None:
        """Zindan bitti (Sifirla/cikis): son hali yaz, rekorlara kat, yeni dosyaya gec."""
        self.save(run)
        self._learn(run)
        self.current_path = None
