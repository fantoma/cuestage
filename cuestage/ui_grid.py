"""Vista GRID di un PANEL: caselle colorate su più pagine, da cliccare (o da tastiera) per avviare le cue."""
from .i18n import tr
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QInputDialog,
    QKeySequenceEdit, QLabel, QMenu, QMessageBox, QPushButton, QSpinBox, QTabBar, QVBoxLayout, QWidget,
)

from . import grid as G
from .model import CUE_TYPES, TYPE_ICONS, TYPE_LABELS
from .ui_cuelist import PFL_TYPES, paint_pfl_button, pfl_inherited

CLICK_MODES = [("toggle", tr("Clic: avvia / ferma")), ("restart", tr("Clic: riavvia dall'inizio")),
               ("start", tr("Clic: avvia (se è già attiva non fa nulla)"))]
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
        occ = self.panel.cue_map()
        self.pfl_hits = {}                 # id cue -> rettangolo del pulsante PFL
        self._pfl_on = bool(mw.audio.preview.get("enabled"))
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
                            (self.panel.editing or self.panel.group_id) and self.hover == (self.panel.page, r, c)):
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
        bx = inner.left()
        if active and getattr(inst, "blind", False):
            fmb = p.fontMetrics()
            bw = fmb.horizontalAdvance("BLIND") + 10
            bb = QRect(inner.left(), inner.bottom() - fmb.height() - 2, bw, fmb.height() + 2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#b3261e"))
            p.drawRoundedRect(bb, 4, 4)
            p.setPen(QColor("white"))
            p.drawText(bb, Qt.AlignCenter, "BLIND")
            p.setPen(fg)
            bx = bb.right() + 4
        if active:
            from .ui_duckicon import paint_duck
            fmd = p.fontMetrics()
            paint_duck(p, QRectF(bx, inner.bottom() - fmd.height() - 2, fmd.height() + 4, fmd.height() + 2),
                       inst, ctrl, right=False)
            p.setPen(fg)
        right = inner.right()
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
            right = badge.left() - 4
        if self._pfl_on and cue.type in PFL_TYPES:
            # pulsante PFL dentro la casella: manda la cue al preascolto (clic sul pulsante, non avvia la cue)
            fm = p.fontMetrics()
            h = fm.height() + 6
            w = fm.horizontalAdvance("PFL") + 14
            pr = QRectF(right - w, inner.top() - 1, w, h)
            paint_pfl_button(p, pr, bool(cue.pfl), pfl_inherited(self.panel.mw.show_data, cue) is not None)
            self.pfl_hits[cue.id] = pr.adjusted(-3, -3, 3, 3)
            p.setPen(fg)
        p.setFont(f_name)
        name = cue.name or TYPE_LABELS.get(cue.type, cue.type)
        if cue.type == "group":
            from .engine import fmt_div, tap_active
            n = sum(1 for x in self.panel.lst.cues if x.group == cue.id)
            name = tr("{0}\n({1} cue)", name, n)
            if tap_active(cue):
                name += f"\n♩ {float(cue.group_bpm):g} · {fmt_div(cue.group_tap_div)}"
        mid = inner.adjusted(0, p.fontMetrics().height() // 2, 0, -p.fontMetrics().height() // 2)
        p.drawText(mid, Qt.AlignCenter | Qt.TextWordWrap, name)
        p.setFont(f_small)
        if remain is not None:
            p.drawText(inner, Qt.AlignRight | Qt.AlignBottom, f"-{fmt_t(remain)}")
        elif waiting:
            p.drawText(inner, Qt.AlignRight | Qt.AlignBottom, tr("pre-wait…"))
        elif self.panel.mw.show_data.settings.get("go_target", "main") == "main" and \
                nxt is not None and nxt.id == cue.id:
            p.drawText(inner, Qt.AlignRight | Qt.AlignBottom, tr("▶ GO"))

    # ---- mouse
    def mousePressEvent(self, e):
        cell = self.cell_at(e.position().toPoint())
        if cell is None:
            return
        cue = self.panel.cell_cue(cell)
        if e.button() == Qt.RightButton:
            self.panel.cell_menu(cell, cue, e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        if self._pfl_click(cue, e):
            return
        if self.panel.editing or self.panel.group_id:     # Opzioni o dentro un gruppo: si seleziona e si sposta
            if cue is not None:
                self.panel.mw.grid_select(cue, move_playhead=True)
                self.drag_from = (cue, e.position().toPoint())
            self.update()
            return
        if cue is not None:
            self.panel.mw.grid_activate(cue)
            self.panel.mw.grid_select(cue, move_playhead=False)
            self.update()

    def _pfl_click(self, cue, e):
        """Clic sul pulsante PFL della casella: preascolto acceso/spento (la cue non parte)."""
        r = getattr(self, "pfl_hits", {}).get(cue.id) if cue is not None else None
        if r is None or not r.contains(e.position()):
            return False
        self.panel.mw.toggle_pfl(cue)
        self.update()
        return True

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
            if target is not None:
                tcue = self.panel.cell_cue(target)
                if self.panel.group_id:
                    self.panel.mw.grid_group_move(cue, self.panel.cell_index(target))
                elif tcue is not None and tcue.type == "group" and cue.type != "group" and tcue is not cue:
                    self.panel.mw.grid_add_to_group(cue, tcue)       # casella trascinata su un gruppo: ci entra
                elif list(target) != list(cue.grid_pos or []):
                    self.panel.mw.grid_move(cue, target)
            self.update()

    def mouseDoubleClickEvent(self, e):
        cell = self.cell_at(e.position().toPoint())
        if cell is None:
            return
        cue = self.panel.cell_cue(cell)
        if self._pfl_click(cue, e):           # doppio clic sul PFL = due clic
            return
        if cue is None:
            self.panel.new_cue_menu(cell, e.globalPosition().toPoint())
        elif cue.type == "group" and self.panel.editing and not self.panel.group_id:
            self.panel.open_group(cue)
        elif self.panel.editing or self.panel.group_id:
            self.panel.mw.grid_select(cue, move_playhead=False)
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
            if self.panel.group_id:
                self.panel.mw.grid_group_add_files(paths, self.panel.group_cue(), self.panel.cell_index(cell))
            else:
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
        self.group_id = None      # gruppo «aperto»: si vedono e si modificano le sue cue
        self._pages = {}          # pagina mostrata per ogni PANEL
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.group_bar = QWidget()
        self.group_bar.setObjectName("groupBar")
        self.group_bar.setStyleSheet("#groupBar{background:#342c4e;border-radius:5px;}")
        gb = QHBoxLayout(self.group_bar)
        gb.setContentsMargins(6, 3, 6, 3)
        back = QPushButton(tr("◀ Torna alla griglia"))
        back.clicked.connect(self.close_group)
        gb.addWidget(back)
        self.group_label = QLabel()
        self.group_label.setStyleSheet("color:#e8e8ee;font-weight:bold;")
        gb.addWidget(self.group_label, 1)
        self.group_bar.setVisible(False)
        v.addWidget(self.group_bar)
        bar = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._page_menu)
        self.tabs.currentChanged.connect(self._page_changed)
        self.tabs.tabBarDoubleClicked.connect(self.rename_page)
        bar.addWidget(self.tabs, 1)
        self.add_page_btn = QPushButton(tr("+ Pagina"))
        self.add_page_btn.setToolTip(tr("Aggiungi una pagina alla griglia"))
        self.add_page_btn.clicked.connect(self.add_page)
        bar.addWidget(self.add_page_btn)
        self.size_box = QWidget()
        sb = QHBoxLayout(self.size_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.addWidget(QLabel(tr("Righe")))
        self.rows = QSpinBox()
        self.rows.setRange(1, 16)
        sb.addWidget(self.rows)
        sb.addWidget(QLabel(tr("Colonne")))
        self.cols = QSpinBox()
        self.cols.setRange(1, 16)
        sb.addWidget(self.cols)
        self.click = QComboBox()
        for k, t in CLICK_MODES:
            self.click.addItem(t, k)
        sb.addWidget(self.click)
        sb.addWidget(QLabel(tr("Fade out")))
        self.fade = QDoubleSpinBox()
        self.fade.setRange(0, 60)
        self.fade.setDecimals(1)
        self.fade.setSingleStep(0.1)
        self.fade.setSuffix(" s")
        self.fade.setKeyboardTracking(False)
        self.fade.setSpecialValueText(tr("nessuno"))
        self.fade.setToolTip(tr("Sfumatura usata dagli stop del GRID: clic su una casella attiva, modalità Solo, "
                             "STOP del PANEL"))
        sb.addWidget(self.fade)
        self.rows.valueChanged.connect(self._size_changed)
        self.cols.valueChanged.connect(self._size_changed)
        self.click.currentIndexChanged.connect(self._click_changed)
        self.fade.valueChanged.connect(self._fade_changed)
        bar.addWidget(self.size_box)
        # Solo e BLIND sempre a portata di mano (anche con le Opzioni chiuse)
        self.solo = QCheckBox("SOLO")
        self.solo.setToolTip(tr("Quando parte una cue (audio, video, immagine, testo o gruppo) le altre attive di "
                             "questo GRID si fermano con il fade out (impostabile nelle Opzioni). "
                             "Viene salvato nello show."))
        self.solo.toggled.connect(self._solo_changed)
        bar.addWidget(self.solo)
        self.blind = QCheckBox("BLIND")
        self.blind.setToolTip(tr("Prova cieca: le cue di questo GRID funzionano (tempi, barre, vumeter, GO) ma non "
                              "mandano audio né video in uscita. Le cue di controllo (OSC, MIDI, mixer…) agiscono "
                              "comunque. Non viene salvato nello show."))
        self.blind.toggled.connect(self._blind_changed)
        bar.addWidget(self.blind)
        self.edit_btn = QPushButton(tr("⚙ Opzioni"))
        self.edit_btn.setCheckable(True)
        self.edit_btn.setToolTip(tr("Opzioni: righe, colonne, pagine, clic e fade out. Con le Opzioni aperte un "
                                 "clic seleziona (ispettore), si trascina per spostare, doppio clic su una casella "
                                 "vuota crea una cue e su un gruppo lo apre. Chiuse: clic avvia la cue."))
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
        g = self.group_cue()
        if self.group_id and g is None:          # gruppo eliminato o spostato altrove
            self.group_id = None
        self.group_bar.setVisible(g is not None)
        if g is not None:
            from .groups import GROUP_SHORT
            n = len(self.members())
            self.group_label.setText(tr("◫ Dentro il gruppo «{0}» ({1}, {2} cue): l'ordine delle caselle è l'ordine di esecuzione", g.name or g.number, GROUP_SHORT.get(g.group_mode, ''), n))
        names = self.page_names()
        self.page = max(0, min(self.page, len(names) - 1))
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for name in names:
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
        self.solo.blockSignals(True)
        self.solo.setChecked(bool(lst.grid_solo))
        self.solo.blockSignals(False)
        self._style_solo()
        self.fade.blockSignals(True)
        self.fade.setValue(float(lst.grid_fade or 0))
        self.fade.blockSignals(False)
        self.add_page_btn.setVisible(self.editing and g is None)
        self._sync_blind()
        self._update_hint()
        self.canvas.update()

    def _sync_blind(self):
        on = self.lst is not None and self.lst.id in self.mw.ctrl.blind_lists
        self.blind.blockSignals(True)
        self.blind.setChecked(on)
        self.blind.blockSignals(False)
        self.blind.setStyleSheet("QCheckBox{background:#b3261e;color:white;font-weight:bold;padding:2px 8px;"
                                 "border-radius:4px;}" if on else "QCheckBox{font-weight:bold;}")

    def _blind_changed(self, on):
        if self.lst is not None:
            self.mw.ctrl.set_blind(self.lst.id, bool(on))
            self.mw.status(tr("BLIND {0} su «{1}»", tr('attivo') if on else tr('spento'), self.lst.name)
                           + (tr(": niente audio né video in uscita da questo GRID") if on else ""))
        self._sync_blind()
        self._update_hint()
        self.canvas.update()

    # ---- caselle: griglia del PANEL oppure cue del gruppo aperto
    def group_cue(self):
        if not self.group_id or self.lst is None:
            return None
        c = next((x for x in self.lst.cues if x.id == self.group_id), None)
        return c if (c is not None and c.type == "group") else None

    def members(self):
        return [c for c in self.lst.cues if c.group == self.group_id] if self.group_id else []

    def cells_per_page(self):
        return self.lst.grid_rows * self.lst.grid_cols

    def page_names(self):
        if self.group_cue() is None:
            return list(self.lst.grid_pages)
        n = len(self.members()) + 1                # sempre una casella libera per aggiungere
        pages = max(1, -(-n // self.cells_per_page()))
        return [tr("Pagina {0}", i + 1) for i in range(pages)]

    def cell_index(self, cell):
        p, r, c = cell
        return p * self.cells_per_page() + r * self.lst.grid_cols + c

    def cue_map(self):
        if self.group_cue() is None:
            return G.occupied(self.lst)
        cols, per = self.lst.grid_cols, self.cells_per_page()
        return {(i // per, (i % per) // cols, i % cols): c for i, c in enumerate(self.members())}

    def cell_cue(self, cell):
        return self.cue_map().get(tuple(cell))

    def open_group(self, cue):
        self.group_id = cue.id
        self._outer_page = self.page
        self.page = 0
        self.mw.grid_select(cue, move_playhead=False)
        self.sync()

    def close_group(self):
        self.group_id = None
        self.page = getattr(self, "_outer_page", 0)
        self.sync()

    def _update_hint(self):
        if self.lst is not None and self.lst.id in self.mw.ctrl.blind_lists:
            self.hint.setStyleSheet("color:#ff6b5e;font-size:11px;font-weight:bold;")
            pre = tr("BLIND: le cue di questo GRID non mandano audio né video in uscita · ")
        else:
            self.hint.setStyleSheet("color:#888;font-size:11px;")
            pre = ""
        self._hint_prefix = pre
        self._set_hint_body()

    def _set_hint_body(self):
        pre = getattr(self, "_hint_prefix", "")
        if self.group_cue() is not None:
            self.hint.setText(pre + tr("Gruppo aperto: clic = seleziona (ispettore) · trascina = cambia l'ordine · doppio clic "
                              "su una casella vuota = nuova cue nel gruppo · trascina qui dei file = nuove cue nel "
                              "gruppo · tasto destro = avvia, togli dal gruppo, elimina…"))
        elif self.editing:
            self.hint.setText(pre + tr("Opzioni: clic = seleziona · trascina = sposta (o scambia; su un gruppo = entra nel "
                              "gruppo) · doppio clic su una casella vuota = nuova cue, su un gruppo = lo apre · tasto "
                              "destro = colore, scorciatoia, elimina · trascina qui dei file audio/video per creare le cue."))
        else:
            mode = dict(CLICK_MODES).get(self.lst.grid_click if self.lst else "toggle", "")
            solo = tr(" · Solo") if (self.lst and self.lst.grid_solo) else ""
            self.hint.setText(tr("{0}{1}{2} · tasto destro: colore, scorciatoia, nuova cue, apri gruppo · il GO del PANEL avanza casella dopo casella.", pre, mode, solo))

    def set_editing(self, on):
        self.editing = bool(on)
        self.size_box.setVisible(self.editing)
        self.add_page_btn.setVisible(self.editing and self.group_cue() is None)
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

    def _solo_changed(self, on):
        if self.lst is not None:
            self.lst.grid_solo = bool(on)
            self.mw.show_data.dirty = True
            self.mw.status(tr("SOLO {0} su «{1}»", tr('attivo') if on else tr('spento'), self.lst.name))
            self._update_hint()
        self._style_solo()

    def _style_solo(self):
        on = self.solo.isChecked()
        self.solo.setStyleSheet("QCheckBox{background:#c98a00;color:black;font-weight:bold;padding:2px 8px;"
                                "border-radius:4px;}" if on else "QCheckBox{font-weight:bold;}")

    def _fade_changed(self, v):
        if self.lst is not None:
            self.lst.grid_fade = float(v)
            self.mw.show_data.dirty = True

    def _click_changed(self, j):
        if self.lst is not None:
            self.lst.grid_click = self.click.itemData(j)
            self.mw.show_data.dirty = True
            self._update_hint()

    # ---- pagine
    def add_page(self):
        lst = self.lst
        lst.grid_pages.append(tr("Pagina {0}", len(lst.grid_pages) + 1))
        self.page = len(lst.grid_pages) - 1
        self.mw.show_data.dirty = True
        self.sync()

    def rename_page(self, i):
        if i < 0 or self.group_cue() is not None:
            return
        name, ok = QInputDialog.getText(self, tr("Rinomina pagina"), tr("Nome della pagina:"), text=self.lst.grid_pages[i])
        if ok and name.strip():
            self.lst.grid_pages[i] = name.strip()
            self.mw.show_data.dirty = True
            self.sync()

    def delete_page(self, i):
        if G.page_cues(self.lst, i):
            QMessageBox.information(self, tr("Elimina pagina"), tr("La pagina contiene delle cue: spostale su un'altra "
                                    "pagina (trascinandole in modifica) o eliminale prima."))
            return
        if G.delete_page(self.lst, i):
            self.mw.show_data.dirty = True
            self.sync()

    def _page_menu(self, pos):
        if self.group_cue() is not None:
            return
        i = self.tabs.tabAt(pos)
        m = QMenu(self)
        m.addAction(tr("Nuova pagina"), self.add_page)
        if i >= 0:
            m.addAction(tr("Rinomina…"), lambda: self.rename_page(i))
            a = m.addAction(tr("Elimina pagina"), lambda: self.delete_page(i))
            a.setEnabled(len(self.lst.grid_pages) > 1)
        m.exec(self.tabs.mapToGlobal(pos))

    # ---- menu delle caselle
    def new_cue_menu(self, cell, gpos):
        m = QMenu(self)
        self._fill_new(m, cell)
        m.exec(gpos)

    def _fill_new(self, m, cell):
        g = self.group_cue()
        for t in CUE_TYPES:
            if t == "groupexit" or (g is not None and t == "group"):     # niente gruppi dentro i gruppi
                continue
            if g is not None:
                m.addAction(f"{TYPE_ICONS.get(t, '')}  {TYPE_LABELS[t]}",
                            lambda _=False, t=t: self.mw.grid_group_add_cue(t, g, self.cell_index(cell)))
            else:
                m.addAction(f"{TYPE_ICONS.get(t, '')}  {TYPE_LABELS[t]}",
                            lambda _=False, t=t: self.mw.grid_add_cue(t, list(cell)))

    def cell_menu(self, cell, cue, gpos):
        mw = self.mw
        m = QMenu(self)
        g = self.group_cue()
        if cue is None:
            self._fill_new(m.addMenu(tr("Nuova cue nel gruppo") if g is not None else tr("Nuova cue qui")), cell)
            if g is not None:
                m.addSeparator()
                m.addAction(tr("◀ Torna alla griglia"), self.close_group)
            m.exec(gpos)
            return
        m.addAction(f"{TYPE_ICONS.get(cue.type, '')}  {cue.label()}").setEnabled(False)
        m.addSeparator()
        if cue.type == "group" and g is None:
            m.addAction(tr("◫ Apri il gruppo (aggiungi e modifica le sue cue)"), lambda: self.open_group(cue))
            m.addSeparator()
        m.addAction(tr("▶ Avvia") + (tr(" (solo questa cue)") if g is not None else ""),
                    lambda: mw.ctrl.trigger(cue, cascade=False))
        m.addAction(tr("■ Ferma"), lambda: mw.ctrl.stop_cue(cue.id, float(self.lst.grid_fade or 0)))
        if g is None:
            m.addAction(tr("Imposta come prossima del GO"), lambda: mw.grid_select(cue, move_playhead=True))
        m.addSeparator()
        cm = m.addMenu(tr("Colore"))
        for col in G.PALETTE:
            a = cm.addAction("■  " + col)
            a.triggered.connect(lambda _=False, col=col: mw.grid_set_color(cue, col))
        cm.addSeparator()
        cm.addAction(tr("Altro colore…"), lambda: self.pick_color(cue))
        cm.addAction(tr("Nessun colore"), lambda: mw.grid_set_color(cue, ""))
        m.addAction(tr("Impara scorciatoia da tastiera…"), lambda: mw.learn_hotkey(cue))
        a = m.addAction(tr("Togli scorciatoia"), lambda: mw.set_hotkey(cue, ""))
        a.setEnabled(bool(cue.hotkey))
        m.addSeparator()
        m.addAction(tr("Modifica nell'ispettore"), lambda: mw.grid_select(cue, move_playhead=False))
        if g is not None:
            m.addAction(tr("Togli dal gruppo (diventa una casella)"), lambda: mw.grid_ungroup_member(cue))
        m.addAction(tr("Elimina cue…"), lambda: mw.grid_delete(cue))
        if g is not None:
            m.addSeparator()
            m.addAction(tr("◀ Torna alla griglia"), self.close_group)
        m.exec(gpos)

    def pick_color(self, cue):
        col = QColorDialog.getColor(QColor(cue.color or DEFAULT_BG), self, tr("Colore della casella"))
        if col.isValid():
            self.mw.grid_set_color(cue, col.name())


class HotkeyDialog(QDialog):
    """«Impara» una scorciatoia: si preme la combinazione di tasti e la si conferma."""

    def __init__(self, cue, reserved, others, parent=None, current=None, purpose=""):
        super().__init__(parent)
        self.setWindowTitle(tr("Scorciatoia da tastiera"))
        current = cue.hotkey if current is None else current
        self.reserved = reserved       # {testo: descrizione} già usate dal programma
        self.others = others           # {testo: cue} già usate da altre cue
        self.result_key = None
        v = QVBoxLayout(self)
        v.addWidget(QLabel(tr("Premi la combinazione di tasti per {0}«{1}»\n(es. F5, 1, Q, Ctrl+1). La scorciatoia funziona con la finestra di CueStage attiva,\nma non mentre stai scrivendo in un campo di testo.", purpose, cue.label())))
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
            self.msg.setText(tr("«{0}» è già usata da CueStage ({1}): scegline un'altra.", k, self.reserved[k]))
            self.msg.setStyleSheet("color:#e66;")
            ok.setEnabled(False)
            return
        if k in self.others:
            self.steal = self.others[k]
            sc, sf = self.steal
            what = tr("il Tap Tempo di ") if sf == "group_tap_key" else ""
            self.msg.setText(tr("«{0}» è già assegnata a {1}«{2}»: confermando passa qui.", k, what, sc.label()))
            self.msg.setStyleSheet("color:#e0b040;")
        else:
            self.msg.setText(tr("«{0}» è libera.", k))
            self.msg.setStyleSheet("color:#6c6;")
        ok.setEnabled(True)


def point(x, y):
    return QPoint(int(x), int(y))
