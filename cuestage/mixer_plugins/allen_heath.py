"""Plugin CueStage — Allen & Heath Qu e SQ (MIDI su TCP/IP, porta 51325).

Qu (documento «Qu MIDI Protocol V1.9+»):
  - mute: Note On sul canale MIDI del mixer: 9N CH 7F 9N CH 00 = muto · 9N CH 3F 9N CH 00 = acceso
  - fader: NRPN BN 63 CH, BN 62 17, BN 06 VA, BN 26 07 — VA 00 = −∞, 62 = 0 dB, 7F = +10 dB
  - numeri CH: ingressi 1–32 = 20–3F, stereo ST1–3 = 40–42, ritorni FX 1–4 = 08–0B, mandate FX 1–4 = 00–03,
    Mix 1–10 (7 master) = 60–66, LR = 67, gruppi = 68–6B, matrici = 6C–6D, DCA 1–4 = 10–13, mute group = 50–53
  - lettura: SysEx «Get System State» (F0 00 00 1A 50 11 01 00 7F 10 00 F7): il mixer risponde e invia
    tutto il suo stato (mute e fader) come messaggi MIDI.
SQ (documento «SQ MIDI Protocol», issue 5):
  - mute: NRPN BN 63 MB, 62 LB, 06 00, 26 01/00 (muto/acceso)
  - livello: NRPN BN 63 MB, 62 LB, 06 VC, 26 VF; con la legge «lineare» (Utility › General › MIDI ›
    NRPN Fader Law) VC·128+VF = 15196 + 118,7·dB (0 = −∞, 16383 = +10 dB)
  - lettura: BN 63 MB, BN 62 LB, BN 60 7F → il mixer risponde con il valore
  - parametri mute (MB LB): ingressi 1–48 00 00–2F, gruppi 00 30–3B, ritorni FX 00 3C–43, LR 00 44,
    aux 00 45–50, mandate FX 00 51–54, matrici 00 55–57, DCA 02 00–07, mute group 04 00–07
  - livelli: ingresso→LR 40 00–2F, gruppo→LR 40 30–3B, LR 4F 00, aux 4F 01–0C, mandate FX 4F 0D–10,
    matrici 4F 11–13, DCA 4F 20–27
"""
import time

from cuestage.mixers import (MixerDriver, MixerError, NrpnParser, TcpMidiLink, interp, nrpn, tcp_scan)

AH_PORT = 51325
QU_SYSEX_HEAD = bytes([0xF0, 0x00, 0x00, 0x1A, 0x50, 0x11, 0x01, 0x00])
QU_GET_STATE = bytes([0xF0, 0x00, 0x00, 0x1A, 0x50, 0x11, 0x01, 0x00, 0x7F, 0x10, 0x00, 0xF7])

# Qu: punti della tabella del fader (VA -> dB) del documento ufficiale
QU_FADER = [(0x00, -90.0), (0x0C, -45.0), (0x10, -40.0), (0x17, -35.0), (0x1F, -30.0), (0x27, -25.0),
            (0x2F, -20.0), (0x36, -15.0), (0x3F, -10.0), (0x4F, -5.0), (0x62, 0.0), (0x72, 5.0), (0x7F, 10.0)]


def qu_va_to_db(va):
    return -90.0 if va <= 0 else interp(QU_FADER, va)


def qu_db_to_va(db):
    if db <= -89.9:
        return 0
    pts = [(d, v) for v, d in QU_FADER[1:]]
    return max(1, min(0x7F, int(round(interp(pts, max(-45.0, db))))))


def _midi_ch(conf):
    try:
        return max(1, min(16, int(conf.get("midi_ch") or 1))) - 1
    except (TypeError, ValueError):
        return 0


def _identify(host, timeout=0.8):
    """("qu"|"sq"|None, canale MIDI 0-15|None, info) per un host con la porta 51325 aperta."""
    link = TcpMidiLink(host, AH_PORT, timeout=timeout)
    found = {}
    try:
        link.connect()
        p = NrpnParser(on_sysex=lambda b: found.setdefault("sysex", b),
                       on_nrpn=lambda ch, mb, lb, vc, vf: found.setdefault("nrpn", (ch, mb, lb)))
        link.send(QU_GET_STATE)
        link.read_for(timeout, p, stop=lambda: "sysex" in found)
        sx = found.get("sysex")
        if sx and sx.startswith(QU_SYSEX_HEAD) and len(sx) > 11:
            return "qu", sx[8] & 0x0F, f"Qu (box {sx[10]:02X})"
        for ch in range(16):            # SQ: richiesta del mute LR su ogni canale MIDI
            link.send(nrpn(ch, 0x00, 0x44, 0, inc=0x7F))
        link.read_for(timeout, p, stop=lambda: "nrpn" in found)
        if "nrpn" in found:
            return "sq", found["nrpn"][0], "SQ"
        return None, None, "Allen & Heath (porta 51325)"
    except Exception:
        return None, None, ""
    finally:
        link.close()


