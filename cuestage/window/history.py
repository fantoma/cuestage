"""Cronologia (annulla / ripeti / torna a un punto) e versioni dello show (parte di MainWindow)."""
from ..i18n import tr

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from ..history import History

CAPTURE_MS = 450          # pausa dopo l'ultima modifica prima di fissare il passo (unisce digitazione e trascinamenti)


class HistoryMixin:
    # ------------------------------------------------------------------ cronologia
    def _history_init(self):
        self.history = History()
        self._hist_restoring = False
        self._hist_bg = False              # modifica arrivata da un altro thread: la riprende _ui_tick
        self._hist_dialog = None
        self._ver_dialog = None
        self._hist_timer = QTimer(self)
        self._hist_timer.setSingleShot(True)
        self._hist_timer.timeout.connect(self._history_tick)

    def _history_reset(self, desc=None):
        sh = self.show_data
        sh.on_change = self._history_touch
        self._hist_timer.stop()
        self.history.reset(sh, desc or (tr("Show aperto") if sh.path else tr("Nuovo show")), saved=not sh.dirty)
        self._history_changed()

    def _history_touch(self):
        if self._hist_restoring:
            return
        if QThread.currentThread() is not self.thread():
            self._hist_bg = True
            return
        self._hist_timer.start(CAPTURE_MS)

    def _history_tick(self):
        if QApplication.mouseButtons():            # trascinamento in corso: un solo passo alla fine
            self._hist_timer.start(CAPTURE_MS)
            return
        self._history_capture()

    def _history_capture(self, desc=None):
        self._hist_timer.stop()
        if self._hist_restoring:
            return None
        try:
            step = self.history.capture(self.show_data, desc)
        except Exception as e:              # la cronologia non deve mai bloccare il lavoro
            self.status(tr("Cronologia: {0}", e))
            return None
        if step is not None:
            self._history_changed()
        return step

    def _history_flush(self):
        """Fissa subito la modifica in sospeso (prima di annullare, salvare, cambiare versione…)."""
        if self._hist_timer.isActive():
            self._history_capture()

    def _history_saved(self):
        self._history_flush()
        self.history.mark_saved()
        self._history_changed()

    def _history_allowed(self):
        hub = getattr(self, "cluster", None)
        return not (hub is not None and hub.role == "slave" and not hub.can_edit)

    def _history_changed(self):
        h = self.history
        ok = self._history_allowed()
        if hasattr(self, "act_undo"):
            self.act_undo.setEnabled(ok and h.can_undo())
            self.act_redo.setEnabled(ok and h.can_redo())
            self.act_undo.setText(tr("Annulla: {0}", h.undo_text()) if h.can_undo() else tr("Annulla"))
            self.act_redo.setText(tr("Ripeti: {0}", h.redo_text()) if h.can_redo() else tr("Ripeti"))
        if self._hist_dialog is not None and self._hist_dialog.isVisible():
            self._hist_dialog.refresh()
        if self._ver_dialog is not None and self._ver_dialog.isVisible():
            self._ver_dialog.refresh()
        self._update_version_btn()

    def undo(self):
        self._history_flush()
        if self.history.can_undo():
            desc = self.history.undo_text()
            self._history_go(self.history.index - 1)
            self.status(tr("Annullato: {0}", desc))

    def redo(self):
        self._history_flush()
        if self.history.can_redo():
            self._history_go(self.history.index + 1)
            self.status(tr("Ripetuto: {0}", self.history.current.desc))

    def history_go(self, i):
        """Torna al passo i della cronologia (i passi dopo restano disponibili con Ripeti)."""
        self._history_flush()
        if i != self.history.index:
            self._history_go(i)
            self.status(tr("Show riportato a: {0}", self.history.current.desc))

    def _history_go(self, i):
        if not self._history_allowed():
            return
        sh = self.show_data
        sel_ids = [sh.cues[r].id for r in self._selected_rows() if 0 <= r < len(sh.cues)]
        insp = self.inspector.cue.id if getattr(self.inspector, "cue", None) is not None else None
        old = dict(sh.settings)
        self._hist_restoring = True
        try:
            settings_changed = self.history.restore(sh, i)
            self._after_replace(sel_ids, insp)
            if settings_changed:
                self._apply_settings(old=old)
        finally:
            self._hist_restoring = False
        sh._dirty = self.history.index != self.history.saved
        self._update_title()
        self._history_changed()

    def _after_replace(self, sel_ids=(), insp_id=None):
        """Aggiorna lista, griglia, schede e ispettore dopo che liste e cue sono state sostituite."""
        sh = self.show_data
        self.model.set_show(sh)
        for c in sh.all_cues():
            self._preload(c)
        self._apply_collapse()
        lst = sh.current
        if lst.is_grid:
            self.grid.set_list(lst)
            self._grid_sync(force=True)
        self._apply_view_kind()
        rows = [sh.index_of(cid) for cid in sel_ids]
        rows = [r for r in rows if r >= 0]
        self._select_guard = True
        if rows:
            self._select_rows(rows)
        else:
            self.table.clearSelection()
        self._select_guard = False
        cue = sh.by_id(insp_id) if insp_id else None
        self.inspector.set_cue(cue)
        if cue is not None:
            self.inspector.refresh_fields()
        self._update_tabs()
        self._update_standby()

    def history_dialog(self):
        self._history_flush()
        from ..ui_history import HistoryDialog
        if self._hist_dialog is None:
            self._hist_dialog = HistoryDialog(self)
        self._hist_dialog.refresh()
        self._hist_dialog.show()
        self._hist_dialog.raise_()
        self._hist_dialog.activateWindow()

    def history_mark(self, i):
        step = self.history.steps[i]
        name, ok = QInputDialog.getText(self, tr("Segna punto"), tr("Nome del punto (es. «prima della prova generale»):"),
                                        text=step.mark)
        if ok:
            step.mark = name.strip()
            self._history_changed()

    def history_to_version(self, i):
        """Nuova versione con lo show com'era al passo i (lo show attuale non cambia)."""
        import time as _t
        step = self.history.steps[i]
        name, ok = QInputDialog.getText(
            self, tr("Nuova versione"), tr("Nome della versione:"),
            text=step.mark or tr("Dalla cronologia {0}", _t.strftime("%H:%M:%S", _t.localtime(step.time))))
        if not ok or not name.strip():
            return
        v = self.show_data.add_version(name.strip(), lists=self.history.lists_at(i),
                                       notes=tr("Creata dalla cronologia: {0}", step.desc))
        self._history_capture(tr("Nuova versione «{0}» dalla cronologia", v.name))
        self._update_title()
        self.status(tr("Creata la versione «{0}»: aprila da File › Versioni dello show", v.name))

    # ------------------------------------------------------------------ versioni
    def versions_dialog(self):
        self._history_flush()
        from ..ui_history import VersionsDialog
        if self._ver_dialog is None:
            self._ver_dialog = VersionsDialog(self)
        self._ver_dialog.refresh()
        self._ver_dialog.show()
        self._ver_dialog.raise_()
        self._ver_dialog.activateWindow()

    def new_version(self, source=None):
        sh = self.show_data
        src = sh.version_by_id(source) if source else sh.active
        name, ok = QInputDialog.getText(self, tr("Nuova versione"),
                                        tr("Nome della nuova versione (copia di «{0}»):", src.name),
                                        text=tr("{0} – copia", src.name))
        if not ok or not name.strip():
            return None
        self._history_flush()
        v = sh.add_version(name.strip(), source=src.id)
        self._history_capture(tr("Nuova versione «{0}»", v.name))
        if QMessageBox.question(self, tr("Nuova versione"), tr("Passare subito alla versione «{0}»?", v.name),
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes:
            self.switch_version(v.id)
        self._update_title()
        return v

    def switch_version(self, vid):
        sh = self.show_data
        if vid == sh.active_version or sh.version_by_id(vid) is None:
            return False
        self._history_flush()
        if self.table.state() == self.table.State.EditingState:
            self.table.closePersistentEditor(self.table.currentIndex())
        self._hist_restoring = True
        try:
            sh.switch_version(vid)
            self._after_replace()
        finally:
            self._hist_restoring = False
        self._history_capture()
        self._update_title()
        running = tr(" (le cue in esecuzione continuano)") if self.ctrl.instances else ""
        self.status(tr("Versione attiva: «{0}»", sh.active.name) + running)
        return True

    def delete_version(self, vid):
        sh = self.show_data
        v = sh.version_by_id(vid)
        if v is None or vid == sh.active_version:
            return False
        if QMessageBox.question(self, tr("Elimina versione"),
                                tr("Eliminare la versione «{0}»?\n\nSi può recuperare con Annulla (Ctrl+Z) finché lo show è aperto.", v.name)) != QMessageBox.Yes:
            return False
        self._history_flush()
        sh.remove_version(vid)
        self._history_capture(tr("Eliminata la versione «{0}»", v.name))
        self._update_title()
        return True

    def _update_version_btn(self):
        btn = getattr(self, "version_btn", None)
        if btn is None:
            return
        sh = self.show_data
        many = len(sh.versions) > 1
        btn.setVisible(many)
        if many:
            btn.setText(tr("Versione: {0}", sh.active.name))
            btn.setToolTip(tr("{0} versioni in questo show · clic: versioni e annotazioni", len(sh.versions)))
