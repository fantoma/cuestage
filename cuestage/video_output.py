"""Uscita video: composizione dei layer e correzione geometrica a 4 angoli (corner pin)."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QTransform, QBrush
from PySide6.QtWidgets import QWidget

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
except Exception:  # pragma: no cover
    QOpenGLWidget = None


class Layer:
    __slots__ = ("key", "z", "order", "opacity", "image", "fit", "cue")

    def __init__(self, key, z, order, fit="fit", cue=None):
        self.key = key
        self.z = z
        self.order = order
        self.opacity = 1.0
        self.image = None
        self.fit = fit
        self.cue = cue  # i parametri di crop/posizione/scala sono letti dal vivo dalla cue


class Compositor:
    """Contiene i layer visivi attivi; la finestra di uscita e l'anteprima li disegnano."""

    def __init__(self):
        self.layers = {}
        self.corners = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
        self.grid = False
        self.out_size = (1920, 1080)  # risoluzione reale dell'uscita, in pixel
        self.counter = 0
        self.listeners = []

    def add(self, key, z, fit, cue=None):
        self.counter += 1
        lay = Layer(key, z, self.counter, fit, cue)
        self.layers[key] = lay
        return lay

    def remove(self, key, layer=None):
        # con `layer` si rimuove solo se è ancora lo stesso (una cue riavviata ne ha uno nuovo)
        if layer is None or self.layers.get(key) is layer:
            self.layers.pop(key, None)
        self.changed()

    def changed(self):
        for fn in self.listeners:
            fn()

    def sorted_layers(self):
        return sorted(self.layers.values(), key=lambda l: (l.z, l.order))


def fit_rect(iw, ih, W, H, mode):
    if iw <= 0 or ih <= 0:
        return QRectF(0, 0, W, H)
    if mode == "stretch":
        return QRectF(0, 0, W, H)
    if mode == "native":
        return QRectF((W - iw) / 2, (H - ih) / 2, iw, ih)
    sx, sy = W / iw, H / ih
    s = min(sx, sy) if mode == "fit" else max(sx, sy)
    w, h = iw * s, ih * s
    return QRectF((W - w) / 2, (H - h) / 2, w, h)


def layer_geometry(lay, img, W, H, out_size=None):
    """(rettangolo sorgente ritagliato, rettangolo di destinazione) per un layer.

    Crop in pixel del sorgente; spostamento in pixel dell'uscita reale `out_size`
    (W×H è l'area in cui si disegna: finestra di uscita o anteprima ridotta)."""
    iw, ih = img.width(), img.height()
    c = lay.cue
    cl = ct = cr = cb = 0.0
    ox = oy = 0.0
    sc = 1.0
    fit = lay.fit
    if c is not None:
        cl, ct, cr, cb = (float(c.crop_l), float(c.crop_t), float(c.crop_r), float(c.crop_b))
        ox, oy = float(c.off_x), float(c.off_y)
        sc = max(0.01, float(c.scale) / 100)
        fit = c.fit
    # almeno 1 pixel visibile
    cl, ct = max(0.0, min(cl, iw - 1)), max(0.0, min(ct, ih - 1))
    cr, cb = max(0.0, min(cr, iw - 1 - cl)), max(0.0, min(cb, ih - 1 - ct))
    src = QRectF(cl, ct, iw - cl - cr, ih - ct - cb)
    ow, oh = out_size if out_size else (W, H)
    if fit == "native":
        # dimensione originale = pixel reali dell'uscita
        dst = fit_rect(src.width() * W / ow, src.height() * H / oh, W, H, "native")
    else:
        dst = fit_rect(src.width(), src.height(), W, H, fit)
    cx = dst.center().x() + ox * W / max(1, ow)
    cy = dst.center().y() + oy * H / max(1, oh)
    w, h = dst.width() * sc, dst.height() * sc
    return src, QRectF(cx - w / 2, cy - h / 2, w, h)


def has_warp(cue):
    return cue is not None and any(a or b for a, b in (getattr(cue, "warp", None) or []))


