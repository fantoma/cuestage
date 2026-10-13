"""Barra dei keyframe: timeline con le corsie Immagine/Audio, cursore del tempo e riga di modifica."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QVBoxLayout, QWidget)

from . import keyframes as kf
from .media import fmt_time

LANE_COL = {"visual": QColor(255, 170, 40), "audio": QColor(80, 200, 255)}


class KeyframeStrip(QWidget):
    """Valori in decimi di secondo nel tempo della cue (offset 0)."""
    valueChanged = Signal(int)
    selectionChanged = Signal(object)    # keyframe (dict) o None
    edited = Signal()
    addRequested = Signal(str, float)    # corsia, tempo della cue
    markerNav = Signal(int)              # Ctrl+frecce: marcatore precedente/successivo
    markClicked = Signal(float)          # clic su un marcatore della corsia «Marcatori» (tempo della cue)

    LM, RM = 64, 10                      # margini: etichette corsie / destra
    LANE_H = 22
    MARK_H = 18                          # corsia «Marcatori» (IN/OUT, loop, fade, curva)
    TOP = 4 + MARK_H

    def __init__(self, cue, lanes, offset_fn, end_fn, parent=None, axis_fn=None):
        super().__init__(parent)
        self.axis_fn = axis_fn or (lambda: None)
        self._mark_hits = []
        self.cue = cue
        self.lanes = lanes
        self.offset_fn = offset_fn
        self.end_fn = end_fn
        self.lo, self.hi = 0, 0
        self.val = 0
        self.selected = None
        self.drag = None
        self.setFixedHeight(self.TOP + len(lanes) * self.LANE_H + 12)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setToolTip("Clic: sposta il cursore · doppio clic su una corsia: nuovo keyframe\n"
                        "Trascina un ◆ per spostarlo · tasto destro su un ◆: preset / elimina · "
                        "frecce: ±0,1 s (Maiusc ±1 s) · Canc: elimina il keyframe selezionato")

    # ---- stile QSlider
    def setRange(self, lo, hi):
        self.lo, self.hi = int(lo), max(int(lo), int(hi))
        self.val = max(self.lo, min(self.val, self.hi))
        self.update()

    def maximum(self):
        return self.hi

    def value(self):
        return self.val

    def setValue(self, v):
        v = max(self.lo, min(int(v), self.hi))
        if v != self.val:
            self.val = v
            self.update()
            self.valueChanged.emit(v)

    # ---- geometria
    def _x(self, tenths):
        w = self.width() - self.LM - self.RM
        return self.LM + (tenths - self.lo) / max(1, self.hi - self.lo) * w

    def _v(self, x):
        w = self.width() - self.LM - self.RM
        return int(round(self.lo + (x - self.LM) / max(1, w) * (self.hi - self.lo)))

    def _lane_y(self, i):
        return self.TOP + i * self.LANE_H + self.LANE_H / 2

    def _lane_at(self, y):
        i = int((y - self.TOP) // self.LANE_H)
        return self.lanes[i] if 0 <= i < len(self.lanes) else None

    def _kx(self, p):
        return self._x((self.offset_fn() + float(p.get("t", 0))) * 10)

    def _points(self, lane):
        return sorted((p for p in self.cue.preset_points if p.get("kind") == lane),
                      key=lambda p: float(p.get("t", 0)))

    def _hit(self, pos):
        lane = self._lane_at(pos.y())
        if lane is None:
            return None
        for p in self._points(lane):
            if abs(self._kx(p) - pos.x()) < 7:
                return p
        return None

    def cue_t_at(self, x):
        return round(max(0.0, self._v(x) / 10.0 - self.offset_fn()), 1)

    # ---- disegno
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(24, 25, 30))
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() * 0.8))
        p.setFont(f)
        fm = p.fontMetrics()
        off = self.offset_fn()
        end = self.end_fn()
        x0, x1 = self._x(self.lo), self._x(self.hi)
        self._paint_marks(p, x0, x1)
        for i, lane in enumerate(self.lanes):
            y = self._lane_y(i)
            col = LANE_COL[lane]
            p.setPen(col)
            p.drawText(QRectF(4, y - 9, self.LM - 8, 18), Qt.AlignVCenter | Qt.AlignLeft, kf.KIND_LABELS[lane])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(52, 54, 63))
            p.drawRoundedRect(QRectF(x0, y - 2, x1 - x0, 4), 2, 2)
            if end is not None:   # tratto della cue (IN–OUT)
                xa, xb = self._x(off * 10), self._x(end * 10)
                p.setBrush(QColor(60, 130, 200, 90))
                p.drawRect(QRectF(xa, y - 2, max(1.0, xb - xa), 4))
            pts = self._points(lane)
            xs = [self._kx(q) for q in pts]
            # dissolvenze (tra preset diversi) e tenute
            for (qa, xa), (qb, xb) in zip(zip(pts, xs), zip(pts[1:], xs[1:])):
                if qa.get("preset") != qb.get("preset"):
                    g = QLinearGradient(xa, 0, xb, 0)
                    c0, c1 = QColor(col), QColor(col)
                    c0.setAlpha(70)
                    c1.setAlpha(200)
                    g.setColorAt(0, c0)
                    g.setColorAt(1, c1)
                    p.setBrush(g)
                    p.setPen(Qt.NoPen)
                    p.drawRect(QRectF(xa, y - 5, max(1.0, xb - xa), 10))
                else:
                    p.setPen(QPen(col, 1.5))
                    p.drawLine(QPointF(xa, y), QPointF(xb, y))
            for j, (q, x) in enumerate(zip(pts, xs)):
                sel = q is self.selected
                is_base = q.get("preset") == kf.BASE
                d = 6
                poly = QPolygonF([QPointF(x, y - d), QPointF(x + d, y), QPointF(x, y + d), QPointF(x - d, y)])
                p.setPen(QPen(QColor(255, 80, 60) if sel else QColor(0, 0, 0), 1.5 if sel else 1))
                p.setBrush(QColor(30, 30, 36) if is_base else (QColor(255, 80, 60) if sel else col))
                p.drawPolygon(poly)
                name = kf.label(q.get("preset", ""))
                room = (xs[j + 1] - x - 12) if j + 1 < len(xs) else (self.width() - x - 10)
                if sel or fm.horizontalAdvance(name) <= room:
                    p.setPen(QColor(255, 120, 100) if sel else col)
                    p.drawText(QRectF(x + 8, y - 11, 160, 12), Qt.AlignLeft | Qt.AlignVCenter, name)
        # cursore
        x = self._x(self.val)
        p.setPen(QPen(QColor(255, 255, 255), 1.5))
        p.drawLine(QPointF(x, 2), QPointF(x, self.height() - 2))
        p.setBrush(QColor(255, 255, 255))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 5, self.height() - 1), QPointF(x + 5, self.height() - 1),
                                 QPointF(x, self.height() - 8)]))
        p.end()

    def _paint_marks(self, p, x0, x1):
        """Corsia «Marcatori» nel tempo della cue: IN/OUT, loop con le ripetizioni, fade, curva."""
        from .markers import COLORS, cue_marks, unrolled_regions
        self._mark_hits = []
        y0, y1 = 4, 4 + self.MARK_H - 3
        p.setPen(QColor(150, 150, 160))
        p.drawText(QRectF(4, y0, self.LM - 8, y1 - y0), Qt.AlignVCenter | Qt.AlignLeft, "Marcatori")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(36, 38, 46))
        p.drawRect(QRectF(x0, y0, x1 - x0, y1 - y0))
        ax = self.axis_fn()
        if ax is None:
            return
        upto = self.hi / 10.0
        for i, (a, b) in enumerate(unrolled_regions(ax, upto)):
            xa, xb = self._x(a * 10), self._x(b * 10)
            c = QColor(COLORS["loop"])
            c.setAlpha(70 if i == 0 else 40)
            p.fillRect(QRectF(xa, y0, max(1.0, xb - xa), y1 - y0), c)
            p.setPen(QPen(COLORS["loop"], 1.2))
            p.drawLine(QPointF(xa, y0), QPointF(xa, y1))
            p.drawLine(QPointF(xb, y0), QPointF(xb, y1))
            if i == 0:
                p.setPen(COLORS["loop"])
                p.drawText(QRectF(xa + 3, y0, max(40.0, xb - xa), y1 - y0), Qt.AlignVCenter | Qt.AlignLeft,
                           f"loop ×{ax.loops}" if ax.loops > 0 else "loop ∞")
        from .markers import Mark
        ends = [Mark(0.0, "in", "IN")] + ([Mark(ax.total, "out", "OUT")] if ax.total else [])
        for m in ends + [m for m in cue_marks(self.cue, ax) if m.kind not in ("visual", "audio")]:
            x = self._x(m.t * 10)
            col = COLORS[m.kind]
            if m.kind in ("in", "out"):
                p.setPen(QPen(col, 2))
                p.drawLine(QPointF(x, y0), QPointF(x, self.height() - 2))
                p.setPen(col)
                w = 28
                rr = QRectF(x + 2 if m.kind == "in" else x - w - 2, y0, w, y1 - y0)
                p.drawText(rr, Qt.AlignVCenter | (Qt.AlignLeft if m.kind == "in" else Qt.AlignRight), m.label)
            elif m.kind == "fade":
                p.setPen(QPen(col, 1, Qt.DashLine))
                p.drawLine(QPointF(x, y0), QPointF(x, self.height() - 2))
            elif m.kind == "curve":
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawEllipse(QPointF(x, y1 - 3), 2.2, 2.2)
            self._mark_hits.append((x, m))
        for a, b in unrolled_regions(ax, upto):
            for t, lab in ((a, "inizio loop"), (b, "fine loop")):
                self._mark_hits.append((self._x(t * 10), Mark(t, "loop", lab)))

    # ---- interazione
    def select(self, p, jump=False):
        self.selected = p
        if jump and p is not None:
            self.setValue(int(round((self.offset_fn() + float(p.get("t", 0))) * 10)))
        self.update()
        self.selectionChanged.emit(p)

    def mousePressEvent(self, e):
        pos = e.position()
        hit = self._hit(pos)
        if e.button() == Qt.RightButton and hit is not None:
            self._menu(e, hit)
            return
        if e.button() != Qt.LeftButton:
            return
        if hit is not None:
            self.drag = ("kf", hit, pos.x(), False)
            self.select(hit)
        elif pos.y() < self.TOP and self._mark_hits:
            x, m = min(self._mark_hits, key=lambda h: abs(h[0] - pos.x()))
            if abs(x - pos.x()) < 6:
                self.markClicked.emit(m.t)
            else:
                self.drag = ("scrub",)
                self.setValue(self._v(pos.x()))
        elif pos.x() >= self.LM - 4:
            self.drag = ("scrub",)
            self.setValue(self._v(pos.x()))

    def mouseMoveEvent(self, e):
        pos = e.position()
        if not self.drag:
            self.setCursor(Qt.SizeHorCursor if self._hit(pos) is not None else Qt.ArrowCursor)
            if pos.y() < self.TOP:
                from PySide6.QtWidgets import QToolTip
                near = [m for x, m in self._mark_hits if abs(x - pos.x()) < 5]
                if near:
                    QToolTip.showText(e.globalPosition().toPoint(),
                                      "\n".join(f"{fmt_time(m.t)}  {m.label}" for m in near), self)
            return
        if self.drag[0] == "scrub":
            self.setValue(self._v(pos.x()))
            return
        _, p, x0, moved = self.drag
        if not moved and abs(pos.x() - x0) < 3:
            return
        self.drag = ("kf", p, x0, True)
        p["t"] = self.cue_t_at(pos.x())
        self.update()
        self.edited.emit()

    def mouseReleaseEvent(self, e):
        if self.drag and self.drag[0] == "kf":
            self.select(self.drag[1], jump=True)
        self.drag = None

    def mouseDoubleClickEvent(self, e):
        pos = e.position()
        lane = self._lane_at(pos.y())
        if e.button() == Qt.LeftButton and lane and self._hit(pos) is None and pos.x() >= self.LM:
            self.addRequested.emit(lane, self.cue_t_at(pos.x()))

    def keyPressEvent(self, e):
        step = 10 if e.modifiers() & Qt.ShiftModifier else 1
        if e.modifiers() & Qt.ControlModifier and e.key() in (Qt.Key_Left, Qt.Key_Right):
            self.markerNav.emit(-1 if e.key() == Qt.Key_Left else 1)
        elif e.key() == Qt.Key_Left:
            self.setValue(self.val - step)
        elif e.key() == Qt.Key_Right:
            self.setValue(self.val + step)
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected is not None:
            self.delete(self.selected)
        else:
            super().keyPressEvent(e)

    def delete(self, p):
        if p in self.cue.preset_points:
            self.cue.preset_points.remove(p)
        self.select(None)
        self.edited.emit()

    def _menu(self, e, p):
        m = QMenu(self)
        lane = p.get("kind")
        sub = m.addMenu("Preset")
        for name in [kf.BASE] + sorted(self.cue.preset_dict(lane), key=str.lower):
            a = sub.addAction(kf.BASE_LABEL if name == kf.BASE else name)
            a.setCheckable(True)
            a.setChecked(p.get("preset") == name)
            a.triggered.connect(lambda _=False, name=name: (p.__setitem__("preset", name), self.select(p),
                                                          self.edited.emit()))
        m.addAction("Vai a questo keyframe").triggered.connect(lambda: self.select(p, jump=True))
        m.addAction("Elimina keyframe").triggered.connect(lambda: self.delete(p))
        m.exec(e.globalPosition().toPoint())


class KeyframeBar(QWidget):
    """Timeline dei keyframe nel TEMPO DELLA CUE (0 = avvio, loop srotolato nelle ripetizioni),
    navigazione tra i marcatori, preset in uso e riga di modifica del keyframe selezionato."""
    timeChanged = Signal(float)   # tempo del FILE corrispondente al cursore (fotogramma, forma d'onda)
    edited = Signal()
    presetInUse = Signal(dict)    # corsia -> (preset_a, preset_b, frazione) o None

    def __init__(self, cue, duration_fn=None, in_out=True, parent=None):
        super().__init__(parent)
        self.cue = cue
        from .markers import cue_axis
        self.duration_fn = duration_fn or (lambda: 0)
        self.axis_fn = lambda: cue_axis(cue, self.duration_fn())
        if not isinstance(cue.preset_points, list):
            cue.preset_points = []
        self.lanes = kf.lanes_for(cue)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        row = QHBoxLayout()
        self.time_lab = QLabel("00:00.0")
        self.time_lab.setMinimumWidth(96)
        self.strip = KeyframeStrip(cue, self.lanes, lambda: 0.0, self._out, axis_fn=self.axis_fn)
        self.strip.valueChanged.connect(self._moved)
        self.strip.selectionChanged.connect(self._selected)
        self.strip.edited.connect(self._edited)
        self.strip.addRequested.connect(self.add)
        row.addWidget(self.time_lab)
        row.addWidget(self.strip, 1)
        nav = QVBoxLayout()
        nav.setSpacing(2)
        for text, tip, step in (("◀", "Marcatore precedente (Ctrl+←)", -1), ("▶", "Marcatore successivo (Ctrl+→)", 1)):
            b = QPushButton(text)
            b.setFixedWidth(30)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, step=step: self.goto_marker(step))
            nav.addWidget(b)
        row.addLayout(nav)
        if in_out:
            b_in = QPushButton("IN")
            b_in.setToolTip("Vai all'inizio della cue")
            b_in.setFixedWidth(40)
            b_in.clicked.connect(lambda: self.goto_time(0.0))
            b_out = QPushButton("OUT")
            b_out.setToolTip("Vai alla fine della cue")
            b_out.setFixedWidth(46)
            b_out.clicked.connect(lambda: self.goto_time(self._out() or self.strip.hi / 10.0))
            row.addWidget(b_in)
            row.addWidget(b_out)
        v.addLayout(row)
        self.use_lab = QLabel()
        self.use_lab.setWordWrap(True)
        self.use_lab.setStyleSheet("color:#ccc;")
        v.addWidget(self.use_lab)
        ed = QHBoxLayout()
        self.sel_lab = QLabel()
        self.sel_lab.setStyleSheet("font-weight:bold;")
        ed.addWidget(self.sel_lab)
        self.preset_cb = QComboBox()
        self.preset_cb.setMinimumWidth(110)
        self.preset_cb.currentIndexChanged.connect(self._preset_changed)
        ed.addWidget(self.preset_cb)
        ed.addWidget(QLabel("a"))
        self.t_spin = QDoubleSpinBox()
        self.t_spin.setRange(0, 360000)
        self.t_spin.setDecimals(1)
        self.t_spin.setSingleStep(0.1)
        self.t_spin.setSuffix(" s")
        self.t_spin.setKeyboardTracking(False)
        self.t_spin.valueChanged.connect(self._t_changed)
        ed.addWidget(self.t_spin)
        self.b_del = QPushButton("Elimina")
        self.b_del.clicked.connect(lambda: self.strip.delete(self.strip.selected))
        ed.addWidget(self.b_del)
        ed.addSpacing(6)
        for lane in self.lanes:
            b = QPushButton(f"+◆ {kf.KIND_LABELS[lane]}")
            b.setToolTip(f"Nuovo keyframe {kf.KIND_LABELS[lane].lower()} al tempo del cursore "
                         "(anche: doppio clic sulla corsia)")
            b.setStyleSheet(f"color:{LANE_COL[lane].name()};")
            b.clicked.connect(lambda _=False, lane=lane: self.add(lane, self.cue_time()))
            ed.addWidget(b)
        ed.addStretch(1)
        v.addLayout(ed)
        self._last = {}       # ultimo preset usato per corsia
        self._mark_here = ""
        self._selected(None)
        self.strip.markerNav.connect(self.goto_marker)
        self.strip.markClicked.connect(self.goto_time)
        self.refresh_axis()

    # ---- asse
    def _out(self):
        ax = self.axis_fn()
        return ax.total if ax is not None else None

    def axis_length(self):
        ax = self.axis_fn()
        last = max([float(p.get("t", 0) or 0) for p in self.cue.preset_points] or [0.0])
        if ax is None:
            return max(30.0, last + 5)
        if ax.total is not None:
            return max(ax.total, last)
        extra = 2 * (ax.loop[1] - ax.loop[0]) if ax.loop else 0.0   # loop infinito: due giri in più
        return max(ax.length + extra, last + 5)

    def refresh_axis(self):
        import math
        v = self.strip.value()
        self.strip.blockSignals(True)
        self.strip.setRange(0, int(math.ceil(self.axis_length() * 10)))
        self.strip.setValue(v)
        self.strip.blockSignals(False)
        self.strip.update()
        self._label()

    # ---- marcatori
    def all_markers(self):
        """[(tempo della cue, descrizione, keyframe o None)] ordinati: IN/OUT, loop (con le ripetizioni),
        fade, curva, keyframe."""
        from .markers import KIND_LABELS as MK, cue_marks, unrolled_regions
        out = [(0.0, "IN", None)]
        ax = self.axis_fn()
        if ax is not None:
            if ax.total:
                out.append((ax.total, "OUT", None))
            out += [(m.t, m.label or MK.get(m.kind, m.kind), None) for m in cue_marks(self.cue, ax)
                    if m.kind not in ("visual", "audio", "loop")]
            for i, (a, b) in enumerate(unrolled_regions(ax, self.strip.hi / 10.0)):
                rep = f" (ripetizione {i})" if i else ""
                out += [(a, "inizio loop" + rep, None), (b, "fine loop" + rep, None)]
        for p in self.cue.preset_points:
            try:
                t = float(p.get("t", 0))
            except (TypeError, ValueError):
                continue
            out.append((t, f"keyframe {kf.KIND_LABELS.get(p.get('kind'), '')}: {kf.label(p.get('preset', ''))}", p))
        out.sort(key=lambda x: x[0])
        return out

    def _marks_at(self, t, marks=None):
        return [m for m in (marks or self.all_markers()) if abs(m[0] - t) <= 0.049]

    def goto_time(self, t):
        here = self._marks_at(t)
        kfs = [m[2] for m in here if m[2] is not None]
        self._mark_here = " · ".join(dict.fromkeys(m[1] for m in here))
        self.set_time(int(round(t * 10)))
        self.strip.select(kfs[0] if kfs else None)
        self.update_in_use()

    def goto_marker(self, step):
        """Salta al marcatore precedente (-1) o successivo (+1) rispetto al cursore."""
        cur = self.cue_time()
        eps = 0.049
        marks = self.all_markers()
        cand = [m for m in marks if (m[0] > cur + eps if step > 0 else m[0] < cur - eps)]
        if cand:
            self.goto_time(cand[0][0] if step > 0 else cand[-1][0])

    def update_in_use(self):
        """Mostra (e segnala) il preset in uso su ogni corsia al tempo del cursore."""
        T = self.cue_time()
        states = {lane: kf.names_at(self.cue, lane, T) for lane in self.lanes}
        parts = [f"<b style='color:{LANE_COL[l].name()}'>{kf.KIND_LABELS[l]}</b>: {kf.describe(states[l])}"
                 for l in self.lanes]
        head = f"In uso a {fmt_time(T)}"
        if self._mark_here:
            head = f"◆ {self._mark_here} — in uso a {fmt_time(T)}"
        self.use_lab.setText(head + ": " + " · ".join(parts))
        self.presetInUse.emit(states)

    # ---- tempo
    def cue_time(self):
        return max(0.0, self.strip.value() / 10.0)

    def file_time(self):
        from .markers import cue_to_file
        ax = self.axis_fn()
        T = self.cue_time()
        if ax is None:
            return T
        res = cue_to_file(ax, T)
        if res is None:      # oltre la fine: ultima posizione
            return ax.offset + ax.first
        return res[0]

    def set_time(self, tenths):
        if tenths == self.strip.value():
            self._label()
            self.update_in_use()
            return
        self.strip.setValue(tenths)
        self._label()

    def _label(self):
        if self.cue.type in ("audio", "video"):
            self.time_lab.setText(f"cue {fmt_time(self.cue_time())}\nfile {fmt_time(self.file_time())}")
        else:
            self.time_lab.setText(f"cue {fmt_time(self.cue_time())}")

    def _moved(self, v):
        self._label()
        self._mark_here = " · ".join(dict.fromkeys(m[1] for m in self._marks_at(v / 10.0)))
        self.update_in_use()
        self.timeChanged.emit(self.file_time())

    # ---- keyframe
    def add(self, lane, t):
        names = sorted(self.cue.preset_dict(lane), key=str.lower)
        name = self._last.get(lane)
        if name not in names:
            name = names[0] if names else kf.BASE
        p = kf.new_keyframe(self.cue, lane, t, name)
        self.strip.select(p, jump=True)
        self._edited()

    def reload(self):
        self._selected(self.strip.selected if self.strip.selected in self.cue.preset_points else None)
        self.refresh_axis()
        self.update_in_use()

    def _selected(self, p):
        ok = p is not None
        for w in (self.preset_cb, self.t_spin, self.b_del):
            w.setEnabled(ok)
        if not ok:
            n = {l: sum(1 for q in self.cue.preset_points if q.get("kind") == l) for l in self.lanes}
            self.sel_lab.setText("◆ —")
            self.sel_lab.setToolTip("Nessun keyframe selezionato · " +
                                    ", ".join(f"{kf.KIND_LABELS[l]}: {n[l]}" for l in self.lanes))
            self.sel_lab.setStyleSheet("font-weight:bold; color:#888;")
            self.preset_cb.blockSignals(True)
            self.preset_cb.clear()
            self.preset_cb.blockSignals(False)
            return
        lane = p.get("kind")
        self.sel_lab.setText(f"◆ {kf.KIND_LABELS[lane]}")
        self.sel_lab.setStyleSheet(f"font-weight:bold; color:{LANE_COL[lane].name()};")
        self.preset_cb.blockSignals(True)
        self.preset_cb.clear()
        self.preset_cb.addItem(kf.BASE_LABEL, kf.BASE)
        for n in sorted(self.cue.preset_dict(lane), key=str.lower):
            self.preset_cb.addItem(n, n)
        i = self.preset_cb.findData(p.get("preset"))
        if i < 0:
            self.preset_cb.addItem(f"{p.get('preset')} (mancante)", p.get("preset"))
            i = self.preset_cb.count() - 1
        self.preset_cb.setCurrentIndex(i)
        self.preset_cb.blockSignals(False)
        self.t_spin.blockSignals(True)
        self.t_spin.setValue(float(p.get("t", 0)))
        self.t_spin.blockSignals(False)

    def _preset_changed(self, i):
        p = self.strip.selected
        if p is not None and i >= 0:
            p["preset"] = self.preset_cb.itemData(i)
            self._last[p.get("kind")] = p["preset"]
            self._edited()

    def _t_changed(self, t):
        p = self.strip.selected
        if p is not None:
            p["t"] = round(float(t), 2)
            self.strip.select(p, jump=True)
            self._edited()

    def _edited(self):
        self.cue.preset_points.sort(key=lambda q: float(q.get("t", 0)))
        self.refresh_axis()
        self.strip.update()
        self._selected(self.strip.selected)
        self.update_in_use()
        self.edited.emit()
