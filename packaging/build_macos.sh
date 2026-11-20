#!/usr/bin/env bash
# Costruisce Cuevivo.app e il .dmg per macOS 12 Monterey o successivo (Apple Silicon: 12.3; MACOS_MIN=13.0 per alzarlo).
#
#   packaging/build_macos.sh                 (dalla cartella del progetto, su un Mac)
#
# Serve: Python 3.11–3.13 (python.org o Homebrew) e la connessione a Internet. Nessun Xcode completo:
# bastano gli strumenti da riga di comando (xcode-select --install).
# Il pacchetto è per l'architettura del Mac su cui lo costruisci (Apple Silicon = arm64, Intel = x86_64).
#
# Firma e notarizzazione (facoltative, servono un Apple Developer ID):
#   CUEVIVO_CODESIGN_ID="Developer ID Application: Nome (TEAMID)"
#   APPLE_ID=..., APPLE_TEAM_ID=..., APPLE_APP_PASSWORD=...   (password specifica per app)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BUILD="$ROOT/build/macos"
CACHE="$ROOT/build/cache"
VERSION="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' cuestage/__init__.py)"
ARCH="$(uname -m)"                                   # arm64 o x86_64
PY="${PYTHON:-python3}"
say() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
mkdir -p "$BUILD" "$CACHE" dist

say "ambiente Python ($("$PY" --version))"
"$PY" -m venv "$BUILD/venv"
"$BUILD/venv/bin/pip" install -q --upgrade pip
# Librerie compilate per il macOS minimo (MACOS_MIN), non per il Mac che costruisce: su un Mac recente pip
# prenderebbe numpy/scipy «macosx_14_0» e PySide6 6.10+ (macOS 13+) e su Monterey l'app si chiuderebbe subito.
# PySide6 resta sotto la 6.10 (Qt 6.10 richiede macOS 13).
# Apple Silicon: scipy per arm64 è compilato per macOS 12.3 (le sue librerie lo richiedono, anche se il pacchetto
# dice 12.0), quindi su quei Mac il minimo è 12.3; sui Mac Intel resta 12.0.
if [ -z "${MACOS_MIN:-}" ]; then
    case "$ARCH" in arm64) MACOS_MIN=12.3 ;; *) MACOS_MIN=12.0 ;; esac
fi
export MACOS_MIN                                     # anche per cuestage.spec (LSMinimumSystemVersion)
MACOS_TAG="${MACOS_MIN%%.*}_0"                       # pip: le etichette dei pacchetti da macOS 11 in poi sono «N_0»
PYV="$("$BUILD/venv/bin/python" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")')"
WHEELS="$BUILD/wheels-$ARCH"
rm -rf "$WHEELS"
"$BUILD/venv/bin/pip" download -q --only-binary=:all: --platform "macosx_${MACOS_TAG}_$ARCH" \
    --python-version "$PYV" --implementation cp -d "$WHEELS" -r requirements.txt -c packaging/macos/constraints.txt
"$BUILD/venv/bin/pip" install -q --no-index --find-links "$WHEELS" -r requirements.txt \
    -c packaging/macos/constraints.txt
"$BUILD/venv/bin/pip" install -q pyinstaller
"$BUILD/venv/bin/python" -c "import PySide6, numpy, scipy; print('PySide6', PySide6.__version__, '· numpy', numpy.__version__, '· scipy', scipy.__version__)"

say "ffmpeg per $ARCH"
case "$ARCH" in arm64) FFZ=darwin_arm64 ;; *) FFZ=darwin ;; esac
FFURL="${FFMPEG_URL:-https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/$FFZ.zip}"
[ -s "$CACHE/ffmpeg-$FFZ.zip" ] || curl -fL --retry 3 -o "$CACHE/ffmpeg-$FFZ.zip" "$FFURL"
rm -rf "$BUILD/ffmpeg" && mkdir -p "$BUILD/ffmpeg"
unzip -q -j "$CACHE/ffmpeg-$FFZ.zip" "*/ffmpeg" "*/ffprobe" -d "$BUILD/ffmpeg"
chmod +x "$BUILD/ffmpeg/"*
xattr -c "$BUILD/ffmpeg/"* 2>/dev/null || true
"$BUILD/ffmpeg/ffprobe" -version | head -1

say "PyInstaller"
rm -rf dist/Cuevivo dist/Cuevivo.app
CUEVIVO_FFMPEG_DIR="$BUILD/ffmpeg" CUEVIVO_MAC_ARCH="$ARCH" \
    "$BUILD/venv/bin/pyinstaller" packaging/cuestage.spec --noconfirm --distpath dist --workpath "$BUILD/pyi"
rm -rf dist/Cuevivo                                  # resta solo Cuevivo.app
APP="dist/Cuevivo.app"

say "compatibilità con macOS $MACOS_MIN"
"$BUILD/venv/bin/python" packaging/check_macos_min.py "$APP" "$MACOS_MIN"

SIGN="${CUEVIVO_CODESIGN_ID:--}"                     # «-» = firma ad hoc (gratuita, solo locale)
say "firma ($([ "$SIGN" = "-" ] && echo "ad hoc" || echo "$SIGN"))"
codesign --force --deep --timestamp=none --options runtime \
    --entitlements packaging/macos/entitlements.plist -s "$SIGN" "$APP" 2>/dev/null \
  || codesign --force --deep -s "$SIGN" "$APP"

say "prova di avvio"
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/Cuevivo" --self-test "$BUILD/self-test.txt" || {
    cat "$BUILD/self-test.txt" 2>/dev/null; exit 1; }

say "immagine disco"
DMG="dist/Cuevivo-$VERSION-macos-$ARCH.dmg"
rm -rf "$BUILD/dmg" "$DMG" && mkdir -p "$BUILD/dmg"
ditto "$APP" "$BUILD/dmg/Cuevivo.app"            # come cp -R, ma conserva firme e attributi
ln -s /Applications "$BUILD/dmg/Applicazioni"
# hdiutil a volte fallisce con «Resource busy» (l'antivirus di macOS sta ancora esaminando i file appena
# creati, capita spesso sui Mac di GitHub): si riprova qualche volta prima di arrendersi
DMG_OK=0
for n in 1 2 3 4 5; do
    if hdiutil create -volname "Cuevivo $VERSION" -srcfolder "$BUILD/dmg" -ov -fs HFS+ -format UDZO "$DMG"; then
        DMG_OK=1
        break
    fi
    echo "hdiutil non riuscito (tentativo $n di 5): riprovo tra $((n * 10)) s"
    rm -f "$DMG"
    sudo -n pkill -9 XProtect >/dev/null 2>&1 || true
    sleep $((n * 10))
done
if [ "$DMG_OK" != 1 ]; then
    # piano B: l'app in uno zip (si scompatta con un doppio clic e si trascina in Applicazioni)
    ZIP="dist/Cuevivo-$VERSION-macos-$ARCH.zip"
    echo "::warning::immagine disco non creata: pubblico l'app in $ZIP"
    ditto -c -k --keepParent "$APP" "$ZIP"
    DMG="$ZIP"
fi

if [ "$SIGN" != "-" ] && [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
    say "notarizzazione Apple (qualche minuto)"
    if [ "${DMG##*.}" = dmg ]; then codesign -s "$SIGN" --timestamp "$DMG"; fi
    xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_APP_PASSWORD" --wait
    if [ "${DMG##*.}" = dmg ]; then xcrun stapler staple "$DMG"; fi
fi
say "fatto: $DMG ($(du -h "$DMG" | cut -f1))"
