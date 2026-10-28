"""Controller della riproduzione: GO, catene di cue, istanze in esecuzione."""
from .i18n import tr
import math
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from .audio_engine import Voice, build_gains
from .media import SR, Envelope, curve_arrays, curve_gain
from . import keyframes as kf
from .model import db_to_lin
from .remote import osc_send

MAX_CHAIN = 500


class CueView:
    """Vista di una cue per l'istanza in riproduzione.

    Legge i valori dalla cue (così le modifiche fatte nell'ispettore si vedono dal vivo), tranne
    quelli sovrascritti da un «Cambia preset», che valgono solo per questa esecuzione."""

    def __init__(self, cue):
        self.__dict__["_cue"] = cue
        self.__dict__["_ov"] = {}

    def __getattr__(self, k):
        ov = self.__dict__["_ov"]
        return ov[k] if k in ov else getattr(self.__dict__["_cue"], k)

    def override(self, k, v):
        self.__dict__["_ov"][k] = v


class Instance:
    # fattori dal vivo: fader MIDI (live_*) e crossfade (xf_*), moltiplicati a volume e opacità
    live_gain = 1.0
    live_op = 1.0
    xf_gain = 1.0
    xf_op = 1.0

    def __init__(self, ctrl, cue, cascade):
        self.ctrl = ctrl
        self.cue = cue
        self.key = cue.id
        self.cascade = cascade
        self.paused = False
        self.done = False
        self.user_stopped = False
        self.elapsed = 0.0
        self.started_at = ctrl.clock
        # avviata da una Playlist: suona una volta sola, anche se la cue ha il loop
        self.no_loop = getattr(ctrl, "_play_once", None) == cue.id
        # BLIND: cue di un GRID in prova cieca: tutto funziona (tempi, barre, vumeter) ma niente audio né video
        blind = getattr(ctrl, "blind_lists", None)
        self.blind = False
        if blind:
            lst, _i = ctrl.show.list_of(cue.id)
            self.blind = lst is not None and lst.id in blind
        # ducking: la cue e i gruppi che la contengono (vale il primo con una riduzione)
        self._duck_chain = [cue]
        seen = {cue.id}
        gid = getattr(cue, "group", "")
        while gid and gid not in seen and len(self._duck_chain) < 16:
            seen.add(gid)
            g = ctrl.show.by_id(gid) if getattr(ctrl, "show", None) is not None else None
            if g is None:
                break
            self._duck_chain.append(g)
            gid = getattr(g, "group", "")
        pend = getattr(ctrl, "_xf_incoming", None)
        if pend is not None and pend == cue.id:      # cue che entra con un crossfade: parte da zero
            self.xf_gain = 0.0
            self.xf_op = 0.0

    @property
    def loop_on(self):
        return bool(self.cue.loop) and not self.no_loop

    @property
    def pfl(self):
        """Preascolto: la cue (o un gruppo che la contiene) va solo alla scheda del preascolto."""
        pv = getattr(getattr(self.ctrl, "audio", None), "preview", None)
        return bool(pv and pv.get("enabled")) and any(getattr(c, "pfl", False) for c in self._duck_chain)

    @property
    def duck_db(self):
        """Riduzione del ducking (dB) di questa esecuzione: quella della cue o del gruppo più vicino."""
        for c in self._duck_chain:
            d = float(getattr(c, "duck_db", 0.0) or 0.0)
            if d > 0:
                return d
        return 0.0

    def duck_gain(self):
        return self.ctrl.audio.duck.gain(self.duck_db)

    @property
    def trim_gain(self):
        return self.live_gain * self.xf_gain

    @property
    def trim_op(self):
        return 0.0 if self.blind else self.live_op * self.xf_op

    # interfaccia
    def start(self, silent=False):
        pass

    def tick(self, now, dt):
        if not self.paused:
            self.elapsed += dt

    def stop(self, fade=0.0):
        self.user_stopped = True
        self.finish()

    def pause(self, on):
        self.paused = on

    def fade_to(self, gain, opacity, dur, shape, stop_after):
        pass

    def total(self):
        """Durata totale in secondi, None se infinita/sconosciuta."""
        return 0.0

    def position(self):
        return self.elapsed

    def finish(self):
        if not self.done:
            self.done = True
            self.cleanup()
            self.ctrl._finished(self)

    def cleanup(self):
        pass

    def progress(self):
        t = self.total()
        if not t:
            return None
        return max(0.0, min(1.0, self.elapsed / t))


# ------------------------------------------------------------------ preset / keyframe

VIS_MORPH = ("crop_l", "crop_t", "crop_r", "crop_b", "off_x", "off_y", "scale")


def _warp_list(w):
    w = [list(map(float, p)) for p in (w or [])]
    return (w + [[0.0, 0.0]] * 4)[:4]


class Morph:
    """Transizione dei valori visivi (crop, posizione, scala, prospettiva) di un'istanza verso un preset."""

    def __init__(self, view, pv, dur, shape):
        self.view = view
        self.start = {k: float(getattr(view, k)) for k in VIS_MORPH if k in pv}
        self.end = {k: float(pv[k]) for k in self.start}
        self.w0 = _warp_list(getattr(view, "warp", None))
        self.w1 = _warp_list(pv.get("warp")) if "warp" in pv else None
        self.dur = max(0.0, dur)
        self.shape = "linear" if shape == "db" else shape
        self.elapsed = 0.0
        self.done = False
        if "fit" in pv:
            view.override("fit", pv["fit"])
        self._apply(0.0 if self.dur > 0 else 1.0)

    def _apply(self, t):
        from .media import fade_shape
        f = float(fade_shape(t, self.shape))
        for k in self.start:
            self.view.override(k, self.start[k] + (self.end[k] - self.start[k]) * f)
        if self.w1 is not None:
            self.view.override("warp", [[a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]
                                        for a, b in zip(self.w0, self.w1)])

    def step(self, dt):
        if self.done:
            return
        self.elapsed += dt
        t = self.elapsed / self.dur if self.dur > 0 else 1.0
        self._apply(min(1.0, t))
        if t >= 1.0:
            self.done = True


class AudioMorph:
    """Transizione di livello, matrice e volumi delle uscite verso un preset audio (Cambia preset)."""

    def __init__(self, s0, target, name, n_src, dur, shape):
        self.s0, self.target, self.name, self.n_src = s0, target, name, n_src
        self.dur = max(0.0, dur)
        self.shape = "linear" if shape == "db" else shape
        self.elapsed = 0.0

    def step(self, dt):
        self.elapsed += dt

    def __call__(self, n_out):
        from .media import fade_shape
        s1 = kf.audio_values(self.target, self.name, self.n_src, n_out)
        s0 = self.s0 if len(self.s0[2]) == n_out else s1
        f = float(fade_shape(min(1.0, self.elapsed / self.dur) if self.dur > 0 else 1.0, self.shape))
        return kf.interp_audio(s0, s1, f)


def start_preset(inst, target, vis_name, aud_name, dur, shape):
    """«Cambia preset»: porta un'istanza in esecuzione verso un preset della sua cue. La corsia
    interessata smette di seguire i keyframe e parte dal valore che ha in quel momento."""
    voice = getattr(inst, "voice", None)
    if aud_name:
        if voice is not None and voice.n_out():
            n_out = voice.n_out()
            cur = inst._aud_override(n_out) if inst._aud_override else \
                kf.audio_values(target, kf.BASE, voice.n_src, n_out)
            inst.kf_aud_off = True
            inst._aud_override = AudioMorph(cur, target, aud_name, voice.n_src, dur, shape)
        elif target.type in ("audio", "video"):        # video con il lettore di sistema
            pa = target.preset_dict("audio").get(aud_name) or {}
            inst.fade_to(db_to_lin(float(pa.get("level_db", target.level_db))), None, dur, shape, False)
    view = getattr(inst, "view", None)
    if vis_name and view is not None:
        pv = target.preset_dict("visual").get(vis_name) or {}
        inst.kf_vis_off = True
        env = getattr(inst, "env_op", None)
        if env is not None and inst.kf_op_ratio != 1.0:      # l'opacità riparte dal valore attuale
            env.set(min(1.0, env.get(inst.ctrl.clock) * inst.kf_op_ratio))
            inst.kf_op_ratio = 1.0
        if "opacity" in pv:
            inst.fade_to(None, float(pv["opacity"]), dur, shape, False)
        inst.morph = Morph(view, pv, dur, shape)


def state_at(cue, T):
    """Stato visivo della cue al tempo T secondo i keyframe (per l'anteprima nella scheda crop: è lo
    stesso calcolo della riproduzione). Restituisce la cue se a quel tempo valgono i valori base."""
    st = kf.visual_state(cue, T)
    if st is None:
        return cue
    vals, na, nb, f = st
    if kf.is_base(na, nb, f):
        return cue
    view = CueView(cue)
    for k in kf.VIS_NUM:
        view.override(k, vals[k])
    view.override("warp", vals["warp"])
    view.override("fit", vals["fit"])
    view.active = (na, nb, f)
    return view


