"""Modello dati: cue e show, con salvataggio/caricamento JSON."""
import copy
import json
import os
import uuid

from .system import local_path, portable_rel

CUE_TYPES = ["audio", "video", "image", "text", "group", "groupexit", "fade", "preset", "control", "stoppanel",
             "osc", "midiout", "projector", "mixer", "wait", "memo"]
TYPE_LABELS = {
    "audio": "Audio", "video": "Video", "image": "Immagine", "fade": "Fade",
    "preset": "Cambia preset", "control": "Comando", "osc": "OSC", "projector": "Proiettore", "mixer": "Mixer",
    "group": "Gruppo", "groupexit": "Esci dal gruppo", "text": "Testo", "midiout": "MIDI out",
    "wait": "Attesa", "memo": "Memo", "stoppanel": "Stop panel",
}
TYPE_ICONS = {
    "audio": "♪", "video": "▶", "image": "▣", "fade": "◢", "preset": "⇢", "control": "⌘", "projector": "⌖",
    "osc": "⇄", "wait": "⧗", "memo": "✎", "mixer": "≣", "group": "◫", "groupexit": "⇥", "text": "T", "midiout": "♫",
    "stoppanel": "⊠",
}

# campi salvati nei preset (per tipo di preset)
PRESET_KINDS = {
    "audio": ["level_db", "fade_in", "fade_out", "matrix", "out_levels"],
    "visual": ["crop_l", "crop_t", "crop_r", "crop_b", "off_x", "off_y", "scale", "warp", "opacity", "fit"],
}
PRESET_LABELS = {"audio": "Preset audio", "visual": "Preset crop / posizione"}
TRIGGERS = ["manual", "with_prev", "after_prev"]
TRIGGER_LABELS = {
    "manual": "Manuale (GO)",
    "with_prev": "Insieme alla precedente",
    "after_prev": "Alla fine della precedente",
}
TRIGGER_SHORT = {"manual": "GO", "with_prev": "⇉ con prec.", "after_prev": "↳ dopo prec."}

CONTROL_ACTIONS = ["start", "stop", "pause", "resume", "stop_all", "playhead"]
CONTROL_LABELS = {
    "start": "Play (avvia target)", "stop": "Stop target", "pause": "Pausa target",
    "resume": "Riprendi target", "stop_all": "Stop tutto", "playhead": "Sposta playhead su target",
}
FADE_SHAPES = ["scurve", "linear", "db"]
FADE_SHAPE_LABELS = {"scurve": "Curva S", "linear": "Lineare (guadagno)", "db": "Lineare in dB"}
FIT_MODES = ["fit", "fill", "stretch", "native"]
FIT_LABELS = {"fit": "Adatta (contenuto intero)", "fill": "Riempi (ritaglia)",
              "stretch": "Deforma (riempi tutto)", "native": "Dimensione originale"}

MIN_DB = -60.0
MAX_DB = 12.0

