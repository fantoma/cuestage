"""Uscita video su GPU (Qt Quick): i fotogrammi del decoder vanno alla scheda video senza essere convertiti dal
processore né disegnati con QPainter; crop, posizione, scala, rotazione, prospettiva e correzione a 4 angoli
diventano una matrice per ogni layer. Se Qt Quick non ha una GPU (renderer «software») l'uscita chiede di
tornare al motore classico (raster), che fa tutto con QPainter."""
import os

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QMatrix4x4, QPainter, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget

from .video_output import (
    FrameRef, _OutputMixin, corner_transform, has_warp, layer_geometry, layer_quad, layer_rotation, render,
)

QML_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")

try:
    from PySide6.QtQml import QQmlComponent
    from PySide6.QtQuick import QQuickImageProvider, QSGRendererInterface
    from PySide6.QtQuickWidgets import QQuickWidget
except Exception:                     # Qt Quick non disponibile: si usa solo il motore classico
    QQuickWidget = None


def quick_possible():
    """Qt Quick c'è e la piattaforma può avere una GPU (non «offscreen», «minimal», «vnc»)."""
    from PySide6.QtGui import QGuiApplication
    if QQuickWidget is None or os.environ.get("QT_QUICK_BACKEND", "").lower() == "software":
        return False
    return QGuiApplication.platformName() not in ("offscreen", "minimal", "vnc", "linuxfb")


def qt_matrix(t):
    """QTransform (vettori riga, proiettivo) → QMatrix4x4 per la proprietà transform di un Item."""
    return QMatrix4x4(t.m11(), t.m21(), 0.0, t.m31(),
                      t.m12(), t.m22(), 0.0, t.m32(),
                      0.0, 0.0, 1.0, 0.0,
                      t.m13(), t.m23(), 0.0, t.m33())


def layer_matrix(lay, img, W, H, out_size, corners):
    """(rettangolo sorgente dopo il crop, matrice) di un layer: il rettangolo va nel quadrilatero d'uscita."""
    src, dst = layer_geometry(lay, img, W, H, out_size)
    sx = dst.width() / max(1e-6, src.width())
    sy = dst.height() / max(1e-6, src.height())
    m = QTransform(sx, 0.0, 0.0, sy, dst.x(), dst.y())
    if has_warp(lay.cue) or layer_rotation(lay.cue):
        rect_poly = QPolygonF([dst.topLeft(), dst.topRight(), dst.bottomRight(), dst.bottomLeft()])
        lt = QTransform()
        if QTransform.quadToQuad(rect_poly, layer_quad(lay, dst, W, H, out_size), lt):
            m = m * lt
    m = m * corner_transform(W, H, corners)
    return src, m


