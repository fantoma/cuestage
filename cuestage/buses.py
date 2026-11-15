"""Bus effetti interni dello show (impostazioni «buses»): le cue vi mandano una parte del segnale (mandate
nella scheda Effetti), il bus lo passa nel suo rack (riverbero, delay, gate, EQ, compressore) e lo manda alle
uscite della sua scheda con una matrice come quella delle cue. Il motore audio è in audio_engine.BusRuntime."""
import copy
import uuid

from .i18n import tr
from .model import PRESET_KINDS


def buses(show):
    lst = show.settings.get("buses")
    if not isinstance(lst, list):
        lst = show.settings["buses"] = []
    return lst


def new_bus(show, name=None, fx=None):
    from . import dsp
    n = len(buses(show)) + 1
    b = {"id": uuid.uuid4().hex[:8], "name": name or tr("Bus {0}", n), "device": "",
         "fx": [dsp.new_fx(k) for k in (fx or ())], "matrix": [], "out_levels": [], "level_db": 0.0,
         "mute": False, "presets": {}}
    buses(show).append(b)
    return b


def bus_by_id(show, bid):
    return next((b for b in buses(show) if b.get("id") == bid), None)


class BusView:
    """Un bus visto come una cue: attributi matrix / out_levels (editor della matrice) e preset con nome
    (barra dei preset), sopra al dizionario salvato nelle impostazioni."""
    type = "bus"

    def __init__(self, d):
        self.__dict__["d"] = d

    def __getattr__(self, k):
        d = self.__dict__["d"]
        if k in ("matrix", "out_levels", "fx"):
            v = d.get(k)
            if not isinstance(v, list):
                v = d[k] = []
            return v
        if k == "level_db":
            return float(d.get("level_db", 0.0) or 0.0)
        if k == "mute":
            return bool(d.get("mute"))
        if k in ("id", "name", "device"):
            return d.get(k, "")
        raise AttributeError(k)

    def __setattr__(self, k, v):
        self.__dict__["d"][k] = v

    def label(self):
        return self.name or tr("Bus")

    # ---- preset (come Cue)
    def preset_dict(self, kind):
        p = self.d.get("presets")
        if not isinstance(p, dict):
            p = self.d["presets"] = {}
        return p.setdefault(kind, {})

    def preset_values(self, kind):
        return {k: copy.deepcopy(getattr(self, k)) for k in PRESET_KINDS[kind]}

    def apply_preset(self, kind, name):
        p = self.preset_dict(kind).get(name)
        if p is None:
            return False
        for k, v in p.items():
            if k in PRESET_KINDS[kind]:
                self.d[k] = copy.deepcopy(v)
        return True