def layer_quad(lay, dst, W, H, out_size=None):
    """I 4 angoli (alto-sx, alto-dx, basso-dx, basso-sx) del layer dopo la deformazione prospettica."""
    pts = [dst.topLeft(), dst.topRight(), dst.bottomRight(), dst.bottomLeft()]
    c = lay.cue
    if has_warp(c):
        ow, oh = out_size if out_size else (W, H)
        kx, ky = W / max(1, ow), H / max(1, oh)
        pts = [QPointF(p.x() + float(w[0]) * kx, p.y() + float(w[1]) * ky) for p, w in zip(pts, c.warp)]
    return QPolygonF(pts)


def draw_layer(p, lay, img, W, H, out_size):
    """Disegna un layer (crop, posizione, scala, prospettiva) nel sistema di coordinate corrente."""
    src, dst = layer_geometry(lay, img, W, H, out_size)
    if has_warp(lay.cue):
        rect_poly = QPolygonF([dst.topLeft(), dst.topRight(), dst.bottomRight(), dst.bottomLeft()])
        lt = QTransform()
        if QTransform.quadToQuad(rect_poly, layer_quad(lay, dst, W, H, out_size), lt):
            p.save()
            p.setTransform(lt, True)   # prima la prospettiva del layer, poi la correzione d'uscita
            p.drawImage(dst, img, src)
            p.restore()
            return
    p.drawImage(dst, img, src)


def corner_transform(W, H, corners, ox=0.0, oy=0.0, sx=1.0, sy=1.0):
    """Trasformazione proiettiva che porta il rettangolo WxH sui 4 angoli (normalizzati)."""
    src = QPolygonF([QPointF(0, 0), QPointF(W, 0), QPointF(W, H), QPointF(0, H)])
    dst = QPolygonF([QPointF(ox + c[0] * W * sx, oy + c[1] * H * sy) for c in corners])
    t = QTransform()
    if not QTransform.quadToQuad(src, dst, t):
        t = QTransform()
    return t


def draw_grid(p, W, H):
    pen = QPen(QColor(255, 255, 255), max(1.0, W / 960))
    p.setPen(pen)
    n = 8
    for i in range(n + 1):
        x = W * i / n
        y = H * i / n
        p.drawLine(QPointF(x, 0), QPointF(x, H))
        p.drawLine(QPointF(0, y), QPointF(W, y))
    p.setPen(QPen(QColor(255, 200, 0), max(2.0, W / 480)))
    p.drawLine(QPointF(0, 0), QPointF(W, H))
    p.drawLine(QPointF(W, 0), QPointF(0, H))
    r = min(W, H) * 0.35
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(W / 2, H / 2), r, r)
    f = QFont()
    f.setPixelSize(max(10, int(H / 14)))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(0, 220, 255))
    m = H / 40
    labels = ["1", "2", "3", "4"]
    pos = [(m, m), (W - m, m), (W - m, H - m), (m, H - m)]
    aligns = [Qt.AlignLeft | Qt.AlignTop, Qt.AlignRight | Qt.AlignTop,
              Qt.AlignRight | Qt.AlignBottom, Qt.AlignLeft | Qt.AlignBottom]
    for lab, (x, y), al in zip(labels, pos, aligns):
        rect = QRectF(x - W * 0.2 if al & Qt.AlignRight else x, y - H * 0.2 if al & Qt.AlignBottom else y,
                      W * 0.2, H * 0.2)
        p.drawText(rect, int(al), lab)


