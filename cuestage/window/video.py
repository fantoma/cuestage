"""Uscita video: finestra, schermi, geometria a 4 angoli (parte di MainWindow)."""

from ..i18n import tr
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication, )

from ..ui_dialogs import GeometryDialog
from ..video_output import make_output

FILE_FILTER = "Show Cuevivo (*.cueshow);;JSON (*.json);;Tutti i file (*)"


class VideoMixin:
    def video_engine_setting(self):
        """Motore video scelto nello show (gli show precedenti avevano solo «OpenGL sì/no»)."""
        s = self.show_data.settings
        e = s.get("output_engine")
        if e:
            return e
        return "auto" if s.get("output_opengl", True) else "raster"

    def _create_output(self, engine=None):
        if self.output is None:
            self.output = make_output(self.comp, engine or self.video_engine_setting())
            self.output.cornersChanged.connect(self._corners_changed)
            fb = getattr(self.output, "fallbackRequested", None)
            if fb is not None:
                fb.connect(self._video_fallback)
            self._build_shortcuts(self.output)

    def _drop_output(self):
        """Chiude la finestra di uscita (per ricrearla con un altro motore). Restituisce se era visibile."""
        out = self.output
        if out is None:
            return False
        vis = out.isVisible()
        out.detach()
        out.close()
        out.deleteLater()
        self.output = None
        return vis

    def _video_fallback(self, reason):
        """L'uscita su GPU non può funzionare (niente GPU, errore della scena): si passa al motore classico."""
        if self.output is None or getattr(self.output, "engine_name", "") != "quick":
            return
        self.ctrl.message.emit(tr("Uscita video: GPU non disponibile ({0}), uso il motore classico", reason))
        vis = self._drop_output()
        from ..video_output import GLOutput, opengl_available
        self._create_output("gl" if (GLOutput is not None and opengl_available()) else "raster")
        if vis:
            self.show_output()

    def set_video_engine(self, engine):
        self.show_data.settings["output_engine"] = engine
        self.show_data.dirty = True
        vis = self._drop_output()
        if vis:
            self.show_output()

    def _out_screen(self):
        """(indice dello schermo di uscita o None = automatico, stato): lo schermo scelto si cerca per identità
        (produttore, modello, numero di serie, porta), non per posizione nell'elenco (vedi screens.py)."""
        from ..screens import resolve
        return resolve(self.show_data.settings, QApplication.screens())

    def _out_qscreen(self):
        screens = QApplication.screens()
        i, _st = self._out_screen()
        return screens[i] if i is not None and 0 <= i < len(screens) else screens[-1]

    def _output_aspect(self):
        if self.output is not None and self.output.isVisible() and self.output.height():
            return self.output.width() / self.output.height()
        scr = self._out_qscreen()
        g = scr.geometry()
        return g.width() / g.height() if g.height() else 16 / 9

    def _output_pixels(self):
        """Risoluzione reale (pixel fisici) dell'uscita: finestra se visibile, altrimenti lo schermo scelto."""
        if self.output is not None and self.output.isVisible() and self.output.width() > 0:
            r = self.output.devicePixelRatioF()
            return int(round(self.output.width() * r)), int(round(self.output.height() * r))
        scr = self._out_qscreen()
        g, r = scr.geometry(), scr.devicePixelRatio()
        return int(round(g.width() * r)), int(round(g.height() * r))

    def _ensure_output_visible(self):
        if self.output is None or not self.output.isVisible():
            self.show_output()

    def show_output(self):
        self._create_output()
        s = self.show_data.settings
        scr, st = self._out_screen()
        self._note_screen_missing(st)
        screens = QApplication.screens()
        full = bool(s.get("output_fullscreen", True))
        if len(screens) == 1 and scr in (None, 0) and full:
            # un solo schermo: evita di coprire la finestra di controllo
            full = False
        self.output.place_on_screen(scr, full)
        self.activateWindow()
        self.act_out_show.setText(tr("Nascondi finestra di uscita"))

    def toggle_output(self):
        if self.output is not None and self.output.isVisible():
            self.output.hide()
            self.act_out_show.setText(tr("Mostra finestra di uscita"))
        else:
            self.show_output()

    def _output_request(self, what):
        """Cue «Schermo intero». Apri: la finestra di uscita si mostra a schermo intero sullo schermo scelto (con un
        solo schermo resta in finestra, per non coprire la regia). Chiudi: esce dallo schermo intero (resta aperta in
        finestra, sullo stesso schermo) oppure si nasconde. L'impostazione «Schermo intero» non cambia."""
        if what == "open":
            self._create_output()
            scr, st = self._out_screen()
            self._note_screen_missing(st)
            screens = QApplication.screens()
            full = not (len(screens) == 1 and scr in (None, 0))
            self.output.place_on_screen(scr, full)
            self.act_out_show.setText(tr("Nascondi finestra di uscita"))
            self.status(tr("Schermo intero aperto") if full else tr("Finestra di uscita aperta (un solo schermo)"))
            return
        out = self.output
        if out is None or not out.isVisible():
            return
        if what == "hide":
            out.hide()
            self.act_out_show.setText(tr("Mostra finestra di uscita"))
        elif out.isFullScreen():
            out.place_on_screen(self._out_screen()[0], False)
        self.status(tr("Schermo intero chiuso") if what != "hide" else tr("Finestra di uscita nascosta"))

    def toggle_fullscreen(self):
        self.show_data.settings["output_fullscreen"] = self.act_full.isChecked()
        self.show_data.dirty = True
        if self.output is not None and self.output.isVisible():
            self.show_output()

    def _fill_screens(self):
        from ..screens import describe, identity
        self.screens_menu.clear()
        grp = QActionGroup(self.screens_menu)
        cur, st = self._out_screen()
        cur = -1 if st == "auto" else cur
        screens = QApplication.screens()
        opts = [(-1, tr("Automatico (ultimo schermo)"))] + [
            (i, tr("{0}: {1}", i + 1, describe(identity(s)))) for i, s in enumerate(screens)]
        for i, label in opts:
            a = QAction(label, self.screens_menu)
            a.setCheckable(True)
            a.setChecked(i == cur)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, i=i: self._set_screen(i))
            self.screens_menu.addAction(a)
        if st == "missing":                       # lo schermo scelto non c'è: si vede quale manca
            ident = self.show_data.settings.get("output_screen_id") or {}
            a = QAction(tr("⚠ {0} — non collegato (uso l'ultimo schermo)", describe(ident) or tr("schermo scelto")),
                        self.screens_menu)
            a.setEnabled(False)
            self.screens_menu.addSeparator()
            self.screens_menu.addAction(a)

    def _set_screen(self, i):
        from ..screens import identity
        s = self.show_data.settings
        screens = QApplication.screens()
        s["output_screen"] = i
        if 0 <= i < len(screens):
            s["output_screen_id"] = identity(screens[i])       # riconosciuto anche se cambia l'ordine
        else:
            s.pop("output_screen_id", None)
        self._screen_missing_noted = False
        self.show_data.dirty = True
        if self.output is not None and self.output.isVisible():
            self.show_output()

    def _note_screen_missing(self, st):
        """Avviso (una volta per mancanza) se lo schermo scelto non è collegato."""
        from ..screens import describe
        if st != "missing":
            self._screen_missing_noted = False
            return
        if getattr(self, "_screen_missing_noted", False):
            return
        self._screen_missing_noted = True
        ident = self.show_data.settings.get("output_screen_id") or {}
        msg = tr("Schermo di uscita «{0}» non collegato: uso l'ultimo schermo",
                 describe(ident) or tr("n. {0}", int(self.show_data.settings.get("output_screen", -1)) + 1))
        self.ctrl.message.emit(msg)
        self.status(msg)

    def _watch_screens(self):
        """Schermi collegati o staccati mentre Cuevivo è aperto: l'uscita torna sullo schermo giusto."""
        from PySide6.QtCore import QTimer
        app = QApplication.instance()
        self._screens_timer = QTimer(self)
        self._screens_timer.setSingleShot(True)
        self._screens_timer.setInterval(600)        # il sistema assesta gli schermi in più passi
        self._screens_timer.timeout.connect(self._screens_changed)
        self._screens_seen = self._screen_names()
        app.screenAdded.connect(lambda _s: self._screens_timer.start())
        app.screenRemoved.connect(lambda _s: self._screens_timer.start())

    def _screen_names(self):
        from ..screens import describe, identity
        return [describe(identity(s)) for s in QApplication.screens()]

    def _screens_changed(self):
        now = self._screen_names()
        before = getattr(self, "_screens_seen", [])
        self._screens_seen = now
        for name in now:
            if name not in before:
                self.ctrl.message.emit(tr("Schermo collegato: {0}", name))
        for name in before:
            if name not in now:
                self.ctrl.message.emit(tr("Schermo scollegato: {0}", name))
        out = self.output
        if out is not None and out.isVisible():
            self.show_output()                       # schermo scelto, oppure l'ultimo (in finestra se è uno solo)
            _i, st = self._out_screen()
            if st != "missing":
                self.status(tr("Uscita video sullo schermo {0}", describe_current(self)))

    def geometry_dialog(self):
        self._ensure_output_visible()
        d = GeometryDialog(self.comp, self.output, self._corners_changed, self)
        d.exec()
        self.act_grid.setChecked(self.comp.grid)
        self._corners_changed()

    def _corners_changed_silent(self):
        s = self.show_data.settings
        s["output_corners"] = [list(map(float, c)) for c in self.comp.corners]
        s["output_grid"] = self.comp.grid

    def _corners_changed(self):
        s = self.show_data.settings
        s["output_corners"] = [list(map(float, c)) for c in self.comp.corners]
        s["output_grid"] = self.comp.grid
        self.show_data.dirty = True

    def toggle_grid(self):
        self.comp.grid = self.act_grid.isChecked()
        self._corners_changed()
        self.comp.changed()
        if self.comp.grid:
            self._ensure_output_visible()

    def toggle_gl(self):        # compatibilità: OpenGL acceso = motore automatico, spento = raster
        self.set_video_engine("raster" if self.video_engine_setting() != "raster" else "auto")


def describe_current(mw):
    from ..screens import describe, identity
    try:
        return describe(identity(mw._out_qscreen()))
    except Exception:
        return ""
