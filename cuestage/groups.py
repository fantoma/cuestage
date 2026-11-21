"""Gruppi di cue: una cue «Gruppo» seguita dalle sue cue (campo «group» = id del gruppo).

La lista resta piatta: un gruppo occupa le righe dalla sua cue fino all'ultimo membro contiguo.
"""
from .i18n import tr
GROUP_MODES = {
    "sequence": tr("Sequence — la prima parte col GO, le altre in sequenza con i GO"),
    "random": tr("Random — parte una cue a caso; il GO successivo esce"),
    "loop": tr("Loop — in sequenza da sole (attesa + crossfade); il GO successivo esce"),
    "rloop": tr("Random loop — in ordine casuale; avanzamento a tempo o col GO"),
    "all": tr("All — partono tutte insieme (pre-wait rispettati); il GO successivo esce"),
    "playlist": tr("Playlist — una dopo l'altra da sole, senza GO né pre-wait; finisce dopo l'ultima"),
    "rplaylist": tr("Random playlist — come Playlist, in ordine casuale (ognuna una volta)"),
    "stinger": tr("Stinger — come Sequence, ogni cue con una PRE prima e una POST dopo (collegamenti)"),
}
GROUP_SHORT = {"sequence": tr("Sequence"), "random": tr("Random"), "loop": tr("Loop"), "rloop": tr("Random loop"), "all": tr("All"),
               "playlist": tr("Playlist"), "rplaylist": tr("Random playlist"), "stinger": tr("Stinger")}
SEQ_MODES = ("sequence", "stinger")          # un GO per cue, nell'ordine della lista
ST_STARTS = ("with", "after", "go")          # partenza di PRE → cue e cue → POST
ST_TRIGGER = {"with": "with_prev", "after": "after_prev", "go": "manual"}


def normalize(cues):
    """Rende coerenti le appartenenze: un membro sta subito dopo la sua cue Gruppo o dopo un altro membro
    dello stesso gruppo; altrimenti esce dal gruppo. Niente gruppi annidati."""
    cur = None
    for c in cues:
        if c.type == "group":
            c.group = ""
            cur = c
            continue
        if c.group and cur is not None and c.group == cur.id:
            continue
        if c.group:
            c.group = ""
        cur = None


def block_end(cues, i):
    """Indice successivo all'ultima riga del gruppo che inizia in i (i se non è un gruppo)."""
    if not (0 <= i < len(cues)) or cues[i].type != "group":
        return i + 1
    gid = cues[i].id
    j = i + 1
    while j < len(cues) and cues[j].group == gid:
        j += 1
    return j


def members(cues, i, playable=False):
    """Cue del gruppo che inizia all'indice i (con playable=True senza le cue «Esci dal gruppo»)."""
    out = cues[i + 1:block_end(cues, i)]
    if playable:
        out = [c for c in out if c.type not in ("groupexit", "memo")]
    return out


def membership_for_insert(cues, row):
    """Gruppo a cui assegnare una cue inserita alla riga «row» (quella sopra è il gruppo o un suo membro)."""
    if row <= 0 or row > len(cues):
        return ""
    prev = cues[row - 1]
    return prev.id if prev.type == "group" else prev.group


# ------------------------------------------------------------------ gruppi Stinger
def _is_role(c, role=None):
    r = getattr(c, "st_role", "") if getattr(c, "is_link", False) else ""
    return bool(r) if role is None else r == role


def stinger_blocks(cues, i):
    """Blocchi di un gruppo Stinger che inizia all'indice i: {id della riga: indice del blocco} (la PRE va con
    la cue che segue, la POST con quella che precede)."""
    out = {}
    rows = cues[i + 1:block_end(cues, i)]
    k = -1
    pending = []
    for c in rows:
        if _is_role(c, "pre"):
            pending.append(c.id)
            continue
        if _is_role(c, "post"):
            out[c.id] = max(k, 0)
            continue
        k += 1
        out[c.id] = k
        for pid in pending:
            out[pid] = k
        pending = []
    for pid in pending:
        out[pid] = k + 1
    return out


def _set_trigger(c, trig):
    """Partenza decisa dal gruppo Stinger (quella di prima si ricorda per quando la cue esce dal gruppo)."""
    if getattr(c, "is_link", False):
        if c.st_trigger != trig:
            c.st_trigger = trig
            return True
        return False
    changed = False
    if not c.st_saved_trigger:
        c.st_saved_trigger = c.trigger or "manual"
        changed = True
    if c.trigger != trig:
        c.trigger = trig
        changed = True
    return changed


