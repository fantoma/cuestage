"""Finestra Impostazioni › Ducking: ingresso, canale, vumeter, gate, attacco/rilascio e Master ducking."""
import copy

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QLabel, QSpinBox, QVBoxLayout, QWidget)

from .ducking import MIN_METER_DB, duck_settings, lin_to_db, list_input_devices


class DuckMeter(QWidget):
    """Vumeter orizzontale dell'ingresso (−70…0 dBFS) con la soglia del gate, il LED del gate e la riduzione."""

    def __init__(self, ducker, threshold_fn, parent=None):
        super().__init__(parent)
        self.duck = ducker
        self.threshold_fn = threshold_fn
        self.setFixedHeight(58)
        self.setMinimumWidth(320)

    def sizeHint(self):
        return QSize(360, 58)

    @staticmethod
    def _x(db, r):
        f = (max(MIN_METER_DB, min(0.0, db)) - MIN_METER_DB) / -MIN_METER_DB
        return r.left() + f * r.width()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        d = self.duck
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85))
        p.setFont(f)
        led = QRectF(self.width() - 86, 4, 82, 22)
        bar = QRectF(4, 4, self.width() - 96, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(30, 32, 38))
        p.drawRoundedRect(bar, 3, 3)
        listening = d.listening
        db = lin_to_db(d.meter) if listening else -120.0
        thr = float(self.threshold_fn())
        if db > MIN_METER_DB:
            x = self._x(db, bar)
            col = QColor(60, 200, 110) if db < -12 else (QColor(230, 190, 40) if db < -3 else QColor(220, 60, 50))
            p.setBrush(col)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), x - bar.left(), bar.height()), 3, 3)
        tx = self._x(thr, bar)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawLine(int(tx), int(bar.top()) - 2, int(tx), int(bar.bottom()) + 2)
        txt = self.palette().windowText().color()
        p.setPen(txt)
        for mark in (-60, -48, -36, -24, -12, 0):
            x = self._x(mark, bar)
            p.drawText(QRectF(x - 14, bar.bottom() + 2, 28, 12), Qt.AlignCenter, str(mark))
        # LED del gate
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(200, 50, 40) if (listening and d.gate) else QColor(60, 62, 70))
        p.drawRoundedRect(led, 4, 4)
        p.setPen(QColor(255, 255, 255))
        p.drawText(led, Qt.AlignCenter, "GATE APERTO" if (listening and d.gate) else "gate chiuso")
        # riduzione applicata ora (0–100 %)
        rb = QRectF(4, 40, self.width() - 96, 10)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(30, 32, 38))
        p.drawRoundedRect(rb, 2, 2)
        if listening and d.env > 0:
            p.setBrush(QColor(220, 70, 60))
            p.drawRoundedRect(QRectF(rb.left(), rb.top(), rb.width() * d.env, rb.height()), 2, 2)
        p.setPen(txt)
        p.drawText(QRectF(self.width() - 86, 36, 82, 18), Qt.AlignCenter,
                   f"riduzione {d.env * 100:.0f}%" if listening else "non in ascolto")
        p.end()


