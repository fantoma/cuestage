#!/usr/bin/env bash
# Costruisce CueStage-x86_64.AppImage: un unico file eseguibile che contiene Python, Qt (PySide6),
# numpy, sounddevice + PortAudio, mido/rtmidi, ffmpeg/ffprobe e amixer.
#
#   packaging/build_appimage.sh            (dalla cartella del progetto, su Ubuntu/Debian x86_64)
#
# Serve: bash, curl, tar, xz, dpkg-deb (libportaudio2, alsa-utils e libxcb-cursor0 vengono estratti
# dai pacchetti di Ubuntu 22.04, senza installarli) e ldd. Nessun permesso di root richiesto.
# Il risultato funziona su distribuzioni con glibc >= 2.34: Ubuntu/Xubuntu 22.04+, Debian 12+, Fedora 35+…
# I download restano in build/cache e non vengono riscaricati.
# Variabili utili: PY_URL, FFMPEG_URL, APPIMAGETOOL_URL (per cambiare versione), KEEP_APPDIR=1.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
CACHE="$BUILD/cache"
APPDIR="$BUILD/CueStage.AppDir"
VERSION="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$ROOT/cuestage/__init__.py")"
VERSION="${VERSION:-dev}"
OUT="$ROOT/dist/CueStage-${VERSION}-x86_64.AppImage"

PY_URL="${PY_URL:-https://github.com/astral-sh/python-build-standalone/releases/download/20251014/cpython-3.12.12+20251014-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz}"
FFMPEG_URL="${FFMPEG_URL:-https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-lgpl-shared.tar.xz}"
APPIMAGETOOL_URL="${APPIMAGETOOL_URL:-https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage}"

say() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
fetch() {  # fetch URL FILE
    [ -s "$CACHE/$2" ] || { say "scarico $2"; curl -fL --retry 3 -o "$CACHE/$2.part" "$1"; mv "$CACHE/$2.part" "$CACHE/$2"; }
}

mkdir -p "$CACHE" "$ROOT/dist"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/app"

# ---------------------------------------------------------------- Python portabile
fetch "$PY_URL" python.tar.gz
say "Python portabile"
tar -xzf "$CACHE/python.tar.gz" -C "$APPDIR/usr"          # -> usr/python
PY="$APPDIR/usr/python/bin/python3"
rm -rf "$APPDIR"/usr/python/lib/python3.*/{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3} \
       "$APPDIR"/usr/python/lib/{libtcl,libtk}* "$APPDIR"/usr/python/lib/{tcl,tk,itcl,thread}* 2>/dev/null || true

# ---------------------------------------------------------------- librerie Python
say "PySide6, numpy, sounddevice, mido…"
"$PY" -m pip install --no-warn-script-location --disable-pip-version-check -q -r "$ROOT/requirements.txt"
"$PY" "$ROOT/packaging/prune_qt.py" "$APPDIR/usr/python"
"$PY" -m pip uninstall -y -q pip setuptools 2>/dev/null || true

# ---------------------------------------------------------------- ffmpeg / ffprobe
fetch "$FFMPEG_URL" ffmpeg.tar.xz
say "ffmpeg"
mkdir -p "$BUILD/ff" && rm -rf "$BUILD/ff"/* && tar -xJf "$CACHE/ffmpeg.tar.xz" -C "$BUILD/ff" --strip-components=1
mkdir -p "$APPDIR/usr/ffmpeg/bin" "$APPDIR/usr/ffmpeg/lib"
cp "$BUILD/ff/bin/ffmpeg" "$BUILD/ff/bin/ffprobe" "$APPDIR/usr/ffmpeg/bin/"
cp -a "$BUILD/ff/lib/"*.so* "$APPDIR/usr/ffmpeg/lib/"
ln -sf ../ffmpeg/bin/ffmpeg "$APPDIR/usr/bin/ffmpeg"
ln -sf ../ffmpeg/bin/ffprobe "$APPDIR/usr/bin/ffprobe"

# ---------------------------------------------------------------- pacchetti Debian/Ubuntu (estratti, non installati)
say "PortAudio, amixer, xcb-cursor"
DEBS="$BUILD/debs"; mkdir -p "$DEBS" && rm -rf "$DEBS"/*
# pacchetti di Ubuntu 22.04: richiedono glibc 2.34 come Qt, quindi non alzano i requisiti minimi
UBU=http://archive.ubuntu.com/ubuntu/pool
for u in universe/p/portaudio19/libportaudio2_19.6.0-1.1_amd64.deb \
         main/j/jackd2/libjack-jackd2-0_1.9.20~dfsg-1_amd64.deb \
         main/d/db5.3/libdb5.3_5.3.28+dfsg1-0.8ubuntu3_amd64.deb \
         main/a/alsa-utils/alsa-utils_1.2.6-1ubuntu1_amd64.deb \
         universe/x/xcb-util-cursor/libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb; do
    fetch "$UBU/$u" "$(basename "$u")"
    dpkg-deb -x "$CACHE/$(basename "$u")" "$DEBS"
done
LIBS="$DEBS/usr/lib/x86_64-linux-gnu"
cp -a "$LIBS"/libportaudio.so.2* "$APPDIR/usr/lib/"
# usate solo se il sistema non le ha (vedi AppRun): libjack di sistema = PipeWire/JACK dell'utente
mkdir -p "$APPDIR/usr/lib/fallback/jack" "$APPDIR/usr/lib/fallback/xcb" "$APPDIR/usr/fallback-bin"
cp -a "$LIBS"/libjack.so.0* "$LIBS"/libdb-5.3.so "$APPDIR/usr/lib/fallback/jack/"
cp -a "$LIBS"/libxcb-cursor.so.0* "$APPDIR/usr/lib/fallback/xcb/"
cp "$DEBS/usr/bin/amixer" "$APPDIR/usr/fallback-bin/"          # quello di sistema, se c'è, ha la precedenza

# ---------------------------------------------------------------- CueStage
say "CueStage $VERSION"
cp -r "$ROOT/cuestage" "$APPDIR/usr/app/"
cp "$ROOT/packaging/launch.py" "$APPDIR/usr/app/"
find "$APPDIR" -name __pycache__ -prune -exec rm -rf {} +
"$PY" -m compileall -q -j0 "$APPDIR/usr/app" "$APPDIR/usr/python/lib" >/dev/null || true
cp "$ROOT/packaging/AppRun" "$APPDIR/AppRun" && chmod +x "$APPDIR/AppRun"
cp "$ROOT/packaging/cuestage.desktop" "$APPDIR/"
cp "$ROOT/packaging/cuestage.png" "$APPDIR/cuestage.png"
ln -sf cuestage.png "$APPDIR/.DirIcon"

# ---------------------------------------------------------------- prova rapida dentro l'AppDir
say "prova di avvio (senza schermo)"
QT_QPA_PLATFORM=offscreen "$APPDIR/AppRun" --self-test

# ---------------------------------------------------------------- AppImage
fetch "$APPIMAGETOOL_URL" appimagetool
chmod +x "$CACHE/appimagetool"
say "creo $(basename "$OUT")"
rm -f "$OUT"
ARCH=x86_64 "$CACHE/appimagetool" --appimage-extract-and-run --no-appstream -n "$APPDIR" "$OUT" >/dev/null
[ "${KEEP_APPDIR:-0}" = 1 ] || rm -rf "$APPDIR" "$BUILD/ff" "$DEBS"
say "fatto: $OUT ($(du -h "$OUT" | cut -f1))"
