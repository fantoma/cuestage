"""Pannello a scomparsa «Proiettori»: controllo diretto di tutti i proiettori configurati.

Per ogni proiettore mostra i comandi che il suo driver/plugin dichiara (CAPS, INPUTS, EXTRA_ACTIONS):
accensione, otturatore, freeze, ingresso, ottica (zoom, fuoco, shift — tenendo premuto il pulsante il
movimento continua), lens memory, azioni specifiche del modello, comando libero e lettura dello stato.
Anche col pannello in primo piano valgono i tasti di regia (Spazio = GO, Esc = STOP TUTTO, F7, F8).
"""
from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .projectors import ACTIONS, CORNER_KEYS, CORNER_LABELS, LENS_ACTIONS, SPEEDS, format_corners

LENS_LABELS = {"zoom": ("Zoom", "−", "+"), "focus": ("Fuoco", "−", "+"),
               "shift_h": ("Shift orizz.", "◀", "▶"), "shift_v": ("Shift vert.", "▼", "▲")}


class _Relay(QObject):
    status = Signal(str, bool, object)
    corners = Signal(str, bool, object)


def _btn(text, tip=None, color=None, width=None):
    b = QPushButton(text)
    b.setFocusPolicy(Qt.NoFocus)
    if tip:
        b.setToolTip(tip)
    if color:
        b.setStyleSheet(f"QPushButton{{background:{color};color:white;font-weight:bold;border-radius:4px;"
                        f"padding:3px 8px;}}QPushButton:pressed{{background:#555;}}")
    if width:
        b.setFixedWidth(width)
    return b