class KeyframeMixin:
    """Applica i keyframe (corsie immagine e audio) al tempo della cue in riproduzione."""
    morph = None
    kf_op_ratio = 1.0

    def _init_kf(self):
        self.morph = None
        self.kf_vis_off = False
        self.kf_aud_off = False
        self.kf_op_ratio = 1.0
        self.kf_level_ratio = 1.0
        self._aud_override = None
        self._kf_T = 0.0

    def cue_time(self):
        return self.elapsed

    def _tick_kf(self, dt):
        T = self.cue_time()
        self._kf_T = T
        c = self.cue
        view = getattr(self, "view", None)
        if self.morph is not None:
            self.morph.step(dt)
        elif not self.kf_vis_off and view is not None:
            st = kf.visual_state(c, T)
            if st is not None:
                vals = st[0]
                for k in VIS_MORPH:
                    view.override(k, vals[k])
                view.override("warp", vals["warp"])
                view.override("fit", vals["fit"])
                base = float(c.opacity)
                self.kf_op_ratio = vals["opacity"] / base if base > 1e-3 else vals["opacity"]
            else:
                self.kf_op_ratio = 1.0
        voice = getattr(self, "voice", None)
        if isinstance(self._aud_override, AudioMorph):
            self._aud_override.step(dt)
        elif voice is not None:
            if not self.kf_aud_off and kf.track(c, "audio"):
                self._aud_override = lambda n, self=self: kf.audio_state(
                    self.cue, self._kf_T, self.voice.n_src, n)[:3]
            else:
                self._aud_override = None
        elif c.type == "video" and not self.kf_aud_off:       # lettore di sistema: solo il livello
            st = kf.audio_state(c, T, 1, 2)
            base = db_to_lin(c.level_db)
            self.kf_level_ratio = (db_to_lin(st[0]) / base if base > 1e-6 else 0.0) if st else 1.0


# ------------------------------------------------------------------ audio

class RoutedVoice:
    """Voce del motore audio con scheda e matrice della cue (aggiornate dal vivo). I keyframe audio e
    i «Cambia preset» sostituiscono livello/matrice/volumi uscite tramite _aud_override."""
    voice = None
    _aud_override = None     # funzione(n_out) -> (livello dB, matrice dB, volumi uscite dB) oppure None

    def _out_dev(self):
        """Scheda su cui suona: quella del preascolto se la cue è in preascolto."""
        if self.pfl:
            return self.ctrl.audio.preview.get("device") or None
        return self.cue.audio_device

    def _gains(self, n_out):
        c = self.cue
        n_src = self.voice.n_src
        trim = self.trim_gain
        if self.pfl:
            from .audio_engine import preview_matrix
            pv = self.ctrl.audio.preview
            return preview_matrix(n_src, n_out, int(pv.get("ch_l", 1)), int(pv.get("ch_r", 2))) * trim
        ov = self._aud_override(n_out) if self._aud_override else None
        if ov is None:
            return build_gains(c.matrix, c.out_levels, n_src, n_out) * trim
        lvl, mat, lev = ov
        base = db_to_lin(c.level_db)
        # il livello base è già nell'inviluppo della voce: qui va solo il rapporto
        return build_gains(mat, lev, n_src, n_out) * (db_to_lin(lvl) / base if base > 1e-6 else 0.0) * trim

    def _start_voice(self, data, silent=False, paused=False):
        c = self.cue
        v = Voice(data, c.start, c.end, self.loop_on, c.loop_start, c.loop_end,
                  int(c.loop_count or 0), db_to_lin(c.level_db),
                  curve_arrays(c.curve), 0.0 if silent else c.fade_in, c.fade_out, gains_fn=None)
        self.voice = v
        v.blind = self.blind               # il mixer calcola il vumeter ma non manda il suono all'uscita
        v.duck_db = self.duck_db
        v.pfl = self.pfl
        v.gains_fn = self._gains
        if silent:
            v.env = 0.0
            v.ramp = None
        v.paused = paused
        if v.looping_forever():
            self._total = None
        else:
            n_loops = max(0, v.loops_left) if v.loop else 0
            self._total = ((v.end - v.start) + n_loops * (v.le - v.ls)) / SR
        self._route_sig = self._route_key()
        self.ctrl.audio.play(self.key, v, self._out_dev())
        return v

    def _route_key(self):
        c = self.cue
        ov = ""
        if self._aud_override is not None and self.voice is not None and self.voice.n_out():
            lvl, mat, lev = self._aud_override(self.voice.n_out())
            r = lambda x: None if x is None else round(x, 2)
            ov = repr((r(lvl), [[r(x) for x in row] for row in mat], [r(x) for x in lev]))
        pv = repr(sorted(self.ctrl.audio.preview.items())) if self.pfl else ""
        return (c.audio_device, repr(c.matrix), repr(c.out_levels), c.level_db, ov, round(self.trim_gain, 4), pv)

    def _update_route(self):
        if self.voice is not None:
            self.voice.duck_db = self.duck_db
        k = self._route_key()
        if k != self._route_sig:
            self._route_sig = k
            if self.voice is not None:
                self.voice.pfl = self.pfl
            self.ctrl.audio.route(self.key, self._out_dev(), self._gains)


class AudioInstance(RoutedVoice, KeyframeMixin, Instance):
    def start(self, silent=False):
        data = self.ctrl.loader.audio_now(self.cue.file)
        if not len(data):
            raise RuntimeError(tr("il file non contiene audio"))
        self._init_kf()
        self._start_voice(data, silent)

    def cue_time(self):
        return self.voice.played / SR

    def tick(self, now, dt):
        v = self.voice
        self.elapsed = v.played / SR
        if not self.paused and not self.ctrl.paused_all:
            self._tick_kf(dt)
        self._update_route()
        if v.done:
            self.finish()

    def stop(self, fade=0.0):
        self.user_stopped = True
        if fade > 0 and not self.paused:
            self.ctrl.audio.stop_voice(self.key, fade)
        else:
            self.finish()

    def pause(self, on):
        self.paused = on
        self.ctrl.audio.pause(self.key, on)

    def fade_to(self, gain, opacity, dur, shape, stop_after):
        if gain is not None:
            self.ctrl.audio.fade(self.key, gain, dur, shape, stop_after)
        elif stop_after:
            self.ctrl.audio.stop_voice(self.key, dur)

    def total(self):
        return self._total

    def position(self):
        return self.voice.position()

    def cleanup(self):
        self.ctrl.audio.remove(self.key)


# ------------------------------------------------------------------ video

_graveyard = []  # oggetti multimediali in attesa di distruzione sicura


def _bury(*objs):
    """Distrugge player/sink dopo un attimo, quando FFmpeg ha smesso di usarli."""
    _graveyard.append(objs)

    def kill():
        if _graveyard:
            for o in _graveyard.pop(0):
                try:
                    o.deleteLater()
                except RuntimeError:
                    pass
    QTimer.singleShot(1500, kill)


