"""Controllo di videoproiettori via rete: comandi comuni, driver PJLink integrato e sistema di plugin.

Un plugin è un file .py (nella cartella «plugins» di CueStage o in ~/.local/share/cuestage/plugins/)
che definisce una o più sottoclassi di ProjectorDriver. Vedi plugins/LEGGIMI_PLUGIN.md.
"""
from .i18n import tr
import hashlib
import importlib.util
import os
import socket
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

from .system import data_dir

# ------------------------------------------------------------------ azioni comuni

ACTIONS = {
    "power_on": tr("Accendi"),
    "power_off": tr("Spegni (standby)"),
    "shutter_close": tr("Otturatore: chiudi"),
    "shutter_open": tr("Otturatore: apri"),
    "input": tr("Seleziona ingresso"),
    "freeze_on": tr("Freeze: attiva"),
    "freeze_off": tr("Freeze: disattiva"),
    "zoom": tr("Zoom"),
    "focus": tr("Fuoco"),
    "shift_h": tr("Shift orizzontale"),
    "shift_v": tr("Shift verticale"),
    "lens_memory": tr("Richiama lens memory"),
    "corners": tr("Correzione 4 angoli: imposta"),
    "corners_off": tr("Correzione 4 angoli: disattiva"),
    "raw": tr("Comando libero"),
}
LENS_ACTIONS = ("zoom", "focus", "shift_h", "shift_v")
CORNER_KEYS = ("tl", "tr", "bl", "br")
CORNER_LABELS = {"tl": tr("Alto sinistra"), "tr": tr("Alto destra"), "bl": tr("Basso sinistra"), "br": tr("Basso destra")}


def parse_corners(text):
    """«x y x y x y x y» (alto sx, alto dx, basso sx, basso dx) -> {"tl": [x, y], …}; None se non valido."""
    try:
        v = [int(round(float(t))) for t in str(text or "").replace(",", " ").split()]
    except ValueError:
        return None
    if len(v) != 8:
        return None
    return {k: [v[2 * i], v[2 * i + 1]] for i, k in enumerate(CORNER_KEYS)}


def format_corners(c):
    return " ".join(f"{int(c[k][0])} {int(c[k][1])}" for k in CORNER_KEYS)


SPEEDS = {"slow": tr("Lento (passo fine)"), "normal": tr("Normale"), "fast": tr("Veloce")}

USER_PLUGIN_DIR = data_dir("plugins")
BUILTIN_PLUGIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")


class ProjectorError(Exception):
    pass


