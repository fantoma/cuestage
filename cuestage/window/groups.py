"""Gruppi di cue: raggruppa, espandi/compatta, cue di uscita (parte di MainWindow)."""


from ..model import (
    Cue, )

FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


class GroupsMixin:
    def _table_clicked(self, idx):
        from ..ui_cuelist import C_TYPE
        cues = self.show_data.cues
        r = idx.row()
        if idx.column() == C_TYPE and 0 <= r < len(cues) and cues[r].type == "group":
            self.toggle_group(r)
            return
        self.ctrl.set_playhead(r)

    def _apply_collapse(self):
        """Nasconde le righe dei gruppi compattati."""
        cues = self.show_data.cues
        hdr = {c.id: c for c in cues if c.type == "group"}
        for r, c in enumerate(cues):
            g = hdr.get(c.group) if c.group else None
            self.table.setRowHidden(r, bool(g is not None and g.collapsed))

    def _structure_changed(self, select=None):
        from ..groups import normalize
        for lst in self.show_data.lists:
            normalize(lst.cues)
        self._ensure_exit_cues()
        self.model.beginResetModel()
        self.model.endResetModel()
        self._apply_collapse()
        self.show_data.dirty = True
        if select is not None:
            self._select_rows(select)
        self._update_standby()

    def _ensure_exit_cues(self):
        """Un random loop avanzato col GO ha in coda la cue «Esci dal gruppo» (richiamabile anche da altri
        PANEL, via MIDI o OSC)."""
        from ..groups import block_end
        for lst in self.show_data.lists:
            cues = lst.cues
            for i, c in enumerate(list(cues)):
                if c.type != "group" or c.group_mode != "rloop" or c.group_advance != "go":
                    continue
                i = cues.index(c)
                end = block_end(cues, i)
                if any(x.type == "groupexit" for x in cues[i + 1:end]):
                    continue
                ex = Cue("groupexit", number=self.show_data.next_number(),
                         name=f"Esci da «{c.name or 'Gruppo'}»", target=c.number or c.id, group=c.id)
                cues.insert(end, ex)

    def toggle_group(self, r=None):
        cues = self.show_data.cues
        if r is None:
            r = self._current_row()
        if not (0 <= r < len(cues)):
            return
        c = cues[r]
        if c.type != "group":
            i = next((k for k, x in enumerate(cues) if x.id == c.group), -1)
            if i < 0:
                return
            r, c = i, cues[i]
        c.collapsed = not c.collapsed
        self.show_data.dirty = True
        self._apply_collapse()
        self.model.refresh()
        if c.collapsed:
            self._select_rows([r])

    def group_selected(self):
        """Raggruppa le cue selezionate in un nuovo gruppo (Sequence)."""
        cues = self.show_data.cues
        rows = [r for r in self._selected_rows() if cues[r].type != "group"]
        if not rows:
            self.status("Seleziona le cue da raggruppare (non gruppi)")
            return
        moving = [cues[r] for r in rows]
        g = Cue("group", number=self.show_data.next_number(), name="Gruppo")
        first = rows[0]
        rest = [c for i, c in enumerate(cues) if i not in rows]
        pos = sum(1 for i in range(first) if i not in rows)
        for c in moving:
            c.group = g.id
        self.model.beginResetModel()
        cues[:] = rest[:pos] + [g] + moving + rest[pos:]
        self.model.endResetModel()
        self._structure_changed(select=[pos])
        self.status(f"Creato un gruppo con {len(moving)} cue (tipo nella scheda «Gruppo» dell'ispettore)")

    def ungroup_selected(self):
        """Scioglie il gruppo selezionato (le cue restano al loro posto)."""
        cues = self.show_data.cues
        r = self._current_row()
        if not (0 <= r < len(cues)):
            return
        c = cues[r]
        hdr = c if c.type == "group" else self.show_data.by_id(c.group) if c.group else None
        if hdr is None:
            return
        i = cues.index(hdr)
        self.ctrl.stop_cue(hdr.id, 0)
        for x in cues:
            if x.group == hdr.id:
                x.group = ""
        self.model.beginResetModel()
        del cues[i]
        cues[:] = [x for x in cues if not (x.type == "groupexit" and (x.target in (hdr.number, hdr.id)))]
        self.model.endResetModel()
        self._structure_changed(select=[min(i, len(cues) - 1)])

    def remove_from_group(self):
        """Toglie le cue selezionate dal loro gruppo (vanno subito dopo il gruppo)."""
        from ..groups import block_end
        cues = self.show_data.cues
        sel = [cues[r] for r in self._selected_rows() if cues[r].group]
        if not sel:
            return
        self.model.beginResetModel()
        for c in sel:
            hi = next((k for k, x in enumerate(cues) if x.id == c.group), -1)
            end = block_end(cues, hi) if hi >= 0 else len(cues)
            cues.remove(c)
            c.group = ""
            cues.insert(end - 1, c)
        self.model.endResetModel()
        self._structure_changed(select=[cues.index(c) for c in sel])
