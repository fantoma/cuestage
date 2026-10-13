"""File dello show: nuovo, apri, salva, ultimo progetto, salvataggio automatico e recupero (parte di MainWindow)."""
import os
import time

from PySide6.QtWidgets import (
    QFileDialog, QMessageBox, )

from ..system import data_dir
from ..model import (
    Show, )

FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


class FilesMixin:
    @staticmethod
    def _autosave_path(show_path):
        """File nascosto di autosave: accanto allo show (".nome.cueshow.autosave") oppure, per uno
        show mai salvato, in ~/.local/share/cuestage/."""
        if show_path:
            d, b = os.path.split(os.path.abspath(show_path))
            return os.path.join(d, f".{b}.autosave")
        d = data_dir()
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, ".senza-titolo.cueshow.autosave")

    def _autosave(self):
        if not self.show_data.dirty:
            return
        try:
            self._corners_changed_silent()
            self.show_data.save(self._autosave_path(self.show_data.path), copy_only=True)
        except OSError as e:
            # cartella non scrivibile: ripiega sulla cartella dell'applicazione
            try:
                self.show_data.save(self._autosave_path(None), copy_only=True)
            except OSError:
                self.status(f"Autosave non riuscito: {e}")

    def _remove_autosave(self, show_path="__current__"):
        paths = {self._autosave_path(None)}
        if show_path == "__current__":
            show_path = self.show_data.path
        if show_path:
            paths.add(self._autosave_path(show_path))
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass

    def _startup_open(self, path=None):
        target = path or self.qs.value("last_show", "") or None
        if self._offer_recovery(target):
            return
        if target and os.path.exists(target):
            self.open_show(target)

    def _offer_recovery(self, show_path):
        """Se esiste un autosave (= l'ultima sessione non si è chiusa correttamente) propone il recupero."""
        cands = [p for p in ({self._autosave_path(show_path)} if show_path else set())
                 | {self._autosave_path(None)} if os.path.exists(p)]
        if not cands:
            return False
        auto = max(cands, key=os.path.getmtime)
        when = time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(os.path.getmtime(auto)))
        try:
            rec = Show.load(auto)
        except Exception:
            self._discard_file(auto)
            return False
        orig = getattr(rec, "original_path", None)
        name = os.path.basename(orig) if orig else "show senza titolo"
        r = QMessageBox.question(
            self, "Recupero dopo chiusura imprevista",
            f"CueStage non è stato chiuso correttamente.\n\nÈ disponibile un salvataggio automatico di "
            f"«{name}» del {when}, con modifiche non salvate.\n\nRecuperarlo?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            self._discard_file(auto)
            return False
        rec.path = orig if orig and os.path.isdir(os.path.dirname(orig)) else None
        rec.dirty = True
        self._set_show(rec)
        self.status("Show recuperato dal salvataggio automatico: salvalo per conservarlo")
        return True

    @staticmethod
    def _discard_file(p):
        try:
            os.remove(p)
        except OSError:
            pass

    def _remember_last(self, path):
        if path:
            self.qs.setValue("last_show", os.path.abspath(path))

    def _confirm_discard(self):
        if not self.show_data.dirty or not self.show_data.cues:
            return True
        r = QMessageBox.question(self, "Modifiche non salvate", "Salvare lo show corrente?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        if r == QMessageBox.Discard:
            self._remove_autosave()
            return True
        return False

    def new_show(self):
        if not self._confirm_discard():
            return
        self._set_show(Show())

    def _set_show(self, show):
        old = dict(self.show_data.settings)
        self.ctrl.set_show(show)
        self.show_data = show
        self.model.set_show(show)
        self.inspector.set_cue(None)
        self._apply_settings(old=old)
        keep = set()
        for c in show.all_cues():
            self._preload(c)
            if c.file:
                keep.add(c.file)
        self.loader.forget(keep)
        self._open_cue_outputs()
        self._apply_collapse()
        self._select_row(0)
        self._update_standby()
        self._update_title()

    def open_dialog(self):
        if not self._confirm_discard():
            return
        start = self.qs.value("last_show_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "Apri show", start, FILE_FILTER)
        if path:
            self.open_show(path)

    def open_show(self, path):
        try:
            show = Show.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Errore", f"Impossibile aprire lo show:\n{e}")
            return
        self.qs.setValue("last_show_dir", os.path.dirname(path))
        self._remember_last(path)
        self._set_show(show)
        missing = [c for c in show.all_cues() if c.is_media() and c.file and not os.path.exists(c.file)]
        if missing:
            QMessageBox.warning(self, "File mancanti",
                                "File non trovati:\n" + "\n".join(c.file for c in missing[:15]))
        self.status(f"Aperto {path}")

    def save(self):
        if not self.show_data.path:
            return self.save_as()
        try:
            self._corners_changed()
            self.show_data.save(self.show_data.path)
        except Exception as e:
            QMessageBox.critical(self, "Errore", f"Salvataggio fallito:\n{e}")
            return False
        self._remove_autosave()        # salvato davvero: l'autosave non serve più
        self._remember_last(self.show_data.path)
        self.status(f"Salvato {self.show_data.path}")
        self._update_title()
        return True

    def save_as(self):
        start = self.show_data.path or os.path.join(
            self.qs.value("last_show_dir", os.path.expanduser("~")), "show.cueshow")
        path, _ = QFileDialog.getSaveFileName(self, "Salva show", start, FILE_FILTER)
        if not path:
            return False
        if not os.path.splitext(path)[1]:
            path += ".cueshow"
        self._remove_autosave()        # autosave legato al vecchio nome
        self.show_data.path = path
        self.qs.setValue("last_show_dir", os.path.dirname(path))
        return self.save()
