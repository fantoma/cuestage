"""Schede dell'ispettore per cue di controllo: comandi, MIDI out, gruppi, OSC, attesa (parte di Inspector)."""

from ..i18n import tr
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QSpinBox,
    QWidget,
)

from ..model import (
    CONTROL_ACTIONS, CONTROL_LABELS, )


class ControlTabsMixin:
    def _tab_control(self):
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(tr("Azione"), self._combo("action", CONTROL_ACTIONS, CONTROL_LABELS))
        f.addRow(tr("Cue target"), self._target_combo())
        f.addRow(tr("Fade sullo stop"), self._dspin("stop_fade", 0, 600, 0.1, 2))
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
        pc.addItem(tr("(predefinita: {0})", default or tr('non impostata')), "")
        for n in midi_out.list_ports():
            pc.addItem(n, n)
        j = pc.findData(cue.mo_port)
        if j < 0 and cue.mo_port:
            pc.addItem(cue.mo_port, cue.mo_port)
            j = pc.count() - 1
        pc.setCurrentIndex(max(0, j))
        pc.currentIndexChanged.connect(lambda k: (setattr(cue, "mo_port", pc.itemData(k) or ""), self._emit()))
        f.addRow(tr("Uscita MIDI"), pc)
        ed = _PTE(cue.mo_msgs)
        ed.setPlaceholderText("es.\nnote 1 C4 100\nwait 0.5\noff 1 C4\ncc 1 7 100\nmsc all go 12")
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        ed.setFont(mono)
        ed.setMinimumHeight(140)
        f.addRow(tr("Messaggi"), ed)
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
                res.setText(tr("✔ {0} messaggi", n) + (tr(" in {0:.2f} s", tot) if tot else ""))
                res.setStyleSheet("color:#6c6;")
            except ValueError as err:
                res.setText(f"✘ {err}")
                res.setStyleSheet("color:#e66;")
        ed.textChanged.connect(check)
        check()
        b = QPushButton(tr("Invia ora"))

        def send():
            try:
                n = midi_out.send_script(cue.mo_port or self.show_fn().settings.get("midi_out_port", ""), cue.mo_msgs)
                res.setText(tr("✔ Inviati {0} messaggi", n))
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
            new = mc.itemData(j)
            if new in ("playlist", "rplaylist") and cue.group_mode not in ("playlist", "rplaylist"):
                cue.group_xfade = 0.0          # playlist: di serie una dopo l'altra, senza crossfade
            cue.group_mode = new
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        mc.currentIndexChanged.connect(mode_changed)
        f.addRow(tr("Tipo"), mc)
        m = cue.group_mode
        if m == "rloop":
            ac = QComboBox()
            ac.addItem(tr("A tempo (attesa + crossfade); il GO successivo esce"), "time")
            ac.addItem(tr("Col GO: ogni GO passa a un'altra cue a caso; si esce con ESC o con la cue «Esci»"), "go")
            ac.setCurrentIndex(max(0, ac.findData(cue.group_advance)))

            def adv_changed(j):
                cue.group_advance = ac.itemData(j)
                self._emit()
                QTimer.singleShot(0, self.rebuild)
            ac.currentIndexChanged.connect(adv_changed)
            f.addRow(tr("Avanzamento"), ac)
        from ..engine import tap_active
        if (m == "loop" or (m == "rloop" and cue.group_advance != "go")) and not tap_active(cue):
            hs = self._dspin("group_hold", 0, 36000, 0.5, 1)
            hs.setSpecialValueText(tr("durata di ogni cue"))
            f.addRow(tr("Attesa su ogni cue"), hs)
        if m in ("loop", "rloop", "playlist", "rplaylist"):
            xs = self._dspin("group_xfade", 0, 600, 0.1, 1)
            if m in ("playlist", "rplaylist"):
                xs.setSpecialValueText(tr("nessuno: una dopo l'altra"))
                lp = self._check("group_loop", tr("Ricomincia dopo l'ultima cue (loop della playlist)"))
                lp.setToolTip(tr("Il loop impostato sulle singole cue è ignorato: nella playlist ognuna suona una volta "
                              "per giro.") + (tr(" A ogni giro l'ordine viene rimescolato.") if m == "rplaylist" else ""))
                f.addRow(tr("Loop"), lp)
            f.addRow(tr("Crossfade"), xs)
            ec = QComboBox()
            ec.addItem(tr("Sfuma la cue corrente (con il tempo di crossfade)"), "fade")
            ec.addItem(tr("Lascia finire la cue corrente"), "finish")
            ec.setCurrentIndex(max(0, ec.findData(cue.group_exit)))
            ec.currentIndexChanged.connect(lambda j: (setattr(cue, "group_exit", ec.itemData(j)), self._emit()))
            f.addRow(tr("All'uscita"), ec)
        f.addRow(self._tap_box(cue))
        f.addRow(tr("Ducking"), self._duck_row())
        sh = self.show_fn()
        lst, i = sh.list_of(cue.id)
        n = (block_end(lst.cues, i) - i - 1) if lst is not None else 0
        info = QLabel(tr("Cue nel gruppo: {0}. Per aggiungerne: selezionale e «Raggruppa», trascinale dentro il gruppo, oppure inseriscile con il gruppo (o una sua cue) selezionato.", n))
        info.setWordWrap(True)
        info.setStyleSheet("color:#999;")
        f.addRow(info)
        help_ = {"sequence": tr("GO sul gruppo avvia la prima cue; i GO successivi le altre (rispettando «con la "
                             "precedente» e «dopo la precedente»). Dopo l'ultima si esce dal gruppo."),
                 "random": tr("GO avvia una cue a caso (mai la stessa due volte di seguito). Il GO successivo esce."),
                 "loop": tr("Le cue si susseguono da sole, all'infinito, con il crossfade indicato. Il GO successivo "
                         "esce dal gruppo; STOP lo ferma."),
                 "rloop": tr("Come il Loop ma in ordine casuale. «Col GO»: il GO resta al gruppo e passa a un'altra cue; "
                          "si esce con ESC o con la cue «Esci dal gruppo» (creata in coda: si può richiamare da "
                          "altri PANEL, via MIDI o OSC)."),
                 "all": tr("GO avvia tutte le cue insieme; il pre-wait di ognuna è rispettato. Il GO successivo esce."),
                 "playlist": tr("GO avvia la prima cue; le altre partono da sole, ognuna appena finisce la precedente "
                             "(o in crossfade), nell'ordine della lista. Pre-wait e «con/dopo la precedente» delle "
                             "cue del gruppo non contano. Ogni cue suona una volta (il suo loop è ignorato): dopo "
                             "l'ultima il gruppo finisce, oppure ricomincia se è attivo il loop della playlist. "
                             "Il GO successivo esce dal gruppo; STOP lo ferma."),
                 "rplaylist": tr("Come Playlist, ma in ordine casuale: ogni cue suona una volta, in un ordine diverso a "
                              "ogni avvio. Dopo l'ultima il gruppo finisce, oppure, con il loop, "
                              "rimescola e ricomincia. Il GO successivo esce; STOP lo ferma.")}
        h = QLabel(help_.get(m, ""))
        h.setWordWrap(True)
        f.addRow(h)
        return w

    def _tap_box(self, cue):
        """Tap Tempo del gruppo: BPM battuti a mano (tasto, MIDI, OSC o pulsante) e divisore."""
        from PySide6.QtGui import QKeySequence
        from PySide6.QtWidgets import QGroupBox
        from ..engine import TAP_DIVS, TAP_MIN_INTERVAL, TAP_MODES, fmt_div, tap_interval
        box = QGroupBox(tr("Tap Tempo"))
        f = QFormLayout(box)
        m = cue.group_mode
        ok_mode = m in TAP_MODES
        on = QCheckBox(tr("Una cue a ogni battito, come se si premesse GO"))
        on.setChecked(bool(cue.group_tap) and ok_mode)
        on.setEnabled(ok_mode)

        def toggled(v):
            cue.group_tap = bool(v)
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        on.toggled.connect(toggled)
        f.addRow(on)
        if not ok_mode:
            why = {"random": tr("Random avvia una sola cue: non c'è nulla da scandire a tempo (usa Random loop)."),
                   "all": tr("All avvia tutte le cue insieme: non c'è una sequenza da scandire."),
                   "playlist": tr("La Playlist segue la durata naturale di ogni cue: a tempo diventerebbe un Loop "
                               "(usa Loop o Random loop)."),
                   "rplaylist": tr("La Random playlist segue la durata naturale di ogni cue: a tempo diventerebbe "
                                "un Random loop.")}.get(m, "")
            lab = QLabel(tr("Non disponibile con questo tipo di gruppo. ") + why)
            lab.setWordWrap(True)
            lab.setStyleSheet("color:#999;")
            f.addRow(lab)
            return box
        if not cue.group_tap:
            return box
        # BPM + TAP
        row = QHBoxLayout()
        bpm = QDoubleSpinBox()
        bpm.setRange(20, 300)
        bpm.setDecimals(1)
        bpm.setSuffix(" BPM")
        bpm.setKeyboardTracking(False)
        bpm.setValue(float(cue.group_bpm or 120))
        row.addWidget(bpm)
        tap = QPushButton("TAP")
        tap.setMinimumSize(110, 40)
        tap.setStyleSheet("QPushButton{background:#6a3fb0;color:white;font-size:16px;font-weight:bold;"
                          "border-radius:6px;}QPushButton:pressed{background:#9a6af0;}")
        tap.setToolTip(tr("Batti il tempo: almeno due colpi (si usa la media degli ultimi 8; dopo 2,5 s di "
                       "pausa si ricomincia). Ogni colpo rimette anche in fase il battito."))
        row.addWidget(tap)
        row.addStretch(1)
        f.addRow(tr("Tempo"), row)
        dc = QComboBox()
        for d in TAP_DIVS:
            dc.addItem((tr("{0} battito", fmt_div(d)) if d <= 1 else tr("{0} battiti", fmt_div(d))), d)
        cur = min(range(len(TAP_DIVS)), key=lambda j: abs(TAP_DIVS[j] - float(cue.group_tap_div or 1)))
        dc.setCurrentIndex(cur)
        f.addRow(tr("Una cue ogni"), dc)
        iv_lab = QLabel()
        f.addRow("", iv_lab)

        def refresh_iv():
            iv = tap_interval(cue)
            raw = 60.0 / max(20.0, float(cue.group_bpm or 120)) * float(cue.group_tap_div or 1)
            if raw < 0.1:
                iv_lab.setText(tr("⚠ {0:.0f} ms tra una cue e l'altra: troppo veloce (minimo {1:.0f} ms; video e immagini non stanno al passo)", raw * 1000, TAP_MIN_INTERVAL * 1000))
                iv_lab.setStyleSheet("color:#e0a030;")
            else:
                iv_lab.setText(tr("= una cue ogni {0:.3g} s", iv))
                iv_lab.setStyleSheet("color:#999;")

        def bpm_set(v):
            cue.group_bpm = round(float(v), 1)
            self._emit()
            refresh_iv()
        bpm.valueChanged.connect(bpm_set)

        def div_set(j):
            cue.group_tap_div = float(dc.itemData(j))
            self._emit()
            refresh_iv()
        dc.currentIndexChanged.connect(div_set)

        def do_tap():
            self.ctrl.group_tap(cue.id)
            sync()
        tap.clicked.connect(do_tap)

        def sync():
            if not bpm.hasFocus() and abs(bpm.value() - float(cue.group_bpm or 120)) > 0.05:
                bpm.blockSignals(True)
                bpm.setValue(float(cue.group_bpm or 120))
                bpm.blockSignals(False)
            refresh_iv()
        t = QTimer(box)                      # battute da tastiera, MIDI o OSC: il valore si aggiorna da solo
        t.timeout.connect(sync)
        t.start(250)
        refresh_iv()
        # ingressi del tap
        kr = QHBoxLayout()
        ktxt = QKeySequence.fromString(cue.group_tap_key, QKeySequence.PortableText).toString(
            QKeySequence.NativeText) if cue.group_tap_key else tr("— nessuno —")
        kr.addWidget(QLabel(ktxt))
        kb = QPushButton(tr("Impara…"))
        kb.setToolTip(tr("Il tasto da premere a tempo (anche un pedale o una pulsantiera USB che si presenta "
                      "come tastiera)"))
        kb.clicked.connect(lambda: self.hotkey_fn and self.hotkey_fn(cue, "group_tap_key"))
        kr.addWidget(kb)
        kx = QPushButton("✕")
        kx.setFixedWidth(28)
        kx.setEnabled(bool(cue.group_tap_key))
        kx.clicked.connect(lambda: self.set_hotkey_fn and self.set_hotkey_fn(cue, "", "group_tap_key"))
        kr.addWidget(kx)
        kr.addStretch(1)
        f.addRow(tr("Tasto TAP"), kr)
        mr = QHBoxLayout()
        ml = self._line("group_tap_midi", tr("es. note 1 36  /  cc 1 64  (pedale, pad, controller)"))
        mr.addWidget(ml)
        mb = QPushButton(tr("Impara"))

        def learned(spec):
            cue.group_tap_midi = spec
            ml.setText(spec)
            self._emit()
        mb.clicked.connect(lambda: self.learnMidi.emit(learned))
        mr.addWidget(mb)
        f.addRow(tr("MIDI TAP"), mr)
        f.addRow(tr("OSC TAP"), self._line("group_tap_osc", tr("es. /tap  (sempre valido: /cue/{0}/tap)", cue.number or tr('<numero>'))))
        note = {
            "sequence": tr("A ogni battito il gruppo avanza come con un GO; dopo l'ultima cue il tempo si ferma. "
                        "Una cue con pre-wait parte in ritardo rispetto al battito; quelle «con la precedente» "
                        "partono insieme alla loro; un GO dato a mano avanza in più."),
            "loop": tr("Il battito sostituisce l'«attesa»: una cue più lunga dell'intervallo viene tagliata (con il "
                    "crossfade, al massimo metà intervallo), una più corta lascia silenzio fino al battito."),
            "rloop": tr("Il battito sostituisce l'attesa (o il GO, se l'avanzamento è «col GO»): una cue più lunga "
                     "dell'intervallo viene tagliata, una più corta lascia silenzio fino al battito. Il GO "
                     "esce dal gruppo."),
        }.get(m, "")
        nl = QLabel(note + tr(" Il tempo si può battere anche mentre il gruppo non suona."))
        nl.setWordWrap(True)
        nl.setStyleSheet("color:#999;")
        f.addRow(nl)
        return box

    def _tab_groupexit(self):
        cue = self.cue
        w = QWidget()
        f = QFormLayout(w)
        gc = QComboBox()
        gc.addItem(tr("— scegli —"), "")
        sh = self.show_fn()
        for lst in sh.lists:
            for c in lst.cues:
                if c.type == "group":
                    where = "" if lst.is_main else f"[{lst.name}] "
                    gc.addItem(f"{where}{c.number or '·'} – {c.name or tr('Gruppo')}", c.number or c.id)
        tg = sh.resolve_target(cue.target) if cue.target else None
        if tg is not None:
            gc.setCurrentIndex(max(0, gc.findData(tg.number or tg.id)))
        gc.currentIndexChanged.connect(lambda j: (setattr(cue, "target", gc.itemData(j) or ""), self._emit()))
        f.addRow(tr("Gruppo"), gc)
        lab = QLabel(tr("Chiude il gruppo: niente più avanzamenti, la cue corrente sfuma (o finisce, secondo il "
                     "gruppo) e il playhead esce dal gruppo. Si può richiamare da un'altra lista (cue Comando › "
                     "Avvia), con il suo trigger MIDI/OSC (scheda «Base») o con /cue/<numero>/start."))
        lab.setWordWrap(True)
        lab.setStyleSheet("color:#999;")
        f.addRow(lab)
        return w

    def _tab_stoppanel(self):
        cue = self.cue
        w = QWidget()
        f = QFormLayout(w)
        sh = self.show_fn()
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        main_cb = QCheckBox(tr("{0}   (lista principale)", sh.main.name))
        main_cb.setChecked(sh.main.id in (cue.sp_panels or []))
        v.addWidget(main_cb)
        every = QCheckBox(tr("Tutti i PANEL (anche quelli creati in seguito)"))
        every.setChecked("*" in (cue.sp_panels or []))
        v.addWidget(every)
        checks = []
        for lst in sh.panels:
            cb = QCheckBox(f"{lst.name}" + ("   (GRID)" if getattr(lst, "kind", "") == "grid" else ""))
            cb.setChecked(lst.id in (cue.sp_panels or []))
            checks.append((cb, lst.id))
            v.addWidget(cb)
        if not sh.panels:
            nl = QLabel(tr("Non ci sono ancora PANEL: «Tutti» fermerà quelli che creerai."))
            nl.setStyleSheet("color:#999;")
            v.addWidget(nl)

        def refresh():
            for cb, _ in checks:
                cb.setEnabled(not every.isChecked())

        def save(*_a):
            sel = [sh.main.id] if main_cb.isChecked() else []
            if every.isChecked():
                sel.append("*")
            else:
                sel += [pid for cb, pid in checks if cb.isChecked()]
            cue.sp_panels = sel
            refresh()
            self._emit()
        main_cb.toggled.connect(save)
        every.toggled.connect(save)
        for cb, _ in checks:
            cb.toggled.connect(save)
        refresh()
        f.addRow(tr("Liste da fermare"), box)
        f.addRow(tr("Fade"), self._dspin("stop_fade", 0, 600, 0.1, 2, special=tr("nessuno (stop immediato)")))
        lab = QLabel(tr("Ferma le cue in esecuzione (e i pre-wait) delle liste scelte, sfumandole nel tempo indicato; "
                     "le altre liste continuano. Con «Tutti i PANEL» vale anche per i PANEL aggiunti dopo; la MAIN "
                     "si sceglie a parte. La cue «Stop panel» non ferma sé stessa."))
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
        dc.addItem(tr("— manuale (IP, indirizzo e argomenti scritti a mano) —"), "")
        for d in devs:
            p = profiles().get(d.get("profile")) or {}
            dc.addItem(f"{d.get('name')}  ({p.get('name', '?')} · {d.get('host')})", d.get("id"))
        j = dc.findData(cue.osc_device)
        if j < 0 and cue.osc_device:
            dc.addItem(tr("{0} (non configurato)", cue.osc_device), cue.osc_device)
            j = dc.count() - 1
        dc.setCurrentIndex(max(0, j))

        def dev_changed(i):
            cue.osc_device = dc.itemData(i) or ""
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        dc.currentIndexChanged.connect(dev_changed)
        f.addRow(tr("Dispositivo"), dc)
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
            f.addRow(tr("Comando"), cc)
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
            f.addRow(tr("Messaggio"), prev)
            if prof.get("notes"):
                n = QLabel(prof["notes"])
                n.setWordWrap(True)
                n.setStyleSheet("color:#888; font-size:10px;")
                f.addRow(n)
        else:
            if not devs:
                tip = QLabel(tr("Per console e software con profilo (es. ChamSys MagicQ): Impostazioni › OSC › "
                             "Dispositivi OSC › Aggiungi da profilo…"))
                tip.setWordWrap(True)
                tip.setStyleSheet("color:#888;")
                f.addRow(tip)
            f.addRow(tr("Host / IP"), self._line("osc_host"))
            f.addRow(tr("Porta"), self._ispin("osc_port", 1, 65535))
            f.addRow(tr("Indirizzo"), self._line("osc_address", "/esempio/comando"))
            f.addRow(tr("Argomenti"), self._line("osc_args", tr('es.  1 0.5 "testo" true')))
        b = QPushButton(tr("Invia ora"))
        b.clicked.connect(lambda: self.oscSend.emit(self.cue.id))
        f.addRow(b)
        return w

    def _tab_wait(self):
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(tr("Durata"), self._dspin("duration", 0, 360000, 0.5, 2))
        return w
