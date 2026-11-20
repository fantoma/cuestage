"""Effetti audio interni (rack): gate, equalizzatore parametrico a 5 bande con passa-alto e passa-basso,
compressore, delay e riverbero. Scritti in numpy + scipy.signal (filtri in C con memoria tra un blocco e
l'altro), così suonano uguali su Linux, Windows e macOS e si possono provare.

Un rack è una lista di effetti nell'ordine scelto: [{"id", "type", "on", …parametri}]. Il motore audio
chiama Rack.process(blocco) nel thread audio; i parametri cambiati dall'interfaccia valgono dal blocco
successivo (Rack.update), senza perdere lo stato (code del riverbero, eco del delay, filtri).
Le cue usano gate, EQ e compressore (insert, prima del volume e dei fade); i bus anche delay e riverbero.
"""
import math
import uuid

import numpy as np

from .i18n import tr
from .media import SR

try:
    from scipy.signal import lfilter, sosfilt
    IMPORT_ERROR = ""
except Exception as _e:               # scipy mancante: gli effetti restano spenti (il suono passa così com'è)
    lfilter = sosfilt = None
    IMPORT_ERROR = f"{type(_e).__name__}: {_e}"


def available():
    return sosfilt is not None


def unavailable_reason():
    """Perché gli effetti non sono disponibili: errore dell'import e Python in uso (per l'avviso)."""
    import sys
    return tr("errore: {0} · Python in uso: {1}", IMPORT_ERROR or "?", sys.executable)


FX_TYPES = ["gate", "eq", "comp", "delay", "reverb"]
CUE_FX = ("gate", "eq", "comp")
BUS_FX = tuple(FX_TYPES)
FX_LABELS = {"gate": tr("Gate"), "eq": tr("EQ parametrico"), "comp": tr("Compressore"),
             "delay": tr("Delay"), "reverb": tr("Riverbero")}
BAND_TYPES = ("peak", "lowshelf", "highshelf")
BAND_LABELS = {"peak": tr("Campana"), "lowshelf": tr("Shelf bassi"), "highshelf": tr("Shelf acuti")}


def _band(f, kind="peak", q=1.0):
    return {"on": True, "type": kind, "f": float(f), "g": 0.0, "q": float(q)}


DEFAULTS = {
    "gate": {"thr": -50.0, "range": -80.0, "attack": 1.0, "hold": 50.0, "release": 150.0},
    "eq": {"hpf_on": False, "hpf_f": 80.0, "hpf_slope": 12, "lpf_on": False, "lpf_f": 16000.0, "lpf_slope": 12,
           "bands": [_band(100, "lowshelf", 0.7), _band(400), _band(1000), _band(3000), _band(8000, "highshelf", 0.7)],
           "gain": 0.0},
    "comp": {"thr": -18.0, "ratio": 3.0, "knee": 6.0, "attack": 10.0, "release": 120.0, "makeup": 0.0},
    "delay": {"time": 350.0, "feedback": 35.0, "pingpong": False, "hp": 120.0, "lp": 6000.0, "mix": 100.0,
              "level": 0.0, "pitch": 0.0, "pitch_win": 40.0, "pitch_out": False},
    "reverb": {"decay": 1.8, "predelay": 20.0, "size": 60.0, "damp": 6000.0, "width": 100.0, "mix": 100.0,
               "level": 0.0},
}


def new_fx(kind):
    import copy
    d = {"id": uuid.uuid4().hex[:8], "type": kind, "on": True}
    d.update(copy.deepcopy(DEFAULTS[kind]))
    return d


def params(fx):
    """Parametri dell'effetto con i valori mancanti presi dai predefiniti (show salvati da versioni precedenti)."""
    import copy
    d = copy.deepcopy(DEFAULTS.get(fx.get("type"), {}))
    d.update({k: v for k, v in fx.items() if k not in ("id", "type", "on")})
    if fx.get("type") == "eq":
        bands = list(d.get("bands") or [])
        base = DEFAULTS["eq"]["bands"]
        d["bands"] = [dict(base[i], **(bands[i] if i < len(bands) and isinstance(bands[i], dict) else {}))
                      for i in range(len(base))]
    return d


