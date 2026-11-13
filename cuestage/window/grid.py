"""PANEL GRID e scorciatoie da tastiera delle cue (parte di MainWindow)."""
from ..i18n import tr
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QMessageBox

from .. import grid as G


class GridMixin:
    # ---- stato
    def _grid_list(self):
        lst = self.show_data.current
        return lst if lst.is_grid else None

    def _grid_sync(self, force=False):
        """Dà una casella alle cue nuove e riordina la lista per caselle (il GO locale segue la griglia)."""
        lst = self._grid_list()
        if lst is None:
            return
        sig = G.signature(lst)
        if not force and sig == getattr(self, "_grid_sig", None):
            return
        ph_cue = None
        ph = self.ctrl.playhead_of(lst)
        if 0 <= ph < len(lst.cues):
            ph_cue = lst.cues[ph]
        sel = [self.show_data.cues[r].id for r in self._selected_rows() if r < len(self.show_data.cues)]
        pref = getattr(self, "_grid_pending_cell", None)
        changed = G.ensure_positions(lst, preferred=pref, page=self.grid.page)
        self._grid_pending_cell = None
        if G.sort_cues(lst) or changed:
            self.model.beginResetModel()
            self.model.endResetModel()
            if ph_cue is not None:
                self.ctrl.set_playhead(lst.index_of(ph_cue.id), lst)
            self._select_guard = True
            self._select_rows([lst.index_of(i) for i in sel if lst.index_of(i) >= 0])
            self._select_guard = False
            self.show_data.dirty = True
        if pref is not None:                    # la pagina della cue appena aggiunta
            self.grid.page = int(pref[0])
        self._grid_sig = G.signature(lst)
        self.grid.sync()

    # ---- azioni sulle caselle
    def grid_select(self, cue, move_playhead=True):
        lst, i = self.show_data.list_of(cue.id)
        if lst is None or lst is not self.show_data.current:
            return
        self._select_guard = not move_playhead
        self._select_rows([i])
        self._select_guard = False
        self.inspector.set_cue(cue)
        if move_playhead:
            self.ctrl.set_playhead(i, lst)
            self._update_standby()
        self.grid.canvas.update()

    def grid_activate(self, cue, mode=None):
        """Clic su una casella (o scorciatoia): avvia / ferma / riavvia secondo l'impostazione del PANEL, o
        secondo il pulsante della cue (Toggle / Pushbutton)."""
        lst, _i = self.show_data.list_of(cue.id)
        btn = getattr(cue, "grid_button", "") or ""
        if mode is None:
            mode = btn or (lst.grid_click if (lst is not None and lst.is_grid) else "start")
        ctrl = self.ctrl
        if mode == "push":
            # pushbutton: parte alla pressione e resta attiva finché il tasto è premuto (vedi grid_release)
            if not hasattr(self, "_pushed"):
                self._pushed = set()
            if cue.id in self._pushed:
                return                                   # ripetizione del tasto tenuto premuto
            self._pushed.add(cue.id)
            (getattr(self, "_release_waits", None) or {}).pop(cue.id, None)   # ripremuto: niente cue «al rilascio»
            inst = ctrl.instances.get(cue.id)
            if inst is None or inst.done:
                ctrl.trigger(cue, cascade=False)
            self.status(f"▶ {cue.label()}  " + tr("(pushbutton)"))
            return
        inst = ctrl.instances.get(cue.id)
        active = (inst is not None and not inst.done) or any(p[1].id == cue.id for p in ctrl.pending)
        if mode == "toggle" and active:
            fade = float(lst.grid_fade) if (lst is not None and lst.is_grid) else \
                float(self.show_data.settings.get("stop_fade", 0.3))
            ctrl.stop_cue(cue.id, fade)
            self.status(f"■ {cue.label()}")
            return
        if mode == "start" and active:
            return
        ctrl.trigger(cue, cascade=False)
        self.status(f"▶ {cue.label()}")

    def grid_set_button(self, cue, mode):
        cue.grid_button = mode or ""
        self.show_data.dirty = True
        self._rebuild_hotkeys(force=True)
        if self.inspector.cue is cue:
            self.inspector.set_cue(cue)
        if self._grid_list() is not None:
            self.grid.canvas.update()

    def grid_release(self, cue):
        """Pushbutton rilasciato: la cue si ferma con il suo fade out (o quello del GRID) e, se impostata,
        parte la cue «al rilascio»."""
        pushed = getattr(self, "_pushed", set())
        if cue is None or cue.id not in pushed:
            return
        pushed.discard(cue.id)
        lst, _i = self.show_data.list_of(cue.id)
        base = float(lst.grid_fade) if (lst is not None and lst.is_grid) else \
            float(self.show_data.settings.get("stop_fade", 0.3))

        def own_fade(c):
            """Fade out della cue (per video e immagini anche quello del video), altrimenti quello del GRID."""
            f = float(getattr(c, "fade_out", 0) or 0)
            if c.type in ("video", "image"):
                f = max(f, float(getattr(c, "v_fade_out", 0) or 0))
            return f if f > 0 else base
        inst = self.ctrl.instances.get(cue.id)
        spawned = getattr(inst, "spawned", None)
        stopping = [cue.id]                      # cue che stanno finendo (per «attendi la fine»)
        if cue.type == "group" and inst is not None and not inst.done and spawned is not None:
            stopping += list(spawned)
            # gruppo: ogni sua cue in riproduzione sfuma con il proprio fade out, poi il gruppo esce
            gf = float(getattr(cue, "fade_out", 0) or 0)
            for k in list(spawned):
                ci = self.ctrl.instances.get(k)
                cc = self.show_data.by_id(k)
                if ci is not None and not ci.done and cc is not None:
                    f = float(getattr(cc, "fade_out", 0) or 0)
                    if cc.type in ("video", "image"):
                        f = max(f, float(getattr(cc, "v_fade_out", 0) or 0))
                    ci.stop(f if f > 0 else (gf if gf > 0 else base))
            spawned.clear()                      # già fermate: il gruppo non le rifà sfumare
            self.ctrl.pending = [p for p in self.ctrl.pending if p[1].group != cue.id]
            self.ctrl.stop_cue(cue.id, 0.0)
        else:
            self.ctrl.stop_cue(cue.id, own_fade(cue))
        rel = getattr(cue, "grid_release", "") or ""
        tg = self.show_data.resolve_target(rel) if rel else None
        if tg is not None and tg is not cue and getattr(cue, "grid_release_wait", False):
            # parte quando le cue attive (anche quelle del gruppo) hanno finito il loro fade out
            waits = getattr(self, "_release_waits", None)
            if waits is None:
                waits = self._release_waits = {}
            waits[cue.id] = (stopping, tg)
            self._release_wait_timer().start()
            self.status(f"■ {cue.label()}  … ▶ {tg.label()}")
        elif tg is not None and tg is not cue:
            self.ctrl.trigger(tg, cascade=False)
            self.status(f"■ {cue.label()}  ▶ {tg.label()}")
        else:
            self.status(f"■ {cue.label()}")
        if self._grid_list() is not None:
            self.grid.canvas.update()

    def _release_wait_timer(self):
        t = getattr(self, "_rel_timer", None)
        if t is None:
            from PySide6.QtCore import QTimer
            t = self._rel_timer = QTimer(self)
            t.setInterval(40)
            t.timeout.connect(self._release_wait_tick)
        return t

    def _release_wait_tick(self):
        """«Al rilascio avvia» con attesa: la cue parte quando tutte le cue che stavano finendo sono ferme."""
        waits = getattr(self, "_release_waits", {}) or {}
        ctrl = self.ctrl
        for cid, (ids, tg) in list(waits.items()):
            busy = any((ctrl.instances.get(k) is not None and not ctrl.instances[k].done) for k in ids)
            if not busy:
                del waits[cid]
                if self.show_data.by_id(tg.id) is not None:
                    ctrl.trigger(tg, cascade=False)
                    self.status(f"▶ {tg.label()}")
        if not waits:
            self._release_wait_timer().stop()

    def grid_move(self, cue, cell):
        lst = self._grid_list()
        if lst is None:
            return
        G.move_cell(lst, cue, *cell)
        self.show_data.dirty = True
        self._grid_sync(force=True)

    def grid_resize(self, lst, rows, cols):
        G.resize(lst, rows, cols)
        self.show_data.dirty = True
        self._grid_sync(force=True)

    def grid_add_cue(self, ctype, cell):
        self._grid_pending_cell = list(cell)
        self.table.clearSelection()
        self.add_cue(ctype)
        self._grid_sync(force=True)

    def grid_add_files(self, paths, cell):
        self._grid_pending_cell = list(cell)
        self._files_dropped(paths, len(self.show_data.cues))
        self._grid_sync(force=True)

    # ---- gruppo aperto nel GRID: le sue cue
    def _member_row(self, group, index):
        """Riga della lista in cui inserire una cue alla posizione «index» tra i membri del gruppo."""
        from ..groups import block_end
        lst = self.show_data.current
        hi = lst.index_of(group.id)
        members = [i for i in range(hi + 1, block_end(lst.cues, hi))]
        return members[index] if 0 <= index < len(members) else block_end(lst.cues, hi)

    def grid_group_add_cue(self, ctype, group, index):
        self.add_cue(ctype, row=self._member_row(group, index), group=group.id)
        self._grid_sync(force=True)

    def grid_group_add_files(self, paths, group, index):
        self._files_dropped(paths, self._member_row(group, index), group=group.id)
        self._grid_sync(force=True)

    def grid_group_move(self, cue, index):
        """Cambia l'ordine di una cue dentro il suo gruppo (= ordine di esecuzione)."""
        lst = self.show_data.current
        g = self.show_data.by_id(cue.group)
        if g is None:
            return
        members = [c for c in lst.cues if c.group == g.id]
        if cue not in members:
            return
        index = max(0, min(index, len(members) - 1))
        if members.index(cue) == index:
            return
        members.remove(cue)
        members.insert(index, cue)
        hi = lst.index_of(g.id)
        self.model.beginResetModel()
        rest = [c for c in lst.cues if c.group != g.id]
        hi = rest.index(g)
        lst.cues[:] = rest[:hi + 1] + members + rest[hi + 1:]
        self.model.endResetModel()
        self._structure_changed(select=[lst.index_of(cue.id)])
        self._grid_sync(force=True)

    def grid_add_to_group(self, cue, group, copy=False):
        """Una casella trascinata su un gruppo diventa l'ultima cue del gruppo (anche da un'altra lista); con
        copy=True ci entra una copia. Restituisce la cue entrata nel gruppo (o None)."""
        moved = self.move_cue_to(cue, group=group, copy=copy)
        if moved:
            lst, i = self.show_data.list_of(moved.id)
            if lst is self.show_data.current:
                self._select_rows([i])
        return moved

    def grid_ungroup_member(self, cue):
        self.grid_select(cue, move_playhead=False)
        self.remove_from_group()
        self._grid_sync(force=True)

    def grid_set_color(self, cue, color):
        cue.color = color or ""
        self.show_data.dirty = True
        self.model.layoutChanged.emit()
        self.grid.canvas.update()
        if self.inspector.cue is cue:
            self.inspector.set_cue(cue)

    def grid_delete(self, cue):
        self.grid_select(cue, move_playhead=False)
        self.delete_cues()
        self._grid_sync(force=True)

    def set_panel_kind(self, i, kind):
        lst = self.show_data.lists[i]
        if lst.is_main or lst.kind == kind:
            return
        lst.kind = kind
        if kind == "grid":
            G.ensure_positions(lst)
            G.sort_cues(lst)
        self.show_data.dirty = True
        self.show_list(i)
        self._update_tabs()

    # ---- scorciatoie da tastiera
    def _reserved_keys(self):
        """Scorciatoie già usate dal programma (menu e comandi fissi): non si possono dare alle cue."""
        own = set(getattr(self, "_hotkey_objs", {}).values())
        out = {}
        for a in self.findChildren(QAction):
            for ks in a.shortcuts():
                if not ks.isEmpty():
                    out[ks.toString(QKeySequence.PortableText)] = a.text().replace("&", "")
        for s in self.findChildren(QShortcut):
            if s in own:
                continue
            k = s.key()
            if not k.isEmpty():
                out[k.toString(QKeySequence.PortableText)] = tr("comando di CueStage")
        for k, why in (("Space", "GO"), ("Esc", tr("STOP / esci")), ("Return", "modifica"), ("Enter", "modifica"),
                       ("Del", "elimina"), ("Backspace", "elimina"), ("Tab", "navigazione")):
            out.setdefault(k, why)
        return out

    def _used_keys(self, exclude=None):
        """{tasto: (cue, campo)} delle scorciatoie già date alle cue (avvio e Tap Tempo)."""
        out = {}
        for c in self.show_data.all_cues():
            for field in ("hotkey", "group_tap_key"):
                k = getattr(c, field, "")
                if k and (c, field) != exclude:
                    out[k] = (c, field)
        return out

    def learn_hotkey(self, cue, field="hotkey"):
        from ..ui_grid import HotkeyDialog
        others = self._used_keys(exclude=(cue, field))
        title = tr("Tap Tempo di ") if field == "group_tap_key" else ""
        dlg = HotkeyDialog(cue, self._reserved_keys(), others, self, current=getattr(cue, field, ""),
                           purpose=title)
        if dlg.exec() != QDialog.Accepted:
            return
        k = dlg.key()
        if dlg.steal is not None:
            sc, sf = dlg.steal
            setattr(sc, sf, "")
        self.set_hotkey(cue, k, field)

    def set_hotkey(self, cue, key, field="hotkey"):
        setattr(cue, field, key or "")
        self.show_data.dirty = True
        self._rebuild_hotkeys(force=True)
        self.grid.canvas.update()
        if self.inspector.cue is cue:
            self.inspector.set_cue(cue)
        self.status(tr("Scorciatoia {0} → {1}", key, cue.label()) if key else tr("Scorciatoia tolta da {0}", cue.label()))

    def _rebuild_hotkeys(self, force=False):
        sig = tuple((c.id, field, getattr(c, field), getattr(c, "grid_button", "")) for c in self.show_data.all_cues()
                    for field in ("hotkey", "group_tap_key") if getattr(c, field, ""))
        if not force and sig == getattr(self, "_hotkey_sig", None):
            return
        self._hotkey_sig = sig
        for s in getattr(self, "_hotkey_objs", {}).values():
            s.setEnabled(False)
            s.setParent(None)
            s.deleteLater()
        self._hotkey_objs = {}
        self._push_keys = {}
        self._install_push_filter()
        reserved = None
        for cid, field, key, _btn in sig:
            ks = QKeySequence.fromString(key, QKeySequence.PortableText)
            if ks.isEmpty():
                continue
            if reserved is None:
                reserved = self._reserved_keys()
            if key in reserved:
                continue                       # scorciatoia (da uno show vecchio) che ora è del programma
            s = QShortcut(ks, self)
            s.setContext(Qt.WindowShortcut)
            if field == "group_tap_key":
                s.setAutoRepeat(False)          # tenere premuto non deve battere il tempo
                s.activated.connect(lambda cid=cid: self.ctrl.group_tap(cid))
            else:
                c_ = self.show_data.by_id(cid)
                if c_ is not None and getattr(c_, "grid_button", "") == "push":
                    s.setAutoRepeat(False)      # pushbutton: tenere premuto non deve ripartire
                    self._push_keys = getattr(self, "_push_keys", {})
                    self._push_keys[cid] = ks[0].key()
                s.activated.connect(lambda cid=cid: self._hotkey_fired(cid))
            self._hotkey_objs[(cid, field)] = s

    def _install_push_filter(self):
        """Filtro sui tasti rilasciati: il rilascio della scorciatoia di un pushbutton ferma la cue."""
        if getattr(self, "_push_filter", None) is not None:
            return
        from PySide6.QtCore import QEvent, QObject
        from PySide6.QtWidgets import QApplication
        mw = self

        class _PushFilter(QObject):
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.KeyRelease and not ev.isAutoRepeat():
                    keys = getattr(mw, "_push_keys", {}) or {}
                    for cid, k in list(keys.items()):
                        if ev.key() == k and cid in getattr(mw, "_pushed", set()):
                            mw.grid_release(mw.show_data.by_id(cid))
                return False
        self._push_filter = _PushFilter(self)
        QApplication.instance().installEventFilter(self._push_filter)

    def _hotkey_fired(self, cue_id):
        cue = self.show_data.by_id(cue_id)
        if cue is None:
            return
        self.grid_activate(cue)
        if self._grid_list() is not None:
            self.grid.canvas.update()

    def _grid_tick(self):
        if self._grid_list() is not None:
            self._grid_sync()
            self.grid.canvas.update()
        self._rebuild_hotkeys()

    def _confirm_hotkey_conflicts(self):
        """Avvisa (una volta) se lo show usa scorciatoie che CueStage riserva."""
        bad = [c for c in self.show_data.all_cues() if c.hotkey and c.hotkey in self._reserved_keys()]
        if bad:
            QMessageBox.information(self, tr("Scorciatoie"), tr("Queste scorciatoie sono usate da CueStage e sono state "
                                    "ignorate:\n") + "\n".join(f"{c.hotkey} → {c.label()}" for c in bad[:10]))
