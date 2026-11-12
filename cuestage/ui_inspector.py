"""Ispettore: modifica delle proprietà della cue selezionata."""

from .i18n import tr
from PySide6.QtCore import Qt, Signal, QTimer
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
from .inspector.switcher import SwitcherTabMixin
from .inspector.presets import PresetBar  # noqa: F401  (importata anche da qui)
from .inspector.projector import ProjectorTabMixin
from .inspector.text import TextTabMixin


class Inspector(MediaTabsMixin, TextTabMixin, ControlTabsMixin, MixerTabMixin, SwitcherTabMixin, ProjectorTabMixin,
                QWidget):
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
        self.file_edit = self.file_state = self.preset_bar = None
        self.size_fn = None       # cue -> (w, h) del sorgente
        self.out_size_fn = None   # -> (w, h) dell'uscita
        self.hotkey_fn = None     # cue -> «impara» la scorciatoia (finestra principale)
        self.set_hotkey_fn = None  # (cue, tasto) -> imposta/toglie la scorciatoia
        self.color_fn = None      # (cue, "#rrggbb" o "") -> colore della cue
        self.duck_dialog_fn = None  # apre Impostazioni › Ducking
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.header = QLabel(tr("Nessuna cue selezionata"))
        self.header.setStyleSheet("font-weight:bold; font-size:13px; padding:4px;")
        lay.addWidget(self.header)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        from PySide6.QtCore import QSettings
        self._tab_keys = []
        self._tab_restoring = False
        self._pref_tab = str(QSettings("CueStage", "CueStage").value("inspector_tab", "") or "")
        self.tabs.currentChanged.connect(self._tab_chosen)
        self.loader.peaksReady.connect(self._media_ready)
        self.loader.thumbsReady.connect(self._media_ready)
        self.loader.infoReady.connect(self._media_ready)
        self.ph_timer = QTimer(self)
        self.ph_timer.timeout.connect(self._update_playhead)
        self.ph_timer.start(40)

    # ------------------------------------------------------------ binding
    def _emit(self, file_changed=False):
        self.show_fn().dirty = True
        self.edited.emit(file_changed)
        for f in list(getattr(self, "_video_sep_syncs", None) or []):   # video: valori collegati all'audio
            try:
                f()
            except RuntimeError:
                pass
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

    def cursor_preview(self):
        """Scheda «Tempi e loop» di una cue video aperta e cursore spostato: l'uscita (e la sua anteprima)
        mostrano il fotogramma al cursore, come con la scheda Video / Immagine."""
        tl = self.timeline
        try:
            return (tl is not None and self.cue is not None and self.cue.type == "video" and tl.isVisible()
                    and getattr(tl, "cursor_used", False))
        except RuntimeError:
            return False

    def refresh_fields(self, skip=None):
        """Rilegge i valori della cue nei campi. skip: widget (con il cursore) da non toccare."""
        bars = list(self.findChildren(PresetBar))   # anche quella accanto ai tab
        if self.preset_bar is not None and self.preset_bar not in bars:
            bars.append(self.preset_bar)
        for bar in bars:
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
            if skip is not None and (w is skip or w.isAncestorOf(skip)):
                continue
            w.blockSignals(True)
            try:
                fn()
            finally:
                w.blockSignals(False)
        if self.crop_panel is not None:
            self.crop_panel.refresh()
        mp = getattr(self, "markers_panel", None)
        if mp is not None:
            try:
                mp.reload()
            except RuntimeError:
                self.markers_panel = None

    # ------------------------------------------------------------ costruzione
    def set_cue(self, cue):
        if cue is self.cue:
            return
        self.cue = cue
        self.rebuild()

    def rebuild(self):
        keys = getattr(self, "_tab_keys", [])
        cur_key = keys[self.tabs.currentIndex()] if 0 <= self.tabs.currentIndex() < len(keys) else ""
        self._tab_restoring = True        # clear() e addTab() cambiano la scheda: non è una scelta dell'utente
        old_pages = [self.tabs.widget(i) for i in range(self.tabs.count())]
        self._text_redraw = None
        self.tabs.clear()
        for pg in old_pages:   # clear() non le distrugge: evita editor orfani ancora collegati
            pg.deleteLater()
        old_corner = self.tabs.cornerWidget(Qt.TopRightCorner)
        if old_corner is not None:
            self.tabs.setCornerWidget(None, Qt.TopRightCorner)
            old_corner.deleteLater()
        self.bound = []
        self.timeline = None
        self.info_label = None
        self.file_edit = None
        self.file_state = None
        self.src_label = self.out_label = None
        self.crop_panel = None
        self.audio_kbar = None
        self.preset_bar = None
        self.markers_panel = None
        cue = self.cue
        if cue is None:
            self.header.setText(tr("Nessuna cue selezionata"))
            self._tab_restoring = False
            return
        self.header.setText(tr("Cue {0} — {1}", cue.number or '·', TYPE_LABELS[cue.type]))

        self._tab_keys = []

        def add(widget, title, scroll=True, key=None):
            # ogni scheda scorre se il pannello è basso: nessun campo resta nascosto
            if scroll:
                widget = self._scrolled(widget)
            self.tabs.addTab(widget, title)
            self._tab_keys.append(key or title)

        add(self._tab_base(), tr("Base"), key="Base")
        add(self._tab_trigger(), tr("Trigger"), key="Trigger")
        t = cue.type
        # video: la scheda Video / Immagine si costruisce prima, perché la sua timeline dei keyframe serve
        # anche alle schede Tempi e loop (cursore sulle miniature) e Audio (matrice al tempo del cursore)
        visual = self._tab_visual() if t in ("video", "image") else None
        if t in ("audio", "video"):
            add(self._tab_media_time(), tr("Tempi e loop"), key="Tempi e loop")
            add(self._tab_audio(), tr("Audio"), key="Audio")
        if t == "image":
            add(self._tab_image_time(), tr("Tempi"), key="Tempi e loop")
        if visual is not None:
            add(visual, tr("Video / Immagine"), scroll=False, key="Video / Immagine")
        if t in ("audio", "video", "image"):
            # preset della cue (audio e video insieme), sulla riga dei tab
            self.preset_bar = self._preset_bar()
            self.tabs.setCornerWidget(self.preset_bar, Qt.TopRightCorner)
            self.preset_bar.show()
        if t == "fade":
            add(self._tab_fade(), tr("Fade"), key="Fade")
        if t == "preset":
            add(self._tab_preset(), tr("Cambia preset"), key="Cambia preset")
        if t == "control":
            add(self._tab_control(), tr("Comando"), key="Comando")
        if t == "osc":
            add(self._tab_osc(), "OSC", key="OSC")
        if t == "projector":
            add(self._tab_projector(), tr("Proiettore"), key="Proiettore")
        if t == "mixer":
            add(self._tab_mixer(), tr("Mixer"), key="Mixer")
        if t == "switcher":
            add(self._tab_switcher(), tr("Regia video"), key="Regia video")
        if t == "text":
            add(self._tab_text(), tr("Testo"), key="Testo")
        if t == "midiout":
            add(self._tab_midiout(), tr("MIDI out"), key="MIDI out")
        if t == "group":
            add(self._tab_group(), tr("Gruppo"), key="Gruppo")
        if t == "groupexit":
            add(self._tab_groupexit(), tr("Esci dal gruppo"), key="Esci dal gruppo")
        if t == "stoppanel":
            add(self._tab_stoppanel(), tr("Stop panel"), key="Stop panel")
        if t == "wait":
            add(self._tab_wait(), tr("Attesa"), key="Attesa")
        if t == "fsclose":
            add(self._tab_fsclose(), tr("Chiudi schermo intero"), key="Chiudi schermo intero")
        # resta sulla stessa scheda (per nome) passando da una cue all'altra; altrimenti la prima propria del tipo
        # l'ultima scheda scelta (anche dopo il riavvio); se questa cue non ce l'ha, la prima del suo tipo
        try:
            pref = self._pref_tab or cur_key
            if pref in self._tab_keys:
                self.tabs.setCurrentIndex(self._tab_keys.index(pref))
            elif self.tabs.count() > 2:
                self.tabs.setCurrentIndex(2)
            else:
                self.tabs.setCurrentIndex(0)
        finally:
            self._tab_restoring = False

    def _tab_chosen(self, i):
        """Scheda scelta dall'utente: si ricorda (anche al prossimo avvio)."""
        if self._tab_restoring or not (0 <= i < len(self._tab_keys)):
            return
        self._pref_tab = self._tab_keys[i]
        from PySide6.QtCore import QSettings
        QSettings("CueStage", "CueStage").setValue("inspector_tab", self._pref_tab)

    def _hotkey_row(self, cue):
        from PySide6.QtGui import QKeySequence
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        txt = QKeySequence.fromString(cue.hotkey, QKeySequence.PortableText).toString(QKeySequence.NativeText) \
            if cue.hotkey else tr("— nessuna —")
        lab = QLabel(txt)
        lab.setMinimumWidth(90)
        h.addWidget(lab)
        b = QPushButton(tr("Impara…"))
        b.setToolTip(tr("Premi un tasto (o una combinazione) che avvierà questa cue"))
        b.clicked.connect(lambda: self.hotkey_fn and self.hotkey_fn(cue))
        h.addWidget(b)
        x = QPushButton("✕")
        x.setFixedWidth(28)
        x.setToolTip(tr("Togli la scorciatoia"))
        x.setEnabled(bool(cue.hotkey))
        x.clicked.connect(lambda: self.set_hotkey_fn and self.set_hotkey_fn(cue, ""))
        h.addWidget(x)
        h.addStretch(1)
        return w

    def _color_button(self, cue):
        """Pulsante col colore della cue: il menu ha i 12 colori, «Altro…» e «Nessun colore»."""
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog, QGridLayout, QMenu, QToolButton, QWidgetAction
        from .grid import PALETTE
        b = QToolButton()
        b.setPopupMode(QToolButton.InstantPopup)
        b.setToolTip(tr("Colore della cue (casella del GRID e riga della lista)"))
        b.setText("    " if cue.color else tr("nessuno"))
        if cue.color:
            b.setStyleSheet(f"QToolButton{{background:{cue.color};border:1px solid #777;border-radius:3px;"
                            f"min-width:44px;}}")
        menu = QMenu(b)
        grid_w = QWidget()
        g = QGridLayout(grid_w)
        g.setContentsMargins(6, 6, 6, 6)
        g.setSpacing(3)

        def pick(col):
            menu.close()
            if self.color_fn:
                self.color_fn(cue, col)
        for i, col in enumerate(PALETTE):
            sw = QPushButton()
            sw.setFixedSize(22, 22)
            sw.setToolTip(col)
            border = "2px solid white" if cue.color == col else "1px solid #555"
            sw.setStyleSheet(f"background:{col};border:{border};border-radius:3px;")
            sw.clicked.connect(lambda _=False, col=col: pick(col))
            g.addWidget(sw, i // 6, i % 6)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(grid_w)
        menu.addAction(wa)
        menu.addSeparator()

        def other():
            c = QColorDialog.getColor(QColor(cue.color or "#2f3440"), self, tr("Colore della cue"))
            if c.isValid() and self.color_fn:
                self.color_fn(cue, c.name())
        menu.addAction(tr("Altro…"), other)
        menu.addAction(tr("Nessun colore"), lambda: self.color_fn and self.color_fn(cue, ""))
        b.setMenu(menu)
        return b

    @staticmethod
    def _scrolled(widget):
        sa = QScrollArea()
        sa.setWidget(widget)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        return sa

    def _test_buttons(self):
        row = QHBoxLayout()
        row.setSpacing(4)
        b1 = QPushButton(tr("▶ Prova questa cue"))
        b1.setToolTip(tr("Avvia solo questa cue (senza catena e senza spostare il playhead)"))
        b1.clicked.connect(lambda: self.testRequested.emit(self.cue.id))
        b2 = QPushButton(tr("■ Stop"))
        b2.clicked.connect(lambda: self.stopRequested.emit(self.cue.id))
        row.addWidget(b1)
        row.addWidget(b2)
        return row

    def _tab_base(self):
        """Numero, nome, prova/stop · file (cue con file) · avvio, ritardo, colore · note."""
        cue = self.cue
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Numero")))
        num = self._line("number")
        num.setMaximumWidth(64)
        num.setToolTip(tr("Numero della cue (unico nello show)"))
        row.addWidget(num)
        row.addSpacing(8)
        row.addWidget(QLabel(tr("Nome")))
        row.addWidget(self._line("name"), 1)
        row.addSpacing(8)
        row.addLayout(self._test_buttons())
        v.addLayout(row)
        if cue.is_media():
            v.addLayout(self._file_row([cue.type]))
            self.file_state = QLabel()
            self.file_state.setStyleSheet("color:#e66;")
            self.file_state.setVisible(False)
            v.addWidget(self.file_state)
            self._update_file_state()
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Avvio")))
        trig = self._combo("trigger", TRIGGERS, TRIGGER_LABELS)
        trig.setToolTip(tr("Manuale: parte con GO.\nInsieme alla precedente: parte quando parte la cue "
                           "sopra, dopo il ritardo.\nAlla fine della precedente: parte quando la cue sopra "
                           "termina, dopo il ritardo.\nSolo trigger: il GO non la avvia (la salta); parte "
                           "con il suo trigger MIDI/OSC, la scorciatoia, una cue Comando o un clic nel GRID. "
                           "Nella lista è segnata con «T»."))
        row.addWidget(trig, 1)
        row.addSpacing(8)
        row.addWidget(QLabel(tr("Ritardo")))
        dl = self._dspin("delay", 0, 36000, 0.1, 2)
        dl.setToolTip(tr("Ritardo (pre-wait): attesa tra il comando e la partenza della cue"))
        row.addWidget(dl)
        row.addSpacing(8)
        row.addWidget(QLabel(tr("Colore")))
        row.addWidget(self._color_button(cue))
        v.addLayout(row)
        v.addWidget(QLabel(tr("Note")))
        notes = QPlainTextEdit(cue.notes)
        notes.setMaximumHeight(90)
        notes.textChanged.connect(lambda: (setattr(cue, "notes", notes.toPlainText()), self._emit()))
        v.addWidget(notes)
        v.addStretch(1)
        return w

    def _tab_trigger(self):
        """Comandi che avviano o regolano la cue: MIDI, fader MIDI, OSC, tastiera."""
        cue = self.cue
        w = QWidget()
        f = QFormLayout(w)
        midi_row = QHBoxLayout()
        ml = self._line("midi", tr("es. note 1 60  /  cc 1 20  /  pc 1 5  (canale 0 = tutti)"))
        midi_row.addWidget(ml)
        lb = QPushButton(tr("Impara"))

        def learned(spec):
            cue.midi = spec
            ml.setText(spec)
            self._emit()
        lb.clicked.connect(lambda: self.learnMidi.emit(learned))
        midi_row.addWidget(lb)
        f.addRow(tr("Trigger MIDI"), midi_row)
        for field, label, types in (("midi_vol", tr("Fader MIDI volume"), ("audio", "video")),
                                    ("midi_op", tr("Fader MIDI trasparenza"), ("video", "image"))):
            if cue.type not in types:
                continue
            row = QHBoxLayout()
            le = QLineEdit(getattr(cue, field, ""))
            le.setPlaceholderText(tr("es. cc 1 7  /  pb 1  /  pc 1 *  (canale 0 = tutti)"))
            le.setToolTip(tr("Controllo continuo (fader/potenziometro) per questa cue: dal vivo se è in "
                             "riproduzione, altrimenti modifica il valore programmato"))

            def typed(le=le, field=field):
                from .remote import normalize_control
                val = normalize_control(le.text())
                setattr(cue, field, val)
                if le.text().strip() and not val:
                    le.setStyleSheet("color:#e66;")
                else:
                    le.setStyleSheet("")
                self._emit()
            le.editingFinished.connect(typed)
            row.addWidget(le)
            b = QPushButton(tr("Impara"))

            def learned_ctl(spec, le=le, field=field):
                setattr(cue, field, spec)
                le.setText(spec)
                le.setStyleSheet("")
                self._emit()
            b.clicked.connect(lambda _=False, cb=learned_ctl: self.learnMidiControl.emit(cb))
            row.addWidget(b)
            clr = QPushButton("✕")
            clr.setFixedWidth(28)
            clr.setToolTip(tr("Togli il controllo"))
            clr.clicked.connect(lambda _=False, le=le, field=field: (le.clear(), setattr(cue, field, ""), self._emit()))
            row.addWidget(clr)
            f.addRow(label, row)
        f.addRow(tr("Trigger OSC"), self._line("osc_trigger", tr("es. /scena/1  (oltre a /cue/<numero>/start)")))
        f.addRow(tr("Scorciatoia"), self._hotkey_row(cue))
        from .model import EVENT_LABELS
        from PySide6.QtWidgets import QCheckBox
        for label, keys in ((tr("Ducking"), ("duck_on", "duck_off")),
                            (tr("Comportamento slave"), ("master_lost", "promoted"))):
            row = QHBoxLayout()
            for key in keys:
                cb = QCheckBox(EVENT_LABELS[key])
                cb.setChecked(key in (cue.event_trig or []))

                def toggled(on, key=key):
                    ev = [k for k in (cue.event_trig or []) if k != key]
                    if on:
                        ev.append(key)
                    cue.event_trig = ev
                    self._emit()
                cb.toggled.connect(toggled)
                row.addWidget(cb)
            row.addStretch(1)
            f.addRow(label, row)
        ev_note = QLabel(tr("Ducking: la cue parte quando l'ingresso del ducking (Impostazioni › Ducking) supera la "
                            "soglia o torna sotto (dopo l'hold). Slave: parte su questa istanza secondaria quando "
                            "il master non risponde più o quando diventa indipendente."))
        ev_note.setWordWrap(True)
        ev_note.setStyleSheet("color:#888; font-size:10px;")
        f.addRow(ev_note)
        if cue.trigger == "trigger":
            note = QLabel(tr("Questa cue è «Solo trigger»: il GO non la avvia, parte solo con i comandi di questa scheda, "
                             "una cue Comando o un clic nel GRID."))
        else:
            note = QLabel(tr("Questi comandi avviano la cue in qualsiasi momento, oltre al GO. Con «Avvio: Solo trigger» "
                             "(scheda Base) il GO la salta."))
        note.setWordWrap(True)
        note.setStyleSheet("color:#888; font-size:10px;")
        f.addRow(note)
        return w
