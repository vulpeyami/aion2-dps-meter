"""Hasar istatistikleri: karsilasma (encounter) bazinda kaynak basina toplam ve DPS."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .protocol import Hit


@dataclass(slots=True)
class SourceStats:
    total: int = 0
    hits: int = 0
    crits: int = 0
    targets: set[int] = field(default_factory=set)


@dataclass(slots=True)
class Row:
    source: int
    name: str
    total: int
    dps: float
    share: float
    crit_rate: float
    is_self: bool


@dataclass(slots=True)
class Snapshot:
    duration: float
    active: bool
    self_id: int | None
    self_name: str | None
    self_row: Row | None
    rows: list[Row]


class Meter:
    """Thread-safe. Son vurustan idle_reset saniye sonra yeni vurus yeni karsilasma baslatir."""

    def __init__(self, idle_reset: float = 15.0, clock=time.monotonic):
        self.idle_reset = idle_reset
        self.clock = clock
        self._lock = threading.Lock()
        self.self_id: int | None = None
        self.self_name: str | None = None
        self._reset_locked()

    def _reset_locked(self) -> None:
        self.sources: dict[int, SourceStats] = {}
        self.first_hit: float | None = None
        self.last_hit: float | None = None

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def set_self(self, player_id: int, name: str | None = None) -> None:
        with self._lock:
            if name:
                self.self_name = name
            elif player_id != self.self_id:
                self.self_name = None
            self.self_id = player_id

    def add_hit(self, hit: Hit) -> None:
        now = self.clock()
        with self._lock:
            if self.last_hit is not None and now - self.last_hit > self.idle_reset:
                self._reset_locked()
            if self.first_hit is None:
                self.first_hit = now
            self.last_hit = now
            s = self.sources.get(hit.source)
            if s is None:
                s = self.sources[hit.source] = SourceStats()
            s.total += hit.amount
            s.hits += 1
            s.crits += hit.crit
            s.targets.add(hit.target)

    def snapshot(self, max_rows: int = 8) -> Snapshot:
        now = self.clock()
        with self._lock:
            if self.first_hit is None:
                return Snapshot(0.0, False, self.self_id, self.self_name, None, [])
            active = now - self.last_hit <= self.idle_reset
            duration = max(1.0, self.last_hit - self.first_hit)

            me = self.sources.get(self.self_id) if self.self_id else None
            if me is not None:
                # Bizim vurdugumuz hedeflere vuranlar = grup arkadaslari.
                # Boylece canavarlarin bize verdigi hasar listeye girmez.
                enemies = me.targets
                candidates = {sid: s for sid, s in self.sources.items()
                              if sid == self.self_id or s.targets & enemies}
            else:
                candidates = dict(self.sources)

            grand = sum(s.total for s in candidates.values()) or 1
            rows = []
            for sid, s in candidates.items():
                is_self = sid == self.self_id
                name = (self.self_name or "Ben") if is_self else f"#{sid}"
                rows.append(Row(sid, name, s.total, s.total / duration, s.total / grand,
                                s.crits / s.hits if s.hits else 0.0, is_self))
            rows.sort(key=lambda r: r.total, reverse=True)
            self_row = next((r for r in rows if r.is_self), None)
            return Snapshot(duration, active, self.self_id, self.self_name, self_row,
                            rows[:max_rows])
