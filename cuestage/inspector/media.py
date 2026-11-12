"""Schede dell'ispettore per audio, video e immagini: tempi, uscita audio, crop, preset, fade (parte di Inspector)."""
from ..i18n import tr
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..media import fmt_time
from ..model import (
    FADE_SHAPE_LABELS, FADE_SHAPES, FIT_LABELS, FIT_MODES,
    MAX_DB, MEDIA_EXT, MIN_DB, )
from ..ui_crop import CropPanel
from ..ui_timeline import TimelineEditor
from .presets import PresetBar


class MediaTabsMixin:
    def _duck_row(self):
        """Riduzione del ducking (dB) di questa cue o gruppo, con lo stato del ducking dello show."""
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget
        from ..ducking import duck_settings
        cue = self.cue
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        sp = self._dspin("duck_db", 0, 60, 1, 1, " dB", special=tr("nessuno"))
        sp.setToolTip(tr("Di quanti dB si abbassa questa cue quando l'ingresso del ducking supera la soglia "
                      "(Impostazioni › Ducking…). ") + (tr("Vale per tutte le cue del gruppo che non hanno un "
                                                      "ducking proprio.") if cue.type == "group" else
                                                      tr("Se è «nessuno» vale quello del gruppo che la contiene.")))
        h.addWidget(sp)
        note = QLabel()
        note.setStyleSheet("color:#999;")
        note.setWordWrap(True)
        sh = self.show_fn()
        cfg = duck_settings(sh.settings)
        parts = []
        g = sh.by_id(cue.group) if cue.group else None
        seen = set()
        while g is not None and g.id not in seen:
            seen.add(g.id)
            if float(g.duck_db or 0) > 0:
                parts.append(tr("gruppo «{0}»: −{1:.1f} dB", g.label(), g.duck_db) +
                             (tr(" (vale se qui è «nessuno»)") if float(cue.duck_db or 0) > 0 else tr(" (in uso)")))
                break
            g = sh.by_id(g.group) if g.group else None
        if not cfg["enabled"]:
            parts.append(tr("ducking spento nelle impostazioni"))
        note.setText(" · ".join(parts))
        h.addWidget(note, 1)
        b = QPushButton(tr("Impostazioni…"))
        b.clicked.connect(lambda: self.duck_dialog_fn() if self.duck_dialog_fn else None)
        h.addWidget(b)
        if self.duck_dialog_fn is None:
            b.hide()
        return w

    def _audio_out_section(self, kbar, info):
        """Scheda audio della cue e matrice canali sorgente × uscite della scheda, al tempo del
        cursore della timeline dei keyframe (kbar)."""
        from ..audio_engine import list_devices, device_label
        from ..media import channel_labels
        from ..ui_matrix import MatrixEditor
        cue = self.cue
        eng = self.ctrl.audio
        w = QGroupBox(tr("Uscita audio: scheda e matrice dei canali"))
        v = QVBoxLayout(w)
        f = QFormLayout()
        dev = QComboBox()
        dname, dch, _ = eng.resolve(None)
        dev.addItem(tr("Predefinita del progetto — {0} ({1} canali)", dname, dch), "")
        for d in list_devices():
            dev.addItem(tr("{0} — {1} canali", device_label(d), eng.channels_for(d)), d["id"])
        i = dev.findData(cue.audio_device or "")
        if i < 0:
            dev.addItem(tr("{0} (non collegata)", cue.audio_device.split('|', 1)[-1]), cue.audio_device)
            i = dev.count() - 1
        dev.setCurrentIndex(i)

        def dev_changed(i):
            cue.audio_device = dev.itemData(i) or ""
            self._emit()
            QTimer.singleShot(0, self.rebuild)   # la matrice dipende dai canali della scheda
        dev.currentIndexChanged.connect(dev_changed)
        f.addRow(tr("Scheda audio"), dev)
        f.addRow(tr("Ducking"), self._duck_row())
        engine_route = True
        if cue.type == "video":
            route = QComboBox()
            route.addItem(tr("Motore CueStage (scheda e matrice di questa cue)"), "engine")
            route.addItem(tr("Lettore di sistema (scheda predefinita, senza matrice)"), "system")
            route.setCurrentIndex(max(0, route.findData(cue.video_audio or "engine")))

            def route_changed(i):
                cue.video_audio = route.itemData(i)
                self._emit()
                QTimer.singleShot(0, self.rebuild)
            route.currentIndexChanged.connect(route_changed)
            f.addRow(tr("Audio del video"), route)
            engine_route = cue.video_audio != "system"
        v.addLayout(f)
        name, n_out, found = eng.resolve(cue.audio_device)
        lab = QLabel()
        lab.setWordWrap(True)
        v.addWidget(lab)
        if not cue.file:
            lab.setText(tr("Nessun file."))
        elif info is None or kbar is None:
            lab.setText(tr("Analisi del file…"))
        elif not info.get("has_audio"):
            lab.setText(tr("Il file non contiene audio."))
        elif not engine_route:
            lab.setText(tr("Con il lettore di sistema l'audio esce dalla scheda predefinita del sistema, "
                        "in stereo, senza matrice."))
        else:
            n_src = int(info.get("channels") or 2)
            labels = channel_labels(n_src, info.get("layout", ""))
            warn = "" if found else tr("  ⚠ scheda scelta non collegata: in riproduzione si usa la predefinita.")
            lab.setText(tr("Sorgente: {0} canali ({1})  →  {2}: {3} canali.{4}", n_src, ' '.join(labels), name, n_out, warn))
            if not found:
                lab.setStyleSheet("color:#e0b040;")
            # ---- matrice al tempo del cursore della timeline dei keyframe
            from .. import keyframes as kfm
            from ..media import fmt_time
            state_lab = QLabel()
            state_lab.setWordWrap(True)
            v.addWidget(state_lab)

            def astate():
                return kfm.audio_state(cue, kbar.cue_time(), n_src, n_out)

            def mstate():
                st = astate()
                return None if st is None or kfm.is_base(*st[3:]) else (st[1], st[2])

            def mlocked():
                st = astate()
                if st is None or kfm.is_base(*st[3:]):
                    return None
                na, nb, f = st[3:]
                what = f"«{kfm.label(na)}»" if (na == nb or f <= 0) else \
                    tr("dissolvenza «{0}» → «{1}» ({2:.0f}%)", kfm.label(na), kfm.label(nb), f * 100)
                return (tr("A {0} vale {1}: sola visualizzazione. Modifica la matrice dove vale «base», oppure aggiorna il preset con la barra dei preset.", fmt_time(kbar.cue_time()), what))

            def show_state(*a):
                st = astate()
                lk = mlocked()
                if st is None:
                    state_lab.setText(tr("Nessun keyframe audio: valgono i valori della cue (volume {0:+.1f} dB).", cue.level_db))
                    state_lab.setStyleSheet("color:#888;")
                else:
                    state_lab.setText(tr("Volume a {0}: {1:+.1f} dB", fmt_time(kbar.cue_time()), st[0])
                                      + (f" — {lk}" if lk else tr(" — valori base: modificabili")))
                    state_lab.setStyleSheet("color:#50c8ff;" if lk else "color:#888;")
                mx.update()

            mx = MatrixEditor(cue, n_src, labels, n_out)
            mx.state_fn = mstate
            mx.locked_fn = mlocked
            mx.changed.connect(lambda: self._emit())
            kbar.timeChanged.connect(show_state)
            kbar.edited.connect(lambda: (show_state(), self._emit()))
            show_state()
            sa = QScrollArea()
            sa.setWidget(mx)
            sa.setFrameShape(QFrame.NoFrame)
            hgt = max(200, min(mx.height() + 20, 330))
            sa.setFixedHeight(hgt)
            fader = self._matrix_fader(mx, n_src, n_out, mlocked)
            fader.setFixedHeight(hgt)                    # alto quanto la matrice, alla sua sinistra
            hh = QHBoxLayout()
            hh.addWidget(fader)
            hh.addWidget(sa, 1)
            v.addLayout(hh)
            kbar.timeChanged.connect(lambda *_a: fader.sync())
            row = QHBoxLayout()
            b1 = QPushButton(tr("Routing predefinito"))
            b1.setToolTip(tr("Mono → Out 1-2 · stereo → Out 1/2 · multicanale → canale per canale "
                          "(5.1/7.1 su stereo: downmix)"))

            def reset():
                cue.matrix, cue.out_levels = [], []
                mx.update()
                self._emit()
            b1.clicked.connect(reset)
            b2 = QPushButton(tr("Volumi uscite a 0 dB"))

            def levels0():
                cue.out_levels = []
                mx.update()
                self._emit()
            b2.clicked.connect(levels0)
            row.addWidget(b1)
            row.addWidget(b2)
            row.addStretch(1)
            v.addLayout(row)
            hint = QLabel(tr("Clic su un incrocio: acceso (0 dB) / spento · trascina in verticale o rotella: "
                          "volume (Ctrl+rotella: decimi) · doppio clic: valore esatto · tasto destro: valori "
                          "rapidi. L'ultima riga regola il volume di ogni uscita. Le modifiche valgono anche "
                          "durante la riproduzione."))
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#888; font-size:10px;")
            v.addWidget(hint)
        return w

    def _matrix_fader(self, mx, n_src, n_out, locked_fn=None):
        """Fader a sinistra della matrice: alza o abbassa in proporzione tutti gli incroci accesi della matrice
        (stessa differenza in dB per tutti). Mostra l'incrocio più alto. Durante un trascinamento parte da una
        copia della matrice, così tornando indietro le proporzioni restano anche se qualche incrocio tocca −∞."""
        from PySide6.QtWidgets import QSlider
        from ..audio_engine import effective_matrix
        cue = self.cue
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 4, 0)
        col.setSpacing(2)
        t = QLabel(tr("Matrice"))
        t.setAlignment(Qt.AlignHCenter)
        t.setStyleSheet("color:#50c8ff; font-weight:bold;")
        sl = QSlider(Qt.Vertical)
        sl.setObjectName("matrix_fader")
        sl.setRange(int(MIN_DB * 10), int(MAX_DB * 10))
        sl.setSingleStep(5)
        sl.setPageStep(30)
        sl.setTickPosition(QSlider.TicksBothSides)
        sl.setTickInterval(60)
        tip = tr("Alza o abbassa in proporzione tutti gli incroci accesi della matrice (il valore è quello "
                 "dell'incrocio più alto). Vale anche mentre la cue suona.")
        sl.setToolTip(tip)
        sp = QDoubleSpinBox()
        sp.setObjectName("matrix_fader_spin")
        sp.setRange(MIN_DB, MAX_DB)
        sp.setDecimals(1)
        sp.setSingleStep(0.5)
        sp.setSuffix(" dB")
        sp.setSpecialValueText(tr("-∞ dB"))
        sp.setKeyboardTracking(False)
        sp.setFixedWidth(78)
        snap = {"m": None, "top": None, "written": None}

        def cur():
            return effective_matrix(cue.matrix, n_src, n_out)

        def top_of(m):
            vals = [x for row in m for x in row if x is not None]
            return max(vals) if vals else None

        def sync():
            m = cur()
            top = top_of(m)
            lk = locked_fn() if locked_fn else None
            on = top is not None and not lk
            for x in (sl, sp):
                x.blockSignals(True)
                x.setEnabled(on)
            if top is not None:
                sl.setValue(int(round(max(MIN_DB, min(MAX_DB, top)) * 10)))
                sp.setValue(max(MIN_DB, min(MAX_DB, top)))
            for x in (sl, sp):
                x.blockSignals(False)
            sl.setToolTip(lk or tip)
        w.sync = sync

        def apply(db):
            m = cur()
            if snap["m"] is None or snap["written"] != m:          # matrice cambiata altrove: nuova copia
                snap["m"], snap["top"] = m, top_of(m)
            if snap["top"] is None:
                return
            d = float(db) - snap["top"]
            new = [[None if x is None else round(max(MIN_DB, min(MAX_DB, x + d)), 1) for x in row]
                   for row in snap["m"]]
            cue.matrix = new
            snap["written"] = effective_matrix(new, n_src, n_out)
            for x in (sl, sp):
                x.blockSignals(True)
            sl.setValue(int(round(float(db) * 10)))
            sp.setValue(float(db))
            for x in (sl, sp):
                x.blockSignals(False)
            mx.update()
            self._emit()
        sl.valueChanged.connect(lambda val: apply(val / 10.0))
        sp.valueChanged.connect(apply)
        mx.changed.connect(sync)
        sync()
        self.bound.append((sl, sync))
        meter = CueMeter(self.ctrl, cue)
        meter.setObjectName("matrix_fader_meter")
        mid = QHBoxLayout()
        mid.setSpacing(3)
        mid.addStretch(1)
        mid.addWidget(sl)
        mid.addWidget(meter)
        mid.addStretch(1)
        col.addWidget(t)
        col.addLayout(mid, 1)
        col.addWidget(sp)
        w.meter = meter
        return w

    def _show_in_use(self, states):
        bars = list(self.findChildren(PresetBar))
        pb = getattr(self, "preset_bar", None)
        if pb is not None and pb not in bars:
            bars.append(pb)
        for bar in bars:
            try:
                if bar.cue is not self.cue:
                    continue
                if bar.kind == "all":
                    bar.set_in_use(states)
                elif bar.kind in states:
                    bar.set_in_use(states[bar.kind])
            except RuntimeError:
                pass

    def _preset_bar(self, kind="all"):
        """Barra dei preset della cue: «all» = audio e video insieme (accanto ai tab)."""
        from .presets import CuePresetBar
        bar = CuePresetBar(self.show_fn, self.cue) if kind == "all" else PresetBar(kind, self.show_fn, self.cue)

        def applied():
            self.refresh_fields()
            self._emit()
            if self.timeline is not None:
                self.timeline.update()
        bar.applied.connect(applied)
        bar.listChanged.connect(lambda: self._emit())   # aggiorna anche gli elenchi dei punti preset
        return bar

    def _tab_preset(self):
        cue = self.cue
        w = QWidget()
        f = QFormLayout(w)
        # cambiando target, gli elenchi dei preset si ricaricano (sono quelli della cue target)
        f.addRow(tr("Cue target (in esecuzione)"),
                 self._target_combo(on_change=lambda: QTimer.singleShot(0, self.rebuild)))
        target = self.show_fn().resolve_target(cue.target)
        from ..model import PRESET_KINDS_FOR
        kinds = PRESET_KINDS_FOR.get(target.type, ()) if target is not None else ()
        names = sorted({n for k in kinds for n in target.preset_dict(k)}, key=str.lower) if kinds else []
        cb = QComboBox()
        cb.addItem(tr("(nessuno)"), "")
        for n in names:
            cb.addItem(n, n)
        cur_a, cur_v = cue.preset_audio or "", cue.preset_visual or ""
        cur = cur_a or cur_v
        if cur_a and cur_v and cur_a != cur_v:      # show di versioni precedenti: due preset diversi
            cb.addItem(tr("{0} (audio) + {1} (video)", cur_a, cur_v), "\0legacy")
            i = cb.count() - 1
        else:
            i = cb.findData(cur)
            if i < 0 and cur:
                cb.addItem(tr("{0} (non presente nella cue target)", cur), cur)
                i = cb.count() - 1
        cb.setCurrentIndex(max(0, i))
        cb.setEnabled(bool(kinds))

        def chosen(i):
            n = cb.itemData(i) or ""
            if n == "\0legacy":
                return
            # il nome vale per ogni tipo di valori che il preset ha nella cue target
            has = lambda k: target is not None and k in kinds and n in target.preset_dict(k)
            cue.preset_audio = n if has("audio") else ""
            cue.preset_visual = n if has("visual") else ""
            if n and not (cue.preset_audio or cue.preset_visual):
                cue.preset_audio = n if "audio" in kinds else ""
                cue.preset_visual = n if "visual" in kinds else ""
            self._emit()
        cb.currentIndexChanged.connect(chosen)
        f.addRow(tr("Preset"), cb)
        if target is None:
            msg = tr("Scegli la cue target: l'elenco mostrerà i suoi preset.")
        elif not names:
            msg = tr("La cue «{0}» non ha ancora preset: creali con la barra «Preset» accanto ai suoi tab.", target.label())
        else:
            msg = ""
        if msg:
            ml = QLabel(msg)
            ml.setWordWrap(True)
            ml.setStyleSheet("color:#e0b040;")
            f.addRow(ml)
        f.addRow(tr("Durata transizione"), self._dspin("fade_duration", 0, 3600, 0.1, 2))
        f.addRow(tr("Forma"), self._combo("fade_shape", FADE_SHAPES, FADE_SHAPE_LABELS))
        hint = QLabel(tr("Porta gradualmente la cue target verso i suoi preset scelti: volume, matrice e volumi "
                      "delle uscite (audio e video) e crop, spostamento, scala, prospettiva, opacità (video e "
                      "immagini). La corsia cambiata smette di seguire i keyframe della cue. I valori cambiano "
                      "solo per l'esecuzione in corso: la cue salvata non viene modificata."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888; font-size:10px;")
        f.addRow(hint)
        return w

    def _warp_spin(self, i, j):
        """Campo per un angolo della deformazione prospettica (cue.warp[i][j])."""
        w = QSpinBox()
        w.setRange(-16384, 16384)
        w.setSuffix(" px")
        w.setKeyboardTracking(False)
        cue = self.cue

        def get():
            try:
                return int(cue.warp[i][j])
            except (IndexError, TypeError):
                return 0

        def changed(v):
            warp = [list(x) for x in (cue.warp or [[0, 0]] * 4)]
            while len(warp) < 4:
                warp.append([0, 0])
            warp[i][j] = int(v)
            cue.warp = warp
            self._emit()
        w.setValue(get())
        w.valueChanged.connect(changed)
        self.bound.append((w, lambda: w.setValue(get())))
        return w

    def _file_row(self, kinds):
        """File della cue con il percorso (modificabile), «Sfoglia…» e «Apri cartella»."""
        row = QHBoxLayout()
        cue = self.cue
        row.addWidget(QLabel(tr("File")))
        le = QLineEdit(cue.file)
        le.setPlaceholderText(tr("percorso del file (scrivilo o usa «Sfoglia…»)"))
        le.setToolTip(cue.file or "")
        self.file_edit = le
        b = QPushButton(tr("Sfoglia…"))
        of = QPushButton(tr("Apri cartella"))
        of.setToolTip(tr("Apre la cartella del file nel gestore dei file del sistema"))

        def browse():
            exts = set()
            for k in kinds:
                exts |= MEDIA_EXT[k]
            pat = " ".join("*" + e for e in sorted(exts))
            start = os.path.dirname(cue.file) if cue.file else os.path.expanduser("~")
            path, _ = QFileDialog.getOpenFileName(self, tr("Scegli file"), start, tr("Media ({0});;Tutti (*)", pat))
            if path:
                self._set_file(path, reset=True)

        def typed():
            path = os.path.expanduser(le.text().strip())
            if path != cue.file:
                # percorso corretto a mano (es. file spostato): IN, OUT e loop restano quelli di prima
                self._set_file(path, reset=False)

        def open_folder():
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            d = os.path.dirname(cue.file) if cue.file else ""
            if d and os.path.isdir(d):
                QDesktopServices.openUrl(QUrl.fromLocalFile(d))
        b.clicked.connect(browse)
        le.editingFinished.connect(typed)
        of.clicked.connect(open_folder)
        of.setEnabled(bool(cue.file) and os.path.isdir(os.path.dirname(cue.file)))
        row.addWidget(le, 1)
        row.addWidget(b)
        row.addWidget(of)
        return row

    def _set_file(self, path, reset):
        cue = self.cue
        cue.file = path
        if path and not cue.name:
            cue.name = os.path.splitext(os.path.basename(path))[0]
        if reset:
            cue.start = cue.end = cue.loop_start = cue.loop_end = 0.0
            cue.curve = []
        self._emit(True)
        QTimer.singleShot(0, self.rebuild)

    def _update_file_state(self):
        lab = getattr(self, "file_state", None)
        cue = self.cue
        if lab is None or cue is None:
            return
        try:
            if not cue.file:
                lab.setText(tr("Nessun file: usa «Sfoglia…» o trascina un file nella lista"))
            elif not os.path.exists(cue.file):
                lab.setText(tr("⚠ File non trovato"))
            else:
                lab.setText("")
            lab.setVisible(bool(lab.text()))
            if self.file_edit is not None:
                self.file_edit.setStyleSheet("" if cue.file and os.path.exists(cue.file) else "color:#e66;")
        except RuntimeError:
            self.file_state = None

    def _tab_media_time(self):
        cue = self.cue
        w = QWidget()
        v = QVBoxLayout(w)
        self.info_label = QLabel()
        self.info_label.setStyleSheet("color:#999;")
        v.addWidget(self.info_label)
        self.timeline = TimelineEditor()
        self.timeline.setFixedHeight(114)
        self.timeline.changed.connect(self._timeline_changed)
        v.addWidget(self.timeline)
        v.addWidget(self._timeline_scrollbar(self.timeline))
        if cue.type == "video":
            hint = QLabel(tr("Trascina IN/OUT e i marcatori del loop · quadratini arancioni = fade in e fade out · linea "
                             "arancione = {0} · clic o trascina = cursore · tasto destro: marker e keyframe · M = marker al "
                             "cursore · doppio clic o Ctrl+clic: {1} · rotella: zoom · Maiusc+rotella o tasto "
                             "centrale: scorri", tr("opacità (trasparenza)"), tr("punto di opacità (Maiusc: di volume)")))
        else:
            hint = QLabel(tr("Trascina IN/OUT e i marcatori del loop · linea rosa = volume (con i fade; trascina un tratto "
                             "per alzarlo o abbassarlo con i suoi due punti) · quadratini = fade in e fade out · clic o "
                             "trascina = cursore · tasto destro: marker e keyframe · M = marker al cursore · doppio clic "
                             "o Ctrl+clic: punto di volume · rotella: zoom · Maiusc+rotella o tasto centrale: scorri"))
        hint.setStyleSheet("color:#888; font-size:10px;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        # ---- timeline dei keyframe con navigazione tra i marcatori
        info = self.loader.get_info(cue.file) if cue.file else None
        kbar = None
        self._waiting_src = bool(cue.file) and info is None
        if cue.type == "video" and self.crop_panel is not None:
            # video: niente timeline dei keyframe qui (è nella scheda Video / Immagine), il suo cursore si
            # vede sulle miniature
            kbar = self.crop_panel.kbar
            kbar.timeChanged.connect(lambda t: self.timeline.set_cursor(t) if self.timeline else None)
            kbar.edited.connect(lambda: self.timeline.update() if self.timeline else None)
            self.audio_kbar = kbar
        elif info is not None and float(info.get("duration") or 0) > 0:
            # audio: la timeline dei keyframe resta nascosta (tiene il cursore e i preset in uso); marker,
            # keyframe e cursore si gestiscono direttamente sulla forma d'onda
            from ..ui_keyframes import KeyframeBar
            dur = float(info.get("duration") or 0.0)
            kbar = KeyframeBar(cue, duration_fn=lambda: dur, in_out=True, parent=w)
            kbar.hide()
            kbar.timeChanged.connect(lambda t: self.timeline.set_cursor(t) if self.timeline else None)
            kbar.presetInUse.connect(self._show_in_use)
            kbar.edited.connect(lambda: self.timeline.update() if self.timeline else None)
            self.audio_kbar = kbar
        if kbar is not None:
            def moved(ft, kbar=kbar):
                kbar.goto_time(self.timeline._cue_time_of(ft) if self.timeline else ft)
            self.timeline.cursorMoved.connect(moved)
        top = QHBoxLayout()
        top.addWidget(QLabel("IN"))
        in_sp = self._dspin("start", 0, 36000, 0.1, 3)
        top.addWidget(in_sp)
        top.addWidget(QLabel("OUT"))
        out_sp = self._dspin("end", 0, 36000, 0.1, 3, special=tr("fine file"))
        top.addWidget(out_sp)
        for x in (in_sp, out_sp):                # il loop è mostrato rispetto a IN e OUT: si aggiorna con loro
            x.valueChanged.connect(lambda _v: getattr(self, "_loop_sync", lambda: None)())
        top.addWidget(QLabel(tr("Volume")))
        top.addWidget(self._cursor_value_spin("volume"))
        top.addWidget(QLabel(tr("Fade in")))
        top.addWidget(self._dspin("fade_in", 0, 3600, 0.1, 2))
        top.addWidget(QLabel(tr("Fade out")))
        top.addWidget(self._dspin("fade_out", 0, 3600, 0.1, 2))
        if cue.type == "video":
            top.addWidget(QLabel(tr("Opacità")))
            top.addWidget(self._cursor_value_spin("opacity"))
        top.addSpacing(10)
        row = top                       # loop sulla stessa riga di IN, OUT, volume e fade
        lc = self._check("loop", tr("Loop"))
        grp = self.show_fn().by_id(cue.group) if cue.group else None
        if grp is not None and grp.type == "group" and grp.group_mode in ("playlist", "rplaylist"):
            lc.setEnabled(False)          # nella playlist ogni cue suona una volta
            lc.setToolTip(tr("Questa cue è in una Playlist: suona una volta sola. Per ripetere usa il loop del gruppo."))
            lc.setText(tr("Loop (ignorato nella Playlist)"))
        row.addWidget(lc)
        ls_sp, le_sp = self._loop_rel_spins()
        row.addWidget(QLabel(tr("da IN +")))
        row.addWidget(ls_sp)
        row.addWidget(QLabel(tr("a OUT −")))
        row.addWidget(le_sp)
        row.addWidget(QLabel(tr("ripetizioni")))
        row.addWidget(self._ispin("loop_count", 0, 100000, special="∞"))
        lsc = self._check("loop_stop", tr("Ferma alla fine"))
        lsc.setToolTip(tr("Ferma la cue alla fine delle ripetizioni: dopo l'ultimo giro si ferma a fine loop invece "
                          "di proseguire fino a OUT. Una cue «Esci dal loop» la fa comunque proseguire fino a OUT."))
        row.addWidget(lsc)
        if cue.type in ("audio", "video"):
            row.addWidget(QLabel(tr("crossfade")))
            xs = self._dspin("loop_xfade", 0, 60, 0.05, 2)
            xs.setSpecialValueText(tr("no"))
            xs.setToolTip(tr("Crossfade del loop: quando il cursore arriva a fine loop − crossfade parte l'inizio "
                             "del loop mentre la coda sfuma (ogni giro si accorcia di questo tempo). Vale anche "
                             "mentre la cue suona."))
            row.addWidget(xs)
        if cue.type == "video":
            pc = self._check("preload", tr("Precarica"))
            pc.setToolTip(tr("Tiene il video pronto in anticipo (file aperto, fermo sul primo fotogramma) quando la "
                          "cue è vicina al playhead o nella pagina del GRID: parte subito. Nei loop un secondo "
                          "lettore aspetta all'inizio del loop, così la ripetizione non ha attese. Toglilo per "
                          "risparmiare memoria con molti video grandi."))
            row.addWidget(pc)
        row.addStretch(1)
        # strumenti del grafico: sulla riga dei marker, così la riga dei tempi resta intera
        from PySide6.QtWidgets import QToolButton
        row = QHBoxLayout()
        row.setSpacing(3)
        if cue.type == "video":
            # doppio clic / Ctrl+clic sul grafico: punto di volume o di opacità
            from PySide6.QtWidgets import QButtonGroup
            grp_pts = QButtonGroup(w)
            for mode, text, tip in (("volume", tr("∿ Volume"), tr("Doppio clic o Ctrl+clic sul grafico: punto di volume")),
                                    ("opacity", tr("◐ Opacità"), tr("Doppio clic o Ctrl+clic sul grafico: punto di opacità"))):
                b = QToolButton()
                b.setText(text)
                b.setToolTip(tip)
                b.setCheckable(True)
                b.setChecked(mode == "opacity")      # video: di serie punti di opacità
                b.setProperty("mode", mode)
                col = "#ff8cdc" if mode == "volume" else "#ffa528"        # colori delle curve sul grafico
                b.setStyleSheet(f"QToolButton:checked{{background:{col};color:#111;font-weight:bold;"
                                f"border:1px solid {col};border-radius:3px;}}")
                grp_pts.addButton(b)
                row.addWidget(b)
            grp_pts.setExclusive(True)
            grp_pts.buttonClicked.connect(lambda b: self.timeline.set_point_mode(b.property("mode")))
            self._pts_group = grp_pts
        from PySide6.QtCore import QSettings
        qs = QSettings("CueStage", "CueStage")
        sn = QToolButton()
        sn.setText(tr("Snap"))
        sn.setCheckable(True)
        sn.setChecked(str(qs.value("timeline_snap", "true")).lower() != "false")
        sn.setStyleSheet("QToolButton:checked{background:#3478d0;color:white;font-weight:bold;"
                         "border:1px solid #3478d0;border-radius:3px;}")
        sn.setToolTip(tr("Trascinando un punto, un marker o un keyframe si aggancia ai punti delle altre curve, ai "
                         "marker e a IN/OUT vicini (tieni premuto Alt per non agganciare)"))
        self.timeline.set_snap(sn.isChecked())

        def snap_toggled(on):
            self.timeline.set_snap(on)
            QSettings("CueStage", "CueStage").setValue("timeline_snap", "true" if on else "false")
        sn.toggled.connect(snap_toggled)
        row.addWidget(sn)
        bc = QToolButton()
        bc.setText(tr("✕ curva volume"))
        bc.setToolTip(tr("Cancella curva volume"))
        bc.clicked.connect(lambda: (cue.curve.clear(), self._timeline_changed()))
        if cue.type == "video":
            bo = QToolButton()
            bo.setText(tr("✕ curva opacità"))
            bo.setToolTip(tr("Cancella curva di opacità"))
            bo.clicked.connect(lambda: (setattr(cue, "op_curve", []), self._timeline_changed()))
            row.addWidget(bo)
        bz = QToolButton()
        bz.setText("⤢")
        bz.setToolTip(tr("Zoom tutto"))
        bz.clicked.connect(lambda: self.timeline.zoom_all())
        row.addWidget(bc)
        row.addWidget(bz)
        v.addLayout(top)
        if cue.type == "video":
            v.addLayout(self._video_sep_row())
        # ---- elenco dei marker: nome, tempo, note, azione
        from ..ui_markers import MarkersPanel
        mp = MarkersPanel(cue, self.show_fn)
        self.markers_panel = mp

        def panel_changed():
            self.show_fn().dirty = True
            self.edited.emit(False)
            if self.timeline is not None:
                self.timeline.update()
        mp.changed.connect(panel_changed)

        def goto(t):
            if self.timeline is not None:
                self.timeline.cursor_t = t
                self.timeline.cursor_used = True
                self.timeline.update()
                self.timeline.cursorMoved.emit(t)
        mp.gotoRequested.connect(goto)

        def add_here():
            tl = self.timeline
            if tl is not None:
                tl.add_marker(tl.cursor_t if tl.cursor_t is not None else float(cue.start or 0))
        mp.addRequested.connect(add_here)
        self.timeline.changed.connect(mp.reload)
        mp.head.addLayout(row)
        v.addWidget(mp)
        v.addStretch(1)
        self._media_info = info
        self._load_media()
        if kbar is not None:
            self.timeline.set_cursor(kbar.file_time())
            QTimer.singleShot(0, kbar.update_in_use)
        return w

    def _tab_audio(self):
        """Scheda audio della cue, ducking e matrice dei canali (al tempo del cursore della timeline dei keyframe)."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(self._audio_out_section(self.audio_kbar, getattr(self, "_media_info", None)))
        v.addStretch(1)
        return w

    def _tab_image_time(self):
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(tr("Durata"), self._dspin("duration", 0, 360000, 1, 1, special=tr("fino allo stop")))
        f.addRow(tr("Fade in"), self._dspin("fade_in", 0, 3600, 0.1, 2))
        f.addRow(tr("Fade out (prima della fine)"), self._dspin("fade_out", 0, 3600, 0.1, 2))
        return w

    def _size_row(self):
        """Dimensione dell'immagine in uscita (% rispetto all'adattamento): cursore, valore, −, +, 100%."""
        from PySide6.QtWidgets import QSlider
        cue = self.cue
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        sl = QSlider(Qt.Horizontal)
        sl.setRange(10, 400)
        sl.setMinimumWidth(120)
        sl.setToolTip(tr("Ingrandisce o rimpicciolisce l'immagine in uscita (anche con la rotella sull'anteprima "
                         "dell'uscita)"))
        sp = self._dspin("scale", 1, 1000, 1, 1, " %")

        def sync_slider():
            sl.blockSignals(True)
            sl.setValue(int(round(max(10.0, min(400.0, float(cue.scale or 100))))))
            sl.blockSignals(False)
        sync_slider()
        sl.valueChanged.connect(lambda val: sp.setValue(float(val)))
        sp.valueChanged.connect(lambda _v: sync_slider())
        self.bound.append((sl, sync_slider))

        def step(f):
            sp.setValue(round(max(1.0, min(1000.0, float(cue.scale or 100) * f)), 1))
        bm = QPushButton("−")
        bm.setFixedWidth(28)
        bm.setToolTip(tr("Rimpicciolisci (−10%)"))
        bm.clicked.connect(lambda: step(1 / 1.1))
        bp = QPushButton("+")
        bp.setFixedWidth(28)
        bp.setToolTip(tr("Ingrandisci (+10%)"))
        bp.clicked.connect(lambda: step(1.1))
        b1 = QPushButton("100%")
        b1.setToolTip(tr("Dimensione originale dell'adattamento"))
        b1.clicked.connect(lambda: sp.setValue(100.0))
        h.addWidget(bm)
        h.addWidget(sl, 1)
        h.addWidget(bp)
        h.addWidget(sp)
        h.addWidget(b1)
        return w

    def _video_sep_row(self):
        """Video: fade e crossfade del loop dell'immagine, di serie uguali a quelli dell'audio."""
        cue = self.cue
        h = QHBoxLayout()
        lab = QLabel(tr("Immagine:"))
        lab.setStyleSheet("color:#ffa528; font-weight:bold;")
        h.addWidget(lab)
        pairs = []

        def group(link_attr, link_text, fields):
            ck = QCheckBox(link_text)
            ck.setChecked(bool(getattr(cue, link_attr, True)))
            spins = []
            for label, attr, audio_attr in fields:
                h.addWidget(QLabel(label))
                sp = QDoubleSpinBox()
                sp.setRange(0, 3600)
                sp.setDecimals(2)
                sp.setSingleStep(0.1)
                sp.setSuffix(" s")
                sp.setKeyboardTracking(False)
                spins.append((sp, attr, audio_attr))
                h.addWidget(sp)

            def sync():
                linked = bool(getattr(cue, link_attr, True))
                ck.blockSignals(True)
                ck.setChecked(linked)
                ck.blockSignals(False)
                for sp, attr, audio_attr in spins:
                    sp.blockSignals(True)
                    sp.setValue(float(getattr(cue, audio_attr if linked else attr) or 0))
                    sp.setEnabled(not linked)
                    sp.blockSignals(False)

            def toggled(on):
                if not on:                         # si separa: si parte dai valori dell'audio
                    for _sp, attr, audio_attr in spins:
                        setattr(cue, attr, float(getattr(cue, audio_attr) or 0))
                setattr(cue, link_attr, bool(on))
                self._emit()
                if self.timeline:
                    self.timeline.update()
                sync()
            ck.toggled.connect(toggled)
            for sp, attr, _a in spins:
                sp.valueChanged.connect(lambda val, attr=attr: (setattr(cue, attr, float(val)), self._emit(),
                                                               self.timeline.update() if self.timeline else None))
            h.insertWidget(h.count() - 2 * len(fields), ck)
            sync()
            self.bound.append((ck, sync))
            pairs.append(sync)
            return ck
        ck = group("fade_link", tr("fade uguali all'audio"),
                   [(tr("Fade in"), "v_fade_in", "fade_in"), (tr("Fade out"), "v_fade_out", "fade_out")])
        ck.setToolTip(tr("Fade in e fade out dell'immagine uguali a quelli dell'audio. Togli la spunta per "
                         "regolarli a parte (anche con i quadratini arancioni del grafico)."))
        h.addSpacing(12)
        ck2 = group("loop_xfade_link", tr("crossfade del loop uguale all'audio"),
                    [(tr("crossfade"), "loop_xfade_video", "loop_xfade")])
        ck2.setToolTip(tr("Crossfade dell'immagine a fine loop: quando il cursore arriva a fine loop − crossfade "
                          "l'inizio del loop entra in dissolvenza sopra la coda. Richiede «Precarica»."))
        h.addStretch(1)
        # il collegamento si aggiorna quando cambiano i valori dell'audio
        self._video_sep_syncs = pairs
        return h

    def _timeline_scrollbar(self, tl):
        """Barra di scorrimento sotto il grafico: compare quando il grafico è ingrandito (rotella)."""
        from PySide6.QtWidgets import QScrollBar
        sb = QScrollBar(Qt.Horizontal)
        sb.setObjectName("timeline_scroll")
        sb.setFixedHeight(12)
        sb.setVisible(False)
        busy = [False]

        def sync():
            dur = max(float(tl.duration or 0), 1.0)
            span = tl.v1 - tl.v0
            zoomed = span < dur - 1e-6
            busy[0] = True
            sb.setRange(0, max(0, int(round((dur - span) * 1000))))
            sb.setPageStep(max(1, int(round(span * 1000))))
            sb.setSingleStep(max(1, int(round(span * 100))))
            sb.setValue(int(round(tl.v0 * 1000)))
            busy[0] = False
            sb.setVisible(zoomed)

        def moved(val):
            if not busy[0]:
                tl.set_view(val / 1000.0)
        tl.viewChanged.connect(sync)
        sb.valueChanged.connect(moved)
        sync()
        return sb

    def _loop_rel_spins(self):
        """Inizio e fine del loop come distanza da IN e da OUT (0 = il loop parte da IN / finisce a OUT).
        Nella cue restano tempi del file (0 = IN / OUT), come i marcatori del grafico."""
        cue = self.cue

        def dur():
            tl = self.timeline
            return float(tl.duration) if tl is not None and tl.duration else 0.0

        def out_t():
            return float(cue.end) if cue.end and cue.end > 0 else dur()

        sps = []
        for kind in ("start", "end"):
            sp = QDoubleSpinBox()
            sp.setRange(0, 36000)
            sp.setDecimals(3)
            sp.setSingleStep(0.1)
            sp.setSuffix(" s")
            sp.setKeyboardTracking(False)
            sp.setSpecialValueText("IN" if kind == "start" else "OUT")
            sp.setToolTip(tr("Inizio del loop: secondi dopo IN") if kind == "start"
                          else tr("Fine del loop: secondi prima di OUT"))
            sps.append(sp)
        ls_sp, le_sp = sps

        def sync():
            for x in sps:
                x.blockSignals(True)
            ls_sp.setValue(max(0.0, float(cue.loop_start) - float(cue.start or 0)) if cue.loop_start > 0 else 0.0)
            o = out_t()
            le_sp.setValue(max(0.0, o - float(cue.loop_end)) if cue.loop_end > 0 and o > 0 else 0.0)
            for x in sps:
                x.blockSignals(False)

        def set_start(v):
            cue.loop_start = 0.0 if v <= 0 else round(float(cue.start or 0) + float(v), 3)
            done()

        def set_end(v):
            o = out_t()
            cue.loop_end = 0.0 if v <= 0 or o <= 0 else round(max(float(cue.start or 0) + 0.05, o - float(v)), 3)
            done()

        def done():
            self._emit()
            if self.timeline:
                self.timeline.update()
            sync()
        ls_sp.valueChanged.connect(set_start)
        le_sp.valueChanged.connect(set_end)
        sync()
        self.bound.append((ls_sp, sync))
        if self.timeline is not None:
            self.timeline.changed.connect(sync)
        self._loop_sync = sync
        return ls_sp, le_sp

    def _cursor_value_spin(self, kind):
        """Volume (dB) o opacità (%) nella riga dei tempi. Senza curva è il valore della cue; con la curva dei
        punti mostra il valore al cursore del grafico e modificarlo sposta (o crea) il punto al cursore."""
        import numpy as np
        cue = self.cue
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)
        sp = QDoubleSpinBox()
        sp.setKeyboardTracking(False)
        if kind == "volume":
            sp.setRange(MIN_DB, MAX_DB)
            sp.setDecimals(1)
            sp.setSingleStep(0.5)
            sp.setSuffix(" dB")
            sp.setSpecialValueText(tr("-∞ dB"))
        else:
            sp.setRange(0, 100)
            sp.setDecimals(1)
            sp.setSingleStep(1)
            sp.setSuffix(" %")
        lab = QLabel()
        lab.setStyleSheet("color:#ffdc78; font-size:10px;")

        def pts():
            return (cue.curve if kind == "volume" else cue.op_curve) or []

        def cursor():
            """Durante il play la posizione in riproduzione, altrimenti il cursore del grafico."""
            tl = self.timeline
            if tl is not None and tl.playhead is not None:
                return float(tl.playhead)
            t = tl.cursor_t if tl is not None and tl.cursor_t is not None else None
            return float(cue.start or 0) if t is None else float(t)

        def fade_db():
            """Attenuazione dei fade al cursore (dB, ≤ 0), come la linea rosa; 0 dentro i fade profondi."""
            tl = self.timeline
            if kind != "volume" or tl is None or not tl.duration:
                return 0.0
            g = tl.fade_gain(cursor())
            return 20.0 * np.log10(g) if 0.05 < g < 0.9999 else 0.0

        def in_fade():
            tl = self.timeline
            return kind == "volume" and tl is not None and bool(tl.duration) and tl.fade_gain(cursor()) < 0.9999

        def value():
            p_ = sorted(pts())
            tl = self.timeline
            if kind == "volume" and tl is not None and tl.duration:
                return tl.vol_db_at(cursor())           # come la linea rosa al cursore: curva e fade
            if kind == "volume":
                lvl = float(cue.level_db)
                if not p_:
                    return lvl
                c = float(np.interp(cursor(), [a for a, _b in p_], [b for _a, b in p_]))
                return MIN_DB if c <= MIN_DB + 0.01 or lvl <= MIN_DB else max(MIN_DB, min(MAX_DB, lvl + c))
            if not p_:
                return float(cue.opacity) * 100
            return max(0.0, min(1.0, float(np.interp(cursor(), [a for a, _b in p_], [b for _a, b in p_])))) * 100

        def sync():
            if sp.hasFocus() and self.timeline is not None and self.timeline.playhead is not None:
                return                                   # si sta scrivendo nel campo: non lo si cambia sotto
            sp.blockSignals(True)
            sp.setValue(value())
            sp.blockSignals(False)
            from ..media import fmt_time
            if pts() or in_fade():
                lab.setText(tr("@ {0}", fmt_time(cursor())))
                sp.setToolTip(tr("Valore della curva al cursore ({0}): modificandolo si sposta o si crea il punto al "
                                 "cursore", fmt_time(cursor())))
            else:
                lab.setText("")
                sp.setToolTip(tr("Volume della cue") if kind == "volume" else tr("Opacità della cue"))
            lab.setVisible(bool(pts()) or in_fade())

        def changed(v):
            p_ = pts()
            if kind == "volume" and v > MIN_DB + 0.01:
                v = max(MIN_DB, min(MAX_DB, v - fade_db()))   # il valore mostrato comprende il fade
            if p_:
                t = round(cursor(), 3)
                if kind == "volume":
                    val = MIN_DB if v <= MIN_DB + 0.01 else round(v - float(cue.level_db), 1)
                else:
                    val = round(v / 100.0, 3)
                tol = 0.05
                tl = self.timeline
                if tl is not None and tl.width() > 0:
                    tol = max(tol, 4 * (tl.v1 - tl.v0) / tl.width())      # 4 pixel del grafico
                near = [i for i, (a, _b) in enumerate(p_) if abs(float(a) - t) <= tol]
                if near:
                    i = min(near, key=lambda i: abs(float(p_[i][0]) - t))
                    p_[i] = [p_[i][0], val]
                else:
                    p_.append([t, val])
                    p_.sort()
            elif kind == "volume":
                cue.level_db = float(v)
            else:
                cue.opacity = v / 100.0
            self._emit()
            sync()
            if self.timeline is not None:
                self.timeline.update()
        sp.valueChanged.connect(changed)
        sync()
        self.bound.append((sp, sync))
        if getattr(self, "_ph_syncs_cue", None) is not cue:
            self._ph_syncs, self._ph_syncs_cue = {}, cue
        self._ph_syncs[kind] = sync                    # l'ultimo campo costruito (i vecchi sono distrutti)
        if self.timeline is not None:
            self.timeline.cursorMoved.connect(lambda _t: sync())
            self.timeline.changed.connect(sync)
        h.addWidget(sp)
        h.addWidget(lab)
        return w

    def _opacity_spin(self):
        cue = self.cue
        op = QDoubleSpinBox()
        op.setRange(0, 100)
        op.setSuffix(" %")
        op.setKeyboardTracking(False)
        op.setValue(float(cue.opacity) * 100)

        def changed(val):
            cue.opacity = val / 100.0
            self._emit()
            if self.timeline is not None:
                self.timeline.update()
        op.valueChanged.connect(changed)
        self.bound.append((op, lambda: op.setValue(float(cue.opacity) * 100)))
        return op

    def _tab_visual(self):
        cue = self.cue
        outer = QWidget()
        h = QHBoxLayout(outer)
        h.setContentsMargins(0, 0, 0, 0)
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(tr("Dimensione"), self._size_row())
        f.addRow(tr("Layer (più alto = sopra)"), self._ispin("layer", 0, 99))
        f.addRow(tr("Opacità"), self._opacity_spin())
        f.addRow(tr("Adattamento"), self._combo("fit", FIT_MODES, FIT_LABELS))

        # --- crop in pixel del sorgente
        crop = QGridLayout()
        for i, (lab, attr) in enumerate((("sx", "crop_l"), ("dx", "crop_r"),
                                         ("alto", "crop_t"), ("basso", "crop_b"))):
            crop.addWidget(QLabel(lab), i // 2, (i % 2) * 2)
            crop.addWidget(self._ispin(attr, 0, 16384, suffix=" px"), i // 2, (i % 2) * 2 + 1)
        self.src_label = QLabel()
        self.src_label.setStyleSheet("color:#888;")
        crop.addWidget(self.src_label, 2, 0, 1, 4)
        f.addRow(tr("Crop (px sorgente)"), crop)

        # --- spostamento in pixel dell'uscita e scala
        pos = QGridLayout()
        pos.addWidget(QLabel("X"), 0, 0)
        pos.addWidget(self._ispin("off_x", -16384, 16384, suffix=" px"), 0, 1)
        pos.addWidget(QLabel("Y"), 0, 2)
        pos.addWidget(self._ispin("off_y", -16384, 16384, suffix=" px"), 0, 3)
        self.out_label = QLabel()
        self.out_label.setStyleSheet("color:#888;")
        pos.addWidget(self.out_label, 1, 0, 1, 4)
        f.addRow(tr("Spostamento (px uscita)"), pos)
        self._update_px_labels()

        # --- deformazione prospettica: spostamento dei 4 angoli in pixel d'uscita
        warp = QGridLayout()
        for i, lab in enumerate((tr("↖ alto sx"), tr("↗ alto dx"), tr("↘ basso dx"), tr("↙ basso sx"))):
            warp.addWidget(QLabel(lab), i, 0)
            for j, ax in enumerate(("x", "y")):
                warp.addWidget(QLabel(ax), i, 1 + j * 2)
                warp.addWidget(self._warp_spin(i, j), i, 2 + j * 2)
        f.addRow(tr("Prospettiva (px uscita)"), warp)

        reset = QPushButton(tr("Reimposta crop, posizione, dimensione, deformazione e prospettiva"))

        def do_reset():
            for a in ("crop_l", "crop_t", "crop_r", "crop_b", "off_x", "off_y"):
                setattr(cue, a, 0)
            cue.scale = cue.scale_x = cue.scale_y = 100.0     # dimensione e deformazione
            cue.warp = [[0, 0], [0, 0], [0, 0], [0, 0]]
            self.refresh_fields()
            self._emit()
        reset.clicked.connect(do_reset)
        hint = QLabel(tr("Spostamento: 0,0 = centrato secondo l'adattamento. Sorgente · crop: trascina i quadratini = "
                         "dimensione, Alt+trascina = proporzioni (deformazione), Ctrl+trascina = crop, "
                         "Maiusc = simmetrico, rotella = dimensione. Anteprima dell'uscita: trascina = sposta, "
                         "rotella = dimensione. Le modifiche valgono anche durante la riproduzione."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888; font-size:10px;")
        f.addRow(reset)
        f.addRow(hint)
        left = self._scrolled(w)   # scorre solo la colonna dei campi
        left.setMinimumWidth(w.sizeHint().width() + 20)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        h.addWidget(left, 0)

        # --- editor visivo del crop (fermo immagine con timeline per i video)
        self.crop_panel = CropPanel(cue, self.loader, self.ctrl.image, self.ctrl.comp,
                                    lambda: self.show_fn().settings)
        self.crop_panel.setMinimumWidth(560)

        def crop_dragged():
            self.refresh_fields()
            self._emit()
        self.crop_panel.changed.connect(crop_dragged)
        self.crop_panel.kbar.presetInUse.connect(self._show_in_use)
        QTimer.singleShot(0, self.crop_panel.kbar.update_in_use)
        h.addWidget(self.crop_panel, 1)
        return outer

    def _update_px_labels(self):
        """Mostra la risoluzione del sorgente e dell'uscita accanto ai campi in pixel."""
        cue = self.cue
        if cue is None or not cue.is_visual() or getattr(self, "src_label", None) is None:
            return
        try:
            wh = self.size_fn(cue) if self.size_fn else None
            self.src_label.setText(tr("sorgente {0}×{1}", wh[0], wh[1]) if wh else "")
            ow, oh = self.out_size_fn() if self.out_size_fn else (0, 0)
            self.out_label.setText(tr("uscita {0}×{1}", ow, oh) if ow else "")
        except RuntimeError:
            pass

    def _tab_fade(self):
        cue = self.cue
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(tr("Cue target"), self._target_combo())
        f.addRow(tr("Durata fade"), self._dspin("fade_duration", 0, 3600, 0.1, 2))
        f.addRow(tr("Forma"), self._combo("fade_shape", FADE_SHAPES, FADE_SHAPE_LABELS))
        row = QHBoxLayout()
        row.addWidget(self._check("fade_level", tr("Volume a")))
        row.addWidget(self._dspin("target_db", MIN_DB, MAX_DB, 1, 1, " dB", special=tr("-∞ (silenzio)")))
        row.addStretch(1)
        f.addRow(tr("Livello"), row)
        row2 = QHBoxLayout()
        row2.addWidget(self._check("fade_opacity", tr("Opacità a")))
        op = QDoubleSpinBox()
        op.setRange(0, 100)
        op.setSuffix(" %")
        op.setValue(float(cue.target_opacity) * 100)
        op.valueChanged.connect(lambda v: (setattr(cue, "target_opacity", v / 100.0), self._emit()))
        self.bound.append((op, lambda: op.setValue(float(cue.target_opacity) * 100)))
        row2.addWidget(op)
        row2.addStretch(1)
        f.addRow(tr("Video/immagini"), row2)
        f.addRow(self._check("stop_when_done", tr("Ferma il target alla fine del fade (fade out)")))
        f.addRow(self._check("start_if_idle", tr("Se il target non è attivo, avvialo da silenzio (fade in)")))
        pres = QHBoxLayout()
        b1 = QPushButton(tr("Preset: Fade OUT + stop"))
        b2 = QPushButton(tr("Preset: Fade IN"))

        def preset(out):
            cue.fade_level = cue.fade_opacity = True
            if out:
                cue.target_db, cue.target_opacity = MIN_DB, 0.0
                cue.stop_when_done, cue.start_if_idle = True, False
            else:
                cue.target_db, cue.target_opacity = 0.0, 1.0
                cue.stop_when_done, cue.start_if_idle = False, True
            self.refresh_fields()
            self._emit()
        b1.clicked.connect(lambda: preset(True))
        b2.clicked.connect(lambda: preset(False))
        pres.addWidget(b1)
        pres.addWidget(b2)
        pres.addStretch(1)
        f.addRow(pres)
        return w

    # ------------------------------------------------------------ media
    def _load_media(self):
        self._update_file_state()
        if not self.timeline or not self.cue:
            return
        path = self.cue.file
        info = self.loader.get_info(path) if path else None
        peaks = self.loader.get_peaks(path) if path else None
        dur = info["duration"] if info else 0.0
        self.timeline.set_cue(self.cue, dur, peaks)
        if info and info.get("has_video") and self.cue.type == "video":
            self.timeline.set_thumbs(self.loader.get_thumbs(path))
        if self.info_label is not None:
            if not path:
                self.info_label.setText(tr("Nessun file: usa «Sfoglia…» o trascina un file nella lista"))
            elif not os.path.exists(path):
                self.info_label.setText(tr("⚠ File non trovato"))
            elif info:
                extra = tr(" · video {0}×{1}", info['width'], info['height']) if info["has_video"] else ""
                self.info_label.setText(tr("Durata file {0}{1}{2}", fmt_time(dur), extra, '' if info['has_audio'] else tr(' · senza audio')))
            else:
                self.info_label.setText(tr("Analisi del file…"))

    def _media_ready(self, path):
        if self.cue is not None and self.cue.file == path and getattr(self, "_waiting_src", False) \
                and path in self.loader.info:
            self._waiting_src = False
            QTimer.singleShot(0, self.rebuild)
            return
        if self.cue is not None and self.cue.file == path:
            if self.timeline is not None:
                self._load_media()
            self._update_px_labels()

    def _timeline_changed(self):
        self.refresh_fields()
        self._emit()
        if self.timeline:
            self.timeline.update()

    def _update_playhead(self):
        if self.timeline is None or self.cue is None:
            return
        inst = self.ctrl.instances.get(self.cue.id)
        ph = inst.position() if inst is not None else None
        changed = ph != self.timeline.playhead
        self.timeline.set_playhead(ph)
        if changed and getattr(self, "_ph_syncs_cue", None) is self.cue:
            for f in list(getattr(self, "_ph_syncs", {}).values()):   # campi Volume / Opacità: seguono il play
                try:
                    f()
                except RuntimeError:                          # campo già distrutto
                    pass


class CueMeter(QWidget):
    """Vumeter della cue accanto al fader della matrice: una barra per canale del file, con il livello che esce
    (picco × incrocio più alto della matrice × master). Stessa scala del fader (−∞…+12 dB); senza cue in
    riproduzione resta spento."""
    BAR, GAP = 5, 1

    def __init__(self, ctrl, cue, parent=None):
        from PySide6.QtCore import QTimer
        super().__init__(parent)
        self.ctrl, self.cue = ctrl, cue
        self.levels = []
        self.setFixedWidth(2 * self.BAR + self.GAP + 4)
        self.setToolTip(tr("Livello della cue in uscita (per canale del file)"))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)

    def poll(self):
        if not self.isVisible():
            return
        inst = getattr(self.ctrl, "instances", {}).get(self.cue.id) if self.ctrl is not None else None
        v = getattr(inst, "voice", None)
        lv = [float(x) for x in v.meter] if v is not None and len(v.meter) else []
        if lv or self.levels:
            self.levels = lv
            n = max(2, len(lv))
            wd = n * (self.BAR + self.GAP) + 3
            if self.width() != wd:
                self.setFixedWidth(min(wd, 140))
            self.update()

    def paintEvent(self, e):
        import math
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 20, 24))
        h = self.height() - 2
        x = 2
        for v in self.levels:
            db = 20 * math.log10(max(v, 1e-6))
            frac = max(0.0, min(1.0, (db - MIN_DB) / (MAX_DB - MIN_DB)))
            col = QColor(220, 60, 50) if db > -1 else QColor(230, 200, 50) if db > -12 else QColor(70, 200, 100)
            p.fillRect(int(x), int(1 + h * (1 - frac)), self.BAR, int(h * frac), col)
            x += self.BAR + self.GAP
        y0 = int(1 + h * (MAX_DB / (MAX_DB - MIN_DB)))             # riga dello 0 dB, come sul fader
        p.fillRect(0, y0, self.width(), 1, QColor(120, 120, 135))
        p.end()
