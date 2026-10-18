"""Scheda dell'ispettore per le cue mixer (mute a scena, fader, controlli delle schede) (parte di Inspector)."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)


class MixerTabMixin:
    MUTE_STYLE = {None: "QToolButton{background:#55585f;color:#ddd;border:none;border-radius:3px;}",
                  True: "QToolButton{background:#b3261e;color:white;border:none;border-radius:3px;font-weight:bold;}",
                  False: "QToolButton{background:#2e7d46;color:white;border:none;border-radius:3px;font-weight:bold;}"}

    def _mixer_control_tab(self, w, f, cls, conf, mm):
        """Cue «Imposta un controllo»: un controllo della scheda (dall'elenco letto) e il suo valore."""
        from PySide6.QtWidgets import QSlider
        from ..ui_mixers import _ctl_text
        cue = self.cue
        mm.ensure_controls(cue.mix_target)
        ctls = mm.controls.get(cue.mix_target) or []
        cc = QComboBox()
        cc.setEditable(False)
        cc.addItem("— scegli —", "")
        groups = {}
        for c in ctls:
            groups.setdefault(c.get("group") or "Altro", []).append(c)
        from ..alsactl import group_rank
        for gname, items in sorted(groups.items(), key=lambda kv: group_rank(kv[0])):
            for c in items:
                if c.get("writable", True):
                    from ..alsactl import pretty_name
                    cc.addItem(f"{gname} › {pretty_name(c['name'])}", c["key"])
        j = cc.findData(cue.mix_ctl)
        if j < 0 and cue.mix_ctl:
            cc.addItem(f"{cue.mix_ctl} (non letto)", cue.mix_ctl)
            j = cc.count() - 1
        cc.setCurrentIndex(max(0, j))

        def ctl_changed(i):
            cue.mix_ctl = cc.itemData(i) or ""
            info = mm.control_info(cue.mix_target, cue.mix_ctl)
            if info is not None and info.get("value") is not None:
                cue.mix_ctl_value = int(info["value"])      # parte dal valore attuale della scheda
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        cc.currentIndexChanged.connect(ctl_changed)
        row = QHBoxLayout()
        row.addWidget(cc, 1)
        rd = QPushButton("⟲ Rileggi controlli")
        rd.clicked.connect(lambda: mm.read_controls(cue.mix_target))
        row.addWidget(rd)
        f.addRow("Controllo", row)
        if not ctls:
            lab = QLabel("Controlli non ancora letti: scheda collegata? (serve «amixer», pacchetto alsa-utils)")
            lab.setStyleSheet("color:#e0b040;")
            lab.setWordWrap(True)
            f.addRow(lab)
        info = mm.control_info(cue.mix_target, cue.mix_ctl)
        res = QLabel()
        res.setStyleSheet("color:#999;")
        if info is not None:
            typ = info["type"]
            cur = int(cue.mix_ctl_value or 0)
            if typ == "bool":
                vw = QComboBox()
                vw.addItem("spento", 0)
                vw.addItem("acceso", 1)
                vw.setCurrentIndex(1 if cur else 0)
                vw.currentIndexChanged.connect(lambda i: (setattr(cue, "mix_ctl_value", i), self._emit()))
            elif typ == "enum":
                vw = QComboBox()
                for it in info.get("items") or []:
                    vw.addItem(it)
                vw.setCurrentIndex(max(0, min(cur, vw.count() - 1)))
                vw.currentIndexChanged.connect(lambda i: (setattr(cue, "mix_ctl_value", i), self._emit()))
            else:
                vw = QWidget()
                h = QHBoxLayout(vw)
                h.setContentsMargins(0, 0, 0, 0)
                sl = QSlider(Qt.Horizontal)
                sl.setRange(int(info["min"]), int(info["max"]))
                sl.setValue(max(int(info["min"]), min(int(info["max"]), cur)))
                lab = QLabel(_ctl_text(info, sl.value()))
                lab.setMinimumWidth(80)
                sl.valueChanged.connect(lambda x: (lab.setText(_ctl_text(info, x)),
                                                   setattr(cue, "mix_ctl_value", int(x)), self._emit()))
                h.addWidget(sl, 1)
                h.addWidget(lab)
            f.addRow("Valore", vw)
            now = QLabel(f"Valore attuale sulla scheda: {_ctl_text(info, info.get('value'))}")
            now.setStyleSheet("color:#999;")
            f.addRow(now)
        b = QPushButton("Invia ora")
        b.clicked.connect(lambda: (mm.set_control(cue.mix_target, cue.mix_ctl, int(cue.mix_ctl_value or 0))
                                   if cue.mix_ctl else None,
                                   res.setText("Inviato (l'esito compare nella barra di stato)")))
        f.addRow(b)
        f.addRow(res)
        if cls.NOTES:
            n = QLabel(cls.NOTES)
            n.setWordWrap(True)
            n.setStyleSheet("color:#888; font-size:10px;")
            f.addRow(n)
        return w

    def _mixer_mute_tab(self, w, f, cls, conf, mm):
        """Mute «a scena»: tutte le sezioni e tutti i canali del mixer, ciascuno grigio (non toccare),
        rosso (forza mute) o verde (forza acceso). Clic: grigio → rosso → verde → grigio;
        clic destro: torna grigio; Maiusc+clic: stesso stato su tutto l'intervallo dall'ultimo cliccato."""
        from PySide6.QtCore import QObject, Signal as _Sig
        from PySide6.QtWidgets import QApplication, QGridLayout, QToolButton
        from ..mixers import KIND_LABELS, mute_states, mute_summary, store_mute_states
        cue = self.cue
        secs = [(k, n) for k, n, caps in cls.sections(conf) if "mute" in caps]
        order = [k for k, _n in secs]
        valid = dict(secs)
        states = {k: {n: v for n, v in st.items() if 1 <= n <= valid[k]}
                  for k, st in mute_states(cue).items() if k in valid}
        btns = {}
        last = {"key": None}
        summary = QLabel()
        summary.setWordWrap(True)

        def paint(k, n):
            b = btns[(k, n)]
            b.setStyleSheet(self.MUTE_STYLE[states.get(k, {}).get(n)])

        def commit():
            store_mute_states(cue, states)
            summary.setText(mute_summary(states, order))
            self._emit()

        def set_state(k, n, v):
            st = states.setdefault(k, {})
            if v is None:
                st.pop(n, None)
            else:
                st[n] = v
            if not st:
                states.pop(k, None)
            paint(k, n)

        def clicked(k, n, right=False):
            cur = states.get(k, {}).get(n)
            nxt = None if right else {None: True, True: False, False: None}[cur]
            prev = last["key"]
            if (QApplication.keyboardModifiers() & Qt.ShiftModifier) and prev and prev[0] == k and not right:
                nxt = states.get(k, {}).get(prev[1])
                for m in range(min(prev[1], n), max(prev[1], n) + 1):
                    set_state(k, m, nxt)
            else:
                set_state(k, n, nxt)
            last["key"] = (k, n)
            commit()

        legend = QLabel("<span style='background:#b3261e;color:white'>&nbsp;rosso&nbsp;</span> forza mute · "
                        "<span style='background:#2e7d46;color:white'>&nbsp;verde&nbsp;</span> forza acceso · "
                        "<span style='background:#55585f;color:white'>&nbsp;grigio&nbsp;</span> non toccare &nbsp;—&nbsp; "
                        "clic: grigio → rosso → verde · clic destro: grigio · Maiusc+clic: intervallo")
        legend.setWordWrap(True)
        legend.setStyleSheet("color:#999;")
        f.addRow(legend)

        class _R(QObject):
            got = _Sig(str, bool, object)
        relay = _R(w)
        res = QLabel()
        res.setWordWrap(True)
        res.setStyleSheet("color:#999;")

        def sec_fill(k, n_max, v):
            for m in range(1, n_max + 1):
                set_state(k, m, v)
            commit()

        for k, n_max in secs:
            box = QWidget()
            bv = QVBoxLayout(box)
            bv.setContentsMargins(0, 4, 0, 2)
            bv.setSpacing(3)
            head = QHBoxLayout()
            t = QLabel(f"<b>{KIND_LABELS.get(k, k)}</b>")
            head.addWidget(t)
            head.addStretch(1)
            for text, v, tip in (("tutti mute", True, "Forza mute su tutta la sezione"),
                                 ("tutti on", False, "Forza acceso su tutta la sezione"),
                                 ("non toccare", None, "Riporta in grigio tutta la sezione")):
                pb = QPushButton(text)
                pb.setToolTip(tip)
                pb.setFocusPolicy(Qt.NoFocus)
                pb.clicked.connect(lambda _=False, k=k, n_max=n_max, v=v: sec_fill(k, n_max, v))
                head.addWidget(pb)
            rd = QPushButton("⟲ Leggi")
            rd.setToolTip("Copia nella cue lo stato attuale del mixer per questa sezione (rosso/verde su ogni canale)")
            rd.setFocusPolicy(Qt.NoFocus)
            rd.clicked.connect(lambda _=False, k=k: (res.setText("… lettura dal mixer"),
                               mm.read_mutes(cue.mix_target, k, lambda ok, r, k=k: relay.got.emit(k, ok, r))))
            head.addWidget(rd)
            bv.addLayout(head)
            grid = QGridLayout()
            grid.setSpacing(3)
            named = any(mm.channel_name(cue.mix_target, k, m) for m in range(1, n_max + 1))
            per_row = 12 if named else 16
            for m in range(1, n_max + 1):
                b = QToolButton()
                nm = mm.channel_name(cue.mix_target, k, m)
                if n_max > 1:
                    b.setText(f"{m}\n{nm[:10]}" if named else str(m))
                    b.setFixedSize(70 if named else 34, 38 if named else 26)
                else:
                    b.setText(nm or KIND_LABELS.get(k, k))
                    b.setFixedSize(110, 26)
                b.setToolTip(f"{KIND_LABELS.get(k, k)} {m}" + (f" · {nm}" if nm else ""))
                b.setFocusPolicy(Qt.NoFocus)
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.clicked.connect(lambda _=False, k=k, m=m: clicked(k, m))
                b.customContextMenuRequested.connect(lambda _p, k=k, m=m: clicked(k, m, right=True))
                btns[(k, m)] = b
                grid.addWidget(b, (m - 1) // per_row, (m - 1) % per_row)
                paint(k, m)
            grid.setColumnStretch(per_row, 1)
            bv.addLayout(grid)
            f.addRow(box)

        def got(k, ok, r):
            if not ok:
                res.setText(f"✘ {KIND_LABELS.get(k, k)}: lettura non riuscita: {r}")
                res.setStyleSheet("color:#e66;")
                return
            for m, v in enumerate(r or [], start=1):
                if (k, m) in btns and v is not None:       # None = stato non noto: resta com'è
                    set_state(k, m, bool(v))
            commit()
            res.setText(f"✔ {KIND_LABELS.get(k, k)}: stato del mixer registrato nella cue")
            res.setStyleSheet("color:#6c6;")
        relay.got.connect(got)

        row = QHBoxLayout()
        rall = QPushButton("⟲ Leggi tutto dal mixer")
        rall.setToolTip("Registra nella cue lo stato attuale di tutte le sezioni (una «scena» di mute)")
        rall.clicked.connect(lambda: (res.setText("… lettura dal mixer"), [
            mm.read_mutes(cue.mix_target, k, lambda ok, r, k=k: relay.got.emit(k, ok, r)) for k in order]))
        row.addWidget(rall)
        if mm.supports_names(cue.mix_target):
            nb = QPushButton("⟲ Nomi canali")
            nb.setToolTip("Rilegge dal mixer i nomi dei canali")
            nb.clicked.connect(lambda: (res.setText("… lettura dei nomi"), mm.read_names(cue.mix_target)))
            row.addWidget(nb)
        clr = QPushButton("Tutto grigio")
        clr.setToolTip("Nessun cambiamento: la cue non tocca nessun mute")

        def clear_all():
            for (k, m) in btns:
                set_state(k, m, None)
            commit()
        clr.clicked.connect(clear_all)
        row.addWidget(clr)
        b = QPushButton("Invia ora")

        def send():
            if not states:
                res.setText("✘ Nessun canale rosso o verde")
                res.setStyleSheet("color:#e66;")
                return
            for k, st in states.items():
                for flag in (True, False):
                    nums = sorted(n for n, v in st.items() if v is flag)
                    if nums:
                        mm.mute(cue.mix_target, k, nums, flag)
            res.setText("Inviato (l'esito compare nella barra di stato)")
            res.setStyleSheet("color:#999;")
        b.clicked.connect(send)
        row.addWidget(b)
        f.addRow(row)
        summary.setText(mute_summary(states, order))
        summary.setStyleSheet("color:#bbb;")
        f.addRow("Cambia", summary)
        f.addRow(res)
        if cls.NOTES:
            n = QLabel(cls.NOTES)
            n.setWordWrap(True)
            n.setStyleSheet("color:#888; font-size:10px;")
            f.addRow(n)
        return w

    def _mixer_channel_picker(self, n_max):
        """Scelta dei canali della sezione: un pulsante per numero (clic = aggiungi/togli,
        Maiusc+clic = intervallo dall'ultimo cliccato), più «Tutti» e «Nessuno»."""
        from PySide6.QtWidgets import QGridLayout, QToolButton
        from ..mixers import fmt_numbers, parse_numbers
        cue = self.cue
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        if n_max <= 1:
            if cue.mix_channels != "1":
                cue.mix_channels = "1"
                self._emit()
            lab = QLabel("unico (la sezione ha un solo elemento)")
            lab.setStyleSheet("color:#999;")
            v.addWidget(lab)
            return box
        sel = set(n for n in (parse_numbers(cue.mix_channels) or []) if 1 <= n <= n_max)
        grid = QGridLayout()
        grid.setSpacing(3)
        mm = getattr(self.ctrl, "mixers", None)
        names = [mm.channel_name(cue.mix_target, cue.mix_kind, n) if mm else "" for n in range(1, n_max + 1)]
        named = any(names)
        per_row = (12 if named else 16) if n_max > 8 else n_max
        btns = {}
        state = {"last": None}
        summary = QLabel()
        summary.setStyleSheet("color:#999;")

        def commit():
            cue.mix_channels = fmt_numbers(sorted(sel))
            for n, b in btns.items():
                b.blockSignals(True)
                b.setChecked(n in sel)
                b.blockSignals(False)
            summary.setText(f"Selezionati: {cue.mix_channels}" if sel else "⚠ Nessun canale selezionato")
            summary.setStyleSheet("color:#999;" if sel else "color:#e0b040;")
            self._emit()

        def clicked(n, on):
            from PySide6.QtWidgets import QApplication
            last = state["last"]
            if QApplication.keyboardModifiers() & Qt.ShiftModifier and last is not None:
                rng = range(min(last, n), max(last, n) + 1)
                (sel.update if on else sel.difference_update)(rng)
            elif on:
                sel.add(n)
            else:
                sel.discard(n)
            state["last"] = n
            commit()

        for n in range(1, n_max + 1):
            b = QToolButton()
            nm = names[n - 1]
            b.setText(f"{n}\n{nm[:10]}" if named else str(n))
            if nm:
                b.setToolTip(f"{n} · {nm}")
            b.setCheckable(True)
            b.setChecked(n in sel)
            b.setFocusPolicy(Qt.NoFocus)
            b.setFixedSize(70 if named else 34, 38 if named else 26)
            b.setStyleSheet("QToolButton{background:#3a3d44;color:#ddd;border:none;border-radius:3px;}"
                            "QToolButton:checked{background:#2e7d46;color:white;font-weight:bold;}")
            b.toggled.connect(lambda on, n=n: clicked(n, on))
            btns[n] = b
            grid.addWidget(b, (n - 1) // per_row, (n - 1) % per_row)
        grid.setColumnStretch(per_row, 1)
        v.addLayout(grid)
        row = QHBoxLayout()
        for text, fn in (("Tutti", lambda: (sel.update(range(1, n_max + 1)), commit())),
                         ("Nessuno", lambda: (sel.clear(), commit()))):
            pb = QPushButton(text)
            pb.clicked.connect(fn)
            row.addWidget(pb)
        row.addWidget(summary, 1)
        v.addLayout(row)
        hint = QLabel("Clic per aggiungere o togliere · Maiusc+clic per un intervallo")
        hint.setStyleSheet("color:#888;font-size:11px;")
        v.addWidget(hint)
        summary.setText(f"Selezionati: {fmt_numbers(sorted(sel))}" if sel else "⚠ Nessun canale selezionato")
        if not sel:
            summary.setStyleSheet("color:#e0b040;")
        if fmt_numbers(sorted(sel)) != (cue.mix_channels or "") and sel:
            cue.mix_channels = fmt_numbers(sorted(sel))     # normalizza (fuori sezione scartati)
        return box

    def _tab_mixer(self):
        from PySide6.QtCore import QObject, Signal as _Sig
        from ..mixers import KIND_LABELS, fmt_db, fmt_numbers, parse_numbers
        cue = self.cue
        mm = getattr(self.ctrl, "mixers", None)
        w = QWidget()
        f = QFormLayout(w)
        mixers = mm.mixers if mm else []
        mc = QComboBox()
        mc.addItem("— scegli —", "")
        for m in mixers:
            cls = mm.cls_for(m)
            mc.addItem(f"{m.get('name') or m.get('host')}  ({cls.NAME if cls else m.get('driver')})", m.get("id"))
        i = mc.findData(cue.mix_target)
        if i < 0 and cue.mix_target:
            mc.addItem(f"{cue.mix_target} (non configurato)", cue.mix_target)
            i = mc.count() - 1
        if i <= 0 and len(mixers) == 1 and not cue.mix_target:
            cue.mix_target = mixers[0].get("id")
            i = 1
        mc.setCurrentIndex(max(0, i))

        def mixer_changed(j):
            cue.mix_target = mc.itemData(j) or ""
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        mc.currentIndexChanged.connect(mixer_changed)
        f.addRow("Mixer", mc)
        if not mixers:
            lab = QLabel("Nessun mixer configurato: Impostazioni › Mixer… (con ricerca in rete)")
            lab.setStyleSheet("color:#e0b040;")
            f.addRow(lab)
            return w
        conf = mm.find(cue.mix_target)
        cls = mm.cls_for(conf) if conf else None
        if cls is None:
            if conf is not None:
                lab = QLabel(f"Driver «{conf.get('driver')}» non disponibile.")
                lab.setStyleSheet("color:#e0b040;")
                f.addRow(lab)
            return w
        mm.ensure_names(cue.mix_target)
        if cue.mix_action == "unmute":
            from ..mixers import migrate_mute_cue
            migrate_mute_cue(cue)
            cue.mix_action = "mute"
        ac = QComboBox()
        acts = []
        if cls.sections(conf):
            acts += [("mute", "Mute: forza acceso / spento canale per canale"), ("fader", "Fader: porta al livello")]
            if any("fader" in caps for _k, _n, caps in cls.sections(conf)):
                acts.append(("duck", "Ducking: aggancia i fader alla curva del ducking"))
        if cls.CONTROLS:
            acts.append(("control", "Imposta un controllo della scheda (interruttore, menu, volume)"))
        for code, text in acts:
            ac.addItem(text, code)
        if ac.findData(cue.mix_action) < 0 and acts:
            cue.mix_action = acts[0][0]
        ac.setCurrentIndex(max(0, ac.findData(cue.mix_action)))

        def act_changed(j):
            cue.mix_action = ac.itemData(j)
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        ac.currentIndexChanged.connect(act_changed)
        f.addRow("Azione", ac)
        if cue.mix_action == "control":
            return self._mixer_control_tab(w, f, cls, conf, mm)
        if cue.mix_action not in ("fader", "duck"):
            return self._mixer_mute_tab(w, f, cls, conf, mm)
        need = "fader"
        secs = [(k, n) for k, n, caps in cls.sections(conf) if need in caps]
        kc = QComboBox()
        for k, n in secs:
            kc.addItem(f"{KIND_LABELS.get(k, k)} (1–{n})" if n > 1 else KIND_LABELS.get(k, k), k)
        j = kc.findData(cue.mix_kind)
        if j < 0 and secs:
            cue.mix_kind = secs[0][0]
            j = 0
        kc.setCurrentIndex(max(0, j))
        sec_n = dict(secs)

        def kind_changed(j):
            cue.mix_kind = kc.itemData(j)
            n_max = sec_n.get(cue.mix_kind, 1)
            keep = [n for n in (parse_numbers(cue.mix_channels) or []) if 1 <= n <= n_max]
            cue.mix_channels = fmt_numbers(keep or [1])
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        kc.currentIndexChanged.connect(kind_changed)
        f.addRow("Sezione", kc)
        f.addRow("Canali", self._mixer_channel_picker(sec_n.get(cue.mix_kind, 1)))

        class _R(QObject):
            got = _Sig(bool, object)
        relay = _R(w)
        res = QLabel()
        res.setWordWrap(True)
        res.setStyleSheet("color:#999;")
        if cue.mix_action in ("fader", "duck"):
            lv = QDoubleSpinBox()
            lv.setRange(-90.0, 10.0)
            lv.setDecimals(1)
            lv.setSingleStep(0.5)
            lv.setSuffix(" dB")
            lv.setSpecialValueText("-∞ dB")
            lv.setValue(float(cue.mix_level))
            lv.valueChanged.connect(lambda val: (setattr(cue, "mix_level", round(val, 1)), self._emit()))
            row = QHBoxLayout()
            row.addWidget(lv, 1)
            rb = QPushButton("⟲ Leggi dal mixer")
            rb.setToolTip("Registra nella cue il livello attuale del fader sul mixer (primo canale indicato)")
            row.addWidget(rb)
            if cue.mix_action == "duck":
                f.addRow("Livello normale", row)
                dd = self._dspin("mix_duck_db", 0, 90, 1, 1, " dB")
                dd.setToolTip("Di quanti dB scendono i fader quando il ducking interviene (con attacco, hold e "
                              "rilascio delle impostazioni del ducking)")
                f.addRow("Riduzione", dd)
                hint = QLabel("Finché la cue è attiva i fader seguono la curva del ducking: «livello normale» "
                              "quando nessuno parla, «livello normale − riduzione» quando l'ingresso del "
                              "ducking supera la soglia. STOP sgancia i fader e li riporta al livello normale. "
                              "Ingresso, soglia e tempi: Impostazioni › Ducking…")
                hint.setWordWrap(True)
                hint.setStyleSheet("color:#999;")
                f.addRow(hint)
            else:
                f.addRow("Livello", row)
                f.addRow("Fade", self._dspin("mix_fade", 0, 600, 0.1, 1))

            def read():
                nums = parse_numbers(cue.mix_channels) or [1]
                res.setText("… lettura dal mixer")
                rb.setEnabled(False)
                mm.read_fader(cue.mix_target, cue.mix_kind, nums[0], lambda ok, v: relay.got.emit(ok, v))

            def got(ok, v):
                rb.setEnabled(True)
                if ok and v is not None:
                    lv.setValue(round(max(-90.0, float(v)), 1))
                    res.setText(f"✔ Livello registrato dal mixer: {fmt_db(float(v))}")
                    res.setStyleSheet("color:#6c6;")
                else:
                    res.setText(f"✘ Lettura non riuscita: {v}")
                    res.setStyleSheet("color:#e66;")
            rb.clicked.connect(read)
            relay.got.connect(got)
        b = QPushButton("Invia ora")

        def send():
            nums = parse_numbers(cue.mix_channels) or []
            if not nums:
                res.setText("✘ Indica almeno un canale")
                return
            fade = float(cue.mix_fade or 0) if cue.mix_action == "fader" else 0.0
            for n in nums:
                mm.fader(cue.mix_target, cue.mix_kind, n, float(cue.mix_level), fade)
            res.setText("Inviato (l'esito compare nella barra di stato)")
            res.setStyleSheet("color:#999;")
        b.clicked.connect(send)
        f.addRow(b)
        f.addRow(res)
        if cls.NOTES:
            n = QLabel(cls.NOTES)
            n.setWordWrap(True)
            n.setStyleSheet("color:#888; font-size:10px;")
            f.addRow(n)
        return w