def db2lin(db):
    return 10.0 ** (float(db) / 20.0)


def _level(db):
    """Livello d'uscita di un effetto: −60 dB (in fondo) = spento del tutto, come i fader di Cuevivo."""
    return 0.0 if float(db) <= -60.0 else db2lin(db)


# ======================================================================= filtri (RBJ cookbook)

def _norm(b, a):
    a0 = a[0]
    return [b[0] / a0, b[1] / a0, b[2] / a0, 1.0, a[1] / a0, a[2] / a0]


def biquad(kind, f, g=0.0, q=0.7071):
    f = max(10.0, min(float(f), SR * 0.45))
    w = 2 * math.pi * f / SR
    cw, sw = math.cos(w), math.sin(w)
    q = max(0.1, float(q))
    al = sw / (2 * q)
    A = 10 ** (float(g) / 40.0)
    if kind == "peak":
        return _norm([1 + al * A, -2 * cw, 1 - al * A], [1 + al / A, -2 * cw, 1 - al / A])
    if kind in ("lowshelf", "highshelf"):
        sq = 2 * math.sqrt(A) * al
        if kind == "lowshelf":
            return _norm([A * ((A + 1) - (A - 1) * cw + sq), 2 * A * ((A - 1) - (A + 1) * cw),
                          A * ((A + 1) - (A - 1) * cw - sq)],
                         [(A + 1) + (A - 1) * cw + sq, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sq])
        return _norm([A * ((A + 1) + (A - 1) * cw + sq), -2 * A * ((A - 1) + (A + 1) * cw),
                      A * ((A + 1) + (A - 1) * cw - sq)],
                     [(A + 1) - (A - 1) * cw + sq, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sq])
    if kind == "hpf":
        return _norm([(1 + cw) / 2, -(1 + cw), (1 + cw) / 2], [1 + al, -2 * cw, 1 - al])
    if kind == "lpf":
        return _norm([(1 - cw) / 2, 1 - cw, (1 - cw) / 2], [1 + al, -2 * cw, 1 - al])
    raise ValueError(kind)


# Butterworth: 12 dB/ottava = una sezione (Q 0,707); 24 dB/ottava = due sezioni (Q 0,541 e 1,307)
_BUTTER_Q = {12: (0.70710678,), 24: (0.54119610, 1.30656296)}


def eq_sections(p):
    """Sezioni del filtro (righe sos) per i parametri di un EQ."""
    rows = []
    if p.get("hpf_on"):
        for q in _BUTTER_Q.get(int(p.get("hpf_slope", 12)), _BUTTER_Q[12]):
            rows.append(biquad("hpf", p["hpf_f"], 0.0, q))
    for b in p.get("bands") or []:
        if b.get("on", True) and abs(float(b.get("g", 0.0))) > 0.01:
            rows.append(biquad(b.get("type", "peak"), b.get("f", 1000), b.get("g", 0.0), b.get("q", 1.0)))
    if p.get("lpf_on"):
        for q in _BUTTER_Q.get(int(p.get("lpf_slope", 12)), _BUTTER_Q[12]):
            rows.append(biquad("lpf", p["lpf_f"], 0.0, q))
    return np.array(rows, np.float64) if rows else np.zeros((0, 6))


def eq_response(p, freqs):
    """Risposta in dB dell'EQ alle frequenze indicate (per la curva dell'interfaccia)."""
    sos = eq_sections(p)
    z = np.exp(-1j * 2 * np.pi * np.asarray(freqs, np.float64) / SR)
    h = np.ones(len(z), complex)
    for b0, b1, b2, _a0, a1, a2 in sos:
        h *= (b0 + b1 * z + b2 * z * z) / (1 + a1 * z + a2 * z * z)
    return 20 * np.log10(np.maximum(np.abs(h), 1e-9)) + float(p.get("gain", 0.0))


