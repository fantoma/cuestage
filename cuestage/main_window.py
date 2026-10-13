"""Finestra principale di CueStage."""
import os
import time

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSlider, QSplitter, QTableView,
    QVBoxLayout, QWidget,
)

from .audio_engine import AudioEngine
from .engine import Controller
from .media import MediaLoader
from .model import (
    CUE_TYPES, MIN_DB, TYPE_LABELS, Show, db_to_lin, )
from .remote import MidiInput, OscServer
from .ui_crop import EDIT_KEY
from .ui_cuelist import C_STATE, ActivePanel, CueTableModel, StateDelegate
from .ui_dialogs import HelpDialog, SettingsDialog
from .ui_inspector import Inspector
from .video_output import Compositor, OutputPreview
from .window.cluster import ClusterMixin
from .window.editing import EditingMixin
from .window.files import FilesMixin
from .window.groups import GroupsMixin
from .window.hardware import HardwareMixin
from .window.lists import ListsMixin
from .window.playback import PlaybackMixin
from .window.remote import RemoteMixin
from .window.video import VideoMixin
from .window.web import WebMixin

FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"
AUTOSAVE_SECONDS = 5


class Meter(QWidget):
    """Livelli di uscita: un gruppo di barre per ogni scheda aperta, una barra per canale."""
    BAR, GAP, SEP = 5, 1, 6

    def __init__(self, audio, parent=None):
        super().__init__(parent)
        self.audio = audio
        self.setFixedHeight(56)
        self.setMinimumWidth(18)

    def _outputs(self):
        return list({id(o): o for o in self.audio.outputs.values()}.values())

    def update(self):
        outs = self._outputs()
        w = 4 + sum(len(o.meter) * (self.BAR + self.GAP) for o in outs) + self.SEP * max(0, len(outs) - 1)
        if self.width() != max(18, w):
            self.setFixedWidth(max(18, min(w, 420)))
            self.setToolTip("\n".join(f"{o.name}: {o.channels} canali" for o in outs))
        super().update()

    def paintEvent(self, e):
        import math
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 20, 24))
        h = self.height() - 2
        x = 2
        for oi, o in enumerate(self._outputs()):
            if oi:
                p.fillRect(int(x + self.SEP / 2 - 2), 2, 1, h - 2, QColor(70, 70, 80))
                x += self.SEP
            for v in o.meter:
                db = 20 * math.log10(max(float(v), 1e-5))
                frac = max(0.0, min(1.0, (db + 60) / 60))
                col = QColor(220, 60, 50) if db > -1 else QColor(230, 200, 50) if db > -12 else QColor(70, 200, 100)
                p.fillRect(int(x), int(1 + h * (1 - frac)), self.BAR, int(h * frac), col)
                x += self.BAR + self.GAP
        p.end()


class MasterSlider(QSlider):
    """Cursore del volume master: doppio clic riporta a 0 dB."""

    def mouseDoubleClickEvent(self, e):
        self.setValue(0)


