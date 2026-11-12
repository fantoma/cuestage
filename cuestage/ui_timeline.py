"""Editor grafico: forma d'onda, punti di inizio/fine, loop e curva di volume."""
from .i18n import tr
import math

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


C_MARK = QColor(90, 220, 230)          # marker dell'utente
WAVE_H = 0.45                          # altezza massima della semionda (frazione del grafico)


class TimelineEditor(QWidget):
    changed = Signal()
    cursorMoved = Signal(float)        # clic sul grafico: nuovo cursore (tempo del file)
    viewChanged = Signal()             # zoom o scorrimento: per la barra di scorrimento sotto il grafico

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
        self.point_mode = "volume"    # doppio clic / Ctrl+clic: punto di «volume» o di «opacity» (video)
        self.snap = True              # trascinando un punto si aggancia a quelli delle altre curve e ai marker
        self._snap_x = None
        self.setMinimumHeight(102)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)

    # ------------------------------------------------------------ dati
    def set_cue(self, cue, duration, peaks):
        same = self.cue is cue
        if not same:     # video: di serie il doppio clic aggiunge punti di opacità
            self.point_mode = "opacity" if cue is not None and cue.type == "video" else "volume"
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
            cols = int(data[2]) if len(data) > 2 else int(data[1])
            self.thumbs = (img, int(data[1]), max(1, cols)) if not img.isNull() else None
        self.update()

    def _draw_thumbs(self, p, r):
        img, n, cols = self.thumbs
        rows = max(1, int(math.ceil(n / cols)))
        tw = img.width() / cols
        th = img.height() / rows
        if tw <= 0 or th <= 0 or self.duration <= 0:
            return
        w = r.height() * tw / th                     # miniature affiancate con le loro proporzioni
        step = self.duration / n                     # la miniatura i è il fotogramma al tempo i·step
        p.save()
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setClipRect(r)
        # ogni casella mostra il fotogramma più vicino al tempo del suo bordo sinistro, così l'immagine
        # cambia dove cambia davvero anche con lo zoom (le caselle seguono la griglia dei tempi)
        t0 = max(0.0, self.x2t(0))
        i0 = int(t0 / step)
        i = i0
        while True:
            x0 = self.t2x(i * step)
            if x0 > self.width():
                break
            x1 = self.t2x((i + 1) * step)
            k = max(0, min(n - 1, i))
            src = QRectF((k % cols) * tw, (k // cols) * th, tw, th)
            if x1 - x0 >= w * 0.98:
                # zoom forte: la miniatura ha il suo intervallo tutto per sé, ripetuta per riempirlo
                x = x0
                while x < x1:
                    p.drawImage(QRectF(x, r.top(), min(w, x1 - x), r.height()), img,
                                QRectF(src.left(), src.top(), tw * min(1.0, (x1 - x) / w), th))
                    x += w
                i += 1
            else:
                # più intervalli in una casella: si mostra quello all'inizio della casella
                p.drawImage(QRectF(x0, r.top(), w, r.height()), img, src)
                i = max(i + 1, int(self.x2t(x0 + w) / step + 1e-9))
            if i >= n:
                break
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

    # i punti della curva di volume sono relativi al volume della cue: si disegnano al volume effettivo
    def _base_db(self):
        lv = float(self.cue.level_db) if self.cue is not None else 0.0
        return lv if lv > MIN_DB else 0.0

    def pt2y(self, rel):
        rel = float(rel)
        return self.db2y(MIN_DB if rel <= MIN_DB + 0.01 else max(MIN_DB, min(MAX_DB, self._base_db() + rel)))

    def y2pt(self, y):
        db = self.y2db(y)
        return MIN_DB if db <= MIN_DB + 0.01 else round(db - self._base_db(), 1)

    def _curve_rel(self, t):
        """Valore (dB, relativo al volume della cue) della curva di volume al tempo t; None senza punti."""
        pts = sorted(self.cue.curve or []) if self.cue is not None else []
        if not pts:
            return None
        return float(np.interp(t, [a for a, _b in pts], [b for _a, b in pts]))

    def _vol_y(self, t):
        """y del volume effettivo (volume della cue + curva) al tempo t, senza i fade."""
        rel = self._curve_rel(t)
        if rel is None or float(self.cue.level_db) <= MIN_DB:
            return self.db2y(max(MIN_DB, min(MAX_DB, float(self.cue.level_db))))
        return self.pt2y(rel)

    def op2y(self, v):
        r = self._plot()
        return r.top() + (1.0 - max(0.0, min(1.0, float(v)))) * r.height()

    def y2op(self, y):
        r = self._plot()
        return max(0.0, min(1.0, 1.0 - (y - r.top()) / max(1.0, r.height())))

    def _op_points(self):
        c = self.cue
        if not isinstance(getattr(c, "op_curve", None), list):
            c.op_curve = []
        return c.op_curve

    def set_point_mode(self, mode):
        self.point_mode = mode if mode in ("volume", "opacity") else "volume"
        self.update()

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
    def set_view(self, v0):
        """Scorre la vista (zoom invariato) facendo partire il grafico da v0 secondi."""
        span = self.v1 - self.v0
        v0 = max(0.0, min(float(v0), max(self.duration, 1.0) - span))
        if abs(v0 - self.v0) > 1e-9:
            self.v0, self.v1 = v0, v0 + span
            self.update()

    def paintEvent(self, e):
        view = (round(self.v0, 4), round(self.v1, 4), round(float(self.duration or 0), 4))
        if view != getattr(self, "_last_view", None):
            self._last_view = view
            self.viewChanged.emit()
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
            n = len(self.peaks)
            cols = max(1, int(W))
            i0 = np.clip((self.v0 + np.arange(cols) / cols * (self.v1 - self.v0)) * PEAK_RATE, 0, n).astype(int)
            i1 = np.clip((self.v0 + (np.arange(cols) + 1) / cols * (self.v1 - self.v0)) * PEAK_RATE, 0, n).astype(int)
            i1 = np.maximum(i1, i0 + 1)
            mins, maxs = self.peaks[:, 0], self.peaks[:, 1]
            # solo la semionda superiore, appoggiata in basso, bianca (in trasparenza sulle miniature)
            p.setPen(QPen(QColor(255, 255, 255, 150 if self.thumbs is not None else 190), 1))
            full = r.height() * WAVE_H
            for x in range(cols):
                a, b = i0[x], min(i1[x], n)
                if a >= n:
                    break
                v = max(abs(float(mins[a:b].min())), abs(float(maxs[a:b].max())))
                if v > 0.002:
                    p.drawLine(QPointF(x, r.bottom()), QPointF(x, r.bottom() - min(1.0, v) * full))
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
        # volume: linea rosa (volume della cue + curva dei punti, con i fade) e punti della curva
        self._draw_volume(p, r)
        # curva di opacità (video)
        ops = sorted(self._op_points()) if self._video_mode() else []
        if ops:
            path = QPainterPath()
            path.moveTo(0, self.op2y(ops[0][1]))
            for t, v in ops:
                path.lineTo(self.t2x(t), self.op2y(v))
            path.lineTo(W, self.op2y(ops[-1][1]))
            p.setPen(QPen(QColor(255, 220, 120, 150), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(QPen(QColor(0, 0, 0, 180), 1))
            p.setBrush(QBrush(QColor(255, 220, 120)))
            for t, v in ops:
                x, y = self.t2x(t), self.op2y(v)
                p.drawRect(QRectF(x - 4.5, y - 4.5, 9, 9))
        # marcatori
        for name, (t, col, lab) in mk.items():
            x = self.t2x(t)
            p.setPen(QPen(col, 2))
            p.drawLine(QPointF(x, 0), QPointF(x, r.bottom()))
            p.setBrush(QBrush(col))
            p.setPen(Qt.NoPen)
            w = 26
            # IN/OUT in alto, marcatori del loop in basso: anche quando coincidono si prendono separatamente
            top = r.bottom() - (HANDLE_H - 2) if name.startswith("loop") else 0
            box = QRectF(x - (w if name in ("end", "loop_end") else 0), top, w, HANDLE_H - 2)
            p.drawRoundedRect(box, 3, 3)
            p.setPen(QColor(0, 0, 0))
            p.drawText(box, Qt.AlignCenter, lab)
        self._draw_fade_handles(p)
        # marker dell'utente
        self._umark_hits = []
        f2 = QFont()
        f2.setPixelSize(10)
        f2.setBold(True)
        p.setFont(f2)
        fm = p.fontMetrics()
        from .markers import marker_window
        ms_all = self._user_markers()
        end_t = c.end if c.end > 0 else self.duration
        for m in ms_all:                         # durata del fade delle azioni (fascia azzurra)
            try:
                at, dur = marker_window(ms_all, m, float(c.start or 0), end_t)
            except (TypeError, ValueError, AttributeError):
                continue
            if dur > 0:
                xa, xb = self.t2x(at), self.t2x(at + dur)
                p.fillRect(QRectF(xa, r.top() + 15, max(1.0, xb - xa), 5), QColor(90, 220, 230, 120))
        for i, m in enumerate(ms_all):
            x = self.t2x(float(m.get("t", 0)))
            if x < -40 or x > W + 2:
                continue
            p.setPen(QPen(C_MARK, 1.3, Qt.DashLine))
            p.drawLine(QPointF(x, HANDLE_H), QPointF(x, r.bottom()))
            name = str(m.get("name") or "") or "M"
            box = QRectF(x, r.top() + 1, fm.horizontalAdvance(name) + 8, 13)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(C_MARK))
            p.drawRoundedRect(box, 2, 2)
            p.setPen(QColor(0, 0, 0))
            p.drawText(box, Qt.AlignCenter, name)
            self._umark_hits.append((i, x, box))
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
                self._kf_hits.append((x, y, tip, pt, ft))
        # guida dell'aggancio (snap) durante il trascinamento
        if self._snap_x is not None and self.drag is not None:
            p.setPen(QPen(QColor(255, 255, 255, 220), 1, Qt.DotLine))
            p.drawLine(QPointF(self._snap_x, HANDLE_H), QPointF(self._snap_x, r.bottom()))
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
        if self._video_mode():                      # video: i fade dell'immagine (linea arancione)
            fi, fo = (max(0.0, x) for x in c.video_fades())
        else:
            fi = max(0.0, float(c.fade_in or 0))
            fo = max(0.0, float(c.fade_out or 0))
        span = max(0.0, end - float(c.start or 0))
        if fi + fo > span > 0:                      # fade più lunghi della cue: si dividono la durata
            k = span / (fi + fo)
            fi, fo = fi * k, fo * k
        if self._video_mode():
            y = r.top() + (1.0 - max(0.0, min(1.0, float(c.opacity)))) * r.height()
        else:
            y = self.db2y(max(MIN_DB, min(MAX_DB, float(c.level_db))))     # audio: volume della cue
        return self.t2x(c.start), self.t2x(c.start + fi), self.t2x(end - fo), self.t2x(end), y

    def _curve_op(self, t):
        """Opacità della curva dei punti al tempo t del file (None se la cue non ha punti di opacità)."""
        ops = sorted(self._op_points()) if self._video_mode() else []
        if not ops:
            return None
        return max(0.0, min(1.0, float(np.interp(t, [a for a, _b in ops], [b for _a, b in ops]))))

    def _handle_ys(self):
        """y dei quadratini di fade in e fade out (con la curva di opacità stanno sulla curva)."""
        xs, xfi, xfo, xe, y = self._env_points()
        if not self._video_mode():                  # audio: i fade stanno sulla linea rosa del volume
            return self._vol_y(self.x2t(xfi)), self._vol_y(self.x2t(xfo))
        if self._curve_op(0.0) is None:
            return y, y
        return self.op2y(self._curve_op(self.x2t(xfi))), self.op2y(self._curve_op(self.x2t(xfo)))

    def _draw_envelope(self, p, r):
        c = self.cue
        if self.duration <= 0:
            return
        xs, xfi, xfo, xe, y = self._env_points()
        video = self._video_mode()
        if not video:
            return                                  # audio: niente linea arancione, i fade sono sulla rosa
        col = C_ENV
        path = QPainterPath()
        path.moveTo(xs, r.bottom())
        curve = video and self._curve_op(0.0) is not None
        if curve:
            # opacità effettiva: curva dei punti × fade in/out (è quella che va in uscita)
            x0, x1 = max(xs, -2.0), min(xe, self.width() + 2.0)
            path.moveTo(x0, r.bottom())
            x = x0
            while x <= x1:
                t = self.x2t(x)
                v = self._curve_op(t)
                if xfi > xs and x < xfi:
                    v *= (x - xs) / (xfi - xs)
                if xe > xfo and x > xfo:
                    v *= (xe - x) / (xe - xfo)
                path.lineTo(x, self.op2y(max(0.0, v)))
                x += 2.0
            path.lineTo(x1, r.bottom())
        else:
            path.lineTo(xfi, y)
            path.lineTo(xfo, y)
            path.lineTo(xe, r.bottom())
        p.setPen(QPen(col, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.setBrush(QBrush(col))
        yi, yo = self._handle_ys()
        for x, hy in ((xfi, yi), (xfo, yo)):        # quadratini dei fade
            p.drawRect(QRectF(x - 4, hy - 4, 8, 8))
        f = QFont()
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        if curve:
            txt = tr("opacità: curva dei punti")
            ty = r.top() + 11
        else:
            txt = tr("opacità {0:.0f}%", float(c.opacity) * 100)
            ty = y + 12 if y + 14 < r.bottom() else y - 4
        p.setPen(QColor(0, 0, 0, 200))
        p.drawText(QPointF(xfi + 7, ty + 1), txt)
        p.setPen(C_ENV)
        p.drawText(QPointF(xfi + 6, ty), txt)

    def _draw_volume(self, p, r):
        """Linea rosa del volume: volume della cue + curva dei punti, con i fade in/out (è quello che si sente);
        fuori da IN/OUT tratteggiata. Sopra i punti della curva e, per l'audio, i quadratini dei fade."""
        c = self.cue
        W = self.width()
        video = self._video_mode()
        pts = sorted(c.curve or [])
        if self.duration > 0:
            xs, xfi, _xfo, xe, _y = self._env_points()
        else:
            xs, xfi, xe = 0.0, 0.0, float(W)
        faint = QPen(QColor(255, 140, 220, 90), 1, Qt.DotLine)
        p.setBrush(Qt.NoBrush)
        p.setPen(faint)
        for a, b in ((0.0, xs), (xe, float(W))):     # fuori da IN/OUT
            if b > a:
                path = QPainterPath()
                path.moveTo(a, self._vol_y(self.x2t(a)))
                x = a
                while x < b:
                    x = min(b, x + 4.0)
                    path.lineTo(x, self._vol_y(self.x2t(x)))
                p.drawPath(path)
        x0, x1 = max(xs, -2.0), min(xe, W + 2.0)
        if x1 > x0:
            bottom = r.bottom()
            path = QPainterPath()
            path.moveTo(x0, bottom if xfi > xs else self._vol_draw_y(x0))
            x = x0
            while True:
                path.lineTo(x, self._vol_draw_y(x))
                if x >= x1:
                    break
                x = min(x1, x + 2.0)
            p.setPen(QPen(C_CURVE, 2 if pts else 1.6))
            p.drawPath(path)
        p.setBrush(QBrush(C_CURVE))
        p.setPen(Qt.NoPen)
        for t, db in pts:
            p.drawEllipse(QPointF(self.t2x(t), self.pt2y(db)), 4.5, 4.5)
        if self.duration <= 0:
            return
        if video:
            return
        f = QFont()
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        lv = float(c.level_db)
        txt = tr("volume −∞") if lv <= MIN_DB else tr("volume {0:+.1f} dB", lv)
        if pts and lv > MIN_DB:
            txt = tr("volume {0:+.1f} dB + curva dei punti", lv)
        ty = r.top() + 11
        p.setPen(QColor(0, 0, 0, 200))
        p.drawText(QPointF(xfi + 7, ty + 1), txt)
        p.setPen(C_CURVE)
        p.drawText(QPointF(xfi + 6, ty), txt)

    def _draw_fade_handles(self, p):
        """Quadratini rosa dei fade dell'audio: disegnati dopo i marcatori, così restano visibili anche quando
        un fade è a zero (sopra la linea di IN o di OUT)."""
        if self.cue is None or self.duration <= 0:
            return
        p.setPen(QPen(QColor(255, 255, 255, 220), 1.2))
        p.setBrush(QBrush(C_CURVE))
        if self._video_mode():
            hs = self._audio_handles()
            sz = 9
        else:
            xs, xfi, xfo, xe, _y = self._env_points()
            yi, yo = self._handle_ys()
            hs = [(xfi, yi), (xfo, yo)]
            sz = 10
        for x, hy in hs:
            p.drawRect(QRectF(x - sz / 2, hy - sz / 2, sz, sz))

    def _audio_handles(self):
        """(x, y) dei quadratini dei fade dell'audio sulla linea rosa (fine fade in, inizio fade out)."""
        c = self.cue
        start = float(c.start or 0)
        end = float(c.end) if c.end and c.end > 0 else float(self.duration or 0)
        fi, fo = max(0.0, float(c.fade_in or 0)), max(0.0, float(c.fade_out or 0))
        span = max(0.0, end - start)
        if fi + fo > span > 0:
            k = span / (fi + fo)
            fi, fo = fi * k, fo * k
        return [(self.t2x(start + fi), self._vol_y(start + fi)), (self.t2x(end - fo), self._vol_y(end - fo))]

    def fade_gain(self, t):
        """Guadagno dei fade al tempo t del file, come in riproduzione: fade in a curva S, fade out lineare."""
        c = self.cue
        if c is None:
            return 1.0
        start = float(c.start or 0)
        end = float(c.end) if c.end and c.end > 0 else float(self.duration or 0)
        fi, fo = max(0.0, float(c.fade_in or 0)), max(0.0, float(c.fade_out or 0))
        span = max(0.0, end - start)
        if fi + fo > span > 0:
            k = span / (fi + fo)
            fi, fo = fi * k, fo * k
        g = 1.0
        if fi > 0 and t < start + fi:
            g = 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, (t - start) / fi)))
        if fo > 0 and end > 0 and t > end - fo:
            g = min(g, max(0.0, min(1.0, (end - t) / fo)))
        return g

    def vol_db_at(self, t, fades=True):
        """Volume (dB) che si sente al tempo t del file: volume della cue + curva dei punti (+ fade). −60 = −∞."""
        c = self.cue
        lv = float(c.level_db)
        rel = self._curve_rel(t)
        if lv <= MIN_DB or (rel is not None and rel <= MIN_DB + 0.01):
            return MIN_DB
        db = lv + (rel or 0.0)
        if fades:
            g = self.fade_gain(t)
            if g <= 1e-4:
                return MIN_DB
            db += 20.0 * math.log10(g)
        return max(MIN_DB, min(MAX_DB, db))

    def _vol_draw_y(self, x):
        """y della linea rosa disegnata alla x: il volume che si sente, fade compresi (dentro IN/OUT)."""
        return self.db2y(self.vol_db_at(self.x2t(x)))

    def _seg_hit(self, pos):
        """Clic sul tratto rettilineo della linea rosa: ("seg", indici dei punti che lo definiscono). Senza punti
        il tratto è il volume della cue."""
        if self.cue is None or abs(self._vol_draw_y(pos.x()) - pos.y()) >= 5:
            return None
        pts = sorted(self.cue.curve or [])
        self.cue.curve[:] = pts
        t = self.x2t(pos.x())
        if not pts:
            return ("seg", ())
        after = next((i for i, (a, _b) in enumerate(pts) if a >= t), None)
        if after is None:
            return ("seg", (len(pts) - 1,))                 # dopo l'ultimo punto
        if after == 0:
            return ("seg", (0,))                            # prima del primo punto
        return ("seg", (after - 1, after))

    def _op_draw_y(self, x):
        """y della linea arancione (opacità della curva dei punti con i fade dell'immagine) alla x."""
        xs, xfi, xfo, xe, _y = self._env_points()
        v = self._curve_op(self.x2t(x))
        if v is None:
            return _y
        if xfi > xs and xs <= x < xfi:
            v *= (x - xs) / (xfi - xs)
        if xe > xfo and xfo < x <= xe:
            v *= (xe - x) / (xe - xfo)
        return self.op2y(max(0.0, v))

    def _oseg_hit(self, pos):
        """Clic sul tratto rettilineo della linea arancione: ("oseg", indici dei punti di opacità che lo definiscono)."""
        xs, _xfi, _xfo, xe, _y = self._env_points()
        if not (xs <= pos.x() <= xe) or abs(self._op_draw_y(pos.x()) - pos.y()) >= 5:
            return None
        ops = self._op_points()
        ops.sort()
        t = self.x2t(pos.x())
        after = next((i for i, (a, _b) in enumerate(ops) if a >= t), None)
        if after is None:
            return ("oseg", (len(ops) - 1,))
        if after == 0:
            return ("oseg", (0,))
        return ("oseg", (after - 1, after))

    def _env_hit(self, pos):
        if self.cue is None or self.duration <= 0:
            return None
        xs, xfi, xfo, xe, y = self._env_points()
        yi, yo = self._handle_ys()
        if abs(pos.y() - yi) < 7 and abs(pos.x() - xfi) < 7:
            return ("fade_in",)
        if abs(pos.y() - yo) < 7 and abs(pos.x() - xfo) < 7:
            return ("fade_out",)
        if self._video_mode():                      # quadratini rosa: fade dell'audio del video
            (ax_i, ay_i), (ax_o, ay_o) = self._audio_handles()
            if abs(pos.y() - ay_i) < 7 and abs(pos.x() - ax_i) < 7:
                return ("afade_in",)
            if abs(pos.y() - ay_o) < 7 and abs(pos.x() - ax_o) < 7:
                return ("afade_out",)
        if self._video_mode() and self._curve_op(0.0) is not None:
            return self._oseg_hit(pos)      # con la curva dei punti: i tratti si trascinano con i loro punti
        if not self._video_mode():
            return None                     # audio: il volume si regola con il fader della scheda Audio
        if abs(pos.y() - y) < 5 and xfi + 7 <= pos.x() <= xfo - 7:
            return ("opacity",)
        return None

    # ------------------------------------------------------------ marker dell'utente e keyframe
    def _user_markers(self):
        c = self.cue
        if not isinstance(getattr(c, "markers", None), list):
            c.markers = []
        return c.markers

    def add_marker(self, t, name=None):
        """Nuovo marker al tempo t del file (nome automatico «M1», «M2»…)."""
        ms = self._user_markers()
        if name is None:
            used = {str(m.get("name")) for m in ms}
            k = 1
            while f"M{k}" in used:
                k += 1
            name = f"M{k}"
        m = {"t": round(max(0.0, min(float(t), self.duration or float(t))), 3), "name": name}
        ms.append(m)
        ms.sort(key=lambda x: float(x.get("t", 0)))
        self.changed.emit()
        self.update()
        return m

    def rename_marker(self, i):
        from PySide6.QtWidgets import QInputDialog
        ms = self._user_markers()
        if not (0 <= i < len(ms)):
            return
        n, ok = QInputDialog.getText(self, tr("Marker"), tr("Nome del marker:"), text=str(ms[i].get("name") or ""))
        if ok:
            ms[i]["name"] = n.strip()
            self.changed.emit()
            self.update()

    def _cue_time_of(self, ft):
        """Tempo della cue (prima volta) in cui si suona la posizione ft del file."""
        from .markers import file_to_cue_times
        ax = cue_axis(self.cue, self.duration) if self.duration else None
        ts = file_to_cue_times(ax, ft) if ax is not None else []
        return ts[0] if ts else max(0.0, ft - float(self.cue.start or 0))

    def add_keyframe(self, kind, ft, preset):
        from . import keyframes as kfm
        kfm.new_keyframe(self.cue, kind, round(self._cue_time_of(ft), 2), preset)
        self.changed.emit()
        self.update()

    def _preset_menu(self, m, kind, fn):
        from . import keyframes as kfm
        names = [kfm.BASE] + sorted(self.cue.preset_dict(kind), key=str.lower)
        for n in names:
            a = QAction(kfm.label(n), m)
            a.triggered.connect(lambda _=False, n=n: fn(n))
            m.addAction(a)

    def _move_cursor(self, x):
        t = self.x2t(max(0.0, min(float(self.width()), x)))
        if t == self.cursor_t:
            return
        self.cursor_t = t
        self.cursor_used = True
        self.update()
        self.cursorMoved.emit(t)

    def _snap_targets(self, kind, drag):
        """Tempi (del file) a cui agganciarsi: punti delle altre curve, marker, IN/OUT, loop, keyframe."""
        c = self.cue
        out = []
        if kind != "point":
            out += [float(t) for t, _d in c.curve]
        if kind != "opoint" and self._video_mode():
            out += [float(t) for t, _v in self._op_points()]
        for i, m in enumerate(self._user_markers()):
            if not (kind == "umark" and drag[1] == i):
                out.append(float(m.get("t", 0)))
        out += [t for (t, _col, _lab) in self._markers().values()]
        for _x, _y, _tip, pt, ft in getattr(self, "_kf_hits", []):
            if not (kind == "kf" and pt is drag[1]):
                out.append(float(ft))
        return out

    def _snap(self, t, kind, drag):
        tol = 6 * (self.v1 - self.v0) / max(1, self.width())         # 6 pixel
        best, bd = None, tol
        for s in self._snap_targets(kind, drag):
            d = abs(s - t)
            if d <= bd:
                best, bd = s, d
        self._snap_x = self.t2x(best) if best is not None else None
        return round(best, 3) if best is not None else t

    def set_snap(self, on):
        self.snap = bool(on)

    def keyPressEvent(self, e):
        if self.cue is not None and e.key() == Qt.Key_M and not e.modifiers():
            t = self.cursor_t if self.cursor_t is not None else (self.playhead or float(self.cue.start or 0))
            self.add_marker(t)
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------ interazione
    def _hit(self, pos):
        if self.cue is None:
            return None
        if self._video_mode():
            for i, (t, v) in enumerate(self._op_points()):
                if abs(self.t2x(t) - pos.x()) < 7 and abs(self.op2y(v) - pos.y()) < 7:
                    return ("opoint", i)
        for i, (t, db) in enumerate(self.cue.curve):
            if abs(self.t2x(t) - pos.x()) < 7 and abs(self.pt2y(db) - pos.y()) < 7:
                return ("point", i)
        env = self._env_hit(pos)
        if env is not None:
            return env
        seg = self._seg_hit(pos)
        if seg is not None:
            return seg
        for kx, ky, _tip, pt, ft in getattr(self, "_kf_hits", []):
            if abs(kx - pos.x()) < 6 and abs(ky - pos.y()) < 6:
                return ("kf", pt, ft)
        for i, x, box in getattr(self, "_umark_hits", []):
            if box.contains(pos) or (abs(x - pos.x()) < 5 and pos.y() > HANDLE_H):
                return ("umark", i)
        best, bd = None, 7
        bottom = self._plot().bottom()
        for name, (t, col, lab) in self._markers().items():
            loop = name.startswith("loop")
            d = abs(self.t2x(t) - pos.x())
            on_box = (pos.y() > bottom - HANDLE_H) if loop else (pos.y() < HANDLE_H + 4)
            if on_box:
                d = min(d, abs(self.t2x(t) + (-13 if name in ("end", "loop_end") else 13) - pos.x()))
            elif (pos.y() < HANDLE_H + 4) if loop else (pos.y() > bottom - HANDLE_H):
                d += 6                      # sull'etichetta dell'altro marcatore: vince quello
            # a parità (marcatori sovrapposti) vince il loop: IN e OUT si prendono dall'etichetta in alto
            if d < bd or (best is not None and d == bd and loop):
                best, bd = ("marker", name), d
        return best

    def mousePressEvent(self, e):
        if self.cue is None:
            return
        pos = e.position()
        if e.button() == Qt.MiddleButton:
            self.drag = ("pan", pos.x(), self.v0, self.v1, [True])
            return
        hit = self._hit(pos)
        if e.button() == Qt.RightButton:
            if hit and hit[0] in ("point", "opoint"):
                del (self.cue.curve if hit[0] == "point" else self._op_points())[hit[1]]
                self.changed.emit()
                self.update()
            elif hit and hit[0] == "umark":
                self._marker_menu(e, hit[1])
            elif hit and hit[0] == "kf":
                self._kf_menu(e, hit[1])
            else:
                self._menu(e, pos)
            return
        if e.button() == Qt.LeftButton:
            if hit and hit[0] == "kf":
                self.drag = ("kf", hit[1], float(hit[1].get("t", 0)), hit[2])
            elif hit and hit[0] == "oseg":
                ops = self._op_points()
                self.drag = ("oseg", hit[1], self.y2op(pos.y()), [float(ops[i][1]) for i in hit[1]])
                self._move_cursor(pos.x())
            elif hit and hit[0] == "seg":
                c = self.cue
                orig = [float(c.curve[i][1]) for i in hit[1]] if hit[1] else [float(c.level_db)]
                self.drag = ("seg", hit[1], self.y2db(pos.y()), orig)
                self._move_cursor(pos.x())          # il campo Volume mostra il valore sotto il mouse
            elif hit:
                self.drag = hit
            elif e.modifiers() & Qt.ControlModifier or pos.y() > HANDLE_H:
                if e.modifiers() & Qt.ControlModifier:
                    self._add_point(pos)
                else:
                    # clic o trascinamento: sposta il cursore (per scorrere: tasto centrale o Maiusc+rotella)
                    self.drag = ("cursor",)
                    self._move_cursor(pos.x())

    def mouseDoubleClickEvent(self, e):
        if self.cue is None or e.button() != Qt.LeftButton:
            return
        hit = self._hit(e.position())
        if hit and hit[0] == "umark":
            self.rename_marker(hit[1])
        elif hit and hit[0] in ("opacity", "oseg"):
            self.drag = None
            self._add_point(e.position(), "opacity")       # doppio clic sulla linea dell'opacità
        elif hit and hit[0] in ("level", "seg"):
            self.drag = None
            self._add_point(e.position(), "volume")
        elif not hit:
            # Maiusc+doppio clic: l'altro tipo di punto (volume ↔ opacità)
            mode = self.point_mode
            if e.modifiers() & Qt.ShiftModifier and self._video_mode():
                mode = "volume" if mode == "opacity" else "opacity"
            self._add_point(e.position(), mode)

    def _add_point(self, pos, mode=None):
        if (mode or self.point_mode) == "opacity" and self._video_mode():
            ops = self._op_points()
            ops.append([round(self.x2t(pos.x()), 3), round(self.y2op(pos.y()), 3)])
            ops.sort()
            for i, (t, v) in enumerate(ops):
                if abs(self.t2x(t) - pos.x()) < 1:
                    self.drag = ("opoint", i)
            self.changed.emit()
            self.update()
            return
        self.cue.curve.append([round(self.x2t(pos.x()), 3), self.y2pt(pos.y())])
        self.cue.curve.sort()
        for i, (t, db) in enumerate(self.cue.curve):
            if abs(self.t2x(t) - pos.x()) < 1:
                self.drag = ("point", i)
        self.changed.emit()
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.drag is None:
            for kx, ky, tip, _pt, _ft in getattr(self, "_kf_hits", []):
                if abs(kx - pos.x()) < 6 and abs(ky - pos.y()) < 6:
                    QToolTip.showText(e.globalPosition().toPoint(), tip, self)
                    break
            hit = self._hit(pos)
            self.setCursor(Qt.SizeHorCursor if hit and hit[0] in ("marker", "fade_in", "fade_out", "afade_in",
                                                                 "afade_out", "umark", "kf")
                           else Qt.PointingHandCursor if hit and hit[0] == "opoint"
                           else Qt.SizeVerCursor if hit and hit[0] in ("opacity", "level", "seg", "oseg")
                           else Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            return
        kind = self.drag[0]
        if kind == "cursor":
            self._move_cursor(pos.x())
            QToolTip.showText(e.globalPosition().toPoint(), fmt_time(self.cursor_t), self)
            return
        if kind == "pan":
            _, x0, v0, v1, moved = self.drag
            if abs(pos.x() - x0) > 3:
                moved[0] = True
            dt = (pos.x() - x0) / max(1, self.width()) * (v1 - v0)
            span = v1 - v0
            nv0 = max(0.0, min(v0 - dt, max(self.duration, 1) - span))
            self.v0, self.v1 = nv0, nv0 + span
            self.update()
            return
        if kind == "oseg":
            # tratto della linea arancione: i punti di opacità che lo definiscono si spostano insieme
            _k, idx, v0, orig = self.drag
            d = self.y2op(pos.y()) - v0
            ops = self._op_points()
            for i, o in zip(idx, orig):
                if i < len(ops):
                    ops[i] = [ops[i][0], round(max(0.0, min(1.0, o + d)), 3)]
            if idx and idx[0] < len(ops):
                QToolTip.showText(e.globalPosition().toPoint(), tr("Opacità {0:.0f}%", ops[idx[0]][1] * 100), self)
            self.changed.emit()
            self.update()
            return
        if kind == "seg":
            # tratto della linea rosa: i due punti che lo definiscono si spostano insieme, della stessa quantità
            _k, idx, db0, orig = self.drag
            d = self.y2db(pos.y()) - db0
            c = self.cue
            base = self._base_db()
            if idx:
                for i, o in zip(idx, orig):
                    if i < len(c.curve) and o > MIN_DB + 0.01:
                        c.curve[i] = [c.curve[i][0], round(max(MIN_DB - base, min(MAX_DB - base, o + d)), 1)]
                eff = base + float(c.curve[idx[0]][1]) if idx[0] < len(c.curve) else base
            else:
                lv = orig[0] + d if orig[0] > MIN_DB else MIN_DB + d
                c.level_db = MIN_DB if lv <= MIN_DB + 0.01 else round(max(MIN_DB, min(MAX_DB, lv)) * 2) / 2
                eff = c.level_db
            QToolTip.showText(e.globalPosition().toPoint(),
                              tr("volume −∞") if eff <= MIN_DB + 0.01 else tr("volume {0:+.1f} dB", eff), self)
            self.changed.emit()
            self.update()
            return
        t = round(self.x2t(pos.x()), 3)
        c = self.cue
        if kind in ("point", "opoint", "umark", "kf") and self.snap and not (e.modifiers() & Qt.AltModifier):
            t = self._snap(t, kind, self.drag)
        if kind == "umark":
            ms = self._user_markers()
            i = self.drag[1]
            if i < len(ms):
                ms[i]["t"] = t
                QToolTip.showText(e.globalPosition().toPoint(), f"{ms[i].get('name') or 'M'}  {fmt_time(t)}", self)
            self.changed.emit()
            self.update()
            return
        if kind == "kf":
            _k, pt, ct0, ft0 = self.drag
            pt["t"] = round(max(0.0, ct0 + (t - ft0)), 2)
            QToolTip.showText(e.globalPosition().toPoint(), tr("keyframe — cue {0}", fmt_time(pt["t"])), self)
            self.changed.emit()
            self.update()
            return
        if kind in ("afade_in", "afade_out"):        # fade dell'audio di una cue video (quadratini rosa)
            end = c.end if c.end > 0 else self.duration
            span = max(0.0, end - c.start)
            if kind == "afade_in":
                c.fade_in = round(max(0.0, min(t - c.start, span - float(c.fade_out or 0))), 2)
                tip = tr("Fade in audio {0:.2f} s", c.fade_in)
            else:
                c.fade_out = round(max(0.0, min(end - t, span - float(c.fade_in or 0))), 2)
                tip = tr("Fade out audio {0:.2f} s", c.fade_out)
            QToolTip.showText(e.globalPosition().toPoint(), tip, self)
            self.changed.emit()
            self.update()
            return
        if kind in ("fade_in", "fade_out", "opacity", "level"):
            end = c.end if c.end > 0 else self.duration
            span = max(0.0, end - c.start)
            # video con i fade dell'immagine separati: i quadratini arancioni regolano quelli
            vsep = self._video_mode() and not getattr(c, "fade_link", True)
            fin, fout = ("v_fade_in", "v_fade_out") if vsep else ("fade_in", "fade_out")
            if kind == "fade_in":
                setattr(c, fin, round(max(0.0, min(t - c.start, span - float(getattr(c, fout) or 0))), 2))
                tip = tr("Fade in {0:.2f} s", getattr(c, fin))
            elif kind == "fade_out":
                setattr(c, fout, round(max(0.0, min(end - t, span - float(getattr(c, fin) or 0))), 2))
                tip = tr("Fade out {0:.2f} s", getattr(c, fout))
            elif kind == "level":
                c.level_db = round(self.y2db(pos.y()) * 2) / 2
                tip = tr("Volume {0:+.1f} dB", c.level_db)
            else:
                r = self._plot()
                c.opacity = round(max(0.0, min(1.0, 1.0 - (pos.y() - r.top()) / max(1.0, r.height()))), 2)
                tip = tr("Opacità {0:.0f}%", c.opacity * 100)
            QToolTip.showText(e.globalPosition().toPoint(), tip, self)
            self.changed.emit()
            self.update()
            return
        if kind == "opoint":
            ops = self._op_points()
            i = self.drag[1]
            if i < len(ops):
                ops[i] = [t, round(self.y2op(pos.y()), 3)]
                QToolTip.showText(e.globalPosition().toPoint(),
                                  tr("{0}  opacità {1:.0f}%", fmt_time(t), ops[i][1] * 100), self)
            self.changed.emit()
            self.update()
            return
        if kind == "point":
            i = self.drag[1]
            if i < len(c.curve):
                c.curve[i] = [t, self.y2pt(pos.y())]
                self._move_cursor(self.t2x(t))     # il campo Volume segue il punto trascinato
                eff = self.y2db(pos.y())
                QToolTip.showText(e.globalPosition().toPoint(),
                                  tr("{0}  volume {1}", fmt_time(t), tr("−∞") if eff <= MIN_DB + 0.01
                                     else f"{eff:+.1f} dB"), self)
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
        d = self.drag
        self.drag = None
        if self._snap_x is not None:
            self._snap_x = None
            self.update()
        if not d:
            return
        if d[0] == "point":
            self.cue.curve.sort()
            self.changed.emit()
        elif d[0] == "opoint":
            self._op_points().sort()
            self.changed.emit()
        elif d[0] == "umark":
            self._user_markers().sort(key=lambda m: float(m.get("t", 0)))
            self.changed.emit()
        elif d[0] == "kf":
            self.cue.preset_points.sort(key=lambda p: float(p.get("t", 0)))
            self.changed.emit()
        elif d[0] == "pan" and not d[4][0]:
            self._move_cursor(e.position().x())

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

    def _marker_menu(self, e, i):
        ms = self._user_markers()
        if not (0 <= i < len(ms)):
            return
        mk = ms[i]
        c = self.cue
        t = float(mk.get("t", 0))
        m = QMenu(self)

        def act(label, fn):
            a = QAction(label, m)
            a.triggered.connect(lambda: (fn(), self.changed.emit(), self.update()))
            m.addAction(a)
        m.addAction(tr("Rinomina…"), lambda: self.rename_marker(i))
        act(tr("Imposta IN qui"), lambda: setattr(c, "start", t))
        act(tr("Imposta OUT qui"), lambda: setattr(c, "end", t))
        act(tr("Inizio loop qui"), lambda: (setattr(c, "loop", True), setattr(c, "loop_start", t)))
        act(tr("Fine loop qui"), lambda: (setattr(c, "loop", True), setattr(c, "loop_end", t)))
        m.addSeparator()
        act(tr("Elimina marker"), lambda: ms.remove(mk))
        m.exec(e.globalPosition().toPoint())

    def _kf_menu(self, e, pt):
        from . import keyframes as kfm
        m = QMenu(self)
        sub = m.addMenu(tr("Preset del keyframe"))

        def setp(n):
            pt["preset"] = n
            self.changed.emit()
            self.update()
        self._preset_menu(sub, pt.get("kind", "audio"), setp)

        def delete():
            if pt in self.cue.preset_points:
                self.cue.preset_points.remove(pt)
            self.changed.emit()
            self.update()
        m.addAction(tr("Elimina keyframe"), delete)
        m.setToolTip(kfm.label(pt.get("preset", "")))
        m.exec(e.globalPosition().toPoint())

    def _menu(self, e, pos):
        t = round(self.x2t(pos.x()), 3)
        c = self.cue
        m = QMenu(self)

        def act(label, fn):
            a = QAction(label, m)
            a.triggered.connect(lambda: (fn(), self.changed.emit(), self.update()))
            m.addAction(a)

        m.addAction(tr("Aggiungi marker qui (M)"), lambda: self.add_marker(t))
        kinds = ["audio"] if c.type == "audio" else ["visual", "audio"]
        from .keyframes import KIND_LABELS as KL
        for kind in kinds:
            sub = m.addMenu(tr("Aggiungi keyframe {0} qui", KL.get(kind, kind).lower()))
            self._preset_menu(sub, kind, lambda n, kind=kind: self.add_keyframe(kind, t, n))
        ms = self._user_markers()
        if ms:
            go = m.addMenu(tr("Vai al marker"))
            for mk in ms:
                a = QAction(f"{mk.get('name') or 'M'}  {fmt_time(float(mk.get('t', 0)))}", go)
                a.triggered.connect(lambda _=False, tt=float(mk.get("t", 0)): (
                    setattr(self, "cursor_t", tt), self.update(), self.cursorMoved.emit(tt)))
                go.addAction(a)
        m.addSeparator()

        act(tr("Imposta IN a {0}", fmt_time(t)), lambda: setattr(c, "start", t))
        act(tr("Imposta OUT a {0}", fmt_time(t)), lambda: setattr(c, "end", t))
        m.addSeparator()
        act(tr("Inizio loop qui"), lambda: (setattr(c, "loop", True), setattr(c, "loop_start", t)))
        act(tr("Fine loop qui"), lambda: (setattr(c, "loop", True), setattr(c, "loop_end", t)))
        m.addSeparator()
        act(tr("Aggiungi punto curva"), lambda: self._add_point(pos, "volume"))
        act(tr("Cancella curva di volume"), lambda: c.curve.clear())
        if self._video_mode():
            def add_op():
                self._add_point(pos, "opacity")
                self.drag = None
            m.addAction(tr("Aggiungi punto di opacità"), add_op)
            act(tr("Cancella curva di opacità"), lambda: self._op_points().clear())
        act(tr("Reimposta IN/OUT"), lambda: (setattr(c, "start", 0.0), setattr(c, "end", 0.0)))
        m.addSeparator()
        act(tr("Mostra tutto"), self.zoom_all)
        m.exec(e.globalPosition().toPoint())
