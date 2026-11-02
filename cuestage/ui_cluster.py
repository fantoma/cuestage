"""Finestra «Diventa slave»: ricerca del master, modalità, sincronizzazione."""
from .i18n import tr
import os
import threading

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QPushButton,
                               QRadioButton, QSpinBox, QVBoxLayout)

from .cluster import DEFAULT_PORT, discover_masters


class _Relay(QObject):
    found = Signal(object)


class BecomeSlaveDialog(QDialog):
    def __init__(self, hub, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.setWindowTitle(tr("Diventa slave"))
        self.qs = QSettings("CueStage", "CueStage")
        v = QVBoxLayout(self)
        # ---------------------------------------------------- master
        gm = QGroupBox(tr("Master"))
        fm = QVBoxLayout(gm)
        row = QHBoxLayout()
        self.search_btn = QPushButton(tr("🔍 Cerca master in rete"))
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
        hr.addWidget(QLabel(tr("Indirizzo")))
        self.host = QLineEdit(self.qs.value("cluster/host", ""))
        self.host.setPlaceholderText(tr("es. 192.168.1.20"))
        hr.addWidget(self.host, 1)
        hr.addWidget(QLabel(tr("Porta")))
        self.port = QSpinBox()
        self.port.setRange(1, 65534)
        self.port.setValue(int(self.qs.value("cluster/port", DEFAULT_PORT)))
        hr.addWidget(self.port)
        fm.addLayout(hr)
        v.addWidget(gm)
        # ---------------------------------------------------- questa istanza
        gs = QGroupBox(tr("Questa istanza"))
        fs = QFormLayout(gs)
        self.name = QLineEdit(self.qs.value("cluster/name", "") or hub.name)
        fs.addRow(tr("Nome"), self.name)
        self.m_slave = QRadioButton(tr("Slave: copia del progetto sempre aggiornata, non esegue le cue del master"))
        self.m_red = QRadioButton(tr("Ridondanza: esegue esattamente le stesse cue comandate dal master"))
        mg = QButtonGroup(self)
        mg.addButton(self.m_slave)
        mg.addButton(self.m_red)
        (self.m_red if self.qs.value("cluster/mode", "redundancy") == "redundancy" else self.m_slave).setChecked(True)
        mv = QVBoxLayout()
        mv.addWidget(self.m_slave)
        mv.addWidget(self.m_red)
        fs.addRow(tr("Modalità"), mv)
        self.s_diff = QRadioButton(tr("Differenziale: scarica i file mancanti e aggiorna quelli diversi"))
        self.s_full = QRadioButton(tr("Totale: scarica di nuovo tutti i file"))
        sg = QButtonGroup(self)
        sg.addButton(self.s_diff)
        sg.addButton(self.s_full)
        (self.s_full if self.qs.value("cluster/sync", "diff") == "full" else self.s_diff).setChecked(True)
        sv = QVBoxLayout()
        sv.addWidget(self.s_diff)
        sv.addWidget(self.s_full)
        fs.addRow(tr("Contenuti"), sv)
        self.copy = QCheckBox(tr("Copia tutte le impostazioni del progetto (schede audio, mixer, proiettori, MIDI, OSC…)"))
        self.copy.setChecked(self.qs.value("cluster/copy", "true") in (True, "true"))
        fs.addRow(self.copy)
        dr = QHBoxLayout()
        self.dir = QLineEdit(self.qs.value("cluster/dir", "") or os.path.expanduser("~/CueStage-slave"))
        dr.addWidget(self.dir, 1)
        b = QPushButton(tr("Sfoglia…"))
        b.clicked.connect(self._browse)
        dr.addWidget(b)
        fs.addRow(tr("Cartella locale"), dr)
        lr = QHBoxLayout()
        self.lost = QSpinBox()
        self.lost.setRange(200, 10000)
        self.lost.setSingleStep(100)
        self.lost.setSuffix(" ms")
        self.lost.setValue(int(self.qs.value("cluster/lost_ms", 500)))
        self.lost.setToolTip(tr("Il master invia un heartbeat ogni 100 ms: senza heartbeat per questo tempo è «perso»"))
        lr.addWidget(self.lost)
        self.auto = QCheckBox(tr("promuovi automaticamente a master"))
        self.auto.setChecked(self.qs.value("cluster/auto_promote", "false") in (True, "true"))
        lr.addWidget(self.auto)
        lr.addStretch(1)
        fs.addRow(tr("Master perso dopo"), lr)
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
        self.go_btn = QPushButton(tr("Collega e sincronizza"))
        self.go_btn.setDefault(True)
        self.go_btn.clicked.connect(self.connect_master)
        br.addWidget(self.go_btn)
        self.close_btn = QPushButton(tr("Chiudi"))
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
        d = QFileDialog.getExistingDirectory(self, tr("Cartella locale dei contenuti"), self.dir.text())
        if d:
            self.dir.setText(d)

    def search(self):
        self.search_btn.setEnabled(False)
        self.search_lab.setText(tr("ricerca…"))
        port = self.port.value()
        threading.Thread(target=lambda: self.relay.found.emit(discover_masters(port)), daemon=True).start()

    def _found(self, res):
        self.search_btn.setEnabled(True)
        self.found.clear()
        own = self.hub.instance_id
        res = [r for r in res if r.get("id") != own]
        for r in res:
            it = QListWidgetItem(tr("{0}  ·  {1}:{2}  ·  show «{3}»  ·  {4} secondarie", r.get('name'), r['host'], r.get('port'), r.get('show'), r.get('slaves', 0)))
            it.setData(Qt.UserRole, r)
            self.found.addItem(it)
        self.search_lab.setText(tr("{0} master trovati", len(res)) if res else
                                tr("nessun master: sul master attiva «Abilita istanze secondarie» nelle impostazioni"))
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
            self.info.setText(tr("Scegli un master dall'elenco o scrivi il suo indirizzo."))
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
            self.info.setText(tr("✔ Collegato come {0}: {1}", 'RIDONDANZA' if self.m_red.isChecked() else 'SLAVE', text))
            self.info.setStyleSheet("color:#6c6;")
            self.close_btn.setText(tr("Fatto"))
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


# =========================================================================== master: autorizzazione
PERM_LABELS = {"": tr("— esclusa —"), "control": tr("Solo controllo"), "edit": tr("Modifica")}


def perms_summary(perms, lists):
    """Testo breve dei permessi di una secondaria."""
    perms = perms or {}
    if "*" in perms:
        return tr("tutto il progetto · {0}", PERM_LABELS.get(perms["*"], perms["*"]).lower())
    names = {l.id: l.name for l in lists}
    parts = [f"{names.get(k, k)} ({PERM_LABELS.get(v, v).lower()})" for k, v in perms.items()]
    return ", ".join(parts) if parts else tr("nessuna lista")


class JoinDialog(QDialog):
    """Richiesta di collegamento di un'istanza secondaria. In modalità SLAVE il master sceglie a cosa ha
    accesso: tutto il progetto oppure solo alcune liste (MAIN, PANEL, GRID), ciascuna in «solo controllo»
    (esegue le cue) o «modifica» (può anche cambiarle). La secondaria riceve solo i file delle sue liste."""

    def __init__(self, name, addr, mode, lists, edit_default=False, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QComboBox, QGridLayout
        self.setWindowTitle(tr("Istanza secondaria"))
        self.lists = lists
        v = QVBoxLayout(self)
        red = mode == "redundancy"
        what = tr("RIDONDANZA (esegue le stesse cue)") if red else tr("SLAVE (copia del progetto)")
        lab = QLabel(tr("«{0}» ({1}) chiede di collegarsi come {2}.", name, addr, what))
        lab.setWordWrap(True)
        v.addWidget(lab)
        self.all_rb = QRadioButton(tr("Tutto il progetto"))
        self.some_rb = QRadioButton(tr("Solo queste liste (riceve solo i loro file):"))
        self.all_rb.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(self.all_rb)
        self.all_mode = QComboBox()
        for k in ("control", "edit"):
            self.all_mode.addItem(PERM_LABELS[k], k)
        self.all_mode.setCurrentIndex(1 if edit_default else 0)
        row.addWidget(self.all_mode)
        row.addStretch(1)
        v.addLayout(row)
        self.combos = {}
        box = QGroupBox()
        g = QGridLayout(box)
        for r, lst in enumerate(lists):
            kind = tr("GRID") if lst.is_grid else ("MAIN" if r == 0 else "PANEL")
            kind = "" if kind == lst.name else kind + " · "
            g.addWidget(QLabel(f"<b>{lst.name}</b>  <span style='color:#888'>{kind}"
                               + tr("{0} cue", len(lst.cues)) + "</span>"), r, 0)
            cb = QComboBox()
            for k in ("", "control", "edit"):
                cb.addItem(PERM_LABELS[k], k)
            cb.setCurrentIndex(0)
            g.addWidget(cb, r, 1)
            self.combos[lst.id] = cb
        g.setColumnStretch(0, 1)
        if not red:
            v.addWidget(self.some_rb)
            v.addWidget(box)
        else:
            box.hide()
            note = QLabel(tr("La ridondanza riceve sempre tutto il progetto ed esegue gli stessi comandi del master."))
            note.setWordWrap(True)
            note.setStyleSheet("color:#888;")
            v.addWidget(note)
        self.some_rb.toggled.connect(box.setEnabled)
        self.all_rb.toggled.connect(self.all_mode.setEnabled)
        box.setEnabled(False)
        v.addWidget(QLabel(tr("Riceverà lo show, i file multimediali e ogni modifica. Accetti?")))
        br = QHBoxLayout()
        br.addStretch(1)
        no = QPushButton(tr("Rifiuta"))
        no.clicked.connect(self.reject)
        ok = QPushButton(tr("Accetta"))
        ok.setDefault(True)
        ok.clicked.connect(self._ok)
        br.addWidget(no)
        br.addWidget(ok)
        v.addLayout(br)
        self.resize(520, 200 + (0 if red else 34 * len(lists)))

    def _ok(self):
        if self.some_rb.isChecked() and not self.perms():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, tr("Istanza secondaria"), tr("Scegli almeno una lista."))
            return
        self.accept()

    def perms(self):
        if self.all_rb.isChecked():
            return {"*": self.all_mode.currentData()}
        return {lid: cb.currentData() for lid, cb in self.combos.items() if cb.currentData()}


# =========================================================================== istanze e messaggi
class ClusterWindow(QDialog):
    """Istanze collegate (sul master: con «Disconnetti») e messaggi tra tutte le istanze e le interfacce web."""

    def __init__(self, hub, main, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QComboBox, QPlainTextEdit, QTableWidget, QHeaderView
        self.hub, self.main = hub, main
        self.setWindowTitle(tr("Istanze e messaggi"))
        v = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr("Istanza"), tr("Indirizzo"), tr("Modalità"), tr("Accesso"), ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        for i in (0, 1, 2, 4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.setMaximumHeight(170)
        v.addWidget(self.table)
        v.addWidget(QLabel(tr("<b>Messaggi</b>")))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        row = QHBoxLayout()
        self.to = QComboBox()
        self.to.setMinimumWidth(150)
        row.addWidget(QLabel(tr("A")))
        row.addWidget(self.to)
        self.text = QLineEdit()
        self.text.setPlaceholderText(tr("Scrivi un messaggio e premi Invio"))
        self.text.returnPressed.connect(self.send)
        row.addWidget(self.text, 1)
        b = QPushButton(tr("Invia"))
        b.clicked.connect(self.send)
        row.addWidget(b)
        v.addLayout(row)
        hub.chatReceived.connect(self._append)
        hub.rosterChanged.connect(self.refresh)
        hub.clientsChanged.connect(self.refresh)
        hub.roleChanged.connect(self.refresh)
        self.resize(720, 560)
        self.refresh()
        for m in hub.chat:
            self._append(m, scroll=False)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def done(self, r):
        for sig, slot in ((self.hub.chatReceived, self._append), (self.hub.rosterChanged, self.refresh),
                          (self.hub.clientsChanged, self.refresh), (self.hub.roleChanged, self.refresh)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(r)

    @staticmethod
    def line(m):
        import time as _t
        when = _t.strftime("%H:%M", _t.localtime(m.get("ts") or _t.time()))
        return f"[{when}] {m.get('from')}: {m.get('text')}"

    def _append(self, m, scroll=True):
        txt = self.line(m)
        if m.get("to") not in (None, "*"):
            name = dict(self.hub.recipients()).get(m.get("to"), m.get("to"))
            txt = txt.replace(f"{m.get('from')}:", f"{m.get('from')} → {name}:", 1)
        self.log.appendPlainText(txt)
        if scroll:
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def refresh(self):
        hub = self.hub
        if hub.role == "master":
            self.info.setText(tr("Questa istanza è MASTER: le secondarie collegate sono qui sotto."))
        elif hub.role == "slave":
            self.info.setText(tr("Collegata a «{0}» ({1}:{2}).", hub.master_info.get("name"),
                                 hub.master_info.get("host"), hub.master_info.get("port")))
        else:
            self.info.setText(tr("Nessun collegamento: i messaggi restano su questa istanza (operatore e "
                                 "interfaccia web)."))
        rows = hub.client_rows() if hub.role == "master" else []
        if hub.role == "slave":
            rows = [(None, r.get("name"), "", r.get("role"), None) for r in hub.roster]
        self.table.setRowCount(len(rows))
        from PySide6.QtWidgets import QTableWidgetItem
        lists = self.main.show_data.lists
        for i, (cid, name, addr, mode, perms) in enumerate(rows):
            kind = {"redundancy": tr("ridondanza"), "slave": tr("slave"), "master": tr("master")}.get(mode, mode)
            for col, val in enumerate((name, addr, kind, perms_summary(perms, lists) if perms else "")):
                self.table.setItem(i, col, QTableWidgetItem(str(val or "")))
            if cid is not None:
                b = QPushButton(tr("Disconnetti"))
                b.clicked.connect(lambda _=False, cid=cid, name=name: self._kick(cid, name))
                self.table.setCellWidget(i, 4, b)
            else:
                self.table.setCellWidget(i, 4, None)
        self.table.setVisible(bool(rows))
        cur = self.to.currentData()
        self.to.clear()
        self.to.addItem(tr("Tutti"), "*")
        for iid, name in hub.recipients():
            if iid != hub.instance_id:
                self.to.addItem(name, iid)
        j = self.to.findData(cur)
        self.to.setCurrentIndex(max(0, j))

    def _kick(self, cid, name):
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, tr("Disconnetti"), tr("Disconnettere «{0}»?", name)) == QMessageBox.Yes:
            self.hub.kick(cid)

    def send(self):
        t = self.text.text().strip()
        if not t:
            return
        if self.hub.send_chat(t, self.to.currentData() or "*"):
            self.text.clear()


# =========================================================================== allarme «master perso»
class LostMasterAlarm(QFrame):
    """Banda rossa lampeggiante in cima alla finestra quando un'istanza secondaria perde il master: resta
    finché l'operatore preme OK (anche se nel frattempo il master torna o l'istanza viene promossa)."""
    promoteRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import QTimer
        self.setObjectName("lostAlarm")
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 6, 8, 6)
        self.label = QLabel()
        self.label.setWordWrap(True)
        h.addWidget(self.label, 1)
        self.promote_btn = QPushButton(tr("Promuovi ora"))
        self.promote_btn.setFocusPolicy(Qt.NoFocus)
        self.promote_btn.clicked.connect(self.promoteRequested.emit)
        h.addWidget(self.promote_btn)
        self.ok_btn = QPushButton(tr("OK"))
        self.ok_btn.setFocusPolicy(Qt.NoFocus)
        self.ok_btn.setMinimumWidth(90)
        self.ok_btn.clicked.connect(self.dismiss)
        h.addWidget(self.ok_btn)
        self.timer = QTimer(self)
        self.timer.setInterval(450)
        self.timer.timeout.connect(self._blink)
        self._on = False
        self._blinking = False
        self.hide()

    def _paint(self, bright):
        bg = "#e0261b" if bright else "#5a0d09"
        self.setStyleSheet(f"#lostAlarm{{background:{bg};border-radius:6px;}}"
                           "#lostAlarm QLabel{color:white;font-size:17px;font-weight:bold;}"
                           "#lostAlarm QPushButton{background:white;color:#900;font-weight:bold;"
                           "border-radius:4px;padding:6px 14px;}")

    def _blink(self):
        self._on = not self._on
        self._paint(self._on)

    def alarm(self, master_name):
        self.label.setText(tr("⚠ MASTER PERSO: «{0}» non risponde", master_name))
        self.promote_btn.setVisible(True)
        self._blinking = True
        self._on = True
        self._paint(True)
        self.timer.start()
        self.show()

    def master_back(self):
        """Il master risponde di nuovo: l'allarme resta (va confermato) ma smette di lampeggiare."""
        if not self.isVisible():
            return
        self.timer.stop()
        self._blinking = False
        self._paint(False)
        self.label.setText(self.label.text() + tr("  —  di nuovo raggiungibile"))
        self.promote_btn.setVisible(False)

    def promoted(self):
        if self.isVisible():
            self.promote_btn.setVisible(False)

    @property
    def active(self):
        return self.isVisible()

    def dismiss(self):
        self.timer.stop()
        self._blinking = False
        self.hide()
