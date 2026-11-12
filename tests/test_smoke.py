"""Test di regressione rapidi di CueStage (senza schede audio né rete esterna).

    python3 tests/test_smoke.py          (dalla cartella del progetto)

Ogni prova stampa ✔ o ✘; il codice di uscita è il numero di prove fallite.
"""
import glob
import json
import os
import sys
import tempfile
import time
import traceback
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])
QSettings("CueStage", "CueStage").remove("last_show")
from cuestage.system import data_dir  # noqa: E402

for f in glob.glob(os.path.join(data_dir(), ".*.autosave")):
    os.remove(f)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)

import cuestage.audio_engine as ae  # noqa: E402
ae.sd = None                                   # audio «silenzioso»: nessuna scheda richiesta
from cuestage.main_window import MainWindow  # noqa: E402
from cuestage.model import Cue, Show  # noqa: E402

TMP = tempfile.mkdtemp(prefix="cuestage-test-")
FAILED = []

# richiesta di collegamento di una secondaria: accettata con i permessi scelti dalla prova (JOIN["perms"])
import cuestage.ui_cluster as _uc  # noqa: E402
JOIN = {"perms": None}


def _join_exec(self):
    perms = JOIN.get("perms")
    if perms and "*" not in perms:
        self.some_rb.setChecked(True)
        for lid, cb in self.combos.items():
            cb.setCurrentIndex(max(0, cb.findData(perms.get(lid, ""))))
    elif perms:
        self.all_mode.setCurrentIndex(self.all_mode.findData(perms["*"]))
    return _uc.JoinDialog.Accepted


_uc.JoinDialog.exec = _join_exec


def run(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.003)


def wait_until(cond, timeout=5.0):
    """Aspetta che cond() sia vera (le macchine lente, come i Mac di GitHub, hanno tempi più lunghi)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        run(0.02)
    return cond()


def tone(name, sec=5.0, freq=440):
    path = os.path.join(TMP, name)
    t = np.arange(int(48000 * sec)) / 48000
    data = (np.sin(2 * np.pi * freq * t) * 0.25 * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(data.tobytes())
    return path


def check(name, fn):
    try:
        fn()
        print(f"✔ {name}")
    except Exception:
        FAILED.append(name)
        print(f"✘ {name}")
        traceback.print_exc()


def names(w):
    return sorted(w.show_data.by_id(k).name for k in w.ctrl.instances)


# ----------------------------------------------------------------------------- prove
def t_save_load():
    sh = Show()
    sh.cues = [Cue("audio", number="1", name="A", file=tone("a.wav")), Cue("wait", number="2", duration=3)]
    sh.add_panel("Regia").cues.append(Cue("memo", number="R1", name="Nota"))
    p = os.path.join(TMP, "show.cueshow")
    sh.save(p)
    s2 = Show.load(p)
    assert [c.name for c in s2.main.cues] == ["A", ""] or len(s2.main.cues) == 2
    assert s2.panels[0].name == "Regia" and s2.panels[0].cues[0].name == "Nota"


def t_portable_paths():
    """Show salvato su un altro sistema (percorsi Windows) e percorsi ricevuti dal master."""
    from cuestage.system import data_dir, local_path, portable_rel
    os.makedirs(os.path.join(TMP, "media"), exist_ok=True)
    f = tone(os.path.join("media", "win.wav"), sec=0.5)
    sh = Show()
    sh.cues = [Cue("audio", number="1", name="Da Windows", file=f)]
    p = os.path.join(TMP, "portabile.cueshow")
    sh.save(p)
    data = json.load(open(p, encoding="utf-8"))
    cues = data["cues"]
    assert cues[0]["file_rel"] == "media/win.wav", cues[0]["file_rel"]
    cues[0]["file"] = "C:\\Users\\Regia\\Show\\media\\win.wav"     # come lo salverebbe Windows
    cues[0]["file_rel"] = "media\\win.wav"
    json.dump(data, open(p, "w", encoding="utf-8"))
    s2 = Show.load(p)
    got = s2.main.cues[0].file
    assert os.path.samefile(got, f), got
    assert local_path("../../x/../y.wav") == os.path.join("x", "y.wav")
    assert local_path("C:\\a\\b.wav") == os.path.join("a", "b.wav")
    assert portable_rel(os.path.join("a", "b")) == "a/b"
    assert os.path.isabs(data_dir())


def t_go_and_groups(w):
    sh = Show()
    sh.settings["output_fullscreen"] = False
    g = Cue("group", number="2", name="Gruppo", group_mode="all")
    m = [Cue("audio", number=f"2.{i}", name=f"M{i}", file=tone(f"m{i}.wav", freq=300 + 50 * i), group=g.id)
         for i in (1, 2)]
    sh.cues = [Cue("audio", number="1", name="Uno", file=tone("uno.wav")), g, *m,
               Cue("wait", number="3", name="Attesa", duration=10)]
    w._set_show(sh)
    run(0.5)
    w.go()
    run(0.3)
    assert names(w) == ["Uno"], names(w)
    w.go()
    run(0.3)
    assert {"Gruppo", "M1", "M2"} <= set(names(w)), names(w)
    w.ctrl.stop_all(0)
    run(0.3)
    assert not w.ctrl.instances


def t_playlist(w):
    """Playlist e Random playlist: da sole, senza pre-wait, una volta ciascuna, poi il gruppo finisce."""
    for mode in ("playlist", "rplaylist"):
        sh = Show()
        sh.settings["output_fullscreen"] = False
        g = Cue("group", number="5", name="PL", group_mode=mode)
        ms = [Cue("audio", number=f"5.{i}", name=f"P{i}", file=tone(f"p{i}.wav", sec=0.6, freq=500 + 50 * i),
                  group=g.id, delay=5.0) for i in (1, 2, 3)]
        sh.cues = [g, *ms]
        w._set_show(sh)
        run(0.4)
        w.go()
        seen = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3.5:
            run(0.02)
            for k, inst in w.ctrl.instances.items():
                n = w.show_data.by_id(k).name
                if n in ("P1", "P2", "P3") and not inst.done and n not in seen:
                    seen.append(n)
        assert sorted(seen) == ["P1", "P2", "P3"], (mode, seen)
        if mode == "playlist":
            assert seen == ["P1", "P2", "P3"], seen
        assert g.id not in w.ctrl.instances or w.ctrl.instances[g.id].done, "il gruppo non è finito"
        w.ctrl.stop_all(0)
        run(0.2)


def t_grid_and_stop_panel(w):
    """PANEL GRID (caselle, ordine del GO, clic, scorciatoia, salvataggio) e cue «Stop panel»."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    sh = Show()
    sh.settings["output_fullscreen"] = False
    sh.cues = [Cue("audio", number="1", name="Main", file=tone("gm.wav", sec=6)),
               Cue("stoppanel", number="2", name="Stop", sp_panels=["*"], stop_fade=0.2)]
    w._set_show(sh)
    run(0.2)
    w.add_panel("grid")
    run(0.2)
    lst = w.show_data.current
    assert lst.is_grid and w.view_stack.currentWidget() is w.grid
    w.grid_add_files([tone(f"gg{i}.wav", sec=4, freq=300 + 40 * i) for i in range(3)], [0, 1, 1])
    run(0.2)
    assert [tuple(c.grid_pos) for c in lst.cues] == [(0, 1, 1), (0, 1, 2), (0, 1, 3)]
    c0 = lst.cues[0]
    w.set_hotkey(c0, "F4")
    w.grid_set_color(c0, "#7a2730")
    can = w.grid.canvas
    r = can.cell_rect(1, 2).center()
    QTest.mouseClick(can, Qt.LeftButton, Qt.NoModifier, QPoint(int(r.x()), int(r.y())))
    wait_until(lambda: lst.cues[1].id in w.ctrl.instances)
    assert lst.cues[1].id in w.ctrl.instances, "il clic sulla casella non avvia"
    QTest.keyClick(w, Qt.Key_F4)
    wait_until(lambda: c0.id in w.ctrl.instances)
    assert c0.id in w.ctrl.instances, "la scorciatoia non avvia"
    w.ctrl.trigger(sh.main.cues[0], cascade=False)
    run(0.2)
    w.ctrl.start_cue(sh.main.cues[1], cascade=False)       # Stop panel «Tutti» (fade 0,2 s)

    def alive():
        return sorted(w.show_data.by_id(k).name for k, i in w.ctrl.instances.items() if not i.done)
    wait_until(lambda: alive() == ["Main"])
    assert alive() == ["Main"], alive()
    # Stop panel nel GRID che ferma solo la MAIN (il GRID continua)
    sp_main = Cue("stoppanel", number="99", name="Ferma MAIN", sp_panels=[sh.main.id], stop_fade=0.1)
    lst.cues.append(sp_main)
    w.ctrl.trigger(sh.main.cues[0], cascade=False)
    w.ctrl.trigger(lst.cues[0], cascade=False)
    wait_until(lambda: alive() == ["Main", "gg0"])
    w.ctrl.start_cue(sp_main, cascade=False)
    wait_until(lambda: alive() == ["gg0"])
    assert alive() == ["gg0"], alive()
    sp_main.sp_panels = [sh.main.id, "*"]                     # MAIN + tutti i PANEL
    w.ctrl.trigger(sh.main.cues[0], cascade=False)
    wait_until(lambda: alive() == ["Main", "gg0"])
    w.ctrl.start_cue(sp_main, cascade=False)
    wait_until(lambda: alive() == [])
    assert alive() == [], alive()
    p = os.path.join(TMP, "grid.cueshow")
    w.show_data.save(p)
    l2 = Show.load(p).lists[1]
    k = next((c for c in l2.cues if c.hotkey == "F4"), None)
    assert l2.is_grid and k is not None and k.color == "#7a2730"
    s3 = next(c for c in l2.cues if c.type == "stoppanel")
    assert s3.sp_panels == ["main", "*"], s3.sp_panels
    w.ctrl.stop_all(0)
    run(0.2)


def t_grid_solo_blind_groups(w):
    """GRID: Solo con fade, BLIND (vumeter sì, uscita no), gruppo aperto, rilascio sopra un gruppo."""
    from PySide6.QtCore import Qt
    sh = Show()
    sh.settings["output_fullscreen"] = False
    w._set_show(sh)
    run(0.2)
    w.add_panel("grid")
    run(0.2)
    lst = w.show_data.current
    w.grid_add_files([tone(f"so{i}.wav", sec=5, freq=350 + 70 * i) for i in range(2)], [0, 0, 0])
    run(0.2)
    a, b = lst.cues[:2]

    def alive():
        return sorted(w.show_data.by_id(k).name for k, i in w.ctrl.instances.items() if not i.done)
    # SOLO nella barra, usabile con le Opzioni chiuse
    w.grid.set_editing(False)
    assert not w.grid.solo.isHidden() and w.grid.solo.parent() is not w.grid.size_box
    w.grid.solo.setChecked(True)
    assert lst.grid_solo is True
    lst.grid_fade = 0.3
    w.grid_activate(a)
    wait_until(lambda: alive() == ["so0"])
    w.grid_activate(b)
    wait_until(lambda: alive() == ["so1"])
    assert alive() == ["so1"], alive()
    w.ctrl.stop_all(0)
    run(0.2)
    def out_level():
        return max((float(o.meter.max()) for o in w.audio.outputs.values() if len(o.meter)), default=0.0)
    wait_until(lambda: out_level() < 1e-4)                   # la coda del vumeter delle cue appena fermate
    w.ctrl.set_blind(lst.id, True)
    w.grid_activate(a)
    wait_until(lambda: a.id in w.ctrl.instances and w.ctrl.instances[a.id].voice is not None
               and float(w.ctrl.instances[a.id].voice.meter.max()) > 0.01)
    inst = w.ctrl.instances[a.id]
    run(0.3)
    out = out_level()
    assert inst.blind and float(inst.voice.meter.max()) > 0.01 and out < 1e-4, (inst.blind, out)
    w.ctrl.set_blind(lst.id, False)
    w.ctrl.stop_all(0)
    run(0.2)
    g = Cue("group", number="30", name="Gruppo")
    lst.cues.append(g)
    w._grid_sync(force=True)
    w.grid.open_group(g)
    w.grid_group_add_cue("memo", g, 0)
    w.grid_group_add_cue("wait", g, 0)
    assert [c.type for c in lst.cues if c.group == g.id] == ["wait", "memo"]
    w.grid.close_group()
    w.grid_add_to_group(b, g)
    assert b.group == g.id and not b.grid_pos
    w.show_list(0)
    run(0.1)
    sh.main.cues[:] = [Cue("group", number="1", name="G"), Cue("memo", number="2", name="x")]
    w.model.beginResetModel()
    w.model.endResetModel()
    md = w.model.mimeData([w.model.index(1, 0)])
    w.model.dropMimeData(md, Qt.MoveAction, -1, -1, w.model.index(0, 0))
    run(0.1)
    assert sh.main.cues[1].group == sh.main.cues[0].id


def t_history_versions(w):
    """Annulla / ripeti, ritorno a un punto della cronologia, versioni dello show con annotazioni."""
    from PySide6.QtWidgets import QInputDialog
    from cuestage.history import MAX_STEPS
    from cuestage.i18n import tr
    sh = Show()
    sh.settings["output_fullscreen"] = False
    w._set_show(sh)
    run(0.1)
    h = w.history
    assert not w.act_undo.isEnabled() and len(h.steps) == 1
    w.add_cue("memo")
    w._history_flush()
    c = sh.main.cues[0]
    c.name = "Prima"
    sh.dirty = True
    run(0.7)                                                # la modifica si fissa da sola dopo una pausa
    assert len(h.steps) == 3, [s.desc for s in h.steps]
    assert "Prima" in h.steps[-1].desc, h.steps[-1].desc
    a = Cue("audio", number="9", file=tone("hist.wav", sec=6))
    sh.main.cues.append(a)
    w.model.set_show(sh)
    sh.dirty = True
    w._history_flush()
    w.ctrl.start_cue(a)
    run(0.3)
    playing = a.id in w.ctrl.instances
    sh.settings["master_db"] = -6.0                         # comandi dal vivo: non entrano nella cronologia
    sh.dirty = True
    w._history_flush()
    n = len(h.steps)
    w.act_undo.trigger()
    assert [x.id for x in sh.main.cues] == [c.id], [x.name for x in sh.main.cues]
    assert sh.settings["master_db"] == -6.0
    if playing:
        assert a.id in w.ctrl.instances                     # annullare non ferma le cue in corso
    w.act_undo.trigger()
    assert sh.main.cues[0].name == "", sh.main.cues[0].name
    assert w.inspector.cue is None or w.inspector.cue in sh.main.cues
    w.act_redo.trigger()
    w.act_redo.trigger()
    assert [x.name for x in sh.main.cues] == ["Prima", ""] and len(h.steps) == n
    w.history_go(0)                                         # torna al punto di partenza
    assert sh.main.cues == [] and h.can_redo()
    w.history_go(n - 1)
    assert len(sh.main.cues) == 2
    # una nuova modifica dopo un annulla cancella i passi «ripeti»
    w.act_undo.trigger()
    sh.main.cues[0].notes = "nota"
    sh.dirty = True
    w._history_flush()
    assert not h.can_redo() and len(h.steps) == n
    w.ctrl.stop_all(0)
    run(0.2)
    # PANEL creato e annullato: sparisce anche il tab
    w.add_panel()
    w._history_flush()
    assert len(sh.lists) == 2
    w.undo()
    assert len(sh.lists) == 1 and sh.view == 0
    # versioni
    orig_text = QInputDialog.getText
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Tournée", True))
    try:
        v = w.new_version()                                 # copia della versione attiva e passa a quella
    finally:
        QInputDialog.getText = orig_text
    assert v is not None and sh.active_version == v.id and len(sh.versions) == 2
    assert "Tournée" in w.windowTitle() and "Tournée" in w.version_btn.text()
    first = sh.versions[0]
    sh.main.cues[0].name = "Solo in tournée"
    sh.active.notes = "Teatro piccolo: niente video"
    sh.dirty = True
    w._history_flush()
    assert first.lists[0].cues[0].name == "Prima"           # l'altra versione non cambia
    w.versions_dialog()
    run(0.1)
    d = w._ver_dialog
    d.list.setCurrentRow(0)
    run(0.05)
    assert tr("{0} cue diverse", 1) in d.meta.text(), d.meta.text()
    d.notes.setPlainText("Versione per i teatri grandi")
    w._history_flush()
    assert first.notes == "Versione per i teatri grandi"
    w.switch_version(first.id)
    assert sh.main.cues[0].name == "Prima" and sh.active is first
    w.undo()                                                # il cambio di versione si annulla
    assert sh.active.name == "Tournée" and sh.main.cues[0].name == "Solo in tournée"
    w.redo()
    assert sh.active is first
    d.close()
    # salvataggio e riapertura con le versioni e le annotazioni
    path = os.path.join(TMP, "versioni.cueshow")
    sh.path = path
    assert w.save()
    QSettings("CueStage", "CueStage").remove("last_show")   # le finestre aperte dopo non devono riaprirlo
    assert not sh.dirty
    data = json.load(open(path, encoding="utf-8"))
    assert data["active_version"] == first.id and len(data["versions"]) == 2
    assert [x.get("name") for x in data["cues"]] == ["Prima"]          # i CueStage precedenti aprono l'attiva
    back = Show.load(path)
    tour = next(x for x in back.versions if x.name == "Tournée")
    assert tour.notes == "Teatro piccolo: niente video" and tour.lists[0].cues[0].name == "Solo in tournée"
    assert back.active.notes == "Versione per i teatri grandi"
    # dopo il salvataggio: annulla → modificato, ripeti → di nuovo come il file salvato
    w.undo()
    assert sh.dirty
    w.redo()
    assert not sh.dirty
    # cronologia dalla finestra e versione creata da un passo vecchio
    w.history_dialog()
    run(0.1)
    hd = w._hist_dialog
    assert hd.list.count() == len(h.steps)
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Dal passo 1", True))
    try:
        w.history_to_version(1)
    finally:
        QInputDialog.getText = orig_text
    newv = sh.versions[-1]
    assert newv.name == "Dal passo 1" and sh.active is first
    assert [x.name for x in newv.lists[0].cues] == [""], [x.name for x in newv.lists[0].cues]
    hd.close()
    # show di una versione precedente (senza versioni): una sola versione
    old = Show.from_data({"cues": [Cue("memo", name="x").to_dict()]}, None)
    assert len(old.versions) == 1 and old.main.cues[0].name == "x"
    # i passi più vecchi si perdono oltre il limite
    assert len(h.steps) <= MAX_STEPS