def render(p, comp, W, H, ox=0.0, oy=0.0, sx=1.0, sy=1.0, handles=False, active_corner=-1,
           layers=None):
    """Disegna l'uscita completa in un'area WxH (offset/scala per l'anteprima).
    `layers`: lista alternativa di layer (altrimenti quelli attivi nel compositore)."""
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.Antialiasing, True)
    cw, ch = W, H  # dimensione del "canvas" logico
    t = corner_transform(cw, ch, comp.corners, ox, oy, sx, sy)
    p.save()
    p.setTransform(t, True)
    if layers is None:
        layers = comp.sorted_layers()
    for lay in layers:
        img = lay.image
        if img is None or img.isNull() or lay.opacity <= 0.001:
            continue
        p.setOpacity(max(0.0, min(1.0, lay.opacity)))
        draw_layer(p, lay, img, cw, ch, comp.out_size)
    p.setOpacity(1.0)
    if comp.grid or handles:
        draw_grid(p, cw, ch)
    p.restore()
    if handles:
        pts = [QPointF(ox + c[0] * W * sx, oy + c[1] * H * sy) for c in comp.corners]
        p.setPen(QPen(QColor(0, 220, 255), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(QPolygonF(pts))
        for i, pt in enumerate(pts):
            col = QColor(255, 80, 60) if i == active_corner else QColor(0, 220, 255)
            p.setBrush(QBrush(col))
            p.drawEllipse(pt, 7, 7)


class _OutputMixin:
    cornersChanged = None  # definito nelle sottoclassi

    def _init_output(self, comp):
        self.comp = comp
        self.edit_mode = False
        self.drag = -1
        self.setWindowTitle("CueStage – Uscita video")
        self.setMouseTracking(True)
        self.resize(960, 540)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), Qt.black)
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        comp.listeners.append(self.update)

    def _paint(self, p):
        p.fillRect(self.rect(), Qt.black)
        render(p, self.comp, self.width(), self.height(), handles=self.edit_mode,
               active_corner=self.drag)

    def set_edit_mode(self, on):
        self.edit_mode = on
        self.setCursor(Qt.CrossCursor if on else (Qt.BlankCursor if self.isFullScreen() else Qt.ArrowCursor))
        self.update()

    def mousePressEvent(self, e):
        if not self.edit_mode:
            return
        pos = e.position()
        best, bd = -1, 1e9
        for i, c in enumerate(self.comp.corners):
            d = (c[0] * self.width() - pos.x()) ** 2 + (c[1] * self.height() - pos.y()) ** 2
            if d < bd:
                best, bd = i, d
        self.drag = best
        self.update()

    def mouseMoveEvent(self, e):
        if self.edit_mode and self.drag >= 0 and e.buttons() & Qt.LeftButton:
            pos = e.position()
            self.comp.corners[self.drag] = [pos.x() / max(1, self.width()), pos.y() / max(1, self.height())]
            self.cornersChanged.emit()
            self.comp.changed()

    def mouseReleaseEvent(self, e):
        self.drag = -1
        self.update()

    def place_on_screen(self, screen, fullscreen):
        from PySide6.QtWidgets import QApplication
        screens = QApplication.screens()
        if screen is None or not (0 <= screen < len(screens)):
            screen = len(screens) - 1  # di default l'ultimo schermo (proiettore)
        scr = screens[screen]
        self.setScreen(scr)
        if fullscreen:
            self.setGeometry(scr.geometry())
            self.showFullScreen()
            if not self.edit_mode:
                self.setCursor(Qt.BlankCursor)
        else:
            g = scr.availableGeometry()
            self.showNormal()
            self.setGeometry(g.x() + 40, g.y() + 40, min(960, g.width() - 80), min(540, g.height() - 80))
            self.setCursor(Qt.ArrowCursor)
        self.show()
        self.raise_()


class RasterOutput(QWidget, _OutputMixin):
    cornersChanged = Signal()

    def __init__(self, comp):
        super().__init__(None, Qt.Window)
        self._init_output(comp)

    def paintEvent(self, e):
        p = QPainter(self)
        self._paint(p)
        p.end()


if QOpenGLWidget is not None:
    class GLOutput(QOpenGLWidget, _OutputMixin):
        cornersChanged = Signal()

        def __init__(self, comp):
            super().__init__(None, Qt.Window)
            self._init_output(comp)

        def paintGL(self):
            p = QPainter(self)
            self._paint(p)
            p.end()
else:
    GLOutput = None


