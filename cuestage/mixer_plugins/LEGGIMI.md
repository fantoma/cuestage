# Plugin dei mixer

Un plugin è un file `.py` in `~/.local/share/cuestage/mixer_plugins/` (o in questa cartella) con una classe
derivata da `cuestage.mixers.MixerDriver`. «Ricarica plugin» in Impostazioni › Mixer… lo carica.

```python
from cuestage.mixers import MixerDriver, MixerError, OscLink, TcpMidiLink, NrpnParser, nrpn

class MioMixer(MixerDriver):
    ID = "mio_mixer"                 # univoco
    NAME = "Marca Modello"
    DEFAULT_PORT = 10023
    FIELDS = ("host", "port")        # tra host, port, midi_ch, model, fader_law, midi_out, midi_in, device
    PLATFORMS = ()                   # ("linux",), ("win", "mac")… : vuoto = tutti i sistemi
    MODELS = []                      # [(codice, etichetta)] se FIELDS contiene "model"
    SECTIONS = [("ch", 32, {"mute", "fader"}), ("aux", 16, {"mute", "fader"}), ("mgrp", 6, {"mute"})]
    NOTES = "testo mostrato nel dialogo"

    def set_mute(self, kind, n, muted): ...      # muted=True = muto
    def get_mute(self, kind, n): ...             # -> bool
    def set_fader(self, kind, n, db): ...        # dB, -90 = -∞
    def get_fader(self, kind, n): ...            # -> dB (serve per «Leggi dal mixer» e per i fade)
    def status(self): return {"modello": "..."}  # «Prova connessione»
    def close(self): ...                         # chiude socket/porte

    @classmethod
    def discover(cls, local_ips, timeout=1.5):   # facoltativo: ricerca in rete
        return [{"host": "192.168.1.20", "port": 10023, "name": "FOH", "model": "..."}]
```

Tipi di sezione: `ch` canali, `st` canali stereo, `auxin` aux in, `fx` ritorni effetti, `aux` aux/mix,
`grp` gruppi, `fxs` mandate effetti, `mtx` matrici, `main` master, `dca` DCA, `mgrp` mute group, `ply` player USB.
Nomi dei canali: `NAMES = True` e `get_name(kind, n)` (facoltativo).
Ogni mixer ha il suo thread: i metodi possono bloccare (attese di risposta) senza fermare lo show.
Un driver resta aperto tra un comando e l'altro: tenere socket/porte negli attributi e riaprirli se cadono.
Utilità: `OscLink` (OSC su UDP con richiesta/risposta), `TcpMidiLink` + `NrpnParser` + `nrpn()` (MIDI su
TCP), `udp_discover()` e `tcp_scan()` per la ricerca, `interp()` per tabelle di conversione dei fader.

## Schede audio (controlli generici)

Un driver con `CONTROLS = True` espone controlli qualsiasi (interruttori, menu, volumi) che compaiono nel
pannello mixer e nelle cue Mixer › «Imposta un controllo»:

```python
    def list_controls(self):   # -> [{"key", "name", "type": "int"|"bool"|"enum", "min", "max", "items",
                               #      "db": (dB_min, dB_passo) o ("minmax", min, max) o None, "value",
                               #      "writable", "group"}]
    def set_control(self, key, value): ...
```
`schede_audio_alsa.py` (Focusrite Scarlett/Clarett e qualsiasi scheda ALSA) usa `cuestage.alsactl`
(lettura e scrittura con «amixer») e il campo di configurazione `card` (id della scheda ALSA).
