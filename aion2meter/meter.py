"""Hasar istatistikleri: dovus gecmisi, zindan toplami, skill dokumu ve alinan hasar."""

from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from .gamedata import npc_info, skill_name
from .protocol import Hit, class_from_skill

VIEW_SELF, VIEW_PARTY, VIEW_ALL = "ben", "parti", "herkes"
# Son dovus: idle_reset sonra kendiliginden sifirlanir. Toplam: elle sifirlanana kadar birikir
# (zindanin tum bosslari); suresi sadece dovusulen zamandir. Tamsayi kapsam: o numarali dovus.
SCOPE_FIGHT, SCOPE_TOTAL = "dovus", "toplam"
MODE_DEALT, MODE_TAKEN = "verilen", "alinan"
MIN_CLASS_VOTES = 3
# Boss faz gecisinde (dokunulmazlik, ara sahne) hasar kesilse de ayni boss'a donulurse
# yeni dovus acma; bu kadar saniyeye kadar ayni dovuse devam edilir.
BOSS_REJOIN_SEC = 90.0


@dataclass(slots=True)
class SkillStats:
    total: int = 0
    hits: int = 0
    crits: int = 0
    max_hit: int = 0


@dataclass(slots=True)
class SourceStats:
    total: int = 0
    hits: int = 0
    crits: int = 0
    max_hit: int = 0
    max_skill: int = 0
    targets: set[int] = field(default_factory=set)
    skills: dict[tuple[int, bool], SkillStats] = field(default_factory=dict)
    # Dovus basindan beri gecen saniye -> o saniyedeki hasar (grafik icin).
    buckets: dict[int, int] = field(default_factory=dict)

    def add(self, hit: Hit, second: int | None) -> None:
        self.total += hit.amount
        self.hits += 1
        self.crits += hit.crit
        if hit.amount > self.max_hit:
            self.max_hit, self.max_skill = hit.amount, hit.skill
        self.targets.add(hit.target)
        sk = self.skills.get((hit.skill, hit.periodic))
        if sk is None:
            sk = self.skills[(hit.skill, hit.periodic)] = SkillStats()
        sk.total += hit.amount
        sk.hits += 1
        sk.crits += hit.crit
        sk.max_hit = max(sk.max_hit, hit.amount)
        if second is not None:
            self.buckets[second] = self.buckets.get(second, 0) + hit.amount


class Fight:
    def __init__(self, serial: int, start: float):
        self.serial = serial
        self.first = self.last = start
        self.sources: dict[int, SourceStats] = {}
        self.taken: dict[int, int] = {}
        self.boss_damage: dict[int, int] = {}
        self.boss: int | None = None  # en cok hasar alan boss'un varlik id'si

    @property
    def duration(self) -> float:
        return self.last - self.first

    def add(self, hit: Hit, now: float, target_is_boss: bool, timeline: bool = True) -> None:
        s = self.sources.get(hit.source)
        if s is None:
            s = self.sources[hit.source] = SourceStats()
        s.add(hit, int(now - self.first) if timeline else None)
        self.taken[hit.target] = self.taken.get(hit.target, 0) + hit.amount
        if target_is_boss:
            dealt = self.boss_damage[hit.target] = self.boss_damage.get(hit.target, 0) + hit.amount
            if self.boss is None or dealt > self.boss_damage[self.boss]:
                self.boss = hit.target


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
    hits: int = 0
    max_hit: int = 0


@dataclass(slots=True)
class SkillRow:
    name: str
    periodic: bool
    total: int
    share: float
    hits: int
    crit_rate: float
    max_hit: int


@dataclass(slots=True)
class FightInfo:
    serial: int
    label: str
    boss_name: str | None
    duration: float
    self_dps: float | None