class VideoInstance(RoutedVoice, KeyframeMixin, Instance):
    """Video. Con l'audio nel motore CueStage (predefinito) l'audio del file passa dal mixer (scheda e
    matrice della cue) e fa da orologio: l'immagine lo segue e viene riallineata se si scosta."""
    layer = None
    SYNC_TOL = 0.15      # scarto massimo video/audio prima di riallineare (s)

    def start(self, silent=False):
        c = self.cue
        ctrl = self.ctrl
        self.view = CueView(c)
        self._init_kf()
        self.frames = 0
        self.layer = ctrl.comp.add(self.key, int(c.layer), c.fit, cue=self.view)
        self.layer.opacity = 0.0
        info = ctrl.loader.info_now(c.file)
        self.file_dur = float(info.get("duration") or 0.0)
        self.voice = None
        self.synced = False
        self.sync_cool = 0.0
        self.wraps = 0
        self.audio_ended = False
        if c.video_audio != "system" and info.get("has_audio"):
            data = ctrl.loader.audio_now(c.file)
            if len(data):
                # in pausa finché il lettore non mostra il primo fotogramma
                self._start_voice(data, silent, paused=True)
        from .video_deck import VideoDeck
        self.alt = None                       # deck fermo all'inizio del loop (ripetizione senza attesa)
        pre = getattr(ctrl, "video_decks", None)
        deck = pre.take(c.id, c.file, c.start) if (pre is not None and c.preload) else None
        self.preloaded = deck is not None and deck.ready
        if deck is None:
            deck = VideoDeck(c.file, c.start)
        self._use_deck(deck)
        self.aout = None
        if self.voice is None:
            self.aout = QAudioOutput()
            self.aout.setVolume(0.0)
            self.player.setAudioOutput(self.aout)
        now = ctrl.clock
        lvl = db_to_lin(c.level_db)
        self.env_vol = Envelope(lvl)          # usato solo con il lettore di sistema
        self.env_op = Envelope(float(c.opacity))
        if silent:
            self.env_vol.set(0.0)
            self.env_op.set(0.0)
        elif c.fade_in > 0:
            self.env_vol.set(0.0)
            self.env_op.set(0.0)
            self.env_vol.ramp_to(now, lvl, c.fade_in)
            self.env_op.ramp_to(now, float(c.opacity), c.fade_in)
        self.curve = curve_arrays(c.curve)
        self.seeked = deck.loaded
        self.loops_left = (int(c.loop_count) - 1) if c.loop_count and c.loop_count > 0 else -1
        self.ended = False
        if deck.ready and deck.image is not None:      # precaricato: il primo fotogramma c'è già
            self.layer.image = deck.image
            self.frames = 1
        if self.player.duration() > 0:
            self.file_dur = self.player.duration() / 1000.0
        deck.go()

    # ---------------------------------------------------- deck (lettore + sink)
    def _use_deck(self, deck):
        self.deck = deck
        deck.owner = self
        self.player = deck.player
        self.sink = deck.sink
        self.bridge = deck          # compatibilità: «bridge» non è None finché c'è un lettore

    def _deck_status(self, deck, st):
        if self.done or deck is not self.deck:
            return
        if st in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self.seeked = True
        if st == QMediaPlayer.EndOfMedia:
            self.ended = True
        if self.player.duration() > 0:
            self.file_dur = self.player.duration() / 1000.0

    def _deck_frame(self, deck, img):
        if self.done or deck is not self.deck or self.layer is None:
            return
        self.layer.image = img
        self.frames += 1

    def _deck_error(self, deck):
        if self.done or deck is not self.deck:
            return
        self.ctrl.message.emit(tr("Errore video '{0}': {1}", self.cue.label(), deck.error))
        self.finish()

    def _want_alt(self):
        """Serve un secondo deck fermo all'inizio del loop? (loop di almeno 1 s, precaricamento attivo)"""
        if not self.cue.preload or not self.looping() or self.alt is not None or self.frames == 0:
            return False
        end, ls, le = self._bounds()
        return le - ls >= 1.0 and le < 1e8

    def _loop_swap(self):
        """Ripetizione del loop: parte il deck già fermo all'inizio del loop, l'altro si riarma lì.
        False se il deck di riserva non è pronto (allora si cerca nel file come prima)."""
        alt = self.alt
        if alt is None or not alt.ready:
            return False
        end, ls, le = self._bounds()
        old = self.deck
        self._use_deck(alt)
        if alt.image is not None and self.layer is not None:
            self.layer.image = alt.image
        if self.aout is not None:
            old.player.setAudioOutput(None)
            self.player.setAudioOutput(self.aout)
        self.ended = False
        self.seeked = True
        if not (self.paused or self.ctrl.paused_all):
            alt.go()
        old.player.pause()
        old.arm(ls)
        old.owner = self
        self.alt = old
        self.sync_cool = self.ctrl.clock + 0.8
        return True

    def _bounds(self):
        c = self.cue
        dur = self.file_dur or 1e9
        end = c.end if c.end and c.end > 0 else dur
        end = min(end, dur)
        ls = c.loop_start if c.loop_start and c.loop_start > 0 else c.start
        le = c.loop_end if c.loop_end and c.loop_end > 0 else end
        return end, max(c.start, ls), min(le, end)

    def looping(self):
        return self.loop_on and self.loops_left != 0

    def _audio_master(self):
        return self.voice is not None and not self.audio_ended

    # ---------------------------------------------------- sincronizzazione video → audio
    def _seek_video(self, t):
        self.player.setPosition(int(max(0.0, t) * 1000))
        self.ended = False
        if self.player.playbackState() != QMediaPlayer.PlayingState and not (self.paused or self.ctrl.paused_all):
            self.player.play()
        self.sync_cool = self.ctrl.clock + 0.8

    def _sync(self, now):
        v = self.voice
        vpos = self.player.position() / 1000.0
        if not self.synced:
            ready = (self.frames > 0 and self.seeked
                     and self.player.playbackState() == QMediaPlayer.PlayingState
                     and vpos >= self.cue.start - 0.05)
            if ready or self.elapsed > 2.5:     # dopo 2,5 s parte comunque
                v.pos = min(max(v.start, int(vpos * SR)), max(v.start, v.end - 1)) if ready else v.start
                v.paused = self.paused
                self.synced = True
                self.sync_cool = now + 0.5
            return
        if v.wraps != self.wraps:             # l'audio è ripartito dall'inizio del loop
            self.wraps = v.wraps
            if not self._loop_swap():
                self._seek_video(v.pos / SR)
            return
        if self.paused or self.ctrl.paused_all or now < self.sync_cool:
            return
        # tempo dell'audio che si sta sentendo ora (il mixer è avanti della latenza della scheda)
        a_t = v.pos / SR - self.ctrl.audio.latency(v.out_key)
        if abs(vpos - a_t) > self.SYNC_TOL and not (self.ended and vpos < a_t):
            self._seek_video(a_t + 0.03)

    def tick(self, now, dt):
        if self.done:
            return
        if not self.paused and not self.ctrl.paused_all:
            self.elapsed += dt
            self._tick_kf(dt)
        c = self.cue
        end, ls, le = self._bounds()
        if self._want_alt():
            from .video_deck import VideoDeck
            self.alt = VideoDeck(c.file, ls)
            self.alt.owner = self
        if self._audio_master():
            v = self.voice
            self._update_route()
            self._sync(now)
            if v.done:
                if self.user_stopped:
                    self.finish()
                    return
                # l'audio è finito: se il video ha ancora immagini (traccia audio più corta) continua da solo
                vpos = self.player.position() / 1000.0
                if end - vpos > 0.3 and not self.ended:
                    self.audio_ended = True
                else:
                    self.finish()
                    return
            pos = v.pos / SR
            looping = v.loop and v.loops_left != 0
        else:
            pos = self.player.position() / 1000.0
            if self.looping() and (pos >= le - 0.03 or self.ended):
                if self.loops_left > 0:
                    self.loops_left -= 1
                self.ended = False
                if not self._loop_swap():
                    self.player.setPosition(int(ls * 1000))
                    if self.player.playbackState() != QMediaPlayer.PlayingState and not self.paused:
                        self.player.play()
                pos = ls
            elif not self.looping() and self.seeked and (self.ended or pos >= end - 0.01):
                self.finish()
                return
            looping = self.looping()
        fo = 1.0
        if c.fade_out > 0 and not looping:
            fo = max(0.0, min(1.0, (end - pos) / c.fade_out))
        if self.aout is not None:
            vol = self.env_vol.get(now) * float(curve_gain(self.curve, pos)) * fo * self.ctrl.audio.master \
                * self.kf_level_ratio * self.trim_gain * self.duck_gain() * self.ctrl.audio.duck.master_gain()
            # il lettore di sistema non può andare alla cuffia del preascolto: in preascolto tace
            self.aout.setVolume(0.0 if (self.blind or self.pfl) else max(0.0, min(1.0, vol)))
        self.layer.opacity = min(1.0, self.env_op.get(now) * fo * self.kf_op_ratio * self.trim_op)

    def stop(self, fade=0.0):
        self.user_stopped = True
        if fade > 0 and not self.paused:
            now = self.ctrl.clock
            if self.voice is not None:
                self.ctrl.audio.stop_voice(self.key, fade)
            self.env_vol.ramp_to(now, 0.0, fade)
            self.env_op.ramp_to(now, 0.0, fade, on_done=self.finish)
        else:
            self.finish()

    def pause(self, on):
        self.paused = on
        if self.voice is not None and self.synced:
            self.ctrl.audio.pause(self.key, on)
        self._apply_pause()

    def _apply_pause(self):
        if self.paused or self.ctrl.paused_all:
            self.player.pause()
        else:
            self.player.play()
            self.sync_cool = self.ctrl.clock + 0.4

    def fade_to(self, gain, opacity, dur, shape, stop_after):
        now = self.ctrl.clock
        cb = self.finish if stop_after else None
        if gain is not None:
            if self.voice is not None:
                self.ctrl.audio.fade(self.key, gain, dur, shape, False)
            self.env_vol.ramp_to(now, gain, dur, shape, None if opacity is not None else cb)
        if opacity is not None:
            self.env_op.ramp_to(now, opacity, dur, shape, cb)
        if gain is None and opacity is None and stop_after:
            self.stop(dur)

    def total(self):
        if self.voice is not None:
            return self._total
        if self.loop_on and self.loops_left < 0:
            return None
        end, ls, le = self._bounds()
        if end >= 1e8:
            return None
        n = max(0, int(self.cue.loop_count) - 1) if self.loop_on else 0
        return (end - self.cue.start) + n * (le - ls)

    def cue_time(self):
        if self.voice is not None and self.synced:
            return self.voice.played / SR
        return self.elapsed

    def position(self):
        if self._audio_master() and self.synced:
            return self.voice.pos / SR
        return self.player.position() / 1000.0

    def cleanup(self):
        if self.voice is not None:
            self.ctrl.audio.remove(self.key)
        if getattr(self, "bridge", None) is not None:
            for d in (self.deck, self.alt):
                if d is not None:
                    d.dispose()
            if self.aout is not None:
                _bury(self.aout)
            self.bridge = None
            self.alt = None
        self.ctrl.comp.remove(self.key, self.layer)


# ------------------------------------------------------------------ immagine

class ImageInstance(KeyframeMixin, Instance):
    def start(self, silent=False):
        c = self.cue
        img = self.ctrl.image(c.file)
        if img is None or img.isNull():
            self.ctrl.message.emit(tr("Impossibile aprire l'immagine '{0}'", c.file))
            self.finish()
            return
        self.view = CueView(c)
        self._init_kf()
        self.layer = self.ctrl.comp.add(self.key, int(c.layer), c.fit, cue=self.view)
        self.layer.image = img
        self.env_op = Envelope(float(c.opacity))
        if silent:
            self.env_op.set(0.0)
        elif c.fade_in > 0:
            self.env_op.set(0.0)
            self.env_op.ramp_to(self.ctrl.clock, float(c.opacity), c.fade_in)
        self.layer.opacity = self.env_op.get(self.ctrl.clock) * self.trim_op
        self.ctrl.comp.changed()

    def tick(self, now, dt):
        if self.done:
            return
        if not self.paused:
            self.elapsed += dt
            self._tick_kf(dt)
        d = float(self.cue.duration or 0)
        fo = 1.0
        if d > 0:
            if self.cue.fade_out > 0:
                fo = max(0.0, min(1.0, (d - self.elapsed) / self.cue.fade_out))
            if self.elapsed >= d:
                self.finish()
                return
        self.layer.opacity = min(1.0, self.env_op.get(now) * fo * self.kf_op_ratio * self.trim_op)

    def stop(self, fade=0.0):
        self.user_stopped = True
        if fade > 0:
            self.env_op.ramp_to(self.ctrl.clock, 0.0, fade, on_done=self.finish)
        else:
            self.finish()

    def fade_to(self, gain, opacity, dur, shape, stop_after):
        cb = self.finish if stop_after else None
        if opacity is not None:
            self.env_op.ramp_to(self.ctrl.clock, opacity, dur, shape, cb)
        elif stop_after:
            self.stop(dur)

    def total(self):
        return float(self.cue.duration) if self.cue.duration else None

    def cleanup(self):
        self.ctrl.comp.remove(self.key, getattr(self, "layer", None))


# ------------------------------------------------------------------ altre

