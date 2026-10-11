"""Hasar istatistikleri: karsilasma (encounter) bazinda kaynak basina toplam ve DPS."""

from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from .protocol import Hit, class_from_skill

VIEW_SELF, VIEW_PARTY, VIEW_ALL = "ben", "parti", "herkes"
MIN_CLASS_VOTES = 3


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
    cls: str | None
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
    in_group: bool


class Meter:
    """Thread-safe. Son vurustan idle_reset saniye sonra yeni vurus yeni karsilasma baslatir."""

    def __init__(self, idle_reset: float = 15.0, clock=time.monotonic):
        self.idle_reset = idle_reset
        self.clock = clock
        self._lock = threading.Lock()
        self.self_id: int | None = None
        self.self_name: str | None = None
        # Kimlik bilgileri karsilasmalar arasinda korunur.
        self.names: dict[int, str] = {}
        self.known_class: dict[int, str] = {}
        self.class_votes: dict[int, Counter] = {}
        self.groups: dict[str, set[int]] = {"party": set(), "force": set()}
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
                self.names[player_id] = name
            elif player_id != self.self_id:
                self.self_name = self.names.get(player_id)
            self.self_id = player_id

    def set_identity(self, entity: int, name: str | None, cls: str | None = None) -> None:
        with self._lock:
            if name:
                self.names[entity] = name
                if entity == self.self_id:
                    self.self_name = name
            if cls:
                self.known_class[entity] = cls

    def update_group(self, kind: str, members: list[int], full_list: bool) -> None:
        with self._lock:
            if full_list:
                self.groups[kind] = set(members)
            else:
                self.groups[kind].update(members)

    def add_hit(self, hit: Hit) -> None:
        now = self.clock()
        cls = class_from_skill(hit.skill)
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
            if cls:
                self.class_votes.setdefault(hit.source, Counter())[cls] += 1

    def _class_of(self, sid: int) -> str | None:
        if sid in self.known_class:
            return self.known_class[sid]
        votes = self.class_votes.get(sid)
        if votes:
            cls, n = votes.most_common(1)[0]
            if n >= MIN_CLASS_VOTES:
                return cls
        return None

    def _is_player(self, sid: int) -> bool:
        return (sid == self.self_id or sid in self.names or sid in self.known_class
                or any(sid in g for g in self.groups.values()) or self._class_of(sid) is not None)

    def _select(self, view: str) -> dict[int, SourceStats]:
        sources = self.sources
        if self.self_id is None:
            # Kendimizi secebilmemiz icin oyuncu gibi gorunen herkesi goster.
            players = {sid: s for sid, s in sources.items() if self._is_player(sid)}
            return players or dict(sources)
        if view == VIEW_SELF:
            return {sid: s for sid, s in sources.items() if sid == self.self_id}
        if view == VIEW_PARTY:
            group = self.groups["party"] | self.groups["force"]
            return {sid: s for sid, s in sources.items() if sid == self.self_id or sid in group}
        # Herkes: tanimli oyuncular + bizim vurdugumuz hedeflere vuranlar.
        me = sources.get(self.self_id)
        enemies = me.targets if me else set()
        return {sid: s for sid, s in sources.items()
                if self._is_player(sid) or s.targets & enemies}

    def snapshot(self, max_rows: int = 8, view: str = VIEW_PARTY) -> Snapshot:
        now = self.clock()
        with self._lock:
            in_group = bool(self.groups["party"] or self.groups["force"])
            if self.first_hit is None:
                return Snapshot(0.0, False, self.self_id, self.self_name, None, [], in_group)
            active = now - self.last_hit <= self.idle_reset
            duration = max(1.0, self.last_hit - self.first_hit)

            candidates = self._select(view)
            grand = sum(s.total for s in candidates.values()) or 1
            rows = []
            for sid, s in candidates.items():
                is_self = sid == self.self_id
                name = self.names.get(sid) or ((self.self_name or "Ben") if is_self else f"#{sid}")
                rows.append(Row(sid, name, self._class_of(sid), s.total, s.total / duration,
                                s.total / grand, s.crits / s.hits if s.hits else 0.0, is_self))
            rows.sort(key=lambda r: r.total, reverse=True)
            self_row = next((r for r in rows if r.is_self), None)
            if self_row is None and self.self_id in self.sources:
                s = self.sources[self.self_id]
                self_row = Row(self.self_id, self.self_name or "Ben", self._class_of(self.self_id),
                               s.total, s.total / duration, 1.0, s.crits / s.hits if s.hits else 0.0,
                               True)
            return Snapshot(duration, active, self.self_id, self.self_name, self_row,
                            rows[:max_rows], in_group)
