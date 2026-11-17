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


def describe_control(spec, changes):
    """(righe «cosa cambia», voci che non si possono applicare) di una cue Controllo bus sul bus «spec»: per la
    Diagnostica, così si vede se la cue è partita, cosa ha cambiato e se punta a effetti che il bus non ha più
    (tolti o ricreati dopo aver preparato la cue, o sostituiti da un preset del bus)."""
    from . import dsp
    rows, missing = [], []
    ch = changes or {}

    def num(v):
        return f"{float(v):g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
    if "level_db" in ch:
        rows.append(tr("volume {0} → {1} dB", num(spec.get("level_db", 0.0)), num(ch["level_db"])))
    if "mute" in ch:
        rows.append(tr("mute → {0}", tr("sì") if ch["mute"] else tr("no")))
    if "input_on" in ch:
        rows.append(tr("ingresso fisico → {0}", tr("acceso") if ch["input_on"] else tr("spento")))
    if "input_trim" in ch:
        rows.append(tr("trim ingresso {0} → {1} dB", num(bus_input(spec)["trim"]), num(ch["input_trim"])))
    by = {f.get("id"): f for f in spec.get("fx") or []}
    for fid, vals in (ch.get("fx") or {}).items():
        fx = by.get(fid)
        if fx is None:
            missing.append(fid)
            continue
        p = dsp.params(fx)
        name = dsp.FX_LABELS.get(fx.get("type"), fx.get("type"))
        for k, v in (vals or {}).items():
            if k.startswith("band") and k.endswith("_g"):
                try:
                    old = p["bands"][int(k[4:-2]) - 1]["g"]
                except (ValueError, IndexError):
                    old = "?"
            else:
                old = p.get(k, "?")
            rows.append(f"{name}: {k} {num(old)} → {num(v)}")
    return rows, missing


def mirror_edit(before, after, over):
    """Modifica fatta al bus salvato (finestra dei bus, da «before» ad «after») riportata anche sullo stato dal
    vivo «over» (impostato da una cue Controllo bus o Cambia preset): così le modifiche valgono subito anche
    mentre il bus usa valori dal vivo, e le voci non toccate restano quelle della cue."""
    for k, v in after.items():
        if k in ("presets", "fx") or before.get(k) == v:
            continue
        over[k] = copy.deepcopy(v)
    fb, fa, fo = before.get("fx") or [], after.get("fx") or [], over.get("fx") or []
    ids = [f.get("id") for f in fa]
    if [f.get("id") for f in fb] != ids or [f.get("id") for f in fo] != ids:
        if fb != fa:                          # effetti aggiunti, tolti o spostati: il rack è quello salvato
            over["fx"] = copy.deepcopy(fa)
        return
    for b_fx, a_fx, o_fx in zip(fb, fa, fo):
        for k in set(b_fx) | set(a_fx):
            if b_fx.get(k) != a_fx.get(k):
                if k in a_fx:
                    o_fx[k] = copy.deepcopy(a_fx[k])
                else:
                    o_fx.pop(k, None)


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


def control_paths(changes):
    """Voci dello stato del bus comandate da una cue Controllo bus (percorsi come in engine._spec_diff)."""
    ch = changes or {}
    out = [(k,) for k in ("level_db", "mute") if k in ch]
    if "input_on" in ch:
        out.append(("input", "on"))
    if "input_trim" in ch:
        out.append(("input", "trim"))
    for fid, vals in (ch.get("fx") or {}).items():
        for k in vals or {}:
            if k.startswith("band") and k.endswith("_g"):
                try:
                    out.append(("fx", ("#", fid), "bands", int(k[4:-2]) - 1, "g"))
                except ValueError:
                    pass
            else:
                out.append(("fx", ("#", fid), k))
    return out


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
