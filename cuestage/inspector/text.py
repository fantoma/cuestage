"""Scheda dell'ispettore per le cue di testo (sopratitoli) (parte di Inspector)."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)


class TextTabMixin:
    # ------------------------------------------------------------------ testo (sottotitoli)
    def _tab_text(self):
        from PySide6.QtGui import QColor, QFont, QPixmap, QPainter as _P
        from PySide6.QtWidgets import QColorDialog, QFontComboBox, QPlainTextEdit as _PTE
        from ..text_render import FIT_MODES, parse_srt, render_text, split_phrases
        cue = self.cue
        w = QWidget()
        outer = QHBoxLayout(w)
        left = QVBoxLayout()
        # --- contenuto
        top = QHBoxLayout()
        mc = QComboBox()
        mc.addItem("Sequenza di frasi: una per GO (sottotitoli)", "sequence")
        mc.addItem("Testo libero: tutto insieme", "free")
        mc.setCurrentIndex(max(0, mc.findData(cue.text_mode)))
        top.addWidget(QLabel("Modalità"))
        top.addWidget(mc, 1)
        imp = QPushButton("Importa .txt / .srt…")
        top.addWidget(imp)
        left.addLayout(top)
        ed = _PTE(cue.text_content)
        ed.setPlaceholderText("Scrivi qui il testo. In sequenza: ogni frase è separata da una riga vuota; "
                              "una frase può avere più righe. «_» da solo = schermo vuoto.")
        f0 = ed.font()
        f0.setPointSizeF(f0.pointSizeF() * 1.1)
        ed.setFont(f0)
        left.addWidget(ed, 1)
        info = QLabel()
        info.setStyleSheet("color:#999;")
        info.setWordWrap(True)
        outer.addLayout(left, 3)

        # --- stile
        right = QVBoxLayout()
        f = QFormLayout()
        pre = QComboBox()
        presets = {"Sottotitolo in basso": ([5, 78, 90, 18], "center", "bottom"),
                   "Sopratitolo in alto": ([5, 3, 90, 18], "center", "top"),
                   "Centro dello schermo": ([10, 30, 80, 40], "center", "middle"),
                   "Schermo intero": ([3, 3, 94, 94], "center", "middle")}
        pre.addItem("— posizione rapida —")
        for k in presets:
            pre.addItem(k)
        f.addRow("Area", pre)
        arow = QHBoxLayout()
        spins = []
        for j, lab in enumerate(("x", "y", "larg.", "alt.")):
            sp = QDoubleSpinBox()
            sp.setRange(0, 100)
            sp.setDecimals(1)
            sp.setSuffix(" %")
            sp.setValue(float((cue.text_area or [5, 78, 90, 18])[j]))
            sp.setToolTip(f"{lab} dell'area in % dell'uscita")
            spins.append(sp)
            arow.addWidget(QLabel(lab))
            arow.addWidget(sp)
        f.addRow("", arow)
        al = QHBoxLayout()
        ha = self._combo("text_halign", ["left", "center", "right"], {"left": "sinistra", "center": "centro", "right": "destra"})
        va = self._combo("text_valign", ["top", "middle", "bottom"], {"top": "in alto", "middle": "al centro", "bottom": "in basso"})
        al.addWidget(ha)
        al.addWidget(va)
        f.addRow("Allineamento", al)
        fr = QHBoxLayout()
        fc = QFontComboBox()
        fc.setCurrentFont(QFont(cue.text_font or "Sans Serif"))
        fr.addWidget(fc, 1)
        sz = self._dspin("text_size", 6, 400, 1, 0, suffix=" px")
        sz.setToolTip("Dimensione riferita a un'uscita alta 1080 px")
        fr.addWidget(sz)
        f.addRow("Carattere", fr)
        st = QHBoxLayout()
        for attr, lab in (("text_bold", "Grassetto"), ("text_italic", "Corsivo")):
            cb = QCheckBox(lab)
            cb.setChecked(bool(getattr(cue, attr)))
            cb.toggled.connect(lambda on, attr=attr: (setattr(cue, attr, on), self._emit()))
            st.addWidget(cb)

        def color_btn(attr, allow_none=False):
            b = QPushButton()
            b.setFixedWidth(70)

            def paint():
                c = getattr(cue, attr)
                b.setText("nessuno" if not c else "")
                b.setStyleSheet(f"background:{c};" if c else "")
            paint()

            def pick():
                c = QColorDialog.getColor(QColor(getattr(cue, attr) or "#000000"), self, "Colore")
                if c.isValid():
                    setattr(cue, attr, c.name())
                    paint()
                    self._emit()
            b.clicked.connect(pick)
            if allow_none:
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.setToolTip("Clic: scegli · tasto destro: nessuno sfondo")
                b.customContextMenuRequested.connect(lambda *_: (setattr(cue, attr, ""), paint(), self._emit()))
            return b
        st.addWidget(QLabel("Colore"))
        st.addWidget(color_btn("text_color"))
        st.addStretch(1)
        f.addRow("Stile", st)
        ol = QHBoxLayout()
        ol.addWidget(self._dspin("text_outline", 0, 30, 0.5, 1, suffix=" px"))
        ol.addWidget(color_btn("text_outline_color"))
        f.addRow("Contorno", ol)
        bgr = QHBoxLayout()
        bgr.addWidget(color_btn("text_bg", allow_none=True))
        op = self._dspin("text_bg_opacity", 0, 100, 5, 0, suffix=" %")
        bgr.addWidget(op)
        bgr.addWidget(self._combo("text_bg_mode", ["block", "area"], {"block": "dietro il testo", "area": "tutta l'area"}))
        f.addRow("Sfondo", bgr)
        f.addRow("Margine interno", self._dspin("text_padding", 0, 200, 1, 0, suffix=" px"))
        f.addRow("Adattamento", self._combo("text_fit", list(FIT_MODES), FIT_MODES))
        ml = QSpinBox()
        ml.setRange(0, 20)
        ml.setSpecialValueText("senza limite")
        ml.setValue(int(cue.text_max_lines or 0))
        ml.valueChanged.connect(lambda v: (setattr(cue, "text_max_lines", v), self._emit()))
        f.addRow("Righe massime", ml)
        f.addRow("Interlinea", self._dspin("text_spacing", 60, 250, 5, 0, suffix=" %"))
        f.addRow("Dissolvenza tra frasi", self._dspin("text_fade", 0, 10, 0.1, 2))
        if cue.text_mode == "free":
            dur = self._dspin("duration", 0, 36000, 0.5, 1)
            dur.setSpecialValueText("finché non si ferma")
            f.addRow("Durata", dur)
        f.addRow("Layer", self._ispin("layer", 0, 99))
        right.addLayout(f)
        # --- anteprima
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Anteprima frase"))
        pn = QSpinBox()
        pn.setRange(1, 1)
        prow.addWidget(pn)
        live = QPushButton("Mostra in uscita da questa frase")
        prow.addWidget(live)
        prow.addStretch(1)
        prev = QLabel()
        prev.setFixedSize(320, 180)
        prev.setStyleSheet("background:#111;border:1px solid #444;")
        pv = QHBoxLayout()
        pv.addWidget(prev)
        pvr = QVBoxLayout()
        pvr.addLayout(prow)
        pvr.addWidget(info)
        pvr.addStretch(1)
        pv.addLayout(pvr, 1)
        left.addLayout(pv)
        sa_r = QScrollArea()
        sa_r.setWidgetResizable(True)
        sa_r.setFrameShape(QFrame.NoFrame)
        rw = QWidget()
        rw.setLayout(right)
        sa_r.setWidget(rw)
        outer.addWidget(sa_r, 2)

        def phrases():
            return split_phrases(cue.text_content) if cue.text_mode == "sequence" else [cue.text_content or ""]

        def redraw():
            ps = phrases() or [""]
            pn.blockSignals(True)
            pn.setRange(1, max(1, len(ps)))
            pn.blockSignals(False)
            info.setText(f"{len(ps)} frasi · GO avanza di una frase, dopo l'ultima il GO esce · "
                         f"Alt+→ / Alt+← frase successiva / precedente · ESC esce"
                         if cue.text_mode == "sequence" else "Il testo compare intero all'avvio della cue")
            W, H = self.ctrl.comp.out_size
            img = render_text(cue, ps[pn.value() - 1], int(W), int(H))
            pm = QPixmap(320, 180)
            pm.fill(QColor("#111"))
            pp = _P(pm)
            pp.setRenderHint(_P.SmoothPixmapTransform, True)
            pp.drawImage(pm.rect(), img)
            x, y, ww, hh = (float(v) for v in cue.text_area)
            pp.setPen(QColor(80, 160, 255))
            pp.drawRect(int(320 * x / 100), int(180 * y / 100), int(320 * ww / 100), int(180 * hh / 100))
            pp.end()
            prev.setPixmap(pm)
            inst = self.ctrl.instances.get(cue.id)
            if inst is not None and hasattr(inst, "refresh"):
                inst.refresh()
        self._text_redraw = redraw

        def text_changed():
            cue.text_content = ed.toPlainText()
            self.show_fn().dirty = True
            redraw()
        ed.textChanged.connect(text_changed)
        mc.currentIndexChanged.connect(lambda j: (setattr(cue, "text_mode", mc.itemData(j)), self._emit(),
                                                  QTimer.singleShot(0, self.rebuild)))
        fc.currentFontChanged.connect(lambda font: (setattr(cue, "text_font", font.family()), self._emit()))
        pn.valueChanged.connect(lambda _v: redraw())

        def area_changed(*_):
            cue.text_area = [round(sp.value(), 1) for sp in spins]
            self._emit()
        for sp in spins:
            sp.valueChanged.connect(area_changed)

        def preset(j):
            if j <= 0:
                return
            area, h, v = presets[pre.itemText(j)]
            cue.text_area = list(area)
            cue.text_halign, cue.text_valign = h, v
            pre.setCurrentIndex(0)
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        pre.currentIndexChanged.connect(preset)

        def do_import():
            path, _ = QFileDialog.getOpenFileName(self, "Importa testo", "", "Testo (*.txt *.srt);;Tutti (*)")
            if not path:
                return
            try:
                raw = open(path, encoding="utf-8-sig", errors="replace").read()
            except OSError as err:
                info.setText(f"✘ {err}")
                return
            ps = parse_srt(raw) if path.lower().endswith(".srt") else split_phrases(raw)
            ed.setPlainText("\n\n".join(ps))
        imp.clicked.connect(do_import)

        def show_live():
            inst = self.ctrl.instances.get(cue.id)
            if inst is None or not hasattr(inst, "goto"):
                self.ctrl.start_cue(cue, cascade=False)
                inst = self.ctrl.instances.get(cue.id)
            if inst is not None and hasattr(inst, "goto"):
                inst.goto(pn.value() - 1)
        live.clicked.connect(show_live)
        redraw()
        return w
