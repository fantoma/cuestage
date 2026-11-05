"""Simulatori dei mixer per i test di CueStage."""
import os, socket, threading, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cuestage.mixers import osc_encode, osc_decode, NrpnParser

class X32Sim:
    def __init__(self, port=10023, name="FOH X32", model="X32"):
        self.state = {}; self.log = []; self.model = model; self.name = name
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(("0.0.0.0", port)); self.port = self.s.getsockname()[1]
        threading.Thread(target=self.run, daemon=True).start()
    def run(self):
        while True:
            data, src = self.s.recvfrom(4096)
            m = osc_decode(data)
            if not m: continue
            addr, args = m; self.log.append((addr, args))
            if addr == "/xinfo":
                self.s.sendto(osc_encode("/xinfo", "127.0.0.1", self.name, self.model, "4.06"), src); continue
            if args: self.state[addr] = args[0]
            else:
                default = 1 if addr.endswith("/on") else (0.75 if addr.endswith("fader") else 0)
                self.s.sendto(osc_encode(addr, self.state.get(addr, default)), src)

class WingSim:
    def __init__(self):
        self.state = {}; self.log = []
        self.d = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.d.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.d.bind(("0.0.0.0", 2222))
        self.o = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.o.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.o.bind(("0.0.0.0", 2223))
        threading.Thread(target=self.disc, daemon=True).start(); threading.Thread(target=self.run, daemon=True).start()
    def disc(self):
        while True:
            data, src = self.d.recvfrom(100)
            if data == b"WING?": self.d.sendto(b"WING,127.0.0.1,Palco WING,ngc-full,S123,3.0.6", src)
    def run(self):
        while True:
            data, src = self.o.recvfrom(4096)
            addr, args = osc_decode(data); self.log.append((addr, args))
            if addr == "/?": self.o.sendto(osc_encode("/?", "WING,127.0.0.1,Palco WING,ngc-full,S123,3.0.6"), src); continue
            if args: self.state[addr] = args[0]; continue
            if addr.endswith("/fdr"):
                db = self.state.get(addr, 0.0); self.o.sendto(osc_encode(addr, f"{db:.1f}", 0.75, float(db)), src)
            else:
                v = int(self.state.get(addr, 0)); self.o.sendto(osc_encode(addr, "ON" if v else "OFF", float(v), v), src)

class AHSim:
    """kind 'qu' o 'sq' su TCP host:51325."""
    def __init__(self, host, kind, midi_ch=0):
        self.kind, self.ch = kind, midi_ch; self.mutes = {}; self.levels = {}; self.qu_fader = {}; self.log = []
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((host, 51325)); self.srv.listen(8)
        threading.Thread(target=self.accept, daemon=True).start()
    def accept(self):
        while True:
            c, _ = self.srv.accept(); threading.Thread(target=self.serve, args=(c,), daemon=True).start()
    def serve(self, c):
        def sysex(b):
            self.log.append(("sysex", b.hex()))
            if self.kind == "qu" and b.startswith(bytes([0xF0,0,0,0x1A,0x50,0x11,1,0,0x7F,0x10])):
                out = bytes([0xF0,0,0,0x1A,0x50,0x11,1,0,self.ch,0x11,0x05,0x01,0xF7])
                for code, m in self.mutes.items(): out += bytes([0x90|self.ch, code, 0x7F if m else 0x3F, 0x90|self.ch, code, 0])
                for code, va in self.qu_fader.items():
                    b0 = 0xB0|self.ch; out += bytes([b0,0x63,code,b0,0x62,0x17,b0,0x06,va,b0,0x26,0x07])
                c.sendall(out)
        def note(ch, n, v):
            self.log.append(("note", ch, n, v))
            if self.kind == "qu" and ch == self.ch and v: self.mutes[n] = v >= 0x40
        def par(ch, mb, lb, vc, vf):
            self.log.append(("nrpn", ch, mb, lb, vc, vf))
            if ch != self.ch: return
            if self.kind == "qu" and lb == 0x17: self.qu_fader[mb] = vc
            elif self.kind == "sq": self.levels[(mb, lb)] = (vc, vf)
        p = NrpnParser(on_nrpn=par, on_note=note, on_sysex=sysex)
        # SQ: «get» (BN 60 7F) -> risposta
        buf = b""
        while True:
            try: data = c.recv(4096)
            except OSError: return
            if not data: return
            p.feed(data)
            if self.kind == "sq":
                buf += data
                i = 0
                while i + 9 <= len(buf):
                    if buf[i] & 0xF0 == 0xB0 and buf[i+1] == 0x63 and buf[i+4] == 0x62 and buf[i+7] == 0x60 and buf[i+8] == 0x7F:
                        ch = buf[i] & 0x0F; mb, lb = buf[i+2], buf[i+5]
                        if ch == self.ch:
                            vc, vf = self.levels.get((mb, lb), (0, 0))
                            b0 = 0xB0|ch; c.sendall(bytes([b0,0x63,mb,b0,0x62,lb,b0,0x06,vc,b0,0x26,vf]))
                        i += 9
                    else: i += 1
                buf = buf[-9:]