class ProjectorDriver:
    """Classe base dei driver. Un plugin la estende e implementa i metodi delle azioni supportate.

    Attributi da definire nel plugin:
      ID            identificativo univoco (es. "panasonic_mz880")
      NAME          nome mostrato (es. "Panasonic PT-MZ880 / MZ882")
      DEFAULT_PORT  porta TCP predefinita
      FIELDS        campi di connessione da chiedere: tra "host", "port", "user", "password", "proj_id"
      CAPS          azioni comuni supportate (chiavi di ACTIONS)
      INPUTS        [(codice, etichetta)] degli ingressi
      EXTRA_ACTIONS {id: etichetta} azioni specifiche del modello (eseguite da extra())
      EXTRA_PARAMS  {id: descrizione} facoltativo: azioni extra che vogliono un parametro (testo)
      CORNERS       facoltativo: correzione geometrica a 4 angoli. {angolo: {"x": (min, max), "y": (min, max)}}
                    con angolo in "tl", "tr", "bl", "br"; valori nelle unità del proiettore (di solito
                    pixel; x verso destra, y verso il basso). Con CORNERS il driver implementa
                    corner_set(angolo, asse, valore), e se può corners_get() e corners_enable(on).
      CORNERS_NOTE  facoltativo: avvertenza mostrata nel pannello (es. «comandi da verificare»)
      NOTES         testo informativo mostrato nelle impostazioni
    """
    ID = ""
    NAME = ""
    DEFAULT_PORT = 0
    FIELDS = ("host", "port")
    CAPS = set()
    INPUTS = []
    EXTRA_ACTIONS = {}
    EXTRA_PARAMS = {}
    CORNERS = {}
    CORNERS_NOTE = ""
    NOTES = ""
    TIMEOUT = 3.0

    def __init__(self, conf):
        self.conf = dict(conf)
        self.host = (conf.get("host") or "").strip()
        self.port = int(conf.get("port") or self.DEFAULT_PORT or 0)
        self.user = conf.get("user") or ""
        self.password = conf.get("password") or ""
        self.proj_id = conf.get("proj_id") or ""

    # ---- utilità di rete per i plugin
    def connect(self, port=None):
        if not self.host:
            raise ProjectorError(tr("indirizzo IP non impostato"))
        try:
            s = socket.create_connection((self.host, port or self.port), timeout=self.TIMEOUT)
        except OSError as e:
            raise ProjectorError(tr("connessione a {0}:{1} non riuscita ({2})", self.host, port or self.port, e))
        s.settimeout(self.TIMEOUT)
        return s

    @staticmethod
    def read_line(sock, term=b"\r", limit=4096, timeout=None):
        """Legge fino al terminatore (escluso). Con timeout restituisce quanto ricevuto."""
        if timeout is not None:
            sock.settimeout(timeout)
        buf = b""
        while len(buf) < limit:
            try:
                ch = sock.recv(1)
            except socket.timeout:
                if buf:
                    break
                raise ProjectorError(tr("nessuna risposta dal proiettore"))
            if not ch:
                break
            if ch == term:
                break
            if ch in (b"\n",) and term == b"\r":
                continue
            buf += ch
        return buf.decode("ascii", errors="replace")

    # ---- azioni comuni (i plugin implementano quelle in CAPS)
    def power(self, on):
        raise ProjectorError(tr("non supportato"))

    def shutter(self, closed):
        raise ProjectorError(tr("non supportato"))

    def select_input(self, code):
        raise ProjectorError(tr("non supportato"))

    def freeze(self, on):
        raise ProjectorError(tr("non supportato"))

    def lens(self, axis, direction, steps=1, speed="normal"):
        """axis: zoom|focus|shift_h|shift_v · direction: +1/-1 · steps: ripetizioni."""
        raise ProjectorError(tr("non supportato"))

    def lens_memory(self, n):
        raise ProjectorError(tr("non supportato"))

    def raw(self, text):
        raise ProjectorError(tr("non supportato"))

    # ---- correzione geometrica a 4 angoli (facoltativa)
    def corners_enable(self, on):
        """Attiva/disattiva la correzione angoli (se il proiettore ha una modalità da scegliere)."""
        return None

    def corner_set(self, corner, axis, value):
        raise ProjectorError(tr("correzione a 4 angoli non supportata"))

    def corners_get(self):
        """{"tl": [x, y], …} letti dal proiettore."""
        raise ProjectorError(tr("lettura della correzione angoli non supportata"))

    def corners_apply(self, corners):
        self.corners_enable(True)
        for k in CORNER_KEYS:
            for j, axis in enumerate(("x", "y")):
                lo, hi = self.CORNERS[k][axis]
                self.corner_set(k, axis, max(lo, min(hi, int(corners[k][j]))))
        return format_corners(corners)

    def extra(self, action, param):
        raise ProjectorError(tr("non supportato"))

    def status(self):
        """Dizionario con lo stato (almeno «power» se possibile). Usato da «Prova connessione»."""
        raise ProjectorError(tr("non supportato"))

    # ---- esecuzione di una cue
    def run(self, action, value="", direction=1, steps=1, speed="normal"):
        if action in ("corner_one", "corner_live") and "corners" in self.CAPS:
            pass
        elif action not in self.CAPS and action not in self.EXTRA_ACTIONS:
            raise ProjectorError(tr("«{0}» non è disponibile su {1}", ACTIONS.get(action, action), self.NAME))
        if action == "power_on":
            return self.power(True)
        if action == "power_off":
            return self.power(False)
        if action == "shutter_close":
            return self.shutter(True)
        if action == "shutter_open":
            return self.shutter(False)
        if action == "input":
            return self.select_input(value)
        if action == "freeze_on":
            return self.freeze(True)
        if action == "freeze_off":
            return self.freeze(False)
        if action in LENS_ACTIONS:
            return self.lens(action, 1 if int(direction) >= 0 else -1, max(1, int(steps or 1)), speed)
        if action == "lens_memory":
            return self.lens_memory(int(value or 1))
        if action == "raw":
            return self.raw(value)
        if action == "corners":
            c = parse_corners(value)
            if c is None:
                raise ProjectorError(tr("valori della correzione angoli non validi (servono 8 numeri)"))
            return self.corners_apply(c)
        if action == "corners_off":
            return self.corners_enable(False)
        if action in ("corner_one", "corner_live"):   # dal pannello: «tl x 12; br y -5»
            if action == "corner_one":
                self.corners_enable(True)   # primo invio: la modifica deve vedersi anche se era spenta
            res = None
            for part in str(value).split(";"):
                if part.strip():
                    k, axis, v = part.split()
                    lo, hi = self.CORNERS[k][axis]
                    res = self.corner_set(k, axis, max(lo, min(hi, int(v))))
            return res
        if action in self.EXTRA_ACTIONS:
            return self.extra(action, value)
        raise ProjectorError(tr("azione «{0}» non supportata da {1}", action, self.NAME))

    @classmethod
    def actions(cls):
        out = [(a, ACTIONS[a]) for a in ACTIONS if a in cls.CAPS]
        out += [(a, lab) for a, lab in cls.EXTRA_ACTIONS.items()]
        return out


