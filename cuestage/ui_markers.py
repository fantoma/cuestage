"""Elenco dei marker di una cue audio/video (scheda «Tempi e loop»): nome, tempo, note e azione.

Azioni: «Nessuna», «Recall Preset» (porta la cue in esecuzione a un suo preset, con un tempo di
transizione) e «Call Cue» (avvia un'altra cue). Le esegue il motore quando la riproduzione passa sul marker."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton, QWidget,
)

from .i18n import tr
from .model import PRESET_KINDS_FOR, TYPE_LABELS

ACTIONS = ("none", "preset", "cue")


def action_labels():
    return {"none": tr("Nessuna"), "preset": tr("Recall Preset"), "cue": tr("Call Cue")}


def preset_names(cue):
    return sorted({n for k in PRESET_KINDS_FOR.get(cue.type, ()) for n in cue.preset_dict(k)}, key=str.lower)


class MarkersPanel(QWidget):
    """Una riga per marker. `changed` quando l'utente modifica qualcosa (lo show è da salvare)."""
    changed = Signal()
    gotoRequested = Signal(float)       # tempo del file
    addRequested = Signal()             # «+ Marker al cursore»

    def __init__(self, cue, show_fn, parent=None):
        super().__init__(parent)
        self.cue = cue
        self.show_fn = show_fn
        self._sig = None
        self._rows = []
        from PySide6.QtWidgets import QVBoxLayout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(3)
        head = QHBoxLayout()
        lab = QLabel(tr("Marker"))
        lab.setStyleSheet("font-weight:bold;")
        head.addWidget(lab)
        add = QPushButton(tr("+ Marker al cursore"))
        add.setToolTip(tr("Aggiunge un marker al cursore del grafico (anche con il tasto M o con il tasto destro)"))
        add.clicked.connect(self.addRequested)
        head.addWidget(add)
        self.empty = QLabel(tr("Nessun marker: tasto destro sul grafico › «Aggiungi marker qui», oppure M."))
        self.empty.setStyleSheet("color:#888;")
        head.addWidget(self.empty)
        head.addStretch(1)
        outer.addLayout(head)
        self.grid = QGridLayout()
        outer.addLayout(self.grid)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(3)
        self.reload()

    def markers(self):
        if not isinstance(getattr(self.cue, "markers", None), list):
            self.cue.markers = []
        return self.cue.markers

    def _signature(self):
        return tuple(id(m) for m in self.markers())

    # ---------------------------------------------------------------- costruzione
    def _clear(self):
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._rows = []

    def reload(self):
        """Ricostruisce le righe se i marker sono cambiati (aggiunti, tolti, riordinati), altrimenti
        aggiorna solo i valori dei campi che non si stanno modificando."""
        sig = self._signature()
        if sig != self._sig:
            self._sig = sig
            self._build()
            return
        for row in self._rows:
            row["sync"]()

    def _build(self):
        self._clear()
        g = self.grid
        ms = self.markers()
        self.empty.setVisible(not ms)
        if not ms:
            return
        for col, txt in enumerate((tr("Nome"), tr("Tempo"), tr("Note"), tr("Azione"))):
            lab = QLabel(txt)
            lab.setStyleSheet("color:#999; font-size:10px;")
            g.addWidget(lab, 0, col)
        for i, m in enumerate(ms):
            self._row(i + 1, m)
        g.setColumnStretch(6, 1)

    def _row(self, r, m):
        g = self.grid
        cue = self.cue

        name = QLineEdit(str(m.get("name") or ""))
        name.setMaximumWidth(130)
        name.setPlaceholderText("M")

        def name_done():
            if name.text().strip() != str(m.get("name") or ""):
                m["name"] = name.text().strip()
                self.changed.emit()
        name.editingFinished.connect(name_done)

        tm = QDoubleSpinBox()
        tm.setRange(0, 360000)
        tm.setDecimals(2)
        tm.setSingleStep(0.1)
        tm.setSuffix(" s")
        tm.setKeyboardTracking(False)
        tm.setValue(float(m.get("t", 0)))
        tm.setToolTip(tr("Posizione nel file (secondi)"))

        def t_changed(v):
            m["t"] = round(float(v), 3)
            self.changed.emit()
        tm.valueChanged.connect(t_changed)
        go = QToolButton()
        go.setText("⌖")
        go.setToolTip(tr("Porta il cursore su questo marker"))
        go.clicked.connect(lambda: self.gotoRequested.emit(float(m.get("t", 0))))
        tw = QWidget()
        th = QHBoxLayout(tw)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(2)
        th.addWidget(tm)
        th.addWidget(go)

        notes = QLineEdit(str(m.get("notes") or ""))
        notes.setPlaceholderText(tr("note"))
        notes.setMaxLength(200)
        notes.setMinimumWidth(160)
        notes.setMaximumWidth(280)

        def notes_done():
            if notes.text() != str(m.get("notes") or ""):
                m["notes"] = notes.text()
                self.changed.emit()
        notes.editingFinished.connect(notes_done)

        act = QComboBox()
        labels = action_labels()
        for k in ACTIONS:
            act.addItem(labels[k], k)
        act.setCurrentIndex(max(0, act.findData(m.get("action") or "none")))
        act.setToolTip(tr("Cosa succede quando la riproduzione arriva al marker:\n"
                          "Recall Preset: la cue va a un suo preset (audio e video) nel tempo indicato\n"
                          "Call Cue: avvia un'altra cue"))

        # parametri dell'azione
        pw = QWidget()
        ph = QHBoxLayout(pw)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(4)
        pre = QComboBox()
        pre.setMinimumWidth(120)
        names = preset_names(cue)
        pre.addItem(tr("— scegli il preset —"), "")
        for n in names:
            pre.addItem(n, n)
        cur = str(m.get("preset") or "")
        if cur and cur not in names:
            pre.addItem(tr("{0} (non presente)", cur), cur)
        pre.setCurrentIndex(max(0, pre.findData(cur)))
        pre.currentIndexChanged.connect(lambda i: (m.__setitem__("preset", pre.itemData(i) or ""), self.changed.emit()))
        fade = QDoubleSpinBox()
        fade.setRange(0, 600)
        fade.setDecimals(1)
        fade.setSingleStep(0.5)
        fade.setSuffix(" s")
        fade.setKeyboardTracking(False)
        fade.setValue(float(m.get("fade", 0) or 0))
        fade.setToolTip(tr("Tempo di transizione verso il preset"))
        fade.valueChanged.connect(lambda v: (m.__setitem__("fade", round(float(v), 2)), self.changed.emit()))
        tgt = QComboBox()
        tgt.setMinimumWidth(200)
        self._fill_targets(tgt, m)
        tgt.currentIndexChanged.connect(lambda i: (m.__setitem__("target", tgt.itemData(i) or ""), self.changed.emit()))
        ph.addWidget(pre)
        ph.addWidget(fade)
        ph.addWidget(tgt)

        def show_params():
            k = act.currentData()
            pre.setVisible(k == "preset")
            fade.setVisible(k == "preset")
            tgt.setVisible(k == "cue")

        def act_changed(_i):
            m["action"] = act.currentData()
            show_params()
            self.changed.emit()
        act.currentIndexChanged.connect(act_changed)
        show_params()

        rm = QToolButton()
        rm.setText("✕")
        rm.setToolTip(tr("Elimina marker"))

        def remove():
            if m in self.markers():
                self.markers().remove(m)
            self.changed.emit()
            self.reload()
        rm.clicked.connect(remove)

        for c, w in enumerate((name, tw, notes, act, pw, rm)):
            g.addWidget(w, r, c)

        def sync():
            for w, val in ((name, str(m.get("name") or "")), (notes, str(m.get("notes") or ""))):
                if not w.hasFocus() and w.text() != val:
                    w.setText(val)
            if not tm.hasFocus():
                tm.blockSignals(True)
                tm.setValue(float(m.get("t", 0)))
                tm.blockSignals(False)
        self._rows.append({"marker": m, "sync": sync})

    def _fill_targets(self, w, m):
        w.addItem(tr("— scegli la cue —"), "")
        show = self.show_fn()
        cue = self.cue
        nums = {}
        for c in show.all_cues():
            n = str(c.number).strip()
            nums[n] = nums.get(n, 0) + 1
        for lst in show.lists:
            for c in lst.cues:
                if c is cue:
                    continue
                num = str(c.number).strip()
                ref = num if num and nums.get(num) == 1 else c.id
                where = "" if lst.is_main else f"[{lst.name}] "
                w.addItem(f"{where}{c.number or '·'} – {c.name or TYPE_LABELS.get(c.type, c.type)}", ref)
        cur = str(m.get("target") or "")
        i = w.findData(cur)
        if i < 0 and cur:
            tg = show.resolve_target(cur)
            if tg is not None:
                i = w.findData(tg.id)
                if i < 0:
                    i = w.findData(str(tg.number).strip())
            if i < 0:
                w.addItem(tr("{0} (non trovata)", cur), cur)
                i = w.count() - 1
        w.setCurrentIndex(max(0, i))