class DuckingDialog(QDialog):
    """Le modifiche valgono subito (si sente e si vede il risultato); «Annulla» rimette quelle di prima."""

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.duck = mw.audio.duck
        self.setWindowTitle("Ducking")
        self.setModal(False)
        self._loading = False
        self.orig = {}
        v = QVBoxLayout(self)
        intro = QLabel("Quando l'ingresso scelto (es. il microfono dello speaker) supera la soglia del gate, le cue "
                       "con un valore di <b>Ducking</b> (ispettore della cue audio/video o del gruppo) si abbassano "
                       "di quei dB; il <b>Master ducking</b> abbassa l'uscita master quando il tasto «Duck» accanto "
                       "al volume master è acceso.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.on = QCheckBox("Attiva il ducking delle cue")
        v.addWidget(self.on)

        gi = QGroupBox("Ingresso")
        gv = QVBoxLayout(gi)
        fi = QFormLayout()
        gv.addLayout(fi)
        self.dev = QComboBox()
        fi.addRow("Scheda in ingresso", self.dev)
        self.ch = QSpinBox()
        self.ch.setRange(1, 64)
        fi.addRow("Canale", self.ch)
        self.meter = DuckMeter(self.duck, lambda: self.thr.value())
        gv.addWidget(self.meter)
        self.err = QLabel()
        self.err.setWordWrap(True)
        self.err.setStyleSheet("color:#e0b040;")
        gv.addWidget(self.err)
        self.to_pv = QCheckBox("Manda l'ingresso, dopo il gate, al preascolto (cuffia)")
        self.to_pv.setToolTip("Si sente in cuffia il canale d'ingresso solo quando supera la soglia (e durante "
                              "l'hold): utile per controllare il microfono e regolare la soglia. Usa la scheda, i "
                              "canali e il volume del preascolto.")
        gv.addWidget(self.to_pv)
        self.to_pv_note = QLabel()
        self.to_pv_note.setStyleSheet("color:#999;")
        self.to_pv_note.setWordWrap(True)
        gv.addWidget(self.to_pv_note)
        v.addWidget(gi)

        gg = QGroupBox("Gate e tempi")
        fg = QFormLayout(gg)
        self.thr = QDoubleSpinBox()
        self.thr.setRange(-90, 0)
        self.thr.setDecimals(1)
        self.thr.setSingleStep(1)
        self.thr.setSuffix(" dBFS")
        self.thr.setToolTip("Il ducking interviene quando il livello dell'ingresso supera questa soglia "
                            "(la linea bianca sul vumeter)")
        fg.addRow("Soglia del gate", self.thr)
        self.att = QSpinBox()
        self.att.setRange(0, 1000)
        self.att.setSingleStep(10)
        self.att.setSuffix(" ms")
        self.att.setToolTip("Tempo per arrivare alla riduzione piena dopo che il segnale ha superato la soglia")
        fg.addRow("Attacco", self.att)
        self.hold = QSpinBox()
        self.hold.setRange(0, 5000)
        self.hold.setSingleStep(100)
        self.hold.setSuffix(" ms")
        self.hold.setToolTip("Quanto resta abbassato dopo che il segnale è sceso sotto la soglia, prima del "
                             "rilascio (evita che il volume risalga tra una parola e l'altra)")
        fg.addRow("Hold", self.hold)
        self.rel = QSpinBox()
        self.rel.setRange(0, 5000)
        self.rel.setSingleStep(50)
        self.rel.setSuffix(" ms")
        self.rel.setToolTip("Tempo per tornare al volume pieno, dopo l'hold")
        fg.addRow("Rilascio", self.rel)
        v.addWidget(gg)

        gm = QGroupBox("Master ducking")
        fm = QFormLayout(gm)
        self.mdb = QDoubleSpinBox()
        self.mdb.setRange(0, 60)
        self.mdb.setDecimals(1)
        self.mdb.setSingleStep(1)
        self.mdb.setSuffix(" dB")
        self.mdb.setSpecialValueText("0 dB (nessuna riduzione)")
        self.mdb.setToolTip("Riduzione dell'uscita master quando il ducking interviene e il tasto «Duck» è acceso")
        fm.addRow("Riduzione del master", self.mdb)
        self.mstate = QLabel()
        self.mstate.setStyleSheet("color:#999;")
        fm.addRow(self.mstate)
        v.addWidget(gm)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.on.toggled.connect(self._changed)
        self.to_pv.toggled.connect(self._changed)
        self.dev.currentIndexChanged.connect(self._dev_changed)
        for w in (self.ch, self.att, self.hold, self.rel):
            w.valueChanged.connect(self._changed)
        for w in (self.thr, self.mdb):
            w.valueChanged.connect(self._changed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ dati
    def load(self):
        self._loading = True
        cfg = duck_settings(self.mw.show_data.settings)
        self.orig = copy.deepcopy(cfg)
        self.on.setChecked(bool(cfg["enabled"]))
        self.dev.clear()
        self.dev.addItem("Predefinita di sistema", "")
        for d in list_input_devices():
            self.dev.addItem(f"{d['name']}  [{d['hostapi']}] — {d['max_ch']} canali", d["id"])
        i = self.dev.findData(cfg["device"])
        if i < 0:
            self.dev.addItem(f"{cfg['device'].split('|', 1)[-1]} (non collegata)", cfg["device"])
            i = self.dev.count() - 1
        self.dev.setCurrentIndex(i)
        self._fit_channels()
        self.ch.setValue(int(cfg["channel"]))
        self.thr.setValue(float(cfg["threshold_db"]))
        self.att.setValue(int(cfg["attack_ms"]))
        self.hold.setValue(int(cfg["hold_ms"]))
        self.rel.setValue(int(cfg["release_ms"]))
        self.mdb.setValue(float(cfg["master_db"]))
        self.to_pv.setChecked(bool(cfg["to_preview"]))
        pv = self.mw.audio.preview
        self.to_pv_note.setText("" if pv.get("enabled") else
                                "Il preascolto non è configurato: Impostazioni › Audio, OSC e MIDI › Preascolto.")
        self._loading = False
        self.duck.configure(cfg, monitor=True)        # in ascolto per il vumeter anche se è spento
        self._tick()
        self.adjustSize()

    def _fit_channels(self):
        dev_id = self.dev.currentData() or ""
        n = 64
        for d in list_input_devices():
            if d["id"] == dev_id:
                n = d["max_ch"]
        self.ch.setMaximum(max(1, n))

    def cfg(self):
        cur = duck_settings(self.mw.show_data.settings)
        return {"enabled": self.on.isChecked(), "device": self.dev.currentData() or "", "channel": self.ch.value(),
                "threshold_db": self.thr.value(), "attack_ms": self.att.value(), "hold_ms": self.hold.value(),
                "release_ms": self.rel.value(),
                "master_db": self.mdb.value(), "master_on": cur["master_on"],
                "to_preview": self.to_pv.isChecked()}

    def _dev_changed(self, *_a):
        if self._loading:
            return
        self._fit_channels()
        self._changed()

    def _changed(self, *_a):
        if self._loading:
            return
        self.mw.set_ducking(self.cfg())

    # ------------------------------------------------------------ vumeter
    def _tick(self):
        d = self.duck
        if self.err.text() != d.error:
            self.err.setText(d.error)         # la riga resta sempre: la finestra non cambia altezza
        on = bool(d.cfg.get("master_on"))
        self.mstate.setText("Tasto «Duck»: " + ("acceso" if on else "spento") +
                            ("" if self.mdb.value() > 0 or not on else " — con 0 dB non riduce nulla"))
        self.meter.update()

    def showEvent(self, e):
        super().showEvent(e)
        self.timer.start(40)

    def _close_monitor(self):
        self.timer.stop()
        self.duck.configure(self.mw.show_data.settings.get("ducking") or {}, monitor=False)

    def accept(self):
        self.mw.set_ducking(self.cfg())
        self._close_monitor()
        super().accept()

    def reject(self):
        orig = dict(self.orig)
        orig["master_on"] = duck_settings(self.mw.show_data.settings)["master_on"]   # il tasto Duck resta com'è
        self.mw.set_ducking(orig)
        self._close_monitor()
        super().reject()
