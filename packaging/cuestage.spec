# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: CueStage per Windows (cartella con CueStage.exe) e macOS (CueStage.app).

    pyinstaller packaging/cuestage.spec --noconfirm        (dalla cartella del progetto)

Variabili d'ambiente:
    CUESTAGE_FFMPEG_DIR   cartella con ffmpeg/ffprobe (o .exe) da includere (facoltativa ma consigliata)
    CUESTAGE_MAC_ARCH     arm64, x86_64 o universal2 (solo macOS; predefinita: quella del Python usato)
Funziona anche su Linux (per prova), ma lì il pacchetto ufficiale è l'AppImage.
"""
import ast
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
PKG = os.path.join(ROOT, "cuestage")
sys.path.insert(0, ROOT)
from cuestage import __version__ as VERSION  # noqa: E402

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# ---- file non Python del pacchetto + plugin caricati da file (mixer, proiettori, profili OSC)
datas = []
for path in glob.glob(os.path.join(PKG, "**", "*"), recursive=True):
    if os.path.isdir(path) or "__pycache__" in path or path.endswith(".pyc"):
        continue
    rel = os.path.relpath(path, ROOT)
    top = rel.split(os.sep)[1] if os.sep in rel else ""
    if not path.endswith(".py") or top in ("mixer_plugins", "plugins"):
        datas.append((path, os.path.dirname(rel)))

# ---- moduli importati dai plugin (PyInstaller non li vede perché si caricano da file)
hidden = set(collect_submodules("cuestage")) | {"mido.backends.rtmidi", "rtmidi"}
for path in glob.glob(os.path.join(PKG, "mixer_plugins", "*.py")) + glob.glob(os.path.join(PKG, "plugins", "*.py")):
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            hidden.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            hidden.add(node.module)

# ---- ffmpeg e ffprobe
binaries = []
ff = os.environ.get("CUESTAGE_FFMPEG_DIR")
if ff:
    for name in ("ffmpeg", "ffprobe"):
        for p in glob.glob(os.path.join(ff, name + (".exe" if IS_WIN else ""))):
            binaries.append((p, "ffmpeg"))
    if IS_WIN:
        binaries += [(p, "ffmpeg") for p in glob.glob(os.path.join(ff, "*.dll"))]

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(hidden),
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick3D",
              "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
              "PySide6.QtDesigner", "PySide6.QtQml", "PySide6.QtQuick", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CueStage",
    console=False,                 # app a finestra (il log va in cuestage.log nella cartella dei dati)
    icon=os.path.join(SPECPATH, "cuestage.ico" if IS_WIN else "cuestage.icns"),
    target_arch=os.environ.get("CUESTAGE_MAC_ARCH") or None,
    codesign_identity=os.environ.get("CUESTAGE_CODESIGN_ID") or None,
    entitlements_file=os.path.join(SPECPATH, "macos", "entitlements.plist") if IS_MAC else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CueStage", upx=False)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="CueStage.app",
        icon=os.path.join(SPECPATH, "cuestage.icns"),
        bundle_identifier="org.cuestage.CueStage",
        version=VERSION,
        info_plist={
            "CFBundleName": "CueStage",
            "CFBundleDisplayName": "CueStage",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            # macOS 15+: senza questa voce non si raggiungono mixer, proiettori, OSC e istanze in rete locale
            "NSLocalNetworkUsageDescription": "CueStage comanda mixer, proiettori, luci (OSC) e altre istanze "
                                              "di CueStage sulla rete locale.",
            "NSMicrophoneUsageDescription": "CueStage apre le schede audio anche in ingresso per alcuni driver.",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Show CueStage",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
                "CFBundleTypeExtensions": ["cueshow"],
                "CFBundleTypeIconFile": "cuestage.icns",
            }],
        },
    )
