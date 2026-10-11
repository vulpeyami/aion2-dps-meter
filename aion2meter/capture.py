"""Aion2.exe'nin TCP trafigini pasif olarak dinler (sadece okuma).

Iki yontem:
  raw        Windows'un yerlesik ham soketi (SIO_RCVALL). Surucu yuklenmez. Varsayilan.
  windivert  WinDivert surucusu (SNIFF modu). Sadece --mode windivert ile.

Akis: oyun baglanti izleyici -> alici thread(ler) -> kuyruk -> isleyici thread
      (TCP birlestirme + cerceve ayirma + paket cozme).
"""

from __future__ import annotations

import ctypes
import os
import queue
import socket
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
NO_PACKET_WARNING_SEC = 20.0

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
        # Birlestirilmis (RSC) paketlerde uzunluk alani 0 gelebiliyor.
        tcp_start, ip_end = ihl, total if 0 < total <= len(pkt) else len(pkt)
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


def find_game_endpoints() -> set[tuple[str, int]]:
    """Oyunun acik TCP baglantilarinin yerel (ip, port) ciftleri."""
    pids = set()
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name in GAME_EXE_NAMES:
            pids.add(proc.pid)
    if not pids:
        return set()
    endpoints = set()
    for c in psutil.net_connections(kind="tcp"):
        if (c.pid in pids and c.raddr and c.laddr and c.status == psutil.CONN_ESTABLISHED
                and c.raddr.port not in WEB_PORTS):
            ip = c.laddr.ip
            if ip.startswith("::ffff:") and "." in ip:  # IPv4-mapped IPv6
                ip = ip[7:]
            endpoints.add((ip, c.laddr.port))
    return endpoints


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


class _RawSocketBackend:
    """Windows'un yerlesik ham soketi. Oyunun kullandigi her yerel IPv4 adresine bir soket."""

    name = "ham soket"

    def __init__(self, out: queue.Queue, log):
        self.out = out
        self.log = log
        self.ports: frozenset[int] = frozenset()
        self.received = 0
        self._sockets: dict[str, threading.Event] = {}

    def update(self, endpoints: set[tuple[str, int]]) -> str | None:
        """Hata varsa aciklamasini dondurur."""
        self.ports = frozenset(port for _, port in endpoints)
        ips = {ip for ip, _ in endpoints}
        for ip in set(self._sockets) - ips:
            self._sockets.pop(ip).set()
        for ip in ips - set(self._sockets):
            if ":" in ip:
                continue
            try:
                sock = self._open(ip)
            except PermissionError:
                return "Ham soket açılamadı: yönetici olarak çalıştırın"
            except OSError as e:
                return f"Ham soket açılamadı ({ip}): {e}"
            stop = threading.Event()
            self._sockets[ip] = stop
            threading.Thread(target=self._loop, args=(ip, sock, stop), name=f"raw-{ip}",
                             daemon=True).start()
            self.log(f"Ham soket acildi: {ip}")
        if any(":" in ip for ip in ips):
            return "Oyun IPv6 kullanıyor, ham soket desteklemiyor (--mode windivert)"
        return None

    def stop(self) -> None:
        for stop in self._sockets.values():
            stop.set()
        self._sockets.clear()

    @staticmethod
    def _open(ip: str) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
        try:
            sock.bind((ip, 0))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
            sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
            sock.settimeout(1.0)
        except OSError:
            sock.close()
            raise
        return sock

    def _loop(self, ip: str, sock: socket.socket, stop: threading.Event) -> None:
        # Soket kartin TUM trafigini verir; sadece oyuna gelen TCP paketlerini ayikla.
        local = socket.inet_aton(ip)
        buf = bytearray(0xFFFF)
        view = memoryview(buf)
        try:
            while not stop.is_set():
                try:
                    n = sock.recv_into(buf)
                except socket.timeout:
                    continue
                except OSError as e:
                    self.log(f"Ham soket kapandi ({ip}): {e}")
                    break
                if n < 40 or buf[9] != 6 or view[16:20] != local:
                    continue
                ihl = (buf[0] & 0x0F) * 4
                if ((buf[ihl + 2] << 8) | buf[ihl + 3]) not in self.ports:
                    continue
                self.received += 1
                try:
                    self.out.put_nowait((time.time(), bytes(view[:n])))
                except queue.Full:
                    pass
        finally:
            try:
                sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
            except OSError:
                pass
            sock.close()


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


