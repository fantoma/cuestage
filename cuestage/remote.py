"""Controllo remoto: ricezione/invio OSC e ricezione MIDI."""
import shlex
import re
import threading

from PySide6.QtCore import QObject, Signal

try:
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer
    from pythonosc.udp_client import SimpleUDPClient
except Exception:  # pragma: no cover
    Dispatcher = ThreadingOSCUDPServer = SimpleUDPClient = None

try:
    import mido
except Exception:  # pragma: no cover
    mido = None


# ------------------------------------------------------------------ OSC

def parse_osc_args(text):
    """'1 2.5 ciao "due parole" true' -> [1, 2.5, 'ciao', 'due parole', True]"""
    out = []
    try:
        tokens = shlex.split(text or "")
    except ValueError:
        tokens = (text or "").split()
    for t in tokens:
        low = t.lower()
        if low in ("true", "false"):
            out.append(low == "true")
            continue
        try:
            out.append(int(t))
            continue
        except ValueError:
            pass
        try:
            out.append(float(t))
            continue
        except ValueError:
            pass
        out.append(t)
    return out


_clients = {}


def osc_send(host, port, address, args_text):
    if SimpleUDPClient is None:
        raise RuntimeError("python-osc non installato")
    if not address.startswith("/"):
        address = "/" + address
    key = (host, int(port))
    cl = _clients.get(key)
    if cl is None:
        cl = _clients[key] = SimpleUDPClient(host, int(port), allow_broadcast=True)
    args = parse_osc_args(args_text)
    cl.send_message(address, args if args else [])