class WaitInstance(Instance):
    def tick(self, now, dt):
        super().tick(now, dt)
        if self.elapsed >= self.total():
            self.finish()

    def total(self):
        return float(self.cue.duration or 0)


class FadeInstance(Instance):
    def start(self, silent=False):
        c = self.cue
        ctrl = self.ctrl
        target = ctrl.show.resolve_target(c.target)
        if target is None:
            ctrl.message.emit(tr("Fade '{0}': cue target '{1}' non trovata", c.label(), c.target))
            self.finish()
            return
        inst = ctrl.instances.get(target.id)
        if inst is None and c.start_if_idle and target.is_media():
            inst = ctrl.start_cue(target, cascade=False, silent=True)
        if inst is None:
            ctrl.message.emit(tr("Fade '{0}': la cue '{1}' non è in esecuzione", c.label(), target.label()))
            self.finish()
            return
        gain = db_to_lin(c.target_db) if c.fade_level else None
        op = float(c.target_opacity) if c.fade_opacity and target.is_visual() else None
        inst.fade_to(gain, op, float(c.fade_duration), c.fade_shape, bool(c.stop_when_done))
        if float(c.fade_duration) <= 0:
            self.finish()

    def tick(self, now, dt):
        super().tick(now, dt)
        if self.elapsed >= self.total():
            self.finish()

    def total(self):
        return float(self.cue.fade_duration or 0)


class PresetInstance(Instance):
    """Porta una cue in esecuzione verso un preset (audio e/o crop/posizione) nel tempo di fade."""

    def start(self, silent=False):
        c = self.cue
        ctrl = self.ctrl
        target = ctrl.show.resolve_target(c.target)
        if target is None:
            ctrl.message.emit(tr("Cambia preset '{0}': cue target '{1}' non trovata", c.label(), c.target))
            self.finish()
            return
        inst = ctrl.instances.get(target.id)
        if inst is None:
            ctrl.message.emit(tr("Cambia preset '{0}': la cue '{1}' non è in esecuzione", c.label(), target.label()))
            self.finish()
            return
        dur = max(0.0, float(c.fade_duration or 0))
        # i preset sono quelli della cue target
        pa = target.preset_dict("audio").get(c.preset_audio) if c.preset_audio else None
        pv = target.preset_dict("visual").get(c.preset_visual) if c.preset_visual else None
        if c.preset_audio and pa is None:
            ctrl.message.emit(tr("La cue '{0}' non ha il preset audio «{1}»", target.label(), c.preset_audio))
        if c.preset_visual and pv is None:
            ctrl.message.emit(tr("La cue '{0}' non ha il preset crop/posizione «{1}»", target.label(), c.preset_visual))
        start_preset(inst, target, c.preset_visual if pv is not None else "",
                     c.preset_audio if pa is not None else "", dur, c.fade_shape)
        if dur <= 0:
            self.finish()

    def tick(self, now, dt):
        super().tick(now, dt)
        if self.elapsed >= self.total():
            self.finish()

    def total(self):
        return float(self.cue.fade_duration or 0)


class ControlInstance(Instance):
    def start(self, silent=False):
        c = self.cue
        ctrl = self.ctrl
        act = c.action
        if act == "stop_all":
            ctrl.stop_all(float(c.stop_fade), exclude=self.key)
        else:
            target = ctrl.show.resolve_target(c.target)
            if target is None:
                ctrl.message.emit(tr("Comando '{0}': cue target '{1}' non trovata", c.label(), c.target))
            elif act == "start":
                ctrl.trigger(target, cascade=True, depth=ctrl._depth + 1)
            elif act == "stop":
                ctrl.stop_cue(target.id, float(c.stop_fade))
            elif act == "pause":
                ctrl.pause_cue(target.id, True)
            elif act == "resume":
                ctrl.pause_cue(target.id, False)
            elif act == "playhead":
                ctrl.playhead_to(target)
        self.finish()


class ProjectorInstance(Instance):
    """Invia un comando a un videoproiettore (in un thread: la cue termina subito)."""

    def start(self, silent=False):
        c = self.cue
        pm = getattr(self.ctrl, "projectors", None)
        if pm is None or not c.proj_target:
            self.ctrl.message.emit(tr("Proiettore '{0}': nessun proiettore selezionato", c.label()))
        else:
            pm.run(c.proj_target, c.proj_action, c.proj_value, c.proj_dir, c.proj_steps, c.proj_speed)
        self.finish()


TAP_MODES = ("sequence", "loop", "rloop")       # gruppi in cui il Tap Tempo ha senso
TAP_DIVS = [1 / 16, 1 / 8, 1 / 4, 1 / 2, 1, 2, 4, 8, 16]
TAP_MIN_INTERVAL = 0.05                          # sotto questo intervallo (s) il motore non sta dietro


def tap_active(cue):
    return bool(cue.group_tap) and cue.group_mode in TAP_MODES


def tap_interval(cue):
    """Secondi tra una cue e la successiva: (60 / BPM) × divisore."""
    bpm = max(20.0, min(300.0, float(cue.group_bpm or 120.0)))
    return max(TAP_MIN_INTERVAL, 60.0 / bpm * float(cue.group_tap_div or 1.0))


def fmt_div(d):
    d = float(d or 1)
    return f"1/{int(round(1 / d))}" if d < 1 else f"{d:g}"