class _AHBase(MixerDriver):
    DEFAULT_PORT = AH_PORT
    FIELDS = ("host", "port", "midi_ch", "model")

    def __init__(self, conf):
        super().__init__(conf)
        self.link = None
        self.ch = _midi_ch(conf)

    def _link(self):
        if not self.host:
            raise MixerError("indirizzo IP non impostato")
        if self.link is None:
            self.link = TcpMidiLink(self.host, self.port)
        return self.link

    def close(self):
        if self.link is not None:
            self.link.close()
            self.link = None


# ------------------------------------------------------------------ Qu

QU_BASE = {"ch": 0x20, "st": 0x40, "fx": 0x08, "fxs": 0x00, "aux": 0x60, "main": 0x67, "grp": 0x68, "mtx": 0x6C,
           "dca": 0x10, "mgrp": 0x50}


class AllenHeathQu(_AHBase):
    ID = "ah_qu"
    NAME = "Allen & Heath Qu"
    MODELS = [("qu32", "Qu-32 / Qu-Pac / Qu-SB"), ("qu24", "Qu-24"), ("qu16", "Qu-16")]
    NOTES = ("MIDI su TCP, porta 51325. Canale MIDI: quello impostato sul mixer (Setup › Control › MIDI). "
             "Aux = Mix 1–10 (7 master: 1, 2, 3, 4, 5-6, 7-8, 9-10). Mandate effetti = master FX send 1–4. Gruppi e matrici non ci sono sul Qu-16. "
             "La lettura usa «Get System State»: il mixer invia tutto il suo stato.")

    @classmethod
    def sections(cls, conf=None):
        model = (conf or {}).get("model") or "qu32"
        n_in = {"qu16": 16, "qu24": 24}.get(model, 32)
        out = [("ch", n_in, {"mute", "fader"}), ("st", 3, {"mute", "fader"}), ("fx", 4, {"mute", "fader"}),
               ("fxs", 4, {"mute", "fader"}), ("aux", 7, {"mute", "fader"}), ("main", 1, {"mute", "fader"})]
        if model != "qu16":
            out += [("grp", 4, {"mute", "fader"}), ("mtx", 2, {"mute", "fader"})]
        out += [("dca", 4, {"mute", "fader"}), ("mgrp", 4, {"mute"})]
        return out

    SECTIONS = [("ch", 32, {"mute", "fader"}), ("st", 3, {"mute", "fader"}), ("fx", 4, {"mute", "fader"}),
                ("fxs", 4, {"mute", "fader"}), ("aux", 7, {"mute", "fader"}), ("main", 1, {"mute", "fader"}),
                ("grp", 4, {"mute", "fader"}), ("mtx", 2, {"mute", "fader"}), ("dca", 4, {"mute", "fader"}),
                ("mgrp", 4, {"mute"})]

    def __init__(self, conf):
        super().__init__(conf)
        self.state = {"mute": {}, "fader": {}}
        self.synced = 0.0

    def _code(self, kind, n):
        return QU_BASE[kind] + int(n) - 1

    def set_mute(self, kind, n, muted):
        self.check(kind, n, "mute")
        c = self._code(kind, n)
        b = 0x90 | self.ch
        self._link().send([b, c, 0x7F if muted else 0x3F, b, c, 0x00])
        self.state["mute"][c] = bool(muted)

    def set_fader(self, kind, n, db):
        self.check(kind, n, "fader")
        c = self._code(kind, n)
        va = qu_db_to_va(db)
        self._link().send(nrpn(self.ch, c, 0x17, va, 0x07))
        self.state["fader"][c] = va

    def _sync(self, force=False):
        if not force and time.monotonic() - self.synced < 2.0:
            return
        st = self.state

        def note(ch, c, vel):
            if ch == self.ch and vel:
                st["mute"][c] = vel >= 0x40

        def par(ch, mb, lb, vc, vf):
            if ch == self.ch and lb == 0x17:
                st["fader"][mb] = vc

        def sysex(b):
            if b.startswith(QU_SYSEX_HEAD) and len(b) > 9 and b[9] == 0x11:
                self.ch = b[8] & 0x0F            # canale MIDI dichiarato dal mixer
        link = self._link()
        link.drain()
        link.send(QU_GET_STATE)
        link.read_for(1.5, NrpnParser(on_nrpn=par, on_note=note, on_sysex=sysex))
        self.synced = time.monotonic()

    def get_mute(self, kind, n):
        self.check(kind, n, "mute")
        self._sync()
        v = self.state["mute"].get(self._code(kind, n))
        if v is None:
            raise MixerError("stato non ricevuto dal mixer")
        return v

    def get_mutes(self, kind):
        self._sync(force=True)
        sec = self.section(kind, self.conf)
        return [self.state["mute"].get(self._code(kind, i)) for i in range(1, sec[1] + 1)]

    def get_fader(self, kind, n):
        self.check(kind, n, "fader")
        self._sync(force=True)
        va = self.state["fader"].get(self._code(kind, n))
        if va is None:
            raise MixerError("livello non ricevuto dal mixer")
        return qu_va_to_db(va)

    def status(self):
        self._sync(force=True)
        return {"canale MIDI": self.ch + 1, "mute ricevuti": len(self.state["mute"]),
                "fader ricevuti": len(self.state["fader"])}

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        out = []
        for h in tcp_scan(local_ips, AH_PORT):
            kind, ch, info = _identify(h)
            if kind == "qu" or kind is None:
                out.append({"driver": cls.ID, "host": h, "port": AH_PORT, "name": "",
                            "model": info or "Allen & Heath", "midi_ch": (ch or 0) + 1,
                            "info": "" if kind else "modello da verificare (Qu o SQ?)"})
        return out