def opengl_available():
    """Verifica che sia possibile creare un contesto OpenGL (altrimenti si usa il rendering software)."""
    try:
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext
        ctx = QOpenGLContext()
        if not ctx.create():
            return False
        surf = QOffscreenSurface()
        surf.create()
        ok = ctx.makeCurrent(surf)
        ctx.doneCurrent()
        return bool(ok)
    except Exception:
        return False


def make_output(comp, opengl=True):
    if opengl and GLOutput is not None and opengl_available():
        try:
            return GLOutput(comp)
        except Exception:
            pass
    return RasterOutput(comp)


class _Size:
    """Sostituto di QImage con solo le dimensioni (per calcolare la geometria)."""

    def __init__(self, w, h):
        self._w, self._h = max(1, int(w)), max(1, int(h))

    def width(self):
        return self._w

    def height(self):
        return self._h


class OutputPreview(QWidget):
    """Anteprima ridotta dell'uscita; permette di spostare/scalare/ritagliare la cue selezionata."""
    edited = Signal()

    def __init__(self, comp, aspect_fn, parent=None):
        super().__init__(parent)
        self.comp = comp
        self.aspect_fn = aspect_fn
        self.selected_fn = lambda: None     # -> cue visiva selezionata o None
        self.size_fn = lambda cue: None     # -> (larghezza, altezza) del sorgente
        self.drag = None
        self.setMinimumSize(160, 90)
        self.setMouseTracking(True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(66)

    def _area(self):
        ar = self.aspect_fn() or (16 / 9)
        W, H = self.width() - 8, self.height() - 8
        if W / H > ar:
            w, h = H * ar, H
        else:
            w, h = W, W / ar
        return (self.width() - w) / 2, (self.height() - h) / 2, w, h

    def _selected(self):
        cue = self.selected_fn()
        if cue is None or not cue.is_visual():
            return None, None
        lay = self.comp.layers.get(cue.id)
        if lay is not None and lay.image is not None and not lay.image.isNull():
            size = _Size(lay.image.width(), lay.image.height())
        else:
            wh = self.size_fn(cue)
            if not wh:
                return cue, None
            size = _Size(*wh)
        return cue, size

    def _geom(self, cue, size, w, h):
        return layer_geometry(Layer(cue.id, 0, 0, cue.fit, cue), size, w, h, self.comp.out_size)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 30, 34))
        ox, oy, w, h = self._area()
        p.fillRect(QRectF(ox, oy, w, h), Qt.black)
        p.save()
        p.setClipRect(QRectF(ox, oy, w, h))
        render(p, self.comp, w, h, ox, oy)
        p.restore()
        cue, size = self._selected()
        if cue is not None and size is not None:
            _, dst = self._geom(cue, size, w, h)
            t = corner_transform(w, h, self.comp.corners, ox, oy)
            poly = t.map(layer_quad(Layer(cue.id, 0, 0, cue.fit, cue), dst, w, h, self.comp.out_size))
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(255, 180, 40), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(poly)
        p.end()

    def _to_canvas(self, pos):
        ox, oy, w, h = self._area()
        t = corner_transform(w, h, self.comp.corners, ox, oy)
        inv, ok = t.inverted()
        return (inv.map(pos) if ok else pos), w, h

    def mousePressEvent(self, e):
        cue, size = self._selected()
        if cue is None or size is None or e.button() != Qt.LeftButton:
            return
        pt, w, h = self._to_canvas(e.position())
        edge = None
        if e.modifiers() & Qt.ShiftModifier:
            _, dst = self._geom(cue, size, w, h)
            d = {"crop_l": abs(pt.x() - dst.left()), "crop_r": abs(pt.x() - dst.right()),
                 "crop_t": abs(pt.y() - dst.top()), "crop_b": abs(pt.y() - dst.bottom())}
            edge = min(d, key=d.get)
        self.drag = (cue, pt, edge, float(cue.off_x), float(cue.off_y),
                     {k: float(getattr(cue, k)) for k in ("crop_l", "crop_r", "crop_t", "crop_b")})
        self.setCursor(Qt.SizeAllCursor)

    def mouseMoveEvent(self, e):
        if not self.drag:
            return
        cue, start, edge, px, py, crops = self.drag
        pt, w, h = self._to_canvas(e.position())
        dx, dy = pt.x() - start.x(), pt.y() - start.y()
        if edge is None:
            ow, oh = self.comp.out_size
            cue.off_x = int(round(px + dx / w * ow))
            cue.off_y = int(round(py + dy / h * oh))
        else:
            cue2, size = self._selected()
            if size is None:
                return
            # pixel di sorgente rappresentati da un pixel dell'anteprima lungo quel lato
            for k, val in crops.items():
                setattr(cue, k, int(val))
            src, dst = self._geom(cue, size, w, h)
            if edge in ("crop_l", "crop_r"):
                delta = (dx if edge == "crop_l" else -dx) / max(1.0, dst.width()) * src.width()
                lim = size.width() - 1 - crops["crop_r" if edge == "crop_l" else "crop_l"]
            else:
                delta = (dy if edge == "crop_t" else -dy) / max(1.0, dst.height()) * src.height()
                lim = size.height() - 1 - crops["crop_b" if edge == "crop_t" else "crop_t"]
            setattr(cue, edge, int(round(max(0.0, min(lim, crops[edge] + delta)))))
        self.edited.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None
        self.setCursor(Qt.ArrowCursor)

    def wheelEvent(self, e):
        cue, size = self._selected()
        if cue is None:
            return
        f = 1.05 if e.angleDelta().y() > 0 else 1 / 1.05
        cue.scale = round(max(1.0, min(1000.0, float(cue.scale) * f)), 1)
        self.edited.emit()
        self.update()


