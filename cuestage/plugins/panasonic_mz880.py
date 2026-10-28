"""Plugin CueStage — Panasonic PT-MZ880 / PT-MZ882 (e serie MZ780/MZ680, MZ782/MZ682).

Protocollo: comandi di controllo Panasonic via LAN («NTCONTROL», TCP 1024).
  - Alla connessione il proiettore invia «NTCONTROL 0» (senza protezione) oppure
    «NTCONTROL 1 <casuale>» (protezione comandi attiva).
  - Con protezione: hash = MD5("utente:password:casuale") in esadecimale, anteposto al comando.
  - Comando: [hash] + "00" + comando + CR.  Risposta: "00" + dati + CR, oppure ERR1..ERR5 / ERRA.
  - Il proiettore chiude la connessione dopo la risposta o dopo 30 s: si riconnette a ogni comando.
Sul proiettore: [NETWORK] → [NETWORK CONTROL] → comandi abilitati; porta in [COMMAND PORT];
utente/password = account amministratore impostato nel proiettore.

Correzione angoli: VXX:GMMI0=+00010 sceglie [CORNER CORRECTION] (+00000 = geometria spenta);
VXX:GMFI1..4 = angoli in verticale, GMFI6..9 = in orizzontale (alto sx, alto dx, basso sx, basso dx),
valore con segno a 5 cifre (es. VXX:GMFI1=+00120); lettura con QVX:GMFIn.

Ottica motorizzata (lente standard ET-ELS20 e ottiche opzionali): shift H/V, fuoco e zoom con i comandi
VXX:LNSIn a tre velocità. Questi modelli NON hanno la lens memory.
I codici sono quelli della documentazione dei comandi Panasonic per le serie da installazione: se un
modello risponde ERR1/ERR2, verificare sul suo elenco comandi e correggere le tabelle qui sotto.
"""
from cuestage.i18n import tr
import hashlib

from cuestage.projectors import ProjectorDriver, ProjectorError


