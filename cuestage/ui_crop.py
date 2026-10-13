"""Editor visivo del crop: fermo immagine del video (scelto con una piccola timeline) o immagine,
con il riquadro di ritaglio da trascinare. I valori sono in pixel del sorgente."""
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget)

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


class CropView(QWidget):
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
        self.setMinimumSize(300, 150)
        self.setMouseTracking(True)

    def set_source(self, cue, img, src_w, src_h):
        self.cue = cue
        self.img = img
        self.src_w, self.src_h = int(src_w or 0), int(src_h or 0)
        self.update()

    # -------------------------------------------------- geometria
    def _img_rect(self):
        if not self.src_w or not self.src_h:
            return None
        m = 12
        W, H = self.width() - 2 * m, self.height() - 2 * m
        s = min(W / self.src_w, H / self.src_h)
        w, h = self.src_w * s, self.src_h * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _disp(self):
        return self.state_fn() if self.state_fn else self.cue

    def _locked(self):
        return self.locked_fn() if self.locked_fn else None

    def _crop_rect(self, r):
        c = self._disp()
        k = r.width() / self.src_w
        return QRectF(r.left() + c.crop_l * k, r.top() + c.crop_t * k,
                      r.width() - (c.crop_l + c.crop_r) * k, r.height() - (c.crop_t + c.crop_b) * k)

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

    # -------------------------------------------------- disegno
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 21, 26))
        r = self._img_rect()
        if r is None or self.cue is None:
            p.setPen(QColor(140, 140, 150))
            p.drawText(self.rect(), Qt.AlignCenter, self.message or "Nessun file")
            p.end()
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.img is not None and not self.img.isNull():
            p.drawImage(r, self.img)
        else:
            p.fillRect(r, QColor(45, 45, 52))
            p.setPen(QColor(150, 150, 160))
            p.drawText(r, Qt.AlignCenter, self.message or "Caricamento fotogramma…")
        cr = self._crop_rect(r)
        # zona esclusa scurita
        outside = QPainterPath()
        outside.addRect(r)
        inner = QPainterPath()
        inner.addRect(cr)
        p.fillPath(outside.subtracted(inner), QColor(0, 0, 0, 165))
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 180, 40), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(cr)
        # terzi, utili per comporre
        p.setPen(QPen(QColor(255, 180, 40, 70), 1))
        for i in (1, 2):
            x = cr.left() + cr.width() * i / 3
            y = cr.top() + cr.height() * i / 3
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
        # maniglie
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 180, 40))
        for pt in (cr.topLeft(), cr.topRight(), cr.bottomLeft(), cr.bottomRight(),
                   QPointF(cr.center().x(), cr.top()), QPointF(cr.center().x(), cr.bottom()),
                   QPointF(cr.left(), cr.center().y()), QPointF(cr.right(), cr.center().y())):
            p.drawRect(QRectF(pt.x() - 3.5, pt.y() - 3.5, 7, 7))
        # dimensioni risultanti
        c = self._disp()
        w = int(round(self.src_w - c.crop_l - c.crop_r))
        h = int(round(self.src_h - c.crop_t - c.crop_b))
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        txt = f"{w} × {h} px"
        tr = QRectF(cr.left() + 4, cr.top() + 4, 120, 18)
        p.fillRect(tr, QColor(0, 0, 0, 150))
        p.setPen(QColor(255, 220, 150))
        p.drawText(tr, Qt.AlignCenter, txt)
        _draw_lock_banner(p, self.rect(), self._locked())
        p.end()

    # -------------------------------------------------- interazione
    def mousePressEvent(self, e):
        r = self._img_rect()
        if r is None or self.cue is None or e.button() != Qt.LeftButton or self._locked():
            return
        mode = self._hit(e.position(), self._crop_rect(r))
        if mode:
            c = self.cue
            self.drag = (mode, e.position(), (c.crop_l, c.crop_t, c.crop_r, c.crop_b))

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
            cur = {"l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "t": Qt.SizeVerCursor,
                   "b": Qt.SizeVerCursor, "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
                   "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor, "move": Qt.SizeAllCursor}
            self.setCursor(cur.get(mode, Qt.ArrowCursor))
            return
        mode, start, (l0, t0, r0, b0) = self.drag
        k = self.src_w / r.width()
        dx = int(round((pos.x() - start.x()) * k))
        dy = int(round((pos.y() - start.y()) * k))
        W, H = self.src_w, self.src_h
        l, t, rr, b = l0, t0, r0, b0
        if mode == "move":
            dx = max(-l0, min(dx, r0))
            dy = max(-t0, min(dy, b0))
            l, rr, t, b = l0 + dx, r0 - dx, t0 + dy, b0 - dy
        else:
            if "l" in mode:
                l = max(0, min(l0 + dx, W - r0 - 2))
            if "r" in mode:
                rr = max(0, min(r0 - dx, W - l0 - 2))
            if "t" in mode:
                t = max(0, min(t0 + dy, H - b0 - 2))
            if "b" in mode:
                b = max(0, min(b0 - dy, H - t0 - 2))
        c = self.cue
        c.crop_l, c.crop_t, c.crop_r, c.crop_b = int(l), int(t), int(rr), int(b)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None

    def mouseDoubleClickEvent(self, e):
        # doppio clic: nessun crop
        if self.cue is not None and not self._locked():
            c = self.cue
            c.crop_l = c.crop_t = c.crop_r = c.crop_b = 0
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
            p.drawText(QRectF(ox, oy, w, h), Qt.AlignCenter, "Nessun fotogramma")
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
        p.drawText(QRectF(ox + 4, oy + h - 16, w - 8, 14), Qt.AlignRight, f"uscita {ow}×{oh}")
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
        title = QLabel("Sinistra: crop sul sorgente · Destra: uscita (angoli = prospettiva, "
                       "interno = sposta, rotella = scala, doppio clic su un angolo = azzera)")
        title.setStyleSheet("color:#999; font-size:10px;")
        title.setWordWrap(True)
        top.addWidget(title, 1)
        self.blind = QCheckBox("Blind")
        self.blind.setToolTip("Blind: le modifiche fatte qui NON vengono mostrate sull'uscita video.\n"
                              "Senza blind, aprendo questa scheda l'uscita mostra il fermo immagine "
                              "con le impostazioni correnti (se la cue non è già in riproduzione).")
        self.blind.setChecked(bool(settings_fn().get("edit_blind", False)))
        self.blind.toggled.connect(self._blind_toggled)
        top.addWidget(self.blind)
        v.addLayout(top)

        self.view = CropView()
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
        v.addWidget(self.kbar)

        if cue.type == "video":
            self.debounce = QTimer(self)
            self.debounce.setSingleShot(True)
            self.debounce.setInterval(120)
            self.debounce.timeout.connect(self._request)
            loader.frameReady.connect(self._frame_ready)
            loader.infoReady.connect(self._info_ready)
            self.t = float(cue.start or 0.0)

        views = QHBoxLayout()
        for lab, wdg in (("Sorgente · crop", self.view), ("Uscita · rendering", self.out_view)):
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
            f"dissolvenza «{kf.label(na)}» → «{kf.label(nb)}» ({f * 100:.0f}%)"
        return (f"Keyframe a {fmt_time(self.cue_time())}: {what}. Sola visualizzazione: modifica i valori "
                f"base dove vale «base», oppure aggiorna il preset.")

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

    def _crop_changed(self):
        self.out_view.update()
        self.changed.emit()

    def reload(self):
        c = self.cue
        self._full = None
        if not c.file:
            self.view.message = "Nessun file"
            self.view.set_source(c, None, 0, 0)
            self._set_range()
            return
        if c.type == "image":
            img = self.image_fn(c.file)
            if img is None or img.isNull():
                self.view.message = "Immagine non leggibile"
                self.view.set_source(c, None, 0, 0)
            else:
                self.view.set_source(c, img, img.width(), img.height())
            self._set_range()
            return
        info = self.loader.get_info(c.file)
        if not info:
            self.view.message = "Analisi del video…"
            self.view.set_source(c, None, 0, 0)
            return
        if not info.get("has_video"):
            self.view.message = "Il file non contiene video"
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
