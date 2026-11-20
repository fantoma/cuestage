"""Interfaccia degli effetti interni: rack (elenco riordinabile), finestre di regolazione in sovrimpressione
per ogni effetto, curva dell'EQ, mandate delle cue ai bus e finestra dei bus effetti."""
import math

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from . import dsp
from .i18n import tr

# parametri regolabili: (chiave, etichetta, minimo, massimo, passo, decimali, unità, scala logaritmica)
PARAMS = {
    "gate": [("thr", tr("Soglia"), -90, 0, 1, 1, " dB", False),
             ("range", tr("Riduzione da chiuso"), -90, 0, 1, 1, " dB", False),
             ("attack", tr("Attacco"), 0.1, 100, 0.1, 1, " ms", True),
             ("hold", tr("Hold"), 0, 2000, 5, 0, " ms", False),
             ("release", tr("Rilascio"), 5, 3000, 5, 0, " ms", True)],
    "comp": [("thr", tr("Soglia"), -60, 0, 0.5, 1, " dB", False),
             ("ratio", tr("Rapporto"), 1, 20, 0.1, 1, ":1", True),
             ("knee", tr("Ginocchio"), 0, 24, 0.5, 1, " dB", False),
             ("attack", tr("Attacco"), 0.1, 300, 0.1, 1, " ms", True),
             ("release", tr("Rilascio"), 5, 3000, 5, 0, " ms", True),
             ("makeup", tr("Guadagno di compensazione"), 0, 24, 0.5, 1, " dB", False)],
    "delay": [("time", tr("Tempo"), 10, 2500, 1, 0, " ms", True),
              ("feedback", tr("Feedback"), 0, 95, 1, 0, " %", False),
              ("hp", tr("Passa-alto nel feedback"), 10, 2000, 1, 0, " Hz", True),
              ("lp", tr("Passa-basso nel feedback"), 500, 20000, 10, 0, " Hz", True),
              ("pitch", tr("Pitch per ripetizione"), -12, 12, 0.1, 1, " st", False),
              ("pitch_win", tr("Finestra del pitch"), 20, 120, 1, 0, " ms", False),
              ("mix", tr("Effetto (dry/wet)"), 0, 100, 1, 0, " %", False),
              ("level", tr("Livello effetto"), -60, 12, 0.5, 1, " dB", False)],
    "reverb": [("decay", tr("Decadimento (T60)"), 0.1, 15, 0.1, 1, " s", True),
               ("predelay", tr("Pre-delay"), 0, 250, 1, 0, " ms", False),
               ("size", tr("Dimensione"), 0, 100, 1, 0, " %", False),
               ("damp", tr("Smorzamento acuti"), 500, 20000, 10, 0, " Hz", True),
               ("width", tr("Larghezza stereo"), 0, 100, 1, 0, " %", False),
               ("mix", tr("Effetto (dry/wet)"), 0, 100, 1, 0, " %", False),
               ("level", tr("Livello effetto"), -60, 12, 0.5, 1, " dB", False)],
}
_WINDOWS = {}        # (chiave proprietario, id effetto) -> finestra aperta


def fx_summary(fx):
    p = dsp.params(fx)
    k = fx.get("type")
    if k == "gate":
        return tr("soglia {0:.0f} dB", p["thr"])
    if k == "comp":
        return tr("{0:.0f} dB · {1:.1f}:1", p["thr"], p["ratio"])
    if k == "eq":
        n = sum(1 for b in p["bands"] if b.get("on", True) and abs(float(b.get("g", 0))) > 0.01)
        parts = [tr("{0} bande", n)]
        if p.get("hpf_on"):
            parts.append(tr("HPF {0:.0f} Hz", p["hpf_f"]))
        if p.get("lpf_on"):
            parts.append(tr("LPF {0:.0f} Hz", p["lpf_f"]))
        return " · ".join(parts)
    if k == "delay":
        txt = tr("{0:.0f} ms · fb {1:.0f} %", p["time"], p["feedback"])
        if abs(float(p.get("pitch") or 0.0)) > 1e-3:
            txt += " · " + (tr("pitch {0:+.1f} st", p["pitch"]) if p.get("pitch_out")
                            else tr("pitch {0:+.1f} st a ripetizione", p["pitch"]))
        return txt
    if k == "reverb":
        return tr("{0:.1f} s · pre {1:.0f} ms", p["decay"], p["predelay"])
    return ""


