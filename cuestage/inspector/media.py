"""Schede dell'ispettore per audio, video e immagini: tempi, uscita audio, crop, preset, fade (parte di Inspector)."""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..media import fmt_time
from ..model import (
    PRESET_LABELS,
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
        sp = self._dspin("duck_db", 0, 60, 1, 1, " dB", special="nessuno")
        sp.setToolTip("Di quanti dB si abbassa questa cue quando l'ingresso del ducking supera la soglia "
                      "(Impostazioni › Ducking…). " + ("Vale per tutte le cue del gruppo che non hanno un "
                                                      "ducking proprio." if cue.type == "group" else
                                                      "Se è «nessuno» vale quello del gruppo che la contiene."))
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
                parts.append(f"gruppo «{g.label()}»: −{g.duck_db:.1f} dB" +
                             (" (vale se qui è «nessuno»)" if float(cue.duck_db or 0) > 0 else " (in uso)"))
                break
            g = sh.by_id(g.group) if g.group else None
        if not cfg["enabled"]:
            parts.append("ducking spento nelle impostazioni")
        note.setText(" · ".join(parts))
        h.addWidget(note, 1)
        b = QPushButton("Impostazioni…")
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
        w = QGroupBox("Uscita audio: scheda e matrice dei canali")
        v = QVBoxLayout(w)
        f = QFormLayout()
        dev = QComboBox()
        dname, dch, _ = eng.resolve(None)
        dev.addItem(f"Predefinita del progetto — {dname} ({dch} canali)", "")
        for d in list_devices():
            dev.addItem(f"{device_label(d)} — {eng.channels_for(d)} canali", d["id"])
        i = dev.findData(cue.audio_device or "")
        if i < 0:
            dev.addItem(f"{cue.audio_device.split('|', 1)[-1]} (non collegata)", cue.audio_device)
            i = dev.count() - 1
        dev.setCurrentIndex(i)

        def dev_changed(i):
            cue.audio_device = dev.itemData(i) or ""
            self._emit()
            QTimer.singleShot(0, self.rebuild)   # la matrice dipende dai canali della scheda
        dev.currentIndexChanged.connect(dev_changed)
        f.addRow("Scheda audio", dev)
        f.addRow("Ducking", self._duck_row())
        engine_route = True
        if cue.type == "video":
            route = QComboBox()
            route.addItem("Motore CueStage (scheda e matrice di questa cue)", "engine")
            route.addItem("Lettore di sistema (scheda predefinita, senza matrice)", "system")
            route.setCurrentIndex(max(0, route.findData(cue.video_audio or "engine")))

            def route_changed(i):
                cue.video_audio = route.itemData(i)
                self._emit()
                QTimer.singleShot(0, self.rebuild)
            route.currentIndexChanged.connect(route_changed)
            f.addRow("Audio del video", route)
            engine_route = cue.video_audio != "system"
        v.addLayout(f)
        name, n_out, found = eng.resolve(cue.audio_device)
        lab = QLabel()
        lab.setWordWrap(True)
        v.addWidget(lab)
        if not cue.file:
            lab.setText("Nessun file.")
        elif info is None or kbar is None:
            lab.setText("Analisi del file…")
        elif not info.get("has_audio"):
            lab.setText("Il file non contiene audio.")
        elif not engine_route:
            lab.setText("Con il lettore di sistema l'audio esce dalla scheda predefinita del sistema, "
                        "in stereo, senza matrice.")
        else:
            n_src = int(info.get("channels") or 2)
            labels = channel_labels(n_src, info.get("layout", ""))
            warn = "" if found else "  ⚠ scheda scelta non collegata: in riproduzione si usa la predefinita."
            lab.setText(f"Sorgente: {n_src} canali ({' '.join(labels)})  →  {name}: {n_out} canali.{warn}")
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
                    f"dissolvenza «{kfm.label(na)}» → «{kfm.label(nb)}» ({f * 100:.0f}%)"
                return (f"A {fmt_time(kbar.cue_time())} vale {what}: sola visualizzazione. Modifica la "
                        f"matrice dove vale «base», oppure aggiorna il preset con la barra dei preset.")

            def show_state(*a):
                st = astate()
                lk = mlocked()
                if st is None:
                    state_lab.setText(f"Nessun keyframe audio: valgono i valori della cue "
                                      f"(volume {cue.level_db:+.1f} dB).")
                    state_lab.setStyleSheet("color:#888;")
                else:
                    state_lab.setText(f"Volume a {fmt_time(kbar.cue_time())}: {st[0]:+.1f} dB"
                                      + (f" — {lk}" if lk else " — valori base: modificabili"))
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
            sa.setMinimumHeight(min(mx.height() + 20, 330))
            v.addWidget(sa)
            row = QHBoxLayout()
            b1 = QPushButton("Routing predefinito")
            b1.setToolTip("Mono → Out 1-2 · stereo → Out 1/2 · multicanale → canale per canale "
                          "(5.1/7.1 su stereo: downmix)")

            def reset():
                cue.matrix, cue.out_levels = [], []
                mx.update()
                self._emit()
            b1.clicked.connect(reset)
            b2 = QPushButton("Volumi uscite a 0 dB")

            def levels0():
                cue.out_levels = []
                mx.update()
                self._emit()
            b2.clicked.connect(levels0)
            row.addWidget(b1)
            row.addWidget(b2)
            row.addStretch(1)
            v.addLayout(row)
            hint = QLabel("Clic su un incrocio: acceso (0 dB) / spento · trascina in verticale o rotella: "
                          "volume (Ctrl+rotella: decimi) · doppio clic: valore esatto · tasto destro: valori "
                          "rapidi. L'ultima riga regola il volume di ogni uscita. Le modifiche valgono anche "
                          "durante la riproduzione.")
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#888; font-size:10px;")
            v.addWidget(hint)
        return w

    def _show_in_use(self, states):
        for bar in self.findChildren(PresetBar):
            try:
                if bar.cue is self.cue and bar.kind in states:
                    bar.set_in_use(states[bar.kind])
            except RuntimeError:
                pass

    def _preset_bar(self, kind):
        bar = PresetBar(kind, self.show_fn, self.cue)

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
        f.addRow("Cue target (in esecuzione)",
                 self._target_combo(on_change=lambda: QTimer.singleShot(0, self.rebuild)))
        target = self.show_fn().resolve_target(cue.target)
        compatible = {"audio": ("audio", "video"), "visual": ("video", "image")}
        for kind, attr in (("audio", "preset_audio"), ("visual", "preset_visual")):
            cb = QComboBox()
            cb.addItem("(non cambiare)", "")
            names = []
            if target is not None and target.type in compatible[kind]:
                names = sorted(target.preset_dict(kind), key=str.lower)
            for n in names:
                cb.addItem(n, n)
            cur = getattr(cue, attr)
            i = cb.findData(cur)
            if i < 0 and cur:
                cb.addItem(f"{cur} (non presente nella cue target)", cur)
                i = cb.count() - 1
            cb.setCurrentIndex(max(0, i))
            cb.setEnabled(target is not None and target.type in compatible[kind])
            cb.currentIndexChanged.connect(
                lambda i, cb=cb, attr=attr: (setattr(cue, attr, cb.itemData(i) or ""), self._emit()))
            f.addRow(PRESET_LABELS[kind], cb)
        if target is None:
            msg = "Scegli la cue target: gli elenchi mostreranno i suoi preset."
        elif not any(target.preset_dict(k) for k in ("audio", "visual")):
            msg = (f"La cue «{target.label()}» non ha ancora preset: creali nelle sue schede "
                   "«Tempi, loop e volume» e «Video / immagine · crop».")
        else:
            msg = ""
        if msg:
            ml = QLabel(msg)
            ml.setWordWrap(True)
            ml.setStyleSheet("color:#e0b040;")
            f.addRow(ml)
        f.addRow("Durata transizione", self._dspin("fade_duration", 0, 3600, 0.1, 2))
        f.addRow("Forma", self._combo("fade_shape", FADE_SHAPES, FADE_SHAPE_LABELS))
        hint = QLabel("Porta gradualmente la cue target verso i suoi preset scelti: volume, matrice e volumi "
                      "delle uscite (audio e video) e crop, spostamento, scala, prospettiva, opacità (video e "
                      "immagini). La corsia cambiata smette di seguire i keyframe della cue. I valori cambiano "
                      "solo per l'esecuzione in corso: la cue salvata non viene modificata.")
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
        row = QHBoxLayout()
        le = QLineEdit(self.cue.file)
        le.setReadOnly(True)
        b = QPushButton("Sfoglia…")
        cue = self.cue

        def browse():
            exts = set()
            for k in kinds:
                exts |= MEDIA_EXT[k]
            pat = " ".join("*" + e for e in sorted(exts))
            start = os.path.dirname(cue.file) if cue.file else os.path.expanduser("~")
            path, _ = QFileDialog.getOpenFileName(self, "Scegli file", start, f"Media ({pat});;Tutti (*)")
            if path:
                cue.file = path
                if not cue.name:
                    cue.name = os.path.splitext(os.path.basename(path))[0]
                cue.start = cue.end = cue.loop_start = cue.loop_end = 0.0
                cue.curve = []
                le.setText(path)
                self._emit(True)
                self.rebuild()
        b.clicked.connect(browse)
        row.addWidget(le, 1)
        row.addWidget(b)
        return row

    def _tab_media_time(self):
        cue = self.cue
        w = QWidget()
        v = QVBoxLayout(w)
        v.addLayout(self._file_row([cue.type]))
        self.info_label = QLabel()
        self.info_label.setStyleSheet("color:#999;")
        v.addWidget(self.info_label)
        v.addWidget(self._preset_bar("audio"))
        self.timeline = TimelineEditor()
        self.timeline.setMinimumHeight(190)
        self.timeline.changed.connect(self._timeline_changed)
        v.addWidget(self.timeline)
        hint = QLabel("Trascina IN/OUT e i marcatori del loop · doppio clic o Ctrl+clic: aggiungi punto "
                      "di volume · tasto destro su un punto: elimina · rotella: zoom · Maiusc+rotella o "
                      "trascina: scorri")
        hint.setStyleSheet("color:#888; font-size:10px;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        # ---- timeline dei keyframe con navigazione tra i marcatori
        info = self.loader.get_info(cue.file) if cue.file else None
        kbar = None
        self._waiting_src = bool(cue.file) and info is None
        if info is not None and float(info.get("duration") or 0) > 0:
            from ..ui_keyframes import KeyframeBar
            dur = float(info.get("duration") or 0.0)
            kbar = KeyframeBar(cue, duration_fn=lambda: dur, in_out=True)
            kbar.timeChanged.connect(lambda t: self.timeline.set_cursor(t) if self.timeline else None)
            kbar.presetInUse.connect(self._show_in_use)
            kbar.edited.connect(lambda: self.timeline.update() if self.timeline else None)
            self.audio_kbar = kbar
            v.addWidget(kbar)
        top = QHBoxLayout()
        top.addWidget(QLabel("IN"))
        top.addWidget(self._dspin("start", 0, 36000, 0.1, 3))
        top.addWidget(QLabel("OUT"))
        top.addWidget(self._dspin("end", 0, 36000, 0.1, 3, special="fine file"))
        top.addWidget(QLabel("Volume"))
        top.addWidget(self._dspin("level_db", MIN_DB, MAX_DB, 0.5, 1, " dB", special="-∞ dB"))
        top.addWidget(QLabel("Fade in"))
        top.addWidget(self._dspin("fade_in", 0, 3600, 0.1, 2))
        top.addWidget(QLabel("Fade out"))
        top.addWidget(self._dspin("fade_out", 0, 3600, 0.1, 2))
        top.addStretch(1)
        row = QHBoxLayout()
        lc = self._check("loop", "Loop")
        grp = self.show_fn().by_id(cue.group) if cue.group else None
        if grp is not None and grp.type == "group" and grp.group_mode in ("playlist", "rplaylist"):
            lc.setEnabled(False)          # nella playlist ogni cue suona una volta
            lc.setToolTip("Questa cue è in una Playlist: suona una volta sola. Per ripetere usa il loop del gruppo.")
            lc.setText("Loop (ignorato nella Playlist)")
        row.addWidget(lc)
        row.addWidget(QLabel("da"))
        row.addWidget(self._dspin("loop_start", 0, 36000, 0.1, 3, special="IN"))
        row.addWidget(QLabel("a"))
        row.addWidget(self._dspin("loop_end", 0, 36000, 0.1, 3, special="OUT"))
        row.addWidget(QLabel("ripetizioni"))
        row.addWidget(self._ispin("loop_count", 0, 100000, special="∞"))
        if cue.type == "video":
            pc = self._check("preload", "Precarica")
            pc.setToolTip("Tiene il video pronto in anticipo (file aperto, fermo sul primo fotogramma) quando la "
                          "cue è vicina al playhead o nella pagina del GRID: parte subito. Nei loop un secondo "
                          "lettore aspetta all'inizio del loop, così la ripetizione non ha attese. Toglilo per "
                          "risparmiare memoria con molti video grandi.")
            row.addWidget(pc)
        row.addStretch(1)
        bc = QPushButton("Cancella curva volume")
        bc.clicked.connect(lambda: (cue.curve.clear(), self._timeline_changed()))
        bz = QPushButton("Zoom tutto")
        bz.clicked.connect(lambda: self.timeline.zoom_all())
        row.addWidget(bc)
        row.addWidget(bz)
        v.addLayout(top)
        v.addLayout(row)
        v.addLayout(self._test_buttons())
        v.addWidget(self._audio_out_section(kbar, info))
        self._load_media()
        if kbar is not None:
            self.timeline.set_cursor(kbar.file_time())
            QTimer.singleShot(0, kbar.update_in_use)
        return w

    def _tab_visual(self):
        cue = self.cue
        outer = QWidget()
        h = QHBoxLayout(outer)
        h.setContentsMargins(0, 0, 0, 0)
        w = QWidget()
        f = QFormLayout(w)
        pb = self._preset_bar("visual")
        f.addRow(pb)
        if cue.type == "image":
            f.addRow("File", self._file_row(["image"]))
            f.addRow("Durata", self._dspin("duration", 0, 360000, 1, 1, special="fino allo stop"))
            f.addRow("Fade in", self._dspin("fade_in", 0, 3600, 0.1, 2))
            f.addRow("Fade out (prima della fine)", self._dspin("fade_out", 0, 3600, 0.1, 2))
        f.addRow("Layer (più alto = sopra)", self._ispin("layer", 0, 99))
        op = QDoubleSpinBox()
        op.setRange(0, 100)
        op.setSuffix(" %")
        op.setValue(float(cue.opacity) * 100)
        op.valueChanged.connect(lambda v: (setattr(cue, "opacity", v / 100.0), self._emit()))
        f.addRow("Opacità", op)
        f.addRow("Adattamento", self._combo("fit", FIT_MODES, FIT_LABELS))

        # --- crop in pixel del sorgente
        crop = QGridLayout()
        for i, (lab, attr) in enumerate((("sx", "crop_l"), ("dx", "crop_r"),
                                         ("alto", "crop_t"), ("basso", "crop_b"))):
            crop.addWidget(QLabel(lab), i // 2, (i % 2) * 2)
            crop.addWidget(self._ispin(attr, 0, 16384, suffix=" px"), i // 2, (i % 2) * 2 + 1)
        self.src_label = QLabel()
        self.src_label.setStyleSheet("color:#888;")
        crop.addWidget(self.src_label, 2, 0, 1, 4)
        f.addRow("Crop (px sorgente)", crop)

        # --- spostamento in pixel dell'uscita e scala
        pos = QGridLayout()
        pos.addWidget(QLabel("X"), 0, 0)
        pos.addWidget(self._ispin("off_x", -16384, 16384, suffix=" px"), 0, 1)
        pos.addWidget(QLabel("Y"), 0, 2)
        pos.addWidget(self._ispin("off_y", -16384, 16384, suffix=" px"), 0, 3)
        pos.addWidget(QLabel("Scala"), 1, 0)
        pos.addWidget(self._dspin("scale", 1, 1000, 1, 1, " %"), 1, 1)
        self.out_label = QLabel()
        self.out_label.setStyleSheet("color:#888;")
        pos.addWidget(self.out_label, 1, 2, 1, 2)
        f.addRow("Spostamento (px uscita)", pos)
        self._update_px_labels()

        # --- deformazione prospettica: spostamento dei 4 angoli in pixel d'uscita
        warp = QGridLayout()
        for i, lab in enumerate(("↖ alto sx", "↗ alto dx", "↘ basso dx", "↙ basso sx")):
            warp.addWidget(QLabel(lab), i, 0)
            for j, ax in enumerate(("x", "y")):
                warp.addWidget(QLabel(ax), i, 1 + j * 2)
                warp.addWidget(self._warp_spin(i, j), i, 2 + j * 2)
        f.addRow("Prospettiva (px uscita)", warp)

        reset = QPushButton("Reimposta crop, posizione e prospettiva")

        def do_reset():
            for a in ("crop_l", "crop_t", "crop_r", "crop_b", "off_x", "off_y"):
                setattr(cue, a, 0)
            cue.scale = 100.0
            cue.warp = [[0, 0], [0, 0], [0, 0], [0, 0]]
            self.refresh_fields()
            self._emit()
        reset.clicked.connect(do_reset)
        hint = QLabel("Spostamento: 0,0 = centrato secondo l'adattamento. Nell'anteprima dell'uscita: "
                      "trascina = sposta, rotella = scala, Maiusc+trascina = crop. Le modifiche valgono "
                      "anche durante la riproduzione.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888; font-size:10px;")
        f.addRow(reset)
        f.addRow(hint)
        f.addRow(self._test_buttons())
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
            self.src_label.setText(f"sorgente {wh[0]}×{wh[1]}" if wh else "")
            ow, oh = self.out_size_fn() if self.out_size_fn else (0, 0)
            self.out_label.setText(f"uscita {ow}×{oh}" if ow else "")
        except RuntimeError:
            pass

    def _tab_fade(self):
        cue = self.cue
        w = QWidget()
        f = QFormLayout(w)
        f.addRow("Cue target", self._target_combo())
        f.addRow("Durata fade", self._dspin("fade_duration", 0, 3600, 0.1, 2))
        f.addRow("Forma", self._combo("fade_shape", FADE_SHAPES, FADE_SHAPE_LABELS))
        row = QHBoxLayout()
        row.addWidget(self._check("fade_level", "Volume a"))
        row.addWidget(self._dspin("target_db", MIN_DB, MAX_DB, 1, 1, " dB", special="-∞ (silenzio)"))
        row.addStretch(1)
        f.addRow("Livello", row)
        row2 = QHBoxLayout()
        row2.addWidget(self._check("fade_opacity", "Opacità a"))
        op = QDoubleSpinBox()
        op.setRange(0, 100)
        op.setSuffix(" %")
        op.setValue(float(cue.target_opacity) * 100)
        op.valueChanged.connect(lambda v: (setattr(cue, "target_opacity", v / 100.0), self._emit()))
        self.bound.append((op, lambda: op.setValue(float(cue.target_opacity) * 100)))
        row2.addWidget(op)
        row2.addStretch(1)
        f.addRow("Video/immagini", row2)
        f.addRow(self._check("stop_when_done", "Ferma il target alla fine del fade (fade out)"))
        f.addRow(self._check("start_if_idle", "Se il target non è attivo, avvialo da silenzio (fade in)"))
        pres = QHBoxLayout()
        b1 = QPushButton("Preset: Fade OUT + stop")
        b2 = QPushButton("Preset: Fade IN")

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
        if not self.timeline or not self.cue:
            return
        path = self.cue.file
        info = self.loader.get_info(path) if path else None
        peaks = self.loader.get_peaks(path) if path else None
        dur = info["duration"] if info else 0.0
        self.timeline.set_cue(self.cue, dur, peaks)
        if self.info_label is not None:
            if not path:
                self.info_label.setText("Nessun file: usa «Sfoglia…» o trascina un file nella lista")
            elif not os.path.exists(path):
                self.info_label.setText("⚠ File non trovato")
            elif info:
                extra = f" · video {info['width']}×{info['height']}" if info["has_video"] else ""
                self.info_label.setText(f"Durata file {fmt_time(dur)}{extra}"
                                        f"{'' if info['has_audio'] else ' · senza audio'}")
            else:
                self.info_label.setText("Analisi del file…")

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
        self.timeline.set_playhead(inst.position() if inst is not None else None)
