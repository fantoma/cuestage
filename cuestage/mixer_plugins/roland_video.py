"""Plugin CueStage — regie video Roland V-8HD e V-1HD, via MIDI (USB).

Collegamento: cavo USB dal computer alla regia; la regia compare come porta MIDI (su Linux: «amidi -l»).

V-8HD (porta USB-C REMOTE / USB, canale MIDI fisso 1; dal «V-8HD Reference Manual» v3, MIDI Implementation):
  - CC10 fader video (0 = in basso, 127 = in alto) · CC11 tipo di transizione (MIX, WIPE) · CC12 tempo MIX/WIPE
    0–40 (0,0–4,0 s) · CC13/14/15 tempi PinP 1, PinP 2, DSK
  - CC16–20 PinP 1: sorgente 0–15 (HDMI 1–8, STILL 1–8), posizione H/V 10–100 (−50…50 %), dimensione 10–100 %,
    zoom 10–100 (100–1000 %) · CC21–25 PinP 2 · CC26–29 DSK: sorgente, level, gain, mix level
  - CC30/31 SPLIT/VFX A on/off e tipo · CC32/33 SPLIT/VFX B · CC34/35 OUTPUT FADE verso sinistra / destra 0–63
  - CC36–43 livello audio ingressi 1–8 · CC44 AUDIO IN · CC45 uscita (tabella in dB a 128 passi)
  - CC54 CUT · CC55 AUTO TAKE · CC56–64 mute ingressi 1–8 e AUDIO IN · CC65 mute uscita
  - Program Change 0–7 = memorie 1–8
  - SysEx DT1  F0 41 10 00 00 00 68 12 aa bb cc dato somma F7:  00 12 00 PGM · 00 12 01 PST · 00 12 02 AUX
    (0–7 = INPUT 1–8) · 00 0D 00 / 00 0E 00 PinP 1/2 sul programma · 00 0F 00 DSK sul programma

V-1HD (USB o MIDI IN; «MIDI Rx SOURCE» = USB e «MIDI Rx CHANNEL» = canale scelto qui; dalla «V-1HD Remote
Control Guide»):
  - Bank Select CC0/CC32 + Program Change: 00/00 + 0–3 = bus A ingresso 1–4 · 01/00 + 0–3 = bus B ·
    50H/00 + 0–7 = memoria 1–8
  - CC10–15 livelli HDMI 1–4, AUDIO IN, MIC · CC16 master · CC17 fader A/B · CC19 transizione (WIPE, MIX, CUT)
  - CC23/24 EFFECTS A/B (0 = spento) · CC25 manopola OUTPUT FADE · CC26 FREEZE (0 spento, 127 acceso)
  Il V-1HD non ha un comando MIDI per l'AUTO: CueStage lo esegue muovendo il fader A/B nel tempo scelto.

Le regie non rimandano il loro stato: i valori mostrati sono gli ultimi inviati da CueStage.
"""
import time

from cuestage.i18n import tr
from cuestage.mixers import MixerDriver, MixerError, resolve_midi_port

try:
    import mido
except ImportError:            # pragma: no cover
    mido = None