def t_notice_and_addresses():
    """Avviso iniziale in tutte le lingue (con l'autore) e indirizzi di tutte le interfacce per il web."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QLabel
    from cuestage.ui_disclaimer import AUTHOR, NOTICE, DisclaimerDialog
    from cuestage.webserver import local_addresses, local_interfaces
    d = DisclaimerDialog("en", "1.2.3")
    text = " ".join(lab.text() for lab in d.findChildren(QLabel))
    assert AUTHOR == "Designed by Diego Fantoma" and AUTHOR in text and "1.2.3" in text
    for code, _name, title, _body, ok, quit_ in NOTICE:
        assert d.findChild(QLabel, f"notice_{code}") is not None and title in text, code
        assert ok in d.ok_btn.text() and quit_ in d.quit_btn.text()
    assert d.ok_btn.text().startswith("I understand")             # prima la lingua dell'interfaccia
    QTimer.singleShot(50, d.ok_btn.click)
    assert d.exec() == QDialog.Accepted
    d2 = DisclaimerDialog("sl")
    QTimer.singleShot(50, d2.quit_btn.click)
    assert d2.exec() == QDialog.Rejected
    ifs = local_interfaces()
    assert all(not ip.startswith("127.") for ip, _n in ifs)
    assert local_addresses() == ([ip for ip, _n in ifs] or ["127.0.0.1"])
    # impostazioni: tutte le interfacce nella tendina, le altre sotto il QR code
    import cuestage.webserver as ws
    from cuestage.ui_dialogs import SettingsDialog
    from cuestage.model import DEFAULT_SETTINGS
    import copy
    orig = ws.local_interfaces
    ws.local_interfaces = lambda: [("192.168.1.20", "eth0"), ("10.0.0.5", "wlan0")]
    try:
        sd = SettingsDialog(copy.deepcopy(DEFAULT_SETTINGS), lambda *a, **k: None, None)
    finally:
        ws.local_interfaces = orig
    items = [sd.web_ip.itemData(i) for i in range(sd.web_ip.count())]
    assert items == ["192.168.1.20", "10.0.0.5", "127.0.0.1"], items
    assert "wlan0" in sd.web_ip.itemText(1)
    assert sd.web_address(False) == "http://192.168.1.20:8080/"
    assert "http://10.0.0.5:8080/" in sd.web_url.text(), sd.web_url.text()
    sd.web_ip.setCurrentIndex(1)
    assert sd.web_address(False) == "http://10.0.0.5:8080/" and "http://192.168.1.20:8080/" in sd.web_url.text()
    sd.deleteLater()


def t_inspector_layout(w):
    """Ispettore: tab Base/Trigger/Tempi e loop/Audio/Video, preset audio+video insieme, «Solo trigger»,
    file modificabile, dimensione e zoom della vista del crop."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtWidgets import QDoubleSpinBox, QInputDialog, QLineEdit
    from cuestage.i18n import tr
    from cuestage.inspector.presets import CuePresetBar
    from cuestage.ui_cuelist import C_PLAYHEAD
    sh = Show()
    sh.settings["output_fullscreen"] = False
    vid = make_video("insp.mp4", sec=3)
    a = Cue("audio", number="1", name="A", file=tone("insp.wav", sec=3))
    t = Cue("audio", number="2", name="Solo T", file=tone("insp2.wav", sec=3), trigger="trigger")
    b = Cue("memo", number="3", name="B")
    v = Cue("video", number="4", name="V", file=vid)
    sh.main.cues[:] = [a, t, b, v]
    w._set_show(sh)
    wait_until(lambda: w.loader.get_info(vid) is not None and w.loader.get_info(a.file) is not None, 10)
    ins = w.inspector
    tabs = lambda: [ins.tabs.tabText(i) for i in range(ins.tabs.count())]
    ins.set_cue(v)
    run(0.3)
    assert tabs() == [tr("Base"), tr("Trigger"), tr("Tempi e loop"), tr("Audio"), tr("Video / Immagine")], tabs()
    assert isinstance(ins.tabs.cornerWidget(Qt.TopRightCorner), CuePresetBar)
    ins.set_cue(b)
    assert tabs() == [tr("Base"), tr("Trigger")] and ins.tabs.cornerWidget(Qt.TopRightCorner) is None
    # l'ultima scheda scelta si ricorda: anche passando per una cue che non ce l'ha, e al riavvio
    ins.set_cue(v)
    ins.tabs.setCurrentIndex(3)                                # Audio
    ins.set_cue(b)
    assert ins.tabs.currentIndex() == 0
    ins.set_cue(v)
    assert ins.tabs.tabText(ins.tabs.currentIndex()) == tr("Audio")
    assert QSettings("CueStage", "CueStage").value("inspector_tab") == "Audio"
    ins.tabs.setCurrentIndex(0)
    ins.set_cue(b)
    # «Solo trigger»: «T» nella prima colonna, il GO la salta
    assert w.model.data(w.model.index(1, C_PLAYHEAD)) == "T"
    w.ctrl.set_playhead(0)
    w.go()
    run(0.2)
    assert a.id in w.ctrl.instances and t.id not in w.ctrl.instances
    assert w.ctrl.playhead == 2, w.ctrl.playhead              # dopo «A» la prossima manuale è «B»
    w.ctrl.set_playhead(1)                                     # in standby la cue «Solo trigger»: GO avvia «B»
    w.go()
    run(0.2)
    assert t.id not in w.ctrl.instances and w.ctrl.playhead == 3
    w.ctrl.trigger(t, cascade=True)                            # parte con un trigger
    run(0.2)
    assert t.id in w.ctrl.instances
    w.ctrl.stop_all(0)
    run(0.2)
    # preset unico: salva audio e video insieme, li applica insieme
    ins.set_cue(v)
    run(0.3)
    bar = ins.preset_bar
    v.level_db, v.scale = -6.0, 150.0
    QInputDialog.getText = staticmethod(lambda *a_, **k: ("scena", True))
    bar.save_new()
    assert v.preset_dict("audio")["scena"]["level_db"] == -6.0 and v.preset_dict("visual")["scena"]["scale"] == 150.0
    v.level_db, v.scale = 0.0, 100.0
    bar.combo.setCurrentText("scena")
    bar.apply()
    assert v.level_db == -6.0 and v.scale == 150.0
    QInputDialog.getText = staticmethod(lambda *a_, **k: ("scena 2", True))
    v.preset_points = [{"t": 1.0, "kind": "visual", "preset": "scena"}]
    bar.rename()
    assert "scena 2" in v.preset_dict("audio") and "scena 2" in v.preset_dict("visual")
    assert v.preset_points[0]["preset"] == "scena 2"
    # dimensione nel tab Video / Immagine
    from PySide6.QtWidgets import QSlider
    ins.tabs.setCurrentIndex(4)
    run(0.3)
    sl = [x for x in ins.findChildren(QSlider) if x.isVisible() and x.maximum() == 400][0]
    sl.setValue(200)
    assert v.scale == 200.0
    v.scale_x = 150.0                                          # deformazione: larghezza al 150%
    from cuestage.video_output import layer_geometry
    class _L:
        cue = v
        fit = v.fit
    class _I:
        width = staticmethod(lambda: 640)
        height = staticmethod(lambda: 360)
    dst = layer_geometry(_L, _I, 1920, 1080, (1920, 1080))[1]
    assert abs(dst.width() / dst.height() - 16 / 9 * 1.5) < 0.01, dst
    v.scale_x = 100.0
    # vista «Sorgente · crop»: rotella = dimensione; quadratini = dimensione, Ctrl = crop, Maiusc = simmetrico
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent, QWheelEvent
    from PySide6.QtCore import QPoint
    cv = ins.crop_panel.view
    v.scale, v.off_x, v.off_y = 100.0, 0, 0
    v.preset_points = []
    run(0.2)

    def mouse(kind, pos, mods=Qt.NoModifier):
        t = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove, "release": QEvent.MouseButtonRelease}[kind]
        btn = Qt.NoButton if kind == "move" else Qt.LeftButton
        ev = QMouseEvent(t, QPointF(pos), QPointF(cv.mapToGlobal(pos.toPoint())), btn,
                         Qt.LeftButton if kind != "release" else Qt.NoButton, mods)
        app.sendEvent(cv, ev)

    def drag(p0, p1, mods=Qt.NoModifier):
        mouse("press", p0, mods)
        mouse("move", QPointF((p0.x() + p1.x()) / 2, (p0.y() + p1.y()) / 2), mods)
        mouse("move", p1, mods)
        mouse("release", p1, mods)

    ev = QWheelEvent(QPointF(cv.width() / 2, cv.height() / 2), QPointF(0, 0), QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    app.sendEvent(cv, ev)
    assert v.scale > 100.0 and v.crop_l == 0, v.scale
    v.scale = 100.0
    cr = cv._crop_rect(cv._img_rect())
    right = QPointF(cr.right(), cr.center().y())
    before = cv._out_rect()
    drag(right, right + QPointF(cr.width() / 2, 0))            # lato destro: dimensione, sinistro fermo
    after = cv._out_rect()
    assert v.scale > 140 and v.crop_r == 0 and v.scale_x == 100.0, (v.scale, v.crop_r)
    assert abs(after.left() - before.left()) < 2 and v.off_x > 0, (before, after, v.off_x)
    v.scale, v.off_x = 100.0, 0
    run(0.05)
    cr = cv._crop_rect(cv._img_rect())
    right = QPointF(cr.right(), cr.center().y())
    drag(right, right + QPointF(cr.width() / 4, 0), Qt.ShiftModifier)    # simmetrico: centro fermo
    assert v.scale > 140 and v.off_x == 0, (v.scale, v.off_x)
    v.scale = 100.0
    ins.crop_panel.keep.setChecked(False)                      # deformazione: solo la larghezza
    run(0.05)
    cr = cv._crop_rect(cv._img_rect())
    right = QPointF(cr.right(), cr.center().y())
    drag(right, right + QPointF(cr.width() / 2, 0))
    assert v.scale == 100.0 and v.scale_x > 140 and v.scale_y == 100.0, (v.scale, v.scale_x, v.scale_y)
    ins.crop_panel.keep.setChecked(True)
    v.scale_x, v.off_x = 100.0, 0
    run(0.05)
    cr = cv._crop_rect(cv._img_rect())
    bottom = QPointF(cr.center().x(), cr.bottom())
    drag(bottom, bottom + QPointF(0, cr.height() / 2), Qt.AltModifier)    # Alt: proporzioni (solo l'altezza)
    assert v.scale == 100.0 and v.scale_y > 140 and v.scale_x == 100.0, (v.scale, v.scale_x, v.scale_y)
    ins.crop_panel.ratio_btn.click()                           # ⟲ Proporzioni
    assert v.scale_x == 100.0 and v.scale_y == 100.0
    v.off_y = 0
    run(0.05)
    cr = cv._crop_rect(cv._img_rect())
    left = QPointF(cr.left(), cr.center().y())
    drag(left, left + QPointF(cr.width() / 4, 0), Qt.ControlModifier)     # Ctrl: crop
    assert v.crop_l > 0 and v.crop_r == 0 and v.scale == 100.0, (v.crop_l, v.crop_r)
    v.crop_l = 0
    run(0.05)
    cr = cv._crop_rect(cv._img_rect())
    left = QPointF(cr.left(), cr.center().y())
    drag(left, left + QPointF(cr.width() / 4, 0), Qt.ControlModifier | Qt.ShiftModifier)   # crop simmetrico
    assert v.crop_l > 0 and abs(v.crop_l - v.crop_r) <= 1, (v.crop_l, v.crop_r)
    v.crop_l = v.crop_r = 0
    # «Tempi e loop»: grafico basso, miniature del video sotto la forma d'onda, conservate su disco
    import cuestage.media as md
    ins.tabs.setCurrentIndex(2)
    run(0.2)
    assert ins.timeline.height() == 114
    assert wait_until(lambda: (w.loader.thumbs.get(vid) or (0, 0, 0, False))[3], 30), "miniature esatte non create"
    run(0.2)
    assert ins.timeline.thumbs is not None and ins.timeline.thumbs[1] == md.thumb_count(3.0)
    # video: niente timeline dei keyframe nella scheda; fade e opacità si trascinano sulle miniature
    from cuestage.ui_keyframes import KeyframeBar
    page = ins.tabs.widget(2)
    assert not [k for k in page.findChildren(KeyframeBar) if k.isVisible()], "timeline dei keyframe ancora visibile"
    tl = ins.timeline
    v.start = v.end = 0.0
    v.fade_in = v.fade_out = 0.0
    v.opacity = 1.0
    tl.zoom_all()
    tl.update()
    xs, xfi, xfo, xe, y = tl._env_points()
    assert tl._hit(QPointF(xfi, y)) == ("fade_in",)
    tl.drag = ("fade_in",)
    tl.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(tl.t2x(1.0), y), QPointF(0, 0), Qt.NoButton,
                                  Qt.LeftButton, Qt.NoModifier))
    tl.drag = None
    assert abs(v.fade_in - 1.0) < 0.05, v.fade_in
    xs, xfi, xfo, xe, y = tl._env_points()
    mid = QPointF((xfi + xfo) / 2, y)
    assert tl._hit(mid) == ("opacity",), tl._hit(mid)
    r = tl._plot()
    tl.drag = ("opacity",)
    tl.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(mid.x(), r.top() + r.height() / 2), QPointF(0, 0),
                                  Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    tl.drag = None
    assert abs(v.opacity - 0.5) < 0.03, v.opacity
    v.fade_in, v.opacity = 0.0, 1.0
    # video: il doppio clic aggiunge punti di opacità (anche sulla linea arancione), come i punti di volume
    assert tl.point_mode == "opacity"
    r = tl._plot()
    tl.mouseDoubleClickEvent(QMouseEvent(QEvent.MouseButtonDblClick, QPointF(tl.t2x(0.8), r.top() + r.height() * 0.6),
                                         QPointF(0, 0), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    tl.drag = None
    xs, xfi, xfo, xe, y = tl._env_points()
    tl.mouseDoubleClickEvent(QMouseEvent(QEvent.MouseButtonDblClick, QPointF((xfi + xfo) / 2, y), QPointF(0, 0),
                                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    tl.drag = None
    assert len(v.op_curve) == 2 and v.curve == [], (v.op_curve, v.curve)
    v.op_curve = []
    # punti di opacità (come quelli di volume), scelti con «◐ Opacità»
    btn = [b for b in ins._pts_group.buttons() if b.property("mode") == "opacity"][0]
    btn.click()
    assert tl.point_mode == "opacity"
    r = tl._plot()
    tl._add_point(QPointF(tl.t2x(1.0), r.top() + r.height() * 0.75))
    tl.drag = None
    assert len(v.op_curve) == 1 and abs(v.op_curve[0][1] - 0.25) < 0.03 and v.curve == [], (v.op_curve, v.curve)
    assert tl._hit(QPointF(tl.t2x(1.0), tl.op2y(v.op_curve[0][1]))) == ("opoint", 0)
    from cuestage.media import curve_arrays, curve_value
    assert abs(curve_value(curve_arrays(v.op_curve + [[2.0, 1.0]]), 1.5) - 0.625) < 0.01
    # la linea arancione segue la curva dei punti; l'anteprima del cursore usa la stessa opacità
    from cuestage.engine import opacity_at
    v.op_curve = [[0.0, 0.2], [2.0, 0.8]]
    tl.update()
    assert tl._hit(QPointF((tl._env_points()[1] + tl._env_points()[2]) / 2, tl._env_points()[4])) != ("opacity",)
    assert abs(opacity_at(v, v, 1.0, 3.0) - 0.5) < 0.01
    v.fade_out = 1.0
    assert abs(opacity_at(v, v, 2.5, 3.0) - 0.8 * 0.5) < 0.01
    v.fade_out = 0.0
    # il campo «Opacità» della riga dei tempi mostra la curva al cursore; modificandolo si crea il punto
    page = ins.tabs.widget(2)
    ops = [x for x in page.findChildren(QDoubleSpinBox) if x.suffix() == " %" and x.maximum() == 100]
    assert len(ops) == 1, ops
    tl._move_cursor(tl.t2x(1.0))
    assert abs(ops[0].value() - 50.0) < 1.5, ops[0].value()
    tl._move_cursor(tl.t2x(1.5))
    ops[0].setValue(30.0)
    assert any(abs(t_ - tl.cursor_t) < 0.01 and abs(v_ - 0.3) < 0.001 for t_, v_ in v.op_curve), v.op_curve
    assert abs(v.opacity - 1.0) < 1e-6                         # il valore della cue non cambia
    v.op_curve = [[0.0, 0.3]]                                  # in riproduzione l'opacità segue la curva
    v.fade_in = 0.0
    w.ctrl.start_cue(v, cascade=False)
    assert wait_until(lambda: v.id in w.ctrl.instances and abs(w.ctrl.instances[v.id].layer.opacity - 0.3) < 0.02, 6), \
        getattr(w.ctrl.instances.get(v.id), "layer", None) and w.ctrl.instances[v.id].layer.opacity
    w.ctrl.stop_all(0)
    run(0.3)
    v.op_curve = []
    [b for b in ins._pts_group.buttons() if b.property("mode") == "volume"][0].click()
    # nella scheda Video / Immagine la timeline dei keyframe non si vede
    assert not ins.crop_panel.kbar.isVisible()
    cache = md._thumb_cache_path(vid, md.thumb_count(3.0))
    assert cache and os.path.isfile(cache)
    orig_ff = md.FFMPEG
    real_run = md.subprocess.run
    md.subprocess.run = lambda *a_, **k: (_ for _ in ()).throw(AssertionError("ffmpeg rilanciato"))
    try:
        assert md.make_thumbs(vid, 3.0)[1] == md.thumb_count(3.0)       # dalla cache, senza ffmpeg
    finally:
        md.subprocess.run = real_run
        md.FFMPEG = orig_ff
    # cue solo audio: stessa interfaccia (niente timeline dei keyframe), niente linea arancione: la linea rosa è
    # il volume con i fade, il volume della cue si regola con il fader della scheda Audio; marker, keyframe e
    # cursore sulla forma d'onda
    ins.set_cue(a)
    ins.tabs.setCurrentIndex(2)
    run(0.3)
    page = ins.tabs.widget(2)
    assert not [k for k in page.findChildren(KeyframeBar) if k.isVisible()]
    tl = ins.timeline
    tl.zoom_all()
    a.level_db, a.fade_in, a.fade_out, a.start, a.end = 0.0, 1.0, 0.5, 0.0, 0.0
    xs, xfi, xfo, xe, y = tl._env_points()
    mid = QPointF((xfi + xfo) / 2, tl._vol_y(tl.x2t((xfi + xfo) / 2)))
    assert tl._hit(mid) == ("seg", ()), tl._hit(mid)                # senza punti: il tratto è il volume della cue
    assert tl._hit(QPointF(xfi, tl._vol_y(tl.x2t(xfi)))) == ("fade_in",)   # i fade stanno sulla linea rosa
    a.fade_in = a.fade_out = 0.0
    ins.tabs.setCurrentIndex(ins._tab_keys.index("Audio"))
    run(0.2)
    # fader a sinistra della matrice: sposta in proporzione gli incroci accesi (−∞ e ritorno senza perdere
    # le proporzioni), alto quanto la matrice
    from PySide6.QtWidgets import QScrollArea, QSlider
    fader = [f_ for f_ in ins.findChildren(QSlider, "matrix_fader") if f_.isVisible()][-1]
    box = fader.parentWidget()
    sa = [x for x in ins.findChildren(QScrollArea) if x.isVisible() and x.height() == box.height()]
    assert sa and box.mapTo(ins, box.rect().topLeft()).x() < sa[0].mapTo(ins, sa[0].rect().topLeft()).x()
    a.matrix = [[0.0, -50.0]]                                       # file mono: una riga
    ins.refresh_fields()
    assert fader.value() == 0
    fader.setValue(-200)
    assert a.matrix == [[-20.0, -60.0]] and a.level_db == 0.0, a.matrix
    fader.setValue(0)
    assert a.matrix == [[0.0, -50.0]], a.matrix
    a.matrix = []
    ins.tabs.setCurrentIndex(2)
    run(0.1)
    m = tl.add_marker(1.0)
    assert a.markers == [m] and m["name"] == "M1"
    run(0.1)
    tl.repaint()
    hit = tl._hit(QPointF(tl.t2x(1.0), tl._plot().center().y()))
    assert hit == ("umark", 0), hit
    tl.drag = hit
    tl.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(tl.t2x(1.5), 40), QPointF(0, 0), Qt.NoButton,
                                  Qt.LeftButton, Qt.NoModifier))
    tl.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(tl.t2x(1.5), 40), QPointF(0, 0),
                                     Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert abs(a.markers[0]["t"] - 1.5) < 0.05, a.markers
    a.preset_dict("audio")["piano"] = a.preset_values("audio")
    tl.add_keyframe("audio", 2.0, "piano")
    assert any(p_.get("preset") == "piano" and abs(p_["t"] - 2.0) < 0.01 for p_ in a.preset_points), a.preset_points
    # clic (senza trascinare) = cursore, che arriva anche alla timeline dei keyframe nascosta
    px = tl.t2x(2.0)
    tl.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(px, tl._plot().bottom() - 3), QPointF(0, 0),
                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    tl.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(px, tl._plot().bottom() - 3), QPointF(0, 0),
                                     Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert abs(ins.audio_kbar.cue_time() - 2.0) < 0.15, ins.audio_kbar.cue_time()
    # trascinando, il cursore segue il mouse
    y0 = tl._plot().bottom() - 3
    tl.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(tl.t2x(0.5), y0), QPointF(0, 0),
                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    tl.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(tl.t2x(1.2), y0), QPointF(0, 0), Qt.NoButton,
                                  Qt.LeftButton, Qt.NoModifier))
    assert abs(tl.cursor_t - 1.2) < 0.05 and abs(ins.audio_kbar.cue_time() - 1.2) < 0.15, tl.cursor_t
    tl.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(tl.t2x(1.2), y0), QPointF(0, 0),
                                     Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    # snap: trascinando un punto di volume vicino a un marker si aggancia al suo tempo
    tl.set_snap(True)
    saved_markers = a.markers
    a.markers = [{"t": 1.0, "name": "S"}]
    a.curve = [[0.5, 0.0]]
    tl.update()
    run(0.05)
    tl.drag = ("point", 0)
    tl.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(tl.t2x(1.0) + 3, tl.db2y(-3)), QPointF(0, 0),
                                  Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    tl.drag = None
    assert abs(a.curve[0][0] - 1.0) < 1e-6, a.curve
    tl.drag = ("point", 0)
    tl.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(tl.t2x(1.0) + 3, tl.db2y(-3)), QPointF(0, 0),
                                  Qt.NoButton, Qt.LeftButton, Qt.AltModifier))           # Alt: niente snap
    tl.drag = None
    assert abs(a.curve[0][0] - 1.0) > 1e-4, a.curve
    a.markers, a.curve = saved_markers, []
    ins.markers_panel.reload()
    run(0.1)
    # fade delle azioni: dopo / prima del marker, dal precedente, fino al successivo (o IN / OUT)
    from cuestage.markers import marker_window
    ms_ = [{"t": 1.0, "action": "preset", "fade": 0.4}, {"t": 2.0, "action": "stop"}]
    assert marker_window(ms_, ms_[0], 0.0, 3.0) == (1.0, 0.4)
    ms_[0]["fade_mode"] = "before"
    assert marker_window(ms_, ms_[0], 0.0, 3.0) == (0.6, 0.4)
    ms_[0]["fade_mode"] = "prev"
    assert marker_window(ms_, ms_[0], 0.25, 3.0) == (0.25, 0.75)      # nessun marker prima: dall'IN
    ms_[0]["fade_mode"] = "next"
    assert marker_window(ms_, ms_[0], 0.0, 3.0) == (1.0, 1.0)
    ms_[1]["fade_mode"] = "next"
    assert marker_window(ms_, ms_[1], 0.0, 3.0) == (2.0, 1.0)         # nessun marker dopo: fino all'OUT
    # campo «Volume»: con la curva mostra livello + curva al cursore e crea il punto al cursore
    page = ins.tabs.widget(2)
    vols = [x for x in page.findChildren(QDoubleSpinBox) if x.suffix() == " dB" and x.minimum() < -50]
    a.curve = [[0.0, -6.0], [2.0, 0.0]]
    a.level_db = -3.0
    tl.update()
    tl._move_cursor(tl.t2x(1.0))
    vol = [x for x in vols if x.isVisible()][0]
    assert abs(vol.value() + 6.0) < 0.3, vol.value()
    vol.setValue(-10.0)
    assert any(abs(t_ - tl.cursor_t) < 0.01 and abs(d_ + 7.0) < 0.05 for t_, d_ in a.curve), a.curve
    assert a.level_db == -3.0
    a.curve, a.level_db = [], 0.0
    sh2 = Show.from_data(sh.to_data(None), None)
    assert sh2.by_id(a.id).markers[0]["name"] == "M1"         # i marker si salvano con lo show
    # tabella dei marker sotto la riga di IN/OUT/fade/loop: nome, tempo, note, azione
    from cuestage.ui_markers import MarkersPanel
    from PySide6.QtWidgets import QComboBox, QDoubleSpinBox
    mp = ins.markers_panel
    assert isinstance(mp, MarkersPanel) and len(mp._rows) == 1
    names = [x for x in mp.findChildren(QLineEdit) if x.isVisible() and x.text() == "M1"]
    assert names, "nome del marker non modificabile"
    names[0].setText("Tuono")
    names[0].editingFinished.emit()
    assert a.markers[0]["name"] == "Tuono"
    spins = [x for x in mp.findChildren(QDoubleSpinBox) if x.isVisible() and x.suffix() == " s" and x.maximum() > 1000]
    spins[0].setValue(0.4)                                     # colonna del tempo
    assert abs(a.markers[0]["t"] - 0.4) < 1e-6
    combos = [x for x in mp.findChildren(QComboBox) if x.isVisible() and x.findData("cue") >= 0]
    combos[0].setCurrentIndex(combos[0].findData("cue"))
    assert a.markers[0]["action"] == "cue"
    # in riproduzione: «Call Cue» al marker avvia l'altra cue, «Recall Preset» porta la cue al preset
    a.markers[0]["target"] = t.id
    a.markers.append({"t": 0.6, "name": "P", "action": "preset", "preset": "piano", "fade": 0.0})
    a.markers.append({"t": 1.6, "name": "S", "action": "stop", "target": "", "fade": 0.6,
                      "fade_mode": "before"})                   # Stop Cue con fade che finisce sul marker
    a.preset_dict("audio")["piano"] = dict(a.preset_values("audio"), level_db=-20.0)
    a.preset_points = []
    w.ctrl.start_cue(a, cascade=False)
    assert wait_until(lambda: t.id in w.ctrl.instances, 3), "Call Cue non eseguita"
    inst = w.ctrl.instances.get(a.id)
    assert wait_until(lambda: inst._aud_override is not None, 3), "Recall Preset non eseguito"
    assert wait_until(lambda: a.id not in w.ctrl.instances, 4), "Stop Cue non eseguito"
    assert t.id in w.ctrl.instances                            # lo stop riguarda solo questa cue
    w.ctrl.stop_all(0)
    run(0.2)
    a.markers, a.preset_points, a.level_db = [], [], 0.0
    a.presets = {"audio": {}, "visual": {}}
    # percorso del file modificabile nel tab Base (IN/OUT restano)
    ins.set_cue(a)
    ins.tabs.setCurrentIndex(0)
    run(0.2)
    a.start = 0.5
    le = ins.file_edit
    assert isinstance(le, QLineEdit) and le.text() == a.file
    le.setText(t.file)
    le.editingFinished.emit()
    run(0.2)
    assert a.file == t.file and a.start == 0.5
    sh.dirty = False


