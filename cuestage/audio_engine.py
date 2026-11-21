"""Motore audio: mixer sample-accurate con loop, curve di volume, fade, più schede audio
contemporaneamente e matrice di routing (canali sorgente × canali della scheda) per ogni voce."""
from .i18n import tr
import re
import threading
import time

import numpy as np

from .media import SR, fade_value
from .system import IS_WIN

try:
    import sounddevice as sd
except Exception:  # PortAudio mancante
    sd = None

BLOCK = 512
# dimensione del buffer audio: (campioni per blocco, latenza richiesta in s). Più grande = meno rischio di
# «underrun» (buchi nell'audio quando il computer è impegnato, es. col video), ma partenza un po' più lenta.
# 128 e 256: per computer veloci e scarichi (vedi Aiuto › Diagnostica › Prestazioni, che consiglia il buffer)
BUFFERS = {"minima": (128, 0.012), "molto_bassa": (256, 0.025), "bassa": (512, 0.05), "normale": (1024, 0.12),
           "alta": (2048, 0.25), "massima": (4096, 0.5)}
BUFFER_LABELS = {"minima": tr("Latenza minima (128 · ~12 ms) — computer dedicato, scheda diretta"),
                 "molto_bassa": tr("Latenza molto bassa (256 · ~25 ms) — computer veloce e scarico"),
                 "bassa": tr("Bassa latenza (512 · ~50 ms) — solo per computer scarichi"),
                 "normale": tr("Normale (1024 · ~120 ms)"), "alta": tr("Sicura (2048 · ~250 ms)"),
                 "massima": tr("Massima sicurezza (4096 · ~500 ms)")}


# preascolto: scheda e 2 canali (cuffia del tecnico) a cui vanno le cue con «Preascolto», al posto delle uscite
PREVIEW_DEFAULTS = {"enabled": False, "device": "", "ch_l": 1, "ch_r": 2, "db": 0.0}


def preview_settings(settings):
    d = dict(PREVIEW_DEFAULTS)
    d.update((settings or {}).get("preview") or {})
    d["enabled"] = bool(d.get("enabled"))
    d["device"] = d.get("device") or ""
    d["ch_l"] = max(1, int(d.get("ch_l") or 1))
    d["ch_r"] = max(1, int(d.get("ch_r") or 2))
    d["db"] = max(-60.0, min(12.0, float(d.get("db") or 0.0)))
    return d


def preview_matrix(n_src, n_out, ch_l, ch_r):
    """Matrice sorgente × uscite del preascolto: tutto in stereo sui canali ch_l / ch_r (da 1)."""
    m = np.zeros((max(1, n_src), max(0, n_out)), np.float32)
    if n_out <= 0:
        return m
    left = min(ch_l, n_out) - 1
    right = min(ch_r, n_out) - 1
    if n_src <= 1:
        m[0, left] = m[0, right] = 0.7071 if left != right else 1.0
        return m
    k = max(1.0, n_src / 2.0)
    for i in range(n_src):
        m[i, left if i % 2 == 0 else right] = 1.0 / np.sqrt(k)
    return m


def silence_alsa_messages():
    """Le librerie ALSA scrivono «underrun occurred» sul terminale a ogni buco: li conta Cuevivo
    (barra di stato) e il terminale resta leggibile."""
    try:
        import ctypes
        import ctypes.util
        name = ctypes.util.find_library("asound")
        if not name:
            return False
        lib = ctypes.cdll.LoadLibrary(name)
        proto = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
        silence_alsa_messages.handler = proto(lambda *a: None)    # il riferimento deve restare vivo
        lib.snd_lib_error_set_handler(silence_alsa_messages.handler)
        return True
    except Exception:
        return False
MAX_OUT_CHANNELS = 64
SYSTEM = "__system__"     # chiave della scheda predefinita di sistema
I16 = np.float32(1.0 / 32768.0)


# ======================================================================= matrice

def db_lin(db):
    if db is None:
        return 0.0
    db = float(db)
    return 0.0 if db <= -60.0 else 10.0 ** (db / 20.0)


def default_matrix(n_src, n_out):
    """Routing predefinito (dB o None = spento): mono → 1 e 2; stereo → L→1, R→2;
    multicanale → canale i → uscita i. Con un'uscita sola si somma tutto."""
    m = [[None] * n_out for _ in range(n_src)]
    if n_out <= 0:
        return m
    if n_out == 1:
        att = -20.0 * np.log10(max(1, n_src)) if n_src > 1 else 0.0
        for s in range(n_src):
            m[s][0] = round(float(att), 1)
        return m
    if n_src == 1:
        m[0][0] = m[0][1] = 0.0
        return m
    if n_out == 2 and n_src in (6, 8):
        # downmix 5.1 / 7.1 (ordine FFmpeg: L R C LFE poi surround) su stereo
        m[0][0], m[1][1] = 0.0, 0.0
        m[2][0] = m[2][1] = -3.0
        for s in range(4, n_src):
            m[s][(s - 4) % 2] = -3.0
        return m
    for s in range(min(n_src, n_out)):
        m[s][s] = 0.0
    return m


def effective_matrix(matrix, n_src, n_out):
    """Matrice della cue adattata alle dimensioni reali (sorgente e scheda); vuota = predefinita."""
    if not matrix:
        return default_matrix(n_src, n_out)
    base = default_matrix(n_src, n_out)
    out = []
    for s in range(n_src):
        row = list(matrix[s]) if s < len(matrix) and isinstance(matrix[s], list) else None
        if row is None:
            out.append(base[s])
            continue
        out.append([row[o] if o < len(row) else None for o in range(n_out)])
    return out


