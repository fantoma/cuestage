# Plugin per videoproiettori

Un plugin è un file `.py` che definisce una classe derivata da `ProjectorDriver`.
CueStage li carica da due cartelle:

- `plugins/` dentro CueStage (plugin inclusi: PJLink è integrato, più Panasonic MZ880/MZ882 e Optoma 506);
- `~/.local/share/cuestage/plugins/` — **i tuoi plugin**: non vengono sovrascritti dagli aggiornamenti.
  Un plugin utente con lo stesso `ID` di uno incluso lo sostituisce.

Dopo aver aggiunto o modificato un file: *Impostazioni › Proiettori… › Ricarica plugin*.
I file che iniziano con `_` vengono ignorati. Gli errori di caricamento sono mostrati nello stesso dialogo.

## Esempio minimo

```python
from cuestage.projectors import ProjectorDriver, ProjectorError

class MioProiettore(ProjectorDriver):
    ID = "marca_modello"                 # univoco
    NAME = "Marca Modello"
    DEFAULT_PORT = 4352
    FIELDS = ("host", "port", "password")  # campi chiesti: host, port, user, password, proj_id
    CAPS = {"power_on", "power_off", "shutter_close", "shutter_open", "input", "zoom", "raw"}
    INPUTS = [("HDMI1", "HDMI 1"), ("HDMI2", "HDMI 2")]
    EXTRA_ACTIONS = {"test_pattern": "Mostra pattern di test"}   # azioni specifiche
    NOTES = "Testo mostrato nelle impostazioni."

    def cmd(self, text):
        s = self.connect()                  # socket TCP verso host:port (timeout 3 s)
        try:
            s.sendall((text + "\r").encode())
            resp = self.read_line(s)        # legge fino a CR
        finally:
            s.close()
        if resp.startswith("ERR"):
            raise ProjectorError(resp)      # il messaggio compare nella barra di stato
        return resp

    def power(self, on):            return self.cmd("PWR ON" if on else "PWR OFF")
    def shutter(self, closed):      return self.cmd("SHUT " + ("1" if closed else "0"))
    def select_input(self, code):   return self.cmd("INPUT " + code)
    def lens(self, axis, direction, steps=1, speed="normal"):
        # axis: zoom | focus | shift_h | shift_v ; direction: +1/-1 ; speed: slow | normal | fast
        for _ in range(steps):
            self.cmd(f"ZOOM {'+' if direction > 0 else '-'}")
    def raw(self, text):            return self.cmd(text)
    def extra(self, action, param): return self.cmd("PATTERN ON")
    def status(self):               return {"power": self.cmd("PWR?")}
```

## Azioni comuni (`CAPS`)

`power_on`, `power_off`, `shutter_close`, `shutter_open`, `input`, `freeze_on`, `freeze_off`,
`zoom`, `focus`, `shift_h`, `shift_v`, `lens_memory`, `raw` — implementate dai metodi `power`,
`shutter`, `select_input`, `freeze`, `lens`, `lens_memory`, `raw`. Le azioni non elencate in `CAPS` non
compaiono nelle cue. `EXTRA_ACTIONS` aggiunge azioni proprie del modello, eseguite da `extra(azione, valore)`.
Se un'azione extra vuole un parametro, dichiaralo in `EXTRA_PARAMS = {"id_azione": "descrizione"}`: nel
pannello «Proiettori» accanto al pulsante compare un campo di testo, il cui contenuto arriva come `valore`.
Il pannello «Proiettori» costruisce i comandi diretti da `CAPS`, `INPUTS`, `EXTRA_ACTIONS` ed
`EXTRA_PARAMS`, e «Leggi stato» mostra il dizionario restituito da `status()`.

I comandi vengono eseguiti in un thread separato per ogni proiettore (in ordine, senza bloccare
CueStage): un plugin può usare `time.sleep` e connessioni bloccanti. Ogni eccezione viene mostrata
nella barra di stato e non interrompe lo spettacolo.


## Correzione geometrica a 4 angoli

Per offrire la correzione a 4 angoli (sezione nel pannello «Proiettori» e azione di cue «Correzione 4
angoli: imposta») aggiungi `"corners"` (e se serve `"corners_off"`) a `CAPS` e definisci:

```python
CORNERS = {"tl": {"x": (0, 480), "y": (0, 300)}, "tr": {"x": (-480, 0), "y": (0, 300)},
           "bl": {"x": (0, 480), "y": (-300, 0)}, "br": {"x": (-480, 0), "y": (-300, 0)}}
CORNERS_NOTE = ""              # facoltativo: avvertenza mostrata nel pannello

def corner_set(self, corner, axis, value): ...   # corner: tl/tr/bl/br · axis: x/y · value: int nell'intervallo
def corners_enable(self, on): ...                # facoltativo: attiva/disattiva la modalità angoli
def corners_get(self): ...                       # facoltativo: {"tl": [x, y], …} per «Leggi»
```

Gli intervalli decidono anche segni e limiti degli spin del pannello; i valori sono nelle unità del
proiettore. La classe base si occupa di limitare i valori e di inviare gli 8 valori di una cue.
