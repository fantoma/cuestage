"""Ducking: un ingresso audio (microfono, voce dello speaker) abbassa le cue che hanno un valore di ducking.

Il segnale di un canale della scheda in ingresso passa per un gate (solo soglia): quando lo supera la
riduzione entra con il tempo di attacco; quando scende sotto la soglia resta piena per il tempo di hold, poi
esce con il tempo di rilascio.
La riduzione in dB è un'impostazione di ogni cue audio/video (o del gruppo, per tutte le sue cue)."""
from .i18n import tr
import math
import threading

import numpy as np

from .media import SR

DUCK_DEFAULTS = {
    "enabled": False,        # il ducking agisce sulle cue
    "device": "",            # scheda in ingresso ("" = predefinita di sistema)
    "channel": 1,            # canale della scheda (da 1)
    "threshold_db": -40.0,   # soglia del gate (dBFS)
    "attack_ms": 50,         # 0–1000 ms
    "hold_ms": 1000,         # 0–5000 ms: attesa sotto soglia prima del rilascio
    "release_ms": 800,       # 0–5000 ms
    "master_db": 0.0,        # Master ducking: riduzione (dB) dell'uscita master quando interviene
    "master_on": False,      # tasto «Duck» accanto al volume master
    "to_preview": False,     # manda il canale d'ingresso, dopo il gate, alla cuffia del preascolto
    "dim_on": False,         # tasto «DIM»: master abbassato della riduzione del Master ducking, fisso
}
MIN_METER_DB = -70.0


def duck_settings(settings):
    """Impostazioni del ducking dello show, completate con i valori di serie."""
    d = dict(DUCK_DEFAULTS)
    d.update((settings or {}).get("ducking") or {})
    d["channel"] = max(1, int(d.get("channel") or 1))
    d["threshold_db"] = max(-90.0, min(0.0, float(d.get("threshold_db", -40.0))))
    d["attack_ms"] = max(0, min(1000, int(d.get("attack_ms", 50))))
    d["hold_ms"] = max(0, min(5000, int(d.get("hold_ms", 1000))))
    d["release_ms"] = max(0, min(5000, int(d.get("release_ms", 800))))
    d["master_db"] = max(0.0, min(60.0, float(d.get("master_db") or 0.0)))
    d["master_on"] = bool(d.get("master_on"))
    d["to_preview"] = bool(d.get("to_preview"))
    d["dim_on"] = bool(d.get("dim_on"))
    return d


def lin_to_db(x):
    return 20.0 * math.log10(x) if x > 1e-6 else -120.0


def list_input_devices():
    """Schede con ingressi: [{id, name, hostapi, index, max_ch}] (stesso formato degli id delle uscite)."""
    from .audio_engine import WIN_APIS, _base_name, sd
    from .system import IS_WIN
    if sd is None:
        return []
    try:
        apis = sd.query_hostapis()
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) <= 0:
                continue
            api = apis[d["hostapi"]]["name"]
            out.append({"id": f"{api}|{_base_name(d['name'])}", "name": d["name"], "hostapi": api, "index": i,
                        "max_ch": int(d["max_input_channels"])})
        if IS_WIN:
            good = [d for d in out if d["hostapi"] in WIN_APIS]
            out = good or out
        return out
    except Exception:
        return []


def find_input(dev_id):
    if not dev_id:
        return None
    for d in list_input_devices():
        if d["id"] == dev_id:
            return d
    return None


