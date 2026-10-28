"""Controlli ALSA delle schede audio (volumi, interruttori, menu) tramite «amixer» (pacchetto alsa-utils).

Su Linux le funzioni delle schede che hanno un pannello di controllo proprio (Focusrite Scarlett/Clarett con il
driver «scarlett2» del kernel, RME, MOTU, Behringer…) compaiono come controlli del mixer ALSA: qui si leggono
con «amixer -c SCHEDA contents» e si scrivono con «amixer -c SCHEDA cset numid=N valore».
"""
from .i18n import tr
import os
import re
import shutil
import subprocess
import threading
import time


class AlsaError(Exception):
    pass


def amixer_path():
    return shutil.which("amixer")


def _run(args, timeout=3.0):
    exe = amixer_path()
    if not exe:
        raise AlsaError(tr("«amixer» non trovato: installa il pacchetto alsa-utils (sudo apt install alsa-utils)"))
    env = dict(os.environ, LANG="C", LC_ALL="C")
    try:
        r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise AlsaError(tr("amixer non risponde"))
    if r.returncode != 0:
        raise AlsaError((r.stderr or r.stdout or tr("errore di amixer")).strip().splitlines()[-1])
    return r.stdout


# ------------------------------------------------------------------ schede
def alsa_cards():
    """[(indice, id, nome breve, nome lungo)] da /proc/asound/cards."""
    out = []
    try:
        with open("/proc/asound/cards") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    i = 0
    while i < len(lines):
        m = re.match(r"\s*(\d+)\s+\[(\S+)\s*\]:\s*(.*?)\s+-\s+(.*)$", lines[i])
        if m:
            long = lines[i + 1].strip() if i + 1 < len(lines) else ""
            out.append((int(m.group(1)), m.group(2), m.group(4).strip(), long))
            i += 2
        else:
            i += 1
    return out


def resolve_card(card, hint=""):
    """Argomento per «amixer -c»: l'id della scheda (stabile) o, se manca, quella col nome indicato."""
    cards = alsa_cards()
    if card:
        for idx, cid, short, long in cards:
            if card in (cid, str(idx)):
                return cid
    for key in (hint, card):
        if key:
            for idx, cid, short, long in cards:
                if key.lower() in short.lower() or key.lower() in long.lower():
                    return cid
    if not cards and card:
        return card                      # /proc non leggibile: prova comunque
    raise AlsaError(tr("scheda audio «{0}» non collegata", card or hint))


# ------------------------------------------------------------------ controlli
class Control:
    """Un controllo ALSA: type in «int», «bool», «enum»."""

    def __init__(self, numid, name, index=0):
        self.numid = numid
        self.name = name
        self.index = index
        self.type = "int"
        self.count = 1
        self.min = 0
        self.max = 1
        self.items = []
        self.values = []
        self.db = None            # (dB al minimo, dB per passo) oppure ("minmax", dBmin, dBmax)
        self.writable = True

    @property
    def key(self):
        return self.name if not self.index else f"{self.name} #{self.index}"

    def value(self):
        return self.values[0] if self.values else None

    def to_db(self, raw):
        if self.db is None:
            return None
        if self.db[0] == "minmax":
            _k, lo, hi = self.db
            span = max(1, self.max - self.min)
            return lo + (hi - lo) * (raw - self.min) / span
        lo, step = self.db
        return lo + (raw - self.min) * step

    def from_db(self, db):
        if self.db is None:
            return None
        if self.db[0] == "minmax":
            _k, lo, hi = self.db
            f = (db - lo) / (hi - lo) if hi != lo else 0
            return int(round(self.min + max(0.0, min(1.0, f)) * (self.max - self.min)))
        lo, step = self.db
        raw = self.min + (db - lo) / step if step else self.min
        return int(max(self.min, min(self.max, round(raw))))

    def text(self, raw=None):
        raw = self.value() if raw is None else raw
        if raw is None:
            return "?"
        if self.type == "bool":
            return tr("acceso") if raw else tr("spento")
        if self.type == "enum":
            return self.items[raw] if 0 <= raw < len(self.items) else str(raw)
        db = self.to_db(raw)
        if db is not None:
            return tr("-∞ dB") if raw == self.min and self.db and self.db[0] != "minmax" and db <= -100 else f"{db:+.1f} dB"
        return f"{raw} ({self.min}…{self.max})"

    def as_dict(self):
        return {"key": self.key, "name": self.name, "type": self.type, "min": self.min, "max": self.max,
                "items": list(self.items), "count": self.count, "db": list(self.db) if self.db else None,
                "value": self.value(), "writable": self.writable}


