"""Modifica dello show: selezione, inserimento, spostamento, duplicazione, ispettore (parte di MainWindow)."""
import os

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QMessageBox, )

from ..model import (
    TYPE_LABELS, Cue, type_for_file,
)

FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


class EditingMixin:
    def _current_row(self):
        rows = self._selected_rows()
        return rows[0] if rows else -1

    def _selected_rows(self):
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def _current_cue(self):
        r = self._current_row()
        return self.show_data.cues[r] if 0 <= r < len(self.show_data.cues) else None

    def _select_row(self, r):
        if 0 <= r < len(self.show_data.cues):
            self._select_rows([r])
        else:
            self.table.clearSelection()
            self.inspector.set_cue(None)

    def _select_rows(self, rows):
        rows = list(rows)
        sm = self.table.selectionModel()
        sm.clearSelection()
        for r in rows:
            sm.select(self.model.index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        if rows:
            sm.setCurrentIndex(self.model.index(rows[0], 1), QItemSelectionModel.NoUpdate)
            self.table.scrollTo(self.model.index(rows[0], 0))

    def _selection_changed(self, *a):
        cue = self._current_cue()
        self.inspector.set_cue(cue)
        if self._select_guard:
            return
        r = self._current_row()
        if r >= 0:
            self._select_guard = True
            self.ctrl.set_playhead(r)
            self._select_guard = False

    def _insert_cues(self, cues, row=None):
        grid = self.show_data.current.is_grid
        if row is None:
            r = self._current_row()
            row = r + 1 if (r >= 0 and not grid) else len(self.show_data.cues)
        from ..groups import membership_for_insert
        # nel GRID ogni nuova cue ha la sua casella (non entra nel gruppo vicino)
        g = "" if grid else membership_for_insert(self.show_data.cues, row)
        self.model.beginResetModel()
        for i, c in enumerate(cues):
            if c.type != "group" and not c.group:
                c.group = g
            self.show_data.cues.insert(row + i, c)
        self.model.endResetModel()
        self.show_data.dirty = True
        for c in cues:
            self._preload(c)
        self._structure_changed()
        self._select_row(row)

    def add_cue(self, ctype, row=None, group=None):
        """Nuova cue (dopo la selezione). row/group: posizione e gruppo imposti (GRID, gruppo aperto)."""
        if ctype in ("audio", "video", "image"):
            from ..model import MEDIA_EXT
            pat = " ".join("*" + e for e in sorted(MEDIA_EXT[ctype]))
            start = self.qs.value("last_dir", os.path.expanduser("~"))
            paths, _ = QFileDialog.getOpenFileNames(self, f"Scegli file {TYPE_LABELS[ctype]}", start,
                                                    f"{TYPE_LABELS[ctype]} ({pat});;Tutti (*)")
            if not paths:
                return
            self.qs.setValue("last_dir", os.path.dirname(paths[0]))
            cues = [self._cue_for_file(p, ctype) for p in paths]
        else:
            c = Cue(ctype, number=self.show_data.next_number())
            cur = self._current_cue()
            if ctype in ("fade", "control", "preset") and cur is not None:
                c.target = cur.number or cur.id
                if ctype == "preset":
                    c.name = f"Cambia preset {cur.name}".strip()
                    c.fade_duration = 3.0
                if ctype == "fade":
                    c.name = f"Fade out {cur.name}".strip()
                    c.stop_when_done = True
            if ctype == "osc":
                c.name = "Messaggio OSC"
            if ctype == "wait":
                c.duration = 5.0
            cues = [c]
        if group is not None:
            for c in cues:
                c.group = group
        self._insert_cues(cues, row)

    def _cue_for_file(self, path, ctype=None):
        ctype = ctype or type_for_file(path) or "audio"
        c = Cue(ctype, number=self.show_data.next_number(),
                name=os.path.splitext(os.path.basename(path))[0], file=path)
        # numerazione progressiva anche per inserimenti multipli
        self.show_data.cues.append(c)
        c.number = self.show_data.next_number()
        self.show_data.cues.remove(c)
        return c

    def _files_dropped(self, paths, row, group=None):
        cues = []
        for p in paths:
            if os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    fp = os.path.join(p, f)
                    if type_for_file(fp):
                        cues.append(fp)
            elif type_for_file(p):
                cues.append(p)
        made = []
        for p in cues:
            c = Cue(type_for_file(p), name=os.path.splitext(os.path.basename(p))[0], file=p)
            made.append(c)
        n = self.show_data.next_number()
        try:
            base = int(n)
        except ValueError:
            base = len(self.show_data.cues) + 1
        for i, c in enumerate(made):
            c.number = str(base + i)
            if group is not None:
                c.group = group
        if made:
            self._insert_cues(made, row)
        else:
            self.status("Nessun file multimediale riconosciuto")

    def _rows_moved(self, rows, dest):
        from ..groups import block_end, membership_for_insert
        cues = self.show_data.cues
        rows = sorted(set(rows))
        for r in list(rows):                      # un gruppo si porta dietro le sue cue
            if 0 <= r < len(cues) and cues[r].type == "group":
                rows += list(range(r + 1, block_end(cues, r)))
        rows = sorted(set(r for r in rows if 0 <= r < len(cues)))
        if any(cues[r].type == "group" for r in rows):
            # niente gruppi dentro i gruppi: se la destinazione cade dentro un gruppo, va dopo di esso
            g = membership_for_insert(cues, dest)
            if g:
                hi = next(k for k, x in enumerate(cues) if x.id == g)
                dest = block_end(cues, hi)
        else:
            g = membership_for_insert(cues, dest)
            gi = next((k for k, x in enumerate(cues) if x.id == g), -1)
            for r in rows:                        # trascinate dentro un gruppo ne diventano membri
                cues[r].group = g if gi not in rows else cues[r].group
        moving = [cues[i] for i in rows if 0 <= i < len(cues)]
        before = sum(1 for i in rows if i < dest)
        rest = [c for i, c in enumerate(cues) if i not in rows]
        dest = max(0, min(len(rest), dest - before))
        self.model.beginResetModel()
        self.show_data.cues[:] = rest[:dest] + moving + rest[dest:]
        self.model.endResetModel()
        self._structure_changed(select=range(dest, dest + len(moving)))

    def move_selected(self, delta):
        rows = self._selected_rows()
        if not rows:
            return
        dest = rows[0] + delta if delta < 0 else rows[-1] + 2
        if dest < 0 or rows[-1] + delta >= len(self.show_data.cues):
            return
        self._rows_moved(rows, dest)

    def duplicate(self):
        rows = self._selected_rows()
        if not rows:
            return
        from ..groups import block_end
        cues = self.show_data.cues
        for r in list(rows):                      # un gruppo si duplica con le sue cue
            if cues[r].type == "group":
                rows += list(range(r + 1, block_end(cues, r)))
        rows = sorted(set(rows))
        clones, idmap = [], {}
        for r in rows:
            c = cues[r].clone()
            idmap[cues[r].id] = c.id
            c.number = ""
            clones.append(c)
        for c in clones:
            if c.group in idmap:
                c.group = idmap[c.group]
                if c.type == "groupexit":       # la copia della cue di uscita chiude la copia del gruppo
                    c.target = c.group
        n = self.show_data.next_number()
        try:
            for i, c in enumerate(clones):
                c.number = str(int(n) + i)
        except ValueError:
            pass
        last = rows[-1]
        if cues[last].group and not any(c.type == "group" for c in clones):
            pass                                   # duplicato di cue di un gruppo: resta nel gruppo
        elif clones and clones[0].type == "group":
            last = block_end(cues, rows[0]) - 1
        self._insert_cues(clones, last + 1)

    def delete_cues(self):
        if self.table.state() == QAbstractItemView.EditingState:
            return
        rows = self._selected_rows()
        if not rows:
            return
        from ..groups import block_end
        cues = self.show_data.cues
        heads = [r for r in rows if cues[r].type == "group"]
        if heads:
            extra = sorted({k for r in heads for k in range(r + 1, block_end(cues, r))} - set(rows))
            if extra and QMessageBox.question(
                    self, "Elimina gruppo", f"Eliminare il gruppo insieme alle sue {len(extra)} cue?\n"
                    "(«No» elimina solo il gruppo: le cue restano nella lista)") == QMessageBox.Yes:
                rows = sorted(set(rows) | set(extra))
        for r in rows:
            self.ctrl.stop_cue(cues[r].id, 0)
        self.model.beginResetModel()
        for r in reversed(rows):
            del cues[r]
        self.model.endResetModel()
        self._structure_changed()
        self._select_row(min(rows[0], len(cues) - 1))

    def renumber(self):
        sh = self.show_data
        prefix = "" if sh.view == 0 else f"P{sh.view}."     # i PANEL non si sovrappongono alla MAIN
        for i, c in enumerate(sh.cues, start=1):
            c.number = f"{prefix}{i}"
        self.show_data.dirty = True
        self.model.refresh()
        self.inspector.rebuild()

    def _visual_size(self, cue):
        """Dimensioni del sorgente di una cue visiva non in riproduzione."""
        if not cue.file:
            return None
        if cue.type == "image":
            img = self.ctrl.image(cue.file)
            return (img.width(), img.height()) if img is not None and not img.isNull() else None
        info = self.loader.get_info(cue.file)
        if info and info.get("width"):
            return info["width"], info["height"]
        return None

    def _preview_edited(self):
        self.show_data.dirty = True
        self.inspector.refresh_fields()

    def _inspector_edited(self, file_changed):
        cue = self.inspector.cue
        if cue is not None and cue.type == "group":
            need = cue.group_mode == "rloop" and cue.group_advance == "go"
            has = any(c.type == "groupexit" and c.group == cue.id for c in self.show_data.cues)
            if need and not has:
                r = self._current_row()
                self._structure_changed(select=[r] if r >= 0 else None)
        if file_changed and cue is not None:
            self._preload(cue)
        self.model.refresh()
        self._update_standby()

    def _model_edited(self):
        self.inspector.refresh_fields()
        self._update_standby()

    def _preload(self, cue):
        if cue.file and cue.is_media():
            self.loader.get_info(cue.file)
            if cue.type == "audio" or (cue.type == "video" and cue.video_audio != "system"):
                self.loader.preload_audio(cue.file)   # i file senza audio non occupano memoria

    def _open_cue_outputs(self):
        """Apre in anticipo le schede usate dalle cue (un'apertura durante lo show darebbe un ritardo)."""
        for c in self.show_data.all_cues():
            if c.type in ("audio", "video") and c.audio_device:
                self.audio.ensure_output(c.audio_device)
        if self.audio.preview.get("enabled"):
            self.audio.ensure_output(self.audio.preview.get("device") or None)