class PanasonicMZ880(ProjectorDriver):
    ID = "panasonic_mz880"
    NAME = tr("Panasonic PT-MZ880 / PT-MZ882")
    DEFAULT_PORT = 1024
    FIELDS = ("host", "port", "user", "password")
    CAPS = {"power_on", "power_off", "shutter_close", "shutter_open", "input",
            "freeze_on", "freeze_off", "zoom", "focus", "shift_h", "shift_v", "raw",
            "corners", "corners_off"}
    INPUTS = [("HD1", tr("HDMI 1")), ("HD2", tr("HDMI 2")), ("DL1", tr("DIGITAL LINK")), ("RG1", tr("COMPUTER (RGB)"))]
    EXTRA_ACTIONS = {"lens_home": tr("Ottica: posizione iniziale (home)")}
    # Correzione angoli ([GEOMETRY] → [CORNER CORRECTION]): pixel, x verso destra, y verso il basso.
    # Intervalli per i modelli WUXGA 1920×1200 (¼ della risoluzione): 480 in orizzontale, 300 in verticale.
    CORNERS = {"tl": {"x": (0, 480), "y": (0, 300)}, "tr": {"x": (-480, 0), "y": (0, 300)},
               "bl": {"x": (0, 480), "y": (-300, 0)}, "br": {"x": (-480, 0), "y": (-300, 0)}}
    CORNER_CMD = {("tl", "y"): "GMFI1", ("tr", "y"): "GMFI2", ("bl", "y"): "GMFI3", ("br", "y"): "GMFI4",
                  ("tl", "x"): "GMFI6", ("tr", "x"): "GMFI7", ("bl", "x"): "GMFI8", ("br", "x"): "GMFI9"}
    GEOMETRY_OFF, GEOMETRY_CORNER = "+00000", "+00010"
    NOTES = (tr("Porta 1024. Utente e password: account amministratore del proiettore (servono se è attiva "
             "la protezione comandi). Ottica: zoom, fuoco, shift con passi lenti/normali/veloci. "
             "Nessuna lens memory su questi modelli. Otturatore: OSH."))
    ERRORS = {"ERR1": tr("comando non definito"), "ERR2": tr("parametro fuori intervallo"),
              "ERR3": tr("proiettore occupato o funzione non disponibile ora"),
              "ERR4": tr("timeout / non disponibile"), "ERR5": tr("lunghezza dati errata"),
              "ER401": tr("il comando non può essere eseguito ora"), "ERRA": tr("password errata")}
    # VXX:LNSIn  —  2 shift orizzontale, 3 shift verticale, 4 fuoco, 5 zoom
    LENS_CH = {"shift_h": "LNSI2", "shift_v": "LNSI3", "focus": "LNSI4", "zoom": "LNSI5"}
    # valori: (positivo, negativo) per velocità
    LENS_VAL = {"slow": ("+00000", "+00001"), "normal": ("+00100", "+00101"), "fast": ("+00200", "+00201")}
    STEP_PAUSE = 0.15   # pausa tra passi ripetuti (s)

    # ---------------------------------------------------- protocollo
    def _session(self):
        s = self.connect()
        greet = self.read_line(s)
        parts = greet.split()
        if not parts or parts[0] != "NTCONTROL":
            s.close()
            raise ProjectorError(tr("saluto inatteso: {0!r}", greet))
        prefix = ""
        if len(parts) >= 3 and parts[1] == "1":
            if not self.password:
                s.close()
                raise ProjectorError(tr("protezione comandi attiva: imposta utente e password del proiettore"))
            prefix = hashlib.md5(f"{self.user}:{self.password}:{parts[2]}".encode()).hexdigest()
        return s, prefix

    def cmd(self, command):
        """Invia un comando e restituisce la risposta (senza il prefisso «00»)."""
        s, prefix = self._session()
        try:
            s.sendall((prefix + "00" + command + "\r").encode("ascii"))
            resp = self.read_line(s)
        finally:
            s.close()
        if resp.startswith("00"):
            resp = resp[2:]
        for code, text in self.ERRORS.items():
            if resp.startswith(code):
                raise ProjectorError(f"{command}: {text} ({code})")
        return resp

    # ---------------------------------------------------- azioni
    def power(self, on):
        return self.cmd("PON" if on else "POF")

    def shutter(self, closed):
        return self.cmd("OSH:1" if closed else "OSH:0")

    def select_input(self, code):
        return self.cmd(f"IIS:{code.strip().upper()}")

    def freeze(self, on):
        return self.cmd("OFZ:1" if on else "OFZ:0")

    def lens(self, axis, direction, steps=1, speed="normal"):
        import time
        ch = self.LENS_CH[axis]
        pos, neg = self.LENS_VAL.get(speed, self.LENS_VAL["normal"])
        val = pos if direction > 0 else neg
        last = ""
        for i in range(steps):
            last = self.cmd(f"VXX:{ch}={val}")
            if i + 1 < steps:
                time.sleep(self.STEP_PAUSE)
        return tr("{0} passi", steps) if steps > 1 else last

    def extra(self, action, param):
        if action == "lens_home":
            return self.cmd("VXX:LNSI1=+00001")
        raise ProjectorError(tr("azione {0} sconosciuta", action))

    def raw(self, text):
        return self.cmd(text.strip())

    # ---------------------------------------------------- correzione angoli (VXX:GMMI0 / VXX:GMFIn)
    def corners_enable(self, on):
        return self.cmd(f"VXX:GMMI0={self.GEOMETRY_CORNER if on else self.GEOMETRY_OFF}")

    def corner_set(self, corner, axis, value):
        v = int(value)
        return self.cmd(f"VXX:{self.CORNER_CMD[(corner, axis)]}={'+' if v >= 0 else '-'}{abs(v):05d}")

    def corners_get(self):
        out = {}
        for k in ("tl", "tr", "bl", "br"):
            xy = []
            for axis in ("x", "y"):
                r = self.cmd(f"QVX:{self.CORNER_CMD[(k, axis)]}")
                r = r.split("=")[-1].strip()
                try:
                    xy.append(int(float(r)))
                except ValueError:
                    raise ProjectorError(tr("risposta inattesa alla lettura degli angoli: {0!r}", r))
            out[k] = xy
        return out

    def status(self):
        st = {}
        p = self.cmd("QPW")
        st["power"] = {"000": "standby", "001": tr("acceso")}.get(p, p)
        for key, q in (("otturatore", "QSH"), ("ingresso", "QIN")):
            try:
                v = self.cmd(q)
                st[key] = {"0": "aperto", "1": "chiuso"}.get(v, v) if key == "otturatore" else v
            except ProjectorError as e:
                st[key] = f"({e})"
        return st