# ======================================================================= effetti

class _Fx:
    def __init__(self, p, ch):
        self.ch = ch
        self.p = p
        self.setup()

    def setup(self):
        pass

    def set(self, p):
        self.p = p
        self.setup()

    def reset(self):
        pass

    def meter(self):
        return {}


class EQ(_Fx):
    def setup(self):
        sos = eq_sections(self.p)
        old = getattr(self, "sos", None)
        if old is None or old.shape != sos.shape or not np.allclose(old, sos):
            # coefficienti cambiati: per un blocco si fa la dissolvenza tra il vecchio e il nuovo filtro
            self.prev = (old, getattr(self, "zi", None)) if old is not None and len(old) else None
            self.sos = sos
            zi = getattr(self, "zi", None)
            self.zi = zi.copy() if (zi is not None and zi.shape[0] == len(sos)) else np.zeros((len(sos), 2, self.ch))
        self.gain = db2lin(self.p.get("gain", 0.0))

    RTA_N = 8192            # campioni d'ingresso tenuti per l'analizzatore di spettro (RTA) della finestra

    def reset(self):
        self.zi = np.zeros((len(self.sos), 2, self.ch))
        self.prev = None

    def meter(self):
        """Ultimi campioni d'ingresso (mono, in ordine) per l'RTA: l'analisi la fa l'interfaccia."""
        buf = getattr(self, "rbuf", None)
        if buf is None:
            return {}
        return {"rta": np.concatenate((buf[self.rw:], buf[:self.rw]))}

    def process(self, x):
        n = len(x)
        if n:                    # ingresso nel buffer dell'RTA (costa una copia di un blocco)
            if not hasattr(self, "rbuf"):
                self.rbuf = np.zeros(self.RTA_N, np.float32)
                self.rw = 0
            mono = x.mean(axis=1) if x.ndim == 2 else x
            k = min(n, self.RTA_N)
            idx = (self.rw + np.arange(k)) % self.RTA_N
            self.rbuf[idx] = mono[-k:]
            self.rw = (self.rw + k) % self.RTA_N
        y = x
        if len(self.sos):
            y, self.zi = sosfilt(self.sos, x, axis=0, zi=self.zi)
        prev = self.prev
        if prev is not None:
            self.prev = None
            psos, pzi = prev
            if pzi is not None and pzi.shape[0] == len(psos):
                yo, _ = sosfilt(psos, x, axis=0, zi=pzi)
                r = np.linspace(0.0, 1.0, len(x))[:, None]
                y = yo + (y - yo) * r
        return (y * self.gain).astype(np.float32, copy=False)


def comp_curve(p, x_db):
    """Curva statica del compressore: livello d'uscita (dB) per livello d'entrata (dB), senza compensazione."""
    x = np.asarray(x_db, np.float64)
    thr, ratio, knee = float(p["thr"]), max(1.0, float(p["ratio"])), max(0.0, float(p["knee"]))
    over = x - thr
    slope = 1.0 - 1.0 / ratio
    if knee > 0:
        red = np.where(over <= -knee / 2, 0.0,
                       np.where(over >= knee / 2, over * slope, slope * (over + knee / 2) ** 2 / (2 * knee)))
    else:
        red = np.maximum(over, 0.0) * slope
    return x - red + float(p.get("makeup", 0.0))


def gate_curve(p, x_db):
    """Curva statica del gate: sotto soglia il segnale scende della riduzione da chiuso."""
    x = np.asarray(x_db, np.float64)
    return np.where(x >= float(p["thr"]), x, x + min(0.0, float(p["range"])))