# livello audio V-8HD: valore MIDI 0…127 → dB (tabella del manuale; 0 = −∞)
V8_LEVEL_DB = [None, -80.0, -76.7, -73.3, -70.0, -66.7, -63.3, -60.0, -58.6, -57.1, -55.7, -54.3, -52.9, -51.4,
               -50.0, -48.9, -47.8, -46.7, -45.6, -44.4, -43.3, -42.2, -41.1, -40.0, -39.2, -38.5, -37.7, -36.9,
               -36.2, -35.4, -34.6, -33.8, -33.1, -32.3, -31.5, -30.8, -30.0, -29.3, -28.7, -28.0, -27.3, -26.7,
               -26.0, -25.3, -24.7, -24.0, -23.3, -22.7, -22.0, -21.3, -20.7, -20.0, -19.3, -18.7, -18.0, -17.3,
               -16.7, -16.0, -15.3, -14.7, -14.0, -13.3, -12.7, -12.0, -11.3, -10.7, -10.3, -10.0, -9.7, -9.3,
               -9.0, -8.7, -8.3, -8.0, -7.7, -7.3, -7.0, -6.7, -6.3, -6.0, -5.7, -5.3, -5.0, -4.7, -4.3, -4.0,
               -3.7, -3.3, -3.0, -2.7, -2.3, -2.0, -1.7, -1.3, -1.0, -0.7, -0.3, 0.0, 0.3, 0.7, 1.0, 1.3, 1.7,
               2.0, 2.3, 2.7, 3.0, 3.3, 3.7, 4.0, 4.3, 4.7, 5.0, 5.3, 5.7, 6.0, 6.3, 6.7, 7.0, 7.3, 7.7, 8.0,
               8.3, 8.7, 9.0, 9.3, 9.7, 10.0]


def v8_level_to_db(v):
    v = max(0, min(127, int(v)))
    return -90.0 if v == 0 else V8_LEVEL_DB[v]


def v8_db_to_level(db):
    if db is None or db <= -80.5:
        return 0
    return min(range(1, 128), key=lambda i: abs(V8_LEVEL_DB[i] - db))


def roland_checksum(data):
    return (128 - sum(data) % 128) % 128


def roland_dt1(model, addr, values, dev=0x10):
    """Messaggio SysEx Roland «Data Set 1» (senza F0/F7, come lo vuole mido)."""
    body = list(addr) + list(values)
    return [0x41, dev] + list(model) + [0x12] + body + [roland_checksum(body)]


GROUPS = (tr("Commutazione"), tr("Transizioni"), tr("Memorie"), tr("PinP e keyer"), tr("Effetti"),
          tr("Uscita video"), tr("Audio della regia"))


class _RolandMidi(MixerDriver):
    """Parte comune: porta MIDI in uscita, invio di CC / Program Change / SysEx, stato in memoria."""
    DEFAULT_PORT = 0
    CONTROLS = True
    CHANNEL_FIXED = None            # V-8HD: canale fisso

    def __init__(self, conf):
        super().__init__(conf)
        self.out = None
        self.state = {}
        if self.CHANNEL_FIXED is not None:
            self.ch = self.CHANNEL_FIXED
        else:
            try:
                self.ch = max(1, min(16, int(conf.get("midi_ch") or 1))) - 1
            except (TypeError, ValueError):
                self.ch = 0
        for c in self._controls():
            self.state.setdefault(c["key"], c.get("default", 0 if c["type"] != "action" else None))

    # --- MIDI
    def _out(self):
        if self.out is not None:
            return self.out
        if mido is None:
            raise MixerError(tr("mido/python-rtmidi non installati"))
        name = resolve_midi_port(self.conf.get("midi_out"), mido.get_output_names())
        if name is None:
            raise MixerError(tr("uscita MIDI «{0}» non trovata (regia collegata via USB e accesa?)",
                                self.conf.get('midi_out') or '—'))
        try:
            self.out = mido.open_output(name)
        except Exception as e:
            raise MixerError(tr("impossibile aprire l'uscita MIDI ({0})", e))
        return self.out

    def _send(self, msg):
        try:
            self._out().send(msg)
        except MixerError:
            raise
        except Exception as e:
            self.close()
            raise MixerError(tr("invio MIDI non riuscito ({0})", e))

    def cc(self, num, value):
        self._send(mido.Message("control_change", channel=self.ch, control=int(num),
                                value=max(0, min(127, int(value)))))

    def pc(self, prog):
        self._send(mido.Message("program_change", channel=self.ch, program=max(0, min(127, int(prog)))))

    def sysex(self, data):
        self._send(mido.Message("sysex", data=list(data)))

    def close(self):
        if self.out is not None:
            try:
                self.out.close()
            except Exception:
                pass
        self.out = None

    def status(self):
        port = self._out()
        return {tr("porta MIDI"): getattr(port, "name", str(self.conf.get("midi_out") or "")),
                tr("canale"): self.ch + 1}

    # --- controlli
    def _controls(self):
        return []

    def list_controls(self):
        out = []
        for c in self._controls():
            d = dict(c)
            d.setdefault("min", 0)
            d.setdefault("max", len(d.get("items") or []) - 1 if d["type"] == "enum" else 1)
            d.setdefault("items", [])
            d.setdefault("db", None)
            d.setdefault("writable", True)
            d["value"] = self.state.get(c["key"])
            out.append(d)
        return out

    def set_control(self, key, value):
        handler = getattr(self, "_set_" + key, None)
        info = next((c for c in self._controls() if c["key"] == key), None)
        if info is None:
            raise MixerError(tr("controllo «{0}» sconosciuto", key))
        value = int(value or 0)
        if info["type"] != "action":
            lo = info.get("min", 0)
            hi = info.get("max", len(info.get("items") or []) - 1 if info["type"] == "enum" else 1)
            value = max(lo, min(hi, value))
        if handler is not None:
            handler(value)
        elif "cc" in info:
            if info["type"] == "bool":
                self.cc(info["cc"], info.get("on", 127) if value else 0)
            else:
                self.cc(info["cc"], value)
        else:
            raise MixerError(tr("controllo «{0}» non comandabile", key))
        if info["type"] != "action" and key not in self.NO_STATE:
            self.state[key] = value

    NO_STATE = ()


