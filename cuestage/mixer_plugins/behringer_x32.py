"""Plugin CueStage — Behringer X32 e Midas M32 (OSC su UDP 10023).

Protocollo OSC del X32/M32 (lo stesso per le due famiglie):
  - ricerca: «/xinfo» in broadcast sulla porta 10023 → /xinfo ,ssss  IP, nome, modello, firmware
  - mute: «…/mix/on» intero 1 = canale acceso, 0 = muto (sui DCA «/dca/N/on»)
  - fader: «…/mix/fader» float 0…1 (sui DCA «/dca/N/fader»); legge del fader a 4 tratti:
        f ≥ 0,5: dB = 40·f − 30   ·   f ≥ 0,25: dB = 80·f − 50
        f ≥ 0,0625: dB = 160·f − 70   ·   altrimenti dB = 480·f − 90   (0 = −∞)
  - mute group: «/config/mute/N» intero 1 = mute group attivo
  - lettura: si invia l'indirizzo senza argomenti e il mixer risponde con il valore
  - nomi: «…/config/name» (es. /ch/01/config/name, /bus/03/config/name, /main/st/config/name,
    /dca/1/config/name); i mute group non hanno nome
Sezioni: 32 canali, 8 aux in, 8 ritorni FX, 16 mix bus (aux), 6 matrici, master stereo, 8 DCA, 6 mute group.
"""
from cuestage.mixers import MixerDriver, MixerError, OscLink, db_to_x32, x32_to_db, osc_decode, osc_encode, udp_discover

PATHS = {"ch": "/ch/{:02d}/mix", "auxin": "/auxin/{:02d}/mix", "fx": "/fxrtn/{:02d}/mix",
         "aux": "/bus/{:02d}/mix", "mtx": "/mtx/{:02d}/mix", "main": "/main/st/mix", "dca": "/dca/{}"}


class BehringerX32(MixerDriver):
    ID = "behringer_x32"
    NAME = "Behringer X32"
    DEFAULT_PORT = 10023
    FIELDS = ("host", "port")
    SECTIONS = [("ch", 32, {"mute", "fader"}), ("auxin", 8, {"mute", "fader"}), ("fx", 8, {"mute", "fader"}),
                ("aux", 16, {"mute", "fader"}), ("mtx", 6, {"mute", "fader"}), ("main", 1, {"mute", "fader"}),
                ("dca", 8, {"mute", "fader"}), ("mgrp", 6, {"mute"})]
    NOTES = ("OSC su UDP 10023. Nessuna impostazione sul mixer: basta che sia in rete (stessa sottorete). "
             "Aux = mix bus 1–16; Ritorni FX = FX return 1–8; Mute group 1–6.")
    MODEL_KEYS = ("X32",)
    NAMES = True

    def __init__(self, conf):
        super().__init__(conf)
        self.link = None

    def _link(self):
        if not self.host:
            raise MixerError("indirizzo IP non impostato")
        if self.link is None:
            self.link = OscLink(self.host, self.port)
        return self.link

    def _base(self, kind, n):
        if kind == "mgrp":
            return f"/config/mute/{int(n)}"
        return PATHS[kind].format(int(n))

    def set_mute(self, kind, n, muted):
        self.check(kind, n, "mute")
        if kind == "mgrp":
            self._link().send(self._base(kind, n), 1 if muted else 0)
        else:
            self._link().send(self._base(kind, n) + "/on", 0 if muted else 1)

    def get_mute(self, kind, n):
        self.check(kind, n, "mute")
        if kind == "mgrp":
            a = self._link().query(self._base(kind, n))
            return bool(a and int(a[0]))
        a = self._link().query(self._base(kind, n) + "/on")
        return not (a and int(a[0]))

    def get_name(self, kind, n):
        if kind == "mgrp":
            return ""
        base = self._base(kind, n)
        if base.endswith("/mix"):
            base = base[:-4]
        a = self._link().query(base + "/config/name")
        return str(a[0]) if a and isinstance(a[0], str) else ""

    def set_fader(self, kind, n, db):
        self.check(kind, n, "fader")
        self._link().send(self._base(kind, n) + "/fader", float(db_to_x32(db)))

    def get_fader(self, kind, n):
        self.check(kind, n, "fader")
        a = self._link().query(self._base(kind, n) + "/fader")
        if not a:
            raise MixerError("risposta vuota")
        return x32_to_db(a[0])

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
        if cls is not BehringerX32:          # la ricerca la fa una sola classe per entrambi
            return []

        def parse(data, ip):
            msg = osc_decode(data)
            if not msg or msg[0] != "/xinfo":
                return None
            a = msg[1]
            model = str(a[2]) if len(a) > 2 else ""
            drv = "midas_m32" if "M32" in model.upper() else "behringer_x32"
            host = ip
            try:                                  # l'IP dichiarato dal mixer (risposte su più interfacce)
                import ipaddress
                host = str(ipaddress.ip_address(str(a[0])))
            except (ValueError, IndexError):
                pass
            return {"driver": drv, "host": host, "port": 10023, "name": str(a[1]) if len(a) > 1 else "",
                    "model": model, "info": f"firmware {a[3]}" if len(a) > 3 else ""}
        return udp_discover(local_ips, 10023, osc_encode("/xinfo"), parse, timeout)


class MidasM32(BehringerX32):
    ID = "midas_m32"
    NAME = "Midas M32"
    NOTES = ("OSC su UDP 10023 (stesso protocollo del Behringer X32). Aux = mix bus 1–16; "
             "Ritorni FX = FX return 1–8; Mute group 1–6.")
