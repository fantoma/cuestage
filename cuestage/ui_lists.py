"""Tab delle liste di cue: MAIN + PANEL, con il selettore della lista su cui agisce GO."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMenu, QPushButton, QTabBar, QToolButton,
                               QVBoxLayout, QWidget)

GO_COL = QColor(40, 170, 80)


def _dot(col):
    pm = QPixmap(12, 12)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(col)
    p.drawRoundedRect(1, 1, 10, 10, 2, 2)
    p.end()
    return QIcon(pm)


def list_color(i):
    from .ui_cuelist import list_color as lc
    return lc(i)


class ListTabs(QWidget):
    """Barra dei tab sopra la tabella delle cue."""
    currentChanged = Signal(int)
    addRequested = Signal()
    renameRequested = Signal(int)
    deleteRequested = Signal(int)
    moveRequested = Signal(int, int)        # indice, direzione
    addGridRequested = Signal()
    kindRequested = Signal(int, str)        # indice, «list» / «grid»

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self.bar = QTabBar()
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.bar.setFocusPolicy(Qt.NoFocus)
        self.bar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.bar.customContextMenuRequested.connect(self._menu)
        self.bar.tabBarDoubleClicked.connect(lambda i: i > 0 and self.renameRequested.emit(i))
        self.bar.currentChanged.connect(self._changed)
        self.bar.setStyleSheet("QTabBar::tab{padding:4px 14px;font-weight:bold;}"
                               "QTabBar::tab:selected{background:#3a4a6a;color:white;}")
        h.addWidget(self.bar)
        self.add_btn = QToolButton()
        self.add_btn.setText("+ PANEL")
        self.add_btn.setToolTip("Nuova lista di cue (PANEL)")
        self.add_btn.setFocusPolicy(Qt.NoFocus)
        self.add_btn.clicked.connect(self.addRequested.emit)
        h.addWidget(self.add_btn)
        self.add_grid_btn = QToolButton()
        self.add_grid_btn.setText("+ GRID")
        self.add_grid_btn.setToolTip("Nuovo PANEL a caselle (GRID): cue da cliccare o da tastiera, su più pagine")
        self.add_grid_btn.setFocusPolicy(Qt.NoFocus)
        self.add_grid_btn.clicked.connect(self.addGridRequested.emit)
        h.addWidget(self.add_grid_btn)
        h.addStretch(1)
        self.kinds = []
        self._guard = False

    def set_lists(self, names, current, go_index, running, kinds=None):
        """names: nomi delle liste ([0] = MAIN) · go_index: lista di GO · running: set di indici in play."""
        self.kinds = list(kinds or [])
        self._guard = True
        while self.bar.count() > len(names):
            self.bar.removeTab(self.bar.count() - 1)
        for i, n in enumerate(names):
            text = ("▶ " if i in running else "") + n + ("  ⏵GO" if i == go_index else "")
            if i < self.bar.count():
                if self.bar.tabText(i) != text:
                    self.bar.setTabText(i, text)
            else:
                self.bar.addTab(text)
            self.bar.setTabTextColor(i, GO_COL if i == go_index and i != current else QColor())
            if len(names) > 1:          # stesso colore dell'etichetta nel pannello «Cue attive»
                self.bar.setTabIcon(i, _dot(list_color(i)))
            else:
                self.bar.setTabIcon(i, QIcon())
            self.bar.setTabToolTip(i, ("Lista principale" if i == 0 else "PANEL · doppio clic: rinomina · "
                                       "tasto destro: altre azioni")
                                   + ("\nGO agisce su questa lista" if i == go_index else "")
                                   + ("\nCue in riproduzione" if i in running else ""))
        if self.bar.currentIndex() != current:
            self.bar.setCurrentIndex(current)
        self._guard = False

    def _changed(self, i):
        if not self._guard and i >= 0:
            self.currentChanged.emit(i)

    def _menu(self, pos):
        i = self.bar.tabAt(pos)
        m = QMenu(self)
        m.addAction("Nuovo PANEL", self.addRequested.emit)
        m.addAction("Nuovo PANEL GRID (caselle)", self.addGridRequested.emit)
        if i > 0:
            m.addSeparator()
            m.addAction("Rinomina…", lambda: self.renameRequested.emit(i))
            if i < len(self.kinds) and self.kinds[i] == "grid":
                m.addAction("Mostra come lista", lambda: self.kindRequested.emit(i, "list"))
            else:
                m.addAction("Mostra come griglia (GRID)", lambda: self.kindRequested.emit(i, "grid"))
            a = m.addAction("Sposta a sinistra", lambda: self.moveRequested.emit(i, -1))
            a.setEnabled(i > 1)
            a = m.addAction("Sposta a destra", lambda: self.moveRequested.emit(i, 1))
            a.setEnabled(i < self.bar.count() - 1)
            m.addSeparator()
            m.addAction("Elimina PANEL…", lambda: self.deleteRequested.emit(i))
        m.exec(self.bar.mapToGlobal(pos))


class GoTarget(QWidget):
    """Accanto al GO: GO sempre sulla MAIN oppure sul PANEL selezionato."""
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        lab = QLabel("GO su")
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet("color:#888;font-size:11px;")
        v.addWidget(lab)
        self.group = QButtonGroup(self)
        self.btns = {}
        for key, text, tip in (("main", "MAIN", "GO avvia sempre la cue in standby della MAIN, "
                                                 "qualunque lista si stia guardando"),
                               ("panel", "PANEL", "GO segue la lista selezionata nei tab (MAIN o PANEL)")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setFixedSize(64, 26)
            b.setToolTip(tip)
            b.setStyleSheet("QPushButton{font-weight:bold;font-size:11px;}"
                            "QPushButton:checked{background:#1f8f45;color:white;}")
            self.group.addButton(b)
            self.btns[key] = b
            v.addWidget(b)
        self.group.buttonClicked.connect(self._clicked)

    def set_value(self, key):
        self.btns.get(key, self.btns["main"]).setChecked(True)

    def value(self):
        return "panel" if self.btns["panel"].isChecked() else "main"

    def _clicked(self, _b):
        self.changed.emit(self.value())
