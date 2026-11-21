"""Menu Normalizza: analisi LUFS delle cue con correzione del volume, e misuratore LUFS in tempo reale."""
import collections
import math
import threading

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import loudness
from .i18n import tr

DEFAULT_TARGET = -14.0


def _fmt(v, unit="", sign=False):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return (f"{v:+.1f}" if sign else f"{v:.1f}") + unit


def audio_cues(show):
    """Cue con audio (audio e video, con un file), nell'ordine delle liste: [(lista, cue)]."""
    out = []
    for lst in show.lists:
        for c in lst.cues:
            if c.type in ("audio", "video") and c.file and not getattr(c, "is_link", False):
                out.append((lst, c))
    return out


def target_setting(show):
    try:
        return float(show.settings.get("norm_target", DEFAULT_TARGET))
    except (TypeError, ValueError):
        return DEFAULT_TARGET


# ============================================================================ normalizzazione

TRIM_MIN, TRIM_MAX = -24.0, 24.0


class NormalizeDialog(QDialog):
    """Elenco delle cue con audio: si scelgono, si analizzano (LUFS integrati del tratto IN–OUT) e la correzione
    va nell'OUT TRIM di ogni cue (il Volume della cue resta com'è: una cue abbassata apposta resta sotto le altre).
    Prima di applicare, il nuovo OUT TRIM di ogni cue si ritocca con la rotella del mouse."""
    COLS = 10
    C_NEW = 9

    def __init__(self, mw, parent=None):
        super().__init__(parent or mw)
        self.mw = mw
        self.setWindowTitle(tr("Normalizza il volume (LUFS)"))
        self.setWindowFlags(Qt.Tool | Qt.WindowCloseButtonHint | Qt.WindowTitleHint)
        self.resize(1040, 560)
        self.results = {}          # id cue -> {lufs, peak} o {error}
        self.adjust = {}           # id cue -> ritocco a mano (dB) del nuovo OUT TRIM (rotella)
        self._queue = []
        self._worker = None
        self._cancel = False
        self._prog = {}
        self._total = 0
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Riferimento")))
        self.target = QDoubleSpinBox()
        self.target.setRange(-40.0, -5.0)
        self.target.setSingleStep(0.5)
        self.target.setDecimals(1)
        self.target.setSuffix(" LUFS")
        self.target.setValue(target_setting(mw.show_data))
        self.target.setToolTip(tr("−14 LUFS: musica in streaming · −16: podcast · −23: broadcast (EBU R128)"))
        self.target.valueChanged.connect(self._target_changed)
        top.addWidget(self.target)
        top.addStretch(1)
        v.addLayout(top)
        info = QLabel(tr("Si misura il tratto tra IN e OUT di ogni cue (senza curva di volume e fade). La correzione "
                         "va nell'OUT TRIM della cue (manopola nella scheda Audio, accanto alla matrice): il Volume "
                         "della cue non cambia, quindi una cue abbassata apposta di 3 dB resta 3 dB sotto le altre. "
                         "Rotella del mouse sul «Nuovo OUT TRIM»: ±0,5 dB (Maiusc ±0,1) · doppio clic: torna al "
                         "valore calcolato."))
        info.setWordWrap(True)
        info.setStyleSheet("color:#888;")
        v.addWidget(info)
        self.table = QTableWidget(0, self.COLS)
        self.table.setHorizontalHeaderLabels(["", tr("Cue"), tr("Nome"), tr("Lista"), "LUFS", tr("Picco"), tr("Volume"),
                                              tr("OUT TRIM"), tr("Correzione"), tr("Nuovo OUT TRIM")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemChanged.connect(lambda _it: self._update_buttons())
        self.table.viewport().installEventFilter(self)
        v.addWidget(self.table, 1)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        bt = QHBoxLayout()
        for text, fn in ((tr("Tutte"), lambda: self._check_all(True)), (tr("Nessuna"), lambda: self._check_all(False))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bt.addWidget(b)
        bt.addStretch(1)
        self.analyze_btn = QPushButton(tr("Analizza le selezionate"))
        self.analyze_btn.clicked.connect(self.analyze)
        bt.addWidget(self.analyze_btn)
        self.apply_btn = QPushButton(tr("Applica l'OUT TRIM alle selezionate"))
        self.apply_btn.clicked.connect(self.apply)
        bt.addWidget(self.apply_btn)
        close = QPushButton(tr("Chiudi"))
        close.clicked.connect(self.close)
        bt.addWidget(close)
        v.addLayout(bt)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self._poll)
        self.reload()

    # ---- elenco
    def reload(self):
        self.cues = audio_cues(self.mw.show_data)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.cues))
        for r, (lst, c) in enumerate(self.cues):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, c.id)
            self.table.setItem(r, 0, it)
            for col, text in ((1, c.number or ""), (2, c.name or c.label()), (3, lst.name)):
                self.table.setItem(r, col, QTableWidgetItem(text))
            for col in range(4, self.COLS):
                self.table.setItem(r, col, QTableWidgetItem(""))
            self._fill_row(r)
        self.table.blockSignals(False)
        self._update_buttons()

    def _check_all(self, on):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.table.blockSignals(False)
        self._update_buttons()

    def checked(self):
        return [self.cues[r][1] for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.Checked]

    def set_checked(self, cue_ids):
        ids = set(cue_ids)
        self.table.blockSignals(True)
        for r, (_l, c) in enumerate(self.cues):
            self.table.item(r, 0).setCheckState(Qt.Checked if c.id in ids else Qt.Unchecked)
        self.table.blockSignals(False)
        self._update_buttons()

    # ---- valori
    def correction(self, cue):
        """Correzione calcolata (dB) per arrivare al riferimento, None se non analizzata o non misurabile."""
        res = self.results.get(cue.id) or {}
        return loudness.correction(res.get("lufs"), self.target.value())

    def new_trim(self, cue):
        """Nuovo OUT TRIM della cue: correzione calcolata più l'eventuale ritocco con la rotella."""
        corr = self.correction(cue)
        if corr is None:
            return None
        return round(max(TRIM_MIN, min(TRIM_MAX, corr + self.adjust.get(cue.id, 0.0))), 1)

    def pending(self, cue):
        """Quanto cambierebbe l'OUT TRIM applicando (dB), None se non analizzata."""
        nt = self.new_trim(cue)
        if nt is None:
            return None
        return round(nt - float(getattr(cue, "out_trim_db", 0.0) or 0.0), 1)

    def nudge(self, cue, delta):
        """Ritocca il nuovo OUT TRIM di una cue (rotella)."""
        if self.correction(cue) is None:
            return
        self.adjust[cue.id] = round(self.adjust.get(cue.id, 0.0) + delta, 1)
        self._refresh()

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.table.viewport() and e.type() in (QEvent.Wheel, QEvent.MouseButtonDblClick):
            pos = e.position().toPoint()
            r, col = self.table.rowAt(pos.y()), self.table.columnAt(pos.x())
            if col == self.C_NEW and 0 <= r < len(self.cues):
                c = self.cues[r][1]
                if e.type() == QEvent.MouseButtonDblClick:
                    self.adjust.pop(c.id, None)
                    self._refresh()
                    return True
                d = e.angleDelta().y() or e.angleDelta().x()
                if d:
                    step = 0.1 if e.modifiers() & Qt.ShiftModifier else 0.5
                    self.nudge(c, step if d > 0 else -step)
                return True
        return super().eventFilter(obj, e)

    def _fill_row(self, r):
        c = self.cues[r][1]
        res = self.results.get(c.id)
        lvl = float(c.level_db)
        trim = float(getattr(c, "out_trim_db", 0.0) or 0.0)
        cells = {6: _fmt(lvl, " dB", sign=True), 7: _fmt(trim, " dB", sign=True)}
        tip = tr("Rotella: ±0,5 dB (Maiusc ±0,1) · doppio clic: valore calcolato")
        color = None
        if c.id in self._prog and res is None:
            cells[4] = tr("analisi… {0} %", int(self._prog[c.id] * 100))
        elif res is None:
            cells[4] = ""
        elif "error" in res:
            cells[4] = "⚠ " + res["error"]
            color = "#e0b040"
        else:
            cells[4] = _fmt(res.get("lufs"), " LUFS")
            cells[5] = _fmt(res.get("peak"), " dBFS")
            corr = self.correction(c)
            if corr is None:
                cells[8] = tr("silenzio")
            else:
                cells[8] = _fmt(corr, " dB", sign=True)
                nt = self.new_trim(c)
                adj = self.adjust.get(c.id, 0.0)
                cells[9] = _fmt(nt, " dB", sign=True) + (tr(" (ritocco {0})", _fmt(adj, "", sign=True)) if adj else "")
                peak_after = (res.get("peak") or -120.0) + lvl + nt
                if corr + adj > TRIM_MAX:
                    cells[9] += " ⚠"
                    tip = tr("Oltre il massimo di {0:+.0f} dB: la cue resterà sotto il riferimento", TRIM_MAX)
                    color = "#e0b040"
                elif peak_after > -1.0:
                    cells[9] += " ⚠"
                    tip = tr("Picco previsto {0:+.1f} dBFS: rischio di distorsione (con il master a 0 dB)", peak_after)
                    color = "#e05050"
                elif abs(nt - trim) < 0.05:
                    color = "#50c070"                    # già applicato
        for col in range(4, self.COLS):
            it = self.table.item(r, col)
            it.setText(cells.get(col, ""))
            it.setToolTip(tip if col == self.C_NEW else "")
            it.setForeground(QBrush(QColor(color)) if color and col in (8, 9) else QBrush())

    def _refresh(self):
        for r in range(self.table.rowCount()):
            self._fill_row(r)
        done = [c for _l, c in self.cues if c.id in self.results and "lufs" in self.results[c.id]]
        if done:
            ups = sum(1 for c in done if (self.correction(c) or 0) > 0.05)
            downs = sum(1 for c in done if (self.correction(c) or 0) < -0.05)
            self.summary.setText(tr("Analizzate {0} cue · da alzare: {1} · da abbassare: {2} · riferimento {3:.1f} "
                                    "LUFS", len(done), ups, downs, self.target.value()))
        self._update_buttons()

    def _update_buttons(self):
        sel = self.checked()
        busy = self._worker is not None and self._worker.is_alive()
        self.analyze_btn.setEnabled(bool(sel) and not busy)
        self.apply_btn.setEnabled(not busy and any(self.pending(c) not in (None, 0.0) for c in sel))

    def _target_changed(self, val):
        self.mw.show_data.settings["norm_target"] = float(val)
        self.mw.show_data.dirty = True
        self._refresh()

    # ---- analisi (in un thread: l'interfaccia resta libera)
    def analyze(self):
        self._queue = [c for c in self.checked()]
        if not self._queue:
            return
        for c in self._queue:
            self.results.pop(c.id, None)
        self._prog = {c.id: 0.0 for c in self._queue}
        self._total = len(self._queue)
        self._cancel = False
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._worker = threading.Thread(target=self._run, args=(list(self._queue),), daemon=True,
                                        name="loudness")
        self._worker.start()
        self.timer.start()
        self._update_buttons()

    def _run(self, cues):
        for c in cues:
            if self._cancel:
                return

            def prog(f, cid=c.id):
                self._prog[cid] = f
            res = loudness.measure_cue(c, prog, lambda: self._cancel)
            if res is None:
                return
            self.results[c.id] = res
            self._prog.pop(c.id, None)

    def _poll(self):
        busy = self._worker is not None and self._worker.is_alive()
        n = self._total or 1
        partial = sum(self._prog.values())
        doneq = sum(1 for c in self._queue if c.id in self.results)
        self.progress.setValue(int(1000 * (doneq + partial) / n))
        self._refresh()
        if not busy:
            self.timer.stop()
            self.progress.setVisible(False)
            self._prog = {}
            self._refresh()

    def wait(self, timeout=60.0):
        """Per le prove: aspetta la fine dell'analisi."""
        if self._worker is not None:
            self._worker.join(timeout)
        self._poll()

    # ---- applicazione
    def apply(self):
        n, extra = 0, []
        for c in self.checked():
            nt = self.new_trim(c)
            if nt is None or abs(nt - float(getattr(c, "out_trim_db", 0.0) or 0.0)) < 0.05:
                continue
            c.out_trim_db = nt
            n += 1
            extra.append(f"{c.number or c.label()} {nt:+.1f}")
        if not n:
            self.mw.status(tr("Normalizza: niente da cambiare (OUT TRIM già applicati)"))
            return
        sh = self.mw.show_data
        sh.dirty = True
        try:
            self.mw.model.refresh()
            if self.mw.inspector.cue is not None:
                self.mw.inspector.refresh_fields()
        except Exception:
            pass
        self.mw.status(tr("OUT TRIM normalizzato su {0} cue: {1}", n, ", ".join(extra[:12]) +
                          (" …" if len(extra) > 12 else "")))
        self._refresh()

    def closeEvent(self, e):
        self._cancel = True
        super().closeEvent(e)


