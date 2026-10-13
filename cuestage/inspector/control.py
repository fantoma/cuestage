"""Schede dell'ispettore per cue di controllo: comandi, MIDI out, gruppi, OSC, attesa (parte di Inspector)."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox,
    QWidget,
)

from ..model import (
    CONTROL_ACTIONS, CONTROL_LABELS, )


class ControlTabsMixin:
    def _tab_control(self):
        w = QWidget()
        f = QFormLayout(w)
        f.addRow("Azione", self._combo("action", CONTROL_ACTIONS, CONTROL_LABELS))
        f.addRow("Cue target", self._target_combo())
        f.addRow("Fade sullo stop", self._dspin("stop_fade", 0, 600, 0.1, 2))
        return w

    # ------------------------------------------------------------------ MIDI out
    def _tab_midiout(self):
        from PySide6.QtWidgets import QPlainTextEdit as _PTE
        from ..remote import MIDI_OUT_HELP, midi_out, parse_midi_script
        cue = self.cue
        w = QWidget()
        f = QFormLayout(w)
        pc = QComboBox()
        pc.setEditable(True)
        default = self.show_fn().settings.get("midi_out_port", "")
        pc.addItem(f"(predefinita: {default or 'non impostata'})", "")
        for n in midi_out.list_ports():
            pc.addItem(n, n)
        j = pc.findData(cue.mo_port)
        if j < 0 and cue.mo_port:
            pc.addItem(cue.mo_port, cue.mo_port)
            j = pc.count() - 1
        pc.setCurrentIndex(max(0, j))
        pc.currentIndexChanged.connect(lambda k: (setattr(cue, "mo_port", pc.itemData(k) or ""), self._emit()))
        f.addRow("Uscita MIDI", pc)
        ed = _PTE(cue.mo_msgs)
        ed.setPlaceholderText("es.\nnote 1 C4 100\nwait 0.5\noff 1 C4\ncc 1 7 100\nmsc all go 12")
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        ed.setFont(mono)
        ed.setMinimumHeight(140)
        f.addRow("Messaggi", ed)
        res = QLabel()
        res.setWordWrap(True)
        f.addRow(res)

        def check():
            cue.mo_msgs = ed.toPlainText()
            self.show_fn().dirty = True
            try:
                items = parse_midi_script(cue.mo_msgs)
                n = sum(1 for _w, m in items if m is not None)
                tot = sum(wt for wt, _m in items)
                res.setText(f"✔ {n} messaggi" + (f" in {tot:.2f} s" if tot else ""))
                res.setStyleSheet("color:#6c6;")
            except ValueError as err:
                res.setText(f"✘ {err}")
                res.setStyleSheet("color:#e66;")
        ed.textChanged.connect(check)
        check()
        b = QPushButton("Invia ora")

        def send():
            try:
                n = midi_out.send_script(cue.mo_port or self.show_fn().settings.get("midi_out_port", ""), cue.mo_msgs)
                res.setText(f"✔ Inviati {n} messaggi")
                res.setStyleSheet("color:#6c6;")
            except Exception as err:
                res.setText(f"✘ {err}")
                res.setStyleSheet("color:#e66;")
        b.clicked.connect(send)
        f.addRow(b)
        h = QLabel(MIDI_OUT_HELP)
        h.setStyleSheet("color:#999; font-family:monospace; font-size:11px;")
        h.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f.addRow(h)
        return w

    def _tab_group(self):
        from ..groups import GROUP_MODES, block_end
        cue = self.cue
        w = QWidget()
        f = QFormLayout(w)
        mc = QComboBox()
        for k, text in GROUP_MODES.items():
            mc.addItem(text, k)
        mc.setCurrentIndex(max(0, mc.findData(cue.group_mode)))

        def mode_changed(j):
            cue.group_mode = mc.itemData(j)
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        mc.currentIndexChanged.connect(mode_changed)
        f.addRow("Tipo", mc)
        m = cue.group_mode
        if m == "rloop":
            ac = QComboBox()
            ac.addItem("A tempo (attesa + crossfade); il GO successivo esce", "time")
            ac.addItem("Col GO: ogni GO passa a un'altra cue a caso; si esce con ESC o con la cue «Esci»", "go")
            ac.setCurrentIndex(max(0, ac.findData(cue.group_advance)))

            def adv_changed(j):
                cue.group_advance = ac.itemData(j)
                self._emit()
                QTimer.singleShot(0, self.rebuild)
            ac.currentIndexChanged.connect(adv_changed)
            f.addRow("Avanzamento", ac)
        if m == "loop" or (m == "rloop" and cue.group_advance != "go"):
            hs = self._dspin("group_hold", 0, 36000, 0.5, 1)
            hs.setSpecialValueText("durata di ogni cue")
            f.addRow("Attesa su ogni cue", hs)
        if m in ("loop", "rloop"):
            f.addRow("Crossfade", self._dspin("group_xfade", 0, 600, 0.1, 1))
            ec = QComboBox()
            ec.addItem("Sfuma la cue corrente (con il tempo di crossfade)", "fade")
            ec.addItem("Lascia finire la cue corrente", "finish")
            ec.setCurrentIndex(max(0, ec.findData(cue.group_exit)))
            ec.currentIndexChanged.connect(lambda j: (setattr(cue, "group_exit", ec.itemData(j)), self._emit()))
            f.addRow("All'uscita", ec)
        sh = self.show_fn()
        lst, i = sh.list_of(cue.id)
        n = (block_end(lst.cues, i) - i - 1) if lst is not None else 0
        info = QLabel(f"Cue nel gruppo: {n}. Per aggiungerne: selezionale e «Raggruppa», trascinale dentro il "
                      "gruppo, oppure inseriscile con il gruppo (o una sua cue) selezionato.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#999;")
        f.addRow(info)
        help_ = {"sequence": "GO sul gruppo avvia la prima cue; i GO successivi le altre (rispettando «con la "
                             "precedente» e «dopo la precedente»). Dopo l'ultima si esce dal gruppo.",
                 "random": "GO avvia una cue a caso (mai la stessa due volte di seguito). Il GO successivo esce.",
                 "loop": "Le cue si susseguono da sole, all'infinito, con il crossfade indicato. Il GO successivo "
                         "esce dal gruppo; STOP lo ferma.",
                 "rloop": "Come il Loop ma in ordine casuale. «Col GO»: il GO resta al gruppo e passa a un'altra cue; "
                          "si esce con ESC o con la cue «Esci dal gruppo» (creata in coda: si può richiamare da "
                          "altri PANEL, via MIDI o OSC).",
                 "all": "GO avvia tutte le cue insieme; il pre-wait di ognuna è rispettato. Il GO successivo esce."}
        h = QLabel(help_.get(m, ""))
        h.setWordWrap(True)
        f.addRow(h)
        return w

    def _tab_groupexit(self):
        cue = self.cue
        w = QWidget()
        f = QFormLayout(w)
        gc = QComboBox()
        gc.addItem("— scegli —", "")
        sh = self.show_fn()
        for lst in sh.lists:
            for c in lst.cues:
                if c.type == "group":
                    where = "" if lst.is_main else f"[{lst.name}] "
                    gc.addItem(f"{where}{c.number or '·'} – {c.name or 'Gruppo'}", c.number or c.id)
        tg = sh.resolve_target(cue.target) if cue.target else None
        if tg is not None:
            gc.setCurrentIndex(max(0, gc.findData(tg.number or tg.id)))
        gc.currentIndexChanged.connect(lambda j: (setattr(cue, "target", gc.itemData(j) or ""), self._emit()))
        f.addRow("Gruppo", gc)
        lab = QLabel("Chiude il gruppo: niente più avanzamenti, la cue corrente sfuma (o finisce, secondo il "
                     "gruppo) e il playhead esce dal gruppo. Si può richiamare da un'altra lista (cue Comando › "
                     "Avvia), con il suo trigger MIDI/OSC (scheda «Base») o con /cue/<numero>/start.")
        lab.setWordWrap(True)
        lab.setStyleSheet("color:#999;")
        f.addRow(lab)
        return w

    def _tab_osc(self):
        from ..osc_profiles import command, param_values, profiles, render
        cue = self.cue
        w = QWidget()
        f = QFormLayout(w)
        settings = self.show_fn().settings if self.show_fn else {}
        devs = settings.get("osc_devices") or []
        dc = QComboBox()
        dc.addItem("— manuale (IP, indirizzo e argomenti scritti a mano) —", "")
        for d in devs:
            p = profiles().get(d.get("profile")) or {}
            dc.addItem(f"{d.get('name')}  ({p.get('name', '?')} · {d.get('host')})", d.get("id"))
        j = dc.findData(cue.osc_device)
        if j < 0 and cue.osc_device:
            dc.addItem(f"{cue.osc_device} (non configurato)", cue.osc_device)
            j = dc.count() - 1
        dc.setCurrentIndex(max(0, j))

        def dev_changed(i):
            cue.osc_device = dc.itemData(i) or ""
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        dc.currentIndexChanged.connect(dev_changed)
        f.addRow("Dispositivo", dc)
        dev = next((d for d in devs if d.get("id") == cue.osc_device), None)
        if cue.osc_device and dev is not None:
            prof = profiles().get(dev.get("profile")) or {}
            cc = QComboBox()
            for cmd in prof.get("commands") or []:
                cc.addItem(cmd["label"], cmd["id"])
            if cc.findData(cue.osc_cmd) < 0 and cc.count():
                cue.osc_cmd = cc.itemData(0)
                cue.osc_params = {}
            cc.setCurrentIndex(max(0, cc.findData(cue.osc_cmd)))

            def cmd_changed(i):
                cue.osc_cmd = cc.itemData(i)
                old = dict(cue.osc_params or {})
                _p, newc = command(dev.get("profile"), cue.osc_cmd)
                ids = {prm["id"] for prm in (newc or {}).get("params") or []}
                cue.osc_params = {k: v for k, v in old.items() if k in ids}   # tiene es. il n. di playback
                self._emit()
                QTimer.singleShot(0, self.rebuild)
            cc.currentIndexChanged.connect(cmd_changed)
            f.addRow("Comando", cc)
            _p, cmd = command(dev.get("profile"), cue.osc_cmd)
            prev = QLabel()
            prev.setStyleSheet("color:#9a9aa5;font-family:monospace;")

            def refresh_preview():
                try:
                    a, args = render(dev.get("profile"), cue.osc_cmd, cue.osc_params or {})
                    prev.setText(f"{dev.get('host')}:{dev.get('port')}  {a}  {args}")
                except (KeyError, ValueError) as e:
                    prev.setText(f"✘ {e}")
            if cmd is not None:
                if not isinstance(cue.osc_params, dict):
                    cue.osc_params = {}
                cur = param_values(cmd, cue.osc_params)
                for prm in cmd.get("params") or []:
                    pid, typ = prm["id"], prm.get("type")

                    def store(val, pid=pid):
                        cue.osc_params = dict(cue.osc_params or {}, **{pid: val})
                        refresh_preview()
                        self._emit()
                    if typ == "int":
                        ed = QSpinBox()
                        ed.setRange(int(prm.get("min", 0)), int(prm.get("max", 9999)))
                        ed.setValue(int(float(cur[pid] or 0)))
                        ed.valueChanged.connect(store)
                    elif typ == "float":
                        ed = QDoubleSpinBox()
                        ed.setDecimals(int(prm.get("decimals", 2)))
                        ed.setRange(float(prm.get("min", 0)), float(prm.get("max", 9999)))
                        ed.setValue(float(cur[pid] or 0))
                        ed.valueChanged.connect(store)
                    elif typ == "enum":
                        ed = QComboBox()
                        for val, lab in prm.get("items") or []:
                            ed.addItem(lab, str(val))
                        ed.setCurrentIndex(max(0, ed.findData(str(cur[pid]))))
                        ed.currentIndexChanged.connect(lambda i, ed=ed, store=store: store(ed.itemData(i)))
                    else:
                        ed = QLineEdit(str(cur[pid]))
                        ed.textEdited.connect(store)
                    f.addRow(prm["label"], ed)
                if cmd.get("help"):
                    hl = QLabel(cmd["help"])
                    hl.setWordWrap(True)
                    hl.setStyleSheet("color:#e0b040;")
                    f.addRow(hl)
            refresh_preview()
            f.addRow("Messaggio", prev)
            if prof.get("notes"):
                n = QLabel(prof["notes"])
                n.setWordWrap(True)
                n.setStyleSheet("color:#888; font-size:10px;")
                f.addRow(n)
        else:
            if not devs:
                tip = QLabel("Per console e software con profilo (es. ChamSys MagicQ): Impostazioni › OSC › "
                             "Dispositivi OSC › Aggiungi da profilo…")
                tip.setWordWrap(True)
                tip.setStyleSheet("color:#888;")
                f.addRow(tip)
            f.addRow("Host / IP", self._line("osc_host"))
            f.addRow("Porta", self._ispin("osc_port", 1, 65535))
            f.addRow("Indirizzo", self._line("osc_address", "/esempio/comando"))
            f.addRow("Argomenti", self._line("osc_args", 'es.  1 0.5 "testo" true'))
        b = QPushButton("Invia ora")
        b.clicked.connect(lambda: self.oscSend.emit(self.cue.id))
        f.addRow(b)
        return w

    def _tab_wait(self):
        w = QWidget()
        f = QFormLayout(w)
        f.addRow("Durata", self._dspin("duration", 0, 360000, 0.5, 2))
        return w