class GroupInstance(Instance):
    """Esecuzione di un gruppo di cue.
    sequence: parte la prima cue (le altre con i GO) · random: una cue a caso · all: tutte insieme ·
    loop / rloop: le cue si susseguono da sole con attesa e crossfade (rloop in ordine casuale, oppure
    avanzando col GO) · playlist / rplaylist: le cue si susseguono da sole, una volta ciascuna (rplaylist in
    ordine casuale), ognuna appena finisce la precedente, senza pre-wait né «con/dopo la precedente».
    Resta attiva finché suonano le sue cue; STOP o l'uscita la fermano."""

    def start(self, silent=False):
        import random
        from .groups import members
        c = self.cue
        ctrl = self.ctrl
        self.lst, i = ctrl.show.list_of(c.id)
        self.pool = members(self.lst.cues, i, playable=True) if self.lst is not None else []
        self.spawned = set()
        self.current = None
        self.xf = None
        self.next_at = None
        self.k = 0
        self.mode = c.group_mode
        # sequenza casuale riproducibile: le istanze in ridondanza ricevono lo stesso seme dal master
        self.rng = random.Random(f"{ctrl.seed_base}|{c.id}|{ctrl.seed_count}")
        ctrl.seed_count += 1
        if not self.pool:
            ctrl.message.emit(tr("Gruppo '{0}': nessuna cue nel gruppo", c.label()))
            self.cascade = False
            self.finish()
            return
        depth = ctrl._depth + 1
        self.tap = tap_active(c)
        self.beat_next = ctrl.clock + tap_interval(c) if self.tap else None
        if self.mode == "sequence":
            self.cascade = False          # le «dopo la precedente» seguono l'ultima cue del gruppo
            ctrl.trigger(self.pool[0], cascade=True, depth=depth)
            if self.tap:                  # Tap Tempo: i GO successivi li dà il battito (vedi Controller._tick)
                ctrl.tempo_runs[c.id] = {"lst": self.lst, "next": self.beat_next}
            self.finish()
            return
        if self.mode == "all":
            for m in self.pool:
                ctrl.trigger(m, cascade=False, depth=depth)    # il pre-wait di ognuna è rispettato
                self.spawned.add(m.id)
            return
        if self.mode == "random":
            self._play(self._pick())
            return
        # loop / random loop / playlist / random playlist
        self.order = list(self.pool)
        if self.mode in ("rloop", "rplaylist"):
            self.rng.shuffle(self.order)
        if self.mode == "rloop":               # la prima cue conta come «ultima suonata»: niente ripetizione subito
            ctrl._group_last[c.id] = self.order[0].id
        self._play(self.order[0])

    @property
    def captures_go(self):
        return self.mode == "rloop" and self.cue.group_advance == "go" and not self.done and \
            not getattr(self, "tap", False)

    @property
    def timed(self):
        return self.mode in ("loop", "playlist", "rplaylist") or getattr(self, "tap", False) or \
            (self.mode == "rloop" and self.cue.group_advance != "go")

    @property
    def playlist(self):
        return self.mode in ("playlist", "rplaylist")

    def _pick(self):
        last = self.ctrl._group_last.get(self.cue.id)
        cands = [m for m in self.pool if m.id != last] or self.pool
        m = self.rng.choice(cands)
        self.ctrl._group_last[self.cue.id] = m.id
        return m

    def _play(self, cue, xfade=0.0):
        ctrl = self.ctrl
        old = ctrl.instances.get(self.current) if self.current else None
        if old is not None and old.done:
            old = None
        if self.xf is not None:                       # crossfade precedente non finito: lo chiude
            ctrl._xf_finish(self.xf)
            self.xf = None
        if old is not None and old.cue.id == cue.id:  # stessa cue (gruppo di una sola cue): riparte
            old.stop(0)
            old = None
        ctrl._play_once = cue.id if self.playlist else None
        if xfade > 0 and old is not None:
            ctrl._xf_incoming = cue.id
            try:
                ctrl.start_cue(cue, cascade=False)
            finally:
                ctrl._xf_incoming = None
                ctrl._play_once = None
            new = ctrl.instances.get(cue.id)
            if new is not None:
                self.xf = {"A": [old], "B": [new], "t0": ctrl.clock, "dur": float(xfade)}
                ctrl._xf_apply(self.xf, 0.0)
        else:
            if old is not None and self.timed:
                old.stop(0.05)
            try:
                ctrl.start_cue(cue, cascade=False)
            finally:
                ctrl._play_once = None
        self.current = cue.id
        self.spawned.add(cue.id)
        self._schedule()
        ctrl.changed.emit()

    def _schedule(self):
        c = self.cue
        if getattr(self, "tap", False) and self.mode in ("loop", "rloop"):
            self.next_at = None           # con il Tap Tempo decide il battito (tick)
            return
        if not self.timed:
            self.next_at = None
            return
        hold = float(c.group_hold or 0)
        xf = float(c.group_xfade or 0)
        if self.playlist:
            # la successiva parte alla fine della corrente (o prima, se c'è un crossfade); senza durata
            # nota (es. una cue OSC) si aspetta che la cue finisca da sola
            if xf > 0 and (self.k + 1 < len(self.order) or self.cue.group_loop):
                inst = self.ctrl.instances.get(self.current)
                tot = inst.total() if inst is not None else None
                self.xf_eff = min(xf, tot * 0.5) if tot else 0.0     # mai più di metà della cue
                self.next_at = self.ctrl.clock + (tot - self.xf_eff) if tot else None
            else:
                self.next_at = None
            return
        if hold > 0:
            self.next_at = self.ctrl.clock + hold
            return
        inst = self.ctrl.instances.get(self.current)
        tot = inst.total() if inst is not None else None
        self.next_at = self.ctrl.clock + (max(0.5, tot - xf) if tot else 10.0)

    def advance(self, xfade=None):
        """Cue successiva del loop (o del random loop), con crossfade."""
        if self.done:
            return
        if self.playlist:
            if self.k + 1 >= len(self.order):
                if not self.cue.group_loop:
                    self.next_at = None    # era l'ultima: il gruppo finisce quando finisce lei
                    return
                last = self.order[-1]       # ricomincia; la random playlist rimescola (mai la stessa di fila)
                if self.mode == "rplaylist" and len(self.order) > 1:
                    self.rng.shuffle(self.order)
                    if self.order[0].id == last.id:
                        self.order.append(self.order.pop(0))
                self.k = -1
            self.k += 1
            xf = getattr(self, "xf_eff", 0.0) if self.next_at is not None else 0.0
            self._play(self.order[self.k], xf)
            return
        if self.mode == "loop":
            self.k = (self.k + 1) % len(self.order)
            nxt = self.order[self.k]
        else:
            nxt = self._pick()
        self._play(nxt, float(self.cue.group_xfade or 0) if xfade is None else xfade)

    def tick(self, now, dt):
        if self.done or self.paused:
            return
        self.elapsed += dt
        ctrl = self.ctrl
        if self.xf is not None:
            t = (ctrl.clock - self.xf["t0"]) / max(1e-6, self.xf["dur"])
            ctrl._xf_apply(self.xf, t)
            if t >= 1.0:
                ctrl._xf_finish(self.xf)
                self.xf = None
        if self.tap and self.mode in ("loop", "rloop"):
            # Tap Tempo: si cambia cue solo sul battito (una cue più corta lascia silenzio fino al battito,
            # una più lunga viene tagliata con il crossfade, mai più lungo di metà intervallo)
            if self.beat_next is not None and ctrl.clock >= self.beat_next:
                iv = tap_interval(self.cue)
                self.beat_next += iv
                if self.beat_next < ctrl.clock:          # rimasti indietro (pausa, sistema lento): si riallinea
                    self.beat_next = ctrl.clock + iv
                self.advance(min(float(self.cue.group_xfade or 0), iv * 0.5))
            return
        if self.playlist:
            cur = ctrl.instances.get(self.current)
            ended = cur is None or cur.done
            if self.next_at is not None and ctrl.clock >= self.next_at:
                self.advance()
            elif ended and self.xf is None:
                if self.k + 1 < len(self.order) or self.cue.group_loop:
                    self.advance()
                else:
                    self.finish()
            return
        if self.mode in ("loop", "rloop"):
            cur = ctrl.instances.get(self.current)
            if self.timed and self.next_at is not None and ctrl.clock >= self.next_at:
                self.advance()
            elif cur is None and self.xf is None:
                if self.timed:
                    self.advance()          # la cue è finita prima del tempo: si passa alla successiva
            return
        # random / all: il gruppo resta attivo finché suonano le sue cue
        pending = {p[1].id for p in ctrl.pending}
        if not any(k in ctrl.instances or k in pending for k in self.spawned):
            self.finish()

    def exit(self, fade=None):
        """Esce dal gruppo: niente più avanzamenti; la cue corrente sfuma (o finisce da sola)."""
        if self.done:
            return
        ctrl = self.ctrl
        if self.xf is not None:
            ctrl._xf_finish(self.xf)
            self.xf = None
        if fade is None:
            fade = max(0.1, float(self.cue.group_xfade or 0)) if self.cue.group_exit == "fade" else None
        if fade is not None:
            for k in list(self.spawned):
                inst = ctrl.instances.get(k)
                if inst is not None:
                    inst.stop(fade)
        # il playhead lascia il gruppo
        if self.lst is not None:
            from .groups import block_end
            i = self.lst.index_of(self.cue.id)
            ph = ctrl.playhead_of(self.lst)
            end = block_end(self.lst.cues, i)
            if i >= 0 and i <= ph < end:
                ctrl.set_playhead(end, self.lst)
        self.cascade = False
        self.user_stopped = True
        self.finish()

    def stop(self, fade=0.0):
        self.exit(fade)

    def pause(self, on):
        self.paused = on

    def total(self):
        return None


class TextInstance(Instance):
    """Testo in uscita (sottotitoli): con «sequence» ogni GO passa alla frase successiva; dopo l'ultima il
    GO successivo esce (il testo sfuma e il GO va alla cue seguente). ESC esce subito."""

    def start(self, silent=False):
        from .text_render import split_phrases
        c = self.cue
        self.lst, _i = self.ctrl.show.list_of(c.id)
        self.phrases = split_phrases(c.text_content) if c.text_mode == "sequence" else [c.text_content or ""]
        if not self.phrases:
            self.phrases = [""]
        self.idx = 0
        self.layers = []         # [(layer, chiave, inviluppo)]
        self.n = 0
        self._show(0, float(c.text_fade or 0))

    def _image(self, text):
        from .text_render import render_text
        W, H = self.ctrl.comp.out_size
        return render_text(self.cue, text, int(W), int(H))

    def _show(self, idx, fade):
        ctrl = self.ctrl
        now = ctrl.clock
        for lay, key, env in self.layers:        # la frase precedente sfuma
            env.ramp_to(now, 0.0, fade)
        self.n += 1
        key = f"{self.key}#t{self.n}"
        lay = ctrl.comp.add(key, int(self.cue.layer), "stretch", cue=None)
        lay.image = self._image(self.phrases[idx])
        env = Envelope(0.0 if fade > 0 else 1.0)
        if fade > 0:
            env.ramp_to(now, 1.0, fade)
        lay.opacity = env.get(now) * self.trim_op
        self.layers.append((lay, key, env))
        self.idx = idx
        ctrl.comp.changed()
        ctrl.changed.emit()

    @property
    def captures_go(self):
        return self.cue.text_mode == "sequence" and not self.done and self.idx < len(self.phrases) - 1

    def next(self):
        if self.idx < len(self.phrases) - 1:
            self._show(self.idx + 1, float(self.cue.text_fade or 0))

    def prev(self):
        if self.idx > 0:
            self._show(self.idx - 1, float(self.cue.text_fade or 0))

    def goto(self, idx):
        if 0 <= idx < len(self.phrases):
            self._show(idx, float(self.cue.text_fade or 0))

    def refresh(self):
        """Testo o stile modificati durante la riproduzione: ridisegna la frase corrente."""
        from .text_render import split_phrases
        c = self.cue
        self.phrases = (split_phrases(c.text_content) if c.text_mode == "sequence" else [c.text_content or ""]) or [""]
        self.idx = min(self.idx, len(self.phrases) - 1)
        if self.layers:
            lay = self.layers[-1][0]
            lay.image = self._image(self.phrases[self.idx])
            lay.z = int(c.layer)
            self.ctrl.comp.changed()

    def tick(self, now, dt):
        if self.done:
            return
        if not self.paused:
            self.elapsed += dt
        keep = []
        for lay, key, env in self.layers:
            v = env.get(now)
            lay.opacity = v * self.trim_op
            if v <= 0.001 and (lay, key, env) is not self.layers[-1]:
                self.ctrl.comp.remove(key, lay)
            else:
                keep.append((lay, key, env))
        self.layers = keep
        if getattr(self, "_closing", False) and all(env.get(now) <= 0.001 for _l, _k, env in self.layers):
            self.finish()
            return
        d = float(self.cue.duration or 0)
        if self.cue.text_mode == "free" and d > 0 and self.elapsed >= d:
            self.stop(float(self.cue.text_fade or 0))

    def stop(self, fade=0.0):
        self.user_stopped = True
        if fade > 0:
            now = self.ctrl.clock
            for lay, key, env in self.layers:
                env.ramp_to(now, 0.0, fade)
            self._closing = True
        else:
            self.finish()

    def total(self):
        return None

    def progress(self):
        return (self.idx + 1) / len(self.phrases) if self.cue.text_mode == "sequence" else None

    def status_text(self):
        if self.cue.text_mode == "sequence":
            return tr("frase {0}/{1}", self.idx + 1, len(self.phrases))
        return "testo"

    def cleanup(self):
        for lay, key, env in self.layers:
            self.ctrl.comp.remove(key, lay)
        self.layers = []


class MidiOutInstance(Instance):
    """Invia i messaggi MIDI della cue (con le eventuali attese tra un messaggio e l'altro)."""

    def start(self, silent=False):
        from .remote import midi_out
        c = self.cue
        port = c.mo_port or self.ctrl.show.settings.get("midi_out_port", "")
        try:
            n = midi_out.send_script(port, c.mo_msgs)
        except Exception as e:
            self.ctrl.message.emit(tr("MIDI out '{0}': {1}", c.label(), e))
        else:
            self.ctrl.message.emit(tr("MIDI out ← {0}: {1} messaggi", c.label(), n))
        self.finish()


