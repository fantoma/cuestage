"""Schermo di uscita riconosciuto per identità, non per posizione nell'elenco.

L'ordine degli schermi cambia (proiettore acceso dopo il computer, cavo in un'altra porta, altro computer): per
questo lo show salva anche l'identità dello schermo scelto — produttore, modello e numero di serie letti dall'EDID,
nome della porta (HDMI-1, DP-2…) e geometria — e all'apertura lo cerca tra quelli collegati.
Funziona con oggetti tipo QScreen (name, manufacturer, model, serialNumber, geometry): le prove ne usano di finti.
"""
from .i18n import tr


def _call(scr, attr):
    try:
        v = getattr(scr, attr)()
        return str(v or "").strip()
    except Exception:
        return ""


def identity(scr):
    """Identità di uno schermo da salvare nello show."""
    g = scr.geometry()
    return {"name": _call(scr, "name"), "manufacturer": _call(scr, "manufacturer"), "model": _call(scr, "model"),
            "serial": _call(scr, "serialNumber"), "w": g.width(), "h": g.height(), "x": g.x(), "y": g.y()}


def describe(ident):
    """Testo leggibile: «Epson EB-X41 · HDMI-1 · 1920×1080»."""
    if not ident:
        return ""
    who = " ".join(x for x in (ident.get("manufacturer"), ident.get("model")) if x)
    parts = [who] if who else []
    if ident.get("name") and ident.get("name") != who:
        parts.append(ident["name"])
    if ident.get("w"):
        parts.append(f"{ident['w']}×{ident['h']}")
    if ident.get("serial"):
        parts.append(tr("n/s {0}", ident["serial"]))
    return " · ".join(parts)


def score(ident, scr):
    """Quanto lo schermo somiglia all'identità salvata (0 = per niente). Il numero di serie vale più di tutto,
    poi produttore + modello, poi il nome della porta; misura e posizione decidono solo tra pari."""
    cur = identity(scr)
    s = 0
    ser = ident.get("serial")
    if ser and cur["serial"]:
        if ser != cur["serial"]:
            return 0                                    # numero di serie diverso: è un altro apparecchio
        s += 100
    if ident.get("manufacturer") and ident.get("model"):
        if (ident["manufacturer"], ident["model"]) == (cur["manufacturer"], cur["model"]):
            s += 40
        elif cur["manufacturer"] or cur["model"]:
            return 0                                    # un altro modello noto
    if ident.get("name") and ident["name"] == cur["name"]:
        s += 20
    if s == 0:
        return 0                                        # la sola misura non basta a dire che è lo stesso
    if (ident.get("w"), ident.get("h")) == (cur["w"], cur["h"]):
        s += 5
    if (ident.get("x"), ident.get("y")) == (cur["x"], cur["y"]):
        s += 2
    return s


def find(ident, screens):
    """Indice dello schermo che corrisponde all'identità salvata, -1 se non è collegato."""
    best, best_s = -1, 0
    for i, scr in enumerate(screens):
        sc = score(ident, scr)
        if sc > best_s:
            best, best_s = i, sc
    return best


def resolve(settings, screens):
    """Schermo di uscita da usare: (indice o None = automatico, stato).
    stato: «auto» (scelta automatica), «ok» (trovato per identità), «index» (show senza identità: per posizione),
    «missing» (lo schermo scelto non è collegato: si usa quello automatico)."""
    idx = settings.get("output_screen", -1)
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = -1
    if idx < 0:
        return None, "auto"
    ident = settings.get("output_screen_id")
    if isinstance(ident, dict) and any(ident.get(k) for k in ("name", "manufacturer", "model", "serial")):
        i = find(ident, screens)
        return (i, "ok") if i >= 0 else (None, "missing")
    return (idx, "index") if idx < len(screens) else (None, "missing")


def auto_index(screens):
    """Scelta automatica: l'ultimo schermo (di solito il proiettore)."""
    return len(screens) - 1 if screens else -1
