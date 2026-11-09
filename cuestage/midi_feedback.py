"""Ritorno MIDI (feedback) verso i controller: anelli LED, fader motorizzati e LED dei pulsanti.

Per ogni scheda MIDI in ingresso si può indicare una «porta di ritorno» (Impostazioni › MIDI, filtri della
scheda): CueStage le manda il valore attuale di ogni controllo mappato su quella scheda, così il controller
mostra lo stato vero anche quando i valori cambiano da CueStage (mouse, fade, cue, altra istanza):

  - volume e trasparenza delle cue con un fader MIDI (dal vivo se la cue suona, altrimenti il valore programmato);
  - «volume / trasparenza della cue selezionata» (cambia con la selezione);
  - crossfader (lato a riposo);
  - LED: cue in esecuzione (trigger MIDI della cue), DIM, Duck, Pausa generale.

Un controllo appena mosso sul controller non riceve il suo valore indietro per mezzo secondo (niente rimbalzi
sui fader motorizzati). All'apertura dello show, al cambio delle impostazioni e quando la ridondanza torna a
comandare, tutti i valori ripartono. Sull'istanza in ridondanza collegata al master non parte nulla.
"""
import math
import time

from .i18n import tr
from .remote import normalize_control, normalize_spec, port_key, port_matches, split_port

MIN_DB = -60.0
QUIET_S = 0.5               # dopo un movimento sul controller, per tanto non si rimanda quel controllo
SETTLE = 1                  # differenza (0..127) considerata uguale a quella appena ricevuta


def db_to_fader(db):
    """Inverso della legge dei fader MIDI di CueStage (0 = −∞, ~70% = 0 dB, in cima = +6 dB)."""
    if db is None or db <= MIN_DB + 0.01:
        return 0.0
    return max(0.0, min(1.0, math.sqrt(10 ** (db / 20.0) / 2.0)))


def encode(spec, value):
    """Messaggio di ritorno (byte) per «cc 1 7» / «pb 1 0» / «note 1 60» con valore 0..1 (o bool per i LED)."""
    parts = spec.split()
    kind, ch = parts[0], max(1, min(16, int(parts[1]))) - 1
    v = 1.0 if value is True else (0.0 if value is False else max(0.0, min(1.0, float(value))))
    if kind == "cc":
        return [0xB0 | ch, int(parts[2]) & 0x7F, int(round(v * 127))]
    if kind == "pb":
        p = int(round(v * 16383))
        return [0xE0 | ch, p & 0x7F, (p >> 7) & 0x7F]
    if kind == "note":
        return [0x90 | ch, int(parts[2]) & 0x7F, 127 if v >= 0.5 else 0]
    return None


def raw(spec, value):
    """Valore come lo vede il controller (0..127, o 0..16383 per il pitch bend): per confrontare."""
    m = encode(spec, value)
    if m is None:
        return None
    if spec.startswith("pb"):
        return m[1] | (m[2] << 7)
    return m[2]