def _peak_db(x):
    return 20 * math.log10(max(float(np.abs(x).max()) if len(x) else 0.0, 1e-6))


class _Dyn(_Fx):
    """Gate e compressore: rivelatore a piccoli blocchi (32 campioni), stereo collegato, guadagno interpolato."""
    SUB = 32

    def setup(self):
        p = self.p
        rate = SR / self.SUB
        self.att = math.exp(-1.0 / max(1e-4, float(p.get("attack", 10)) / 1000.0 * rate))
        self.rel = math.exp(-1.0 / max(1e-4, float(p.get("release", 100)) / 1000.0 * rate))
        if not hasattr(self, "g"):
            self.g = 0.0          # guadagno attuale (dB)
            self.env = -120.0     # livello rivelato (dB)
            self.gr = 0.0
            self.lv_in = self.lv_out = -120.0   # picchi dell'ultimo blocco (vumeter)

    def reset(self):
        self.g, self.env, self.gr = 0.0, -120.0, 0.0

    def _levels(self, x):
        n = len(x)
        k = n // self.SUB
        if k == 0:
            return np.array([20 * math.log10(max(float(np.abs(x).max()) if n else 0.0, 1e-6))]), [n]
        body = np.abs(x[:k * self.SUB]).reshape(k, self.SUB, -1).max(axis=(1, 2))
        lv = list(body)
        sizes = [self.SUB] * k
        if n > k * self.SUB:
            lv.append(float(np.abs(x[k * self.SUB:]).max()))
            sizes.append(n - k * self.SUB)
        return 20 * np.log10(np.maximum(np.asarray(lv), 1e-6)), sizes

    def _apply(self, x, targets, sizes):
        g0 = self.g
        pts = np.empty(len(targets))
        g = g0
        for i, t in enumerate(targets):
            c = self.att if t < g else self.rel        # verso più riduzione = attacco
            g = c * g + (1 - c) * t
            pts[i] = g
        self.g = g
        ends = np.cumsum(sizes)
        gdb = np.interp(np.arange(len(x)) + 1, np.concatenate(([0], ends)), np.concatenate(([g0], pts)))
        self.gr = float(g)
        return (x * np.power(10.0, gdb / 20.0)[:, None]).astype(np.float32, copy=False)

    def meter(self):
        return {"gr": self.gr, "in": self.lv_in, "out": self.lv_out}

    def _meter_io(self, x, y):
        self.lv_in = max(_peak_db(x), self.lv_in - 3.0)       # discesa morbida dei vumeter
        self.lv_out = max(_peak_db(y), self.lv_out - 3.0)
        return y


class Compressor(_Dyn):
    def process(self, x):
        return self._meter_io(x, self._process(x))

    def _process(self, x):
        p = self.p
        thr, ratio, knee = float(p["thr"]), max(1.0, float(p["ratio"])), max(0.0, float(p["knee"]))
        lv, sizes = self._levels(x)
        over = lv - thr
        slope = 1.0 - 1.0 / ratio
        if knee > 0:
            red = np.where(over <= -knee / 2, 0.0,
                           np.where(over >= knee / 2, over * slope, slope * (over + knee / 2) ** 2 / (2 * knee)))
        else:
            red = np.maximum(over, 0.0) * slope
        y = self._apply(x, -red, sizes)
        mk = float(p.get("makeup", 0.0))
        return y * db2lin(mk) if mk else y


