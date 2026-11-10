"""Plugin CueStage — Blackmagic Design ATEM Mini, Mini Pro (e Pro ISO), Mini Extreme (e Extreme ISO).

Collegamento in rete (porta Ethernet dell'ATEM): protocollo ATEM su UDP porta 9910, lo stesso di ATEM Software
Control. Blackmagic non lo documenta: qui è implementato come nelle librerie libere che lo usano da anni
(SKAARHOJ ATEMmax, PyATEMMax, atem-connection di NRK/Sofie). Nessuna libreria esterna da installare.

Pacchetti UDP: intestazione di 12 byte
    byte 0–1   5 bit di flag (in alto) + lunghezza del pacchetto (11 bit)
               flag: 0x01 richiede conferma · 0x02 Hello / nuova sessione · 0x04 ritrasmissione
                     0x08 richiesta di ritrasmissione · 0x10 conferma
    byte 2–3   sessione
    byte 4–5   numero del pacchetto confermato
    byte 10–11 numero del pacchetto (per quelli che chiedono conferma)
poi i comandi, ognuno: lunghezza (2 byte, compresi gli 8 di intestazione), 2 byte a zero, nome di 4 lettere, dati.
Il client manda Hello, l'ATEM risponde (0x02 = accettato, 0x03 = troppi client collegati), il client conferma;
poi l'ATEM manda tutto il suo stato (fino al comando «InCm») e dei pacchetti di controllo: ognuno va confermato,
altrimenti dopo qualche secondo l'ATEM chiude la sessione. Per questo il driver ha un thread di ricezione.

Le sorgenti sono indicate con il loro numero ATEM (1–8 ingressi, 0 nero, 1000 barre, 2001/2002 colori, 3010/3020
media player, 6000 SuperSource): una cue resta valida anche se gli ingressi cambiano nome.
"""
import random
import socket
import struct
import threading
import time

from cuestage.i18n import tr
from cuestage.mixers import MixerDriver, MixerError

PORT = 9910
F_ACK_REQ, F_HELLO, F_RETRANSMIT, F_RETRANSMIT_REQ, F_ACK = 0x01, 0x02, 0x04, 0x08, 0x10
HELLO_PAYLOAD = bytes([0x01, 0, 0, 0, 0, 0, 0, 0])
TIMEOUT_LOST = 5.0                  # niente dall'ATEM per tanto: collegamento perso (si ricollega al comando dopo)

MODELS = [("auto", tr("Riconoscimento automatico")), ("mini", "ATEM Mini"),
          ("pro", "ATEM Mini Pro / Pro ISO"), ("extreme", "ATEM Mini Extreme / Extreme ISO")]
# per modello: ingressi HDMI, chiavi upstream, chiavi downstream, media player, SuperSource, registrazione/streaming
SPECS = {"mini": dict(inputs=4, usk=1, dsk=2, mp=1, ssrc=False, rec=False),
         "pro": dict(inputs=4, usk=1, dsk=2, mp=1, ssrc=False, rec=True),
         "extreme": dict(inputs=8, usk=4, dsk=2, mp=2, ssrc=True, rec=True)}
TRANS_STYLES = [tr("Mix"), tr("Dip"), tr("Wipe"), tr("DVE"), tr("Stinger")]
MIX_OPTIONS = [(2, tr("Acceso")), (1, tr("Spento")), (4, tr("Segue il video (AFV)"))]   # Fairlight
MIC_INPUTS = (1301, 1302)
STEREO_SOURCE = -65280             # sorgente Fairlight di un ingresso stereo non diviso
VOL_MIN, VOL_MAX = -600, 100       # decimi di dB; −60 dB = −∞
G_SRC, G_TRANS, G_KEYS, G_AUDIO, G_MACRO, G_OUT = (tr("Sorgenti"), tr("Transizioni"), tr("Chiavi"), tr("Audio"),
                                                   tr("Macro"), tr("Registrazione e streaming"))


def default_name(src):
    if 1 <= src <= 40:
        return tr("Ingresso {0}", src)
    return {0: tr("Nero"), 1000: tr("Barre colore"), 2001: tr("Colore 1"), 2002: tr("Colore 2"),
            3010: tr("Media player 1"), 3020: tr("Media player 2"), 6000: "SuperSource",
            1301: tr("Microfono 1"), 1302: tr("Microfono 2")}.get(src, str(src))