class MappingEditor(QWidget):
    """Editor degli angoli in miniatura (dialogo geometria)."""
    changed = Signal()

    def __init__(self, comp, aspect_fn, parent=None):
        super().__init__(parent)
        self.comp = comp
        self.aspect_fn = aspect_fn
        self.active = 0
        self.dragging = False
        self.setMinimumSize(480, 300)
        self.setFocusPolicy(Qt.StrongFocus)

    def _area(self):
        ar = self.aspect_fn() or (16 / 9)
        m = 30
        W, H = self.width() - 2 * m, self.height() - 2 * m
        if W / H > ar:
            w, h = H * ar, H
        else:
            w, h = W, W / ar
        return (self.width() - w) / 2, (self.height() - h) / 2, w, h

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(24, 24, 28))
        ox, oy, w, h = self._area()
        p.setPen(QPen(QColor(90, 90, 100), 1, Qt.DashLine))
        p.drawRect(QRectF(ox, oy, w, h))
        render(p, self.comp, w, h, ox, oy, handles=True, active_corner=self.active)
        p.end()

    def mousePressEvent(self, e):
        ox, oy, w, h = self._area()
        pos = e.position()
        best, bd = 0, 1e9
        for i, c in enumerate(self.comp.corners):
            d = (ox + c[0] * w - pos.x()) ** 2 + (oy + c[1] * h - pos.y()) ** 2
            if d < bd:
                best, bd = i, d
        self.active = best
        self.dragging = True
        self.update()

    def mouseMoveEvent(self, e):
        if not self.dragging:
            return
        ox, oy, w, h = self._area()
        pos = e.position()
        self.comp.corners[self.active] = [(pos.x() - ox) / w, (pos.y() - oy) / h]
        self.comp.changed()
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        self.dragging = False

    def keyPressEvent(self, e):
        k = e.key()
        if Qt.Key_1 <= k <= Qt.Key_4:
            self.active = k - Qt.Key_1
            self.update()
            return
        step = 0.01 if e.modifiers() & Qt.ShiftModifier else 0.001
        dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(k, 0)
        dy = {Qt.Key_Up: -step, Qt.Key_Down: step}.get(k, 0)
        if dx or dy:
            c = self.comp.corners[self.active]
            c[0] += dx
            c[1] += dy
            self.comp.changed()
            self.changed.emit()
            self.update()
            return
        super().keyPressEvent(e)
