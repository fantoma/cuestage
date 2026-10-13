"""Plugin CueStage — Behringer WING (OSC su UDP 2223).

Dal documento «WING Remote Protocols» (Behringer / P.-G. Maillot):
  - ricerca: datagramma «WING?» sulla porta UDP 2222 → «WING,ip,nome,modello,seriale,firmware»
  - OSC sulla porta UDP 2223; lettura: indirizzo senza argomenti
  - fader: «/ch/N/fdr» con float in dB (−144 = −∞, fino a +10); la lettura risponde ,sff
    (testo, posizione 0…1, dB)
  - mute: «/ch/N/mute» intero 1 = muto, 0 = acceso; la lettura risponde ,sfi (testo, posizione, intero)
Le stesse foglie «mute»/«fdr» valgono per aux in (/aux), bus (/bus), main (/main), matrici (/mtx) e DCA (/dca);
i mute group hanno «/mgrp/N/mute». Nomi: «/ch/N/name» (stessa struttura per le altre sezioni). Il WING non ha canali «ritorno effetti»: gli effetti rientrano su
canali, aux o bus.
"""
from cuestage.mixers import MixerDriver, MixerError, OscLink, udp_discover

NODES = {"ch": "ch", "auxin": "aux", "aux": "bus", "main": "main", "mtx": "mtx", "dca": "dca", "mgrp": "mgrp"}


class BehringerWing(MixerDriver):
    ID = "behringer_wing"
    NAME = "Behringer WING"
    DEFAULT_PORT = 2223
    FIELDS = ("host", "port")
    SECTIONS = [("ch", 40, {"mute", "fader"}), ("auxin", 8, {"mute", "fader"}), ("aux", 16, {"mute", "fader"}),
                ("main", 4, {"mute", "fader"}), ("mtx", 8, {"mute", "fader"}), ("dca", 16, {"mute", "fader"}),
                ("mgrp", 8, {"mute"})]
    NOTES = ("OSC su UDP 2223 (ricerca su 2222). Aux = bus 1–16; Aux in = ingressi aux 1–8; Master = main 1–4. "
             "Gli effetti del WING rientrano su canali/aux: si mutano lì.")

    def __init__(self, conf):
        super().__init__(conf)
        self.link = None

    def _link(self):
        if not self.host:
            raise MixerError("indirizzo IP non impostato")
        if self.link is None:
            self.link = OscLink(self.host, self.port)
        return self.link

    def _path(self, kind, n, leaf):
        return f"/{NODES[kind]}/{int(n)}/{leaf}"

    def set_mute(self, kind, n, muted):
        self.check(kind, n, "mute")
        self._link().send(self._path(kind, n, "mute"), 1 if muted else 0)

    def get_mute(self, kind, n):
        self.check(kind, n, "mute")
        a = self._link().query(self._path(kind, n, "mute"))
        if not a:
            raise MixerError("risposta vuota")
        v = a[-1]
        if isinstance(v, str):
            return v.strip().upper() in ("1", "ON", "MUTE")
        return bool(int(round(float(v))))

    NAMES = True

    def get_name(self, kind, n):
        a = self._link().query(self._path(kind, n, "name"))
        return str(a[0]) if a and isinstance(a[0], str) else ""

    def set_fader(self, kind, n, db):
        self.check(kind, n, "fader")
        self._link().send(self._path(kind, n, "fdr"), float(max(-144.0, min(10.0, db))))

    def get_fader(self, kind, n):
        self.check(kind, n, "fader")
        a = self._link().query(self._path(kind, n, "fdr"))
        if not a:
            raise MixerError("risposta vuota")
        if len(a) >= 3:
            db = float(a[2])
        elif isinstance(a[0], str):
            t = a[0].strip()
            db = -144.0 if t.startswith("-oo") else float(t)
        else:
            db = float(a[-1])
        return max(-90.0, db) if db > -144 else -90.0

    def status(self):
        a = self._link().query("/?", match="/?")
        return {"risposta": ", ".join(str(x) for x in a)}

    def close(self):
        if self.link is not None:
            self.link.close()
            self.link = None

    @classmethod
    def discover(cls, local_ips, timeout=1.5):
        def parse(data, ip):
            t = data.decode(errors="replace").strip("\0 \r\n")
            if not t.startswith("WING,"):
                return None
            p = t.split(",")
            return {"host": p[1] if len(p) > 1 and p[1] else ip, "port": 2223, "name": p[2] if len(p) > 2 else "",
                    "model": f"WING ({p[3]})" if len(p) > 3 else "WING",
                    "info": f"firmware {p[5]}" if len(p) > 5 else ""}
        return udp_discover(local_ips, 2222, b"WING?", parse, timeout)