def t_languages():
    """File di lingua: completi, con gli stessi segnaposto {0}… dell'italiano; cambio di lingua e ritorno."""
    import importlib.util
    import cuestage.i18n as i18n
    spec = importlib.util.spec_from_file_location("lang_update", os.path.join(ROOT, "tools", "lang_update.py"))
    lu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lu)
    src = lu.source_strings()
    langs = i18n.available()
    assert {"it", "en", "sl"} <= set(langs), langs
    for code in ("en", "sl"):
        cat = i18n.load(code)
        missing = [k for k in src if k not in cat]
        assert not missing, (code, len(missing), missing[:5])
        wrong = [k for k in src if lu.placeholders(k) != lu.placeholders(cat[k])]
        assert not wrong, (code, wrong[:5])
    try:
        assert i18n.set_language("en") == "en"
        assert i18n.tr("Pagina {0}", 2) == "Page 2"
        assert i18n.tr("testo che non esiste") == "testo che non esiste"
        assert i18n.set_language("sl") == "sl" and i18n.tr("Salva") == "Shrani"
        assert i18n.set_language("xx") == "it"
    finally:
        i18n.set_language("it")
    assert i18n.tr("Pagina {0}", 2) == "Pagina 2"


def t_wav_without_ffmpeg():
    """I WAV si leggono anche se ffprobe/ffmpeg non rispondono (lettura diretta del file)."""
    import cuestage.media as media
    path = tone("noff.wav", sec=1, freq=440)
    ref = media.decode_audio(path, channels=1)
    old = media.FFPROBE, media.FFMPEG
    media.FFPROBE = media.FFMPEG = os.path.join(TMP, "manca-ffmpeg")
    try:
        info = media.probe(path)
        assert info["has_audio"] and info["channels"] == 1 and abs(info["duration"] - 1.0) < 0.01, info
        a = media.decode_audio(path, channels=1)
        assert a.shape == ref.shape and int(np.abs(a.astype(int) - ref.astype(int)).max()) <= 1
        assert "manca-ffmpeg" in media.LAST_PROBE_ERROR or "FileNotFound" in media.LAST_PROBE_ERROR, \
            media.LAST_PROBE_ERROR
    finally:
        media.FFPROBE, media.FFMPEG = old
        media.LAST_PROBE_ERROR = ""


def t_ducking(w):
    """Ducking: gate/attacco/rilascio, riduzione della cue e del gruppo, Master ducking, finestra, icona."""
    from cuestage.ducking import Ducker, duck_settings
    from cuestage.ui_duckicon import duck_state
    d = Ducker()
    d.cfg = duck_settings({"ducking": {"threshold_db": -30, "attack_ms": 100, "hold_ms": 200,
                                         "release_ms": 500}})
    d.feed(0.5, 4800)                       # 100 ms sopra soglia: riduzione piena
    assert d.gate and abs(d.env - 1.0) < 1e-6, d.env
    d.feed(0.0, 4800)                       # 100 ms di silenzio: ancora in hold
    assert not d.gate and d.env == 1.0, d.env
    d.feed(0.0, 9600)                       # finito l'hold (200 ms), 100 ms di rilascio: 80%
    assert abs(d.env - 0.8) < 1e-6, d.env
    d.feed(0.01, 48000)                     # −40 dB: sotto soglia
    assert not d.gate and d.env == 0.0

    class FakeStream:
        def stop(self):
            pass

        def close(self):
            pass
    sh = Show()
    sh.settings["output_fullscreen"] = False
    w._set_show(sh)
    run(0.2)
    duck = w.audio.duck
    duck.open = lambda: False               # niente schede in ingresso nei test: ascolto simulato
    w.set_ducking({"enabled": True, "threshold_db": -30, "attack_ms": 0, "hold_ms": 0, "release_ms": 0})
    duck.stream = FakeStream()
    msgs = []
    w.ctrl.message.connect(msgs.append)
    # tono lungo: sui Mac di GitHub i tempi si allungano e la cue non deve finire durante la prova
    c = Cue("audio", number="1", file=tone("duck.wav", sec=30, freq=500), duck_db=20.0)
    g = Cue("group", number="2", group_mode="all", duck_db=12.0)
    m = Cue("audio", number="3", file=c.file, group=g.id)
    sh.main.cues[:] = [c, g, m]
    w.ctrl.trigger(c, cascade=False)

    def vmeter():
        i = w.ctrl.instances.get(c.id)
        return float(i.voice.meter.max()) if i is not None and i.voice is not None else -1.0
    import cuestage.media as media
    if not wait_until(lambda: vmeter() > 0.02, 15):
        raise AssertionError((vmeter(), msgs, duck.error, w.audio.status, os.path.getsize(c.file),
                              media.probe(c.file), media.FFMPEG, media.FFPROBE, media.LAST_PROBE_ERROR))
    if media.LAST_PROBE_ERROR:
        print("AVVISO (diagnostica): " + media.LAST_PROBE_ERROR)
    run(0.3)
    inst = w.ctrl.instances[c.id]
    m0 = vmeter()
    assert duck_state(inst, w.ctrl) == "on"
    duck.feed(0.5, 256)                     # parla il microfono (attacco 0: riduzione subito piena)
    assert wait_until(lambda: 0.07 < vmeter() / m0 < 0.14, 10), (m0, vmeter())   # −20 dB
    assert duck_state(inst, w.ctrl) == "duck"
    duck.feed(0.0, 256)
    assert wait_until(lambda: abs(vmeter() / m0 - 1) < 0.1, 10), (m0, vmeter())
    assert duck_state(inst, w.ctrl) == "on"
    w.ctrl.trigger(m, cascade=False)
    wait_until(lambda: m.id in w.ctrl.instances)
    assert w.ctrl.instances[m.id].duck_db == 12.0          # ereditato dal gruppo
    w.ctrl.stop_cue(m.id, 0)
    # Master ducking col tasto Duck
    c.duck_db = 0.0

    def out_level():
        return max((float(o.meter.max()) for o in w.audio.outputs.values() if len(o.meter)), default=0.0)
    run(0.4)
    wait_until(lambda: abs(vmeter() / m0 - 1) < 0.1, 10)
    o0 = out_level()
    w.set_ducking(dict(duck_settings(sh.settings), master_db=6.0))
    w.duck_btn.setChecked(True)
    assert duck.master_active and duck_settings(sh.settings)["master_on"]
    duck.feed(0.5, 256)
    w._update_duck_btn()
    assert w._duck_look == "red"
    assert wait_until(lambda: 0.42 < out_level() / o0 < 0.6, 10), (o0, out_level())  # −6 dB
    duck.feed(0.0, 256)
    w.duck_btn.setChecked(False)
    # DIM: master fisso a −6 dB, anche senza microfono; da OSC e da MIDI
    assert wait_until(lambda: abs(out_level() / o0 - 1) < 0.1, 10), (o0, out_level())
    w.dim_btn.click()
    assert duck_settings(sh.settings)["dim_on"] and duck.dim_active and w._dim_look == "on"
    assert wait_until(lambda: 0.42 < out_level() / o0 < 0.6, 10), (o0, out_level())
    duck.feed(0.5, 256)                     # Duck spento: il microfono non cambia nulla
    run(0.2)
    assert 0.42 < out_level() / o0 < 0.6, (o0, out_level())
    duck.feed(0.0, 256)
    w._osc_message("/dim", [0])
    assert not w.dim_btn.isChecked() and not duck_settings(sh.settings)["dim_on"]
    assert wait_until(lambda: abs(out_level() / o0 - 1) < 0.1, 10), (o0, out_level())
    w._osc_message("/dim", [])
    assert w.dim_btn.isChecked()
    sh.settings["midi_map"]["dim"] = "note 1 60"
    w._midi_message("note 1 60")
    assert not w.dim_btn.isChecked() and not duck.dim_active
    w._osc_message("/duck", [1])
    assert w.duck_btn.isChecked()
    w._osc_message("/duck", [0])
    assert not w.duck_btn.isChecked()
    # finestra: le modifiche valgono subito, «Annulla» le toglie
    w.ducking_dialog()
    dlg = w._duck_dlg
    assert duck.monitor
    dlg.thr.setValue(-20)
    assert duck_settings(sh.settings)["threshold_db"] == -20
    dlg.reject()
    assert duck_settings(sh.settings)["threshold_db"] == -30 and not duck.monitor
    w.ctrl.stop_all(0)
    w.set_ducking({"enabled": False})
    assert duck.stream is None
    run(0.2)


