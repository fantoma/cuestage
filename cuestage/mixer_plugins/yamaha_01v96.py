"""Plugin CueStage — Yamaha 01V96 (01V96, 01V96i, 01V96VCM) e 02R96, via MIDI (SysEx «Parameter Change»).

Formato (come nel progetto open source «01v96-remote», verificato sul 01V96):
  - Parameter Change:  F0 43 1n 3E 7F 01 <elemento> <indice> <canale> d1 d2 d3 d4 F7
  - Parameter Request: F0 43 3n 3E 7F 01 <elemento> <indice> <canale> F7  → il mixer risponde con un Change
    (n = numero di dispositivo MIDI, di solito 0; 7F = valido per tutti i modelli della famiglia)
  - elementi: fader canale 28, ON canale 26 · fader aux master 57, ON 54 · fader bus 43, ON 41 ·
    fader stereo master 79, ON 77; indice 0; canale = numero − 1
  - fader a 10 bit (0…1023) in d3 d4: 00 00 0hhh 0lllllll · ON: d4 = 1 acceso, 0 spento (= muto)
Sul mixer: DIGITAL I/O › MIDI › Setup: «Parameter Change» Rx ON (e Tx ON per leggere i livelli),
stesso numero di dispositivo; collegare MIDI OUT del computer al MIDI IN del mixer (e viceversa per leggere).
La conversione 0…1023 ↔ dB è quella della scala Yamaha a 1024 passi (0 dB = 823, +10 dB = 1023):
i valori intermedi sono approssimati, ma rileggere e reinviare un livello lo riporta identico.
02R96: stessi messaggi e stessi elementi di base della famiglia, ma la tabella dei parametri del 02R96
NON è stata verificata: provare prima e, se necessario, correggere ELEMENTS e MODEL_BYTE qui sotto.
"""
from cuestage.i18n import tr
import queue

from cuestage.mixers import MixerDriver, MixerError, interp, resolve_midi_port

try:
    import mido
except ImportError:            # pragma: no cover
    mido = None

YAMAHA_FADER = [(0, -90.0), (1, -86.0), (63, -60.0), (223, -40.0), (423, -25.0), (623, -10.0), (823, 0.0),
                (1023, 10.0)]


def ya_to_db(v):
    return -90.0 if v <= 0 else interp(YAMAHA_FADER, v)


def db_to_ya(db):
    if db <= -89.9:
        return 0
    return max(1, min(1023, int(round(interp([(d, v) for v, d in YAMAHA_FADER[1:]], max(-86.0, db))))))