# ------------------------------------------------------------------ PJLink (integrato)

class PJLinkDriver(ProjectorDriver):
    """PJLink classe 1: standard comune a quasi tutti i proiettori (porta 4352).
    Accensione, ingresso, otturatore (AV mute) e stato. Non prevede comandi per l'ottica."""
    ID = "pjlink"
    NAME = tr("PJLink (qualsiasi marca)")
    DEFAULT_PORT = 4352
    FIELDS = ("host", "port", "password")
    CAPS = {"power_on", "power_off", "shutter_close", "shutter_open", "input", "raw"}
    INPUTS = [("11", tr("RGB 1")), ("12", tr("RGB 2")), ("21", tr("Video 1")), ("31", tr("Digitale 1 (es. HDMI 1)")),
              ("32", tr("Digitale 2 (es. HDMI 2)")), ("33", tr("Digitale 3")), ("41", tr("Memoria (USB)")),
              ("51", tr("Rete"))]
    NOTES = (tr("Codici ingresso PJLink: 1x RGB, 2x video, 3x digitale, 4x memoria, 5x rete. Gli ingressi "
             "disponibili si leggono con «Prova connessione». Otturatore = AV mute (AVMT 31/30). "
             "Zoom, fuoco e shift non fanno parte di PJLink."))
    ERRORS = {"ERR1": tr("comando non riconosciuto"), "ERR2": tr("parametro non valido"),
              "ERR3": tr("non disponibile in questo momento"), "ERR4": tr("guasto del proiettore")}

    def _cmd(self, body):
        s = self.connect()
        try:
            greet = self.read_line(s)
            if not greet.startswith("PJLINK"):
                raise ProjectorError(tr("risposta inattesa: {0!r}", greet))
            prefix = ""
            parts = greet.split()
            if len(parts) >= 3 and parts[1] == "1":
                if not self.password:
                    raise ProjectorError(tr("il proiettore richiede la password PJLink"))
                prefix = hashlib.md5((parts[2] + self.password).encode()).hexdigest()
            elif len(parts) >= 2 and parts[1] == "ERRA":
                raise ProjectorError(tr("autenticazione PJLink rifiutata"))
            s.sendall((prefix + "%1" + body + "\r").encode("ascii"))
            resp = self.read_line(s)
        finally:
            s.close()
        if "ERRA" in resp:
            raise ProjectorError(tr("password PJLink errata"))
        val = resp.split("=", 1)[1] if "=" in resp else resp
        if val in self.ERRORS:
            raise ProjectorError(f"{body.split()[0]}: {self.ERRORS[val]}")
        return val

    def power(self, on):
        return self._cmd(f"POWR {1 if on else 0}")

    def shutter(self, closed):
        return self._cmd(f"AVMT {31 if closed else 30}")

    def select_input(self, code):
        return self._cmd(f"INPT {code}")

    def raw(self, text):
        text = text.strip()
        if text.startswith("%1"):
            text = text[2:]
        return self._cmd(text)

    def status(self):
        p = {"0": tr("spento"), "1": tr("acceso"), "2": tr("in raffreddamento"), "3": tr("in riscaldamento")}
        st = {"power": p.get(self._cmd("POWR ?"), "?")}
        for key, cmd in (("nome", "NAME ?"), ("ingresso", "INPT ?"), ("av_mute", "AVMT ?"),
                         ("ingressi_disponibili", "INST ?")):
            try:
                st[key] = self._cmd(cmd)
            except ProjectorError as e:
                st[key] = f"({e})"
        return st


# ------------------------------------------------------------------ plugin

