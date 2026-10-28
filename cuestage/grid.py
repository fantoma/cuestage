"""PANEL GRID: le cue di un PANEL disposte in caselle (righe × colonne), su più pagine.

Ogni cue «di primo livello» (non membro di un gruppo) occupa una casella: cue.grid_pos = [pagina, riga, colonna].
I membri di un gruppo stanno dentro la casella del loro gruppo. L'ordine della lista segue le caselle
(pagina, riga, colonna), così il GO locale del PANEL avanza casella dopo casella.
"""
from .i18n import tr


def top_level(lst):
    """Cue che occupano una casella: tutte tranne i membri dei gruppi."""
    return [c for c in lst.cues if not c.group]


def _valid(lst, pos):
    return (isinstance(pos, (list, tuple)) and len(pos) == 3
            and 0 <= int(pos[0]) < len(lst.grid_pages)
            and 0 <= int(pos[1]) < lst.grid_rows and 0 <= int(pos[2]) < lst.grid_cols)


def occupied(lst):
    """{(pagina, riga, colonna): cue}."""
    out = {}
    for c in top_level(lst):
        if _valid(lst, c.grid_pos):
            out[tuple(int(x) for x in c.grid_pos)] = c
    return out


def cell_cue(lst, page, row, col):
    return occupied(lst).get((page, row, col))


def free_cell(lst, page=0, taken=None, start=None):
    """Prima casella libera a partire da «start» = [pagina, riga, colonna] (o dall'inizio di «page»),
    in ordine di lettura, poi sulle pagine successive (aggiungendone una se servono)."""
    taken = occupied(lst) if taken is None else taken
    pages = len(lst.grid_pages)
    R, C = lst.grid_rows, lst.grid_cols
    if start is not None and _valid(lst, start):
        p0, i0 = int(start[0]), int(start[1]) * C + int(start[2])
    else:
        p0, i0 = max(0, min(page, pages - 1)), 0
    for k in range(pages):
        p = (p0 + k) % pages
        for i in range(i0 if k == 0 else 0, R * C):
            if (p, i // C, i % C) not in taken:
                return [p, i // C, i % C]
    if i0 > 0:                                   # le caselle prima del punto di partenza
        for i in range(0, i0):
            if (p0, i // C, i % C) not in taken:
                return [p0, i // C, i % C]
    lst.grid_pages.append(tr("Pagina {0}", pages + 1))
    return [pages, 0, 0]


def ensure_positions(lst, preferred=None, page=0):
    """Dà una casella a ogni cue che non ne ha una valida (o che la condivide con un'altra).
    «preferred» = casella da cui partire per le cue nuove (es. dove sono stati trascinati dei file):
    la prima va lì (o nella prima libera dopo), le altre nelle caselle seguenti.
    Restituisce True se qualcosa è cambiato."""
    changed = False
    taken = {}
    pending = []
    for c in top_level(lst):
        if c.grid_pos and _valid(lst, c.grid_pos):
            key = tuple(int(x) for x in c.grid_pos)
            if key not in taken:
                taken[key] = c
                if list(key) != list(c.grid_pos):
                    c.grid_pos = list(key)
                continue
        pending.append(c)
    for c in lst.cues:                        # i membri dei gruppi non hanno caselle proprie
        if c.group and c.grid_pos:
            c.grid_pos = []
            changed = True
    cursor = list(preferred) if preferred is not None and _valid(lst, preferred) else None
    for c in pending:
        pos = free_cell(lst, page, taken, start=cursor)
        c.grid_pos = pos
        taken[tuple(pos)] = c
        if cursor is not None:
            cursor = pos
        changed = True
    return changed


def sort_cues(lst):
    """Ordina la lista per casella (pagina, riga, colonna), tenendo ogni gruppo unito alle sue cue.
    Restituisce True se l'ordine è cambiato."""
    blocks = []
    cur = None
    for c in lst.cues:
        if c.group and cur is not None and cur[0].id == c.group:
            cur.append(c)
            continue
        cur = [c]
        blocks.append(cur)
    big = (1 << 30,) * 3

    def key(b):
        p = b[0].grid_pos
        return tuple(int(x) for x in p) if p and len(p) == 3 else big
    new = [c for b in sorted(blocks, key=key) for c in b]
    if [c.id for c in new] != [c.id for c in lst.cues]:
        lst.cues[:] = new
        return True
    return False


def move_cell(lst, cue, page, row, col):
    """Sposta la cue in una casella; se è occupata, le due cue si scambiano di posto."""
    other = cell_cue(lst, page, row, col)
    if other is cue:
        return False
    if other is not None:
        other.grid_pos = list(cue.grid_pos) if cue.grid_pos else []
    cue.grid_pos = [page, row, col]
    return True


def resize(lst, rows, cols):
    """Nuove dimensioni: le cue che non ci stanno più vanno nella prima casella libera (anche su nuove pagine)."""
    lst.grid_rows = max(1, min(16, int(rows)))
    lst.grid_cols = max(1, min(16, int(cols)))
    ensure_positions(lst)
    sort_cues(lst)


def page_cues(lst, page):
    return [c for c in top_level(lst) if c.grid_pos and int(c.grid_pos[0]) == page]


def delete_page(lst, page):
    """Toglie una pagina vuota (le pagine successive scalano). False se la pagina contiene cue."""
    if page_cues(lst, page) or len(lst.grid_pages) <= 1:
        return False
    del lst.grid_pages[page]
    for c in top_level(lst):
        if c.grid_pos and int(c.grid_pos[0]) > page:
            c.grid_pos = [int(c.grid_pos[0]) - 1, c.grid_pos[1], c.grid_pos[2]]
    sort_cues(lst)
    return True


def signature(lst):
    """Impronta di ciò che la griglia mostra (per accorgersi di modifiche fatte altrove)."""
    return (lst.kind, lst.grid_rows, lst.grid_cols, tuple(lst.grid_pages),
            tuple((c.id, c.group, tuple(c.grid_pos or ())) for c in lst.cues))


def text_color(hex_color):
    """Bianco o nero, a seconda del colore di fondo."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return "#f0f0f4"
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#111114" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#f4f4f8"


PALETTE = ["#2f3440", "#7a2730", "#a14a1c", "#9a7b14", "#3f7a2a", "#1d7262", "#1f5d8f", "#43439a",
           "#76398d", "#9a2f68", "#5b5f6a", "#d9d9de"]