class Gate(_Dyn):
    def process(self, x):
        return self._meter_io(x, self._process(x))

    def setup(self):
        super().setup()
        p = self.p
        rate = SR / self.SUB
        self.att = math.exp(-1.0 / max(1e-4, float(p.get("attack", 1)) / 1000.0 * rate))   # apertura
        self.rel = math.exp(-1.0 / max(1e-4, float(p.get("release", 150)) / 1000.0 * rate))  # chiusura
        self.hold_n = int(float(p.get("hold", 50)) / 1000.0 * rate)
        if not hasattr(self, "open"):
            self.open, self.hold_left = False, 0
            self.g = float(p.get("range", -80))

    def reset(self):
        self.open, self.hold_left, self.gr = False, 0, 0.0
        self.g = float(self.p.get("range", -80))

    def _process(self, x):
        p = self.p
        thr, rng = float(p["thr"]), min(0.0, float(p["range"]))
        lv, sizes = self._levels(x)
        targets = np.empty(len(lv))
        for i, l in enumerate(lv):
            if l >= thr:
                self.open, self.hold_left = True, self.hold_n
            elif self.open and l < thr - 4.0:              # isteresi di 4 dB, poi hold
                if self.hold_left > 0:
                    self.hold_left -= 1
                else:
                    self.open = False
            targets[i] = 0.0 if self.open else rng
        g0 = self.g
        pts = np.empty(len(targets))
        g = g0
        for i, t in enumerate(targets):
            c = self.att if t > g else self.rel          # apertura veloce, chiusura con il rilascio
            g = c * g + (1 - c) * t
            pts[i] = g
        self.g = g
        ends = np.cumsum(sizes)
        gdb = np.interp(np.arange(len(x)) + 1, np.concatenate(([0], ends)), np.concatenate(([g0], pts)))
        self.gr = float(g)
        return (x * np.power(10.0, gdb / 20.0)[:, None]).astype(np.float32, copy=False)

    def meter(self):
        return {"gr": self.gr, "open": self.open, "in": self.lv_in, "out": self.lv_out}


def _onepole(kind, f):
    """Coefficienti (b, a) di un filtro a un polo (passa-basso o passa-alto) per il feedback del delay."""
    x = math.exp(-2 * math.pi * min(max(float(f), 10.0), SR * 0.45) / SR)
    if kind == "lp":
        return [1 - x], [1, -x]
    return [(1 + x) / 2, -(1 + x) / 2], [1, -x]


class PitchShifter:
    """Pitch shifter a linea di ritardo: due letture che scorrono a velocità ratio, sfalsate di mezza finestra e
    incrociate con finestre sin² (la somma resta 1), così i salti all'inizio della finestra non si sentono.
    Latenza media dmin + finestra/2; cambi di intonazione continui (glissando) senza scatti."""
    DMIN = 8

    def __init__(self, ch, max_block, win_ms=40.0):
        self.ch = ch
        self.L = int(max_block) + int(SR * 0.25) + 64
        self.buf = np.zeros((self.L, ch), np.float32)
        self.w = 0
        self.ph = 0.0
        self.set_window(win_ms)

    def set_window(self, win_ms):
        self.W = max(256, min(int(SR * 0.2), int(float(win_ms) / 1000.0 * SR)))

    def latency(self):
        return self.DMIN + self.W // 2

    def reset(self):
        self.buf[:] = 0
        self.ph = 0.0

    def process(self, x, semitones):
        n = len(x)
        idx = (self.w + np.arange(n)) % self.L
        self.buf[idx] = x
        if abs(semitones) < 1e-3:
            out = self.buf[(self.w + np.arange(n) - self.latency()) % self.L]
            self.w = (self.w + n) % self.L
            return out
        r = 2.0 ** (float(semitones) / 12.0)
        t = np.arange(n, dtype=np.float64)
        ph = (self.ph - (r - 1.0) * t / self.W) % 1.0           # ritardo che cala (in su) o cresce (in giù)
        out = np.zeros((n, self.ch), np.float32)
        for off in (0.0, 0.5):
            p2 = (ph + off) % 1.0
            d = self.DMIN + p2 * self.W
            pos = self.w + t - d
            i0 = np.floor(pos)
            fr = (pos - i0)[:, None].astype(np.float32)
            a = self.buf[i0.astype(np.int64) % self.L]
            b = self.buf[(i0.astype(np.int64) + 1) % self.L]
            g = (np.sin(np.pi * p2) ** 2)[:, None].astype(np.float32)
            out += (a + (b - a) * fr) * g
        self.ph = float((self.ph - (r - 1.0) * n / self.W) % 1.0)
        self.w = (self.w + n) % self.L
        return out