# ============================================================================ misuratore in tempo reale

class LoudnessGraph(QWidget):
    """Grafico stile Youlean: loudness a breve termine (area) e momentanea (linea) nel tempo, con il riferimento."""
    SPAN = 120.0             # secondi visibili
    LO, HI = -50.0, 0.0

    def __init__(self, meter, target_fn, parent=None):
        super().__init__(parent)
        self.meter, self.target_fn = meter, target_fn
        self.setMinimumSize(360, 180)

    def _y(self, v, r):
        v = max(self.LO, min(self.HI, v))
        return r.bottom() - (v - self.LO) / (self.HI - self.LO) * r.height()

    def _color(self, v, target):
        if v > target + 1.0:
            return QColor("#e0b040") if v < target + 4.0 else QColor("#e05050")
        if v < target - 1.0:
            return QColor("#3d8bd8")
        return QColor("#50c070")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(34, 6, -6, -18)
        p.fillRect(self.rect(), QColor("#121419"))
        p.fillRect(r, QColor("#16181d"))
        small = QFont()
        small.setPointSizeF(max(6.5, small.pointSizeF() * 0.8))
        p.setFont(small)
        for db in range(int(self.LO), int(self.HI) + 1, 5):
            y = self._y(db, r)
            p.setPen(QPen(QColor("#2a2d35"), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor("#888"))
            p.drawText(QRectF(0, y - 7, 30, 14), Qt.AlignRight | Qt.AlignVCenter, str(db))
        target = self.target_fn()
        yt = self._y(target, r)
        p.setPen(QPen(QColor("#50c070"), 1, Qt.DashLine))
        p.drawLine(QPointF(r.left(), yt), QPointF(r.right(), yt))
        st, mv = self.meter.st_values, self.meter.m_values
        n = int(self.SPAN / loudness.HOP_S)
        st, mv = st[-n:], mv[-n:]
        if st:
            dx = r.width() / n
            x0 = r.left() if len(st) < n else r.right() - len(st) * dx     # si riempie da sinistra, poi scorre
            for i, v in enumerate(st):                       # area della breve termine, colorata come Youlean
                if not math.isfinite(v) or v <= self.LO:
                    continue
                x = x0 + i * dx
                y = self._y(v, r)
                c = self._color(v, target)
                c.setAlpha(150)
                p.fillRect(QRectF(x, y, max(1.0, dx + 0.5), r.bottom() - y), c)
            path = QPainterPath()
            started = False
            for i, v in enumerate(mv):                       # linea della momentanea
                if not math.isfinite(v) or v <= self.LO:
                    started = False
                    continue
                pt = QPointF(x0 + i * dx, self._y(v, r))
                if started:
                    path.lineTo(pt)
                else:
                    path.moveTo(pt)
                    started = True
            p.setPen(QPen(QColor(230, 230, 230, 170), 1.2))
            p.drawPath(path)
        p.setPen(QColor("#888"))
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 14), Qt.AlignLeft,
                   tr("−{0:.0f} s", self.SPAN) if len(self.meter.st_values) >= n else "0 s")
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 14), Qt.AlignRight,
                   tr("adesso") if len(self.meter.st_values) >= n else tr("{0:.0f} s", self.SPAN))


