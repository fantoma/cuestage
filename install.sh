#!/usr/bin/env bash
# Installazione di CueStage su Ubuntu (22.04 e successive)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/cuestage"

echo "==> Pacchetti di sistema (richiede la password di amministratore)"
# un repository difettoso nel sistema non deve bloccare l'installazione
if ! sudo apt update; then
  echo
  echo "!! 'apt update' ha segnalato errori (di solito una sorgente software non valida nel sistema)."
  echo "!! Proseguo con gli elenchi di pacchetti già presenti."
  echo
fi
sudo apt install -y python3 python3-venv python3-dev ffmpeg libportaudio2 \
     libasound2-dev libjack-jackd2-dev pkg-config build-essential \
     libxcb-cursor0 libgl1 libegl1 || true

echo "==> Copia dei file in $DEST"
mkdir -p "$DEST"
cp -r "$DIR/cuestage" "$DIR/requirements.txt" "$DIR/run.sh" "$DEST/"
[ -f "$DIR/README.md" ] && cp "$DIR/README.md" "$DEST/"
chmod +x "$DEST/run.sh"

echo "==> Ambiente Python isolato"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --upgrade pip wheel
"$DEST/.venv/bin/pip" install "PySide6>=6.6,<6.12" numpy sounddevice python-osc mido
if ! "$DEST/.venv/bin/pip" install python-rtmidi; then
  echo "!! python-rtmidi non installato: l'ingresso MIDI sarà disattivato (il resto funziona)."
fi

echo "==> Avviatore e voce di menu"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
ln -sf "$DEST/run.sh" "$HOME/.local/bin/cuestage"
cat > "$HOME/.local/share/applications/cuestage.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=CueStage
Comment=Sistema di cue per spettacolo (audio, video, OSC, MIDI)
Exec=$DEST/run.sh %f
Icon=media-playback-start
Terminal=false
Categories=AudioVideo;Audio;Video;
DESK
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

# accesso alle porte MIDI ALSA
if ! id -nG "$USER" | grep -qw audio; then
  sudo usermod -aG audio "$USER" && echo "Aggiunto al gruppo 'audio' (effettivo dal prossimo login)."
fi
echo
echo "Fatto! Avvia «CueStage» dal menu applicazioni oppure con:  cuestage"
