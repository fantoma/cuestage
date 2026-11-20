"""Aiuto › Diagnostica: carico del processore, tempi del buffer audio per ogni scheda, uscita video e registro
degli errori del motore audio e video (aggiornati due volte al secondo)."""
import os
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QTabWidget,
                               QVBoxLayout, QWidget)

from .i18n import tr
from .appinfo import APP_NAME, settings as _app_settings


def _proc_stat():
    """(tempo occupato, tempo totale) di tutti i processori, da /proc/stat (solo Linux)."""
    try:
        with open("/proc/stat", encoding="ascii") as f:
            parts = [float(x) for x in f.readline().split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0.0)
        return sum(parts) - idle, sum(parts)
    except (OSError, ValueError, IndexError):
        return None


def _memory_mb():
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024
    except Exception:
        return None


def _color(frac):
    return "#e05050" if frac >= 0.8 else "#e0b040" if frac >= 0.5 else "#50c070"


class DiagnosticsDialog(QDialog):
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.setWindowTitle(tr("Diagnostica"))
        self.resize(780, 680)
        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        page = QWidget()
        self.tabs.addTab(page, tr("Stato"))
        self.tabs.addTab(self._perf_page(), tr("Prestazioni"))
        v = QVBoxLayout(page)
        self.info = QLabel()
        self.info.setTextFormat(Qt.RichText)
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        v.addWidget(QLabel(tr("Errori e messaggi del motore audio e video (più recenti in basso):")))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        h = QHBoxLayout()
        for text, fn in ((tr("Azzera i massimi"), self.reset), (tr("Azzera XRUN"), self.reset_xrun),
                         (tr("Copia negli appunti"), self.copy), (tr("Chiudi"), self.close)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            h.addWidget(b)
            if fn == self.reset_xrun:
                b.setToolTip(tr("Riporta a zero i buchi audio (underrun) di tutte le schede e toglie l'avviso dalla "
                                "barra di stato: utile dopo aver cambiato buffer o scheda"))
                self.xrun_btn = b
        h.insertStretch(3, 1)
        outer.addLayout(h)
        self._cpu0 = (time.process_time(), time.monotonic())
        self._sys0 = _proc_stat()
        self._cpu = 0.0
        self._sys = None
        self._log_sig = None
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    # ---------------------------------------------------------------- valutazione delle prestazioni
    def _perf_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        lab = QLabel(tr("Prove brevi su questo computer: puntualità del sistema, costo del mixer audio e degli "
                        "effetti, video con il motore classico, lettura dal disco. Alla fine c'è il buffer audio "
                        "consigliato. Meglio farla con lo show aperto e senza cue in riproduzione."))
        lab.setWordWrap(True)
        v.addWidget(lab)
        h = QHBoxLayout()
        self.perf_btn = QPushButton(tr("Esegui la valutazione"))
        self.perf_btn.clicked.connect(self.run_perf)
        h.addWidget(self.perf_btn)
        self.perf_bar = QProgressBar()
        self.perf_bar.setVisible(False)
        h.addWidget(self.perf_bar, 1)
        v.addLayout(h)
        self.perf_out = QPlainTextEdit()
        self.perf_out.setReadOnly(True)
        v.addWidget(self.perf_out, 1)
        last = _app_settings().value("perf/last", "")
        if last:
            self.perf_out.setPlainText(tr("Ultima valutazione:") + "\n" + str(last))
        return w

    def run_perf(self):
        from .perf import steps
        self._perf_steps = steps()
        self._perf_res = {}
        self._perf_i = 0
        self.perf_btn.setEnabled(False)
        self.perf_bar.setVisible(True)
        self.perf_bar.setRange(0, len(self._perf_steps))
        self.perf_bar.setValue(0)
        self.perf_out.setPlainText("")
        QTimer.singleShot(50, self._perf_next)

    def _perf_next(self):
        r = self._perf_res
        wait = r.get("_wait")
        if wait is not None:                      # passo in corso in un thread: si aspetta senza bloccare
            if not wait.done():
                QTimer.singleShot(100, self._perf_next)
                return
            r.pop("_wait")
            after = r.pop("_after", None)
            if after:
                after()
        if self._perf_i >= len(self._perf_steps):
            self._perf_done()
            return
        desc, fn = self._perf_steps[self._perf_i]
        self.perf_out.appendPlainText(desc)
        self._perf_i += 1
        self.perf_bar.setValue(self._perf_i)
        try:
            fn(r)
        except Exception as e:
            self.perf_out.appendPlainText(tr("  errore: {0}", e))
        QTimer.singleShot(30, self._perf_next)

    def _perf_done(self):
        from .perf import summary
        self.perf_btn.setEnabled(True)
        self.perf_bar.setVisible(False)
        try:                               # picco reale del mixer (schede vere, dopo qualche blocco)
            peaks = [d["max_ms"] for d in self.mw.ctrl.audio.diagnostics() if not d["null"] and d["blocks"] > 50]
            if peaks:
                self._perf_res["live_peak_ms"] = max(peaks)
        except Exception:
            pass
        lines = summary(self._perf_res)
        text = time.strftime("%Y-%m-%d %H:%M") + "\n" + "\n".join(lines)
        self.perf_out.setPlainText(text)
        _app_settings().setValue("perf/last", text)
        self.perf_result = self._perf_res

    def reset(self):
        self.mw.ctrl.audio.reset_diagnostics()
        from .rt import reset_gc_stats
        reset_gc_stats()
        out = getattr(self.mw, "output", None)
        st = getattr(out, "_diag", None) if out is not None else None
        if st:
            st["max"] = 0.0
        self.refresh()

    def reset_xrun(self):
        if hasattr(self.mw, "reset_underruns"):
            self.mw.reset_underruns()
        else:
            self.mw.ctrl.audio.reset_underruns()
        self.refresh()

    def _sample_cpu(self):
        p, t = time.process_time(), time.monotonic()
        p0, t0 = self._cpu0
        if t - t0 > 0.2:
            self._cpu = (p - p0) / (t - t0)
            self._cpu0 = (p, t)
            s = _proc_stat()
            if s is not None and self._sys0 is not None and s[1] > self._sys0[1]:
                self._sys = (s[0] - self._sys0[0]) / (s[1] - self._sys0[1])
            self._sys0 = s

    def text(self):
        """Riepilogo in testo semplice (per «Copia negli appunti»)."""
        perf = self.perf_out.toPlainText().strip()
        return self._summary(html=False) + "\n\n" + (("== " + tr("Prestazioni") + "\n" + perf + "\n\n")
                                                     if perf else "") + self.log.toPlainText()

    def _summary(self, html=True):
        from .audio_engine import BUFFER_LABELS
        mw = self.mw
        eng = mw.ctrl.audio
        ncpu = os.cpu_count() or 1
        lines = []

        def head(t):
            lines.append(f"<b>{t}</b>" if html else f"== {t}")

        def row(label, val, color=None):
            if html and color:
                val = f"<span style='color:{color};font-weight:bold'>{val}</span>"
            lines.append(f"{label}: {val}")
        head(tr("Processore"))
        row(APP_NAME, tr("{0:.0f} % di un core ({1:.0f} % del totale, {2} core)",
                               self._cpu * 100, self._cpu * 100 / ncpu, ncpu), _color(self._cpu / ncpu))
        if self._sys is not None:
            row(tr("Sistema"), f"{self._sys * 100:.0f} %", _color(self._sys))
        if hasattr(os, "getloadavg"):
            try:
                la = os.getloadavg()
                row(tr("Carico medio (1/5/15 min)"), f"{la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f}")
            except OSError:
                pass
        mem = _memory_mb()
        if mem:
            row(tr("Memoria (picco)"), f"{mem:.0f} MB")
        head(tr("Audio"))
        row(tr("Buffer"), BUFFER_LABELS.get(eng.buffer, eng.buffer))
        row(tr("Voci attive"), str(len(eng.voices)))
        if eng.buses:
            busy = sum(1 for b in eng.buses.values() if b.silent <= b.IDLE)
            row(tr("Bus effetti"), tr("{0} ({1} in elaborazione)", len(eng.buses), busy))
        for d in eng.diagnostics():
            kind = tr(" (senza scheda: simulata)") if d["null"] else ""
            lines.append(("&nbsp;&nbsp;• " if html else "  - ") + f"{d['name']}{kind} — {d['channels']} " + tr("canali"))
            row("&nbsp;&nbsp;&nbsp;&nbsp;" + tr("Blocco") if html else "    " + tr("Blocco"),
                tr("{0} campioni = {1:.1f} ms · latenza della scheda {2:.0f} ms", d["frames"], d["block_ms"],
                   d["latency_ms"]))
            row("&nbsp;&nbsp;&nbsp;&nbsp;" + tr("Calcolo del blocco") if html else "    " + tr("Calcolo del blocco"),
                tr("medio {0:.2f} ms ({1:.0f} %) · massimo {2:.2f} ms ({3:.0f} %)", d["avg_ms"], d["load"] * 100,
                   d["max_ms"], d["peak_load"] * 100), _color(d["peak_load"]))
            row("&nbsp;&nbsp;&nbsp;&nbsp;" + tr("Buchi audio (underrun)") if html else "    " + tr("Buchi audio (underrun)"),
                str(d["underruns"]), "#e05050" if d["underruns"] else None)
            if not d["null"]:
                pr = d.get("prio")
                row("&nbsp;&nbsp;&nbsp;&nbsp;" + tr("Thread audio") if html else "    " + tr("Thread audio"),
                    tr("in attesa del primo blocco") if pr is None else pr[1],
                    None if pr is None else ("#50c070" if pr[0] else "#e0b040"))
        from .rt import gc_stats
        g = gc_stats()
        # una pausa più lunga della latenza della scheda (l'audio già pronto) può causare un buco
        blk = min([d["latency_ms"] or d["block_ms"] for d in eng.diagnostics() if not d["null"]] or [0.0])
        gmax = g["max"] * 1000
        row(tr("Garbage collector"),
            tr("{0} oggetti congelati · {1} raccolte, pausa massima {2:.1f} ms (gen. {3}), ultima "
               "{4:.2f} ms", g["frozen"], g["count"], gmax, g["max_gen"], g["last"] * 1000),
            _color(gmax / blk) if blk else None)
        try:
            from .media import audio_memory
            with mw.loader.lock:
                arrays = list(mw.loader.audio.values())
            ram, disk = audio_memory(arrays)
            row(tr("File audio caricati"), tr("{0} · in memoria {1:.0f} MB · letti dal disco (cache) {2:.0f} MB",
                                               len(arrays), ram, disk))
        except Exception:
            pass
        head(tr("Video"))
        out = getattr(mw, "output", None)
        if out is not None:
            from .video_output import engine_labels
            eng_name = getattr(out, "engine_name", "")
            label = {"quick": tr("GPU (Qt Quick)"), "gl": tr("classico con OpenGL"),
                     "raster": tr("raster (solo processore)")}.get(eng_name, eng_name)
            api = out.api_name() if hasattr(out, "api_name") else ""
            row(tr("Motore video"), label + (f" · {api}" if api else "") + " · " + tr("scelto: {0}",
                engine_labels().get(mw.video_engine_setting(), "")))
        try:
            from .video_output import gpu_status
            import html as _html
            gtxt, glev = gpu_status(out)
            row(tr("GPU"), _html.escape(gtxt) if html else gtxt, _color((0.3, 0.75, 1.0)[glev]))
        except Exception:
            pass
        if out is None or not out.isVisible():
            row(tr("Finestra di uscita"), tr("chiusa"))
        else:
            st = getattr(out, "_diag", None) or {}
            mode = tr("schermo intero") if out.isFullScreen() else tr("ridotta a icona") if out.isMinimized() \
                else tr("in finestra")
            small = out.width() < 320 or out.height() < 180 or out.isMinimized()
            row(tr("Finestra di uscita"), tr("{0}×{1}, {2}", out.width(), out.height(), mode),
                _color(0.6) if small else None)
            ms_max = st.get("max", 0.0) * 1000
            row(tr("Disegno"), tr("{0:.0f} fotogrammi/s · medio {1:.1f} ms · massimo {2:.1f} ms", st.get("fps", 0.0),
                                  st.get("avg", 0.0) * 1000, ms_max), _color(ms_max / 40.0))
        row(tr("Livelli visibili"), str(len(mw.comp.layers)))
        dk = getattr(mw.ctrl, "video_decks", None)
        if dk is not None:
            row(tr("Video precaricati"), str(len(getattr(dk, "decks", {}) or {})))
        return "<br>".join(lines) if html else "\n".join(lines)

    def refresh(self):
        self._sample_cpu()
        self.info.setText(self._summary())
        eng = self.mw.ctrl.audio
        items = [(t, tr("[audio] ") + m) for t, m in list(eng.errors)] + list(self.mw.ctrl.log)
        sig = (len(items), items[-1] if items else None)
        if sig != self._log_sig:
            self._log_sig = sig
            items.sort(key=lambda x: x[0])
            self.log.setPlainText("\n".join(time.strftime("%H:%M:%S", time.localtime(t)) + "  " + m
                                            for t, m in items[-300:]))
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def copy(self):
        QGuiApplication.clipboard().setText(self.text())
