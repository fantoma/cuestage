"""Avvio di CueStage impacchettato (AppImage su Linux, PyInstaller su Windows e macOS).

    CueStage [show.cueshow]          avvia il programma
    CueStage --version               versione
    CueStage --self-test [file]      verifica che tutte le parti incluse funzionino
                                     (su Windows il risultato va anche in «file», perché non c'è console)
    CueStage --python ARGS           esegue il Python incluso (solo AppImage: diagnosi)
"""
import ctypes.util
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = getattr(sys, "frozen", False)                 # PyInstaller
if not FROZEN:      # AppImage: cuestage/ accanto a questo file; sorgenti: nella cartella superiore
    sys.path.insert(0, HERE if os.path.isdir(os.path.join(HERE, "cuestage")) else os.path.dirname(HERE))

# AppImage: sounddevice cerca PortAudio con ctypes.util.find_library, gli indichiamo quella inclusa
_TOP = os.environ.get("CUESTAGE_APPIMAGE_DIR") or os.path.dirname(os.path.dirname(HERE))
PORTAUDIO = os.path.join(_TOP, "usr", "lib", "libportaudio.so.2")
if not FROZEN and os.path.exists(PORTAUDIO):
    _find = ctypes.util.find_library

    def _find_library(name):
        return PORTAUDIO if name == "portaudio" else _find(name)

    ctypes.util.find_library = _find_library


def _log_without_console():
    """App a finestra su Windows/macOS: senza console stdout/stderr sono None e qualunque print
    farebbe cadere il programma. Li mandiamo in un file di log nella cartella dei dati."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from cuestage.system import data_dir
    try:
        os.makedirs(data_dir(), exist_ok=True)
        f = open(data_dir("cuestage.log"), "a", encoding="utf-8", buffering=1)
    except OSError:
        f = open(os.devnull, "w")
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


def self_test(report=None):

    import subprocess
    import time
    from cuestage.system import IS_LINUX, IS_WIN, OS_NAME, find_tool, subprocess_flags
    lines = []
    ok = True

    def out(s):
        lines.append(s)
        try:
            print(s, flush=True)
        except Exception:
            pass

    def step(name, fn, optional=False):
        nonlocal ok
        try:
            out(f"  ✔ {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            ok = ok and optional
            out(f"  {'!' if optional else '✘'} {name}: {type(e).__name__}: {e}"
                + ("  (non disponibile su questo computer: il resto funziona)" if optional else ""))

    import cuestage
    out(f"CueStage {cuestage.__version__} — {OS_NAME} — Python {sys.version.split()[0]}")
    step("PySide6", lambda: __import__("PySide6").__version__)
    step("Qt Multimedia", lambda: (__import__("PySide6.QtMultimedia", fromlist=["x"]), "ok")[1])
    step("numpy", lambda: __import__("numpy").__version__)

    def pa():
        import sounddevice as sd
        apis = ", ".join(a["name"] for a in sd.query_hostapis())
        return f"{sd.get_portaudio_version()[1]}, {len(sd.query_devices())} dispositivi ({apis})"
    step("PortAudio", pa)
    step("mido/rtmidi", lambda: ", ".join(__import__("mido").get_output_names()) or "nessuna porta MIDI (ok)",
         optional=IS_LINUX and not os.path.exists("/dev/snd/seq"))
    tools = ["ffmpeg", "ffprobe"] + (["amixer"] if IS_LINUX else [])
    for tool in tools:
        def run(t=tool):
            exe = find_tool(t)
            if not exe:
                raise FileNotFoundError(f"{t} non trovato")
            arg = "--version" if t == "amixer" else "-version"
            r = subprocess.run([exe, arg], capture_output=True, text=True, check=True, timeout=30,
                               **subprocess_flags())
            return r.stdout.splitlines()[0]
        step(tool, run, optional=(tool == "amixer" and not os.environ.get("CUESTAGE_APPIMAGE_DIR")))

    def decode():
        import tempfile
        import wave
        from cuestage import media
        path = os.path.join(tempfile.mkdtemp(), "prova.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"\x00\x10" * 2 * 44100)
        info = media.probe(path)
        a = media.decode_audio(path, channels=info["channels"])
        if abs(info["duration"] - 1.0) > 0.05 or a.shape != (48000, 2):
            raise RuntimeError(f"risultato inatteso: {info['duration']} s, {a.shape}")
        return "WAV 44,1 kHz → 48 kHz ok"
    step("decodifica audio (ffmpeg)", decode)

    def plugins():
        from cuestage.mixers import MixerRegistry
        r = MixerRegistry().load()
        if r.errors:
            raise RuntimeError("; ".join(r.errors))
        return f"{len(r.drivers)} driver mixer"
    step("plugin mixer", plugins)

    def window():
        from PySide6.QtWidgets import QApplication
        from cuestage.main_window import MainWindow
        app = QApplication.instance() or QApplication([])
        w = MainWindow()
        w.show()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.5:
            app.processEvents()
        w.show_data.dirty = False
        w.close()
        return "finestra principale creata"
    step("interfaccia", window)
    out("tutto bene" if ok else "ci sono problemi")
    if report or (IS_WIN and FROZEN):
        path = report or os.path.join(os.getcwd(), "cuestage-self-test.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if args[:1] == ["--version"]:
        import cuestage
        print("CueStage", cuestage.__version__)
        return 0
    if args[:1] == ["--self-test"]:
        if sys.stdout is None:
            sys.stdout = sys.stderr = open(os.devnull, "w")
        return self_test(args[1] if len(args) > 1 else None)
    if args[:1] == ["--python"] and not FROZEN:
        sys.argv = args[1:] or [""]
        if not args[1:]:
            import code
            code.interact()
            return 0
        if sys.argv[0] == "-m":
            sys.argv = sys.argv[1:]
            runpy.run_module(sys.argv[0], run_name="__main__", alter_sys=True)
        elif sys.argv[0] == "-c":
            exec(compile(sys.argv[1], "<string>", "exec"), {"__name__": "__main__"})
        else:
            runpy.run_path(sys.argv[0], run_name="__main__")
        return 0
    _log_without_console()
    # macOS: il Finder aggiunge «-psn_…» agli argomenti delle app avviate a mano
    args = [a for a in args if not a.startswith("-psn_")]
    sys.argv = ["cuestage"] + args
    from cuestage.__main__ import main as run
    return run()


if __name__ == "__main__":
    sys.exit(main())
