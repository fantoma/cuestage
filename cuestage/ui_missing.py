"""File › Trova file mancanti: cerca sul disco i file multimediali delle cue che non si trovano più (show spostato
su un altro computer, disco esterno con un'altra lettera o un altro punto di montaggio, cartella rinominata).

La ricerca va per nome del file (maiuscole e minuscole indifferenti) e, se ci sono più file con lo stesso nome,
sceglie quello con più cartelle finali uguali al percorso originale. Indicando a mano un file, lo stesso
spostamento di cartella viene provato su tutti gli altri file mancanti."""
import os
import sys
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from .i18n import tr

SKIP_DIRS = {".git", "__pycache__", "node_modules", "$recycle.bin", "system volume information", ".trash",
             ".trash-1000", "proc", "sys", "dev", "snap", "windows", "program files", "program files (x86)",
             "programdata", "appdata", "library", ".cache", ".local", ".venv", "venv"}


def _parts(path):
    """Parti del percorso in minuscolo, con «/» o «\\» (show salvati su un altro sistema)."""
    return [x.lower() for x in str(path).replace("\\", "/").split("/") if x and x != "."]


def _basename(path):
    return _parts(path)[-1] if _parts(path) else ""


def missing_cues(show):
    """Cue (di tutte le versioni dello show) con un file multimediale che non c'è."""
    return [c for c in show.every_cue() if c.is_media() and c.file and not os.path.exists(c.file)
            and not getattr(c, "is_link", False)]          # i collegamenti seguono il file dell'originale


def score(old, cand):
    """Quante parti finali del percorso (nome compreso) coincidono."""
    a, b = _parts(old), _parts(cand)
    n = 0
    while n < len(a) and n < len(b) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n


def best_match(old, candidates):
    if not candidates:
        return None
    return max(candidates, key=lambda c: (score(old, c), -len(c)))


def relocate(old_ref, new_ref, other):
    """Il file old_ref è stato trovato in new_ref: dove sarebbe «other» con lo stesso spostamento? (o None)
    Si toglie dai due percorsi la parte finale comune e si sostituisce l'inizio."""
    a = _parts(old_ref)
    n = score(old_ref, new_ref)
    if n == 0:
        return None
    old_prefix = a[:len(a) - n]
    o = _parts(other)
    if o[:len(old_prefix)] != old_prefix:
        return None
    # parte iniziale del nuovo percorso con le maiuscole originali
    raw_new = os.path.normpath(new_ref)
    head = raw_new
    for _ in range(n):
        head = os.path.dirname(head)
    raw_other = other.replace("\\", "/").split("/")
    raw_other = [x for x in raw_other if x]
    cand = os.path.join(head, *raw_other[len(old_prefix):])
    return cand if os.path.exists(cand) else None


