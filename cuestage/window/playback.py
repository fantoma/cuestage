"""Riproduzione: GO, stop, pausa, GO/STOP locali dei PANEL, cue in attesa (parte di MainWindow)."""


from ..i18n import tr
from ..model import (
    TYPE_ICONS, )

FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


class PlaybackMixin:
    def go(self):
        if self.learn_dialog is not None:
            return
        self.ctrl.go()
        self._update_standby()

    def stop_all(self):
        if self.ctrl.exit_captured_groups():      # ESC esce prima dai random loop col GO e dai testi
            self.status(tr("Uscita dal gruppo / dal testo (ESC di nuovo = STOP TUTTO)"))
            self._update_standby()
            return
        self.ctrl.stop_all(float(self.show_data.settings.get("stop_fade", 0.3)))

    def panic(self):
        self.ctrl.stop_all(0.0)

    def pause_all(self):
        self.ctrl.pause_all()

    def resume_all(self):
        self.ctrl.resume_all()

    def test_cue(self, cid):
        cue = self.show_data.by_id(cid)
        if cue:
            self.ctrl.start_cue(cue, cascade=False)

    def _toggle_pause_cue(self, cid):
        inst = self.ctrl.instances.get(cid)
        if inst:
            if self.ctrl.paused_all:
                self.ctrl.resume_all()
            else:
                self.ctrl.pause_cue(cid, not inst.paused)

    def local_go_pressed(self):
        if self.learn_dialog is not None:
            return
        lst = self.show_data.current
        if not lst.is_main:
            self.ctrl.go(lst)

    def local_stop_pressed(self):
        if self.learn_dialog is not None:
            return
        lst = self.show_data.current
        if not lst.is_main:
            self.stop_list(lst)

    def stop_list(self, lst):
        fade = float(lst.grid_fade) if lst.is_grid else float(self.show_data.settings.get("stop_fade", 0.3))
        self.ctrl.stop_list(lst, fade)
        self.status(tr("STOP «{0}»", lst.name))

    def _update_local_go(self):
        if not hasattr(self, "local_go"):
            return
        sh = self.show_data
        lst = sh.current
        show = (not lst.is_main) and sh.settings.get("go_target", "main") == "main"
        self.local_go.setVisible(show)
        if not show:
            return
        i = self.ctrl.playhead_of(lst)
        self.local_go_btn.setText(f"GO {lst.name}" if len(lst.name) <= 10 else "GO")
        self.local_stop_btn.setText(f"STOP {lst.name}" if len(lst.name) <= 10 else "STOP")
        self.local_stop_btn.setEnabled(bool(self.ctrl.list_active(lst)))
        if 0 <= i < len(lst.cues):
            c = lst.cues[i]
            self.local_standby.setText(tr("In attesa in «{0}»:  {1}  {2}", lst.name, TYPE_ICONS.get(c.type, ''), c.label()))
            self.local_go_btn.setEnabled(True)
        else:
            self.local_standby.setText(tr("«{0}»: — fine della lista —", lst.name))
            self.local_go_btn.setEnabled(False)

    def _update_standby(self):
        if self.ctrl.show is not self.show_data:
            return
        self._update_local_go()
        lst = self.ctrl.go_list()
        i = self.ctrl.playhead_of(lst)
        cues = lst.cues
        where = f"{lst.name} › " if len(self.show_data.lists) > 1 else ""
        self._update_tabs()
        cap = self.ctrl.captured_group(lst)
        if cap is not None and hasattr(cap, "phrases"):
            nxt = cap.phrases[cap.idx + 1].replace("\n", " / ")
            self.standby.setText(tr("{0}T {1}/{2}: {3}", where, cap.idx + 2, len(cap.phrases), nxt[:60]))
            self.standby_notes.setText(tr("GO = frase successiva · Alt+← precedente · ESC = esci dal testo"))
            self.go_btn.setEnabled(True)
            return
        if cap is not None and hasattr(cap, "pages"):          # cue PDF: GO = pagina successiva
            self.standby.setText(tr("{0}▤ {1}: pagina {2} di {3}", where, cap.cue.label(), cap.idx + 2,
                                    len(cap.pages)))
            self.standby_notes.setText(tr("GO = pagina successiva · Alt+← precedente · ESC = esci dal PDF"))
            self.go_btn.setEnabled(True)
            return
        if cap is not None:
            self.standby.setText(tr("{0}◫ {1}: GO = cue a caso successiva", where, cap.cue.label()))
            self.standby_notes.setText(tr("ESC = esci dal gruppo (poi ESC = STOP TUTTO)"))
            self.go_btn.setEnabled(True)
            return
        if 0 <= i < len(cues):
            c = cues[i]
            self.standby.setText(f"{where}{TYPE_ICONS.get(c.type, '')}  {c.label()}")
            self.standby_notes.setText(c.notes.splitlines()[0] if c.notes else "")
            self.go_btn.setEnabled(True)
        else:
            self.standby.setText(tr("{0}— fine della lista —", where))
            self.standby_notes.setText("")
            self.go_btn.setEnabled(False)

    def _playhead_changed(self, _idx=None):
        idx = self.ctrl.playhead              # playhead della lista visualizzata
        self.model.refresh()
        self._update_standby()
        # la selezione (e quindi l'ispettore) resta sulla cue scelta dall'utente anche quando il playhead si sposta
        # (GO, avanti / indietro, OSC): si può continuare a lavorare sulla cue che suona (marker, «Ricomincia da
        # qui»). Il playhead si vede nella lista e in «In attesa». Solo senza nessuna cue selezionata la selezione
        # va sul playhead.
        if self._select_guard or self._current_cue() is not None:
            return
        self._select_guard = True
        if 0 <= idx < len(self.show_data.cues):
            self._select_rows([idx])
        self._select_guard = False

    def _ctrl_changed(self):
        self.model.refresh()
        self._update_standby()
