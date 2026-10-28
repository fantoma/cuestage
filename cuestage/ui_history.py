"""Finestre della cronologia (annulla / ripeti / torna a un punto) e delle versioni dello show."""
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout, QWidget, )

from .i18n import tr


def fmt_date(iso):
    """«2026-10-28 15:31:12» → «28/10/2026 15:31»."""
    try:
        return time.strftime("%d/%m/%Y %H:%M", time.strptime(iso, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return str(iso or "")


class HistoryDialog(QDialog):
    """Elenco dei passi dall'apertura dello show: doppio clic (o «Torna a questo punto») riporta lo show
    a quel passo; i passi successivi restano grigi e si riprendono con Ripeti finché non si modifica."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(tr("Cronologia delle modifiche"))
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        info = QLabel(tr("Doppio clic su un passo per riportare lo show a quel punto. I passi successivi restano "
                         "disponibili (Ripeti) finché non fai una nuova modifica. La cronologia vale finché lo show "
                         "è aperto: per conservare un punto, creane una versione."))
        info.setWordWrap(True)
        lay.addWidget(info)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda it: self._go())
        self.list.currentRowChanged.connect(lambda _r: self._update_buttons())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.b_undo = QPushButton(tr("↶ Annulla"))
        self.b_undo.clicked.connect(win.undo)
        self.b_redo = QPushButton(tr("↷ Ripeti"))
        self.b_redo.clicked.connect(win.redo)
        self.b_go = QPushButton(tr("Torna a questo punto"))
        self.b_go.clicked.connect(self._go)
        row.addWidget(self.b_undo)
        row.addWidget(self.b_redo)
        row.addStretch(1)
        row.addWidget(self.b_go)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.b_mark = QPushButton(tr("★ Segna punto…"))
        self.b_mark.setToolTip(tr("Dà un nome al passo, per ritrovarlo facilmente"))
        self.b_mark.clicked.connect(lambda: self._with_row(win.history_mark))
        self.b_ver = QPushButton(tr("Crea versione da questo punto…"))
        self.b_ver.setToolTip(tr("Nuova versione dello show com'era a questo passo; lo show attuale non cambia"))
        self.b_ver.clicked.connect(lambda: self._with_row(win.history_to_version))
        close = QPushButton(tr("Chiudi"))
        close.clicked.connect(self.close)
        row.addWidget(self.b_mark)
        row.addWidget(self.b_ver)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)

    def _row_to_step(self, r):
        return len(self.win.history.steps) - 1 - r       # il più recente in alto

    def _with_row(self, fn):
        r = self.list.currentRow()
        if r >= 0:
            fn(self._row_to_step(r))

    def _go(self):
        r = self.list.currentRow()
        if r >= 0:
            self.win.history_go(self._row_to_step(r))

    def refresh(self):
        h = self.win.history
        self.list.blockSignals(True)
        self.list.clear()
        grey = QColor(self.palette().color(self.foregroundRole()))
        grey.setAlpha(110)
        for i in range(len(h.steps) - 1, -1, -1):
            st = h.steps[i]
            when = time.strftime("%H:%M:%S", time.localtime(st.time))
            text = f"{when}   {st.desc}"
            if st.mark:
                text = f"★ {st.mark} — " + text
            if i == h.saved:
                text += "   " + tr("(salvato)")
            it = QListWidgetItem(("▶ " if i == h.index else "    ") + text)
            if i == h.index:
                f = QFont(it.font())
                f.setBold(True)
                it.setFont(f)
            elif i > h.index:
                it.setForeground(grey)
                it.setToolTip(tr("Annullato: si riprende con Ripeti o con un doppio clic"))
            self.list.addItem(it)
        cur = len(h.steps) - 1 - h.index
        self.list.setCurrentRow(cur)
        self.list.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self):
        h = self.win.history
        ok = self.win._history_allowed()
        self.b_undo.setEnabled(ok and h.can_undo())
        self.b_redo.setEnabled(ok and h.can_redo())
        r = self.list.currentRow()
        self.b_go.setEnabled(ok and r >= 0 and self._row_to_step(r) != h.index)
        self.b_mark.setEnabled(r >= 0)
        self.b_ver.setEnabled(ok and r >= 0)


class VersionsDialog(QDialog):
    """Versioni dello show: ognuna ha le sue liste e cue e le sue annotazioni; le impostazioni
    (schede audio, mixer, proiettori, OSC, MIDI) sono comuni."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self._loading = False
        self.setWindowTitle(tr("Versioni dello show"))
        self.resize(760, 500)
        lay = QVBoxLayout(self)
        info = QLabel(tr("Ogni versione ha le sue cue, liste e PANEL e le sue annotazioni; le impostazioni "
                         "(schede audio, mixer, proiettori, OSC, MIDI) sono comuni a tutte. Si lavora e si va in "
                         "scena con la versione attiva (●). Tutte le versioni stanno nello stesso file dello show."))
        info.setWordWrap(True)
        lay.addWidget(info)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(lambda _r: self._load())
        self.list.itemDoubleClicked.connect(lambda _it: self._switch())
        ll.addWidget(self.list, 1)
        self.b_new = QPushButton(tr("＋ Nuova versione da questa…"))
        self.b_new.clicked.connect(lambda: self._sel() and self.win.new_version(self._sel().id))
        self.b_switch = QPushButton(tr("● Rendi attiva"))
        self.b_switch.clicked.connect(self._switch)
        self.b_del = QPushButton(tr("Elimina"))
        self.b_del.clicked.connect(lambda: self._sel() and self.win.delete_version(self._sel().id))
        for b in (self.b_new, self.b_switch, self.b_del):
            ll.addWidget(b)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel(tr("Nome")))
        self.name = QLineEdit()
        self.name.textEdited.connect(self._name_edited)
        rl.addWidget(self.name)
        self.meta = QLabel()
        self.meta.setWordWrap(True)
        self.meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rl.addWidget(self.meta)
        hr = QHBoxLayout()
        hr.addWidget(QLabel(tr("Annotazioni")))
        hr.addStretch(1)
        self.b_stamp = QPushButton(tr("Inserisci data e ora"))
        self.b_stamp.clicked.connect(self._stamp)
        hr.addWidget(self.b_stamp)
        rl.addLayout(hr)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(tr("Cosa cambia in questa versione, per quale teatro o replica, "
                                         "cose da provare, decisioni prese…"))
        self.notes.textChanged.connect(self._notes_edited)
        rl.addWidget(self.notes, 1)
        split.addWidget(right)
        split.setSizes([280, 480])
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton(tr("Chiudi"))
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)

    def _sel(self):
        r = self.list.currentRow()
        vs = self.win.show_data.versions
        return vs[r] if 0 <= r < len(vs) else None

    def refresh(self):
        sh = self.win.show_data
        cur = self._sel()
        cur_id = cur.id if cur is not None else sh.active_version
        self.list.blockSignals(True)
        self.list.clear()
        row = 0
        for i, v in enumerate(sh.versions):
            active = v.id == sh.active_version
            n = sum(len(l.cues) for l in sh.version_lists(v.id))
            it = QListWidgetItem(("● " if active else "   ") + v.name + "\n      " +
                                 tr("{0} cue · modificata {1}", n, fmt_date(v.modified)))
            if active:
                f = QFont(it.font())
                f.setBold(True)
                it.setFont(f)
                it.setToolTip(tr("Versione attiva"))
            self.list.addItem(it)
            if v.id == cur_id:
                row = i
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self._load()

    def _load(self):
        v = self._sel()
        sh = self.win.show_data
        self._loading = True
        try:
            if v is None:
                self.name.clear()
                self.notes.clear()
                self.meta.clear()
                return
            if self.name.text() != v.name:
                self.name.setText(v.name)
            if self.notes.toPlainText() != v.notes:
                self.notes.setPlainText(v.notes)
            active = v.id == sh.active_version
            lists = sh.version_lists(v.id)
            n = sum(len(l.cues) for l in lists)
            bits = [tr("Creata {0} · modificata {1}", fmt_date(v.created), fmt_date(v.modified)),
                    tr("{0} cue in {1} liste", n, len(lists))]
            if active:
                bits.append(tr("● versione attiva"))
            else:
                from .history import History, compare_summary
                h = History()
                bits.append(compare_summary(h._lists_snap(sh.lists, "a"), h._lists_snap(lists, "b")))
            self.meta.setText("\n".join(bits))
            self.b_switch.setEnabled(not active)
            self.b_del.setEnabled(not active and len(sh.versions) > 1)
        finally:
            self._loading = False

    def _name_edited(self, text):
        v = self._sel()
        if v is None or self._loading or not text.strip():
            return
        v.name = text.strip()
        self.win.show_data.dirty = True
        it = self.list.currentItem()
        if it is not None:
            head, _sep, rest = it.text().partition("\n")
            it.setText(head[:3] + v.name + "\n" + rest)
        self.win._update_version_btn()

    def _notes_edited(self):
        v = self._sel()
        if v is None or self._loading:
            return
        v.notes = self.notes.toPlainText()
        self.win.show_data.dirty = True

    def _stamp(self):
        c = self.notes.textCursor()
        c.movePosition(c.MoveOperation.End)
        self.notes.setTextCursor(c)
        pre = "" if not self.notes.toPlainText() or self.notes.toPlainText().endswith("\n\n") else (
            "\n" if self.notes.toPlainText().endswith("\n") else "\n\n")
        self.notes.insertPlainText(pre + time.strftime("%d/%m/%Y %H:%M") + " — ")
        self.notes.setFocus()

    def _switch(self):
        v = self._sel()
        if v is not None:
            self.win.switch_version(v.id)
            self.refresh()
