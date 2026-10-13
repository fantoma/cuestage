"""Plugin CueStage — schede audio con controlli ALSA: Focusrite Scarlett / Clarett (driver «scarlett2»)
e qualsiasi altra scheda che espone controlli nel mixer ALSA.

Su Linux il pannello «Focusrite Control» (solo Windows/macOS) non serve: dal kernel 6.7 il driver
«scarlett2» espone tutte le funzioni della scheda come controlli ALSA (le stesse che usa alsa-scarlett-gui):
  - ingressi: livello Line/Inst, Pad, Air, phantom 48V (e guadagno/autogain sulle Gen 4)
  - uscite: volume e mute per uscita («Line 01 (Monitor L) Playback Volume», «… Mute Playback Switch»),
    volume master/HW, controllo software/hardware delle uscite, speaker switching, talkback, dim
  - direct monitor, matrice di mix interna («Mix A Input 01 Playback Volume»), routing («… Enum»)
I nomi esatti dipendono dal modello e dalla versione del kernel: il plugin li legge dalla scheda.
Per Scarlett Gen 2/3 con kernel tra 5.14 e 6.6 serve l'opzione del modulo:
    echo "options snd_usb_audio device_setup=1" | sudo tee /etc/modprobe.d/scarlett.conf   (poi riavvio)
Richiede «amixer» (pacchetto alsa-utils).
"""
import re

from cuestage.mixers import MixerDriver, MixerError
from cuestage.alsactl import (AlsaError, alsa_cards, cached_controls, cset, group_of, read_controls,
                              resolve_card)

RE_OUT_VOL = re.compile(r"^Line (?:Out )?(\d+)\b(?!.*Volume Control).*Playback Volume$")
RE_OUT_MUTE = re.compile(r"^Line (?:Out )?(\d+)\b.*Mute Playback Switch$")
RE_MASTER = re.compile(r"^Master\b.*Playback Volume$")
FOCUSRITE_WORDS = ("focusrite", "scarlett", "clarett", "vocaster")


def _is_focusrite(*names):
    t = " ".join(n or "" for n in names).lower()
    return any(w in t for w in FOCUSRITE_WORDS)


class SchedaAudioAlsa(MixerDriver):
    ID = "alsa_card"
    PLATFORMS = ("linux",)
    NAME = "Scheda audio (controlli ALSA, qualsiasi modello)"
    DEFAULT_PORT = 0
    FIELDS = ("card",)
    CONTROLS = True
    NOTES = ("Controlli della scheda audio dal mixer ALSA (amixer): interruttori, menu e volumi compaiono nel "
             "pannello mixer (F10) e si possono impostare con una cue Mixer › «Imposta un controllo».")

    def __init__(self, conf):
        super().__init__(conf)
        self.card = None
        self.ctls = {}

    # ------------------------------------------------------------ scheda e controlli
    def _card(self):
        try:
            self.card = resolve_card(self.conf.get("card"), self.conf.get("card_name") or "")
        except AlsaError as e:
            raise MixerError(str(e))
        return self.card

    def _load(self, max_age=0.0):
        card = self._card()
        try:
            ctls = read_controls(card, max_age=max_age)
        except AlsaError as e:
            raise MixerError(str(e))
        self.ctls = {c.key: c for c in ctls}
        return ctls

    def _ctl(self, key):
        c = self.ctls.get(key)
        if c is None:
            self._load()
            c = self.ctls.get(key)
        if c is None:
            raise MixerError(f"controllo «{key}» non presente sulla scheda")
        return c

    def list_controls(self):
        out = []
        for c in self._load():
            d = c.as_dict()
            d["group"] = group_of(c.name)
            out.append(d)
        return out

    def _write(self, c, value):
        try:
            cset(self.card or self._card(), c, int(value))
        except AlsaError as e:
            raise MixerError(str(e))

    def set_control(self, key, value):
        c = self._ctl(key)
        if not c.writable:
            raise MixerError(f"«{key}» è di sola lettura")
        self._write(c, value)
        return c.text(int(value))

    def status(self):
        ctls = self._load()
        name = next((f"{s} — {l}" for _i, cid, s, l in alsa_cards() if cid == self.card), self.card)
        return {"scheda": name, "controlli": len(ctls)}

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        out = []
        for idx, cid, short, long in alsa_cards():
            if _is_focusrite(short, long):         # le Focusrite le trova il loro driver
                continue
            out.append({"driver": cls.ID, "card": cid, "card_name": short, "name": short,
                        "model": "", "info": f"scheda ALSA {idx} · {long[:60]}"})
        return out


