"""Proiettori, mixer e schede audio: pannelli e configurazione (parte di MainWindow)."""
from ..i18n import tr
import traceback


FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


class HardwareMixin:
    def _set_mixers(self, lst):
        self.mixers.set_mixers(lst)
        self.mixers.load_names(self.show_data.settings.get("mixer_names") or {})
        if not hasattr(self, "mix_panel"):
            return
        has = bool(self.mixers.mixers)
        self.mix_btn.setVisible(has)
        try:
            self.mix_panel.rebuild()
        except Exception as e:
            traceback.print_exc()
            self.status(tr("✘ Pannello mixer: {0}", e))
        if not has:
            self.mix_panel.hide()

    def _mixer_names_changed(self, mid):
        """Nomi dei canali letti dal mixer: salvati nello show (visibili anche a mixer spento)."""
        names = self.show_data.settings.setdefault("mixer_names", {})
        if names.get(mid) != self.mixers.names.get(mid):
            names[mid] = self.mixers.names.get(mid)
            self.show_data.dirty = True
        cue = getattr(self.inspector, "cue", None)
        if cue is not None and cue.type == "mixer" and cue.mix_target == mid:
            self.inspector.rebuild()

    def _mixer_controls_changed(self, mid):
        self.model.refresh()
        cue = getattr(self.inspector, "cue", None)
        if cue is not None and cue.type == "mixer" and cue.mix_target == mid and cue.mix_action == "control":
            self.inspector.rebuild()

    def mixers_dialog(self):
        from ..ui_mixers import MixersDialog
        d = MixersDialog(self.show_data.settings.get("mixers", []), self.mixers, self)
        if d.exec():
            self.show_data.settings["mixers"] = d.result_mixers()
            self._set_mixers(self.show_data.settings["mixers"])
            self.show_data.dirty = True
            self.model.refresh()
            self.inspector.rebuild()

    def toggle_mixer_panel(self):
        if not self.mixers.mixers:
            self.mixers_dialog()
            return
        self.mix_btn.setVisible(True)
        self.mix_btn.setChecked(not self.mix_panel.isVisible())

    def _toggle_mix_panel(self, on):
        if on and not self.mix_panel.isVisible():
            self.mix_panel.popup_under(self.mix_btn)
        elif not on and self.mix_panel.isVisible():
            self.mix_panel.hide()

    def _mixer_done(self, name, action, ok, msg):
        if not action:
            return
        text = tr("Mixer «{0}» – {1}: {2}", name, action, msg)
        self.status(("✔ " if ok else "✘ ") + text)
        if not ok:
            print(text)

    def projectors_dialog(self):
        from ..ui_projectors import ProjectorsDialog
        d = ProjectorsDialog(self.show_data.settings.get("projectors", []), self.projectors, self)
        if d.exec():
            self.show_data.settings["projectors"] = d.result_projectors()
            self._set_projectors(self.show_data.settings["projectors"])
            self.show_data.dirty = True
            self.model.refresh()
            self.inspector.rebuild()

    def _set_projectors(self, lst):
        self.projectors.set_projectors(lst)
        if not hasattr(self, "proj_panel"):
            return
        has = bool(self.projectors.projectors)
        self.proj_btn.setVisible(has)          # prima di costruire il pannello: un errore non lo nasconde
        try:
            self.proj_panel.rebuild()
        except Exception as e:
            traceback.print_exc()
            self.status(tr("✘ Pannello proiettori: errore nella costruzione ({0})", e))
        if not has:
            self.proj_panel.hide()

    def toggle_projector_panel(self):
        """Menu / F9: apre o chiude il pannello; senza proiettori apre la configurazione."""
        if not self.projectors.projectors:
            self.projectors_dialog()
            return
        self.proj_btn.setVisible(True)
        self.proj_btn.setChecked(not self.proj_panel.isVisible())

    def _toggle_proj_panel(self, on):
        try:
            if on and not self.proj_panel.isVisible():
                self.proj_panel.popup_under(self.proj_btn if self.proj_btn.isVisible() else self.go_btn)
            elif not on and self.proj_panel.isVisible():
                self.proj_panel.hide()
        except Exception as e:
            traceback.print_exc()
            self.status(tr("✘ Pannello proiettori: {0}", e))

    def _projector_done(self, name, action, ok, msg):
        text = tr("Proiettore «{0}» – {1}: {2}", name, action, msg)
        self.status(("✔ " if ok else "✘ ") + text)
        if not ok:
            print(text)
