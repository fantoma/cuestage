"""Uscita video: finestra, schermi, geometria a 4 angoli (parte di MainWindow)."""

from ..i18n import tr
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication, )

from ..ui_dialogs import GeometryDialog
from ..video_output import make_output

FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


class VideoMixin:
    def _create_output(self):
        if self.output is None:
            self.output = make_output(self.comp, bool(self.show_data.settings.get("output_opengl", True)))
            self.output.cornersChanged.connect(self._corners_changed)
            self._build_shortcuts(self.output)

    def _output_aspect(self):
        if self.output is not None and self.output.isVisible() and self.output.height():
            return self.output.width() / self.output.height()
        screens = QApplication.screens()
        i = self.show_data.settings.get("output_screen", -1)
        scr = screens[i] if 0 <= i < len(screens) else screens[-1]
        g = scr.geometry()
        return g.width() / g.height() if g.height() else 16 / 9

    def _output_pixels(self):
        """Risoluzione reale (pixel fisici) dell'uscita: finestra se visibile, altrimenti lo schermo scelto."""
        if self.output is not None and self.output.isVisible() and self.output.width() > 0:
            r = self.output.devicePixelRatioF()
            return int(round(self.output.width() * r)), int(round(self.output.height() * r))
        screens = QApplication.screens()
        i = self.show_data.settings.get("output_screen", -1)
        scr = screens[i] if 0 <= i < len(screens) else screens[-1]
        g, r = scr.geometry(), scr.devicePixelRatio()
        return int(round(g.width() * r)), int(round(g.height() * r))

    def _ensure_output_visible(self):
        if self.output is None or not self.output.isVisible():
            self.show_output()

    def show_output(self):
        self._create_output()
        s = self.show_data.settings
        scr = s.get("output_screen", -1)
        screens = QApplication.screens()
        full = bool(s.get("output_fullscreen", True))
        if len(screens) == 1 and scr in (-1, 0) and full:
            # un solo schermo: evita di coprire la finestra di controllo
            full = False
        self.output.place_on_screen(scr if scr >= 0 else None, full)
        self.activateWindow()
        self.act_out_show.setText(tr("Nascondi finestra di uscita"))

    def toggle_output(self):
        if self.output is not None and self.output.isVisible():
            self.output.hide()
            self.act_out_show.setText(tr("Mostra finestra di uscita"))
        else:
            self.show_output()

    def toggle_fullscreen(self):
        self.show_data.settings["output_fullscreen"] = self.act_full.isChecked()
        self.show_data.dirty = True
        if self.output is not None and self.output.isVisible():
            self.show_output()

    def _fill_screens(self):
        self.screens_menu.clear()
        grp = QActionGroup(self.screens_menu)
        cur = self.show_data.settings.get("output_screen", -1)
        screens = QApplication.screens()
        opts = [(-1, tr("Automatico (ultimo schermo)"))] + [
            (i, tr("{0}: {1} {2}×{3}", i + 1, s.name(), s.geometry().width(), s.geometry().height()))
            for i, s in enumerate(screens)]
        for i, label in opts:
            a = QAction(label, self.screens_menu)
            a.setCheckable(True)
            a.setChecked(i == cur)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, i=i: self._set_screen(i))
            self.screens_menu.addAction(a)

    def _set_screen(self, i):
        self.show_data.settings["output_screen"] = i
        self.show_data.dirty = True
        if self.output is not None and self.output.isVisible():
            self.show_output()

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

    def toggle_gl(self):
        self.show_data.settings["output_opengl"] = self.act_gl.isChecked()
        self.show_data.dirty = True
        if self.output is not None:
            vis = self.output.isVisible()
            self.comp.listeners.remove(self.output.update)
            self.output.close()
            self.output.deleteLater()
            self.output = None
            if vis:
                self.show_output()