class Yamaha01V96(MixerDriver):
    ID = "yamaha_01v96"
    NAME = tr("Yamaha 01V96 / 01V96i / VCM (MIDI)")
    DEFAULT_PORT = 0
    FIELDS = ("midi_out", "midi_in", "device")
    MODEL_BYTE = 0x7F                       # 0x7F = famiglia (0x0D = solo 01V96)
    ELEMENTS = {"ch": (28, 26), "aux": (57, 54), "grp": (43, 41), "main": (79, 77)}   # (fader, ON)
    SECTIONS = [("ch", 32, {"mute", "fader"}), ("aux", 8, {"mute", "fader"}), ("grp", 8, {"mute", "fader"}),
                ("main", 1, {"mute", "fader"})]
    NOTES = (tr("Via MIDI: scegli l'uscita MIDI collegata al mixer e, per leggere i livelli, l'ingresso MIDI che "
             "riceve dal mixer. Sul mixer: Parameter Change Rx/Tx ON, stesso numero di dispositivo. "
             "Gruppi = bus 1–8. Mute = tasto ON del canale."))

    def __init__(self, conf):
        super().__init__(conf)
        self.out = None
        self.inp = None
        self.q = queue.Queue()
        try:
            self.dev = max(0, min(15, int(conf.get("device") or 0)))
        except (TypeError, ValueError):
            self.dev = 0

    def _out(self):
        if mido is None:
            raise MixerError(tr("mido/python-rtmidi non installati"))
        if self.out is None:
            name = resolve_midi_port(self.conf.get("midi_out"), mido.get_output_names())
            if name is None:
                raise MixerError(tr("uscita MIDI «{0}» non trovata", self.conf.get('midi_out') or '—'))
            try:
                self.out = mido.open_output(name)
            except Exception as e:
                raise MixerError(tr("impossibile aprire l'uscita MIDI ({0})", e))
        return self.out

    def _in(self):
        if mido is None:
            raise MixerError(tr("mido/python-rtmidi non installati"))
        if self.inp is None:
            name = resolve_midi_port(self.conf.get("midi_in"), mido.get_input_names())
            if name is None:
                raise MixerError(tr("per leggere i livelli serve l'ingresso MIDI collegato al MIDI OUT del mixer"))
            try:
                self.inp = mido.open_input(name, callback=lambda m: m.type == "sysex" and self.q.put(list(m.data)))
            except Exception as e:
                raise MixerError(tr("impossibile aprire l'ingresso MIDI ({0})", e))
        return self.inp

    def _send(self, data):
        self._out().send(mido.Message("sysex", data=data))

    def _change(self, elem, n, d):
        self._send([0x43, 0x10 | self.dev, 0x3E, self.MODEL_BYTE, 0x01, elem, 0x00, int(n) - 1] + d)

    def _request(self, elem, n):
        self._in()
        while not self.q.empty():
            self.q.get_nowait()
        self._send([0x43, 0x30 | self.dev, 0x3E, self.MODEL_BYTE, 0x01, elem, 0x00, int(n) - 1])
        try:
            while True:
                d = self.q.get(timeout=1.0)
                # risposta: 43 1n 3E <modello> 01 elem idx ch d1 d2 d3 d4
                if len(d) >= 12 and d[0] == 0x43 and d[2] == 0x3E and d[4] == 0x01 and d[5] == elem \
                        and d[7] == int(n) - 1:
                    return d[8:12]
        except queue.Empty:
            raise MixerError(tr("nessuna risposta dal mixer (Parameter Change Tx attivo? ingresso MIDI giusto?)"))

    def set_mute(self, kind, n, muted):
        self.check(kind, n, "mute")
        self._change(self.ELEMENTS[kind][1], n, [0, 0, 0, 0 if muted else 1])

    def get_mute(self, kind, n):
        self.check(kind, n, "mute")
        d = self._request(self.ELEMENTS[kind][1], n)
        return not bool(d[3])

    def set_fader(self, kind, n, db):
        self.check(kind, n, "fader")
        v = db_to_ya(db)
        self._change(self.ELEMENTS[kind][0], n, [0, 0, v >> 7, v & 0x7F])

    def get_fader(self, kind, n):
        self.check(kind, n, "fader")
        d = self._request(self.ELEMENTS[kind][0], n)
        return ya_to_db((d[2] << 7) + d[3])

    def status(self):
        self._out()
        st = {"uscita MIDI": self.out.name}
        if self.conf.get("midi_in"):
            try:
                st["fader master"] = f"{self.get_fader('main', 1):+.1f} dB"
            except MixerError as e:
                st["lettura"] = str(e)
        return st

    def close(self):
        for p in (self.out, self.inp):
            try:
                if p is not None:
                    p.close()
            except Exception:
                pass
        self.out = self.inp = None


class Yamaha02R96(Yamaha01V96):
    ID = "yamaha_02r96"
    NAME = tr("Yamaha 02R96 (MIDI, da verificare)")
    SECTIONS = [("ch", 56, {"mute", "fader"}), ("aux", 8, {"mute", "fader"}), ("grp", 8, {"mute", "fader"}),
                ("main", 1, {"mute", "fader"})]
    NOTES = (tr("⚠ Stessi messaggi SysEx della famiglia 01V96, ma la tabella dei parametri del 02R96 non è stata "
             "verificata: prova un mute e un fader prima dello spettacolo. Sul mixer: Parameter Change Rx/Tx ON."))
