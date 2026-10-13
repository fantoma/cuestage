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
    check("cue mixer su X32 simulato (mute a scena e fader)", lambda: t_mixer_x32(w))
    check("profilo OSC ChamSys MagicQ", t_osc_profile)
    check("lettura dei controlli ALSA", t_alsa_parser)
    w.show_data.dirty = False
    w.close()
    check("master + ridondanza: comandi, modifiche, heartbeat", t_cluster_redundancy)
    print(f"\n{'Tutto bene' if not FAILED else f'{len(FAILED)} prove fallite: ' + ', '.join(FAILED)}")
    sys.exit(len(FAILED))
