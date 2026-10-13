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