@dataclass(slots=True)
class Snapshot:
    duration: float
    active: bool
    self_id: int | None
    self_name: str | None
    self_row: Row | None
    rows: list[Row]
    in_group: bool
    label: str = ""
    boss_name: str | None = None
    timeline: list[int] = field(default_factory=list)
    fights: list[FightInfo] = field(default_factory=list)
    scope: str | int = SCOPE_FIGHT  # gercekten gosterilen kapsam (secilen dovus silindiyse canli)


class Meter:
    """Thread-safe. Son vurustan idle_reset saniye sonra yeni vurus yeni dovus baslatir;
    dovusler zindan boyunca saklanir, reset() hepsini (ve toplami) siler."""

    def __init__(self, idle_reset: float = 15.0, clock=time.monotonic,
                 self_name_hint: str | None = None):
        self.idle_reset = idle_reset
        self.clock = clock
        self._lock = threading.Lock()
        self.self_id: int | None = None
        self.self_name: str | None = None
        # Onceki oturumdan hatirlanan karakter adi: isim paketi gelince bizi otomatik tanir.
        self.self_name_hint = self_name_hint
        # Kimlik bilgileri dovusler arasinda korunur.
        self.names: dict[int, str] = {}
        self.known_class: dict[int, str] = {}
        self.class_votes: dict[int, Counter] = {}
        self.groups: dict[str, set[int]] = {"party": set(), "force": set()}
        self.npc_of: dict[int, int] = {}  # varlik id -> NPC kodu
        self.changes = 0  # her degisiklikte artar; kayit icin
        self._reset_locked()

    def _reset_locked(self) -> None:
        self.fights: list[Fight] = []
        self.total = Fight(0, 0.0)
        self.run_started = time.time()
        self.changes += 1

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

    def set_self_entity(self, entity: int) -> bool:
        """Isimsiz kimlik: sadece bize gelen durum paketinden varlik id'miz. Ad, bilinen
        adimizdan (onceki bolge ya da ayarlara kaydedilmis) gelir. Degistiyse True."""
        with self._lock:
            if entity == self.self_id:
                return False
            self.self_id = entity
            name = self.names.get(entity) or self.self_name or self.self_name_hint
            if name:
                self.self_name = name
                self.names.setdefault(entity, name)
            return True

    def set_identity(self, entity: int, name: str | None, cls: str | None = None) -> None:
        with self._lock:
            if name:
                self.names[entity] = name
                if entity == self.self_id:
                    self.self_name = name
                elif self.self_id is None and name == self.self_name_hint:
                    self.self_id, self.self_name = entity, name
            if cls:
                self.known_class[entity] = cls

    def set_npc(self, entity: int, code: int) -> None:
        with self._lock:
            self.npc_of[entity] = code

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
            fight = self.fights[-1] if self.fights else None
            if fight is None or now - fight.last > self.idle_reset:
                rejoin = (fight is not None and fight.boss is not None
                          and now - fight.last <= BOSS_REJOIN_SEC
                          and fight.boss in (hit.source, hit.target))
                if not rejoin:
                    fight = Fight(len(self.fights) + 1, now)
                    self.fights.append(fight)
            fight.last = now
            target_is_boss = self._is_boss(hit.target)
            fight.add(hit, now, target_is_boss)
            self.total.add(hit, now, target_is_boss, timeline=False)
            if cls:
                self.class_votes.setdefault(hit.source, Counter())[cls] += 1
            self.changes += 1

    # --- yardimcilar (kilit altinda cagrilir) ---

    def _is_boss(self, entity: int) -> bool:
        code = self.npc_of.get(entity)
        info = npc_info(code) if code else None
        return bool(info and info[1])

    def _npc_name(self, entity: int | None) -> str | None:
        code = self.npc_of.get(entity) if entity is not None else None
        info = npc_info(code) if code else None
        return info[0] if info and info[0] else None

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
        if sid in self.npc_of:
            return False
        return (sid == self.self_id or sid in self.names or sid in self.known_class
                or any(sid in g for g in self.groups.values()) or self._class_of(sid) is not None)

    def _name(self, sid: int) -> str:
        if sid == self.self_id:
            return self.names.get(sid) or self.self_name or "Ben"
        return self.names.get(sid) or f"#{sid}"

    def _fight_label(self, f: Fight) -> str:
        return self._npc_name(f.boss) or f"Dövüş {f.serial}"

    def _resolve(self, scope) -> tuple[Fight | None, float, list[Fight], str | int]:
        """Kapsam -> (istatistik, sure, grafik icin dovusler, gercek kapsam)."""
        if scope == SCOPE_TOTAL:
            return self.total, sum(f.duration for f in self.fights), self.fights, SCOPE_TOTAL
        if isinstance(scope, int):
            for f in self.fights:
                if f.serial == scope:
                    return f, f.duration, [f], scope
        f = self.fights[-1] if self.fights else None
        return f, (f.duration if f else 0.0), ([f] if f else []), SCOPE_FIGHT

    def _select(self, sources: dict[int, SourceStats], view: str) -> dict[int, SourceStats]:
        if self.self_id is None:
            # Kendimizi secebilmemiz icin oyuncu gibi gorunen herkesi goster.
            players = {sid: s for sid, s in sources.items() if self._is_player(sid)}
            return players or {sid: s for sid, s in sources.items() if sid not in self.npc_of}
        if view == VIEW_SELF:
            return {sid: s for sid, s in sources.items() if sid == self.self_id}
        if view == VIEW_PARTY:
            group = self.groups["party"] | self.groups["force"]
            return {sid: s for sid, s in sources.items() if sid == self.self_id or sid in group}
        # Herkes: tanimli oyuncular + bizim vurdugumuz hedeflere vuranlar.
        me = sources.get(self.self_id)
        enemies = me.targets if me else set()
        return {sid: s for sid, s in sources.items()
                if sid not in self.npc_of and (self._is_player(sid) or s.targets & enemies)}

    def _select_taken(self, taken: dict[int, int], view: str) -> dict[int, int]:
        players = {t: v for t, v in taken.items() if self._is_player(t)}
        if self.self_id is None or view == VIEW_ALL:
            return players
        if view == VIEW_SELF:
            return {t: v for t, v in players.items() if t == self.self_id}
        group = self.groups["party"] | self.groups["force"]
        return {t: v for t, v in players.items() if t == self.self_id or t in group}

    def _rows(self, fight: Fight, duration: float, view: str, mode: str) -> list[Row]:
        rows = []
        if mode == MODE_TAKEN:
            chosen = self._select_taken(fight.taken, view)
            grand = sum(chosen.values()) or 1
            for sid, amount in chosen.items():
                rows.append(Row(sid, self._name(sid), self._class_of(sid), amount, amount / duration,
                                amount / grand, 0.0, sid == self.self_id))
        else:
            chosen = self._select(fight.sources, view)
            grand = sum(s.total for s in chosen.values()) or 1
            for sid, s in chosen.items():
                rows.append(self._row(sid, s, duration, s.total / grand))
        rows.sort(key=lambda r: r.total, reverse=True)
        return rows

    def _row(self, sid: int, s: SourceStats, duration: float, share: float) -> Row:
        return Row(sid, self._name(sid), self._class_of(sid), s.total, s.total / duration, share,
                   s.crits / s.hits if s.hits else 0.0, sid == self.self_id, s.hits, s.max_hit)

    def _timeline(self, fights: list[Fight], sid: int | None) -> list[int]:
        out: list[int] = []
        if sid is None:
            return out
        for f in fights:
            s = f.sources.get(sid)
            buckets = s.buckets if s else {}
            out.extend(buckets.get(i, 0) for i in range(int(f.duration) + 1))
        return out

    def _fight_infos(self) -> list[FightInfo]:
        infos = []
        for f in self.fights:
            me = f.sources.get(self.self_id) if self.self_id is not None else None
            dur = max(1.0, f.duration)
            infos.append(FightInfo(f.serial, self._fight_label(f), self._npc_name(f.boss),
                                   f.duration, me.total / dur if me else None))
        return infos

    # --- disariya acik okumalar ---

    def in_combat(self) -> bool:
        with self._lock:
            return bool(self.fights) and self.clock() - self.fights[-1].last <= self.idle_reset

    def snapshot(self, max_rows: int = 8, view: str = VIEW_PARTY, scope=SCOPE_FIGHT,
                 mode: str = MODE_DEALT) -> Snapshot:
        now = self.clock()
        with self._lock:
            in_group = bool(self.groups["party"] or self.groups["force"])
            latest = self.fights[-1] if self.fights else None
            fight, raw_duration, timeline_fights, scope = self._resolve(scope)
            if fight is None or latest is None:
                return Snapshot(0.0, False, self.self_id, self.self_name, None, [], in_group)
            active = now - latest.last <= self.idle_reset and fight in (latest, self.total)
            duration = max(1.0, raw_duration)
            rows = self._rows(fight, duration, view, mode)

            self_row = None
            me = fight.sources.get(self.self_id) if self.self_id is not None else None
            if me is not None:
                dealt = self._select(fight.sources, view)
                grand = sum(s.total for s in dealt.values()) or me.total
                self_row = self._row(self.self_id, me, duration, me.total / grand)
            if scope == SCOPE_TOTAL:
                label, boss_name = "Zindan toplamı", None
            else:
                label, boss_name = self._fight_label(fight), self._npc_name(fight.boss)
            return Snapshot(duration, active, self.self_id, self.self_name, self_row,
                            rows[:max_rows], in_group, label, boss_name,
                            self._timeline(timeline_fights, self.self_id), self._fight_infos(), scope)

    def skill_breakdown(self, source: int, scope=SCOPE_FIGHT) -> tuple[Row | None, list[SkillRow]]:
        """Bir oyuncunun skill bazinda hasari. Ayni ada sahip varyantlar birlestirilir."""
        with self._lock:
            fight, raw_duration, _, _ = self._resolve(scope)
            s = fight.sources.get(source) if fight else None
            if s is None:
                return None, []
            row = self._row(source, s, max(1.0, raw_duration), 1.0)
            merged: dict[tuple[str, bool], SkillStats] = {}
            for (code, periodic), sk in s.skills.items():
                name = skill_name(code) or (f"Skill {code}" if code else "Diğer")
                m = merged.get((name, periodic))
                if m is None:
                    m = merged[(name, periodic)] = SkillStats()
                m.total += sk.total
                m.hits += sk.hits
                m.crits += sk.crits
                m.max_hit = max(m.max_hit, sk.max_hit)
            total = s.total or 1
            rows = [SkillRow(name, periodic, m.total, m.total / total, m.hits,
                             m.crits / m.hits if m.hits else 0.0, m.max_hit)
                    for (name, periodic), m in merged.items()]
            rows.sort(key=lambda r: r.total, reverse=True)
            return row, rows

    def export_run(self) -> dict | None:
        """Zindan kaydi icin dovuslerin ozeti (records.py yazar)."""
        with self._lock:
            if not self.fights:
                return None
            fights = [self._export(f, max(1.0, f.duration)) | {
                          "label": self._fight_label(f), "boss": self._npc_name(f.boss)}
                      for f in self.fights]
            total = self._export(self.total, max(1.0, sum(f.duration for f in self.fights)))
            return {
                "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.run_started)),
                "character": self.self_name,
                "class": self._class_of(self.self_id) if self.self_id is not None else None,
                "fights": fights,
                "total": total,
            }

    def _export(self, fight: Fight, duration: float) -> dict:
        players = [{"name": r.name, "class": r.cls, "total": r.total, "dps": round(r.dps),
                    "self": r.is_self} for r in self._rows(fight, duration, VIEW_ALL, MODE_DEALT)[:12]]
        me = fight.sources.get(self.self_id) if self.self_id is not None else None
        return {"duration": round(duration, 1), "players": players,
                "self_dps": round(me.total / duration) if me else None}
