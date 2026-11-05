"""Plugin CueStage — OBS Studio (obs-websocket 5, incluso in OBS dalla versione 28).

In OBS: Strumenti › Impostazioni del server WebSocket › «Abilita server WebSocket» (porta 4455, password
facoltativa). Funziona con OBS sullo stesso computer (indirizzo «localhost») o su un altro della rete;
«Cerca regie in rete» prova la porta 4455 sulle reti locali e su questo computer.

Protocollo (JSON su WebSocket): il server manda Hello (op 0, con salt e challenge se c'è la password), il client
risponde Identify (op 1; autenticazione = base64(sha256(base64(sha256(password + salt)) + challenge))),
il server conferma con Identified (op 2); poi Request (op 6) → RequestResponse (op 7).

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
MEDIA_ACTIONS = [("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY", tr("Play")),
                 ("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE", tr("Pausa")),
                 ("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP", tr("Stop")),
                 ("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART", tr("Ricomincia")),
                 ("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_NEXT", tr("Successivo")),
                 ("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PREVIOUS", tr("Precedente"))]
MEDIA_KINDS = ("ffmpeg_source", "vlc_source")
VOL_MIN = -600                       # volume in decimi di dB: −60 dB = −∞
VOL_MAX = 260
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
    NOTES = tr("In OBS: Strumenti › Impostazioni del server WebSocket › «Abilita server WebSocket» (porta 4455) e, se "
               "impostata, la stessa password qui. Indirizzo «localhost» se OBS gira su questo computer. Scene, "
               "sorgenti e ingressi si leggono da OBS (⟲ Leggi dopo averli cambiati). «Vai a» usa la transizione "
               "corrente, anche in modalità Studio; «In anteprima» e «TAKE» solo in modalità Studio.")

    def __init__(self, conf):
        super().__init__(conf)
        self.host = self.host or "localhost"
        self.ws = None
        self.lock = threading.Lock()
        self._rid = 0
        self.info = {}

    # ------------------------------------------------------------------ collegamento
    def _connect(self):
        if self.ws is not None:
            return self.ws
        try:
            ws = WebSocketClient(self.host, self.port or PORT, timeout=3.0)
        except OSError as e:
            raise MixerError(tr("OBS non raggiungibile su {0}:{1} ({2}): OBS acceso e server WebSocket abilitato?",
                                self.host, self.port or PORT, e))
        ws.settimeout(5.0)
        try:
            hello = json.loads(ws.recv() or "{}")
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
            raise MixerError(tr("collegamento a OBS non riuscito ({0})", e))
        except MixerError:
            ws.close()
            raise
        self.info = {"websocket": d.get("obsWebSocketVersion", "?")}
        self.ws = ws
        return ws

    def close(self):
        if self.ws is not None:
            self.ws.close()
        self.ws = None

    def request(self, rtype, data=None, ok_codes=(100,)):
        """Una richiesta a OBS; risponde con responseData (dict). Errore di OBS → ObsError."""
        with self.lock:
            for attempt in (0, 1):
                ws = self._connect()
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
        old = None
        if cut:
            name = self._cut_name()
            cur = self.request("GetCurrentSceneTransition").get("transitionName")
            if name and cur != name:
                old = cur
                self.request("SetCurrentSceneTransition", {"transitionName": name})
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
            ws = WebSocketClient(host, port, timeout=timeout)
        except (OSError, MixerError):
            return None
        try:
            ws.settimeout(timeout)
            hello = json.loads(ws.recv() or "{}")
        except (OSError, ValueError, ConnectionError):
            return None
        finally:
            ws.close()
        d = hello.get("d") or {}
        if hello.get("op") != 0 or "obsWebSocketVersion" not in d:
            return None
        return {"version": d.get("obsWebSocketVersion"), "auth": bool(d.get("authentication"))}

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        hosts = list(tcp_scan(local_ips, PORT))
        for h in ["127.0.0.1"] + list(local_ips):      # OBS anche su questo computer
            try:
                with socket.create_connection((h, PORT), timeout=0.3):
                    hosts.append(h)
            except OSError:
                pass
        out, seen = [], set()
        for h in dict.fromkeys(hosts):
            p = cls.probe(h)
            if p is None:
                continue
            local = h == "127.0.0.1" or h in local_ips
            if local and "local" in seen:
                continue
            seen.add("local" if local else h)
            out.append({"driver": cls.ID, "host": "localhost" if local else h, "port": PORT,
                        "name": tr("OBS su questo computer") if local else "OBS " + h, "model": "OBS Studio",
                        "info": "WebSocket " + str(p["version"]) + (tr(" · password richiesta") if p["auth"] else "")})
        return out