def effective_levels(levels, n_out):
    lv = list(levels or [])
    return [float(lv[o]) if o < len(lv) and lv[o] is not None else 0.0 for o in range(n_out)]


def build_gains(matrix, levels, n_src, n_out):
    """Matrice di guadagni lineari (n_src × n_out) pronta per il mixer."""
    m = effective_matrix(matrix, n_src, n_out)
    lv = effective_levels(levels, n_out)
    g = np.zeros((max(1, n_src), max(1, n_out)), np.float32)
    for s in range(n_src):
        for o in range(n_out):
            g[s, o] = db_lin(m[s][o]) * db_lin(lv[o])
    return g


# ======================================================================= dispositivi

WIN_APIS = ("Windows WASAPI", "ASIO")


def _base_name(name):
    return re.sub(r"\s*\((hw|plughw):[^)]*\)\s*$", "", name).strip()


def list_devices():
    """Schede con uscite: [{id, name, hostapi, index, max_ch, virtual}]."""
    if sd is None:
        return []
    try:
        apis = sd.query_hostapis()
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_output_channels", 0) <= 0:
                continue
            api = apis[d["hostapi"]]["name"]
            name = d["name"]
            # i dispositivi ALSA "virtuali" (default, pipewire, pulse, dmix…) dichiarano molti
            # canali ma sono stereo: si usano 2 canali salvo diversa impostazione
            virtual = api == "ALSA" and "(hw:" not in name
            out.append({"id": f"{api}|{_base_name(name)}", "name": name, "hostapi": api, "index": i,
                        "max_ch": int(d["max_output_channels"]), "virtual": virtual})
        if IS_WIN:
            # Windows mostra ogni scheda fino a 4 volte (MME, DirectSound, WASAPI, WDM-KS): si tengono
            # WASAPI (bassa latenza, nomi completi) e ASIO se c'è; le altre solo se mancano entrambe
            good = [d for d in out if d["hostapi"] in WIN_APIS]
            out = good or out
        return out
    except Exception:
        return []


def _extra_settings(dev):
    """WASAPI condiviso accetta solo la frequenza impostata in Windows: con auto_convert apre a 48 kHz
    anche se la scheda è a 44,1 kHz (conversione di Windows)."""
    if not dev or dev.get("hostapi") != "Windows WASAPI" or sd is None:
        return {}
    try:
        return {"extra_settings": sd.WasapiSettings(exclusive=False, auto_convert=True)}
    except TypeError:                         # sounddevice vecchio: senza auto_convert
        return {}


def alsa_pcm_owner(card, dev):
    """Chi tiene aperta l'uscita ALSA hw:card,dev (nome del programma), "" se è libera, None se non si sa."""
    import os
    base = f"/proc/asound/card{card}/pcm{dev}p"
    if not os.path.isdir(base):
        return None
    try:
        with open(f"{base}/sub0/status", encoding="utf-8", errors="replace") as f:
            st = f.read()
    except OSError:
        return None
    if st.strip() == "closed":
        return ""
    m = re.search(r"owner_pid\s*:\s*(\d+)", st)
    if not m:
        return tr("un altro programma")
    try:
        with open(f"/proc/{m.group(1)}/comm", encoding="utf-8") as f:
            return tr("{0} (pid {1})", f.read().strip(), m.group(1))
    except OSError:
        return f"pid {m.group(1)}"


def busy_devices():
    """Schede hardware ALSA con uscite che PortAudio non può usare perché un altro programma (di solito
    PipeWire/PulseAudio) le tiene aperte: [{name, owner}] — PortAudio le elenca con 0 uscite."""
    if sd is None or IS_WIN:
        return []
    out = []
    try:
        apis = sd.query_hostapis()
        for d in sd.query_devices():
            if d.get("max_output_channels", 0) > 0 or apis[d["hostapi"]]["name"] != "ALSA":
                continue
            m = re.search(r"\(hw:(\d+),(\d+)\)", d["name"])
            if not m:
                continue
            owner = alsa_pcm_owner(m.group(1), m.group(2))
            if owner:
                out.append({"name": _base_name(d["name"]), "owner": owner})
    except Exception:
        return []
    return out


def device_label(dev):
    return f"{dev['name']}  [{dev['hostapi']}]"


class Output:
    """Uscita aperta su una scheda: flusso PortAudio (o simulato se la scheda non c'è)."""

    def __init__(self, key, name, channels):
        self.key = key
        self.name = name
        self.channels = channels
        self.stream = None
        self.meter = np.zeros(channels, np.float32)
        self.null = False
        self.underruns = 0
        # diagnostica: tempo di calcolo dei blocchi (s) rispetto alla loro durata
        self.cb_last = 0.0
        self.cb_avg = 0.0
        self.cb_max = 0.0
        self.cb_frames = 0
        self.cb_count = 0
        self.prio = None           # (riuscito, descrizione) della priorità del thread audio (rt.py)


