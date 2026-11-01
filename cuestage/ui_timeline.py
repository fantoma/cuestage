"""Editor grafico: forma d'onda, punti di inizio/fine, loop e curva di volume."""
from .i18n import tr
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QBrush, QAction
from PySide6.QtWidgets import QMenu, QWidget, QToolTip

from .markers import COLORS as MCOL, cue_axis, cue_to_file, diamond
from .media import PEAK_RATE, fmt_time
from .model import MAX_DB, MIN_DB

C_BG = QColor(22, 23, 28)
C_WAVE = QColor(80, 160, 230)
C_START = QColor(60, 210, 110)
C_END = QColor(235, 70, 70)
C_LOOP = QColor(240, 190, 40)
C_CURVE = QColor(255, 140, 220)
C_HEAD = QColor(255, 255, 255)
C_ENV = QColor(255, 165, 40)          # inviluppo del video: fade e opacità

HANDLE_H = 16


class TimelineEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cue = None
        self.duration = 0.0
        self.peaks = None
        self.v0, self.v1 = 0.0, 1.0
        self.drag = None          # ("marker", nome) | ("point", indice) | ("pan", x0, v0, v1)
        self.playhead = None
        self.cursor_t = None          # cursore della timeline dei keyframe (tempo del file)
        self.thumbs = None            # (QImage striscia, n) miniature del video
        self.setMinimumHeight(102)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)

    # ------------------------------------------------------------ dati
    def set_cue(self, cue, duration, peaks):
        same = self.cue is cue
        self.cue = cue
        self.duration = max(0.0, duration or 0.0)
        self.peaks = peaks
        if not same or self.v1 <= self.v0 or self.v1 > max(self.duration, 1.0) * 1.05:
            self.zoom_all()
        self.update()

    def set_media(self, duration, peaks):
        old = self.duration
        self.duration = max(0.0, duration or 0.0)
        self.peaks = peaks
        if old <= 0 and self.duration > 0:
            self.zoom_all()
        self.update()

    def set_thumbs(self, data):
        """Miniature del video (png, n) da mostrare sotto la forma d'onda; None = nessuna."""
        if data is getattr(self, "_thumb_src", None):
            return
        self._thumb_src = data
        if not data:
            self.thumbs = None
        else:
            from PySide6.QtGui import QImage
            img = QImage.fromData(data[0])
            self.thumbs = (img, int(data[1])) if not img.isNull() else None
        self.update()

    def _draw_thumbs(self, p, r):
        img, n = self.thumbs
        tw = img.width() / max(1, n)
        th = img.height()
        if tw <= 0 or th <= 0 or self.duration <= 0:
            return
        w = r.height() * tw / th                     # miniature affiancate con le loro proporzioni
        p.save()
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setClipRect(r)
        x = 0.0
        while x < self.width():
            t = self.x2t(x + w / 2)
            i = max(0, min(n - 1, int(t / self.duration * n)))
            p.drawImage(QRectF(x, r.top(), w, r.height()), img, QRectF(i * tw, 0, tw, th))
            x += w
        p.fillRect(r, QColor(20, 21, 28, 140))      # più scure: la forma d'onda resta leggibile
        p.restore()

    def set_cursor(self, t):
        self.cursor_t = t
        self.update()

    def set_playhead(self, t):
        if t != self.playhead:
            self.playhead = t
            self.update()

    def zoom_all(self):
        self.v0 = 0.0
        self.v1 = max(self.duration, 1.0)
        self.update()

    # ------------------------------------------------------------ coordinate
    def _plot(self):
        return QRectF(0, HANDLE_H, self.width(), self.height() - HANDLE_H - 14)

    def t2x(self, t):
        return (t - self.v0) / max(1e-9, self.v1 - self.v0) * self.width()

    def x2t(self, x):
        t = self.v0 + x / max(1, self.width()) * (self.v1 - self.v0)
        return max(0.0, min(self.duration or t, t))

    def db2y(self, db):
        r = self._plot()
        return r.top() + (MAX_DB - db) / (MAX_DB - MIN_DB) * r.height()

    def y2db(self, y):
        r = self._plot()
        db = MAX_DB - (y - r.top()) / max(1, r.height()) * (MAX_DB - MIN_DB)
        return max(MIN_DB, min(MAX_DB, db))

    def _markers(self):
        c = self.cue
        end = c.end if c.end > 0 else self.duration
        m = {"start": (c.start, C_START, "IN"), "end": (end, C_END, "OUT")}
        if c.loop:
            ls = c.loop_start if c.loop_start > 0 else c.start
            le = c.loop_end if c.loop_end > 0 else end
            m["loop_start"] = (ls, C_LOOP, "L⟨")
            m["loop_end"] = (le, C_LOOP, "⟩L")
        return m

    # ------------------------------------------------------------ disegno
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), C_BG)
        if self.cue is None:
            p.end()
            return
        W = self.width()
        r = self._plot()
        p.setRenderHint(QPainter.Antialiasing, False)
        if self.thumbs is not None:
            self._draw_thumbs(p, r)
        # regione fuori da IN/OUT
        mk = self._markers()
        xs, xe = self.t2x(mk["start"][0]), self.t2x(mk["end"][0])
        shade = QColor(0, 0, 0, 120)
        # loop
        if "loop_start" in mk:
            p.fillRect(QRectF(self.t2x(mk["loop_start"][0]), r.top(),
                              self.t2x(mk["loop_end"][0]) - self.t2x(mk["loop_start"][0]), r.height()),
                       QColor(240, 190, 40, 35))
        # griglia dB
        p.setPen(QPen(QColor(45, 47, 56), 1))
        f = QFont()
        f.setPixelSize(9)
        p.setFont(f)
        for db in (12, 6, 0, -6, -12, -24, -40):
            y = self.db2y(db)
            p.drawLine(QPointF(0, y), QPointF(W, y))
        p.setPen(QColor(110, 110, 125))
        last_y = -100.0
        for db in (6, 0, -12, -24, -40):        # con il grafico basso solo le etichette che non si toccano
            y = self.db2y(db)
            if y - last_y < 12:
                continue
            last_y = y
            p.drawText(QPointF(3, y - 2), f"{db:+d} dB" if db else tr("0 dB"))
        # forma d'onda
        if self.peaks is not None and len(self.peaks):
            mid = r.center().y()
            amp = r.height() / 2 * 0.95
            n = len(self.peaks)
            cols = max(1, int(W))
            i0 = np.clip((self.v0 + np.arange(cols) / cols * (self.v1 - self.v0)) * PEAK_RATE, 0, n).astype(int)
            i1 = np.clip((self.v0 + (np.arange(cols) + 1) / cols * (self.v1 - self.v0)) * PEAK_RATE, 0, n).astype(int)
            i1 = np.maximum(i1, i0 + 1)
            mins, maxs = self.peaks[:, 0], self.peaks[:, 1]
            if self._video_mode():
                # video: solo la semionda superiore, appoggiata in basso, bianca in trasparenza sulle miniature
                p.setPen(QPen(QColor(255, 255, 255, 150), 1))
                full = r.height() * 0.9
                for x in range(cols):
                    a, b = i0[x], min(i1[x], n)
                    if a >= n:
                        break
                    v = max(abs(float(mins[a:b].min())), abs(float(maxs[a:b].max())))
                    if v > 0.002:
                        p.drawLine(QPointF(x, r.bottom()), QPointF(x, r.bottom() - min(1.0, v) * full))
            else:
                p.setPen(QPen(C_WAVE, 1))
                for x in range(cols):
                    a, b = i0[x], min(i1[x], n)
                    if a >= n:
                        break
                    lo = float(mins[a:b].min())
                    hi = float(maxs[a:b].max())
                    p.drawLine(QPointF(x, mid - hi * amp), QPointF(x, mid - lo * amp))
        elif self.duration <= 0:
            p.setPen(QColor(140, 140, 150))
            p.drawText(r, Qt.AlignCenter, tr("Caricamento media…") if self.cue.file else tr("Nessun file"))
        # zone escluse
        if xs > 0:
            p.fillRect(QRectF(0, r.top(), xs, r.height()), shade)
        if xe < W:
            p.fillRect(QRectF(xe, r.top(), W - xe, r.height()), shade)
        # fade in/out automatici
        p.setRenderHint(QPainter.Antialiasing, True)
        c = self.cue
        self._draw_envelope(p, r)
        # curva di volume
        pts = sorted(c.curve)
        if pts:
            path = QPainterPath()
            path.moveTo(0, self.db2y(pts[0][1]))
            for t, db in pts:
                path.lineTo(self.t2x(t), self.db2y(db))
            path.lineTo(W, self.db2y(pts[-1][1]))
            p.setPen(QPen(C_CURVE, 2))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setBrush(QBrush(C_CURVE))
            for t, db in pts:
                p.drawEllipse(QPointF(self.t2x(t), self.db2y(db)), 4.5, 4.5)
        else:
            p.setPen(QPen(QColor(255, 140, 220, 90), 1, Qt.DotLine))
            p.drawLine(QPointF(0, self.db2y(0)), QPointF(W, self.db2y(0)))
        # marcatori
        for name, (t, col, lab) in mk.items():
            x = self.t2x(t)
            p.setPen(QPen(col, 2))
            p.drawLine(QPointF(x, 0), QPointF(x, r.bottom()))
            p.setBrush(QBrush(col))
            p.setPen(Qt.NoPen)
            w = 26
            box = QRectF(x - (w if name in ("end", "loop_end") else 0), 0, w, HANDLE_H - 2)
            p.drawRoundedRect(box, 3, 3)
            p.setPen(QColor(0, 0, 0))
            p.drawText(box, Qt.AlignCenter, lab)
        # keyframe (immagine in alto, audio in basso nella fascia inferiore), posizionati sul file
        self._kf_hits = []
        ax = cue_axis(c, self.duration) if self.duration else None
        if ax is not None:
            from .keyframes import KIND_LABELS as KL, label as kf_label
            for pt in (c.preset_points or []):
                try:
                    res = cue_to_file(ax, float(pt.get("t", 0)))
                except (TypeError, ValueError, AttributeError):
                    continue
                if res is None:
                    continue
                ft, rep = res
                x = self.t2x(ft)
                kind = pt.get("kind", "visual")
                y = r.bottom() - (17 if kind == "visual" else 7)
                diamond(p, x, y, 5, MCOL[kind], hollow=pt.get("preset") == "__base__")
                tip = tr("{0}: {1} — cue {2}", KL.get(kind, kind), kf_label(pt.get('preset', '')), fmt_time(float(pt['t'])))
                if rep:
                    tip += tr(" (ripetizione {0} del loop)", rep)
                self._kf_hits.append((x, y, tip))
        # cursore dei keyframe
        if self.cursor_t is not None:
            x = self.t2x(self.cursor_t)
            p.setPen(QPen(QColor(255, 255, 255, 170), 1, Qt.DashLine))
            p.drawLine(QPointF(x, HANDLE_H), QPointF(x, r.bottom()))
        # playhead
        if self.playhead is not None:
            x = self.t2x(self.playhead)
            p.setPen(QPen(C_HEAD, 1.5))
            p.drawLine(QPointF(x, HANDLE_H), QPointF(x, r.bottom()))
        # righello
        p.setPen(QColor(140, 140, 150))
        span = self.v1 - self.v0
        step = next((s for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600)
                     if span / s < W / 70), 1200)
        t = (self.v0 // step) * step
        while t <= self.v1:
            x = self.t2x(t)
            if x >= 0:
                p.drawLine(QPointF(x, self.height() - 14), QPointF(x, self.height() - 10))
                p.drawText(QPointF(x + 2, self.height() - 2), fmt_time(t))
            t += step
        p.end()

    # ------------------------------------------------------------ inviluppo: fade e opacità
    def _video_mode(self):
        return self.cue is not None and self.cue.type == "video"

    def _env_points(self):
        """Angoli dell'inviluppo: (x IN, x fine fade in, x inizio fade out, x OUT, y del livello)."""
        c = self.cue
        r = self._plot()
        end = c.end if c.end > 0 else self.duration
        fi = max(0.0, float(c.fade_in or 0))
        fo = max(0.0, float(c.fade_out or 0))
        if self._video_mode():
            y = r.top() + (1.0 - max(0.0, min(1.0, float(c.opacity)))) * r.height()
        else:
            y = r.top() + 6
        return self.t2x(c.start), self.t2x(c.start + fi), self.t2x(end - fo), self.t2x(end), y

    def _draw_envelope(self, p, r):
        c = self.cue
        if self.duration <= 0:
            return
        xs, xfi, xfo, xe, y = self._env_points()
        video = self._video_mode()
        col = C_ENV if video else QColor(255, 255, 255, 170)
        if video or c.fade_in > 0 or c.fade_out > 0:
            path = QPainterPath()
            path.moveTo(xs, r.bottom())
            path.lineTo(xfi, y)
            path.lineTo(xfo, y)
            path.lineTo(xe, r.bottom())
            p.setPen(QPen(col, 1.6 if video else 1.2, Qt.SolidLine if video else Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.setBrush(QBrush(col))
        for x in (xfi, xfo):                       # quadratini dei fade
            p.drawRect(QRectF(x - 4, y - 4, 8, 8))
        if video:
            f = QFont()
            f.setPixelSize(10)
            f.setBold(True)
            p.setFont(f)
            txt = tr("opacità {0:.0f}%", float(c.opacity) * 100)
            ty = y + 12 if y + 14 < r.bottom() else y - 4
            p.setPen(QColor(0, 0, 0, 200))
            p.drawText(QPointF(xfi + 7, ty + 1), txt)
            p.setPen(C_ENV)
            p.drawText(QPointF(xfi + 6, ty), txt)

    def _env_hit(self, pos):
        if self.cue is None or self.duration <= 0:
            return None
        xs, xfi, xfo, xe, y = self._env_points()
        if abs(pos.y() - y) < 7:
            if abs(pos.x() - xfi) < 7:
                return ("fade_in",)
            if abs(pos.x() - xfo) < 7:
                return ("fade_out",)
        if self._video_mode() and abs(pos.y() - y) < 5 and xfi + 7 <= pos.x() <= xfo - 7:
            return ("opacity",)
        return None

    # ------------------------------------------------------------ interazione
    def _hit(self, pos):
        if self.cue is None:
            return None
        for i, (t, db) in enumerate(self.cue.curve):
            if abs(self.t2x(t) - pos.x()) < 7 and abs(self.db2y(db) - pos.y()) < 7:
                return ("point", i)
        env = self._env_hit(pos)
        if env is not None:
            return env
        best, bd = None, 7
        for name, (t, col, lab) in self._markers().items():
            d = abs(self.t2x(t) - pos.x())
            if pos.y() < HANDLE_H + 4:
                d = min(d, abs(self.t2x(t) + (-13 if name in ("end", "loop_end") else 13) - pos.x()))
            if d < bd:
                best, bd = ("marker", name), d
        return best

    def mousePressEvent(self, e):
        if self.cue is None:
            return
        pos = e.position()
        if e.button() == Qt.MiddleButton:
            self.drag = ("pan", pos.x(), self.v0, self.v1)
            return
        hit = self._hit(pos)
        if e.button() == Qt.RightButton:
            if hit and hit[0] == "point":
                del self.cue.curve[hit[1]]
                self.changed.emit()
                self.update()
            else:
                self._menu(e, pos)
            return
        if e.button() == Qt.LeftButton:
            if hit:
                self.drag = hit
            elif e.modifiers() & Qt.ControlModifier or pos.y() > HANDLE_H:
                if e.modifiers() & Qt.ControlModifier:
                    self._add_point(pos)
                else:
                    self.drag = ("pan", pos.x(), self.v0, self.v1)

    def mouseDoubleClickEvent(self, e):
        if self.cue is not None and e.button() == Qt.LeftButton and not self._hit(e.position()):
            self._add_point(e.position())

    def _add_point(self, pos):
        self.cue.curve.append([round(self.x2t(pos.x()), 3), round(self.y2db(pos.y()), 1)])
        self.cue.curve.sort()
        for i, (t, db) in enumerate(self.cue.curve):
            if abs(self.t2x(t) - pos.x()) < 1:
                self.drag = ("point", i)
        self.changed.emit()
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.drag is None:
            for kx, ky, tip in getattr(self, "_kf_hits", []):
                if abs(kx - pos.x()) < 6 and abs(ky - pos.y()) < 6:
                    QToolTip.showText(e.globalPosition().toPoint(), tip, self)
                    break
            hit = self._hit(pos)
            self.setCursor(Qt.SizeHorCursor if hit and hit[0] in ("marker", "fade_in", "fade_out")
                           else Qt.SizeVerCursor if hit and hit[0] == "opacity"
                           else Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            return
        kind = self.drag[0]
        if kind == "pan":
            _, x0, v0, v1 = self.drag
            dt = (pos.x() - x0) / max(1, self.width()) * (v1 - v0)
            span = v1 - v0
            nv0 = max(0.0, min(v0 - dt, max(self.duration, 1) - span))
            self.v0, self.v1 = nv0, nv0 + span
            self.update()
            return
        t = round(self.x2t(pos.x()), 3)
        c = self.cue
        if kind in ("fade_in", "fade_out", "opacity"):
            end = c.end if c.end > 0 else self.duration
            span = max(0.0, end - c.start)
            if kind == "fade_in":
                c.fade_in = round(max(0.0, min(t - c.start, span - float(c.fade_out or 0))), 2)
                tip = tr("Fade in {0:.2f} s", c.fade_in)
            elif kind == "fade_out":
                c.fade_out = round(max(0.0, min(end - t, span - float(c.fade_in or 0))), 2)
                tip = tr("Fade out {0:.2f} s", c.fade_out)
            else:
                r = self._plot()
                c.opacity = round(max(0.0, min(1.0, 1.0 - (pos.y() - r.top()) / max(1.0, r.height()))), 2)
                tip = tr("Opacità {0:.0f}%", c.opacity * 100)
            QToolTip.showText(e.globalPosition().toPoint(), tip, self)
            self.changed.emit()
            self.update()
            return
        if kind == "point":
            i = self.drag[1]
            if i < len(c.curve):
                c.curve[i] = [t, round(self.y2db(pos.y()), 1)]
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"{fmt_time(t)}  {c.curve[i][1]:+.1f} dB", self)
        else:
            name = self.drag[1]
            end = c.end if c.end > 0 else self.duration
            if name == "start":
                c.start = max(0.0, min(t, end - 0.05))
            elif name == "end":
                c.end = max(c.start + 0.05, t)
                if self.duration and c.end >= self.duration - 0.005:
                    c.end = 0.0
            elif name == "loop_start":
                c.loop_start = max(c.start, t)
            elif name == "loop_end":
                c.loop_end = max(t, (c.loop_start or c.start) + 0.05)
            QToolTip.showText(e.globalPosition().toPoint(), fmt_time(t), self)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        if self.drag and self.drag[0] == "point":
            self.cue.curve.sort()
            self.changed.emit()
        self.drag = None

    def wheelEvent(self, e):
        if self.cue is None:
            return
        d = e.angleDelta().y() or e.angleDelta().x()
        span = self.v1 - self.v0
        maxspan = max(self.duration, 1.0)
        if e.modifiers() & Qt.ShiftModifier:
            shift = -d / 120 * span * 0.1
            nv0 = max(0.0, min(self.v0 + shift, maxspan - span))
            self.v0, self.v1 = nv0, nv0 + span
        else:
            f = 0.8 if d > 0 else 1.25
            tm = self.x2t(e.position().x())
            nspan = max(0.05, min(maxspan, span * f))
            ratio = (tm - self.v0) / span if span else 0
            nv0 = max(0.0, min(tm - ratio * nspan, maxspan - nspan))
            self.v0, self.v1 = nv0, nv0 + nspan
        self.update()

    def _menu(self, e, pos):
        t = round(self.x2t(pos.x()), 3)
        c = self.cue
        m = QMenu(self)

        def act(label, fn):
            a = QAction(label, m)
            a.triggered.connect(lambda: (fn(), self.changed.emit(), self.update()))
            m.addAction(a)

        act(tr("Imposta IN a {0}", fmt_time(t)), lambda: setattr(c, "start", t))
        act(tr("Imposta OUT a {0}", fmt_time(t)), lambda: setattr(c, "end", t))
        m.addSeparator()
        act(tr("Inizio loop qui"), lambda: (setattr(c, "loop", True), setattr(c, "loop_start", t)))
        act(tr("Fine loop qui"), lambda: (setattr(c, "loop", True), setattr(c, "loop_end", t)))
        m.addSeparator()
        act(tr("Aggiungi punto curva"), lambda: self._add_point(pos))
        act(tr("Cancella curva di volume"), lambda: c.curve.clear())
        act(tr("Reimposta IN/OUT"), lambda: (setattr(c, "start", 0.0), setattr(c, "end", 0.0)))
        m.addSeparator()
        act(tr("Mostra tutto"), self.zoom_all)
        m.exec(e.globalPosition().toPoint())
