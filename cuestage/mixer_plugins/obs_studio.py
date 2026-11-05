"""Plugin CueStage — OBS Studio: obs-websocket 5 (incluso in OBS dalla versione 28) e 4.x (plugin per OBS 27 e
precedenti, o «4.x Compat»). Il protocollo si riconosce da solo: la 5 manda subito un Hello, la 4.x no.

In OBS: Strumenti › Impostazioni del server WebSocket › «Abilita server WebSocket» (porta 4455, password
facoltativa). Funziona con OBS sullo stesso computer (indirizzo «localhost») o su un altro della rete;
«Cerca regie in rete» prova la porta 4455 sulle reti locali e su questo computer.

Protocollo (JSON su WebSocket): il server manda Hello (op 0, con salt e challenge se c'è la password), il client
risponde Identify (op 1; autenticazione = base64(sha256(base64(sha256(password + salt)) + challenge))),
il server conferma con Identified (op 2); poi Request (op 6) → RequestResponse (op 7).
Con la 4.x le richieste hanno «request-type» e «message-id» e i campi in cima al JSON (GetAuthRequired /
Authenticate con la stessa formula): _v4() traduce le richieste della 5 usate qui in quelle della 4.x, così il
resto del plugin è uguale per tutte e due.

I comandi si riferiscono a scene, sorgenti e ingressi per NOME (letti da OBS): una cue resta valida anche se
in OBS le scene cambiano ordine. La modalità Studio può essere accesa o spenta: «Vai a» usa la transizione
corrente in entrambi i casi (in Studio mette la scena in anteprima e fa la transizione), «Taglio a» passa
senza transizione, «In anteprima» funziona solo in modalità Studio.
"""
import base64
import hashlib
import json
import socket
import threading
import time

from cuestage.i18n import tr
from cuestage.mixers import MixerDriver, MixerError, WebSocketClient, tcp_scan

PORT = 4455
PORT_V4 = 4444                       # porta predefinita del plugin obs-websocket 4.x
MEDIA_ACTIONS = [("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY", tr("Play")),
                 ("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE", tr("Pausa")),
                 ("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP", tr("Stop")),
                 ("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART", tr("Ricomincia")),
                 ("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_NEXT", tr("Successivo")),
                 ("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PREVIOUS", tr("Precedente"))]
