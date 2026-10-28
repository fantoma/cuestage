"""Mixer: dialogo di configurazione con ricerca in rete, e pannello a scomparsa dei mute."""
from .i18n import tr
import copy
import os
import uuid

import re

from PySide6.QtCore import QObject, QPoint, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget)

from .mixers import KIND_LABELS, USER_MIXER_DIR

FIELD_LABELS = {"host": tr("Indirizzo IP"), "port": tr("Porta"), "midi_ch": tr("Canale MIDI del mixer"), "model": tr("Modello"),
                "fader_law": tr("Legge fader NRPN"), "midi_out": tr("Uscita MIDI → mixer"), "midi_in": tr("Ingresso MIDI ← mixer"),
                "device": tr("N. dispositivo MIDI"), "card": tr("Scheda audio (ALSA)")}


class _Relay(QObject):
    result = Signal(bool, str)
    found = Signal(object, object)
    mutes = Signal(str, str, bool, object)     # id mixer, sezione, ok, lista|testo


def _midi_names(output):
    try:
        import mido
        return mido.get_output_names() if output else mido.get_input_names()
    except Exception:
        return []


class MixersDialog(QDialog):
    def __init__(self, mixers, manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Mixer"))
        self.mm = manager
        self.items = copy.deepcopy(mixers or [])
        self.cur = None
        self.relay = _Relay()
        self.relay.result.connect(self._test_done)
        self.relay.found.connect(self._found)
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
        for key in ("host", "port", "midi_ch", "model", "fader_law", "midi_out", "midi_in", "device", "card"):
            if key in ("port", "midi_ch", "device"):
                w = QSpinBox()
                w.setRange({"port": 1, "midi_ch": 1, "device": 0}[key], {"port": 65535, "midi_ch": 16, "device": 15}[key])
                w.valueChanged.connect(self._store)
            elif key in ("model", "fader_law", "midi_out", "midi_in", "card"):
                w = QComboBox()
                if key in ("midi_out", "midi_in", "card"):
                    w.setEditable(True)
                w.currentIndexChanged.connect(self._store)
                if w.isEditable():
                    w.editTextChanged.connect(self._store)
            else:
                w = QLineEdit()
                w.setPlaceholderText(tr("es. 192.168.1.10"))
                w.textEdited.connect(self._store)
            lab = QLabel(FIELD_LABELS[key])
            f.addRow(lab, w)
            self.fields[key] = (lab, w)
        self.fields["fader_law"][1].addItem(tr("Lineare (consigliata, esatta)"), "linear")
        self.fields["fader_law"][1].addItem(tr("Audio (approssimata)"), "audio")
        right.addWidget(self.form_w)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color:#999;")
        self.notes.setMinimumHeight(self.fontMetrics().height() * 3)
        right.addWidget(self.notes)
        tb = QHBoxLayout()
        self.test_btn = QPushButton(tr("Prova connessione"))
        self.test_btn.clicked.connect(self._test)
        tb.addWidget(self.test_btn)
        tb.addStretch(1)
        right.addLayout(tb)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(70)
        self.result.setPlaceholderText(tr("Risultato della prova…"))
        right.addWidget(self.result)
        right.addStretch(1)
        h.addLayout(right, 2)
        v.addLayout(h, 1)

        # ricerca in rete
        gs = QGroupBox(tr("Ricerca in rete"))
        gl = QVBoxLayout(gs)
        row = QHBoxLayout()
        self.scan_btn = QPushButton(tr("🔍 Cerca mixer in rete"))
        self.scan_btn.setToolTip(tr("X32 / M32 (OSC 10023), WING (2222), Allen & Heath Qu e SQ (TCP 51325)"))
        self.scan_btn.clicked.connect(self._scan)
        row.addWidget(self.scan_btn)
        self.scan_lab = QLabel(tr("I mixer Yamaha via MIDI non si trovano in rete: aggiungili a mano."))
        self.scan_lab.setStyleSheet("color:#888;")
        self.scan_lab.setWordWrap(True)
        row.addWidget(self.scan_lab, 1)
        gl.addLayout(row)
        self.found_list = QListWidget()
        self.found_list.setMinimumHeight(70)
        self.found_list.setMaximumHeight(130)
        self.found_list.itemDoubleClicked.connect(lambda _it: self._add_found())
        gl.addWidget(self.found_list)
        ab = QPushButton(tr("Aggiungi il mixer trovato selezionato"))
        ab.clicked.connect(self._add_found)
        gl.addWidget(ab)
        v.addWidget(gs)

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
        self.resize(900, 760)
        self._fill_drivers()
        self._fill_list()

    # ------------------------------------------------ elenco
    def _fill_drivers(self):
        cur = self.driver.currentData()
        self.driver.blockSignals(True)
        self.driver.clear()
        for did, cls in sorted(self.mm.registry.drivers.items(), key=lambda x: x[1].NAME):
            self.driver.addItem(cls.NAME, did)
        self.driver.setCurrentIndex(max(0, self.driver.findData(cur)))
        self.driver.blockSignals(False)
        errs = self.mm.registry.errors
        txt = tr("Driver mixer: {0}. Plugin utente in {1}", len(self.mm.registry.drivers), USER_MIXER_DIR)
        if errs:
            txt += "\n⚠ " + "\n⚠ ".join(errs)
        self.plug_lab.setText(txt)
        self.plug_lab.setStyleSheet("color:#e0b040;" if errs else "color:#888;")

    def _label(self, m):
        cls = self.mm.registry.get(m.get("driver"))
        where = m.get("host") or m.get("midi_out") or m.get("card_name") or m.get("card") or "—"
        return f"{m.get('name') or tr('(senza nome)')}\n   {where} · {cls.NAME if cls else m.get('driver')}"

    def _fill_list(self, select=0):
        self.list.blockSignals(True)
        self.list.clear()
        for m in self.items:
            QListWidgetItem(self._label(m), self.list)
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
            self.notes.setText(tr("Aggiungi un mixer con «Aggiungi» oppure con la ricerca in rete."))
            return
        m = self.cur
        self.name.setText(m.get("name", ""))
        self.driver.blockSignals(True)
        i = self.driver.findData(m.get("driver"))
        if i < 0:
            self.driver.addItem(tr("{0} (mancante)", m.get('driver')), m.get("driver"))
            i = self.driver.count() - 1
        self.driver.setCurrentIndex(i)
        self.driver.blockSignals(False)
        self._show_fields()

    def _show_fields(self):
        cls = self.mm.registry.get(self.driver.currentData())
        wanted = set(cls.FIELDS) if cls else {"host", "port"}
        m = self.cur or {}
        for key, (lab, w) in self.fields.items():
            lab.setVisible(key in wanted)
            w.setVisible(key in wanted)
            w.blockSignals(True)
            if key == "model":
                w.clear()
                for code, text in (cls.MODELS if cls else []):
                    w.addItem(text, code)
                w.setCurrentIndex(max(0, w.findData(m.get("model"))))
            elif key in ("midi_out", "midi_in"):
                w.clear()
                w.addItem("", "")
                for n in _midi_names(key == "midi_out"):
                    w.addItem(n, n)
                cur = m.get(key) or ""
                j = w.findData(cur)
                if j < 0 and cur:
                    w.addItem(f"{cur}", cur)
                    j = w.count() - 1
                w.setCurrentIndex(max(0, j))
            elif key == "card":
                from .alsactl import alsa_cards
                w.clear()
                for idx, cid, short, long in alsa_cards():
                    w.addItem(f"{cid} — {short}", cid)
                cur = m.get("card") or ""
                j = w.findData(cur)
                if j < 0 and cur:
                    w.addItem(f"{cur} — {m.get('card_name') or tr('non collegata')}", cur)
                    j = w.count() - 1
                w.setCurrentIndex(max(0, j))
            elif key == "fader_law":
                w.setCurrentIndex(max(0, w.findData(m.get("fader_law") or "linear")))
            elif isinstance(w, QSpinBox):
                default = {"port": cls.DEFAULT_PORT if cls else 1, "midi_ch": 1, "device": 0}[key]
                w.setValue(int(m.get(key) if m.get(key) not in (None, "") else (default or 1 if key == "port" else default)))
            else:
                w.setText(str(m.get(key) or ""))
            w.blockSignals(False)
        self.notes.setText(cls.NOTES if cls else tr("Driver non disponibile: controlla i plugin."))

    def _driver_changed(self, _i):
        if self.cur is None:
            return
        cls = self.mm.registry.get(self.driver.currentData())
        old = self.mm.registry.get(self.cur.get("driver"))
        self.cur["driver"] = self.driver.currentData()
        if cls and (not self.cur.get("port") or (old and int(self.cur.get("port") or 0) == old.DEFAULT_PORT)):
            self.cur["port"] = cls.DEFAULT_PORT
        self._show_fields()
        self._store()

    def _store(self, *_):
        if self.cur is None:
            return
        cls = self.mm.registry.get(self.driver.currentData())
        wanted = set(cls.FIELDS) if cls else set()
        self.cur["name"] = self.name.text()
        for key, (lab, w) in self.fields.items():
            if key not in wanted:
                continue
            if isinstance(w, QSpinBox):
                self.cur[key] = w.value()
            elif key == "card":
                t = w.currentText().strip()
                data = w.currentData() if w.currentIndex() >= 0 and w.itemText(w.currentIndex()) == t else None
                self.cur["card"] = data or t.split(" — ")[0].strip()
                if " — " in t:
                    self.cur["card_name"] = t.split(" — ", 1)[1].strip()
            elif isinstance(w, QComboBox):
                self.cur[key] = (w.currentData() if not w.isEditable() else (w.currentText().strip() or ""))
            else:
                self.cur[key] = w.text().strip()
        it = self.list.item(self.list.currentRow())
        if it is not None:
            it.setText(self._label(self.cur))

    def _new(self, did, **kw):
        cls = self.mm.registry.get(did)
        m = {"id": uuid.uuid4().hex[:8], "name": tr("Mixer {0}", len(self.items) + 1), "driver": did, "host": "",
             "port": cls.DEFAULT_PORT if cls else 0, "midi_ch": 1}
        if cls and cls.MODELS:
            m["model"] = cls.MODELS[0][0]
        m.update({k: v for k, v in kw.items() if v not in (None, "")})
        return m

    def _add(self):
        self.items.append(self._new(self.driver.currentData() or "behringer_x32"))
        self._fill_list(len(self.items) - 1)

    def _dup(self):
        if self.cur is None:
            return
        m = copy.deepcopy(self.cur)
        m["id"] = uuid.uuid4().hex[:8]
        m["name"] = (m.get("name") or "") + tr(" (copia)")
        self.items.append(m)
        self._fill_list(len(self.items) - 1)

    def _del(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            del self.items[row]
            self._fill_list(max(0, row - 1))

    # ------------------------------------------------ ricerca
    def _scan(self):
        self.scan_btn.setEnabled(False)
        self.found_list.clear()
        self.scan_lab.setText(tr("Ricerca in corso… (qualche secondo)"))
        self.scan_lab.setStyleSheet("color:#ccc;")
        self.mm.discover(lambda res, ips: self.relay.found.emit(res, ips))

    def _found(self, res, ips):
        self.scan_btn.setEnabled(True)
        self.found_list.clear()
        good = [r for r in res if "error" not in r]
        for r in good:
            cls = self.mm.registry.get(r.get("driver"))
            known = any(m.get("host") == r.get("host") and m.get("card") == r.get("card")
                        and m.get("driver") == r.get("driver") for m in self.items)
            where = f"{r.get('host')}:{r.get('port')}" if r.get("host") else tr("scheda «{0}»", r.get('card'))
            txt = (f"{r.get('model') or (cls.NAME if cls else r.get('driver'))}  «{r.get('name') or '—'}»  ·  "
                   f"{where}" + (tr("  ·  canale MIDI {0}", r['midi_ch']) if r.get("midi_ch") else "")
                   + (f"  ·  {r['info']}" if r.get("info") else "") + (tr("  ✔ già configurato") if known else ""))
            it = QListWidgetItem(txt, self.found_list)
            it.setData(Qt.UserRole, r)
        errs = [r["error"] for r in res if "error" in r]
        msg = (tr("Trovati {0} mixer / schede", len(good)) if good else tr("Nessun mixer trovato")) + tr(" sulle reti {0}/24.", ', '.join(ips))
        if errs:
            msg += " ⚠ " + "; ".join(errs)
        if not good:
            msg += tr(" Controlla che computer e mixer siano sulla stessa rete (e il firewall).")
        self.scan_lab.setText(msg)
        self.scan_lab.setStyleSheet("color:#6c6;" if good else "color:#e0b040;")
        if good:
            self.found_list.setCurrentRow(0)

    def _add_found(self):
        it = self.found_list.currentItem()
        if it is None:
            return
        r = it.data(Qt.UserRole)
        name = r.get("name") or r.get("model") or tr("Mixer")
        extra = {k: r[k] for k in ("model", "card", "card_name") if r.get(k)}
        self.items.append(self._new(r.get("driver"), name=name, host=r.get("host") or "", port=r.get("port"),
                                    midi_ch=r.get("midi_ch"), **extra))
        self._fill_list(len(self.items) - 1)

    # ------------------------------------------------ prova / plugin
    def _test(self):
        if self.cur is None:
            return
        self.result.setPlainText(tr("Connessione in corso…"))
        self.test_btn.setEnabled(False)
        self.mm.test(dict(self.cur), lambda ok, txt: self.relay.result.emit(ok, txt))

    def _test_done(self, ok, txt):
        self.test_btn.setEnabled(True)
        self.result.setPlainText((tr("✔ Collegato\n") if ok else "✘ ") + txt)

    def _reload(self):
        self.mm.reload_plugins()
        self._fill_drivers()
        self._fill_list(self.list.currentRow())

    def _open_dir(self):
        os.makedirs(USER_MIXER_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(USER_MIXER_DIR))

    def result_mixers(self):
        return self.items


# ------------------------------------------------------------------ pannello mute

class ControlsBox(QWidget):
    """Controlli generici di una scheda audio (volumi, interruttori, menu), raggruppati e richiudibili."""

    @staticmethod
    def collapsed(g):
        return g.startswith("Mix") or g.startswith("Routing") or g == "Altro"

    def __init__(self, mm, mid, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QToolButton
        from .alsactl import group_rank
        self.mm, self.mid = mm, mid
        self.pending = {}
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._flush)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 6, 0, 0)
        head = QHBoxLayout()
        t = QLabel(tr("<b>Controlli della scheda</b>"))
        head.addWidget(t)
        head.addStretch(1)
        rd = QPushButton(tr("⟲ Leggi"))
        rd.setToolTip(tr("Rilegge tutti i controlli dalla scheda"))
        rd.setFocusPolicy(Qt.NoFocus)
        rd.clicked.connect(lambda: self.mm.read_controls(self.mid))
        head.addWidget(rd)
        v.addLayout(head)
        ctls = self.mm.controls.get(mid) or []
        if not ctls:
            lab = QLabel(tr("… lettura dei controlli dalla scheda (se non compare nulla: scheda scollegata o "
                         "«amixer» mancante)"))
            lab.setStyleSheet("color:#999;")
            lab.setWordWrap(True)
            v.addWidget(lab)
            return
        self.by_name = {c["name"]: c for c in ctls}
        hw = [c for c in ctls if re.match(r"Line Out \d+ Volume Control", c["name"]) and c.get("value") == 1
              and c["type"] == "enum" and (c.get("items") or [""])[1:2] == ["HW"]]
        if hw:
            note = QLabel(tr("⚠ {0} uscite hanno il volume sulla manopola della scheda (HW): volume e mute di quelle uscite non si possono comandare da CueStage.", len(hw)))
            note.setWordWrap(True)
            note.setStyleSheet("color:#e0b040;")
            v.addWidget(note)
            sw = QPushButton(tr("Volume e mute delle uscite da CueStage (SW)…"))
            sw.setFocusPolicy(Qt.NoFocus)
            sw.clicked.connect(lambda: self._to_sw(hw))
            v.addWidget(sw, 0, Qt.AlignLeft)
        groups = {}
        for c in ctls:
            groups.setdefault(c.get("group") or tr("Altro"), []).append(c)
        for gname in sorted(groups, key=group_rank):
            items = groups[gname]
            tb = QToolButton()
            tb.setCheckable(True)
            tb.setChecked(not self.collapsed(gname))
            tb.setStyleSheet("QToolButton{border:none;font-weight:bold;}")
            tb.setFocusPolicy(Qt.NoFocus)
            body = QWidget()
            body.setVisible(False)
            built = []

            def toggled(on, tb=tb, body=body, gname=gname, items=items, built=built):
                if on and not built:              # i gruppi grandi (mix, routing) si costruiscono all'apertura
                    self._fill(body, items)
                    built.append(True)
                body.setVisible(on)
                tb.setText(("▾ " if on else "▸ ") + f"{gname} ({len(items)})")
            tb.toggled.connect(toggled)
            toggled(tb.isChecked())
            v.addWidget(tb)
            v.addWidget(body)

    def _label(self, c):
        from .alsactl import pretty_name
        name = pretty_name(c["name"])
        m = re.match(r"Mix ([A-Z]) Input (\d+)", c["name"])
        if m:                                       # sorgente dell'ingresso del mixer (dal routing)
            src = self.by_name.get(f"Mixer Input {m.group(2)} Capture Enum")
            if src and src.get("items") and src.get("value") is not None:
                return tr("Mix {0} ← in {1}: {2}", m.group(1), m.group(2), src['items'][int(src['value'])])
        return name

    def _fill(self, body, items):
        from PySide6.QtWidgets import QGridLayout
        g = QGridLayout(body)
        g.setContentsMargins(12, 0, 0, 4)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(2)
        for row, c in enumerate(items):
            lab = QLabel(self._label(c))
            lab.setToolTip(tr("Controllo ALSA: {0}", c['key']))
            lab.setMinimumWidth(250)
            g.addWidget(lab, row, 0)
            ed = self._editor(c)
            if c["type"] == "int":
                g.addWidget(ed, row, 1)
            else:
                g.addWidget(ed, row, 1, Qt.AlignLeft)
        g.setColumnStretch(1, 1)

    def _to_sw(self, hw):
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, tr("Volume delle uscite"), tr("Mettere le uscite su controllo software (SW)?\n\n"
                                "Volume e mute di quelle uscite si comandano da CueStage (pannello e cue mute/fader); "
                                "la manopola Monitor della scheda non agisce più su di esse finché non le rimetti "
                                "su HW.")) != QMessageBox.Yes:
            return
        for c in hw:
            self.mm.set_control(self.mid, c["key"], 0, report=False)
        self.mm.read_controls(self.mid)

    def _editor(self, c):
        from PySide6.QtWidgets import QSlider
        key, typ = c["key"], c["type"]
        if typ == "bool":
            b = QPushButton()
            b.setCheckable(True)
            b.setChecked(bool(c.get("value")))
            b.setFixedWidth(90)
            b.setFocusPolicy(Qt.NoFocus)

            def paint(on, b=b):
                b.setText(tr("acceso") if on else tr("spento"))
                b.setStyleSheet("QPushButton{background:#2e7d46;color:white;font-weight:bold;border-radius:4px;}"
                                if on else "QPushButton{background:#3a3c46;color:#bbb;border-radius:4px;}")
            paint(b.isChecked())

            def toggled(on, key=key, b=b, name=c["name"]):
                if on and "phantom" in name.lower():
                    from PySide6.QtWidgets import QMessageBox
                    if QMessageBox.question(self, tr("Phantom 48V"), tr("Accendere l'alimentazione phantom 48V?\n({0})\n\nAttenzione ai microfoni a nastro e alle sorgenti di linea.", name)) \
                            != QMessageBox.Yes:
                        b.blockSignals(True)
                        b.setChecked(False)
                        b.blockSignals(False)
                        paint(False)
                        return
                paint(on)
                self.mm.set_control(self.mid, key, 1 if on else 0)
            b.toggled.connect(toggled)
            w = b
        elif typ == "enum":
            w = QComboBox()
            w.setMinimumWidth(170)
            for it in c.get("items") or []:
                w.addItem(it)
            w.setCurrentIndex(int(c.get("value") or 0))
            w.currentIndexChanged.connect(lambda j, key=key: self.mm.set_control(self.mid, key, j))
        else:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(int(c["min"]), int(c["max"]))
            sl.setValue(int(c.get("value") if c.get("value") is not None else c["min"]))
            sl.setFocusPolicy(Qt.NoFocus)
            val = QLabel()
            val.setMinimumWidth(70)

            def show(x, c=c, val=val):
                val.setText(_ctl_text(c, x))
            show(sl.value())
            sl.valueChanged.connect(lambda x, key=key, show=show: (show(x), self._queue(key, x)))
            h.addWidget(sl, 1)
            h.addWidget(val)
        if not c.get("writable", True):
            w.setEnabled(False)
        return w

    def _queue(self, key, value):
        self.pending[key] = value              # durante il trascinamento parte solo l'ultimo valore
        if not self.timer.isActive():
            self.timer.start()

    def _flush(self):
        if not self.pending:
            self.timer.stop()
            return
        items, self.pending = self.pending, {}
        for key, value in items.items():
            self.mm.set_control(self.mid, key, value, report=False)


