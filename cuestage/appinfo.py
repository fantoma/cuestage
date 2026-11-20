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

APP_NAME = "CueStage"
APP_SLUG = "cuestage"
ENV_PREFIX = "CUESTAGE"
PROTOCOL_ID = "CueStage"


def env(key, default=None):
    """Variabile d'ambiente del programma: env("LANG") legge <ENV_PREFIX>_LANG."""
    v = os.environ.get(f"{ENV_PREFIX}_{key}")
    return default if v is None else v


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
