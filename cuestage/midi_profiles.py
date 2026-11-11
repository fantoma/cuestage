"""Profili MIDI: apparecchi comandati via MIDI (es. Akai MPC) con i loro comandi già pronti per le cue MIDI out.

Un profilo è un file JSON in cuestage/midi_profiles/ o in ~/.local/share/cuestage/midi_profiles/:
  id, name, notes, commands: [{id, label, lines: ["mmc {dev} play", …], params: [{id, label,
  type int|float|text|note, min, max, default}], help}]
Le righe usano la sintassi delle cue MIDI out (note, cc, mmc, start, wait…) con i parametri tra graffe:
il comando scelto nell'ispettore diventa testo nella cue, che resta modificabile a mano.
"""
import json
import os

from .i18n import tr
from .system import data_dir

BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "midi_profiles")
USER_DIR = data_dir("midi_profiles")

_cache = None


def profiles(reload=False):
    """{id: profilo}"""
    global _cache
    if _cache is not None and not reload:
        return _cache
    out = {}
    for d in (BUILTIN_DIR, USER_DIR):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(d, fn), encoding="utf-8") as f:
                        p = json.load(f)
                    if p.get("id") and p.get("commands"):
                        out[p["id"]] = p
                except (OSError, ValueError) as e:
                    print(tr("Profilo MIDI {0} non valido: {1}", fn, e))
    _cache = out
    return out


def command(profile_id, cmd_id):
    p = profiles().get(profile_id)
    for c in (p or {}).get("commands") or []:
        if c["id"] == cmd_id:
            return p, c
    return p, None


def _fmt(v, param):
    t = param.get("type")
    if t == "int":
        return str(int(float(v)))
    if t == "float":
        return f"{float(v):g}"
    return str(v).strip()


def render(profile_id, cmd_id, values=None):
    """Righe di testo MIDI out del comando con i valori dei parametri (predefiniti dove mancano)."""
    _p, c = command(profile_id, cmd_id)
    if c is None:
        raise KeyError(tr("comando «{0}» non presente nel profilo MIDI «{1}»", cmd_id, profile_id))
    vals = {}
    for prm in c.get("params") or []:
        v = (values or {}).get(prm["id"], prm.get("default", ""))
        vals[prm["id"]] = _fmt(v, prm)
    head = "# " + c.get("label", cmd_id)
    return "\n".join([head] + [ln.format(**vals) for ln in c.get("lines") or []])