def _ctl_text(c, raw):
    """Testo del valore di un controllo (dict da list_controls)."""
    if raw is None:
        return "?"
    if c["type"] == "bool":
        return tr("acceso") if raw else tr("spento")
    if c["type"] == "enum":
        items = c.get("items") or []
        return items[raw] if 0 <= raw < len(items) else str(raw)
    db = c.get("db")
    if db:
        if db[0] == "minmax":
            lo, hi = db[1], db[2]
            d = lo + (hi - lo) * (raw - c["min"]) / max(1, c["max"] - c["min"])
        else:
            d = db[0] + (raw - c["min"]) * db[1]
        return f"{d:+.1f} dB"
    return f"{raw}"


class MixerPanel(QFrame):
    """Finestrella a comparsa: i mute di ogni sezione del mixer, come pulsanti (rosso = muto)."""
    visibilityChanged = Signal(bool)

    def __init__(self, manager, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("mixPanel")
        self.setAutoFillBackground(True)
        self.setStyleSheet("#mixPanel{border:1px solid palette(mid);}")
        self.mm = manager
        self.btns = {}           # (id, sezione, n) -> pulsante
        self.states = {}         # (id, sezione, n) -> muto (ultimo stato noto)
        self.mm.namesChanged.connect(self.names_changed)
        self.mm.controlsChanged.connect(self.names_changed)
        self.relay = _Relay()
        self.relay.mutes.connect(self._mutes_read)
        self.mm.finished.connect(self._done)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 8)
        head = QHBoxLayout()
        t = QLabel(tr("Mixer · mute"))
        t.setStyleSheet("font-weight:bold;font-size:14px;")
        head.addWidget(t)
        head.addStretch(1)
        rb = QPushButton(tr("Leggi tutto"))
        rb.setFocusPolicy(Qt.NoFocus)
        rb.setToolTip(tr("Legge dal mixer lo stato di tutti i mute"))
        rb.clicked.connect(self.read_all)
        head.addWidget(rb)
        close = QPushButton("✕")
        close.setFixedWidth(28)
        close.setFocusPolicy(Qt.NoFocus)
        close.clicked.connect(self.hide)
        head.addWidget(close)
        v.addLayout(head)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        self.result = QLabel("—")
        self.result.setWordWrap(True)
        self.result.setStyleSheet("color:#bbb;")
        v.addWidget(self.result)

    def rebuild(self):
        self.btns = {}
        cur = self.tabs.currentIndex()
        self.tabs.clear()
        for conf in self.mm.mixers:
            cls = self.mm.cls_for(conf)
            page = QWidget()
            pv = QVBoxLayout(page)
            if cls is None:
                pv.addWidget(QLabel(tr("Driver «{0}» non disponibile", conf.get('driver'))))
            else:
                for kind, count, caps in cls.sections(conf):
                    if "mute" not in caps:
                        continue
                    box = QWidget()
                    bl = QVBoxLayout(box)
                    bl.setContentsMargins(0, 2, 0, 4)
                    bl.setSpacing(2)
                    hr = QHBoxLayout()
                    tl = QLabel(KIND_LABELS.get(kind, kind))
                    tl.setStyleSheet("font-weight:bold;")
                    hr.addWidget(tl)
                    hr.addStretch(1)
                    rd = QPushButton(tr("Leggi"))
                    rd.setFocusPolicy(Qt.NoFocus)
                    rd.setToolTip(tr("Legge dal mixer lo stato di questi mute"))
                    rd.clicked.connect(lambda _=False, mid=conf.get("id"), k=kind: self.read(mid, k))
                    hr.addWidget(rd)
                    bl.addLayout(hr)
                    g = QGridLayout()
                    g.setSpacing(3)
                    mid = conf.get("id")
                    named = any(self.mm.channel_name(mid, kind, n) for n in range(1, count + 1))
                    cols = 8 if named else 16
                    for n in range(1, count + 1):
                        nm = self.mm.channel_name(mid, kind, n)
                        if count > 1:
                            b = QPushButton(f"{n}\n{nm[:10]}" if named else str(n))
                            b.setFixedSize(74 if named else 36, 40 if named else 30)
                        else:
                            b = QPushButton(nm or KIND_LABELS.get(kind, kind))
                            b.setFixedSize(120, 30)
                        b.setCheckable(True)
                        b.setFocusPolicy(Qt.NoFocus)
                        b.setToolTip(f"{KIND_LABELS.get(kind, kind)} {n}" + (f" · {nm}" if nm else "")
                                     + tr(": clic = mute / riattiva"))
                        b.setStyleSheet(self._style(None))
                        b.clicked.connect(lambda on, mid=conf.get("id"), k=kind, n=n: self._toggle(mid, k, n, on))
                        g.addWidget(b, (n - 1) // cols, (n - 1) % cols)
                        self.btns[(conf.get("id"), kind, n)] = b
                    g.setColumnStretch(cols, 1)
                    bl.addLayout(g)
                    pv.addWidget(box)
                if cls.CONTROLS:
                    pv.addWidget(ControlsBox(self.mm, conf.get("id")))
            pv.addStretch(1)
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QFrame.NoFrame)
            sa.setWidget(page)
            self.tabs.addTab(sa, conf.get("name") or conf.get("host") or tr("Mixer"))
        if 0 <= cur < self.tabs.count():
            self.tabs.setCurrentIndex(cur)
        for key, muted in list(self.states.items()):       # dopo un rebuild (es. nomi letti)
            self._set(key, muted)

    def names_changed(self, mid):
        if any(c.get("id") == mid for c in self.mm.mixers):
            self.rebuild()

    @staticmethod
    def _style(muted):
        if muted is None:
            return "QPushButton{background:#3a3c46;color:#aaa;border-radius:4px;}"
        if muted:
            return "QPushButton{background:#b52a2a;color:white;font-weight:bold;border-radius:4px;}"
        return "QPushButton{background:#2f5d3a;color:white;border-radius:4px;}"

    def _set(self, key, muted):
        self.states[key] = muted
        b = self.btns.get(key)
        if b is None:
            return
        b.blockSignals(True)
        b.setChecked(bool(muted))
        b.blockSignals(False)
        b.setStyleSheet(self._style(muted))

    def _toggle(self, mid, kind, n, on):
        self._set((mid, kind, n), on)
        self.mm.mute(mid, kind, [n], on)

    def read(self, mid, kind):
        self.result.setText(tr("… lettura {0}", KIND_LABELS.get(kind, kind).lower()))
        self.mm.read_mutes(mid, kind, lambda ok, r, mid=mid, kind=kind: self.relay.mutes.emit(mid, kind, ok, r))

    def read_all(self):
        for conf in self.mm.mixers:
            self.mm.read_names(conf.get("id"))
            self.mm.read_controls(conf.get("id"))
            cls = self.mm.cls_for(conf)
            if cls is None:
                continue
            for kind, _n, caps in cls.sections(conf):
                if "mute" in caps:
                    self.read(conf.get("id"), kind)

    def _mutes_read(self, mid, kind, ok, res):
        if not ok:
            self.result.setText(tr("✘ {0} – lettura {1}: {2}", self.mm.name(mid), KIND_LABELS.get(kind, kind).lower(), res))
            self.result.setStyleSheet("color:#e66;")
            return
        for i, m in enumerate(res or [], start=1):
            self._set((mid, kind, i), m)
        self.result.setText(tr("✔ {0} – {1} letti", self.mm.name(mid), KIND_LABELS.get(kind, kind)))
        self.result.setStyleSheet("color:#6c6;")

    def _done(self, name, action, ok, msg):
        if not action:
            return
        self.result.setText(f"{'✔' if ok else '✘'} {name} – {action}: {msg}")
        self.result.setStyleSheet("color:#6c6;" if ok else "color:#e66;")

    def popup_under(self, anchor):
        self.resize(700, min(720, max(360, 120 + 70 * max([len(c.sections(m)) for m in self.mm.mixers
                                                            for c in [self.mm.cls_for(m)] if c] or [3]))))
        g = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 4))
        x = g.x() - self.width()
        scr = anchor.screen().availableGeometry() if anchor.screen() else None
        if scr is not None:
            x = max(scr.left(), min(x, scr.right() - self.width()))
        self.move(x, g.y())
        self.show()
        self.raise_()
        for conf in self.mm.mixers:
            self.mm.ensure_names(conf.get("id"))
            self.mm.ensure_controls(conf.get("id"))

    def showEvent(self, e):
        super().showEvent(e)
        self.visibilityChanged.emit(True)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.visibilityChanged.emit(False)
