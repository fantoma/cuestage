"""Editor visivo del crop: fermo immagine del video (scelto con una piccola timeline) o immagine,
con il riquadro di ritaglio da trascinare. I valori sono in pixel del sorgente."""
from .i18n import tr
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget)

from . import keyframes as kf
from .media import fmt_time
from .video_output import Layer, corner_transform, layer_geometry, layer_quad, render

EDIT_KEY = "__edit__"   # layer temporaneo mostrato sull'uscita durante la modifica

EDGE = 7  # tolleranza in pixel per afferrare un bordo


def _draw_lock_banner(p, rect, msg):
    if not msg:
        return
    f = QFont()
    f.setPointSizeF(8.5)
    p.setFont(f)
    r = QRectF(rect.left() + 4, rect.top() + 4, rect.width() - 8, 48)
    p.fillRect(r, QColor(60, 40, 0, 215))
    p.setPen(QColor(255, 200, 90))
    p.drawText(r.adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.TextWordWrap, msg)


class _Geo:
    """Valori della cue usati per calcolare il rettangolo d'uscita (con dimensione/spostamento di prova)."""

    def __init__(self, cue, **kw):
        for k in ("crop_l", "crop_t", "crop_r", "crop_b", "off_x", "off_y", "scale", "scale_x", "scale_y", "fit"):
            setattr(self, k, kw.get(k, getattr(cue, k, 100.0 if k.startswith("scale") else 0)))


class _Img:
    def __init__(self, w, h):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