# ------------------------------------------------------------------ SQ

SQ_MUTE = {"ch": (0x00, 0x00), "grp": (0x00, 0x30), "fx": (0x00, 0x3C), "main": (0x00, 0x44), "aux": (0x00, 0x45),
           "fxs": (0x00, 0x51), "mtx": (0x00, 0x55), "dca": (0x02, 0x00), "mgrp": (0x04, 0x00)}
SQ_LEVEL = {"ch": (0x40, 0x00), "grp": (0x40, 0x30), "main": (0x4F, 0x00), "aux": (0x4F, 0x01), "fxs": (0x4F, 0x0D),
            "mtx": (0x4F, 0x11), "dca": (0x4F, 0x20)}
# legge «audio» (approssimata dai punti del documento): valore a 14 bit -> dB
SQ_AUDIO = [(0, -90.0), (0x0800, -50.0), (0x2E40, -20.0), (0x3E00, -10.0), (0x6200, 0.0), (0x7F40, 10.0)]


def _add(base, n):
    mb, lb = base
    v = (mb << 7) + lb + int(n) - 1
    return v >> 7, v & 0x7F


class AllenHeathSQ(_AHBase):
    ID = "ah_sq"
    NAME = "Allen & Heath SQ"
    FIELDS = ("host", "port", "midi_ch", "fader_law")
    SECTIONS = [("ch", 48, {"mute", "fader"}), ("grp", 12, {"mute", "fader"}), ("fx", 8, {"mute"}),
                ("aux", 12, {"mute", "fader"}), ("fxs", 4, {"mute", "fader"}), ("mtx", 3, {"mute", "fader"}),
                ("main", 1, {"mute", "fader"}), ("dca", 8, {"mute", "fader"}), ("mgrp", 8, {"mute"})]
    NOTES = ("MIDI su TCP, porta 51325. Canale MIDI: Utility › General › MIDI. Imposta la stessa «NRPN Fader Law» "
             "del mixer (consigliata: lineare, esatta; «audio» è approssimata). Livello dei canali e dei gruppi = "
             "mandata al LR.")

    def _law(self):
        return "audio" if (self.conf.get("fader_law") or "linear") == "audio" else "linear"

    def _db_to_v(self, db):
        if db <= -89.9:
            return 0
        if self._law() == "linear":
            return max(0, min(16383, int(round(15196 + 118.7 * db))))
        return int(round(interp([(d, v) for v, d in SQ_AUDIO], db)))

    def _v_to_db(self, v):
        if v <= 0:
            return -90.0
        if self._law() == "linear":
            return max(-90.0, (v - 15196) / 118.7)
        return interp(SQ_AUDIO, v)

    def _get(self, mb, lb):
        link = self._link()
        link.drain()
        res = {}

        def par(ch, m, l, vc, vf):
            if m == mb and l == lb:
                res["v"] = (vc, vf)
        link.send(nrpn(self.ch, mb, lb, 0, inc=0x7F))
        link.read_for(1.0, NrpnParser(on_nrpn=par), stop=lambda: "v" in res)
        if "v" not in res:
            raise MixerError("nessuna risposta (canale MIDI giusto?)")
        return res["v"]

    def set_mute(self, kind, n, muted):
        self.check(kind, n, "mute")
        mb, lb = _add(SQ_MUTE[kind], n)
        self._link().send(nrpn(self.ch, mb, lb, 0, 1 if muted else 0))

    def get_mute(self, kind, n):
        self.check(kind, n, "mute")
        vc, vf = self._get(*_add(SQ_MUTE[kind], n))
        return bool(vf)

    def set_fader(self, kind, n, db):
        self.check(kind, n, "fader")
        mb, lb = _add(SQ_LEVEL[kind], n)
        v = self._db_to_v(db)
        self._link().send(nrpn(self.ch, mb, lb, v >> 7, v & 0x7F))

    def get_fader(self, kind, n):
        self.check(kind, n, "fader")
        vc, vf = self._get(*_add(SQ_LEVEL[kind], n))
        return self._v_to_db((vc << 7) + vf)

    def status(self):
        return {"canale MIDI": self.ch + 1, "LR": "muto" if self.get_mute("main", 1) else "acceso"}

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        out = []
        for h in tcp_scan(local_ips, AH_PORT):
            kind, ch, info = _identify(h)
            if kind == "sq":
                out.append({"driver": cls.ID, "host": h, "port": AH_PORT, "name": "", "model": info,
                            "midi_ch": (ch or 0) + 1, "info": ""})
        return out
