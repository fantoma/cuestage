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


INPUT_DEFAULTS = {"on": False, "device": "", "ch": 1, "ch2": 0, "trim": 0.0}


def bus_input(b):
    """Ingresso fisico del bus (microfono, linea) con i valori mancanti presi dai predefiniti."""
    d = dict(INPUT_DEFAULTS)
    d.update(b.get("input") or {})
    return d


def new_bus(show, name=None, fx=None):
    from . import dsp
    n = len(buses(show)) + 1
    b = {"id": uuid.uuid4().hex[:8], "name": name or tr("Bus {0}", n), "device": "",
         "fx": [dsp.new_fx(k) for k in (fx or ())], "matrix": [], "out_levels": [], "level_db": 0.0,
         "mute": False, "presets": {}, "trims": {}, "input": dict(INPUT_DEFAULTS)}
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
        if k == "trims":
            v = d.get("trims")
            if not isinstance(v, dict):
                v = d["trims"] = {}
            return v
        if k == "input":
            return bus_input(d)
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


def sources(show, bid):
    """Cue che mandano al bus: [(cue, mandata)] nell'ordine delle liste."""
    out = []
    for c in show.all_cues():
        for snd in getattr(c, "sends", None) or []:
            if isinstance(snd, dict) and snd.get("bus") == bid:
                out.append((c, snd))
    return out


def trim_db(show, bid, cue_id):
    b = bus_by_id(show, bid)
    try:
        return float(((b or {}).get("trims") or {}).get(cue_id, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- valori dal vivo (cue Controllo bus, preset)

def lerp_spec(a, b, f):
    """Valori a metà strada tra due stati di un bus: numeri interpolati dove la struttura è uguale (stessi
    effetti nello stesso ordine), il resto preso subito da b."""
    if isinstance(a, bool) or isinstance(b, bool):
        return b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * f
    if isinstance(a, dict) and isinstance(b, dict):
        if a.get("id") is not None and a.get("id") != b.get("id"):
            return copy.deepcopy(b)
        return {k: (lerp_spec(a[k], v, f) if k in a else copy.deepcopy(v)) for k, v in b.items()}
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [lerp_spec(x, y, f) for x, y in zip(a, b)]
    return copy.deepcopy(b)


# parametri che una cue «Controllo bus» può cambiare in un effetto (oltre ad «Acceso»)
def control_params(kind):
    from .ui_fx import PARAMS
    if kind == "eq":
        rows = [("gain", tr("Guadagno d'uscita"), -18, 18, 0.5, 1, " dB", False)]
        rows += [(f"band{i + 1}_g", tr("Banda {0}: guadagno", i + 1), -18, 18, 0.5, 1, " dB", False) for i in range(5)]
        rows += [("hpf_f", tr("Passa-alto"), 20, 20000, 1, 0, " Hz", True),
                 ("lpf_f", tr("Passa-basso"), 20, 20000, 1, 0, " Hz", True)]
        return rows
    return list(PARAMS.get(kind, []))


def apply_control(spec, changes):
    """Stato del bus dopo i cambiamenti di una cue «Controllo bus» (spec non viene modificato).
    changes: {level_db, mute, input_on, input_trim, fx: {id effetto: {on, parametro: valore}}}."""
    out = copy.deepcopy(spec)
    ch = changes or {}
    if "level_db" in ch:
        out["level_db"] = float(ch["level_db"])
    if "mute" in ch:
        out["mute"] = bool(ch["mute"])
    if "input_on" in ch or "input_trim" in ch:
        inp = bus_input(out)
        if "input_on" in ch:
            inp["on"] = bool(ch["input_on"])
        if "input_trim" in ch:
            inp["trim"] = float(ch["input_trim"])
        out["input"] = inp
    for fx in out.get("fx") or []:
        c = (ch.get("fx") or {}).get(fx.get("id"))
        if not c:
            continue
        for k, v in c.items():
            if k.startswith("band") and k.endswith("_g"):
                try:
                    i = int(k[4:-2]) - 1
                except ValueError:
                    continue
                from .dsp import params
                bands = params(fx)["bands"]
                if 0 <= i < len(bands):
                    bands[i]["g"] = float(v)
                    fx["bands"] = bands
            else:
                fx[k] = v
    return out