def _release(c):
    """La cue non è (più) in un gruppo Stinger: torna la sua partenza."""
    if getattr(c, "is_link", False):
        if c.st_trigger or c.st_role:
            c.st_trigger = ""
            c.st_role = ""
            return True
        return False
    if c.st_saved_trigger:
        c.trigger = c.st_saved_trigger
        c.st_saved_trigger = ""
        return True
    return False


def normalize_stingers(show, new_number):
    """Mette in ordine i gruppi Stinger di tutte le liste: ogni cue del gruppo ha la sua PRE prima e la sua POST
    dopo (collegamenti alle cue scelte nel gruppo, tranne la PRE della prima e la POST dell'ultima se escluse),
    le righe PRE/POST in più si tolgono, le partenze sono quelle del gruppo. Fuori dagli Stinger le cue tornano
    come prima. new_number() dà il numero di un nuovo collegamento. Ritorna True se ha cambiato qualcosa."""
    from .model import LinkCue, link_target_ok
    changed = False
    for lst in show.lists:
        cues = lst.cues
        inside = set()
        i = 0
        while i < len(cues):
            g = cues[i]
            if g.type != "group" or g.group_mode != "stinger":
                i += 1
                continue
            end = block_end(cues, i)
            rows = cues[i + 1:end]
            pre_o = show.by_id(g.st_pre) if g.st_pre else None
            post_o = show.by_id(g.st_post) if g.st_post else None
            pre_o = pre_o if link_target_ok(pre_o) else None
            post_o = post_o if link_target_ok(post_o) else None
            mem = [c for c in rows if not _is_role(c)]
            new_rows = []
            for k, m in enumerate(mem):
                j = rows.index(m)
                if pre_o is not None and not (k == 0 and g.st_skip_first_pre):
                    p = rows[j - 1] if j > 0 and _is_role(rows[j - 1], "pre") else None
                    if p is None:
                        p = LinkCue(link_target=pre_o.id, number=new_number(), st_role="pre")
                        changed = True
                    if p.link_target != pre_o.id:
                        p.set_target(pre_o)
                        changed = True
                    p.group = g.id
                    if p.disabled != bool(m.disabled):     # PRE e POST seguono la loro cue (anche disabilitata)
                        p.disabled = bool(m.disabled)
                        changed = True
                    changed |= _set_trigger(p, "manual")
                    new_rows.append(p)
                    changed |= _set_trigger(m, ST_TRIGGER.get(g.st_pre_start, "with_prev"))
                else:
                    changed |= _set_trigger(m, "manual")
                new_rows.append(m)
                if post_o is not None and not (k == len(mem) - 1 and g.st_skip_last_post):
                    q = rows[j + 1] if j + 1 < len(rows) and _is_role(rows[j + 1], "post") else None
                    if q is None:
                        q = LinkCue(link_target=post_o.id, number=new_number(), st_role="post")
                        changed = True
                    if q.link_target != post_o.id:
                        q.set_target(post_o)
                        changed = True
                    q.group = g.id
                    if q.disabled != bool(m.disabled):
                        q.disabled = bool(m.disabled)
                        changed = True
                    changed |= _set_trigger(q, ST_TRIGGER.get(g.st_post_start, "after_prev"))
                    new_rows.append(q)
            if [c.id for c in new_rows] != [c.id for c in rows]:
                cues[i + 1:end] = new_rows
                changed = True
            inside.update(c.id for c in new_rows)
            i = i + 1 + len(new_rows)
        # righe PRE/POST fuori da un gruppo Stinger (gruppo cambiato di tipo, righe trascinate fuori): si tolgono
        stray = [c for c in cues if c.id not in inside and _is_role(c)]
        if stray:
            cues[:] = [c for c in cues if not (c.id not in inside and _is_role(c))]
            changed = True
        for c in cues:
            if c.id not in inside and (c.type != "group") and _release_needed(c):
                changed |= _release(c)
    return changed


def _release_needed(c):
    if getattr(c, "is_link", False):
        return bool(c.st_role or c.st_trigger)
    return bool(getattr(c, "st_saved_trigger", ""))
