"""Marcatori di una cue in un unico punto: IN/OUT, loop, fade, curva di volume, keyframe.

Due sistemi di riferimento:
- tempo del FILE (forma d'onda, timeline dei keyframe): ogni posizione del file compare una volta;
- tempo della CUE (barre di avanzamento): 0 = avvio, il loop è «srotolato» con le sue ripetizioni.
Le viste chiedono qui i marcatori e li disegnano con paint_bar() o con il proprio stile.
"""
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

COLORS = {
    "in": QColor(60, 210, 110), "out": QColor(235, 70, 70), "loop": QColor(240, 190, 40),
    "fade": QColor(235, 235, 240), "visual": QColor(255, 170, 40), "audio": QColor(80, 200, 255),
    "curve": QColor(255, 140, 220),
}
KIND_LABELS = {"in": "IN", "out": "OUT", "loop": "loop", "fade": "fade", "visual": "keyframe immagine",
               "audio": "keyframe audio", "curve": "punto curva volume"}


@dataclass
class Mark:
    t: float
    kind: str
    label: str = ""


@dataclass
class Axis:
    """Asse temporale di una cue. first = durata del primo passaggio (IN→OUT);
    total = durata in riproduzione (None = infinita); loop = (inizio, fine) nel tempo della cue."""
    offset: float = 0.0          # tempo del file all'avvio (IN)
    first: float = 0.0
    total: float = None
    loop: tuple = None
    loops: int = 0               # ripetizioni (0 = infinite)
    fade_in: float = 0.0
    fade_out: float = 0.0
    regions: list = field(default_factory=list)   # [(t0, t1)] regioni di loop nel tempo della cue

    @property
    def length(self):
        """Lunghezza dell'asse disegnato: la durata, o il primo passaggio se il loop è infinito."""
        return self.total if self.total else self.first


def cue_axis(cue, duration):
    """duration = durata del file (audio/video), ignorata per le immagini."""
    if cue.type in ("audio", "video"):
        dur = float(duration or 0)
        if dur <= 0:
            return None
        s = max(0.0, float(cue.start or 0))
        e = float(cue.end) if cue.end and cue.end > 0 else dur
        e = min(e, dur)
        if e <= s:
            return None
        ax = Axis(offset=s, first=e - s, fade_in=float(cue.fade_in or 0), fade_out=float(cue.fade_out or 0))
        if cue.loop:
            ls = float(cue.loop_start) if cue.loop_start and cue.loop_start > 0 else s
            le = float(cue.loop_end) if cue.loop_end and cue.loop_end > 0 else e
            ls, le = max(s, min(ls, e)), max(s, min(le, e))
            if le - ls > 0.01:
                ax.loop = (ls - s, le - s)
                ax.loops = int(cue.loop_count or 0)
        if ax.loop:
            L = ax.loop[1] - ax.loop[0]
            if ax.loops > 0:
                ax.total = ax.first + (ax.loops - 1) * L
                ax.regions = [ax.loop] + [(ax.loop[1] + (k - 1) * L, ax.loop[1] + k * L)
                                          for k in range(1, ax.loops)]
            else:
                ax.total = None
                ax.regions = [ax.loop]
        else:
            ax.total = ax.first
        return ax
    if cue.type == "image":
        d = float(cue.duration or 0)
        kf_last = max([float(p.get("t", 0)) for p in (cue.preset_points or [])] or [0])
        return Axis(offset=0.0, first=d if d > 0 else max(10.0, kf_last + 5), total=d if d > 0 else None,
                    fade_in=float(cue.fade_in or 0), fade_out=float(cue.fade_out or 0))
    return None


