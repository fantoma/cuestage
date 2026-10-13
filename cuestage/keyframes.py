"""Keyframe di preset sulla timeline di una cue.

Ogni keyframe è {"t": secondi dall'avvio della cue, "kind": "visual" | "audio", "preset": nome}.
Il nome BASE indica i valori propri della cue. Tra due keyframe consecutivi della stessa corsia i valori
passano in modo lineare dal primo al secondo (le posizioni sono inizio e fine della dissolvenza);
prima del primo e dopo l'ultimo restano fermi.
"""

BASE = "__base__"
BASE_LABEL = "Valori base della cue"
KINDS = ("visual", "audio")
KIND_LABELS = {"visual": "Immagine", "audio": "Audio"}
VIS_NUM = ("crop_l", "crop_t", "crop_r", "crop_b", "off_x", "off_y", "scale", "opacity")
MIN_DB = -60.0


def lanes_for(cue):
    return {"video": ["visual", "audio"], "image": ["visual"], "audio": ["audio"]}.get(cue.type, [])


def migrate_points(points):
    """Formato precedente {t, visual, audio, fade} → keyframe. Una dissolvenza di durata d che
    partiva a t diventa: keyframe «valore precedente» a t e keyframe «preset» a t + d."""
    if not points or all(isinstance(p, dict) and "kind" in p for p in points):
        return points or []
    out = []
    last = {"visual": BASE, "audio": BASE}
    for p in sorted((p for p in points if isinstance(p, dict)), key=lambda p: float(p.get("t", 0) or 0)):
        t = max(0.0, float(p.get("t", 0) or 0))
        fade = max(0.0, float(p.get("fade", 0) or 0))
        for kind in KINDS:
            name = p.get(kind) or ""
            if not name:
                continue
            if fade > 0:
                out.append({"t": t, "kind": kind, "preset": last[kind]})
                out.append({"t": round(t + fade, 3), "kind": kind, "preset": name})
            else:
                if t > 0.01:
                    out.append({"t": round(t - 0.01, 3), "kind": kind, "preset": last[kind]})
                out.append({"t": t, "kind": kind, "preset": name})
            last[kind] = name
    return out


def track(cue, kind):
    """Keyframe validi di una corsia, ordinati: [(t, nome)]."""
    names = cue.preset_dict(kind)
    out = []
    for p in (getattr(cue, "preset_points", None) or []):
        try:
            if p.get("kind") != kind:
                continue
            n = p.get("preset") or ""
            if n == BASE or n in names:
                out.append((max(0.0, float(p.get("t", 0))), n))
        except (AttributeError, TypeError, ValueError):
            pass
    out.sort(key=lambda x: x[0])
    return out


def _segment(tr, T):
    """(nome_a, nome_b, frazione) per il tempo T sulla corsia."""
    if T <= tr[0][0]:
        return tr[0][1], tr[0][1], 0.0
    if T >= tr[-1][0]:
        return tr[-1][1], tr[-1][1], 0.0
    for (ta, na), (tb, nb) in zip(tr, tr[1:]):
        if ta <= T <= tb:
            f = 0.0 if tb <= ta else (T - ta) / (tb - ta)
            return na, nb, f
    return tr[-1][1], tr[-1][1], 0.0


def names_at(cue, kind, T):
    """(preset_a, preset_b, frazione) in uso al tempo T sulla corsia, None se la corsia è vuota."""
    tr = track(cue, kind)
    return _segment(tr, T) if tr else None


def describe(state):
    """Testo del preset in uso: «nome» oppure dissolvenza «a» → «b» (xx%)."""
    if state is None:
        return "nessun keyframe (valori della cue)"
    na, nb, f = state
    if na == nb or f <= 0:
        return f"«{label(na)}»"
    return f"dissolvenza «{label(na)}» → «{label(nb)}» ({f * 100:.0f}%)"


def _lerp(a, b, f):
    return a + (b - a) * f


# ------------------------------------------------------------------ immagine

def _warp4(w):
    w = [[float(x) for x in p] for p in (w or [])]
    return (w + [[0.0, 0.0]] * 4)[:4]


