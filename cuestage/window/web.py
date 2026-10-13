"""Interfaccia web: stato, comandi, proiettori dal telefono (parte di MainWindow)."""
import os


from ..model import (
    TYPE_ICONS, TYPE_LABELS, )

FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


class WebMixin:
    def _web_projectors(self):
        """Configurazione dei proiettori per la pagina web (comandi offerti da ogni plugin)."""
        from ..projectors import ACTIONS, CORNER_KEYS, CORNER_LABELS, LENS_ACTIONS, SPEEDS
        if not self.show_data.settings.get("web_projectors", True):
            return []
        out = []
        for conf in self.projectors.projectors:
            cls = self.projectors.registry.get(conf.get("driver"))
            item = {"id": conf.get("id"), "name": conf.get("name") or conf.get("host") or "Proiettore",
                    "driver": cls.NAME if cls else f"driver «{conf.get('driver')}» non disponibile",
                    "host": conf.get("host", ""), "caps": sorted(cls.CAPS) if cls else []}
            if cls is not None:
                params = getattr(cls, "EXTRA_PARAMS", {}) or {}
                item.update(
                    inputs=[[c, l] for c, l in cls.INPUTS],
                    lens=[a for a in LENS_ACTIONS if a in cls.CAPS],
                    speeds=[[k, v] for k, v in SPEEDS.items()],
                    extra=[{"id": a, "label": l, "param": params.get(a, "")} for a, l in cls.EXTRA_ACTIONS.items()],
                    corners=({k: {"label": CORNER_LABELS[k], "x": list(cls.CORNERS[k]["x"]),
                                  "y": list(cls.CORNERS[k]["y"])} for k in CORNER_KEYS}
                             if getattr(cls, "CORNERS", None) and "corners" in cls.CAPS else None),
                    corners_note=getattr(cls, "CORNERS_NOTE", ""),
                    labels={a: ACTIONS.get(a, a) for a in cls.CAPS})
            out.append(item)
        return out

    def _web_proj_finished(self, name, action, ok, msg):
        from ..projectors import ACTIONS
        for conf in self.projectors.projectors:
            if (conf.get("name") or conf.get("host") or conf.get("id")) == name:
                d = self._web_proj.setdefault(conf.get("id"), {})
                d.update(result=f"{'✔' if ok else '✘'} {action}: {msg}", ok=ok)
                if action == ACTIONS["corners_off"]:
                    self._web_corner_mode.discard(conf.get("id"))

    def _web_proj_result(self, pid, kind, ok, data):
        d = self._web_proj.setdefault(pid, {})
        if kind == "status":
            if ok and isinstance(data, dict):
                data = " · ".join(f"{k}: {v}" for k, v in data.items() if k != "risposta")
            d.update(result=("Stato — " if ok else "✘ ") + str(data), ok=ok)
        elif kind == "corners":
            if ok and isinstance(data, dict):
                d.update(corners=data, result="✔ Angoli letti dal proiettore", ok=True)
            else:
                d.update(result=f"✘ Lettura angoli: {data}", ok=False)
        self.web.refresh()

    def _web_proj_command(self, d):
        """Comandi proiettore dal web. Ottica: niente coda (se il comando precedente non è finito, si
        scarta). Angoli: in tempo reale, vince l'ultimo valore."""
        from ..projectors import CORNER_KEYS, LENS_ACTIONS
        if not self.show_data.settings.get("web_projectors", True):
            return False
        pid = str(d.get("pid") or "")
        conf = self.projectors.find(pid)
        if conf is None:
            return False
        cls = self.projectors.registry.get(conf.get("driver"))
        if cls is None:
            return False
        cmd = d.get("cmd")
        if cmd == "proj_status":
            self._web_proj.setdefault(pid, {}).update(result="… lettura stato", ok=None)
            self.projectors.query_status(pid, lambda ok, r: self._web_relay.result.emit(pid, "status", ok, r))
            return True
        if cmd == "proj_corners_read":
            self._web_proj.setdefault(pid, {}).update(result="… lettura angoli", ok=None)
            self.projectors.query_corners(pid, lambda ok, r: self._web_relay.result.emit(pid, "corners", ok, r))
            return True
        if cmd == "proj_corner":
            k, axis = d.get("corner"), d.get("axis")
            if k not in CORNER_KEYS or axis not in ("x", "y") or not getattr(cls, "CORNERS", None):
                return False
            try:
                v = int(d.get("value"))
            except (TypeError, ValueError):
                return False
            lo, hi = cls.CORNERS[k][axis]
            v = max(lo, min(hi, v))
            cur = self._web_proj.setdefault(pid, {}).setdefault("corners", {c: [0, 0] for c in CORNER_KEYS})
            cur.setdefault(k, [0, 0])[0 if axis == "x" else 1] = v
            self._web_corner_pending.setdefault(pid, {})[(k, axis)] = v
            self._web_corner_flush()
            return True
        action = str(d.get("action") or "")
        if action not in cls.CAPS and action not in cls.EXTRA_ACTIONS:
            return False
        if action in LENS_ACTIONS:
            job = self._web_proj_jobs.get((pid, "lens"))
            if job is not None and not job.done():
                return True                       # tasto tenuto premuto: niente arretrati
        if action == "corners_off":
            self._web_corner_mode.discard(pid)
        if action == "corners":
            self._web_corner_mode.add(pid)
        try:
            direction = 1 if int(d.get("direction", 1)) >= 0 else -1
            steps = max(1, min(50, int(d.get("steps", 1))))
        except (TypeError, ValueError):
            direction, steps = 1, 1
        speed = d.get("speed") if d.get("speed") in ("slow", "normal", "fast") else "normal"
        self._web_proj.setdefault(pid, {}).update(result="… invio", ok=None)
        from ..projectors import ACTIONS
        lab = ACTIONS.get(action) or cls.EXTRA_ACTIONS.get(action, action)
        job = self.projectors.run(pid, action, str(d.get("value", "")), direction, steps, speed, label=lab)
        if action in LENS_ACTIONS:
            self._web_proj_jobs[(pid, "lens")] = job
        return True

    def _web_corner_flush(self):
        from ..projectors import CORNER_LABELS
        busy = False
        for pid, pend in list(self._web_corner_pending.items()):
            job = self._web_proj_jobs.get((pid, "corner"))
            if job is not None and not job.done():
                busy = True
                continue
            if not pend:
                continue
            items = sorted(pend.items())
            pend.clear()
            action = "corner_live" if pid in self._web_corner_mode else "corner_one"
            self._web_corner_mode.add(pid)
            lab = ", ".join(f"{CORNER_LABELS[k].lower()} {a.upper()}={v}" for (k, a), v in items)
            self._web_proj_jobs[(pid, "corner")] = self.projectors.run(
                pid, action, "; ".join(f"{k} {a} {v}" for (k, a), v in items), label=f"Angoli (web): {lab}")
            busy = True
        if busy and not self._web_corner_timer.isActive():
            self._web_corner_timer.start()
        elif not busy:
            self._web_corner_timer.stop()

    def _web_lists(self):
        from ..model import TRIGGER_SHORT
        from ..ui_cuelist import list_color
        sh = self.show_data
        return {"projectors": self._web_projectors(), "lists": [{"id": l.id, "name": l.name, "main": l.is_main, "color": list_color(i).name(),
                           "cues": [{"id": c.id, "number": str(c.number or ""), "name": c.name or TYPE_LABELS[c.type],
                                     "icon": TYPE_ICONS.get(c.type, ""),
                                     "trigger": "" if c.trigger == "manual" else TRIGGER_SHORT.get(c.trigger, ""),
                                     "grp": bool(c.group), "isgroup": c.type == "group"}
                                    for c in l.cues]}
                          for i, l in enumerate(sh.lists)]}

    def _web_state(self):
        from ..ui_cuelist import list_color
        sh = self.show_data
        ctrl = self.ctrl
        if ctrl.show is not sh:
            return {"show": "", "lists_rev": 0, "active": [], "playheads": {}, "running_lists": []}
        act, running = [], set()

        def entry(cue, **kw):
            lst, _ = sh.list_of(cue.id)
            if lst is not None:
                running.add(lst.id)
            d = {"id": cue.id, "number": str(cue.number or ""), "name": cue.name or TYPE_LABELS[cue.type],
                 "icon": TYPE_ICONS.get(cue.type, ""), "list": lst.name if lst else "",
                 "color": list_color(sh.lists.index(lst)).name() if lst else "#555"}
            d.update(kw)
            return d
        for cue, rem in ctrl.waiting():
            act.append(entry(cue, state="wait", wait=round(rem, 2)))
        for inst in list(ctrl.instances.values()):
            tot = inst.total()
            act.append(entry(inst.cue, state="run", elapsed=round(inst.elapsed, 2),
                             remaining=round(max(0.0, tot - inst.elapsed), 2) if tot else None,
                             progress=inst.progress(), paused=bool(inst.paused or ctrl.paused_all)))
        return {"show": os.path.basename(sh.path) if sh.path else "senza titolo",
                "go_target": sh.settings.get("go_target", "main"), "go_list": ctrl.go_list().id,
                "playheads": {l.id: ctrl.playhead_of(l) for l in sh.lists},
                "paused_all": bool(ctrl.paused_all), "active": act, "running_lists": sorted(running),
                "proj": self._web_proj}

    def _web_command(self, d):
        """Comandi dall'interfaccia web (già nel thread di Qt)."""
        if self.learn_dialog is not None:
            return
        sh = self.show_data
        cmd = d.get("cmd")
        lst = sh.list_by_id(str(d.get("list") or "")) if d.get("list") else None
        if cmd == "go":
            self.go()
        elif cmd == "go_list" and lst is not None:
            self.ctrl.go(lst)
        elif cmd == "stop_list" and lst is not None:
            self.stop_list(lst)
        elif cmd == "xfade":
            self.crossfade_next()
        elif cmd == "stop_all":
            self.stop_all()
        elif cmd == "pause_all":
            self.pause_all()
        elif cmd == "resume_all":
            self.resume_all()
        elif cmd == "stop_cue":
            self.ctrl.stop_cue(str(d.get("cue")), float(sh.settings.get("stop_fade", 0.3)))
        elif cmd == "pause_cue":
            self._toggle_pause_cue(str(d.get("cue")))
        elif cmd == "playhead" and lst is not None:
            try:
                self.ctrl.set_playhead(int(d.get("index", 0)), lst)
            except (TypeError, ValueError):
                return
        elif cmd.startswith("proj"):
            if not self._web_proj_command(d):
                return
            self.web.refresh()
            return               # niente messaggio in barra di stato a ogni passo (ottica/angoli)
        else:
            return
        self.status(f"Web ← {cmd}" + (f" ({lst.name})" if lst is not None else ""))
        self.web.refresh()