class FakeYamaha:
    """mido finto: porta di uscita verso un 01V96 simulato che risponde alle richieste sull'ingresso."""
    def __init__(self):
        self.state = {}; self.log = []; self.in_cb = None
        sim = self
        class Out:
            name = "UM-ONE:UM-ONE MIDI 1 20:0"
            def send(self, msg):
                d = list(msg.data); sim.log.append(d)
                if d[1] & 0xF0 == 0x10: sim.state[(d[5], d[7])] = d[8:12]
                elif d[1] & 0xF0 == 0x30 and sim.in_cb:
                    import mido
                    v = sim.state.get((d[5], d[7]), [0, 0, 6, 55] if d[5] in (28, 57, 43, 79) else [0, 0, 0, 1])
                    sim.in_cb(mido.Message("sysex", data=[0x43, 0x10, 0x3E, 0x0D, 0x01, d[5], d[6], d[7]] + v))
            def close(self): pass
        class In:
            def close(self): pass
        self.Out, self.In = Out, In
    def install(self, module):
        sim = self
        import mido as real
        class M:
            Message = real.Message
            @staticmethod
            def get_output_names(): return ["UM-ONE:UM-ONE MIDI 1 20:0"]
            @staticmethod
            def get_input_names(): return ["UM-ONE:UM-ONE MIDI 1 20:0"]
            @staticmethod
            def open_output(n): return sim.Out()
            @staticmethod
            def open_input(n, callback=None): sim.in_cb = callback; return sim.In()
        if isinstance(module, dict): module["mido"] = M
        else: module.mido = M


