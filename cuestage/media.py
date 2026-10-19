"""Utility media: durata (ffprobe), decodifica audio (ffmpeg), forme d'onda, curve."""
import json
import math
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QObject, Signal

from .model import MIN_DB
from .system import find_tool, install_hint, subprocess_flags

SR = 48000
PEAK_RATE = 100  # colonne di forma d'onda al secondo

FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


def probe(path):
    """Restituisce dict con durata, presenza audio/video, dimensioni."""
    info = {"duration": 0.0, "has_audio": False, "has_video": False, "width": 0, "height": 0,
            "channels": 0, "layout": ""}
    global LAST_PROBE_ERROR
    if not path:
        return info
    if not FFPROBE:
        return _probe_wav(path) or info
    try:
        p = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, timeout=20, check=False, **subprocess_flags(),
        )
        out = p.stdout
        if p.returncode != 0 or not out:
            LAST_PROBE_ERROR = f"ffprobe uscita {p.returncode}: {p.stderr.decode(errors='replace').strip()[:300]}"
        data = json.loads(out or b"{}")
        info["duration"] = float(data.get("format", {}).get("duration") or 0.0)
        for st in data.get("streams", []):
            if st.get("codec_type") == "audio" and not info["has_audio"]:
                info["has_audio"] = True
                info["channels"] = int(st.get("channels") or 0)
                info["layout"] = st.get("channel_layout") or ""
            elif st.get("codec_type") == "video" and st.get("disposition", {}).get("attached_pic", 0) == 0:
                info["has_video"] = True
                info["width"] = st.get("width", 0)
                info["height"] = st.get("height", 0)
    except Exception as e:
        LAST_PROBE_ERROR = f"ffprobe: {type(e).__name__}: {e}"
    if not info["has_audio"] and not info["has_video"]:
        # ffprobe non ha risposto (o non c'è): i WAV si leggono anche senza
        info = _probe_wav(path) or info
    return info


LAST_PROBE_ERROR = ""      # ultimo errore di ffprobe (diagnostica)


def _probe_wav(path):
    """Informazioni di un WAV PCM letto con il modulo wave di Python (None se non è un WAV leggibile)."""
    import wave
    try:
        with wave.open(path, "rb") as w:
            ch, sr, n = w.getnchannels(), w.getframerate(), w.getnframes()
    except Exception:
        return None
    if ch <= 0 or sr <= 0:
        return None
    return {"duration": n / float(sr), "has_audio": True, "has_video": False, "width": 0, "height": 0,
            "channels": ch, "layout": "mono" if ch == 1 else ("stereo" if ch == 2 else "")}


