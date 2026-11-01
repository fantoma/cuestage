"""Scheda dell'ispettore per le cue «Regia video»: regia e comandi eseguiti in ordine al GO (parte di Inspector)."""

from ..i18n import tr
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)


def ctl_value_editor(info, cur, setter):
    """Editor del valore di un controllo (dict da list_controls); setter(int) a ogni modifica."""
    from ..ui_mixers import _ctl_text
    typ = info["type"]
    if typ == "action":
        lab = QLabel(tr("comando"))
        lab.setStyleSheet("color:#999;")
        return lab
    if typ == "bool":
        w = QComboBox()
        w.addItem(tr("spento"), 0)
        w.addItem(tr("acceso"), 1)
        w.setCurrentIndex(1 if cur else 0)
        w.currentIndexChanged.connect(setter)
        return w
    if typ == "enum":
        w = QComboBox()
        for it in info.get("items") or []:
            w.addItem(it)
        w.setCurrentIndex(max(0, min(int(cur or 0), w.count() - 1)))
        w.currentIndexChanged.connect(setter)
        return w
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    sl = QSlider(Qt.Horizontal)
    sl.setRange(int(info["min"]), int(info["max"]))
    sl.setValue(max(int(info["min"]), min(int(info["max"]), int(cur or 0))))
    lab = QLabel(_ctl_text(info, sl.value()))
    lab.setMinimumWidth(70)
    sl.valueChanged.connect(lambda x: (lab.setText(_ctl_text(info, x)), setter(int(x))))
    h.addWidget(sl, 1)
    h.addWidget(lab)
    return w


class SwitcherTabMixin:
    def _tab_switcher(self):
        from ..alsactl import group_rank
        cue = self.cue
        sm = getattr(self.ctrl, "switchers", None)
        w = QWidget()
        f = QFormLayout(w)
        confs = sm.mixers if sm else []
        sc = QComboBox()
        sc.addItem(tr("— scegli —"), "")
        for m in confs:
            cls = sm.cls_for(m)
            sc.addItem(f"{m.get('name') or m.get('midi_out')}  ({cls.NAME if cls else m.get('driver')})", m.get("id"))
        i = sc.findData(cue.sw_target)
        if i < 0 and cue.sw_target:
            sc.addItem(tr("{0} (non configurata)", cue.sw_target), cue.sw_target)
            i = sc.count() - 1
        if i <= 0 and len(confs) == 1 and not cue.sw_target:
            cue.sw_target = confs[0].get("id")
            i = 1
        sc.setCurrentIndex(max(0, i))

        def target_changed(j):
            cue.sw_target = sc.itemData(j) or ""
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        sc.currentIndexChanged.connect(target_changed)
        f.addRow(tr("Regia"), sc)
        if not confs:
            lab = QLabel(tr("Nessuna regia video configurata: Impostazioni › Regie video…"))
            lab.setStyleSheet("color:#e0b040;")
            lab.setWordWrap(True)
            f.addRow(lab)
            return w
        conf = sm.find(cue.sw_target)
        if conf is None:
            return w
        cls = sm.cls_for(conf)
        sm.ensure_controls(cue.sw_target)
        ctls = [c for c in (sm.controls.get(cue.sw_target) or []) if c.get("writable", True)]
        infos = {c["key"]: c for c in ctls}
        groups = {}
        for c in ctls:
            groups.setdefault(c.get("group") or tr("Altro"), []).append(c)
        ordered = [c for _g, items in sorted(groups.items(), key=lambda kv: group_rank(kv[0])) for c in items]
        if not cue.sw_cmds:
            cue.sw_cmds = [{"key": "", "value": 0}]
        box = QWidget()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(3)
        cmds = cue.sw_cmds

        for idx, cmd in enumerate(cmds):
            row = QHBoxLayout()
            num = QLabel(f"{idx + 1}.")
            num.setFixedWidth(22)
            row.addWidget(num)
            cc = QComboBox()
            cc.addItem(tr("— scegli il comando —"), "")
            for c in ordered:
                cc.addItem(f"{c.get('group') or tr('Altro')} › {c['name']}", c["key"])
            j = cc.findData(cmd.get("key"))
            if j < 0 and cmd.get("key"):
                cc.addItem(tr("{0} (non letto)", cmd["key"]), cmd["key"])
                j = cc.count() - 1
            cc.setCurrentIndex(max(0, j))
            cc.setMinimumWidth(230)

            def ctl_changed(k, cmd=cmd, cc=cc):
                cmd["key"] = cc.itemData(k) or ""
                info = infos.get(cmd["key"])
                if info is not None:
                    cmd["value"] = 1 if info["type"] == "action" else int(info.get("value") or 0)
                self._emit()
                QTimer.singleShot(0, self.rebuild)
            cc.currentIndexChanged.connect(ctl_changed)
            row.addWidget(cc, 2)
            info = infos.get(cmd.get("key"))
            if info is not None:
                ed = ctl_value_editor(info, cmd.get("value"),
                                      lambda v, cmd=cmd: (cmd.__setitem__("value", int(v)), self._emit()))
                ed.setMinimumWidth(150)
                row.addWidget(ed, 2)
            else:
                row.addStretch(2)
            for text, tip, delta in (("↑", tr("Sposta su"), -1), ("↓", tr("Sposta giù"), 1)):
                b = QPushButton(text)
                b.setFixedWidth(26)
                b.setToolTip(tip)
                b.setEnabled(0 <= idx + delta < len(cmds))

                def move(_=False, idx=idx, delta=delta):
                    cmds[idx], cmds[idx + delta] = cmds[idx + delta], cmds[idx]
                    self._emit()
                    QTimer.singleShot(0, self.rebuild)
                b.clicked.connect(move)
                row.addWidget(b)
            x = QPushButton("✕")
            x.setFixedWidth(26)
            x.setToolTip(tr("Togli il comando"))

            def remove(_=False, idx=idx):
                del cmds[idx]
                self._emit()
                QTimer.singleShot(0, self.rebuild)
            x.clicked.connect(remove)
            row.addWidget(x)
            bv.addLayout(row)
        f.addRow(tr("Comandi"), box)
        tools = QHBoxLayout()
        add = QPushButton(tr("+ Aggiungi comando"))

        def add_cmd():
            cmds.append({"key": "", "value": 0})
            self._emit()
            QTimer.singleShot(0, self.rebuild)
        add.clicked.connect(add_cmd)
        tools.addWidget(add)
        res = QLabel()
        res.setStyleSheet("color:#999;")
        send = QPushButton(tr("Invia ora"))

        def send_now():
            n = 0
            for d in cmds:
                if d.get("key"):
                    sm.set_control(cue.sw_target, d["key"], int(d.get("value") or 0))
                    n += 1
            res.setText(tr("Inviati {0} comandi (l'esito compare nella barra di stato)", n))
        send.clicked.connect(send_now)
        tools.addWidget(send)
        tools.addStretch(1)
        f.addRow(tools)
        f.addRow(res)
        hint = QLabel(tr("I comandi partono uno dopo l'altro, nell'ordine dell'elenco, al GO della cue."))
        hint.setStyleSheet("color:#888;")
        hint.setWordWrap(True)
        f.addRow(hint)
        if not ctls:
            lab = QLabel(tr("Comandi non ancora letti: regia configurata?"))
            lab.setStyleSheet("color:#e0b040;")
            f.addRow(lab)
        if cls is not None and cls.NOTES:
            n = QLabel(cls.NOTES)
            n.setWordWrap(True)
            n.setStyleSheet("color:#888; font-size:10px;")
            f.addRow(n)
        return w