class _ParamRow(QWidget):
    """Cursore + casella numerica per un parametro (scala logaritmica dove serve)."""
    changed = Signal(float)

    def __init__(self, lo, hi, step, dec, unit, log, value, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.log = float(lo), float(hi), bool(log) and lo > 0
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.sl = QSlider(Qt.Horizontal)
        self.sl.setRange(0, 1000)
        self.sp = QDoubleSpinBox()
        self.sp.setRange(lo, hi)
        self.sp.setSingleStep(step)
        self.sp.setDecimals(dec)
        self.sp.setSuffix(unit)
        self.sp.setKeyboardTracking(False)
        self.sp.setMinimumWidth(96)
        h.addWidget(self.sl, 1)
        h.addWidget(self.sp)
        self.set(value)
        self.sl.valueChanged.connect(self._from_slider)
        self.sp.valueChanged.connect(self._from_spin)

    def _to_pos(self, v):
        if self.log:
            return int(round(1000 * math.log(v / self.lo) / math.log(self.hi / self.lo)))
        return int(round(1000 * (v - self.lo) / (self.hi - self.lo)))

    def _to_val(self, pos):
        f = pos / 1000.0
        if self.log:
            return self.lo * (self.hi / self.lo) ** f
        return self.lo + (self.hi - self.lo) * f

    def set(self, v):
        v = max(self.lo, min(self.hi, float(v)))
        self.sp.blockSignals(True)
        self.sl.blockSignals(True)
        self.sp.setValue(v)
        self.sl.setValue(self._to_pos(v))
        self.sp.blockSignals(False)
        self.sl.blockSignals(False)

    def _from_slider(self, pos):
        v = self._to_val(pos)
        self.sp.blockSignals(True)
        self.sp.setValue(v)
        self.sp.blockSignals(False)
        self.changed.emit(self.sp.value())

    def _from_spin(self, v):
        self.sl.blockSignals(True)
        self.sl.setValue(self._to_pos(v))
        self.sl.blockSignals(False)
        self.changed.emit(v)


class EqCurve(QWidget):
    """Curva di risposta dell'EQ (20 Hz – 20 kHz, ±18 dB) con i punti delle bande: si trascinano con il mouse
    (frequenza e guadagno) e la rotella sopra un punto allarga o restringe la banda (Q)."""
    bandChanged = Signal(int, dict)        # banda, {chiave: valore}

    def __init__(self, get_fx, parent=None):
        super().__init__(parent)
        self.get_fx = get_fx
        self.drag = None
        self.hover = None
        self.rta = None              # (frequenze, dBFS) dell'analizzatore di spettro dell'ingresso
        self.setMinimumSize(420, 190)
        self.setMouseTracking(True)

    RTA_FREQS = None

    def set_rta(self, samples):
        """Analizzatore di spettro (RTA) dell'ingresso: FFT con finestra di Hann, bande da 1/6 di ottava,
        salita veloce e discesa lenta. samples = ultimi campioni d'ingresso (mono) o None."""
        import numpy as _np
        if samples is None or len(samples) < 1024:
            if self.rta is not None:
                self.rta = None
                self.update()
            return
        n = len(samples)
        win = _np.hanning(n)
        spec = _np.abs(_np.fft.rfft(samples * win)) * (2.0 / win.sum())
        fr = _np.fft.rfftfreq(n, 1.0 / dsp.SR)
        if EqCurve.RTA_FREQS is None:
            EqCurve.RTA_FREQS = 20.0 * 2 ** (_np.arange(0, 10 * 6 + 1) / 6.0)
        centers = EqCurve.RTA_FREQS
        lo, hi = centers * 2 ** (-1 / 12), centers * 2 ** (1 / 12)
        pw = spec ** 2
        cum = _np.concatenate(([0.0], _np.cumsum(pw)))
        i0 = _np.searchsorted(fr, lo)
        i1 = _np.maximum(_np.searchsorted(fr, hi), i0 + 1)
        band = _np.sqrt((cum[i1] - cum[i0]))
        db = 20 * _np.log10(_np.maximum(band, 1e-7))
        if self.rta is not None and len(self.rta[1]) == len(db):
            old = self.rta[1]
            db = _np.where(db > old, db, old * 0.7 + db * 0.3 - 0.0)   # discesa morbida
        self.rta = (centers, db)
        self.update()

    def _y_rta(self, db):
        """RTA: scala propria −90…0 dBFS su tutta l'altezza."""
        h = self.height() - 20
        return 10 + h * (1.0 - (max(-90.0, min(0.0, db)) + 90.0) / 90.0)

    def _f_at(self, x):
        return 20.0 * 10 ** (3.0 * (x - 30) / max(1.0, self.width() - 40))

    def _db_at(self, y):
        h = self.height() - 20
        return (0.5 - (y - 10) / max(1.0, h)) * 36.0

    def _band_at(self, pos):
        fx = self.get_fx()
        if fx is None:
            return None
        best, bd = None, 14.0 ** 2
        for i, b in enumerate(dsp.params(fx)["bands"]):
            if not b.get("on", True):
                continue
            dx = self._x(float(b["f"])) - pos.x()
            dy = self._y(float(b["g"])) - pos.y()
            if dx * dx + dy * dy < bd:
                best, bd = i, dx * dx + dy * dy
        return best

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag = self._band_at(e.position())
            if self.drag is not None:
                self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.drag is None:
            b = self._band_at(pos)
            if b != self.hover:
                self.hover = b
                self.setCursor(Qt.OpenHandCursor if b is not None else Qt.ArrowCursor)
                self.setToolTip(tr("Trascina: frequenza e guadagno · rotella: larghezza della banda (Q)")
                                if b is not None else "")
            return
        f = max(20.0, min(20000.0, self._f_at(pos.x())))
        g = max(-18.0, min(18.0, self._db_at(pos.y())))
        if e.modifiers() & Qt.ShiftModifier:            # Shift: solo il guadagno
            self.bandChanged.emit(self.drag, {"g": round(g, 1)})
        else:
            self.bandChanged.emit(self.drag, {"f": round(f, 0 if f >= 100 else 1), "g": round(g, 1)})
        self.update()

    def mouseReleaseEvent(self, e):
        if self.drag is not None:
            self.drag = None
            self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, e):
        i = self._band_at(e.position())
        fx = self.get_fx()
        if i is None or fx is None:
            e.ignore()
            return
        q = float(dsp.params(fx)["bands"][i].get("q", 1.0))
        step = 1.12 if e.angleDelta().y() > 0 else 1 / 1.12          # rotella in su = banda più stretta
        q = max(0.1, min(10.0, q * step))
        self.bandChanged.emit(i, {"q": round(q, 2)})
        self.update()
        e.accept()

    def _x(self, f):
        return 30 + (self.width() - 40) * math.log10(f / 20.0) / 3.0

    def _y(self, db):
        h = self.height() - 20
        return 10 + h * (0.5 - max(-18.0, min(18.0, db)) / 36.0)

    def paintEvent(self, _e):
        fx = self.get_fx()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#16181d"))
        small = QFont()
        small.setPointSizeF(max(6.5, small.pointSizeF() * 0.75))
        p.setFont(small)
        for f in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000):
            x = self._x(f)
            p.setPen(QPen(QColor("#2a2e36"), 1))
            p.drawLine(QPointF(x, 10), QPointF(x, self.height() - 10))
            p.setPen(QColor("#777"))
            p.drawText(QPointF(x + 2, self.height() - 2), f"{f // 1000}k" if f >= 1000 else str(f))
        for db in (-12, -6, 0, 6, 12):
            y = self._y(db)
            p.setPen(QPen(QColor("#3a3f4a" if db == 0 else "#2a2e36"), 1))
            p.drawLine(QPointF(30, y), QPointF(self.width() - 10, y))
            p.setPen(QColor("#777"))
            p.drawText(QPointF(2, y + 4), f"{db:+d}")
        if self.rta is not None:                  # spettro dell'ingresso, dietro la curva
            fr, db = self.rta
            poly = QPainterPath()
            base = self._y_rta(-90.0)
            poly.moveTo(QPointF(self._x(float(fr[0])), base))
            for f, d in zip(fr, db):
                if 20.0 <= f <= 20000.0:
                    poly.lineTo(QPointF(self._x(float(f)), self._y_rta(float(d))))
            poly.lineTo(QPointF(self._x(float(min(fr[-1], 20000.0))), base))
            poly.closeSubpath()
            p.setPen(QPen(QColor(120, 200, 120, 150), 1))
            p.setBrush(QColor(120, 200, 120, 45))
            p.drawPath(poly)
            p.setBrush(Qt.NoBrush)
            p.setPen(QColor("#6a9a6a"))
            p.drawText(QPointF(self.width() - 92, 22), tr("RTA ingresso"))
        if fx is None:
            return
        prm = dsp.params(fx)
        freqs = [20.0 * 10 ** (3.0 * i / 300) for i in range(301)]
        try:
            resp = dsp.eq_response(prm, freqs)
        except Exception:
            return
        path = QPainterPath()
        for i, (f, db) in enumerate(zip(freqs, resp)):
            pt = QPointF(self._x(f), self._y(float(db)))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setPen(QPen(QColor("#50c8ff") if fx.get("on", True) else QColor("#666"), 2))
        p.drawPath(path)
        for i, b in enumerate(prm["bands"]):
            if not b.get("on", True):
                continue
            pt = QPointF(self._x(float(b["f"])), self._y(float(b["g"])))
            p.setPen(QPen(QColor("#fff"), 1))
            p.setBrush(QColor("#ffaa28"))
            p.drawEllipse(pt, 5, 5)
            p.drawText(pt + QPointF(6, -6), str(i + 1))


class DynGraph(QWidget):
    """Curva del gate o del compressore: livello d'uscita in funzione di quello d'entrata (−60…0 dB), con la
    soglia e il punto di lavoro attuale (picco d'entrata)."""
    LO = -60.0

    def __init__(self, kind, get_fx, parent=None):
        super().__init__(parent)
        self.kind, self.get_fx = kind, get_fx
        self.point = None
        self.setMinimumSize(220, 220)

    def set_point(self, db):
        self.point = db
        self.update()

    def _map(self, x_db, y_db):
        s = min(self.width(), self.height()) - 36
        x0, y0 = 28, 8
        fx = (max(self.LO, min(0.0, x_db)) - self.LO) / -self.LO
        fy = (max(self.LO, min(12.0, y_db)) - self.LO) / -self.LO
        return QPointF(x0 + fx * s, y0 + s - fy * s)

    def paintEvent(self, _e):
        fx = self.get_fx()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#16181d"))
        small = QFont()
        small.setPointSizeF(max(6.5, small.pointSizeF() * 0.75))
        p.setFont(small)
        for db in range(-60, 1, 12):
            a, b = self._map(db, self.LO), self._map(db, 0)
            c, d = self._map(self.LO, db), self._map(0, db)
            p.setPen(QPen(QColor("#2a2e36"), 1))
            p.drawLine(a, b)
            p.drawLine(c, d)
            p.setPen(QColor("#777"))
            p.drawText(QPointF(2, d.y() + 4), str(db))
            p.drawText(a + QPointF(-8, 12), str(db))
        p.setPen(QPen(QColor("#444"), 1, Qt.DashLine))
        p.drawLine(self._map(self.LO, self.LO), self._map(0, 0))          # diagonale 1:1
        if fx is None:
            return
        prm = dsp.params(fx)
        xs = [self.LO + i * (-self.LO) / 120 for i in range(121)]
        ys = (dsp.comp_curve if self.kind == "comp" else dsp.gate_curve)(prm, xs)
        thr = float(prm["thr"])
        p.setPen(QPen(QColor("#ffaa28"), 1, Qt.DotLine))
        p.drawLine(self._map(thr, self.LO), self._map(thr, 0))
        path = QPainterPath()
        for i, (x, y) in enumerate(zip(xs, ys)):
            pt = self._map(x, float(y))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setPen(QPen(QColor("#50c8ff") if fx.get("on", True) else QColor("#666"), 2))
        p.drawPath(path)
        if self.point is not None and self.point > self.LO:
            y = float((dsp.comp_curve if self.kind == "comp" else dsp.gate_curve)(prm, [self.point])[0])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(self._map(self.point, y), 4.5, 4.5)


