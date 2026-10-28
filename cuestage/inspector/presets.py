"""Barra dei preset con nome della singola cue."""
from ..i18n import tr
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
    QWidget,
)

from ..model import (
    PRESET_LABELS,
    )


class PresetBar(QWidget):
    """Preset con nome della singola cue: applica / salva come nuovo / aggiorna / rinomina / elimina."""
    applied = Signal()
    listChanged = Signal()

    def __init__(self, kind, show_fn, cue, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.show_fn = show_fn
        self.cue = cue
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(PRESET_LABELS[kind] + tr(" di questa cue:"))
        lab.setStyleSheet("font-weight:bold;")
        h.addWidget(lab)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(160)
        h.addWidget(self.combo, 1)
        self.use_lab = QLabel()
        self.use_lab.setStyleSheet("color:#888;")
        h.addWidget(self.use_lab)
        for text, tip, fn in (
            (tr("Applica"), tr("Copia i valori del preset in questa cue"), self.apply),
            (tr("Salva come…"), tr("Crea un nuovo preset con i valori attuali di questa cue"), self.save_new),
            (tr("Aggiorna"), tr("Sovrascrive il preset selezionato con i valori attuali"), self.update_sel),
            (tr("Rinomina…"), tr("Rinomina il preset selezionato"), self.rename),
            (tr("Elimina"), tr("Elimina il preset selezionato"), self.delete),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            h.addWidget(b)
        self.reload()

    def presets(self):
        return self.cue.preset_dict(self.kind)

    def set_in_use(self, state):
        """Mostra il preset in uso al tempo del cursore e lo seleziona nell'elenco."""
        from .. import keyframes as kfm
        if state is None:
            self.use_lab.setText("")
            return
        na, nb, f = state
        cur = nb if f >= 0.999 else na
        self.use_lab.setText(tr("▸ in uso: ") + kfm.describe(state))
        self.use_lab.setStyleSheet("color:#ffaa28;" if self.kind == "visual" else "color:#50c8ff;")
        if cur != kfm.BASE and cur in self.presets():
            self.combo.setCurrentText(cur)

    def reload(self, select=None):
        cur = select or self.combo.currentText()
        self.combo.blockSignals(True)
        self.combo.clear()
        names = sorted(self.presets(), key=str.lower)
        self.combo.addItems(names)
        if cur in names:
            self.combo.setCurrentText(cur)
        self.combo.blockSignals(False)

    def _sel(self):
        n = self.combo.currentText()
        return n if n in self.presets() else None

    def _dirty(self):
        self.show_fn().dirty = True
        self.listChanged.emit()

    def apply(self):
        n = self._sel()
        if n and self.cue.apply_preset(self.kind, n):
            self.show_fn().dirty = True
            self.applied.emit()

    def save_new(self):
        n, ok = QInputDialog.getText(self, tr("Nuovo preset"), tr("Nome del preset:"))
        n = n.strip()
        if not ok or not n:
            return
        if n in self.presets() and QMessageBox.question(
                self, tr("Preset esistente"), tr("Il preset «{0}» esiste già. Sovrascriverlo?", n)) != QMessageBox.Yes:
            return
        self.presets()[n] = self.cue.preset_values(self.kind)
        self.reload(n)
        self._dirty()

    def update_sel(self):
        n = self._sel()
        if n:
            self.presets()[n] = self.cue.preset_values(self.kind)
            self._dirty()

    def rename(self):
        old = self._sel()
        if not old:
            return
        n, ok = QInputDialog.getText(self, tr("Rinomina preset"), tr("Nuovo nome:"), text=old)
        n = n.strip()
        if not ok or not n or n == old:
            return
        p = self.presets()
        p[n] = p.pop(old)
        # le cue «Cambia preset» che puntano a questa cue e usavano il preset seguono il nuovo nome
        field = "preset_audio" if self.kind == "audio" else "preset_visual"
        show = self.show_fn()
        for c in show.all_cues():
            if c.type == "preset" and show.resolve_target(c.target) is self.cue and getattr(c, field) == old:
                setattr(c, field, n)
        self.reload(n)
        self._dirty()

    def delete(self):
        n = self._sel()
        if n and QMessageBox.question(self, tr("Elimina preset"), tr("Eliminare il preset «{0}»?", n)) == QMessageBox.Yes:
            del self.presets()[n]
            self.reload()
            self._dirty()
