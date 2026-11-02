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
    # cue solo audio: stessa interfaccia (niente timeline dei keyframe), volume sulla linea arancione, marker,
    # keyframe e cursore sulla forma d'onda
    ins.set_cue(a)
    ins.tabs.setCurrentIndex(2)
    run(0.3)
    page = ins.tabs.widget(2)
    assert not [k for k in page.findChildren(KeyframeBar) if k.isVisible()]
    tl = ins.timeline
    tl.zoom_all()
    a.level_db, a.fade_in, a.fade_out, a.start, a.end = 0.0, 0.0, 0.0, 0.0, 0.0
    xs, xfi, xfo, xe, y = tl._env_points()
    mid = QPointF((xfi + xfo) / 2, y)
    assert tl._hit(mid) == ("level",), tl._hit(mid)
    tl.drag = ("level",)
    tl.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(mid.x(), tl.db2y(-12)), QPointF(0, 0), Qt.NoButton,
                                  Qt.LeftButton, Qt.NoModifier))
    tl.drag = None
    assert abs(a.level_db + 12) <= 0.5, a.level_db
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
        assert set(drv2) == {"roland_v8hd", "roland_v1hd"}, drv2
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
        a.go()
        run(0.4)
        assert names(a) == names(b) == ["Musica"], (names(a), names(b))
        a.show_data.cues[1].name = "Modificata"
        run(0.8)
        assert b.show_data.cues[1].name == "Modificata"
        a.cluster.stop_master()
        t0 = time.monotonic()
        while not b.cluster.lost and time.monotonic() - t0 < 3:
            run(0.02)
        assert b.cluster.lost
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
        os.remove(local)
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
    check("WAV letti anche senza ffmpeg", t_wav_without_ffmpeg)
    check("lingue: italiano, inglese, sloveno", t_languages)
    check("ispettore: tab, preset audio+video, Solo trigger, dimensione, deformazione, crop con Ctrl", lambda: t_inspector_layout(w))
    check("avviso iniziale e indirizzi dell'interfaccia web", t_notice_and_addresses)
    check("annulla / ripeti, cronologia e versioni dello show", lambda: t_history_versions(w))
    w.show_data.dirty = False
    w.close()
    check("master + ridondanza: comandi, modifiche, heartbeat", t_cluster_redundancy)
    check("istanze: file grande su rete lenta, Esci dalla modalità slave", t_cluster_slow_transfer)
    print(f"\n{'Tutto bene' if not FAILED else f'{len(FAILED)} prove fallite: ' + ', '.join(FAILED)}")
    sys.exit(len(FAILED))
