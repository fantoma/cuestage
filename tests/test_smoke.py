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
    run(0.2)
    rb = next(b for b in w.inspector.findChildren(QPushButton) if tr("⟲ Leggi dal mixer") == b.text() and b.isVisible())
    rb.click()
    wait_until(lambda: abs(d.mix_level + 10.0) < 0.2)
    assert abs(d.mix_level + 10.0) < 0.2, d.mix_level
    spins = [sp for sp in w.inspector.findChildren(QDoubleSpinBox)
             if sp.isVisible() and abs(sp.value() + 10.0) < 0.2]
    assert spins, "il livello letto non compare nell'ispettore"


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
    check("WAV letti anche senza ffmpeg", t_wav_without_ffmpeg)
    check("lingue: italiano, inglese, sloveno", t_languages)
    w.show_data.dirty = False
    w.close()
    check("master + ridondanza: comandi, modifiche, heartbeat", t_cluster_redundancy)
    print(f"\n{'Tutto bene' if not FAILED else f'{len(FAILED)} prove fallite: ' + ', '.join(FAILED)}")
    sys.exit(len(FAILED))