def default_roots(show_path=None):
    """Dove cercare con «Cerca ovunque»: la cartella dello show e la sua superiore, la home, i dischi esterni."""
    roots = []
    if show_path:
        d = os.path.dirname(os.path.abspath(show_path))
        roots += [d, os.path.dirname(d)]
    roots.append(os.path.expanduser("~"))
    if sys.platform.startswith("win"):
        import string
        roots += [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    elif sys.platform == "darwin":
        roots.append("/Volumes")
    else:
        user = os.environ.get("USER") or ""
        roots += [p for p in ("/media/" + user, "/run/media/" + user, "/media", "/mnt") if os.path.isdir(p)]
    out = []
    for r in roots:
        r = os.path.normpath(r)
        if os.path.isdir(r) and not any(r == o or r.startswith(o.rstrip(os.sep) + os.sep) for o in out):
            out = [o for o in out if not o.startswith(r.rstrip(os.sep) + os.sep)] + [r]
    return out


def search(names, roots, cancel=lambda: False, progress=None):
    """Cerca i nomi (minuscoli) sotto le cartelle: {nome: [percorsi]}. progress(cartelle viste, cartella)."""
    found = {n: [] for n in names}
    seen = set()
    count = 0
    for root in roots:
        for dirpath, dirs, files in os.walk(root, onerror=lambda e: None):
            if cancel():
                return found
            try:
                key = os.path.realpath(dirpath)
            except OSError:
                key = dirpath
            if key in seen:
                dirs[:] = []
                continue
            seen.add(key)
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            count += 1
            if progress is not None and count % 50 == 0:
                progress(count, dirpath)
            for f in files:
                lf = f.lower()
                if lf in found:
                    found[lf].append(os.path.join(dirpath, f))
    return found


class MissingFilesDialog(QDialog):
    """Elenco dei file mancanti, ricerca su disco (in un thread) e ricollegamento."""
    C_CUE, C_NAME, C_OLD, C_NEW = range(4)

    def __init__(self, mw, parent=None):
        super().__init__(parent or mw)
        self.mw = mw
        self.setWindowTitle(tr("Trova file mancanti"))
        self.setWindowFlags(Qt.Tool | Qt.WindowCloseButtonHint | Qt.WindowTitleHint)
        self.resize(1000, 480)
        self.found = {}             # percorso mancante -> nuovo percorso
        self._worker = None
        self._cancel = False
        self._result = None
        self._prog = (0, "")
        v = QVBoxLayout(self)
        info = QLabel(tr("Questi file delle cue non ci sono più nel percorso salvato. «Cerca in una cartella…» li "
                         "cerca per nome in quella cartella e nelle sottocartelle (se ce ne sono più con lo stesso "
                         "nome, sceglie quello con il percorso più simile). Indicandone uno a mano, lo stesso "
                         "spostamento viene provato sugli altri."))
        info.setWordWrap(True)
        info.setStyleSheet("color:#888;")
        v.addWidget(info)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Cue"), tr("Nome"), tr("File mancante"), tr("Trovato")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(self.C_OLD, QHeaderView.Stretch)
        hh.setSectionResizeMode(self.C_NEW, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda r, _c: self.locate(r))
        v.addWidget(self.table, 1)
        self.state = QLabel()
        self.state.setWordWrap(True)
        v.addWidget(self.state)
        bt = QHBoxLayout()
        self.folder_btn = QPushButton(tr("Cerca in una cartella…"))
        self.folder_btn.clicked.connect(self.search_folder)
        bt.addWidget(self.folder_btn)
        self.all_btn = QPushButton(tr("Cerca ovunque"))
        self.all_btn.setToolTip(tr("Cartella dello show e quella sopra, cartella personale, dischi esterni"))
        self.all_btn.clicked.connect(lambda: self.start_search(default_roots(self.mw.show_data.path)))
        bt.addWidget(self.all_btn)
        self.locate_btn = QPushButton(tr("Indica il file…"))
        self.locate_btn.setToolTip(tr("Scegli a mano il file della riga selezionata (anche doppio clic sulla riga)"))
        self.locate_btn.clicked.connect(lambda: self.locate(self.table.currentRow()))
        bt.addWidget(self.locate_btn)
        self.stop_btn = QPushButton(tr("Ferma la ricerca"))
        self.stop_btn.clicked.connect(self.stop_search)
        self.stop_btn.setVisible(False)
        bt.addWidget(self.stop_btn)
        bt.addStretch(1)
        self.apply_btn = QPushButton(tr("Ricollega i file trovati"))
        self.apply_btn.clicked.connect(self.apply)
        bt.addWidget(self.apply_btn)
        close = QPushButton(tr("Chiudi"))
        close.clicked.connect(self.close)
        bt.addWidget(close)
        v.addLayout(bt)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self._poll)
        self.reload()

    # ---- elenco
    def reload(self):
        cues = missing_cues(self.mw.show_data)
        by_path = {}
        for c in cues:
            by_path.setdefault(c.file, []).append(c)
        self.paths = list(by_path)
        self.cues_of = by_path
        self.found = {p: n for p, n in self.found.items() if p in by_path and os.path.exists(n)}
        self.table.setRowCount(len(self.paths))
        for r, p in enumerate(self.paths):
            cs = by_path[p]
            nums = ", ".join(c.number or "—" for c in cs[:4]) + (" …" if len(cs) > 4 else "")
            names = ", ".join(c.name or c.label() for c in cs[:2]) + (" …" if len(cs) > 2 else "")
            for col, text in ((self.C_CUE, nums), (self.C_NAME, names), (self.C_OLD, p)):
                it = QTableWidgetItem(text)
                it.setToolTip(text)
                self.table.setItem(r, col, it)
            self.table.setItem(r, self.C_NEW, QTableWidgetItem(""))
        self._refresh()

    def _refresh(self):
        for r, p in enumerate(self.paths):
            it = self.table.item(r, self.C_NEW)
            n = self.found.get(p)
            it.setText(n or "")
            it.setToolTip(n or "")
            it.setForeground(QBrush(QColor("#50c070")) if n else QBrush())
        busy = self._worker is not None and self._worker.is_alive()
        nf = len(self.found)
        if busy:
            self.state.setText(tr("Ricerca… {0} cartelle · {1}", self._prog[0], self._prog[1]))
        elif not self.paths:
            self.state.setText(tr("Nessun file mancante."))
        else:
            self.state.setText(tr("{0} file mancanti · trovati: {1}", len(self.paths), nf))
        for b in (self.folder_btn, self.all_btn, self.locate_btn):
            b.setEnabled(not busy and bool(self.paths))
        self.apply_btn.setEnabled(not busy and nf > 0)
        self.stop_btn.setVisible(busy)

    # ---- ricerca
    def search_folder(self):
        start = os.path.dirname(self.mw.show_data.path) if self.mw.show_data.path else os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, tr("Cerca i file mancanti in…"), start)
        if d:
            self.start_search([d])

    def start_search(self, roots):
        todo = [p for p in self.paths if p not in self.found]
        if not todo or not roots:
            return
        self._names = {_basename(p) for p in todo}
        self._cancel = False
        self._result = None
        self._prog = (0, "")
        self._worker = threading.Thread(target=self._run, args=(list(roots),), daemon=True, name="find-missing")
        self._worker.start()
        self.timer.start()
        self._refresh()

    def _run(self, roots):
        def prog(n, d):
            self._prog = (n, d)
        self._result = search(self._names, roots, lambda: self._cancel, prog)

    def stop_search(self):
        self._cancel = True

    def _poll(self):
        if self._worker is not None and self._worker.is_alive():
            self._refresh()
            return
        self.timer.stop()
        res = self._result or {}
        for p in self.paths:
            if p in self.found:
                continue
            m = best_match(p, res.get(_basename(p)) or [])
            if m:
                self.found[p] = m
        self._worker = None
        self._refresh()

    def wait(self, timeout=60.0):
        """Per le prove: aspetta la fine della ricerca."""
        if self._worker is not None:
            self._worker.join(timeout)
        self._poll()

    def locate(self, row, path=None):
        """Indica a mano il file della riga (path: per le prove), poi prova lo stesso spostamento sugli altri."""
        if not (0 <= row < len(self.paths)):
            return
        old = self.paths[row]
        if path is None:
            ext = os.path.splitext(old)[1]
            start = os.path.dirname(self.found.get(old) or "") or (
                os.path.dirname(self.mw.show_data.path) if self.mw.show_data.path else os.path.expanduser("~"))
            path, _ = QFileDialog.getOpenFileName(self, tr("Dov'è «{0}»?", os.path.basename(old)), start,
                                                  (f"*{ext} (*{ext});;" if ext else "") + tr("Tutti i file (*)"))
        if not path:
            return
        self.found[old] = path
        for p in self.paths:
            if p not in self.found:
                n = relocate(old, path, p)
                if n:
                    self.found[p] = n
        self._refresh()

    # ---- applicazione
    def apply(self):
        n = 0
        changed = []
        for p, new in list(self.found.items()):
            if not os.path.exists(new):
                continue
            for c in self.cues_of.get(p, []):
                c.file = new
                changed.append(c)
                n += 1
        if not n:
            return
        mw = self.mw
        mw.show_data.dirty = True
        for c in changed:
            try:
                mw._preload(c)
            except Exception:
                pass
        try:
            mw.model.refresh()
            if mw.inspector.cue is not None:
                mw.inspector.rebuild()
        except Exception:
            pass
        mw.status(tr("Ricollegati {0} file ({1} cue)", len({c.file for c in changed}), n))
        self.reload()

    def closeEvent(self, e):
        self._cancel = True
        super().closeEvent(e)