def parse_contents(text):
    """Testo di «amixer contents» → [Control] (solo interfaccia MIXER)."""
    ctls = []
    cur = None
    for line in text.splitlines():
        m = re.match(r"numid=(\d+),iface=(\w+)(?:,[^']*?)?,name='(.*)'(?:,index=(\d+))?(?:,device=\d+)?\s*$", line)
        if m:
            cur = None
            if m.group(2) == "MIXER":
                cur = Control(int(m.group(1)), m.group(3), int(m.group(4) or 0))
                ctls.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("; type="):
            kv = dict(p.split("=", 1) for p in s[2:].split(",") if "=" in p)
            t = kv.get("type", "")
            cur.type = {"INTEGER": "int", "INTEGER64": "int", "BOOLEAN": "bool", "ENUMERATED": "enum"}.get(t, t.lower())
            cur.count = int(kv.get("values", 1) or 1)
            cur.writable = "w" in kv.get("access", "rw")[:2]
            if cur.type == "int":
                cur.min, cur.max = int(kv.get("min", 0)), int(kv.get("max", 1))
            elif cur.type == "bool":
                cur.min, cur.max = 0, 1
        elif s.startswith("; Item #"):
            im = re.match(r"; Item #(\d+) '(.*)'", s)
            if im:
                cur.items.append(im.group(2))
        elif s.startswith(": values="):
            raw = s[len(": values="):].split(",")
            vals = []
            for r in raw:
                r = r.strip()
                if r in ("on", "off"):
                    vals.append(1 if r == "on" else 0)
                else:
                    try:
                        vals.append(int(r))
                    except ValueError:
                        pass
            cur.values = vals
        elif s.startswith("| dBscale-min="):
            dm = re.match(r"\| dBscale-min=(-?[\d.]+)dB,step=(-?[\d.]+)dB", s)
            if dm:
                cur.db = (float(dm.group(1)), float(dm.group(2)))
        elif s.startswith("| dBminmax-min="):
            dm = re.match(r"\| dBminmax-min=(-?[\d.]+)dB,max=(-?[\d.]+)dB", s)
            if dm:
                cur.db = ("minmax", float(dm.group(1)), float(dm.group(2)))
    for c in ctls:
        if c.type == "enum":
            c.min, c.max = 0, max(0, len(c.items) - 1)
    return [c for c in ctls if c.type in ("int", "bool", "enum")]


_cache = {}
_lock = threading.Lock()


def read_controls(card, max_age=0.0):
    """Controlli della scheda (con cache facoltativa di max_age secondi)."""
    with _lock:
        hit = _cache.get(card)
        if hit and max_age and time.monotonic() - hit[0] < max_age:
            return hit[1]
    ctls = parse_contents(_run(["-c", card, "contents"]))
    with _lock:
        _cache[card] = (time.monotonic(), ctls)
    return ctls


def cached_controls(card):
    with _lock:
        hit = _cache.get(card)
    return hit[1] if hit else None


def cset(card, ctl, value):
    """Scrive il valore (uguale su tutti i canali del controllo, es. sinistro e destro)."""
    if ctl.type == "bool":
        v = "on" if int(value) else "off"
    else:
        v = str(int(value))
    _run(["-c", card, "cset", f"numid={ctl.numid}", ",".join([v] * max(1, ctl.count))])
    ctl.values = [int(value)] * max(1, ctl.count)


def group_of(name):
    """Gruppo del pannello per un controllo (nomi del driver Focusrite «scarlett2» e generici)."""
    n = name.lower()
    m = re.match(r"mix ([a-z]) input", n)
    if m:
        return tr("Mix {0}", m.group(1).upper())
    if "mixer" in n and "volume" in n:
        return tr("Mix")
    if n.endswith("enum") and "output" in n and "volume control" not in n:
        return tr("Routing uscite")
    if n.endswith("enum") and ("capture enum" in n and "level" not in n or "mixer input" in n or "dsp input" in n
                               or n.startswith("pcm")):
        return tr("Routing ingressi (PC e mixer)")
    if any(k in n for k in ("line in", "input", "mic", "phantom", "air", "pad", "inst", "level capture", "gain",
                            "capture", "48v", "safe", "autogain")):
        return tr("Ingressi")
    if any(k in n for k in ("line ", "master", "headphone", "speaker", "playback", "dim", "mute", "monitor",
                            "talkback", "output")):
        return tr("Uscite e monitor")
    return tr("Altro")


def pretty_name(name):
    """Nome leggibile: senza i suffissi tecnici ALSA («Capture Switch», «Playback Volume», «Enum»…)."""
    n = re.sub(r"\s+(Playback|Capture)?\s*(Switch|Volume|Enum|Route)$", "", name).strip()
    n = re.sub(r"\s+(Playback|Capture)$", "", n).strip()
    return n or name


GROUP_ORDER = [tr("Ingressi"), tr("Uscite e monitor"), tr("Mix"), tr("Routing uscite"), tr("Routing ingressi (PC e mixer)"), tr("Altro")]


def group_rank(g):
    if g.startswith("Mix "):
        return (GROUP_ORDER.index("Mix"), g)
    return (GROUP_ORDER.index(g) if g in GROUP_ORDER else 99, g)