MEDIA_KINDS = ("ffmpeg_source", "vlc_source")
VOL_MIN = -600                       # volume in decimi di dB: −60 dB = −∞
VOL_MAX = 260
# transizione «Taglio» nella 4.x (che non dice il tipo): nomi predefiniti di OBS nelle lingue più comuni
CUT_NAMES = {"cut", "taglio", "schnitt", "coupure", "corte", "corte seco", "rez", "knip", "cięcie", "střih"}
V4_MEDIA = {"OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY": ("PlayPauseMedia", {"playPause": False}),
            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE": ("PlayPauseMedia", {"playPause": True}),
            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP": ("StopMedia", {}),
            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART": ("RestartMedia", {}),
            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_NEXT": ("NextMedia", {}),
            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PREVIOUS": ("PreviousMedia", {})}
V4_OUTPUTS = {"StartRecord": "StartRecording", "StopRecord": "StopRecording", "PauseRecord": "PauseRecording",
              "ResumeRecord": "ResumeRecording", "StartStream": "StartStreaming", "StopStream": "StopStreaming",
              "StartVirtualCam": "StartVirtualCam", "StopVirtualCam": "StopVirtualCam",
              "StartReplayBuffer": "StartReplayBuffer", "StopReplayBuffer": "StopReplayBuffer"}
G_SCENES, G_TRANS, G_SOURCES, G_AUDIO, G_MEDIA, G_OUT = (tr("Scene"), tr("Transizioni"), tr("Sorgenti"),
                                                        tr("Audio"), tr("Media"), tr("Uscite"))


def obs_auth(password, salt, challenge):
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class ObsError(MixerError):
    pass


class OBSStudio(MixerDriver):
    ID = "obs_studio"
    NAME = tr("OBS Studio (WebSocket)")
    DEFAULT_PORT = PORT
    FIELDS = ("host", "port", "password")
    CONTROLS = True
    SWITCHER = True
    NOTES = tr("In OBS: Strumenti › Impostazioni del server WebSocket › «Abilita server WebSocket» (porta 4455; con "
               "OBS 27 e il plugin obs-websocket 4.x di solito 4444) e, se impostata, la stessa password qui. Indirizzo «localhost» se OBS gira su questo computer. Scene, "
               "sorgenti e ingressi si leggono da OBS (⟲ Leggi dopo averli cambiati). «Vai a» usa la transizione "
               "corrente, anche in modalità Studio; «In anteprima» e «TAKE» solo in modalità Studio.")

    def __init__(self, conf):
        super().__init__(conf)
        self.host = self.host or "localhost"
        self.ws = None
        self.lock = threading.Lock()
        self._rid = 0
        self.info = {}
        self.proto = 5
        self._audio_kinds = None          # (4.x) tipi di sorgente con audio

    # ------------------------------------------------------------------ collegamento
    def _connect(self):
        if self.ws is not None:
            return self.ws
        try:
            ws = WebSocketClient(self.host, self.port or PORT, timeout=3.0, protocol="obswebsocket.json")
        except OSError as e:
            raise MixerError(tr("OBS non raggiungibile su {0}:{1} ({2}): OBS acceso e server WebSocket abilitato?",
                                self.host, self.port or PORT, e))
        ws.settimeout(2.0)
        step = tr("attesa del messaggio Hello di OBS")
        try:
            try:
                raw = ws.recv()
            except socket.timeout:
                raw = None                     # niente Hello: obs-websocket 4.x
            if raw is None and ws.close_code is None:
                return self._connect_v4(ws)
            ws.settimeout(5.0)
            hello = json.loads(raw or "{}")
            if hello.get("op") != 0:
                raise MixerError(tr("risposta inattesa: non è OBS Studio?"))
            d = hello.get("d") or {}
            ident = {"rpcVersion": 1, "eventSubscriptions": 0}       # niente eventi: lo stato si legge a richiesta
            auth = d.get("authentication")
            if auth:
                pw = str(self.conf.get("password") or "")
                if not pw:
                    raise MixerError(tr("OBS chiede una password: impostala nella configurazione della regia"))
                ident["authentication"] = obs_auth(pw, auth.get("salt", ""), auth.get("challenge", ""))
            ws.send(json.dumps({"op": 1, "d": ident}))
            step = tr("attesa della conferma Identified di OBS")
            try:
                ok = ws.recv()
            except (OSError, ConnectionError):
                ok = None
            if ok is None:
                raise MixerError(tr("OBS ha chiuso il collegamento: password sbagliata?"))
            if json.loads(ok).get("op") != 2:
                raise MixerError(tr("OBS non ha accettato il collegamento"))
        except (OSError, ValueError, ConnectionError) as e:
            ws.close()
            raise MixerError(tr("collegamento a OBS non riuscito: {0} ({1})", step, e))
        except MixerError:
            ws.close()
            raise
        self.info = {"websocket": d.get("obsWebSocketVersion", "?")}
        self.proto = 5
        self.ws = ws
        return ws

    def _connect_v4(self, ws):
        """obs-websocket 4.x: GetAuthRequired e, se serve, Authenticate."""
        ws.settimeout(5.0)
        self.ws, self.proto = ws, 4
        try:
            r = self._call4("GetAuthRequired")
            if r.get("authRequired"):
                pw = str(self.conf.get("password") or "")
                if not pw:
                    raise MixerError(tr("OBS chiede una password: impostala nella configurazione della regia"))
                try:
                    self._call4("Authenticate", {"auth": obs_auth(pw, r.get("salt", ""), r.get("challenge", ""))})
                except ObsError:
                    raise MixerError(tr("OBS ha rifiutato la password"))
            v = self._call4("GetVersion")
        except (OSError, ValueError, ConnectionError) as e:
            self.ws = None
            ws.close()
            raise MixerError(tr("collegamento a OBS non riuscito: {0} ({1})", tr("protocollo 4.x"), e))
        except MixerError:
            self.ws = None
            ws.close()
            raise
        self.info = {"websocket": v.get("obs-websocket-version", "4.x")}
        self._audio_kinds = None
        return ws

    def _call4(self, rtype, fields=None):
        """Una richiesta nel formato 4.x sul collegamento aperto (risposta: dict; errore → ObsError)."""
        ws = self.ws
        self._rid += 1
        rid = str(self._rid)
        ws.send(json.dumps(dict(fields or {}, **{"request-type": rtype, "message-id": rid})))
        while True:
            txt = ws.recv()
            if txt is None:
                raise ConnectionError(tr("connessione chiusa"))
            m = json.loads(txt)
            if m.get("message-id") == rid:
                break                                       # (gli «update-type» sono eventi: si ignorano)
        if m.get("status") != "ok":
            raise ObsError(tr("OBS: {0} non riuscito ({1})", rtype, m.get("error") or "?"))
        return m

    def _v4(self, rtype, a):
        """Richiesta nel formato della 5 → richieste 4.x; risposta nella forma della 5."""
        c = self._call4
        if rtype == "GetVersion":
            r = c("GetVersion")
            return {"obsVersion": r.get("obs-studio-version", "?"), "obsWebSocketVersion": r.get("obs-websocket-version", "4.x"),
                    "platformDescription": ""}
        if rtype == "GetStudioModeEnabled":
            return {"studioModeEnabled": bool(c("GetStudioModeStatus").get("studio-mode"))}
        if rtype == "SetStudioModeEnabled":
            c("EnableStudioMode" if a.get("studioModeEnabled") else "DisableStudioMode")
            return {}
        if rtype == "GetSceneList":
            r = c("GetSceneList")
            pvw = None
            try:
                pvw = c("GetPreviewScene").get("name")
            except ObsError:
                pass                                        # modalità Studio spenta
            return {"scenes": [{"sceneName": sc.get("name")} for sc in reversed(r.get("scenes") or [])],
                    "currentProgramSceneName": r.get("current-scene"), "currentPreviewSceneName": pvw}
        if rtype == "SetCurrentProgramScene":
            c("SetCurrentScene", {"scene-name": a["sceneName"]})
            return {}
        if rtype == "SetCurrentPreviewScene":
            c("SetPreviewScene", {"scene-name": a["sceneName"]})
            return {}
        if rtype == "TriggerStudioModeTransition":
            c("TransitionToProgram")
            return {}
        if rtype == "GetSceneTransitionList":
            r = c("GetTransitionList")
            return {"currentSceneTransitionName": r.get("current-transition"),
                    "transitions": [{"transitionName": t.get("name"),
                                     "transitionKind": "cut_transition" if str(t.get("name")).lower() in CUT_NAMES else ""}
                                    for t in r.get("transitions") or []]}
        if rtype == "GetCurrentSceneTransition":
            r = c("GetCurrentTransition")
            return {"transitionName": r.get("name"), "transitionDuration": r.get("duration")}
        if rtype == "SetCurrentSceneTransition":
            c("SetCurrentTransition", {"transition-name": a["transitionName"]})
            return {}
        if rtype == "SetCurrentSceneTransitionDuration":
            c("SetTransitionDuration", {"duration": int(a["transitionDuration"])})
            return {}
        if rtype == "GetSceneItemList":
            for sc in c("GetSceneList").get("scenes") or []:
                if sc.get("name") == a["sceneName"]:
                    return {"sceneItems": [{"sourceName": it.get("name"), "sceneItemEnabled": bool(it.get("render")),
                                            "sceneItemId": it.get("id")} for it in reversed(sc.get("sources") or [])]}
            raise ObsError(tr("OBS: scena «{0}» non trovata", a["sceneName"]))
        if rtype == "GetSceneItemId":
            return {"sceneItemId": a["sourceName"]}          # la 4.x usa il nome della sorgente
        if rtype == "SetSceneItemEnabled":
            c("SetSceneItemRender", {"scene-name": a["sceneName"], "source": a["sceneItemId"],
                                     "render": bool(a["sceneItemEnabled"])})
            return {}
        if rtype == "GetInputList":
            srcs = c("GetSourcesList").get("sources") or []
            return {"inputs": [{"inputName": x.get("name"), "inputKind": x.get("typeId")} for x in srcs
                               if x.get("type") == "input"]}
        if rtype == "GetInputMute":
            if self._audio_kinds is None:
                try:
                    types = c("GetSourceTypesList").get("types") or []
                    self._audio_kinds = {t.get("typeId") for t in types if (t.get("caps") or {}).get("hasAudio")}
                except ObsError:
                    self._audio_kinds = set()
            kind = next((x.get("typeId") for x in c("GetSourcesList").get("sources") or []
                         if x.get("name") == a["inputName"]), None)
            if self._audio_kinds and kind not in self._audio_kinds:
                raise ObsError(tr("OBS: «{0}» non ha audio", a["inputName"]))
            return {"inputMuted": bool(c("GetMute", {"source": a["inputName"]}).get("muted"))}
        if rtype == "SetInputMute":
            c("SetMute", {"source": a["inputName"], "mute": bool(a["inputMuted"])})
            return {}
        if rtype == "GetInputVolume":
            return {"inputVolumeDb": c("GetVolume", {"source": a["inputName"], "useDecibel": True}).get("volume", 0.0)}
        if rtype == "SetInputVolume":
            c("SetVolume", {"source": a["inputName"], "volume": float(a["inputVolumeDb"]), "useDecibel": True})
            return {}
        if rtype == "TriggerMediaInputAction":
            req, extra = V4_MEDIA[a["mediaAction"]]
            c(req, dict(extra, sourceName=a["inputName"]))
            return {}
        if rtype == "GetRecordStatus":
            r = c("GetRecordingStatus")
            return {"outputActive": bool(r.get("isRecording")), "outputPaused": bool(r.get("isRecordingPaused"))}
        if rtype == "GetStreamStatus":
            return {"outputActive": bool(c("GetStreamingStatus").get("streaming"))}
        if rtype == "GetVirtualCamStatus":
            return {"outputActive": bool(c("GetVirtualCamStatus").get("isVirtualCam"))}
        if rtype == "GetReplayBufferStatus":
            return {"outputActive": bool(c("GetReplayBufferStatus").get("isReplayBufferActive"))}
        if rtype in V4_OUTPUTS:
            try:
                c(V4_OUTPUTS[rtype])
            except ObsError:
                pass                                        # già acceso / già spento
            return {}
        if rtype == "SaveReplayBuffer":
            c("SaveReplayBuffer")
            return {}
        raise ObsError(tr("OBS: {0} non disponibile con obs-websocket 4.x", rtype))

    def close(self):
        if self.ws is not None:
            self.ws.close()
        self.ws = None

    def request(self, rtype, data=None, ok_codes=(100,)):
        """Una richiesta a OBS; risponde con responseData (dict). Errore di OBS → ObsError."""
        with self.lock:
            for attempt in (0, 1):
                ws = self._connect()
                if self.proto == 4:
                    try:
                        return self._v4(rtype, data or {})
                    except (OSError, ConnectionError, ValueError) as e:
                        self.close()
                        if attempt:
                            raise MixerError(tr("OBS non risponde ({0})", e))
                        continue
                self._rid += 1
                rid = str(self._rid)
                try:
                    ws.send(json.dumps({"op": 6, "d": {"requestType": rtype, "requestId": rid,
                                                        "requestData": data or {}}}))
                    while True:
                        txt = ws.recv()
                        if txt is None:
                            raise ConnectionError(tr("connessione chiusa"))
                        m = json.loads(txt)
                        if m.get("op") == 7 and (m.get("d") or {}).get("requestId") == rid:
                            break
                except (OSError, ConnectionError, ValueError) as e:
                    self.close()
                    if attempt:
                        raise MixerError(tr("OBS non risponde ({0})", e))
                    continue                                   # OBS riavviato: un nuovo collegamento
                d = m["d"]
                st = d.get("requestStatus") or {}
                if not st.get("result") and st.get("code") not in ok_codes:
                    raise ObsError(tr("OBS: {0} non riuscito ({1})", rtype, st.get("comment") or st.get("code")))
                return d.get("responseData") or {}

    def status(self):
        v = self.request("GetVersion")
        st = self.request("GetStudioModeEnabled")
        return {tr("OBS"): v.get("obsVersion", "?"), "WebSocket": v.get("obsWebSocketVersion", "?"),
                tr("piattaforma"): v.get("platformDescription", ""),
                tr("modalità Studio"): tr("accesa") if st.get("studioModeEnabled") else tr("spenta")}

    # ------------------------------------------------------------------ elenco dei comandi
    def _scenes(self):
        r = self.request("GetSceneList")
        names = [s.get("sceneName") for s in reversed(r.get("scenes") or [])]   # OBS le elenca dal basso
        return names, r.get("currentProgramSceneName"), r.get("currentPreviewSceneName")

    def list_controls(self):
        out = []
        scenes, program, preview = self._scenes()
        studio = bool(self.request("GetStudioModeEnabled").get("studioModeEnabled"))
        out.append(dict(key="on_air", name=tr("In onda"), type="enum", items=scenes or ["—"],
                        value=scenes.index(program) if program in scenes else 0, writable=False, group=G_SCENES))
        out.append(dict(key="studio", name=tr("Modalità Studio (anteprima + programma)"), type="bool",
                        value=int(studio), group=G_SCENES))
        if studio:
            out.append(dict(key="take", name=tr("TAKE (transizione anteprima → programma)"), type="action",
                            group=G_SCENES))
        for sc in scenes:
            out.append(dict(key=f"go:{sc}", name=tr("Vai a «{0}»", sc), type="action", group=G_SCENES))
        for sc in scenes:
            out.append(dict(key=f"cut:{sc}", name=tr("Taglio a «{0}»", sc), type="action", group=G_SCENES))
        for sc in scenes:
            out.append(dict(key=f"pvw:{sc}", name=tr("In anteprima «{0}» (Studio)", sc), type="action",
                            group=G_SCENES))
        # transizioni
        try:
            tr_ = self.request("GetSceneTransitionList")
            cur = tr_.get("currentSceneTransitionName")
            for t in tr_.get("transitions") or []:
                n = t.get("transitionName")
                out.append(dict(key=f"trans:{n}", name=tr("Transizione «{0}»", n) + (" ✓" if n == cur else ""),
                                type="action", group=G_TRANS))
            ms = self.request("GetCurrentSceneTransition").get("transitionDuration")
            out.append(dict(key="trans_ms", name=tr("Durata della transizione"), type="int", min=0, max=10000,
                            value=int(ms or 300), fmt=(0, 0.001, " s", 2), group=G_TRANS))
        except ObsError:
            pass
        # sorgenti visibili nelle scene
        for sc in scenes:
            try:
                items = self.request("GetSceneItemList", {"sceneName": sc}).get("sceneItems") or []
            except ObsError:
                continue
            for it in reversed(items):
                src = it.get("sourceName")
                out.append(dict(key=f"item:{sc}\x1f{src}", name=f"{sc} › {src}", type="bool",
                                value=int(bool(it.get("sceneItemEnabled"))), group=G_SOURCES))
        # ingressi: audio e media
        inputs = self.request("GetInputList").get("inputs") or []
        for inp in inputs:
            name = inp.get("inputName")
            try:
                mute = self.request("GetInputMute", {"inputName": name}).get("inputMuted")
            except ObsError:
                mute = None                                   # ingresso senza audio
            if mute is not None:
                try:
                    db = float(self.request("GetInputVolume", {"inputName": name}).get("inputVolumeDb") or 0)
                except ObsError:
                    db = 0.0
                out.append(dict(key=f"vol:{name}", name=tr("{0} volume", name), type="int", min=VOL_MIN,
                                max=VOL_MAX, value=max(VOL_MIN, min(VOL_MAX, int(round(db * 10)))),
                                fmt=(0, 0.1, " dB", 1), group=G_AUDIO))
                out.append(dict(key=f"mute:{name}", name=tr("{0} muto", name), type="bool", value=int(bool(mute)),
                                group=G_AUDIO))
            if inp.get("inputKind") in MEDIA_KINDS or inp.get("unversionedInputKind") in MEDIA_KINDS:
                for i, (_a, label) in enumerate(MEDIA_ACTIONS[:4]):          # Play, Pausa, Stop, Ricomincia
                    out.append(dict(key=f"media:{name}\x1f{i}", name=f"{name} › {label}", type="action",
                                    group=G_MEDIA))
        # uscite
        for key, req, field, label in (("record", "GetRecordStatus", "outputActive", tr("Registrazione")),
                                       ("stream", "GetStreamStatus", "outputActive", tr("Streaming")),
                                       ("vcam", "GetVirtualCamStatus", "outputActive", tr("Telecamera virtuale")),
                                       ("replay", "GetReplayBufferStatus", "outputActive", tr("Replay buffer"))):
            try:
                on = bool(self.request(req).get(field))
            except ObsError:
                continue
            out.append(dict(key=key, name=label, type="bool", value=int(on), group=G_OUT))
            if key == "record":
                out.append(dict(key="record_pause", name=tr("Registrazione in pausa"), type="bool", value=0,
                                group=G_OUT))
            if key == "replay":
                out.append(dict(key="replay_save", name=tr("Salva il replay"), type="action", group=G_OUT))
        for c in out:
            c.setdefault("min", 0)
            c.setdefault("max", len(c.get("items") or []) - 1 if c["type"] == "enum" else 1)
            c.setdefault("items", [])
            c.setdefault("db", None)
            c.setdefault("writable", True)
        return out

    # ------------------------------------------------------------------ comandi
    def _studio(self):
        return bool(self.request("GetStudioModeEnabled").get("studioModeEnabled"))

    def _cut_name(self):
        for t in self.request("GetSceneTransitionList").get("transitions") or []:
            if t.get("transitionKind") == "cut_transition":
                return t.get("transitionName")
        return None

    def _go(self, scene, cut=False):
        studio = self._studio()
        old = old_ms = None
        if cut:
            name = self._cut_name()
            cur = self.request("GetCurrentSceneTransition")
            if name and cur.get("transitionName") != name:
                old = cur.get("transitionName")
                self.request("SetCurrentSceneTransition", {"transitionName": name})
            elif not name:                       # nessuna transizione «Taglio» riconosciuta: durata minima
                old_ms = cur.get("transitionDuration")
                self.request("SetCurrentSceneTransitionDuration", {"transitionDuration": 0 if self.proto == 4 else 50})
        try:
            if studio:
                self.request("SetCurrentPreviewScene", {"sceneName": scene})
                self.request("TriggerStudioModeTransition")
            else:
                self.request("SetCurrentProgramScene", {"sceneName": scene})
        finally:
            if old:
                time.sleep(0.05)
                self.request("SetCurrentSceneTransition", {"transitionName": old})
            if old_ms:
                time.sleep(0.1)
                self.request("SetCurrentSceneTransitionDuration", {"transitionDuration": int(old_ms)})

    def set_control(self, key, value):
        value = int(value or 0)
        kind, _, arg = key.partition(":")
        if kind == "go":
            self._go(arg)
        elif kind == "cut":
            self._go(arg, cut=True)
        elif kind == "pvw":
            if not self._studio():
                raise MixerError(tr("«In anteprima» funziona solo con la modalità Studio di OBS accesa"))
            self.request("SetCurrentPreviewScene", {"sceneName": arg})
        elif key == "take":
            if not self._studio():
                raise MixerError(tr("TAKE funziona solo con la modalità Studio di OBS accesa"))
            self.request("TriggerStudioModeTransition")
        elif key == "studio":
            self.request("SetStudioModeEnabled", {"studioModeEnabled": bool(value)})
        elif kind == "trans":
            self.request("SetCurrentSceneTransition", {"transitionName": arg})
        elif key == "trans_ms":
            self.request("SetCurrentSceneTransitionDuration", {"transitionDuration": max(50, value)})
        elif kind == "item":
            scene, _, src = arg.partition("\x1f")
            iid = self.request("GetSceneItemId", {"sceneName": scene, "sourceName": src}).get("sceneItemId")
            self.request("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": iid,
                                                 "sceneItemEnabled": bool(value)})
        elif kind == "vol":
            db = -100.0 if value <= VOL_MIN else value / 10.0
            self.request("SetInputVolume", {"inputName": arg, "inputVolumeDb": db})
        elif kind == "mute":
            self.request("SetInputMute", {"inputName": arg, "inputMuted": bool(value)})
        elif kind == "media":
            name, _, idx = arg.partition("\x1f")
            i = int(idx) if idx.isdigit() else value
            action = MEDIA_ACTIONS[max(0, min(i, len(MEDIA_ACTIONS) - 1))][0]
            self.request("TriggerMediaInputAction", {"inputName": name, "mediaAction": action})
        elif key in ("record", "stream", "vcam", "replay"):
            req = {"record": "Record", "stream": "Stream", "vcam": "VirtualCam", "replay": "ReplayBuffer"}[key]
            # 500/501 = già acceso / già spento: va bene così
            self.request(("Start" if value else "Stop") + req, ok_codes=(100, 500, 501))
        elif key == "record_pause":
            self.request("PauseRecord" if value else "ResumeRecord", ok_codes=(100, 500, 501))
        elif key == "replay_save":
            self.request("SaveReplayBuffer")
        else:
            raise MixerError(tr("comando OBS sconosciuto: {0}", key))

    # ------------------------------------------------------------------ ricerca in rete
    @classmethod
    def probe(cls, host, port=PORT, timeout=1.0):
        """Risponde come OBS (obs-websocket)? → dict con versione e password richiesta, altrimenti None."""
        try:
            ws = WebSocketClient(host, port, timeout=timeout, protocol="obswebsocket.json")
        except (OSError, MixerError):
            return None
        try:
            ws.settimeout(timeout)
            try:
                hello = json.loads(ws.recv() or "{}")
            except socket.timeout:                      # niente Hello: forse obs-websocket 4.x
                ws.settimeout(timeout)

                def ask(rtype):
                    ws.send(json.dumps({"request-type": rtype, "message-id": rtype}))
                    for _i in range(20):                     # salta gli eventi («update-type»)
                        m = json.loads(ws.recv() or "{}")
                        if m.get("message-id") == rtype:
                            return m
                    return {}
                v = ask("GetVersion")
                if "obs-websocket-version" not in v:
                    return None
                return {"version": v["obs-websocket-version"], "auth": bool(ask("GetAuthRequired").get("authRequired"))}
        except (OSError, ValueError, ConnectionError):
            return None
        finally:
            ws.close()
        d = hello.get("d") or {}
        if hello.get("op") != 0 or "obsWebSocketVersion" not in d:
            return None
        return {"version": d.get("obsWebSocketVersion"), "auth": bool(d.get("authentication"))}

    LOCAL_HOSTS = ("127.0.0.1", "::1")

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        """OBS sulle reti /24 locali e su questo computer (localhost IPv4 e IPv6, indirizzi propri): basta che il
        server WebSocket di OBS sia acceso, anche senza nessuna rete collegata."""
        local_ips = [ip for ip in local_ips or [] if ip]
        out, seen = [], set()
        for port in (PORT, PORT_V4):                        # 4455 (OBS 28+) e 4444 (plugin 4.x per OBS 27)
            hosts = list(tcp_scan([ip for ip in local_ips if not ip.startswith("127.")], port)) if local_ips else []
            for h in list(cls.LOCAL_HOSTS) + local_ips:     # OBS su questo computer
                try:
                    with socket.create_connection((h, port), timeout=0.3):
                        hosts.insert(0, h)
                except OSError:
                    pass
            for h in dict.fromkeys(hosts):
                p = cls.probe(h, port)
                if p is None:
                    continue
                local = h in cls.LOCAL_HOSTS or h.startswith("127.") or h in local_ips
                k = ("local" if local else h, port)
                if k in seen:
                    continue
                seen.add(k)
                out.append(cls._found(h, port, p, local))
        return out

    @classmethod
    def _found(cls, h, port, p, local):
        return {"driver": cls.ID, "host": "localhost" if local else h, "port": port,
                "name": tr("OBS su questo computer") if local else "OBS " + h, "model": "OBS Studio",
                "info": "WebSocket " + str(p["version"]) + (tr(" · password richiesta") if p["auth"] else "")}
