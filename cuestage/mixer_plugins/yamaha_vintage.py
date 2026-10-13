"""Plugin CueStage — Yamaha 01V e 02R (i modelli originali), via MIDI Control Change.

Questi mixer si comandano con i Control Change della loro «tabella di assegnazione» (MIDI › Control Change
Assign); il plugin usa la tabella INIZIALE di fabbrica dei manuali:
  01V («01V Parameter To Control Change Table», pag. 274-275), valori 0…127:
    FADER: CC 1–12 CH1–12 · 13 CH13-14 · 14 CH15-16 · 15–16 RETURN 1–2 · 17–20 AUX 1–4 MASTER ·
           21–24 BUS 1–4 MASTER · 25–26 EFFECT 1–2 MASTER · 27 ST OUT
    ON:    CC 28–31 CH1–4 · (32 non assegnato) · 33–40 CH5–12 · 41 CH13-14 · 42 CH15-16 · 43–44 RETURN 1–2 ·
           45–48 AUX 1–4 · 49–52 BUS 1–4 · 53–54 EFFECT 1–2 · 55 ST OUT
  02R («Parameter to Control Change Table, 02R Map», pag. 256-258):
    FADER: CC 1–16 MIC 1–16 · 17–20 LINE 17/18…23/24 · 21–22 EFF 1–2 · 23 ST MASTER · 24–31 AUX 1–8 ·
           (32 non assegnato) · 33–40 BUS 1–8
    ON:    CC 41–56 MIC 1–16 · 57–60 LINE 17/18…23/24 · 61–62 EFF 1–2 · 63 ST MASTER
           (nella mappa 02R gli AUX e i BUS master non hanno ON)
Messaggio: Bn cc vv sul canale MIDI di ricezione del mixer (Rx CH); ON: 127 acceso, 0 spento (= muto).
Sul mixer: MIDI Setup › Control Change Rx ON e canale Rx = quello indicato qui.
Lettura dei livelli: il 01V trasmette i Control Change quando si muove un fader (Control Change Tx ON):
CueStage ricorda l'ultimo valore ricevuto per ogni controllo. Il 02R NON trasmette Control Change, quindi
non si può leggere il livello dal mixer (si imposta a mano nella cue).
Se sul mixer la tabella è stata cambiata, correggere FADER_CC / ON_CC qui sotto.
La scala 0…127 ↔ dB è approssimata (0 = −∞, 103 ≈ 0 dB, 127 = +6 dB).
"""
import threading

from cuestage.mixers import MixerDriver, MixerError, interp, resolve_midi_port

try:
    import mido
except ImportError:            # pragma: no cover
    mido = None

CC_FADER_LAW = [(0, -90.0), (1, -80.0), (31, -45.0), (55, -25.0), (79, -10.0), (103, 0.0), (127, 6.0)]


def cc_to_db(v):
    return -90.0 if v <= 0 else interp(CC_FADER_LAW, v)


def db_to_cc(db):
    if db <= -89.9:
        return 0
    return max(1, min(127, int(round(interp([(d, v) for v, d in CC_FADER_LAW[1:]], max(-80.0, db))))))