class LevelMeter(QWidget):
    """Vumeter verticale (−60…0 dBFS); con reduction=True mostra la riduzione di guadagno dall'alto (0…−30 dB)."""

    def __init__(self, label, reduction=False, parent=None):
        super().__init__(parent)
        self.label, self.reduction = label, reduction
        self.db = 0.0 if reduction else -120.0
        self.setFixedWidth(30)
        self.setMinimumHeight(180)

    def set_db(self, db):
        db = float(db)
        if abs(db - self.db) > 0.05:
            self.db = db
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        h = self.height() - 34
        x, w, top = 6, self.width() - 12, 4
        p.fillRect(x, top, w, h, QColor("#16181d"))
        if self.reduction:
            f = max(0.0, min(1.0, -self.db / 30.0))
            p.fillRect(x, top, w, int(h * f), QColor("#ffaa28"))
            txt = f"{self.db:.0f}" if self.db < -0.5 else "0"
        else:
            f = max(0.0, min(1.0, (self.db + 60.0) / 60.0))
            col = QColor("#e05050") if self.db > -3 else QColor("#e0b040") if self.db > -12 else QColor("#50c070")
            hh = int(h * f)
            p.fillRect(x, top + h - hh, w, hh, col)
            txt = "-∞" if self.db <= -59.5 else f"{self.db:.0f}"
        p.setPen(QColor("#ccc"))
        small = QFont()
        small.setPointSizeF(max(6.5, small.pointSizeF() * 0.75))
        p.setFont(small)
        p.drawText(QRectF(0, top + h + 2, self.width(), 14), Qt.AlignHCenter, txt)
        p.drawText(QRectF(0, top + h + 15, self.width(), 14), Qt.AlignHCenter, self.label)


