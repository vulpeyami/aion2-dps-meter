"""TCP akisinin birlestirilmesi ve cercevelere ayrilmasi.

Sunucu iki bicimde gonderebiliyor:
  direct: cerceveler dogrudan TCP akisinda
  lp:     [u32 LE uzunluk][govde] zarflari; govdeler birlestirilince cerceve akisi
Meter oyundan sonra acildiginda akisin ortasina denk geliriz, bu yuzden hizalama
arka arkaya taninan cerceve zinciri aranarak bulunur.
"""

from __future__ import annotations

from .protocol import BAD, MAX_FRAME_LEN, NEED, OK, PacketParser, is_recognized_start, probe_frame

SEQ_MASK = 0xFFFFFFFF
MAX_PENDING_SEGMENTS = 256
MAX_BUFFER = 2 * MAX_FRAME_LEN
MAX_UNRECOGNIZED_RUN = 32
DETECT_WINDOW = 64 * 1024


class TcpReassembler:
    """Sira numarasina gore segmentleri dizer. feed() -> [(veri, bosluk_var_mi)].

    Kayip belirtilerini de sayar: retransmits (daha once gelmis veri tekrar geldi),
    holes (arada eksik segment var, sonrakiler once geldi).
    """

    def __init__(self):
        self.next_seq: int | None = None
        self.pending: dict[int, bytes] = {}
        self.retransmits = 0
        self.holes = 0

    def reset(self, seq: int | None = None) -> None:
        self.next_seq = seq
        self.pending.clear()

    def feed(self, seq: int, data: bytes):
        out = []
        if self.next_seq is None:
            self.next_seq = seq
        diff = (seq - self.next_seq) & SEQ_MASK
        if diff >= 1 << 31:  # eski ya da ortusen segment
            self.retransmits += 1
            overlap = (self.next_seq - seq) & SEQ_MASK
            if overlap >= len(data):
                return out
            data = data[overlap:]
            diff = 0
        if diff == 0:
            out.append((data, False))
            self.next_seq = (self.next_seq + len(data)) & SEQ_MASK
            self._drain(out)
        else:
            if not self.pending:
                self.holes += 1
            elif seq in self.pending:
                self.retransmits += 1
            self.pending[seq] = data
            if len(self.pending) > MAX_PENDING_SEGMENTS:
                # Kayip paket geri gelmedi: boslugu atla.
                nearest = min(self.pending, key=lambda s: (s - self.next_seq) & SEQ_MASK)
                self.next_seq = nearest
                gap_data = self.pending.pop(nearest)
                out.append((gap_data, True))
                self.next_seq = (nearest + len(gap_data)) & SEQ_MASK
                self._drain(out)
        return out

    def _drain(self, out) -> None:
        while self.pending:
            progressed = False
            for s in list(self.pending):
                diff = (s - self.next_seq) & SEQ_MASK
                if diff == 0 or diff >= 1 << 31:
                    data = self.pending.pop(s)
                    overlap = (self.next_seq - s) & SEQ_MASK if diff else 0
                    if overlap < len(data):
                        data = data[overlap:]
                        out.append((data, False))
                        self.next_seq = (self.next_seq + len(data)) & SEQ_MASK
                    progressed = True
            if not progressed:
                return


def _find_frame_chain(buf, start: int):
    """Arka arkaya en az iki taninan cerceve baslayan ilk ofset."""
    n = len(buf)
    for k in range(start, n - 2):
        st, f1, p1 = probe_frame(buf, k)
        if st != OK or not is_recognized_start(buf, k, p1):
            continue
        k2 = k + f1
        if k2 == n:
            return k
        st2, _, p2 = probe_frame(buf, k2)
        if st2 == BAD:
            continue
        if st2 == NEED and n - k2 < 8:
            return k
        if is_recognized_start(buf, k2, p2):
            return k
    return None