class Delay(_Fx):
    MAX = int(SR * 2.5)

    def setup(self):
        p = self.p
        self.d = max(16, min(self.MAX - 1, int(float(p["time"]) / 1000.0 * SR)))
        self.fb = max(0.0, min(0.95, float(p["feedback"]) / 100.0))
        self.lp_b, self.lp_a = _onepole("lp", p.get("lp", 6000))
        self.hp_b, self.hp_a = _onepole("hp", p.get("hp", 120))
        self.wet = _level(p.get("level", 0.0)) * max(0.0, min(1.0, float(p.get("mix", 100)) / 100.0))
        self.dry = 1.0 - max(0.0, min(1.0, float(p.get("mix", 100)) / 100.0))
        # pitch: nel feedback ogni ripetizione sale (o scende) di «pitch» semitoni rispetto alla precedente;
        # con «pitch_out» si sposta solo l'uscita dell'effetto (tutte le ripetizioni insieme, non si accumula)
        self.pitch = max(-24.0, min(24.0, float(p.get("pitch", 0.0) or 0.0)))
        self.pitch_out = bool(p.get("pitch_out"))
        if not hasattr(self, "ps"):
            self.ps = None
        if self.ps is None and abs(self.pitch) > 1e-3:
            self.ps = PitchShifter(self.ch, self.MAX, p.get("pitch_win", 40.0))
        if self.ps is not None:
            self.ps.set_window(p.get("pitch_win", 40.0))
        if not hasattr(self, "buf") or self.buf.shape[1] != self.ch:
            self.buf = np.zeros((self.MAX, self.ch), np.float32)
            self.w = 0
            self.zlp = np.zeros((0 + 1, self.ch))
            self.zhp = np.zeros((1, self.ch))
            self.d_old = self.d
        self.peak = 0.0

    def reset(self):
        self.buf[:] = 0
        self.zlp[:] = 0
        self.zhp[:] = 0
        if self.ps is not None:
            self.ps.reset()

    def _shifting(self):
        return self.ps is not None and abs(self.pitch) > 1e-3

    def _read(self, d, n):
        idx = (self.w - d + np.arange(n)) % self.MAX
        return self.buf[idx]

    def process(self, x):
        n_tot = len(x)
        out = np.empty_like(x, dtype=np.float32)
        s = 0
        shifting = self._shifting()
        lat = self.ps.latency() if shifting else 0
        loop_ps = shifting and not self.pitch_out
        # il pitch shifter ritarda di lat: la lettura si anticipa di altrettanto (il tempo del delay non cambia).
        # Nel loop: si legge prima ciò che è già stato spostato; sull'uscita: lettura a parte per l'effetto.
        d = max(16, self.d - lat) if loop_ps else self.d
        d_wet = max(16, self.d - lat) if (shifting and not loop_ps) else d
        last = getattr(self, "_d_last", d)
        while s < n_tot:
            n = min(n_tot - s, d, d_wet)                # a pezzi non più lunghi del ritardo (feedback corretto)
            rd = self._read(d, n)
            if last != d:                                # tempo cambiato: dissolvenza dalla vecchia lettura
                old = self._read(last, n)
                r = np.linspace(0.0, 1.0, n)[:, None]
                rd = old + (rd - old) * r
                last = d
            fb, self.zlp = lfilter(self.lp_b, self.lp_a, rd, axis=0, zi=self.zlp)
            fb, self.zhp = lfilter(self.hp_b, self.hp_a, fb, axis=0, zi=self.zhp)
            fb = fb * self.fb
            if self.p.get("pingpong") and self.ch >= 2:
                fb = fb[:, ::-1]                         # l'eco passa da un canale all'altro
                xin = x[s:s + n].copy()
                xin[:, 1:] = 0.0                         # entra da sinistra, poi rimbalza
                xin[:, 0] = x[s:s + n].mean(axis=1)
            else:
                xin = x[s:s + n]
            wet = rd
            if shifting and not loop_ps:                 # solo l'uscita: tutte le ripetizioni insieme
                wet = self.ps.process(self._read(d_wet, n), self.pitch)
            idx = (self.w + np.arange(n)) % self.MAX
            wr = xin + fb
            if loop_ps:                                  # ogni passaggio nel loop sposta l'intonazione
                wr = self.ps.process(wr, self.pitch)
            self.buf[idx] = wr
            self.w = (self.w + n) % self.MAX
            out[s:s + n] = x[s:s + n] * self.dry + wet * self.wet
            s += n
        self._d_last = last
        self.d_old = self.d
        self.peak = float(np.abs(out).max()) if n_tot else 0.0
        return out


