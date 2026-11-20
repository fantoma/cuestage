"""Loudness integrata (LUFS) secondo ITU-R BS.1770-4 / EBU R128, per il menu Normalizza.

Filtro K (shelf acuti + passa-alto RLB), blocchi da 400 ms con passo di 100 ms, gate assoluto a −70 LUFS e gate
relativo a −10 LU. I file mono si misurano come «doppio mono» (Cuevivo li manda di norma a sinistra e a destra):
così un mono e uno stereo con lo stesso segnale risultano uguali, come all'ascolto. Si legge l'audio dalla cache su
disco a pezzi: la memoria usata resta piccola anche con file lunghi.
"""
import math
import os

import numpy as np

from .media import SR

ABS_GATE = -70.0
REL_GATE = -10.0
BLOCK_S = 0.4
HOP_S = 0.1

# filtro K a 48 kHz (coefficienti della raccomandazione BS.1770)
_K48 = (
    ([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585]),
    ([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621]),
)


def _k_sos(sr):
    """Filtro K come sezioni sos per la frequenza di campionamento sr (calcolato come in libebur128)."""
    if sr == 48000:
        return np.array([b + a for b, a in _K48], np.float64)
    # shelf acuti
    f0, g, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sr)
    vh = 10 ** (g / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    b1 = [(vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]
    a1 = [1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]
    # passa-alto RLB
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / sr)
    a0 = 1.0 + k / q + k * k
    a2 = [1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]
    return np.array([b1 + a1, [1.0, -2.0, 1.0] + a2], np.float64)


def channel_weights(n):
    """Pesi dei canali: L, R, C = 1; LFE escluso; surround = 1,41 (ordine FFmpeg L R C LFE Ls Rs …)."""
    if n == 1:
        return np.array([2.0])                      # doppio mono
    if n in (6, 8):
        w = np.ones(n)
        w[3] = 0.0
        w[4:] = 1.41
        return w
    return np.ones(n)


def measure(data, start=0, end=None, sr=SR, progress=None, cancel=None, chunk_s=10.0):
    """(LUFS integrati, picco in dBFS) di data[start:end] (frames × canali, float o int16).
    LUFS = −inf se tutto è sotto il gate (silenzio). progress(f) con f tra 0 e 1; cancel() → True per fermarsi."""
    from scipy.signal import sosfilt, sosfilt_zi
    n_total = len(data)
    end = n_total if end is None or end <= 0 or end > n_total else int(end)
    start = max(0, min(int(start), end))
    if end - start < int(BLOCK_S * sr):
        return float("-inf"), float("-inf")
    ch = data.shape[1] if data.ndim > 1 else 1
    weights = channel_weights(ch)
    sos = _k_sos(sr)
    zi = np.repeat(sosfilt_zi(sos)[:, :, None], ch, axis=2) * 0.0
    scale = 1.0 / 32768.0 if data.dtype == np.int16 else 1.0
    hop = int(round(HOP_S * sr))
    seg_energy = []                                 # energia pesata di ogni segmento da 100 ms
    rest = np.zeros((0, ch))
    peak = 0.0
    step = max(hop, int(chunk_s * sr) // hop * hop)
    pos = start
    while pos < end:
        if cancel is not None and cancel():
            return None, None
        blk = np.asarray(data[pos:min(end, pos + step)], np.float64)
        if blk.ndim == 1:
            blk = blk[:, None]
        blk = blk * scale
        if len(blk):
            peak = max(peak, float(np.abs(blk).max()))
        y, zi = sosfilt(sos, blk, axis=0, zi=zi)
        y = np.concatenate([rest, y]) if len(rest) else y
        n_seg = len(y) // hop
        if n_seg:
            sq = (y[:n_seg * hop] ** 2).reshape(n_seg, hop, ch).sum(axis=1)       # somma dei quadrati per canale
            seg_energy.append(sq @ weights)
        rest = y[n_seg * hop:]
        pos += len(blk)
        if progress is not None:
            progress((pos - start) / max(1, end - start))
    if not seg_energy:
        return float("-inf"), 20 * math.log10(peak) if peak > 0 else float("-inf")
    seg = np.concatenate(seg_energy)
    per_block = int(round(BLOCK_S / HOP_S))
    if len(seg) < per_block:
        return float("-inf"), 20 * math.log10(peak) if peak > 0 else float("-inf")
    csum = np.concatenate([[0.0], np.cumsum(seg)])
    z = (csum[per_block:] - csum[:-per_block]) / (per_block * hop)           # media quadratica pesata dei blocchi
    with np.errstate(divide="ignore"):
        lk = -0.691 + 10 * np.log10(np.maximum(z, 1e-20))
    z1 = z[lk > ABS_GATE]
    if not len(z1):
        return float("-inf"), 20 * math.log10(peak) if peak > 0 else float("-inf")
    rel = -0.691 + 10 * math.log10(z1.mean()) + REL_GATE
    z2 = z1[(-0.691 + 10 * np.log10(np.maximum(z1, 1e-20))) > rel]
    lufs = -0.691 + 10 * math.log10(z2.mean()) if len(z2) else float("-inf")
    return lufs, (20 * math.log10(peak) if peak > 0 else float("-inf"))


_CACHE = {}


def cue_region(cue):
    """(file, inizio, fine) in secondi del tratto della cue che suona (IN–OUT; fine 0 = fino alla fine del file)."""
    return cue.file, float(cue.start or 0.0), float(cue.end or 0.0)


def measure_cue(cue, progress=None, cancel=None):
    """Loudness del tratto IN–OUT della cue: dict {lufs, peak} oppure {error}. I risultati restano in memoria
    finché file, tratto e data di modifica non cambiano."""
    from .i18n import tr
    from .media import decode_audio_cached
    path, t0, t1 = cue_region(cue)
    if not path or not os.path.isfile(path):
        return {"error": tr("file non trovato")}
    try:
        key = (os.path.abspath(path), round(t0, 3), round(t1, 3), os.path.getmtime(path))
    except OSError:
        key = None
    if key is not None and key in _CACHE:
        return dict(_CACHE[key])
    try:
        data = decode_audio_cached(path)
    except Exception as e:                           # file illeggibile, ffmpeg mancante…
        return {"error": str(e)}
    if not len(data):
        return {"error": tr("il file non contiene audio")}
    a = int(t0 * SR)
    b = int(t1 * SR) if t1 > 0 else len(data)
    lufs, peak = measure(data, a, b, SR, progress, cancel)
    if lufs is None:
        return None                                  # annullato
    res = {"lufs": lufs, "peak": peak}
    if key is not None:
        _CACHE[key] = dict(res)
    return res


def correction(lufs, target):
    """dB da aggiungere al volume perché il file suoni a «target» LUFS (None se non misurabile)."""
    if lufs is None or not math.isfinite(lufs):
        return None
    return round(float(target) - float(lufs), 1)


class LiveMeter:
    """Misura in tempo reale (stile EBU R128): momentanea (400 ms), a breve termine (3 s), integrata dall'avvio
    (o dall'ultimo azzeramento), intervallo di loudness (LRA) e picco. Riceve blocchi di campioni (frames × canali)
    con feed(); i calcoli si fanno fuori dal thread audio."""

    def __init__(self, sr=SR, history_s=600):
        self.sr = sr
        self.hop = int(round(HOP_S * sr))
        self.history_s = history_s
        self.reset()

    def reset(self):
        self.ch = None
        self.zi = None
        self.rest = None
        self.segs = []            # energia pesata di ogni segmento da 100 ms
        self.blocks = []          # energia dei blocchi da 400 ms (per l'integrata)
        self.st_values = []       # loudness a breve termine ogni 100 ms (per LRA e grafico)
        self.m_values = []        # loudness momentanea ogni 100 ms (grafico)
        self.peak = 0.0
        self.m_max = float("-inf")
        self.st_max = float("-inf")

    def _setup(self, ch):
        from scipy.signal import sosfilt_zi
        self.ch = ch
        self.sos = _k_sos(self.sr)
        self.zi = np.repeat(sosfilt_zi(self.sos)[:, :, None], ch, axis=2) * 0.0
        self.rest = np.zeros((0, ch))
        self.w = channel_weights(ch)

    def feed(self, x):
        from scipy.signal import sosfilt
        x = np.asarray(x, np.float64)
        if x.ndim == 1:
            x = x[:, None]
        if not len(x):
            return
        if self.ch != x.shape[1]:
            self._setup(x.shape[1])
        self.peak = max(self.peak, float(np.abs(x).max()))
        y, self.zi = sosfilt(self.sos, x, axis=0, zi=self.zi)
        y = np.concatenate([self.rest, y]) if len(self.rest) else y
        n = len(y) // self.hop
        if n:
            e = ((y[:n * self.hop] ** 2).reshape(n, self.hop, self.ch).sum(axis=1)) @ self.w
            for v in e:
                self._segment(float(v))
        self.rest = y[n * self.hop:]

    @staticmethod
    def _lk(z):
        return -0.691 + 10 * math.log10(z) if z > 1e-20 else float("-inf")

    def _segment(self, e):
        self.segs.append(e)
        keep = int(self.history_s / HOP_S) + 40
        if len(self.segs) > keep:
            del self.segs[:len(self.segs) - keep]
        hop = self.hop
        if len(self.segs) >= 4:
            zm = sum(self.segs[-4:]) / (4 * hop)
            self.blocks.append(zm)
            m = self._lk(zm)
            self.m_values.append(m)
            self.m_max = max(self.m_max, m)
        else:
            self.m_values.append(float("-inf"))
        if len(self.segs) >= 30:
            st = self._lk(sum(self.segs[-30:]) / (30 * hop))
            self.st_values.append(st)
            self.st_max = max(self.st_max, st)
        else:
            self.st_values.append(float("-inf"))
        lim = int(self.history_s / HOP_S)
        for lst in (self.m_values, self.st_values):
            if len(lst) > lim:
                del lst[:len(lst) - lim]

    # ---- valori
    def momentary(self):
        return self.m_values[-1] if self.m_values else float("-inf")

    def short_term(self):
        return self.st_values[-1] if self.st_values else float("-inf")

    def integrated(self):
        z = np.array(self.blocks)
        if not len(z):
            return float("-inf")
        with np.errstate(divide="ignore"):
            lk = -0.691 + 10 * np.log10(np.maximum(z, 1e-20))
        z1 = z[lk > ABS_GATE]
        if not len(z1):
            return float("-inf")
        rel = -0.691 + 10 * math.log10(z1.mean()) + REL_GATE
        z2 = z1[lk[lk > ABS_GATE] > rel]
        return -0.691 + 10 * math.log10(z2.mean()) if len(z2) else float("-inf")

    def lra(self):
        """Loudness Range (EBU Tech 3342): dal 10° al 95° percentile dei valori a breve termine, con gate assoluto
        −70 LUFS e relativo −20 LU."""
        st = np.array([v for v in self.st_values if math.isfinite(v)])
        st = st[st > ABS_GATE]
        if len(st) < 2:
            return 0.0
        rel = 10 * math.log10(np.mean(10 ** (st / 10.0))) - 20.0
        st = st[st > rel]
        if len(st) < 2:
            return 0.0
        return float(np.percentile(st, 95) - np.percentile(st, 10))

    def peak_db(self):
        return 20 * math.log10(self.peak) if self.peak > 0 else float("-inf")

    def seconds(self):
        return len(self.blocks) * HOP_S