def file_to_cue_times(ax, ft):
    """Tempi della cue in cui viene suonata la posizione ft del file (più d'uno dentro un loop)."""
    t = ft - ax.offset
    if t < -1e-9 or t > ax.first + 1e-9:
        return []
    if not ax.loop:
        return [t]
    ls, le = ax.loop
    L = le - ls
    if t < ls:
        return [t]
    if t <= le:
        n = ax.loops if ax.loops > 0 else 1
        return [t + k * L for k in range(n)]
    if ax.loops > 0:
        return [t + (ax.loops - 1) * L]
    return []     # dopo un loop infinito non si arriva mai


def cue_to_file(ax, t):
    """(tempo del file, n. di ripetizione del loop o 0) per un tempo della cue; None se oltre la fine."""
    if t < 0:
        return None
    if not ax.loop or t <= ax.loop[1]:
        return (ax.offset + t, 0) if t <= ax.first + 1e-9 else None
    ls, le = ax.loop
    L = le - ls
    if ax.loops <= 0 or t < le + (ax.loops - 1) * L - 1e-9:
        k = int((t - le) // L) + 1
        return ax.offset + ls + ((t - le) % L), k
    rest = t - (ax.loops - 1) * L
    return (ax.offset + rest, 0) if rest <= ax.first + 1e-9 else None


def unrolled_regions(ax, upto):
    """Regioni di loop nel tempo della cue fino a «upto» (con loop infinito: le ripetizioni visibili)."""
    if not ax.loop:
        return []
    if ax.loops > 0:
        return list(ax.regions)
    ls, le = ax.loop
    L = le - ls
    out = [ax.loop]
    k = 1
    while le + (k - 1) * L < upto and k < 1000:
        out.append((le + (k - 1) * L, le + k * L))
        k += 1
    return out


def display_pos(ax, elapsed):
    """Posizione da mostrare sull'asse (con loop infinito il cursore resta dentro la regione di loop)."""
    if ax.total is None and ax.loop and elapsed > ax.loop[1]:
        ls, le = ax.loop
        return ls + ((elapsed - ls) % (le - ls))
    return min(elapsed, ax.length)


def cue_marks(cue, ax):
    """Marcatori nel tempo della cue, per le barre di avanzamento."""
    from . import keyframes as kf
    out = []
    if ax.fade_in > 0:
        out.append(Mark(min(ax.fade_in, ax.length), "fade", f"fine fade in ({ax.fade_in:.1f} s)"))
    if ax.fade_out > 0 and ax.total:
        out.append(Mark(max(0.0, ax.total - ax.fade_out), "fade", f"inizio fade out ({ax.fade_out:.1f} s)"))
    for i, (a, b) in enumerate(ax.regions):
        out.append(Mark(a, "loop", "inizio loop" + (f" (ripetizione {i})" if i else "")))
        out.append(Mark(b, "loop", "fine loop"))
    for ft, db in (cue.curve or []) if cue.type in ("audio", "video") else []:
        for t in file_to_cue_times(ax, float(ft)):
            out.append(Mark(t, "curve", f"curva {float(db):+.1f} dB"))
    for p in (cue.preset_points or []):
        try:
            t = float(p.get("t", 0))
        except (TypeError, ValueError, AttributeError):
            continue
        if t <= ax.length + 1e-6:
            out.append(Mark(t, p.get("kind", "visual"), f"{kf.KIND_LABELS.get(p.get('kind'), '')}: "
                                                        f"{kf.label(p.get('preset', ''))}"))
    return out


# ------------------------------------------------------------------ disegno

def diamond(p, x, y, d, color, hollow=False):
    p.setPen(QPen(QColor(0, 0, 0, 160), 0.8))
    p.setBrush(QColor(30, 30, 36) if hollow else color)
    if hollow:
        p.setPen(QPen(color, 1.2))
    p.drawPolygon(QPolygonF([QPointF(x, y - d), QPointF(x + d, y), QPointF(x, y + d), QPointF(x - d, y)]))


def paint_bar(p, rect, ax, marks, pos, fill, show_text=None):
    """Barra di avanzamento con tutti i marcatori. pos = tempo della cue da mostrare."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    L = max(1e-6, ax.length)

    def X(t):
        return rect.left() + max(0.0, min(1.0, t / L)) * rect.width()

    p.setPen(Qt.NoPen)
    p.setBrush(QColor(40, 42, 50))
    p.drawRoundedRect(rect, 3, 3)
    # regioni di loop
    for a, b in ax.regions:
        c = QColor(COLORS["loop"])
        c.setAlpha(55)
        p.fillRect(QRectF(X(a), rect.top(), max(1.0, X(b) - X(a)), rect.height()), c)
    # avanzamento
    x = X(pos)
    if x > rect.left():
        rr = QRectF(rect.left(), rect.top(), x - rect.left(), rect.height())
        p.setBrush(fill)
        p.drawRoundedRect(rr, 3, 3)
    # rampe dei fade: ombreggiato il volume «mancante», con la diagonale del fade
    h = rect.height()
    ramps = []
    if ax.fade_in > 0:
        ramps.append((X(0.0), X(ax.fade_in), True))
    if ax.fade_out > 0 and ax.total:
        ramps.append((X(ax.total - ax.fade_out), X(ax.total), False))
    for xa, xb, up in ramps:
        if up:
            poly = QPolygonF([QPointF(xa, rect.top()), QPointF(xb, rect.top()), QPointF(xa, rect.bottom())])
            line = (QPointF(xa, rect.bottom()), QPointF(xb, rect.top()))
        else:
            poly = QPolygonF([QPointF(xa, rect.top()), QPointF(xb, rect.top()), QPointF(xb, rect.bottom())])
            line = (QPointF(xa, rect.top()), QPointF(xb, rect.bottom()))
        c = QColor(0, 0, 0, 90)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(poly)
        p.setPen(QPen(COLORS["fade"], 1))
        p.drawLine(*line)
    # testo (sotto ai marcatori, che restano tutti visibili)
    if show_text:
        tw = p.fontMetrics().horizontalAdvance(show_text) + 10
        plate = QRectF(rect.center().x() - tw / 2, rect.top() + 1, tw, rect.height() - 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawRoundedRect(plate, 3, 3)
        p.setPen(QColor(255, 255, 255))
        p.drawText(plate, Qt.AlignCenter, show_text)
    # marcatori
    for m in marks:
        mx = X(m.t)
        col = COLORS.get(m.kind, QColor(200, 200, 200))
        if m.kind in ("loop", "fade"):
            p.setPen(QPen(col, 1.2, Qt.SolidLine if m.kind == "loop" else Qt.DashLine))
            p.drawLine(QPointF(mx, rect.top()), QPointF(mx, rect.bottom()))
        elif m.kind == "curve":
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(mx, rect.bottom() - 2), 1.8, 1.8)
        elif m.kind in ("visual", "audio"):
            yy = rect.top() + (2.5 if m.kind == "visual" else h - 2.5)
            diamond(p, mx, yy, min(4.0, h / 3), col, hollow=m.label.endswith(": base"))
    # cursore
    if pos > 0:
        p.setPen(QPen(QColor(255, 255, 255), 1.5))
        p.drawLine(QPointF(x, rect.top() - 1), QPointF(x, rect.bottom() + 1))
    if ax.total is None:
        p.setPen(COLORS["loop"])
        p.drawText(QRectF(rect.right() - 14, rect.top(), 13, h), Qt.AlignCenter, "∞")
    p.restore()


def marks_tooltip(marks, ax, t_hover, width_px):
    """Testo per il marcatore più vicino alla posizione (in tempo della cue) sotto il mouse."""
    tol = ax.length / max(1.0, width_px) * 5
    near = [m for m in marks if abs(m.t - t_hover) <= tol]
    from .media import fmt_time
    return "\n".join(f"{fmt_time(m.t)}  {m.label or KIND_LABELS.get(m.kind, m.kind)}" for m in near)