class LoudnessBar(QWidget):
    """Barra verticale di un valore LUFS (momentanea o breve termine) con il riferimento."""

    def __init__(self, label, target_fn, parent=None):
        super().__init__(parent)
        self.label, self.target_fn = label, target_fn
        self.value = float("-inf")
        self.setFixedWidth(46)
        self.setMinimumHeight(180)

    def set_value(self, v):
        if v != self.value:
            self.value = v
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        r = QRectF(10, 6, self.width() - 20, self.height() - 40)
        p.fillRect(r, QColor("#16181d"))
        lo, hi = LoudnessGraph.LO, LoudnessGraph.HI
        t = self.target_fn()
        if math.isfinite(self.value) and self.value > lo:
            f = (min(hi, self.value) - lo) / (hi - lo)
            c = QColor("#50c070") if abs(self.value - t) <= 1.0 else (
                QColor("#3d8bd8") if self.value < t else QColor("#e0b040"))
            p.fillRect(QRectF(r.left(), r.bottom() - f * r.height(), r.width(), f * r.height()), c)
        yt = r.bottom() - (t - lo) / (hi - lo) * r.height()
        p.setPen(QPen(QColor("#ffffff"), 1, Qt.DashLine))
        p.drawLine(QPointF(r.left() - 4, yt), QPointF(r.right() + 4, yt))
        p.setPen(QColor("#ccc"))
        small = QFont()
        small.setPointSizeF(max(6.5, small.pointSizeF() * 0.8))
        p.setFont(small)
        p.drawText(QRectF(0, r.bottom() + 2, self.width(), 14), Qt.AlignHCenter, _fmt(self.value))
        p.drawText(QRectF(0, r.bottom() + 16, self.width(), 14), Qt.AlignHCenter, self.label)


