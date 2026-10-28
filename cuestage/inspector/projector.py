"""Scheda dell'ispettore per le cue proiettore (parte di Inspector)."""

from ..i18n import tr
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox,
    QWidget,
)


class ProjectorTabMixin:
    def _tab_projector(self):
        from ..projectors import LENS_ACTIONS, SPEEDS
        cue = self.cue
        pm = getattr(self.ctrl, "projectors", None)
        w = QWidget()
        f = QFormLayout(w)
        projs = pm.projectors if pm else []
        pc = QComboBox()
        pc.addItem(tr("— scegli —"), "")
        for p in projs:
            cls = pm.registry.get(p.get("driver"))
            pc.addItem(f"{p.get('name')}  ({cls.NAME if cls else p.get('driver')})", p.get("id"))
        i = pc.findData(cue.proj_target)
        if i < 0 and cue.proj_target:
            pc.addItem(tr("{0} (non configurato)", cue.proj_target), cue.proj_target)
            i = pc.count() - 1
        pc.setCurrentIndex(max(0, i))

        def proj_changed(i):
            cue.proj_target = pc.itemData(i) or ""
            self._emit()
            QTimer.singleShot(0, self.rebuild)     # azioni e ingressi dipendono dal driver
        pc.currentIndexChanged.connect(proj_changed)
        f.addRow(tr("Proiettore"), pc)
        if not projs:
            lab = QLabel(tr("Nessun proiettore configurato: Impostazioni › Proiettori…"))
            lab.setStyleSheet("color:#e0b040;")
            f.addRow(lab)
            return w
        conf = pm.find(cue.proj_target)
        cls = pm.registry.get(conf.get("driver")) if conf else None
        if cls is None:
            if conf is not None:
                lab = QLabel(tr("Driver «{0}» non disponibile (plugin mancante).", conf.get('driver')))
                lab.setStyleSheet("color:#e0b040;")
                f.addRow(lab)
            return w
        ac = QComboBox()
        actions = cls.actions()
        for a, lab in actions:
            ac.addItem(lab, a)
        i = ac.findData(cue.proj_action)
        if i < 0:
            if cue.proj_action:
                ac.addItem(tr("{0} (non supportata da questo proiettore)", cue.proj_action), cue.proj_action)
                i = ac.count() - 1
            else:
                i = 0
        ac.setCurrentIndex(i)
        if cue.proj_action != ac.itemData(ac.currentIndex()):
            cue.proj_action = ac.itemData(ac.currentIndex())

        def act_changed(i):
            cue.proj_action = ac.itemData(i)
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        ac.currentIndexChanged.connect(act_changed)
        f.addRow(tr("Azione"), ac)
        act = cue.proj_action
        if act == "input":
            ic = QComboBox()
            ic.setEditable(True)
            for code, lab in cls.INPUTS:
                ic.addItem(f"{lab}  [{code}]", code)
            j = ic.findData(cue.proj_value)
            if j >= 0:
                ic.setCurrentIndex(j)
            else:
                ic.setEditText(cue.proj_value)
            if not cue.proj_value and cls.INPUTS:
                cue.proj_value = cls.INPUTS[0][0]

            def in_sel(j):
                cue.proj_value = ic.itemData(j) or ic.currentText().strip()
                self._emit()

            def in_typed():
                txt = ic.currentText().strip()
                j = ic.findText(txt)
                cue.proj_value = ic.itemData(j) if j >= 0 else txt.split("[")[-1].rstrip("]").strip()
                self._emit()
            ic.activated.connect(in_sel)
            ic.lineEdit().editingFinished.connect(in_typed)
            f.addRow(tr("Ingresso (o codice)"), ic)
        elif act in LENS_ACTIONS:
            dc = QComboBox()
            names = {"zoom": (tr("+ (zoom avanti)"), tr("− (zoom indietro)")), "focus": ("+", "−"),
                     "shift_h": (tr("+ (destra)"), tr("− (sinistra)")), "shift_v": (tr("+ (su)"), tr("− (giù)"))}[act]
            dc.addItem(names[0], 1)
            dc.addItem(names[1], -1)
            dc.setCurrentIndex(0 if int(cue.proj_dir) >= 0 else 1)
            dc.currentIndexChanged.connect(lambda j: (setattr(cue, "proj_dir", dc.itemData(j)), self._emit()))
            f.addRow(tr("Direzione"), dc)
            f.addRow(tr("Passi"), self._ispin("proj_steps", 1, 200))
            f.addRow(tr("Velocità"), self._combo("proj_speed", list(SPEEDS), SPEEDS))
        elif act == "lens_memory":
            sp = QSpinBox()
            sp.setRange(1, 99)
            try:
                sp.setValue(int(cue.proj_value or 1))
            except ValueError:
                sp.setValue(1)
            cue.proj_value = str(sp.value())
            sp.valueChanged.connect(lambda val: (setattr(cue, "proj_value", str(val)), self._emit()))
            f.addRow(tr("Memoria n."), sp)
        elif act == "corners" and getattr(cls, "CORNERS", None):
            from ..projectors import CORNER_KEYS, CORNER_LABELS, format_corners, parse_corners
            vals = parse_corners(cue.proj_value) or {k: [0, 0] for k in CORNER_KEYS}
            spins = {}

            def corners_changed():
                c = {k: [spins[(k, "x")].value(), spins[(k, "y")].value()] for k in CORNER_KEYS}
                cue.proj_value = format_corners(c)
                self._emit()
            for k in CORNER_KEYS:
                row = QHBoxLayout()
                for j, axis in enumerate(("x", "y")):
                    lo, hi = cls.CORNERS[k][axis]
                    sp = QSpinBox()
                    sp.setRange(lo, hi)
                    sp.setPrefix(f"{axis.upper()} ")
                    sp.setValue(max(lo, min(hi, int(vals[k][j]))))
                    sp.valueChanged.connect(lambda _v: corners_changed())
                    spins[(k, axis)] = sp
                    row.addWidget(sp)
                row.addStretch(1)
                f.addRow(CORNER_LABELS[k], row)
            cue.proj_value = format_corners({k: [spins[(k, "x")].value(), spins[(k, "y")].value()]
                                             for k in CORNER_KEYS})
            paste = QLineEdit()
            paste.setPlaceholderText(tr("incolla qui i «Valori per cue» del pannello Proiettori"))

            def pasted():
                c = parse_corners(paste.text())
                if c is None:
                    return
                for k in CORNER_KEYS:
                    for j, axis in enumerate(("x", "y")):
                        spins[(k, axis)].setValue(int(c[k][j]))
                paste.clear()
            paste.editingFinished.connect(pasted)
            f.addRow(tr("Incolla valori"), paste)
            if getattr(cls, "CORNERS_NOTE", ""):
                n = QLabel("⚠ " + cls.CORNERS_NOTE)
                n.setWordWrap(True)
                n.setStyleSheet("color:#d9a441;")
                f.addRow(n)
        elif act == "raw":
            f.addRow(tr("Comando"), self._line("proj_value", tr("comando nel protocollo del proiettore")))
        elif act in cls.EXTRA_ACTIONS:
            f.addRow(tr("Parametro (se serve)"), self._line("proj_value"))
        b = QPushButton(tr("Invia ora"))
        b.clicked.connect(lambda: pm.run(cue.proj_target, cue.proj_action, cue.proj_value, cue.proj_dir,
                                         cue.proj_steps, cue.proj_speed))
        f.addRow(b)
        if cls.NOTES:
            n = QLabel(cls.NOTES)
            n.setWordWrap(True)
            n.setStyleSheet("color:#888; font-size:10px;")
            f.addRow(n)
        return w
