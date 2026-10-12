"""Takilma (lag) sensoru: sunucudan gelen veride kesinti ve paket kaybi.

Tamamen pasif: sadece zaten okunan paketlerin zamanina ve TCP sira numarasina bakar.
Sunucu normalde ~60 ms'de bir veri gonderir (kayitlarda en uzun ara 0.08 sn), bu yuzden
yarim saniyelik sessizlik bile oyunda "herkes durdu" olarak hissedilir.

Ayrim:
  paket kaybi      kesinti civarinda yeniden gonderim ya da delik: paket yolda kayboldu
  sunucu takildi   kesinti boyunca sunucu bos ACK gondermeye devam etti: baglanti ayakta,
                   senin paketlerini aliyor ama oyun verisi gondermiyor (sunucu tarafi)
  hat sessiz       hicbir paket gelmedi ve kayip da yok: sunucu ya da hatta gecikme

Ornek (2026-10-12 zindan kaydi): 4.8 sn veri yok, arada 88 bos ACK -> sunucu takildi.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

GAP_SEC = 0.5           # bu kadar sessizlik takilma sayilir
WARMUP_SEGMENTS = 100   # yeni baglanti: yukleme ekrani sirasindaki bosluklar sayilmasin
SETTLE_SEC = 3.0        # kesintiden sonra kayip belirtileri bu sure icinde toplanir
MAX_EVENTS = 50


@dataclass
class LagEvent:
    at: float           # kesintinin bittigi an (time.time())
    duration: float     # sunucudan veri gelmeyen sure
    losses: int = 0     # kesinti civarinda gorulen yeniden gonderim/delik sayisi
    acks: int = 0       # kesinti sirasinda gelen veri tasimayan paketler
    final: bool = False

    @property
    def kind(self) -> str:
        if self.losses:
            return "paket kaybı"
        return "sunucu takıldı" if self.acks else "hat sessiz"

    def describe(self) -> str:
        start = time.strftime('%H:%M:%S', time.localtime(self.at - self.duration))
        return f"{start}  {self.duration:.1f} sn  ({self.kind})"


class LagMonitor:
    def __init__(self, log=lambda msg: None, gap_sec: float = GAP_SEC):
        self.log = log
        self.gap_sec = gap_sec
        self.events: list[LagEvent] = []
        self.retransmits = 0
        self.holes = 0
        # anahtar -> [son veri zamani, veri segmenti sayisi, son veriden beri bos ACK]
        self._flows: dict[tuple, list] = {}
        self._recent_loss: list[float] = []   # son kayip belirtilerinin zamanlari
        self._lock = threading.Lock()

    def ack(self, key) -> None:
        """Veri tasimayan segment (bos ACK) geldi."""
        with self._lock:
            flow = self._flows.get(key)
            if flow is not None:
                flow[2] += 1

    def feed(self, key, ts: float, retransmit: bool = False, hole: bool = False) -> None:
        """Her veri tasiyan segment icin cagrilir (isleyici thread)."""
        with self._lock:
            if retransmit or hole:
                self.retransmits += retransmit
                self.holes += hole
                self._recent_loss = [t for t in self._recent_loss if ts - t < SETTLE_SEC]
                self._recent_loss.append(ts)
                for e in self.events[-3:]:
                    if not e.final and ts - e.at <= SETTLE_SEC:
                        e.losses += 1
            flow = self._flows.get(key)
            if flow is None:
                if len(self._flows) > 64:  # kapanmis baglantilar
                    self._flows.clear()
                flow = self._flows[key] = [ts, 0, 0]
            gap = ts - flow[0]
            if flow[1] >= WARMUP_SEGMENTS and gap >= self.gap_sec:
                # Kesintiden hemen once gorulen kayiplar da bu olaya aittir.
                losses = sum(1 for t in self._recent_loss if flow[0] - 1.0 <= t <= ts)
                self.events.append(LagEvent(ts, gap, losses, flow[2]))
                del self.events[:-MAX_EVENTS]
            flow[0] = max(flow[0], ts)
            flow[1] += 1
            flow[2] = 0
            self._finalize(ts)

    def _finalize(self, now: float) -> None:
        for e in self.events[-3:]:
            if not e.final and now - e.at > SETTLE_SEC:
                e.final = True
                if e.losses:
                    why = f"paket kaybi ({e.losses} yeniden gonderim/delik)"
                elif e.acks:
                    why = (f"sunucu takildi (baglanti ayakta, arada {e.acks} bos ACK geldi, "
                           "oyun verisi gelmedi)")
                else:
                    why = "hat sessiz (hic paket gelmedi, kayip yok)"
                self.log(f"Takilma: {e.duration:.1f} sn sunucudan veri gelmedi - {why}")

    def last_event(self) -> LagEvent | None:
        with self._lock:
            return self.events[-1] if self.events else None

    def recent(self, n: int = 10) -> list[LagEvent]:
        with self._lock:
            return list(self.events[-n:])

    def stats(self) -> dict:
        with self._lock:
            return {"takilma": len(self.events),
                    "en_uzun": round(max((e.duration for e in self.events), default=0.0), 1),
                    "yeniden_gonderim": self.retransmits, "delik": self.holes}
