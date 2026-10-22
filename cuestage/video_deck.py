"""Precaricamento dei video: un «deck» è un lettore già aperto, fermo sul primo fotogramma da mostrare.

Aprire un file video grande (demuxer, decoder, eventuale salto al punto IN) richiede da qualche decimo di secondo
a qualche secondo. Un deck lo fa in anticipo: quando la cue parte, il lettore è già pronto e il primo
fotogramma è già decodificato. Nei loop un secondo deck aspetta fermo all'inizio del loop: al punto di
ripetizione si passa dall'uno all'altro senza dover cercare nel file.
"""
from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

MAX_DECKS = 8           # video tenuti pronti al massimo (ognuno occupa un decoder e un po' di memoria)


class VideoDeck(QObject):
    """Un QMediaPlayer con il suo QVideoSink, «armato» al tempo t (in pausa sul fotogramma di t)."""

    def __init__(self, path, t=0.0):
        super().__init__()
        self.path = path
        self.t = max(0.0, float(t or 0.0))
        self.loaded = False       # file aperto (LoadedMedia)
        self.arming = True        # in pausa sul tempo t, in attesa di partire
        self.ready = False        # il fotogramma di t è arrivato
        self.image = None         # ultimo fotogramma (QImage)
        self.frames = 0
        self.owner = None         # VideoInstance che lo usa: riceve fotogrammi e stati
        self.error = ""
        self.player = QMediaPlayer()
        self.sink = QVideoSink()
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._frame)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.errorOccurred.connect(self._error)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()       # apre il file e decodifica il primo fotogramma senza partire

    # ------------------------------------------------------------ segnali del lettore
    def _status(self, st):
        if st in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and not self.loaded:
            self.loaded = True
            if self.t > 0:
                self.player.setPosition(int(self.t * 1000))
            if self.arming:
                self.player.pause()
            else:
                self.player.play()
        if self.owner is not None:
            self.owner._deck_status(self, st)

    def _frame(self, frame):
        if not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        self.image = img
        self.frames += 1
        if self.arming and self.loaded and not self.ready:
            ft = frame.startTime()
            if ft < 0 or ft / 1e6 >= self.t - 0.25:
                self.ready = True
        if self.owner is not None:
            self.owner._deck_frame(self, img)

    def _error(self, *a):
        self.error = self.player.errorString() or "errore"
        if self.owner is not None:
            self.owner._deck_error(self)

    # ------------------------------------------------------------ comandi
    def arm(self, t):
        """Ferma il lettore sul fotogramma di t, pronto a ripartire da lì."""
        self.t = max(0.0, float(t or 0.0))
        self.arming = True
        self.ready = False
        if self.loaded:
            self.player.pause()
            self.player.setPosition(int(self.t * 1000))

    def go(self):
        """Parte (subito se il file è già aperto, altrimenti appena lo è)."""
        self.arming = False
        if self.loaded:
            self.player.play()

    def dispose(self):
        from .engine import _bury
        self.owner = None
        for sig in (self.sink.videoFrameChanged, self.player.mediaStatusChanged, self.player.errorOccurred):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        try:
            self.player.stop()
        except RuntimeError:
            pass
        _bury(self.player, self.sink, self)


class VideoPreloader:
    """Tiene pronti i video delle cue che probabilmente partiranno presto (vedi MainWindow._preload_videos)."""

    def __init__(self):
        self.decks = {}          # id cue -> VideoDeck
        self.enabled = True

    def update(self, wanted):
        """wanted: [(id cue, file, inizio)] in ordine di priorità. Apre i mancanti, chiude quelli non più utili."""
        if not self.enabled:
            wanted = []
        wanted = wanted[:MAX_DECKS]
        keep = {cid: (path, t) for cid, path, t in wanted}
        for cid in list(self.decks):
            d = self.decks[cid]
            w = keep.get(cid)
            if w is None or w[0] != d.path or abs(w[1] - d.t) > 0.01:
                self.decks.pop(cid).dispose()
        # uno alla volta: aprire molti file insieme rallenterebbe proprio la cue che sta partendo
        for cid, path, t in wanted:
            if cid not in self.decks:
                self.decks[cid] = VideoDeck(path, t)
                break

    def take(self, cue_id, path, t):
        """Il deck pronto per questa cue (che passa alla cue), o None."""
        d = self.decks.get(cue_id)
        if d is None or d.path != path or abs(d.t - max(0.0, float(t or 0.0))) > 0.01 or d.error:
            return None
        return self.decks.pop(cue_id)

    def clear(self):
        for d in self.decks.values():
            d.dispose()
        self.decks.clear()

    def ready_count(self):
        return sum(1 for d in self.decks.values() if d.ready)
