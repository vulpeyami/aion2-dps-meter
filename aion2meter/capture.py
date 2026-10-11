"""Aion2.exe'nin TCP trafigini WinDivert ile pasif olarak dinler (sadece okuma, SNIFF).

Akis: port izleyici -> WinDivert alici thread(ler)i -> kuyruk -> isleyici thread
      (TCP birlestirme + cerceve ayirma + paket cozme).
"""

from __future__ import annotations

import ctypes
import os
import queue
import struct
import threading
import time
from ctypes import wintypes

import psutil

from .protocol import PacketParser
from .stream import StreamDecoder, TcpReassembler

GAME_EXE_NAMES = {"aion2.exe"}
WEB_PORTS = {80, 443}  # oyunun HTTPS baglantilari sifreli, dinlemeye degmez
WINDIVERT_LAYER_NETWORK = 0
WINDIVERT_FLAG_SNIFF = 0x0001
WINDIVERT_FLAG_RECV_ONLY = 0x0004
WINDIVERT_SHUTDOWN_BOTH = 0x3
INVALID_HANDLE = ctypes.c_void_p(-1).value
FLOW_IDLE_SEC = 120.0

DUMP_MAGIC = b"A2MDUMP1"


def parse_ip_tcp(pkt: bytes):
    """-> (akis_anahtari, seq, syn, veri) ya da None."""
    if not pkt:
        return None
    version = pkt[0] >> 4
    if version == 4:
        ihl = (pkt[0] & 0x0F) * 4
        if pkt[9] != 6:
            return None
        total = int.from_bytes(pkt[2:4], "big")
        src, dst = pkt[12:16], pkt[16:20]
        tcp_start, ip_end = ihl, min(total, len(pkt))
    elif version == 6:
        if pkt[6] != 6:
            return None
        src, dst = pkt[8:24], pkt[24:40]
        tcp_start = 40
        ip_end = min(40 + int.from_bytes(pkt[4:6], "big"), len(pkt))
    else:
        return None
    t = pkt[tcp_start:ip_end]
    if len(t) < 20:
        return None
    sport, dport, seq = struct.unpack_from(">HHI", t, 0)
    doff = (t[12] >> 4) * 4
    syn = bool(t[13] & 0x02)
    key = (bytes(src), sport, bytes(dst), dport)
    return key, seq, syn, bytes(t[doff:])


def find_game_ports() -> set[int]:
    pids = set()
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name in GAME_EXE_NAMES:
            pids.add(proc.pid)
    if not pids:
        return set()
    ports = set()
    for c in psutil.net_connections(kind="tcp"):
        if (c.pid in pids and c.raddr and c.laddr and c.status == psutil.CONN_ESTABLISHED
                and c.raddr.port not in WEB_PORTS):
            ports.add(c.laddr.port)
    return ports


class Pipeline:
    """Ham TCP segmentlerini akislara ayirip parser'a verir. Tek thread'den cagrilmali."""

    def __init__(self, parser: PacketParser, dump_file=None):
        self.parser = parser
        self.dump_file = dump_file
        self.flows: dict[tuple, tuple[TcpReassembler, StreamDecoder, list]] = {}
        self.segments = 0
        self._last_cleanup = time.monotonic()

    def process(self, key, seq: int, syn: bool, data: bytes, ts: float | None = None) -> None:
        ts = time.time() if ts is None else ts
        if self.dump_file is not None:
            k = repr(key).encode()
            self.dump_file.write(struct.pack("<dHIB I", ts, len(k), seq, syn, len(data)))
            self.dump_file.write(k)
            self.dump_file.write(data)
        flow = self.flows.get(key)
        if flow is None or syn:
            flow = (TcpReassembler(), StreamDecoder(self.parser, key), [0.0])
            self.flows[key] = flow
            if syn:
                flow[0].reset((seq + 1) & 0xFFFFFFFF)
        reasm, decoder, last_seen = flow
        last_seen[0] = time.monotonic()
        if not data:
            return
        self.segments += 1
        for chunk, gap in reasm.feed(seq, data):
            decoder.feed(chunk, gap)
        self._cleanup()

    def _cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < 10:
            return
        self._last_cleanup = now
        for key in [k for k, f in self.flows.items() if now - f[2][0] > FLOW_IDLE_SEC]:
            del self.flows[key]