def visual_values(cue, name):
    """Valori visivi di un keyframe (preset o base), completi."""
    v = {k: float(getattr(cue, k)) for k in VIS_NUM}
    v["warp"] = _warp4(cue.warp)
    v["fit"] = cue.fit
    if name != BASE:
        p = cue.preset_dict("visual").get(name) or {}
        for k in VIS_NUM:
            if k in p:
                v[k] = float(p[k])
        if "warp" in p:
            v["warp"] = _warp4(p["warp"])
        if "fit" in p:
            v["fit"] = p["fit"]
    return v


def interp_visual(a, b, f):
    out = {k: _lerp(a[k], b[k], f) for k in VIS_NUM}
    out["warp"] = [[_lerp(p[0], q[0], f), _lerp(p[1], q[1], f)] for p, q in zip(a["warp"], b["warp"])]
    out["fit"] = a["fit"] if f < 1.0 else b["fit"]
    return out


def visual_state(cue, T):
    """(valori, nome_a, nome_b, frazione) al tempo T, oppure None se la corsia è vuota."""
    tr = track(cue, "visual")
    if not tr:
        return None
    na, nb, f = _segment(tr, T)
    return interp_visual(visual_values(cue, na), visual_values(cue, nb), f), na, nb, f


# ------------------------------------------------------------------ audio

def audio_values(cue, name, n_src, n_out):
    """(livello dB, matrice dB n_src×n_out con None = spento, volumi uscite dB) di un keyframe."""
    from .audio_engine import effective_levels, effective_matrix
    level, matrix, levels = float(cue.level_db), cue.matrix, cue.out_levels
    if name != BASE:
        p = cue.preset_dict("audio").get(name) or {}
        level = float(p.get("level_db", level))
        matrix = p.get("matrix", matrix)
        levels = p.get("out_levels", levels)
    return level, effective_matrix(matrix, n_src, n_out), effective_levels(levels, n_out)


def _gain(db):
    return 0.0 if db is None or db <= MIN_DB else 10.0 ** (float(db) / 20.0)


def _power_lerp(x, y, f, off_is_none=True):
    """Interpolazione a potenza costante tra due valori in dB (None = spento): spostando un segnale
    da un'uscita all'altra il volume percepito resta costante (a metà strada entrambe a -3 dB)."""
    import math
    ga, gb = _gain(x), _gain(y)
    g = math.sqrt((1.0 - f) * ga * ga + f * gb * gb)
    if g <= 10 ** (MIN_DB / 20.0):
        return None if off_is_none else MIN_DB
    return 20.0 * math.log10(g)


def interp_audio(a, b, f):
    """Livello della cue interpolato in dB; incroci della matrice e volumi delle uscite a potenza
    costante (un incrocio spento si accende o si spegne in dissolvenza, senza buchi a metà)."""
    la, ma, va = a
    lb, mb, vb = b
    m = [[None if (x is None and y is None) else _power_lerp(x, y, f) for x, y in zip(ra, rb)]
         for ra, rb in zip(ma, mb)]
    lev = [_power_lerp(x, y, f, off_is_none=False) for x, y in zip(va, vb)]
    return _lerp(la, lb, f), m, lev


def audio_state(cue, T, n_src, n_out):
    """(livello, matrice, volumi uscite, nome_a, nome_b, frazione) al tempo T, None se corsia vuota."""
    tr = track(cue, "audio")
    if not tr:
        return None
    na, nb, f = _segment(tr, T)
    st = interp_audio(audio_values(cue, na, n_src, n_out), audio_values(cue, nb, n_src, n_out), f)
    return st + (na, nb, f)


def is_base(na, nb, f):
    return na == BASE and (nb == BASE or f <= 0.0)


def label(name):
    return "base" if name == BASE else name


def new_keyframe(cue, kind, t, preset):
    """Aggiunge un keyframe; se è il primo della corsia e non è a 0, ne aggiunge uno «base» a 0
    (la cue parte dai suoi valori e sfuma verso il preset)."""
    pts = cue.preset_points
    first = not any(p.get("kind") == kind for p in pts)
    if first and t > 0.05 and preset != BASE:
        pts.append({"t": 0.0, "kind": kind, "preset": BASE})
    kf = {"t": round(float(t), 2), "kind": kind, "preset": preset}
    pts.append(kf)
    pts.sort(key=lambda p: float(p.get("t", 0)))
    return kf

