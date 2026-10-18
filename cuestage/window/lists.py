"""Liste MAIN e PANEL: schede, GO su, spostamento tra liste (parte di MainWindow)."""

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
        self.status("GO agisce sempre sulla MAIN" if key == "main" else "GO segue la lista selezionata")

    def add_panel(self, kind="list"):
        if kind not in ("list", "grid"):        # dal segnale «clicked» del pulsante arriva un booleano
            kind = "list"
        lst = self.show_data.add_panel(kind=kind)
        if kind == "grid":
            lst.name = lst.name.replace("PANEL", "GRID")
        self.show_list(len(self.show_data.lists) - 1)
        self.status(f"Creato «{lst.name}» (doppio clic sul tab per rinominarlo)"
                    + (" · ✎ Modifica per righe, colonne e pagine" if kind == "grid" else ""))

    def rename_panel(self, i):
        sh = self.show_data
        if i <= 0 or i >= len(sh.lists):
            return
        name, ok = QInputDialog.getText(self, "Rinomina PANEL", "Nome:", text=sh.lists[i].name)
        name = name.strip()
        if ok and name and name != sh.lists[i].name:
            if any(l.name.lower() == name.lower() for l in sh.lists):
                QMessageBox.warning(self, "Rinomina PANEL", f"Esiste già una lista «{name}».")
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
                self, "Elimina PANEL", f"Eliminare «{lst.name}» con le sue {len(lst.cues)} cue?") != QMessageBox.Yes:
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
        a = menu.addAction("Nuovo PANEL…", lambda: self.move_cues_to(None))
        a.setEnabled(bool(rows))

    def move_cues_to(self, i):
        """Sposta le cue selezionate in fondo a un'altra lista (None = in un nuovo PANEL)."""
        sh = self.show_data
        rows = self._selected_rows()
        if not rows:
            return
        if i is None:
            sh.add_panel()
            i = len(sh.lists) - 1
        dest = sh.lists[i]
        moving = [sh.cues[r] for r in rows]
        self.model.beginResetModel()
        for r in reversed(rows):
            del sh.cues[r]
        dest.cues.extend(moving)
        self.model.endResetModel()
        sh.dirty = True
        self.ctrl.set_playhead(min(rows[0], len(sh.cues)))
        self._select_row(min(rows[0], len(sh.cues) - 1))
        self.status(f"{len(moving)} cue spostate in «{dest.name}»")
        self._update_standby()

    def _table_menu(self, pos):
        m = QMenu(self)
        rows = self._selected_rows()
        if rows:
            m.addAction("Duplica", self.duplicate)
            m.addAction("Elimina", self.delete_cues)
            sub = m.addMenu("Sposta in")
            self._fill_move_menu(sub)
            m.addSeparator()
            m.addAction("Raggruppa le selezionate", self.group_selected)
            cur = self._current_cue()
            if cur is not None and (cur.type == "group" or cur.group):
                m.addAction("Espandi / compatta gruppo", lambda: self.toggle_group())
                if cur.group:
                    m.addAction("Togli dal gruppo", self.remove_from_group)
                m.addAction("Sciogli il gruppo", self.ungroup_selected)
            m.addSeparator()
        m.addAction("Nuovo PANEL", self.add_panel)
        m.exec(self.table.viewport().mapToGlobal(pos))