def _inputs(n, still=0):
    return [f"HDMI {i}" for i in range(1, n + 1)] + [f"STILL {i}" for i in range(1, still + 1)]


class RolandV8HD(_RolandMidi):
    ID = "roland_v8hd"
    NAME = tr("Roland V-8HD (regia video, MIDI via USB)")
    FIELDS = ("midi_out",)
    CHANNEL_FIXED = 0
    MODEL = (0x00, 0x00, 0x00, 0x68)
    SECTIONS = [("ch", 9, {"mute", "fader"}), ("main", 1, {"mute", "fader"})]
    NO_STATE = ("cut_to", "auto_to")
    NOTES = tr("Collega la regia al computer con il cavo USB e scegli la sua porta MIDI. Canale MIDI fisso (1). "
               "Canali audio 1–8 = ingressi HDMI 1–8, canale 9 = AUDIO IN; Master = uscita audio. "
               "«CUT a» / «AUTO a» mettono l'ingresso in preset e lo mandano in onda (taglio o dissolvenza). "
               "La regia non rimanda il suo stato: i valori mostrati sono gli ultimi inviati da CueStage.")

    def _controls(self):
        G = GROUPS
        ins = _inputs(8)
        srcs = _inputs(8, 8)
        pct_pos = (-50 - 10 * 100 / 90, 100 / 90, " %", 0)
        out = [
            dict(key="pgm", name=tr("Programma (PGM)"), type="enum", items=ins, group=G[0]),
            dict(key="pst", name=tr("Preset (PST)"), type="enum", items=ins, default=1, group=G[0]),
            dict(key="cut_to", name=tr("CUT a"), type="enum", items=ins, group=G[0]),
            dict(key="auto_to", name=tr("AUTO a"), type="enum", items=ins, group=G[0]),
            dict(key="cut", name=tr("CUT (scambia PGM e PST)"), type="action", group=G[0]),
            dict(key="auto", name=tr("AUTO TAKE (scambia PGM e PST)"), type="action", group=G[0]),
            dict(key="aux", name=tr("Uscita AUX"), type="enum", items=ins, group=G[0]),
            dict(key="fader", name=tr("Fader video"), type="int", min=0, max=127, cc=10, group=G[0],
                 fmt=(0, 100 / 127, " %", 0)),
            dict(key="trans_type", name=tr("Tipo di transizione"), type="enum", items=["MIX", "WIPE"], cc=11,
                 group=G[1]),
            dict(key="trans_time", name=tr("Tempo MIX/WIPE"), type="int", min=0, max=40, cc=12, default=10,
                 group=G[1], fmt=(0, 0.1, " s", 1)),
            dict(key="pinp1_time", name=tr("Tempo PinP 1"), type="int", min=0, max=40, cc=13, default=10,
                 group=G[1], fmt=(0, 0.1, " s", 1)),
            dict(key="pinp2_time", name=tr("Tempo PinP 2"), type="int", min=0, max=40, cc=14, default=10,
                 group=G[1], fmt=(0, 0.1, " s", 1)),
            dict(key="dsk_time", name=tr("Tempo DSK"), type="int", min=0, max=40, cc=15, default=10,
                 group=G[1], fmt=(0, 0.1, " s", 1)),
            dict(key="memory", name=tr("Richiama memoria"), type="enum",
                 items=[tr("Memoria {0}", i) for i in range(1, 9)], group=G[2]),
        ]
        for p, base, addr in ((1, 16, 0x0D), (2, 21, 0x0E)):
            out += [
                dict(key=f"pinp{p}_on", name=tr("PinP {0} in onda", p), type="bool", group=G[3], sx=(0x00, addr, 0x00)),
                dict(key=f"pinp{p}_src", name=tr("PinP {0} sorgente", p), type="enum", items=srcs, cc=base,
                     group=G[3]),
                dict(key=f"pinp{p}_h", name=tr("PinP {0} posizione orizzontale", p), type="int", min=10, max=100,
                     default=55, cc=base + 1, group=G[3], fmt=pct_pos),
                dict(key=f"pinp{p}_v", name=tr("PinP {0} posizione verticale", p), type="int", min=10, max=100,
                     default=55, cc=base + 2, group=G[3], fmt=pct_pos),
                dict(key=f"pinp{p}_size", name=tr("PinP {0} dimensione", p), type="int", min=10, max=100,
                     default=50, cc=base + 3, group=G[3], fmt=(0, 1, " %", 0)),
                dict(key=f"pinp{p}_zoom", name=tr("PinP {0} zoom", p), type="int", min=10, max=100, default=10,
                     cc=base + 4, group=G[3], fmt=(0, 10, " %", 0)),
            ]
        out += [
            dict(key="dsk_on", name=tr("DSK in onda"), type="bool", group=G[3], sx=(0x00, 0x0F, 0x00)),
            dict(key="dsk_src", name=tr("DSK sorgente"), type="enum", items=srcs, cc=26, group=G[3]),
            dict(key="dsk_level", name=tr("DSK key level"), type="int", min=0, max=127, cc=27, group=G[3]),
            dict(key="dsk_gain", name=tr("DSK key gain"), type="int", min=0, max=127, cc=28, group=G[3]),
            dict(key="dsk_mix", name=tr("DSK mix level"), type="int", min=0, max=127, default=127, cc=29,
                 group=G[3]),
        ]
        vfx = ["SPLIT V", "SPLIT H", "PART MOSAIC", "BACKGROUND MOSAIC", "FULL MOSAIC", "WAVE", "RGB REPLACE",
               "COLOR PASS", "NEGATIVE", "COLORIZE", "POSTERIZE", "SILHOUETTE", "EMBOSS", "FIND EDGES",
               "MONOCOLOR", "HUE OFFSET", "SATURATION OFFSET", "VALUE OFFSET"]
        for ab, base in (("A", 30), ("B", 32)):
            out += [
                dict(key=f"vfx{ab.lower()}_on", name=tr("SPLIT/VFX {0} attivo", ab), type="bool", cc=base, on=1,
                     group=G[4]),
                dict(key=f"vfx{ab.lower()}_type", name=tr("SPLIT/VFX {0} tipo", ab), type="enum", items=vfx,
                     cc=base + 1, group=G[4]),
            ]
        out += [
            dict(key="out_fade", name=tr("OUTPUT FADE (− = sinistra, + = destra)"), type="int", min=-63, max=63,
                 group=G[5]),
        ]
        return out

    def _dt1(self, addr, value):
        self.sysex(roland_dt1(self.MODEL, addr, [int(value) & 0x7F]))

    def _set_pgm(self, v):
        self._dt1((0x00, 0x12, 0x00), v)

    def _set_pst(self, v):
        self._dt1((0x00, 0x12, 0x01), v)

    def _set_aux(self, v):
        self._dt1((0x00, 0x12, 0x02), v)

    def _swap(self):
        self.state["pgm"], self.state["pst"] = self.state.get("pst", 1), self.state.get("pgm", 0)

    def _set_cut(self, _v):
        self.cc(54, 127)
        self._swap()

    def _set_auto(self, _v):
        self.cc(55, 127)
        self._swap()

    def _set_cut_to(self, v):
        if self.state.get("pgm") == v:
            return
        self._set_pst(v)
        self.state["pst"] = v
        self._set_cut(1)

    def _set_auto_to(self, v):
        if self.state.get("pgm") == v:
            return
        self._set_pst(v)
        self.state["pst"] = v
        self._set_auto(1)

    def _set_memory(self, v):
        self.pc(v)

    def _sw(self, key, v):
        info = next(c for c in self._controls() if c["key"] == key)
        self._dt1(info["sx"], 1 if v else 0)

    def _set_pinp1_on(self, v):
        self._sw("pinp1_on", v)

    def _set_pinp2_on(self, v):
        self._sw("pinp2_on", v)

    def _set_dsk_on(self, v):
        self._sw("dsk_on", v)

    def _set_out_fade(self, v):
        if v < 0:
            self.cc(35, 0)
            self.cc(34, -v)
        else:
            self.cc(34, 0)
            self.cc(35, v)

    # --- audio: sezioni mute / fader
    def _audio_cc(self, kind, n, what):
        if kind == "main":
            return 65 if what == "mute" else 45
        return (56 if what == "mute" else 36) + int(n) - 1

    def set_mute(self, kind, n, muted):
        self.check(kind, n, "mute")
        self.cc(self._audio_cc(kind, n, "mute"), 1 if muted else 0)
        self.state[("mute", kind, int(n))] = bool(muted)

    def get_mute(self, kind, n):
        self.check(kind, n, "mute")
        v = self.state.get(("mute", kind, int(n)))
        if v is None:
            raise MixerError(tr("la regia non rimanda lo stato dei mute"))
        return v

    def set_fader(self, kind, n, db):
        self.check(kind, n, "fader")
        raw = v8_db_to_level(db)
        self.cc(self._audio_cc(kind, n, "fader"), raw)
        self.state[("fader", kind, int(n))] = v8_level_to_db(raw)

    def get_fader(self, kind, n):
        self.check(kind, n, "fader")
        v = self.state.get(("fader", kind, int(n)))
        if v is None:
            raise MixerError(tr("la regia non rimanda i livelli"))
        return v