class MidiFeedback:
    def __init__(self, win):
        self.w = win
        self.map = {}             # porta d'ingresso (etichetta) -> porta di ritorno ("auto" = stessa scheda)
        self.sent = {}            # (porta di ritorno, spec) -> valore grezzo inviato
        self.incoming = {}        # (port_key ingresso, spec) -> (istante, valore grezzo)
        self.errors = set()
        self._was_held = False

    # ------------------------------------------------------------------ configurazione
    def configure(self, settings):
        self.map = {k: v for k, v in (settings.get("midi_feedback") or {}).items() if v}
        self.reset()

    def reset(self):
        """Al prossimo giro si rimandano tutti i valori."""
        self.sent.clear()
        self.errors.clear()

    def out_port(self, in_port):
        from .remote import midi_out
        want = self.map.get(in_port)
        if not want:
            return None
        outs = midi_out.list_ports()
        if want == "auto":
            return next((o for o in outs if port_key(o) == port_key(in_port)), None)
        return next((o for o in outs if port_key(o) == port_key(want)), None)

    # ------------------------------------------------------------------ messaggi dal controller
    def note_incoming(self, spec, value, port):
        """Un controllo è stato mosso sul controller: per un po' non gli si rimanda il suo valore."""
        body = split_port(normalize_control(spec) or normalize_spec(spec))[0]
        if body:
            r = raw(body, value)
            self.incoming[(port_key(port), body)] = (time.monotonic(), r)

    # ------------------------------------------------------------------ valori da mandare
    def targets(self):
        """[(spec «cc 1 7», porta richiesta "" = tutte, valore 0..1 o bool)]."""
        w = self.w
        ctrl, sh = w.ctrl, w.show_data
        s = sh.settings
        out = []

        def level(cue):
            inst = ctrl.instances.get(cue.id)
            db = float(cue.level_db)
            g = getattr(inst, "live_gain", 1.0) if inst is not None else 1.0
            if g and g > 0:
                db += 20 * math.log10(g)
            else:
                db = MIN_DB
            return db_to_fader(db)

        def opacity(cue):
            inst = ctrl.instances.get(cue.id)
            op = float(cue.opacity)
            if inst is not None:
                op *= float(getattr(inst, "live_op", 1.0) or 0.0)
            return max(0.0, min(1.0, op))

        def add(spec, value):
            body, port = split_port(spec)
            if body and value is not None:
                out.append((body, port, value))

        for c in sh.all_cues():
            if c.midi_vol and c.type in ("audio", "video"):
                add(normalize_control(c.midi_vol), level(c))
            if c.midi_op and c.type in ("video", "image"):
                add(normalize_control(c.midi_op), opacity(c))
            if c.midi:
                sp = normalize_spec(c.midi)
                if sp and not sp.startswith("pc"):
                    add(sp, ctrl.cue_state(c.id) is not None)
        mc = s.get("midi_controls") or {}
        cur = w._current_cue() if hasattr(w, "_current_cue") else None
        if cur is not None:
            if mc.get("vol_selected") and cur.type in ("audio", "video"):
                add(normalize_control(mc["vol_selected"]), level(cur))
            if mc.get("op_selected") and cur.type in ("video", "image"):
                add(normalize_control(mc["op_selected"]), opacity(cur))
        if mc.get("xfade") and ctrl.xf_manual is None:
            add(normalize_control(mc["xfade"]), float(ctrl.xf_rest or 0.0))
        mm = s.get("midi_map") or {}
        d = w.audio.duck.cfg
        for key, state in (("dim", bool(d.get("dim_on"))), ("duck", bool(d.get("master_on"))),
                           ("pause_all", bool(ctrl.paused_all))):
            sp = normalize_spec(mm.get(key) or "")
            if sp and not sp.startswith("pc"):
                add(sp, state)
        return out

    # ------------------------------------------------------------------ invio
    def tick(self):
        from .outputs import held
        from .remote import midi_out
        if held():                                    # ridondanza: il ritorno lo manda il master
            self._was_held = True
            return
        if self._was_held:
            self._was_held = False
            self.reset()
        if not self.map:
            return
        now = time.monotonic()
        devices = []
        for in_port in self.map:
            o = self.out_port(in_port)
            if o is not None:
                devices.append((in_port, o))
        if not devices:
            return
        try:
            targets = self.targets()
        except Exception:                             # il ritorno non deve mai fermare lo show
            return
        for body, want, value in targets:
            r = raw(body, value)
            if r is None:
                continue
            for in_port, out in devices:
                if not port_matches(want, in_port):
                    continue
                key = (out, body)
                if self.sent.get(key) == r:
                    continue
                ik = (port_key(in_port), body)
                last = self.incoming.get(ik)
                if last is not None:
                    if now - last[0] < QUIET_S:         # lo si sta muovendo: niente rimbalzi
                        continue
                    del self.incoming[ik]
                    if abs((last[1] or 0) - r) <= SETTLE:
                        self.sent[key] = r              # il controller è già lì
                        continue
                try:
                    midi_out.send_bytes(out, encode(body, value))
                    self.sent[key] = r
                except Exception as e:  # noqa: BLE001
                    if out not in self.errors:
                        self.errors.add(out)
                        self.w.status(tr("MIDI ritorno → {0}: {1}", out, e))
