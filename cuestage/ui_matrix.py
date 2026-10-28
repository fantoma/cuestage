"""Editor della matrice audio: canali della sorgente (righe) × uscite della scheda (colonne),
con il volume di ogni incrocio e una riga finale con il volume di ogni uscita."""
from .i18n import tr
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QInputDialog, QMenu, QToolTip, QWidget

from .audio_engine import effective_levels, effective_matrix

MIN_DB, MAX_DB = -60.0, 12.0


def fmt_db(db, off="·"):
    if db is None:
        return off
    if db <= MIN_DB:
        return "-∞"
    return f"{db:+.0f}" if abs(db - round(db)) < 0.05 else f"{db:+.1f}"


class MatrixEditor(QWidget):
    changed = Signal()
    CW, CH = 46, 26          # cella
    LW, HH = 58, 24          # colonna etichette, riga intestazione
    GAP = 8                  # spazio prima della riga dei volumi d'uscita

    def __init__(self, cue, n_src, labels, n_out, parent=None):
        super().__init__(parent)
        self.cue = cue
        self.n_src, self.labels, self.n_out = n_src, labels, n_out
        self.drag = None
        self.state_fn = None     # -> (matrice, volumi) da mostrare al tempo scelto, None = valori base
        self.locked_fn = None    # -> messaggio se la modifica è sospesa
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self._fix_size()

    def _locked(self):
        return self.locked_fn() if self.locked_fn else None

    def _fix_size(self):
        w = self.LW + self.n_out * self.CW + 2
        h = self.HH + (self.n_src + 1) * self.CH + self.GAP + 2
        self.setFixedSize(w, h)

    # ------------------------------------------------ dati
    def _eff(self):
        st = self.state_fn() if self.state_fn else None
        if st is not None:
            return st
        return (effective_matrix(self.cue.matrix, self.n_src, self.n_out),
                effective_levels(self.cue.out_levels, self.n_out))

    def _materialize(self):
        """Alla prima modifica la matrice predefinita diventa esplicita (e modificabile)."""
        m, lv = self._eff()
        self.cue.matrix = [list(r) for r in m]
        self.cue.out_levels = list(lv)

    def _get(self, cell):
        m, lv = self._eff()
        r, o = cell
        return lv[o] if r == "out" else m[r][o]

    def _set(self, cell, db):
        self._materialize()
        r, o = cell
        if db is not None:
            db = round(max(MIN_DB, min(MAX_DB, float(db))), 1)
        if r == "out":
            self.cue.out_levels[o] = MIN_DB if db is None else db
        else:
            self.cue.matrix[r][o] = None if (db is None or db <= MIN_DB) else db
        self.update()
        self.changed.emit()

    # ------------------------------------------------ geometria
    def _cell_rect(self, r, o):
        x = self.LW + o * self.CW
        y = self.HH + (self.n_src * self.CH + self.GAP if r == "out" else r * self.CH)
        return QRectF(x + 1, y + 1, self.CW - 2, self.CH - 2)

    def _cell_at(self, pos):
        x, y = pos.x() - self.LW, pos.y() - self.HH
        if x < 0 or y < 0:
            return None
        o = int(x // self.CW)
        if o >= self.n_out:
            return None
        if y < self.n_src * self.CH:
            return (int(y // self.CH), o)
        y2 = y - self.n_src * self.CH - self.GAP
        if 0 <= y2 < self.CH:
            return ("out", o)
        return None

    # ------------------------------------------------ disegno
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(24, 25, 30))
        m, lv = self._eff()
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() * 0.9))
        fb = QFont(f)
        fb.setBold(True)
        p.setFont(fb)
        p.setPen(QColor(170, 170, 185))
        for o in range(self.n_out):
            p.drawText(QRectF(self.LW + o * self.CW, 0, self.CW, self.HH), Qt.AlignCenter, tr("Out {0}", o + 1))
        for r in range(self.n_src):
            p.drawText(QRectF(4, self.HH + r * self.CH, self.LW - 8, self.CH),
                       Qt.AlignVCenter | Qt.AlignLeft, self.labels[r] if r < len(self.labels) else str(r + 1))
        p.setPen(QColor(255, 200, 90))
        p.drawText(QRectF(4, self.HH + self.n_src * self.CH + self.GAP, self.LW - 8, self.CH),
                   Qt.AlignVCenter | Qt.AlignLeft, tr("Vol. out"))
        p.setFont(f)
        for r in list(range(self.n_src)) + ["out"]:
            for o in range(self.n_out):
                rect = self._cell_rect(r, o)
                db = lv[o] if r == "out" else m[r][o]
                if db is None:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(38, 40, 48))
                    p.drawRoundedRect(rect, 3, 3)
                    p.setPen(QColor(90, 92, 105))
                    p.drawText(rect, Qt.AlignCenter, "·")
                    continue
                k = max(0.0, min(1.0, (db - MIN_DB) / (0 - MIN_DB)))
                if r == "out":
                    base = QColor(int(70 + 120 * k), int(55 + 90 * k), 25)
                else:
                    base = QColor(25, int(60 + 120 * k), int(45 + 50 * k))
                if db > 0:
                    base = QColor(190, 120, 30) if r != "out" else QColor(210, 120, 30)
                p.setPen(Qt.NoPen)
                p.setBrush(base)
                p.drawRoundedRect(rect, 3, 3)
                p.setPen(QColor(255, 255, 255))
                p.drawText(rect, Qt.AlignCenter, fmt_db(db))
        p.setPen(QPen(QColor(70, 72, 84), 1))
        y = self.HH + self.n_src * self.CH + self.GAP / 2
        p.drawLine(int(self.LW), int(y), int(self.width() - 2), int(y))
        if self._locked():
            p.fillRect(self.rect(), QColor(80, 160, 255, 28))
            p.setPen(QPen(QColor(80, 200, 255), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.rect().adjusted(1, 1, -1, -1))
        p.end()

    # ------------------------------------------------ interazione
    def _tip(self, e, cell):
        r, o = cell
        db = self._get(cell)
        what = tr("Volume uscita {0}", o + 1) if r == "out" else \
            tr("{0} → Out {1}", self.labels[r] if r < len(self.labels) else r + 1, o + 1)
        QToolTip.showText(e.globalPosition().toPoint(), tr("{0}: {1} dB", what, fmt_db(db, tr('spento'))), self)

    def mousePressEvent(self, e):
        cell = self._cell_at(e.position())
        if cell is None:
            return
        if self._locked():
            QToolTip.showText(e.globalPosition().toPoint(), self._locked(), self)
            return
        if e.button() == Qt.RightButton:
            self._menu(e, cell)
            return
        if e.button() != Qt.LeftButton:
            return
        db = self._get(cell)
        self.drag = (cell, e.position().y(), db, False)

    def mouseMoveEvent(self, e):
        cell = self._cell_at(e.position())
        if not self.drag:
            if cell is not None:
                self._tip(e, cell)
            return
        c0, y0, db0, moved = self.drag
        dy = y0 - e.position().y()
        if not moved and abs(dy) < 3:
            return
        if db0 is None:          # trascinando una cella spenta la si accende da -20 dB
            db0 = -20.0
        self.drag = (c0, y0, db0, True)
        self._set(c0, db0 + dy * 0.25)
        self._tip(e, c0)

    def mouseReleaseEvent(self, e):
        if self.drag and not self.drag[3] and e.button() == Qt.LeftButton:
            cell, _, db, _ = self.drag
            if cell[0] == "out":
                self._set(cell, 0.0 if db is not None and db <= MIN_DB else db)
            else:
                self._set(cell, None if db is not None else 0.0)   # clic = acceso/spento
        self.drag = None

    def mouseDoubleClickEvent(self, e):
        cell = self._cell_at(e.position())
        if cell is not None and not self._locked():
            self._ask(cell)

    def wheelEvent(self, e):
        cell = self._cell_at(e.position())
        if cell is None or self._locked():
            return super().wheelEvent(e)
        db = self._get(cell)
        if db is None:
            return
        step = 0.1 if e.modifiers() & Qt.ControlModifier else 1.0
        self._set(cell, db + (step if e.angleDelta().y() > 0 else -step))
        self._tip(e, cell)

    def _ask(self, cell):
        db = self._get(cell)
        v, ok = QInputDialog.getDouble(self, tr("Volume"), tr("dB (-60 = spento):"),
                                       MIN_DB if db is None else db, MIN_DB, MAX_DB, 1)
        if ok:
            self._set(cell, None if v <= MIN_DB and cell[0] != "out" else v)

    def _menu(self, e, cell):
        m = QMenu(self)
        for db in (6.0, 0.0, -3.0, -6.0, -12.0, -20.0):
            a = m.addAction(f"{db:+.0f} dB")
            a.triggered.connect(lambda _=False, db=db: self._set(cell, db))
        m.addSeparator()
        if cell[0] == "out":
            m.addAction(tr("Muto (-∞)")).triggered.connect(lambda: self._set(cell, MIN_DB))
        else:
            m.addAction(tr("Spento")).triggered.connect(lambda: self._set(cell, None))
            r = cell[0]
            m.addAction(tr("Solo questa uscita per questo canale")).triggered.connect(lambda: self._solo(r, cell[1]))
        m.addAction(tr("Imposta…")).triggered.connect(lambda: self._ask(cell))
        m.exec(e.globalPosition().toPoint())

    def _solo(self, r, o):
        self._materialize()
        db = self.cue.matrix[r][o]
        self.cue.matrix[r] = [None] * self.n_out
        self.cue.matrix[r][o] = 0.0 if db is None else db
        self.update()
        self.changed.emit()