def t_pfl_buttons(w):
    """Pulsante PFL: colonna della lista e pulsante nella casella del GRID (non avvia la cue)."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from cuestage.ui_cuelist import C_PFL
    sh = Show()
    sh.settings["output_fullscreen"] = False
    w._set_show(sh)
    w._apply_settings()
    run(0.1)
    assert w.table.isColumnHidden(C_PFL)               # preascolto non configurato: niente colonna
    sh.settings["preview"] = {"enabled": True}
    w._apply_settings()
    a = Cue("audio", number="1", file=tone("pflb.wav", sec=4))
    m = Cue("memo", number="2")
    sh.main.cues[:] = [a, m]
    w.model.beginResetModel()
    w.model.endResetModel()
    run(0.1)
    assert not w.table.isColumnHidden(C_PFL)
    w.ctrl.set_playhead(1)

    def click_pfl(row):
        w.table.scrollTo(w.model.index(row, C_PFL))
        run(0.05)
        r = w.table.visualRect(w.model.index(row, C_PFL))
        QTest.mouseClick(w.table.viewport(), Qt.LeftButton, Qt.NoModifier, r.center())
        run(0.05)
    click_pfl(0)
    assert a.pfl, (w.table.visualRect(w.model.index(0, C_PFL)), w.table.viewport().size(), w.show_data.current.name)
    assert w.ctrl.playhead == 1, w.ctrl.playhead       # acceso, il playhead non si sposta
    click_pfl(1)
    assert not getattr(m, "pfl", False)                 # i memo non hanno PFL
    click_pfl(0)
    assert not a.pfl
    # GRID: il pulsante dentro la casella
    w.add_panel("grid")
    run(0.2)
    lst = w.show_data.current
    w.grid_add_files([tone("pflg.wav", sec=4)], [0, 0, 0])
    run(0.2)
    gc = lst.cues[0]
    w.grid.canvas.update()
    run(0.1)
    hit = w.grid.canvas.pfl_hits.get(gc.id)
    assert hit is not None, w.grid.canvas.pfl_hits
    QTest.mouseClick(w.grid.canvas, Qt.LeftButton, Qt.NoModifier, hit.center().toPoint())
    run(0.2)
    assert gc.pfl and gc.id not in w.ctrl.instances      # PFL acceso, la cue non è partita
    r = w.grid.canvas.cell_rect(0, 0)
    QTest.mouseClick(w.grid.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(int(r.center().x()), int(r.bottom() - 12)))
    wait_until(lambda: gc.id in w.ctrl.instances)
    assert gc.id in w.ctrl.instances and w.ctrl.instances[gc.id].pfl   # il resto della casella la avvia
    w.ctrl.stop_all(0)
    run(0.2)


def t_preview_and_mixer_duck(w):
    """Preascolto (cue e gruppi, fuori dal master, col suo volume) e fader del mixer agganciati al ducking."""
    from cuestage.model import db_to_lin
    sh = Show()
    sh.settings["output_fullscreen"] = False
    sh.settings["preview"] = {"enabled": True, "device": "", "ch_l": 1, "ch_r": 2, "db": 0.0}
    w._set_show(sh)
    w._apply_settings()
    run(0.2)
    assert w.pfl_box.isVisibleTo(w)
    f = tone("pfl.wav", sec=6, freq=600)
    c = Cue("audio", number="1", file=f, pfl=True)
    g = Cue("group", number="2", group_mode="all", pfl=True)
    m = Cue("audio", number="3", file=f, group=g.id)
    sh.main.cues[:] = [c, g, m]

    def out_level():
        return max((float(o.meter.max()) for o in w.audio.outputs.values() if len(o.meter)), default=0.0)
    w.master_slider.setValue(-600)          # master a −60 dB: il preascolto non ne risente
    w.ctrl.trigger(c, cascade=False)
    wait_until(lambda: c.id in w.ctrl.instances and w.ctrl.instances[c.id].voice is not None
               and out_level() > 0.1)
    inst = w.ctrl.instances[c.id]
    assert inst.pfl and inst.voice.pfl
    run(0.3)
    lv = out_level()
    assert lv > 0.1, lv
    w.pfl_slider.setValue(-200)             # volume del preascolto −20 dB
    run(0.4)
    assert 0.07 < out_level() / lv < 0.14, (lv, out_level())
    assert abs(w.audio.preview_gain - db_to_lin(-20)) < 1e-6
    c.pfl = False                            # tolto dal vivo: torna sulle uscite, col master a −60 dB
    run(0.4)
    assert not inst.voice.pfl and out_level() < 0.01, out_level()
    w.ctrl.trigger(m, cascade=False)
    wait_until(lambda: m.id in w.ctrl.instances)
    assert w.ctrl.instances[m.id].pfl          # preascolto ereditato dal gruppo
    w.ctrl.stop_all(0)
    w.master_slider.setValue(0)
    w.pfl_slider.setValue(0)
    run(0.2)

    # Mixer: fader agganciati alla curva del ducking
    class FakeMixers:
        mixers = [{"id": "mx", "name": "Banco"}]

        def __init__(self):
            self.sent = []

        def find(self, mid):
            return self.mixers[0] if mid == "mx" else None

        def fader(self, mid, kind, n, db, fade=0.0, report=True):
            self.sent.append((kind, n, db))

        def __getattr__(self, k):
            return lambda *a, **kw: None
    old_mm = w.ctrl.mixers
    fm = w.ctrl.mixers = FakeMixers()
    duck = w.audio.duck

    class FakeStream:
        def stop(self):
            pass

        def close(self):
            pass
    duck.open = lambda: setattr(duck, "stream", FakeStream()) or True
    w.set_ducking({"enabled": False, "attack_ms": 0, "hold_ms": 0, "release_ms": 0, "threshold_db": -30})
    mc = Cue("mixer", number="9", mix_target="mx", mix_action="duck", mix_kind="ch", mix_channels="1-2",
             mix_level=-5.0, mix_duck_db=15.0)
    sh.main.cues.append(mc)
    try:
        w.ctrl.trigger(mc, cascade=False)
        run(0.1)
        assert mc.id in w.ctrl.instances and duck.listening and mc.id in duck.hooks
        assert fm.sent[-2:] == [("ch", 1, -5.0), ("ch", 2, -5.0)], fm.sent
        duck.feed(0.5, 256)
        run(0.2)
        assert fm.sent[-1] == ("ch", 2, -20.0), fm.sent[-3:]
        duck.feed(0.0, 256)
        run(0.2)
        assert fm.sent[-1] == ("ch", 2, -5.0), fm.sent[-3:]
        duck.feed(0.5, 256)
        run(0.2)
        w.ctrl.stop_cue(mc.id, 0)
        run(0.1)
        assert mc.id not in duck.hooks and not duck.listening
        assert fm.sent[-1] == ("ch", 2, -5.0), fm.sent[-3:]   # sganciato: livello normale
        # ingresso del ducking dopo il gate nella cuffia del preascolto (fuori dal master)
        w.master_slider.setValue(-600)
        w.set_ducking({"to_preview": True, "threshold_db": -30, "attack_ms": 0, "hold_ms": 0, "release_ms": 0})
        assert duck.listening

        def pump(level, cond, timeout=10.0):
            """Simula il microfono (blocchi da 512 campioni) finché cond() è vera."""
            t_end = time.monotonic() + timeout
            while time.monotonic() < t_end:
                duck.process(np.full(512, level, np.float32))
                run(0.01)
                if cond():
                    return True
            return cond()
        assert pump(0.5, lambda: out_level() > 0.3), out_level()
        assert pump(0.01, lambda: out_level() < 0.02), out_level()   # −40 dB: sotto la soglia, il gate chiude
        w.set_ducking({"to_preview": False})
        w.master_slider.setValue(0)
        assert not duck.listening
    finally:
        w.ctrl.mixers = old_mm
        del duck.open
    sh.settings["preview"] = {}
    w._apply_settings()


def make_video(name, sec=4):
    import subprocess
    from cuestage.media import FFMPEG
    path = os.path.join(TMP, name)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=48000", "-t", str(sec),
                    "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p", "-c:a", "aac", path],
                   check=True, capture_output=True)
    return path


def t_video_preload(w):
    """Video precaricato: parte con il primo fotogramma già pronto; nel loop si passa al secondo lettore."""
    sh = Show()
    sh.settings["output_fullscreen"] = False
    path = make_video("pre.mp4", sec=4)
    v = Cue("video", number="1", file=path, start=0.5, loop=True, loop_start=0.5, loop_end=2.0)
    n = Cue("video", number="2", file=path, preload=False)
    sh.main.cues[:] = [v, n]
    w._set_show(sh)
    w._apply_settings()
    w.ctrl.set_playhead(0)
    w.loader.audio_now(path)
    pre = w.ctrl.video_decks
    assert wait_until(lambda: v.id in pre.decks and pre.decks[v.id].ready, 20), list(pre.decks)
    assert wait_until(lambda: len(pre.decks) == 1, 5), list(pre.decks)   # «Precarica» spento: niente deck
    w.ctrl.trigger(v, cascade=False)
    inst = w.ctrl.instances[v.id]
    assert inst.preloaded and inst.layer.image is not None and inst.frames >= 1
    assert v.id not in pre.decks                               # il deck è passato alla cue
    decks = {id(inst.deck)}

    def swapped():
        decks.add(id(inst.deck))
        return len(decks) >= 2
    assert wait_until(swapped, 20), "il loop non è passato al secondo lettore"
    assert not inst.done
    w.ctrl.stop_all(0)
    run(0.3)
    w.ctrl.trigger(n, cascade=False)
    assert not w.ctrl.instances[n.id].preloaded
    w.ctrl.stop_all(0)
    run(0.3)


def t_tap_tempo(w):
    """Tap Tempo: BPM dalle battute; Loop e Sequence avanzano sul battito; i modi non adatti lo ignorano."""
    from cuestage.engine import tap_active
    for mode in ("loop", "sequence"):
        sh = Show()
        sh.settings["output_fullscreen"] = False
        g = Cue("group", number="7", name="T", group_mode=mode, group_tap=True, group_xfade=0.0)
        ms = [Cue("audio", number=f"7.{i}", name=f"t{i}", group=g.id, file=tone(f"tt{i}.wav", sec=2.0))
              for i in range(3)]
        sh.cues = [g, *ms]
        w._set_show(sh)
        run(0.3)
        for k in range(4):                       # 4 battute a 0,25 s = 240 BPM
            w.ctrl.clock = 50.0 + k * 0.25
            w.ctrl.group_tap(g.id)
        assert g.group_bpm == 240.0, g.group_bpm
        w.go()
        seen, t0 = [], time.monotonic()
        while time.monotonic() - t0 < 3.0 and len(seen) < 3:
            run(0.01)
            for k2, inst in w.ctrl.instances.items():
                n = w.show_data.by_id(k2).name
                if n.startswith("t") and not inst.done and n not in seen:
                    seen.append(n)
        assert seen == ["t0", "t1", "t2"], (mode, seen)
        w.ctrl.stop_all(0)
        run(0.2)
    for mode in ("random", "all", "playlist", "rplaylist"):
        assert not tap_active(Cue("group", group_mode=mode, group_tap=True))


def t_text_sequence(w):
    sh = Show()
    sh.settings["output_fullscreen"] = False
    sh.cues = [Cue("text", number="1", name="Sopratitoli", text_content="Uno\n\nDue\n\nTre")]
    w._set_show(sh)
    run(0.2)
    w.go()
    run(0.2)
    ti = w.ctrl.instances[sh.cues[0].id]
    w.go()
    run(0.1)
    assert ti.idx == 1, ti.idx
    w.ctrl.stop_all(0)
    run(0.2)


def t_mixer_x32(w):
    from sims import X32Sim
    x = X32Sim(port=0)
    sh = Show()
    sh.settings["output_fullscreen"] = False
    sh.settings["mixers"] = [dict(id="m", name="X32", driver="behringer_x32", host="127.0.0.1", port=x.port)]
    sh.cues = [Cue("mixer", number="1", name="Scena", mix_target="m", mix_action="mute",
                   mix_states={"ch": {"mute": "1-2", "on": "3"}}),
               Cue("mixer", number="2", name="Fader", mix_target="m", mix_action="fader", mix_kind="ch",
                   mix_channels="5", mix_level=-10.0)]
    w._set_show(sh)
    run(0.2)
    w.go()
    w.go()
    run(0.8)
    sent = {a: v for a, v in x.log if v}
    assert sent.get("/ch/01/mix/on") == [0] and sent.get("/ch/03/mix/on") == [1], sent
    assert abs(sent["/ch/05/mix/fader"][0] - 0.5) < 0.01, sent
    # azione «Ducking»: il livello normale si legge dal mixer come per l'azione Fader
    from PySide6.QtWidgets import QDoubleSpinBox, QPushButton
    from cuestage.i18n import tr
    d = Cue("mixer", number="3", name="Duck", mix_target="m", mix_action="duck", mix_kind="ch",
            mix_channels="5", mix_level=0.0, mix_duck_db=12.0)
    sh.cues.append(d)
    w.inspector.set_cue(d)
    w.inspector.tabs.setCurrentIndex(w.inspector._tab_keys.index("Mixer"))   # l'ispettore ricorda l'ultima scheda
    run(0.2)
    rb = next(b for b in w.inspector.findChildren(QPushButton) if tr("⟲ Leggi dal mixer") == b.text() and b.isVisible())
    rb.click()
    wait_until(lambda: abs(d.mix_level + 10.0) < 0.2)
    assert abs(d.mix_level + 10.0) < 0.2, d.mix_level
    spins = [sp for sp in w.inspector.findChildren(QDoubleSpinBox)
             if sp.isVisible() and abs(sp.value() + 10.0) < 0.2]
    assert spins, "il livello letto non compare nell'ispettore"


def t_roland_video(w):
    """Regie video Roland V-8HD / V-1HD: impostazioni proprie, cue «Regia video», byte MIDI esatti (porta finta)."""
    import mido
    from PySide6.QtWidgets import QComboBox, QPushButton, QToolButton
    from cuestage.i18n import tr
    from cuestage.ui_mixers import ControlsBox, MixersDialog, _ctl_text
    sent = []

    class FakeOut:
        name = "V-8HD MIDI"

        def send(self, msg):
            sent.append(msg.hex() if msg.type == "sysex" else (msg.type, msg.channel, *msg.bytes()[1:]))

        def close(self):
            pass
    old = mido.get_output_names, mido.open_output
    mido.get_output_names = lambda: ["V-8HD MIDI", "V-1HD MIDI"]
    mido.open_output = lambda name: FakeOut()
    try:
        sh = Show()
        sh.settings["output_fullscreen"] = False
        # show salvato quando le regie stavano tra i mixer: regia e cue vengono spostate
        sh.settings["mixers"] = [dict(id="v8", name="V-8HD", driver="roland_v8hd", midi_out="V-8HD")]
        sh.settings["switchers"] = [dict(id="v1", name="V-1HD", driver="roland_v1hd", midi_out="V-1HD", midi_ch=2)]
        sh.cues = [Cue("mixer", number="1", name="PGM 3", mix_target="v8", mix_action="control",
                       mix_ctl="pgm", mix_ctl_value=2),
                   Cue("switcher", number="2", name="AUTO 6 + CUT", sw_target="v8",
                       sw_cmds=[{"key": "auto_to", "value": 5}, {"key": "cut", "value": 1}]),
                   Cue("switcher", number="3", name="V1 CUT 4", sw_target="v1",
                       sw_cmds=[{"key": "cut_to", "value": 3}])]
        w._set_show(sh)
        run(0.2)
        assert sh.settings["mixers"] == [] and [m["id"] for m in sh.settings["switchers"]] == ["v1", "v8"]
        assert sh.cues[0].type == "switcher" and sh.cues[0].sw_cmds == [{"key": "pgm", "value": 2}]
        assert not w.mixers.mixers and w.sw_btn.isVisibleTo(w) and not w.mix_btn.isVisibleTo(w)
        for _ in range(3):
            w.go()
        wait_until(lambda: len(sent) >= 8)
        assert sent[0] == "F0 41 10 00 00 00 68 12 00 12 00 02 6C F7", sent
        assert sent[1] == "F0 41 10 00 00 00 68 12 00 12 01 05 68 F7", sent
        assert sent[2] == ("control_change", 0, 55, 127) and sent[3] == ("control_change", 0, 54, 127), sent
        # V-1HD sul canale 2: ingresso 4 sul bus B (fuori onda) e fader tutto su B
        assert sent[4:8] == [("control_change", 1, 0, 1), ("control_change", 1, 32, 0), ("program_change", 1, 3),
                             ("control_change", 1, 17, 127)], sent
        sm = w.switchers
        done = []
        sm.read_controls("v8", lambda ok, res: done.append(ok))
        wait_until(lambda: done)
        assert sm.control_info("v8", "pgm")["value"] == 2 and sm.control_info("v8", "pst")["value"] == 5
        assert _ctl_text(sm.control_info("v8", "trans_time"), 15) == "1.5 s"
        assert _ctl_text(sm.control_info("v8", "lvl_9"), 97) == "+0.0 dB"
        # audio della regia: livello e mute come comandi
        del sent[:]
        sm.set_control("v8", "lvl_9", 97)
        sm.set_control("v8", "mute_out", 1)
        wait_until(lambda: len(sent) >= 2)
        assert sent == [("control_change", 0, 44, 97), ("control_change", 0, 65, 1)], sent
        # elenco: comandi della cue in chiaro
        txt = w.model.data(w.model.index(1, 0), 0)
        for col in range(w.model.columnCount()):
            t = w.model.data(w.model.index(1, col), 0) or ""
            if "AUTO" in t and "V-8HD:" in t:
                txt = t
        assert tr("AUTO a") in txt and "HDMI 6" in txt, txt
        # pannello delle regie: i comandi hanno il pulsante «Esegui»
        box = ControlsBox(sm, "v8", title="")
        for tb in box.findChildren(QToolButton):
            tb.setChecked(True)
        ex = [b for b in box.findChildren(QPushButton) if b.text() == tr("▶ Esegui")]
        assert len(ex) == 2, len(ex)
        del sent[:]
        ex[0].click()
        wait_until(lambda: sent)
        assert sent == [("control_change", 0, 54, 127)], sent
        box.deleteLater()
        w.toggle_switcher_panel()
        run(0.2)
        assert w.sw_panel.isVisible() and w.sw_panel.tabs.count() == 2
        w.sw_panel.hide()
        # le regie non compaiono tra i driver dei mixer, e viceversa
        d = MixersDialog([], w.mixers, w)
        drv = [d.driver.itemData(i) for i in range(d.driver.count())]
        assert "roland_v8hd" not in drv and "behringer_x32" in drv
        d2 = MixersDialog([], w.switchers, w, switchers=True)
        drv2 = [d2.driver.itemData(i) for i in range(d2.driver.count())]
        assert set(drv2) == {"roland_v8hd", "roland_v1hd", "obs_studio", "blackmagic_atem"}, drv2
        d.deleteLater()
        d2.deleteLater()
        # ispettore: elenco dei comandi, aggiunta di un comando
        c = sh.cues[1]
        w.inspector.set_cue(c)
        w.inspector.tabs.setCurrentIndex(w.inspector._tab_keys.index("Regia video"))
        run(0.2)
        combos = [cb for cb in w.inspector.findChildren(QComboBox) if cb.isVisible()
                  and cb.findData("auto_to") >= 0]
        assert len(combos) == 2 and combos[0].currentData() == "auto_to", [cb.currentData() for cb in combos]
        addb = next(b for b in w.inspector.findChildren(QPushButton) if b.text() == tr("+ Aggiungi comando")
                    and b.isVisible())
        addb.click()
        run(0.2)
        assert len(c.sw_cmds) == 3
        combos = [cb for cb in w.inspector.findChildren(QComboBox) if cb.isVisible() and cb.findData("memory") >= 0]
        combos[-1].setCurrentIndex(combos[-1].findData("memory"))
        run(0.2)
        assert c.sw_cmds[2]["key"] == "memory"
    finally:
        mido.get_output_names, mido.open_output = old


def t_obs_studio(w):
    """OBS Studio come regia video, protocollo 5 (OBS 28+) e 4.x (OBS 27), simulato con password: scene, Studio
    sì/no, taglio, sorgenti, audio, registrazione; riconoscimento nella ricerca in rete."""
    for proto in (5, 4):
        _obs_run(w, proto)


def _obs_run(w, proto):
    from sims import OBSSim
    from cuestage.mixers import MixerRegistry
    from cuestage.i18n import tr
    obs = OBSSim(password="segreta", protocol=proto)
    try:
        cls = MixerRegistry().load().get("obs_studio")
        assert cls is not None and cls.SWITCHER
        assert cls.probe("127.0.0.1", obs.port) == {"version": "5.5.0" if proto == 5 else "4.9.1", "auth": True}
        # ricerca: trovato anche su questo computer (localhost), pure senza nessuna rete
        g = cls.discover.__func__.__globals__
        old_port = g["PORT"]
        g["PORT"] = obs.port
        try:
            for ips in ([], ["192.0.2.250"]):
                found = cls.discover(ips)
                assert [(f["host"], f["port"]) for f in found] == [("localhost", obs.port)], found
        finally:
            g["PORT"] = old_port
        bad = cls({"host": "127.0.0.1", "port": obs.port, "password": "sbagliata"})
        try:
            bad.status()
            raise AssertionError("password sbagliata accettata")
        except Exception as e:
            assert str(e) == (tr("OBS ha chiuso il collegamento: password sbagliata?") if proto == 5
                              else tr("OBS ha rifiutato la password")), e
        sh = Show()
        sh.settings["output_fullscreen"] = False
        sh.settings["switchers"] = [dict(id="obs", name="OBS", driver="obs_studio", host="127.0.0.1", port=obs.port,
                                         password="segreta")]
        sh.cues = [Cue("switcher", number="1", name="B", sw_target="obs",
                       sw_cmds=[{"key": "go:Scena B", "value": 1}, {"key": "item:Scena A\x1fLogo", "value": 1},
                                {"key": "mute:Mic", "value": 1}, {"key": "vol:Video", "value": -120},
                                {"key": "record", "value": 1}]),
                   Cue("switcher", number="2", name="Taglio", sw_target="obs", sw_cmds=[{"key": "cut:Titoli", "value": 1}]),
                   Cue("switcher", number="3", name="Studio", sw_target="obs",
                       sw_cmds=[{"key": "studio", "value": 1}, {"key": "go:Scena A", "value": 1}])]
        w._set_show(sh)
        run(0.2)
        sm = w.switchers
        done = []
        sm.read_controls("obs", lambda ok, res: done.append((ok, res)))
        wait_until(lambda: done)
        assert done[0][0], done
        keys = {c["key"] for c in sm.controls["obs"]}
        assert {"go:Scena A", "cut:Titoli", "pvw:Titoli", "item:Scena A\x1fLogo", "mute:Mic", "vol:Video",
                "media:Video\x1f0", "record", "trans_ms"} <= keys, keys
        assert "mute:Camera" not in keys                              # ingresso senza audio
        assert sm.control_info("obs", "on_air")["items"] == ["Scena A", "Scena B", "Titoli"]
        w.go()
        wait_until(lambda: obs.record)
        assert obs.program == "Scena B" and obs.program_log[-1] == ("Scena B", "Dissolvenza")
        assert obs.items["Scena A"][1]["sceneItemEnabled"] and obs.mute["Mic"] and obs.vol["Video"] == -12.0
        w.go()
        wait_until(lambda: obs.program == "Titoli")
        run(0.2)
        assert obs.program_log[-1] == ("Titoli", "Taglio") and obs.transition == "Dissolvenza"   # poi rimessa
        w.go()
        wait_until(lambda: obs.studio and obs.program == "Scena A")
        assert ("TriggerStudioModeTransition" if proto == 5 else "TransitionToProgram", {}) in obs.log
        assert obs.program_log[-1] == ("Scena A", "Dissolvenza")
        # Pannello: i comandi «Vai a» sono pulsanti
        done.clear()
        sm.read_controls("obs", lambda ok, res: done.append(ok))
        wait_until(lambda: done)
        assert sm.control_info("obs", "take") is not None and sm.control_info("obs", "on_air")["value"] == 0
    finally:
        obs.close()
        w.show_data.dirty = False


def t_atem(w):
    """Blackmagic ATEM Mini Pro simulato (UDP 9910): stato iniziale, cue «Regia video», conferma persa e
    ritrasmessa, ATEM occupato, ricerca in rete; Mini ed Extreme con i loro comandi."""
    from sims import ATEMSim
    from cuestage.mixers import MixerRegistry
    from cuestage.i18n import tr
    cls = MixerRegistry().load().get("blackmagic_atem")
    assert cls is not None and cls.SWITCHER
    atem = ATEMSim(product="ATEM Mini Pro")
    busy = ATEMSim(busy=True)
    try:
        try:
            cls({"host": "127.0.0.1", "port": busy.port}).status()
            raise AssertionError("ATEM occupato accettato")
        except Exception as e:
            assert str(e) == tr("l'ATEM rifiuta il collegamento: troppi programmi già collegati"), e
        g = cls.discover.__func__.__globals__
        old_port = g["PORT"]
        g["PORT"] = atem.port
        try:
            found = cls.discover(["192.0.2.250"])
        finally:
            g["PORT"] = old_port
        assert [(f["host"], f["model"], f["name"]) for f in found] == [("127.0.0.1", "pro", "ATEM Mini Pro")], found
        sh = Show()
        sh.settings["output_fullscreen"] = False
        sh.settings["switchers"] = [dict(id="atem", name="ATEM", driver="blackmagic_atem", host="127.0.0.1",
                                         port=atem.port, model="auto")]
        sh.cues = [Cue("switcher", number="1", name="Camera 3", sw_target="atem",
                       sw_cmds=[{"key": "go:3", "value": 1}, {"key": "dsk:1", "value": 1},
                                {"key": "record", "value": 1}, {"key": "vol:1301", "value": -100},
                                {"key": "macro:2", "value": 1}]),
                   Cue("switcher", number="2", name="Taglio", sw_target="atem",
                       sw_cmds=[{"key": "pgm:4", "value": 1}, {"key": "style", "value": 2},
                                {"key": "mix_rate", "value": 50}, {"key": "ftb", "value": 1}])]
        w._set_show(sh)
        run(0.2)
        sm = w.switchers
        done = []
        sm.read_controls("atem", lambda ok, res: done.append((ok, res)))
        wait_until(lambda: done)
        assert done[0][0], done
        keys = {c["key"] for c in sm.controls["atem"]}
        assert {"go:1", "pgm:3010", "pvw:2001", "cut", "auto", "usk:0", "dsk:1", "dsk_auto:0", "vol:1301",
                "mix:2", "master_vol", "macro:0", "macro:2", "record", "stream", "ftb"} <= keys, keys
        assert "usk:1" not in keys and "go:6000" not in keys and "macro:1" not in keys
        info = sm.control_info("atem", "on_air")
        assert info["items"][info["value"]] == "Camera 1" and "Color Bars" in info["items"], info
        atem.lose_acks = 1                                  # la prima conferma si perde: il comando è ritrasmesso
        w.go()
        wait_until(lambda: atem.recording and any(n == "MAct" for n, _d in atem.log))
        assert atem.program == 3 and atem.dsk[1] and len(atem.pids) > len(set(atem.pids))
        names = [n for n, _d in atem.log]
        assert names[:2] == ["CPvI", "DAut"] and names.count("CPvI") == 1, names     # ritrasmesso, non rieseguito
        cfsp = [d for n, d in atem.log if n == "CFSP"][0]
        assert cfsp[2:4] == (1301).to_bytes(2, "big") and int.from_bytes(cfsp[40:44], "big", signed=True) == -1000
        w.go()
        wait_until(lambda: atem.ftb)
        assert atem.program == 4 and atem.style == 2 and atem.mix_rate == 50
        done.clear()
        sm.read_controls("atem", lambda ok, res: done.append(ok))
        wait_until(lambda: done)
        assert sm.control_info("atem", "record")["value"] == 1 and sm.control_info("atem", "ftb_on")["value"] == 1
        assert sm.control_info("atem", "style")["value"] == 2
    finally:
        atem.close()
        busy.close()
        w.show_data.dirty = False
    # modelli: Mini (niente registrazione), Extreme (8 ingressi, 4 chiavi, SuperSource)
    for product, inputs, has, hasnt in (("ATEM Mini", 4, {"go:4"}, {"record", "go:6000", "usk:1"}),
                                        ("ATEM Mini Extreme ISO", 8, {"go:8", "usk:3", "record"}, set())):
        sim = ATEMSim(product=product, inputs=inputs)
        d = cls({"host": "127.0.0.1", "port": sim.port, "model": "auto"})
        try:
            keys = {c["key"] for c in d.list_controls()}
            assert has <= keys and not (hasnt & keys), (product, keys)
        finally:
            d.close()
            sim.close()


def t_midi_profiles(w):
    """MIDI out: MMC, clock (start/continue/stop), Song Position; profilo Akai MPC nell'ispettore: il comando
    scelto si aggiunge come testo in fondo ai messaggi."""
    from PySide6.QtWidgets import QSpinBox
    from cuestage.midi_profiles import profiles, render
    from cuestage.remote import parse_midi_script
    msgs = [m for _w, m in parse_midi_script("mmc all deferred_play\nmmc 3 locate 0 2 5 1\nstart\ncontinue\nstop\n"
                                              "spp 300\nmmc all record")]
    assert msgs == [[0xF0, 0x7F, 0x7F, 0x06, 0x03, 0xF7], [0xF0, 0x7F, 3, 0x06, 0x44, 0x06, 0x01, 0, 2, 5, 1, 0, 0xF7],
                    [0xFA], [0xFB], [0xFC], [0xF2, 44, 2], [0xF0, 0x7F, 0x7F, 0x06, 0x06, 0xF7]], msgs
    for bad in ("mmc all salta", "mmc all locate 0 61 0 0", "spp 20000"):
        try:
            parse_midi_script(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    p = profiles(reload=True)["akai_mpc"]
    for c in p["commands"]:                                  # ogni comando del profilo è valido
        assert parse_midi_script(render("akai_mpc", c["id"])), c["id"]
    assert render("akai_mpc", "locate_play", {"min": 1, "sec": 30}).splitlines()[1:] == \
        ["mmc all locate 0 1 30 0", "mmc all deferred_play"]
    sh = Show()
    sh.settings["output_fullscreen"] = False
    c = Cue("midiout", number="1", name="MPC", mo_msgs="cc 1 7 100")
    sh.cues = [c]
    w._set_show(sh)
    ins = w.inspector
    ins.set_cue(c)
    ins.tabs.setCurrentIndex(ins._tab_keys.index("MIDI out"))
    run(0.1)
    pc, cc, add = ins._midi_prof
    assert pc.currentData() == "akai_mpc"
    cc.setCurrentIndex(cc.findData("locate_play"))
    run(0.05)
    ins.findChild(QSpinBox, "midiprm_sec").setValue(42)
    add.click()
    run(0.05)
    lines = c.mo_msgs.splitlines()
    assert lines[0] == "cc 1 7 100" and lines[-2:] == ["mmc all locate 0 0 42 0", "mmc all deferred_play"], lines
    assert len(parse_midi_script(c.mo_msgs)) == 3
    w.show_data.dirty = False


def t_live_curve(w):
    """Curva di volume e volume cambiati nell'ispettore mentre la cue suona: valgono subito (audio e video);
    un Fade in corso non viene sovrascritto da una modifica alla sola curva."""
    import copy
    import numpy as np
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    sh = Show()
    sh.settings["output_fullscreen"] = False
    a = Cue("audio", number="1", name="A", file=tone("live.wav", sec=20))
    v = Cue("video", number="2", name="V", file=make_video("live.mp4", sec=4))
    sh.cues = [a, v]
    w._set_show(sh)
    wait_until(lambda: w.loader.get_info(a.file) is not None and w.loader.get_info(v.file) is not None, 10)

    def peak():
        vv = copy.copy(w.ctrl.instances[a.id].voice)
        vv.ramp = None
        out = np.zeros((1024, 8), np.float32)
        w.audio._mix_voice(vv, out, 1024, out.shape[1])
        return float(np.abs(out).max())
    w.ctrl.start_cue(a)
    run(0.3)
    p0 = peak()
    assert p0 > 0.01, p0
    ins = w.inspector
    ins.set_cue(a)
    ins.tabs.setCurrentIndex(ins._tab_keys.index("Tempi e loop"))
    run(0.2)
    tl = ins.timeline
    a.curve = [[0.0, 0.0]]
    tl.update()
    x, y = int(tl.t2x(0.0)) + 2, int(tl.db2y(0.0))
    QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x, y))      # punto trascinato in giù: più piano
    QTest.mouseMove(tl, QPoint(x, int(tl.db2y(-20.0))))
    QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x, int(tl.db2y(-20.0))))
    run(0.1)
    assert -21.5 < a.curve[0][1] < -18.5, a.curve
    assert abs(peak() / p0 - 10 ** (a.curve[0][1] / 20)) < 0.02, (peak(), p0, a.curve)
    # i punti rosa stanno sul volume effettivo (volume della cue + curva): il fader sposta tutto in proporzione
    a.level_db = -10.0
    a.curve = [[2.0, 0.0], [6.0, -10.0]]
    tl.update()
    assert abs(tl.pt2y(0.0) - tl.db2y(-10.0)) < 0.5 and tl._hit(QPointF(tl.t2x(2.0), tl.db2y(-10.0))) == ("point", 0)
    assert abs(tl._vol_y(4.0) - tl.db2y(-15.0)) < 0.5
    xp = int(round(tl.t2x(6.0)))
    QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, QPoint(xp, int(round(tl.pt2y(-10.0)))))
    QTest.mouseMove(tl, QPoint(xp, int(round(tl.db2y(-30.0)))))                  # punto trascinato in giù
    QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(xp, int(round(tl.db2y(-30.0)))))
    assert -21.5 < a.curve[1][1] < -18.5, a.curve                                # −30 effettivi = −10 −20
    # clic sul tratto rettilineo della linea rosa: i due punti che lo definiscono si spostano insieme
    a.curve = [[2.0, 0.0], [6.0, -10.0]]
    tl.update()
    x4 = int(round(tl.t2x(4.0)))
    assert tl._hit(QPointF(x4, tl._vol_draw_y(x4))) == ("seg", (0, 1)), tl._hit(QPointF(x4, tl._vol_draw_y(x4)))
    y4 = int(round(tl._vol_draw_y(x4)))
    QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x4, y4))
    QTest.mouseMove(tl, QPoint(x4, int(round(tl.db2y(tl.y2db(y4) - 10)))))
    QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x4, int(round(tl.db2y(tl.y2db(y4) - 10)))))
    assert abs(a.curve[0][1] + 10) < 0.6 and abs(a.curve[1][1] + 20) < 0.6 and a.curve[0][0] == 2.0, a.curve
    x1 = int(round(tl.t2x(1.0)))
    assert tl._hit(QPointF(x1, tl._vol_draw_y(x1))) == ("seg", (0,))           # prima del primo punto: solo lui
    a.curve = []
    tl.update()
    y1 = int(round(tl._vol_draw_y(x4)))
    QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x4, y1))           # senza punti: volume della cue
    QTest.mouseMove(tl, QPoint(x4, int(round(tl.db2y(tl.y2db(y1) + 6)))))
    QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x4, int(round(tl.db2y(tl.y2db(y1) + 6)))))
    assert abs(a.level_db + 4.0) < 0.6, a.level_db
    a.level_db = -10.0
    a.curve = [[2.0, 0.0], [6.0, -20.0]]
    ins.tabs.setCurrentIndex(ins._tab_keys.index("Audio"))
    run(0.1)
    from PySide6.QtWidgets import QSlider
    vo = w.ctrl.instances[a.id].voice
    m0 = vo.mtx.copy()
    from cuestage.inspector.media import CueMeter
    meter = [m_ for m_ in ins.findChildren(CueMeter) if m_.isVisible()][-1]
    vo.meter[:] = 0.5                                       # il vumeter accanto al fader legge la voce della cue
    meter.poll()
    assert meter.levels and max(meter.levels) > 0.1, meter.levels
    [f_ for f_ in ins.findChildren(QSlider, "matrix_fader") if f_.isVisible()][-1].setValue(-200)   # mentre suona
    run(0.2)
    assert m0.max() > 0 and abs(vo.mtx.max() / m0.max() - 0.1) < 1e-3, (m0, vo.mtx)          # matrice −20 dB
    a.matrix = []
    run(0.2)
    ins.tabs.setCurrentIndex(ins._tab_keys.index("Tempi e loop"))
    # la curva di volume si salva nei preset audio: cancellarla dalla cue non la toglie dal preset
    a.presets = {"audio": {}, "visual": {}}
    a.preset_dict("audio")["Piano"] = a.preset_values("audio")
    a.curve = []
    saved = a.preset_dict("audio")["Piano"]["curve"]
    assert len(saved) == 2 and saved[0] == [2.0, 0.0], saved
    assert a.apply_preset("audio", "Piano") and len(a.curve) == 2
    # volume della cue cambiato mentre suona
    a.curve = [[0.0, -20.0]]
    a.level_db = -6.0
    ins._emit()
    run(0.05)
    vo = w.ctrl.instances[a.id].voice
    assert abs((vo.ramp[1] if vo.ramp else vo.env) - 10 ** (-6 / 20)) < 1e-3       # con una rampa breve
    # un Fade in corso (rampa con stop) non viene toccato da una modifica alla sola curva
    w.ctrl.instances[a.id].voice.ramp = [0.5, 0.0, 0, 48000, "scurve", True]
    a.curve = [[0.0, -3.0]]
    ins._emit()
    assert w.ctrl.instances[a.id].voice.ramp[1] == 0.0
    w.ctrl.stop_all(0)
    a.curve, a.level_db = [], 0.0
    # video: curve di volume e opacità
    w.ctrl.start_cue(v)
    wait_until(lambda: v.id in w.ctrl.instances, 5)
    ins.set_cue(v)
    v.curve = [[0.0, -12.0]]
    v.op_curve = [[0.0, 0.5]]
    ins._emit()
    vi = w.ctrl.instances[v.id]
    assert vi.curve is not None and float(vi.curve[1][0]) == -12.0 and float(vi.op_curve[1][0]) == 0.5
    if vi.voice is not None:
        assert float(vi.voice.curve[1][0]) == -12.0
    w.ctrl.stop_all(0)
    run(0.2)
    w.show_data.dirty = False


def t_loop_edit(w):
    """Loop: marcatori in basso (separati da IN/OUT anche quando coincidono), caselle rispetto a IN e OUT,
    spostamenti in riproduzione recepiti subito, crossfade del loop."""
    import numpy as np
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QDoubleSpinBox
    from cuestage.audio_engine import SR, Voice
    from cuestage.media import curve_arrays
    # crossfade nel mixer: coda e inizio del loop si sommano a potenza costante, poi si riparte da ls + xf
    data = np.zeros((SR * 4, 1), np.float32)
    data[:SR] = 1.0                                           # 1 nel primo secondo (inizio del loop), 0 dopo
    v = Voice(data, 0.0, 0.0, True, 0.0, 2.0, 0, 1.0, curve_arrays([]), 0.0, 0.0)
    v.loop_xf = SR // 2
    v.fit_matrix(1)
    v.pos = int(1.5 * SR) - 1000                               # poco prima dell'inizio del crossfade
    out = np.zeros((4000, 1), np.float32)
    w.audio._mix_voice(v, out, 4000, 1)
    blk = out[:, 0] / max(1e-9, float(w.audio.master))
    assert abs(blk[500]) < 1e-3 and blk[1500] > 0.03 and blk[-1] > blk[1500], (blk[500], blk[1500], blk[-1])
    v.pos = 2 * SR - 10
    w.audio._mix_voice(v, np.zeros((100, 1), np.float32), 100, 1)
    assert v.wraps == 1 and v.pos == SR // 2 + 90, (v.wraps, v.pos)       # loop da IN: niente prima, ls + xf
    # l'inizio del loop parte quando il cursore è a fine loop − crossfade; poi si continua da ls + xf
    v2 = Voice(data, 0.0, 0.0, True, 1.0, 3.0, 0, 1.0, curve_arrays([]), 0.0, 0.0)
    v2.loop_xf = SR // 2
    v2.fit_matrix(1)
    v2.pos = int(2.5 * SR)                             # inizio del crossfade
    w.audio._mix_voice(v2, np.zeros((100, 1), np.float32), 100, 1)
    assert v2.wraps == 0 and v2.pos == int(2.5 * SR) + 100
    v2.pos = 3 * SR - 10
    w.audio._mix_voice(v2, np.zeros((100, 1), np.float32), 100, 1)
    assert v2.wraps == 1 and v2.pos == int(1.5 * SR) + 90 and v2.lap_len() == int(1.5 * SR), (v2.pos, v2.lap_len())
    v2.loops_left = -1
    v2.wraps = 0
    v2.seek(5.0)                                       # 3 s fino a fine loop, poi giri da 1,5 s (1,5–3)
    assert v2.pos == 2 * SR and v2.wraps == 2, (v2.pos / SR, v2.wraps)
    # «Ferma alla fine»: finite le ripetizioni la voce si ferma a fine loop, senza andare fino a OUT
    def run_voice(vv, frames=4 * SR):
        blk = 4096
        for _i in range(frames // blk + 2):
            if vv.done:
                break
            w.audio._mix_voice(vv, np.zeros((blk, 1), np.float32), blk, 1)
    v3 = Voice(data, 0.0, 0.0, True, 1.0, 2.0, 2, 1.0, curve_arrays([]), 0.0, 0.0)
    v3.fit_matrix(1)
    v3.stop_after_loop = True
    run_voice(v3)
    assert v3.done and v3.wraps == 1 and v3.pos == 2 * SR, (v3.done, v3.wraps, v3.pos / SR)
    v4 = Voice(data, 0.0, 0.0, True, 1.0, 2.0, 2, 1.0, curve_arrays([]), 0.0, 0.0)
    v4.fit_matrix(1)
    run_voice(v4, 6 * SR)
    assert v4.done and v4.pos == 4 * SR                                  # senza: prosegue fino a OUT
    # marcatori: loop alla fine (= OUT): in basso si prende il loop, in alto OUT, sulla linea il loop
    sh = Show()
    sh.settings["output_fullscreen"] = False
    a = Cue("audio", number="1", name="L", file=tone("loopedit.wav", sec=8), loop=True)
    sh.cues = [a]
    w._set_show(sh)
    wait_until(lambda: w.loader.get_info(a.file) is not None, 10)
    ins = w.inspector
    ins.set_cue(a)
    ins.tabs.setCurrentIndex(ins._tab_keys.index("Tempi e loop"))
    run(0.3)
    tl = ins.timeline
    tl.zoom_all()
    xo = tl.t2x(8.0) - 1
    r = tl._plot()
    assert tl._hit(QPointF(xo - 10, 5)) == ("marker", "end"), tl._hit(QPointF(xo - 10, 5))
    assert tl._hit(QPointF(xo - 10, r.bottom() - 4)) == ("marker", "loop_end")
    assert tl._hit(QPointF(xo, r.bottom() - 30)) == ("marker", "loop_end")       # sovrapposti: vince il loop
    # caselle del loop rispetto a IN e OUT
    a.start = 1.0
    sps = [x for x in ins.findChildren(QDoubleSpinBox) if x.isVisible() and x.specialValueText() in ("IN", "OUT")]
    ls_sp = next(x for x in sps if x.specialValueText() == "IN")
    le_sp = next(x for x in sps if x.specialValueText() == "OUT")
    ls_sp.setValue(0.5)
    assert abs(a.loop_start - 1.5) < 1e-6, a.loop_start
    le_sp.setValue(2.0)
    assert abs(a.loop_end - 6.0) < 1e-6, a.loop_end                      # 8 s (fine file) − 2
    a.loop_start, a.loop_end = 3.0, 5.0
    ins.refresh_fields()
    assert abs(ls_sp.value() - 2.0) < 1e-6 and abs(le_sp.value() - 3.0) < 1e-6
    # in riproduzione: lo spostamento del loop vale subito
    w.ctrl.start_cue(a)
    run(0.3)
    vo = w.ctrl.instances[a.id].voice
    assert vo.ls == 3 * SR and vo.le == 5 * SR, (vo.ls, vo.le)
    le_sp.setValue(4.0)                                                    # fine del loop a 4 s
    run(0.05)
    assert vo.le == 4 * SR, vo.le
    a.loop_xfade = 0.5
    ins._emit()
    assert vo.loop_xf == SR // 2
    # «Ferma alla fine» in riproduzione e durata totale
    a.loop_count, a.loop_stop = 3, True
    inst = w.ctrl.instances[a.id]
    ins._emit()
    assert vo.stop_after_loop and vo.out_end() == vo.le
    # cue di comando «Esci dal loop»: finito il giro prosegue fino a OUT (anche con «Ferma alla fine»)
    ex = Cue("control", number="2", name="Esci", action="exit_loop", target=a.id)
    sh.cues.append(ex)
    w.ctrl.start_cue(ex)
    run(0.1)
    assert vo.loops_left == 0 and not vo.stop_after_loop and vo.out_end() == vo.end
    assert inst.total() is not None
    msgs = []
    w.ctrl.message.connect(msgs.append)
    w.ctrl.stop_all(0)
    run(0.2)
    w.ctrl.start_cue(ex)                                                   # target fermo: avviso
    run(0.1)
    from cuestage.i18n import tr
    assert tr("Esci dal loop: la cue «{0}» non sta suonando in loop", a.label()) in msgs, msgs
    w.ctrl.message.disconnect(msgs.append)
    w.ctrl.stop_all(0)
    run(0.2)
    w.show_data.dirty = False


def t_volume_field(w):
    """Campo Volume sotto il grafico: segue la linea rosa al cursore (curva e fade); cliccando un tratto o
    trascinando un punto il cursore va lì; scrivendo un valore nel fade si tiene conto del fade."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDoubleSpinBox
    sh = Show()
    sh.settings["output_fullscreen"] = False
    a = Cue("audio", number="1", name="V", file=tone("volfield.wav", sec=8))
    a.level_db, a.curve, a.fade_out = -6.0, [[2.0, 0.0], [6.0, -12.0]], 1.0
    sh.cues = [a]
    w._set_show(sh)
    wait_until(lambda: w.loader.get_info(a.file) is not None, 10)
    ins = w.inspector
    ins.set_cue(a)
    ins.tabs.setCurrentIndex(ins._tab_keys.index("Tempi e loop"))
    run(0.3)
    tl = ins.timeline
    tl.zoom_all()
    sp = [x for x in ins.findChildren(QDoubleSpinBox) if x.isVisible() and x.suffix() == " dB"][0]
    for t_, exp in ((1.0, -6.0), (4.0, -12.0), (6.5, -18.0)):
        tl._move_cursor(tl.t2x(t_))
        run(0.02)
        assert abs(sp.value() - exp) < 0.2, (t_, sp.value(), exp)
    tl._move_cursor(tl.t2x(7.5))                                       # metà del fade out: −6 dB in più
    run(0.02)
    assert abs(sp.value() - (-18.0 - 6.02)) < 0.3, sp.value()
    assert abs(tl._vol_draw_y(tl.t2x(7.5)) - tl.db2y(sp.value())) < 1.0   # uguale alla linea rosa
    sp.setValue(-20.0)                                                 # scritto nel fade: il punto tiene conto
    assert abs(tl.vol_db_at(7.5) + 20.0) < 0.3, (tl.vol_db_at(7.5), a.curve)
    a.curve = [[2.0, 0.0], [6.0, -12.0]]
    tl.update()
    x4 = int(round(tl.t2x(4.0)))
    QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x4, int(round(tl._vol_draw_y(x4)))))
    QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x4, int(round(tl._vol_draw_y(x4)))))
    run(0.02)
    assert abs(tl.cursor_t - 4.0) < 0.05 and abs(sp.value() + 12.0) < 0.3, (tl.cursor_t, sp.value())
    x2 = int(round(tl.t2x(2.0)))
    QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x2, int(round(tl.pt2y(0.0)))))
    QTest.mouseMove(tl, QPoint(x2, int(round(tl.db2y(-16.0)))))
    run(0.02)
    assert abs(tl.cursor_t - 2.0) < 0.05 and abs(sp.value() + 16.0) < 0.6, (tl.cursor_t, sp.value())
    QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x2, int(round(tl.db2y(-16.0)))))
    # durante il play il campo segue la posizione in riproduzione, poi torna al cursore
    a.curve = [[0.0, 0.0], [8.0, -24.0]]
    a.fade_out = 0.0
    w.ctrl.start_cue(a)
    run(0.1)
    w.ctrl.instances[a.id].seek_to(4.0)
    ins._update_playhead()
    assert abs(sp.value() - tl.vol_db_at(tl.playhead)) < 0.2 and abs(tl.playhead - 4.0) < 0.3, (sp.value(), tl.playhead)
    w.ctrl.stop_all(0)
    run(0.2)
    ins._update_playhead()
    assert tl.playhead is None and abs(sp.value() - tl.vol_db_at(tl.cursor_t)) < 0.2
    w.show_data.dirty = False


