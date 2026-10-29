"""Dialoghi: impostazioni, geometria a 4 angoli, apprendimento MIDI, guida OSC."""
from .i18n import tr
import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
    QTextBrowser, QVBoxLayout, QWidget, )

from .remote import MidiInput, normalize_control, normalize_spec
from .system import IS_MAC, IS_WIN, OS_NAME, data_dir_label

if IS_WIN:
    AUTO_CH_NOTE = (tr("«auto»: tutti i canali della scheda. Su Windows CueStage usa WASAPI (o ASIO, se il driver "
                    "della scheda lo offre): per la latenza più bassa preferisci ASIO."))
elif IS_MAC:
    AUTO_CH_NOTE = tr("«auto»: tutti i canali della scheda (Core Audio).")
else:
    AUTO_CH_NOTE = (tr("«auto»: 2 canali per i dispositivi virtuali (default, pipewire, pulse…), tutti per le "
                    "schede hardware (hw:). Per più canali attraverso PipeWire, impostali sul dispositivo "
                    "«pipewire» o «default»."))
from .video_output import MappingEditor

MIDI_ACTIONS = [
    ("go", "GO"), ("stop_all", tr("Stop tutto")), ("pause_all", tr("Pausa tutto")),
    ("resume_all", tr("Riprendi tutto")), ("next", tr("Playhead avanti")), ("prev", tr("Playhead indietro")),
    ("xfade_go", tr("Crossfade alla successiva")),
    ("dim", tr("DIM acceso / spento")), ("duck", tr("Duck acceso / spento")),
]
MIDI_CONTROLS = [
    ("vol_selected", tr("Volume della cue selezionata")),
    ("op_selected", tr("Trasparenza della cue selezionata")),
    ("xfade", tr("Crossfader (corrente ↔ successiva)")),
]