def _find_lp_chain(buf, start: int):
    """En az uc tutarli [u32 uzunluk][govde] zarfinin basladigi ilk ofset."""
    n = len(buf)
    for k in range(start, n - 4):
        pos, bodies = k, []
        while len(bodies) < 3 and pos + 4 <= n:
            size = int.from_bytes(buf[pos:pos + 4], "little")
            if not 0 < size <= 65536 or pos + 4 + size > n:
                break
            bodies.append((pos + 4, size))
            pos += 4 + size
        if len(bodies) < 3:
            continue
        head = bytearray()
        for body_start, size in bodies:
            head += buf[body_start:body_start + min(size, 512 - len(head))]
            if len(head) >= 512:
                break
        if _find_frame_chain(head, 0) is not None:
            return k
    return None


def _lp_undecided(buf, k: int) -> bool:
    """k'da baslayan zarf zinciri henuz ne dogrulanabiliyor ne de curutulebiliyorsa True."""
    if len(buf) > DETECT_WINDOW:
        return False
    pos = k
    for _ in range(3):
        if pos + 4 > len(buf):
            return True
        size = int.from_bytes(buf[pos:pos + 4], "little")
        if not 0 < size <= 65536:
            return False
        pos += 4 + size
    return pos > len(buf)


class StreamDecoder:
    """Bir TCP yonu icin bayt akisini cercevelere boler ve parser'a verir."""

    def __init__(self, parser: PacketParser, conn):
        self.parser = parser
        self.conn = conn
        self.raw = bytearray()
        self.canon = bytearray()
        self.mode: str | None = None
        self.aligned = False
        self.envelope_left = 0
        self.unrecognized_run = 0

    def reset(self) -> None:
        self.raw.clear()
        self.canon.clear()
        self.mode = None
        self.aligned = False
        self.envelope_left = 0
        self.unrecognized_run = 0

    def feed(self, data: bytes, gap: bool = False) -> None:
        if gap:
            self.reset()
        self.raw += data
        if self.mode is None and not self._detect():
            if len(self.raw) > DETECT_WINDOW:
                del self.raw[:len(self.raw) - 16]
            return
        if self.mode == "lp":
            if not self._unwrap():
                self.reset()
                return
            self._walk(self.canon)
        else:
            self._walk(self.raw)

    def _detect(self) -> bool:
        lp = _find_lp_chain(self.raw, 0)
        direct = _find_frame_chain(self.raw, 0)
        if lp is not None and (direct is None or lp <= direct):
            del self.raw[:lp]
            self.mode = "lp"
            return True
        if direct is not None:
            if direct >= 4 and _lp_undecided(self.raw, direct - 4):
                return False  # zarf olabilir; dogrulamak icin daha fazla veri bekle
            del self.raw[:direct]
            self.mode = "direct"
            self.aligned = True
            return True
        return False

    def _unwrap(self) -> bool:
        raw = self.raw
        while raw:
            if self.envelope_left == 0:
                if len(raw) < 4:
                    break
                size = int.from_bytes(raw[:4], "little")
                if not 0 < size <= MAX_FRAME_LEN:
                    return False
                del raw[:4]
                self.envelope_left = size
            take = min(self.envelope_left, len(raw))
            self.canon += raw[:take]
            del raw[:take]
            self.envelope_left -= take
        return True

    def _walk(self, buf: bytearray) -> None:
        off = 0
        while True:
            if not self.aligned:
                k = _find_frame_chain(buf, off)
                if k is None:
                    off = max(off, len(buf) - 16)
                    break
                off = k
                self.aligned = True
                self.unrecognized_run = 0
            st, flen, plen = probe_frame(buf, off)
            if st == NEED:
                if len(buf) - off > MAX_BUFFER:
                    self.aligned = False
                    off += 1
                    continue
                break
            if st == BAD:
                self.aligned = False
                off += 1
                continue
            if is_recognized_start(buf, off, plen):
                self.unrecognized_run = 0
            else:
                self.unrecognized_run += 1
                if self.unrecognized_run > MAX_UNRECOGNIZED_RUN:
                    self.aligned = False
                    off += 1
                    continue
            self.parser.handle_frame(bytes(buf[off:off + flen]), plen, self.conn)
            off += flen
        if off:
            del buf[:off]