class Reverb(_Fx):
    """Riverbero a rete di ritardi con retroazione (FDN, 8 linee, matrice di Hadamard), diffusione d'ingresso
    con due all-pass, pre-delay, smorzamento degli acuti nella retroazione e decadimento in secondi (T60)."""
    BASE_MS = (29.7, 37.1, 41.1, 43.7, 53.9, 61.3, 67.1, 73.9)
    AP_MS = (4.77, 3.59)

    def setup(self):
        p = self.p
        size = 0.5 + max(0.0, min(100.0, float(p.get("size", 60)))) / 100.0       # 0,5 … 1,5
        D = np.array([int(ms * size / 1000.0 * SR) for ms in self.BASE_MS])
        if not hasattr(self, "D") or not np.array_equal(D, self.D) or self.buf.shape[1] != 8:
            self.D = D
            self.L = int(D.max()) + 4096
            self.buf = np.zeros((self.L, 8), np.float64)
            self.w = 0
            self.zd = np.zeros((1, 8))
        T = max(0.1, float(p.get("decay", 1.8)))
        self.g = 10 ** (-3.0 * self.D / (T * SR))                     # −60 dB in T secondi per ogni linea
        self.db, self.da = _onepole("lp", p.get("damp", 6000))
        self.pre = max(0, min(int(SR * 0.25), int(float(p.get("predelay", 20)) / 1000.0 * SR)))
        if not hasattr(self, "pbuf"):
            self.pbuf = np.zeros(int(SR * 0.25) + 4096)
            self.pw = 0
            self.ap = []
            for ms in self.AP_MS:
                d = int(ms / 1000.0 * SR)
                b = np.zeros(d + 1)
                a = np.zeros(d + 1)
                b[0], b[d] = 0.6, 1.0
                a[0], a[d] = 1.0, 0.6
                self.ap.append((b, a, np.zeros(d)))
        H = np.array([[1.0]])
        for _ in range(3):
            H = np.block([[H, H], [H, -H]])
        self.H = H / math.sqrt(8)
        wd = max(0.0, min(1.0, float(p.get("width", 100)) / 100.0))
        self.tl = np.array([1, 0, 1, 0, 1, 0, 1, 0], np.float64) / 2.0
        self.tr = np.array([0, 1, 0, 1, 0, 1, 0, 1], np.float64) / 2.0
        mix = max(0.0, min(1.0, float(p.get("mix", 100)) / 100.0))
        self.wet = _level(p.get("level", 0.0)) * mix
        self.dry = 1.0 - mix
        self.width = wd
        self.sign = np.array([1, -1, 1, -1, -1, 1, -1, 1], np.float64)
        self.peak = 0.0

    def reset(self):
        self.buf[:] = 0
        self.pbuf[:] = 0
        self.zd[:] = 0
        self.ap = [(b, a, np.zeros_like(z)) for b, a, z in self.ap]

    def process(self, x):
        n_tot = len(x)
        mono = x.mean(axis=1).astype(np.float64) if x.ndim == 2 else x.astype(np.float64)
        # pre-delay
        P = len(self.pbuf)
        idx = (self.pw + np.arange(n_tot)) % P
        self.pbuf[idx] = mono
        mono = self.pbuf[(self.pw - self.pre + np.arange(n_tot)) % P]
        self.pw = (self.pw + n_tot) % P
        # diffusione
        ap = []
        for b, a, z in self.ap:
            mono, z = lfilter(b, a, mono, zi=z)
            ap.append((b, a, z))
        self.ap = ap
        out = np.zeros((n_tot, 2))
        step = int(self.D.min())
        s = 0
        rows = np.arange(8)
        while s < n_tot:
            n = min(n_tot - s, step)
            ri = (self.w - self.D[None, :] + np.arange(n)[:, None]) % self.L        # n × 8
            rd = self.buf[ri, rows[None, :]]
            out[s:s + n, 0] = rd @ self.tl
            out[s:s + n, 1] = rd @ self.tr
            fb = (rd @ self.H.T) * self.g[None, :]
            fb, self.zd = lfilter(self.db, self.da, fb, axis=0, zi=self.zd)
            fb += mono[s:s + n, None] * self.sign[None, :] * 0.9
            wi = (self.w + np.arange(n)) % self.L
            self.buf[wi] = fb
            self.w = (self.w + n) % self.L
            s += n
        # larghezza stereo: 0 = mono, 1 = pieno
        mid = (out[:, 0] + out[:, 1]) * 0.5
        out[:, 0] = mid + (out[:, 0] - mid) * self.width
        out[:, 1] = mid + (out[:, 1] - mid) * self.width
        wet = out * self.wet
        if x.ndim == 2 and x.shape[1] == 2:
            y = x * self.dry + wet
        elif x.ndim == 2 and x.shape[1] == 1:
            y = x * self.dry + wet.mean(axis=1, keepdims=True)
        else:
            y = np.repeat(wet.mean(axis=1, keepdims=True), x.shape[1], axis=1) + x * self.dry
        self.peak = float(np.abs(wet).max()) if n_tot else 0.0
        return y.astype(np.float32)


