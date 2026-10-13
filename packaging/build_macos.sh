#!/usr/bin/env bash
# Costruisce CueStage.app e il .dmg per macOS (12 Monterey o successivo).
#
#   packaging/build_macos.sh                 (dalla cartella del progetto, su un Mac)
#
# Serve: Python 3.11–3.13 (python.org o Homebrew) e la connessione a Internet. Nessun Xcode completo:
# bastano gli strumenti da riga di comando (xcode-select --install).
# Il pacchetto è per l'architettura del Mac su cui lo costruisci (Apple Silicon = arm64, Intel = x86_64).
#
# Firma e notarizzazione (facoltative, servono un Apple Developer ID):
#   CUESTAGE_CODESIGN_ID="Developer ID Application: Nome (TEAMID)"
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
"$BUILD/venv/bin/pip" install -q -r requirements.txt pyinstaller

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
rm -rf dist/CueStage dist/CueStage.app
CUESTAGE_FFMPEG_DIR="$BUILD/ffmpeg" CUESTAGE_MAC_ARCH="$ARCH" \
    "$BUILD/venv/bin/pyinstaller" packaging/cuestage.spec --noconfirm --distpath dist --workpath "$BUILD/pyi"
rm -rf dist/CueStage                                  # resta solo CueStage.app
APP="dist/CueStage.app"

SIGN="${CUESTAGE_CODESIGN_ID:--}"                     # «-» = firma ad hoc (gratuita, solo locale)
say "firma ($([ "$SIGN" = "-" ] && echo "ad hoc" || echo "$SIGN"))"
codesign --force --deep --timestamp=none --options runtime \
    --entitlements packaging/macos/entitlements.plist -s "$SIGN" "$APP" 2>/dev/null \
  || codesign --force --deep -s "$SIGN" "$APP"

say "prova di avvio"
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/CueStage" --self-test "$BUILD/self-test.txt" || {
    cat "$BUILD/self-test.txt" 2>/dev/null; exit 1; }

say "immagine disco"
DMG="dist/CueStage-$VERSION-macos-$ARCH.dmg"
rm -rf "$BUILD/dmg" "$DMG" && mkdir -p "$BUILD/dmg"
cp -R "$APP" "$BUILD/dmg/"
ln -s /Applications "$BUILD/dmg/Applicazioni"
hdiutil create -quiet -volname "CueStage $VERSION" -srcfolder "$BUILD/dmg" -ov -format UDZO "$DMG"

if [ "$SIGN" != "-" ] && [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
    say "notarizzazione Apple (qualche minuto)"
    codesign -s "$SIGN" --timestamp "$DMG"
    xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_APP_PASSWORD" --wait
    xcrun stapler staple "$DMG"
fi
say "fatto: $DMG ($(du -h "$DMG" | cut -f1))"
