"""Finestra «Diventa slave»: ricerca del master, modalità, sincronizzazione."""
import os
import threading

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QPushButton,
                               QRadioButton, QSpinBox, QVBoxLayout)

from .cluster import DEFAULT_PORT, discover_masters


class _Relay(QObject):
    found = Signal(object)


class BecomeSlaveDialog(QDialog):
    def __init__(self, hub, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.setWindowTitle("Diventa slave")
        self.qs = QSettings("CueStage", "CueStage")
        v = QVBoxLayout(self)
        # ---------------------------------------------------- master
        gm = QGroupBox("Master")
        fm = QVBoxLayout(gm)
        row = QHBoxLayout()
        self.search_btn = QPushButton("🔍 Cerca master in rete")
        self.search_btn.clicked.connect(self.search)
        row.addWidget(self.search_btn)
        self.search_lab = QLabel("")
        self.search_lab.setStyleSheet("color:#999;")
        row.addWidget(self.search_lab, 1)
        fm.addLayout(row)
        self.found = QListWidget()
        self.found.setMinimumHeight(110)
        self.found.currentItemChanged.connect(self._picked)
        fm.addWidget(self.found)
        hr = QHBoxLayout()
        hr.addWidget(QLabel("Indirizzo"))
        self.host = QLineEdit(self.qs.value("cluster/host", ""))
        self.host.setPlaceholderText("es. 192.168.1.20")
        hr.addWidget(self.host, 1)
        hr.addWidget(QLabel("Porta"))
        self.port = QSpinBox()
        self.port.setRange(1, 65534)
        self.port.setValue(int(self.qs.value("cluster/port", DEFAULT_PORT)))
        hr.addWidget(self.port)
        fm.addLayout(hr)
        v.addWidget(gm)
        # ---------------------------------------------------- questa istanza
        gs = QGroupBox("Questa istanza")
        fs = QFormLayout(gs)
        self.name = QLineEdit(self.qs.value("cluster/name", "") or hub.name)
        fs.addRow("Nome", self.name)
        self.m_slave = QRadioButton("Slave: copia del progetto sempre aggiornata, non esegue le cue del master")
        self.m_red = QRadioButton("Ridondanza: esegue esattamente le stesse cue comandate dal master")
        mg = QButtonGroup(self)
        mg.addButton(self.m_slave)
        mg.addButton(self.m_red)
        (self.m_red if self.qs.value("cluster/mode", "redundancy") == "redundancy" else self.m_slave).setChecked(True)
        mv = QVBoxLayout()
        mv.addWidget(self.m_slave)
        mv.addWidget(self.m_red)
        fs.addRow("Modalità", mv)
        self.s_diff = QRadioButton("Differenziale: scarica i file mancanti e aggiorna quelli diversi")
        self.s_full = QRadioButton("Totale: scarica di nuovo tutti i file")
        sg = QButtonGroup(self)
        sg.addButton(self.s_diff)
        sg.addButton(self.s_full)
        (self.s_full if self.qs.value("cluster/sync", "diff") == "full" else self.s_diff).setChecked(True)
        sv = QVBoxLayout()
        sv.addWidget(self.s_diff)
        sv.addWidget(self.s_full)
        fs.addRow("Contenuti", sv)
        self.copy = QCheckBox("Copia tutte le impostazioni del progetto (schede audio, mixer, proiettori, MIDI, OSC…)")
        self.copy.setChecked(self.qs.value("cluster/copy", "true") in (True, "true"))
        fs.addRow(self.copy)
        dr = QHBoxLayout()
        self.dir = QLineEdit(self.qs.value("cluster/dir", "") or os.path.expanduser("~/CueStage-slave"))
        dr.addWidget(self.dir, 1)
        b = QPushButton("Sfoglia…")
        b.clicked.connect(self._browse)
        dr.addWidget(b)
        fs.addRow("Cartella locale", dr)
        lr = QHBoxLayout()
        self.lost = QSpinBox()
        self.lost.setRange(200, 10000)
        self.lost.setSingleStep(100)
        self.lost.setSuffix(" ms")
        self.lost.setValue(int(self.qs.value("cluster/lost_ms", 500)))
        self.lost.setToolTip("Il master invia un heartbeat ogni 100 ms: senza heartbeat per questo tempo è «perso»")
        lr.addWidget(self.lost)
        self.auto = QCheckBox("promuovi automaticamente a master")
        self.auto.setChecked(self.qs.value("cluster/auto_promote", "false") in (True, "true"))
        lr.addWidget(self.auto)
        lr.addStretch(1)
        fs.addRow("Master perso dopo", lr)
        v.addWidget(gs)
        # ---------------------------------------------------- avanzamento
        self.info = QLabel("")
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        v.addWidget(self.bar)
        br = QHBoxLayout()
        br.addStretch(1)
        self.go_btn = QPushButton("Collega e sincronizza")
        self.go_btn.setDefault(True)
        self.go_btn.clicked.connect(self.connect_master)
        br.addWidget(self.go_btn)
        self.close_btn = QPushButton("Chiudi")
        self.close_btn.clicked.connect(self.reject)
        br.addWidget(self.close_btn)
        v.addLayout(br)
        self.relay = _Relay()
        self.relay.found.connect(self._found)
        hub.syncProgress.connect(self._progress)
        hub.syncDone.connect(self._done)
        self.resize(720, 640)
        self.search()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Cartella locale dei contenuti", self.dir.text())
        if d:
            self.dir.setText(d)

    def search(self):
        self.search_btn.setEnabled(False)
        self.search_lab.setText("ricerca…")
        port = self.port.value()
        threading.Thread(target=lambda: self.relay.found.emit(discover_masters(port)), daemon=True).start()

    def _found(self, res):
        self.search_btn.setEnabled(True)
        self.found.clear()
        own = self.hub.instance_id
        res = [r for r in res if r.get("id") != own]
        for r in res:
            it = QListWidgetItem(f"{r.get('name')}  ·  {r['host']}:{r.get('port')}  ·  show «{r.get('show')}»"
                                 f"  ·  {r.get('slaves', 0)} secondarie")
            it.setData(Qt.UserRole, r)
            self.found.addItem(it)
        self.search_lab.setText(f"{len(res)} master trovati" if res else
                                "nessun master: sul master attiva «Abilita istanze secondarie» nelle impostazioni")
        if res and not self.host.text():
            self.found.setCurrentRow(0)

    def _picked(self, it, _prev=None):
        if it is not None:
            r = it.data(Qt.UserRole)
            self.host.setText(r["host"])
            self.port.setValue(int(r.get("port") or DEFAULT_PORT))

    def connect_master(self):
        host = self.host.text().strip()
        if not host:
            self.info.setText("Scegli un master dall'elenco o scrivi il suo indirizzo.")
            return
        qs = self.qs
        mode = "redundancy" if self.m_red.isChecked() else "slave"
        sync = "full" if self.s_full.isChecked() else "diff"
        qs.setValue("cluster/host", host)
        qs.setValue("cluster/port", self.port.value())
        qs.setValue("cluster/name", self.name.text().strip())
        qs.setValue("cluster/mode", mode)
        qs.setValue("cluster/sync", sync)
        qs.setValue("cluster/copy", "true" if self.copy.isChecked() else "false")
        qs.setValue("cluster/dir", self.dir.text().strip())
        qs.setValue("cluster/lost_ms", self.lost.value())
        qs.setValue("cluster/auto_promote", "true" if self.auto.isChecked() else "false")
        self.hub.name = self.name.text().strip() or self.hub.name
        self.go_btn.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.hub.connect_slave(host, self.port.value(), mode, sync, self.copy.isChecked(),
                               os.path.expanduser(self.dir.text().strip()), self.lost.value())

    def _progress(self, text, done, total):
        self.info.setText(text)
        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done * 1000 / total))
            self.bar.setFormat(f"{done / 1e6:.1f} / {total / 1e6:.1f} MB")
        else:
            self.bar.setRange(0, 0)

    def _done(self, ok, text):
        self.bar.setRange(0, 1)
        self.bar.setValue(1 if ok else 0)
        if ok:
            self.info.setText(f"✔ Collegato come {'RIDONDANZA' if self.m_red.isChecked() else 'SLAVE'}: {text}")
            self.info.setStyleSheet("color:#6c6;")
            self.close_btn.setText("Fatto")
        else:
            self.info.setText(f"✘ {text}")
            self.info.setStyleSheet("color:#e66;")
            self.go_btn.setEnabled(True)

    def done(self, r):
        for sig, slot in ((self.hub.syncProgress, self._progress), (self.hub.syncDone, self._done)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(r)
