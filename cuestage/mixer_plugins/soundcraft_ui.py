"""Plugin CueStage — Soundcraft Ui12 / Ui16 (WebSocket sulla porta 80).

Protocollo come nella libreria open source «soundcraft-ui-connection» (F. Malcher):
  - WebSocket su ws://<ip>/ ; ogni messaggio ha il prefisso «3:::»; keepalive «3:::ALIVE» ogni secondo
  - all'apertura il mixer invia tutto il suo stato, una riga per valore:
        SETD^i.0.mix^0.7647      SETD^i.0.mute^1      SETS^i.0.name^Voce
  - comandi: le stesse righe verso il mixer (SETD^percorso^valore)
  - percorsi (numeri da 0): ingressi «i.N», line in «l.N», player USB «p.N», ritorni FX «f.N», aux «a.N»;
    fader «.mix» (0…1), mute «.mute» (1 = muto); master «m.mix»
  - mute group: maschera di bit «mgmask» (bit 0–5 = gruppi 1–6, bit 22 = MUTE FX, bit 23 = MUTE ALL)
  - legge del fader 0…1 ↔ dB: la stessa funzione della libreria (0 dB ≈ 0,765)
Numero di canali (dalle specifiche, da verificare): Ui16 12 ingressi + 4 aux, Ui12 8 ingressi + 2 aux;
per entrambi 2 line in, 2 player, 4 FX. Il master non ha un mute proprio: «Master» = MUTE ALL.
"""
import base64
import math
import os
import socket
import struct
import threading
import time

from cuestage.mixers import MixerDriver, MixerError, tcp_scan

PORT = 80
LAYOUT = {"ui16": {"ch": 12, "aux": 4}, "ui12": {"ch": 8, "aux": 2}}
PREFIX = {"ch": "i", "st": "l", "ply": "p", "fx": "f", "aux": "a"}
_A = (23.90844819639692, -26.23877598214595, 12.195249692570245, -0.4878099877028098)


def ui_to_db(v):
    v = max(0.0, min(1.0, float(v)))
    amp = (math.sin(28.559933214452666 * v) if v < 0.055 else 1.0) * 2.676529517952372e-4 * \
        math.exp(_A[0] * v + _A[1] * v ** 2 + _A[2] * v ** 3 + _A[3] * v ** 4)
    return -90.0 if amp < 1e-10 else round(max(-90.0, 20 * math.log10(amp)), 2) + 0.0


def db_to_ui(db):
    if db <= -89.9:
        return 0.0
    if db >= 10:
        return 1.0
    lo, hi = 0.0, 1.0                  # funzione crescente: bisezione
    for _ in range(40):
        mid = (lo + hi) / 2
        if ui_to_db(mid) < db:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 6)


