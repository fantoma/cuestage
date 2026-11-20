#!/usr/bin/env bash
# Installazione di CueStage su Ubuntu (22.04 e successive)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APP="CueStage"                     # nome che si vede (come APP_NAME in cuestage/appinfo.py)
SLUG="cuestage"                    # cartella, comando e voce di menu (come APP_SLUG)
OLD_SLUGS=""                       # nomi precedenti: installazione e dati vengono spostati qui
DEST="$HOME/.local/share/$SLUG"

for OLD in $OLD_SLUGS; do
  OLD_DIR="$HOME/.local/share/$OLD"
  if [ "$OLD" != "$SLUG" ] && [ -d "$OLD_DIR" ] && [ ! -e "$DEST" ]; then
    echo "==> Sposto l'installazione precedente ($OLD_DIR) in $DEST (dati, plugin e profili compresi)"
    mv "$OLD_DIR" "$DEST"
    rm -rf "$DEST/.venv"            # l'ambiente Python va ricreato nel nuovo percorso
    echo "$OLD_DIR" > "$DEST/.migrated-from"
  fi
  # vecchia voce di menu e icona via; il vecchio comando resta e avvia il nuovo programma
  rm -f "$HOME/.local/share/applications/$OLD.desktop" \
        "$HOME/.local/share/icons/hicolor/256x256/apps/$OLD.png"
  if [ -L "$HOME/.local/bin/$OLD" ] || [ ! -e "$HOME/.local/bin/$OLD" ]; then
    mkdir -p "$HOME/.local/bin"
    ln -sf "$DEST/run.sh" "$HOME/.local/bin/$OLD"
  fi
done

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
"$DEST/.venv/bin/pip" install "PySide6>=6.6,<6.12" numpy scipy sounddevice python-osc mido
if ! "$DEST/.venv/bin/python" -c "import scipy.signal" 2>/dev/null; then
  echo "!! scipy non utilizzabile nel venv: riprovo l'installazione"
  "$DEST/.venv/bin/pip" install --force-reinstall scipy || echo "!! scipy non installato: gli effetti audio saranno disattivati."
fi
if ! "$DEST/.venv/bin/pip" install python-rtmidi; then
  echo "!! python-rtmidi non installato: l'ingresso MIDI sarà disattivato (il resto funziona)."
fi

echo "==> Avviatore, icona e voce di menu"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
ln -sf "$DEST/run.sh" "$HOME/.local/bin/$SLUG"
cp "$DIR/cuestage/icon.png" "$HOME/.local/share/icons/hicolor/256x256/apps/$SLUG.png"
# StartupWMClass e il nome del file ($SLUG.desktop = setDesktopFileName nel programma) fanno sì che GNOME
# riconosca la finestra e ne mostri l'icona nella barra delle applicazioni (X11 e Wayland)
cat > "$HOME/.local/share/applications/$SLUG.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=$APP
GenericName=Show control
Comment=Sistema di cue per spettacolo (audio, video, OSC, MIDI)
Exec="$DEST/run.sh" %f
Icon=$SLUG
Terminal=false
StartupNotify=true
StartupWMClass=$APP
Categories=AudioVideo;Audio;Video;
Keywords=cue;show;theatre;teatro;qlab;
DESK
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

# accesso alle porte MIDI ALSA
if ! id -nG "$USER" | grep -qw audio; then
  sudo usermod -aG audio "$USER" && echo "Aggiunto al gruppo 'audio' (effettivo dal prossimo login)."
fi
echo
echo "Fatto! Avvia «$APP» dal menu applicazioni oppure con:  $SLUG"