class OBSSim:
    """OBS Studio finto: server obs-websocket 5 minimo (handshake, Hello con password, richieste principali)."""
    def __init__(self, port=0, password=""):
        import base64, hashlib, json, struct
        self.b64, self.sha, self.json, self.struct = base64, hashlib, json, struct
        self.password = password
        self.log = []
        self.scenes = ["Scena A", "Scena B", "Titoli"]
        self.program, self.preview, self.studio = "Scena A", "Scena A", False
        self.transitions = [{"transitionName": "Dissolvenza", "transitionKind": "fade_transition"},
                            {"transitionName": "Taglio", "transitionKind": "cut_transition"}]
        self.transition, self.duration = "Dissolvenza", 300
        self.program_log = []                      # (scena, transizione) a ogni cambio del programma
        self.items = {"Scena A": [{"sourceName": "Camera", "sceneItemId": 1, "sceneItemEnabled": True},
                                  {"sourceName": "Logo", "sceneItemId": 2, "sceneItemEnabled": False}],
                      "Scena B": [], "Titoli": [{"sourceName": "Testo", "sceneItemId": 1, "sceneItemEnabled": True}]}
        self.inputs = [{"inputName": "Mic", "inputKind": "pulse_input_capture"},
                       {"inputName": "Video", "inputKind": "ffmpeg_source"},
                       {"inputName": "Camera", "inputKind": "v4l2_input"}]
        self.mute = {"Mic": False, "Video": False}
        self.vol = {"Mic": 0.0, "Video": -6.0}
        self.record = False
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port)); self.srv.listen(4); self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.client, args=(c,), daemon=True).start()

    def close(self):
        self.srv.close()

    def _send(self, c, obj):
        data = self.json.dumps(obj).encode()
        n = len(data)
        hdr = bytes([0x81]) + (bytes([n]) if n < 126 else (bytes([126]) + self.struct.pack(">H", n) if n < 65536
                                                           else bytes([127]) + self.struct.pack(">Q", n)))
        c.sendall(hdr + data)

    def _recv(self, f):
        b0, b1 = f.read(2)
        n = b1 & 0x7F
        if n == 126: n = self.struct.unpack(">H", f.read(2))[0]
        elif n == 127: n = self.struct.unpack(">Q", f.read(8))[0]
        mask = f.read(4); data = bytes(b ^ mask[i % 4] for i, b in enumerate(f.read(n)))
        if b0 & 0x0F == 0x8: return None
        return self.json.loads(data)

    def client(self, c):
        f = c.makefile("rb")
        try:
            head = b""
            while not head.endswith(b"\r\n\r\n"):
                head += f.read(1)
            key = [l.split(b":", 1)[1].strip() for l in head.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
            acc = self.b64.b64encode(self.sha.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()).decode()
            c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Accept: {acc}\r\n\r\n").encode())
            d = {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}
            if self.password:
                d["authentication"] = {"salt": "sale", "challenge": "sfida"}
            self._send(c, {"op": 0, "d": d})
            ident = self._recv(f)
            if ident is None: return
            if self.password:
                secret = self.b64.b64encode(self.sha.sha256((self.password + "sale").encode()).digest()).decode()
                good = self.b64.b64encode(self.sha.sha256((secret + "sfida").encode()).digest()).decode()
                if ident["d"].get("authentication") != good:
                    c.close(); return
            self._send(c, {"op": 2, "d": {"negotiatedRpcVersion": 1}})
            while True:
                m = self._recv(f)
                if m is None: return
                d = m["d"]; t = d["requestType"]; a = d.get("requestData") or {}
                self.log.append((t, a))
                ok, res = self.handle(t, a)
                self._send(c, {"op": 7, "d": {"requestType": t, "requestId": d["requestId"],
                                              "requestStatus": {"result": ok, "code": 100 if ok else 600,
                                                                "comment": None if ok else "errore"},
                                              "responseData": res}})
        except (OSError, ValueError, IndexError):
            pass
        finally:
            c.close()

    def _set_program(self, sc):
        self.program = sc; self.program_log.append((sc, self.transition))

    def handle(self, t, a):
        if t == "GetVersion": return True, {"obsVersion": "31.0.0", "obsWebSocketVersion": "5.5.0", "platformDescription": "sim"}
        if t == "GetStudioModeEnabled": return True, {"studioModeEnabled": self.studio}
        if t == "SetStudioModeEnabled": self.studio = a["studioModeEnabled"]; return True, {}
        if t == "GetSceneList":
            return True, {"currentProgramSceneName": self.program, "currentPreviewSceneName": self.preview if self.studio else None,
                          "scenes": [{"sceneName": s, "sceneIndex": i} for i, s in enumerate(reversed(self.scenes))]}
        if t == "SetCurrentProgramScene": self._set_program(a["sceneName"]); return True, {}
        if t == "SetCurrentPreviewScene":
            if not self.studio: return False, {}
            self.preview = a["sceneName"]; return True, {}
        if t == "TriggerStudioModeTransition":
            if not self.studio: return False, {}
            old = self.program; self._set_program(self.preview); self.preview = old; return True, {}
        if t == "GetSceneTransitionList":
            return True, {"currentSceneTransitionName": self.transition, "transitions": self.transitions}
        if t == "GetCurrentSceneTransition": return True, {"transitionName": self.transition, "transitionDuration": self.duration}
        if t == "SetCurrentSceneTransition": self.transition = a["transitionName"]; return True, {}
        if t == "SetCurrentSceneTransitionDuration": self.duration = a["transitionDuration"]; return True, {}
        if t == "GetSceneItemList": return True, {"sceneItems": list(reversed(self.items.get(a["sceneName"], [])))}
        if t == "GetSceneItemId":
            it = next((i for i in self.items.get(a["sceneName"], []) if i["sourceName"] == a["sourceName"]), None)
            return (True, {"sceneItemId": it["sceneItemId"]}) if it else (False, {})
        if t == "SetSceneItemEnabled":
            for i in self.items.get(a["sceneName"], []):
                if i["sceneItemId"] == a["sceneItemId"]: i["sceneItemEnabled"] = a["sceneItemEnabled"]
            return True, {}
        if t == "GetInputList": return True, {"inputs": self.inputs}
        if t == "GetInputMute":
            return (True, {"inputMuted": self.mute[a["inputName"]]}) if a["inputName"] in self.mute else (False, {})
        if t == "SetInputMute": self.mute[a["inputName"]] = a["inputMuted"]; return True, {}
        if t == "GetInputVolume": return True, {"inputVolumeDb": self.vol.get(a["inputName"], 0.0), "inputVolumeMul": 1.0}
        if t == "SetInputVolume": self.vol[a["inputName"]] = a["inputVolumeDb"]; return True, {}
        if t == "TriggerMediaInputAction": return True, {}
        if t == "GetRecordStatus": return True, {"outputActive": self.record}
        if t == "StartRecord": self.record = True; return True, {}
        if t == "StopRecord": self.record = False; return True, {}
        return False, {}