class _WebSocket:
    """Client WebSocket minimo (solo testo, senza estensioni)."""

    def __init__(self, host, port, timeout=3.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET / HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise MixerError("connessione chiusa durante l'apertura")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise MixerError("il dispositivo non accetta WebSocket (è un Soundcraft Ui?)")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.lock = threading.Lock()
        self.sock.settimeout(None)

    def send(self, text):
        data = text.encode("utf-8")
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.lock:
            self.sock.sendall(bytes(hdr) + body)

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connessione chiusa")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        """Testo del messaggio successivo (frammenti riuniti); None se chiuso."""
        parts = []
        while True:
            b0, b1 = self._read(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x8:
                return None
            if op == 0x9:                               # ping → pong
                with self.lock:
                    self.sock.sendall(bytes([0x8A, 0x80 | len(data)]) + b"\0\0\0\0" + data)
                continue
            if op in (0x0, 0x1, 0x2):
                parts.append(data)
                if b0 & 0x80:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class SoundcraftUi(MixerDriver):
    ID = "soundcraft_ui"
    NAME = "Soundcraft Ui12 / Ui16"
    DEFAULT_PORT = PORT
    FIELDS = ("host", "port", "model")
    MODELS = [("ui16", "Ui16"), ("ui12", "Ui12")]
    NAMES = True
    NOTES = ("WebSocket sulla porta 80 (la stessa dell'interfaccia web del mixer): basta che il computer sia "
             "sulla rete del mixer. Master = MUTE ALL; mute group 1–6. Ingressi: «Canali»; line in stereo: "
             "«Canali stereo»; player USB; FX 1–4; aux. Numero di canali per modello da verificare.")

    @classmethod
    def sections(cls, conf=None):
        lay = LAYOUT.get((conf or {}).get("model") or "ui16", LAYOUT["ui16"])
        return [("ch", lay["ch"], {"mute", "fader"}), ("st", 2, {"mute", "fader"}), ("ply", 2, {"mute", "fader"}),
                ("fx", 4, {"mute", "fader"}), ("aux", lay["aux"], {"mute", "fader"}),
                ("main", 1, {"mute", "fader"}), ("mgrp", 6, {"mute"})]

    SECTIONS = [("ch", 12, {"mute", "fader"}), ("st", 2, {"mute", "fader"}), ("ply", 2, {"mute", "fader"}),
                ("fx", 4, {"mute", "fader"}), ("aux", 4, {"mute", "fader"}), ("main", 1, {"mute", "fader"}),
                ("mgrp", 6, {"mute"})]

    def __init__(self, conf):
        super().__init__(conf)
        self.ws = None
        self.state = {}
        self.got_state = threading.Event()
        self.alive = False

    # ---------------------------------------------------------------- collegamento
    def _conn(self):
        if self.ws is not None and self.alive:
            return self.ws
        if not self.host:
            raise MixerError("indirizzo IP non impostato")
        self.close()
        try:
            self.ws = _WebSocket(self.host, int(self.port or PORT))
        except OSError as e:
            raise MixerError(f"connessione a {self.host} non riuscita ({e})")
        self.alive = True
        self.got_state.clear()
        threading.Thread(target=self._reader, args=(self.ws,), daemon=True).start()
        threading.Thread(target=self._keepalive, args=(self.ws,), daemon=True).start()
        self.got_state.wait(3.0)               # lo stato iniziale arriva subito dopo l'apertura
        return self.ws

    def _reader(self, ws):
        last = time.monotonic()
        try:
            while True:
                msg = ws.recv()
                if msg is None:
                    break
                for line in msg.split("\n"):
                    if line.startswith("3:::"):
                        line = line[4:]
                    parts = line.split("^")
                    if len(parts) >= 3 and parts[0] in ("SETD", "SETS"):
                        self.state[parts[1]] = "^".join(parts[2:])
                        last = time.monotonic()
                if self.state and not self.got_state.is_set() and time.monotonic() - last > 0.3:
                    self.got_state.set()
                elif len(self.state) > 200:
                    self.got_state.set()
        except (OSError, ConnectionError, ValueError):
            pass
        finally:
            if ws is self.ws:
                self.alive = False
            self.got_state.set()

    def _keepalive(self, ws):
        while ws is self.ws and self.alive:
            try:
                ws.send("3:::ALIVE")
            except OSError:
                self.alive = False
                break
            time.sleep(1.0)

    def _send(self, line):
        ws = self._conn()
        try:
            ws.send("3:::" + line)
        except OSError as e:
            self.alive = False
            raise MixerError(f"invio non riuscito ({e})")

    def _setd(self, path, value):
        self._send(f"SETD^{path}^{value}")
        self.state[path] = str(value)

    def _value(self, path):
        self._conn()
        v = self.state.get(path)
        if v is None:
            raise MixerError(f"valore «{path}» non ricevuto dal mixer")
        return v

    # ---------------------------------------------------------------- percorsi
    @staticmethod
    def _path(kind, n):
        if kind == "main":
            return "m"
        return f"{PREFIX[kind]}.{int(n) - 1}"

    def _mask(self):
        try:
            return int(float(self._value("mgmask")))
        except MixerError:
            return 0

    def _set_bit(self, bit, on):
        m = self._mask()
        m = (m | (1 << bit)) if on else (m & ~(1 << bit))
        self._setd("mgmask", m)

    # ---------------------------------------------------------------- comandi
    def set_mute(self, kind, n, muted):
        self.check(kind, n, "mute")
        if kind == "mgrp":
            self._set_bit(int(n) - 1, muted)
        elif kind == "main":
            self._set_bit(23, muted)                    # MUTE ALL
        else:
            self._setd(self._path(kind, n) + ".mute", 1 if muted else 0)

    def get_mute(self, kind, n):
        self.check(kind, n, "mute")
        if kind == "mgrp":
            return bool(self._mask() >> (int(n) - 1) & 1)
        if kind == "main":
            return bool(self._mask() >> 23 & 1)
        return bool(int(float(self._value(self._path(kind, n) + ".mute"))))

    def get_mutes(self, kind):
        sec = self.section(kind, self.conf)
        out = []
        for i in range(1, sec[1] + 1):
            try:
                out.append(self.get_mute(kind, i))
            except MixerError:
                out.append(None)                  # valore non (ancora) ricevuto: stato sconosciuto
        return out

    def set_fader(self, kind, n, db):
        self.check(kind, n, "fader")
        self._setd(self._path(kind, n) + ".mix", db_to_ui(db))

    def get_fader(self, kind, n):
        self.check(kind, n, "fader")
        return ui_to_db(float(self._value(self._path(kind, n) + ".mix")))

    def get_name(self, kind, n):
        if kind in ("mgrp", "main"):
            return ""
        self._conn()
        return self.state.get(self._path(kind, n) + ".name", "")

    def status(self):
        self._conn()
        return {"valori ricevuti": len(self.state),
                "master": f"{self.get_fader('main', 1):+.1f} dB" if "m.mix" in self.state else "?"}

    def close(self):
        ws, self.ws = self.ws, None
        self.alive = False
        if ws is not None:
            ws.close()

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        out = []
        for h in tcp_scan(local_ips, PORT):
            try:
                s = socket.create_connection((h, PORT), timeout=0.8)
                s.sendall(f"GET / HTTP/1.0\r\nHost: {h}\r\n\r\n".encode())
                s.settimeout(0.8)
                page = b""
                while len(page) < 20000:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    page += chunk
                s.close()
            except OSError:
                continue
            low = page.lower()
            if b"soundcraft" in low or b"ui24" in low or b"ui16" in low or b"ui12" in low:
                model = "ui12" if b"ui12" in low else "ui16"
                out.append({"driver": cls.ID, "host": h, "port": PORT, "name": "", "model": model,
                            "info": "modello da verificare" if b"ui1" not in low else ""})
        return out