class SettingsDialog(QDialog):
    def __init__(self, settings, learn_fn, parent=None, rescan_fn=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Impostazioni"))
        self.s = copy.deepcopy(settings)
        self.learn_fn = learn_fn
        self.rescan_fn = rescan_fn
        v = QVBoxLayout(self)
        from PySide6.QtWidgets import QTabWidget
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        pages = []
        for title in (tr("Audio"), "OSC", "MIDI", tr("Interfaccia web"), tr("Istanze")):
            w = QWidget()
            pages.append(QVBoxLayout(w))
            sa = QScrollArea()                 # ogni scheda scorre: i controlli non vengono mai schiacciati
            sa.setWidgetResizable(True)
            sa.setFrameShape(QFrame.NoFrame)
            sa.setWidget(w)
            self.tabs.addTab(sa, title)

        ga = QGroupBox(tr("Audio"))
        fa = QFormLayout(ga)
        self.dev = QComboBox()
        fa.addRow(tr("Scheda predefinita"), self.dev)
        chbox = QGroupBox(tr("Canali usati per ogni scheda"))
        cbl = QVBoxLayout(chbox)
        inner = QWidget()
        self.ch_grid = QGridLayout(inner)
        self.ch_grid.setContentsMargins(0, 0, 0, 0)
        sa = QScrollArea()
        sa.setWidget(inner)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setMaximumHeight(200)
        cbl.addWidget(sa)
        note = QLabel(AUTO_CH_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color:#888; font-size:10px;")
        cbl.addWidget(note)
        fa.addRow(chbox)
        rescan = QPushButton(tr("Rileggi le schede collegate"))
        rescan.setToolTip(tr("Per una scheda USB collegata dopo l'avvio di CueStage"))
        rescan.clicked.connect(self._rescan)
        fa.addRow(rescan)
        self._fill_devices()
        from .audio_engine import BUFFER_LABELS
        self.abuf = QComboBox()
        for k, t in BUFFER_LABELS.items():
            self.abuf.addItem(t, k)
        self.abuf.setCurrentIndex(max(0, self.abuf.findData(self.s.get("audio_buffer") or "normale")))
        self.abuf.setToolTip(tr("Se compaiono buchi nell'audio («underrun» nella barra di stato o nel terminale), "
                             "scegli un buffer più grande: le cue partono qualche millisecondo più tardi."))
        fa.addRow(tr("Buffer audio"), self.abuf)
        self.master = QDoubleSpinBox()
        self.master.setRange(-60, 12)
        self.master.setSuffix(" dB")
        self.master.setValue(float(self.s.get("master_db", 0)))
        fa.addRow(tr("Volume master"), self.master)
        self.stopfade = QDoubleSpinBox()
        self.stopfade.setRange(0, 10)
        self.stopfade.setSuffix(" s")
        self.stopfade.setValue(float(self.s.get("stop_fade", 0.3)))
        fa.addRow(tr("Fade di «Stop tutto»"), self.stopfade)
        self.xftime = QDoubleSpinBox()
        self.xftime.setRange(0, 120)
        self.xftime.setDecimals(1)
        self.xftime.setSingleStep(0.5)
        self.xftime.setSuffix(" s")
        self.xftime.setValue(float(self.s.get("xfade_time", 3.0)))
        self.xftime.setToolTip(tr("Durata del crossfade automatico (Riproduzione › Crossfade alla successiva, F6)"))
        fa.addRow(tr("Durata crossfade"), self.xftime)
        pages[0].addWidget(ga)

        from .audio_engine import preview_settings
        pvs = preview_settings(self.s)
        gp = QGroupBox(tr("Preascolto (cuffia)"))
        fp = QFormLayout(gp)
        self.pv_on = QCheckBox(tr("Attiva il preascolto"))
        self.pv_on.setChecked(pvs["enabled"])
        self.pv_on.setToolTip(tr("Le cue con «Preascolto» (ispettore) suonano solo qui, non sulle uscite dello "
                              "spettacolo. Accanto al volume master compare il volume del preascolto."))
        fp.addRow(self.pv_on)
        self.pv_dev = QComboBox()
        fp.addRow(tr("Scheda"), self.pv_dev)
        pr = QHBoxLayout()
        self.pv_l = QSpinBox()
        self.pv_r = QSpinBox()
        for sp, val, lab in ((self.pv_l, pvs["ch_l"], tr("Sinistro")), (self.pv_r, pvs["ch_r"], tr("Destro"))):
            sp.setRange(1, 64)
            sp.setValue(val)
            pr.addWidget(QLabel(lab))
            pr.addWidget(sp)
        pr.addStretch(1)
        fp.addRow(tr("Canali"), pr)
        self._pv_cur = pvs
        self._fill_preview_devices()
        self.pv_dev.currentIndexChanged.connect(lambda *_: self._fit_preview_channels())
        pages[0].addWidget(gp)

        go = QGroupBox(tr("OSC in ingresso"))
        fo = QFormLayout(go)
        self.osc_on = QCheckBox(tr("Abilita ricezione OSC"))
        self.osc_on.setChecked(bool(self.s.get("osc_in_enabled", True)))
        fo.addRow(self.osc_on)
        self.osc_port = QSpinBox()
        self.osc_port.setRange(1, 65535)
        self.osc_port.setValue(int(self.s.get("osc_in_port", 53000)))
        fo.addRow(tr("Porta UDP"), self.osc_port)
        pages[1].addWidget(go)

        gd = QGroupBox(tr("Dispositivi OSC (profili: console luci, software…)"))
        gdl = QVBoxLayout(gd)
        from PySide6.QtWidgets import QListWidget
        self.osc_devs = [dict(d) for d in self.s.get("osc_devices") or []]
        self.dev_list = QListWidget()
        self.dev_list.setMinimumHeight(110)
        self.dev_list.itemDoubleClicked.connect(lambda *_: self._edit_osc_dev())
        gdl.addWidget(self.dev_list)
        br = QHBoxLayout()
        for text, fn in ((tr("Aggiungi da profilo…"), self._add_osc_dev), (tr("Modifica…"), self._edit_osc_dev),
                         (tr("Rimuovi"), self._del_osc_dev)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            br.addWidget(b)
        br.addStretch(1)
        gdl.addLayout(br)
        nd = QLabel(tr("Un dispositivo nasce da un profilo (es. ChamSys MagicQ) con il suo indirizzo IP, scritto o trovato con la ricerca in rete. Nelle cue OSC si sceglie poi il dispositivo e il comando (GO di un playback, GOTO CUE, exec, DBO…). Profili aggiuntivi: file .json in {0}.", data_dir_label('osc_profiles')))
        nd.setWordWrap(True)
        nd.setStyleSheet("color:#888;")
        gdl.addWidget(nd)
        pages[1].addWidget(gd)
        self._fill_osc_devs()

        gw = QGroupBox(tr("Interfaccia web (telefono / tablet)"))
        fw = QFormLayout(gw)
        self.web_on = QCheckBox(tr("Abilita interfaccia web"))
        self.web_on.setChecked(bool(self.s.get("web_enabled", False)))
        fw.addRow(self.web_on)
        self.web_port = QSpinBox()
        self.web_port.setRange(1, 65535)
        self.web_port.setValue(int(self.s.get("web_port", 8080)))
        fw.addRow(tr("Porta TCP"), self.web_port)
        self.web_pin = QLineEdit(str(self.s.get("web_pin", "")))
        self.web_pin.setPlaceholderText(tr("vuoto = nessun PIN"))
        self.web_pin.setToolTip(tr("Se impostato, il browser lo chiede una volta (consigliato su reti condivise)"))
        fw.addRow("PIN", self.web_pin)
        self.web_proj = QCheckBox(tr("Mostra il controllo dei proiettori nell'interfaccia web"))
        self.web_proj.setChecked(bool(self.s.get("web_projectors", True)))
        fw.addRow(self.web_proj)
        from .webserver import local_interfaces
        self.web_ip = QComboBox()
        for ip, name in local_interfaces():
            self.web_ip.addItem(f"{ip}  ({name})" if name else ip, ip)
        self.web_ip.addItem(tr("127.0.0.1  (solo questo computer)"), "127.0.0.1")
        self.web_ip.setToolTip(tr("Indirizzo della rete a cui è collegato il telefono: il server risponde su "
                                  "tutti gli indirizzi elencati, la scelta serve solo per il QR code"))
        fw.addRow(tr("Indirizzo"), self.web_ip)
        self.web_qr_pin = QCheckBox(tr("Includi il PIN nel QR code (accesso diretto senza digitarlo)"))
        self.web_qr_pin.setChecked(True)
        fw.addRow(self.web_qr_pin)
        qrow = QHBoxLayout()
        self.web_qr = QLabel()
        self.web_qr.setFixedSize(180, 180)
        self.web_qr.setAlignment(Qt.AlignCenter)
        self.web_qr.setToolTip(tr("Inquadra con la fotocamera del telefono per aprire l'interfaccia web"))
        qrow.addWidget(self.web_qr)
        self.web_url = QLabel()
        self.web_url.setWordWrap(True)
        self.web_url.setTextInteractionFlags(Qt.TextSelectableByMouse)
        qrow.addWidget(self.web_url, 1)
        fw.addRow(qrow)
        self.web_port.valueChanged.connect(lambda _v: self._update_qr())
        self.web_pin.textChanged.connect(lambda _t: self._update_qr())
        self.web_ip.currentIndexChanged.connect(lambda _i: self._update_qr())
        self.web_qr_pin.toggled.connect(lambda _b: self._update_qr())
        self.web_on.toggled.connect(lambda _b: self._update_qr())
        self._update_qr()
        pages[3].addWidget(gw)

        gm = QGroupBox(tr("Comandi MIDI"))
        gl = QGridLayout(gm)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        from .remote import port_key, port_label, port_short
        self.midi_ports = QListWidget()
        self.midi_ports.setMinimumHeight(7 * self.fontMetrics().height() + 12)
        self.midi_ports.setMinimumWidth(220)
        self.midi_ports.setToolTip(tr("Spunta tutte le schede MIDI da usare: vengono aperte insieme"))
        cur = list(self.s.get("midi_in_ports") or ([self.s["midi_in_port"]] if self.s.get("midi_in_port") else []))
        avail = MidiInput.list_ports()
        seen = set()
        for n in avail:
            it = QListWidgetItem(port_short(n))
            it.setData(Qt.UserRole, port_label(n))
            it.setToolTip(port_label(n))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            on = any(port_key(c) == port_key(n) for c in cur)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.midi_ports.addItem(it)
            seen.add(port_key(n))
        for c in cur:
            if port_key(c) not in seen:
                it = QListWidgetItem(tr("{0}  (non collegata)", port_short(c)))
                it.setData(Qt.UserRole, port_label(c))
                it.setToolTip(port_label(c))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked)
                self.midi_ports.addItem(it)
        if self.midi_ports.count() == 0:
            it = QListWidgetItem(tr("(nessuna scheda MIDI collegata)"))
            it.setFlags(Qt.NoItemFlags)
            self.midi_ports.addItem(it)
        gs = QGroupBox(tr("Schede MIDI in ingresso e filtri"))
        gsl = QGridLayout(gs)
        hint = QLabel(tr("Spunta le schede da usare · seleziona una scheda per vederne i filtri"))
        hint.setStyleSheet("color:#888;")
        gsl.addWidget(hint, 0, 0, 1, 2)
        gsl.addWidget(self.midi_ports, 1, 0)
        gsl.addWidget(self._midi_filter_box(), 1, 1)
        gsl.setColumnStretch(0, 2)
        gsl.setColumnStretch(1, 3)
        pages[2].addWidget(gs)
        self.midi_ports.currentItemChanged.connect(lambda *_: self._show_filter())
        if self.midi_ports.count():
            self.midi_ports.setCurrentRow(0)
        pn = QLabel(tr("Ogni comando può indicare la scheda da cui deve arrivare, dopo «@» "
                    "(es. «cc 1 7 @ nanoKONTROL2»); «Impara» la aggiunge da sé. Senza «@» vale da tutte."))
        pn.setWordWrap(True)
        pn.setStyleSheet("color:#888;")
        gl.addWidget(pn, 99, 0, 1, 3)
        self.map_edits = {}
        mm = self.s.get("midi_map", {})
        for r, (key, label) in enumerate(MIDI_ACTIONS, start=1):
            gl.addWidget(QLabel(label), r, 0)
            le = QLineEdit(mm.get(key, ""))
            le.setPlaceholderText(tr("es. note 1 60 · cc 1 64 · pc 1 0 (canale 0 = tutti)"))
            gl.addWidget(le, r, 1)
            b = QPushButton(tr("Impara"))
            b.clicked.connect(lambda _=False, le=le: self._learn(le, "trigger"))
            gl.addWidget(b, r, 2)
            self.map_edits[key] = le
        note = QLabel(tr("Ogni cue può avere anche un proprio trigger MIDI (scheda «Base»)."))
        note.setStyleSheet("color:#888;")
        gl.addWidget(note, len(MIDI_ACTIONS) + 1, 0, 1, 3)
        pages[2].addWidget(gm)

        go_ = QGroupBox(tr("MIDI in uscita"))
        gol = QFormLayout(go_)
        self.midi_out = QComboBox()
        self.midi_out.setEditable(True)
        self.midi_out.addItem("", "")
        from .remote import midi_out as _mo
        for n in _mo.list_ports():
            self.midi_out.addItem(n, n)
        cur_out = self.s.get("midi_out_port", "")
        k = self.midi_out.findData(cur_out)
        if k < 0 and cur_out:
            self.midi_out.addItem(cur_out, cur_out)
            k = self.midi_out.count() - 1
        self.midi_out.setCurrentIndex(max(0, k))
        gol.addRow(tr("Uscita predefinita delle cue «MIDI out»"), self.midi_out)
        pages[2].addWidget(go_)

        gc = QGroupBox(tr("MIDI in ingresso · controlli continui (fader, potenziometri)"))
        gcl = QGridLayout(gc)
        self.ctl_edits = {}
        mc = self.s.get("midi_controls", {})
        for r, (key, label) in enumerate(MIDI_CONTROLS):
            gcl.addWidget(QLabel(label), r, 0)
            le = QLineEdit(mc.get(key, ""))
            le.setPlaceholderText(tr("es. cc 1 7 · pb 1 (pitch bend) · pc 1 * (canale 0 = tutti)"))
            gcl.addWidget(le, r, 1)
            b = QPushButton(tr("Impara"))
            b.clicked.connect(lambda _=False, le=le: self._learn(le, "control"))
            gcl.addWidget(b, r, 2)
            self.ctl_edits[key] = le
        cn = QLabel(tr("Volume e trasparenza agiscono sulla cue selezionata nella lista: dal vivo se è in "
                    "riproduzione, altrimenti sul valore programmato. Il crossfader porta dalla cue corrente "
                    "a quella in attesa; arrivato in fondo il lato di partenza si inverte."))
        cn.setWordWrap(True)
        cn.setStyleSheet("color:#888;")
        gcl.addWidget(cn, len(MIDI_CONTROLS), 0, 1, 3)
        pages[2].addWidget(gc)

        gi = QGroupBox(tr("Istanze secondarie (master / slave / ridondanza)"))
        fi = QFormLayout(gi)
        self.cl_on = QCheckBox(tr("Abilita istanze secondarie (questa istanza fa da master)"))
        self.cl_on.setChecked(bool(self.s.get("cluster_enabled")))
        fi.addRow(self.cl_on)
        self.cl_port = QSpinBox()
        self.cl_port.setRange(1, 65534)
        self.cl_port.setValue(int(self.s.get("cluster_port") or 53900))
        fi.addRow(tr("Porta TCP"), self.cl_port)
        import socket as _s
        self.cl_name = QLineEdit(self.s.get("cluster_name") or "")
        self.cl_name.setPlaceholderText(_s.gethostname())
        fi.addRow(tr("Nome di questa istanza"), self.cl_name)
        self.cl_edit = QCheckBox(tr("Le istanze secondarie possono aggiornare il progetto"))
        self.cl_edit.setToolTip(tr("Le modifiche fatte su uno slave (cue, liste, file nuovi) arrivano al master e da lì "
                                "a tutte le altre secondarie. Senza: l'editor delle secondarie è bloccato."))
        self.cl_edit.setChecked(bool(self.s.get("cluster_slave_edit")))
        fi.addRow(self.cl_edit)
        ni = QLabel(tr("Con l'opzione attiva la porta TCP accetta istanze remote (la porta successiva, UDP, serve alla "
                    "ricerca). Ogni richiesta va confermata qui. Sull'altro computer: Impostazioni › Diventa "
                    "slave… Il master invia un heartbeat ogni 100 ms, lo show, i file e ogni modifica; in "
                    "ridondanza anche ogni comando (GO, stop, playhead, crossfade, livelli…)."))
        ni.setWordWrap(True)
        ni.setStyleSheet("color:#888;")
        fi.addRow(ni)
        pages[4].addWidget(gi)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        for pg in pages:
            pg.addStretch(1)
        v.addWidget(bb)
        # dimensione iniziale ampia (le schede scorrono), entro lo schermo disponibile
        W, H = 1100, 900
        scr = (parent.screen() if parent is not None else None) or self.screen()
        if scr is not None:
            g = scr.availableGeometry()
            W, H = min(W, g.width() - 40), min(H, g.height() - 60)
        self.resize(W, H)

    def _fill_osc_devs(self, row=None):
        from .ui_oscdev import device_label
        self.dev_list.clear()
        for d in self.osc_devs:
            self.dev_list.addItem(device_label(d))
        if row is not None and 0 <= row < self.dev_list.count():
            self.dev_list.setCurrentRow(row)

    def _add_osc_dev(self):
        from .ui_oscdev import OscDeviceDialog
        d = OscDeviceDialog(None, self)
        if d.exec():
            self.osc_devs.append(d.result_device())
            self._fill_osc_devs(len(self.osc_devs) - 1)

    def _edit_osc_dev(self):
        from .ui_oscdev import OscDeviceDialog
        i = self.dev_list.currentRow()
        if not 0 <= i < len(self.osc_devs):
            return
        d = OscDeviceDialog(self.osc_devs[i], self)
        if d.exec():
            self.osc_devs[i] = d.result_device()
            self._fill_osc_devs(i)

    def _del_osc_dev(self):
        i = self.dev_list.currentRow()
        if 0 <= i < len(self.osc_devs):
            del self.osc_devs[i]
            self._fill_osc_devs(min(i, len(self.osc_devs) - 1))

    def _fill_preview_devices(self):
        from .audio_engine import list_devices
        cur = self.pv_dev.currentData() if self.pv_dev.count() else self._pv_cur["device"]
        self.pv_dev.blockSignals(True)
        self.pv_dev.clear()
        self.pv_dev.addItem(tr("Scheda predefinita del progetto"), "")
        for dv in list_devices():
            self.pv_dev.addItem(tr("{0}  [{1}] — {2} canali", dv['name'], dv['hostapi'], dv['max_ch']), dv["id"])
        i = self.pv_dev.findData(cur or "")
        if i < 0:
            self.pv_dev.addItem(tr("{0} (non collegata)", cur.split('|', 1)[-1]), cur)
            i = self.pv_dev.count() - 1
        self.pv_dev.setCurrentIndex(i)
        self.pv_dev.blockSignals(False)
        self._fit_preview_channels()

    def _fit_preview_channels(self):
        from .audio_engine import list_devices
        dev_id = self.pv_dev.currentData() or ""
        n = next((d["max_ch"] for d in list_devices() if d["id"] == dev_id), 64 if dev_id else 2)
        for sp in (self.pv_l, self.pv_r):
            sp.setMaximum(max(1, min(64, n)))

    def _fill_devices(self):
        from .audio_engine import SYSTEM, busy_devices, device_label, list_devices
        cur = self.dev.currentData() if self.dev.count() else self.s.get("audio_device")
        if isinstance(cur, int):      # show di versioni precedenti: indice numerico
            cur = None
        self.dev.clear()
        self.dev.addItem(tr("Predefinita di sistema (quella scelta nelle impostazioni audio di {0})", OS_NAME), None)
        devs = list_devices()
        for dv in devs:
            self.dev.addItem(device_label(dv), dv["id"])
        busy = busy_devices()
        i = self.dev.findData(cur)
        cur_name = cur.split('|', 1)[-1] if cur else ""
        if i < 0 and cur:
            why = next((tr("occupata da {0}", b['owner']) for b in busy if b["name"] == cur_name), tr("non collegata"))
            self.dev.addItem(f"{cur_name} — {why}", cur)
            i = self.dev.count() - 1
        # schede che un altro programma (di solito PipeWire/PulseAudio) tiene aperte: visibili ma non sceglibili
        for b in busy:
            if b["name"] == cur_name:
                continue
            self.dev.addItem(tr("{0} — occupata da {1}", b['name'], b['owner']), None)
            it = self.dev.model().item(self.dev.count() - 1)
            if it is not None:
                it.setEnabled(False)
                it.setToolTip(tr("Un altro programma usa l'uscita di questa scheda. Chiudilo, oppure in PipeWire/"
                              "PulseAudio (pavucontrol › Configurazione) metti il profilo della scheda su «Spento»"))
        self.dev.setCurrentIndex(max(0, i))
        # canali per scheda (0 = automatico)
        while self.ch_grid.count():
            it = self.ch_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        ov = self.s.setdefault("device_channels", {})
        self.ch_spins = {}
        rows = [(SYSTEM, tr("Predefinita di sistema"), 2, 2)] + \
               [(dv["id"], device_label(dv), 2 if dv["virtual"] else min(dv["max_ch"], 64), dv["max_ch"])
                for dv in devs]
        for r, (key, label, auto, mx) in enumerate(rows):
            lab = QLabel(label)
            lab.setToolTip(tr("La scheda dichiara fino a {0} canali", mx))
            sp = QSpinBox()
            sp.setRange(0, 64)
            sp.setSpecialValueText(tr("auto ({0})", auto))
            sp.setValue(int(ov.get(key) or 0))
            self.ch_grid.addWidget(lab, r, 0)
            self.ch_grid.addWidget(sp, r, 1)
            self.ch_spins[key] = sp

    # ---- filtri MIDI per scheda
    FILTER_TYPES = (("note", tr("Note")), ("cc", tr("Control change (CC)")), ("pc", tr("Program change")),
                    ("pb", tr("Pitch bend")))

    def _midi_filter_box(self):
        self.filters = copy.deepcopy(self.s.get("midi_filters") or {})
        self._filter_port = None
        box = QGroupBox(tr("Filtri della scheda selezionata"))
        self.filter_box = box
        g = QGridLayout(box)
        g.addWidget(QLabel(tr("Accetta")), 0, 0)
        self.f_types = {}
        row = QGridLayout()
        for i, (k, lab) in enumerate(self.FILTER_TYPES):
            cb = QCheckBox(lab)
            cb.toggled.connect(self._store_filter)
            self.f_types[k] = cb
            row.addWidget(cb, i // 2, i % 2)
        g.addLayout(row, 0, 1)
        g.addWidget(QLabel(tr("Canali")), 1, 0)
        self.f_channels = QLineEdit()
        self.f_channels.setPlaceholderText(tr("tutti  ·  oppure es. 1, 10, 13-16"))
        self.f_channels.editingFinished.connect(self._store_filter)
        g.addWidget(self.f_channels, 1, 1)
        g.addWidget(QLabel(tr("Ignora CC")), 2, 0)
        self.f_cc = QLineEdit()
        self.f_cc.setPlaceholderText(tr("nessuno  ·  es. 1, 64, 120-127"))
        self.f_cc.setToolTip(tr("Numeri di controller da scartare (es. 1 = modulazione, 64 = sustain)"))
        self.f_cc.editingFinished.connect(self._store_filter)
        g.addWidget(self.f_cc, 2, 1)
        g.addWidget(QLabel(tr("Ignora note")), 3, 0)
        self.f_notes = QLineEdit()
        self.f_notes.setPlaceholderText(tr("nessuna  ·  es. 0-35, 96-127"))
        self.f_notes.editingFinished.connect(self._store_filter)
        g.addWidget(self.f_notes, 3, 1)
        self.f_nolearn = QCheckBox(tr("Escludi da «Impara» (i comandi continuano a funzionare)"))
        self.f_nolearn.toggled.connect(self._store_filter)
        g.addWidget(self.f_nolearn, 4, 0, 1, 2)
        self.f_info = QLabel()
        self.f_info.setStyleSheet("color:#888;")
        self.f_info.setWordWrap(True)
        g.addWidget(self.f_info, 5, 0, 1, 2)
        box.setEnabled(False)
        return box

    def _show_filter(self):
        from .remote import default_filter, port_key
        it = self.midi_ports.currentItem()
        port = it.data(Qt.UserRole) if it is not None else None
        self._filter_port = None
        if not port:
            self.filter_box.setEnabled(False)
            self.filter_box.setTitle(tr("Filtri della scheda selezionata"))
            return
        f = dict(default_filter())
        for k, v in self.filters.items():
            if port_key(k) == port_key(port):
                f.update(v)
        self.filter_box.setEnabled(True)
        from .remote import port_short
        self.filter_box.setTitle(tr("Filtri di «{0}»", port_short(port)))
        for k, cb in self.f_types.items():
            cb.blockSignals(True)
            cb.setChecked(k in f["types"])
            cb.blockSignals(False)
        self.f_channels.setText(f["channels"])
        self.f_cc.setText(f["ignore_cc"])
        self.f_notes.setText(f["ignore_notes"])
        self.f_nolearn.blockSignals(True)
        self.f_nolearn.setChecked(bool(f["no_learn"]))
        self.f_nolearn.blockSignals(False)
        self._filter_port = port
        self._filter_info()

    def _store_filter(self, *_):
        from .remote import default_filter, port_key
        port = self._filter_port
        if not port:
            return
        f = {"types": [k for k, cb in self.f_types.items() if cb.isChecked()],
             "channels": self.f_channels.text().strip(), "ignore_cc": self.f_cc.text().strip(),
             "ignore_notes": self.f_notes.text().strip(), "no_learn": self.f_nolearn.isChecked()}
        for k in [k for k in self.filters if port_key(k) == port_key(port)]:
            del self.filters[k]
        if f != default_filter():
            self.filters[port] = f
        self._filter_info()

    def _filter_info(self):
        from .remote import parse_ranges
        bad = [n for n, w in ((tr("Canali"), self.f_channels), (tr("Ignora CC"), self.f_cc), (tr("Ignora note"), self.f_notes))
               if w.text().strip() and parse_ranges(w.text()) is None]
        if bad:
            self.f_info.setText(tr("⚠ Non valido: ") + ", ".join(bad) + tr(" (usa numeri, virgole e intervalli «a-b»)"))
            self.f_info.setStyleSheet("color:#e0b040;")
        else:
            self.f_info.setText(tr("I messaggi scartati non arrivano né ai comandi né a «Impara». "
                                "Le schede senza filtri accettano tutto."))
            self.f_info.setStyleSheet("color:#888;")

    def _learn(self, edit, mode):
        self._store_filter()
        try:
            self.learn_fn(lambda spec: edit.setText(spec), self.checked_ports() or None, mode, self.filters)
        except TypeError:          # learn_fn senza filtri
            self.learn_fn(lambda spec: edit.setText(spec), self.checked_ports() or None, mode)

    def checked_ports(self):
        out = []
        for i in range(self.midi_ports.count()):
            it = self.midi_ports.item(i)
            if it.flags() & Qt.ItemIsUserCheckable and it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out

    def web_address(self, with_pin=None):
        from urllib.parse import quote
        url = f"http://{self.web_ip.currentData() or self.web_ip.currentText()}:{self.web_port.value()}/"
        pin = self.web_pin.text().strip()
        if pin and (self.web_qr_pin.isChecked() if with_pin is None else with_pin):
            url += "#pin=" + quote(pin)
        return url

    def _update_qr(self):
        from PySide6.QtGui import QPixmap
        from .qr import qr_image
        url = self.web_address()
        try:
            pm = QPixmap.fromImage(qr_image(url, scale=6, border=3))
            self.web_qr.setPixmap(pm.scaled(176, 176, Qt.KeepAspectRatio, Qt.FastTransformation))
        except ValueError:
            self.web_qr.setText(tr("QR non disponibile"))
        on = self.web_on.isChecked()
        self.web_qr.setEnabled(on)
        txt = tr("<b>{0}</b>", self.web_address(False))
        if self.web_pin.text().strip():
            txt += (tr("<br>Il QR contiene anche il PIN: chi lo inquadra entra direttamente.")
                    if self.web_qr_pin.isChecked() else tr("<br>Il browser chiederà il PIN."))
        others = [self.web_ip.itemData(i) for i in range(self.web_ip.count())
                  if i != self.web_ip.currentIndex() and self.web_ip.itemData(i) != "127.0.0.1"]
        if others:
            txt += "<br>" + tr("Anche:") + " " + ", ".join(f"http://{ip}:{self.web_port.value()}/" for ip in others)
        if not on:
            txt += "<br><span style='color:#c90'>" + tr("Abilita l'interfaccia web e conferma con OK.") + "</span>"
        else:
            txt += "<br><span style='color:#888'>" + tr("Telefono e computer devono essere sulla stessa rete.") + "</span>"
        self.web_url.setText(txt)

    def _rescan(self):
        if self.rescan_fn:
            self.rescan_fn()
        self._fill_devices()
        self._fill_preview_devices()

    def result_settings(self):
        s = self.s
        s["audio_device"] = self.dev.currentData()
        s["device_channels"] = {k: sp.value() for k, sp in self.ch_spins.items() if sp.value() > 0}
        s["master_db"] = self.master.value()
        s["audio_buffer"] = self.abuf.currentData()
        s["cluster_enabled"] = self.cl_on.isChecked()
        s["cluster_port"] = self.cl_port.value()
        s["cluster_name"] = self.cl_name.text().strip()
        s["cluster_slave_edit"] = self.cl_edit.isChecked()
        s["stop_fade"] = self.stopfade.value()
        s["osc_in_enabled"] = self.osc_on.isChecked()
        s["osc_in_port"] = self.osc_port.value()
        s["osc_devices"] = self.osc_devs
        s["web_enabled"] = self.web_on.isChecked()
        s["web_port"] = self.web_port.value()
        s["web_pin"] = self.web_pin.text().strip()
        s["web_projectors"] = self.web_proj.isChecked()
        self._store_filter()
        s["midi_in_ports"] = self.checked_ports()
        s["midi_filters"] = self.filters
        s["midi_out_port"] = (self.midi_out.currentText() or "").strip()
        s["midi_in_port"] = s["midi_in_ports"][0] if s["midi_in_ports"] else ""
        s["midi_map"] = {k: normalize_spec(le.text()) for k, le in self.map_edits.items()}
        s["midi_controls"] = {k: normalize_control(le.text()) for k, le in self.ctl_edits.items()}
        s["xfade_time"] = self.xftime.value()
        pv = dict(self._pv_cur)
        pv.update(enabled=self.pv_on.isChecked(), device=self.pv_dev.currentData() or "",
                  ch_l=self.pv_l.value(), ch_r=self.pv_r.value())
        s["preview"] = pv
        return s


class GeometryDialog(QDialog):
    """Correzione dell'immagine proiettata trascinando i 4 angoli."""

    def __init__(self, comp, output, on_change, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Geometria uscita – 4 angoli"))
        self.comp = comp
        self.output = output
        self.on_change = on_change
        self.saved = copy.deepcopy(comp.corners)
        v = QVBoxLayout(self)
        info = QLabel(tr("Trascina gli angoli (qui o direttamente sulla finestra di uscita) finché l'immagine "
                      "combacia con lo schermo/la superficie. Tasti 1-4 selezionano un angolo, frecce = "
                      "spostamento fine (Maiusc = ×10)."))
        info.setWordWrap(True)
        v.addWidget(info)
        self.editor = MappingEditor(comp, self._aspect)
        self.editor.changed.connect(self._changed)
        v.addWidget(self.editor, 1)
        g = QGridLayout()
        self.spins = []
        names = [tr("1 · alto sx"), tr("2 · alto dx"), tr("3 · basso dx"), tr("4 · basso sx")]
        for i, n in enumerate(names):
            g.addWidget(QLabel(n), i // 2, (i % 2) * 3)
            sx, sy = QDoubleSpinBox(), QDoubleSpinBox()
            for s in (sx, sy):
                s.setRange(-50, 150)
                s.setDecimals(2)
                s.setSuffix(" %")
                s.setSingleStep(0.1)
                s.valueChanged.connect(self._spin_changed)
            g.addWidget(sx, i // 2, (i % 2) * 3 + 1)
            g.addWidget(sy, i // 2, (i % 2) * 3 + 2)
            self.spins.append((sx, sy))
        v.addLayout(g)
        row = QHBoxLayout()
        self.grid = QCheckBox(tr("Griglia di test sull'uscita"))
        self.grid.setChecked(comp.grid)
        self.grid.toggled.connect(self._grid)
        self.direct = QCheckBox(tr("Modifica sulla finestra di uscita"))
        self.direct.toggled.connect(lambda on: self.output.set_edit_mode(on))
        reset = QPushButton(tr("Reimposta"))
        reset.clicked.connect(self._reset)
        row.addWidget(self.grid)
        row.addWidget(self.direct)
        row.addStretch(1)
        row.addWidget(reset)
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.resize(760, 620)
        self._sync_spins()
        self.output.cornersChanged.connect(self._external)

    def _aspect(self):
        w, h = self.output.width(), self.output.height()
        return w / h if h else 16 / 9

    def _sync_spins(self):
        for (sx, sy), c in zip(self.spins, self.comp.corners):
            for s, val in ((sx, c[0]), (sy, c[1])):
                s.blockSignals(True)
                s.setValue(val * 100)
                s.blockSignals(False)

    def _spin_changed(self):
        for i, (sx, sy) in enumerate(self.spins):
            self.comp.corners[i] = [sx.value() / 100, sy.value() / 100]
        self.comp.changed()
        self.editor.update()
        self.on_change()

    def _changed(self):
        self._sync_spins()
        self.on_change()

    def _external(self):
        self._sync_spins()
        self.editor.update()
        self.on_change()

    def _grid(self, on):
        self.comp.grid = on
        self.comp.changed()
        self.editor.update()
        self.on_change()

    def _reset(self):
        self.comp.corners[:] = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
        self.comp.changed()
        self._sync_spins()
        self.editor.update()
        self.on_change()

    def reject(self):
        self.comp.corners[:] = self.saved
        self.comp.changed()
        self.on_change()
        self._finish()
        super().reject()

    def accept(self):
        self._finish()
        super().accept()

    def _finish(self):
        self.output.set_edit_mode(False)
        try:
            self.output.cornersChanged.disconnect(self._external)
        except Exception:
            pass


class MidiLearnDialog(QDialog):
    def __init__(self, parent=None, info="", mode="trigger"):
        super().__init__(parent)
        self.mode = mode
        self.setWindowTitle(tr("Impara MIDI") if mode == "trigger" else tr("Impara controllo MIDI"))
        v = QVBoxLayout(self)
        if mode == "control":
            self.lab = QLabel(tr("Muovi il fader / potenziometro sul dispositivo MIDI…\n"
                              "(oppure scrivi: «cc 1 7», «pb 1» per il pitch bend, «pc 1 *» per il program change)"))
        else:
            self.lab = QLabel(tr("Premi un tasto / muovi un controllo sul dispositivo MIDI…\n"
                              "(oppure scrivi la sequenza: «note 1 60», «cc 1 20», «pc 1 3»)"))
        v.addWidget(self.lab)
        if info:
            il = QLabel(info)
            il.setStyleSheet("color:#e0b040;" if info.startswith("⚠") else "color:#6c6;")
            il.setWordWrap(True)
            v.addWidget(il)
        self.edit = QLineEdit()
        v.addWidget(self.edit)
        self.only_port = QCheckBox(tr("Solo dalla scheda da cui arriva (aggiunge «@ scheda»)"))
        self.only_port.setChecked(True)
        v.addWidget(self.only_port)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.callback = None

    def got(self, spec, port=""):
        from .remote import port_label
        if port and self.only_port.isChecked():
            spec = f"{spec} @ {port_label(port)}"
        self.edit.setText(spec)
        self.accept()

    def value(self):
        if self.mode == "control":
            return normalize_control(self.edit.text())
        return normalize_spec(self.edit.text())


OSC_HELP = tr("""
<h3>Comandi OSC ricevuti (UDP, porta configurabile – predefinita 53000)</h3>
<table cellpadding=3>
<tr><td><b>/go</b></td><td>GO sulla cue al playhead</td></tr>
<tr><td><b>/stop</b> &nbsp; <b>/panic</b></td><td>Stop di tutte le cue (con fade / immediato)</td></tr>
<tr><td><b>/pause</b> &nbsp; <b>/resume</b></td><td>Pausa / ripresa generale</td></tr>
<tr><td><b>/dim</b> [1|0]</td><td>DIM: master abbassato della riduzione del ducking (senza argomento cambia stato)</td></tr>
<tr><td><b>/duck</b> [1|0]</td><td>Duck: Master ducking acceso / spento (senza argomento cambia stato)</td></tr>
<tr><td><b>/next</b> &nbsp; <b>/previous</b></td><td>Sposta il playhead</td></tr>
<tr><td><b>/playhead/&lt;numero&gt;</b></td><td>Porta il playhead sulla cue indicata</td></tr>
<tr><td><b>/text/next</b>, <b>/text/prev</b>, <b>/text/goto</b> n</td><td>Frase successiva / precedente / n-esima del testo in sequenza</td></tr>
<tr><td><b>/xfade</b> [secondi]</td><td>Crossfade dalla cue corrente alla successiva</td></tr>
<tr><td><b>/xfader</b> 0…1</td><td>Posizione del crossfader manuale</td></tr>
<tr><td><b>/list/&lt;nome&gt;</b></td><td>Mostra la lista (MAIN o un PANEL)</td></tr>
<tr><td><b>/list/&lt;nome&gt;/go</b></td><td>GO sulla lista indicata, qualunque sia «GO su»</td></tr>
<tr><td><b>/cue/&lt;numero&gt;/start</b></td><td>Avvia la cue (con la sua catena)</td></tr>
<tr><td><b>/cue/&lt;numero&gt;/go</b></td><td>Come sopra</td></tr>
<tr><td><b>/cue/&lt;numero&gt;/stop</b></td><td>Ferma la cue (argomento opzionale: secondi di fade)</td></tr>
<tr><td><b>/cue/&lt;numero&gt;/pause</b>, <b>/resume</b></td><td>Pausa / ripresa di una cue</td></tr>
<tr><td><b>/cue/&lt;numero&gt;/level</b> dB [secondi]</td><td>Porta il volume della cue a dB, con fade opzionale</td></tr>
</table>
<p>Ogni cue può anche avere un <b>indirizzo OSC personalizzato</b> (scheda «Base») che la avvia.</p>
<p>Esempio da terminale: <code>oscsend localhost 53000 /go</code> (pacchetto liblo-tools) oppure
<code>python3 -c "from pythonosc.udp_client import SimpleUDPClient as C; C('127.0.0.1',53000).send_message('/go',[])"</code></p>
<h3>MIDI</h3>
<p>Formato dei trigger: <code>note &lt;canale&gt; &lt;nota&gt;</code>, <code>cc &lt;canale&gt; &lt;controller&gt;</code>,
<code>pc &lt;canale&gt; &lt;programma&gt;</code>. Canale 0 = qualsiasi canale. Le note con velocity 0 e i CC
con valore 0 vengono ignorati.</p>
<h3>Tastiera</h3>
<p><b>Spazio</b> GO · <b>Esc</b> Stop tutto · <b>F7</b> Pausa tutto · <b>F8</b> Riprendi ·
<b>Ctrl+S</b> Salva · <b>Canc</b> elimina cue · <b>Ctrl+D</b> duplica · <b>Ctrl+T</b> prova la cue selezionata</p>
""")


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Guida comandi"))
        v = QVBoxLayout(self)
        t = QTextBrowser()
        t.setHtml(OSC_HELP)
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.resize(640, 520)
