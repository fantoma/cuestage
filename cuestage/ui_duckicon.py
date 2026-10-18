"""Icona del microfono nelle barre di avanzamento: verde = la cue ha il ducking, rossa = il ducking interviene,
grigia = la cue ha il ducking ma il ducking dello show è spento o l'ingresso non è disponibile."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

DUCK_COLORS = {"duck": QColor(235, 60, 50), "on": QColor(70, 220, 110), "idle": QColor(150, 150, 160)}
DUCK_TYPES = ("audio", "video", "group", "mixer")


def duck_state(inst, ctrl):
    """None (nessun ducking) · "idle" · "on" · "duck" (sta intervenendo)."""
    try:
        if inst is None or inst.cue.type not in DUCK_TYPES or inst.duck_db <= 0:
            return None
    except Exception:
        return None
    d = ctrl.audio.duck
    if inst.cue.type == "mixer":          # fader agganciati: basta che l'ingresso sia in ascolto
        if not d.listening:
            return "idle"
        return "duck" if d.env > 0.02 else "on"
    if not d.enabled:
        return "idle"
    return "duck" if d.ducking else "on"


def paint_mic(p, r, color):
    """Microfono stilizzato dentro il rettangolo r."""
    s = min(r.width(), r.height())
    x0 = r.center().x() - s / 2
    y0 = r.center().y() - s / 2
    p.save()
    p.setBrush(Qt.NoBrush)
    # sfondo scuro per leggerlo sopra qualsiasi barra
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 130))
    p.drawRoundedRect(QRectF(x0, y0, s, s), s * 0.22, s * 0.22)
    p.setBrush(color)
    cap = QRectF(x0 + s * 0.36, y0 + s * 0.12, s * 0.28, s * 0.46)
    p.drawRoundedRect(cap, s * 0.14, s * 0.14)
    pen = QPen(color, max(1.2, s * 0.08))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    arc = QRectF(x0 + s * 0.25, y0 + s * 0.22, s * 0.5, s * 0.5)
    path.arcMoveTo(arc, 180)
    path.arcTo(arc, 180, 180)
    p.drawPath(path)
    p.drawLine(QPointF(x0 + s * 0.5, y0 + s * 0.72), QPointF(x0 + s * 0.5, y0 + s * 0.84))
    p.drawLine(QPointF(x0 + s * 0.36, y0 + s * 0.86), QPointF(x0 + s * 0.64, y0 + s * 0.86))
    p.restore()


def paint_duck(p, bar, inst, ctrl, right=True):
    """Disegna l'icona all'estremità della barra (se la cue ha il ducking). Restituisce la larghezza usata."""
    st = duck_state(inst, ctrl)
    if st is None:
        return 0
    s = max(10.0, bar.height() - 2)
    x = bar.right() - s - 1 if right else bar.left() + 1
    paint_mic(p, QRectF(x, bar.center().y() - s / 2, s, s), DUCK_COLORS[st])
    return s + 2


def duck_tooltip(inst, ctrl):
    st = duck_state(inst, ctrl)
    if st is None:
        return ""
    db = inst.duck_db
    return {"duck": f"Ducking: sta intervenendo (−{db:.1f} dB)",
            "on": f"Ducking: −{db:.1f} dB quando l'ingresso supera la soglia",
            "idle": f"Ducking −{db:.1f} dB impostato, ma il ducking è spento (Impostazioni › Ducking…)"}[st]


def paint_pfl(p, bar, inst, left=True):
    """Etichetta «PFL» (cue in preascolto) all'inizio della barra. Restituisce la larghezza usata."""
    try:
        if inst is None or not inst.pfl:
            return 0
    except Exception:
        return 0
    from PySide6.QtGui import QFont
    p.save()
    f = QFont(p.font())
    f.setBold(True)
    ps = f.pointSizeF() if f.pointSizeF() > 0 else 9.0
    f.setPointSizeF(max(6.5, min(ps, bar.height() * 0.5)))
    p.setFont(f)
    w = p.fontMetrics().horizontalAdvance("PFL") + 8
    h = max(10.0, bar.height() - 2)
    x = bar.left() + 1 if left else bar.right() - w - 1
    r = QRectF(x, bar.center().y() - h / 2, w, h)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(40, 120, 200))
    p.drawRoundedRect(r, 3, 3)
    p.setPen(QColor(255, 255, 255))
    p.drawText(r, Qt.AlignCenter, "PFL")
    p.restore()
    return w + 2