class MainWindow(HardwareMixin, ClusterMixin, PlaybackMixin, EditingMixin, GroupsMixin, ListsMixin, RemoteMixin, WebMixin, VideoMixin, FilesMixin, QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.show_data = Show()
        self.loader = MediaLoader()
        self.audio = AudioEngine()
        self.comp = Compositor()
        self.ctrl = Controller(self.show_data, self.audio, self.loader, self.comp)
        self.ctrl.visual_started = self._ensure_output_visible
        # istanze secondarie (master / slave / ridondanza): avvolge i comandi del controller prima di
        # qualsiasi collegamento dell'interfaccia
        from .cluster import ClusterHub
        self.cluster = ClusterHub(self.ctrl, self)
        self.cluster.show_fn = lambda: self.show_data
        self.cluster.hw_fn = self._cluster_hw
        self.cluster.joinRequest.connect(self._cluster_join)
        self.cluster.commandReceived.connect(self.cluster.apply_command)
        self.cluster.showReceived.connect(self._cluster_show)
        self.cluster.masterLost.connect(self._cluster_lost)
        self.cluster.masterBack.connect(self._cluster_back)
        self.cluster.clientsChanged.connect(self._cluster_indicator)
        self.cluster.status.connect(lambda t: self.status(t))
        self.cluster.slaveShow.connect(self._cluster_slave_show)
        self.cluster.upload_dir_fn = self._cluster_upload_dir
        self._slave_timer = QTimer(self)
        self._slave_timer.timeout.connect(self._cluster_slave_tick)
        self._slave_timer.start(300)
        self.hw_bad = {}
        from .projectors import ProjectorManager, USER_PLUGIN_DIR
        self.projectors = ProjectorManager()
        self.ctrl.projectors = self.projectors
        self.projectors.finished.connect(self._projector_done)
        from .mixers import MixerManager
        self.mixers = MixerManager()
        self.ctrl.mixers = self.mixers
        self.mixers.finished.connect(self._mixer_done)
        self.mixers.namesChanged.connect(self._mixer_names_changed)
        self.mixers.controlsChanged.connect(self._mixer_controls_changed)
        try:
            os.makedirs(USER_PLUGIN_DIR, exist_ok=True)
        except OSError:
            pass
        self.osc = OscServer()
        self.midi = MidiInput()
        self.learn_dialog = None
        self.output = None
        self._select_guard = False
        self._edit_on = False
        self.qs = QSettings("CueStage", "CueStage")

        self._build_ui()
        self._build_menus()
        self._build_shortcuts()

        self.ctrl.changed.connect(self._ctrl_changed)
        self.ctrl.message.connect(self.status)
        self.ctrl.playheadChanged.connect(self._playhead_changed)
        self.loader.infoReady.connect(lambda p: self.model.refresh())
        self.loader.error.connect(lambda p, e: self.status(f"Errore media «{os.path.basename(p)}»: {e}"))
        self.osc.message.connect(self._osc_message)
        from .webserver import WebServer
        self.web = WebServer(self._web_state, self._web_lists)
        self.web.command.connect(self._web_command)
        from .webserver import ProjRelay
        self._web_proj = {}            # id proiettore -> {"result", "ok", "corners", "status"}
        self._web_proj_jobs = {}       # (id, "lens"/"corner") -> future in corso
        self._web_corner_pending = {}  # id -> {(angolo, asse): valore} da inviare appena libero
        self._web_corner_mode = set()  # proiettori con la modalità angoli già attivata dal web
        self._web_relay = ProjRelay()
        self._web_relay.result.connect(self._web_proj_result)
        self._web_corner_timer = QTimer(self)
        self._web_corner_timer.setInterval(15)
        self._web_corner_timer.timeout.connect(self._web_corner_flush)
        self.projectors.finished.connect(self._web_proj_finished)
        self.web.status.connect(self.status)
        self.osc.status.connect(self.status)
        self.midi.message.connect(self._midi_message)
        self.midi.control.connect(self._midi_control)
        self._ctl_status_t = 0.0
        self.midi.status.connect(self.status)

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._ui_tick)
        self.ui_timer.start(50)

        self._apply_settings(first=True)
        self._update_title()
        # dopo che la finestra è comparsa: recupero da crash oppure ultimo progetto
        QTimer.singleShot(0, lambda: self._startup_open(path))
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self._autosave)
        self.autosave_timer.start(AUTOSAVE_SECONDS * 1000)
        g = self.qs.value("geometry")
        if g is not None:
            self.restoreGeometry(g)
        else:
            self.resize(1400, 900)

    # =========================================================== costruzione UI
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        # barra GO
        bar = QHBoxLayout()
        self.go_btn = QPushButton("GO")
        self.go_btn.setFixedSize(150, 70)
        self.go_btn.setStyleSheet(
            "QPushButton{background:#1f8f45;color:white;font-size:30px;font-weight:bold;border-radius:8px;}"
            "QPushButton:pressed{background:#2bc060;}")
        self.go_btn.setFocusPolicy(Qt.NoFocus)
        self.go_btn.clicked.connect(self.go)
        bar.addWidget(self.go_btn)
        from .ui_lists import GoTarget, ListTabs
        self.go_target = GoTarget()
        self.go_target.changed.connect(self._go_target_changed)
        bar.addWidget(self.go_target, 0, Qt.AlignVCenter)
        bar.addSpacing(6)
        sb = QVBoxLayout()
        self.standby = QLabel("—")
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        self.standby.setFont(f)
        self.standby_notes = QLabel("")
        self.standby_notes.setStyleSheet("color:#aaa;")
        sb.addWidget(QLabel("In attesa (standby):"))
        sb.addWidget(self.standby)
        sb.addWidget(self.standby_notes)
        bar.addLayout(sb, 1)
        # pannello a scomparsa dei proiettori (visibile se ce n'è almeno uno configurato)
        from .ui_projpanel import ProjectorPanel
        self.proj_btn = QPushButton("📽 Proiettori ▾")
        self.proj_btn.setCheckable(True)
        self.proj_btn.setFocusPolicy(Qt.NoFocus)
        self.proj_btn.setFixedHeight(40)
        self.proj_btn.setToolTip("Controllo diretto dei proiettori configurati")
        self.proj_btn.setStyleSheet("QPushButton{padding:0 10px;font-weight:bold;}"
                                    "QPushButton:checked{background:#3a4a6a;color:white;}")
        self.proj_panel = ProjectorPanel(self.projectors, self)
        self.proj_panel.visibilityChanged.connect(self.proj_btn.setChecked)
        self.proj_btn.toggled.connect(self._toggle_proj_panel)
        self._build_shortcuts(self.proj_panel)
        sc = QShortcut(QKeySequence("F9"), self.proj_panel)
        sc.activated.connect(self.toggle_projector_panel)
        bar.addWidget(self.proj_btn, 0, Qt.AlignVCenter)
        self.proj_btn.setVisible(False)
        # pannello a scomparsa dei mute dei mixer
        from .ui_mixers import MixerPanel
        self.mix_btn = QPushButton("≣ Mixer ▾")
        self.mix_btn.setCheckable(True)
        self.mix_btn.setFocusPolicy(Qt.NoFocus)
        self.mix_btn.setFixedHeight(40)
        self.mix_btn.setToolTip("Mute dei mixer configurati (F10)")
        self.mix_btn.setStyleSheet("QPushButton{padding:0 10px;font-weight:bold;}"
                                   "QPushButton:checked{background:#3a4a6a;color:white;}")
        self.mix_panel = MixerPanel(self.mixers, self)
        self.mix_panel.visibilityChanged.connect(self.mix_btn.setChecked)
        self.mix_btn.toggled.connect(self._toggle_mix_panel)
        self._build_shortcuts(self.mix_panel)
        bar.addWidget(self.mix_btn, 0, Qt.AlignVCenter)
        self.mix_btn.setVisible(False)
        # volume globale (master): vale per audio e video
        mv = QVBoxLayout()
        self.master_label = QLabel("Volume master")
        self.master_label.setAlignment(Qt.AlignCenter)
        self.master_label.setStyleSheet("font-weight:bold;")
        self.master_slider = MasterSlider(Qt.Horizontal)
        self.master_slider.setRange(-600, 60)        # decimi di dB
        self.master_slider.setFixedWidth(180)
        self.master_slider.setFocusPolicy(Qt.NoFocus)
        self.master_slider.setToolTip("Volume globale di audio e video · doppio clic = 0 dB")
        self.master_slider.valueChanged.connect(self._master_changed)
        mv.addStretch(1)
        mv.addWidget(self.master_label)
        mv.addWidget(self.master_slider)
        mv.addStretch(1)
        bar.addLayout(mv)
        for text, color, fn, tip in (
            ("❚❚ Pausa", "#8a6d1a", self.pause_all, "F7"),
            ("▶ Riprendi", "#2d5f8a", self.resume_all, "F8"),
            ("■ STOP TUTTO", "#9b2525", self.stop_all, "Esc"),
        ):
            b = QPushButton(text)
            b.setFixedHeight(70)
            b.setMinimumWidth(110)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(tip)
            b.setStyleSheet(f"QPushButton{{background:{color};color:white;font-weight:bold;"
                            f"font-size:14px;border-radius:8px;padding:0 12px;}}")
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.meter = Meter(self.audio)
        bar.addWidget(self.meter)
        self.clock = QLabel("00:00:00")
        cf = QFontDatabase.systemFont(QFontDatabase.FixedFont)     # monospazio su ogni sistema
        cf.setPointSize(18)
        self.clock.setFont(cf)
        bar.addWidget(self.clock)
        root.addLayout(bar)

        # tabella cue
        self.model = CueTableModel(self.show_data, self.ctrl, self.loader)
        self.model.filesDropped.connect(self._files_dropped)
        self.model.rowsMoved.connect(self._rows_moved)
        self.model.edited.connect(self._model_edited)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setDragDropMode(QAbstractItemView.DragDrop)
        self.table.setDefaultDropAction(Qt.MoveAction)
        self.table.setDragDropOverwriteMode(False)
        self.table.setDropIndicatorShown(True)
        self.table.setAcceptDrops(True)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setAlternatingRowColors(True)
        self.table.setItemDelegateForColumn(C_STATE, StateDelegate(self.ctrl, lambda: self.show_data, self.table))
        hh = self.table.horizontalHeader()
        for col, w in enumerate([24, 50, 125, 230, 220, 70, 70, 110, 190]):
            self.table.setColumnWidth(col, w)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        # clic su una riga (anche già selezionata) la mette in standby
        self.table.clicked.connect(self._table_clicked)

        # pannello destro
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("Cue attive"))
        self.active = ActivePanel(self.ctrl)
        self.active.stopRequested.connect(lambda cid: self.ctrl.stop_cue(cid, float(self.show_data.settings.get("stop_fade", 0.3))))
        self.active.pauseRequested.connect(self._toggle_pause_cue)
        self.active.showCueRequested.connect(self.reveal_cue)
        sa = QScrollArea()
        sa.setWidget(self.active)
        sa.setWidgetResizable(True)
        rv.addWidget(sa, 2)
        rv.addWidget(QLabel("Anteprima uscita video"))
        self.preview = OutputPreview(self.comp, self._output_aspect)
        self.preview.selected_fn = self._current_cue
        self.preview.size_fn = self._visual_size
        self.preview.edited.connect(self._preview_edited)
        rv.addWidget(self.preview, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(2)
        self.list_tabs = ListTabs()
        self.list_tabs.currentChanged.connect(self.show_list)
        self.list_tabs.addRequested.connect(self.add_panel)
        self.list_tabs.renameRequested.connect(self.rename_panel)
        self.list_tabs.deleteRequested.connect(self.delete_panel)
        self.list_tabs.moveRequested.connect(self.move_panel)
        lv.addWidget(self.list_tabs)
        # GO locale del PANEL visualizzato (solo con «GO su: MAIN»)
        self.local_go = QWidget()
        self.local_go.setObjectName("localGo")
        self.local_go.setStyleSheet("#localGo{background:#24303f;border-radius:5px;}")
        lg = QHBoxLayout(self.local_go)
        lg.setContentsMargins(6, 4, 6, 4)
        self.local_go_btn = QPushButton("GO")
        self.local_go_btn.setFixedSize(110, 36)
        self.local_go_btn.setFocusPolicy(Qt.NoFocus)
        self.local_go_btn.setStyleSheet(
            "QPushButton{background:#1f8f45;color:white;font-size:16px;font-weight:bold;border-radius:6px;}"
            "QPushButton:pressed{background:#2bc060;}QPushButton:disabled{background:#3a4a3f;color:#888;}")
        self.local_go_btn.setToolTip("GO su questo PANEL (il GO principale resta sulla MAIN)")
        self.local_go_btn.clicked.connect(self.local_go_pressed)
        lg.addWidget(self.local_go_btn)
        self.local_stop_btn = QPushButton("STOP")
        self.local_stop_btn.setFixedSize(110, 36)
        self.local_stop_btn.setFocusPolicy(Qt.NoFocus)
        self.local_stop_btn.setStyleSheet(
            "QPushButton{background:#b3261e;color:white;font-size:16px;font-weight:bold;border-radius:6px;}"
            "QPushButton:pressed{background:#e0453a;}QPushButton:disabled{background:#4a3a3a;color:#888;}")
        self.local_stop_btn.setToolTip("Ferma solo le cue di questo PANEL (le altre liste continuano)")
        self.local_stop_btn.clicked.connect(self.local_stop_pressed)
        lg.addWidget(self.local_stop_btn)
        self.local_standby = QLabel()
        self.local_standby.setStyleSheet("color:#e8e8ee;font-size:14px;font-weight:bold;")
        lg.addWidget(self.local_standby, 1)
        self.local_go.setVisible(False)
        lv.addWidget(self.local_go)
        lv.addWidget(self.table, 1)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        top = QSplitter(Qt.Horizontal)
        top.addWidget(left)
        top.addWidget(right)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 1)
        top.setSizes([1000, 380])

        self.inspector = Inspector(lambda: self.show_data, self.ctrl, self.loader)
        self.inspector.edited.connect(self._inspector_edited)
        self.inspector.size_fn = self._visual_size
        self.inspector.out_size_fn = lambda: self.comp.out_size
        self.inspector.testRequested.connect(self.test_cue)
        self.inspector.stopRequested.connect(lambda cid: self.ctrl.stop_cue(cid, 0.1))
        self.inspector.learnMidi.connect(self.learn_midi)
        self.inspector.learnMidiControl.connect(lambda cb: self.learn_midi(cb, None, "control"))
        self.inspector.oscSend.connect(self._send_osc_now)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(top)
        vsplit.addWidget(self.inspector)
        vsplit.setSizes([520, 380])
        root.addWidget(vsplit, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Pronto")
        self.engine_label = QLabel()
        self.statusBar().addPermanentWidget(self.engine_label)
        self.cluster_btn = QPushButton("")
        self.cluster_btn.setFlat(True)
        self.cluster_btn.setFocusPolicy(Qt.NoFocus)
        self.cluster_btn.clicked.connect(self._cluster_clicked)
        self.cluster_btn.setVisible(False)
        self.statusBar().addPermanentWidget(self.cluster_btn)

    def _build_menus(self):
        mb = self.menuBar()

        def add(menu, text, fn, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        m = mb.addMenu("&File")
        add(m, "Nuovo show", self.new_show, "Ctrl+N")
        add(m, "Apri…", self.open_dialog, "Ctrl+O")
        add(m, "Salva", self.save, "Ctrl+S")
        add(m, "Salva con nome…", self.save_as, "Ctrl+Shift+S")
        m.addSeparator()
        add(m, "Esci", self.close, "Ctrl+Q")

        m = mb.addMenu("&Cue")
        for t in CUE_TYPES:
            add(m, f"Nuova cue {TYPE_LABELS[t]}", lambda _=False, t=t: self.add_cue(t))
        m.addSeparator()
        add(m, "Duplica", self.duplicate, "Ctrl+D")
        add(m, "Elimina", self.delete_cues, "Delete")
        add(m, "Sposta su", lambda: self.move_selected(-1), "Ctrl+Up")
        add(m, "Sposta giù", lambda: self.move_selected(1), "Ctrl+Down")
        add(m, "Rinumera tutte (lista visualizzata)", self.renumber)
        m.addSeparator()
        add(m, "Raggruppa le selezionate", self.group_selected, "Ctrl+Shift+G")
        add(m, "Espandi / compatta gruppo", lambda: self.toggle_group(), "Ctrl+E")
        add(m, "Togli dal gruppo", self.remove_from_group)
        add(m, "Sciogli il gruppo", self.ungroup_selected)
        self.move_menu = m.addMenu("Sposta le selezionate in")
        self.move_menu.aboutToShow.connect(lambda: self._fill_move_menu(self.move_menu))
        m.addSeparator()
        add(m, "Nuovo PANEL", self.add_panel, "Ctrl+Shift+N")
        add(m, "Lista successiva", lambda: self.show_list((self.show_data.view + 1) % len(self.show_data.lists)),
            "Ctrl+PgDown")
        add(m, "Lista precedente", lambda: self.show_list((self.show_data.view - 1) % len(self.show_data.lists)),
            "Ctrl+PgUp")
        m.addSeparator()
        add(m, "Prova cue selezionata", lambda: self.test_cue(self._current_cue().id) if self._current_cue() else None, "Ctrl+T")

        m = mb.addMenu("&Riproduzione")
        add(m, "GO", self.go)
        add(m, "Stop tutto", self.stop_all)
        add(m, "Pausa tutto", self.pause_all)
        add(m, "Riprendi tutto", self.resume_all)
        add(m, "Playhead alla prima cue", lambda: self.ctrl.set_playhead(0), "Home")
        m.addSeparator()
        add(m, "Crossfade alla successiva", self.crossfade_next, "F6")
        add(m, "Testo: frase successiva", lambda: self.text_step(1), "Alt+Right")
        add(m, "Testo: frase precedente", lambda: self.text_step(-1), "Alt+Left")

        m = mb.addMenu("&Uscita video")
        self.act_out_show = add(m, "Mostra finestra di uscita", self.toggle_output, "Ctrl+Shift+O")
        self.act_full = add(m, "Schermo intero", self.toggle_fullscreen)
        self.act_full.setCheckable(True)
        self.screens_menu = m.addMenu("Schermo di uscita")
        self.screens_menu.aboutToShow.connect(self._fill_screens)
        m.addSeparator()
        add(m, "Geometria a 4 angoli…", self.geometry_dialog, "Ctrl+G")
        self.act_grid = add(m, "Griglia di test", self.toggle_grid)
        self.act_grid.setCheckable(True)
        self.act_gl = add(m, "Usa accelerazione OpenGL (al riavvio dell'uscita)", self.toggle_gl)
        self.act_gl.setCheckable(True)

        m = mb.addMenu("&Impostazioni")
        add(m, "Audio, OSC e MIDI…", self.settings_dialog, "Ctrl+,")
        add(m, "Proiettori…", self.projectors_dialog)
        add(m, "Pannello proiettori", self.toggle_projector_panel, "F9")
        m.addSeparator()
        add(m, "Mixer e schede audio…", self.mixers_dialog)
        add(m, "Pannello mixer", self.toggle_mixer_panel, "F10")
        m.addSeparator()
        add(m, "Diventa slave…", self.become_slave)
        self.act_leave = add(m, "Esci dalla modalità slave (diventa indipendente)", self.cluster_promote)
        self.act_leave.setEnabled(False)
        add(m, "Istanze collegate…", self._cluster_clicked)

        m = mb.addMenu("&Aiuto")
        add(m, "Comandi OSC / MIDI / tastiera", lambda: HelpDialog(self).exec())
        add(m, "Informazioni", lambda: QMessageBox.about(
            self, "CueStage", "<b>CueStage</b><br>Sistema di cue per spettacolo: audio, video, immagini, "
            "fade, comandi, OSC e MIDI.<br>Python + Qt6 (PySide6) · motore audio PortAudio."))

    def _build_shortcuts(self, widget=None):
        """Scorciatoie di regia, installate sia sulla finestra principale che sull'uscita."""
        widget = widget or self
        for key, fn in (("Space", self.go), ("Escape", self.stop_all), ("F7", self.pause_all),
                        ("F8", self.resume_all)):
            sc = QShortcut(QKeySequence(key), widget)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(fn)

    # =========================================================== impostazioni
    def _apply_settings(self, first=False, old=None):
        s = self.show_data.settings
        if isinstance(s.get("audio_device"), int):   # versioni precedenti: indice numerico
            s["audio_device"] = None
        self.audio.buffer = s.get("audio_buffer") or "normale"
        if first or old is None or (old.get("audio_device"), old.get("device_channels"), old.get("audio_buffer")) != \
                (s.get("audio_device"), s.get("device_channels"), s.get("audio_buffer")):
            self.audio.start(s.get("audio_device"), s.get("device_channels") or {})
            self._open_cue_outputs()
        self.audio.master = db_to_lin(float(s.get("master_db", 0)))
        self._set_projectors(s.get("projectors") or [])
        self._set_mixers(s.get("mixers") or [])
        self._cluster_apply_settings()
        self.master_slider.blockSignals(True)
        self.master_slider.setValue(int(round(float(s.get("master_db", 0)) * 10)))
        self.master_slider.blockSignals(False)
        self._update_master_label()
        self.go_target.set_value(s.get("go_target", "main"))
        if first or old is None or (old.get("osc_in_enabled"), old.get("osc_in_port")) != (
                s.get("osc_in_enabled"), s.get("osc_in_port")):
            if s.get("osc_in_enabled", True):
                self.osc.start(int(s.get("osc_in_port", 53000)))
            else:
                self.osc.stop()
        if first or old is None or (old.get("web_enabled"), old.get("web_port"), old.get("web_pin")) != (
                s.get("web_enabled"), s.get("web_port"), s.get("web_pin")):
            if s.get("web_enabled"):
                self.web.start(int(s.get("web_port", 8080)), s.get("web_pin", ""))
            else:
                self.web.stop()
        self.midi.set_filters(s.get("midi_filters") or {})
        if first or old is None or (old.get("midi_in_ports"), old.get("midi_in_port")) != (
                s.get("midi_in_ports"), s.get("midi_in_port")):
            ports = self._midi_ports()
            if ports:
                self.midi.open(ports)
            else:
                self.midi.close()
        self.comp.corners[:] = [list(c) for c in s.get("output_corners")]
        self.comp.grid = bool(s.get("output_grid", False))
        self.act_grid.setChecked(self.comp.grid)
        self.act_gl.setChecked(bool(s.get("output_opengl", True)))
        self.act_full.setChecked(bool(s.get("output_fullscreen", True)))
        self.comp.changed()
        self._update_engine_label()

    def _rescan_audio(self):
        self.audio.rescan()
        self._open_cue_outputs()
        self.status(self.audio.status)

    def settings_dialog(self):
        d = SettingsDialog(self.show_data.settings, self.learn_midi, self, rescan_fn=self._rescan_audio)
        if d.exec():
            old = dict(self.show_data.settings)
            self.show_data.settings = d.result_settings()
            self.show_data.dirty = True
            self._apply_settings(old=old)

    def _master_changed(self, v):
        db = v / 10.0
        self.show_data.settings["master_db"] = db
        self.audio.master = db_to_lin(db)
        self.show_data.dirty = True
        self._update_master_label()

    def _update_master_label(self):
        db = self.master_slider.value() / 10.0
        txt = "-∞ dB" if db <= MIN_DB else f"{db:+.1f} dB"
        self.master_label.setText(f"Volume master  {txt}")

    def _underrun_note(self):
        """Buchi audio negli ultimi 10 s (il contatore delle schede cresce a ogni underrun)."""
        now = time.monotonic()
        hist = getattr(self, "_xrun_hist", None)
        if hist is None:
            hist = self._xrun_hist = []
        n = self.audio.underruns()
        hist.append((now, n))
        while hist and now - hist[0][0] > 10:
            hist.pop(0)
        recent = n - hist[0][1] if hist else 0
        return recent

    def _update_engine_label(self):
        recent = self._underrun_note()
        parts = [self.audio.status]
        if recent:
            parts.insert(0, f"⚠ {recent} buchi audio (underrun) negli ultimi 10 s: aumenta il buffer in "
                            "Impostazioni › Audio")
        parts.append(f"OSC :{self.osc.port}" if self.osc.port else "OSC off")
        n = len(self.midi.ports)
        parts.append("MIDI off" if not n else ("MIDI on" if n == 1 else f"MIDI {n} schede"))
        if getattr(self, "web", None) is not None and self.web.port:
            parts.append(f"Web :{self.web.port}")
        self.engine_label.setText("  ·  ".join(parts))
        self.engine_label.setStyleSheet("color:#e0b040;font-weight:bold;" if recent else "")

    # =========================================================== varie
    def status(self, msg):
        self.statusBar().showMessage(msg, 8000)

    def _update_title(self):
        name = os.path.basename(self.show_data.path) if self.show_data.path else "Senza titolo"
        self.setWindowTitle(f"{'● ' if self.show_data.dirty else ''}{name} — CueStage")

    def _update_edit_overlay(self):
        """Con la scheda crop aperta (e non in blind) l'uscita mostra il fermo immagine in modifica."""
        want = None
        cp = self.inspector.crop_panel
        try:
            if cp is not None and cp.isVisible() and not cp.is_blind() \
                    and cp.cue.id not in self.ctrl.instances:
                img = cp.still()
                if img is not None and not img.isNull():
                    want = (cp.cue, cp.state(), img)   # stato al tempo scelto (punti preset)
        except RuntimeError:   # pannello appena distrutto
            want = None
        lay = self.comp.layers.get(EDIT_KEY)
        if want:
            cue, st, img = want
            if lay is None:
                lay = self.comp.add(EDIT_KEY, int(cue.layer), cue.fit, cue=st)
                if not self._edit_on:
                    self._ensure_output_visible()
            lay.cue = st
            lay.image = img
            lay.z = int(cue.layer)
            lay.fit = st.fit
            lay.opacity = float(st.opacity)
            self._edit_on = True
            self.comp.changed()
        else:
            if lay is not None:
                self.comp.remove(EDIT_KEY)
            self._edit_on = False

    def _ui_tick(self):
        self._update_edit_overlay()
        px = self._output_pixels()
        if px != self.comp.out_size:
            self.comp.out_size = px
            self.inspector._update_px_labels()
        self.clock.setText(time.strftime("%H:%M:%S"))
        self.meter.update()
        if self.ctrl.instances or self.ctrl.pending:
            self.model.refresh_state()
        self._update_title()
        self._update_engine_label()
        self.go_btn.setText("GO" if not self.ctrl.paused_all else "GO ❚❚")

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self.qs.setValue("geometry", self.saveGeometry())
        self._remove_autosave()        # chiusura regolare: nessun recupero necessario
        self.ctrl.stop_all(0)
        self.audio.stop()
        self.osc.stop()
        self.midi.close()
        self.projectors.shutdown()
        self.mixers.shutdown()
        self.web.stop()
        if self.output is not None:
            self.output.close()
        e.accept()