class _YamahaCC(MixerDriver):
    DEFAULT_PORT = 0
    FIELDS = ("midi_out", "midi_in", "midi_ch")
    FADER_CC = {}
    ON_CC = {}
    TRANSMITS_CC = True

    def __init__(self, conf):
        super().__init__(conf)
        self.out = None
        self.inp = None
        self.values = {}            # numero CC -> ultimo valore (inviato o ricevuto dal mixer)
        self.lock = threading.Lock()
        try:
            self.ch = max(1, min(16, int(conf.get("midi_ch") or 1))) - 1
        except (TypeError, ValueError):
            self.ch = 0

    @classmethod
    def sections(cls, conf=None):
        out = []
        for kind in dict.fromkeys(list(cls.FADER_CC) + list(cls.ON_CC)):
            n = max(len(cls.FADER_CC.get(kind, [])), len(cls.ON_CC.get(kind, [])))
            caps = set()
            if kind in cls.ON_CC:
                caps.add("mute")
            if kind in cls.FADER_CC:
                caps.add("fader")
            out.append((kind, n, caps))
        return out

    def _out(self):
        if mido is None:
            raise MixerError("mido/python-rtmidi non installati")
        if self.out is None:
            name = resolve_midi_port(self.conf.get("midi_out"), mido.get_output_names())
            if name is None:
                raise MixerError(f"uscita MIDI «{self.conf.get('midi_out') or '—'}» non trovata")
            try:
                self.out = mido.open_output(name)
            except Exception as e:
                raise MixerError(f"impossibile aprire l'uscita MIDI ({e})")
            self._listen()
        return self.out

    def _listen(self):
        """Ascolta i Control Change che il mixer trasmette (fader mossi a mano)."""
        if self.inp is not None or not self.conf.get("midi_in") or mido is None:
            return
        name = resolve_midi_port(self.conf.get("midi_in"), mido.get_input_names())
        if name is None:
            return
        try:
            self.inp = mido.open_input(name, callback=self._incoming)
        except Exception:
            self.inp = None

    def _incoming(self, msg):
        if msg.type == "control_change" and msg.channel == self.ch:
            with self.lock:
                self.values[msg.control] = msg.value

    def _cc(self, table, kind, n):
        lst = table.get(kind) or []
        if not 1 <= int(n) <= len(lst) or lst[int(n) - 1] is None:
            raise MixerError("controllo non presente nella tabella del mixer")
        return lst[int(n) - 1]

    def _send(self, cc, value):
        self._out().send(mido.Message("control_change", channel=self.ch, control=cc, value=int(value)))
        with self.lock:
            self.values[cc] = int(value)

    def set_mute(self, kind, n, muted):
        self.check(kind, n, "mute")
        self._send(self._cc(self.ON_CC, kind, n), 0 if muted else 127)

    def set_fader(self, kind, n, db):
        self.check(kind, n, "fader")
        self._send(self._cc(self.FADER_CC, kind, n), db_to_cc(db))

    def _known(self, cc):
        self._out()
        with self.lock:
            v = self.values.get(cc)
        if v is None:
            if not self.TRANSMITS_CC:
                raise MixerError(f"il {self.NAME.split(' (')[0]} non trasmette i livelli: impostalo a mano nella cue")
            if not self.conf.get("midi_in"):
                raise MixerError("per leggere serve l'ingresso MIDI collegato al MIDI OUT del mixer")
            raise MixerError("valore non ancora ricevuto: muovi il fader sul mixer (Control Change Tx ON)")
        return v

    def get_mute(self, kind, n):
        self.check(kind, n, "mute")
        return self._known(self._cc(self.ON_CC, kind, n)) < 64

    def get_fader(self, kind, n):
        self.check(kind, n, "fader")
        return cc_to_db(self._known(self._cc(self.FADER_CC, kind, n)))

    def status(self):
        self._out()
        return {"uscita MIDI": self.out.name, "canale MIDI": self.ch + 1,
                "ascolto dal mixer": "sì" if self.inp is not None else "no"}

    def close(self):
        for p in (self.out, self.inp):
            try:
                if p is not None:
                    p.close()
            except Exception:
                pass
        self.out = self.inp = None


class Yamaha01V(_YamahaCC):
    ID = "yamaha_01v"
    NAME = "Yamaha 01V (originale, MIDI CC)"
    FADER_CC = {"ch": list(range(1, 13)), "st": [13, 14], "fx": [15, 16], "aux": [17, 18, 19, 20],
                "grp": [21, 22, 23, 24], "fxs": [25, 26], "main": [27]}
    ON_CC = {"ch": [28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39, 40], "st": [41, 42], "fx": [43, 44],
             "aux": [45, 46, 47, 48], "grp": [49, 50, 51, 52], "fxs": [53, 54], "main": [55]}
    NOTES = ("Via MIDI Control Change, tabella iniziale del 01V. Canali stereo = CH13-14 e CH15-16; Ritorni FX = "
             "RETURN 1–2; Gruppi = BUS 1–4; Mandate FX = EFFECT 1–2 master; Master = ST OUT. Sul mixer: Control "
             "Change Rx ON (e Tx ON per leggere i livelli) sul canale MIDI indicato. dB approssimati.")


class Yamaha02R(_YamahaCC):
    ID = "yamaha_02r"
    NAME = "Yamaha 02R (originale, MIDI CC)"
    TRANSMITS_CC = False
    FADER_CC = {"ch": list(range(1, 17)), "st": [17, 18, 19, 20], "fx": [21, 22], "main": [23],
                "aux": list(range(24, 32)), "grp": list(range(33, 41))}
    ON_CC = {"ch": list(range(41, 57)), "st": [57, 58, 59, 60], "fx": [61, 62], "main": [63]}
    FIELDS = ("midi_out", "midi_ch")
    NOTES = ("Via MIDI Control Change, mappa «02R» di fabbrica. Canali = MIC 1–16; Canali stereo = LINE 17/18…23/24; "
             "Ritorni FX = EFF 1–2; Master = ST MASTER; Aux 1–8 e Gruppi = BUS 1–8 (solo fader). Il 02R non "
             "trasmette Control Change: i livelli non si leggono dal mixer. Sul mixer: Control Change Rx ON. "
             "dB approssimati.")
