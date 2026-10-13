"""Dispositivi OSC da profilo: finestra di aggiunta/modifica con indirizzo IP o ricerca in rete."""
import threading
import uuid

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout)

from .osc_profiles import discover, profiles


class _Relay(QObject):
    found = Signal(object)


class OscDeviceDialog(QDialog):
    def __init__(self, device=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dispositivo OSC")
        self.dev = dict(device or {})
        v = QVBoxLayout(self)
        f = QFormLayout()
        self.prof = QComboBox()
        for pid, p in sorted(profiles(reload=True).items(), key=lambda kv: kv[1]["name"]):
            self.prof.addItem(p["name"], pid)
        self.prof.setCurrentIndex(max(0, self.prof.findData(self.dev.get("profile"))))
        self.prof.currentIndexChanged.connect(self._profile_changed)
        f.addRow("Profilo", self.prof)
        self.name = QLineEdit(self.dev.get("name") or "")
        f.addRow("Nome", self.name)
        hr = QHBoxLayout()
        self.host = QLineEdit(self.dev.get("host") or "")
        self.host.setPlaceholderText("indirizzo IP del dispositivo, es. 192.168.1.50")
        hr.addWidget(self.host, 1)
        self.search_btn = QPushButton("🔍 Cerca in rete")
        self.search_btn.clicked.connect(self.search)
        hr.addWidget(self.search_btn)
        f.addRow("Indirizzo IP", hr)
        self.found = QListWidget()
        self.found.setMaximumHeight(110)
        self.found.setVisible(False)
        self.found.currentItemChanged.connect(self._picked)
        f.addRow("", self.found)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        f.addRow("Porta OSC", self.port)
        v.addLayout(f)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color:#999;")
        v.addWidget(self.notes)
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.relay = _Relay()
        self.relay.found.connect(self._found)
        self._profile_changed(first=True)
        self.resize(600, 420)

    def profile(self):
        return profiles().get(self.prof.currentData()) or {}

    def _profile_changed(self, *_a, first=False):
        p = self.profile()
        if not first or not self.dev.get("port"):
            self.port.setValue(int(p.get("port") or 8000))
        else:
            self.port.setValue(int(self.dev["port"]))
        if not self.name.text() or (not first and self.name.text() in [x["name"] for x in profiles().values()]):
            self.name.setText(p.get("name", ""))
        self.notes.setText(p.get("notes", ""))
        self.search_btn.setEnabled(bool(p.get("discover")))
        self.search_btn.setToolTip("" if p.get("discover") else "Questo profilo non prevede la ricerca: scrivi l'IP")

    def search(self):
        self.search_btn.setEnabled(False)
        self.msg.setText("ricerca in rete…")
        self.msg.setStyleSheet("color:#999;")
        p = self.profile()
        threading.Thread(target=lambda: self.relay.found.emit(discover(p)), daemon=True).start()

    def _found(self, res):
        self.search_btn.setEnabled(True)
        self.found.clear()
        self.found.setVisible(bool(res))
        hits = [r for r in res if r["match"]]
        for r in res:
            it = QListWidgetItem(f"{'✔ ' if r['match'] else '   '}{r['name']}  ·  {r['host']}"
                                 + (f"  ·  {r['long']}" if r.get("long") and r["long"] != r["name"] else ""))
            it.setData(Qt.UserRole, r)
            self.found.addItem(it)
        if hits:
            self.msg.setText(f"Trovati {len(hits)} dispositivi «{self.profile().get('name')}»"
                             + (f" (e {len(res) - len(hits)} altri nodi in rete)" if len(res) > len(hits) else ""))
            self.msg.setStyleSheet("color:#6c6;")
            self.found.setCurrentRow(0)
        elif res:
            self.msg.setText(f"Nessun «{self.profile().get('name')}» riconosciuto; {len(res)} altri nodi in rete: "
                             "sceglilo dall'elenco se è uno di questi")
            self.msg.setStyleSheet("color:#e0b040;")
        else:
            self.msg.setText("Nessun dispositivo trovato: controlla la rete (e, per la MagicQ, che l'uscita Art-Net "
                             "sia attiva) oppure scrivi l'indirizzo IP")
            self.msg.setStyleSheet("color:#e66;")

    def _picked(self, it, _prev=None):
        if it is not None:
            self.host.setText(it.data(Qt.UserRole)["host"])

    def _ok(self):
        if not self.host.text().strip():
            self.msg.setText("Indica l'indirizzo IP (o cercalo in rete).")
            self.msg.setStyleSheet("color:#e66;")
            return
        self.dev.update(id=self.dev.get("id") or uuid.uuid4().hex[:8], profile=self.prof.currentData(),
                        name=self.name.text().strip() or self.profile().get("name", "OSC"),
                        host=self.host.text().strip(), port=self.port.value())
        self.accept()

    def result_device(self):
        return self.dev


def device_label(d):
    p = profiles().get(d.get("profile")) or {}
    return f"{d.get('name')}  ({p.get('name', d.get('profile'))} · {d.get('host')}:{d.get('port')})"