MEDIA_EXT = {
    "audio": {".wav", ".mp3", ".flac", ".ogg", ".oga", ".aif", ".aiff", ".m4a", ".aac", ".opus", ".wma"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpg", ".mpeg", ".wmv", ".ogv"},
    "image": {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"},
}


def type_for_file(path):
    ext = os.path.splitext(path)[1].lower()
    for t, exts in MEDIA_EXT.items():
        if ext in exts:
            return t
    return None


def db_to_lin(db):
    if db <= MIN_DB + 1e-6:
        return 0.0
    return 10.0 ** (db / 20.0)


class Cue:
    FIELDS = {
        # comuni
        "number": "", "name": "", "trigger": "manual", "delay": 0.0, "notes": "",
        "midi": "",              # trigger MIDI per questa cue (es. "note 1 60")
        "midi_vol": "",          # controllo MIDI continuo per il volume (es. "cc 1 7")
        "midi_op": "",           # controllo MIDI continuo per la trasparenza (es. "cc 1 8")
        "osc_trigger": "",       # indirizzo OSC aggiuntivo che avvia questa cue
        # audio / video
        "file": "", "start": 0.0, "end": 0.0, "loop": False, "loop_start": 0.0,
        "loop_end": 0.0, "loop_count": 0, "level_db": 0.0, "fade_in": 0.0,
        "fade_out": 0.0, "curve": [],
        # uscita audio (audio e video): scheda ("" = predefinita), matrice sorgente×uscita in dB
        # (None = incrocio spento; lista vuota = routing predefinito), volume per uscita in dB
        "audio_device": "", "matrix": [], "out_levels": [],
        "video_audio": "engine",   # video: "engine" = motore CueStage, "system" = lettore di sistema
        # video: tenuto pronto in anticipo (file aperto, fermo sul primo fotogramma) e loop senza attesa
        "preload": True,
        # ducking: riduzione in dB (positiva, 0 = spento) quando l'ingresso del ducking supera la soglia;
        # su un gruppo vale per tutte le sue cue (che non ne hanno uno proprio)
        "duck_db": 0.0,
        # preascolto: la cue (o tutte le cue del gruppo) va solo alla scheda/canali del preascolto
        "pfl": False,
        # video / immagine
        "layer": 1, "opacity": 1.0, "fit": "fit", "duration": 0.0,
        # crop: pixel del sorgente da tagliare per lato
        "crop_l": 0, "crop_t": 0, "crop_r": 0, "crop_b": 0,
        # spostamento in pixel dell'uscita (risoluzione reale dello schermo/proiettore)
        "off_x": 0, "off_y": 0,
        "scale": 100.0,               # % rispetto all'adattamento scelto
        # deformazione prospettica: spostamento (px uscita) dei 4 angoli alto-sx, alto-dx, basso-dx, basso-sx
        "warp": [[0, 0], [0, 0], [0, 0], [0, 0]],
        # fade
        "target": "", "fade_duration": 3.0, "fade_shape": "scurve",
        "fade_level": True, "target_db": MIN_DB, "fade_opacity": True,
        "target_opacity": 0.0, "stop_when_done": False, "start_if_idle": False,
        # preset propri di questa cue: tipo -> nome -> {campo: valore}
        "presets": {"audio": {}, "visual": {}},
        # keyframe di preset nel tempo della cue: [{"t": s dall'avvio, "kind": "visual"|"audio",
        # "preset": nome o "__base__"}] — tra due keyframe i valori sfumano dall'uno all'altro
        "preset_points": [],
        # cambia preset (usa anche target, fade_duration, fade_shape): nomi di preset della cue target
        "preset_audio": "", "preset_visual": "",
        # comando
        "action": "start", "stop_fade": 0.0,
        # stop panel: id dei PANEL da fermare ("*" = tutti, anche quelli creati dopo); fade = stop_fade
        "sp_panels": [],
        # proiettore (via rete, driver/plugin)
        "proj_target": "", "proj_action": "power_on", "proj_value": "", "proj_dir": 1,
        "proj_steps": 1, "proj_speed": "normal",
        # cue Mixer: mixer, azione (mute / unmute / fader), sezione, canali («1-4, 7»), livello dB, fade s
        "mix_target": "", "mix_action": "mute", "mix_kind": "ch", "mix_channels": "1",
        "mix_level": 0.0, "mix_fade": 0.0,
        # azione «duck»: i fader seguono la curva del ducking; mix_level = livello normale, riduzione in dB
        "mix_duck_db": 10.0,
        # mute a scena: {sezione: {"mute": "1-4", "on": "7"}} (i canali non elencati non si toccano)
        "mix_states": {},
        # azione «control» (schede audio): chiave del controllo e valore grezzo
        "mix_ctl": "", "mix_ctl_value": 0,
        # aspetto e comandi rapidi: colore (casella della griglia e riga della lista, "#rrggbb" o vuoto),
        # scorciatoia da tastiera (QKeySequence in formato portabile, es. "F5", "Ctrl+1"),
        # posizione nella griglia del PANEL GRID: [pagina, riga, colonna] (vuota = da assegnare)
        "color": "", "hotkey": "", "grid_pos": [],
        # gruppi: «group» = id della cue Gruppo a cui appartiene la cue (vuoto = nessuno)
        "group": "",
        "group_mode": "sequence",   # sequence · random · loop · rloop · all · playlist · rplaylist
        "group_hold": 10.0,         # loop: secondi di permanenza di ogni cue (0 = la sua durata)
        "group_xfade": 2.0,         # loop: crossfade tra una cue e la successiva (s)
        "group_advance": "time",    # rloop: «time» avanzamento a tempo · «go» avanza col GO
        "group_loop": False,        # playlist / random playlist: ricomincia dopo l'ultima cue
        # Tap Tempo (sequence, loop, random loop): una cue ogni «div» battiti al tempo «bpm», come un GO;
        # il tempo si batte con un tasto, una nota/CC MIDI o un messaggio OSC
        "group_tap": False, "group_tap_div": 1.0, "group_bpm": 120.0,
        "group_tap_key": "", "group_tap_midi": "", "group_tap_osc": "",
        "group_exit": "fade",       # uscita da un loop: «fade» sfuma la cue corrente · «finish» la lascia finire
        "collapsed": False,
        # cue Testo (sottotitoli / sopratitoli): «sequence» = una frase per GO (frasi separate da una riga
        # vuota), «free» = tutto il testo insieme
        "text_mode": "sequence", "text_content": "",
        "text_area": [5.0, 78.0, 90.0, 18.0],      # x, y, larghezza, altezza in % dell'uscita
        "text_halign": "center", "text_valign": "bottom",
        "text_font": "Sans Serif", "text_size": 48.0, "text_bold": False, "text_italic": False,
        "text_color": "#ffffff", "text_outline": 2.0, "text_outline_color": "#000000",
        "text_bg": "", "text_bg_opacity": 60.0, "text_bg_mode": "block", "text_padding": 12.0,
        "text_fit": "shrink", "text_max_lines": 2, "text_spacing": 100.0, "text_fade": 0.3,
        # cue MIDI out: porta (vuota = predefinita delle impostazioni) e messaggi, uno per riga
        "mo_port": "", "mo_msgs": "",
        # osc
        "osc_host": "127.0.0.1", "osc_port": 53000, "osc_address": "/", "osc_args": "",
        # OSC da profilo: dispositivo (id in settings["osc_devices"]), comando e parametri
        "osc_device": "", "osc_cmd": "", "osc_params": {},
    }

    def __init__(self, type="audio", **kw):
        for k, v in self.FIELDS.items():
            setattr(self, k, copy.deepcopy(v))
        self.id = uuid.uuid4().hex
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)

    def label(self):
        n = f"{self.number} " if self.number else ""
        return f"{n}{self.name or TYPE_LABELS.get(self.type, self.type)}"

    def is_media(self):
        return self.type in ("audio", "video", "image")

    def is_visual(self):
        return self.type in ("video", "image", "text")

    def to_dict(self, base_dir=None):
        d = {"id": self.id, "type": self.type}
        for k in self.FIELDS:
            d[k] = copy.deepcopy(getattr(self, k))
        if self.file and base_dir:
            try:
                d["file_rel"] = portable_rel(os.path.relpath(self.file, base_dir))
            except ValueError:
                pass
        return d

    @classmethod
    def from_dict(cls, d, base_dir=None):
        c = cls(d.get("type", "memo"))
        c.id = d.get("id") or c.id
        for k in cls.FIELDS:
            if k in d:
                setattr(c, k, d[k])
        from .keyframes import migrate_points
        c.preset_points = migrate_points(c.preset_points)
        if c.type == "mixer" and "mix_states" not in d:
            from .mixers import migrate_mute_cue
            migrate_mute_cue(c)
        if c.file and not os.path.exists(c.file) and base_dir and d.get("file_rel"):
            alt = os.path.normpath(os.path.join(base_dir, local_path(d["file_rel"])))
            if os.path.exists(alt):
                c.file = alt
        return c

    # ---- preset della cue
    def preset_dict(self, kind):
        if not isinstance(self.presets, dict):
            self.presets = {}
        return self.presets.setdefault(kind, {})

    def preset_values(self, kind):
        return {k: copy.deepcopy(getattr(self, k)) for k in PRESET_KINDS[kind]}

    def apply_preset(self, kind, name):
        p = self.preset_dict(kind).get(name)
        if p is None:
            return False
        for k, v in p.items():
            if k in PRESET_KINDS[kind]:
                setattr(self, k, copy.deepcopy(v))
        return True

    def clone(self):
        c = Cue.from_dict(self.to_dict())
        c.id = uuid.uuid4().hex
        return c


DEFAULT_SETTINGS = {
    "audio_device": None,      # id della scheda predefinita del progetto (None = di sistema)
    "device_channels": {},     # id scheda -> canali usati (0/assente = automatico)
    "master_db": 0.0,
    "stop_fade": 0.3,
    "audio_buffer": "normale",
    "ducking": {},
    "preview": {},             # preascolto: {enabled, device, ch_l, ch_r, db} (vedi audio_engine.preview_settings)             # ingresso, canale, soglia, attacco, rilascio, Master ducking (vedi ducking.py)
    # istanze secondarie: questa istanza accetta slave / ridondanze sulla porta TCP indicata
    "cluster_enabled": False, "cluster_port": 53900, "cluster_name": "", "cluster_slave_edit": False,
    "osc_in_enabled": True,
    "osc_in_port": 53000,
    "osc_devices": [],          # [{id, name, profile, host, port}] dispositivi OSC da profilo
    "midi_in_port": "",
    "midi_map": {
        "go": "", "stop_all": "", "pause_all": "", "resume_all": "",
        "next": "", "prev": "", "xfade_go": "", "dim": "", "duck": "",
    },
    # controlli MIDI continui (fader/potenziometri): "cc 1 7", "pb 1 0" (pitch bend), "pc 1 *"
    "midi_in_ports": [],
    "midi_out_port": "",   # uscita MIDI predefinita delle cue «MIDI out»
    "midi_filters": {},    # scheda -> {types, channels, ignore_cc, ignore_notes, no_learn}   # porte MIDI in ingresso aperte insieme
    "midi_controls": {"vol_selected": "", "op_selected": "", "xfade": ""},
    "xfade_time": 3.0,     # durata del crossfade automatico (s)
    "output_screen": -1,
    "output_fullscreen": True,
    "output_corners": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
    "output_opengl": True,
    "output_grid": False,
    "video_preload": True,     # tiene pronti i video delle cue con «Precarica» (vedi video_deck.py)
    "edit_blind": False,
    "go_target": "main",
    "web_enabled": False,  # interfaccia web
    "web_port": 8080,
    "web_pin": "",
    "web_projectors": True,  # pannello proiettori nell'interfaccia web   # GO su: "main" = sempre la MAIN · "panel" = la lista selezionata
    "projectors": [],
    "mixers": [],          # [{id, name, driver, host, port, midi_ch, model, fader_law, midi_out, midi_in, device}]      # [{id, name, driver, host, port, user, password, proj_id}]   # True = le modifiche nella scheda crop non vanno sull'uscita
}


MAIN_ID = "main"


class CueList:
    """Una lista di cue: la MAIN oppure un PANEL."""

    GRID_DEFAULTS = {"kind": "list", "grid_rows": 4, "grid_cols": 6, "grid_pages": ["Pagina 1"],
                     "grid_click": "toggle",
                     "grid_solo": False,     # Solo: una cue che parte ferma le altre attive del GRID
                     "grid_fade": 0.5}       # fade out (s) degli stop del GRID (clic, Solo, STOP del PANEL)

    def __init__(self, name, cues=None, list_id=None, kind="list"):
        self.id = list_id or uuid.uuid4().hex[:8]
        self.name = name
        self.cues = cues if cues is not None else []
        for k, v in self.GRID_DEFAULTS.items():
            setattr(self, k, copy.deepcopy(v))
        self.kind = kind            # «list» = lista classica · «grid» = caselle (con pagine)

    @property
    def is_grid(self):
        return self.kind == "grid"

    def grid_data(self):
        return {k: copy.deepcopy(getattr(self, k)) for k in self.GRID_DEFAULTS}

    def set_grid_data(self, d):
        for k, v in self.GRID_DEFAULTS.items():
            val = d.get(k, v)
            setattr(self, k, copy.deepcopy(val))
        self.grid_rows = max(1, min(16, int(self.grid_rows or 4)))
        self.grid_cols = max(1, min(16, int(self.grid_cols or 6)))
        if not isinstance(self.grid_pages, list) or not self.grid_pages:
            self.grid_pages = ["Pagina 1"]
        self.grid_solo = bool(self.grid_solo)
        try:
            self.grid_fade = max(0.0, min(60.0, float(self.grid_fade)))
        except (TypeError, ValueError):
            self.grid_fade = 0.5

    @property
    def is_main(self):
        return self.id == MAIN_ID

    def index_of(self, cue_id):
        for i, c in enumerate(self.cues):
            if c.id == cue_id:
                return i
        return -1


class Show:
    VERSION = 1

    def __init__(self):
        self.lists = [CueList("MAIN", list_id=MAIN_ID)]   # [0] = MAIN, poi i PANEL
        self.view = 0                                      # lista visualizzata/modificata
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.path = None
        self.dirty = False

    # ---- liste
    @property
    def main(self):
        return self.lists[0]

    @property
    def current(self):
        if not (0 <= self.view < len(self.lists)):
            self.view = 0
        return self.lists[self.view]

    @property
    def cues(self):
        """Le cue della lista visualizzata (la MAIN o il PANEL scelto)."""
        return self.current.cues

    @cues.setter
    def cues(self, value):
        self.current.cues = value

    @property
    def panels(self):
        return self.lists[1:]

    def list_by_id(self, list_id):
        for lst in self.lists:
            if lst.id == list_id:
                return lst
        return None

    def list_of(self, cue_id):
        """(lista, indice) della cue in tutto lo show; (None, -1) se non c'è."""
        for lst in self.lists:
            i = lst.index_of(cue_id)
            if i >= 0:
                return lst, i
        return None, -1

    def all_cues(self):
        for lst in self.lists:
            yield from lst.cues

    def add_panel(self, name=None, kind="list"):
        n = len(self.lists)
        names = {l.name for l in self.lists}
        name = name or f"PANEL {n}"
        while name in names:
            n += 1
            name = f"PANEL {n}"
        lst = CueList(name, kind=kind)
        self.lists.append(lst)
        self.dirty = True
        return lst

    # ---- ricerca (in tutto lo show: i target possono stare in un'altra lista)
    def index_of(self, cue_id):
        """Indice nella lista visualizzata."""
        return self.current.index_of(cue_id)

    def by_id(self, cue_id):
        for c in self.all_cues():
            if c.id == cue_id:
                return c
        return None

    def by_number(self, number):
        number = str(number).strip()
        if not number:
            return None
        for lst in self.lists:      # MAIN prima, poi i PANEL: non dipende dalla lista visualizzata
            for c in lst.cues:
                if str(c.number).strip() == number:
                    return c
        return None

    def resolve_target(self, ref):
        """Un target può essere il numero della cue o il suo id."""
        return self.by_number(ref) or self.by_id(ref)

    def next_number(self):
        best = 0
        for c in self.all_cues():          # numeri unici in tutto lo show
            try:
                best = max(best, int(float(c.number)))
            except (TypeError, ValueError):
                pass
        return str(best + 1)

    PRESET_TYPES = {"audio": ("audio", "video"), "visual": ("video", "image")}

    def _migrate_global_presets(self, legacy):
        """Show salvati con i preset globali (versione precedente): ogni preset va nelle cue
        a cui serve — target delle cue «Cambia preset» che lo usano, oppure la cue che ha
        esattamente quei valori (quella da cui era stato creato); se nessuna, a tutte le cue
        compatibili, così non si perde nulla."""
        for kind, items in legacy.items():
            if kind not in PRESET_KINDS or not isinstance(items, dict):
                continue
            field = "preset_audio" if kind == "audio" else "preset_visual"
            compatible = [c for c in self.all_cues() if c.type in self.PRESET_TYPES[kind]]
            for name, values in items.items():
                owners = []
                for pc in self.all_cues():
                    if pc.type == "preset" and getattr(pc, field) == name:
                        t = self.resolve_target(pc.target)
                        if t is not None and t.type in self.PRESET_TYPES[kind] and t not in owners:
                            owners.append(t)
                if not owners:
                    owners = [c for c in compatible
                              if all(getattr(c, k, None) == v for k, v in values.items())]
                if not owners:
                    owners = compatible
                for c in owners:
                    c.preset_dict(kind).setdefault(name, copy.deepcopy(values))
        if legacy:
            self.dirty = True

    def to_data(self, base_dir):
        return {
            "app": "CueStage", "version": self.VERSION,
            "settings": self.settings,
            "original_path": self.path,
            "cues": [c.to_dict(base_dir) for c in self.main.cues],
            "panels": [dict({"id": l.id, "name": l.name, "cues": [c.to_dict(base_dir) for c in l.cues]},
                            **l.grid_data()) for l in self.panels],
        }

    def save(self, path, copy_only=False):
        """Salva lo show. Con copy_only (autosave) non cambia percorso né stato «modificato»."""
        base = os.path.dirname(os.path.abspath(path))
        data = self.to_data(base)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        if not copy_only:
            self.path = path
            self.dirty = False

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        s = cls.from_data(data, os.path.dirname(os.path.abspath(path)))
        s.path = path
        return s

    @classmethod
    def from_data(cls, data, base):
        """Show dai dati di to_data (file o rete); i percorsi relativi si risolvono rispetto a «base»."""
        s = cls()
        s.settings.update(data.get("settings", {}))
        mm = copy.deepcopy(DEFAULT_SETTINGS["midi_map"])
        mm.update(s.settings.get("midi_map") or {})
        s.settings["midi_map"] = mm
        mc = copy.deepcopy(DEFAULT_SETTINGS["midi_controls"])
        mc.update(s.settings.get("midi_controls") or {})
        s.settings["midi_controls"] = mc
        if not s.settings.get("midi_in_ports") and s.settings.get("midi_in_port"):   # versioni precedenti
            s.settings["midi_in_ports"] = [s.settings["midi_in_port"]]
        s.main.cues = [Cue.from_dict(d, base) for d in data.get("cues", [])]
        for p in data.get("panels") or []:
            if isinstance(p, dict):
                lst = CueList(str(p.get("name") or "PANEL"), [Cue.from_dict(d, base) for d in p.get("cues", [])],
                              p.get("id") if p.get("id") != MAIN_ID else None)
                lst.set_grid_data(p)
                s.lists.append(lst)
        s._migrate_global_presets(data.get("presets") or {})
        s.original_path = data.get("original_path")
        return s