class FocusriteScarlett(SchedaAudioAlsa):
    ID = "focusrite_scarlett"
    NAME = "Focusrite Scarlett / Clarett (driver scarlett2)"
    NAMES = True
    NOTES = ("Funzioni della scheda dal driver «scarlett2» del kernel (come Focusrite Control): ingressi (Inst, Pad, "
             "Air, 48V), uscite con volume e mute (sezione «Uscite», anche nelle cue mute/fader), master, direct "
             "monitor, matrice di mix e routing nel pannello mixer (F10). Kernel 6.7 o successivo; per Gen 2/3 con "
             "kernel precedenti: opzione «device_setup=1» del modulo snd_usb_audio. Serve alsa-utils.")

    @classmethod
    def _outputs(cls, conf):
        """{n: (controllo volume, controllo mute)} dalle informazioni già lette della scheda."""
        card = (conf or {}).get("card")
        ctls = cached_controls(card) if card else None
        if ctls is None and card:
            try:
                ctls = read_controls(resolve_card(card, (conf or {}).get("card_name") or ""), max_age=30)
            except AlsaError:
                ctls = []
        outs, master = {}, None
        for c in ctls or []:
            m = RE_OUT_VOL.match(c.name)
            if m and c.type == "int":
                outs.setdefault(int(m.group(1)), [None, None])[0] = c
                continue
            m = RE_OUT_MUTE.match(c.name)
            if m and c.type == "bool":
                outs.setdefault(int(m.group(1)), [None, None])[1] = c
                continue
            if RE_MASTER.match(c.name) and c.type == "int" and master is None:
                master = c
        return outs, master

    @classmethod
    def sections(cls, conf=None):
        outs, master = cls._outputs(conf)
        secs = []
        if outs:          # anche se ora sono in sola lettura (manopola HW): si passano a SW dal pannello
            caps = set()
            if any(v[0] is not None for v in outs.values()):
                caps.add("fader")
            if any(v[1] is not None for v in outs.values()):
                caps.add("mute")
            secs.append(("out", max(outs), caps))
        if master is not None and master.writable:
            secs.append(("main", 1, {"fader"}))
        return secs

    SECTIONS = [("out", 10, {"mute", "fader"}), ("main", 1, {"fader"})]

    def _out(self, kind, n, which):
        if not self.ctls:
            self._load(max_age=2.0)
        outs, master = self._outputs({"card": self.card or self._card()})
        if kind == "main":
            c = master
        else:
            c = (outs.get(int(n)) or [None, None])[0 if which == "vol" else 1]
        if c is None:
            raise MixerError(f"{'volume' if which == 'vol' else 'mute'} dell'uscita {n} non disponibile")
        c = self._ctl(c.key)
        if not c.writable:
            self._load()                       # forse è appena passata a SW
            c = self._ctl(c.key)
        if not c.writable:
            raise MixerError(f"uscita {n}: volume e mute sono sulla manopola della scheda (HW). Nel pannello "
                             "mixer (F10): «Volume e mute delle uscite da CueStage (SW)», oppure «Line Out "
                             f"{int(n):02d} Volume Control» su SW")
        return c

    def set_mute(self, kind, n, muted):
        self.check(kind, n, "mute")
        self._write(self._out(kind, n, "mute"), 1 if muted else 0)

    def get_mute(self, kind, n):
        self.check(kind, n, "mute")
        self._load()
        return bool(self._out(kind, n, "mute").value())

    def set_fader(self, kind, n, db):
        self.check(kind, n, "fader")
        c = self._out(kind, n, "vol")
        raw = c.from_db(max(-90.0, db)) if c.db else int(round(c.min + (c.max - c.min) * max(0.0, min(1.0, (db + 90) / 90))))
        if db <= -89.9:
            raw = c.min
        self._write(c, raw)

    def get_fader(self, kind, n):
        self.check(kind, n, "fader")
        self._load()
        c = self._out(kind, n, "vol")
        raw = c.value()
        if raw is None:
            raise MixerError("valore non letto")
        if c.db:
            return max(-90.0, c.to_db(raw))
        return -90.0 + 90.0 * (raw - c.min) / max(1, c.max - c.min)

    def get_name(self, kind, n):
        if kind != "out":
            return ""
        outs, _m = self._outputs({"card": self.card or self._card()})
        c = (outs.get(int(n)) or [None, None])[0] or (outs.get(int(n)) or [None, None])[1]
        m = re.search(r"\(([^)]+)\)", c.name) if c else None
        return m.group(1).replace("Headphones", "Cuffie") if m else ""

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        out = []
        for idx, cid, short, long in alsa_cards():
            if _is_focusrite(short, long):
                out.append({"driver": cls.ID, "card": cid, "card_name": short, "name": short,
                            "model": "", "info": f"scheda ALSA {idx} · {long[:60]}"})
        return out
