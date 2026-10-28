"""Lingua dell'interfaccia.

I testi sono scritti in italiano nel codice, dentro tr("…"). I file di lingua (cartella «lang» del programma e
quella dei dati utente) traducono ogni testo italiano: {"_meta": {"name": "English"}, "strings": {"italiano":
"traduzione"}}. I testi con parti variabili usano {0}, {1}… (con l'eventuale formato, es. {0:.1f}), da
lasciare nella traduzione; l'ordine può cambiare. Un testo senza traduzione resta in italiano.

La lingua si sceglie in Impostazioni › Lingua e vale dal riavvio successivo (è salvata nelle preferenze del
computer, non nello show).
"""
import json
import os

SOURCE = "it"                 # lingua in cui è scritto il programma
_cat = {}                     # testo italiano -> traduzione
_lang = SOURCE


def lang_dirs():
    from .system import data_dir
    return [os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang"), data_dir("lang")]


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def available():
    """{codice: nome} delle lingue disponibili (l'italiano c'è sempre)."""
    out = {SOURCE: "Italiano"}
    for d in lang_dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            if fn.endswith(".json"):
                data = _read(os.path.join(d, fn))
                if data is not None:
                    code = fn[:-5]
                    out[code] = str((data.get("_meta") or {}).get("name") or code)
    return out


def load(code):
    """Testi tradotti di una lingua (i file dei dati utente completano e correggono quelli del programma)."""
    cat = {}
    for d in lang_dirs():
        data = _read(os.path.join(d, f"{code}.json"))
        if data:
            cat.update({k: v for k, v in (data.get("strings") or {}).items() if isinstance(v, str) and v})
    return cat


def set_language(code):
    global _cat, _lang
    code = (code or SOURCE).strip()
    _cat = load(code)
    _lang = code if (code == SOURCE or _cat) else SOURCE
    return _lang


def language():
    return _lang


def tr(text, *args):
    """Traduzione di un testo dell'interfaccia; con args, i segnaposto {0}, {1}… sono sostituiti."""
    s = _cat.get(text, text)
    if not args:
        return s
    try:
        return s.format(*args)
    except (IndexError, KeyError, ValueError):
        return text.format(*args)     # traduzione con segnaposto sbagliati: si usa l'italiano


def system_language():
    """Lingua del sistema se disponibile, altrimenti inglese (o italiano se manca anche l'inglese)."""
    try:
        from PySide6.QtCore import QLocale
        code = QLocale.system().name().split("_")[0].lower()
    except Exception:
        code = ""
    langs = available()
    if code in langs:
        return code
    return "en" if "en" in langs else SOURCE


def saved_language():
    """Lingua scelta dall'utente (preferenze del computer), altrimenti quella del sistema."""
    try:
        from PySide6.QtCore import QSettings
        v = QSettings("CueStage", "CueStage").value("language", "")
    except Exception:
        v = ""
    v = str(v or "")
    return v if v in available() else system_language()


def save_language(code):
    from PySide6.QtCore import QSettings
    QSettings("CueStage", "CueStage").setValue("language", code)
