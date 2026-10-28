"""Mixer digitali: driver a plugin, ricerca in rete, mute, fader, fade.

Un driver (plugin in cuestage/mixer_plugins/ o in ~/.local/share/cuestage/mixer_plugins/) estende
MixerDriver e dichiara le SEZIONI del mixer (canali, aux, gruppi, effetti, DCA, mute group…) con il
numero di elementi e cosa si può comandare (mute, fader). I comandi di ogni mixer passano da un suo
thread (in ordine); i driver restano collegati (TCP/UDP/MIDI) tra un comando e l'altro.
"""
from .i18n import tr
import importlib.util
import ipaddress
import os
import socket
import struct
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

from .system import IS_MAC, IS_WIN, data_dir

PLATFORM_KEY = "win" if IS_WIN else "mac" if IS_MAC else "linux"

USER_MIXER_DIR = data_dir("mixer_plugins")
BUILTIN_MIXER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mixer_plugins")

# tipi di sezione: chiave -> etichetta
KIND_LABELS = {
    "ch": tr("Canali"), "st": tr("Canali stereo"), "auxin": tr("Aux in"), "fx": tr("Ritorni effetti"),
    "aux": tr("Aux / Mix"), "grp": tr("Gruppi"), "fxs": tr("Mandate effetti"), "mtx": tr("Matrici"),
    "main": tr("Master"), "dca": "DCA", "mgrp": tr("Mute group"), "ply": tr("Player USB"), "out": tr("Uscite"),
}
KIND_SHORT = {"ch": tr("Ch"), "st": "ST", "auxin": tr("AuxIn"), "fx": "FX", "aux": tr("Aux"), "grp": tr("Grp"), "fxs": tr("FXs"),
              "mtx": tr("Mtx"), "main": tr("Main"), "dca": "DCA", "mgrp": "MG", "ply": tr("Ply"), "out": tr("Out")}
MIN_DB = -90.0


class MixerError(Exception):
    pass


