"""Vista GRID di un PANEL: caselle colorate su più pagine, da cliccare (o da tastiera) per avviare le cue."""
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QKeySequenceEdit, QLabel,
    QMenu, QMessageBox, QPushButton, QSpinBox, QTabBar, QVBoxLayout, QWidget,
)

from . import grid as G
from .model import CUE_TYPES, TYPE_ICONS, TYPE_LABELS

CLICK_MODES = [("toggle", "Clic: avvia / ferma"), ("restart", "Clic: riavvia dall'inizio"),
               ("start", "Clic: avvia (se è già attiva non fa nulla)")]
EMPTY_BG = QColor("#1b1c21")
DEFAULT_BG = "#2f3440"


def fmt_t(s):
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


class GridCanvas(QWidget):
    """Le caselle della pagina corrente."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setMinimumHeight(160)
        self.hover = None
        self.drag_from = None          # (cue, punto) durante il trascinamento in modifica
        self.drag_over = None

    # ---- geometria
    def cell_rect(self, r, c):
        lst = self.panel.lst
        W, H = self.width(), self.height()
        cw, ch = W / lst.grid_cols, H / lst.grid_rows
        return QRectF(c * cw + 3, r * ch + 3, cw - 6, ch - 6)

    def cell_at(self, pos):
        lst = self.panel.lst
        if lst is None or self.width() <= 0 or self.height() <= 0:
            return None
        c = int(pos.x() / (self.width() / lst.grid_cols))
        r = int(pos.y() / (self.height() / lst.grid_rows))
        if 0 <= r < lst.grid_rows and 0 <= c < lst.grid_cols:
            return (self.panel.page, r, c)
        return None

    # ---- disegno
    def paintEvent(self, _e):
        lst = self.panel.lst
        if lst is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#141519"))
        mw = self.panel.mw
        ctrl = mw.ctrl
        occ = G.occupied(lst)
        sel = mw._current_cue()
        ph = ctrl.playhead_of(lst)
        nxt = lst.cues[ph] if 0 <= ph < len(lst.cues) else None
        if nxt is not None and nxt.group:
            nxt = mw.show_data.by_id(nxt.group) or nxt
        pending = {x[1].id for x in ctrl.pending}
        local_go = mw.show_data.settings.get("go_target", "main") == "main"
        f_small = QFont(self.font())
        f_small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        f_name = QFont(self.font())
        f_name.setBold(True)
        cell_h = self.height() / lst.grid_rows
        f_name.setPointSizeF(max(8.0, min(15.0, cell_h / 6.5)))
        for r in range(lst.grid_rows):
            for c in range(lst.grid_cols):
                rect = self.cell_rect(r, c)
                cue = occ.get((self.panel.page, r, c))
                if cue is None:
                    p.setPen(QPen(QColor("#2a2c33"), 1))
                    p.setBrush(EMPTY_BG)
                    p.drawRoundedRect(rect, 6, 6)
                    if self.drag_over == (self.panel.page, r, c) or (
                            self.panel.editing and self.hover == (self.panel.page, r, c)):
                        p.setPen(QColor("#6a6f7c"))
                        p.drawText(rect, Qt.AlignCenter, "+" if self.drag_from is None else "↓")
                    continue
                self._paint_cue(p, rect, cue, ctrl, sel, nxt, pending, local_go, f_small, f_name,
                                (self.panel.page, r, c))
        p.end()

    def _paint_cue(self, p, rect, cue, ctrl, sel, nxt, pending, local_go, f_small, f_name, cell):
        bg = QColor(cue.color or DEFAULT_BG)
        fg = QColor(G.text_color(cue.color or DEFAULT_BG))
        inst = ctrl.instances.get(cue.id)
        active = inst is not None and not inst.done
        waiting = cue.id in pending
        p.setPen(Qt.NoPen)
        p.setBrush(bg if not active else bg.lighter(125))
        p.drawRoundedRect(rect, 6, 6)
        remain = None
        if active:
            tot = None
            try:
                tot = inst.total()
            except Exception:
                pass
            t = inst.cue_time() if hasattr(inst, "cue_time") else inst.elapsed
            if tot:
                frac = max(0.0, min(1.0, t / tot))
                remain = tot - t
                band = QRectF(rect.x(), rect.bottom() - 7, rect.width() * frac, 7)
                p.setBrush(QColor(61, 220, 132, 220))
                p.drawRoundedRect(band, 3, 3)
            else:
                remain = None
        # bordo: attiva (verde), in pre-wait (ambra), selezionata (bianco), prossima del GO locale (giallo)
        if active or waiting:
            p.setPen(QPen(QColor("#3ddc84") if active else QColor("#e0a030"), 3))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), 6, 6)
        if sel is not None and sel.id == cue.id:
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(4, 4, -4, -4), 4, 4)
        if local_go and nxt is not None and nxt.id == cue.id:
            pen = QPen(QColor("#ffd23f"), 2, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 5, 5)
        if self.drag_over == cell and self.drag_from is not None:
            p.setPen(QPen(QColor("#8ab4ff"), 3))
            p.drawRoundedRect(rect, 6, 6)
        inner = rect.adjusted(7, 5, -7, -9).toRect()
        p.setPen(fg)
        p.setFont(f_small)
        p.drawText(inner, Qt.AlignLeft | Qt.AlignTop, f"{TYPE_ICONS.get(cue.type, '')} {cue.number}".strip())
        if cue.hotkey:
            hk = QKeySequence.fromString(cue.hotkey, QKeySequence.PortableText).toString(QKeySequence.NativeText)
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(hk) + 10
            badge = QRect(inner.right() - w, inner.top(), w, fm.height() + 2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRoundedRect(badge, 4, 4)
            p.setPen(fg)
            p.drawText(badge, Qt.AlignCenter, hk)
        p.setFont(f_name)
        name = cue.name or TYPE_LABELS.get(cue.type, cue.type)
        if cue.type == "group":
            from .engine import fmt_div, tap_active
            n = sum(1 for x in self.panel.lst.cues if x.group == cue.id)
            name = f"{name}\n({n} cue)"
            if tap_active(cue):
                name += f"\n♩ {float(cue.group_bpm):g} · {fmt_div(cue.group_tap_div)}"
        mid = inner.adjusted(0, p.fontMetrics().height() // 2, 0, -p.fontMetrics().height() // 2)
        p.drawText(mid, Qt.AlignCenter | Qt.TextWordWrap, name)
        p.setFont(f_small)
        if remain is not None:
            p.drawText(inner, Qt.AlignRight | Qt.AlignBottom, f"-{fmt_t(remain)}")
        elif waiting:
            p.drawText(inner, Qt.AlignRight | Qt.AlignBottom, "pre-wait…")
        elif self.panel.mw.show_data.settings.get("go_target", "main") == "main" and \
                nxt is not None and nxt.id == cue.id:
            p.drawText(inner, Qt.AlignRight | Qt.AlignBottom, "▶ GO")

    # ---- mouse
    def mousePressEvent(self, e):
        cell = self.cell_at(e.position().toPoint())
        if cell is None:
            return
        cue = G.cell_cue(self.panel.lst, *cell)
        if e.button() == Qt.RightButton:
            self.panel.cell_menu(cell, cue, e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        if self.panel.editing:
            if cue is not None:
                self.panel.mw.grid_select(cue, move_playhead=True)
                self.drag_from = (cue, e.position().toPoint())
            self.update()
            return
        if cue is not None:
            self.panel.mw.grid_activate(cue)
            self.panel.mw.grid_select(cue, move_playhead=False)
            self.update()

    def mouseMoveEvent(self, e):
        cell = self.cell_at(e.position().toPoint())
        if cell != self.hover:
            self.hover = cell
            self.update()
        if self.drag_from is not None and (e.position().toPoint() - self.drag_from[1]).manhattanLength() > 8:
            self.drag_over = cell
            self.setCursor(Qt.ClosedHandCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        if self.drag_from is not None:
            cue = self.drag_from[0]
            target = self.drag_over
            self.drag_from = None
            self.drag_over = None
            self.unsetCursor()
            if target is not None and list(target) != list(cue.grid_pos or []):
                self.panel.mw.grid_move(cue, target)
            self.update()

    def mouseDoubleClickEvent(self, e):
        cell = self.cell_at(e.position().toPoint())
        if cell is None:
            return
        cue = G.cell_cue(self.panel.lst, *cell)
        if cue is None:
            self.panel.new_cue_menu(cell, e.globalPosition().toPoint())
        elif self.panel.editing:
            self.panel.mw.grid_select(cue, move_playhead=True)
            self.panel.mw.inspector.setFocus()

    def leaveEvent(self, _e):
        self.hover = None
        self.update()

    # ---- file trascinati dal file manager
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        cell = self.cell_at(e.position().toPoint())
        if cell != self.drag_over:
            self.drag_over = cell
            self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self.drag_over = None
        self.update()

    def dropEvent(self, e):
        cell = self.cell_at(e.position().toPoint())
        self.drag_over = None
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths and cell is not None:
            self.panel.mw.grid_add_files(paths, list(cell))
        e.acceptProposedAction()
        self.update()


class GridPanel(QWidget):
    """Barra (pagine, modifica, dimensioni) + caselle."""
    changed = Signal()

    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        self.lst = None
        self.page = 0
        self.editing = False
        self._pages = {}          # pagina mostrata per ogni PANEL
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        bar = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._page_menu)
        self.tabs.currentChanged.connect(self._page_changed)
        self.tabs.tabBarDoubleClicked.connect(self.rename_page)
        bar.addWidget(self.tabs, 1)
        self.add_page_btn = QPushButton("+ Pagina")
        self.add_page_btn.setToolTip("Aggiungi una pagina alla griglia")
        self.add_page_btn.clicked.connect(self.add_page)
        bar.addWidget(self.add_page_btn)
        self.size_box = QWidget()
        sb = QHBoxLayout(self.size_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.addWidget(QLabel("Righe"))
        self.rows = QSpinBox()
        self.rows.setRange(1, 16)
        sb.addWidget(self.rows)
        sb.addWidget(QLabel("Colonne"))
        self.cols = QSpinBox()
        self.cols.setRange(1, 16)
        sb.addWidget(self.cols)
        self.click = QComboBox()
        for k, t in CLICK_MODES:
            self.click.addItem(t, k)
        sb.addWidget(self.click)
        self.rows.valueChanged.connect(self._size_changed)
        self.cols.valueChanged.connect(self._size_changed)
        self.click.currentIndexChanged.connect(self._click_changed)
        bar.addWidget(self.size_box)
        self.edit_btn = QPushButton("✎ Modifica")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setToolTip("Modifica: clic seleziona (ispettore), trascina per spostare, doppio clic su una "
                                 "casella vuota per una nuova cue. Senza modifica: clic avvia la cue.")
        self.edit_btn.toggled.connect(self.set_editing)
        bar.addWidget(self.edit_btn)
        v.addLayout(bar)
        self.canvas = GridCanvas(self)
        v.addWidget(self.canvas, 1)
        self.hint = QLabel()
        self.hint.setStyleSheet("color:#888;font-size:11px;")
        v.addWidget(self.hint)
        self.set_editing(False)

    # ---- dati
    def set_list(self, lst):
        if self.lst is not None:
            self._pages[self.lst.id] = self.page
        self.lst = lst
        self.page = min(self._pages.get(lst.id, 0), len(lst.grid_pages) - 1)
        self.sync()

    def sync(self):
        lst = self.lst
        if lst is None:
            return
        self.page = max(0, min(self.page, len(lst.grid_pages) - 1))
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for name in lst.grid_pages:
            self.tabs.addTab(name)
        self.tabs.setCurrentIndex(self.page)
        self.tabs.blockSignals(False)
        for w, val in ((self.rows, lst.grid_rows), (self.cols, lst.grid_cols)):
            w.blockSignals(True)
            w.setValue(val)
            w.blockSignals(False)
        self.click.blockSignals(True)
        self.click.setCurrentIndex(max(0, self.click.findData(lst.grid_click)))
        self.click.blockSignals(False)
        self._update_hint()
        self.canvas.update()

    def _update_hint(self):
        if self.editing:
            self.hint.setText("Modifica: clic = seleziona · trascina = sposta (o scambia) · doppio clic su una casella "
                              "vuota = nuova cue · tasto destro = colore, scorciatoia, elimina · trascina qui dei file "
                              "audio/video per creare le cue.")
        else:
            mode = dict(CLICK_MODES).get(self.lst.grid_click if self.lst else "toggle", "")
            self.hint.setText(f"{mode} · tasto destro: colore, scorciatoia da tastiera, nuova cue · "
                              "il GO del PANEL avanza casella dopo casella.")

    def set_editing(self, on):
        self.editing = bool(on)
        self.size_box.setVisible(self.editing)
        self.add_page_btn.setVisible(self.editing)
        if self.lst is not None:
            self._update_hint()
        self.canvas.update()

    def _page_changed(self, i):
        if i >= 0:
            self.page = i
            self.canvas.update()

    def _size_changed(self, *_a):
        if self.lst is None:
            return
        self.mw.grid_resize(self.lst, self.rows.value(), self.cols.value())

    def _click_changed(self, j):
        if self.lst is not None:
            self.lst.grid_click = self.click.itemData(j)
            self.mw.show_data.dirty = True
            self._update_hint()

    # ---- pagine
    def add_page(self):
        lst = self.lst
        lst.grid_pages.append(f"Pagina {len(lst.grid_pages) + 1}")
        self.page = len(lst.grid_pages) - 1
        self.mw.show_data.dirty = True
        self.sync()

    def rename_page(self, i):
        if i < 0:
            return
        name, ok = QInputDialog.getText(self, "Rinomina pagina", "Nome della pagina:", text=self.lst.grid_pages[i])
        if ok and name.strip():
            self.lst.grid_pages[i] = name.strip()
            self.mw.show_data.dirty = True
            self.sync()

    def delete_page(self, i):
        if G.page_cues(self.lst, i):
            QMessageBox.information(self, "Elimina pagina", "La pagina contiene delle cue: spostale su un'altra "
                                    "pagina (trascinandole in modifica) o eliminale prima.")
            return
        if G.delete_page(self.lst, i):
            self.mw.show_data.dirty = True
            self.sync()

    def _page_menu(self, pos):
        i = self.tabs.tabAt(pos)
        m = QMenu(self)
        m.addAction("Nuova pagina", self.add_page)
        if i >= 0:
            m.addAction("Rinomina…", lambda: self.rename_page(i))
            a = m.addAction("Elimina pagina", lambda: self.delete_page(i))
            a.setEnabled(len(self.lst.grid_pages) > 1)
        m.exec(self.tabs.mapToGlobal(pos))

    # ---- menu delle caselle
    def new_cue_menu(self, cell, gpos):
        m = QMenu(self)
        self._fill_new(m, cell)
        m.exec(gpos)

    def _fill_new(self, m, cell):
        for t in CUE_TYPES:
            if t == "groupexit":
                continue
            m.addAction(f"{TYPE_ICONS.get(t, '')}  {TYPE_LABELS[t]}",
                        lambda _=False, t=t: self.mw.grid_add_cue(t, list(cell)))

    def cell_menu(self, cell, cue, gpos):
        mw = self.mw
        m = QMenu(self)
        if cue is None:
            self._fill_new(m.addMenu("Nuova cue qui"), cell)
            m.exec(gpos)
            return
        m.addAction(f"{TYPE_ICONS.get(cue.type, '')}  {cue.label()}").setEnabled(False)
        m.addSeparator()
        m.addAction("▶ Avvia", lambda: mw.ctrl.trigger(cue, cascade=False))
        m.addAction("■ Ferma", lambda: mw.ctrl.stop_cue(cue.id, float(mw.show_data.settings.get("stop_fade", 0.3))))
        m.addAction("Imposta come prossima del GO", lambda: mw.grid_select(cue, move_playhead=True))
        m.addSeparator()
        cm = m.addMenu("Colore")
        for col in G.PALETTE:
            a = cm.addAction("■  " + col)
            a.triggered.connect(lambda _=False, col=col: mw.grid_set_color(cue, col))
        cm.addSeparator()
        cm.addAction("Altro colore…", lambda: self.pick_color(cue))
        cm.addAction("Nessun colore", lambda: mw.grid_set_color(cue, ""))
        m.addAction("Impara scorciatoia da tastiera…", lambda: mw.learn_hotkey(cue))
        a = m.addAction("Togli scorciatoia", lambda: mw.set_hotkey(cue, ""))
        a.setEnabled(bool(cue.hotkey))
        m.addSeparator()
        m.addAction("Modifica nell'ispettore", lambda: mw.grid_select(cue, move_playhead=False))
        m.addAction("Elimina cue…", lambda: mw.grid_delete(cue))
        m.exec(gpos)

    def pick_color(self, cue):
        col = QColorDialog.getColor(QColor(cue.color or DEFAULT_BG), self, "Colore della casella")
        if col.isValid():
            self.mw.grid_set_color(cue, col.name())


class HotkeyDialog(QDialog):
    """«Impara» una scorciatoia: si preme la combinazione di tasti e la si conferma."""

    def __init__(self, cue, reserved, others, parent=None, current=None, purpose=""):
        super().__init__(parent)
        self.setWindowTitle("Scorciatoia da tastiera")
        current = cue.hotkey if current is None else current
        self.reserved = reserved       # {testo: descrizione} già usate dal programma
        self.others = others           # {testo: cue} già usate da altre cue
        self.result_key = None
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"Premi la combinazione di tasti per {purpose}«{cue.label()}»\n"
                           "(es. F5, 1, Q, Ctrl+1). La scorciatoia funziona con la finestra di CueStage attiva,\n"
                           "ma non mentre stai scrivendo in un campo di testo."))
        self.edit = QKeySequenceEdit()
        if hasattr(self.edit, "setMaximumSequenceLength"):
            self.edit.setMaximumSequenceLength(1)
        if current:
            self.edit.setKeySequence(QKeySequence.fromString(current, QKeySequence.PortableText))
        self.edit.keySequenceChanged.connect(self._check)
        v.addWidget(self.edit)
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        v.addWidget(self.bb)
        self.edit.setFocus()
        self._check()

    def key(self):
        ks = self.edit.keySequence()
        if ks.isEmpty():
            return ""
        return QKeySequence(ks[0]).toString(QKeySequence.PortableText)

    def _check(self, *_a):
        k = self.key()
        ok = self.bb.button(QDialogButtonBox.Ok)
        self.steal = None
        if not k:
            self.msg.setText("")
            ok.setEnabled(False)
            return
        if k in self.reserved:
            self.msg.setText(f"«{k}» è già usata da CueStage ({self.reserved[k]}): scegline un'altra.")
            self.msg.setStyleSheet("color:#e66;")
            ok.setEnabled(False)
            return
        if k in self.others:
            self.steal = self.others[k]
            sc, sf = self.steal
            what = "il Tap Tempo di " if sf == "group_tap_key" else ""
            self.msg.setText(f"«{k}» è già assegnata a {what}«{sc.label()}»: confermando passa qui.")
            self.msg.setStyleSheet("color:#e0b040;")
        else:
            self.msg.setText(f"«{k}» è libera.")
            self.msg.setStyleSheet("color:#6c6;")
        ok.setEnabled(True)


def point(x, y):
    return QPoint(int(x), int(y))