class LoudnessMeterWindow(QDialog):
    """Misuratore LUFS in tempo reale della cue scelta (stile Youlean): momentanea, breve termine, integrata,
    LRA, picco e grafico nel tempo. Si misura il suono della cue dopo volume, curva e fade (prima del master)."""

    def __init__(self, mw, parent=None):
        super().__init__(parent or mw)
        self.mw = mw
        self.setWindowTitle(tr("Misuratore LUFS"))
        self.setWindowFlags(Qt.Tool | Qt.WindowCloseButtonHint | Qt.WindowTitleHint)
        self.resize(820, 420)
        self.meter = loudness.LiveMeter()
        self._voice = None
        self._tap = None
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Cue")))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(240)
        self.combo.currentIndexChanged.connect(lambda _i: self._cue_changed())
        top.addWidget(self.combo, 1)
        self.follow_play = QCheckBox(tr("Segui la cue che suona"))
        self.follow_play.setChecked(True)
        self.follow_play.setToolTip(tr("Se la cue scelta è ferma e un'altra cue con audio sta suonando, il misuratore "
                                       "passa da solo a quella (l'ultima partita)"))
        top.addWidget(self.follow_play)
        self.follow = QCheckBox(tr("Segui la cue selezionata"))
        self.follow.setToolTip(tr("Il misuratore passa alla cue selezionata nella lista"))
        top.addWidget(self.follow)
        top.addWidget(QLabel(tr("Riferimento")))
        self.target = QDoubleSpinBox()
        self.target.setRange(-40.0, -5.0)
        self.target.setSingleStep(0.5)
        self.target.setDecimals(1)
        self.target.setSuffix(" LUFS")
        self.target.setValue(target_setting(mw.show_data))
        self.target.valueChanged.connect(lambda _v: self.update_view())
        top.addWidget(self.target)
        self.auto_reset = QCheckBox(tr("Azzera a ogni avvio"))
        self.auto_reset.setChecked(True)
        top.addWidget(self.auto_reset)
        rb = QPushButton(tr("Azzera"))
        rb.clicked.connect(self.reset)
        top.addWidget(rb)
        v.addLayout(top)
        mid = QHBoxLayout()
        self.graph = LoudnessGraph(self.meter, self.target.value)
        mid.addWidget(self.graph, 1)
        self.bar_m = LoudnessBar("M", self.target.value)
        self.bar_s = LoudnessBar("S", self.target.value)
        mid.addWidget(self.bar_m)
        mid.addWidget(self.bar_s)
        v.addLayout(mid, 1)
        g = QGridLayout()
        self.vals = {}
        big = QFont()
        big.setPointSizeF(big.pointSizeF() * 1.6)
        big.setBold(True)
        for i, (key, label) in enumerate((("int", tr("Integrata")), ("st", tr("Breve termine (3 s)")),
                                          ("m", tr("Momentanea (400 ms)")), ("lra", tr("LRA")),
                                          ("peak", tr("Picco")), ("delta", tr("Δ dal riferimento")))):
            lab = QLabel(label)
            lab.setStyleSheet("color:#999;")
            val = QLabel("—")
            val.setFont(big)
            g.addWidget(lab, 0, i)
            g.addWidget(val, 1, i)
            self.vals[key] = val
        v.addLayout(g)
        self.state = QLabel()
        self.state.setStyleSheet("color:#888;")
        v.addWidget(self.state)
        self.fill_cues()
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def fill_cues(self, select=None):
        cur = select or self.combo.currentData()
        self.combo.blockSignals(True)
        self.combo.clear()
        for lst, c in audio_cues(self.mw.show_data):
            self.combo.addItem(f"{c.label()}   [{lst.name}]", c.id)
        i = self.combo.findData(cur) if cur else -1
        if i < 0:
            sel = self.mw._current_cue() if hasattr(self.mw, "_current_cue") else None
            i = self.combo.findData(sel.id) if sel is not None else 0
        self.combo.setCurrentIndex(max(0, i))
        self.combo.blockSignals(False)
        self._cue_changed()

    def cue(self):
        cid = self.combo.currentData()
        return next((c for _l, c in audio_cues(self.mw.show_data) if c.id == cid), None)

    def select_cue(self, cue):
        i = self.combo.findData(cue.id)
        if i < 0:
            self.fill_cues(cue.id)
        else:
            self.combo.setCurrentIndex(i)

    def _detach(self):
        if self._voice is not None:
            try:
                self._voice.loud_tap = None
            except Exception:
                pass
        self._voice = self._tap = None

    def _cue_changed(self):
        self._detach()
        self.reset()

    def reset(self):
        self.meter.reset()
        self.update_view()

    def playing_cue(self):
        """L'ultima cue con audio partita che sta ancora suonando nel motore di Cuevivo, o None."""
        ids = {c.id for _l, c in audio_cues(self.mw.show_data)}
        last = None
        for cid, inst in list(self.mw.ctrl.instances.items()):
            if cid in ids and getattr(inst, "voice", None) is not None and not getattr(inst, "done", False):
                last = cid
        return last

    def tick(self):
        if self.follow_play.isChecked() and not self.follow.isChecked():
            cur = self.combo.currentData()
            inst = self.mw.ctrl.instances.get(cur) if cur else None
            if inst is None or getattr(inst, "voice", None) is None:
                pid = self.playing_cue()
                if pid is not None and pid != cur and self.combo.findData(pid) >= 0:
                    self.combo.setCurrentIndex(self.combo.findData(pid))
        if self.follow.isChecked():
            sel = self.mw._current_cue() if hasattr(self.mw, "_current_cue") else None
            if sel is not None and sel.id != self.combo.currentData() and self.combo.findData(sel.id) >= 0:
                self.combo.setCurrentIndex(self.combo.findData(sel.id))
        cue = self.cue()
        inst = self.mw.ctrl.instances.get(cue.id) if cue is not None else None
        voice = getattr(inst, "voice", None) if inst is not None else None
        if voice is not self._voice:
            self._detach()
            if voice is not None:
                if self.auto_reset.isChecked():
                    self.meter.reset()
                self._tap = collections.deque(maxlen=4000)
                voice.loud_tap = self._tap
                self._voice = voice
        tap = self._tap
        while tap:
            try:
                self.meter.feed(tap.popleft())
            except IndexError:
                break
        if cue is None:
            self.state.setText(tr("Nessuna cue con audio nello show"))
        elif inst is None:
            self.state.setText(tr("«{0}» è ferma: falla partire per misurarla", cue.label()))
        elif voice is None:
            self.state.setText(tr("«{0}»: l'audio non passa dal motore di Cuevivo (video con il lettore di "
                                  "sistema): non si può misurare", cue.label()))
        else:
            self.state.setText(tr("Misura di «{0}»: {1:.0f} s · suono della cue dopo volume, curva e fade (prima "
                                  "del master)", cue.label(), self.meter.seconds()))
        self.update_view()

    def update_view(self):
        m = self.meter
        t = self.target.value()
        integ = m.integrated()
        vals = {"int": _fmt(integ, " LUFS"), "st": _fmt(m.short_term(), " LUFS"), "m": _fmt(m.momentary(), " LUFS"),
                "lra": _fmt(m.lra(), " LU"), "peak": _fmt(m.peak_db(), " dBFS"),
                "delta": _fmt(integ - t, " LU", sign=True) if math.isfinite(integ) else "—"}
        for k, txt in vals.items():
            self.vals[k].setText(txt)
        if math.isfinite(integ):
            d = integ - t
            col = "#50c070" if abs(d) <= 1.0 else ("#3d8bd8" if d < 0 else "#e0b040")
            self.vals["delta"].setStyleSheet(f"color:{col};")
            self.vals["int"].setStyleSheet(f"color:{col};")
        self.bar_m.set_value(m.momentary())
        self.bar_s.set_value(m.short_term())
        self.graph.update()

    def closeEvent(self, e):
        self.timer.stop()
        self._detach()
        super().closeEvent(e)