CLASSES = {"gate": Gate, "eq": EQ, "comp": Compressor, "delay": Delay, "reverb": Reverb}


class Rack:
    """Effetti in sequenza per n canali. update(spec) mantiene lo stato degli effetti che restano."""

    def __init__(self, spec, ch, allowed=FX_TYPES):
        self.ch = max(1, int(ch))
        self.allowed = tuple(allowed)
        self.procs = {}       # id -> processore
        self.order = []       # [(id, acceso)]
        self.update(spec)

    def update(self, spec):
        procs, order = {}, []
        for fx in spec or []:
            kind = fx.get("type")
            if kind not in CLASSES or kind not in self.allowed:
                continue
            fid = fx.get("id") or kind
            p = params(fx)
            old = self.procs.get(fid)
            if old is not None and isinstance(old, CLASSES[kind]):
                if old.p != p:
                    old.set(p)
                procs[fid] = old
            else:
                procs[fid] = CLASSES[kind](p, self.ch)
            order.append((fid, bool(fx.get("on", True))))
        self.procs, self.order = procs, order

    def active(self):
        return available() and any(on for _fid, on in self.order)

    def process(self, x):
        if not available():
            return x
        y = np.asarray(x, np.float32)
        if y.ndim == 1:
            y = y[:, None]
        for fid, on in self.order:
            if on:
                y = self.procs[fid].process(y)
        return y

    def reset(self):
        for p in self.procs.values():
            p.reset()

    def meter(self, fid):
        p = self.procs.get(fid)
        return p.meter() if p is not None else {}