if QQuickWidget is not None:
    class _Images(QQuickImageProvider):
        """Immagini e testi dei layer (QImage) per gli elementi Image della scena."""

        def __init__(self):
            super().__init__(QQuickImageProvider.Image)
            self.images = {}

        def requestImage(self, ident, size, requested):
            key = ident.rsplit("/", 1)[0]
            img = self.images.get(key)
            return img if img is not None else QImage(1, 1, QImage.Format_ARGB32_Premultiplied)

    class _Overlay(QWidget):
        """Griglia di prova e maniglie dei 4 angoli sopra la scena (in modifica della geometria)."""

        def __init__(self, out):
            super().__init__(out)
            self.out = out
            self.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.setAttribute(Qt.WA_NoSystemBackground)
            self.setAttribute(Qt.WA_TranslucentBackground)

        def paintEvent(self, _e):
            if not (self.out.edit_mode or self.out.comp.grid):
                return
            p = QPainter(self)
            render(p, self.out.comp, self.width(), self.height(), handles=self.out.edit_mode,
                   active_corner=self.out.drag, layers=[])
            p.end()

    class QuickOutput(_OutputMixin, QQuickWidget):
        """Finestra di uscita su GPU. Stesse funzioni della finestra classica (schermo, schermo intero, angoli)."""
        cornersChanged = Signal()
        fallbackRequested = Signal(str)       # niente GPU: tornare al motore classico (motivo)

        def __init__(self, comp):
            QQuickWidget.__init__(self, None)
            self.setWindowFlags(Qt.Window)
            self.setResizeMode(QQuickWidget.SizeRootObjectToView)
            self.setClearColor(Qt.black)
            self.images = _Images()
            self.engine().addImageProvider("cs", self.images)
            self.setSource(QUrl.fromLocalFile(os.path.join(QML_DIR, "output.qml")))
            self.root = self.rootObject()
            self.layer_comp = QQmlComponent(self.engine(), QUrl.fromLocalFile(os.path.join(QML_DIR, "layer.qml")))
            self.items = {}                   # chiave layer -> (elemento, sink video, ultimo fotogramma/immagine)
            self.gen = 0
            self.engine_name = "quick"
            self.api_checked = False
            self._init_output(comp)
            comp.listeners.remove(self.update)
            comp.listeners.append(self.sync)
            self.overlay = _Overlay(self)
            self.overlay.resize(self.size())
            self.sceneGraphError.connect(lambda _e, msg: self.fallbackRequested.emit(msg))
            qw = self.quickWindow()
            qw.beforeRendering.connect(self._t_start, Qt.DirectConnection)
            qw.afterRendering.connect(self._t_end, Qt.DirectConnection)
            self._t0 = None
            if self.status() != QQuickWidget.Ready or self.layer_comp.isError():
                msg = "; ".join(e.toString() for e in list(self.errors()) + list(self.layer_comp.errors()))
                QTimer.singleShot(0, lambda: self.fallbackRequested.emit(msg or "QML"))

        # ---- diagnostica (stessi valori della finestra classica)
        def _t_start(self):
            import time as _t
            self._t0 = _t.perf_counter()
            if "_gpu" not in self.__dict__:          # thread di rendering: il contesto OpenGL della scena è corrente
                from PySide6.QtGui import QOpenGLContext
                from .video_output import gl_strings, gpu_record
                ctx = QOpenGLContext.currentContext()
                self._gpu = gpu_record(gl_strings(ctx), self.api_name()) if ctx is not None else None

        def gpu_used(self):
            """Scheda video della scena (None con Vulkan / Metal / Direct3D: si usa quella vista da OpenGL)."""
            return self.__dict__.get("_gpu")

        def _t_end(self):
            import time as _t
            if self._t0 is None:
                return
            dt = _t.perf_counter() - self._t0
            st = self.__dict__.setdefault("_diag", {"avg": 0.0, "max": 0.0, "n": 0, "total": 0,
                                                    "t0": _t.monotonic(), "fps": 0.0})
            st["avg"] = dt if st["total"] == 0 else st["avg"] * 0.9 + dt * 0.1
            st["max"] = max(st["max"], dt)
            st["n"] += 1
            st["total"] += 1
            now = _t.monotonic()
            if now - st["t0"] >= 1.0:
                st["fps"] = st["n"] / (now - st["t0"])
                st["n"], st["t0"] = 0, now

        def graphics_api(self):
            try:
                return self.quickWindow().rendererInterface().graphicsApi()
            except Exception:
                return None

        def api_name(self):
            api = self.graphics_api()
            return getattr(api, "name", str(api)) if api is not None else "?"

        # ---- eventi della finestra
        def resizeEvent(self, e):
            QQuickWidget.resizeEvent(self, e)
            self.overlay.resize(self.size())
            self.sync()

        def update(self):
            self.overlay.update()
            self.sync()

        def set_edit_mode(self, on):
            _OutputMixin.set_edit_mode(self, on)
            self.overlay.update()

        def mousePressEvent(self, e):
            _OutputMixin.mousePressEvent(self, e)
            self.overlay.update()

        def mouseMoveEvent(self, e):
            _OutputMixin.mouseMoveEvent(self, e)
            self.overlay.update()

        def mouseReleaseEvent(self, e):
            _OutputMixin.mouseReleaseEvent(self, e)
            self.overlay.update()

        # ---- scena
        def _item(self, key):
            it = self.items.get(key)
            if it is None:
                obj = self.layer_comp.create()
                if obj is None:
                    self.fallbackRequested.emit("layer.qml")
                    return None
                obj.setParentItem(self.root)
                obj.setParent(self.root)
                vo = obj.findChild(QObject, "vo")
                img = obj.findChild(QObject, "img")
                sink = vo.property("videoSink") if vo is not None else None
                it = self.items[key] = {"obj": obj, "sink": sink, "img": img, "last": None}
            return it

        def sync(self):
            """Porta la scena allo stato del compositore (chiamata a ogni cambiamento dei layer)."""
            if not self.isVisible():
                return
            if not self.api_checked and self.graphics_api() is not None:
                self.api_checked = True
                if self.graphics_api() == QSGRendererInterface.Software:
                    self.fallbackRequested.emit("Qt Quick: renderer software (nessuna GPU)")
                    return
            comp = self.comp
            W, H = max(1, self.width()), max(1, self.height())
            seen = set()
            for lay in comp.sorted_layers():
                img = lay.image
                if img is None or img.isNull():
                    continue
                it = self._item(lay.key)
                if it is None:
                    return
                seen.add(lay.key)
                obj = it["obj"]
                src, m = layer_matrix(lay, img, W, H, comp.out_size, comp.corners)
                video = isinstance(img, FrameRef)
                obj.setProperty("isVideo", video)
                obj.setProperty("srcX", src.x())
                obj.setProperty("srcY", src.y())
                obj.setProperty("fullW", float(img.width()))
                obj.setProperty("fullH", float(img.height()))
                obj.setProperty("width", src.width())
                obj.setProperty("height", src.height())
                obj.setProperty("m", qt_matrix(m))
                obj.setProperty("opacity", max(0.0, min(1.0, float(lay.opacity))))
                obj.setProperty("visible", lay.opacity > 0.001)
                obj.setProperty("z", float(lay.z) * 1e6 + float(lay.order % 1000000))
                if it["last"] is not img:
                    it["last"] = img
                    if video:
                        if it["sink"] is not None:
                            it["sink"].setVideoFrame(img.frame)
                    else:
                        self.gen += 1
                        self.images.images[lay.key] = img
                        it["img"].setProperty("source", f"image://cs/{lay.key}/{self.gen}")
            for key in [k for k in self.items if k not in seen]:
                it = self.items.pop(key)
                self.images.images.pop(key, None)
                it["obj"].setProperty("visible", False)
                it["obj"].deleteLater()
            self.overlay.update()

        def place_on_screen(self, screen, fullscreen):
            _OutputMixin.place_on_screen(self, screen, fullscreen)
            QTimer.singleShot(0, self.sync)
else:
    QuickOutput = None