def t_column_widths(w):
    """Colonne della lista ridimensionabili (anche # , Tipo, Nome) e larghezze ricordate al riavvio."""
    from PySide6.QtWidgets import QHeaderView
    from cuestage.ui_cuelist import C_NAME, C_NUM, C_TYPE
    hh = w.table.horizontalHeader()
    for c in (C_NUM, C_TYPE, C_NAME):
        assert hh.sectionResizeMode(c) == QHeaderView.Interactive, c
    hh.resizeSection(C_NAME, 333)
    hh.resizeSection(C_NUM, 77)
    run(0.6)
    w2 = MainWindow()
    try:
        assert w2.table.columnWidth(C_NUM) == 77 and w2.table.columnWidth(C_NAME) == 333, \
            (w2.table.columnWidth(C_NUM), w2.table.columnWidth(C_NAME))
    finally:
        w2.show_data.dirty = False
        w2.close()
    hh.resizeSection(C_NUM, 50)
    hh.resizeSection(C_NAME, 300)
    run(0.6)


def t_ducking_open_nonblocking():
    """Ingresso del ducking che non risponde (es. scheda bloccata): l'apertura non blocca l'interfaccia oltre
    pochi secondi; se poi si apre entra in funzione, se nel frattempo è stato chiuso non resta aperto."""
    from cuestage.ducking import Ducker
    d = Ducker()
    d.OPEN_TIMEOUT = 0.3
    d.cfg = {"device": "", "channel": 1}

    class FakeStream:
        closed = False

        def stop(self):
            pass

        def close(self):
            FakeStream.closed = True

    def slow(gen):
        time.sleep(0.8)
        if gen != d._gen:
            FakeStream.closed = True
            return False
        d.stream = FakeStream()
        return True
    d._open_now = slow
    t0 = time.monotonic()
    assert d.open() is False and time.monotonic() - t0 < 0.6 and d.error
    assert not d.listening
    time.sleep(0.8)
    assert d.listening                                     # aperto in sottofondo
    d.close()
    assert not d.listening
    d.open()
    d.close()                                              # chiuso mentre si apriva: non resta aperto
    time.sleep(1.0)
    assert not d.listening and FakeStream.closed