class Ducker:
    """Ascolta il canale scelto e calcola l'inviluppo della riduzione (0 = nessuna, 1 = piena)."""

    def __init__(self):
        self.cfg = dict(DUCK_DEFAULTS)
        self.monitor = False          # finestra delle impostazioni aperta: si ascolta anche se spento
        self.stream = None
        self.lock = threading.Lock()
        self.meter = 0.0              # picco (lineare) del canale, con caduta, per il vumeter
        self.level_db = -120.0        # livello dell'ultimo blocco
        self.gate = False             # il segnale supera la soglia
        self.env = 0.0                # quanto della riduzione è applicato ora (0–1)
        self.hold_left = 0.0          # secondi di hold rimasti prima del rilascio
        self.hooks = set()            # cue Mixer «Ducking» attive: tengono l'ingresso in ascolto
        # ascolto in cuffia dell'ingresso dopo il gate: coda di campioni letta dall'uscita del preascolto
        self.pv_lock = threading.Lock()
        self.pv_buf = np.zeros(0, np.float32)
        self.pv_primed = False
        self.pv_g = 0.0               # apertura del gate per l'audio (rampa tra un blocco e l'altro)
        self.error = ""
        self.name = ""
        self.n_ch = 0

    # ------------------------------------------------------------ stato
    @property
    def listening(self):
        return self.stream is not None

    @property
    def enabled(self):
        return bool(self.cfg.get("enabled")) and self.stream is not None

    @property
    def ducking(self):
        """La riduzione sta intervenendo (per l'icona rossa)."""
        return self.enabled and self.env > 0.02

    @property
    def master_active(self):
        """Il tasto Duck è acceso, con una riduzione e l'ingresso in ascolto."""
        return bool(self.cfg.get("master_on")) and float(self.cfg.get("master_db") or 0) > 0 \
            and self.stream is not None

    @property
    def master_ducking(self):
        return self.master_active and self.env > 0.02

    def gain(self, duck_db):
        """Guadagno lineare da applicare a una cue con questa riduzione (dB positivi)."""
        if not duck_db or duck_db <= 0 or not self.enabled:
            return 1.0
        return 10.0 ** (-float(duck_db) * self.env / 20.0)

    @property
    def dim_active(self):
        return bool(self.cfg.get("dim_on")) and float(self.cfg.get("master_db") or 0) > 0

    def master_gain(self):
        """Guadagno lineare dell'uscita master: Master ducking e DIM (1 = nessuna riduzione).
        Duck e DIM insieme non si sommano: vale la riduzione maggiore (al massimo quella impostata)."""
        amount = self.env if self.master_active else 0.0
        if self.dim_active:
            amount = 1.0
        if amount <= 0:
            return 1.0
        return 10.0 ** (-float(self.cfg["master_db"]) * amount / 20.0)

    # ------------------------------------------------------------ configurazione
    def configure(self, cfg, monitor=None):
        """Applica le impostazioni; riapre l'ingresso solo se cambiano scheda, canale o accensione."""
        new = duck_settings({"ducking": cfg})
        if monitor is not None:
            self.monitor = bool(monitor)
        self.cfg, old = new, self.cfg
        want = self.wanted()
        same_dev = (new["device"], new["channel"]) == (old.get("device"), old.get("channel"))
        if not want:
            self.close()
        elif self.stream is None or not same_dev:
            self.open()

    def wanted(self):
        """Serve l'ingresso: ducking delle cue acceso, tasto Duck acceso o finestra delle impostazioni aperta."""
        return bool(self.cfg.get("enabled")) or bool(self.cfg.get("master_on")) or self.monitor \
            or bool(self.hooks) or bool(self.cfg.get("to_preview"))

    def hook(self, key, on):
        """Una cue Mixer «Ducking» (key) aggancia o sgancia i fader alla curva del ducking."""
        if on:
            self.hooks.add(key)
            if self.stream is None:
                self.open()
        else:
            self.hooks.discard(key)
            if not self.wanted():
                self.close()

    def open(self):
        from .audio_engine import sd
        self.close()
        self.error = ""
        if sd is None:
            self.error = tr("PortAudio non installato")
            return False
        dev_id = self.cfg.get("device") or ""
        dev = find_input(dev_id) if dev_id else None
        if dev_id and dev is None:
            self.error = tr("Scheda in ingresso «{0}» non trovata", dev_id.split('|', 1)[-1])
            return False
        try:
            if dev is None:
                info = sd.query_devices(kind="input")
                max_ch = int(info.get("max_input_channels") or 0)
                self.name = info.get("name", "predefinita")
            else:
                max_ch = dev["max_ch"]
                self.name = dev["name"]
            ch = int(self.cfg.get("channel") or 1)
            if ch > max_ch:
                self.error = tr("«{0}» ha {1} canali in ingresso: il canale {2} non esiste", self.name, max_ch, ch)
                return False
            self.n_ch = max_ch
            err = None
            # le schede «hw:» di ALSA accettano a volte solo tutti i canali insieme: si riprova così
            for n in dict.fromkeys((ch, max_ch)):
                try:
                    self.stream = sd.InputStream(samplerate=SR, channels=n, dtype="float32", blocksize=256,
                                                 device=dev["index"] if dev else None, callback=self._cb)
                    self.stream.start()
                    return True
                except Exception as e:
                    self.stream = None
                    err = e
            raise err
        except Exception as e:
            self.stream = None
            self.error = tr("Impossibile aprire l'ingresso «{0}»: {1}", self.name, e)
            return False

    def close(self):
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self.env = 0.0
        self.hold_left = 0.0
        self.gate = False
        self.pv_g = 0.0
        with self.pv_lock:
            self.pv_buf = np.zeros(0, np.float32)
            self.pv_primed = False
        self.meter = 0.0
        self.level_db = -120.0

    # ------------------------------------------------------------ elaborazione
    def _cb(self, indata, frames, t, st):
        ch = int(self.cfg.get("channel") or 1) - 1
        x = indata[:, ch] if indata.ndim == 2 and indata.shape[1] > ch else indata.reshape(-1)
        self.process(x)

    def process(self, x):
        """Un blocco di campioni del canale scelto: gate, inviluppo e (se richiesto) ascolto in cuffia."""
        x = np.asarray(x, np.float32).reshape(-1)
        self.feed(float(np.abs(x).max()) if len(x) else 0.0, len(x))
        if not self.cfg.get("to_preview") or not len(x):
            return
        # dopo il gate: passa mentre il segnale supera la soglia e durante l'hold (rampa sul blocco: niente clic)
        g1 = 1.0 if (self.gate or self.hold_left > 0) else 0.0
        g0, self.pv_g = self.pv_g, g1
        if g0 == 0.0 and g1 == 0.0:
            y = np.zeros(len(x), np.float32)
        elif g0 == g1:
            y = x.copy()
        else:
            y = x * np.linspace(g0, g1, len(x), dtype=np.float32)
        with self.pv_lock:
            buf = np.concatenate((self.pv_buf, y))
            if len(buf) > SR // 5:                     # mai più di 200 ms di ritardo
                buf = buf[-SR // 20:]
            self.pv_buf = buf

    def read_preview(self, frames):
        """Campioni per l'uscita del preascolto (None se l'ascolto è spento o non c'è ancora nulla)."""
        if not self.cfg.get("to_preview") or self.stream is None and not len(self.pv_buf):
            return None
        with self.pv_lock:
            buf = self.pv_buf
            if not self.pv_primed:
                if len(buf) < frames + 512:            # un piccolo margine contro i buchi
                    return None
                self.pv_primed = True
            if len(buf) < frames:                      # l'ingresso è in ritardo: si riparte con il margine
                self.pv_primed = False
                out = np.zeros(frames, np.float32)
                out[:len(buf)] = buf
                self.pv_buf = np.zeros(0, np.float32)
                return out
            out, self.pv_buf = buf[:frames], buf[frames:]
            return out

    def feed(self, peak, frames):
        """Un blocco di «frames» campioni con questo picco (lineare): aggiorna gate e inviluppo."""
        self.level_db = lin_to_db(peak)
        self.meter = max(peak, self.meter * 0.92 ** (frames / 256))
        self.gate = self.level_db >= float(self.cfg.get("threshold_db", -40.0))
        dt = frames / SR
        if self.gate:
            a = float(self.cfg.get("attack_ms", 50)) / 1000.0
            self.env = 1.0 if a <= 0 else min(1.0, self.env + dt / a)
            self.hold_left = float(self.cfg.get("hold_ms", 1000)) / 1000.0
        else:
            if self.hold_left > 0:            # hold: la riduzione resta com'è
                used = min(self.hold_left, dt)
                self.hold_left -= used
                dt -= used
            if dt <= 0:
                return
            r = float(self.cfg.get("release_ms", 800)) / 1000.0
            self.env = 0.0 if r <= 0 else max(0.0, self.env - dt / r)
