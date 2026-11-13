"""Liste MAIN e PANEL: schede, GO su, spostamento tra liste (parte di MainWindow)."""

from ..i18n import tr
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QInputDialog, QMenu, QMessageBox, )


FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


class ListsMixin:
    def _update_tabs(self):
        if not hasattr(self, "list_tabs"):
            return
        sh = self.show_data
        if self.ctrl.show is not sh:          # cambio di show in corso
            return
        go = sh.lists.index(self.ctrl.go_list())
        running = set()
        for i, lst in enumerate(sh.lists):
            if any(c.id in self.ctrl.instances for c in lst.cues) or \
                    any(p[1] in lst.cues for p in self.ctrl.pending):
                running.add(i)
        self.list_tabs.set_lists([("▦ " if l.is_grid else "") + l.name for l in sh.lists], sh.view, go, running,
                                 kinds=[l.kind for l in sh.lists])

    def show_list(self, i):
        """Mostra (e rende modificabile) la lista i: 0 = MAIN, poi i PANEL."""
        sh = self.show_data
        if not (0 <= i < len(sh.lists)):
            return
        if self.table.state() == QAbstractItemView.EditingState:
            self.table.closePersistentEditor(self.table.currentIndex())
        sh.view = i
        self.model.beginResetModel()
        self.model.endResetModel()
        self.inspector.set_cue(None)
        self._apply_collapse()
        self._select_guard = True
        r = self.ctrl.playhead
        if 0 <= r < len(sh.cues):
            self._select_rows([r])
            self.inspector.set_cue(sh.cues[r])
        else:
            self.table.clearSelection()
        self._select_guard = False
        self._apply_view_kind()
        if self.cluster.role == "slave":
            self._slave_edit_state()
        self._update_standby()

    def _apply_view_kind(self):
        """Tabella per le liste, caselle per i PANEL GRID."""
        if not hasattr(self, "view_stack"):
            return
        lst = self.show_data.current
        want = self.grid if lst.is_grid else self.table
        if self.view_stack.currentWidget() is not want or (lst.is_grid and self.grid.lst is not lst):
            self.view_stack.setCurrentWidget(want)
            if lst.is_grid:
                self.grid.set_list(lst)
                self._grid_sync(force=True)

    def reveal_cue(self, cue_id):
        """Mostra la lista che contiene la cue e la seleziona (senza spostare il playhead)."""
        lst, i = self.show_data.list_of(cue_id)
        if lst is None:
            return
        self.show_list(self.show_data.lists.index(lst))
        self._select_guard = True
        self._select_rows([i])
        self.inspector.set_cue(lst.cues[i])
        self._select_guard = False

    def _go_target_changed(self, key):
        self.show_data.settings["go_target"] = key
        self.show_data.dirty = True
        self._update_standby()
        self.status(tr("GO agisce sempre sulla MAIN") if key == "main" else tr("GO segue la lista selezionata"))

    def add_panel(self, kind="list"):
        if kind not in ("list", "grid"):        # dal segnale «clicked» del pulsante arriva un booleano
            kind = "list"
        lst = self.show_data.add_panel(kind=kind)
        if kind == "grid":
            lst.name = lst.name.replace("PANEL", "GRID")
        self.show_list(len(self.show_data.lists) - 1)
        self.status(tr("Creato «{0}» (doppio clic sul tab per rinominarlo)", lst.name)
                    + (tr(" · ✎ Modifica per righe, colonne e pagine") if kind == "grid" else ""))

    def rename_panel(self, i):
        sh = self.show_data
        if i <= 0 or i >= len(sh.lists):
            return
        name, ok = QInputDialog.getText(self, tr("Rinomina PANEL"), tr("Nome:"), text=sh.lists[i].name)
        name = name.strip()
        if ok and name and name != sh.lists[i].name:
            if any(l.name.lower() == name.lower() for l in sh.lists):
                QMessageBox.warning(self, tr("Rinomina PANEL"), tr("Esiste già una lista «{0}».", name))
                return
            sh.lists[i].name = name
            sh.dirty = True
            self._update_standby()

    def delete_panel(self, i):
        sh = self.show_data
        if i <= 0 or i >= len(sh.lists):
            return
        lst = sh.lists[i]
        if lst.cues and QMessageBox.question(
                self, tr("Elimina PANEL"), tr("Eliminare «{0}» con le sue {1} cue?", lst.name, len(lst.cues))) != QMessageBox.Yes:
            return
        for c in lst.cues:
            self.ctrl.stop_cue(c.id, 0)
        del sh.lists[i]
        self.ctrl.playheads.pop(lst.id, None)
        sh.dirty = True
        self.show_list(min(sh.view if sh.view < i else sh.view - 1, len(sh.lists) - 1) if sh.view != i else i - 1)

    def move_panel(self, i, d):
        sh = self.show_data
        j = i + d
        if i <= 0 or j <= 0 or j >= len(sh.lists):
            return
        cur = sh.lists[sh.view]
        sh.lists[i], sh.lists[j] = sh.lists[j], sh.lists[i]
        sh.dirty = True
        self.show_list(sh.lists.index(cur))

    def _fill_move_menu(self, menu):
        menu.clear()
        sh = self.show_data
        rows = self._selected_rows()
        for i, lst in enumerate(sh.lists):
            if i == sh.view:
                continue
            a = menu.addAction(lst.name, lambda i=i: self.move_cues_to(i))
            a.setEnabled(bool(rows))
        menu.addSeparator()
        a = menu.addAction(tr("Nuovo PANEL…"), lambda: self.move_cues_to(None))
        a.setEnabled(bool(rows))

    def move_cues_to(self, i):
        """Sposta le cue selezionate in fondo a un'altra lista (None = in un nuovo PANEL). Un gruppo si porta
        dietro tutte le sue cue."""
        from ..groups import block_end
        from .. import grid as G
        sh = self.show_data
        rows = self._selected_rows()
        if not rows:
            return
        full = set(rows)
        for r in rows:
            if sh.cues[r].type == "group":
                full.update(range(r, block_end(sh.cues, r)))
        rows = sorted(full)
        if i is None:
            sh.add_panel()
            i = len(sh.lists) - 1
        dest = sh.lists[i]
        moving = [sh.cues[r] for r in rows]
        moved_ids = {c.id for c in moving}
        self.model.beginResetModel()
        for r in reversed(rows):
            del sh.cues[r]
        for c in moving:
            if c.group and c.group not in moved_ids:       # il suo gruppo resta qui: esce dal gruppo
                c.group = ""
            c.grid_pos = []
        dest.cues.extend(moving)
        self.model.endResetModel()
        if dest.is_grid:
            G.ensure_positions(dest)
            G.sort_cues(dest)
        self._structure_changed()
        sh.dirty = True
        self.ctrl.set_playhead(min(rows[0], len(sh.cues)))
        self._select_row(min(rows[0], len(sh.cues) - 1))
        self.status(tr("{0} cue spostate in «{1}»", len(moving), dest.name))
        self._update_standby()

    def _clone_block(self, block):
        """Copie di una cue (o di un gruppo con le sue cue), con id e numeri nuovi."""
        clones, idmap = [], {}
        for c in block:
            k = c.clone()
            idmap[c.id] = k.id
            k.number = ""
            k.grid_pos = []
            k.hotkey = ""                        # la scorciatoia resta all'originale
            clones.append(k)
        for k in clones:
            if k.group in idmap:
                k.group = idmap[k.group]
                if k.type == "groupexit":
                    k.target = k.group
        n = self.show_data.next_number()
        try:
            for j, k in enumerate(clones):
                k.number = str(int(n) + j)
        except ValueError:
            pass
        return clones

    def move_cue_to(self, cue, dest_i=None, group=None, cell=None, copy=False):
        """Sposta una cue in un'altra lista (dest_i) o dentro un gruppo (group, di qualsiasi lista). Un gruppo si
        porta dietro tutte le sue cue; un gruppo non entra in un altro gruppo. In un PANEL GRID la cue va nella
        casella «cell» se è libera, altrimenti nella prima libera. Con copy=True sposta una copia (l'originale
        resta dov'è). Restituisce la cue spostata (o la copia), None se non è cambiato nulla."""
        from ..groups import block_end
        from .. import grid as G
        sh = self.show_data
        src, i = sh.list_of(cue.id)
        if src is None:
            return None
        if group is not None:
            if cue.type == "group" or group is cue:
                self.status(tr("Un gruppo non può entrare in un altro gruppo"))
                return None
            if cue.group == group.id and not copy:
                return None
            dest, _gi = sh.list_of(group.id)
        else:
            dest = sh.lists[dest_i] if dest_i is not None else src
            if dest is src and not cue.group and not copy:
                return None
        if dest is None:
            return None
        block = src.cues[i:block_end(src.cues, i)] if cue.type == "group" else [cue]
        ph = self.ctrl.playhead_of(src)
        ph_cue = src.cues[ph] if 0 <= ph < len(src.cues) else None
        self.model.beginResetModel()
        if copy:
            block = self._clone_block(block)
            cue = block[0]
        else:
            for c in block:
                src.cues.remove(c)
        for c in block:
            c.grid_pos = []
        if group is not None:
            cue.group = group.id
            hi = dest.cues.index(group)
            dest.cues.insert(block_end(dest.cues, hi), cue)
        else:
            if cue.type != "group":
                cue.group = ""
            if cell is not None and dest.is_grid and G.cell_cue(dest, *cell) is None:
                cue.grid_pos = list(cell)
            dest.cues.extend(block)
        self.model.endResetModel()
        for lst in {id(src): src, id(dest): dest}.values():
            if lst.is_grid:
                G.ensure_positions(lst)
                G.sort_cues(lst)
        self._structure_changed()
        if src is not dest:
            if ph_cue is not None and ph_cue in src.cues:
                self.ctrl.set_playhead(src.index_of(ph_cue.id), src)
            elif src.cues:
                self.ctrl.set_playhead(min(i, len(src.cues) - 1), src)
        if self._grid_list() is not None:
            self._grid_sync(force=True)
        self._update_tabs()
        pre = tr("Copia: ") if copy else ""
        if group is not None:
            self.status(pre + tr("«{0}» ora è nel gruppo «{1}»", cue.label(), group.label()))
        elif cue.type == "group":
            self.status(pre + tr("Gruppo «{0}» spostato in «{1}» con le sue {2} cue", cue.label(), dest.name, len(block) - 1))
        else:
            self.status(pre + tr("«{0}» spostata in «{1}»", cue.label(), dest.name))
        return cue

    def drop_cues(self, ids, dest_i, row=None, cell=None, group=None, copy=False):
        """Cue trascinate (anche da un'altra lista) nella lista dest_i: alla riga «row» di una lista, nella
        casella «cell» (o nelle prime libere) di un GRID, oppure dentro «group». Un gruppo si porta dietro le
        sue cue; con copy=True si spostano delle copie."""
        sh = self.show_data
        if not (0 <= dest_i < len(sh.lists)):
            return
        cues = [c for c in (sh.by_id(i) for i in ids) if c is not None]
        idset = {c.id for c in cues}
        cues = [c for c in cues if not (c.group and c.group in idset)]   # i membri seguono il loro gruppo
        dest = sh.lists[dest_i]
        moved = []
        for k, c in enumerate(cues):
            if group is not None and c.type != "group":
                m = self.move_cue_to(c, group=group, copy=copy)
            else:
                m = self.move_cue_to(c, dest_i, cell=cell if k == 0 else None, copy=copy)
            if m:
                moved.append(m)
        if row is not None and not dest.is_grid and group is None and moved and dest is sh.current:
            # dalla fine della lista alla riga di rilascio (dentro un gruppo le cue ne diventano membri)
            from ..groups import block_end
            r = int(row)
            for m in moved:
                i = dest.index_of(m.id)
                if i < 0:
                    continue
                blk = list(range(i, block_end(dest.cues, i)))
                self._rows_moved(blk, r)
                r += len(blk)
            self._select_rows([dest.index_of(m.id) for m in moved if dest.index_of(m.id) >= 0])
        if len(moved) > 1:
            self.status((tr("Copia: ") if copy else "") + tr("{0} cue spostate in «{1}»", len(moved), dest.name))

    def _new_cue_menu(self, menu):
        from ..model import CUE_TYPES, TYPE_ICONS, TYPE_LABELS
        for t in CUE_TYPES:
            if t != "groupexit":
                menu.addAction(f"{TYPE_ICONS.get(t, '')}  {TYPE_LABELS[t]}", lambda _=False, t=t: self.add_cue(t))

    def eventFilter(self, obj, ev):
        """Doppio clic nello spazio vuoto della lista: menu per aggiungere una cue in fondo."""
        if hasattr(self, "table") and obj is self.table.viewport() and ev.type() == QEvent.MouseButtonDblClick:
            if not self.table.indexAt(ev.position().toPoint()).isValid():
                self.table.clearSelection()
                m = QMenu(self)
                m.addSection(tr("Nuova cue in fondo alla lista"))
                self._new_cue_menu(m)
                m.exec(ev.globalPosition().toPoint())
                return True
        return super().eventFilter(obj, ev)

    def _table_menu(self, pos):
        m = QMenu(self)
        if not self.table.indexAt(pos).isValid():     # clic nello spazio vuoto: si aggiunge in fondo
            self.table.clearSelection()
        rows = self._selected_rows()
        self._new_cue_menu(m.addMenu(tr("Nuova cue") + (tr(" dopo la selezione") if rows else tr(" in fondo"))))
        m.addSeparator()
        if rows:
            m.addAction(tr("Duplica"), self.duplicate)
            m.addAction(tr("Elimina"), self.delete_cues)
            sub = m.addMenu(tr("Sposta in"))
            self._fill_move_menu(sub)
            m.addSeparator()
            m.addAction(tr("Raggruppa le selezionate"), self.group_selected)
            cur = self._current_cue()
            if cur is not None and (cur.type == "group" or cur.group):
                m.addAction(tr("Espandi / compatta gruppo"), lambda: self.toggle_group())
                if cur.group:
                    m.addAction(tr("Togli dal gruppo"), self.remove_from_group)
                m.addAction(tr("Sciogli il gruppo"), self.ungroup_selected)
            m.addSeparator()
        m.addAction(tr("Nuovo PANEL"), self.add_panel)
        m.exec(self.table.viewport().mapToGlobal(pos))