def t_video_xfade_fades(w):
    """Barra di scorrimento del grafico ingrandito; video: fade e crossfade del loop dell'immagine separati
    dall'audio (di serie uguali), crossfade dell'immagine a fine loop con il deck di riserva."""
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QScrollBar
    sh = Show()
    sh.settings["output_fullscreen"] = False
    v = Cue("video", number="1", name="V", file=make_video("vxf.mp4", sec=6), loop=True, preload=True)
    v.loop_start, v.loop_end, v.loop_count = 1.0, 3.0, 3
    sh.cues = [v]
    w._set_show(sh)
    wait_until(lambda: w.loader.get_info(v.file) is not None, 10)
    ins = w.inspector
    ins.set_cue(v)
    ins.tabs.setCurrentIndex(ins._tab_keys.index("Tempi e loop"))
    run(0.3)
    tl = ins.timeline
    tl.zoom_all()
    run(0.05)
    sb = [x for x in ins.findChildren(QScrollBar, "timeline_scroll")][-1]
    assert not sb.isVisible()
    tl.v0, tl.v1 = 0.0, 2.0                                       # ingrandito: compare la barra
    tl.repaint()
    run(0.05)
    assert sb.isVisible() and sb.maximum() > 0
    sb.setValue(sb.maximum())
    assert abs(tl.v1 - tl.duration) < 0.05 and abs((tl.v1 - tl.v0) - 2.0) < 1e-6, (tl.v0, tl.v1)
    tl.zoom_all()
    tl.repaint()
    run(0.05)
    assert not sb.isVisible()
    # fade dell'immagine: di serie uguali all'audio, poi separati (quadratini arancioni sui fade video)
    v.fade_in = 0.4
    assert v.video_fades() == (0.4, 0.0)
    v.fade_link, v.v_fade_in, v.v_fade_out = False, 1.5, 0.0
    assert v.video_fades() == (1.5, 0.0)
    xs, xfi, xfo, xe, y = tl._env_points()
    assert abs(tl.x2t(xfi) - (v.start + 1.5)) < 0.05
    yi, _yo = tl._handle_ys()
    tl.drag = ("fade_in",)
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    tl.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(tl.t2x(1.0), yi), QPointF(0, 0), Qt.NoButton,
                                  Qt.LeftButton, Qt.NoModifier))
    tl.drag = None
    assert abs(v.v_fade_in - 1.0) < 0.05 and v.fade_in == 0.4, (v.v_fade_in, v.fade_in)
    # quadratini rosa: i fade dell'audio del video
    (axi, ayi), _o = tl._audio_handles()
    assert tl._hit(QPointF(axi, ayi)) == ("afade_in",), tl._hit(QPointF(axi, ayi))
    tl.drag = ("afade_in",)
    tl.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(tl.t2x(0.8), ayi), QPointF(0, 0), Qt.NoButton,
                                  Qt.LeftButton, Qt.NoModifier))
    tl.drag = None
    assert abs(v.fade_in - 0.8) < 0.05 and abs(v.v_fade_in - 1.0) < 0.05, (v.fade_in, v.v_fade_in)
    v.fade_in = 0.4
    # linea arancione con i punti di opacità: trascinando un tratto si spostano insieme i due punti
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    v.op_curve = [[2.0, 0.8], [4.0, 0.6]]
    tl.update()
    x3 = int(round(tl.t2x(3.0)))
    y3 = int(round(tl._op_draw_y(x3)))
    assert tl._hit(QPointF(x3, y3)) == ("oseg", (0, 1)), tl._hit(QPointF(x3, y3))
    QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x3, y3))
    QTest.mouseMove(tl, QPoint(x3, int(round(tl.op2y(0.7 - 0.2)))))
    QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x3, int(round(tl.op2y(0.5)))))
    assert abs(v.op_curve[0][1] - 0.6) < 0.03 and abs(v.op_curve[1][1] - 0.4) < 0.03, v.op_curve
    v.op_curve = []
    # in riproduzione: audio e immagine con i loro fade
    v.loop_xfade_link, v.loop_xfade_video = False, 0.5
    w.ctrl.start_cue(v)
    assert wait_until(lambda: v.id in w.ctrl.instances, 5)
    inst = w.ctrl.instances[v.id]
    assert inst.env_op.ramp is not None and abs(inst.env_op.ramp[1] - 1.0) < 1e-6, inst.env_op.ramp
    # crossfade dell'immagine a fine loop: parte prima di fine loop, poi il deck entrato diventa quello della cue
    seen = []
    t_end = time.monotonic() + 15
    while time.monotonic() < t_end and not (seen and inst._xf is None and inst._xf_layer is None):
        run(0.03)
        if inst._xf is not None:
            seen.append(inst._xf_layer is not None)
        if inst.done:
            break
    assert seen and all(seen), "crossfade dell'immagine non partito"
    assert inst._xf is None and inst._xf_layer is None
    w.ctrl.stop_all(0)
    run(0.3)
    w.show_data.dirty = False


def t_levels_zero(w):
    """«Volumi uscite a 0 dB»: volume della cue e volumi delle uscite a 0 dB (anche mentre suona)."""
    from PySide6.QtWidgets import QPushButton
    from cuestage.i18n import tr
    sh = Show()
    sh.settings["output_fullscreen"] = False
    a = Cue("audio", number="1", name="Z", file=tone("zero.wav", sec=10))
    a.level_db, a.out_levels, a.matrix = -8.0, [-10.0, -12.0], [[-20.0, None]]
    sh.cues = [a]
    w._set_show(sh)
    wait_until(lambda: w.loader.get_info(a.file) is not None, 10)
    ins = w.inspector
    ins.set_cue(a)
    ins.tabs.setCurrentIndex(ins._tab_keys.index("Audio"))
    run(0.3)
    w.ctrl.start_cue(a)
    run(0.2)
    vo = w.ctrl.instances[a.id].voice
    m0 = vo.mtx.max()
    b = [x for x in ins.findChildren(QPushButton) if x.text() == tr("Volumi uscite a 0 dB") and x.isVisible()][-1]
    b.click()
    run(0.3)
    assert a.level_db == 0.0 and all(x == 0.0 for x in a.out_levels), (a.level_db, a.out_levels)
    assert a.matrix == [[0.0, None]], a.matrix                         # incroci accesi a 0, routing invariato
    assert vo.mtx.max() > m0 * 20, (m0, vo.mtx.max())                # incrocio +20 dB e uscita +10 dB
    assert abs((vo.ramp[1] if vo.ramp else vo.env) - 1.0) < 1e-3       # volume della cue a 0 dB
    w.ctrl.stop_all(0)
    run(0.2)
    w.show_data.dirty = False


def t_fsclose(w):
    """Cue «Chiudi schermo intero»: la finestra di uscita esce dallo schermo intero (o si nasconde)."""
    sh = Show()
    sh.settings["output_fullscreen"] = False
    c1 = Cue("fsclose", number="1", name="Chiudi")
    c2 = Cue("fsclose", number="2", name="Nascondi", fs_hide=True)
    sh.cues = [c1, c2]
    w._set_show(sh)
    w._create_output()
    w.output.place_on_screen(None, True)
    run(0.2)
    assert w.output.isVisible()
    w.ctrl.start_cue(c1)
    run(0.2)
    assert w.output.isVisible() and not w.output.isFullScreen()
    assert sh.settings["output_fullscreen"] is False                    # l'impostazione non cambia
    w.ctrl.start_cue(c2)
    run(0.2)
    assert not w.output.isVisible()
    w.show_data.dirty = False