class HMeter(QWidget):
    """Vumeter orizzontale compatto (−60…0 dBFS) con una barra per canale (L, R) e il valore del più alto."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dbs = []
        self.setFixedHeight(18)
        self.setMinimumWidth(150)

    def set_peaks(self, peaks):
        """Picchi lineari (uno per canale) o None = sorgente ferma."""
        dbs = [] if not peaks else [20 * math.log10(max(float(x), 1e-6)) for x in peaks]
        if len(dbs) != len(self.dbs) or any(abs(a - b) > 0.2 for a, b in zip(dbs, self.dbs)):
            self.dbs = dbs
            self.update()

    def text(self):
        if not self.dbs:
            return "—"
        m = max(self.dbs)
        return "-∞" if m <= -59.5 else f"{m:+.0f}"

    def paintEvent(self, _e):
        p = QPainter(self)
        tw = 34
        W, H = self.width() - tw - 4, self.height()
        p.fillRect(0, 0, W, H, QColor("#16181d"))
        n = max(1, len(self.dbs))
        bh = (H - 2 - (n - 1)) / n
        for i, db in enumerate(self.dbs):
            f = max(0.0, min(1.0, (db + 60.0) / 60.0))
            col = QColor("#e05050") if db > -3 else QColor("#e0b040") if db > -12 else QColor("#50c070")
            p.fillRect(QRectF(1, 1 + i * (bh + 1), (W - 2) * f, bh), col)
        p.setPen(QColor("#555"))
        for mark in (-48, -24, -12, -6):                   # tacche
            x = 1 + (W - 2) * (mark + 60.0) / 60.0
            p.drawLine(QPointF(x, 0), QPointF(x, 3))
        p.setPen(self.palette().windowText().color())     # leggibile con tema chiaro e scuro
        small = QFont()
        small.setPointSizeF(max(6.5, small.pointSizeF() * 0.85))
        p.setFont(small)
        p.drawText(QRectF(W + 4, 0, tw, H), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class _ShowKeys(QObject):
    """Filtro dell'applicazione: nelle finestre degli effetti e dei bus lo Spazio è sempre il GO, anche se il
    fuoco è su una casella di spunta o un pulsante (prima lo Spazio accendeva / spegneva l'effetto o premeva il
    pulsante, e la cue non partiva). Nei campi di testo veri (nome del bus) lo Spazio resta uno spazio."""

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit, QPlainTextEdit, QTextEdit
        t = e.type()
        if t not in (QEvent.ShortcutOverride, QEvent.KeyPress):
            return False
        if e.key() != Qt.Key_Space or e.modifiers() not in (Qt.NoModifier, Qt.KeypadModifier):
            return False
        win = QApplication.activeWindow()
        mw = getattr(win, "_show_keys", None) if win is not None else None
        if mw is None:
            return False
        fw = QApplication.focusWidget()
        if isinstance(fw, (QPlainTextEdit, QTextEdit)) or (
                isinstance(fw, QLineEdit) and not isinstance(fw.parentWidget(), QAbstractSpinBox)):
            return False
        if t == QEvent.ShortcutOverride:
            e.accept()                      # niente scorciatoie del widget: arriva il KeyPress qui sotto
            return True
        if not e.isAutoRepeat():
            mw.go()
        return True


def install_show_keys(win):
    """Tasti di regia (Spazio = GO, Esc = stop, F7/F8 = pausa/riprendi) anche su questa finestra: con la finestra di
    un effetto o dei bus in primo piano il GO deve partire comunque."""
    from PySide6.QtWidgets import QApplication
    p = win.parentWidget()
    while p is not None and not hasattr(p, "_build_shortcuts"):
        p = p.parentWidget()
    if p is None:
        return
    win._show_keys = p                      # finestra principale che riceve il GO
    p._build_shortcuts(win)
    app = QApplication.instance()
    if getattr(app, "_cs_show_keys", None) is None:
        app._cs_show_keys = _ShowKeys()
        app.installEventFilter(app._cs_show_keys)


class FxWindow(QDialog):
    """Finestra in sovrimpressione con i parametri di un effetto del rack: resta aperta sopra Cuevivo e le
    modifiche valgono subito (anche mentre la cue o il bus suona)."""

    def __init__(self, owner_key, fid, title, get_list, on_change, meter_fn=None, parent=None, live_fn=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint | Qt.WindowTitleHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.owner_key, self.fid = owner_key, fid
        self.get_list, self.on_change, self.meter_fn = get_list, on_change, meter_fn
        self.live_fn = live_fn            # effetti in uso dal vivo (bus cambiato da una cue): si mostrano quelli
        self._shown_sig = None
        fx = self.shown()
        self.kind = fx.get("type") if fx else ""
        self.setWindowTitle(f"{dsp.FX_LABELS.get(self.kind, self.kind)} — {title}")
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.on_cb = QCheckBox(tr("Acceso"))
        self.on_cb.setChecked(bool(fx.get("on", True)) if fx else False)
        self.on_cb.toggled.connect(lambda on: self._set("on", bool(on)))
        top.addWidget(self.on_cb)
        top.addStretch(1)
        self.meter = QLabel()
        self.meter.setStyleSheet("font-weight:bold;color:#ffaa28;")
        top.addWidget(self.meter)
        reset = QPushButton(tr("Valori predefiniti"))
        reset.clicked.connect(self._defaults)
        top.addWidget(reset)
        v.addLayout(top)
        self.body = QWidget()
        v.addWidget(self.body)
        self._build()
        self.timer = QTimer(self)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        _WINDOWS[(owner_key, fid)] = self
        install_show_keys(self)

    def done(self, r):
        _WINDOWS.pop((self.owner_key, self.fid), None)
        super().done(r)

    def closeEvent(self, e):
        _WINDOWS.pop((self.owner_key, self.fid), None)
        super().closeEvent(e)

    def fx(self):
        for f in self.get_list() or []:
            if f.get("id") == self.fid:
                return f
        return None

    def shown(self):
        """Valori da mostrare: quelli in uso dal vivo (cue Controllo bus, Cambia preset) o quelli salvati."""
        if self.live_fn is not None:
            for f in self.live_fn() or []:
                if f.get("id") == self.fid:
                    return f
        return self.fx()

    def _sig(self):
        f = self.shown()
        return repr(dsp.params(f)) if f is not None else ""

    @staticmethod
    def _discrete_sig(p):
        """Valori non numerici (acceso/spento, tipi di banda, pendenze…): se cambiano la finestra si ricostruisce."""
        flat = {k: v for k, v in p.items() if k != "bands" and not isinstance(v, (int, float)) or isinstance(v, bool)}
        bands = [(b.get("on", True), b.get("type")) for b in p.get("bands") or []]
        return repr((sorted(flat.items()), bands, p.get("hpf_slope"), p.get("lpf_slope")))

    def _follow_live(self):
        """Mostra i valori in uso dal vivo: i cursori si spostano al loro posto (non quello che si sta trascinando
        o la casella in cui si sta scrivendo); se cambia un valore non numerico la finestra si ricostruisce."""
        fx = self.shown()
        if fx is None:
            return
        p = dsp.params(fx)
        if self._discrete_sig(p) != getattr(self, "_discrete", None):
            from PySide6.QtWidgets import QApplication
            if QApplication.mouseButtons() == Qt.NoButton:
                self._build()
            return
        for path, row in list(getattr(self, "rows", {}).items()):
            try:
                if row.sl.isSliderDown() or row.sp.hasFocus():
                    continue
                if isinstance(path, tuple):
                    _b, i, k = path
                    val = p["bands"][i].get(k)
                else:
                    val = p.get(path)
                if val is not None and abs(row.sp.value() - float(val)) > 1e-6:
                    row.set(float(val))
            except (RuntimeError, IndexError, KeyError):
                pass
        for name in ("curve", "graph"):
            wdg = getattr(self, name, None)
            if wdg is not None:
                try:
                    wdg.update()
                except RuntimeError:
                    pass
        self._shown_sig = self._sig()

    def _changed(self):
        """Modifica dalla finestra: si salva (e vale anche dal vivo); la finestra non si ricostruisce per questo."""
        self.on_change()
        self._shown_sig = self._sig()

    def _set(self, key, val, band=None):
        fx = self.fx()
        if fx is None:
            return
        if band is not None:
            bands = dsp.params(fx)["bands"]
            bands[band][key] = val
            fx["bands"] = bands
        else:
            fx[key] = val
        self._changed()
        if self.kind == "eq":
            self.curve.update()
        elif self.kind in ("gate", "comp") and getattr(self, "graph", None) is not None:
            self.graph.update()

    def _defaults(self):
        fx = self.fx()
        if fx is None:
            return
        import copy
        for k, val in copy.deepcopy(dsp.DEFAULTS[self.kind]).items():
            fx[k] = val
        self._changed()
        self._build()

    def _build(self):
        old = self.body
        self.body = QWidget()
        self.layout().replaceWidget(old, self.body)
        old.deleteLater()
        fx = self.shown()
        if fx is None:
            return
        self._shown_sig = self._sig()
        p = dsp.params(fx)
        self.rows = {}                    # parametro (o ("band", i, chiave)) -> riga, per seguire i valori dal vivo
        self._discrete = self._discrete_sig(p)
        if self.kind == "eq":
            self._build_eq(p)
            return
        if self.kind in ("gate", "comp"):
            outer = QVBoxLayout(self.body)
            outer.setContentsMargins(0, 0, 0, 0)
            top = QHBoxLayout()
            self.graph = DynGraph(self.kind, self.shown)
            top.addWidget(self.graph, 1)
            self.meters = {}
            for key, label in (("in", tr("In")), ("gr", tr("Rid.")), ("out", tr("Out"))):
                m = LevelMeter(label, reduction=(key == "gr"))
                self.meters[key] = m
                top.addWidget(m)
            outer.addLayout(top)
            f = QFormLayout()
            outer.addLayout(f)
        else:
            f = QFormLayout(self.body)
        for key, label, lo, hi, step, dec, unit, log in PARAMS.get(self.kind, []):
            row = _ParamRow(lo, hi, step, dec, unit, log, p.get(key, lo))
            row.changed.connect(lambda val, key=key: self._set(key, float(val)))
            f.addRow(label, row)
            self.rows[key] = row
        if self.kind in ("gate", "comp"):
            self.graph.update()
        if self.kind == "delay":
            cb = QCheckBox(tr("Ping-pong (l'eco rimbalza tra sinistra e destra)"))
            cb.setChecked(bool(p.get("pingpong")))
            cb.toggled.connect(lambda on: self._set("pingpong", bool(on)))
            f.addRow(cb)
            po = QCheckBox(tr("Pitch solo sull'uscita (tutte le ripetizioni insieme, non si accumula)"))
            po.setChecked(bool(p.get("pitch_out")))
            po.setToolTip(tr("Spento: il pitch è nel feedback e ogni ripetizione sale (o scende) rispetto alla "
                             "precedente. Acceso: si sposta solo l'uscita del delay, utile per un glissando "
                             "con la cue «Controllo bus»."))
            po.toggled.connect(lambda on: self._set("pitch_out", bool(on)))
            f.addRow(po)
        self.adjustSize()

    def _curve_band(self, i, vals):
        """Punto della banda trascinato nella curva (o rotella): valori e caselle aggiornati."""
        fx = self.fx()
        if fx is None:
            return
        bands = dsp.params(fx)["bands"]
        bands[i].update(vals)
        fx["bands"] = bands
        rows = self.band_rows[i] if i < len(self.band_rows) else {}
        for k, val in vals.items():
            if k in rows:
                rows[k].set(val)
        self._changed()

    def _build_eq(self, p):
        v = QVBoxLayout(self.body)
        v.setContentsMargins(0, 0, 0, 0)
        self.curve = EqCurve(self.shown)
        self.curve.bandChanged.connect(self._curve_band)
        self.band_rows = []
        v.addWidget(self.curve)
        hint = QLabel(tr("Trascina i punti delle bande (Shift: solo il guadagno) · rotella sopra un punto: Q"))
        hint.setStyleSheet("color:#888;font-size:11px;")
        v.addWidget(hint)
        g = QGridLayout()
        for col, text in enumerate((tr("Banda"), tr("Tipo"), tr("Frequenza"), tr("Guadagno"), tr("Q"))):
            lab = QLabel(text)
            lab.setStyleSheet("color:#999;")
            g.addWidget(lab, 0, col)
        for i, b in enumerate(p["bands"]):
            self.band_rows.append({})
            on = QCheckBox(str(i + 1))
            on.setChecked(bool(b.get("on", True)))
            on.toggled.connect(lambda val, i=i: self._set("on", bool(val), band=i))
            g.addWidget(on, i + 1, 0)
            ty = QComboBox()
            for k in dsp.BAND_TYPES:
                ty.addItem(dsp.BAND_LABELS[k], k)
            ty.setCurrentIndex(max(0, ty.findData(b.get("type", "peak"))))
            ty.currentIndexChanged.connect(lambda _j, ty=ty, i=i: self._set("type", ty.currentData(), band=i))
            g.addWidget(ty, i + 1, 1)
            for col, (key, lo, hi, step, dec, unit, log) in enumerate(
                    (("f", 20, 20000, 1, 0, " Hz", True), ("g", -18, 18, 0.5, 1, " dB", False),
                     ("q", 0.1, 10, 0.05, 2, "", True)), start=2):
                row = _ParamRow(lo, hi, step, dec, unit, log, b.get(key, lo))
                row.changed.connect(lambda val, key=key, i=i: self._set(key, float(val), band=i))
                g.addWidget(row, i + 1, col)
                self.band_rows[i][key] = row
                self.rows[("band", i, key)] = row
        v.addLayout(g)
        f = QFormLayout()
        for pre, label in (("hpf", tr("Passa-alto")), ("lpf", tr("Passa-basso"))):
            h = QHBoxLayout()
            cb = QCheckBox(tr("Acceso"))
            cb.setChecked(bool(p.get(f"{pre}_on")))
            cb.toggled.connect(lambda val, pre=pre: self._set(f"{pre}_on", bool(val)))
            h.addWidget(cb)
            row = _ParamRow(20, 20000, 1, 0, " Hz", True, p.get(f"{pre}_f", 100))
            row.changed.connect(lambda val, pre=pre: self._set(f"{pre}_f", float(val)))
            h.addWidget(row, 1)
            self.rows[f"{pre}_f"] = row
            sl = QComboBox()
            sl.addItem("12 dB/oct", 12)
            sl.addItem("24 dB/oct", 24)
            sl.setCurrentIndex(max(0, sl.findData(int(p.get(f"{pre}_slope", 12)))))
            sl.currentIndexChanged.connect(lambda _j, sl=sl, pre=pre: self._set(f"{pre}_slope", int(sl.currentData())))
            h.addWidget(sl)
            f.addRow(label, h)
        out = _ParamRow(-18, 18, 0.5, 1, " dB", False, p.get("gain", 0.0))
        out.changed.connect(lambda val: self._set("gain", float(val)))
        f.addRow(tr("Guadagno d'uscita"), out)
        self.rows["gain"] = out
        v.addLayout(f)
        self.adjustSize()

    def _poll(self):
        fx = self.fx()
        if fx is None:            # effetto tolto (o cue / bus eliminati): la finestra si chiude
            self.close()
            return
        if self.live_fn is not None and self._sig() != self._shown_sig:
            self._follow_live()                               # valori cambiati da una cue (sfumano dal vivo)
        fx = self.shown()
        if self.on_cb.isChecked() != bool(fx.get("on", True)):
            self.on_cb.blockSignals(True)
            self.on_cb.setChecked(bool(fx.get("on", True)))
            self.on_cb.blockSignals(False)
        m = self.meter_fn(self.fid) if self.meter_fn else None
        if self.kind == "eq" and getattr(self, "curve", None) is not None:
            try:
                self.curve.set_rta((m or {}).get("rta"))
            except RuntimeError:
                pass
            m = None
        if self.kind in ("gate", "comp") and getattr(self, "meters", None):
            mm = m or {}
            self.meters["in"].set_db(mm.get("in", -120.0))
            self.meters["out"].set_db(mm.get("out", -120.0))
            self.meters["gr"].set_db(mm.get("gr", 0.0))
            self.graph.set_point(mm.get("in") if m else None)
        if not m:
            self.meter.setText("")
        elif self.kind == "gate":
            self.meter.setText((tr("● aperto") if m.get("open") else tr("○ chiuso")) + f"  {m.get('gr', 0):.1f} dB")
        elif "gr" in m:
            self.meter.setText(tr("Riduzione {0:.1f} dB", m["gr"]))


def open_fx_window(owner_key, fid, title, get_list, on_change, meter_fn=None, parent=None, live_fn=None):
    w = _WINDOWS.get((owner_key, fid))
    if w is None:
        w = FxWindow(owner_key, fid, title, get_list, on_change, meter_fn, parent, live_fn)
    w.show()
    w.raise_()
    w.activateWindow()
    return w


class RackEditor(QWidget):
    """Rack: effetti nell'ordine di elaborazione (dall'alto in basso). Trascina per riordinare, spunta per
    accendere o spegnere, doppio clic (o «Regola…») per la finestra dei parametri."""
    changed = Signal()

    def __init__(self, owner_key, title, get_list, set_list, allowed, meter_fn=None, parent=None, live_fn=None):
        super().__init__(parent)
        self.owner_key, self.title = owner_key, title
        self.live_fn = live_fn
        self.get_list, self.set_list, self.allowed, self.meter_fn = get_list, set_list, allowed, meter_fn
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        if not dsp.available():
            warn = QLabel(tr("⚠ Effetti non disponibili: manca la libreria scipy (pip install scipy). "
                             "Il suono passa senza effetti.") + "\n" + dsp.unavailable_reason())
            warn.setTextInteractionFlags(Qt.TextSelectableByMouse)
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#e0b040;")
            v.addWidget(warn)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setMinimumHeight(110)
        self.list.itemDoubleClicked.connect(lambda it: self.open(it.data(Qt.UserRole)))
        self.list.itemChanged.connect(self._item_changed)
        self.list.model().rowsMoved.connect(lambda *a: QTimer.singleShot(0, self._reordered))
        v.addWidget(self.list)
        h = QHBoxLayout()
        add = QPushButton(tr("+ Effetto"))
        m = QMenu(add)
        for k in allowed:
            m.addAction(dsp.FX_LABELS[k], lambda k=k: self.add(k))
        add.setMenu(m)
        h.addWidget(add)
        for text, fn in ((tr("Regola…"), lambda: self.open(self._sel())), (tr("↑"), lambda: self.move(-1)),
                         (tr("↓"), lambda: self.move(1)), (tr("Rimuovi"), self.remove)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        self.reload()

    def _items(self):
        lst = self.get_list()
        return lst if isinstance(lst, list) else []

    def reload(self):
        sel = self._sel()
        self.list.blockSignals(True)
        self.list.clear()
        for fx in self._items():
            if fx.get("type") not in self.allowed:
                continue
            it = QListWidgetItem(f"{dsp.FX_LABELS.get(fx['type'], fx['type'])}   —   {fx_summary(fx)}")
            it.setData(Qt.UserRole, fx.get("id"))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            it.setCheckState(Qt.Checked if fx.get("on", True) else Qt.Unchecked)
            self.list.addItem(it)
            if fx.get("id") == sel:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)

    def _sel(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def _commit(self, new):
        self.set_list(new)
        self.changed.emit()
        self.reload()

    def add(self, kind):
        fx = dsp.new_fx(kind)
        self._commit(list(self._items()) + [fx])
        self.open(fx["id"])

    def remove(self):
        fid = self._sel()
        if fid is None:
            return
        w = _WINDOWS.get((self.owner_key, fid))
        if w is not None:
            w.close()
        self._commit([f for f in self._items() if f.get("id") != fid])

    def move(self, d):
        fid = self._sel()
        items = list(self._items())
        i = next((k for k, f in enumerate(items) if f.get("id") == fid), -1)
        j = i + d
        if i < 0 or not (0 <= j < len(items)):
            return
        items[i], items[j] = items[j], items[i]
        self._commit(items)

    def _reordered(self):
        ids = [self.list.item(r).data(Qt.UserRole) for r in range(self.list.count())]
        by = {f.get("id"): f for f in self._items()}
        new = [by[i] for i in ids if i in by] + [f for f in self._items() if f.get("id") not in ids]
        if [f.get("id") for f in new] != [f.get("id") for f in self._items()]:
            self._commit(new)

    def _item_changed(self, it):
        fid = it.data(Qt.UserRole)
        for f in self._items():
            if f.get("id") == fid:
                on = it.checkState() == Qt.Checked
                if bool(f.get("on", True)) != on:
                    f["on"] = on
                    self.changed.emit()

    def open(self, fid):
        if fid is None:
            return None

        def changed():
            self.changed.emit()
            QTimer.singleShot(0, self._refresh_text)
        return open_fx_window(self.owner_key, fid, self.title, self._items, changed, self.meter_fn, self.window(),
                              self.live_fn)

    def _refresh_text(self):
        """Testo e spunta di ogni effetto, con i valori in uso dal vivo se una cue li ha cambiati."""
        by = {f.get("id"): f for f in self._items()}
        if self.live_fn is not None:
            by.update({f.get("id"): f for f in (self.live_fn() or []) if f.get("id") in by})
        for r in range(self.list.count()):
            it = self.list.item(r)
            fx = by.get(it.data(Qt.UserRole))
            if fx is not None:
                txt = f"{dsp.FX_LABELS.get(fx['type'], fx['type'])}   —   {fx_summary(fx)}"
                chk = Qt.Checked if fx.get("on", True) else Qt.Unchecked
                if it.text() != txt or it.checkState() != chk:
                    self.list.blockSignals(True)
                    it.setText(txt)
                    it.setCheckState(chk)
                    self.list.blockSignals(False)


class SendFader(QWidget):
    """Fader orizzontale di una mandata: corsa da fader (0 dB a tre quarti, più risoluzione in alto), −60 = spenta,
    casella numerica accanto, doppio clic sul fader = 0 dB. Stessa interfaccia di una casella numerica
    (value / setValue / valueChanged)."""
    valueChanged = Signal(float)
    UNITY = 0.75                     # posizione dello 0 dB

    def __init__(self, db, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.sl = QSlider(Qt.Horizontal)
        self.sl.setRange(0, 1000)
        self.sl.setMinimumWidth(160)
        self.sl.setTickPosition(QSlider.TicksBelow)
        self.sl.setTickInterval(250)              # tacche: −∞, −25, −11, 0 (a tre quarti), +12 dB
        self.sl.setToolTip(tr("Doppio clic: 0 dB"))
        self.sl.installEventFilter(self)
        self.sp = QDoubleSpinBox()
        self.sp.setRange(-60.0, 12.0)
        self.sp.setDecimals(1)
        self.sp.setSingleStep(1.0)
        self.sp.setSuffix(" dB")
        self.sp.setSpecialValueText(tr("spenta"))
        self.sp.setKeyboardTracking(False)
        self.sp.setMinimumWidth(84)
        h.addWidget(self.sl, 1)
        h.addWidget(self.sp)
        self.setValue(db)
        self.sl.valueChanged.connect(self._from_slider)
        self.sp.valueChanged.connect(self._from_spin)

    @classmethod
    def to_pos(cls, db):
        db = max(-60.0, min(12.0, float(db)))
        if db >= 0.0:
            f = cls.UNITY + (1.0 - cls.UNITY) * db / 12.0
        else:
            f = cls.UNITY * ((db + 60.0) / 60.0) ** 2
        return int(round(f * 1000))

    @classmethod
    def to_db(cls, pos):
        f = max(0.0, min(1.0, pos / 1000.0))
        if f >= cls.UNITY:
            return 12.0 * (f - cls.UNITY) / (1.0 - cls.UNITY)
        return -60.0 + 60.0 * math.sqrt(f / cls.UNITY)

    def value(self):
        return self.sp.value()

    def setValue(self, db):
        db = max(-60.0, min(12.0, float(db)))
        for w in (self.sp, self.sl):
            w.blockSignals(True)
        self.sp.setValue(db)
        self.sl.setValue(self.to_pos(db))
        for w in (self.sp, self.sl):
            w.blockSignals(False)
        self.valueChanged.emit(self.sp.value())

    def _from_slider(self, pos):
        db = round(self.to_db(pos), 1)
        self.sp.blockSignals(True)
        self.sp.setValue(db)
        self.sp.blockSignals(False)
        self.valueChanged.emit(self.sp.value())

    def _from_spin(self, db):
        self.sl.blockSignals(True)
        self.sl.setValue(self.to_pos(db))
        self.sl.blockSignals(False)
        self.valueChanged.emit(db)

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.sl and e.type() == QEvent.MouseButtonDblClick:
            self.setValue(0.0)
            return True
        return False


class SendsEditor(QWidget):
    """Mandate di una cue ai bus effetti: livello (spenta = −∞) e «pre» (prima di volume e fade)."""
    changed = Signal()

    def __init__(self, cue, show_fn, parent=None, live_fn=None, live_set=None):
        super().__init__(parent)
        from .buses import buses
        self.cue = cue
        self.rows = {}
        # mandate in uso dal vivo (Cambia preset, keyframe con preset): i fader le seguono; una modifica fatta qui
        # vale anche per la cue in esecuzione (live_set)
        self.live_fn, self.live_set = live_fn, live_set
        if live_fn is not None:
            self._timer = QTimer(self)
            self._timer.setInterval(150)
            self._timer.timeout.connect(self._follow_live)
            self._timer.start()
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        bl = buses(show_fn())
        if not bl:
            lab = QLabel(tr("Nessun bus effetti: creane uno con «Bus effetti…» (es. un riverbero) e manda qui "
                            "una parte del suono di questa cue."))
            lab.setWordWrap(True)
            lab.setStyleSheet("color:#888;")
            g.addWidget(lab, 0, 0)
            return
        cur = {s.get("bus"): s for s in (cue.sends or []) if isinstance(s, dict)}
        for r, b in enumerate(bl):
            s = cur.get(b["id"], {})
            g.addWidget(QLabel(b.get("name") or tr("Bus")), r, 0)
            db = s.get("db")
            sp = SendFader(-60.0 if db is None else float(db))
            pre = QCheckBox(tr("pre"))
            pre.setToolTip(tr("Pre: la mandata non segue il volume e i fade della cue (post: li segue)"))
            pre.setChecked(bool(s.get("pre")))
            self.rows[b["id"]] = (sp, pre)
            sp.valueChanged.connect(lambda _v, b=b, sp=sp, pre=pre: self._set(b["id"], sp, pre))
            pre.toggled.connect(lambda _v, b=b, sp=sp, pre=pre: self._set(b["id"], sp, pre))
            g.addWidget(sp, r, 1)
            g.addWidget(pre, r, 2)
        g.setColumnStretch(1, 1)

    def _follow_live(self):
        live = self.live_fn() if self.live_fn is not None else None
        self.reload(live if live is not None else self.cue.sends, skip_active=True)

    def reload(self, sends=None, skip_active=False):
        """Rilegge le mandate della cue (es. dopo «Applica» di un preset) o quelle in uso dal vivo."""
        if sends is None:
            sends = self.cue.sends
        cur = {s.get("bus"): s for s in (sends or []) if isinstance(s, dict)}
        for bid, (sp, pre) in self.rows.items():
            if skip_active and (sp.sl.isSliderDown() or sp.sp.hasFocus()):
                continue
            want_db = cur.get(bid, {}).get("db")
            want = -60.0 if want_db is None else max(-60.0, min(12.0, float(want_db)))
            if skip_active and abs(sp.value() - want) < 0.05 and pre.isChecked() == bool(cur.get(bid, {}).get("pre")):
                continue
            s = cur.get(bid, {})
            db = s.get("db")
            for w_, fn in ((sp, lambda: sp.setValue(-60.0 if db is None else float(db))),
                           (pre, lambda: pre.setChecked(bool(s.get("pre"))))):
                w_.blockSignals(True)
                fn()
                w_.blockSignals(False)

    def _set(self, bid, sp, pre):
        db = None if sp.value() <= -60.0 else float(sp.value())
        sends = [s for s in (self.cue.sends or []) if isinstance(s, dict) and s.get("bus") != bid]
        if db is not None or pre.isChecked():
            sends.append({"bus": bid, "db": db, "pre": bool(pre.isChecked())})
        self.cue.sends = sends
        if self.live_set is not None and self.live_fn is not None and self.live_fn() is not None:
            import copy
            live = [x for x in (self.live_fn() or []) if isinstance(x, dict) and x.get("bus") != bid]
            if db is not None or pre.isChecked():
                live.append({"bus": bid, "db": db, "pre": bool(pre.isChecked())})
            self.live_set(copy.deepcopy(live))
        self.changed.emit()


class BusDialog(QDialog):
    """Bus effetti: elenco dei bus e, per quello scelto, nome, scheda, volume, mute, rack (gate, EQ,
    compressore, delay, riverbero), matrice d'uscita e preset."""

    def __init__(self, mw, parent=None):
        super().__init__(parent or mw)
        self.mw = mw
        self.setWindowTitle(tr("Bus effetti"))
        self.setWindowFlags(Qt.Tool | Qt.WindowCloseButtonHint | Qt.WindowTitleHint)
        self.resize(1120, 720)               # largo abbastanza per i vumeter delle sorgenti
        install_show_keys(self)
        h = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setMaximumWidth(220)
        self.list.currentRowChanged.connect(lambda _r: self._build())
        left.addWidget(self.list, 1)
        for text, fn in ((tr("+ Riverbero"), lambda: self.add(("reverb",), tr("Riverbero"))),
                         (tr("+ Delay"), lambda: self.add(("delay",), tr("Delay"))),
                         (tr("+ Bus vuoto"), lambda: self.add((), None)),
                         (tr("Rinomina…"), self.rename), (tr("Elimina…"), self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            left.addWidget(b)
        h.addLayout(left)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        h.addWidget(self.scroll, 1)
        self.meter_timer = QTimer(self)
        self.meter_timer.setInterval(100)
        self.meter_timer.timeout.connect(self._meter)
        self.meter_timer.start()
        self.reload()

    def show_data(self):
        return self.mw.show_data

    def _buses(self):
        from .buses import buses
        return buses(self.show_data())

    def _dirty(self):
        """Bus modificato: show da salvare; se il bus usa valori dal vivo (cue Controllo bus o Cambia preset) la
        modifica vale anche lì; se cambiano i bus (aggiunti, tolti, rinominati) l'ispettore si aggiorna."""
        import copy
        from .buses import mirror_edit
        self.show_data().dirty = True
        prev = getattr(self, "_prev", None) or {}
        over = self.mw.ctrl.bus_over
        fades = [f for inst in list(self.mw.ctrl.instances.values())
                 for f in (getattr(inst, "fade", None), getattr(inst, "_bus_fade", None))
                 if f is not None and hasattr(f, "bid") and not f.done]
        for b in self._buses():
            p = prev.get(b.get("id"))
            if p is not None and b.get("id") in over and p != b:
                mirror_edit(p, b, over[b["id"]])
                from .engine import _spec_diff
                edited = {q for q, _x, _y in _spec_diff(p, b)}
                for f in fades:                   # sfumatura in corso: le voci toccate qui non le sfuma più
                    if f.bid == b.get("id") and hasattr(f, "lanes"):
                        f.lanes = [ln for ln in f.lanes if ln[0] not in edited]
        self._prev = {b.get("id"): copy.deepcopy(b) for b in self._buses()}
        names = [(b.get("id"), b.get("name")) for b in self._buses()]
        if names != getattr(self, "_names", None):
            self._names = names
            ins = getattr(self.mw, "inspector", None)
            if ins is not None and getattr(ins, "cue", None) is not None:
                QTimer.singleShot(0, ins.rebuild)     # elenchi dei bus (Controllo bus, Cambia preset, mandate)

    def live(self, bid):
        """Stato in uso dal vivo del bus (o None se usa i valori salvati)."""
        return self.mw.ctrl.bus_over.get(bid)

    def reload(self, select=None):
        cur = select or self._bus_id()
        self.list.blockSignals(True)
        self.list.clear()
        for b in self._buses():
            it = QListWidgetItem(b.get("name") or tr("Bus"))
            it.setData(Qt.UserRole, b["id"])
            self.list.addItem(it)
            if b["id"] == cur:
                self.list.setCurrentItem(it)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self._build()

    def _bus_id(self):
        it = self.list.currentItem() if hasattr(self, "list") else None
        return it.data(Qt.UserRole) if it is not None else None

    def bus(self):
        from .buses import bus_by_id
        bid = self._bus_id()
        return bus_by_id(self.show_data(), bid) if bid else None

    def add(self, fx, name):
        from .buses import new_bus
        b = new_bus(self.show_data(), name, fx)
        if name:
            n = sum(1 for x in self._buses() if (x.get("name") or "").startswith(name))
            if n > 1:
                b["name"] = f"{name} {n}"
        self._dirty()
        self.reload(b["id"])

    def rename(self):
        b = self.bus()
        if b is None:
            return
        n, ok = QInputDialog.getText(self, tr("Rinomina bus"), tr("Nome:"), text=b.get("name") or "")
        if ok and n.strip():
            b["name"] = n.strip()
            self._dirty()
            self.reload(b["id"])

    def delete(self):
        b = self.bus()
        if b is None:
            return
        if QMessageBox.question(self, tr("Elimina bus"), tr("Eliminare il bus «{0}»? Le mandate delle cue verso "
                                "questo bus vengono tolte.", b.get("name") or "")) != QMessageBox.Yes:
            return
        sh = self.show_data()
        sh.settings["buses"] = [x for x in self._buses() if x is not b]
        for c in sh.every_cue():
            if getattr(c, "sends", None):
                c.sends = [s for s in c.sends if s.get("bus") != b["id"]]
        self._dirty()
        self.reload()

    def _build(self):
        from .buses import BusView
        from .inspector.presets import PresetBar
        from .ui_matrix import MatrixEditor
        import copy
        self._prev = {x.get("id"): copy.deepcopy(x) for x in self._buses()}
        self._names = [(x.get("id"), x.get("name")) for x in self._buses()]
        b = self.bus()
        w = QWidget()
        self.scroll.setWidget(w)
        self.meter_lab = None
        self.in_meter = None
        self.in_hmeter = None
        self.src_meters = {}
        if b is None:
            v = QVBoxLayout(w)
            lab = QLabel(tr("Nessun bus. Crea un bus (riverbero, delay…), poi nella scheda «Effetti» delle cue "
                            "manda al bus una parte del loro suono."))
            lab.setWordWrap(True)
            v.addWidget(lab)
            v.addStretch(1)
            return
        view = BusView(b)
        v = QVBoxLayout(w)
        pb = PresetBar("bus", self.show_data, view)
        lab = pb.findChild(QLabel)
        if lab is not None:
            lab.setText(tr("Preset del bus:"))

        def applied():
            self._dirty()
            QTimer.singleShot(0, self._build)
        pb.applied.connect(applied)
        v.addWidget(pb)
        f = QFormLayout()
        name = QLineEdit(b.get("name") or "")
        def name_done():
            new = name.text().strip() or b.get("name")
            if new == b.get("name"):
                return
            b["name"] = new
            self._dirty()
            # dopo: ricaricare adesso distruggerebbe la casella dentro il suo stesso segnale (crash)
            QTimer.singleShot(0, lambda bid=b["id"]: self.reload(bid))
        name.editingFinished.connect(name_done)
        f.addRow(tr("Nome"), name)
        eng = self.mw.ctrl.audio
        from .audio_engine import device_label, list_devices
        dev = QComboBox()
        dname, dch, _ = eng.resolve(None)
        dev.addItem(tr("Predefinita del progetto — {0} ({1} canali)", dname, dch), "")
        for d in list_devices():
            dev.addItem(tr("{0} — {1} canali", device_label(d), eng.channels_for(d)), d["id"])
        i = dev.findData(b.get("device") or "")
        if i < 0:
            dev.addItem(tr("{0} (non collegata)", (b.get("device") or "").split('|', 1)[-1]), b.get("device"))
            i = dev.count() - 1
        dev.setCurrentIndex(i)

        def dev_changed(_i):
            b["device"] = dev.currentData() or ""
            self._dirty()
            QTimer.singleShot(0, self._build)
        dev.currentIndexChanged.connect(dev_changed)
        f.addRow(tr("Scheda audio"), dev)
        lv = QHBoxLayout()
        level = QDoubleSpinBox()
        level.setRange(-60.0, 12.0)
        level.setDecimals(1)
        level.setSuffix(" dB")
        level.setSpecialValueText("-∞")
        level.setKeyboardTracking(False)
        shown = self.live(b["id"]) or b                  # valori in uso (anche quelli impostati da una cue)
        level.setValue(float(shown.get("level_db", 0.0) or 0.0))
        level.valueChanged.connect(lambda val: (b.__setitem__("level_db", float(val)), self._dirty()))
        lv.addWidget(level)
        self.level_spin = level
        mute = QCheckBox(tr("Mute"))
        mute.setChecked(bool(shown.get("mute")))
        mute.toggled.connect(lambda on: (b.__setitem__("mute", bool(on)), self._dirty()))
        lv.addWidget(mute)
        self.meter_lab = QLabel()
        self.meter_lab.setMinimumWidth(140)
        lv.addWidget(self.meter_lab)
        lv.addStretch(1)
        f.addRow(tr("Volume del ritorno"), lv)
        v.addLayout(f)
        if bid_live := (b["id"] if b["id"] in self.mw.ctrl.bus_over else None):
            ov = QHBoxLayout()
            note = QLabel(tr("⚡ Il bus sta usando valori impostati dal vivo da una cue (Controllo bus o Cambia "
                             "preset): qui vedi quelli in uso; le modifiche valgono subito e restano nello show. "
                             "«Ripristina» torna ai valori salvati."))
            note.setWordWrap(True)
            note.setStyleSheet("color:#ffaa28;")
            ov.addWidget(note, 1)
            rb = QPushButton(tr("Ripristina"))

            def restore(_=False, bid_live=bid_live):
                self.mw.ctrl.bus_over.pop(bid_live, None)
                self.mw.ctrl.sync_buses(force=True)
                QTimer.singleShot(0, self._build)
            rb.clicked.connect(restore)
            ov.addWidget(rb)
            v.addLayout(ov)
        v.addWidget(self._sources_box(b))
        box = QGroupBox(tr("Rack del bus (dall'alto in basso)"))
        bv = QVBoxLayout(box)
        bid = b["id"]

        def get_list():
            bb = self.bus_by(bid)
            return bb.get("fx") if bb is not None and isinstance(bb.get("fx"), list) else []

        def set_list(new):
            bb = self.bus_by(bid)
            if bb is not None:
                bb["fx"] = new
                self._dirty()

        def meter(fid):
            rt = eng.buses.get(bid)
            return rt.rack.meter(fid) if rt is not None else {}
        def live_list():
            lv_ = self.mw.ctrl.bus_over.get(bid)
            return lv_.get("fx") if lv_ is not None else None
        rack = RackEditor(("bus", bid), b.get("name") or tr("Bus"), get_list, set_list, dsp.BUS_FX, meter,
                          live_fn=live_list)
        rack.changed.connect(self._dirty)
        bv.addWidget(rack)
        v.addWidget(box)
        mbox = QGroupBox(tr("Uscite del bus: matrice (bus stereo L/R × uscite della scheda)"))
        mv = QVBoxLayout(mbox)
        _n, n_out, _found = eng.resolve(b.get("device") or None)
        me = MatrixEditor(view, 2, ["L", "R"], n_out)
        me.changed.connect(self._dirty)
        mv.addWidget(me, 0, Qt.AlignLeft)
        v.addWidget(mbox)
        v.addStretch(1)

    def _sources_box(self, b):
        """Sorgenti del bus: le cue che vi mandano (con il trim di ciascuna) e l'ingresso fisico con il suo trim."""
        from .buses import bus_input, sources
        from .ducking import list_input_devices
        box = QGroupBox(tr("Sorgenti del bus"))
        g = QGridLayout(box)
        sh = self.show_data()
        trims = b.get("trims")
        if not isinstance(trims, dict):
            trims = b["trims"] = {}

        def trim_spin(val, setter):
            sp = QDoubleSpinBox()
            sp.setRange(-60.0, 24.0)
            sp.setDecimals(1)
            sp.setSingleStep(0.5)
            sp.setSuffix(" dB")
            sp.setPrefix(tr("trim "))
            sp.setKeyboardTracking(False)
            sp.setValue(float(val or 0.0))
            sp.valueChanged.connect(setter)
            return sp
        r = 0
        src = sources(sh, b["id"])
        for c, snd in src:
            g.addWidget(QLabel(c.label()), r, 0)
            db = snd.get("db")
            g.addWidget(QLabel((tr("mandata {0}", "−∞" if db is None else f"{float(db):+.1f} dB"))
                               + (tr(" · pre") if snd.get("pre") else "")), r, 1)

            def set_trim(val, cid=c.id):
                if abs(val) < 1e-6:
                    trims.pop(cid, None)
                else:
                    trims[cid] = float(val)
                self._dirty()
            g.addWidget(trim_spin(trims.get(c.id, 0.0), set_trim), r, 2)
            m = HMeter()
            m.setToolTip(tr("Livello che la cue manda al bus (dopo mandata e trim); «—» = la cue non suona"))
            g.addWidget(m, r, 3)
            self.src_meters[c.id] = m
            r += 1
        if not src:
            lab = QLabel(tr("Nessuna cue manda a questo bus (scheda «Effetti» delle cue)."))
            lab.setStyleSheet("color:#888;")
            g.addWidget(lab, r, 0, 1, 4)
            r += 1
        # ingresso fisico
        inp = bus_input(b)

        def set_inp(key, val):
            d = bus_input(b)
            d[key] = val
            b["input"] = d
            self._dirty()
        on = QCheckBox(tr("Ingresso fisico"))
        on.setChecked(bool(inp["on"]))
        on.setToolTip(tr("Microfono o linea della scheda audio nel bus (es. un microfono nel riverbero). "
                         "Latenza: usa il buffer «Bassa latenza» e manda al bus solo l'effetto."))
        on.toggled.connect(lambda val: set_inp("on", bool(val)))
        g.addWidget(on, r, 0)
        dev = QComboBox()
        dev.addItem(tr("Predefinito di sistema"), "")
        for d in list_input_devices():
            dev.addItem(f"{d['name']}  [{d['hostapi']}] — {d['max_ch']} " + tr("canali"), d["id"])
        i = dev.findData(inp["device"] or "")
        if i < 0:
            dev.addItem(tr("{0} (non collegata)", (inp["device"] or "").split('|', 1)[-1]), inp["device"])
            i = dev.count() - 1
        dev.setCurrentIndex(i)
        dev.currentIndexChanged.connect(lambda _i: set_inp("device", dev.currentData() or ""))
        g.addWidget(dev, r, 1)
        g.addWidget(trim_spin(inp["trim"], lambda val: set_inp("trim", float(val))), r, 2)
        self.in_hmeter = HMeter()
        self.in_hmeter.setToolTip(tr("Livello dell'ingresso fisico nel bus (dopo il trim); «—» = ingresso spento"))
        g.addWidget(self.in_hmeter, r, 3)
        r += 1
        chs = QHBoxLayout()
        for key, label, lo in (("ch", tr("Canale (L)"), 1), ("ch2", tr("Canale R (0 = mono)"), 0)):
            chs.addWidget(QLabel(label))
            sp = QDoubleSpinBox()
            sp.setDecimals(0)
            sp.setRange(lo, 64)
            sp.setValue(float(inp[key] or lo))
            sp.valueChanged.connect(lambda val, key=key: set_inp(key, int(val)))
            chs.addWidget(sp)
        self.in_meter = QLabel()
        self.in_meter.setMinimumWidth(160)
        chs.addWidget(self.in_meter)
        chs.addStretch(1)
        g.addLayout(chs, r, 0, 1, 4)
        g.setColumnStretch(1, 1)
        return box

    def bus_by(self, bid):
        from .buses import bus_by_id
        return bus_by_id(self.show_data(), bid)

    def _meter(self):
        if self.meter_lab is None:
            return
        try:
            self.meter_lab.text()
        except RuntimeError:
            self.meter_lab = None
            return
        bid = self._bus_id()
        if not bid:
            return
        for rk in self.findChildren(RackEditor):          # testo degli effetti con i valori dal vivo
            try:
                rk._refresh_text()
            except RuntimeError:
                pass
        lvs = getattr(self, "level_spin", None)
        live = self.live(bid)
        if lvs is not None and live is not None and not lvs.hasFocus():   # volume che sfuma per una cue
            try:
                val = float(live.get("level_db", 0.0) or 0.0)
                if abs(lvs.value() - val) > 0.05:
                    lvs.blockSignals(True)
                    lvs.setValue(val)
                    lvs.blockSignals(False)
            except RuntimeError:
                self.level_spin = None
        pk = self.mw.ctrl.audio.bus_meter(bid)
        db = 20 * math.log10(max(max(pk), 1e-6))
        self.meter_lab.setText(tr("Ritorno {0}", "-∞" if db < -59 else f"{db:+.0f} dB"))
        try:                                    # vumeter delle sorgenti: cue che mandano al bus e ingresso fisico
            sm = self.mw.ctrl.audio.bus_source_meters(bid)
            for cid, m in list(getattr(self, "src_meters", {}).items()):
                m.set_peaks(sm.get(cid))
            if getattr(self, "in_hmeter", None) is not None:
                ipk, err = self.mw.ctrl.audio.bus_input_meter(bid)
                self.in_hmeter.set_peaks(ipk)          # l'eventuale errore è nella riga sotto
        except RuntimeError:                    # finestra ricostruita nel frattempo
            self.src_meters, self.in_hmeter = {}, None
        lab = getattr(self, "in_meter", None)
        if lab is not None:
            try:
                ipk, err = self.mw.ctrl.audio.bus_input_meter(bid)
                if ipk is None:
                    lab.setText("")
                elif err:
                    lab.setText("⚠ " + err)
                    lab.setStyleSheet("color:#e0b040;")
                else:                           # il livello è nel vumeter accanto al trim: qui solo gli errori
                    lab.setText("")
                    lab.setStyleSheet("")
            except RuntimeError:
                self.in_meter = None
