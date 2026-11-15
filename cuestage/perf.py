"""Valutazione delle prestazioni (Aiuto › Diagnostica › Prestazioni): prove brevi e ripetibili su questo
computer, con un riepilogo e il buffer audio consigliato.

- Puntualità: un thread che dovrebbe svegliarsi ogni 2 ms misura di quanto arriva in ritardo (sistema
  operativo, Python, interfaccia): è ciò che decide quanto piccolo può essere il buffer audio.
- Mixer: tempo per blocco di una voce semplice, di una con rack (gate, EQ, compressore) e di un bus con
  riverbero.
- Video: conversione di un fotogramma Full HD in immagine e disegno con QPainter (motore classico); il motore
  su GPU non usa il processore per questo.
- Disco: lettura sequenziale (l'audio delle cue si legge dalla cache su disco).
Le prove sono passi brevi eseguiti uno alla volta: l'interfaccia resta usabile.
"""
import os
import statistics
import tempfile
import threading
import time

import numpy as np

from .i18n import tr
from .media import SR

BUFFERS = (128, 256, 512, 1024, 2048)


def _timeit(fn, n):
    fn()                                  # riscaldamento
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n


class Jitter:
    """Thread che chiede di svegliarsi ogni «period» secondi e annota i ritardi."""

    def __init__(self, period=0.002, dur=2.0):
        self.period, self.dur = period, dur
        self.lat = []
        self.th = threading.Thread(target=self._run, daemon=True, name="perf-jitter")

    def start(self):
        self.th.start()

    def done(self):
        return not self.th.is_alive()

    def _run(self):
        end = time.perf_counter() + self.dur
        nxt = time.perf_counter() + self.period
        while True:
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)
            now = time.perf_counter()
            self.lat.append(max(0.0, now - nxt))
            if now > end:
                break
            nxt += self.period

    def result(self):
        if not self.lat:
            return {}
        a = sorted(self.lat)
        return {"p50_ms": a[len(a) // 2] * 1000, "p99_ms": a[int(len(a) * 0.99) - 1] * 1000,
                "max_ms": a[-1] * 1000, "n": len(a)}


def bench_mixer(block=512):
    """ms per blocco: voce stereo semplice, voce con rack, bus con riverbero (motore audio vero, senza scheda)."""
    from . import dsp
    from .audio_engine import AudioEngine, BusRuntime, Voice
    eng = AudioEngine()
    data = (np.random.default_rng(0).standard_normal((SR * 20, 2)) * 0.1).astype(np.float32)
    out = np.zeros((block, 2), np.float32)

    def voice(fx=None):
        v = Voice(data, 0.0, 0.0, True, 0.0, 0.0, 0, 1.0, None, 0.0, 0.0)
        v.fit_matrix(2)
        if fx:
            v.fx = dsp.Rack([dsp.new_fx(k) for k in fx], 2, dsp.CUE_FX)
        return v
    res = {}
    v = voice()
    res["voice_ms"] = _timeit(lambda: eng._mix_voice(v, out, block, 2), 200) * 1000
    if dsp.available():
        v2 = voice(("gate", "eq", "comp"))
        res["voice_fx_ms"] = _timeit(lambda: eng._mix_voice(v2, out, block, 2), 200) * 1000
        b = BusRuntime({"id": "perf", "fx": [dsp.new_fx("reverb")], "level_db": 0.0}, "perf")
        inp = (np.random.default_rng(1).standard_normal((block, 2)) * 0.1).astype(np.float32)
        res["bus_reverb_ms"] = _timeit(lambda: b.process(inp.copy(), block, 2), 100) * 1000
    return res


def bench_video():
    """ms per fotogramma Full HD: conversione YUV → RGB (QVideoFrame.toImage) e disegno con QPainter."""
    from PySide6.QtCore import QRectF, QSize
    from PySide6.QtGui import QColor, QImage, QPainter
    res = {}
    try:
        from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat
        fmt = QVideoFrameFormat(QSize(1920, 1080), QVideoFrameFormat.Format_NV12)
        # un fotogramma nuovo ogni volta (Qt tiene in cache la conversione di quello già convertito)
        res["convert_ms"] = _timeit(lambda: QVideoFrame(fmt).toImage(), 6) * 1000
    except Exception:
        pass
    src = QImage(1920, 1080, QImage.Format_ARGB32_Premultiplied)
    src.fill(QColor(40, 90, 160))
    dst = QImage(1920, 1080, QImage.Format_ARGB32_Premultiplied)

    def draw():
        p = QPainter(dst)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(QRectF(0, 0, 1920, 1080), src)
        p.end()
    res["draw_ms"] = _timeit(draw, 10) * 1000
    return res


def bench_disk(mb=48):
    """MB/s in lettura (file temporaneo nella cartella della cache audio, senza la cache del sistema se possibile)."""
    try:
        from .media import audio_cache_dir
        d = audio_cache_dir()
    except Exception:
        d = tempfile.gettempdir()
    path = os.path.join(d, f".perf-{os.getpid()}.bin")
    blob = os.urandom(1024 * 1024)
    try:
        with open(path, "wb") as f:
            for _ in range(mb):
                f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            if hasattr(os, "posix_fadvise"):
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            t0 = time.perf_counter()
            n = 0
            while True:
                b = os.read(fd, 4 * 1024 * 1024)
                if not b:
                    break
                n += len(b)
            dt = time.perf_counter() - t0
        finally:
            os.close(fd)
        return {"read_mb_s": (n / 1e6) / max(dt, 1e-6)}
    except OSError as e:
        return {"error": str(e)}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def recommend(r):
    """Buffer consigliato: il più piccolo il cui blocco è almeno il doppio del ritardo peggiore (99°
    percentile della puntualità) più il calcolo di uno show tipico (8 voci con rack e un bus con riverbero)."""
    j = r.get("jitter") or {}
    m = r.get("mixer") or {}
    p99 = float(j.get("p99_ms", 5.0))
    work = 8 * float(m.get("voice_fx_ms", m.get("voice_ms", 0.2) * 3)) + float(m.get("bus_reverb_ms", 0.2))
    need = 2.0 * (p99 + work)
    for n in BUFFERS:
        if n / SR * 1000 >= need:
            return n, need
    return BUFFERS[-1], need


def summary(r):
    """Righe del riepilogo (testo)."""
    out = []
    j = r.get("jitter") or {}
    if j:
        out.append(tr("Puntualità (thread ogni 2 ms): mediana {0:.2f} ms · 99 % entro {1:.2f} ms · peggiore {2:.1f} ms",
                      j["p50_ms"], j["p99_ms"], j["max_ms"]))
    m = r.get("mixer") or {}
    if m:
        out.append(tr("Mixer (blocco di 512 campioni = 10,7 ms): voce {0:.3f} ms", m.get("voice_ms", 0))
                   + (tr(" · voce con gate, EQ e compressore {0:.3f} ms", m["voice_fx_ms"]) if "voice_fx_ms" in m else "")
                   + (tr(" · bus con riverbero {0:.3f} ms", m["bus_reverb_ms"]) if "bus_reverb_ms" in m else ""))
        if m.get("voice_ms"):
            per = m.get("voice_fx_ms", m["voice_ms"])
            out.append(tr("Voci con rack contemporanee stimate (metà del tempo del blocco): circa {0}",
                          int((512 / SR * 1000) * 0.5 / max(per, 1e-6))))
    v = r.get("video") or {}
    if v:
        parts = []
        if "convert_ms" in v:
            parts.append(tr("conversione di un fotogramma Full HD {0:.1f} ms", v["convert_ms"]))
        parts.append(tr("disegno con QPainter {0:.1f} ms", v.get("draw_ms", 0)))
        out.append(tr("Video, motore classico: ") + " · ".join(parts)
                   + tr(" (su GPU queste operazioni non usano il processore)"))
    d = r.get("disk") or {}
    if "read_mb_s" in d:
        out.append(tr("Disco (cache audio): lettura {0:.0f} MB/s", d["read_mb_s"]))
    if j and m:
        n, need = recommend(r)
        out.append(tr("Buffer audio consigliato: {0} campioni ({1:.1f} ms; servono almeno {2:.1f} ms)",
                      n, n / SR * 1000, need))
    return out


def steps():
    """Passi della valutazione: [(descrizione, funzione(risultati) -> None o oggetto da attendere)]."""
    def jitter(r):
        j = Jitter()
        j.start()
        r["_wait"] = j
        r["_after"] = lambda: r.__setitem__("jitter", j.result())
    return [(tr("Puntualità del sistema (2 s)…"), jitter),
            (tr("Mixer audio…"), lambda r: r.__setitem__("mixer", bench_mixer())),
            (tr("Video…"), lambda r: r.__setitem__("video", bench_video())),
            (tr("Disco…"), lambda r: r.__setitem__("disk", bench_disk()))]


def median(xs):
    return statistics.median(xs) if xs else 0.0
