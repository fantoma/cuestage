"""Differenze fra sistemi operativi (Linux, Windows, macOS) raccolte in un solo posto."""
from .i18n import tr
import os
import subprocess
import sys

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
OS_NAME = tr("Windows") if IS_WIN else "macOS" if IS_MAC else tr("Linux")


def data_dir(*sub):
    """Cartella dei dati utente (autosalvataggi, plugin e profili aggiunti dall'utente).

    Linux:   ~/.local/share/cuestage           (come sempre)
    Windows: %APPDATA%\\CueStage
    macOS:   ~/Library/Application Support/CueStage
    """
    if IS_WIN:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming"), "CueStage")
    elif IS_MAC:
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "CueStage")
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "share", "cuestage")
    return os.path.join(base, *sub)


def data_dir_label(*sub):
    """Percorso da mostrare all'utente (con ~ al posto della cartella personale)."""
    p = data_dir(*sub)
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p.startswith(home) else p


def bundle_dir():
    """Cartella del programma impacchettato (PyInstaller/AppImage), oppure None se avviato dai sorgenti."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.environ.get("CUESTAGE_APPIMAGE_DIR") or None


def subprocess_flags():
    """Argomenti per subprocess: su Windows niente finestra di console per ffmpeg/ffprobe."""
    if IS_WIN:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def portable_rel(rel):
    """Percorso relativo da scrivere nello show o mandare in rete: sempre con «/»."""
    return rel.replace("\\", "/") if rel else rel


def local_path(rel):
    """Percorso relativo ricevuto (con «/» o «\\») nel formato di questo sistema.
    Toglie unità, radici e «..» così non può uscire dalla cartella di destinazione."""
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if parts and len(parts[0]) == 2 and parts[0][1] == ":":       # «C:»
        parts = parts[1:]
    return os.path.join(*parts) if parts else "file"


def file_basename(name):
    """Nome del file da un percorso di qualunque sistema."""
    return str(name).replace("\\", "/").rstrip("/").split("/")[-1] or "file"


def find_tool(name):
    """Programma esterno (ffmpeg, ffprobe…): prima quello incluso nel pacchetto, poi quello di sistema."""
    import shutil
    exe = name + (".exe" if IS_WIN else "")
    b = bundle_dir()
    if b:
        for sub in ("", "bin", "ffmpeg", os.path.join("usr", "bin")):
            p = os.path.join(b, sub, exe)
            if os.path.isfile(p):
                return p
    return shutil.which(name)


def install_hint(package):
    """Come installare un programma mancante su questo sistema."""
    if IS_WIN:
        return tr("scarica {0} e aggiungilo al PATH (oppure usa il pacchetto di CueStage, che lo include)", package)
    if IS_MAC:
        return tr("installalo con 'brew install {0}' (oppure usa CueStage.app, che lo include)", package)
    return tr("installalo con 'sudo apt install {0}'", package)
