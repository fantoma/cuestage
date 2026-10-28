"""Plugin CueStage — Optoma ZU506T / ZH506 / ZW506 (serie 506).

Protocollo: i comandi RS232 Optoma inviati in rete via telnet (TCP 23).
  - Formato: "~" + ID proiettore (2 cifre, 00 = tutti) + comando + " " + valore + CR.
  - Risposta ai comandi: "P" (eseguito) oppure "F" (fallito); alle letture "Ok" + dati.
Sul proiettore: abilitare il controllo di rete (Rete → Controllo / Telnet) e impostare l'ID se si usa
un ID diverso da 00. Per accenderlo da standby serve la modalità standby «Attiva» (non «Eco»).

Ottica: la serie 506 ha zoom, fuoco e shift MANUALI → non sono comandabili da rete.
Geometria: keystone H/V con ~XX65 / ~XX66 (-40…40); 4 angoli con ~XX58 (sintassi da verificare,
vedi CORNER_FORMAT).
Non c'è un otturatore meccanico: «Otturatore» usa l'AV mute (immagine nera).
I codici ingresso variano tra i modelli Optoma: quelli indicati «da verificare» vanno provati sul
proiettore (in caso di risposta «F» correggere la tabella INPUTS qui sotto).
"""
from cuestage.i18n import tr
from cuestage.projectors import ProjectorDriver, ProjectorError


class OptomaZ506(ProjectorDriver):
    ID = "optoma_z506"
    NAME = tr("Optoma ZU506T / ZH506 / ZW506")
    DEFAULT_PORT = 23
    FIELDS = ("host", "port", "proj_id")
    CAPS = {"power_on", "power_off", "shutter_close", "shutter_open", "input",
            "freeze_on", "freeze_off", "raw", "corners"}
    EXTRA_ACTIONS = {"keystone_h": tr("Keystone orizzontale"), "keystone_v": tr("Keystone verticale")}
    EXTRA_PARAMS = {"keystone_h": "-40…40", "keystone_v": "-40…40"}
    # 4 angoli: comando ~XX58 con sottoindice 1..8 (alto sx x/y, alto dx x/y, basso sx x/y, basso dx x/y),
    # come nei documenti RS232 Optoma della serie ZU (es. ZU850): 0..120 in orizzontale, 0..80 in verticale,
    # misurati verso l'interno. La sintassi esatta del sottoindice non è documentata per la serie 506:
    # se il proiettore risponde «F», correggere CORNER_FORMAT (es. "{cmd} {idx} {val}" o "{cmd}{idx} {val}").
    CORNERS = {"tl": {"x": (0, 120), "y": (0, 80)}, "tr": {"x": (0, 120), "y": (0, 80)},
               "bl": {"x": (0, 120), "y": (0, 80)}, "br": {"x": (0, 120), "y": (0, 80)}}
    CORNERS_NOTE = (tr("Optoma: comando dei 4 angoli da verificare sul proiettore (valori verso l'interno, "
                    "0 = nessuna correzione). Se risponde «F» vedi CORNER_FORMAT nel plugin."))
    CORNER_INDEX = {("tl", "x"): 1, ("tl", "y"): 2, ("tr", "x"): 3, ("tr", "y"): 4,
                    ("bl", "x"): 5, ("bl", "y"): 6, ("br", "x"): 7, ("br", "y"): 8}
    CORNER_FORMAT = "{idx} {val}"     # parte dopo "~ID58 "
    INPUTS = [("1", tr("HDMI 1")), ("15", tr("HDMI 2")), ("5", tr("VGA 1")), ("6", tr("VGA 2 (da verificare)")),
              ("16", tr("HDBaseT (da verificare)")), ("10", tr("Video composito (da verificare)"))]
    NOTES = (tr("Porta 23 (telnet). ID proiettore 00 = tutti. Ottica manuale: zoom, fuoco e shift non sono "
             "comandabili. Otturatore = AV mute. Per l'accensione da rete: standby «Attivo»."))

    def _id(self):
        pid = str(self.proj_id or "00").strip()
        return pid.zfill(2)[:2] if pid.isdigit() else "00"

    def cmd(self, command, value=None, read=False):
        text = f"~{self._id()}{command}" + (f" {value}" if value is not None else "")
        s = self.connect()
        try:
            # alcuni modelli inviano subito un prompt o byte di negoziazione telnet: li scarto
            try:
                s.settimeout(0.2)
                s.recv(256)
            except OSError:
                pass
            s.settimeout(self.TIMEOUT)
            s.sendall((text + "\r").encode("ascii"))
            resp = self.read_line(s).strip()
        finally:
            s.close()
        resp = "".join(ch for ch in resp if ch.isprintable()).strip()
        if resp.upper().startswith("F"):
            raise ProjectorError(tr("{0}: rifiutato dal proiettore (F)", text))
        if read:
            if not resp.lower().startswith("ok"):
                raise ProjectorError(tr("{0}: risposta inattesa {1!r}", text, resp))
            return resp[2:]
        if not (resp.upper().startswith("P") or resp.lower().startswith("ok")):
            raise ProjectorError(tr("{0}: risposta inattesa {1!r} (è davvero un Optoma?)", text, resp))
        return resp

    def power(self, on):
        return self.cmd("00", 1 if on else 0)

    def shutter(self, closed):
        return self.cmd("02", 1 if closed else 0)

    def select_input(self, code):
        return self.cmd("12", code.strip())

    def freeze(self, on):
        return self.cmd("04", 1 if on else 0)

    def corner_set(self, corner, axis, value):
        idx = self.CORNER_INDEX[(corner, axis)]
        return self.cmd("58", self.CORNER_FORMAT.format(idx=idx, val=int(value)))

    def extra(self, action, param):
        if action in ("keystone_h", "keystone_v"):
            try:
                v = int(float(str(param).strip()))
            except ValueError:
                raise ProjectorError(tr("valore del keystone non valido (-40…40)"))
            return self.cmd("65" if action == "keystone_h" else "66", max(-40, min(40, v)))
        raise ProjectorError(tr("azione {0} sconosciuta", action))

    def raw(self, text):
        text = text.strip()
        if not text.startswith("~"):
            raise ProjectorError(tr("il comando libero deve iniziare con «~» (es. ~0000 1)"))
        s = self.connect()
        try:
            s.sendall((text + "\r").encode("ascii"))
            return self.read_line(s).strip()
        finally:
            s.close()

    def status(self):
        info = self.cmd("150", 1, read=True)      # Information: a bbbbb cc dddd e
        st = {"risposta": info}
        if info:
            st["power"] = {"1": "acceso", "0": tr("spento/standby")}.get(info[0], info[0])
        return st