class BusRuntime:
    """Bus effetti interno: riceve le mandate delle cue (stereo), passa nel suo rack (riverbero, delay…) e
    va alle uscite della sua scheda con la sua matrice (2 canali × uscite), il suo volume e il mute.
    Gira anche quando nessuna cue manda: le code di riverbero e delay si spengono da sole."""
    IDLE = int(3 * SR)          # dopo 3 s di silenzio (entrata e coda) il bus non calcola più nulla

    def __init__(self, spec, key):
        from .dsp import BUS_FX, Rack
        self.id = spec.get("id")
        self.key = key
        self.rack = Rack(spec.get("fx") or [], 2, BUS_FX)
        self.g_prev = None
        self.meter = np.zeros(2, np.float32)
        self.silent = self.IDLE
        self._gcache = {}
        self.live = None            # ingresso fisico (audio_input.LiveInput) o None
        self.in_gain = 1.0
        self.update(spec, key)

    def update(self, spec, key):
        self.key = key
        self.rack.update(spec.get("fx") or [])
        self.spec = spec
        lvl = -120.0 if spec.get("mute") else float(spec.get("level_db", 0.0) or 0.0)
        self.level = db_lin(lvl) if lvl > -60.0 else 0.0
        self._gcache = {}

    def sync_input(self):
        """Ingresso fisico: si apre / chiude / riapre solo se cambiano accensione, scheda o canali (fuori dal
        lock del mixer: aprire una scheda può richiedere un po')."""
        spec = self.spec
        inp = dict({"on": False, "device": "", "ch": 1, "ch2": 0, "trim": 0.0}, **(spec.get("input") or {}))
        self.in_gain = db_lin(float(inp.get("trim", 0.0) or 0.0))
        if not inp.get("on"):
            if self.live is not None:
                self.live.close()
                self.live = None
        elif self.live is None or not self.live.same(inp.get("device"), inp.get("ch"), inp.get("ch2")):
            from .audio_input import LiveInput
            if self.live is not None:
                self.live.close()
            self.live = LiveInput(inp.get("device"), inp.get("ch"), inp.get("ch2"))
            self.live.open()

    def close(self):
        if self.live is not None:
            self.live.close()
            self.live = None

    def gains(self, C):
        g = self._gcache.get(C)
        if g is None:
            g = build_gains(self.spec.get("matrix"), self.spec.get("out_levels"), 2, C)
            self._gcache[C] = g
        return g

    def process(self, inp, frames, C):
        """Uscita del bus (frames × C) dall'entrata (frames × 2), o None se il bus è in silenzio."""
        live = self.live.read(frames) if self.live is not None else None
        if live is not None:
            inp += live * self.in_gain
        if not inp.any():
            self.silent += frames
            if self.silent > self.IDLE:
                self.meter = self.meter * 0.85
                self.g_prev = self.level
                return None
        else:
            self.silent = 0
        y = self.rack.process(inp) if self.rack.order else inp
        if y.shape[1] != 2:
            y = np.repeat(y[:, :1], 2, axis=1)
        g0 = self.level if self.g_prev is None else self.g_prev
        if abs(g0 - self.level) > 1e-6:
            y = y * np.linspace(g0, self.level, frames, dtype=np.float32)[:, None]
        else:
            y = y * self.level
        self.g_prev = self.level
        pk = np.abs(y).max(axis=0) if frames else np.zeros(2, np.float32)
        if float(pk.max()) > 1e-5:
            self.silent = 0
        self.meter = np.maximum(pk.astype(np.float32), self.meter * 0.85)
        return y @ self.gains(C)