def model_from_name(name):
    n = (name or "").lower()
    if "extreme" in n:
        return "extreme"
    if "pro" in n or "iso" in n:
        return "pro"
    return "mini"


def cmd(name, data):
    return struct.pack(">HH4s", 8 + len(data), 0, name.encode("ascii")) + bytes(data)


def header(flags, length, session, ack=0, pid=0):
    return struct.pack(">HHHHHH", (flags << 11) | length, session, ack, 0, 0, pid)


def split_commands(payload):
    """[(nome, dati)] dai comandi di un pacchetto."""
    out, i = [], 0
    while i + 8 <= len(payload):
        ln = struct.unpack_from(">H", payload, i)[0]
        if ln < 8 or i + ln > len(payload):
            break
        out.append((payload[i + 4:i + 8].decode("ascii", "replace"), payload[i + 8:i + ln]))
        i += ln
    return out


def cstr(b):
    return b.split(b"\0", 1)[0].decode("utf-8", "replace").strip()


class ATEMLink:
    """Sessione UDP con un ATEM: Hello, conferme, stato ricevuto, invio dei comandi con ritrasmissione."""

    def __init__(self, host, port=PORT, timeout=3.0):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.2)
        self.session = random.randint(0x0100, 0x7FFF)
        self.pid = 0
        self.lock = threading.Lock()
        self.cond = threading.Condition()
        self.acked = set()
        self.seen = []
        self.last_rx = time.monotonic()
        self.ready = False
        self.closed = False
        self.state = {"ver": (0, 0), "product": "", "program": None, "preview": None, "inputs": {},
                      "usk": {}, "dsk": {}, "ftb": False, "style": 0, "mix_rate": 25, "ftb_rate": 25,
                      "macros": {}, "recording": None, "streaming": None, "audio": {}, "audio_src": {}}
        self.listeners = []
        try:
            self._hello(timeout)
        except Exception:
            self.sock.close()
            raise
        self.thread = threading.Thread(target=self._loop, name="atem-rx", daemon=True)
        self.thread.start()
        with self.cond:                                     # stato iniziale completo (comando InCm)
            self.cond.wait_for(lambda: self.ready or self.closed, timeout)
        if not self.ready:
            self.close()
            raise MixerError(tr("l'ATEM non ha mandato il suo stato: collegamento non riuscito"))

    def _hello(self, timeout):
        pkt = header(F_HELLO, 12 + len(HELLO_PAYLOAD), self.session) + HELLO_PAYLOAD
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                self.sock.sendto(pkt, self.addr)
            except OSError as e:
                raise MixerError(tr("ATEM non raggiungibile su {0}: {1}", self.addr[0], e))
            t0 = time.monotonic()
            while time.monotonic() - t0 < 0.5:
                try:
                    data, _a = self.sock.recvfrom(2048)
                except socket.timeout:
                    break
                except OSError as e:
                    raise MixerError(tr("ATEM non raggiungibile su {0}: {1}", self.addr[0], e))
                if len(data) >= 13 and (data[0] >> 3) & F_HELLO:
                    if data[12] == 0x03:
                        raise MixerError(tr("l'ATEM rifiuta il collegamento: troppi programmi già collegati"))
                    self.sock.sendto(header(F_ACK, 12, self.session), self.addr)
                    return
        raise MixerError(tr("nessuna risposta dall'ATEM su {0}:{1} (acceso, in rete, indirizzo giusto?)",
                            self.addr[0], self.addr[1]))

    # ------------------------------------------------------------------ ricezione
    def _loop(self):
        while not self.closed:
            try:
                data, _a = self.sock.recvfrom(4096)
            except socket.timeout:
                if time.monotonic() - self.last_rx > TIMEOUT_LOST:
                    break
                continue
            except OSError:
                break
            if len(data) < 12:
                continue
            word, session, ack, _x, _y, pid = struct.unpack_from(">HHHHHH", data)
            flags = word >> 11
            self.last_rx = time.monotonic()
            if flags & F_HELLO:
                continue                                    # Hello ripetuto: già confermato
            self.session = session                          # dopo l'Hello l'ATEM sceglie la sessione
            if flags & F_ACK:
                with self.cond:
                    self.acked.add(ack)
                    self.cond.notify_all()
            if flags & F_ACK_REQ:
                try:
                    self.sock.sendto(header(F_ACK, 12, self.session, ack=pid), self.addr)
                except OSError:
                    break
                if pid in self.seen:
                    continue                                # ritrasmesso: già elaborato
                self.seen.append(pid)
                del self.seen[:-64]
            payload = data[12:(word & 0x7FF)]
            if payload:
                self._parse(payload)
        self.closed = True
        with self.cond:
            self.cond.notify_all()

    def _parse(self, payload):
        s = self.state
        for name, d in split_commands(payload):
            try:
                if name == "_ver" and len(d) >= 4:
                    s["ver"] = struct.unpack_from(">HH", d)
                elif name == "_pin":
                    s["product"] = cstr(d[:44])
                elif name == "InPr" and len(d) >= 26:
                    src = struct.unpack_from(">H", d)[0]
                    s["inputs"][src] = (cstr(d[2:22]), cstr(d[22:26]))
                elif name == "PrgI" and len(d) >= 4 and d[0] == 0:
                    s["program"] = struct.unpack_from(">H", d, 2)[0]
                elif name == "PrvI" and len(d) >= 4 and d[0] == 0:
                    s["preview"] = struct.unpack_from(">H", d, 2)[0]
                elif name == "KeOn" and len(d) >= 3 and d[0] == 0:
                    s["usk"][d[1]] = bool(d[2])
                elif name == "DskS" and len(d) >= 2:
                    s["dsk"][d[0]] = bool(d[1])
                elif name == "FtbS" and len(d) >= 2 and d[0] == 0:
                    s["ftb"] = bool(d[1])
                elif name == "FtbP" and len(d) >= 2 and d[0] == 0:
                    s["ftb_rate"] = d[1]
                elif name == "TrSS" and len(d) >= 2 and d[0] == 0:
                    s["style"] = d[1]
                elif name == "TMxP" and len(d) >= 2 and d[0] == 0:
                    s["mix_rate"] = d[1]
                elif name == "MPrp" and len(d) >= 8:
                    idx, used, _u, nlen = struct.unpack_from(">HBBH", d)
                    if used:
                        s["macros"][idx] = d[8:8 + nlen].decode("utf-8", "replace") or tr("Macro {0}", idx + 1)
                    else:
                        s["macros"].pop(idx, None)
                elif name == "RTMS" and len(d) >= 2:
                    s["recording"] = bool(struct.unpack_from(">H", d)[0] & 1)
                elif name == "StRS" and len(d) >= 2:
                    s["streaming"] = struct.unpack_from(">H", d)[0] in (2, 4)
                elif name == "FAIP" and len(d) >= 2:
                    s["audio"].setdefault(struct.unpack_from(">H", d)[0], {})
                elif name == "FASP" and len(d) >= 16:
                    idx, src = struct.unpack_from(">H", d)[0], struct.unpack_from(">q", d, 8)[0]
                    s["audio_src"].setdefault(idx, src)
                elif name == "InCm":
                    with self.cond:
                        self.ready = True
                        self.cond.notify_all()
            except (struct.error, IndexError):
                continue
        for fn in list(self.listeners):
            fn()

    # ------------------------------------------------------------------ invio
    def alive(self):
        return not self.closed and time.monotonic() - self.last_rx < TIMEOUT_LOST

    def send(self, *commands, tries=4):
        payload = b"".join(commands)
        with self.lock:
            self.pid = (self.pid + 1) & 0x7FFF
            pid = self.pid
            for i in range(tries):
                flags = F_ACK_REQ | (F_RETRANSMIT if i else 0)
                pkt = header(flags, 12 + len(payload), self.session, pid=pid) + payload
                try:
                    self.sock.sendto(pkt, self.addr)
                except OSError as e:
                    raise MixerError(tr("ATEM non raggiungibile: {0}", e))
                with self.cond:
                    if self.cond.wait_for(lambda: pid in self.acked or self.closed, 0.4):
                        if pid in self.acked:
                            self.acked.discard(pid)
                            return
                        break
        raise MixerError(tr("l'ATEM non ha confermato il comando (collegamento perso?)"))

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass


class BlackmagicATEM(MixerDriver):
    ID = "blackmagic_atem"
    NAME = tr("Blackmagic ATEM Mini / Pro / Extreme")
    DEFAULT_PORT = PORT
    FIELDS = ("host", "port", "model")
    MODELS = MODELS
    CONTROLS = True
    SWITCHER = True
    NOTES = tr("Collegamento in rete con il cavo Ethernet dell'ATEM (porta UDP 9910, la stessa di ATEM Software "
               "Control, che può restare aperto). L'indirizzo IP si imposta con ATEM Setup (via USB) o si trova con "
               "«Cerca regie in rete». Il modello si riconosce da solo. Registrazione e streaming solo su Mini Pro "
               "ed Extreme; SuperSource e 4 chiavi upstream solo su Extreme.")

    def __init__(self, conf):
        super().__init__(conf)
        self.link = None

    # ------------------------------------------------------------------ collegamento
    def _connect(self):
        if self.link is not None and self.link.alive():
            return self.link
        if self.link is not None:
            self.link.close()
            self.link = None
        if not self.host:
            raise MixerError(tr("indirizzo IP dell'ATEM mancante"))
        self.link = ATEMLink(self.host, self.port or PORT)
        return self.link

    def close(self):
        if self.link is not None:
            self.link.close()
            self.link = None

    def _model(self):
        m = self.conf.get("model") or "auto"
        if m == "auto" or m not in SPECS:
            m = model_from_name(self._connect().state["product"])
        return m

    def status(self):
        st = self._connect().state
        return {tr("modello"): st["product"] or "ATEM", tr("protocollo"): "%d.%d" % st["ver"],
                tr("in onda"): self._name(st["program"]) if st["program"] is not None else "—"}

    # ------------------------------------------------------------------ elenco dei comandi
    def _name(self, src):
        nm = self._connect().state["inputs"].get(src)
        return (nm[0] or nm[1]) if nm and (nm[0] or nm[1]) else default_name(src)

    def _sources(self, spec):
        ids = [0] + list(range(1, spec["inputs"] + 1)) + [1000, 2001, 2002] \
            + [3010 + 10 * i for i in range(spec["mp"])] + ([6000] if spec["ssrc"] else [])
        known = self._connect().state["inputs"]
        if known:                                  # quelle che l'ATEM dichiara davvero (in ordine)
            ids = [s for s in ids if s in known] or ids
        return ids

    def list_controls(self):
        link = self._connect()
        st = link.state
        spec = SPECS[self._model()]
        srcs = self._sources(spec)
        names = [self._name(s) for s in srcs]
        out = []

        def idx(src):
            return srcs.index(src) if src in srcs else 0
        out.append(dict(key="on_air", name=tr("In onda (Program)"), type="enum", items=names,
                        value=idx(st["program"]), writable=False, group=G_SRC))
        out.append(dict(key="preview", name=tr("In anteprima (Preview)"), type="enum", items=names,
                        value=idx(st["preview"]), writable=False, group=G_SRC))
        out.append(dict(key="cut", name=tr("CUT (anteprima → programma)"), type="action", group=G_SRC))
        out.append(dict(key="auto", name=tr("AUTO (transizione anteprima → programma)"), type="action", group=G_SRC))
        for s, n in zip(srcs, names):
            out.append(dict(key=f"go:{s}", name=tr("Vai a «{0}»", n), type="action", group=G_SRC))
        for s, n in zip(srcs, names):
            out.append(dict(key=f"pgm:{s}", name=tr("Taglio a «{0}»", n), type="action", group=G_SRC))
        for s, n in zip(srcs, names):
            out.append(dict(key=f"pvw:{s}", name=tr("In anteprima «{0}»", n), type="action", group=G_SRC))
        # transizioni
        out.append(dict(key="style", name=tr("Tipo di transizione"), type="enum", items=TRANS_STYLES,
                        value=min(st["style"], len(TRANS_STYLES) - 1), group=G_TRANS))
        out.append(dict(key="mix_rate", name=tr("Durata del Mix"), type="int", min=1, max=250,
                        value=int(st["mix_rate"] or 25), fmt=(0, 1, " fr", 0), group=G_TRANS))
        out.append(dict(key="ftb", name=tr("Fade to Black (FTB)"), type="action", group=G_TRANS))
        out.append(dict(key="ftb_on", name=tr("Nero in uscita (FTB)"), type="bool", value=int(st["ftb"]),
                        writable=False, group=G_TRANS))
        out.append(dict(key="ftb_rate", name=tr("Durata del Fade to Black"), type="int", min=1, max=250,
                        value=int(st["ftb_rate"] or 25), fmt=(0, 1, " fr", 0), group=G_TRANS))
        # chiavi
        for k in range(spec["usk"]):
            out.append(dict(key=f"usk:{k}", name=tr("Chiave upstream {0} in onda", k + 1), type="bool",
                            value=int(st["usk"].get(k, False)), group=G_KEYS))
        for k in range(spec["dsk"]):
            out.append(dict(key=f"dsk:{k}", name=tr("DSK {0} in onda", k + 1), type="bool",
                            value=int(st["dsk"].get(k, False)), group=G_KEYS))
            out.append(dict(key=f"dsk_auto:{k}", name=tr("DSK {0} AUTO (dissolvenza)", k + 1), type="action",
                            group=G_KEYS))
        # audio (mixer Fairlight degli ATEM Mini)
        ains = sorted(st["audio"]) or list(range(1, spec["inputs"] + 1)) + list(MIC_INPUTS)
        for a in ains:
            n = self._name(a)
            out.append(dict(key=f"vol:{a}", name=tr("{0} volume", n), type="int", min=VOL_MIN, max=VOL_MAX,
                            value=0, fmt=(0, 0.1, " dB", 1), group=G_AUDIO))
            out.append(dict(key=f"mix:{a}", name=tr("{0} nel mix", n), type="enum",
                            items=[lbl for _v, lbl in MIX_OPTIONS], value=0, group=G_AUDIO))
        out.append(dict(key="master_vol", name=tr("Master volume"), type="int", min=VOL_MIN, max=VOL_MAX, value=0,
                        fmt=(0, 0.1, " dB", 1), group=G_AUDIO))
        # macro salvate nell'ATEM
        for i, n in sorted(st["macros"].items()):
            out.append(dict(key=f"macro:{i}", name=tr("Macro {0}: {1}", i + 1, n), type="action", group=G_MACRO))
        out.append(dict(key="macro_stop", name=tr("Ferma la macro in corso"), type="action", group=G_MACRO))
        if spec["rec"]:
            out.append(dict(key="record", name=tr("Registrazione"), type="bool", value=int(bool(st["recording"])),
                            group=G_OUT))
            out.append(dict(key="stream", name=tr("Streaming"), type="bool", value=int(bool(st["streaming"])),
                            group=G_OUT))
        for c in out:
            c.setdefault("min", 0)
            c.setdefault("max", len(c.get("items") or []) - 1 if c["type"] == "enum" else 1)
            c.setdefault("items", [])
            c.setdefault("db", None)
            c.setdefault("writable", True)
        return out

    # ------------------------------------------------------------------ comandi
    def _wait(self, cond, timeout=1.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not cond():
            time.sleep(0.01)

    def _fairlight_gain(self, value):
        return -10000 if value <= VOL_MIN else int(value) * 10          # centesimi di dB

    def set_control(self, key, value):
        value = int(value or 0)
        link = self._connect()
        st = link.state
        kind, _, arg = key.partition(":")
        if kind in ("go", "pgm", "pvw"):
            src = int(arg)
            if kind == "pgm":
                link.send(cmd("CPgI", struct.pack(">BxH", 0, src)))
            else:
                link.send(cmd("CPvI", struct.pack(">BxH", 0, src)))
                if kind == "go":
                    self._wait(lambda: st["preview"] == src, 0.5)
                    link.send(cmd("DAut", bytes(4)))
        elif key == "cut":
            link.send(cmd("DCut", bytes(4)))
        elif key == "auto":
            link.send(cmd("DAut", bytes(4)))
        elif key == "style":
            link.send(cmd("CTTp", bytes([0x01, 0, max(0, min(value, 4)), 0])))
        elif key == "mix_rate":
            link.send(cmd("CTMx", bytes([0, max(1, min(value, 250)), 0, 0])))
        elif key == "ftb":
            link.send(cmd("FtbA", bytes(4)))
        elif key == "ftb_rate":
            link.send(cmd("FtbC", bytes([0x01, 0, max(1, min(value, 250)), 0])))
        elif kind == "usk":
            link.send(cmd("CKOn", bytes([0, int(arg), 1 if value else 0, 0])))
        elif kind == "dsk":
            link.send(cmd("CDsL", bytes([int(arg), 1 if value else 0, 0, 0])))
        elif kind == "dsk_auto":
            if tuple(st["ver"]) >= (2, 28):                 # firmware 8.0.1 e successivi
                link.send(cmd("DDsA", bytes([0, int(arg), 0, 0])))
            else:
                link.send(cmd("DDsA", bytes([int(arg), 0, 0, 0])))
        elif kind in ("vol", "mix"):
            a = int(arg)
            src = st["audio_src"].get(a, STEREO_SOURCE)
            if kind == "vol":
                flag, gain, opt = 1 << 7, self._fairlight_gain(value), 0
            else:
                flag, gain = 1 << 8, 0
                opt = MIX_OPTIONS[max(0, min(value, len(MIX_OPTIONS) - 1))][0]
            data = bytearray(48)
            struct.pack_into(">HH", data, 0, flag, a)
            struct.pack_into(">q", data, 8, src)
            struct.pack_into(">i", data, 40, gain)
            data[44] = opt
            link.send(cmd("CFSP", data))
        elif key == "master_vol":
            data = bytearray(20)
            data[0] = 1 << 3
            struct.pack_into(">i", data, 12, self._fairlight_gain(value))
            link.send(cmd("CFMP", data))
        elif kind == "macro":
            link.send(cmd("MAct", struct.pack(">HBx", int(arg), 0)))
        elif key == "macro_stop":
            link.send(cmd("MAct", struct.pack(">HBx", 0xFFFF, 1)))
        elif key == "record":
            link.send(cmd("RcTM", struct.pack(">Hxx", 1 if value else 0)))
        elif key == "stream":
            link.send(cmd("StrR", struct.pack(">Hxx", 1 if value else 0)))
        elif key in ("on_air", "preview", "ftb_on"):
            raise MixerError(tr("{0}: solo lettura", key))
        else:
            raise MixerError(tr("comando ATEM sconosciuto: {0}", key))

    # ------------------------------------------------------------------ ricerca in rete
    LOCAL_HOSTS = ("127.0.0.1",)

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        """Hello UDP a ogni indirizzo delle reti /24 locali (e a questo computer): risponde solo un ATEM.
        Agli ATEM trovati si chiede poi il nome del modello."""
        import ipaddress
        hosts = []
        for ip in local_ips or []:
            if not ip or ip.startswith("127.") or ":" in ip:
                continue
            try:
                net = ipaddress.ip_network(f"{ip}/24", strict=False)
            except ValueError:
                continue
            hosts += [str(h) for h in net.hosts() if str(h) != ip]
        hosts = list(dict.fromkeys(list(cls.LOCAL_HOSTS) + hosts))[:2048]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        found = []
        try:
            s.bind(("0.0.0.0", 0))
            s.settimeout(0.1)
            sess = random.randint(0x0100, 0x7FFF)
            pkt = header(F_HELLO, 12 + len(HELLO_PAYLOAD), sess) + HELLO_PAYLOAD
            for h in hosts:
                try:
                    s.sendto(pkt, (h, PORT))
                except OSError:
                    pass
            end = time.monotonic() + min(timeout, 1.5)
            while time.monotonic() < end:
                try:
                    data, (h, _p) = s.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    continue                    # Windows: «porta irraggiungibile» da un indirizzo senza ATEM
                if len(data) >= 13 and (data[0] >> 3) & F_HELLO and h not in found:
                    found.append(h)
        finally:
            s.close()
        out = []
        for h in found:
            name = ""
            try:
                link = ATEMLink(h, PORT, timeout=1.5)
                name = link.state["product"]
                link.close()
            except (MixerError, OSError):
                pass
            local = h.startswith("127.")
            out.append({"driver": cls.ID, "host": h, "port": PORT, "model": model_from_name(name) if name else "auto",
                        "name": name or "ATEM", "info": tr("su questo computer") if local else h})
        return out