class _WinDivertBackend:
    """WinDivert surucusu, SNIFF + RECV_ONLY: paketlere dokunmaz, kopyasini okur."""

    name = "WinDivert"

    def __init__(self, out: queue.Queue, log, dll_dir: str):
        self.out = out
        self.log = log
        self.wd = _WinDivert(dll_dir)
        self.received = 0
        self._ports: set[int] = set()
        self._handle = None

    def update(self, endpoints: set[tuple[str, int]]) -> str | None:
        ports = {port for _, port in endpoints}
        if ports == self._ports:
            return None
        self._ports = ports
        clause = " or ".join(f"tcp.DstPort == {p}" for p in sorted(ports))
        handle = self.wd.open(f"tcp and ({clause})".encode(), WINDIVERT_LAYER_NETWORK, 0,
                              WINDIVERT_FLAG_SNIFF | WINDIVERT_FLAG_RECV_ONLY)
        if handle in (None, INVALID_HANDLE):
            self._ports = set()
            err = ctypes.get_last_error()
            hint = ": yönetici olarak çalıştırın" if err == 5 else ""
            return f"WinDivert açılamadı, hata {err}{hint}"
        threading.Thread(target=self._loop, args=(handle,), name="recv", daemon=True).start()
        self._swap(handle)
        return None

    def stop(self) -> None:
        self._ports = set()
        self._swap(None)

    def _swap(self, new) -> None:
        old, self._handle = self._handle, new
        if old is not None:
            self.wd.shutdown(old, WINDIVERT_SHUTDOWN_BOTH)

    def _loop(self, handle) -> None:
        buf = ctypes.create_string_buffer(0xFFFF + 64)
        addr = ctypes.create_string_buffer(128)  # WINDIVERT_ADDRESS (80 bayt)
        got = ctypes.c_uint(0)
        try:
            while True:
                if not self.wd.recv(handle, buf, len(buf), ctypes.byref(got), addr):
                    break  # kapatildi
                self.received += 1
                try:
                    self.out.put_nowait((time.time(), buf.raw[:got.value]))
                except queue.Full:
                    pass
        finally:
            self.wd.close(handle)


class LiveCapture:
    """Oyunun baglantilarini izler ve secilen yontemle dinler."""

    def __init__(self, pipeline: Pipeline, mode: str, dll_dir: str, log=print):
        self.pipeline = pipeline
        self.log = log
        self.queue: queue.Queue = queue.Queue(maxsize=50000)
        if mode == "windivert":
            self.backend = _WinDivertBackend(self.queue, log, dll_dir)
        else:
            self.backend = _RawSocketBackend(self.queue, log)
        self.endpoints: set[tuple[str, int]] = set()
        self.status = "Oyun aranıyor..."
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._watch, name="watch", daemon=True).start()
        threading.Thread(target=self._process, name="process", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.backend.stop()

    def _watch(self) -> None:
        error = None
        last_count, last_change = 0, time.monotonic()
        while not self._stop.is_set():
            try:
                endpoints = find_game_endpoints()
            except Exception as e:  # psutil gecici hatalari
                self.log(f"Baglanti taramasi hatasi: {e}")
                endpoints = self.endpoints
            if endpoints != self.endpoints or error:
                if endpoints != self.endpoints:
                    self.log(f"Oyun baglantilari: {sorted(endpoints) or 'yok'}")
                self.endpoints = endpoints
                if endpoints:
                    new_error = self.backend.update(endpoints)
                    if new_error and new_error != error:
                        self.log(new_error)
                    error = new_error
                else:
                    self.backend.stop()
                    error = None
                last_change = time.monotonic()

            count = self.backend.received
            if count != last_count:
                last_count, last_change = count, time.monotonic()
            if not self.endpoints:
                self.status = "Oyun bulunamadı"
            elif error:
                self.status = error
            elif time.monotonic() - last_change > NO_PACKET_WARNING_SEC:
                self.status = "Oyundan paket gelmiyor: güvenlik duvarı izni gerekebilir (README)"
            else:
                self.status = f"Dinleniyor ({self.backend.name})"
            self._stop.wait(2.0)

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