class AudioEngine:
    def __init__(self):
        self.voices = {}
        self.buses = {}              # id -> BusRuntime (bus effetti interni)
        self.buffer = "normale"
        self.lock = threading.Lock()
        self.master = 1.0
        self.preview = dict(PREVIEW_DEFAULTS)   # impostazioni del preascolto
        self.preview_gain = 1.0                 # volume del preascolto (lineare)
        self.paused_all = False
        self.outputs = {}            # chiave scheda -> Output
        self.default_id = None       # scheda predefinita del progetto (None = sistema)
        self.channel_overrides = {}  # id scheda -> canali (0 = automatico)
        self.null_thread = None
        self.running = False
        self.messages = []           # avvisi per l'interfaccia
        self.status = tr("spento")
        from .ducking import Ducker
        self.duck = Ducker()         # ingresso del ducking (microfono → riduzione delle cue / del master)
        self._mduck = {}             # chiave uscita -> ultimo guadagno del Master ducking (rampa senza scalini)
        import collections
        self.errors = collections.deque(maxlen=200)   # errori del mixer (ora, testo) per la finestra Diagnostica

    # ------------------------------------------------------------ compatibilità
    @property
    def meter(self):
        o = self.outputs.get(self._key(None))
        if o is None or not len(o.meter):
            return [0.0, 0.0]
        return [float(o.meter[0]), float(o.meter[1] if len(o.meter) > 1 else o.meter[0])]

    @staticmethod
    def list_devices():
        return [(d["id"], device_label(d)) for d in list_devices()]

    # ------------------------------------------------------------ configurazione
    def configure(self, default_id=None, overrides=None):
        changed = (default_id != self.default_id) or (dict(overrides or {}) != self.channel_overrides)
        self.default_id = default_id or None
        self.channel_overrides = dict(overrides or {})
        return changed

    def start(self, default_id=None, overrides=None):
        """(Ri)avvia: chiude le uscite e riapre la predefinita e quelle usate dalle voci attive."""
        self.configure(default_id, overrides)
        self.stop()
        self.running = True
        self.null_thread = threading.Thread(target=self._null_loop, daemon=True)
        self.null_thread.start()
        self.ensure_output(None)
        with self.lock:
            voices = list(self.voices.values())
        for v in voices:
            o = self.ensure_output(v.out_req)
            v.out_key = o.key
            v.fit_matrix(o.channels)
        if self.buses:                           # i bus riaprono la loro scheda
            self.set_buses([b.spec for b in list(self.buses.values())])
        if self.duck.wanted():
            self.duck.open()
            if self.duck.error:
                self.messages.append(tr("Ducking: {0}", self.duck.error))
        self._update_status()

    def stop(self):
        self.running = False
        self.duck.close()
        for o in {id(x): x for x in self.outputs.values()}.values():
            if o.stream is not None:
                try:
                    o.stream.stop()
                    o.stream.close()
                except Exception:
                    pass
        self.outputs = {}
        if self.null_thread is not None:
            self.null_thread.join(timeout=1)
            self.null_thread = None

    def _key(self, dev_id):
        dev_id = dev_id or self.default_id
        return dev_id or SYSTEM

    def channels_for(self, dev):
        """Canali usati per una scheda (dict di list_devices, o None = sistema)."""
        key = dev["id"] if dev else SYSTEM
        n = int(self.channel_overrides.get(key) or 0)
        if n > 0:
            return min(n, MAX_OUT_CHANNELS)
        if dev is None or dev["virtual"]:
            return 2
        return max(1, min(dev["max_ch"], MAX_OUT_CHANNELS))

    def find_device(self, dev_id):
        devs = list_devices()
        for d in devs:
            if d["id"] == dev_id:
                return d
        # stesso nome di scheda con un altro host API: meglio di niente
        base = dev_id.split("|", 1)[-1] if dev_id else ""
        for d in devs:
            if d["id"].split("|", 1)[-1] == base:
                return d
        return None

    def ensure_output(self, dev_id):
        """Uscita per la scheda richiesta ("" o None = predefinita del progetto); la apre se serve.
        Se la scheda non è disponibile ripiega sulla predefinita del progetto (o di sistema) e lascia
        un avviso; il ripiego resta memorizzato, così l'avviso non si ripete a ogni cue."""
        key = self._key(dev_id)
        o = self.outputs.get(key)
        if o is not None:
            return o
        if key == SYSTEM:
            o = self._open(SYSTEM, None)
        else:
            dev = self.find_device(key)
            if dev is None:
                name = key.split('|', 1)[-1]
                busy = [b for b in busy_devices() if b["name"] == name]
                if busy:
                    self.messages.append(tr("Scheda audio «{0}» occupata da {1}: uso la scheda predefinita (chiudi quel programma o disattiva la scheda in PipeWire)", name, busy[0]['owner']))
                else:
                    self.messages.append(tr("Scheda audio «{0}» non trovata: uso la scheda predefinita", name))
                if dev_id and dev_id != self.default_id:
                    o = self.ensure_output(None)
                else:
                    o = self.outputs.get(SYSTEM) or self._open(SYSTEM, None)
                    self.outputs[SYSTEM] = o
            else:
                o = self._open(key, dev)
        self.outputs[key] = o
        self._update_status()
        return o

    def _open(self, key, dev):
        ch = self.channels_for(dev)
        name = dev["name"] if dev else tr("Predefinita di sistema")
        o = Output(key, name, ch)
        if sd is None:
            o.null = True
            self._note_null(tr("PortAudio non installato"))
            return o
        try:
            block, lat = BUFFERS.get(self.buffer, BUFFERS["normale"])

            def cb(outdata, frames, t, st, o=o):
                if o.prio is None:                 # primo blocco: priorità alta per il thread audio
                    from .rt import raise_thread_priority
                    o.prio = raise_thread_priority()
                if st.output_underflow:
                    o.underruns += 1
                self._callback(o, outdata, frames)
            o.stream = sd.OutputStream(
                samplerate=SR, channels=ch, dtype="float32", blocksize=block,
                device=dev["index"] if dev else None, latency=lat, callback=cb, **_extra_settings(dev))
            o.stream.start()
            if dev is None:
                try:
                    o.name = tr("Sistema ({0})", sd.query_devices(o.stream.device)['name'])
                except Exception:
                    pass
        except Exception as e:
            o.stream = None
            o.null = True
            self.messages.append(tr("Impossibile aprire «{0}» con {1} canali ({2}): uscita silenziosa", name, ch, e))
        return o

    def _note_null(self, why):
        self.status = tr("{0} – modalità silenziosa", why)

    def _update_status(self):
        parts = []
        for o in self.outputs.values():
            tag = tr(" (silenziosa)") if o.null else ""
            parts.append(f"{o.name} {o.channels}ch{tag}")
        self.status = tr("Audio: ") + " + ".join(dict.fromkeys(parts)) if parts else tr("Audio spento")

    def resolve(self, dev_id):
        """(nome, canali, trovata) della scheda che userebbe una cue con questa scelta, senza aprirla."""
        key = self._key(dev_id)
        o = self.outputs.get(key)
        if o is not None:
            # un'uscita registrata sotto un'altra chiave è un ripiego: la scheda richiesta manca
            return o.name, o.channels, o.key == key
        if key == SYSTEM:
            return tr("Predefinita di sistema"), self.channels_for(None), True
        dev = self.find_device(key)
        if dev is None:
            if dev_id and dev_id != self.default_id:
                n, c, _ = self.resolve(None)
            else:
                n, c = tr("Predefinita di sistema"), self.channels_for(None)
            return n, c, False
        return dev["name"], self.channels_for(dev), True

    def rescan(self):
        """Rilegge le schede collegate (es. scheda USB inserita dopo l'avvio) e riapre le uscite."""
        self.stop()
        if sd is not None:
            try:
                sd._terminate()
                sd._initialize()
            except Exception as e:
                self.messages.append(tr("Rilettura schede audio non riuscita: {0}", e))
        self.start(self.default_id, self.channel_overrides)

    def diagnostics(self):
        """Stato di ogni uscita per la finestra Diagnostica."""
        out = []
        for o in {id(x): x for x in self.outputs.values()}.values():
            blk = (o.cb_frames or BLOCK) / SR
            lat = 0.0
            if o.stream is not None:
                try:
                    lat = float(o.stream.latency)
                except Exception:
                    lat = 0.0
            out.append({"name": o.name, "channels": o.channels, "null": o.null, "frames": o.cb_frames,
                        "block_ms": blk * 1000, "latency_ms": lat * 1000, "last_ms": o.cb_last * 1000,
                        "avg_ms": o.cb_avg * 1000, "max_ms": o.cb_max * 1000,
                        "load": o.cb_avg / blk if blk else 0.0, "peak_load": o.cb_max / blk if blk else 0.0,
                        "underruns": o.underruns, "blocks": o.cb_count, "prio": o.prio})
        return out

    def reset_diagnostics(self):
        for o in self.outputs.values():
            o.cb_max = 0.0

    def reset_underruns(self):
        """Azzera i contatori dei buchi audio (XRUN) di tutte le schede."""
        for o in self.outputs.values():
            o.underruns = 0

    def underruns(self):
        return sum(o.underruns for o in {id(o): o for o in self.outputs.values()}.values())

    def latency(self, key):
        """Latenza di uscita della scheda (s): l'audio si sente questo tempo dopo essere stato mixato."""
        o = self.outputs.get(key)
        if o is None or o.stream is None:
            return 0.0
        try:
            return float(o.stream.latency)
        except Exception:
            return 0.0

    # ------------------------------------------------------------ uscite senza scheda
    def _null_loop(self):
        period = BLOCK / SR
        nxt = time.monotonic()
        bufs = {}
        while self.running:
            for o in list(self.outputs.values()):
                if o.null and (o.key not in bufs or bufs[o.key].shape[1] != o.channels):
                    bufs[o.key] = np.zeros((BLOCK, o.channels), np.float32)
            seen = set()
            for o in list(self.outputs.values()):
                if o.null and id(o) not in seen:
                    seen.add(id(o))
                    self._callback(o, bufs[o.key], BLOCK)
            nxt += period
            d = nxt - time.monotonic()
            if d > 0:
                time.sleep(d)
            elif d < -0.25:
                nxt = time.monotonic()      # molto in ritardo (sistema fermo): si riparte da adesso
            # in leggero ritardo (sleep poco preciso, macchina virtuale lenta): si calcolano subito i blocchi
            # mancanti, così il tempo delle uscite senza scheda resta quello vero

    # ------------------------------------------------------------ API voci
    def play(self, vid, voice, dev_id=None):
        o = self.ensure_output(dev_id)
        voice.out_req = dev_id or ""
        voice.out_key = o.key
        voice.fit_matrix(o.channels)
        with self.lock:
            self.voices[vid] = voice
        return o

    def route(self, vid, dev_id, gains_fn):
        """Cambia scheda e/o matrice di una voce in riproduzione. gains_fn(n_out) -> matrice."""
        v = self.voices.get(vid)
        if v is None:
            return
        o = self.ensure_output(dev_id)
        v.out_req = dev_id or ""
        if o.key == v.out_key:
            v.set_mtx(gains_fn(o.channels))
        else:
            v.mtx = np.asarray(gains_fn(o.channels), np.float32)
            v.mtx_prev = None
            v.out_key = o.key

    def stop_voice(self, vid, fade=0.0):
        with self.lock:
            v = self.voices.get(vid)
            if not v:
                return
            if fade > 0 and not v.paused and not self.paused_all:
                v.ramp = [self._env_now(v), 0.0, 0, int(fade * SR), "scurve", True]
            else:
                v.done = True

    def fade(self, vid, target, dur, shape="scurve", stop_after=False):
        with self.lock:
            v = self.voices.get(vid)
            if not v:
                return
            if dur <= 0:
                v.env = target
                v.ramp = None
                if stop_after:
                    v.done = True
            else:
                v.ramp = [self._env_now(v), target, 0, int(dur * SR), shape, stop_after]

    def seek(self, vid, t):
        with self.lock:
            v = self.voices.get(vid)
            if v is not None:
                v.seek(t)

    def pause(self, vid, paused=True):
        with self.lock:
            v = self.voices.get(vid)
            if v:
                v.paused = paused

    def get(self, vid):
        return self.voices.get(vid)

    def remove(self, vid):
        with self.lock:
            self.voices.pop(vid, None)

    def stop_all(self, fade=0.0):
        for vid in list(self.voices):
            self.stop_voice(vid, fade)

    # ------------------------------------------------------------ bus effetti
    def set_buses(self, specs):
        """Bus effetti dello show: [{id, name, device, fx, matrix, out_levels, level_db, mute, input}]. Quelli
        che restano conservano lo stato (code del riverbero in corso)."""
        keys = {}
        for s in specs or []:
            if s.get("id"):
                keys[s["id"]] = self.ensure_output(s.get("device") or None).key
        with self.lock:
            old = dict(self.buses)
            new = {}
            for s in specs or []:
                bid = s.get("id")
                if not bid:
                    continue
                b = old.get(bid)
                if b is None:
                    b = BusRuntime(s, keys[bid])
                else:
                    b.update(s, keys[bid])
                new[bid] = b
            self.buses = new
        for b in new.values():                   # fuori dal lock: un ingresso può impiegare un po' ad aprirsi
            b.sync_input()
        for bid, b in old.items():
            if bid not in new:
                b.close()

    def bus_input_meter(self, bid):
        """Picchi dell'ingresso fisico del bus (L, R) e l'eventuale errore di apertura."""
        b = self.buses.get(bid)
        if b is None or b.live is None:
            return None, ""
        return [float(x) for x in b.live.meter], b.live.error

    def reset_buses(self):
        """Azzera le code dei bus (secondo STOP / panico)."""
        with self.lock:
            for b in self.buses.values():
                b.rack.reset()
                b.silent = b.IDLE

    def bus_source_meters(self, bid):
        """Picchi (L, R) di ciò che ogni cue manda al bus: {id cue: [L, R]} (più voci della stessa cue: il massimo).
        Le cue che non suonano non compaiono."""
        out = {}
        with self.lock:
            voices = list(self.voices.values())
        for v in voices:
            sm = getattr(v, "send_meter", None)
            pk = sm.get(bid) if sm is not None and not (v.done or v.paused) else None
            if pk is None or not getattr(v, "src_id", ""):
                continue
            cur = out.get(v.src_id)
            out[v.src_id] = [float(x) for x in (pk if cur is None else np.maximum(pk, cur))]
        return out

    def bus_meter(self, bid):
        b = self.buses.get(bid)
        return [float(x) for x in b.meter] if b is not None else [0.0, 0.0]

    @staticmethod
    def _env_now(v):
        if not v.ramp:
            return v.env
        g0, g1, done, length, shape, _ = v.ramp
        return float(fade_value(g0, g1, done / max(length, 1), shape))

    # ------------------------------------------------------------ mixer
    def note_error(self, where, e):
        """Errore nel thread audio: non ferma il mixer, finisce nell'elenco della finestra Diagnostica."""
        txt = f"{where}: {type(e).__name__}: {e}"
        if not self.errors or self.errors[-1][1] != txt:
            self.errors.append((time.time(), txt))

    def _callback(self, o, outdata, frames):
        t0 = time.perf_counter()
        try:
            self._mix_block(o, outdata, frames)
        except Exception as e:
            self.note_error(tr("mixer «{0}»", o.name), e)
            outdata[:] = 0
        dt = time.perf_counter() - t0
        o.cb_last = dt
        o.cb_avg = dt if o.cb_count == 0 else o.cb_avg * 0.95 + dt * 0.05
        o.cb_max = max(o.cb_max, dt)
        o.cb_frames = frames
        o.cb_count += 1

    def _mix_block(self, o, outdata, frames):
        C = o.channels
        out = np.zeros((frames, C), np.float32)
        pout = None                       # preascolto: fuori dal master e dal Master ducking
        if not self.paused_all:
            with self.lock:
                voices = [v for v in self.voices.values() if v.out_key == o.key]
                buses = [b for b in self.buses.values() if b.key == o.key]
            bus_in = {b.id: np.zeros((frames, 2), np.float32) for b in buses}
            for v in voices:
                if v.paused or v.done:
                    continue
                try:
                    if v.pfl:
                        if pout is None:
                            pout = np.zeros((frames, C), np.float32)
                        self._mix_voice(v, pout, frames, C)
                    else:
                        self._mix_voice(v, out, frames, C, bus_in)
                except Exception as e:
                    self.note_error(tr("voce"), e)
                    v.done = True
            for b in buses:                      # bus effetti: mandate delle cue → rack → uscite
                try:
                    y = b.process(bus_in[b.id], frames, C)
                    if y is not None:
                        out += y
                except Exception as e:
                    self.note_error(tr("bus «{0}»", b.spec.get("name") or b.id), e)
        # ingresso del ducking dopo il gate, nella cuffia del preascolto
        if self.preview.get("enabled") and o.key == self._key(self.preview.get("device") or None):
            mic = self.duck.read_preview(frames)
            if mic is not None and C > 0:
                if pout is None:
                    pout = np.zeros((frames, C), np.float32)
                left = min(int(self.preview.get("ch_l", 1)), C) - 1
                right = min(int(self.preview.get("ch_r", 2)), C) - 1
                pout[:, left] += mic
                if right != left:
                    pout[:, right] += mic
        out *= self.master
        # Master ducking: rampa dal guadagno del blocco precedente, così non ci sono scalini
        g1 = self.duck.master_gain()
        g0 = self._mduck.get(o.key, 1.0)
        if g0 != 1.0 or g1 != 1.0:
            if frames and abs(g1 - g0) > 1e-5:
                out *= np.linspace(g0, g1, frames, dtype=np.float32)[:, None]
            else:
                out *= g1
        self._mduck[o.key] = g1
        if pout is not None:
            out += pout * self.preview_gain
        np.clip(out, -1.0, 1.0, out=out)
        if frames:
            pk = np.abs(out).max(axis=0)
            if o.meter.shape[0] != C:
                o.meter = np.zeros(C, np.float32)
            o.meter = np.maximum(pk, o.meter * 0.85)
        outdata[:] = out

    def _mix_voice(self, v, out, frames, C, bus_in=None):
        idx = np.empty(frames, np.int64)
        idx2 = None                      # crossfade del loop: posizione all'inizio del loop e peso (0→1)
        filled = 0
        p = v.pos
        while filled < frames:
            looping = v.loop and v.loops_left != 0
            seg_end = v.le if looping else v.out_end()
            xf, pre = v.loop_xfade() if looping else (0, False)
            xs = seg_end - xf
            if xf > 0 and p >= xs:
                # a fine loop − xf parte l'inizio del loop mentre la coda sfuma; poi si continua da ls + xf
                take = min(frames - filled, seg_end - p)
                if take > 0:
                    if idx2 is None:
                        idx2 = np.full(frames, -1, np.int64)
                        wgt = np.zeros(frames, np.float32)
                    r = np.arange(p, p + take)
                    idx[filled:filled + take] = r
                    idx2[filled:filled + take] = (v.ls - xf if pre else v.ls) + (r - xs)
                    wgt[filled:filled + take] = (r - xs + 0.5) / xf
                    p += take
                    filled += take
                    continue
                if v.loops_left > 0:
                    v.loops_left -= 1
                p = v.ls if pre else v.ls + xf
                v.wraps += 1
                continue
            take = min(frames - filled, (xs if xf > 0 else seg_end) - p)
            if take <= 0:
                if looping:
                    if v.loops_left > 0:
                        v.loops_left -= 1
                    p = v.ls
                    v.wraps += 1
                    continue
                v.done = True
                break
            idx[filled:filled + take] = np.arange(p, p + take)
            p += take
            filled += take
        v.pos = p
        if filled == 0:
            return
        idx = idx[:filled]
        v.played += filled
        g = np.ones(filled, np.float32)
        if v.curve is not None:
            ct, cd = v.curve
            db = np.interp(idx / SR, ct, cd)
            cg = np.power(10.0, db / 20.0)
            cg[db <= -59.99] = 0.0
            g *= cg.astype(np.float32)
        if v.fade_out > 0 and not (v.loop and v.loops_left != 0):
            rem = (v.out_end() - idx).astype(np.float32)
            g *= np.clip(rem / v.fade_out, 0.0, 1.0)
        if v.ramp:
            g0, g1, done, length, shape, stop_after = v.ramp
            t = (done + np.arange(filled)) / max(length, 1)
            env = fade_value(g0, g1, t, shape)
            g *= np.asarray(env, np.float32)
            v.ramp[2] = done + filled
            if v.ramp[2] >= length:
                v.env = g1
                v.ramp = None
                if stop_after:
                    v.done = True
        else:
            g *= v.env
        # ducking della cue: rampa dal guadagno del blocco precedente
        if v.duck_db > 0 or v.duck_g != 1.0:
            d1 = self.duck.gain(v.duck_db)
            if abs(d1 - v.duck_g) > 1e-5:
                g *= np.linspace(v.duck_g, d1, filled, dtype=np.float32)
            else:
                g *= d1
            v.duck_g = d1
        t0, t1 = v.trim_g, v.trim_t          # OUT TRIM della cue (normalizzazione): prima di mandate e misuratori
        if abs(t1 - t0) > 1e-6:
            g *= np.linspace(t0, t1, filled, dtype=np.float32)
            v.trim_g = t1
        elif t1 != 1.0:
            g *= t1
        src = v.data[idx]
        if src.dtype == np.int16:
            src = src.astype(np.float32) * I16
        if idx2 is not None:                 # crossfade del loop a potenza costante: coda ↘, inizio ↗
            k = idx2[:filled] >= 0
            if k.any():
                head = v.data[idx2[:filled][k]].astype(np.float32)
                if v.data.dtype == np.int16:
                    head *= I16
                wk = wgt[:filled][k][:, None] * (np.pi / 2)
                src = src.astype(np.float32, copy=False)
                src[k] = src[k] * np.cos(wk) + head * np.sin(wk)
        if src.ndim == 1:
            src = src[:, None]
        fx = v.fx
        if fx is not None and fx.order:      # rack della cue (gate, EQ, compressore): prima del volume e dei fade
            src = fx.process(src)
        sends = v.sends if (bus_in and not getattr(v, "blind", False)) else ()
        pre = src.copy() if any(s[2] for s in sends) else None
        src = src.astype(np.float32, copy=False)
        src *= g[:, None]
        tap = getattr(v, "loud_tap", None)
        if tap is not None:                  # misuratore LUFS: copia del suono della cue (dopo volume, fade e OUT TRIM)
            tap.append(src.copy())
        for bid, m2, is_pre in sends:        # mandate ai bus effetti (pre o post volume/fade)
            dst = bus_in.get(bid)
            if dst is not None and m2.shape[0] == src.shape[1]:
                part = (pre if is_pre else src) @ m2
                dst[:filled] += part
                # vumeter della sorgente nel bus (L, R dopo mandata e trim), con discesa come gli altri
                pk = np.abs(part).max(axis=0).astype(np.float32) if len(part) else np.zeros(2, np.float32)
                old_pk = v.send_meter.get(bid)
                v.send_meter[bid] = np.maximum(pk, old_pk * 0.8) if old_pk is not None else pk
        m = v.mtx
        prev = v.mtx_prev
        if m.shape[1] != C:
            m = v.fit_matrix(C)
            prev = None
        # vumeter della voce: picco di ogni canale sorgente × guadagno più alto verso le uscite × master
        pk = np.abs(src).max(axis=0) * (m.max(axis=1) if m.size else 0.0) * \
            (self.preview_gain if v.pfl else self.master)
        v.meter = np.maximum(pk.astype(np.float32), v.meter * 0.8) if v.meter.shape == pk.shape else pk
        if getattr(v, "blind", False):
            pass                         # BLIND: vumeter sì, uscita no
        elif prev is not None and prev.shape == m.shape:
            # cambio di matrice (keyframe, modifica dal vivo): rampa lungo il blocco, senza scalini
            v.mtx_prev = None
            a = src @ prev
            b = src @ m
            r = np.linspace(0.0, 1.0, filled, dtype=np.float32)[:, None]
            out[:filled] += a + (b - a) * r
        else:
            out[:filled] += src @ m


