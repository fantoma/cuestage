"""Dialogo «Proiettori»: elenco dei videoproiettori in rete, driver/plugin, prova di connessione."""
from .i18n import tr
import copy
import os
import uuid

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from .projectors import USER_PLUGIN_DIR

FIELD_LABELS = {"host": tr("Indirizzo IP"), "port": tr("Porta TCP"), "user": tr("Utente"),
                "password": tr("Password"), "proj_id": tr("ID proiettore")}


class _Relay(QObject):
    result = Signal(bool, str)


class ProjectorsDialog(QDialog):
    def __init__(self, projectors, manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Proiettori in rete"))
        self.pm = manager
        self.items = copy.deepcopy(projectors or [])
        self.cur = None
        self.relay = _Relay()
        self.relay.result.connect(self._test_done)
        v = QVBoxLayout(self)
        h = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list, 1)
        bl = QHBoxLayout()
        for text, fn in ((tr("Aggiungi"), self._add), (tr("Duplica"), self._dup), (tr("Elimina"), self._del)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bl.addWidget(b)
        left.addLayout(bl)
        h.addLayout(left, 1)

        right = QVBoxLayout()
        self.form_w = QWidget()
        f = QFormLayout(self.form_w)
        self.name = QLineEdit()
        self.name.textEdited.connect(self._store)
        f.addRow(tr("Nome"), self.name)
        self.driver = QComboBox()
        self.driver.currentIndexChanged.connect(self._driver_changed)
        f.addRow(tr("Driver"), self.driver)
        self.fields = {}
        for key in ("host", "port", "user", "password", "proj_id"):
            if key == "port":
                w = QSpinBox()
                w.setRange(1, 65535)
                w.valueChanged.connect(self._store)
            else:
                w = QLineEdit()
                if key == "password":
                    w.setEchoMode(QLineEdit.Password)
                if key == "proj_id":
                    w.setPlaceholderText(tr("00 = tutti"))
                if key == "host":
                    w.setPlaceholderText(tr("es. 192.168.0.50"))
                w.textEdited.connect(self._store)
            lab = QLabel(FIELD_LABELS[key])
            f.addRow(lab, w)
            self.fields[key] = (lab, w)
        right.addWidget(self.form_w)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color:#999;")
        right.addWidget(self.notes)
        tb = QHBoxLayout()
        self.test_btn = QPushButton(tr("Prova connessione"))
        self.test_btn.clicked.connect(self._test)
        tb.addWidget(self.test_btn)
        tb.addStretch(1)
        right.addLayout(tb)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(110)
        self.result.setPlaceholderText(tr("Risultato della prova…"))
        right.addWidget(self.result)
        h.addLayout(right, 2)
        v.addLayout(h, 1)

        pl = QHBoxLayout()
        self.plug_lab = QLabel()
        self.plug_lab.setWordWrap(True)
        pl.addWidget(self.plug_lab, 1)
        rb = QPushButton(tr("Ricarica plugin"))
        rb.clicked.connect(self._reload)
        ob = QPushButton(tr("Apri cartella plugin"))
        ob.clicked.connect(self._open_dir)
        pl.addWidget(rb)
        pl.addWidget(ob)
        v.addLayout(pl)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.resize(820, 520)
        self._fill_drivers()
        self._fill_list()

    # ------------------------------------------------ elenco
    def _fill_drivers(self):
        cur = self.driver.currentData()
        self.driver.blockSignals(True)
        self.driver.clear()
        for did, cls in sorted(self.pm.registry.drivers.items(), key=lambda x: (x[0] != "pjlink", x[1].NAME)):
            self.driver.addItem(cls.NAME, did)
        i = self.driver.findData(cur)
        self.driver.setCurrentIndex(max(0, i))
        self.driver.blockSignals(False)
        errs = self.pm.registry.errors
        txt = tr("Plugin caricati: {0} driver. Plugin utente in {1}", len(self.pm.registry.drivers), USER_PLUGIN_DIR)
        if errs:
            txt += "\n⚠ " + "\n⚠ ".join(errs)
        self.plug_lab.setText(txt)
        self.plug_lab.setStyleSheet("color:#e0b040;" if errs else "color:#888;")

    def _fill_list(self, select=0):
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.items:
            cls = self.pm.registry.get(p.get("driver"))
            drv = cls.NAME if cls else tr("{0} (driver mancante)", p.get('driver'))
            QListWidgetItem(f"{p.get('name') or tr('(senza nome)')}\n   {p.get('host') or '—'} · {drv}", self.list)
        self.list.blockSignals(False)
        if self.items:
            self.list.setCurrentRow(min(select, len(self.items) - 1))
        else:
            self._select(-1)

    def _select(self, row):
        self.cur = self.items[row] if 0 <= row < len(self.items) else None
        self.form_w.setEnabled(self.cur is not None)
        self.test_btn.setEnabled(self.cur is not None)
        self.result.clear()
        if self.cur is None:
            self.notes.setText(tr("Aggiungi un proiettore con «Aggiungi»."))
            return
        p = self.cur
        self.name.setText(p.get("name", ""))
        self.driver.blockSignals(True)
        i = self.driver.findData(p.get("driver"))
        if i < 0:
            self.driver.addItem(tr("{0} (mancante)", p.get('driver')), p.get("driver"))
            i = self.driver.count() - 1
        self.driver.setCurrentIndex(i)
        self.driver.blockSignals(False)
        for key, (lab, w) in self.fields.items():
            w.blockSignals(True)
            if key == "port":
                w.setValue(int(p.get("port") or 1))
            else:
                w.setText(str(p.get(key) or ""))
            w.blockSignals(False)
        self._show_fields()

    def _show_fields(self):
        cls = self.pm.registry.get(self.driver.currentData())
        wanted = set(cls.FIELDS) if cls else {"host", "port"}
        for key, (lab, w) in self.fields.items():
            lab.setVisible(key in wanted)
            w.setVisible(key in wanted)
        self.notes.setText(cls.NOTES if cls else tr("Driver non disponibile: controlla i plugin."))

    def _driver_changed(self, i):
        if self.cur is None:
            return
        cls = self.pm.registry.get(self.driver.currentData())
        old = self.pm.registry.get(self.cur.get("driver"))
        self.cur["driver"] = self.driver.currentData()
        # porta predefinita del nuovo driver se era quella del precedente
        if cls and (not self.cur.get("port") or (old and int(self.cur.get("port") or 0) == old.DEFAULT_PORT)):
            self.cur["port"] = cls.DEFAULT_PORT
            self.fields["port"][1].blockSignals(True)
            self.fields["port"][1].setValue(cls.DEFAULT_PORT)
            self.fields["port"][1].blockSignals(False)
        self._show_fields()
        self._refresh_item()

    def _store(self, *a):
        if self.cur is None:
            return
        self.cur["name"] = self.name.text()
        for key, (lab, w) in self.fields.items():
            self.cur[key] = w.value() if key == "port" else w.text()
        self._refresh_item()

    def _refresh_item(self):
        row = self.list.currentRow()
        it = self.list.item(row)
        if it is not None and self.cur is not None:
            cls = self.pm.registry.get(self.cur.get("driver"))
            it.setText(f"{self.cur.get('name') or tr('(senza nome)')}\n   {self.cur.get('host') or '—'} · "
                       f"{cls.NAME if cls else self.cur.get('driver')}")

    def _add(self):
        did = self.driver.currentData() or "pjlink"
        cls = self.pm.registry.get(did)
        self.items.append({"id": uuid.uuid4().hex[:8], "name": tr("Proiettore {0}", len(self.items) + 1),
                           "driver": did, "host": "", "port": cls.DEFAULT_PORT if cls else 4352,
                           "user": "", "password": "", "proj_id": ""})
        self._fill_list(len(self.items) - 1)

    def _dup(self):
        if self.cur is None:
            return
        p = copy.deepcopy(self.cur)
        p["id"] = uuid.uuid4().hex[:8]
        p["name"] = (p.get("name") or "") + tr(" (copia)")
        self.items.append(p)
        self._fill_list(len(self.items) - 1)

    def _del(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            del self.items[row]
            self._fill_list(max(0, row - 1))

    # ------------------------------------------------ prova / plugin
    def _test(self):
        if self.cur is None:
            return
        self.result.setPlainText(tr("Connessione in corso…"))
        self.test_btn.setEnabled(False)
        self.pm.test(dict(self.cur), lambda ok, txt: self.relay.result.emit(ok, txt))

    def _test_done(self, ok, txt):
        self.test_btn.setEnabled(True)
        self.result.setPlainText((tr("✔ Collegato\n") if ok else "✘ ") + txt)

    def _reload(self):
        self.pm.reload_plugins()
        self._fill_drivers()
        self._fill_list(self.list.currentRow())

    def _open_dir(self):
        os.makedirs(USER_PLUGIN_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(USER_PLUGIN_DIR))

    def result_projectors(self):
        return self.items