class RolandV1HD(_RolandMidi):
    ID = "roland_v1hd"
    NAME = tr("Roland V-1HD (regia video, MIDI via USB)")
    FIELDS = ("midi_out", "midi_ch")
    SECTIONS = []
    NO_STATE = ("cut_to", "auto_to", "show_a", "show_b")
    RAMP_STEP = 0.03
    NOTES = tr("Collega la regia al computer con il cavo USB e scegli la sua porta MIDI. Sulla regia: SETUP › "
               "MIDI › Rx SOURCE = USB e Rx CHANNEL uguale al canale scelto qui. Il fader A/B va da 0 (bus A) "
               "a 127 (bus B). «CUT a» / «AUTO a» mettono l'ingresso sul bus fuori onda e spostano il fader "
               "(l'AUTO lo muove CueStage nel «Tempo AUTO»). La regia non rimanda il suo stato: i valori "
               "mostrati sono gli ultimi inviati da CueStage.")

    def _controls(self):
        G = GROUPS
        ins = _inputs(4)
        lev = dict(type="int", min=0, max=127, default=100, group=G[6])
        return [
            dict(key="bus_a", name=tr("Bus A"), type="enum", items=ins, group=G[0]),
            dict(key="bus_b", name=tr("Bus B"), type="enum", items=ins, default=1, group=G[0]),
            dict(key="cut_to", name=tr("CUT a"), type="enum", items=ins, group=G[0]),
            dict(key="auto_to", name=tr("AUTO a"), type="enum", items=ins, group=G[0]),
            dict(key="show_a", name=tr("Taglio sul bus A"), type="action", group=G[0]),
            dict(key="show_b", name=tr("Taglio sul bus B"), type="action", group=G[0]),
            dict(key="fader", name=tr("Fader A/B"), type="int", min=0, max=127, cc=17, group=G[0],
                 fmt=(0, 100 / 127, " % B", 0)),
            dict(key="trans_type", name=tr("Tipo di transizione"), type="enum", items=["WIPE", "MIX", "CUT"],
                 default=1, cc=19, group=G[1]),
            dict(key="auto_time", name=tr("Tempo AUTO"), type="int", min=0, max=40, default=10, group=G[1],
                 fmt=(0, 0.1, " s", 1)),
            dict(key="memory", name=tr("Richiama memoria"), type="enum",
                 items=[tr("Memoria {0}", i) for i in range(1, 9)], group=G[2]),
            dict(key="effects_a", name=tr("EFFECTS A (0 = spento)"), type="int", min=0, max=127, cc=23, group=G[4]),
            dict(key="effects_b", name=tr("EFFECTS B (0 = spento)"), type="int", min=0, max=127, cc=24, group=G[4]),
            dict(key="out_fade", name=tr("OUTPUT FADE (manopola, 64 = centro)"), type="int", min=0, max=127,
                 default=64, cc=25, group=G[5]),
            dict(key="freeze", name=tr("FREEZE"), type="bool", cc=26, group=G[5]),
            dict(key="lvl_1", name="HDMI 1", cc=10, **lev),
            dict(key="lvl_2", name="HDMI 2", cc=11, **lev),
            dict(key="lvl_3", name="HDMI 3", cc=12, **lev),
            dict(key="lvl_4", name="HDMI 4", cc=13, **lev),
            dict(key="lvl_in", name="AUDIO IN", cc=14, **lev),
            dict(key="lvl_mic", name="MIC", cc=15, **lev),
            dict(key="lvl_master", name="MASTER", cc=16, **lev),
        ]

    def _select(self, bank, inp):
        self.cc(0, bank)
        self.cc(32, 0)
        self.pc(inp)

    def _set_bus_a(self, v):
        self._select(0, v)

    def _set_bus_b(self, v):
        self._select(1, v)

    def _set_memory(self, v):
        self._select(0x50, v)

    def _on_air_b(self):
        return int(self.state.get("fader") or 0) >= 64

    def _move_fader(self, target, secs):
        start = int(self.state.get("fader") or 0)
        steps = int(secs / self.RAMP_STEP) if secs > 0 else 0
        for i in range(1, steps):
            self.cc(17, round(start + (target - start) * i / steps))
            time.sleep(self.RAMP_STEP)
        self.cc(17, target)
        self.state["fader"] = target

    def _set_show_a(self, _v):
        self._move_fader(0, 0)

    def _set_show_b(self, _v):
        self._move_fader(127, 0)

    def _take(self, v, secs):
        to_b = not self._on_air_b()
        cur = self.state.get("bus_b" if not to_b else "bus_a")
        if cur == v:
            return                                 # già in onda
        key = "bus_b" if to_b else "bus_a"
        self._select(1 if to_b else 0, v)
        self.state[key] = v
        self._move_fader(127 if to_b else 0, secs)

    def _set_cut_to(self, v):
        self._take(v, 0)

    def _set_auto_to(self, v):
        self._take(v, int(self.state.get("auto_time") or 0) / 10)

    def _set_auto_time(self, v):
        pass                                       # solo CueStage: durata dell'AUTO
