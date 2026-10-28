"""Comandi remoti: OSC in ingresso e in uscita, MIDI, controlli continui, crossfade (parte di MainWindow)."""


from ..i18n import tr
from ..model import (
    MIN_DB, db_to_lin, )
from ..remote import osc_send, spec_matches
from ..ui_dialogs import MidiLearnDialog

FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


class RemoteMixin:
    def _osc_message(self, addr, args):
        a = addr.rstrip("/").lower()
        parts = [p for p in addr.strip("/").split("/") if p]
        ctrl = self.ctrl
        if a == "/go":
            self.go()
        elif a == "/stop":
            self.stop_all()
        elif a == "/panic":
            self.panic()
        elif a == "/pause":
            self.pause_all()
        elif a == "/resume":
            self.resume_all()
        elif a in ("/dim", "/duck"):
            # senza argomento cambia stato; 1 = acceso, 0 = spento
            on = None if not args else bool(float(args[0]))
            (self.set_dim if a == "/dim" else self.set_duck)(on)
        elif a == "/next":
            ctrl.step_playhead(1)
        elif a in ("/previous", "/prev"):
            ctrl.step_playhead(-1)
        elif len(parts) == 2 and parts[0].lower() == "playhead":
            c = self.show_data.by_number(parts[1])
            if c:
                ctrl.playhead_to(c)
        elif a in ("/text/next", "/text/prev"):
            self.text_step(1 if a.endswith("next") else -1)
        elif a == "/text/goto" and args:
            self.ctrl.text_goto(int(args[0]) - 1)
        elif a == "/xfade":
            self.ctrl.crossfade(float(args[0]) if args else float(self.show_data.settings.get("xfade_time", 3.0)))
        elif a == "/xfader" and args:
            self.ctrl.set_crossfader(float(args[0]))
        elif len(parts) >= 2 and parts[0].lower() in ("list", "panel"):
            # /list/<nome>  = mostra la lista · /list/<nome>/go = GO su quella lista
            lst = next((l for l in self.show_data.lists if l.name.lower() == parts[1].lower()), None)
            if lst is None:
                self.status(tr("OSC: lista «{0}» non trovata", parts[1]))
                return
            if len(parts) == 3 and parts[2].lower() == "go":
                ctrl.go(lst)
            elif len(parts) == 3 and parts[2].lower() == "stop":
                self.stop_list(lst)
            else:
                self.show_list(self.show_data.lists.index(lst))
        elif len(parts) == 3 and parts[0].lower() == "cue":
            c = self.show_data.by_number(parts[1])
            cmd = parts[2].lower()
            if c is None:
                self.status(tr("OSC: cue «{0}» non trovata", parts[1]))
                return
            if cmd in ("start", "go"):
                ctrl.trigger(c, cascade=True)
            elif cmd == "tap":
                ctrl.group_tap(c.id)
            elif cmd == "stop":
                ctrl.stop_cue(c.id, float(args[0]) if args else 0.0)
            elif cmd == "pause":
                ctrl.pause_cue(c.id, True)
            elif cmd == "resume":
                ctrl.pause_cue(c.id, False)
            elif cmd == "level" and args:
                inst = ctrl.instances.get(c.id)
                if inst:
                    inst.fade_to(db_to_lin(float(args[0])), None, float(args[1]) if len(args) > 1 else 0.0,
                                 "scurve", False)
        else:
            hit = False
            for c in self.show_data.all_cues():
                if c.osc_trigger and c.osc_trigger.rstrip("/").lower() == a:
                    ctrl.trigger(c, cascade=True)
                    hit = True
                if c.type == "group" and c.group_tap_osc and c.group_tap_osc.rstrip("/").lower() == a:
                    ctrl.group_tap(c.id)
                    hit = True
            if not hit:
                self.status(tr("OSC ignorato: {0} {1}", addr, args))
                return
        self.status(tr("OSC ← {0} {1}", addr, ' '.join(map(str, args))))

    def _send_osc_now(self, cid):
        c = self.show_data.by_id(cid)
        try:
            from ..engine import osc_target
            host, port, address, args = osc_target(c, self.show_data.settings)
            osc_send(host, port, address, args)
            self.status(tr("OSC inviato a {0}:{1}: {2} {3}", host, port, address, args))
        except Exception as e:
            self.status(tr("Invio OSC fallito: {0}", e))

    @staticmethod
    def fader_db(v):
        """Legge del fader: 0 = -∞, ~70% della corsa = 0 dB, in cima = +6 dB."""
        if v <= 0.002:
            return MIN_DB
        import math
        return max(MIN_DB, min(6.0, 20 * math.log10(2.0 * v * v)))

    def text_step(self, d):
        """Frase successiva/precedente della cue di testo in sequenza attiva (lista di GO)."""
        if not self.ctrl.text_step(d):
            self.status(tr("Nessun testo in sequenza attivo"))
            return
        self._update_standby()

    def crossfade_next(self):
        if self.learn_dialog is not None:
            return
        t = float(self.show_data.settings.get("xfade_time", 3.0))
        if not self.ctrl.crossfade(t):
            self.status(tr("Crossfade: nessuna cue in attesa"))

    def _apply_cue_control(self, cue, what, v):
        """Volume ("vol") o trasparenza ("op") di una cue da un controllo MIDI: dal vivo se la cue è in
        riproduzione, altrimenti sul valore programmato."""
        if what == "vol":
            if cue.type not in ("audio", "video"):
                return None
            db = self.fader_db(v)
            if not self.ctrl.set_live_level(cue, db):
                cue.level_db = round(db, 1)
                self.show_data.dirty = True
                if self.inspector.cue is cue:
                    self.inspector.refresh_fields()
                return tr("{0}: livello {1} dB", cue.label(), '-∞' if db <= MIN_DB else f'{db:+.1f}')
            return tr("{0}: volume dal vivo {1} dB", cue.label(), '-∞' if db <= MIN_DB else f'{db:+.1f}')
        if cue.type not in ("video", "image"):
            return None
        op = round(v, 3)
        if not self.ctrl.set_live_opacity(cue, op):
            cue.opacity = op
            self.show_data.dirty = True
            if self.inspector.cue is cue:
                self.inspector.refresh_fields()
            return tr("{0}: opacità {1:.0f}%", cue.label(), op * 100)
        return tr("{0}: opacità dal vivo {1:.0f}%", cue.label(), op * 100)

    def _midi_control(self, spec, v, port=""):
        from ..remote import control_matches
        if self.learn_dialog is not None:
            if getattr(self.learn_dialog, "mode", "") == "control":
                self.learn_dialog.got(spec, port)
            return
        mc = self.show_data.settings.get("midi_controls", {})
        msgs = []
        if mc.get("xfade") and control_matches(mc["xfade"], spec, port):
            self.ctrl.set_crossfader(v)
            msgs.append(tr("crossfader {0:.0f}%", v * 100))
        cue = self._current_cue()
        if cue is not None:
            for key, what in (("vol_selected", "vol"), ("op_selected", "op")):
                if mc.get(key) and control_matches(mc[key], spec, port):
                    r = self._apply_cue_control(cue, what, v)
                    if r:
                        msgs.append(r)
        for c in self.show_data.all_cues():
            for field, what in (("midi_vol", "vol"), ("midi_op", "op")):
                sp = getattr(c, field, "")
                if sp and control_matches(sp, spec, port):
                    r = self._apply_cue_control(c, what, v)
                    if r:
                        msgs.append(r)
        if msgs:
            import time as _t
            now = _t.monotonic()
            if now - self._ctl_status_t > 0.1:       # barra di stato: al massimo 10 aggiornamenti/s
                self._ctl_status_t = now
                self.status(tr("MIDI ← ") + " · ".join(msgs))

    def _midi_ports(self):
        s = self.show_data.settings
        return list(s.get("midi_in_ports") or ([s["midi_in_port"]] if s.get("midi_in_port") else []))

    def learn_midi(self, callback, port=None, mode="trigger", filters=None):
        """Attende un messaggio MIDI. `port`: porta o lista di porte da ascoltare (quelle spuntate nelle
        impostazioni, anche se non ancora confermate); altrimenti quelle già aperte."""
        current = self._midi_ports()
        wanted = ([port] if isinstance(port, str) else list(port)) if port else []
        listen = list(dict.fromkeys(current + wanted))
        temp = bool(wanted) and set(wanted) - set(current)
        if temp:
            self.midi.open(listen)
        if self.midi.ports:
            info = tr("In ascolto su: ") + ", ".join(self.midi.ports)
        elif not listen:
            info = tr("⚠ Nessuna scheda MIDI selezionata: spuntala nelle impostazioni.")
        else:
            info = tr("⚠ Impossibile aprire «{0}» (vedi barra di stato).", ', '.join(listen))
        d = MidiLearnDialog(self, info, mode)
        self.learn_dialog = d
        self.midi.learning = True
        if filters is not None:                 # filtri appena modificati nelle impostazioni
            self.midi.set_filters(filters)
        try:
            if d.exec() and d.value():
                callback(d.value())
        finally:
            self.learn_dialog = None
            self.midi.learning = False
            if filters is not None:
                self.midi.set_filters(self.show_data.settings.get("midi_filters") or {})
            if temp:          # ripristina le porte configurate
                if current:
                    self.midi.open(current)
                else:
                    self.midi.close()

    def _midi_message(self, spec, port=""):
        if self.learn_dialog is not None:
            if getattr(self.learn_dialog, "mode", "trigger") == "trigger":
                self.learn_dialog.got(spec, port)
            return
        mm = self.show_data.settings.get("midi_map", {})
        actions = {"go": self.go, "stop_all": self.stop_all, "pause_all": self.pause_all,
                   "resume_all": self.resume_all,
                   "next": lambda: self.ctrl.step_playhead(1),
                   "prev": lambda: self.ctrl.step_playhead(-1),
                   "xfade_go": self.crossfade_next,
                   "dim": self.set_dim, "duck": self.set_duck}
        for key, fn in actions.items():
            if mm.get(key) and spec_matches(mm[key], spec, port):
                fn()
                self.status(tr("MIDI ← {0} ({1}): {2}", spec, port, key) if port else tr("MIDI ← {0}: {1}", spec, key))
                return
        for c in self.show_data.all_cues():
            if c.type == "group" and c.group_tap_midi and spec_matches(c.group_tap_midi, spec, port):
                self.ctrl.group_tap(c.id)
                return
        for c in self.show_data.all_cues():
            if c.midi and spec_matches(c.midi, spec, port):
                self.ctrl.trigger(c, cascade=True)
                self.status(tr("MIDI ← {0}: cue {1}", spec, c.label()))