class Voice:
    def __init__(self, data, start, end, loop, loop_start, loop_end, loop_count,
                 level, curve, fade_in, fade_out, gains_fn=None):
        n = len(data)
        self.data = data
        self.n_src = data.shape[1] if data.ndim == 2 else 1
        s = int(max(0.0, start) * SR)
        e = int(end * SR) if end and end > 0 else n
        e = min(max(e, 0), n)
        s = min(s, max(e - 1, 0))
        self.start, self.end = s, e
        self.loop = bool(loop)
        ls = int(loop_start * SR) if loop_start and loop_start > 0 else s
        le = int(loop_end * SR) if loop_end and loop_end > 0 else e
        ls = min(max(ls, s), e)
        le = min(max(le, ls + 1), e)
        if le - ls < 64:  # loop troppo corto: disattiva
            self.loop = False
        self.ls, self.le = ls, le
        self.loop_xf = 0          # crossfade del loop (campioni): la ripetizione riparte morbida
        self.stop_after_loop = False   # finite le ripetizioni la voce si ferma a fine loop (non va fino a OUT)
        self.loops_left = (loop_count - 1) if loop_count and loop_count > 0 else -1
        self.pos = s
        self.played = 0
        self.wraps = 0
        self.curve = curve  # (tempi, dB) o None
        self.fade_out = int(max(0.0, fade_out) * SR)
        self.level = level
        if fade_in > 0:
            self.env = 0.0
            self.ramp = [0.0, level, 0, int(fade_in * SR), "scurve", False]
        else:
            self.env = level
            self.ramp = None  # [g0, g1, fatto, durata, forma, stop_alla_fine]
        self.paused = False
        self.done = False
        self.duck_db = 0.0     # riduzione (dB) quando il ducking interviene; la imposta l'istanza
        self.duck_g = 1.0      # guadagno di ducking del blocco precedente
        self.trim_g = 1.0      # OUT TRIM della cue (lineare) del blocco precedente
        self.trim_t = 1.0      # OUT TRIM da raggiungere: rampa lungo il blocco, senza click
        self.pfl = False       # preascolto: va solo alla cuffia, fuori dal master
        self.fx = None         # rack della cue (dsp.Rack) o None
        self.sends = []        # mandate ai bus: [(id bus, matrice n_src × 2 lineare, pre)]
        self.send_meter = {}   # id bus -> picchi (L, R) di quanto la voce manda al bus (vumeter delle sorgenti)
        self.src_id = ""       # id della cue che suona (per i vumeter delle sorgenti del bus)
        self.loud_tap = None   # collections.deque per il misuratore LUFS in tempo reale (Normalizza › Misuratore)
        # routing
        self.gains_fn = gains_fn or (lambda n_out: build_gains(None, None, self.n_src, n_out))
        self.out_req = ""
        self.out_key = None
        self.mtx = np.zeros((self.n_src, 0), np.float32)   # calcolata al primo blocco del mixer
        self.mtx_prev = None
        self.meter = np.zeros(self.n_src, np.float32)   # picchi per canale sorgente (vumeter delle cue attive)

    def fit_matrix(self, n_out):
        self.mtx = np.asarray(self.gains_fn(n_out), np.float32).reshape(self.n_src, n_out)
        self.mtx_prev = None
        return self.mtx

    def loop_xfade(self):
        """(campioni di crossfade del loop, False): quando il cursore arriva a fine loop − crossfade l'inizio del
        loop entra mentre la coda sfuma; finito il crossfade si continua da inizio loop + crossfade."""
        xf = min(int(self.loop_xf), (self.le - self.ls) // 2)
        return (xf, False) if xf > 0 else (0, False)

    def out_end(self):
        """Dove finisce la voce quando non ripete più: fine loop se «Ferma alla fine delle ripetizioni», o OUT."""
        return self.le if self.loop and self.stop_after_loop else self.end

    def lap_len(self):
        """Campioni di un giro di loop (con il crossfade un giro è più corto di quel tempo)."""
        xf, pre = self.loop_xfade()
        return self.le - self.ls - (0 if pre else xf)

    def set_bounds(self, end, loop, loop_start, loop_end):
        """OUT e marcatori del loop cambiati mentre suona (secondi del file, 0 = OUT/IN): valgono subito.
        Se la posizione è già oltre la nuova fine del loop, il giro riparte dall'inizio del loop."""
        n = len(self.data)
        e = int(end * SR) if end and end > 0 else n
        e = min(max(e, self.start + 1), n)
        ls = int(loop_start * SR) if loop_start and loop_start > 0 else self.start
        le = int(loop_end * SR) if loop_end and loop_end > 0 else e
        ls = min(max(ls, self.start), e)
        le = min(max(le, ls + 1), e)
        self.end = e
        self.ls, self.le = ls, le
        self.loop = bool(loop) and le - ls >= 64

    def set_mtx(self, m):
        """Nuova matrice; il mixer la raggiunge con una rampa sul blocco successivo."""
        m = np.asarray(m, np.float32)
        if self.mtx.shape == m.shape and self.mtx.size and self.mtx_prev is None:
            self.mtx_prev = self.mtx
        self.mtx = m

    def n_out(self):
        return self.mtx.shape[1]

    def looping_forever(self):
        return self.loop and self.loops_left < 0

    def seek(self, t):
        """Porta la voce a «t» secondi dall'avvio (loop compresi), come se suonasse da allora: fade d'ingresso
        già finito. Serve per allinearsi a un'altra istanza (ridondanza)."""
        n = max(0, int(t * SR))
        xf, pre = self.loop_xfade() if self.loop else (0, False)
        back = 0 if pre else xf                           # dove riparte ogni giro: ls (o ls + xf)
        first = self.le - self.start if self.loop else self.end - self.start
        if n < first:
            self.pos = self.start + n
        elif self.loop and self.lap_len() > 0:
            k = n - first
            span = self.lap_len()
            laps = k // span + 1
            if self.loops_left >= 0 and laps > self.loops_left:       # loop finiti: poi fino alla fine
                rest = k - self.loops_left * span
                self.loops_left = 0
                self.pos = min(self.end, self.le + rest) if rest >= 0 else self.ls + back + k % span
            else:
                if self.loops_left > 0:
                    self.loops_left -= laps
                self.pos = self.ls + back + k % span
                self.wraps += laps
        else:
            self.pos = self.end
        self.played = n
        if self.ramp is not None and not self.ramp[5]:       # fade d'ingresso in corso: già finito
            self.env = self.ramp[1]
            self.ramp = None

    def position(self):
        return self.pos / SR