class CropView(QWidget):
    """Sorgente della cue. Trascinando i quadratini: dimensione (di serie), Ctrl = crop, Maiusc = simmetrico.
    Rotella = dimensione. La vista mostra l'immagine alla dimensione scelta (tratteggiato: il 100%)."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cue = None
        self.img = None
        self.src_w = self.src_h = 0
        self.drag = None
        self.message = ""
        self.state_fn = None    # -> cue (o vista) da disegnare al tempo scelto
        self.locked_fn = None   # -> messaggio se la modifica è bloccata, altrimenti None
        self.keep_aspect_fn = lambda: True    # True = la dimensione mantiene le proporzioni
        self.out_size_fn = None               # -> (w, h) dell'uscita in pixel
        self._frozen = None                   # durante un trascinamento: (k della vista, centro)
        self.setMinimumSize(300, 150)
        self.setMouseTracking(True)
        self.setToolTip(tr("Trascina i quadratini: dimensione · Alt+trascina: cambia le proporzioni · "
                           "Ctrl+trascina: crop · Maiusc: simmetrico · rotella: dimensione · doppio clic: togli il crop"))

    def set_source(self, cue, img, src_w, src_h):
        self.cue = cue
        self.img = img
        self.src_w, self.src_h = int(src_w or 0), int(src_h or 0)
        self.update()

    # -------------------------------------------------- geometria
    def _disp(self):
        return self.state_fn() if self.state_fn else self.cue

    def _locked(self):
        return self.locked_fn() if self.locked_fn else None

    def _factors(self, c=None):
        c = c or self._disp()
        s = max(0.01, float(c.scale) / 100)
        return (s * max(0.01, float(getattr(c, "scale_x", 100) or 100) / 100),
                s * max(0.01, float(getattr(c, "scale_y", 100) or 100) / 100))

    def _base(self):
        """(pixel della vista per pixel sorgente al 100%, centro della vista)."""
        m = 12
        W, H = self.width() - 2 * m, self.height() - 2 * m
        b = min(W / self.src_w, H / self.src_h)
        return b, QPointF(self.width() / 2, self.height() / 2)

    def _view_k(self):
        if self._frozen is not None:
            return self._frozen
        fx, fy = self._factors()
        b, _c = self._base()
        return b / max(1.0, fx, fy)          # tutto visibile: il 100% e l'immagine alla sua dimensione

    def _img_rect(self):
        if not self.src_w or not self.src_h:
            return None
        k = self._view_k()
        fx, fy = self._factors()
        w, h = self.src_w * k * fx, self.src_h * k * fy
        r = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        d = self.drag
        if d is not None and d.get("anchor") is not None:
            # durante il trascinamento resta fermo il punto di ancoraggio (lato opposto o centro)
            A, ax, ay = d["anchor"], d["ax"], d["ay"]
            r0 = d["r0"]
            fx0, fy0 = d["f0"]
            kx, ky = fx / fx0, fy / fy0
            left = A.x() - (A.x() - r0.left()) * kx if ax is not None else (self.width() - w) / 2
            top = A.y() - (A.y() - r0.top()) * ky if ay is not None else (self.height() - h) / 2
            if ax is None:
                left = r0.center().x() - w / 2
            if ay is None:
                top = r0.center().y() - h / 2
            r = QRectF(left, top, w, h)
        return r

    def _ref_rect(self):
        k = self._view_k()
        w, h = self.src_w * k, self.src_h * k
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _crop_rect(self, r):
        c = self._disp()
        kx, ky = r.width() / self.src_w, r.height() / self.src_h
        return QRectF(r.left() + c.crop_l * kx, r.top() + c.crop_t * ky,
                      r.width() - (c.crop_l + c.crop_r) * kx, r.height() - (c.crop_t + c.crop_b) * ky)

    def _hit(self, pos, cr):
        near_l = abs(pos.x() - cr.left()) < EDGE
        near_r = abs(pos.x() - cr.right()) < EDGE
        near_t = abs(pos.y() - cr.top()) < EDGE
        near_b = abs(pos.y() - cr.bottom()) < EDGE
        in_x = cr.left() - EDGE < pos.x() < cr.right() + EDGE
        in_y = cr.top() - EDGE < pos.y() < cr.bottom() + EDGE
        mode = ""
        if in_x and near_t:
            mode += "t"
        elif in_x and near_b:
            mode += "b"
        if in_y and near_l:
            mode += "l"
        elif in_y and near_r:
            mode += "r"
        if mode:
            return mode
        if cr.contains(pos):
            return "move"
        return None

    def _out_rect(self, **kw):
        """Rettangolo della cue sull'uscita (pixel reali) con i valori dati al posto di quelli della cue."""
        from .video_output import layer_geometry
        ow, oh = self.out_size_fn() if self.out_size_fn else (1920, 1080)
        ow, oh = max(1, int(ow or 1920)), max(1, int(oh or 1080))
        g = _Geo(self.cue, **kw)

        class _L:
            cue = g
            fit = g.fit
        return layer_geometry(_L, _Img(self.src_w, self.src_h), ow, oh, (ow, oh))[1]

    # -------------------------------------------------- disegno
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 21, 26))
        r = self._img_rect()
        if r is None or self.cue is None:
            p.setPen(QColor(140, 140, 150))
            p.drawText(self.rect(), Qt.AlignCenter, self.message or tr("Nessun file"))
            p.end()
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.img is not None and not self.img.isNull():
            p.drawImage(r, self.img)
        else:
            p.fillRect(r, QColor(45, 45, 52))
            p.setPen(QColor(150, 150, 160))
            p.drawText(r, Qt.AlignCenter, self.message or tr("Caricamento fotogramma…"))
        cr = self._crop_rect(r)
        outside = QPainterPath()
        outside.addRect(r)
        inner = QPainterPath()
        inner.addRect(cr)
        p.fillPath(outside.subtracted(inner), QColor(0, 0, 0, 165))
        p.setRenderHint(QPainter.Antialiasing)
        fx, fy = self._factors()
        if abs(fx - 1) > 1e-3 or abs(fy - 1) > 1e-3:          # riferimento: dimensione 100%
            p.setPen(QPen(QColor(150, 150, 160, 160), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self._ref_rect())
        crop_mode = self.drag is not None and self.drag.get("crop")
        col = QColor(255, 180, 40) if crop_mode or self.drag is None else QColor(80, 200, 255)
        p.setPen(QPen(col, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(cr)
        p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 70), 1))
        for i in (1, 2):
            x = cr.left() + cr.width() * i / 3
            y = cr.top() + cr.height() * i / 3
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        for pt in (cr.topLeft(), cr.topRight(), cr.bottomLeft(), cr.bottomRight(),
                   QPointF(cr.center().x(), cr.top()), QPointF(cr.center().x(), cr.bottom()),
                   QPointF(cr.left(), cr.center().y()), QPointF(cr.right(), cr.center().y())):
            p.drawRect(QRectF(pt.x() - 3.5, pt.y() - 3.5, 7, 7))
        c = self._disp()
        w = int(round(self.src_w - c.crop_l - c.crop_r))
        h = int(round(self.src_h - c.crop_t - c.crop_b))
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        sx, sy = float(getattr(c, "scale_x", 100) or 100), float(getattr(c, "scale_y", 100) or 100)
        size = f"{float(c.scale):.0f}%" if abs(sx - 100) < 0.05 and abs(sy - 100) < 0.05 else \
            f"{float(c.scale) * sx / 100:.0f}% × {float(c.scale) * sy / 100:.0f}%"
        txt = tr("{0} × {1} px · {2}", w, h, size)
        fm = p.fontMetrics()
        tr_rect = QRectF(cr.left() + 4, cr.top() + 4, fm.horizontalAdvance(txt) + 12, 18)
        p.fillRect(tr_rect, QColor(0, 0, 0, 150))
        p.setPen(QColor(255, 220, 150))
        p.drawText(tr_rect, Qt.AlignCenter, txt)
        _draw_lock_banner(p, self.rect(), self._locked())
        p.end()

    # -------------------------------------------------- interazione
    def mousePressEvent(self, e):
        r = self._img_rect()
        if r is None or self.cue is None or e.button() != Qt.LeftButton or self._locked():
            return
        cr = self._crop_rect(r)
        mode = self._hit(e.position(), cr)
        if not mode:
            return
        c = self.cue
        crop = bool(e.modifiers() & Qt.ControlModifier)
        if mode == "move" and not crop:
            return                        # l'interno sposta solo il crop (con Ctrl)
        self._frozen = self._view_k()     # la vista non si ridimensiona mentre si trascina
        self.drag = {"mode": mode, "start": e.position(), "crop": crop, "anchor": None,
                     "c0": (c.crop_l, c.crop_t, c.crop_r, c.crop_b),
                     "v0": (float(c.scale), float(getattr(c, "scale_x", 100) or 100),
                            float(getattr(c, "scale_y", 100) or 100), float(c.off_x), float(c.off_y)),
                     "r0": QRectF(r), "cr0": QRectF(cr), "f0": self._factors(c)}

    def _size_drag(self, pos, shift, alt=False):
        d = self.drag
        mode, start, cr0 = d["mode"], d["start"], d["cr0"]
        s0, sx0, sy0, ox0, oy0 = d["v0"]
        keep = bool(self.keep_aspect_fn()) and not alt     # Alt: cambia le proporzioni (deformazione)
        # punto fermo: lato opposto (o il centro con Maiusc); asse non trascinato: centro
        ax = ay = None
        if "l" in mode or "r" in mode:
            ax = cr0.center().x() if shift else (cr0.right() if "l" in mode else cr0.left())
        if "t" in mode or "b" in mode:
            ay = cr0.center().y() if shift else (cr0.bottom() if "t" in mode else cr0.top())
        A = QPointF(ax if ax is not None else cr0.center().x(), ay if ay is not None else cr0.center().y())

        def ratio(edge0, a, delta):
            base = edge0 - a
            if abs(base) < 1:
                return 1.0
            return max(0.02, (edge0 + delta - a) / base)
        rx = ry = None
        if ax is not None:
            rx = ratio(cr0.left() if "l" in mode else cr0.right(), ax, pos.x() - start.x())
        if ay is not None:
            ry = ratio(cr0.top() if "t" in mode else cr0.bottom(), ay, pos.y() - start.y())
        c = self.cue
        if keep:
            k = rx if ry is None else ry if rx is None else (rx if abs(rx - 1) >= abs(ry - 1) else ry)
            c.scale = round(max(1.0, min(1000.0, s0 * k)), 1)
            c.scale_x, c.scale_y = sx0, sy0
            if ax is None:
                ax = A.x()          # anche l'altro asse cresce: resta fermo il centro
            if ay is None:
                ay = A.y()
        else:
            c.scale = s0
            c.scale_x = round(max(1.0, min(1000.0, sx0 * (rx if rx is not None else 1.0))), 1)
            c.scale_y = round(max(1.0, min(1000.0, sy0 * (ry if ry is not None else 1.0))), 1)
        d["anchor"] = A
        d["ax"], d["ay"] = ax, ay
        # sull'uscita resta fermo lo stesso lato: si corregge lo spostamento
        if not shift:
            before = self._out_rect(scale=s0, scale_x=sx0, scale_y=sy0, off_x=ox0, off_y=oy0)
            after = self._out_rect(off_x=ox0, off_y=oy0)
            dx = dy = 0.0
            if "l" in mode:
                dx = before.right() - after.right()
            elif "r" in mode:
                dx = before.left() - after.left()
            if "t" in mode:
                dy = before.bottom() - after.bottom()
            elif "b" in mode:
                dy = before.top() - after.top()
            c.off_x, c.off_y = int(round(ox0 + dx)), int(round(oy0 + dy))
        else:
            c.off_x, c.off_y = int(ox0), int(oy0)

    def _crop_drag(self, pos, shift):
        d = self.drag
        mode, start = d["mode"], d["start"]
        l0, t0, r0, b0 = d["c0"]
        r = d["r0"]
        dx = int(round((pos.x() - start.x()) * self.src_w / r.width()))
        dy = int(round((pos.y() - start.y()) * self.src_h / r.height()))
        W, H = self.src_w, self.src_h
        l, t, rr, b = l0, t0, r0, b0
        if mode == "move":
            dx = max(-l0, min(dx, r0))
            dy = max(-t0, min(dy, b0))
            l, rr, t, b = l0 + dx, r0 - dx, t0 + dy, b0 - dy
        else:
            if "l" in mode:
                l = l0 + dx
                if shift:
                    rr = r0 + dx                 # simmetrico: il lato opposto si muove al contrario
            if "r" in mode:
                rr = r0 - dx
                if shift:
                    l = l0 - dx
            if "t" in mode:
                t = t0 + dy
                if shift:
                    b = b0 + dy
            if "b" in mode:
                b = b0 - dy
                if shift:
                    t = t0 - dy
            l, rr = max(0, l), max(0, rr)
            t, b = max(0, t), max(0, b)
            if l + rr > W - 2:
                if "l" in mode and not shift:
                    l = W - 2 - rr
                elif "r" in mode and not shift:
                    rr = W - 2 - l
                else:
                    l = rr = (W - 2) // 2
            if t + b > H - 2:
                if "t" in mode and not shift:
                    t = H - 2 - b
                elif "b" in mode and not shift:
                    b = H - 2 - t
                else:
                    t = b = (H - 2) // 2
        c = self.cue
        c.crop_l, c.crop_t, c.crop_r, c.crop_b = int(l), int(t), int(rr), int(b)

    def mouseMoveEvent(self, e):
        r = self._img_rect()
        if r is None or self.cue is None:
            return
        pos = e.position()
        if self.drag is None:
            if self._locked():
                self.setCursor(Qt.ForbiddenCursor)
                return
            mode = self._hit(pos, self._crop_rect(r))
            if mode == "move" and not (e.modifiers() & Qt.ControlModifier):
                mode = None
            cur = {"l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "t": Qt.SizeVerCursor,
                   "b": Qt.SizeVerCursor, "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
                   "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor, "move": Qt.SizeAllCursor}
            self.setCursor(cur.get(mode, Qt.ArrowCursor))
            return
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        if self.drag["crop"]:
            self._crop_drag(pos, shift)
        else:
            self._size_drag(pos, shift, bool(e.modifiers() & Qt.AltModifier))
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None
        self._frozen = None
        self.update()

    def mouseDoubleClickEvent(self, e):
        # doppio clic: nessun crop
        if e.button() == Qt.LeftButton and self.cue is not None and not self._locked():
            c = self.cue
            c.crop_l = c.crop_t = c.crop_r = c.crop_b = 0
            self.changed.emit()
            self.update()

    def wheelEvent(self, e):
        """Rotella: dimensione della cue (come nell'anteprima dell'uscita)."""
        if self.cue is None or self._img_rect() is None or self._locked():
            return
        f = 1.05 if e.angleDelta().y() > 0 else 1 / 1.05
        self.cue.scale = round(max(1.0, min(1000.0, float(self.cue.scale) * f)), 1)
        self.changed.emit()
        self.update()


class OutputFrameView(QWidget):
    """Fotogramma d'uscita renderizzato (crop, posizione, scala, prospettiva, correzione 4 angoli).

    Trascina gli angoli = deformazione prospettica · trascina l'interno = sposta · rotella = scala ·
    doppio clic su un angolo = azzera quell'angolo."""
    changed = Signal()

    def __init__(self, comp, cue, image_fn, parent=None):
        super().__init__(parent)
        self.comp = comp
        self.cue = cue
        self.image_fn = image_fn   # -> fermo immagine a piena risoluzione (o None)
        self.state_fn = lambda: cue
        self.locked_fn = lambda: None
        self.drag = None
        self.setMinimumSize(300, 150)
        self.setMouseTracking(True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(66)

    def _tick(self):
        if self.isVisible():
            self.update()

    def _area(self):
        ow, oh = self.comp.out_size
        ar = ow / oh if oh else 16 / 9
        m = 10
        W, H = self.width() - 2 * m, self.height() - 2 * m
        if W / H > ar:
            w, h = H * ar, H
        else:
            w, h = W, W / ar
        return (self.width() - w) / 2, (self.height() - h) / 2, w, h

    def _layer(self):
        """Layer di questa cue: fotogramma dal vivo se in riproduzione, altrimenti il fermo immagine."""
        live = self.comp.layers.get(self.cue.id)
        if live is not None and live.image is not None:
            # in riproduzione: fotogramma e valori dal vivo (compresi i cambi preset)
            lay = Layer(self.cue.id, live.z, 10 ** 9, live.fit, live.cue)
            lay.image, lay.opacity = live.image, live.opacity
            return lay
        img = self.image_fn()
        if img is None or img.isNull():
            return None
        st = self.state_fn()
        lay = Layer(self.cue.id, int(self.cue.layer), 10 ** 9, st.fit, st)
        lay.image = img
        lay.opacity = float(st.opacity)
        return lay

    def _geometry(self):
        lay = self._layer()
        if lay is None:
            return None, None, None
        ox, oy, w, h = self._area()
        _, dst = layer_geometry(lay, lay.image, w, h, self.comp.out_size)
        quad = layer_quad(lay, dst, w, h, self.comp.out_size)
        t = corner_transform(w, h, self.comp.corners, ox, oy)
        return lay, dst, (quad, t)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 21, 26))
        ox, oy, w, h = self._area()
        p.fillRect(QRectF(ox, oy, w, h), Qt.black)
        lay = self._layer()
        if lay is None:
            p.setPen(QColor(140, 140, 150))
            p.drawText(QRectF(ox, oy, w, h), Qt.AlignCenter, tr("Nessun fotogramma"))
            p.end()
            return
        # le altre cue attive restano visibili sotto/sopra, come sull'uscita reale
        others = [l for k, l in self.comp.layers.items() if k not in (self.cue.id, EDIT_KEY)]
        layers = sorted(others + [lay], key=lambda l: (l.z, l.order))
        p.save()
        p.setClipRect(QRectF(ox, oy, w, h))
        render(p, self.comp, w, h, ox, oy, layers=layers)
        p.restore()
        _, dst, (quad, t) = self._geometry()
        poly = t.map(quad)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(80, 200, 255), 1.2, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(poly)
        p.setPen(Qt.NoPen)
        for i in range(4):
            active = self.drag is not None and self.drag[0] == i
            p.setBrush(QColor(255, 90, 60) if active else QColor(80, 200, 255))
            p.drawEllipse(poly.at(i), 6, 6)
        ow, oh = self.comp.out_size
        p.setPen(QColor(150, 150, 160))
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85))
        p.setFont(f)
        p.drawText(QRectF(ox + 4, oy + h - 16, w - 8, 14), Qt.AlignRight, tr("uscita {0}×{1}", ow, oh))
        _draw_lock_banner(p, self.rect(), self.locked_fn())
        p.end()

    def _canvas(self, pos):
        ox, oy, w, h = self._area()
        inv, ok = corner_transform(w, h, self.comp.corners, ox, oy).inverted()
        return (inv.map(pos) if ok else pos), w, h

    def mousePressEvent(self, e):
        lay, dst, g = self._geometry()
        if lay is None or e.button() != Qt.LeftButton or self.locked_fn():
            return
        quad, t = g
        poly = t.map(quad)
        pos = e.position()
        for i in range(4):
            q = poly.at(i)
            if (q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2 < 12 ** 2:
                self.drag = (i, None)
                return
        if poly.containsPoint(pos, Qt.OddEvenFill):
            pt, w, h = self._canvas(pos)
            self.drag = ("move", (pt, int(self.cue.off_x), int(self.cue.off_y)))
            self.setCursor(Qt.SizeAllCursor)

    def mouseMoveEvent(self, e):
        if self.drag is None:
            lay, dst, g = self._geometry()
            if g is None:
                return
            poly = g[1].map(g[0])
            pos = e.position()
            near = any((poly.at(i).x() - pos.x()) ** 2 + (poly.at(i).y() - pos.y()) ** 2 < 144
                       for i in range(4))
            self.setCursor(Qt.CrossCursor if near else
                           Qt.OpenHandCursor if poly.containsPoint(pos, Qt.OddEvenFill) else Qt.ArrowCursor)
            return
        kind, data = self.drag
        pt, w, h = self._canvas(e.position())
        ow, oh = self.comp.out_size
        c = self.cue
        if kind == "move":
            start, x0, y0 = data
            c.off_x = int(round(x0 + (pt.x() - start.x()) * ow / w))
            c.off_y = int(round(y0 + (pt.y() - start.y()) * oh / h))
        else:
            lay = self._layer()
            if lay is None:
                return
            _, dst = layer_geometry(lay, lay.image, w, h, self.comp.out_size)
            base = [dst.topLeft(), dst.topRight(), dst.bottomRight(), dst.bottomLeft()][kind]
            warp = [list(x) for x in (c.warp or [[0, 0]] * 4)]
            warp[kind] = [int(round((pt.x() - base.x()) * ow / w)), int(round((pt.y() - base.y()) * oh / h))]
            c.warp = warp
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def mouseDoubleClickEvent(self, e):
        lay, dst, g = self._geometry()
        if g is None or self.locked_fn():
            return
        poly = g[1].map(g[0])
        pos = e.position()
        for i in range(4):
            q = poly.at(i)
            if (q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2 < 144:
                warp = [list(x) for x in self.cue.warp]
                warp[i] = [0, 0]
                self.cue.warp = warp
                self.changed.emit()
                self.update()
                return

    def wheelEvent(self, e):
        if self.locked_fn():
            return
        f = 1.05 if e.angleDelta().y() > 0 else 1 / 1.05
        self.cue.scale = round(max(1.0, min(1000.0, float(self.cue.scale) * f)), 1)
        self.changed.emit()
        self.update()


class CropPanel(QWidget):
    """Sorgente con crop | fotogramma d'uscita renderizzato; sopra, la timeline dei keyframe
    (per i video sceglie anche il fotogramma mostrato)."""
    changed = Signal()

    def __init__(self, cue, loader, image_fn, comp, settings_fn, parent=None):
        super().__init__(parent)
        from .ui_keyframes import KeyframeBar
        self.cue = cue
        self.loader = loader
        self.image_fn = image_fn
        self.comp = comp
        self.settings_fn = settings_fn
        self.duration = 0.0
        self.t = 0.0          # video: tempo del file · immagine: tempo della cue
        self._full = None     # fermo immagine a piena risoluzione (cache)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        title = QLabel(tr("Sinistra: quadratini = dimensione, Alt = proporzioni, Ctrl = crop, Maiusc = simmetrico, "
                          "rotella = dimensione · Destra: uscita (angoli = prospettiva, interno = sposta, rotella = "
                          "dimensione, doppio clic su un angolo = azzera)"))
        title.setStyleSheet("color:#999; font-size:10px;")
        title.setWordWrap(True)
        top.addWidget(title, 1)
        self.blind = QCheckBox(tr("Blind"))
        self.blind.setToolTip(tr("Blind: le modifiche fatte qui NON vengono mostrate sull'uscita video.\n"
                              "Senza blind, aprendo questa scheda l'uscita mostra il fermo immagine "
                              "con le impostazioni correnti (se la cue non è già in riproduzione)."))
        self.blind.setChecked(bool(settings_fn().get("edit_blind", False)))
        self.blind.toggled.connect(self._blind_toggled)
        self.keep = QCheckBox(tr("Mantieni proporzioni"))
        self.keep.setToolTip(tr("Acceso: i quadratini cambiano la dimensione mantenendo le proporzioni "
                                "(con Alt le cambiano).\nSpento: deformazione, larghezza e altezza cambiano "
                                "separatamente."))
        self.keep.setChecked(bool(settings_fn().get("edit_keep_aspect", True)))
        self.keep.toggled.connect(lambda on: settings_fn().__setitem__("edit_keep_aspect", bool(on)))
        top.addWidget(self.keep)
        self.ratio_btn = QToolButton()
        self.ratio_btn.setText(tr("⟲ Proporzioni"))
        self.ratio_btn.setToolTip(tr("Riporta le proporzioni originali (toglie la deformazione, la dimensione resta)"))
        self.ratio_btn.clicked.connect(self._reset_ratio)
        top.addWidget(self.ratio_btn)
        top.addWidget(self.blind)
        v.addLayout(top)

        self.view = CropView()
        self.view.keep_aspect_fn = self.keep.isChecked
        self.view.out_size_fn = lambda: self.comp.out_size
        self.view.state_fn = self.state
        self.view.locked_fn = self.locked
        self.view.changed.connect(self._crop_changed)
        self.out_view = OutputFrameView(comp, cue, self.still)
        self.out_view.state_fn = self.state
        self.out_view.locked_fn = self.locked
        self.out_view.changed.connect(self.changed)

        self.kbar = KeyframeBar(cue, duration_fn=lambda: self.duration, in_out=cue.type == "video")
        self.kbar.timeChanged.connect(self._time_moved)
        self.kbar.edited.connect(self._kf_edited)
        self.slider = self.kbar.strip          # compatibilità
        # la timeline dei keyframe non si mostra: il tempo arriva dal cursore della scheda «Tempi e loop»
        # (marker, keyframe e punti si gestiscono lì); resta come riferimento del tempo e dei preset in uso
        self.kbar.setParent(self)
        self.kbar.hide()

        if cue.type == "video":
            self.debounce = QTimer(self)
            self.debounce.setSingleShot(True)
            self.debounce.setInterval(120)
            self.debounce.timeout.connect(self._request)
            loader.frameReady.connect(self._frame_ready)
            loader.infoReady.connect(self._info_ready)
            self.t = float(cue.start or 0.0)

        views = QHBoxLayout()
        for lab, wdg in ((tr("Sorgente · crop"), self.view), (tr("Uscita · rendering"), self.out_view)):
            col = QVBoxLayout()
            l = QLabel(lab)
            l.setStyleSheet("color:#bbb; font-weight:bold; font-size:11px;")
            col.addWidget(l)
            col.addWidget(wdg, 1)
            views.addLayout(col, 1)
        v.addLayout(views, 1)
        self.reload()

    # ------------------------------------------------ tempo

    def cue_time(self):
        return self.kbar.cue_time()

    def _set_range(self):
        self.kbar.refresh_axis()
        if self.cue.type == "video":
            self.t = self.kbar.file_time()

    # ------------------------------------------------ stato al tempo scelto
    def state(self):
        from .engine import state_at
        return state_at(self.cue, self.cue_time())

    def locked(self):
        st = self.state()
        if st is self.cue:
            return None
        na, nb, f = getattr(st, "active", ("", "", 0))
        what = f"«{kf.label(na)}»" if (na == nb or f <= 0) else \
            tr("dissolvenza «{0}» → «{1}» ({2:.0f}%)", kf.label(na), kf.label(nb), f * 100)
        return (tr("Keyframe a {0}: {1}. Sola visualizzazione: modifica i valori base dove vale «base», oppure aggiorna il preset.", fmt_time(self.cue_time()), what))

    def _kf_edited(self):
        if self.cue.type != "video":
            self._set_range()
        self.view.update()
        self.out_view.update()
        self.changed.emit()

    # ------------------------------------------------ fermo immagine
    def still(self):
        """Fermo immagine alla risoluzione del sorgente (il crop è in pixel del sorgente)."""
        img = self.view.img
        if img is None or img.isNull():
            return None
        if self._full is None or self._full[0] is not img:
            full = img
            if self.view.src_w and (img.width(), img.height()) != (self.view.src_w, self.view.src_h):
                full = img.scaled(self.view.src_w, self.view.src_h, Qt.IgnoreAspectRatio,
                                  Qt.SmoothTransformation)
            self._full = (img, full)
        return self._full[1]

    def is_blind(self):
        return self.blind.isChecked()

    def _blind_toggled(self, on):
        self.settings_fn()["edit_blind"] = bool(on)

    def _reset_ratio(self):
        if self.locked():
            return
        self.cue.scale_x = self.cue.scale_y = 100.0
        self.view.update()
        self._crop_changed()

    def _crop_changed(self):
        self.out_view.update()
        self.changed.emit()

    def reload(self):
        c = self.cue
        self._full = None
        if not c.file:
            self.view.message = tr("Nessun file")
            self.view.set_source(c, None, 0, 0)
            self._set_range()
            return
        if c.type == "image":
            img = self.image_fn(c.file)
            if img is None or img.isNull():
                self.view.message = tr("Immagine non leggibile")
                self.view.set_source(c, None, 0, 0)
            else:
                self.view.set_source(c, img, img.width(), img.height())
            self._set_range()
            return
        info = self.loader.get_info(c.file)
        if not info:
            self.view.message = tr("Analisi del video…")
            self.view.set_source(c, None, 0, 0)
            return
        if not info.get("has_video"):
            self.view.message = tr("Il file non contiene video")
            self.view.set_source(c, None, 0, 0)
            return
        self.duration = float(info.get("duration") or 0.0)
        self._set_range()
        self.view.set_source(c, self.view.img, info["width"], info["height"])
        self._request()

    def _time_moved(self, t):
        self.t = t
        self.view.update()
        self.out_view.update()
        if self.cue.type == "video":
            self.debounce.start()

    def _request(self):
        if self.cue.file and self.cue.type == "video":
            self.loader.request_frame(self.cue.file, self.t)

    def _frame_ready(self, path, t, data):
        if path != self.cue.file or abs(t - round(self.t, 2)) > 0.001:
            return
        img = QImage.fromData(data)
        if not img.isNull():
            self.view.img = img
            self.view.update()
            self.out_view.update()

    def _info_ready(self, path):
        if path == self.cue.file and self.view.src_w == 0:
            self.reload()

    def refresh(self):
        self.kbar.reload()
        if self.cue.type != "video":
            self._set_range()
        self.view.update()
        self.out_view.update()
