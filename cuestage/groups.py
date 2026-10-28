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
}
GROUP_SHORT = {"sequence": tr("Sequence"), "random": tr("Random"), "loop": tr("Loop"), "rloop": tr("Random loop"), "all": tr("All"),
               "playlist": tr("Playlist"), "rplaylist": tr("Random playlist")}


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
