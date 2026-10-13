"""Rendering delle cue di testo (sottotitoli, sopratitoli, didascalie) in un'immagine trasparente
alla risoluzione dell'uscita.

Area in percentuale dell'uscita; dimensione del carattere in pixel riferiti a un'uscita alta 1080 px
(si adatta da sola ad altre risoluzioni). Modalità di adattamento:
  wrap    a capo: dimensione fissa, il testo va a capo; con «righe massime» le righe in più si tagliano (…)
  shrink  riduci: va a capo e, se non entra nell'area o supera le righe massime, rimpicciolisce il carattere
  fit     riduci/allarga: il carattere più grande con cui il testo entra nell'area (e nelle righe massime)
  oneline una riga: niente a capo, il carattere si riduce per far stare la riga nella larghezza
"""
import re

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

FIT_MODES = {"wrap": "A capo (dimensione fissa)", "shrink": "A capo e riduci se non entra",
             "fit": "Riduci / allarga per riempire l'area", "oneline": "Una riga, riduci per farla stare"}
BLANK = ("_", "[vuoto]", "[blank]")


def split_phrases(text):
    """Frasi della sequenza: separate da una riga vuota. «_» da solo = schermo vuoto."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n\s*\n", text.strip("\n"))
    return [p.strip("\n") for p in parts if p.strip() != ""]


def parse_srt(text):
    """Testo di un file .srt -> frasi (numeri e tempi scartati: l'avanzamento è col GO)."""
    out = []
    for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        lines = [l for l in lines if not re.fullmatch(r"\d+", l.strip()) and "-->" not in l]
        lines = [re.sub(r"<[^>]+>", "", l) for l in lines]          # tag <i>, <b>…
        if lines:
            out.append("\n".join(lines))
    return out


def _wrap(text, fm, width):
    """Righe a capo (rispetta gli a capo espliciti); una parola più lunga dell'area va spezzata."""
    lines = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            cand = w if not cur else cur + " " + w
            if fm.horizontalAdvance(cand) <= width or not cur:
                cur = cand
                while fm.horizontalAdvance(cur) > width and len(cur) > 1:   # parola troppo lunga
                    k = len(cur)
                    while k > 1 and fm.horizontalAdvance(cur[:k]) > width:
                        k -= 1
                    lines.append(cur[:k])
                    cur = cur[k:]
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _font(cue, px):
    f = QFont(cue.text_font or "Sans Serif")
    f.setPixelSize(max(4, int(round(px))))
    f.setBold(bool(cue.text_bold))
    f.setItalic(bool(cue.text_italic))
    return f


def layout(cue, text, W, H):
    """(font, righe, interlinea, rettangolo dell'area) secondo la modalità di adattamento."""
    x, y, w, h = (float(v) for v in (cue.text_area or [5, 75, 90, 20]))
    area = QRectF(W * x / 100, H * y / 100, max(10.0, W * w / 100), max(10.0, H * h / 100))
    pad = float(cue.text_padding or 0) * H / 1080
    inner = area.adjusted(pad, pad, -pad, -pad)
    base = float(cue.text_size or 48) * H / 1080
    maxl = int(cue.text_max_lines or 0)
    spacing = float(cue.text_spacing or 100) / 100
    mode = cue.text_fit or "shrink"

    def build(px):
        f = _font(cue, px)
        fm = QFontMetricsF(f)
        if mode == "oneline":
            lines = [" ".join(text.split("\n"))]
        else:
            lines = _wrap(text, fm, inner.width())
        lh = fm.height() * spacing
        return f, lines, lh, fm

    def fits(px):
        f, lines, lh, fm = build(px)
        if mode == "oneline":
            return fm.horizontalAdvance(lines[0]) <= inner.width() and fm.height() <= inner.height()
        if maxl and len(lines) > maxl:
            return False
        return lh * len(lines) <= inner.height() + 0.5

    px = base
    if mode in ("shrink", "oneline"):
        while px > 6 and not fits(px):
            px *= 0.94
    elif mode == "fit":
        lo, hi = 4.0, max(8.0, inner.height())
        for _ in range(22):
            mid = (lo + hi) / 2
            if fits(mid):
                lo = mid
            else:
                hi = mid
        px = lo
    f, lines, lh, fm = build(px)
    if maxl and len(lines) > maxl:            # «a capo»: le righe in più si tagliano
        lines = lines[:maxl]
        last = lines[-1]
        while last and fm.horizontalAdvance(last + "…") > inner.width():
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return f, lines, lh, inner, area


def render_text(cue, text, W, H, img=None):
    """Immagine ARGB WxH trasparente con il testo disegnato secondo lo stile della cue."""
    if img is None or img.width() != W or img.height() != H:
        img = QImage(int(W), int(H), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    if not text or text.strip() in BLANK:
        return img
    f, lines, lh, inner, area = layout(cue, text, W, H)
    fm = QFontMetricsF(f)
    total_h = lh * len(lines)
    va = cue.text_valign or "bottom"
    if total_h > inner.height():          # il testo non entra: si vede l'inizio, si taglia la fine
        va = "top"
    if va == "top":
        y0 = inner.top()
    elif va == "middle":
        y0 = inner.top() + (inner.height() - total_h) / 2
    else:
        y0 = inner.bottom() - total_h
    ha = cue.text_halign or "center"
    widths = [fm.horizontalAdvance(l) for l in lines]
    xs = []
    for wl in widths:
        if ha == "left":
            xs.append(inner.left())
        elif ha == "right":
            xs.append(inner.right() - wl)
        else:
            xs.append(inner.left() + (inner.width() - wl) / 2)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.setClipRect(area)
    bg = cue.text_bg or ""
    if bg and float(cue.text_bg_opacity or 0) > 0:
        col = QColor(bg)
        col.setAlphaF(max(0.0, min(1.0, float(cue.text_bg_opacity) / 100)))
        pad = float(cue.text_padding or 0) * H / 1080
        if cue.text_bg_mode == "area":
            box = area
        else:
            box = QRectF(min(xs) - pad, y0 - pad, max(widths) + 2 * pad, total_h + 2 * pad)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(box, 6 * H / 1080, 6 * H / 1080)
    color = QColor(cue.text_color or "#ffffff")
    outline = float(cue.text_outline or 0) * H / 1080
    ocol = QColor(cue.text_outline_color or "#000000")
    for i, line in enumerate(lines):
        base_y = y0 + i * lh + (lh - fm.height()) / 2 + fm.ascent()
        path = QPainterPath()
        path.addText(QPointF(xs[i], base_y), f, line)
        if outline > 0:
            p.setPen(QPen(ocol, outline * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(color))
        p.drawPath(path)
    p.end()
    return img