class ProjectorBox(QGroupBox):
    """Comandi di un proiettore."""

    def __init__(self, conf, manager, cls, parent=None):
        super().__init__(parent)
        self.conf = conf
        self.pm = manager
        self.pid = conf.get("id")
        self.name = conf.get("name") or conf.get("host") or "Proiettore"
        self.cls = cls
        self._lens_busy = None          # ultimo comando ottica in coda (per non accumulare ripetizioni)
        self.setTitle(self.name)
        v = QVBoxLayout(self)
        v.setSpacing(4)
        info = QLabel((cls.NAME if cls else f"driver «{conf.get('driver')}» non disponibile")
                      + (f" · {conf.get('host')}" if conf.get("host") else ""))
        info.setStyleSheet("color:#999;")
        v.addWidget(info)
        if cls is None:
            return
        caps = set(cls.CAPS)

        row = QHBoxLayout()
        if "power_on" in caps:
            b = _btn("⏻ Accendi", color="#2c6b3c")
            b.clicked.connect(lambda: self.run("power_on"))
            row.addWidget(b)
        if "power_off" in caps:
            b = _btn("⏻ Spegni", "Mette il proiettore in standby (chiede conferma)", "#6b2c2c")
            b.clicked.connect(self._power_off)
            row.addWidget(b)
        if "shutter_close" in caps:
            b = _btn("■ Chiudi otturatore", color="#444")
            b.clicked.connect(lambda: self.run("shutter_close"))
            row.addWidget(b)
        if "shutter_open" in caps:
            b = _btn("□ Apri otturatore", color="#555")
            b.clicked.connect(lambda: self.run("shutter_open"))
            row.addWidget(b)
        row.addStretch(1)
        if row.count() > 1:
            v.addLayout(row)

        row = QHBoxLayout()
        if "freeze_on" in caps or "freeze_off" in caps:
            row.addWidget(QLabel("Freeze"))
            for a, t in (("freeze_on", "attiva"), ("freeze_off", "disattiva")):
                if a in caps:
                    b = _btn(t)
                    b.clicked.connect(lambda _=False, a=a: self.run(a))
                    row.addWidget(b)
            row.addSpacing(10)
        if "input" in caps:
            row.addWidget(QLabel("Ingresso"))
            self.input_cb = QComboBox()
            self.input_cb.setFocusPolicy(Qt.NoFocus)
            for code, lab in cls.INPUTS:
                self.input_cb.addItem(lab, code)
            row.addWidget(self.input_cb)
            b = _btn("Seleziona")
            b.clicked.connect(lambda: self.run("input", self.input_cb.currentData() or "",
                                               label=f"Ingresso {self.input_cb.currentText()}"))
            row.addWidget(b)
        row.addStretch(1)
        if row.count() > 1:
            v.addLayout(row)

        lens = [a for a in LENS_ACTIONS if a in caps]
        if lens:
            g = QGridLayout()
            g.setHorizontalSpacing(4)
            g.setVerticalSpacing(2)
            for i, a in enumerate(lens):
                lab, minus, plus = LENS_LABELS[a]
                g.addWidget(QLabel(lab), i, 0)
                for col, (txt, d) in enumerate(((minus, -1), (plus, 1))):
                    b = _btn(txt, f"{lab} {txt} · tieni premuto per continuare", width=36)
                    b.setAutoRepeat(True)
                    b.setAutoRepeatDelay(450)
                    b.setAutoRepeatInterval(250)
                    b.clicked.connect(lambda _=False, a=a, d=d: self.lens(a, d))
                    g.addWidget(b, i, 1 + col)
            g.addWidget(QLabel("Velocità"), 0, 3, Qt.AlignRight)
            self.speed_cb = QComboBox()
            self.speed_cb.setFocusPolicy(Qt.NoFocus)
            for k, t in SPEEDS.items():
                self.speed_cb.addItem(t, k)
            self.speed_cb.setCurrentIndex(1)
            g.addWidget(self.speed_cb, 0, 4)
            g.addWidget(QLabel("Passi"), 1, 3, Qt.AlignRight)
            self.steps_sp = QSpinBox()
            self.steps_sp.setRange(1, 50)
            self.steps_sp.setFocusPolicy(Qt.ClickFocus)
            g.addWidget(self.steps_sp, 1, 4)
            g.setColumnStretch(5, 1)
            v.addLayout(g)

        row = QHBoxLayout()
        if "lens_memory" in caps:
            row.addWidget(QLabel("Lens memory"))
            self.mem_sp = QSpinBox()
            self.mem_sp.setRange(1, 10)
            self.mem_sp.setFocusPolicy(Qt.ClickFocus)
            row.addWidget(self.mem_sp)
            b = _btn("Richiama")
            b.clicked.connect(lambda: self.run("lens_memory", str(self.mem_sp.value()),
                                               label=f"Lens memory {self.mem_sp.value()}"))
            row.addWidget(b)
            row.addSpacing(10)
        params = getattr(cls, "EXTRA_PARAMS", {}) or {}
        self.extra_edits = {}
        for a, lab in cls.EXTRA_ACTIONS.items():
            if a in params:
                e = QLineEdit()
                e.setPlaceholderText(params[a])
                e.setMaximumWidth(120)
                self.extra_edits[a] = e
                row.addWidget(e)
            b = _btn(lab)
            b.clicked.connect(lambda _=False, a=a, lab=lab: self.run(
                a, self.extra_edits[a].text() if a in self.extra_edits else "", label=lab))
            row.addWidget(b)
        row.addStretch(1)
        if row.count() > 1:
            v.addLayout(row)

        if getattr(cls, "CORNERS", None) and "corners" in caps:
            v.addWidget(self._corners_section(cls))

        if "raw" in caps:
            row = QHBoxLayout()
            row.addWidget(QLabel("Comando libero"))
            self.raw_ed = QLineEdit()
            self.raw_ed.setPlaceholderText("comando del protocollo")
            self.raw_ed.returnPressed.connect(self._raw)
            row.addWidget(self.raw_ed, 1)
            b = _btn("Invia")
            b.clicked.connect(self._raw)
            row.addWidget(b)
            v.addLayout(row)

        row = QHBoxLayout()
        b = _btn("Leggi stato", "Interroga il proiettore (accensione, otturatore, ingresso…)")
        b.clicked.connect(self.query)
        row.addWidget(b)
        self.result = QLabel("—")
        self.result.setWordWrap(True)
        self.result.setStyleSheet("color:#bbb;")
        row.addWidget(self.result, 1)
        v.addLayout(row)

    # ---- comandi
    def run(self, action, value="", direction=1, steps=1, speed="normal", label=None):
        lab = label or ACTIONS.get(action) or self.cls.EXTRA_ACTIONS.get(action, action)
        self.set_result(f"… {lab}", None)
        return self.pm.run(self.pid, action, value, direction, steps, speed, label=lab)

    def lens(self, axis, direction):
        if self._lens_busy is not None and not self._lens_busy.done():
            return                       # pulsante tenuto premuto: niente coda di comandi arretrati
        speed = self.speed_cb.currentData() if hasattr(self, "speed_cb") else "normal"
        steps = self.steps_sp.value() if hasattr(self, "steps_sp") else 1
        lab, minus, plus = LENS_LABELS[axis]
        self._lens_busy = self.run(axis, "", direction, steps, speed, label=f"{lab} {plus if direction > 0 else minus}")

    def _power_off(self):
        r = QMessageBox.question(self.window(), "Spegni proiettore",
                                 f"Mettere in standby «{self.name}»?\n"
                                 "La riaccensione richiede tempo (raffreddamento e riscaldamento lampada).")
        if r == QMessageBox.Yes:
            self.run("power_off")

    def _raw(self):
        t = self.raw_ed.text().strip()
        if t:
            self.run("raw", t, label=f"Comando «{t}»")

    # ---- correzione 4 angoli
    def _corners_section(self, cls):
        from PySide6.QtWidgets import QToolButton
        box = QWidget()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 2, 0, 2)
        bv.setSpacing(3)
        self.corner_toggle = QToolButton()
        self.corner_toggle.setText("▸ Correzione 4 angoli")
        self.corner_toggle.setCheckable(True)
        self.corner_toggle.setFocusPolicy(Qt.NoFocus)
        self.corner_toggle.setStyleSheet("QToolButton{border:none;font-weight:bold;}")
        bv.addWidget(self.corner_toggle)
        body = QWidget()
        body.setVisible(False)
        self.corner_toggle.toggled.connect(lambda on: (body.setVisible(on), self.corner_toggle.setText(
            ("▾" if on else "▸") + " Correzione 4 angoli"), self._resize_panel()))
        g = QGridLayout(body)
        g.setContentsMargins(14, 0, 0, 0)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(2)
        self.corner_spins = {}
        # invio in tempo reale: ogni modifica parte subito; se il proiettore sta ancora rispondendo,
        # appena libero riceve l'ultimo valore (nessuna coda di valori intermedi)
        self._corner_dirty = set()
        self._corner_job = None
        self._corner_mode_on = False
        self._corner_timer = QTimer(self)
        self._corner_timer.setInterval(15)
        self._corner_timer.timeout.connect(self._corner_poll)
        pos = {"tl": (0, 0), "tr": (0, 1), "bl": (1, 0), "br": (1, 1)}
        for k in CORNER_KEYS:
            r, c = pos[k]
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(3)
            lab = QLabel(CORNER_LABELS[k])
            lab.setMinimumWidth(92)
            cl.addWidget(lab)
            for axis in ("x", "y"):
                lo, hi = cls.CORNERS[k][axis]
                sp = QSpinBox()
                sp.setRange(lo, hi)
                sp.setPrefix(f"{axis.upper()} ")
                sp.setFocusPolicy(Qt.ClickFocus)
                sp.setToolTip(f"{CORNER_LABELS[k]} · {'orizzontale' if axis == 'x' else 'verticale'}: "
                              f"{lo}…{hi} (le frecce e la rotella inviano subito)")
                sp.valueChanged.connect(lambda _v, k=k, axis=axis: self._corner_changed(k, axis))
                self.corner_spins[(k, axis)] = sp
                cl.addWidget(sp)
            g.addWidget(cell, r, c)
        row = QHBoxLayout()
        for text, fn, tip in (("Leggi", self.read_corners, "Legge i valori attuali dal proiettore"),
                              ("Azzera", self.reset_corners, "Riporta tutti gli angoli a 0"),
                              ("Invia tutti", self.send_all_corners, "Invia tutti gli 8 valori")):
            b = _btn(text, tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        if "corners_off" in cls.CAPS:
            b = _btn("Disattiva", "Spegne la correzione geometrica (i valori restano nel proiettore)")
            b.clicked.connect(self._corners_off)
            row.addWidget(b)
        row.addStretch(1)
        g.addLayout(row, 2, 0, 1, 2)
        self.corner_values = QLineEdit()
        self.corner_values.setReadOnly(True)
        self.corner_values.setToolTip("Valori da incollare nel campo di una cue Proiettore "
                                      "«Correzione 4 angoli: imposta»")
        vr = QHBoxLayout()
        vr.addWidget(QLabel("Valori per cue:"))
        vr.addWidget(self.corner_values, 1)
        g.addLayout(vr, 3, 0, 1, 2)
        if getattr(cls, "CORNERS_NOTE", ""):
            n = QLabel("⚠ " + cls.CORNERS_NOTE)
            n.setWordWrap(True)
            n.setStyleSheet("color:#d9a441;")
            g.addWidget(n, 4, 0, 1, 2)
        bv.addWidget(body)
        self._update_corner_text()
        return box

    def _resize_panel(self):
        p = self.parent_panel()
        if p is not None and p.isVisible():
            QTimer.singleShot(0, p.fit)

    def corners(self):
        return {k: [self.corner_spins[(k, "x")].value(), self.corner_spins[(k, "y")].value()] for k in CORNER_KEYS}

    def set_corners(self, c):
        for k in CORNER_KEYS:
            for j, axis in enumerate(("x", "y")):
                sp = self.corner_spins[(k, axis)]
                sp.blockSignals(True)
                sp.setValue(int(c[k][j]))
                sp.blockSignals(False)
        self._update_corner_text()

    def _update_corner_text(self):
        if hasattr(self, "corner_values"):
            self.corner_values.setText(format_corners(self.corners()))

    def _corner_changed(self, k, axis):
        self._corner_dirty.add((k, axis))
        self._update_corner_text()
        if self._corner_job is None or self._corner_job.done():
            self._send_corner_changes()
        elif not self._corner_timer.isActive():
            self._corner_timer.start()

    def _corner_poll(self):
        if self._corner_job is not None and not self._corner_job.done():
            return
        self._corner_timer.stop()
        if self._corner_dirty:
            self._send_corner_changes()

    def _send_corner_changes(self):
        items = sorted(self._corner_dirty)
        self._corner_dirty.clear()
        if not items:
            return
        parts = [f"{k} {axis} {self.corner_spins[(k, axis)].value()}" for k, axis in items]
        lab = ", ".join(f"{CORNER_LABELS[k].lower()} {axis.upper()}={self.corner_spins[(k, axis)].value()}"
                        for k, axis in items)
        action = "corner_live" if self._corner_mode_on else "corner_one"
        self._corner_mode_on = True
        self._corner_job = self.run(action, "; ".join(parts), label=f"Angoli: {lab}")
        if self._corner_dirty or not self._corner_timer.isActive():
            self._corner_timer.start()

    def _corners_off(self):
        self._corner_mode_on = False
        self.run("corners_off")

    def send_all_corners(self):
        self._corner_dirty.clear()
        self._corner_mode_on = True
        self._corner_job = self.run("corners", format_corners(self.corners()), label="Correzione 4 angoli")

    def reset_corners(self):
        self.set_corners({k: [0, 0] for k in CORNER_KEYS})
        self.send_all_corners()

    def read_corners(self):
        self.set_result("… lettura angoli", None)
        p = self.parent_panel()
        if p is not None:
            p.relay_corners(self.pid)

    def query(self):
        self.set_result("… lettura stato", None)
        self.parent_panel().relay_status(self.pid)

    def parent_panel(self):
        w = self.parent()
        while w is not None and not isinstance(w, ProjectorPanel):
            w = w.parent()
        return w

    def set_result(self, text, ok):
        col = "#bbb" if ok is None else ("#6c6" if ok else "#e66")
        self.result.setStyleSheet(f"color:{col};")
        self.result.setText(text)
        self.result.setToolTip(text)


class ProjectorPanel(QFrame):
    """Finestrella a comparsa sotto il pulsante «Proiettori» della barra in alto."""
    visibilityChanged = Signal(bool)

    def __init__(self, manager, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("projPanel")
        self.setAutoFillBackground(True)
        self.setStyleSheet("#projPanel{border:1px solid palette(mid);}"
                           "QGroupBox{font-weight:bold;}")
        self.pm = manager
        self.boxes = {}
        self.relay = _Relay()
        self.relay.status.connect(self._status_done)
        self.relay.corners.connect(self._corners_done)
        self.pm.finished.connect(self._done)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 8)
        head = QHBoxLayout()
        t = QLabel("Proiettori")
        t.setStyleSheet("font-weight:bold;font-size:14px;")
        head.addWidget(t)
        head.addStretch(1)
        self.all_row = QWidget()
        ar = QHBoxLayout(self.all_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.addWidget(QLabel("Tutti:"))
        for text, act in (("■ Chiudi otturatori", "shutter_close"), ("□ Apri otturatori", "shutter_open")):
            b = _btn(text)
            b.clicked.connect(lambda _=False, act=act: self.all(act))
            ar.addWidget(b)
        b = _btn("Leggi stati")
        b.clicked.connect(lambda: [bx.query() for bx in self.boxes.values() if bx.cls])
        ar.addWidget(b)
        head.addWidget(self.all_row)
        close = _btn("✕", "Chiudi il pannello", width=28)
        close.clicked.connect(self.hide)
        head.addWidget(close)
        v.addLayout(head)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.addWidget(self.area, 1)
        self.all_row.setVisible(False)      # il contenuto arriva con rebuild() quando ci sono proiettori

    def rebuild(self):
        self.boxes = {}
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        for conf in self.pm.projectors:
            cls = self.pm.registry.get(conf.get("driver"))
            bx = ProjectorBox(conf, self.pm, cls, inner)
            self.boxes[conf.get("id")] = bx
            lay.addWidget(bx)
        lay.addStretch(1)
        old = self.area.takeWidget()
        if old is not None:
            old.deleteLater()
        self.area.setWidget(inner)
        self.all_row.setVisible(len(self.boxes) > 1)

    def all(self, action):
        for bx in self.boxes.values():
            if bx.cls is not None and action in bx.cls.CAPS:
                bx.run(action)

    def relay_corners(self, pid):
        self.pm.query_corners(pid, lambda ok, res, pid=pid: self.relay.corners.emit(pid, ok, res))

    def _corners_done(self, pid, ok, res):
        bx = self.boxes.get(pid)
        if bx is None:
            return
        if ok and isinstance(res, dict):
            bx.set_corners(res)
            bx.set_result("✔ Angoli letti dal proiettore", True)
        else:
            bx.set_result(f"✘ Lettura angoli: {res}", False)

    def fit(self):
        """Ridimensiona il pannello al contenuto (es. aprendo la sezione 4 angoli)."""
        w = self.area.widget()
        if w is None:
            return
        self.resize(max(self.width(), w.sizeHint().width() + 30), min(60 + w.sizeHint().height(), 860))

    def relay_status(self, pid):
        self.pm.query_status(pid, lambda ok, res, pid=pid: self.relay.status.emit(pid, ok, res))

    def _status_done(self, pid, ok, res):
        bx = self.boxes.get(pid)
        if bx is None:
            return
        if ok and isinstance(res, dict):
            res = " · ".join(f"{k}: {v}" for k, v in res.items() if k != "risposta") or str(res)
        bx.set_result(("Stato — " if ok else "✘ ") + str(res), ok)

    def _done(self, name, action, ok, msg):
        for bx in self.boxes.values():
            if bx.name == name:
                if action == ACTIONS["corners_off"]:      # spenta anche da una cue: al prossimo invio si riattiva
                    bx._corner_mode_on = False
                bx.set_result(f"{'✔' if ok else '✘'} {action}: {msg}", ok)

    # ---- posizione
    def popup_under(self, anchor):
        self.adjustSize()
        w = max(600, self.area.widget().sizeHint().width() + 30)
        h = min(60 + self.area.widget().sizeHint().height(), 760)
        self.resize(w, h)
        g = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 4))
        x = g.x() - w
        scr = anchor.screen().availableGeometry() if anchor.screen() else None
        if scr is not None:
            x = max(scr.left(), min(x, scr.right() - w))
        self.move(x, g.y())
        self.show()
        self.raise_()

    def showEvent(self, e):
        super().showEvent(e)
        for bx in self.boxes.values():
            bx._corner_mode_on = False       # la correzione può essere stata spenta dal telecomando
        self.visibilityChanged.emit(True)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.visibilityChanged.emit(False)