class OscServer(QObject):
    message = Signal(str, list)
    status = Signal(str)

    def __init__(self):
        super().__init__()
        self.server = None
        self.thread = None
        self.port = None

    def start(self, port):
        self.stop()
        if ThreadingOSCUDPServer is None:
            self.status.emit("OSC: python-osc non installato")
            return False
        disp = Dispatcher()
        disp.set_default_handler(self._handle)
        try:
            self.server = ThreadingOSCUDPServer(("0.0.0.0", int(port)), disp)
        except OSError as e:
            self.server = None
            self.status.emit(f"OSC: porta {port} non disponibile ({e})")
            return False
        self.port = int(port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.status.emit(f"OSC in ascolto su UDP {port}")
        return True

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.port = None

    def _handle(self, address, *args):
        self.message.emit(address, list(args))


# ------------------------------------------------------------------ MIDI

def midi_spec(msg):
    """Converte un messaggio mido in stringa normalizzata (o None)."""
    t = msg.type
    if t == "note_on" and msg.velocity > 0:
        return f"note {msg.channel + 1} {msg.note}"
    if t == "control_change" and msg.value > 0:
        return f"cc {msg.channel + 1} {msg.control}"
    if t == "program_change":
        return f"pc {msg.channel + 1} {msg.program}"
    return None


def midi_control(msg):
    """Controlli continui: (spec, valore 0..1) oppure None.
    cc = control change (fader, potenziometri) · pb = pitch bend (fader motorizzati, 14 bit) ·
    pc «*» = program change usato come valore (numero del programma 0..127)."""
    t = msg.type
    if t == "control_change":
        return f"cc {msg.channel + 1} {msg.control}", msg.value / 127.0
    if t == "pitchwheel":
        return f"pb {msg.channel + 1} 0", (msg.pitch + 8192) / 16383.0
    if t == "program_change":
        return f"pc {msg.channel + 1} *", msg.program / 127.0
    return None


def port_key(name):
    """Nome della porta senza i numeri di client ALSA finali («… 20:0»), che cambiano tra un avvio e
    l'altro: così una scheda viene riconosciuta anche se riceve un numero diverso."""
    return re.sub(r"\s+\d+:\d+$", "", (name or "").strip()).lower()


def port_label(name):
    """Nome leggibile e stabile da salvare (senza i numeri di client ALSA)."""
    return re.sub(r"\s+\d+:\d+$", "", (name or "").strip())


def port_short(name):
    """Forma breve per l'elenco: «Scarlett 18i20 USB:Scarlett 18i20 USB MIDI 1» -> «Scarlett 18i20 USB MIDI 1»."""
    n = port_label(name)
    if ":" in n:
        client, port = n.split(":", 1)
        if port.lower().startswith(client.lower()) or client.lower() in port.lower():
            return port.strip()
    return n


def split_port(s):
    """«cc 1 7 @ nanoKONTROL2» -> ("cc 1 7", "nanoKONTROL2"); senza «@» la porta è ""."""
    s = s or ""
    if "@" in s:
        a, p = s.split("@", 1)
        return a.strip(), p.strip()
    return s.strip(), ""


def _join(spec, port):
    return f"{spec} @ {port}" if spec and port else spec


def port_matches(want, got):
    return not want or port_key(want) == port_key(got) or port_key(got).startswith(port_key(want))


def normalize_control(s):
    """«cc 1 7», «pb 1 0», «pc 1 *» (con «@ scheda» facoltativo) -> forma normalizzata, "" se non valida."""
    body, port = split_port(s)
    parts = body.lower().split()
    if len(parts) == 2 and parts[0] == "pb":
        parts.append("0")
    if len(parts) != 3 or parts[0] not in ("cc", "pb", "pc"):
        return ""
    try:
        ch = int(parts[1])
        num = "*" if parts[0] == "pc" else str(int(parts[2]) if parts[0] == "cc" else 0)
    except ValueError:
        return ""
    return _join(f"{parts[0]} {ch} {num}", port)


def control_matches(spec, incoming, port=""):
    """La scheda indicata dopo «@» deve coincidere con quella da cui arriva il messaggio."""
    a, b = normalize_control(spec), normalize_control(incoming)
    if not a or not b:
        return False
    a, want = split_port(a)
    b, _ = split_port(b)
    ka, ca, na = a.split()
    kb, cb, nb = b.split()
    return ka == kb and na == nb and (ca == "0" or ca == cb) and port_matches(want, port)


def normalize_spec(s):
    body, port = split_port(s)
    parts = body.lower().split()
    if len(parts) != 3 or parts[0] not in ("note", "cc", "pc"):
        return ""
    try:
        ch, num = int(parts[1]), int(parts[2])
    except ValueError:
        return ""
    return _join(f"{parts[0]} {ch} {num}", port)


def spec_matches(spec, incoming, port=""):
    """Il canale 0 nella configurazione significa 'qualsiasi canale'; «@ scheda» limita a una scheda."""
    a, b = normalize_spec(spec), normalize_spec(incoming)
    if not a or not b:
        return False
    a, want = split_port(a)
    b, _ = split_port(b)
    ka, ca, na = a.split()
    kb, cb, nb = b.split()
    return ka == kb and na == nb and (ca == "0" or ca == cb) and port_matches(want, port)


def parse_ranges(text):
    """«1, 10, 13-16» -> set di interi; "" o «tutti» -> None per «nessun limite»; testo non valido -> None."""
    t = (text or "").strip().lower()
    if not t or t in ("tutti", "tutte", "all", "nessuno", "nessuna"):
        return set()
    out = set()
    for part in t.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = (int(x) for x in part.split("-", 1))
                out.update(range(min(a, b), max(a, b) + 1))
            else:
                out.add(int(part))
        except ValueError:
            return None
    return out


def default_filter():
    return {"types": ["note", "cc", "pc", "pb"], "channels": "", "ignore_cc": "", "ignore_notes": "",
            "no_learn": False}


class MidiFilter:
    """Filtro di una scheda: tipi di messaggio, canali, CC e note da ignorare, esclusione da «Impara»."""

    def __init__(self, conf):
        f = default_filter()
        f.update(conf or {})
        self.types = set(f["types"])
        self.channels = parse_ranges(f["channels"]) or set()      # vuoto = tutti
        self.ignore_cc = parse_ranges(f["ignore_cc"]) or set()
        self.ignore_notes = parse_ranges(f["ignore_notes"]) or set()
        self.no_learn = bool(f["no_learn"])

    def accepts(self, msg):
        t = msg.type
        kind = {"note_on": "note", "note_off": "note", "control_change": "cc", "program_change": "pc",
                "pitchwheel": "pb"}.get(t)
        if kind is None:
            return True                # altri messaggi: CueStage li ignora comunque
        if kind not in self.types:
            return False
        ch = getattr(msg, "channel", None)
        if self.channels and ch is not None and ch + 1 not in self.channels:
            return False
        if kind == "cc" and msg.control in self.ignore_cc:
            return False
        if kind == "note" and msg.note in self.ignore_notes:
            return False
        return True


class MidiInput(QObject):
    """Una o più porte MIDI in ingresso, aperte insieme. I messaggi portano il nome della porta."""
    message = Signal(str, str)          # spec normalizzata («note 1 60»), porta
    control = Signal(str, float, str)   # controllo continuo: spec, valore 0..1, porta
    status = Signal(str)

    def __init__(self):
        super().__init__()
        self.ports = {}      # nome -> porta mido aperta
        self.filters = {}    # port_key -> MidiFilter
        self.learning = False

    def set_filters(self, conf):
        self.filters = {port_key(k): MidiFilter(v) for k, v in (conf or {}).items()}

    def _filter(self, port_name):
        return self.filters.get(port_key(port_name))

    @property
    def port(self):          # compatibilità: una porta qualsiasi aperta (o None)
        return next(iter(self.ports.values()), None)

    @staticmethod
    def list_ports():
        if mido is None:
            return []
        try:
            return mido.get_input_names()
        except Exception:
            return []

    @classmethod
    def resolve(cls, name, available=None):
        """Porta disponibile corrispondente a «name» (anche se è cambiato il numero di client ALSA)."""
        available = cls.list_ports() if available is None else available
        if name in available:
            return name
        for a in available:
            if port_key(a) == port_key(name):
                return a
        return None

    def open(self, names):
        """Apre le porte indicate (lista di nomi, o un nome) e chiude le altre."""
        if isinstance(names, str):
            names = [names] if names else []
        names = [n for n in (names or []) if n]
        self.close()
        if not names:
            self.status.emit("MIDI: nessuna porta selezionata")
            return False
        if mido is None:
            self.status.emit("MIDI: mido/python-rtmidi non installati")
            return False
        available = self.list_ports()
        ok, bad = [], []
        for n in names:
            real = self.resolve(n, available)
            if real is None or real in self.ports:
                if real is None:
                    bad.append(f"{n} (non collegata)")
                continue
            try:
                self.ports[real] = mido.open_input(real, callback=lambda msg, real=real: self._cb(msg, real))
                ok.append(real)
            except Exception as e:
                bad.append(f"{n} ({e})")
        msg = ("MIDI in: " + ", ".join(ok)) if ok else "MIDI: nessuna porta aperta"
        if bad:
            msg += " · non aperte: " + ", ".join(bad)
        self.status.emit(msg)
        return bool(ok)

    def close(self):
        for p in list(self.ports.values()):
            try:
                p.close()
            except Exception:
                pass
        self.ports = {}

    def _cb(self, msg, port_name=""):
        f = self._filter(port_name)
        if f is not None and (not f.accepts(msg) or (self.learning and f.no_learn)):
            return
        s = midi_spec(msg)
        if s:
            self.message.emit(s, port_name)
        c = midi_control(msg)
        if c:
            self.control.emit(c[0], float(c[1]), port_name)


# ------------------------------------------------------------------ MIDI OUT

MIDI_OUT_HELP = """Un messaggio per riga (canali 1–16, valori 0–127; # = commento):
  note 1 60 100        Note On (canale, nota, velocità) — la nota anche come nome: C4 = 60, F#3, Bb2
  off 1 60             Note Off
  cc 1 7 127           Control Change (canale, controller, valore)
  pc 1 5               Program Change (canale, programma 0–127)
  pb 1 0               Pitch Bend (canale, −8192…8191)
  at 1 64              Aftertouch di canale
  nrpn 1 300 8000      NRPN (canale, parametro 0–16383, valore 0–16383)
  msc 1 go 12.5        MIDI Show Control (device 0–127 / all; comandi go, stop, resume, load, fire, reset,
                       all_off; cue e lista facoltativi: msc all go 5 2)
  sysex F0 43 10 3E … F7   System Exclusive (byte esadecimali)
  hex 90 3C 64         byte grezzi esadecimali
  wait 0.5             attende 0,5 s prima del messaggio successivo"""

_NOTE_NAMES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
_MSC_CMDS = {"go": 0x01, "stop": 0x02, "resume": 0x03, "timed_go": 0x04, "load": 0x05, "set": 0x06,
             "fire": 0x07, "all_off": 0x08, "restore": 0x09, "reset": 0x0A, "go_off": 0x0B}


def _note(tok):
    t = tok.strip().lower()
    if re.fullmatch(r"\d+", t):
        return int(t)
    m = re.fullmatch(r"([a-g])([#b]?)(-?\d+)", t)
    if not m:
        raise ValueError(f"nota non valida «{tok}»")
    n = _NOTE_NAMES[m.group(1)] + (1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0)
    return (int(m.group(3)) + 1) * 12 + n            # C4 = 60


def _rng(v, lo, hi, what):
    v = int(v)
    if not lo <= v <= hi:
        raise ValueError(f"{what} fuori intervallo ({lo}–{hi}): {v}")
    return v


def parse_midi_script(text):
    """Testo della cue -> [(attesa_prima_s, [byte])]. Solleva ValueError con il numero di riga."""
    out = []
    wait = 0.0
    for ln, raw in enumerate((text or "").splitlines(), start=1):
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()      # commento: «#» a inizio riga o dopo uno spazio
        if not line:
            continue
        t = line.split()
        k = t[0].lower()
        try:
            if k == "wait":
                wait += float(t[1].replace(",", "."))
                continue
            if k in ("note", "on", "off"):
                ch = _rng(t[1], 1, 16, "canale") - 1
                note = _rng(_note(t[2]), 0, 127, "nota")
                vel = _rng(t[3], 0, 127, "velocità") if len(t) > 3 else (100 if k != "off" else 0)
                msg = [(0x80 if k == "off" else 0x90) | ch, note, vel]
            elif k == "cc":
                msg = [0xB0 | (_rng(t[1], 1, 16, "canale") - 1), _rng(t[2], 0, 127, "controller"),
                       _rng(t[3], 0, 127, "valore")]
            elif k == "pc":
                msg = [0xC0 | (_rng(t[1], 1, 16, "canale") - 1), _rng(t[2], 0, 127, "programma")]
            elif k == "pb":
                v = _rng(t[2], -8192, 8191, "pitch bend") + 8192
                msg = [0xE0 | (_rng(t[1], 1, 16, "canale") - 1), v & 0x7F, v >> 7]
            elif k == "at":
                msg = [0xD0 | (_rng(t[1], 1, 16, "canale") - 1), _rng(t[2], 0, 127, "valore")]
            elif k == "nrpn":
                b = 0xB0 | (_rng(t[1], 1, 16, "canale") - 1)
                p = _rng(t[2], 0, 16383, "parametro")
                v = _rng(t[3], 0, 16383, "valore")
                msg = [b, 0x63, p >> 7, b, 0x62, p & 0x7F, b, 0x06, v >> 7, b, 0x26, v & 0x7F]
            elif k == "msc":
                dev = 0x7F if t[1].lower() == "all" else _rng(t[1], 0, 127, "device")
                cmd = t[2].lower()
                if cmd not in _MSC_CMDS:
                    raise ValueError(f"comando MSC «{t[2]}» (usa: {', '.join(_MSC_CMDS)})")
                data = []
                for j, field in enumerate(t[3:5]):          # numero di cue, poi lista
                    if j:
                        data.append(0x00)
                    data += [ord(ch) for ch in field if ch in "0123456789."]
                msg = [0xF0, 0x7F, dev, 0x02, 0x7F, _MSC_CMDS[cmd]] + data + [0xF7]
            elif k in ("sysex", "hex"):
                msg = [int(x, 16) for x in " ".join(t[1:]).replace(",", " ").split()]
                if k == "sysex":
                    if not msg or msg[0] != 0xF0:
                        msg = [0xF0] + msg
                    if msg[-1] != 0xF7:
                        msg = msg + [0xF7]
                    if any(b > 0x7F for b in msg[1:-1]):
                        raise ValueError("nei SysEx i byte di dati vanno da 00 a 7F")
                if any(not 0 <= b <= 0xFF for b in msg):
                    raise ValueError("byte non validi")
            else:
                raise ValueError(f"comando sconosciuto «{t[0]}»")
        except IndexError:
            raise ValueError(f"riga {ln}: parametri mancanti in «{line}»")
        except ValueError as e:
            raise ValueError(f"riga {ln}: {e}")
        out.append((wait, msg))
        wait = 0.0
    if wait and out:
        out.append((wait, None))
    return out


class MidiOut:
    """Porte MIDI di uscita aperte su richiesta e riusate."""

    def __init__(self):
        self.ports = {}
        self.lock = threading.Lock()

    @staticmethod
    def list_ports():
        if mido is None:
            return []
        try:
            return mido.get_output_names()
        except Exception:
            return []

    def _port(self, name):
        if mido is None:
            raise RuntimeError("mido/python-rtmidi non installati")
        if not name:
            raise RuntimeError("nessuna uscita MIDI scelta (cue o Impostazioni › MIDI)")
        names = self.list_ports()
        real = MidiInput.resolve(name, names)
        if real is None:
            real = next((n for n in names if port_key(n).startswith(port_key(name))), None)
        if real is None:
            raise RuntimeError(f"uscita MIDI «{name}» non collegata")
        p = self.ports.get(real)
        if p is None:
            p = self.ports[real] = mido.open_output(real)
        return p

    def send_bytes(self, port, data):
        with self.lock:
            p = self._port(port)
            if data[0] == 0xF0:
                p.send(mido.Message("sysex", data=data[1:-1]))
            else:
                i = 0
                while i < len(data):                     # più messaggi di canale in fila (es. NRPN)
                    n = {0xC0: 2, 0xD0: 2}.get(data[i] & 0xF0, 3)
                    p.send(mido.Message.from_bytes(data[i:i + n]))
                    i += n

    def send_script(self, port, text):
        """Invia i messaggi; le attese «wait» vengono rispettate in un thread. Restituisce quanti sono."""
        items = parse_midi_script(text)
        self._port(port)                                  # errore subito se la porta non c'è
        msgs = [m for _w, m in items if m is not None]
        if not any(w for w, _m in items):
            for m in msgs:
                self.send_bytes(port, m)
            return len(msgs)

        def run():
            import time as _t
            for w, m in items:
                if w:
                    _t.sleep(w)
                if m is not None:
                    try:
                        self.send_bytes(port, m)
                    except Exception:
                        return
        threading.Thread(target=run, daemon=True, name="midi-out").start()
        return len(msgs)

    def close(self):
        for p in self.ports.values():
            try:
                p.close()
            except Exception:
                pass
        self.ports = {}


midi_out = MidiOut()