def t_rotation(w):
    """Rotazione di video e immagini: campo dopo il crop, pulsanti ±90°, angoli ruotati attorno al centro,
    disegno in uscita, preset e keyframe."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QDoubleSpinBox, QPushButton
    from cuestage import keyframes as kfm
    from cuestage.video_output import Layer, layer_quad, draw_layer
    sh = Show()
    sh.settings["output_fullscreen"] = False
    c = Cue("image", number="1", name="R")
    sh.cues = [c]
    w._set_show(sh)
    ins = w.inspector
    ins.set_cue(c)
    ins.tabs.setCurrentIndex(ins._tab_keys.index("Video / Immagine"))
    run(0.2)
    sp = [x for x in ins.findChildren(QDoubleSpinBox, "rotation_spin") if x.isVisible()][-1]
    b = [x for x in ins.findChildren(QPushButton) if x.text() == "↻ 90°"][-1]
    b.click()
    assert c.rotation == 90.0, c.rotation
    sp.setValue(45.0)
    assert c.rotation == 45.0
    # angoli: ruotati attorno al centro
    c.rotation = 90.0
    lay = Layer(c.id, 1, 0, "fit", c)
    q = layer_quad(lay, QRectF(0, 0, 200, 100), 400, 300)
    tl = q[0]
    assert abs(tl.x() - 150) < 1e-6 and abs(tl.y() - (-50)) < 1e-6, (tl.x(), tl.y())   # alto-sx → in alto a destra
    # disegno: immagine larga ruotata di 90° diventa alta
    img = QImage(200, 100, QImage.Format_ARGB32)
    img.fill(QColor(255, 0, 0))
    out = QImage(400, 400, QImage.Format_ARGB32)
    out.fill(QColor(0, 0, 0))
    c.fit, c.rotation = "native", 90.0
    lay = Layer(c.id, 1, 0, "native", c)
    p = QPainter(out)
    draw_layer(p, lay, img, 400, 400, (400, 400))
    p.end()
    assert QColor(out.pixel(200, 120)).red() > 200 and QColor(out.pixel(120, 200)).red() < 50
    # preset e keyframe
    assert "rotation" in c.preset_values("visual") and "rotation" in kfm.VIS_NUM
    a = kfm.visual_values(c, kfm.BASE)
    b2 = dict(a, rotation=180.0)
    assert abs(kfm.interp_visual(a, b2, 0.5)["rotation"] - 135.0) < 1e-6
    c.rotation = 0.0
    w.show_data.dirty = False


def t_osc_profile():
    from cuestage.osc_profiles import render
    assert render("chamsys_magicq", "go", {"pb": 3}) == ("/pb/3/go", "1")
    assert render("chamsys_magicq", "goto", {"pb": 2, "cue": 12.5}) == ("/pb/2/12.5", "1")


def t_alsa_parser():
    from cuestage.alsactl import parse_contents
    txt = ("numid=8,iface=MIXER,name='Line 01 (Monitor L) Playback Volume'\n"
           "  ; type=INTEGER,access=rw---R--,values=1,min=0,max=127,step=1\n  : values=100\n"
           "  | dBminmax-min=-127.00dB,max=0.00dB\n")
    c = parse_contents(txt)[0]
    assert c.writable and round(c.to_db(100)) == -27 and c.from_db(-27) == 100


def t_cluster_redundancy():
    a = MainWindow()
    b = MainWindow()
    try:
        sh = Show()
        sh.settings["output_fullscreen"] = False
        sh.cues = [Cue("audio", number="1", name="Musica", file=tone("musica.wav")),
                   Cue("wait", number="2", name="Attesa", duration=10)]
        sh.save(os.path.join(TMP, "master.cueshow"))
        sh = Show.load(os.path.join(TMP, "master.cueshow"))
        sh.settings["output_fullscreen"] = False
        a._set_show(sh)
        old = dict(sh.settings)
        sh.settings.update(cluster_enabled=True, cluster_port=18990)
        a._apply_settings(old=old)
        run(0.3)
        done = []
        b.cluster.syncDone.connect(lambda ok, t: done.append(ok))
        b.cluster.connect_slave("127.0.0.1", 18990, "redundancy", "diff", True, os.path.join(TMP, "slave"), 500)
        t0 = time.monotonic()
        while not done and time.monotonic() - t0 < 15:
            run(0.05)
        run(0.3)
        assert done == [True], done
        # ridondanza collegata: niente comandi ai dispositivi esterni (li manda il master), avviso arancione
        from cuestage import outputs
        from cuestage.remote import midi_out, osc_send
        assert outputs.held() and b.held_label.isVisibleTo(b) and not a.held_label.isVisibleTo(a)
        assert osc_send("127.0.0.1", 9, "/prova", "1") is False and midi_out.send_script("x", "cc 1 1 1") == 0
        assert b.mixers.set_control("nessuno", "k", 1).result() is None
        a.go()
        run(0.4)
        assert names(a) == names(b) == ["Musica"], (names(a), names(b))
        # la ridondanza fa esattamente quello che fa il master: playhead, cue perse o in più si riallineano
        a.ctrl.set_playhead(0)
        t0 = time.monotonic()
        while b.ctrl.playhead != 0 and time.monotonic() - t0 < 2:
            run(0.05)
        assert b.ctrl.playhead == 0
        b.ctrl.playheads[b.show_data.main.id] = 1           # cursore spostato solo qui: torna su quello del master
        b.cluster._applying = True
        b.ctrl.stop_cue(b.show_data.cues[0].id)             # cue persa sulla ridondanza
        b.ctrl.start_cue(b.show_data.cues[1])               # cue in più sulla ridondanza
        b.cluster._applying = False
        a.ctrl.set_playhead(1)
        a.ctrl.set_playhead(0)
        t0 = time.monotonic()
        while names(b) != ["Musica"] and time.monotonic() - t0 < 4:
            run(0.05)
        assert names(b) == ["Musica"] and b.ctrl.playhead == 0, (names(b), b.ctrl.playhead)
        # interfaccia web della ridondanza come riserva per la pagina web del master
        b.web.start(18803)
        a.web.start(18804)
        t0 = time.monotonic()
        while not a.cluster.failover_web() and time.monotonic() - t0 < 2:
            run(0.05)
        assert a.cluster.failover_web() == [{"name": b.cluster.name, "url": "http://127.0.0.1:18803/"}]
        assert a._web_state()["failover"]
        import urllib.request
        rq = urllib.request.Request("http://127.0.0.1:18803/api/state", method="OPTIONS")
        with urllib.request.urlopen(rq, timeout=2) as r:
            assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == "*"
            assert "X-CueStage-Pin" in r.headers["Access-Control-Allow-Headers"]
        a.web.stop()
        b.web.stop()
        a.show_data.cues[1].name = "Modificata"
        run(0.8)
        assert b.show_data.cues[1].name == "Modificata"
        # impostazioni: arrivano dal master, ma quelle cambiate sulla secondaria restano sue
        bs = b.show_data.settings
        old_b = dict(bs)
        bs["osc_in_port"] = 19001
        b._apply_settings(old=old_b)
        old_a = dict(sh.settings)
        sh.settings["osc_in_port"] = 19002
        sh.settings["xfade_time"] = 5.0
        a._apply_settings(old=old_a)
        sh.dirty = True
        t0 = time.monotonic()
        while b.show_data.settings.get("xfade_time") != 5.0 and time.monotonic() - t0 < 3:
            run(0.05)
        assert b.show_data.settings["xfade_time"] == 5.0                  # non toccata qui: dal master
        assert b.show_data.settings["osc_in_port"] == 19001               # cambiata qui: resta locale
        assert ("osc_in_port", "OSC") in b.cluster_local_settings()
        b.cluster_reset_local()                                             # «Riprendi dal master»
        assert b.show_data.settings["osc_in_port"] == 19002 and not b.cluster_local_settings()
        old_a = dict(sh.settings)
        sh.settings["osc_in_port"] = 19003
        a._apply_settings(old=old_a)
        t0 = time.monotonic()
        while b.show_data.settings.get("osc_in_port") != 19003 and time.monotonic() - t0 < 3:
            run(0.05)
        assert b.show_data.settings["osc_in_port"] == 19003                # di nuovo aggiornata dal master
        a.cluster.stop_master()
        t0 = time.monotonic()
        while not b.cluster.lost and time.monotonic() - t0 < 3:
            run(0.02)
        assert b.cluster.lost
        run(0.1)
        assert not outputs.held() and not b.held_label.isVisibleTo(b)       # master perso: i comandi ripartono
        # allarme rosso lampeggiante finché non si preme OK
        run(0.1)
        al = b.lost_alarm
        assert al.isVisibleTo(b) and al.timer.isActive()
        c1 = al.styleSheet()
        run(0.5)
        assert al.styleSheet() != c1                        # lampeggia
        al.ok_btn.click()
        assert not al.isVisibleTo(b) and not al.timer.isActive()
    finally:
        for x in (a, b):
            x.ctrl.stop_all(0)
            x.show_data.dirty = False
            x.close()


def t_cluster_live_edit():
    """Master + ridondanza mentre una cue suona: fader della matrice, curva di volume e loop cambiati
    nell'ispettore del master arrivano alla ridondanza e valgono subito anche lì."""
    from PySide6.QtWidgets import QSlider
    from cuestage.audio_engine import SR
    a = MainWindow()
    b = MainWindow()
    try:
        sh = Show()
        sh.settings["output_fullscreen"] = False
        sh.cues = [Cue("audio", number="1", name="Lunga", file=tone("live_cl.wav", 30), loop=True)]
        sh.save(os.path.join(TMP, "live_cl.cueshow"))
        sh = Show.load(os.path.join(TMP, "live_cl.cueshow"))
        sh.settings["output_fullscreen"] = False
        a._set_show(sh)
        old = dict(sh.settings)
        sh.settings.update(cluster_enabled=True, cluster_port=18993)
        a._apply_settings(old=old)
        run(0.3)
        done = []
        b.cluster.syncDone.connect(lambda ok, t: done.append(ok))
        b.cluster.connect_slave("127.0.0.1", 18993, "redundancy", "diff", True, os.path.join(TMP, "live_cl_b"), 500)
        assert wait_until(lambda: done, 10)
        assert done == [True], done
        cue = sh.cues[0]
        a.ctrl.start_cue(cue)
        assert wait_until(lambda: cue.id in b.ctrl.instances, 5)
        run(0.3)
        vb = b.ctrl.instances[cue.id].voice
        m0 = vb.mtx.copy()
        # fader della matrice sul master (scheda Audio dell'ispettore)
        ins = a.inspector
        a.show()
        ins.set_cue(cue)
        ins.tabs.setCurrentIndex(ins._tab_keys.index("Audio"))
        run(0.3)
        fader = [f_ for f_ in ins.findChildren(QSlider, "matrix_fader") if f_.isVisible()][-1]
        fader.setValue(-200)
        bc = lambda: next(c for c in b.show_data.all_cues() if c.id == cue.id)
        assert cue.matrix and wait_until(lambda: bc().matrix == cue.matrix, 5), (cue.matrix, bc().matrix)
        assert wait_until(lambda: abs(vb.mtx.max() / m0.max() - 0.1) < 1e-3, 5), (m0, vb.mtx)
        # curva di volume e fine del loop
        cue.curve = [[0.0, -6.0], [20.0, -12.0]]
        cue.loop_end = 10.0
        ins._emit()
        assert wait_until(lambda: vb.curve is not None and vb.le == 10 * SR, 5), (vb.curve, vb.le)
        assert float(vb.curve[1][0]) == -6.0 and bc().curve == cue.curve
        a.ctrl.stop_all(0)
        run(0.3)
    finally:
        for x in (a, b):
            x.ctrl.stop_all(0)
            x.show_data.dirty = False
            x.close()


def t_cluster_join_align():
    """Una ridondanza che si collega mentre il master suona: le cue partono al punto del master, la pausa e
    gli scostamenti si allineano; spostamento di una voce audio anche con il loop."""
    import numpy as _np
    from cuestage.audio_engine import SR, Voice
    v = Voice(_np.zeros((SR * 10, 1), _np.float32), 1.0, 9.0, True, 2.0, 4.0, 0, 1.0, None, 0.5, 0.0)
    v.seek(2.5)                                          # 1 + 2.5 = 3.5 s nel file, prima del giro
    assert v.pos == int(3.5 * SR) and v.played == int(2.5 * SR) and v.ramp is None
    v.seek(4.0)                                          # 3 s fino a fine loop, poi 1 s dentro il loop (2–4)
    assert v.pos == int(3.0 * SR) and v.wraps >= 1, v.pos / SR
    a = MainWindow()
    b = MainWindow()
    try:
        sh = Show()
        sh.settings["output_fullscreen"] = False
        sh.cues = [Cue("audio", number="1", name="Lunga", file=tone("lunga.wav", 30)),
                   Cue("wait", number="2", name="Attesa", duration=60)]
        sh.save(os.path.join(TMP, "align.cueshow"))
        sh = Show.load(os.path.join(TMP, "align.cueshow"))
        sh.settings["output_fullscreen"] = False
        a._set_show(sh)
        old = dict(sh.settings)
        sh.settings.update(cluster_enabled=True, cluster_port=18997)
        a._apply_settings(old=old)
        run(0.3)
        a.ctrl.start_cue(sh.cues[0])
        a.ctrl.start_cue(sh.cues[1])
        run(2.5)                                         # il master suona da 2,5 s
        done = []
        b.cluster.syncDone.connect(lambda ok, t: done.append(ok))
        b.cluster.connect_slave("127.0.0.1", 18997, "redundancy", "diff", True, os.path.join(TMP, "align_b"), 500)
        t0 = time.monotonic()
        while not (done and len(b.ctrl.instances) == 2) and time.monotonic() - t0 < 10:
            run(0.05)
        assert done == [True] and names(b) == ["Attesa", "Lunga"], (done, names(b))
        run(0.3)
        for c in sh.cues:
            ea, eb = a.ctrl.instances[c.id].elapsed, b.ctrl.instances[c.id].elapsed
            # ea > 1: il master suona davvero (sulle macchine di prova senza scheda audio il tempo dell'audio
            # può scorrere più lento dell'orologio); quel che conta è che la ridondanza sia allo stesso punto
            assert ea > 1.0 and abs(ea - eb) < 0.5, (c.name, ea, eb)
        # la ridondanza si scosta (es. un intoppo): torna al punto del master
        b.ctrl.instances[sh.cues[0].id].seek_to(0.0)
        t0 = time.monotonic()
        while abs(a.ctrl.instances[sh.cues[0].id].elapsed - b.ctrl.instances[sh.cues[0].id].elapsed) > 0.5 \
                and time.monotonic() - t0 < 3:
            run(0.05)
        assert abs(a.ctrl.instances[sh.cues[0].id].elapsed - b.ctrl.instances[sh.cues[0].id].elapsed) < 0.5
        # pausa di una cue sul master: anche sulla ridondanza
        a.ctrl.pause_cue(sh.cues[1].id, True)
        t0 = time.monotonic()
        while not b.ctrl.instances[sh.cues[1].id].paused and time.monotonic() - t0 < 3:
            run(0.05)
        assert b.ctrl.instances[sh.cues[1].id].paused
    finally:
        for x in (a, b):
            x.ctrl.stop_all(0)
            x.show_data.dirty = False
            x.close()