def _decode_wav(path, sr=SR):
    """Decodifica un WAV PCM (8/16/24/32 bit) in int16 (n, canali), ricampionato a sr. None se non riesce."""
    import wave
    try:
        with wave.open(path, "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except Exception:
        return None
    if width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float32) / 8388608.0
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    else:
        return None
    x = x[: (len(x) // ch) * ch].reshape(-1, ch)
    if rate != sr and len(x):
        t_new = np.arange(int(round(len(x) * sr / rate))) * (rate / sr)
        idx = np.arange(len(x))
        x = np.stack([np.interp(t_new, idx, x[:, c]) for c in range(ch)], axis=1).astype(np.float32)
    return np.ascontiguousarray(np.clip(x * 32767.0, -32768, 32767).astype(np.int16))


MAX_SRC_CHANNELS = 32

LAYOUT_LABELS = {
    "mono": ["M"], "stereo": ["L", "R"], "2.1": ["L", "R", "LFE"],
    "quad": ["L", "R", "Ls", "Rs"], "4.0": ["L", "R", "C", "S"],
    "5.0": ["L", "R", "C", "Ls", "Rs"], "5.0(side)": ["L", "R", "C", "Ls", "Rs"],
    "5.1": ["L", "R", "C", "LFE", "Ls", "Rs"], "5.1(side)": ["L", "R", "C", "LFE", "Ls", "Rs"],
    "7.1": ["L", "R", "C", "LFE", "Lb", "Rb", "Ls", "Rs"],
}


def channel_labels(n, layout=""):
    lab = LAYOUT_LABELS.get(layout or "")
    if lab and len(lab) == n:
        return lab
    if n == 1:
        return ["M"]
    if n == 2:
        return ["L", "R"]
    return [str(i + 1) for i in range(n)]


def decode_audio(path, sr=SR, channels=None):
    """Decodifica l'intero file in int16 (n, canali) mantenendo i canali originali
    (int16: qualità CD e metà della memoria rispetto a float32).
    Restituisce un array (0, 2) se il file non ha audio."""
    if not FFMPEG:
        a = _decode_wav(path, sr)
        if a is not None:
            return a
        raise RuntimeError(f"ffmpeg non trovato: {install_hint('ffmpeg')}")
    if channels is None:
        info = probe(path)
        if not info["has_audio"]:
            return np.zeros((0, 2), np.int16)
        channels = info["channels"]
    ch = int(channels or 0)
    args = [FFMPEG, "-v", "error", "-nostdin", "-i", path, "-vn", "-f", "s16le"]
    if not (1 <= ch <= MAX_SRC_CHANNELS):
        ch = 2
        args += ["-ac", "2"]
    args += ["-ar", str(sr), "-"]
    try:
        p = subprocess.run(args, capture_output=True, check=False, **subprocess_flags())
    except OSError as e:
        p = None
        err = str(e)
    if p is None or (p.returncode != 0 and not p.stdout):
        a = _decode_wav(path, sr)          # ffmpeg non risponde: i WAV si leggono anche senza
        if a is not None:
            return a
        raise RuntimeError((p.stderr.decode(errors="replace").strip() if p is not None else err)
                           or "decodifica fallita")
    a = np.frombuffer(p.stdout, dtype=np.int16)
    a = a[: (len(a) // ch) * ch].reshape(-1, ch)
    return np.ascontiguousarray(a)


def compute_peaks(path):
    """Picchi min/max mono a PEAK_RATE colonne/secondo (per disegnare la forma d'onda)."""
    if not FFMPEG:
        return None
    rate = 8000
    p = subprocess.run(
        [FFMPEG, "-v", "error", "-nostdin", "-i", path, "-vn", "-f", "f32le",
         "-ac", "1", "-ar", str(rate), "-"],
        capture_output=True, check=False, **subprocess_flags(),
    )
    a = np.frombuffer(p.stdout, dtype=np.float32)
    if len(a) == 0:
        return None
    blk = rate // PEAK_RATE
    n = len(a) // blk
    if n == 0:
        return None
    a = a[: n * blk].reshape(n, blk)
    return np.stack([a.min(axis=1), a.max(axis=1)], axis=1)


def grab_frame(path, t, max_side=1280):
    """Fotogramma del video al tempo t (secondi) come PNG (bytes), ridotto se più grande di max_side.

    Restituisce anche la risoluzione originale, perché il crop è in pixel del sorgente."""
    if not FFMPEG:
        return None
    vf = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
    p = subprocess.run(
        [FFMPEG, "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", path,
         "-frames:v", "1", "-an", "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=False, **subprocess_flags(), timeout=20,
    )
    return p.stdout or None


# ---------------------------------------------------------------- curve / fade

def fade_shape(t, shape):
    """t in [0,1] (scalare o array) -> frazione di avanzamento della dissolvenza."""
    t = np.clip(t, 0.0, 1.0)
    if shape == "scurve":
        return 0.5 - 0.5 * np.cos(np.pi * t)
    return t


def fade_value(g0, g1, t, shape):
    """Valore di guadagno lineare durante un fade da g0 a g1."""
    if shape == "db":
        d0 = 20 * np.log10(max(g0, 1e-3))
        d1 = 20 * np.log10(max(g1, 1e-3))
        db = d0 + (d1 - d0) * np.clip(t, 0, 1)
        v = np.power(10.0, db / 20.0)
        return np.where(db <= MIN_DB + 0.01, 0.0, v) if isinstance(v, np.ndarray) else (0.0 if db <= MIN_DB + 0.01 else float(v))
    f = fade_shape(t, shape)
    return g0 + (g1 - g0) * f


def curve_arrays(points):
    """Dalla lista [[t, db], ...] restituisce (tempi, dB) ordinati o None."""
    if not points:
        return None
    pts = sorted((float(t), float(d)) for t, d in points)
    return np.array([p[0] for p in pts]), np.array([p[1] for p in pts])


def curve_gain(arrays, t):
    """Guadagno lineare della curva di volume al tempo t (scalare o array)."""
    if arrays is None:
        return 1.0 if np.isscalar(t) else np.ones_like(t)
    ct, cd = arrays
    db = np.interp(t, ct, cd)
    g = np.power(10.0, db / 20.0)
    return np.where(db <= MIN_DB + 0.01, 0.0, g)


class Envelope:
    """Inviluppo di guadagno con rampe, basato su un orologio esterno (secondi)."""

    def __init__(self, value=1.0):
        self.value0 = value
        self.ramp = None  # (t0, dur, g0, g1, shape)
        self.on_done = None

    def set(self, value):
        self.value0 = value
        self.ramp = None

    def ramp_to(self, now, target, dur, shape="scurve", on_done=None):
        cur = self.get(now)
        if dur <= 0:
            self.set(target)
            if on_done:
                on_done()
            return
        self.ramp = (now, dur, cur, target, shape)
        self.on_done = on_done

    def get(self, now):
        if not self.ramp:
            return self.value0
        t0, dur, g0, g1, shape = self.ramp
        t = (now - t0) / dur
        if t >= 1.0:
            self.value0 = g1
            self.ramp = None
            cb, self.on_done = self.on_done, None
            if cb:
                cb()
            return g1
        return float(fade_value(g0, g1, t, shape))

class MediaLoader(QObject):
    """Carica in background durate, audio decodificato, forme d'onda e fotogrammi."""
    infoReady = Signal(str)
    peaksReady = Signal(str)
    audioReady = Signal(str)
    frameReady = Signal(str, float, bytes)
    error = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.frame_pool = ThreadPoolExecutor(max_workers=1)
        self.frame_wanted = None   # ultima richiesta: le precedenti ancora in coda si saltano
        self.frames = {}           # cache (percorso, tempo arrotondato) -> png
        self.lock = threading.Lock()
        self.info = {}
        self.peaks = {}
        self.audio = {}
        self.pending = set()

    def get_info(self, path):
        if not path:
            return None
        if path in self.info:
            return self.info[path]
        self._submit(("info", path), self._load_info, path)
        return None

    def duration(self, path):
        i = self.get_info(path)
        return i["duration"] if i else 0.0

    def get_peaks(self, path):
        if path in self.peaks:
            return self.peaks[path]
        self._submit(("peaks", path), self._load_peaks, path)
        return None

    def preload_audio(self, path):
        if path and path not in self.audio:
            self._submit(("audio", path), self._load_audio, path)

    def audio_now(self, path):
        """Audio decodificato; se non ancora pronto lo decodifica subito (bloccante)."""
        with self.lock:
            a = self.audio.get(path)
        if a is None or not len(a):
            # anche se in cache risulta vuoto: un ffprobe/ffmpeg andato male una volta (computer molto
            # impegnato) non deve lasciare la cue muta per sempre
            a = self._decode_fresh(path, retry=a is not None)
        return a

    def _decode_fresh(self, path, retry=False):
        info = self.info.get(path)
        if retry or info is None or not info.get("has_audio"):
            info = probe(path)
            if not info["has_audio"] and os.path.exists(path):
                time.sleep(0.2)
                info = probe(path)
            self.info[path] = info
        a = np.zeros((0, 2), np.int16)
        if info["has_audio"]:
            a = decode_audio(path, channels=info["channels"])
            if not len(a):
                time.sleep(0.2)
                a = decode_audio(path, channels=info["channels"])
        with self.lock:
            self.audio[path] = a
        return a

    def info_now(self, path):
        """Informazioni sul file, subito (ffprobe sincrono se non ancora in cache)."""
        i = self.info.get(path)
        if i is None:
            i = self.info[path] = probe(path)
        return i

    def request_frame(self, path, t):
        """Chiede il fotogramma al tempo t; arriva con il segnale frameReady."""
        key = (path, round(float(t), 2))
        if key in self.frames:
            self.frameReady.emit(path, key[1], self.frames[key])
            return
        self.frame_wanted = key
        self.frame_pool.submit(self._load_frame, key)

    def _load_frame(self, key):
        if key != self.frame_wanted:
            return  # superata da una richiesta più recente
        try:
            data = grab_frame(key[0], key[1])
        except Exception as e:  # noqa
            self.error.emit(key[0], str(e))
            return
        if data:
            if len(self.frames) > 60:
                self.frames.pop(next(iter(self.frames)))
            self.frames[key] = data
            self.frameReady.emit(key[0], key[1], data)

    def forget(self, keep_paths):
        with self.lock:
            for p in list(self.audio):
                if p not in keep_paths:
                    del self.audio[p]

    # -- interni
    def _submit(self, key, fn, path):
        with self.lock:
            if key in self.pending:
                return
            self.pending.add(key)
        self.pool.submit(self._run, key, fn, path)

    def _run(self, key, fn, path):
        try:
            fn(path)
        except Exception as e:  # noqa
            self.error.emit(path, str(e))
        finally:
            with self.lock:
                self.pending.discard(key)

    def _load_info(self, path):
        self.info[path] = probe(path)
        self.infoReady.emit(path)

    def _load_peaks(self, path):
        self.peaks[path] = compute_peaks(path)
        self.peaksReady.emit(path)

    def _load_audio(self, path):
        self._decode_fresh(path)
        self.audioReady.emit(path)


def fmt_time(s, tenths=True):
    if s is None or (isinstance(s, float) and (math.isinf(s) or math.isnan(s))):
        return "∞"
    neg = s < 0
    s = abs(s)
    m = int(s // 60)
    r = s - m * 60
    txt = f"{m:02d}:{r:04.1f}" if tenths else f"{m:02d}:{int(r):02d}"
    return ("-" if neg else "") + txt