class GroupExitInstance(Instance):
    """«Esci dal gruppo»: chiude il gruppo indicato (utile per i random loop avanzati col GO)."""

    def start(self, silent=False):
        c = self.cue
        tg = self.ctrl.show.resolve_target(c.target) if c.target else None
        if tg is None and c.group:
            tg = self.ctrl.show.by_id(c.group)
        inst = self.ctrl.instances.get(tg.id) if tg is not None else None
        if tg is None:
            self.ctrl.message.emit(tr("'{0}': gruppo non trovato", c.label()))
        elif isinstance(inst, GroupInstance):
            inst.exit()
            self.ctrl.message.emit(tr("Uscita dal gruppo «{0}»", tg.label()))
        self.cascade = False
        self.finish()


def stop_panel_targets(show, cue):
    """Liste fermate da una cue «Stop panel»: la MAIN se scelta, poi tutti i PANEL (anche quelli creati
    dopo) oppure quelli scelti."""
    ids = list(cue.sp_panels or [])
    out = [show.main] if show.main.id in ids else []
    if "*" in ids:
        return out + list(show.panels)
    return out + [lst for lst in show.panels if lst.id in ids]


class StopPanelInstance(Instance):
    """Ferma le cue dei PANEL scelti (o di tutti), con un fade facoltativo. Le altre liste continuano."""

    def start(self, silent=False):
        c = self.cue
        ctrl = self.ctrl
        lists = stop_panel_targets(ctrl.show, c)
        fade = float(c.stop_fade or 0)
        ids = {x.id for lst in lists for x in lst.cues} - {c.id}
        ctrl.pending = [p for p in ctrl.pending if p[1].id not in ids]
        for inst in list(ctrl.instances.values()):
            if inst.cue.id in ids and not inst.done:
                inst.stop(fade)
        names = ", ".join(f"«{lst.name}»" for lst in lists) or tr("nessuna lista")
        ctrl.message.emit(tr("Stop panel: {0}", names) + (tr(" (fade {0:g} s)", fade) if fade > 0 else ""))
        ctrl.changed.emit()
        self.finish()


class MixerInstance(Instance):
    """Comando a un mixer: mute/unmute di uno o più canali, oppure fader (con fade facoltativo).
    Con il fade la cue resta attiva per la sua durata (STOP la interrompe)."""

    def start(self, silent=False):
        from .mixers import parse_numbers
        c = self.cue
        mm = getattr(self.ctrl, "mixers", None)
        self.fade = 0.0
        self.handle = None
        conf = mm.find(c.mix_target) if mm is not None else None
        if conf is None:
            self.ctrl.message.emit(tr("Mixer '{0}': nessun mixer selezionato", c.label()))
            self.finish()
            return
        if c.mix_action == "control":
            if not c.mix_ctl:
                self.ctrl.message.emit(tr("Mixer '{0}': nessun controllo scelto", c.label()))
            else:
                mm.set_control(c.mix_target, c.mix_ctl, int(c.mix_ctl_value or 0))
            self.finish()
            return
        if c.mix_action in ("mute", "unmute"):
            from .mixers import mute_states
            states = mute_states(c)
            if not states:
                self.ctrl.message.emit(tr("Mixer '{0}': nessun mute da cambiare", c.label()))
            for kind, st in states.items():
                for flag in (True, False):
                    nums = sorted(n for n, v in st.items() if v is flag)
                    if nums:
                        mm.mute(c.mix_target, kind, nums, flag)
            self.finish()
            return
        nums = parse_numbers(c.mix_channels) or []
        if not nums:
            self.ctrl.message.emit(tr("Mixer '{0}': nessun canale indicato", c.label()))
            self.finish()
            return
        if c.mix_action == "duck":
            # i fader seguono la curva del ducking finché la cue è attiva (STOP li riporta al livello)
            self.hooked = (c.mix_target, c.mix_kind, list(nums))
            self._sent = None
            self._sent_at = -1.0
            duck = self.ctrl.audio.duck
            duck.hook(self.key, True)
            if duck.error:
                self.ctrl.message.emit(tr("Mixer '{0}': ducking — {1}", c.label(), duck.error))
            self._duck_send(force=True)
            return
        self.fade = max(0.0, float(c.mix_fade or 0))
        for n in nums:
            h = mm.fader(c.mix_target, c.mix_kind, n, float(c.mix_level), self.fade)
            self.handle = self.handle or h
        if self.fade <= 0:
            self.finish()

    hooked = None

    @property
    def duck_db(self):
        return float(self.cue.mix_duck_db or 0) if self.hooked else 0.0

    def _duck_send(self, force=False, level_only=False):
        """Porta i fader a «livello − riduzione × inviluppo» (al massimo 25 volte al secondo)."""
        mm = getattr(self.ctrl, "mixers", None)
        if mm is None or not self.hooked:
            return
        c = self.cue
        mid, kind, nums = self.hooked
        env = 0.0 if level_only else (self.ctrl.audio.duck.env if self.ctrl.audio.duck.listening else 0.0)
        v = round(float(c.mix_level) - float(c.mix_duck_db or 0) * env, 1)
        if not force:
            if self._sent is not None and abs(v - self._sent) < 0.3 and not (env == 0.0 and v != self._sent):
                return
            if self.elapsed - self._sent_at < 0.04:
                return
        self._sent, self._sent_at = v, self.elapsed
        for n in nums:
            mm.fader(mid, kind, n, v, 0.0, report=force)

    def tick(self, now, dt):
        super().tick(now, dt)
        if self.hooked:
            if not self.paused:
                self._duck_send()
            return
        if self.elapsed >= self.fade + 0.1:
            self.finish()

    def total(self):
        if self.hooked:
            return None
        return self.fade or None

    def cleanup(self):
        if self.hooked:
            self._duck_send(force=True, level_only=True)      # sganciato: fader al livello normale
            self.ctrl.audio.duck.hook(self.key, False)
            self.hooked = None

    def stop(self, fade=0.0):
        self.user_stopped = True
        if self.hooked:
            self.finish()
            return
        mm = getattr(self.ctrl, "mixers", None)
        if mm is not None:
            from .mixers import parse_numbers
            for n in parse_numbers(self.cue.mix_channels) or []:
                mm._stop_fade(self.cue.mix_target, self.cue.mix_kind, n)
        self.finish()


def osc_target(c, settings):
    """(host, porta, indirizzo, argomenti) di una cue OSC: manuale o da dispositivo con profilo."""
    if c.osc_device:
        dev = next((d for d in settings.get("osc_devices") or [] if d.get("id") == c.osc_device), None)
        if dev is None:
            raise RuntimeError(tr("dispositivo OSC non configurato (Impostazioni › OSC)"))
        from .osc_profiles import render
        address, args = render(dev.get("profile"), c.osc_cmd, c.osc_params or {})
        return dev.get("host"), int(dev.get("port") or 0), address, args
    return c.osc_host, int(c.osc_port), c.osc_address, c.osc_args


class OscInstance(Instance):
    def start(self, silent=False):
        c = self.cue
        try:
            host, port, address, args = osc_target(c, self.ctrl.show.settings)
            osc_send(host, port, address, args)
        except Exception as e:
            self.ctrl.message.emit(tr("OSC '{0}': invio fallito ({1})", c.label(), e))
        self.finish()


class MemoInstance(Instance):
    def start(self, silent=False):
        self.finish()


INSTANCE_CLASSES = {
    "audio": AudioInstance, "video": VideoInstance, "image": ImageInstance,
    "wait": WaitInstance, "fade": FadeInstance, "preset": PresetInstance, "control": ControlInstance,
    "osc": OscInstance, "projector": ProjectorInstance, "mixer": MixerInstance, "memo": MemoInstance,
    "group": GroupInstance, "groupexit": GroupExitInstance, "stoppanel": StopPanelInstance, "text": TextInstance, "midiout": MidiOutInstance,
}


# ------------------------------------------------------------------ controller