def t_cluster_slow_transfer():
    """File grande su rete lenta: la secondaria non viene scollegata a metà, «Esci dalla modalità slave» è
    attiva già durante il download, e un trasferimento interrotto chiude la sincronizzazione con un errore."""
    import wave
    from cuestage import cluster as cl
    big = os.path.join(TMP, "grande.wav")
    with wave.open(big, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(bytes(4 * 44100 * 18))           # ~3 MB di silenzio
    a = MainWindow()
    b = MainWindow()
    old_send, old_timeout = cl.send_frame, cl.CLIENT_TIMEOUT

    def slow_send(sock, lock, obj, payload=None):
        if payload is not None and obj.get("t") == "chunk":
            time.sleep(len(payload) / 2e6)              # ~2 MB/s
        return old_send(sock, lock, obj, payload)
    cl.send_frame = slow_send
    cl.CLIENT_TIMEOUT = 0.5                             # prima: secondaria scollegata se il file durava di più
    try:
        sh = Show()
        sh.settings["output_fullscreen"] = False
        sh.cues = [Cue("audio", number="1", name="Grande", file=big)]
        sh.save(os.path.join(TMP, "master2.cueshow"))
        sh = Show.load(os.path.join(TMP, "master2.cueshow"))
        sh.settings["output_fullscreen"] = False
        a._set_show(sh)
        old = dict(sh.settings)
        sh.settings.update(cluster_enabled=True, cluster_port=18994)
        a._apply_settings(old=old)
        run(0.3)
        done, prog = [], []
        b.cluster.syncDone.connect(lambda ok, t: done.append((ok, t)))
        b.cluster.syncProgress.connect(lambda t, d, n: prog.append(d))
        b.cluster.connect_slave("127.0.0.1", 18994, "slave", "full", True, os.path.join(TMP, "slave2"), 300)
        t0 = time.monotonic()
        leave_during = False
        while not done and time.monotonic() - t0 < 20:
            run(0.05)
            if prog and prog[-1] > 0 and not done:
                leave_during = leave_during or b.act_leave.isEnabled()
        assert done and done[0][0], done
        assert leave_during, "«Esci dalla modalità slave» non attiva durante il download"
        assert not b.cluster.lost and len(a.cluster.client_list()) == 1
        local = os.path.join(TMP, "slave2", "grande.wav")
        assert os.path.getsize(local) == os.path.getsize(big)
        b.cluster_promote(ask=False)
        run(0.2)
        assert b.cluster.role is None and not b.act_leave.isEnabled()
        # trasferimento interrotto dal master: la finestra riceve l'esito (non resta in attesa)
        done.clear()
        t0 = time.monotonic()
        while True:                                     # Windows: ffmpeg può tenere ancora aperto il file
            try:
                os.remove(local)
                break
            except PermissionError:
                if time.monotonic() - t0 > 10:
                    raise
                run(0.1)
        b.cluster.connect_slave("127.0.0.1", 18994, "slave", "full", True, os.path.join(TMP, "slave2"), 300)
        t0 = time.monotonic()
        while not (prog and b.cluster.role == "slave") and time.monotonic() - t0 < 5:
            run(0.05)
        prog.clear()
        while not prog or prog[-1] < 300000:
            run(0.02)
        a.cluster.stop_master()
        t0 = time.monotonic()
        while not done and time.monotonic() - t0 < 5:
            run(0.05)
        assert done and not done[0][0], done
    finally:
        cl.send_frame, cl.CLIENT_TIMEOUT = old_send, old_timeout
        for x in (a, b):
            x.ctrl.stop_all(0)
            x.show_data.dirty = False
            x.close()


def t_bundle(w):
    """Export bundle → ZIP con show e file (anche esterni e di altre versioni) → Import bundle altrove."""
    import zipfile
    src = os.path.join(TMP, "bundle_src")
    os.makedirs(os.path.join(src, "audio"), exist_ok=True)
    a = tone(os.path.join("bundle_src", "audio", "a.wav"), 0.5)
    ext = tone("esterno.wav", 0.5)
    sh = Show()
    sh.settings["output_fullscreen"] = False
    sh.cues = [Cue("audio", number="1", name="A", file=a)]
    p = sh.add_panel("Effetti")
    p.cues.append(Cue("audio", number="2", name="Esterno", file=ext))
    sh.add_version("Altra")
    sh.save(os.path.join(src, "spettacolo.cueshow"))
    w._set_show(Show.load(os.path.join(src, "spettacolo.cueshow")))
    w._bundle_quiet = True
    z = w.export_bundle(os.path.join(TMP, "spettacolo-bundle.zip"))
    assert z and os.path.isfile(z)
    names = set(zipfile.ZipFile(z).namelist())
    assert {"cuestage-bundle.json", "spettacolo.cueshow", "audio/a.wav"} <= names, names
    assert any(n.startswith("_esterni/") and n.endswith("esterno.wav") for n in names), names
    w.show_data.dirty = False
    dest = os.path.join(TMP, "bundle_dest")
    path = w.import_bundle(z, dest)
    assert path == os.path.join(dest, "spettacolo.cueshow") and w.show_data.path == path
    files = sorted(c.file for c in w.show_data.every_cue())
    assert all(f.startswith(dest) and os.path.isfile(f) for f in files), files
    assert len(files) == 4, files                       # le cue della versione «Altra» comprese
    assert w.show_data.lists[1].name == "Effetti"
    w.qs.remove("last_show")                            # le finestre aperte dopo non devono riaprirlo


def t_web_toggle(w):
    """La casella dell'interfaccia web la accende / spegne subito; spenta, il QR è grigio."""
    from cuestage.ui_dialogs import SettingsDialog
    w.show_data.settings.update(web_enabled=False, web_port=18779, web_pin="")
    w.web.stop()
    d = SettingsDialog(w.show_data.settings, w.learn_midi, w, web_fn=w._web_switch)

    def darkest():
        img = d.web_qr.pixmap().toImage()
        return min(img.pixelColor(x, y).lightness() for x in range(0, img.width(), 3) for y in range(0, img.height(), 3))
    assert darkest() > 150, darkest()                     # spenta: grigio chiaro
    d.web_on.setChecked(True)
    assert w.web.server is not None and w.show_data.settings["web_enabled"]
    assert darkest() < 40                                 # accesa: QR nero
    d.web_on.setChecked(False)
    assert w.web.server is None and not w.show_data.settings["web_enabled"]
    assert darkest() > 150
    d.reject()
    w.show_data.dirty = False


def t_midi_feedback(w):
    """Ritorno MIDI verso un controller (es. BCR2000): anelli / fader con i valori attuali, LED delle cue in
    esecuzione e di DIM, niente rimbalzi mentre si muove un controllo, niente invio dalla ridondanza."""
    import mido
    from cuestage import outputs
    from cuestage.midi_feedback import db_to_fader
    from cuestage.remote import midi_out
    got = []

    class FakeOut:
        def send(self, msg):
            got.append(tuple(msg.bytes()))

        def close(self):
            pass
    old = mido.get_output_names, mido.open_output
    mido.get_output_names = lambda: ["BCR2000 Port 1"]
    mido.open_output = lambda name: FakeOut()
    midi_out.ports.clear()
    try:
        sh = Show()
        sh.settings["output_fullscreen"] = False
        a = Cue("audio", number="1", name="Musica", file=tone("fb.wav", 5), level_db=-10.0, midi_vol="cc 1 7",
                midi="cc 1 20 @ BCR2000 Port 1")
        b = Cue("wait", number="2", name="Attesa", duration=10)
        sh.cues = [a, b]
        sh.settings["midi_controls"] = {"vol_selected": "cc 1 8", "op_selected": "", "xfade": ""}
        sh.settings["midi_map"]["dim"] = "note 1 40"
        sh.settings["midi_feedback"] = {"BCR2000 Port 1": "auto"}
        w._set_show(sh)
        w._select_rows([0])
        run(0.3)
        v10 = round(db_to_fader(-10.0) * 127)
        assert (0xB0, 7, v10) in got and (0xB0, 20, 0) in got and (0xB0, 8, v10) in got, got
        assert (0x90, 40, 0) in got, got
        del got[:]
        a.level_db = 0.0                                   # cambiato da CueStage: l'anello si aggiorna
        run(0.2)
        assert (0xB0, 7, round(db_to_fader(0.0) * 127)) in got, got
        del got[:]
        w.ctrl.start_cue(a)                                # LED della cue in esecuzione
        run(0.2)
        assert (0xB0, 20, 127) in got, got
        del got[:]
        w.set_dim(True)
        run(0.2)
        assert (0x90, 40, 127) in got, got
        w.set_dim(False)
        # controllo mosso sul controller: nessun rimbalzo mentre lo si muove, né dopo se il valore è quello
        del got[:]
        w._midi_control("cc 1 7", 0.5, "BCR2000 Port 1")
        run(0.3)
        assert not [m for m in got if m[:2] == (0xB0, 7)], got
        run(0.5)
        assert not [m for m in got if m[:2] == (0xB0, 7)], got
        # ridondanza collegata al master: niente ritorno; poi tutti i valori ripartono
        del got[:]
        outputs.hold(True)
        a.level_db = -20.0
        run(0.2)
        assert not got
        outputs.hold(False)
        run(0.2)
        assert any(m[:2] == (0xB0, 7) for m in got), got
        assert any(m[:2] == (0xB0, 20) for m in got), got           # dopo la ridondanza: tutto da capo
        w.ctrl.stop_all(0)
        # impostazioni: la porta di ritorno si sceglie nei filtri della scheda
        from cuestage.remote import MidiInput
        from cuestage.ui_dialogs import SettingsDialog
        old_in = MidiInput.list_ports
        MidiInput.list_ports = staticmethod(lambda: ["BCR2000 Port 1"])
        try:
            sh.settings["midi_in_ports"] = ["BCR2000 Port 1"]
            d = SettingsDialog(sh.settings, w.learn_midi, w)
            d.midi_ports.setCurrentRow(0)
            assert d.f_fb.currentData() == "auto", d.f_fb.currentData()
            d.f_fb.setCurrentIndex(0)
            assert d.result_settings()["midi_feedback"] == {}
            d.f_fb.setCurrentIndex(d.f_fb.findData("BCR2000 Port 1"))
            assert d.result_settings()["midi_feedback"] == {"BCR2000 Port 1": "BCR2000 Port 1"}
            d.reject()
        finally:
            MidiInput.list_ports = old_in
    finally:
        outputs.hold(False)
        mido.get_output_names, mido.open_output = old
        midi_out.ports.clear()
        w.show_data.settings["midi_feedback"] = {}
        w.show_data.settings["midi_in_ports"] = []
        w.midi_fb.configure(w.show_data.settings)
        w.show_data.dirty = False


def t_event_triggers(w):
    """Trigger da eventi: ducking sopra / sotto soglia."""
    sh = Show()
    sh.settings["output_fullscreen"] = False
    on = Cue("wait", number="1", name="Voce", duration=30, trigger="trigger", event_trig=["duck_on"])
    off = Cue("wait", number="2", name="Silenzio", duration=30, trigger="trigger", event_trig=["duck_off"])
    sh.cues = [on, off]
    w._set_show(sh)

    fd = w.audio.duck                                   # ingresso del ducking simulato
    hooked = set()
    fd.hook = lambda key, on: (hooked.add if on else hooked.discard)(key)
    fd.stream, fd.gate, fd.hold_left = "simulato", False, 0.0
    w._ev_scan_at = 0.0
    w._ev_duck = False
    try:
        run(0.3)
        assert "__event_trig__" in hooked
        assert not w.ctrl.instances
        fd.gate = True
        run(0.2)
        assert names(w) == ["Voce"], names(w)
        fd.gate = False
        fd.hold_left = 0.5
        run(0.2)
        assert names(w) == ["Voce"]
        fd.hold_left = 0.0
        run(0.2)
        assert names(w) == ["Silenzio", "Voce"], names(w)
        w.ctrl.stop_all(0)
    finally:
        del fd.hook
        fd.stream, fd.gate, fd.hold_left = None, False, 0.0
        w._ev_scan_at = 0.0
        w.show_data.dirty = False


def t_cluster_perms_chat():
    """Slave con accesso a un solo PANEL (solo i suoi file), messaggi tra istanze e web, disconnessione,
    trigger «master perso» e «promossa a master»."""
    a = MainWindow()
    b = MainWindow()
    c = MainWindow()
    try:
        sh = Show()
        sh.settings["output_fullscreen"] = False
        sh.cues = [Cue("audio", number="1", name="Main", file=tone("pm_main.wav", 0.5))]
        p1 = sh.add_panel("Luci")
        p1.cues.append(Cue("audio", number="2", name="Panel", file=tone("pm_panel.wav", 0.5)))
        p2 = sh.add_panel("Effetti", kind="grid")
        p2.cues.append(Cue("audio", number="3", name="Grid", file=tone("pm_grid.wav", 0.5)))
        sh.save(os.path.join(TMP, "perm.cueshow"))
        sh = Show.load(os.path.join(TMP, "perm.cueshow"))
        sh.settings["output_fullscreen"] = False
        a._set_show(sh)
        old = dict(sh.settings)
        sh.settings.update(cluster_enabled=True, cluster_port=18996)
        a._apply_settings(old=old)
        run(0.3)
        p1_id, p2_id = sh.lists[1].id, sh.lists[2].id
        JOIN["perms"] = {p1_id: "edit", p2_id: "control"}
        done = []
        b.cluster.syncDone.connect(lambda ok, t: done.append(ok))
        b.cluster.connect_slave("127.0.0.1", 18996, "slave", "full", True, os.path.join(TMP, "perm_b"), 400)
        t0 = time.monotonic()
        while not done and time.monotonic() - t0 < 15:
            run(0.05)
        assert done == [True], done
        run(0.3)
        bs = b.show_data
        assert [l.name for l in bs.lists] == ["MAIN", "Luci", "Effetti"] and not bs.lists[0].cues
        assert sorted(os.listdir(os.path.join(TMP, "perm_b"))) == ["perm.cueshow", "pm_grid.wav", "pm_panel.wav"]
        assert b.cluster.can_edit_list(p1_id) and not b.cluster.can_edit_list(p2_id)
        # lo slave vede cosa fa il master: playhead e cue in esecuzione
        a.ctrl.start_cue(sh.lists[1].cues[0])
        t0 = time.monotonic()
        while sh.lists[1].cues[0].id not in (getattr(b.ctrl, "remote_running", None) or {}) and time.monotonic() - t0 < 3:
            run(0.05)
        assert sh.lists[1].cues[0].id in b.ctrl.remote_running and not b.ctrl.instances
        a.ctrl.stop_all(0)
        a.ctrl.set_playhead(1, sh.lists[1])
        t0 = time.monotonic()
        while b.ctrl.playheads.get(p1_id) != 1 and time.monotonic() - t0 < 3:
            run(0.05)
        assert b.ctrl.playheads.get(p1_id) == 1
        b.show_list(2)
        assert not b.inspector.isEnabled()
        b.show_list(1)
        assert b.inspector.isEnabled()
        # modifica sul PANEL «Luci» (consentita) e sul GRID (non consentita): sul master entra solo la prima
        bs.lists[1].cues[0].name = "Panel modificato"
        bs.lists[2].cues[0].name = "Grid modificato"
        bs.dirty = True
        t0 = time.monotonic()
        while sh.lists[1].cues[0].name != "Panel modificato" and time.monotonic() - t0 < 5:
            run(0.1)
        assert sh.lists[1].cues[0].name == "Panel modificato"
        assert sh.lists[2].cues[0].name == "Grid" and sh.lists[0].cues[0].name == "Main"
        t0 = time.monotonic()                               # sulla secondaria la modifica non consentita torna indietro
        while b.show_data.lists[2].cues[0].name != "Grid" and time.monotonic() - t0 < 5:
            run(0.1)
        assert b.show_data.lists[2].cues[0].name == "Grid"
        # aggiornamenti dal master mentre sulla secondaria si lavora nell'ispettore: niente ricostruzione,
        # il campo con il cursore resta com'è (anche se cambia proprio quella cue)
        from PySide6.QtWidgets import QApplication, QLineEdit
        b.show_list(1)
        run(0.2)
        cue_b = b.show_data.lists[1].cues[0]
        b.inspector.set_cue(cue_b)
        b.inspector.tabs.setCurrentIndex(0)                  # scheda Base (nome della cue)
        run(0.2)
        page = b.inspector.tabs.widget(0)
        le = next(x for x in page.findChildren(QLineEdit) if x.text() == cue_b.name)
        b.show()
        b.activateWindow()
        le.setFocus()
        run(0.2)
        has_focus = QApplication.focusWidget() is le
        sh.lists[2].cues[0].name = "Grid bis"                 # un'altra cue: l'ispettore non si tocca
        sh.dirty = True
        t0 = time.monotonic()
        while b.show_data.lists[2].cues[0].name != "Grid bis" and time.monotonic() - t0 < 5:
            run(0.1)
        run(0.2)
        assert b.show_data.lists[2].cues[0].name == "Grid bis"
        assert b.inspector.tabs.widget(0) is page and b.inspector.cue is cue_b      # ispettore non ricostruito
        assert not has_focus or QApplication.focusWidget() is le
        sh.lists[1].cues[0].notes = "nota dal master"        # proprio la cue nell'ispettore
        sh.dirty = True
        t0 = time.monotonic()
        while cue_b.notes != "nota dal master" and time.monotonic() - t0 < 5:
            run(0.1)
        run(0.2)
        assert b.show_data.lists[1].cues[0] is cue_b and cue_b.notes == "nota dal master"
        if has_focus:                                        # si sta scrivendo: il campo resta, gli altri si aggiornano
            assert b.inspector.tabs.widget(0) is page and QApplication.focusWidget() is le
        # seconda secondaria con tutto il progetto, poi messaggi
        JOIN["perms"] = {"*": "control"}
        done.clear()
        c.cluster.syncDone.connect(lambda ok, t: done.append(ok))
        c.cluster.connect_slave("127.0.0.1", 18996, "slave", "diff", True, os.path.join(TMP, "perm_c"), 400)
        t0 = time.monotonic()
        while not done and time.monotonic() - t0 < 15:
            run(0.05)
        assert done == [True]
        t0 = time.monotonic()
        while len(b.cluster.roster) < 3 and time.monotonic() - t0 < 3:
            run(0.05)
        assert len(b.cluster.roster) == 3, b.cluster.roster
        b.cluster.send_chat("ciao a tutti")
        c.cluster.send_chat("solo a B", to=b.cluster.instance_id)
        t0 = time.monotonic()
        while len(b.cluster.chat) < 2 and time.monotonic() - t0 < 3:
            run(0.05)
        run(0.2)
        assert [m["text"] for m in a.cluster.chat] == ["ciao a tutti"], a.cluster.chat
        assert sorted(m["text"] for m in b.cluster.chat) == ["ciao a tutti", "solo a B"], b.cluster.chat
        assert sorted(m["text"] for m in c.cluster.chat) == ["ciao a tutti", "solo a B"], c.cluster.chat
        # messaggio dall'interfaccia web del master
        a._web_command({"cmd": "chat", "text": "dal telefono", "to": "*", "name": "Marco"})
        run(0.4)
        assert b.cluster.chat[-1]["text"] == "dal telefono" and "Marco" in b.cluster.chat[-1]["from"]
        st = a._web_state()
        assert st["chat"][-1]["text"] == "dal telefono" and len(st["chat_to"]) == 4, st["chat_to"]
        assert a._cluster_win is not None and a._cluster_win.isVisible()      # finestra aperta dal messaggio
        # il master disconnette C: non è un «master perso» (niente trigger)
        cue_c = c.show_data.lists[0].cues[0]
        cue_c.event_trig = ["master_lost", "promoted"]
        cid = next(r[0] for r in a.cluster.client_rows() if r[1] == c.cluster.name and r[4] == {"*": "control"})
        a.cluster.kick(cid)
        t0 = time.monotonic()
        while c.cluster.role is not None and time.monotonic() - t0 < 3:
            run(0.05)
        run(0.3)
        assert c.cluster.role is None and not c.ctrl.instances and not c.act_leave.isEnabled()
        # B perde il master: parte la cue con «master perso»
        cue_b = b.show_data.lists[1].cues[0]
        cue_b.event_trig = ["master_lost"]
        b.show_data.lists[2].cues[0].event_trig = ["promoted"]
        a.cluster.stop_master()
        t0 = time.monotonic()
        while not b.cluster.lost and time.monotonic() - t0 < 3:
            run(0.02)
        run(0.2)
        assert names(b) == ["Panel modificato"], names(b)
        b.cluster_promote(ask=False)
        run(0.2)
        assert names(b) == ["Grid bis", "Panel modificato"], names(b)
    finally:
        JOIN["perms"] = None
        for x in (a, b, c):
            x.ctrl.stop_all(0)
            x.show_data.dirty = False
            x.close()


def t_cluster_osc_clients():
    """Programmi collegati via OSC (es. Script Follower): si presentano al master, la secondaria li conosce e,
    quando viene promossa, comunica loro il proprio indirizzo (/cuestage/master)."""
    import threading
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer
    from pythonosc.udp_client import SimpleUDPClient
    got = []
    disp = Dispatcher()
    disp.set_default_handler(lambda addr, *args: got.append((addr, list(args))))
    srv = ThreadingOSCUDPServer(("127.0.0.1", 0), disp)          # il programma collegato
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    back = srv.server_address[1]
    a = MainWindow()
    b = MainWindow()
    try:
        sh = Show()
        sh.settings["output_fullscreen"] = False
        sh.cues = [Cue("wait", number="1", name="Attesa", duration=10)]
        a._set_show(sh)
        old = dict(sh.settings)
        sh.settings.update(cluster_enabled=True, cluster_port=18992, osc_in_enabled=True, osc_in_port=18930)
        a._apply_settings(old=old)
        run(0.3)
        done = []
        b.cluster.syncDone.connect(lambda ok, t: done.append(ok))
        b.cluster.connect_slave("127.0.0.1", 18992, "redundancy", "diff", True, os.path.join(TMP, "slave-osc"), 500)
        t0 = time.monotonic()
        while not done and time.monotonic() - t0 < 15:
            run(0.05)
        assert done == [True], done
        # la secondaria ascolta l'OSC su un'altra porta (stessa macchina nella prova)
        b.show_data.settings["osc_in_port"] = 18931
        b.osc.start(18931)
        cl = SimpleUDPClient("127.0.0.1", 18930)
        cl.send_message("/cuestage/client/hello", ["scriptfollower", "0.3.7", "regia-luci", back, "sf1", "p1"])
        t0 = time.monotonic()
        while not got and time.monotonic() - t0 < 2:
            run(0.05)
        assert got and got[0][0] == "/cuestage/client/ack", got
        name, port, members = got[0][1]
        assert name == a.cluster.name and port == 18930 and "127.0.0.1" in members.split(","), got[0]
        assert [c["app"] for c in a.cluster.osc_client_rows()] == ["scriptfollower"]
        t0 = time.monotonic()                                # passa alla secondaria con lo stato
        while not b.cluster.master_osc_clients and time.monotonic() - t0 < 3:
            run(0.05)
        assert b.cluster.master_osc_clients[0]["id"] == "sf1"
        # il master cade, la secondaria viene promossa e lo dice al programma collegato
        a.cluster.stop_master()
        t0 = time.monotonic()
        while not b.cluster.lost and time.monotonic() - t0 < 3:
            run(0.02)
        got.clear()
        b.cluster_promote(ask=False)
        t0 = time.monotonic()
        while not got and time.monotonic() - t0 < 2:
            run(0.05)
        assert got and got[0][0] == "/cuestage/master", got
        assert got[0][1] == ["127.0.0.1", 18931, b.cluster.name, "127.0.0.1"], got[0]
        assert "sf1" in b.cluster.osc_clients
        cl2 = SimpleUDPClient("127.0.0.1", 18931)
        cl2.send_message("/cuestage/client/moved", ["sf1", "p1"])
        cl2.send_message("/cuestage/client/bye", ["sf1"])
        t0 = time.monotonic()
        while "sf1" in b.cluster.osc_clients and time.monotonic() - t0 < 2:
            run(0.05)
        assert "sf1" not in b.cluster.osc_clients
    finally:
        srv.shutdown()
        srv.server_close()
        for x in (a, b):
            x.ctrl.stop_all(0)
            x.show_data.dirty = False
            x.close()


if __name__ == "__main__":
    w = MainWindow()
    w.show()
    run(0.3)
    check("salva e ricarica uno show con PANEL", t_save_load)
    check("show portabile tra Linux, Windows e macOS", t_portable_paths)
    check("GO, gruppo «tutte insieme», stop", lambda: t_go_and_groups(w))
    check("cue di testo in sequenza", lambda: t_text_sequence(w))
    check("gruppi Playlist e Random playlist", lambda: t_playlist(w))
    check("PANEL GRID, scorciatoie e cue Stop panel", lambda: t_grid_and_stop_panel(w))
    check("Tap Tempo dei gruppi", lambda: t_tap_tempo(w))
    check("ducking delle cue, dei gruppi e del master", lambda: t_ducking(w))
    check("preascolto e fader del mixer agganciati al ducking", lambda: t_preview_and_mixer_duck(w))
    check("pulsante PFL nella lista e nel GRID", lambda: t_pfl_buttons(w))
    check("video precaricati e loop senza attesa", lambda: t_video_preload(w))
    check("GRID: Solo, BLIND, gruppo aperto, rilascio su un gruppo", lambda: t_grid_solo_blind_groups(w))
    check("cue mixer su X32 simulato (mute a scena e fader)", lambda: t_mixer_x32(w))
    check("profilo OSC ChamSys MagicQ", t_osc_profile)
    check("lettura dei controlli ALSA", t_alsa_parser)
    check("regie video Roland V-8HD / V-1HD: impostazioni, cue Regia video, MIDI", lambda: t_roland_video(w))
    check("OBS Studio come regia video (WebSocket simulato)", lambda: t_obs_studio(w))
    check("Blackmagic ATEM Mini / Pro / Extreme come regia video (UDP simulato)", lambda: t_atem(w))
    check("MIDI out: MMC, clock e profilo Akai MPC", lambda: t_midi_profiles(w))
    check("curva di volume e volume cambiati mentre la cue suona", lambda: t_live_curve(w))
    check("loop: marcatori, caselle rispetto a IN/OUT, modifiche in riproduzione, crossfade", lambda: t_loop_edit(w))
    check("campo Volume: segue la linea rosa al cursore (curva e fade)", lambda: t_volume_field(w))
    check("lista: colonne ridimensionabili e larghezze ricordate", lambda: t_column_widths(w))
    check("ducking: ingresso che non risponde non blocca l'interfaccia", t_ducking_open_nonblocking)
    check("grafico: barra di scorrimento; video: fade e crossfade dell'immagine separati", lambda: t_video_xfade_fades(w))
    check("«Volumi uscite a 0 dB»: volume della cue e uscite a 0 dB", lambda: t_levels_zero(w))
    check("cue «Chiudi schermo intero»", lambda: t_fsclose(w))
    check("rotazione di video e immagini", lambda: t_rotation(w))
    check("WAV letti anche senza ffmpeg", t_wav_without_ffmpeg)
    check("lingue: italiano, inglese, sloveno", t_languages)
    check("ispettore: tab, preset audio+video, Solo trigger, dimensione, deformazione, crop con Ctrl", lambda: t_inspector_layout(w))
    check("avviso iniziale e indirizzi dell'interfaccia web", t_notice_and_addresses)
    check("annulla / ripeti, cronologia e versioni dello show", lambda: t_history_versions(w))
    check("export / import bundle (ZIP con show e file)", lambda: t_bundle(w))
    check("trigger da eventi: ducking sopra / sotto soglia", lambda: t_event_triggers(w))
    check("ritorno MIDI verso i controller (anelli, fader, LED)", lambda: t_midi_feedback(w))
    check("interfaccia web accesa / spenta dalla casella, QR grigio da spenta", lambda: t_web_toggle(w))
    w.show_data.dirty = False
    w.close()
    check("master + ridondanza: comandi, modifiche, heartbeat", t_cluster_redundancy)
    check("istanze: file grande su rete lenta, Esci dalla modalità slave", t_cluster_slow_transfer)
    check("ridondanza collegata mentre il master suona: cue al punto del master, pause, scostamenti",
          t_cluster_join_align)
    check("ridondanza: fader della matrice, curva e loop modificati sul master valgono subito", t_cluster_live_edit)
    check("istanze: accesso per PANEL / GRID, messaggi (anche web), disconnessione, trigger slave", t_cluster_perms_chat)
    check("istanze: programmi collegati via OSC avvisati del nuovo master", t_cluster_osc_clients)
    print(f"\n{'Tutto bene' if not FAILED else f'{len(FAILED)} prove fallite: ' + ', '.join(FAILED)}")
    sys.exit(len(FAILED))
