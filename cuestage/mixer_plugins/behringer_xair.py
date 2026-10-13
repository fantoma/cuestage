"""Plugin CueStage — Behringer X Air: XR12, XR16, XR18 (e Midas MR18), OSC su UDP 10024.

Stesso linguaggio OSC del X32 ma con indirizzi propri (tabella «X-Air OSC» della comunità behringer.world;
il protocollo non è documentato ufficialmente):
  - canali  /ch/01…16/mix/on · /mix/fader · /config/name     (on: 1 = acceso, 0 = muto)
  - aux in  /rtn/aux/mix/…            · ritorni FX  /rtn/1…4/mix/…
  - bus     /bus/1…6/mix/…            · mandate FX  /fxsend/1…4/mix/…
  - main LR /lr/mix/…                 · DCA  /dca/1…4/on · /dca/1…4/fader
  - mute group /config/mute/1…4  (1 = attivo)
  - fader float 0…1 con la stessa legge a 4 tratti del X32; lettura: indirizzo senza argomenti
  - ricerca: «/xinfo» in broadcast sulla porta 10024 → ip, nome, modello, firmware
Numero di canali per modello (dalle specifiche; i numeri in più non rispondono, non fanno danni):
XR18/MR18 16 canali + aux in, XR16 14, XR12 10.
"""
from cuestage.mixers import MixerDriver, MixerError, OscLink, db_to_x32, x32_to_db, osc_decode, osc_encode, udp_discover


PORT = 10024
CHANNELS = {"xr18": 16, "xr16": 14, "xr12": 10}


def _model_code(text):
    t = str(text or "").upper()
    for code in ("xr12", "xr16", "xr18"):
        if code.upper() in t:
            return code
    return "xr18"                      # XR18, MR18, X18


class BehringerXAir(MixerDriver):
    ID = "behringer_xair"
    NAME = "Behringer X Air (XR12 / XR16 / XR18, Midas MR18)"
    DEFAULT_PORT = PORT
    FIELDS = ("host", "port", "model")
    MODELS = [("xr18", "XR18 / X18 / Midas MR18"), ("xr16", "XR16"), ("xr12", "XR12")]
    NAMES = True
    NOTES = ("OSC su UDP 10024, nessuna impostazione sul mixer (stessa rete). Aux = bus 1–6; Aux in = ritorno "
             "aux stereo; Ritorni effetti = FX 1–4; Mandate effetti = FX send 1–4; 4 DCA e 4 mute group. "
             "Indirizzi dalla documentazione della comunità: prova mute e fader prima dello spettacolo.")

    @classmethod
    def sections(cls, conf=None):
        n = CHANNELS.get(_model_code((conf or {}).get("model")), 16)
        return [("ch", n, {"mute", "fader"}), ("auxin", 1, {"mute", "fader"}), ("fx", 4, {"mute", "fader"}),
                ("aux", 6, {"mute", "fader"}), ("fxs", 4, {"mute", "fader"}), ("main", 1, {"mute", "fader"}),
                ("dca", 4, {"mute", "fader"}), ("mgrp", 4, {"mute"})]

    SECTIONS = [("ch", 16, {"mute", "fader"}), ("auxin", 1, {"mute", "fader"}), ("fx", 4, {"mute", "fader"}),
                ("aux", 6, {"mute", "fader"}), ("fxs", 4, {"mute", "fader"}), ("main", 1, {"mute", "fader"}),
                ("dca", 4, {"mute", "fader"}), ("mgrp", 4, {"mute"})]

    def __init__(self, conf):
        super().__init__(conf)
        self.link = None

    def _link(self):
        if not self.host:
            raise MixerError("indirizzo IP non impostato")
        if self.link is None:
            self.link = OscLink(self.host, self.port or PORT)
        return self.link

    @staticmethod
    def _node(kind, n):
        """Nodo del canale (senza «/mix»)."""
        n = int(n)
        return {"ch": f"/ch/{n:02d}", "auxin": "/rtn/aux", "fx": f"/rtn/{n}", "aux": f"/bus/{n}",
                "fxs": f"/fxsend/{n}", "main": "/lr", "dca": f"/dca/{n}"}[kind]

    def _leaf(self, kind, n, what):
        node = self._node(kind, n)
        return f"{node}/{what}" if kind == "dca" else f"{node}/mix/{what}"

    def set_mute(self, kind, n, muted):
        self.check(kind, n, "mute")
        if kind == "mgrp":
            self._link().send(f"/config/mute/{int(n)}", 1 if muted else 0)
        else:
            self._link().send(self._leaf(kind, n, "on"), 0 if muted else 1)

    def get_mute(self, kind, n):
        self.check(kind, n, "mute")
        if kind == "mgrp":
            a = self._link().query(f"/config/mute/{int(n)}")
            return bool(a and int(a[0]))
        a = self._link().query(self._leaf(kind, n, "on"))
        if not a:
            raise MixerError("risposta vuota")
        return not int(a[0])

    def set_fader(self, kind, n, db):
        self.check(kind, n, "fader")
        self._link().send(self._leaf(kind, n, "fader"), float(db_to_x32(db)))

    def get_fader(self, kind, n):
        self.check(kind, n, "fader")
        a = self._link().query(self._leaf(kind, n, "fader"))
        if not a:
            raise MixerError("risposta vuota")
        return x32_to_db(a[0])

    def get_name(self, kind, n):
        if kind == "mgrp":
            return ""
        a = self._link().query(self._node(kind, n) + "/config/name")
        return str(a[0]) if a and isinstance(a[0], str) else ""

    def status(self):
        a = self._link().query("/xinfo")
        return {"nome": a[1] if len(a) > 1 else "?", "modello": a[2] if len(a) > 2 else "?",
                "firmware": a[3] if len(a) > 3 else "?", "IP": a[0] if a else self.host}

    def close(self):
        if self.link is not None:
            self.link.close()
            self.link = None

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        def parse(data, ip):
            msg = osc_decode(data)
            if not msg or msg[0] != "/xinfo":
                return None
            a = msg[1]
            host = ip
            try:
                import ipaddress
                host = str(ipaddress.ip_address(str(a[0])))
            except (ValueError, IndexError):
                pass
            model = str(a[2]) if len(a) > 2 else ""
            return {"driver": cls.ID, "host": host, "port": PORT, "name": str(a[1]) if len(a) > 1 else "",
                    "model": _model_code(model), "info": f"{model} · firmware {a[3]}" if len(a) > 3 else model}
        return udp_discover(local_ips, PORT, osc_encode("/xinfo"), parse, timeout)
