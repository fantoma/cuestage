"""Cronologia delle modifiche dello show: annulla / ripeti e ritorno a un punto qualsiasi.

Ogni passo è una fotografia dello show (impostazioni, liste, cue, versioni) fatta dopo una
modifica. Le cue sono salvate come testo JSON una per una e le parti che non cambiano tra un
passo e l'altro restano lo stesso oggetto, quindi anche centinaia di passi occupano poco.

Non entrano nella cronologia i comandi «dal vivo»: volume master, Duck, DIM, volume del
preascolto, blind, gruppi aperti o chiusi nella lista."""
import copy
import json
import time

from .i18n import tr
from .model import Cue, CueList, ShowVersion, MAIN_ID

MAX_STEPS = 500

# impostazioni che si muovono durante lo spettacolo: annullare una modifica non le tocca
VOLATILE_SETTINGS = ("master_db", "edit_blind")
VOLATILE_SUB = {"ducking": ("master_on", "dim_on"), "preview": ("db",)}
VOLATILE_CUE = ("collapsed",)


def _dump(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def stable_settings(settings):
    d = {k: v for k, v in settings.items() if k not in VOLATILE_SETTINGS}
    for k, sub in VOLATILE_SUB.items():
        if isinstance(d.get(k), dict):
            d[k] = {a: b for a, b in d[k].items() if a not in sub}
    return d


def _cue_json(c):
    d = c.to_dict(None)
    for k in VOLATILE_CUE:
        d.pop(k, None)
    return _dump(d)


class Snapshot:
    __slots__ = ("settings", "lists", "versions", "active")

    def __init__(self, settings, lists, versions, active):
        self.settings = settings     # JSON delle impostazioni stabili
        self.lists = lists           # ((id lista, JSON nome+griglia, ((id cue, JSON cue), …)), …)
        self.versions = versions     # ((id, JSON nome+note, liste come sopra o None per l'attiva), …)
        self.active = active

    def __eq__(self, other):
        return isinstance(other, Snapshot) and self.active == other.active and \
            self.settings == other.settings and self.lists == other.lists and self.versions == other.versions


class Step:
    __slots__ = ("snap", "desc", "time", "mark")

    def __init__(self, snap, desc, mark=""):
        self.snap = snap
        self.desc = desc
        self.time = time.time()
        self.mark = mark             # nome del punto segnato (vuoto = nessuno)


class History:
    def __init__(self, max_steps=MAX_STEPS):
        self.max_steps = max_steps
        self.steps = []
        self.index = -1
        self.saved = -1              # passo che corrisponde al file salvato (-1 = nessuno)
        self._cache = {}             # chiave -> ultimo JSON: le parti uguali restano lo stesso oggetto

    # ---- fotografia
    def _share(self, key, text):
        old = self._cache.get(key)
        if old is not None and old == text:
            return old
        self._cache[key] = text
        return text

    def _lists_snap(self, lists, prefix):
        out = []
        for l in lists:
            meta = self._share((prefix, "l", l.id), _dump({"name": l.name, "grid": l.grid_data()}))
            cues = tuple((c.id, self._share((prefix, "c", c.id), _cue_json(c))) for c in l.cues)
            out.append((l.id, meta, cues))
        return tuple(out)

    def snapshot(self, show):
        vs = []
        for v in show.versions:
            meta = self._share(("v", v.id), _dump({"name": v.name, "notes": v.notes}))
            lists = None if v.id == show.active_version else self._lists_snap(v.lists or [], v.id)
            vs.append((v.id, meta, lists))
        return Snapshot(self._share(("s",), _dump(stable_settings(show.settings))),
                        self._lists_snap(show.lists, ""), tuple(vs), show.active_version)

    # ---- passi
    def reset(self, show, desc, saved=True):
        self.steps = []
        self._cache = {}
        self.steps.append(Step(self.snapshot(show), desc))
        self.index = 0
        self.saved = 0 if saved else -1

    @property
    def current(self):
        return self.steps[self.index] if 0 <= self.index < len(self.steps) else None

    def capture(self, show, desc=None):
        """Nuovo passo se lo show è cambiato dall'ultimo; i passi «ripeti» si perdono."""
        snap = self.snapshot(show)
        cur = self.current
        if cur is not None and cur.snap == snap:
            return None
        if cur is not None and snap.lists != cur.snap.lists:
            from .model import _now
            show.active.modified = _now()
        step = Step(snap, desc or (describe(cur.snap, snap) if cur else tr("Modifica")))
        del self.steps[self.index + 1:]
        if self.saved > self.index:
            self.saved = -1
        self.steps.append(step)
        self.index = len(self.steps) - 1
        drop = len(self.steps) - self.max_steps
        if drop > 0:
            del self.steps[:drop]
            self.index -= drop
            self.saved = self.saved - drop if self.saved >= drop else -1
        return step

    def can_undo(self):
        return self.index > 0

    def can_redo(self):
        return self.index < len(self.steps) - 1

    def undo_text(self):
        return self.steps[self.index].desc if self.can_undo() else ""

    def redo_text(self):
        return self.steps[self.index + 1].desc if self.can_redo() else ""

    def mark_saved(self):
        self.saved = self.index

    # ---- ritorno a un passo
    def restore(self, show, i):
        """Riporta lo show al passo i (modifica lo show sul posto). Ritorna True se sono cambiate
        le impostazioni (da riapplicare)."""
        if not (0 <= i < len(self.steps)):
            return False
        snap = self.steps[i].snap
        old_cues = {c.id: c for c in show.all_cues()}
        old_lists = {l.id: l for l in show.lists}
        cur = self.current.snap if self.current else None
        cur_cue = {}
        if cur is not None:
            for _lid, _meta, cues in cur.lists:
                cur_cue.update(cues)

        def build(lists_snap, reuse):
            out = []
            for lid, meta, cues in lists_snap:
                m = json.loads(meta)
                lst = old_lists.get(lid) if reuse else None
                if lst is None:
                    lst = CueList(m.get("name") or "PANEL", [], lid)
                lst.name = m.get("name") or lst.name
                lst.set_grid_data(m.get("grid") or {})
                new_cues = []
                for cid, cj in cues:
                    c = old_cues.get(cid) if reuse else None
                    if c is None or cur_cue.get(cid) != cj:
                        keep = {k: getattr(c, k) for k in VOLATILE_CUE} if c is not None else {}
                        c = Cue.from_dict(json.loads(cj), None)
                        for k, v in keep.items():
                            setattr(c, k, v)
                    new_cues.append(c)
                lst.cues = new_cues
                out.append(lst)
            if not out or out[0].id != MAIN_ID:
                out.insert(0, CueList("MAIN", list_id=MAIN_ID))
            return out

        view_id = show.current.id
        same_active = snap.active == show.active_version
        # versioni (gli oggetti esistenti tengono le date)
        old_versions = {v.id: v for v in show.versions}
        cur_versions = {vid: lists for vid, _m, lists in cur.versions} if cur is not None else {}
        versions = []
        for vid, meta, lists in snap.versions:
            m = json.loads(meta)
            v = old_versions.get(vid) or ShowVersion(m.get("name") or "", version_id=vid)
            v.name, v.notes = m.get("name") or v.name, m.get("notes") or ""
            if lists is None:
                v.lists = None
            elif vid == show.active_version:
                # versione attiva ora, non in quel passo: tiene le liste di adesso se uguali
                v.lists = show.lists if cur is not None and lists == cur.lists else build(lists, False)
            elif v.lists is None or cur_versions.get(vid) != lists:
                v.lists = build(lists, False)
            versions.append(v)
        show.lists = build(snap.lists, same_active)
        show.versions = versions
        show.active_version = snap.active
        show.view = next((k for k, l in enumerate(show.lists) if l.id == view_id), 0)
        changed = False
        if cur is None or cur.settings != snap.settings:
            new = json.loads(snap.settings)
            for k in VOLATILE_SETTINGS:
                if k in show.settings:
                    new[k] = show.settings[k]
            for k, sub in VOLATILE_SUB.items():
                if isinstance(new.get(k), dict) and isinstance(show.settings.get(k), dict):
                    for a in sub:
                        if a in show.settings[k]:
                            new[k][a] = show.settings[k][a]
            show.settings = new
            changed = True
        self.index = i
        self._seed(snap)              # le prossime fotografie condividono le parti di questo passo
        return changed

    def _seed(self, snap):
        c = {("s",): snap.settings}

        def lists(ls, prefix):
            for lid, meta, cues in ls or ():
                c[(prefix, "l", lid)] = meta
                for cid, cj in cues:
                    c[(prefix, "c", cid)] = cj
        lists(snap.lists, "")
        for vid, meta, ls in snap.versions:
            c[("v", vid)] = meta
            lists(ls, vid)
        self._cache = c

    def lists_at(self, i):
        """Copia indipendente delle liste (versione attiva) del passo i: per farne una versione."""
        snap = self.steps[i].snap
        out = []
        for lid, meta, cues in snap.lists:
            m = json.loads(meta)
            lst = CueList(m.get("name") or "PANEL", [Cue.from_dict(json.loads(cj), None) for _cid, cj in cues], lid)
            lst.set_grid_data(m.get("grid") or {})
            out.append(lst)
        return out


# ------------------------------------------------------------------ descrizione dei passi
def _field_labels():
    return {
        "name": tr("nome"), "number": tr("numero"), "file": tr("file"), "notes": tr("note"),
        "trigger": tr("avvio"), "delay": tr("ritardo"), "level_db": tr("livello"),
        "fade_in": tr("fade in"), "fade_out": tr("fade out"), "curve": tr("curva"),
        "start": tr("inizio"), "end": tr("fine"), "loop": tr("loop"), "loop_start": tr("loop"),
        "loop_end": tr("loop"), "loop_count": tr("loop"), "audio_device": tr("scheda audio"),
        "matrix": tr("matrice"), "out_levels": tr("livelli delle uscite"), "duck_db": "ducking",
        "pfl": "PFL", "preload": tr("precarica"), "layer": "layer", "opacity": tr("opacità"),
        "fit": tr("adattamento"), "duration": tr("durata"), "crop_l": "crop", "crop_t": "crop",
        "crop_r": "crop", "crop_b": "crop", "off_x": tr("posizione"), "off_y": tr("posizione"),
        "scale": tr("scala"), "warp": tr("prospettiva"), "target": "target",
        "fade_duration": tr("durata del fade"), "target_db": tr("livello finale"),
        "target_opacity": tr("opacità finale"), "presets": "preset", "preset_points": "keyframe",
        "color": tr("colore"), "hotkey": tr("scorciatoia"), "grid_pos": tr("posizione nella griglia"),
        "group": tr("gruppo"), "text_content": tr("testo"), "midi": "MIDI", "osc_trigger": "OSC",
        "mo_msgs": tr("messaggi MIDI"), "osc_address": tr("indirizzo OSC"), "osc_args": tr("argomenti OSC"),
        "type": tr("tipo"),
    }


def _label(cj):
    try:
        d = json.loads(cj)
    except ValueError:
        return "?"
    from .model import TYPE_LABELS
    n = str(d.get("number") or "").strip()
    name = d.get("name") or TYPE_LABELS.get(d.get("type"), d.get("type") or "")
    return f"{n} «{name}»" if n else f"«{name}»"


def _all_cues(lists_snap):
    out, order = {}, []
    for lid, _meta, cues in lists_snap or ():
        for cid, cj in cues:
            out[cid] = cj
            order.append((lid, cid))
    return out, order


def diff_lists(a, b):
    """(aggiunte, eliminate, modificate, spostate) tra due insiemi di liste (in forma di fotografia)."""
    ca, oa = _all_cues(a)
    cb, ob = _all_cues(b)
    added = [cid for _l, cid in ob if cid not in ca]
    removed = [cid for _l, cid in oa if cid not in cb]
    changed = [cid for _l, cid in ob if cid in ca and ca[cid] != cb[cid]]
    keep = set(ca) & set(cb)
    moved = [x for x in oa if x[1] in keep] != [x for x in ob if x[1] in keep]
    return added, removed, changed, moved, ca, cb


def describe_lists(a, b):
    parts = []
    added, removed, changed, moved, ca, cb = diff_lists(a, b)
    la = {lid: json.loads(m).get("name") for lid, m, _c in a or ()}
    lb = {lid: json.loads(m).get("name") for lid, m, _c in b or ()}
    for lid in lb:
        if lid not in la:
            parts.append(tr("nuovo PANEL «{0}»", lb[lid]))
        elif la[lid] != lb[lid]:
            parts.append(tr("PANEL rinominato «{0}»", lb[lid]))
    for lid in la:
        if lid not in lb:
            parts.append(tr("eliminato PANEL «{0}»", la[lid]))
    ma = {lid: m for lid, m, _c in a or ()}
    if any(lid in ma and ma[lid] != m and la.get(lid) == lb.get(lid) for lid, m, _c in b or ()):
        parts.append(tr("griglia del PANEL"))
    if len(added) == 1:
        parts.append(tr("nuova cue {0}", _label(cb[added[0]])))
    elif added:
        parts.append(tr("{0} cue aggiunte", len(added)))
    if len(removed) == 1:
        parts.append(tr("eliminata cue {0}", _label(ca[removed[0]])))
    elif removed:
        parts.append(tr("{0} cue eliminate", len(removed)))
    if len(changed) == 1:
        cid = changed[0]
        try:
            da, db = json.loads(ca[cid]), json.loads(cb[cid])
        except ValueError:
            da, db = {}, {}
        labels = _field_labels()
        fields = []
        for k in db:
            if da.get(k) != db.get(k):
                lab = labels.get(k, tr("parametri"))
                if lab not in fields:
                    fields.append(lab)
        what = ", ".join(fields[:3]) + ("…" if len(fields) > 3 else "")
        parts.append(tr("cue {0}: {1}", _label(cb[cid]), what) if what else tr("cue {0}", _label(cb[cid])))
    elif changed:
        parts.append(tr("{0} cue modificate", len(changed)))
    if moved and not parts:
        parts.append(tr("cue spostate"))
    elif moved:
        parts.append(tr("ordine"))
    return parts


def describe(a, b):
    """Descrizione breve di cosa cambia dalla fotografia a alla b."""
    parts = []
    if a.active != b.active:
        name = next((json.loads(m).get("name") for vid, m, _l in b.versions if vid == b.active), "")
        return tr("Passa alla versione «{0}»", name)
    va = {vid: (m, l) for vid, m, l in a.versions}
    vb = {vid: (m, l) for vid, m, l in b.versions}
    for vid, (m, _l) in vb.items():
        name = json.loads(m).get("name")
        if vid not in va:
            parts.append(tr("nuova versione «{0}»", name))
        elif va[vid][0] != m:
            old = json.loads(va[vid][0])
            parts.append(tr("versione «{0}»: annotazioni", name) if old.get("name") == name
                         else tr("versione rinominata «{0}»", name))
        elif va[vid][1] != vb[vid][1] and vid != b.active:
            parts.append(tr("versione «{0}»", name))
    for vid, (m, _l) in va.items():
        if vid not in vb:
            parts.append(tr("eliminata la versione «{0}»", json.loads(m).get("name")))
    if a.settings != b.settings:
        sa, sb = json.loads(a.settings), json.loads(b.settings)
        keys = [k for k in sb if sa.get(k) != sb.get(k)]
        names = {"ducking": "ducking", "preview": tr("preascolto"), "mixers": tr("mixer"),
                 "projectors": tr("proiettori"), "go_target": tr("GO su"), "midi_map": "MIDI",
                 "midi_controls": "MIDI", "osc_devices": "OSC"}
        sub = sorted({names[k] for k in keys if k in names})
        parts.append(tr("impostazioni") + (": " + ", ".join(sub) if sub else ""))
    parts += describe_lists(a.lists, b.lists)
    if not parts:
        return tr("Modifica")
    text = " · ".join(parts)
    return text[0].upper() + text[1:]


def compare_summary(a_lists, b_lists):
    """Riassunto delle differenze tra due versioni (liste in forma di fotografia)."""
    added, removed, changed, moved, _ca, _cb = diff_lists(a_lists, b_lists)
    if not (added or removed or changed or moved) and \
            [(l, m) for l, m, _c in a_lists] == [(l, m) for l, m, _c in b_lists]:
        return tr("uguale alla versione attiva")
    bits = []
    if added:
        bits.append(tr("{0} cue in più", len(added)))
    if removed:
        bits.append(tr("{0} cue in meno", len(removed)))
    if changed:
        bits.append(tr("{0} cue diverse", len(changed)))
    if moved:
        bits.append(tr("ordine diverso"))
    if not bits:
        bits.append(tr("PANEL diversi"))
    return tr("rispetto alla versione attiva: {0}", ", ".join(bits))