def parse_numbers(text, maximum=None):
    """«1-4, 7» -> [1, 2, 3, 4, 7]; None se non valido."""
    out = []
    for part in str(text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = (int(x) for x in part.split("-", 1))
                rng = range(min(a, b), max(a, b) + 1)
            else:
                rng = [int(part)]
        except ValueError:
            return None
        for n in rng:
            if n >= 1 and (maximum is None or n <= maximum) and n not in out:
                out.append(n)
    return out


def interp(points, x):
    """Interpolazione lineare a tratti; points ordinati per x crescente."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 != x0 else y1
    return points[-1][1]


# ------------------------------------------------------------------ OSC minimale (UDP)

def _pad(b):
    return b + b"\0" * (4 - len(b) % 4)


def osc_encode(address, *args):
    tags, data = ",", b""
    for a in args:
        if isinstance(a, bool):
            a = int(a)
        if isinstance(a, int):
            tags += "i"
            data += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            data += struct.pack(">f", a)
        else:
            tags += "s"
            data += _pad(str(a).encode())
    return _pad(address.encode()) + _pad(tags.encode()) + data


def osc_decode(pkt):
    """(indirizzo, [argomenti]) oppure None."""
    try:
        i = pkt.index(b"\0")
        addr = pkt[:i].decode(errors="replace")
        p = (i + 4) & ~3
        if p >= len(pkt) or pkt[p:p + 1] != b",":
            return addr, []
        j = pkt.index(b"\0", p)
        tags = pkt[p + 1:j].decode()
        p = (j + 4) & ~3
        args = []
        for t in tags:
            if t == "i":
                args.append(struct.unpack(">i", pkt[p:p + 4])[0])
                p += 4
            elif t == "f":
                args.append(struct.unpack(">f", pkt[p:p + 4])[0])
                p += 4
            elif t == "s":
                k = pkt.index(b"\0", p)
                args.append(pkt[p:k].decode(errors="replace"))
                p = (k + 4) & ~3
            elif t == "b":
                n = struct.unpack(">i", pkt[p:p + 4])[0]
                args.append(pkt[p + 4:p + 4 + n])
                p += 4 + ((n + 3) & ~3)
            elif t in "TF":
                args.append(t == "T")
        return addr, args
    except (ValueError, struct.error, UnicodeDecodeError):
        return None


class OscLink:
    """Socket UDP verso un mixer OSC, con richiesta/risposta."""

    def __init__(self, host, port, timeout=0.6):
        self.addr = (host, int(port))
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))

    def send(self, address, *args):
        try:
            self.sock.sendto(osc_encode(address, *args), self.addr)
        except OSError as e:
            raise MixerError(tr("invio a {0}:{1} non riuscito ({2})", self.addr[0], self.addr[1], e))

    def query(self, address, *args, match=None):
        """Invia e attende la risposta con lo stesso indirizzo (o «match»)."""
        want = match or address
        self._drain()
        self.send(address, *args)
        end = time.monotonic() + self.timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise MixerError(tr("nessuna risposta a {0} da {1}", address, self.addr[0]))
            self.sock.settimeout(left)
            try:
                pkt, _ = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError as e:
                raise MixerError(str(e))
            msg = osc_decode(pkt)
            if msg and msg[0] == want:
                return msg[1]

    def _drain(self):
        self.sock.setblocking(False)
        try:
            while True:
                self.sock.recvfrom(65536)
        except (BlockingIOError, OSError):
            pass
        finally:
            self.sock.setblocking(True)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ MIDI su TCP (Allen & Heath)

class NrpnParser:
    """Decodifica un flusso MIDI: NRPN (BN 63 MB, 62 LB, 06 VC, 26 VF), Note On e SysEx.
    Chiama on_nrpn(ch, mb, lb, vc, vf), on_note(ch, note, vel), on_sysex(bytes)."""

    def __init__(self, on_nrpn=None, on_note=None, on_sysex=None):
        self.on_nrpn, self.on_note, self.on_sysex = on_nrpn, on_note, on_sysex
        self.status = 0
        self.data = []
        self.sysex = None
        self.nrpn = {}

    def feed(self, data):
        for b in data:
            if b == 0xF0:
                self.sysex = bytearray([b])
                continue
            if self.sysex is not None:
                self.sysex.append(b)
                if b == 0xF7:
                    if self.on_sysex:
                        self.on_sysex(bytes(self.sysex))
                    self.sysex = None
                continue
            if b >= 0xF8:            # realtime
                continue
            if b & 0x80:
                self.status = b
                self.data = []
                continue
            self.data.append(b)
            st = self.status & 0xF0
            ch = self.status & 0x0F
            if st in (0x90, 0x80, 0xB0, 0xE0) and len(self.data) == 2:
                d1, d2 = self.data
                self.data = []
                if st == 0x90 and self.on_note:
                    self.on_note(ch, d1, d2)
                elif st == 0xB0:
                    s = self.nrpn.setdefault(ch, {})
                    if d1 == 0x63:
                        s.clear()
                        s["mb"] = d2
                    elif d1 == 0x62:
                        s["lb"] = d2
                    elif d1 == 0x06:
                        s["vc"] = d2
                    elif d1 == 0x26:
                        s["vf"] = d2
                        if self.on_nrpn and "mb" in s and "lb" in s:
                            self.on_nrpn(ch, s["mb"], s["lb"], s.get("vc", 0), d2)
            elif st in (0xC0, 0xD0) and len(self.data) == 1:
                self.data = []


class TcpMidiLink:
    """Connessione TCP permanente (MIDI su TCP/IP, es. porta 51325 di Allen & Heath)."""

    def __init__(self, host, port, timeout=1.5):
        self.host, self.port, self.timeout = host, int(port), timeout
        self.sock = None

    def connect(self):
        if self.sock is None:
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            except OSError as e:
                self.sock = None
                raise MixerError(tr("connessione a {0}:{1} non riuscita ({2})", self.host, self.port, e))
        return self.sock

    def send(self, data):
        for attempt in (0, 1):
            s = self.connect()
            try:
                s.sendall(bytes(data))
                return
            except OSError:
                self.close()
                if attempt:
                    raise MixerError(tr("invio a {0}:{1} non riuscito", self.host, self.port))

    def read_for(self, seconds, parser, stop=None):
        """Legge per «seconds» passando i byte al parser; stop(): True per uscire prima."""
        s = self.connect()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if stop and stop():
                return
            s.settimeout(max(0.01, min(0.1, end - time.monotonic())))
            try:
                data = s.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                self.close()
                raise MixerError(tr("connessione interrotta"))
            if not data:
                self.close()
                raise MixerError(tr("il mixer ha chiuso la connessione"))
            parser.feed(data)

    def drain(self):
        if self.sock is None:
            return
        self.sock.setblocking(False)
        try:
            while self.sock.recv(65536):
                pass
        except (BlockingIOError, OSError):
            pass
        finally:
            if self.sock is not None:
                self.sock.setblocking(True)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def nrpn(ch, mb, lb, vc, vf=None, inc=None):
    """Messaggio NRPN: BN 63 MB BN 62 LB BN 06 VC [BN 26 VF] — oppure BN 60 inc (incremento/get)."""
    b = 0xB0 | (ch & 0x0F)
    out = [b, 0x63, mb & 0x7F, b, 0x62, lb & 0x7F]
    if inc is not None:
        return out + [b, 0x60, inc & 0x7F]
    out += [b, 0x06, vc & 0x7F]
    if vf is not None:
        out += [b, 0x26, vf & 0x7F]
    return out


# ------------------------------------------------------------------ driver base

class MixerDriver:
    """Classe base dei driver dei mixer.

    Attributi del plugin:
      ID, NAME            identificativo e nome mostrato
      DEFAULT_PORT        porta di rete (0 per i mixer comandati via MIDI)
      PLATFORMS           sistemi su cui funziona, tra "linux", "win", "mac" (vuoto = tutti)
      FIELDS              campi di configurazione: tra "host", "port", "midi_out", "midi_in", "midi_ch",
                          "model", "fader_law"
      MODELS              facoltativo: [(codice, etichetta)] per il campo "model"
      SECTIONS            [(tipo, quanti, {"mute", "fader"})] — tipo in KIND_LABELS
      NOTES               testo informativo
    Metodi: set_mute(tipo, n, muto), get_mute(tipo, n), set_fader(tipo, n, dB), get_fader(tipo, n),
            e facoltativi status(), get_mutes(tipo) (lettura veloce di una sezione), close().
    classmethod discover(indirizzi_locali, timeout) -> [{"driver", "host", "port", "name", "model", "info"}]
    """
    ID = ""
    NAME = ""
    PLATFORMS = ()
    DEFAULT_PORT = 0
    FIELDS = ("host", "port")
    MODELS = []
    SECTIONS = []
    NOTES = ""

    def __init__(self, conf):
        self.conf = dict(conf)
        self.host = (conf.get("host") or "").strip()
        self.port = int(conf.get("port") or self.DEFAULT_PORT or 0)

    @classmethod
    def sections(cls, conf=None):
        """Sezioni effettive (un driver può variarle in base al modello)."""
        return list(cls.SECTIONS)

    @classmethod
    def section(cls, kind, conf=None):
        for k, n, caps in cls.sections(conf):
            if k == kind:
                return k, n, caps
        return None

    def check(self, kind, n, what):
        sec = self.section(kind, self.conf)
        if sec is None or what not in sec[2]:
            raise MixerError(tr("{0}: {1} non disponibile su {2}", KIND_LABELS.get(kind, kind), what, self.NAME))
        if not 1 <= int(n) <= sec[1]:
            raise MixerError(tr("{0} {1} fuori intervallo (1–{2})", KIND_LABELS.get(kind, kind), n, sec[1]))

    def set_mute(self, kind, n, muted):
        raise MixerError(tr("non supportato"))

    def get_mute(self, kind, n):
        raise MixerError(tr("lettura non supportata"))

    def get_mutes(self, kind):
        sec = self.section(kind, self.conf)
        return [self.get_mute(kind, i) for i in range(1, sec[1] + 1)]

    # nomi dei canali: i driver che li sanno leggere mettono NAMES = True e implementano get_name
    NAMES = False
    # controlli generici (schede audio: interruttori, menu, volumi): CONTROLS = True e i metodi qui sotto
    CONTROLS = False

    def list_controls(self):
        """[dict(key, name, type «int»/«bool»/«enum», min, max, items, db, value, writable, group)]"""
        raise MixerError(tr("controlli non supportati"))

    def set_control(self, key, value):
        raise MixerError(tr("controlli non supportati"))

    def get_name(self, kind, n):
        raise MixerError(tr("lettura dei nomi non supportata"))

    def get_names(self, kind):
        sec = self.section(kind, self.conf)
        out, fails = [], 0
        for i in range(1, sec[1] + 1):
            try:
                out.append((self.get_name(kind, i) or "").strip())
                fails = 0
            except MixerError:
                fails += 1
                if fails >= 2 and not any(out):       # il mixer non risponde: inutile insistere
                    raise
                out.append("")
        return out

    def set_fader(self, kind, n, db):
        raise MixerError(tr("non supportato"))

    def get_fader(self, kind, n):
        raise MixerError(tr("lettura non supportata"))

    def status(self):
        return {"collegato": tr("sì")}

    def close(self):
        pass

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        return []


def broadcast_addresses(local_ips):
    out = {"255.255.255.255"}
    for ip in local_ips:
        try:
            net = ipaddress.ip_network(f"{ip}/24", strict=False)
            out.add(str(net.broadcast_address))
        except ValueError:
            pass
    return sorted(out)


def udp_discover(local_ips, port, payload, parse, timeout=1.5):
    """Invia «payload» in broadcast sulla porta e raccoglie le risposte: parse(dati, ip) -> dict o None."""
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("0.0.0.0", 0))
        for b in broadcast_addresses(local_ips) + list(local_ips):
            try:
                s.sendto(payload, (b, port))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            s.settimeout(max(0.05, end - time.monotonic()))
            try:
                data, (ip, _p) = s.recvfrom(65536)
            except socket.timeout:
                break
            except OSError:
                break
            try:
                r = parse(data, ip)
            except Exception:
                r = None
            if r:
                found[r.get("host") or ip] = r
    finally:
        s.close()
    return list(found.values())


_scan_cache = {}
_scan_lock = threading.Lock()


def tcp_scan(local_ips, port, timeout=0.4, workers=64):
    """IP delle reti /24 locali con la porta TCP aperta (risultato riusato per 10 s: più driver possono
    cercare sulla stessa porta, es. Allen & Heath Qu e SQ)."""
    key = (tuple(sorted(local_ips)), int(port))
    with _scan_lock:
        hit = _scan_cache.get(key)
        if hit and time.monotonic() - hit[0] < 10:
            return list(hit[1])
        res = _tcp_scan(local_ips, port, timeout, workers)
        _scan_cache[key] = (time.monotonic(), res)
        return list(res)


def _tcp_scan(local_ips, port, timeout=0.4, workers=64):
    hosts = []
    for ip in local_ips:
        try:
            net = ipaddress.ip_network(f"{ip}/24", strict=False)
        except ValueError:
            continue
        hosts += [str(h) for h in net.hosts() if str(h) != ip]
    hosts = list(dict.fromkeys(hosts))[:2048]
    open_hosts = []
    lock = threading.Lock()

    def probe(h):
        try:
            with socket.create_connection((h, port), timeout=timeout):
                with lock:
                    open_hosts.append(h)
        except OSError:
            pass
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(probe, hosts))
    return sorted(open_hosts, key=lambda x: tuple(int(p) for p in x.split(".")))


# ------------------------------------------------------------------ registro plugin

class MixerRegistry:
    def __init__(self):
        self.drivers = {}
        self.errors = []
        self.sources = {}

    def load(self):
        self.drivers, self.sources, self.errors = {}, {}, []
        for d in (BUILTIN_MIXER_DIR, USER_MIXER_DIR):
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".py") or fn.startswith("_"):
                    continue
                path = os.path.join(d, fn)
                try:
                    self._load_file(path)
                except Exception as e:
                    self.errors.append(f"{fn}: {e}")
                    traceback.print_exc()
        return self

    def _load_file(self, path):
        name = "cuestage_mixer_" + os.path.splitext(os.path.basename(path))[0]
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        found = 0
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, MixerDriver) and obj is not MixerDriver \
                    and obj.ID and obj.__module__ == name:
                found += 1
                if obj.PLATFORMS and PLATFORM_KEY not in obj.PLATFORMS:
                    continue                 # es. schede audio ALSA: solo Linux
                bad = [k for k, _n, _c in obj.SECTIONS if k not in KIND_LABELS]
                if bad:
                    raise MixerError(tr("tipi di sezione sconosciuti: {0}", ', '.join(bad)))
                self.drivers[obj.ID] = obj
                self.sources[obj.ID] = path
        if not found:
            raise MixerError(tr("nessuna classe MixerDriver con ID trovata"))

    def get(self, driver_id):
        return self.drivers.get(driver_id)


# ------------------------------------------------------------------ manager

class MixerManager(QObject):
    """Un thread per mixer (comandi in ordine) e un driver collegato per mixer."""
    finished = Signal(str, str, bool, str)       # nome mixer, descrizione, riuscito, messaggio
    namesChanged = Signal(str)                   # id mixer: nomi dei canali letti
    controlsChanged = Signal(str)                # id mixer/scheda: elenco dei controlli letto

    def __init__(self):
        super().__init__()
        self.registry = MixerRegistry().load()
        self.mixers = []
        self._pools = {}
        self._drivers = {}
        self._fades = {}          # (id, tipo, n) -> evento di stop del fade in corso
        self.names = {}           # id -> {sezione: [nome canale 1, 2, …]}
        self.controls = {}        # id -> [dict del controllo] (schede audio)
        self._names_tried = set()
        self._lock = threading.Lock()

    def reload_plugins(self):
        self.registry.load()

    def set_mixers(self, lst):
        new = [dict(m) for m in (lst or [])]
        old = {m.get("id"): m for m in self.mixers}
        for m in new:                     # configurazione cambiata: il driver si ricollega
            if old.get(m.get("id")) != m:
                self._drop_driver(m.get("id"))
        for mid in set(old) - {m.get("id") for m in new}:
            self._drop_driver(mid)
        self.mixers = new

    def _drop_driver(self, mid):
        drv = self._drivers.pop(mid, None)
        if drv is not None:
            pool = self._pools.get(mid)
            if pool is not None:
                pool.submit(drv.close)
            else:
                drv.close()

    def find(self, mid):
        for m in self.mixers:
            if m.get("id") == mid:
                return m
        return None

    def cls_for(self, conf):
        return self.registry.get((conf or {}).get("driver"))

    def _driver(self, mid):
        drv = self._drivers.get(mid)
        if drv is None:
            conf = self.find(mid)
            if conf is None:
                raise MixerError(tr("mixer non configurato"))
            cls = self.cls_for(conf)
            if cls is None:
                raise MixerError(tr("driver «{0}» non disponibile", conf.get('driver')))
            drv = self._drivers[mid] = cls(conf)
        return drv

    def _pool(self, mid):
        with self._lock:
            p = self._pools.get(mid)
            if p is None:
                p = self._pools[mid] = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"mixer-{mid}")
            return p

    def name(self, mid):
        conf = self.find(mid) or {}
        return conf.get("name") or conf.get("host") or mid

    def submit(self, mid, fn, label, report=True):
        """Esegue fn(driver) nel thread del mixer; il risultato va in «finished»."""
        def job():
            t0 = time.monotonic()
            try:
                res = fn(self._driver(mid))
                if report:
                    msg = tr("ok ({0:.2f} s)", time.monotonic() - t0) + (f": {res}" if isinstance(res, str) and res else "")
                    self.finished.emit(self.name(mid), label, True, msg)
                return res
            except MixerError as e:
                if report:
                    self.finished.emit(self.name(mid), label, False, str(e))
                raise
            except Exception as e:
                traceback.print_exc()
                self._drop_driver(mid)
                if report:
                    self.finished.emit(self.name(mid), label, False, tr("errore nel driver: {0}", e))
                raise MixerError(str(e))
        return self._pool(mid).submit(job)

    # ---- comandi
    def mute(self, mid, kind, numbers, muted, report=True):
        nums = list(numbers)
        lab = f"{KIND_SHORT.get(kind, kind)} {fmt_numbers(nums)} {'MUTE' if muted else 'on'}"
        for n in nums:
            self._stop_fade(mid, kind, n)
        return self.submit(mid, lambda d: [d.set_mute(kind, n, muted) for n in nums] and "", lab, report)

    def fader(self, mid, kind, n, db, fade=0.0, report=True):
        """Porta il fader a «db»; con fade > 0 legge la posizione attuale e ci arriva gradualmente."""
        lab = f"{KIND_SHORT.get(kind, kind)} {n} → {fmt_db(db)}" + (tr(" in {0:.1f} s", fade) if fade > 0 else "")
        self._stop_fade(mid, kind, n)
        if fade <= 0:
            return self.submit(mid, lambda d: d.set_fader(kind, n, db), lab, report)
        stop = threading.Event()
        self._fades[(mid, kind, n)] = stop
        steps_per_s = 25.0

        def run():
            t0 = time.monotonic()
            try:
                start = self.submit(mid, lambda d: d.get_fader(kind, n), "", report=False).result(timeout=5)
            except Exception:
                start = None
            if start is None:          # posizione sconosciuta: si va direttamente al valore
                self.submit(mid, lambda d: d.set_fader(kind, n, db), lab, report)
                return
            a = max(MIN_DB, float(start))
            b = max(MIN_DB, float(db))
            t_start = time.monotonic()
            while not stop.is_set():
                x = min(1.0, (time.monotonic() - t_start) / fade)
                # interpolazione in dB con curva a S: suona naturale anche nelle dissolvenze lunghe
                s = x * x * (3 - 2 * x)
                v = a + (b - a) * s
                if x >= 1.0:
                    v = db
                fut = self.submit(mid, lambda d, v=v: d.set_fader(kind, n, v), "", report=False)
                try:
                    fut.result(timeout=2)
                except Exception as e:
                    self.finished.emit(self.name(mid), lab, False, str(e))
                    return
                if x >= 1.0:
                    self.finished.emit(self.name(mid), lab, True, tr("ok ({0:.1f} s)", time.monotonic() - t0))
                    break
                stop.wait(1.0 / steps_per_s)
            if self._fades.get((mid, kind, n)) is stop:
                self._fades.pop((mid, kind, n), None)
        threading.Thread(target=run, daemon=True, name=f"fade-{mid}-{kind}{n}").start()
        return stop

    def _stop_fade(self, mid, kind, n):
        ev = self._fades.pop((mid, kind, n), None)
        if ev is not None:
            ev.set()

    def stop_fades(self):
        for ev in list(self._fades.values()):
            ev.set()
        self._fades.clear()

    def read_fader(self, mid, kind, n, callback):
        """callback(ok, dB|testo) dal thread del mixer."""
        def job(d):
            return d.get_fader(kind, n)
        fut = self.submit(mid, job, "", report=False)
        fut.add_done_callback(lambda f: callback(*(_result(f))))
        return fut

    # ------------------------------------------------------------- nomi dei canali
    def supports_names(self, mid):
        cls = self.cls_for(self.find(mid))
        return bool(cls and cls.NAMES)

    def channel_name(self, mid, kind, n):
        lst = (self.names.get(mid) or {}).get(kind) or []
        return lst[n - 1] if 0 < n <= len(lst) else ""

    def load_names(self, data):
        """Nomi salvati nello show (visibili anche senza mixer collegato)."""
        for mid, d in (data or {}).items():
            if isinstance(d, dict):
                self.names[mid] = {k: [str(x) for x in v] for k, v in d.items() if isinstance(v, list)}

    def read_names(self, mid, callback=None):
        """Legge i nomi di tutte le sezioni; al termine emette namesChanged(mid)."""
        conf = self.find(mid)
        cls = self.cls_for(conf)
        if not (cls and cls.NAMES):
            if callback:
                callback(False, tr("questo mixer non fornisce i nomi dei canali"))
            return None
        self._names_tried.add(mid)

        def job(d):
            out = {}
            for kind, _n, _caps in cls.sections(conf):
                try:
                    out[kind] = d.get_names(kind)
                except MixerError:
                    if not out:
                        raise
            return out

        def done(f):
            ok, res = _result(f)
            if ok and isinstance(res, dict) and res:
                self.names[mid] = res
                self.namesChanged.emit(mid)
            if callback:
                callback(ok, res)
        fut = self.submit(mid, job, "", report=False)
        fut.add_done_callback(done)
        return fut

    # ------------------------------------------------------------- controlli (schede audio)
    def supports_controls(self, mid):
        cls = self.cls_for(self.find(mid))
        return bool(cls and cls.CONTROLS)

    def control_info(self, mid, key):
        for c in self.controls.get(mid) or []:
            if c.get("key") == key:
                return c
        return None

    def read_controls(self, mid, callback=None):
        if not self.supports_controls(mid):
            if callback:
                callback(False, tr("nessun controllo disponibile"))
            return None

        def done(f):
            ok, res = _result(f)
            if ok and isinstance(res, list):
                self.controls[mid] = res
                self.controlsChanged.emit(mid)
            if callback:
                callback(ok, res)
        fut = self.submit(mid, lambda d: d.list_controls(), "", report=False)
        fut.add_done_callback(done)
        return fut

    def ensure_controls(self, mid):
        if mid and mid not in self.controls and self.supports_controls(mid):
            self.controls[mid] = []          # una sola lettura automatica
            self.read_controls(mid)

    def set_control(self, mid, key, value, report=True):
        info = self.control_info(mid, key)
        lab = f"{key} = {value}"
        if info is not None:
            info["value"] = value
        return self.submit(mid, lambda d: d.set_control(key, value), lab, report)

    def ensure_names(self, mid):
        """Prima lettura automatica (una volta per sessione) se il mixer sa fornire i nomi."""
        if mid and mid not in self._names_tried and self.supports_names(mid):
            self.read_names(mid)

    def read_mutes(self, mid, kind, callback):
        fut = self.submit(mid, lambda d: d.get_mutes(kind), "", report=False)
        fut.add_done_callback(lambda f: callback(*(_result(f))))
        return fut

    def test(self, conf, callback):
        def job():
            cls = self.cls_for(conf)
            if cls is None:
                callback(False, tr("driver non disponibile"))
                return
            d = cls(conf)
            try:
                st = d.status()
                callback(True, "\n".join(f"{k}: {v}" for k, v in st.items()))
            except Exception as e:
                callback(False, str(e))
            finally:
                d.close()
        threading.Thread(target=job, daemon=True).start()

    def discover(self, callback, local_ips=None):
        """Cerca i mixer in rete con tutti i driver; callback(lista) dal thread."""
        from .webserver import local_addresses
        allips = local_addresses()
        ips = local_ips or [ip for ip in allips if not ip.startswith("127.")] or allips

        def job():
            found = []
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(cls.discover, ips): cls for cls in self.registry.drivers.values()}
                for f, cls in futs.items():
                    try:
                        for r in f.result() or []:
                            r.setdefault("driver", cls.ID)
                            found.append(r)
                    except Exception as e:
                        traceback.print_exc()
                        found.append({"error": f"{cls.NAME}: {e}"})
            # un indirizzo riconosciuto da più driver: tiene il più specifico (quello con il modello)
            out = {}
            for r in found:
                key = (r.get("host"), r.get("port"), r.get("driver"), r.get("card"))
                out[key] = r
            callback(list(out.values()), ips)
        threading.Thread(target=job, daemon=True, name="mixer-discovery").start()

    def shutdown(self):
        self.stop_fades()
        for mid in list(self._drivers):
            self._drop_driver(mid)
        for p in self._pools.values():
            p.shutdown(wait=False, cancel_futures=True)


# ------------------------------------------------------------------ utilità condivise dai plugin
def x32_to_db(f):
    """Legge del fader Behringer X32 / M32 / X Air: float 0…1 → dB (quattro tratti lineari)."""
    f = float(f)
    if f <= 0:
        return -90.0
    if f >= 0.5:
        return f * 40 - 30
    if f >= 0.25:
        return f * 80 - 50
    if f >= 0.0625:
        return f * 160 - 70
    return f * 480 - 90


def db_to_x32(db):
    db = float(db)
    if db <= -90:
        return 0.0
    if db >= -10:
        f = (db + 30) / 40
    elif db >= -30:
        f = (db + 50) / 80
    elif db >= -60:
        f = (db + 70) / 160
    else:
        f = (db + 90) / 480
    f = max(0.0, min(1.0, f))
    return round(f * 1023) / 1023          # il mixer ha 1024 passi


def resolve_midi_port(name, names):
    """Nome completo della porta MIDI: uguale, uguale senza i numeri ALSA, oppure che inizia così (es. «UM-ONE»)."""
    from .remote import port_key
    if not name:
        return None
    if name in names:
        return name
    for n in names:
        if port_key(n) == port_key(name):
            return n
    for n in names:                       # nome parziale, es. «UM-ONE»
        if port_key(n).startswith(port_key(name)):
            return n
    return None


def _result(fut):
    try:
        return True, fut.result()
    except Exception as e:
        return False, str(e)


def fmt_db(db):
    return tr("-∞ dB") if db is None or db <= MIN_DB + 0.01 else f"{db:+.1f} dB"


def mute_states(cue):
    """Stati di mute della cue: {sezione: {numero: True = mute / False = attivo}}.
    I canali non elencati non vengono toccati."""
    out = {}
    raw = cue.mix_states if isinstance(cue.mix_states, dict) else {}
    for kind, d in raw.items():
        if not isinstance(d, dict):
            continue
        st = {}
        for n in parse_numbers(d.get("on", "")) or []:
            st[n] = False
        for n in parse_numbers(d.get("mute", "")) or []:
            st[n] = True
        if st:
            out[kind] = st
    return out


def migrate_mute_cue(cue):
    """Cue mixer di versioni precedenti (mute/unmute su «sezione + canali») → stati a scena."""
    if cue.type != "mixer" or cue.mix_action not in ("mute", "unmute") or cue.mix_states:
        return
    nums = parse_numbers(cue.mix_channels) or []
    if nums:
        store_mute_states(cue, {cue.mix_kind or "ch": {n: cue.mix_action == "mute" for n in nums}})
    cue.mix_action = "mute"


def store_mute_states(cue, states):
    """Scrive gli stati nella cue (azione «mute» a scena)."""
    data = {}
    for kind, st in states.items():
        m = [n for n, v in st.items() if v]
        o = [n for n, v in st.items() if not v]
        if m or o:
            data[kind] = {k: fmt_numbers(v) for k, v in (("mute", m), ("on", o)) if v}
    cue.mix_states = data
    cue.mix_action = "mute"


def mute_summary(states, order=None):
    """«MUTE Ch 1-4, DCA 2 · ON Ch 7»."""
    kinds = sorted(states, key=lambda k: (order or list(KIND_SHORT)).index(k)
                   if k in (order or list(KIND_SHORT)) else 99)
    parts = []
    for flag, lab in ((True, "MUTE"), (False, "ON")):
        items = [f"{KIND_SHORT.get(k, k)} {fmt_numbers([n for n, v in states[k].items() if v is flag])}"
                 for k in kinds if any(v is flag for v in states[k].values())]
        if items:
            parts.append(f"{lab} " + ", ".join(items))
    return " · ".join(parts) or tr("nessun cambiamento")


def fmt_numbers(nums):
    """[1,2,3,7] -> «1-3, 7»."""
    nums = sorted(set(int(n) for n in nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(out)
