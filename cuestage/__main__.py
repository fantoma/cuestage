import os
import sys


def main():
    from . import i18n                  # prima di tutto: i testi dei moduli si traducono all'importazione
    i18n.set_language(os.environ.get("CUESTAGE_LANG") or i18n.saved_language())
    from .system import IS_LINUX, IS_WIN
    # backend FFmpeg di Qt Multimedia (su tutti i sistemi)
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    if IS_LINUX:
        # se il demone PipeWire non è attivo, il backend audio PipeWire di Qt può bloccarsi
        rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        if "QT_AUDIO_BACKEND" not in os.environ and not os.path.exists(os.path.join(rt, "pipewire-0")):
            os.environ["QT_AUDIO_BACKEND"] = "pulseaudio" if os.path.exists(os.path.join(rt, "pulse")) else "alsa"
        if "CUESTAGE_ALSA_MESSAGES" not in os.environ:     # messaggi ALSA sul terminale (es. underrun)
            from .audio_engine import silence_alsa_messages
            silence_alsa_messages()
    if IS_WIN:
        os.environ.setdefault("SD_ENABLE_ASIO", "1")    # PortAudio con ASIO (incluso in sounddevice per Windows)
        try:                                    # icona e raggruppamento corretti nella barra delle applicazioni
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CueStage.CueStage")
        except Exception:
            pass
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QColor, QIcon, QPalette
    from PySide6.QtWidgets import QApplication

    class _App(QApplication):
        """Su macOS il doppio clic su uno show nel Finder arriva come evento, non come argomento."""
        window = None
        pending = []

        def event(self, e):
            if e.type() == QEvent.FileOpen and e.file():
                if self.window is not None:
                    if self.window._confirm_discard():
                        self.window.open_show(e.file())
                else:
                    self.pending.append(e.file())
                return True
            return super().event(e)

    from .main_window import MainWindow

    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = _App(sys.argv)
    app.setApplicationName("CueStage")
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")))
    app.setDesktopFileName("cuestage")
    app.setStyle("Fusion")
    pal = QPalette()
    base = {
        QPalette.Window: (37, 38, 44), QPalette.WindowText: (225, 225, 230),
        QPalette.Base: (28, 29, 34), QPalette.AlternateBase: (34, 35, 41),
        QPalette.ToolTipBase: (50, 50, 58), QPalette.ToolTipText: (235, 235, 240),
        QPalette.Text: (225, 225, 230), QPalette.Button: (50, 52, 60),
        QPalette.ButtonText: (230, 230, 235), QPalette.Highlight: (52, 100, 170),
        QPalette.HighlightedText: (255, 255, 255), QPalette.PlaceholderText: (120, 120, 130),
    }
    for role, rgb in base.items():
        pal.setColor(role, QColor(*rgb))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 110, 118))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 110, 118))
    app.setPalette(pal)

    path = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None
    w = MainWindow(path)
    app.window = w
    for p in app.pending:                    # file aperti dal Finder prima che la finestra esistesse
        w.open_show(p)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
