"""Nome del programma e identificativi in un solo posto.

- APP_NAME: il nome che si vede (titoli, menu, finestre, file di installazione).
- APP_SLUG: il nome in minuscolo per cartelle, avviatore e voce di menu.
- ENV_PREFIX: prefisso delle variabili d'ambiente (es. ENV_PREFIX_LANG).
- PROTOCOL_ID: identificativo nei dati scambiati (file dello show, bundle, cluster): non cambia mai, così
  versioni diverse continuano a riconoscersi.
La cartella del codice (il pacchetto Python) resta «cuestage».
"""
import os
import sys

APP_NAME = "Cuevivo"
APP_SLUG = "cuevivo"
ENV_PREFIX = "CUEVIVO"
PROTOCOL_ID = "CueStage"

# nomi precedenti del programma: preferenze, cartella dei dati e variabili d'ambiente si riprendono da lì
LEGACY = (("CueStage", "cuestage", "CUESTAGE"),)
# spazio dei nomi OSC dei programmi collegati (OSC_CLIENTS.md): quello storico vale sempre
OSC_NS = "/cuestage"
OSC_NAMESPACES = tuple(dict.fromkeys((OSC_NS, "/" + APP_SLUG)))
# intestazione del PIN dell'interfaccia web (si accettano anche quelle dei nomi precedenti)
PIN_HEADER = f"X-{APP_NAME}-Pin"
PIN_HEADERS = tuple(dict.fromkeys([PIN_HEADER] + [f"X-{n}-Pin" for n, _s, _e in LEGACY]))
MIGRATED_MARK = ".migrated-from"


def _legacy():
    """Nomi precedenti diversi da quello attuale."""
    return [x for x in LEGACY if x[0] != APP_NAME]


def env(key, default=None):
    """Variabile d'ambiente del programma: env("LANG") legge <ENV_PREFIX>_LANG (o quella del nome precedente)."""
    for pre in dict.fromkeys([ENV_PREFIX] + [e for _n, _s, e in LEGACY]):
        v = os.environ.get(f"{pre}_{key}")
        if v is not None:
            return v
    return default


def has_env(key):
    return env(key) is not None


def settings():
    """Preferenze del programma su questo computer (QSettings)."""
    from PySide6.QtCore import QSettings
    return QSettings(APP_NAME, APP_NAME)


def data_base(name=None, slug=None):
    """Cartella dei dati utente (autosalvataggi, plugin, profili, cache).

    Linux:   ~/.local/share/<slug>
    Windows: %APPDATA%\\<Nome>
    macOS:   ~/Library/Application Support/<Nome>
    """
    name = name or APP_NAME
    slug = slug or APP_SLUG
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming"), name)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", name)
    return os.path.join(os.path.expanduser("~"), ".local", "share", slug)


# ------------------------------------------------------------------ dal nome precedente
_COPY_SKIP = {".venv", "cuestage", "__pycache__", "run.sh", "requirements.txt", "README.md"}


def migrate_settings(app=None, legacy=None):
    """Preferenze del nome precedente → nome attuale, se quelle attuali sono ancora vuote. Ritorna il nome da cui
    sono state copiate, o None."""
    from PySide6.QtCore import QSettings
    app = app or APP_NAME
    cur = QSettings(app, app)
    # macOS: senza questo allKeys() comprende anche le preferenze globali del sistema (AppleLocale…) e le
    # preferenze del programma non sembrano mai vuote
    cur.setFallbacksEnabled(False)
    if cur.allKeys():
        return None
    for name, _slug, _env in (legacy if legacy is not None else _legacy()):
        if name == app:
            continue
        old = QSettings(name, name)
        old.setFallbacksEnabled(False)
        keys = old.allKeys()
        if not keys:
            continue
        for k in keys:
            cur.setValue(k, old.value(k))
        cur.setValue("migrated_from", name)
        cur.sync()
        return name
    return None


def migrate_data(app=None, slug=None, legacy=None):
    """Cartella dei dati del nome precedente → nuova cartella, se questa non c'è ancora (si copia: la vecchia
    resta, per tornare indietro). Ritorna la cartella copiata, o None."""
    import shutil
    new = data_base(app, slug)
    if os.path.exists(new):
        return None
    for name, oslug, _env in (legacy if legacy is not None else _legacy()):
        old = data_base(name, oslug)
        if old == new or not os.path.isdir(old):
            continue
        try:
            shutil.copytree(old, new, symlinks=True,
                            ignore=lambda d, names, root=old: [n for n in names if d == root and n in _COPY_SKIP])
            with open(os.path.join(new, MIGRATED_MARK), "w", encoding="utf-8") as f:
                f.write(old + "\n")
        except OSError:
            return None
        return old
    return None


def migrate():
    """All'avvio: riprende preferenze e dati del nome precedente del programma. Ritorna i messaggi per il
    registro (vuoto se non c'era niente da fare)."""
    out = []
    try:
        n = migrate_settings()
        if n:
            out.append(f"{APP_NAME}: preferenze riprese da {n}")
    except Exception:
        pass
    try:
        d = migrate_data()
        if d:
            out.append(f"{APP_NAME}: dati ripresi da {d}")
    except Exception:
        pass
    return out