class Controller(QObject):
    changed = Signal()
    message = Signal(str)
    playheadChanged = Signal(int)
    cueStarted = Signal(str)

    def __init__(self, show, audio, loader, comp):
        super().__init__()
        self.show = show
        self.audio = audio
        self.loader = loader
        self.comp = comp
        self.instances = {}
        self.pending = []      # [scatto_al_clock, cue, cascade]
        self.playheads = {}    # id lista -> indice della cue in standby
        self.paused_all = False
        self._xf_incoming = None   # id delle cue che entrano con un crossfade (partono a zero)
        self.taps = {}             # Tap Tempo: id gruppo -> istanti delle ultime battute
        self.blind_lists = set()   # GRID in BLIND (prova senza audio né video in uscita)
        self.tempo_runs = {}       # gruppi Sequence col Tap Tempo in corso: id -> {"lst", "next"}
        import uuid as _uuid
        self.seed_base = _uuid.uuid4().hex[:8]   # semi dei gruppi casuali (uguali sulle istanze in ridondanza)
        self.seed_count = 0
        self.xfade = None          # crossfade automatico in corso
        self.xf_manual = None      # crossfade manuale (crossfader MIDI) in corso
        self.xf_rest = None        # lato «a riposo» del crossfader (0 o 1)
        self._group_last = {}      # ultimo membro scelto a caso, per gruppo (niente ripetizioni)
        self.clock = 0.0
        self._last = time.monotonic()
        self._depth = 0
        self._images = {}
        self.visual_started = None  # callback: mostra l'uscita
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(10)

    # ---- testi (anche via rete: un metodo del controller si replica sulle istanze in ridondanza)
    def text_step(self, d, lst=None):
        t = self.text_instance(lst or self.go_list()) or self.text_instance()
        if t is None:
            return False
        (t.next if d > 0 else t.prev)()
        self.changed.emit()
        return True

    def text_goto(self, n):
        t = self.text_instance()
        if t is None:
            return False
        t.goto(int(n))
        self.changed.emit()
        return True

    def set_show(self, show):
        self.stop_all(0)
        self.show = show
        self.playheads = {}
        self._images.clear()
        self.playheadChanged.emit(0)

    # ---- liste e playhead
    @property
    def playhead(self):
        """Playhead della lista visualizzata."""
        return self.playhead_of(self.show.current)

    @playhead.setter
    def playhead(self, idx):
        self.playheads[self.show.current.id] = idx

    def playhead_of(self, lst):
        return self.playheads.get(lst.id, 0)

    def go_list(self):
        """Lista su cui agisce GO: sempre la MAIN, oppure quella selezionata."""
        if self.show.settings.get("go_target", "main") == "panel":
            return self.show.current
        return self.show.main

    def image(self, path):
        img = self._images.get(path)
        if img is None:
            img = QImage(path)
            if not img.isNull():
                img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
            self._images[path] = img
        return img

    # ---------------------------------------------------- comandi principali
    # ---------------------------------------------------- gruppi
    def group_instances(self, lst=None):
        return [i for i in self.instances.values() if isinstance(i, GroupInstance) and not i.done
                and (lst is None or i.lst is lst)]

    def captured_group(self, lst):
        """Gruppo (random loop avanzato col GO) o testo in sequenza che in questo momento usa il GO."""
        for g in self.group_instances(lst):
            if g.captures_go:
                return g
        t = self.text_instance(lst)
        if t is not None and t.captures_go:
            return t
        return None

    def text_instance(self, lst=None):
        """Cue di testo in sequenza attiva (la più recente) nella lista."""
        ts = [i for i in self.instances.values() if isinstance(i, TextInstance) and not i.done
              and i.cue.text_mode == "sequence" and (lst is None or i.lst is lst)]
        return max(ts, key=lambda i: i.started_at) if ts else None

    def exit_captured_groups(self):
        """ESC: esce dai gruppi e dai testi in sequenza che usano il GO. True se ce n'erano."""
        gs = [g for g in self.group_instances() if g.captures_go]
        for g in gs:
            g.exit()
        ts = [i for i in self.instances.values() if isinstance(i, TextInstance) and i.captures_go]
        for t in ts:
            t.stop(float(t.cue.text_fade or 0))
        gs += ts
        if gs:
            self.changed.emit()
        return bool(gs)

    def go(self, lst=None):
        from .groups import block_end
        lst = lst or self.go_list()
        cues = lst.cues
        cap = self.captured_group(lst)
        if cap is not None:                       # random loop / testo: il GO passa all'elemento successivo
            if isinstance(cap, TextInstance):
                cap.next()
            else:
                cap.advance()
            return
        for t in [i for i in self.instances.values() if isinstance(i, TextInstance) and not i.done
                  and i.cue.text_mode == "sequence" and i.lst is lst]:
            t.stop(float(t.cue.text_fade or 0))   # dopo l'ultima frase il GO successivo esce dal testo
        i = self.playhead_of(lst)
        if not (0 <= i < len(cues)):
            return
        for g in self.group_instances(lst):       # il GO successivo esce dai loop a tempo
            if g.timed and g.cue is not cues[i]:
                g.exit()
        cue = cues[i]
        self.trigger(cue, cascade=True)
        if len(cues) == 1:
            # una sola cue: resta in standby, così ogni GO la riavvia
            self.playheadChanged.emit(i)
            return
        if cue.type == "group":
            end = block_end(cues, i)
            if cue.group_mode == "sequence" and i + 1 < end:
                j = i + 2                          # dopo la prima cue del gruppo
            else:
                j = end                            # gli altri tipi: il GO successivo esce dal gruppo
        else:
            j = i + 1
        while j < len(cues) and cues[j].trigger != "manual":
            j += 1
        self.set_playhead(j, lst)

    # ---------------------------------------------------- volume / opacità dal vivo (fader MIDI)
    def set_live_level(self, cue, db):
        """Volume dal vivo di una cue in riproduzione (dB assoluti). False se non è in riproduzione."""
        inst = self.instances.get(cue.id)
        if inst is None or inst.done or cue.type not in ("audio", "video"):
            return False
        base = db_to_lin(cue.level_db)
        inst.live_gain = db_to_lin(db) / base if base > 1e-6 else db_to_lin(db)
        return True

    def set_live_opacity(self, cue, op):
        inst = self.instances.get(cue.id)
        if inst is None or inst.done or cue.type not in ("video", "image"):
            return False
        base = float(cue.opacity)
        inst.live_op = op / base if base > 1e-3 else op
        return True

    # ---------------------------------------------------- crossfade tra cue corrente e successiva
    XF_TYPES = ("audio", "video", "image")

    def current_scene(self, lst=None, exclude=()):
        """Cue «corrente»: le cue audio/video/immagine della lista partite per ultime (insieme)."""
        lst = lst or self.go_list()
        ids = {c.id for c in lst.cues}
        cands = [i for i in self.instances.values() if i.key in ids and i.cue.type in self.XF_TYPES
                 and not i.done and not i.user_stopped and i.key not in exclude]
        if not cands:
            return []
        t = max(i.started_at for i in cands)
        return [i for i in cands if t - i.started_at < 0.1]

    def _xf_begin(self, lst):
        """Avvia la cue in standby (e le sue «con la precedente») a volume/opacità zero."""
        cues = lst.cues
        i = self.playhead_of(lst)
        if not (0 <= i < len(cues)):
            return None
        group = [cues[i]]
        j = i + 1
        while j < len(cues) and cues[j].trigger == "with_prev":
            group.append(cues[j])
            j += 1
        ids = {c.id for c in group}
        A = self.current_scene(lst, exclude=ids)
        try:
            for c in group:
                self._xf_incoming = c.id
                self.pending = [p for p in self.pending if p[1].id != c.id]
                self.start_cue(c, cascade=(c is group[-1]))
        finally:
            self._xf_incoming = None
        while j < len(cues) and cues[j].trigger != "manual":
            j += 1
        self.set_playhead(j if len(cues) > 1 else i, lst)
        B = [self.instances[k] for k in ids if k in self.instances]
        return {"A": A, "B": B, "lst": lst, "index": i}

    def _xf_apply(self, st, t):
        t = max(0.0, min(1.0, t))
        ga, gb = math.cos(t * math.pi / 2), math.sin(t * math.pi / 2)   # potenza costante
        vis_a = [i for i in st["A"] if i.cue.is_visual()]
        vis_b = [i for i in st["B"] if i.cue.is_visual()]
        # dissolvenza senza «buco»: la cue che sta sopra sfuma, quella sotto resta piena
        b_on_top = (not vis_a or not vis_b or
                    max(int(i.cue.layer) for i in vis_b) >= max(int(i.cue.layer) for i in vis_a))
        for i in st["A"]:
            if not i.done:
                i.xf_gain = ga
                i.xf_op = 1.0 if (b_on_top and vis_b) else 1.0 - t
        for i in st["B"]:
            if not i.done:
                i.xf_gain = gb
                i.xf_op = t if (b_on_top or not vis_a) else 1.0

    def _xf_finish(self, st):
        for i in st["A"]:
            if not i.done:
                i.stop(0.0)
        for i in st["B"]:
            i.xf_gain = i.xf_op = 1.0
        self.changed.emit()

    def crossfade(self, dur=3.0, lst=None):
        """Crossfade automatico: la cue in standby entra mentre la corrente esce, in «dur» secondi."""
        lst = lst or self.go_list()
        if self.xfade is not None:
            self._xf_finish(self.xfade)
            self.xfade = None
        st = self._xf_begin(lst)
        if st is None:
            return False
        if dur <= 0:
            self._xf_apply(st, 1.0)
            self._xf_finish(st)
            return True
        st.update(t0=self.clock, dur=float(dur))
        self._xf_apply(st, 0.0)
        self.xfade = st
        self.message.emit(tr("Crossfade {0:.1f} s", dur))
        return True

    def set_crossfader(self, p, lst=None):
        """Crossfader manuale (fader MIDI 0..1). Allontanandosi dal lato a riposo parte la cue in
        standby; arrivati in fondo la corrente si ferma e il lato a riposo si inverte; tornando indietro
        fino all'inizio il crossfade si annulla."""
        p = max(0.0, min(1.0, float(p)))
        if self.xf_rest is None:
            self.xf_rest = 0.0 if p < 0.5 else 1.0
        t = abs(p - self.xf_rest)
        st = self.xf_manual
        if st is None:
            if t < 0.02 or self.xfade is not None:
                return
            st = self._xf_begin(lst or self.go_list())
            if st is None:
                return
            self.xf_manual = st
        self._xf_apply(st, t)
        if t >= 0.985:
            self._xf_finish(st)
            self.xf_manual = None
            self.xf_rest = 1.0 - self.xf_rest
        elif t <= 0.01:
            for i in st["B"]:
                if not i.done:
                    i.stop(0.0)
            for i in st["A"]:
                i.xf_gain = i.xf_op = 1.0
            self.set_playhead(st["index"], st["lst"])
            self.xf_manual = None
            self.changed.emit()

    def _xf_tick(self):
        st = self.xfade
        if st is None:
            return
        t = (self.clock - st["t0"]) / st["dur"]
        self._xf_apply(st, t)
        if t >= 1.0:
            self._xf_finish(st)
            self.xfade = None

    def set_playhead(self, idx, lst=None):
        """Sposta il playhead di una lista (di serie quella visualizzata)."""
        lst = lst or self.show.current
        idx = max(0, min(idx, len(lst.cues)))
        self.playheads[lst.id] = idx
        self.playheadChanged.emit(idx)

    def step_playhead(self, delta):
        """Cue successiva/precedente nella lista di GO (OSC/MIDI «next»/«prev»)."""
        lst = self.go_list()
        self.set_playhead(self.playhead_of(lst) + delta, lst)

    def playhead_to(self, cue):
        lst, i = self.show.list_of(cue.id)
        if lst is not None:
            self.set_playhead(i, lst)

    def trigger(self, cue, cascade=True, depth=0):
        """Avvia una cue rispettandone il ritardo (pre-wait)."""
        if depth > MAX_CHAIN:
            self.message.emit(tr("Catena di cue troppo lunga o ricorsiva: interrotta"))
            return
        d = float(cue.delay or 0)
        self.pending = [p for p in self.pending if p[1].id != cue.id]
        if d > 0:
            self.pending.append([self.clock + d, cue, cascade])
            self.changed.emit()
        else:
            self.start_cue(cue, cascade, depth=depth)

    SOLO_TYPES = ("audio", "video", "image", "text", "group")

    def _grid_solo(self, cue):
        """GRID in modalità Solo: una cue che parte (audio, video, immagine, testo o gruppo) ferma con il fade
        del GRID le altre cue di quel tipo attive nel GRID. Le cue di controllo (OSC, MIDI, mixer, fade…)
        non fermano nulla; le cue di un gruppo avviate dal gruppo stesso nemmeno."""
        if cue.type not in self.SOLO_TYPES or cue.group:
            return
        lst, _i = self.show.list_of(cue.id)
        if lst is None or not lst.is_grid or not lst.grid_solo:
            return
        keep = {cue.id} | {c.id for c in lst.cues if c.group == cue.id}
        ids = {c.id for c in lst.cues if c.type in self.SOLO_TYPES} - keep
        if not ids:
            return
        self.pending = [p for p in self.pending if p[1].id not in ids]
        fade = float(lst.grid_fade or 0)
        for inst in list(self.instances.values()):
            if inst.cue.id in ids and not inst.done:
                inst.stop(fade)

    def start_cue(self, cue, cascade=True, silent=False, depth=0):
        self._grid_solo(cue)
        old = self.instances.pop(cue.id, None)
        if old is not None:
            old.user_stopped = True
            old.done = True
            old.cleanup()
        if cue.is_media() and not cue.file:
            self.message.emit(tr("La cue '{0}' non ha un file", cue.label()))
            inst = None
        else:
            inst = INSTANCE_CLASSES[cue.type](self, cue, cascade)
            self.instances[cue.id] = inst
        prev_depth = self._depth
        self._depth = depth
        try:
            if inst is not None:
                try:
                    inst.start(silent=silent)
                except Exception as e:
                    self.message.emit(tr("Errore avviando '{0}': {1}", cue.label(), e))
                    inst.user_stopped = True
                    inst.finish()
                    inst = None
                if inst is not None:
                    self.cueStarted.emit(cue.id)
                    if cue.is_visual() and self.visual_started:
                        self.visual_started()
            if cascade:
                nxt = self._next(cue)
                if nxt is not None and nxt.trigger == "with_prev":
                    self.trigger(nxt, True, depth + 1)
        finally:
            self._depth = prev_depth
        self.changed.emit()
        return inst if (inst is not None and not inst.done) else None

    def _finished(self, inst):
        if self.instances.get(inst.key) is inst:
            del self.instances[inst.key]
        if inst.cascade and not inst.user_stopped:
            nxt = self._next(inst.cue)
            if nxt is not None and nxt.trigger == "after_prev":
                self.trigger(nxt, True, self._depth + 1)
        self.changed.emit()

    def _next(self, cue):
        """Cue successiva nella stessa lista (MAIN o PANEL). Un gruppo «salta» le sue cue; dentro i gruppi
        non sequenziali le cue non si concatenano."""
        from .groups import block_end
        lst, i = self.show.list_of(cue.id)
        if lst is None:
            return None
        cues = lst.cues
        if cue.type == "group":
            j = block_end(cues, i)
            return cues[j] if j < len(cues) else None
        if cue.group:
            hdr = self.show.by_id(cue.group)
            if hdr is not None and hdr.group_mode != "sequence":
                return None
        if i + 1 >= len(cues):
            return None
        return cues[i + 1]

    def stop_cue(self, cue_id, fade=0.0):
        self.pending = [p for p in self.pending if p[1].id != cue_id]
        inst = self.instances.get(cue_id)
        if inst:
            inst.stop(fade)
        self.changed.emit()

    def set_blind(self, list_id, on):
        """BLIND di un GRID: le cue che partono da lì funzionano ma non mandano audio e video in uscita."""
        if on:
            self.blind_lists.add(list_id)
        else:
            self.blind_lists.discard(list_id)
        self.changed.emit()

    # ------------------------------------------------------------------ Tap Tempo
    def group_tap(self, group_id):
        """Una battuta del Tap Tempo di un gruppo: dalle ultime battute calcola i BPM e rimette in fase il
        battito (la prossima cue arriva un intervallo dopo questa battuta)."""
        c = self.show.by_id(group_id)
        if c is None or c.type != "group":
            return None
        t = self.clock
        taps = self.taps.setdefault(group_id, [])
        if taps and t - taps[-1] > 2.5:          # pausa lunga: si ricomincia a contare
            taps.clear()
        taps.append(t)
        del taps[:-8]
        if len(taps) >= 2:
            iv = (taps[-1] - taps[0]) / (len(taps) - 1)
            if iv > 0:
                c.group_bpm = round(max(20.0, min(300.0, 60.0 / iv)), 1)
                self.show.dirty = True
        if tap_active(c):
            nxt = t + tap_interval(c)
            inst = self.instances.get(group_id)
            if isinstance(inst, GroupInstance) and not inst.done and inst.beat_next is not None:
                inst.beat_next = nxt
            if group_id in self.tempo_runs:
                self.tempo_runs[group_id]["next"] = nxt
        self.changed.emit()
        return c.group_bpm

    def _tempo_tick(self):
        """Gruppi Sequence col Tap Tempo: a ogni battito un GO sulla loro lista, finché il playhead è dentro."""
        if not self.tempo_runs:
            return
        from .groups import block_end
        for gid, run in list(self.tempo_runs.items()):
            c = self.show.by_id(gid)
            lst = run["lst"]
            i = lst.index_of(gid) if c is not None else -1
            ph = self.playhead_of(lst)
            if i < 0 or not tap_active(c) or not (i < ph < block_end(lst.cues, i)):
                del self.tempo_runs[gid]            # gruppo finito, uscito o modificato
                continue
            if self.clock >= run["next"]:
                iv = tap_interval(c)
                run["next"] += iv
                if run["next"] < self.clock:
                    run["next"] = self.clock + iv
                self.go(lst)

    def stop_all(self, fade=0.0, exclude=None):
        self.tempo_runs.clear()
        self.pending = []
        self.xfade = None
        self.xf_manual = None
        if self.paused_all:
            self.resume_all()
            fade = 0.0
        for inst in list(self.instances.values()):
            if inst.key != exclude:
                inst.stop(fade)
        self.changed.emit()

    def list_active(self, lst):
        """Cue attive (o in pre-wait) di una lista."""
        ids = {c.id for c in lst.cues}
        return [i for i in self.instances.values() if i.cue.id in ids] + \
               [p for p in self.pending if p[1].id in ids]

    def stop_list(self, lst, fade=0.0):
        """STOP di un solo PANEL: ferma le cue di quella lista (e i pre-wait), le altre continuano."""
        ids = {c.id for c in lst.cues}
        for gid in [g for g in self.tempo_runs if g in ids]:
            del self.tempo_runs[gid]
        self.pending = [p for p in self.pending if p[1].id not in ids]
        for inst in list(self.instances.values()):     # un crossfade in corso ignora le cue già ferme
            if inst.cue.id in ids:
                inst.stop(fade)
        self.changed.emit()

    def pause_cue(self, cue_id, on):
        inst = self.instances.get(cue_id)
        if inst:
            inst.pause(on)
        self.changed.emit()

    def pause_all(self):
        self.paused_all = True
        self.audio.paused_all = True
        for inst in self.instances.values():
            if isinstance(inst, VideoInstance):
                inst._apply_pause()
        self.changed.emit()

    def resume_all(self):
        self.paused_all = False
        self.audio.paused_all = False
        self._last = time.monotonic()
        for inst in self.instances.values():
            if isinstance(inst, VideoInstance):
                inst._apply_pause()
        self.changed.emit()

    # ---------------------------------------------------- stato per la UI
    def cue_state(self, cue_id):
        inst = self.instances.get(cue_id)
        if inst is not None:
            return inst
        for p in self.pending:
            if p[1].id == cue_id:
                return max(0.0, p[0] - self.clock)
        return None

    def waiting(self):
        return [(p[1], max(0.0, p[0] - self.clock)) for p in self.pending]

    # ---------------------------------------------------- clock
    def _tick(self):
        while self.audio.messages:
            self.message.emit(self.audio.messages.pop(0))
        t = time.monotonic()
        dt = t - self._last
        self._last = t
        if self.paused_all:
            return
        self.clock += dt
        due = [p for p in self.pending if p[0] <= self.clock]
        if due:
            self.pending = [p for p in self.pending if p[0] > self.clock]
            for _, cue, cascade in sorted(due, key=lambda p: p[0]):
                self.start_cue(cue, cascade)
        visual = False
        for inst in list(self.instances.values()):
            try:
                inst.tick(self.clock, dt)
            except Exception as e:
                self.message.emit(tr("Errore in '{0}': {1}", inst.cue.label(), e))
                inst.user_stopped = True
                inst.finish()
            if inst.cue.is_visual():
                visual = True
        self._xf_tick()
        self._tempo_tick()
        if visual or self.comp.layers:
            self.comp.changed()
