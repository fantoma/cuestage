"""Finestra iniziale: CueStage è un software sperimentale (testo in tutte le lingue, non tradotto con tr)."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

AUTHOR = "Designed by Diego Fantoma"

# un blocco per lingua: (codice, nome, titolo, testo, pulsante accetta, pulsante esci)
NOTICE = [
    ("it", "Italiano", "Software sperimentale",
     "CueStage è un software sperimentale, ancora in fase di sviluppo. <b>Non usarlo in produzione</b> "
     "(spettacoli dal vivo, eventi, installazioni): può contenere errori e comportarsi in modo imprevisto, "
     "fermare o non avviare una cue, perdere modifiche. Lo usi a tuo rischio, senza alcuna garanzia.",
     "Ho capito", "Esci"),
    ("en", "English", "Experimental software",
     "CueStage is experimental software, still under development. <b>Do not use it in production</b> "
     "(live shows, events, installations): it may contain bugs and behave unexpectedly, stop or fail to "
     "start a cue, or lose changes. You use it at your own risk, without any warranty.",
     "I understand", "Quit"),
    ("sl", "Slovenščina", "Preizkusna programska oprema",
     "CueStage je preizkusna programska oprema, ki je še v razvoju. <b>Ne uporabljajte je v produkciji</b> "
     "(predstave v živo, prireditve, instalacije): lahko vsebuje napake in se obnaša nepričakovano, ustavi "
     "cue ali ga ne zažene ali izgubi spremembe. Uporabljate jo na lastno odgovornost, brez kakršnega koli "
     "jamstva.",
     "Razumem", "Izhod"),
]


def ordered(lang):
    """Prima la lingua dell'interfaccia, poi le altre."""
    return sorted(NOTICE, key=lambda n: n[0] != lang)


class DisclaimerDialog(QDialog):
    def __init__(self, lang="it", version="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("CueStage")
        self.setModal(True)
        self.setMinimumWidth(620)
        notes = ordered(lang)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        icon = QLabel()
        pm = QPixmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png"))
        if not pm.isNull():
            icon.setPixmap(pm.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        head.addWidget(icon)
        title = QLabel(f"<span style='font-size:20pt;font-weight:bold'>CueStage</span>"
                       f"{' &nbsp;' + version if version else ''}<br>"
                       f"<span style='color:#9aa'>{AUTHOR}</span>")
        head.addWidget(title, 1)
        lay.addLayout(head)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        for i, (code, name, ttl, text, _ok, _quit) in enumerate(notes):
            if i:
                line = QFrame()
                line.setFrameShape(QFrame.HLine)
                line.setStyleSheet("color:#444")
                bl.addWidget(line)
            lab = QLabel(f"<p style='margin:0'><span style='color:#e0a030;font-weight:bold'>⚠ {ttl}</span>"
                         f" <span style='color:#888'>· {name}</span></p><p style='margin-top:4px'>{text}</p>")
            lab.setObjectName(f"notice_{code}")
            lab.setWordWrap(True)
            lab.setTextFormat(Qt.RichText)
            bl.addWidget(lab)
        bl.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(body)
        sc.setMinimumHeight(330)
        lay.addWidget(sc, 1)
        row = QHBoxLayout()
        self.quit_btn = QPushButton(" / ".join(n[5] for n in notes))
        self.quit_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton(" / ".join(n[4] for n in notes))
        self.ok_btn.setDefault(True)
        self.ok_btn.setStyleSheet("font-weight:bold;padding:6px 18px")
        self.ok_btn.clicked.connect(self.accept)
        row.addWidget(self.quit_btn)
        row.addStretch(1)
        row.addWidget(self.ok_btn)
        lay.addLayout(row)
        self.ok_btn.setFocus()


def accept_notice(lang, version=""):
    """Mostra la finestra e ritorna True se l'utente accetta."""
    return DisclaimerDialog(lang, version).exec() == QDialog.Accepted