class PluginRegistry:
    def __init__(self):
        self.drivers = {}
        self.errors = []
        self.sources = {}

    def load(self):
        self.drivers = {PJLinkDriver.ID: PJLinkDriver}
        self.sources = {PJLinkDriver.ID: "integrato"}
        self.errors = []
        for d in (BUILTIN_PLUGIN_DIR, USER_PLUGIN_DIR):
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
        name = "cuestage_plugin_" + os.path.splitext(os.path.basename(path))[0]
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        found = 0
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, ProjectorDriver) and obj is not ProjectorDriver \
                    and obj.ID and obj.__module__ == name:
                if obj.ID in self.drivers and self.sources.get(obj.ID) != path:
                    self.errors.append(tr("{0}: ID «{1}» già usato da {2} (il plugin utente lo sostituisce)", os.path.basename(path), obj.ID, self.sources.get(obj.ID)))
                bad = set(obj.CAPS) - set(ACTIONS)
                if bad:
                    raise ProjectorError(tr("azioni sconosciute in CAPS: {0}", ', '.join(sorted(bad))))
                self.drivers[obj.ID] = obj
                self.sources[obj.ID] = path
                found += 1
        if not found:
            raise ProjectorError(tr("nessuna classe ProjectorDriver con ID trovata"))

    def get(self, driver_id):
        return self.drivers.get(driver_id)


class ProjectorManager(QObject):
    """Esegue i comandi in thread separati: uno per proiettore (i comandi restano in ordine)."""
    finished = Signal(str, str, bool, str)   # nome proiettore, azione, riuscito, messaggio

    def __init__(self):
        super().__init__()
        self.registry = PluginRegistry().load()
        self.projectors = []
        self._pools = {}
        self._lock = threading.Lock()

    def reload_plugins(self):
        self.registry.load()

    def set_projectors(self, lst):
        self.projectors = [dict(p) for p in (lst or [])]

    def find(self, pid):
        for p in self.projectors:
            if p.get("id") == pid:
                return p
        return None

    def driver_for(self, conf):
        cls = self.registry.get(conf.get("driver"))
        if cls is None:
            raise ProjectorError(tr("driver «{0}» non disponibile (plugin mancante?)", conf.get('driver')))
        return cls(conf)

    def _pool(self, pid):
        with self._lock:
            p = self._pools.get(pid)
            if p is None:
                p = self._pools[pid] = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"proj-{pid}")
            return p

    def run(self, pid, action, value="", direction=1, steps=1, speed="normal", label=None):
        conf = self.find(pid)
        if conf is None:
            self.finished.emit("?", label or action, False, tr("proiettore non configurato"))
            return
        name = conf.get("name") or conf.get("host") or pid
        lab = label or ACTIONS.get(action) or action

        def job():
            t0 = time.monotonic()
            try:
                res = self.driver_for(conf).run(action, value, direction, steps, speed)
                msg = tr("ok ({0:.1f} s)", time.monotonic() - t0) + (f": {res}" if res not in (None, "", "OK") else "")
                self.finished.emit(name, lab, True, msg)
            except ProjectorError as e:
                self.finished.emit(name, lab, False, str(e))
            except Exception as e:      # errore nel plugin: non deve fermare CueStage
                traceback.print_exc()
                self.finished.emit(name, lab, False, tr("errore nel driver: {0}", e))
        return self._pool(pid).submit(job)

    def query_status(self, pid, callback):
        """Stato di un proiettore configurato, nella sua coda comandi; callback(ok, dict|testo) dal thread."""
        conf = self.find(pid)
        if conf is None:
            callback(False, tr("proiettore non configurato"))
            return None

        def job():
            try:
                callback(True, self.driver_for(conf).status())
            except Exception as e:
                callback(False, str(e))
        return self._pool(pid).submit(job)

    def query_corners(self, pid, callback):
        """Legge la correzione angoli (nella coda del proiettore); callback(ok, dict|testo) dal thread."""
        conf = self.find(pid)
        if conf is None:
            callback(False, tr("proiettore non configurato"))
            return None

        def job():
            try:
                callback(True, self.driver_for(conf).corners_get())
            except Exception as e:
                callback(False, str(e))
        return self._pool(pid).submit(job)

    def test(self, conf, callback):
        """Prova di connessione (in un thread); callback(ok, testo) viene chiamata dal thread."""
        def job():
            try:
                st = self.driver_for(conf).status()
                callback(True, "\n".join(f"{k}: {v}" for k, v in st.items()))
            except Exception as e:
                callback(False, str(e))
        threading.Thread(target=job, daemon=True).start()

    def shutdown(self):
        for p in self._pools.values():
            p.shutdown(wait=False, cancel_futures=True)
