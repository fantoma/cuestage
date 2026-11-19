"""File PDF per le cue PDF: numero di pagine, dimensioni e pagine disegnate come immagini (Qt PDF), con cache.

Le pagine si disegnano su fondo bianco (un PDF ha la pagina trasparente) alla risoluzione richiesta; le ultime
pagine usate restano in memoria, così il GO sulla pagina successiva è immediato.
"""
import collections
import os
import re

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage, QPainter

_DOCS = {}                      # percorso -> (data di modifica, QPdfDocument)
_RENDERS = collections.OrderedDict()
MAX_RENDERS = 24


def available():
    """Qt PDF c'è (nei pacchetti è incluso; da sorgente serve PySide6 completo)."""
    import importlib.util
    try:
        return importlib.util.find_spec("PySide6.QtPdf") is not None
    except Exception:
        return False


def _doc(path):
    from PySide6.QtPdf import QPdfDocument
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    cur = _DOCS.get(path)
    if cur is not None and cur[0] == mt:
        return cur[1]
    d = QPdfDocument()
    d.load(path)
    if d.status() != QPdfDocument.Status.Ready or d.pageCount() <= 0:
        return None
    _DOCS[path] = (mt, d)
    return d


def page_count(path):
    """Numero di pagine (0 se il file non si apre)."""
    if not path or not available():
        return 0
    d = _doc(path)
    return d.pageCount() if d is not None else 0


def page_size(path, i):
    """Dimensioni della pagina i in punti (QSizeF), o None."""
    d = _doc(path) if path and available() else None
    if d is None or not (0 <= i < d.pageCount()):
        return None
    return d.pagePointSize(i)


def render(path, i, max_w, max_h):
    """Pagina i (da 0) come QImage che sta in max_w × max_h mantenendo le proporzioni, su fondo bianco."""
    sz = page_size(path, i)
    if sz is None or sz.width() <= 0 or sz.height() <= 0:
        return QImage()
    k = min(max_w / sz.width(), max_h / sz.height())
    w, h = max(1, int(sz.width() * k)), max(1, int(sz.height() * k))
    key = (path, i, w, h, os.path.getmtime(path))
    img = _RENDERS.get(key)
    if img is not None:
        _RENDERS.move_to_end(key)
        return img
    d = _doc(path)
    page = d.render(i, QSize(w, h))
    out = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    out.fill(QColor("white"))
    p = QPainter(out)
    p.drawImage(0, 0, page)
    p.end()
    _RENDERS[key] = out
    while len(_RENDERS) > MAX_RENDERS:
        _RENDERS.popitem(last=False)
    return out


def thumb(path, i, w=120, h=160):
    return render(path, i, w, h)


def parse_pages(text, n):
    """«1-3, 5, 8-» → indici (da 0) nell'ordine scritto, entro le n pagine; testo vuoto = []. Le pagine possono
    ripetersi (es. «1, 3, 1»)."""
    out = []
    for part in re.split(r"[,;\s]+", str(text or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", part)
        try:
            if m:
                a = int(m.group(1)) if m.group(1) else 1
                b = int(m.group(2)) if m.group(2) else n
                rng = range(a, b + 1) if a <= b else range(a, b - 1, -1)
                out += [p - 1 for p in rng if 1 <= p <= n]
            else:
                p = int(part)
                if 1 <= p <= n:
                    out.append(p - 1)
        except ValueError:
            continue
    return out


def format_pages(pages):
    """[0, 1, 2, 4] → «1-3, 5» (intervalli crescenti consecutivi)."""
    out, i = [], 0
    pages = list(pages)
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i] + 1) if j == i else f"{pages[i] + 1}-{pages[j] + 1}")
        i = j + 1
    return ", ".join(out)


def cue_pages(cue):
    """Pagine (da 0) che la cue mostra, nell'ordine: quelle scelte o, se nessuna, tutte."""
    n = page_count(cue.file)
    sel = [int(p) for p in (getattr(cue, "pdf_pages", None) or []) if 0 <= int(p) < n]
    return sel or list(range(n))
