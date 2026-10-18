"""Ispettore: modifica delle proprietà della cue selezionata."""

from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from .model import (
    TRIGGER_LABELS, TRIGGERS, TYPE_LABELS,
)


from .inspector.control import ControlTabsMixin
from .inspector.media import MediaTabsMixin
from .inspector.mixer import MixerTabMixin
from .inspector.presets import PresetBar  # noqa: F401  (importata anche da qui)
from .inspector.projector import ProjectorTabMixin
from .inspector.text import TextTabMixin


class Inspector(MediaTabsMixin, TextTabMixin, ControlTabsMixin, MixerTabMixin, ProjectorTabMixin, QWidget):
    edited = Signal(bool)        # True se è cambiato il file (va ricaricato)
    testRequested = Signal(str)  # id cue da provare
    stopRequested = Signal(str)
    learnMidi = Signal(object)   # callback(spec)
    learnMidiControl = Signal(object)   # callback(spec) per un controllo continuo (fader)
    oscSend = Signal(str)

    def __init__(self, show_fn, ctrl, loader, parent=None):
        super().__init__(parent)
        self.show_fn = show_fn
        self.ctrl = ctrl
        self.loader = loader
        self.cue = None
        self.bound = []
        self.timeline = None
        self.info_label = None
        self.src_label = self.out_label = None
        self.crop_panel = None
        self.size_fn = None       # cue -> (w, h) del sorgente
        self.out_size_fn = None   # -> (w, h) dell'uscita
        self.hotkey_fn = None     # cue -> «impara» la scorciatoia (finestra principale)
        self.set_hotkey_fn = None  # (cue, tasto) -> imposta/toglie la scorciatoia
        self.color_fn = None      # (cue, "#rrggbb" o "") -> colore della cue
        self.duck_dialog_fn = None  # apre Impostazioni › Ducking
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.header = QLabel("Nessuna cue selezionata")
        self.header.setStyleSheet("font-weight:bold; font-size:13px; padding:4px;")
        lay.addWidget(self.header)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.loader.peaksReady.connect(self._media_ready)
        self.loader.infoReady.connect(self._media_ready)
        self.ph_timer = QTimer(self)
        self.ph_timer.timeout.connect(self._update_playhead)
        self.ph_timer.start(40)

    # ------------------------------------------------------------ binding
    def _emit(self, file_changed=False):
        self.show_fn().dirty = True
        self.edited.emit(file_changed)
        if self.crop_panel is not None:
            self.crop_panel.refresh()
        if getattr(self, "_text_redraw", None) is not None:
            try:
                self._text_redraw()
            except RuntimeError:          # widget già distrutti
                self._text_redraw = None

    def _dspin(self, attr, lo, hi, step=0.1, dec=2, suffix=" s", special=None):
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(dec)
        w.setSingleStep(step)
        w.setSuffix(suffix)
        w.setKeyboardTracking(False)
        if special:
            w.setSpecialValueText(special)
        w.setValue(float(getattr(self.cue, attr) or 0))
        cue = self.cue

        def changed(v):
            setattr(cue, attr, float(v))
            self._emit()
            if self.timeline:
                self.timeline.update()
        w.valueChanged.connect(changed)
        self.bound.append((w, lambda: w.setValue(float(getattr(cue, attr) or 0))))
        return w

    def _ispin(self, attr, lo, hi, special=None, suffix=""):
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setSuffix(suffix)
        w.setKeyboardTracking(False)
        if special:
            w.setSpecialValueText(special)
        w.setValue(int(getattr(self.cue, attr) or 0))
        cue = self.cue
        w.valueChanged.connect(lambda v: (setattr(cue, attr, int(v)), self._emit()))
        self.bound.append((w, lambda: w.setValue(int(getattr(cue, attr) or 0))))
        return w

    def _line(self, attr, placeholder=""):
        w = QLineEdit(str(getattr(self.cue, attr) or ""))
        w.setPlaceholderText(placeholder)
        cue = self.cue
        w.editingFinished.connect(lambda: (setattr(cue, attr, w.text()), self._emit()))
        self.bound.append((w, lambda: w.setText(str(getattr(cue, attr) or ""))))
        return w

    def _check(self, attr, text):
        w = QCheckBox(text)
        w.setChecked(bool(getattr(self.cue, attr)))
        cue = self.cue

        def changed(v):
            setattr(cue, attr, bool(v))
            self._emit()
            if self.timeline:
                self.timeline.update()
        w.toggled.connect(changed)
        self.bound.append((w, lambda: w.setChecked(bool(getattr(cue, attr)))))
        return w

    def _combo(self, attr, keys, labels):
        w = QComboBox()
        for k in keys:
            w.addItem(labels.get(k, k), k)
        cue = self.cue
        cur = getattr(cue, attr)
        if cur in keys:
            w.setCurrentIndex(keys.index(cur))
        w.currentIndexChanged.connect(lambda i: (setattr(cue, attr, w.itemData(i)), self._emit()))
        return w

    def _target_combo(self, on_change=None):
        w = QComboBox()
        w.setEditable(True)
        w.addItem("", "")
        cue = self.cue
        show = self.show_fn()
        nums = {}
        for c in show.all_cues():
            nums[str(c.number).strip()] = nums.get(str(c.number).strip(), 0) + 1
        for lst in show.lists:                       # cue di tutte le liste: MAIN e PANEL
            for c in lst.cues:
                if c is cue:
                    continue
                num = str(c.number).strip()
                ref = num if num and nums.get(num) == 1 else c.id     # numero ambiguo: si usa l'id
                where = "" if lst.is_main else f"[{lst.name}] "
                w.addItem(f"{where}{c.number or '·'} – {c.name or TYPE_LABELS[c.type]}", ref)
        tg = self.show_fn().resolve_target(cue.target)
        if tg is not None:
            i = w.findData(tg.number or tg.id)
            if i < 0:
                i = w.findData(tg.id)
            w.setCurrentIndex(max(0, i))
        else:
            w.setEditText(cue.target)

        def changed(i):
            d = w.itemData(i)
            cue.target = d if d is not None else w.currentText()
            self._emit()
            if on_change:
                on_change()

        def typed():
            txt = w.currentText().strip()
            i = w.findText(txt)
            new = w.itemData(i) if i >= 0 else txt.split(" – ")[0]
            if new == cue.target:
                return
            cue.target = new
            self._emit()
            if on_change:
                on_change()
        w.activated.connect(changed)
        w.lineEdit().editingFinished.connect(typed)
        return w

    def refresh_fields(self):
        for bar in self.findChildren(PresetBar):     # più barre per lo stesso tipo di preset
            try:
                bar.reload()
            except RuntimeError:
                pass
        kb = getattr(self, "audio_kbar", None)
        if kb is not None:
            try:
                kb.reload()
            except RuntimeError:
                self.audio_kbar = None
        for w, fn in self.bound:
            w.blockSignals(True)
            try:
                fn()
            finally:
                w.blockSignals(False)
        if self.crop_panel is not None:
            self.crop_panel.refresh()

    # ------------------------------------------------------------ costruzione
    def set_cue(self, cue):
        if cue is self.cue:
            return
        self.cue = cue
        self.rebuild()

    def rebuild(self):
        cur_tab = self.tabs.currentIndex()
        old_pages = [self.tabs.widget(i) for i in range(self.tabs.count())]
        self._text_redraw = None
        self.tabs.clear()
        for pg in old_pages:   # clear() non le distrugge: evita editor orfani ancora collegati
            pg.deleteLater()
        self.bound = []
        self.timeline = None
        self.info_label = None
        self.src_label = self.out_label = None
        self.crop_panel = None
        self.audio_kbar = None
        cue = self.cue
        if cue is None:
            self.header.setText("Nessuna cue selezionata")
            return
        self.header.setText(f"Cue {cue.number or '·'} — {TYPE_LABELS[cue.type]}")

        def add(widget, title, scroll=True):
            # ogni scheda scorre se il pannello è basso: nessun campo resta nascosto
            if scroll:
                widget = self._scrolled(widget)
            self.tabs.addTab(widget, title)

        add(self._tab_base(), "Base")
        t = cue.type
        if t in ("audio", "video"):
            add(self._tab_media_time(), "Tempi, loop e audio")
        if t in ("video", "image"):
            add(self._tab_visual(), "Video / immagine · crop", scroll=False)
        if t == "fade":
            add(self._tab_fade(), "Fade")
        if t == "preset":
            add(self._tab_preset(), "Cambia preset")
        if t == "control":
            add(self._tab_control(), "Comando")
        if t == "osc":
            add(self._tab_osc(), "OSC")
        if t == "projector":
            add(self._tab_projector(), "Proiettore")
        if t == "mixer":
            add(self._tab_mixer(), "Mixer")
        if t == "text":
            add(self._tab_text(), "Testo")
        if t == "midiout":
            add(self._tab_midiout(), "MIDI out")
        if t == "group":
            add(self._tab_group(), "Gruppo")
        if t == "groupexit":
            add(self._tab_groupexit(), "Esci dal gruppo")
        if t == "stoppanel":
            add(self._tab_stoppanel(), "Stop panel")
        if t == "wait":
            add(self._tab_wait(), "Attesa")
        if 0 <= cur_tab < self.tabs.count():
            self.tabs.setCurrentIndex(cur_tab)
        elif self.tabs.count() > 1:
            self.tabs.setCurrentIndex(1)

    def _hotkey_row(self, cue):
        from PySide6.QtGui import QKeySequence
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        txt = QKeySequence.fromString(cue.hotkey, QKeySequence.PortableText).toString(QKeySequence.NativeText) \
            if cue.hotkey else "— nessuna —"
        lab = QLabel(txt)
        lab.setMinimumWidth(90)
        h.addWidget(lab)
        b = QPushButton("Impara…")
        b.setToolTip("Premi un tasto (o una combinazione) che avvierà questa cue")
        b.clicked.connect(lambda: self.hotkey_fn and self.hotkey_fn(cue))
        h.addWidget(b)
        x = QPushButton("✕")
        x.setFixedWidth(28)
        x.setToolTip("Togli la scorciatoia")
        x.setEnabled(bool(cue.hotkey))
        x.clicked.connect(lambda: self.set_hotkey_fn and self.set_hotkey_fn(cue, ""))
        h.addWidget(x)
        h.addStretch(1)
        return w

    def _color_row(self, cue):
        from PySide6.QtWidgets import QColorDialog
        from .grid import PALETTE
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)
        for col in PALETTE:
            b = QPushButton()
            b.setFixedSize(20, 20)
            b.setToolTip(col)
            border = "2px solid white" if cue.color == col else "1px solid #555"
            b.setStyleSheet(f"background:{col};border:{border};border-radius:3px;")
            b.clicked.connect(lambda _=False, col=col: self.color_fn and self.color_fn(cue, col))
            h.addWidget(b)

        def other():
            from PySide6.QtGui import QColor
            c = QColorDialog.getColor(QColor(cue.color or "#2f3440"), self, "Colore della cue")
            if c.isValid() and self.color_fn:
                self.color_fn(cue, c.name())
        o = QPushButton("Altro…")
        o.clicked.connect(other)
        h.addWidget(o)
        x = QPushButton("✕")
        x.setFixedWidth(28)
        x.setToolTip("Nessun colore")
        x.clicked.connect(lambda: self.color_fn and self.color_fn(cue, ""))
        h.addWidget(x)
        h.addStretch(1)
        return w

    @staticmethod
    def _scrolled(widget):
        sa = QScrollArea()
        sa.setWidget(widget)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        return sa

    def _test_buttons(self):
        row = QHBoxLayout()
        b1 = QPushButton("▶ Prova questa cue")
        b1.setToolTip("Avvia solo questa cue (senza catena e senza spostare il playhead)")
        b1.clicked.connect(lambda: self.testRequested.emit(self.cue.id))
        b2 = QPushButton("■ Stop")
        b2.clicked.connect(lambda: self.stopRequested.emit(self.cue.id))
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        return row

    def _tab_base(self):
        w = QWidget()
        f = QFormLayout(w)
        f.addRow("Numero", self._line("number"))
        f.addRow("Nome", self._line("name"))
        trig = self._combo("trigger", TRIGGERS, TRIGGER_LABELS)
        trig.setToolTip("Manuale: parte con GO.\nInsieme alla precedente: parte quando parte la cue "
                        "sopra, dopo il ritardo.\nAlla fine della precedente: parte quando la cue sopra "
                        "termina, dopo il ritardo.")
        f.addRow("Avvio", trig)
        f.addRow("Ritardo (pre-wait)", self._dspin("delay", 0, 36000, 0.1, 2))
        midi_row = QHBoxLayout()
        ml = self._line("midi", "es. note 1 60  /  cc 1 20  /  pc 1 5  (canale 0 = tutti)")
        midi_row.addWidget(ml)
        lb = QPushButton("Impara")
        cue = self.cue

        def learned(spec):
            cue.midi = spec
            ml.setText(spec)
            self._emit()
        lb.clicked.connect(lambda: self.learnMidi.emit(learned))
        midi_row.addWidget(lb)
        f.addRow("Trigger MIDI", midi_row)
        for field, label, types in (("midi_vol", "Fader MIDI volume", ("audio", "video")),
                                    ("midi_op", "Fader MIDI trasparenza", ("video", "image"))):
            if cue.type not in types:
                continue
            row = QHBoxLayout()
            le = QLineEdit(getattr(cue, field, ""))
            le.setPlaceholderText("es. cc 1 7  /  pb 1  /  pc 1 *  (canale 0 = tutti)")
            le.setToolTip("Controllo continuo (fader/potenziometro) per questa cue: dal vivo se è in "
                          "riproduzione, altrimenti modifica il valore programmato")

            def typed(le=le, field=field):
                from .remote import normalize_control
                v = normalize_control(le.text())
                setattr(cue, field, v)
                if le.text().strip() and not v:
                    le.setStyleSheet("color:#e66;")
                else:
                    le.setStyleSheet("")
                self._emit()
            le.editingFinished.connect(typed)
            row.addWidget(le)
            b = QPushButton("Impara")

            def learned_ctl(spec, le=le, field=field):
                setattr(cue, field, spec)
                le.setText(spec)
                le.setStyleSheet("")
                self._emit()
            b.clicked.connect(lambda _=False, cb=learned_ctl: self.learnMidiControl.emit(cb))
            row.addWidget(b)
            clr = QPushButton("✕")
            clr.setFixedWidth(28)
            clr.setToolTip("Togli il controllo")
            clr.clicked.connect(lambda _=False, le=le, field=field: (le.clear(), setattr(cue, field, ""), self._emit()))
            row.addWidget(clr)
            f.addRow(label, row)
        f.addRow("Trigger OSC", self._line("osc_trigger", "es. /scena/1  (oltre a /cue/<numero>/start)"))
        f.addRow("Scorciatoia", self._hotkey_row(cue))
        f.addRow("Colore", self._color_row(cue))
        notes = QPlainTextEdit(cue.notes)
        notes.setMaximumHeight(70)
        notes.textChanged.connect(lambda: (setattr(cue, "notes", notes.toPlainText()), self._emit()))
        f.addRow("Note", notes)
        f.addRow(self._test_buttons())
        return w