def replay_dump(path: str, pipeline: Pipeline) -> int:
    """--dump ile kaydedilmis dosyayi isler. Islenen segment sayisini dondurur."""
    import ast
    count = 0
    head = struct.calcsize("<dHIB I")
    with open(path, "rb") as f:
        if f.read(len(DUMP_MAGIC)) != DUMP_MAGIC:
            raise ValueError("Gecersiz dump dosyasi")
        while True:
            h = f.read(head)
            if len(h) < head:
                break
            ts, klen, seq, syn, dlen = struct.unpack("<dHIB I", h)
            key = ast.literal_eval(f.read(klen).decode())
            data = f.read(dlen)
            pipeline.process(key, seq, bool(syn), data, ts)
            count += 1
    return count


class _WinDivert:
    def __init__(self, dll_dir: str):
        os.add_dll_directory(dll_dir)
        dll = ctypes.WinDLL(os.path.join(dll_dir, "WinDivert.dll"), use_last_error=True)
        self.open = dll.WinDivertOpen
        self.open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int16, ctypes.c_uint64]
        self.open.restype = ctypes.c_void_p
        self.recv = dll.WinDivertRecv
        self.recv.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                              ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p]
        self.recv.restype = wintypes.BOOL
        self.shutdown = dll.WinDivertShutdown
        self.shutdown.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.shutdown.restype = wintypes.BOOL
        self.close = dll.WinDivertClose
        self.close.argtypes = [ctypes.c_void_p]
        self.close.restype = wintypes.BOOL


class LiveCapture:
    """Oyun portlarini izler ve her port seti icin bir WinDivert tutamaci acar."""

    def __init__(self, pipeline: Pipeline, dll_dir: str, log=print):
        self.pipeline = pipeline
        self.log = log
        self.wd = _WinDivert(dll_dir)
        self.queue: queue.Queue = queue.Queue(maxsize=50000)
        self.ports: set[int] = set()
        self.status = "Oyun aranıyor..."
        self.error: str | None = None
        self._handle = None
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._watch_ports, name="ports", daemon=True).start()
        threading.Thread(target=self._process, name="process", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._swap_handle(None)

    def _watch_ports(self) -> None:
        while not self._stop.is_set():
            try:
                ports = find_game_ports()
            except Exception as e:  # psutil gecici hatalari
                self.log(f"Port taramasi hatasi: {e}")
                ports = self.ports
            if ports != self.ports:
                self.ports = ports
                self._reopen(ports)
            self._stop.wait(2.0)

    def _reopen(self, ports: set[int]) -> None:
        if not ports:
            self._swap_handle(None)
            self.status = "Oyun bulunamadı"
            self.log("Aion2.exe baglantisi yok, bekleniyor...")
            return
        clause = " or ".join(f"tcp.DstPort == {p}" for p in sorted(ports))
        filt = f"tcp and ({clause})"
        handle = self.wd.open(filt.encode(), WINDIVERT_LAYER_NETWORK, 0,
                              WINDIVERT_FLAG_SNIFF | WINDIVERT_FLAG_RECV_ONLY)
        if handle in (None, INVALID_HANDLE):
            err = ctypes.get_last_error()
            hint = " (yönetici olarak çalıştırın)" if err == 5 else ""
            self.error = f"WinDivert açılamadı, hata {err}{hint}"
            self.status = self.error
            self.log(self.error)
            return
        self.error = None
        self.status = "Dinleniyor"
        self.log(f"Dinleniyor: portlar {sorted(ports)}")
        threading.Thread(target=self._recv_loop, args=(handle,), name="recv", daemon=True).start()
        self._swap_handle(handle)

    def _swap_handle(self, new) -> None:
        old, self._handle = self._handle, new
        if old is not None:
            self.wd.shutdown(old, WINDIVERT_SHUTDOWN_BOTH)

    def _recv_loop(self, handle) -> None:
        buf = ctypes.create_string_buffer(0xFFFF + 64)
        addr = ctypes.create_string_buffer(128)  # WINDIVERT_ADDRESS (80 bayt)
        got = ctypes.c_uint(0)
        try:
            while not self._stop.is_set():
                if not self.wd.recv(handle, buf, len(buf), ctypes.byref(got), addr):
                    break  # kapatildi
                try:
                    self.queue.put_nowait((time.time(), buf.raw[:got.value]))
                except queue.Full:
                    pass
        finally:
            self.wd.close(handle)

    def _process(self) -> None:
        while not self._stop.is_set():
            try:
                ts, pkt = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            parsed = parse_ip_tcp(pkt)
            if parsed is None:
                continue
            key, seq, syn, data = parsed
            try:
                self.pipeline.process(key, seq, syn, data, ts)
            except Exception as e:
                self.log(f"Paket isleme hatasi: {e!r}")
