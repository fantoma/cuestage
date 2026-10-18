# CueStage

Sistema di cue per spettacolo per Linux (Ubuntu 22.04 e successive), Windows 10/11 e macOS 12+,
sul modello di QLab / Show Cue System. Scritto in Python con Qt 6 (PySide6); l'audio usa un mixer proprio
su PortAudio (PipeWire/ALSA/JACK su Linux, WASAPI/ASIO su Windows, Core Audio su macOS), il video il backend
FFmpeg di Qt Multimedia.

## Installazione

```bash
unzip cuestage.zip && cd cuestage
./install.sh          # pacchetti apt + ambiente Python isolato + voce di menu
cuestage              # oppure dal menu applicazioni
```

Per provarlo senza installare: `sudo apt install ffmpeg libportaudio2 python3-venv`, poi
`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && ./run.sh`.

### AppImage (un solo file, niente da installare)

`CueStage-<versione>-x86_64.AppImage` contiene tutto: Python 3.12, Qt 6, numpy, PortAudio, mido/rtmidi,
ffmpeg/ffprobe e amixer. Funziona su qualsiasi distribuzione x86_64 con glibc 2.34 o successiva
(Ubuntu/Xubuntu/Kubuntu 22.04+, Linux Mint 21+, Debian 12+, Fedora 35+…) senza pacchetti aggiuntivi.

```bash
chmod +x CueStage-1.0.0-x86_64.AppImage
./CueStage-1.0.0-x86_64.AppImage [show.cueshow]
./CueStage-1.0.0-x86_64.AppImage --self-test     # verifica audio, video, MIDI, ffmpeg, interfaccia
```

- Impostazioni, plugin utente (`~/.local/share/cuestage/…`) e show sono gli stessi della versione installata:
  si può passare dall'una all'altra.
- Se il sistema non ha FUSE l'AppImage non si monta: avviala con `--appimage-extract-and-run`.
- Per la voce di menu: copia il file in `~/Applications` e usa *Gear Lever* o *AppImageLauncher*,
  oppure lascia che `install.sh` crei la voce per la versione installata.
- Le librerie JACK e `amixer` del sistema, se presenti, hanno la precedenza su quelle incluse
  (così PipeWire-JACK funziona come previsto).

Per ricostruirla (su un PC Ubuntu/Debian x86_64 con connessione a Internet, senza root):

```bash
packaging/build_appimage.sh        # -> dist/CueStage-<versione>-x86_64.AppImage
```

Lo script scarica in `build/cache` un Python portabile (python-build-standalone), le librerie Python
da PyPI, ffmpeg (BtbN, licenza LGPL), PortAudio/JACK/amixer/xcb-cursor dai pacchetti di Ubuntu 22.04 e
appimagetool; toglie da Qt le parti non usate (`packaging/prune_qt.py`: da ~750 a ~210 MB), esegue
`--self-test` e crea l'AppImage (~160 MB). Le versioni si cambiano con le variabili `PY_URL`,
`FFMPEG_URL` e `APPIMAGETOOL_URL`.

### Windows e macOS

I pacchetti si scaricano dalla pagina *Releases* del repository GitHub (vedi `GITHUB.md` per come
ottenerli): li costruisce GitHub Actions, che prova anche il programma su ciascun sistema.

- **Windows 10/11 (64 bit)**: `CueStage-<versione>-windows-setup.exe` (installatore: menu Start, icona,
  apertura dei file `.cueshow`, regola del firewall per la rete) oppure `…-windows-portable.zip`
  (si scompatta e si avvia `CueStage.exe`, anche da chiavetta). Al primo avvio Windows SmartScreen può dire
  «PC protetto»: *Ulteriori informazioni › Esegui comunque* (il programma non è firmato con un certificato
  commerciale).
- **macOS 12 o successivo**: `CueStage-<versione>-macos-arm64.dmg` per i Mac Apple Silicon (M1, M2…),
  `…-macos-x86_64.dmg` per i Mac Intel. Apri il .dmg e trascina CueStage in Applicazioni. Se non è
  notarizzato, al primo avvio: tasto destro (o ctrl-clic) su CueStage › *Apri* › *Apri*; oppure
  Impostazioni di Sistema › Privacy e sicurezza › *Apri comunque*. Alla prima richiesta di accesso alla
  **rete locale** rispondi *Consenti*: senza, mixer, proiettori, OSC e istanze secondarie non funzionano.

Differenze tra i sistemi:

| | Linux | Windows | macOS |
|---|---|---|---|
| Audio | PipeWire / ALSA / JACK | WASAPI (e ASIO se il driver lo offre) | Core Audio |
| Dati utente (plugin, profili, autosalvataggi) | `~/.local/share/cuestage` | `%APPDATA%\CueStage` | `~/Library/Application Support/CueStage` |
| Pannello schede audio (Scarlett, ALSA) | sì | no (usa Focusrite Control) | no (usa Focusrite Control) |
| Mixer, proiettori, OSC, MIDI, web, master/slave | sì | sì | sì |

Gli show si aprono su qualunque sistema: i percorsi dei file sono salvati anche relativi allo show, quindi
basta copiare la cartella dello show. Anche master e slave possono essere su sistemi diversi.
Le cue che usano una scheda ALSA di Linux, su Windows/macOS risultano senza mixer (in rosso).

Costruire i pacchetti a mano (sul sistema di destinazione):

```text
Windows:  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
macOS:    packaging/build_macos.sh
```

## Tipi di cue

| Tipo | Cosa fa |
|---|---|
| **Audio** | WAV, MP3, FLAC, OGG, AIFF, M4A… IN/OUT, loop (zona e numero di ripetizioni, 0 = infinito), volume, fade in/out automatici, curva di volume disegnata sulla forma d'onda |
| **Video** | MP4, MOV, MKV, WebM… stesse opzioni dell'audio + layer, opacità, adattamento, **crop**, **posizione** e **scala** |
| **Immagine** | PNG, JPG, … durata (0 = fino allo stop), fade in/out, layer, opacità, crop, posizione e scala |
| **Cambia preset** | Porta una cue in esecuzione verso uno dei suoi preset audio (volume) e/o crop/posizione (crop, spostamento, scala, prospettiva, opacità) nel tempo di transizione scelto. Vale solo per quell'esecuzione: la cue salvata non cambia |
| **Fade** | Porta volume e/o opacità di un'altra cue a un valore, con durata e forma (S, lineare, dB). Preset «Fade OUT + stop» e «Fade IN» (avvia il target da silenzio) |
| **Comando** | Play, Stop (con fade), Pausa, Riprendi, Stop tutto, Sposta playhead |
| **OSC** | Invia un messaggio OSC (host, porta, indirizzo, argomenti tipizzati automaticamente) |
| **Proiettore** | Comando a un videoproiettore in rete: accensione, otturatore, ingresso, freeze, ottica, lens memory, comandi specifici (driver PJLink o plugin) |
| **Attesa** | Pausa temporizzata, utile nelle catene |
| **Memo** | Promemoria per l'operatore |

## Liste di cue: MAIN e PANEL

Sopra la tabella ci sono i tab delle liste: **MAIN** (la lista principale) e i **PANEL**, liste
aggiuntive (es. musiche di servizio, effetti a chiamata, cue di emergenza).
- **+ PANEL** (o Cue › Nuovo PANEL, Ctrl+Maiusc+N) crea un PANEL; doppio clic sul tab per rinominarlo;
  tasto destro sul tab: rinomina, sposta a sinistra/destra, elimina. Ctrl+PagGiù / Ctrl+PagSu passano
  da una lista all'altra.
- Ogni lista ha il suo **playhead** (cue in standby): cliccando una cue in un PANEL non si sposta la
  MAIN. «Con la precedente» e «dopo la precedente» valgono dentro la stessa lista.
- **Spostare cue** tra le liste: seleziona le cue, poi tasto destro › Sposta in (o Cue › Sposta le
  selezionate in), anche in un nuovo PANEL.
- Le cue Fade, Cambia preset e Comando possono puntare a cue di qualsiasi lista (nei menu dei target le
  cue dei PANEL sono indicate con [nome PANEL]). I numeri nuovi sono unici in tutto lo show; «Rinumera»
  in un PANEL usa P1.1, P1.2… così non si sovrappone alla MAIN.
- Il tab con **⏵GO** è la lista su cui agisce GO; ▶ indica una lista con cue in riproduzione.
- Ogni lista ha un colore (il quadratino sul tab). Nel pannello **Cue attive** ogni cue in riproduzione
  o in attesa porta un'etichetta con il nome della sua lista, dello stesso colore; un clic
  sull'etichetta apre quella lista con la cue selezionata (il playhead non si sposta).
- STOP TUTTO, Pausa e Riprendi agiscono su tutte le cue di tutte le liste.

**GO su** (accanto al pulsante GO):
- **MAIN**: GO avvia sempre la cue in standby della MAIN, qualunque tab si stia guardando — puoi
  preparare o controllare un PANEL senza rischiare di far partire la cue sbagliata;
- **PANEL**: GO segue il tab selezionato (MAIN o PANEL).

Con «GO su: MAIN», quando si guarda un PANEL sotto i tab compare la barra **GO locale**: il pulsante
«GO <nome del PANEL>» avvia la cue in attesa di quel PANEL (accanto se ne vede il nome) e lo fa
avanzare, mentre il GO principale e la barra spaziatura restano sulla MAIN. Accanto, **«STOP <nome del
PANEL>»** ferma solo le cue di quel PANEL (anche quelle in pre-wait, con il tempo di stop delle
impostazioni); MAIN e gli altri PANEL continuano. È attivo solo quando nel PANEL c'è qualcosa in corso.
Con «GO su: PANEL» la barra non serve e non compare.

La scritta «In attesa» indica anche la lista (es. «Musiche › 4 Applauso»). La scelta è salvata nello
show. Con OSC: `/list/<nome>` mostra una lista, `/list/<nome>/go` fa GO su quella lista, `/list/<nome>/stop` ferma solo quella lista; `/next` e
`/previous` (e i corrispondenti MIDI) agiscono sulla lista di GO.

## PANEL GRID (caselle)

Un PANEL può mostrare le sue cue come **caselle** da cliccare, come un campionatore: **+ GRID** accanto a
+ PANEL, oppure tasto destro su un PANEL › «Mostra come griglia» (e «Mostra come lista» per tornare indietro:
le cue restano le stesse).

- **Dimensioni** in caselle (righe × colonne, fino a 16 × 16) e **più pagine**: premi **⚙ Opzioni** per
  vedere «+ Pagina», righe, colonne e il comportamento del clic. Doppio clic su una pagina per rinominarla;
  tasto destro per eliminarla (solo se è vuota). Se riduci la griglia, le cue che non ci stanno passano nelle
  prime caselle libere (anche su una pagina nuova): nessuna va persa.
- **Clic** su una casella: *avvia / ferma* (predefinito), *riavvia dall'inizio* oppure *avvia (se è già attiva
  non fa nulla)*. La casella attiva ha il bordo verde, la barra di avanzamento e il tempo che manca; in
  pre-wait il bordo è ambra.
- **⚙ Opzioni** aperte: clic = seleziona (ispettore) · trascina = sposta (su una casella occupata le due cue si
  scambiano) · doppio clic su una casella vuota = nuova cue · trascina file audio/video dal file manager su
  una casella = nuove cue a partire da lì.
- **Colore** di ogni casella: tasto destro › Colore (12 colori pronti o «Altro colore…»), oppure dalla scheda
  «Base» dell'ispettore. Il colore tinge anche la riga della cue nelle liste.
- **Scorciatoie da tastiera**: tasto destro › «Impara scorciatoia da tastiera…» (o «Impara…» nella scheda
  Base) e premi il tasto o la combinazione (F5, 1, Q, Ctrl+1…). Funziona per qualsiasi cue, anche nella MAIN o
  in un PANEL a lista, con la finestra di CueStage attiva ma non mentre scrivi in un campo di testo. I tasti già
  usati da CueStage (Spazio, Esc, i comandi dei menu…) non si possono assegnare; se il tasto è già di un'altra
  cue, passa alla nuova. Il tasto compare in alto a destra nella casella.
- **GO locale**: anche il GRID ha GO / STOP del PANEL; il GO avanza casella dopo casella (pagina, riga,
  colonna) e la prossima casella ha il bordo tratteggiato giallo.
- **SOLO** (sempre in vista nella barra, accanto a BLIND; arancione quando è attivo): quando parte una cue
  audio, video, immagine, testo o un gruppo, le altre attive del GRID si fermano. Le cue di controllo (OSC,
  MIDI, mixer, fade…) non fermano nulla. Viene salvato nello show.
- **Fade out** (in ⚙ Opzioni): la sfumatura usata da tutti gli stop del GRID: clic su una casella attiva,
  modalità Solo, STOP del PANEL.
- **BLIND** (sempre in vista nella barra): prova cieca. Le cue del GRID funzionano in tutto (tempi, barre,
  vumeter delle cue, GO, Solo, gruppi) ma **non mandano audio né video in uscita**; le cue di controllo (OSC,
  MIDI, mixer…) agiscono comunque. Le caselle in BLIND hanno l'etichetta rossa «BLIND». Non viene salvato nello
  show (all'apertura è sempre spento) e in ridondanza vale anche per le secondarie.
- **Gruppi**: un gruppo occupa una casella (clic = avvia il gruppo). Per vederne e modificarne le cue: tasto
  destro › «Apri il gruppo», oppure doppio clic sulla casella con le Opzioni aperte. Nel gruppo aperto ogni cue
  è una casella nell'ordine di esecuzione: doppio clic su una casella vuota (o tasto destro) = nuova cue nel
  gruppo, trascina per cambiare l'ordine, trascina dei file per aggiungerli, tasto destro › «Togli dal gruppo»;
  «◀ Torna alla griglia» per uscire. Per mettere una cue già esistente in un gruppo, trascina la sua casella
  sulla casella del gruppo (con le Opzioni aperte).

## Aggiungere cue e gruppi nelle liste

- **Doppio clic nello spazio vuoto** sotto le cue (MAIN o PANEL a lista): menu per aggiungere una cue in fondo.
  Il tasto destro ha sempre «Nuova cue» (dopo la selezione, o in fondo se si clicca nello spazio vuoto).
- **Più cue in un gruppo**: selezionale (Ctrl/Maiusc + clic) e trascinale **sopra la riga del gruppo**: entrano
  in fondo al gruppo. Rilasciate tra due cue del gruppo entrano in quel punto.

## Cue «Stop panel»

Ferma le cue in esecuzione (e i pre-wait) delle liste scelte, con un **fade** facoltativo; le altre liste
continuano. Nella scheda dell'ispettore si sceglie la **MAIN** e uno o più PANEL (anche GRID), oppure **«Tutti i
PANEL»**, che vale anche per i PANEL creati in seguito (la MAIN si aggiunge a parte). La cue non ferma sé
stessa: può stare in qualunque lista, anche in un GRID come casella «ferma tutto».

## Cue «Testo» (sottotitoli, sopratitoli, didascalie)

Il testo viene disegnato sull'uscita video, sopra (o sotto, con il **layer**) le altre cue, e segue la
correzione a 4 angoli dell'uscita.
- **Sequenza di frasi** (sottotitoli): nella casella di testo ogni frase è separata da una **riga vuota**
  (una frase può avere più righe); «_» da solo = schermo vuoto. Il GO sulla cue mostra la prima frase;
  **ogni GO successivo passa alla frase seguente** (la scritta «In attesa» mostra la prossima). Dopo
  l'ultima frase il GO successivo esce: il testo sfuma e il GO va alla cue seguente. **Alt+←/Alt+→**
  frase precedente/successiva, **ESC** esce dal testo, OSC `/text/next`, `/text/prev`, `/text/goto <n>`.
  **Importa .txt / .srt** carica le frasi da un file (dal .srt si tengono solo i testi).
- **Testo libero**: tutto il testo insieme, fino allo STOP o per la **durata** indicata.
- **Area** in % dell'uscita (x, y, larghezza, altezza) con posizioni rapide (sottotitolo in basso,
  sopratitolo in alto, centro, schermo intero); **allineamento** orizzontale e verticale; carattere,
  dimensione (px riferiti a un'uscita alta 1080), grassetto/corsivo, colore, **contorno**, **sfondo**
  (dietro il testo o tutta l'area, con opacità), margine interno, interlinea, **dissolvenza** tra le frasi.
- **Adattamento** del testo all'area: *a capo* (dimensione fissa; con «righe massime» le righe in più
  si tagliano con «…»), *a capo e riduci se non entra*, *riduci / allarga per riempire l'area*, *una riga,
  riduci per farla stare*. **Righe massime**: 0 = senza limite.
- Anteprima nell'ispettore e «Mostra in uscita da questa frase»; le modifiche allo stile valgono subito
  anche sul testo in uscita.

## Cue «MIDI out»

Invia messaggi MIDI arbitrari, uno per riga, sull'uscita MIDI scelta nella cue o su quella predefinita
(Impostazioni › MIDI › MIDI in uscita). Il controllo della sintassi è immediato e indica la riga sbagliata.

    note 1 C4 100        Note On (canale, nota o numero, velocità)   · off 1 C4   Note Off
    cc 1 7 127           Control Change          · pc 1 5         Program Change
    pb 1 0               Pitch Bend (−8192…8191) · at 1 64        Aftertouch di canale
    nrpn 1 300 8000      NRPN (parametro e valore a 14 bit)
    msc all go 12.5      MIDI Show Control (go, stop, resume, load, fire, reset, all_off…; cue e lista)
    sysex 43 10 3E …     System Exclusive        · hex 90 3C 64   byte grezzi
    wait 0.5             attesa prima del messaggio seguente      · # commento

## Gruppi di cue

In qualsiasi lista (MAIN o PANEL) le cue si possono raccogliere in **gruppi**: seleziona le cue e
**Cue › Raggruppa le selezionate** (Ctrl+Maiusc+G, anche dal tasto destro), oppure crea una cue «Gruppo» e
inserisci le cue con il gruppo selezionato, o trascinale dentro. Le cue del gruppo compaiono rientrate (↳);
un **clic sulla colonna «Tipo»** del gruppo (o Ctrl+E) lo **compatta** o lo **espande**. «Togli dal gruppo»
e «Sciogli il gruppo» fanno il contrario; spostare, duplicare ed eliminare un gruppo agisce su tutte le sue
cue (eliminando si può scegliere di tenerle).

Il tipo si sceglie nella scheda **Gruppo** dell'ispettore:
- **Sequence** — GO sul gruppo avvia la prima cue; i GO successivi le altre, in sequenza (valgono «con la
  precedente» e «dopo la precedente»). Dopo l'ultima, il GO successivo esce dal gruppo.
- **Random** — GO avvia una cue a caso (mai la stessa due volte di seguito). Il GO successivo esce.
- **Loop** — le cue si susseguono da sole all'infinito: **attesa** su ogni cue (0 = la sua durata) e
  **crossfade** verso la successiva (audio a potenza costante, immagini e video in dissolvenza). Il GO
  successivo esce dal gruppo.
- **Random loop** — come il Loop, in ordine casuale. **Avanzamento**: *a tempo* (il GO successivo esce)
  oppure *col GO*: ogni GO passa a un'altra cue a caso con il crossfade; si esce con **ESC** (un secondo ESC
  è STOP TUTTO) oppure con la cue **«Esci dal gruppo»**, creata automaticamente in coda al gruppo, che si
  può richiamare da un altro PANEL (cue Comando › Avvia), con il suo trigger MIDI/OSC o con
  `/cue/<numero>/start`.
- **All** — GO avvia tutte le cue insieme; il pre-wait di ognuna è rispettato. Il GO successivo esce.
- **Playlist** — GO avvia la prima cue; le altre partono da sole, ognuna appena finisce la precedente,
  nell'ordine della lista. Il pre-wait e «con/dopo la precedente» delle cue del gruppo non contano. Ogni cue
  suona una volta e dopo l'ultima il gruppo finisce. Facoltativo un **crossfade** tra una cue e la successiva.
  Il GO successivo esce dal gruppo; STOP lo ferma.
- **Random playlist** — come Playlist, ma in ordine casuale: ogni cue suona una volta, in un ordine diverso a
  ogni avvio (uguale su master e istanze in ridondanza).

Nelle playlist il **loop delle singole cue è ignorato** (ognuna suona una volta per giro; nell'ispettore la
casella «Loop» della cue appare disattivata). Con **Loop** attivo sul gruppo, dopo l'ultima cue la playlist
ricomincia; la Random playlist rimescola a ogni giro senza ripetere di fila la stessa cue. Il crossfade, se
c'è, non supera mai metà della durata della cue.

### Tap Tempo

Per **Sequence**, **Loop** e **Random loop** (scheda Gruppo › «Tap Tempo») le cue si susseguono a tempo,
come se a ogni battito si premesse GO: **una cue ogni 1/16, 1/8, 1/4, 1/2, 1, 2, 4, 8 o 16 battiti** al tempo
in **BPM** (20–300). Il tempo si batte, anche mentre il gruppo non suona:
- con il pulsante **TAP** dell'ispettore;
- con un **tasto** («Impara…»: anche un pedale o una pulsantiera USB che si presentano come tastiera; tenerlo
  premuto non batte il tempo);
- con una **nota/CC MIDI** («Impara»: pedale, pad, controller);
- via **OSC**: `/cue/<numero>/tap` (sempre) oppure un indirizzo scelto (es. `/tap`).

I BPM sono la media degli ultimi 8 colpi (almeno 2; dopo 2,5 s di pausa si ricomincia a contare); ogni colpo
rimette anche **in fase** il battito. Si può anche scrivere il valore a mano. Nella lista il gruppo mostra
«♩ 128 BPM · una cue ogni 1/2 battito», nel GRID il tempo compare nella casella. In ridondanza ogni colpo
arriva anche alle secondarie, che tengono lo stesso tempo.

Come si comporta (e dove non ha senso):
- **Sequence** — a ogni battito il gruppo avanza come con un GO; dopo l'ultima cue il battito si ferma (non
  avanza oltre il gruppo). Le cue con pre-wait partono in ritardo rispetto al battito; quelle «con la
  precedente» partono insieme alla loro; un GO dato a mano avanza in più e il battito continua da lì.
- **Loop / Random loop** — il battito sostituisce l'«attesa» (e, nel Random loop *col GO*, il GO: il GO
  torna a far uscire dal gruppo). Una cue più lunga dell'intervallo viene **tagliata** (con il crossfade, che
  non supera mai metà intervallo); una più corta lascia **silenzio** fino al battito.
- **Random, All, Playlist, Random playlist** — l'opzione è disattivata: Random avvia una sola cue e All tutte
  insieme (non c'è una sequenza da scandire); le playlist seguono la durata naturale delle cue (a tempo
  diventerebbero un Loop: usa Loop o Random loop).
- Sotto i 100 ms tra una cue e l'altra (es. 1/16 a tempi alti) l'ispettore avvisa; il minimo assoluto è 50 ms.
  Video e immagini a quelle velocità non stanno al passo.
- Se un tasto TAP coincide con un comando di CueStage non è accettato; se è già usato da un'altra cue passa
  alla nuova. Se la stessa nota MIDI è anche il GO generale, vince il GO.

**All'uscita** da un Loop/Random loop/Playlist: la cue corrente sfuma con il tempo di crossfade, oppure la si lascia
finire. Un gruppo in esecuzione compare tra le «Cue attive»: il suo ■ (o STOP TUTTO) lo ferma.

## Avvio di ogni cue

- **Manuale (GO)** – parte con GO (Spazio).
- **Insieme alla precedente** – parte quando parte la cue sopra, dopo il *ritardo*.
- **Alla fine della precedente** – parte quando la cue sopra termina naturalmente, dopo il *ritardo*.

Dopo un GO il playhead (e la selezione) salta alla prossima cue manuale; se nella lista c'è una sola cue resta su quella, così GO la riavvia. Un clic su una riga la mette sempre in standby. Lo stato di ogni cue (attesa, avanzamento,
tempo trascorso/rimanente) è visibile nella colonna *Stato* e nel pannello *Cue attive*, dove si possono
mettere in pausa o fermare singolarmente. Accanto alla barra di ogni cue con audio c'è un piccolo **vumeter** (una barra per le cue mono, due
per stereo e multicanale; scala −60…0 dBFS, verde / giallo sopra −9 dB / rosso sopra −1 dB): misura l'audio
della cue con livello, fade, trim dal vivo e volume master.

## Editor della forma d'onda

Trascina i marcatori **IN**/**OUT** e quelli del loop; **doppio clic** (o Ctrl+clic) aggiunge un punto alla
curva di volume, che si trascina; **tasto destro** su un punto lo elimina, altrove apre un menu
(IN/OUT/loop qui, cancella curva). Rotella = zoom, Maiusc+rotella o trascinamento = scorrimento.

## Crop, posizione e prospettiva

Scheda «Video / immagine · crop» (video e immagini). Due viste affiancate:
- **Sorgente · crop**: il fermo immagine con il riquadro di ritaglio (trascina bordi, angoli o il
  riquadro; doppio clic = azzera). Per i **video** una timeline sopra le viste (con IN/OUT) sceglie
  il fermo immagine.
- **Uscita · rendering**: il fotogramma come arriva al proiettore (crop, posizione, scala,
  prospettiva e correzione a 4 angoli, insieme alle altre cue attive). Trascina un **angolo** per la
  **deformazione prospettica**, l'interno per spostare, rotella per scalare, doppio clic su un angolo
  per azzerarlo.

Mentre la scheda è aperta, **l'uscita video mostra il fermo immagine con le impostazioni correnti**
(se la cue non è già in riproduzione), così si regola guardando il proiettore. La casella **Blind**
disattiva questo comportamento (resta salvata nello show).

Nella colonna dei campi: crop in pixel del sorgente, spostamento X/Y in pixel dell'uscita
(0,0 = centrato), scala in %, prospettiva come spostamento in pixel dei 4 angoli.
La prospettiva della cue si somma alla correzione a 4 angoli dell'intera uscita.

## Preset

I preset appartengono **alla singola cue**: ogni cue ha i suoi. Nella scheda «Tempi, loop e audio» (preset **audio**: volume, fade in, fade out, matrice e volumi delle uscite) e «Video / immagine · crop» (preset
**crop / posizione**: crop, spostamento, scala, prospettiva, opacità, adattamento) c'è la barra dei
preset della cue: **Applica**, **Salva come…**, **Aggiorna**, **Rinomina…**, **Elimina**.
Duplicando una cue se ne duplicano anche i preset.

Una cue **Cambia preset** sceglie la cue target e poi uno dei *suoi* preset (gli elenchi si
aggiornano cambiando target). Gli show salvati con la versione precedente (preset globali) vengono
convertiti all'apertura: ogni preset va alla cue che lo usava.

### Keyframe di preset

La timeline sopra le viste della scheda «Video / immagine · crop» e quella in fondo alla scheda
«Tempi, loop e audio» contengono i **keyframe**, su due corsie:
- **Immagine** (arancione): preset crop/posizione — crop, spostamento, scala, prospettiva, opacità;
- **Audio** (azzurro): preset audio — volume, **matrice** e **volumi delle uscite**.

Ogni keyframe usa un preset della cue oppure i **valori base** della cue. Tra due keyframe consecutivi
della stessa corsia i valori sfumano dall'uno all'altro: le posizioni dei keyframe sono l'inizio e la
fine delle dissolvenze (due keyframe con lo stesso preset = tenuta). Prima del primo e dopo l'ultimo
keyframe il valore resta fermo; aggiungendo il primo keyframe a un tempo > 0 ne viene creato uno
«base» a 0, così la cue parte dai suoi valori. La timeline è nel **tempo della cue** (0 = avvio, cioè
il punto IN; fine = OUT) con il loop **srotolato** nelle sue ripetizioni, come le barre di avanzamento:
un keyframe nella seconda ripetizione del loop si mette nella seconda ripetizione. Con loop infinito
sono mostrati due giri in più. A sinistra si leggono il tempo della cue e la posizione corrispondente
del file (usata per il fermo immagine e per il cursore sulla forma d'onda). Le dissolvenze della matrice sono a potenza costante: spostando un suono da un'uscita
all'altra il volume resta costante, senza buchi a metà strada.

- **Aggiungere**: doppio clic sulla corsia, oppure «+◆ Immagine» / «+◆ Audio» al tempo del cursore;
- **Modificare**: clic sul ◆ (il cursore ci salta) e scegli il preset e il tempo nella riga sotto;
  trascinalo per spostarlo; tasto destro per cambiare preset o eliminarlo;
- **Eliminare**: «Elimina» o tasto Canc.
- **Navigare**: le frecce **◀ / ▶** (o Ctrl+← / Ctrl+→ sulla timeline) saltano al marcatore precedente
  o successivo: IN, OUT, inizio/fine di ogni ripetizione del loop, fine fade in, inizio fade out, punti
  della curva e keyframe. Anche un clic su un marcatore della corsia «Marcatori» ci porta il cursore.
- **Preset in uso**: sotto la timeline una riga indica il marcatore su cui si è e, per ogni corsia, il
  preset in uso in quel punto (o la dissolvenza in corso, es. «base» → «piano» 40%); la barra dei preset
  mostra «▸ in uso: …» e seleziona quel preset, pronto per «Aggiorna».

Spostando il cursore, le viste, l'uscita video e la matrice mostrano la cue **com'è a quel tempo**;
dove vale un preset (o una dissolvenza) la modifica diretta è sospesa e un avviso lo spiega: si
modificano i valori base dove vale «base», oppure si aggiorna il preset con la barra dei preset.

Una cue **Cambia preset** sulla stessa cue prende il controllo della corsia che cambia (che da lì
smette di seguire i keyframe) partendo dal valore che ha in quel momento.

## Volume master, ultimo progetto e salvataggio automatico

- Il **volume master** (in alto, prima di «Pausa») agisce su audio e video; doppio clic = 0 dB.
- All'avvio CueStage riapre **l'ultimo progetto** usato (o quello passato da riga di comando).
- Ogni 5 secondi, se ci sono modifiche non salvate, viene scritto un **autosave nascosto**:
  `.nome.cueshow.autosave` accanto allo show, oppure
  `~/.local/share/cuestage/.senza-titolo.cueshow.autosave` per uno show mai salvato.
  Dopo un crash, alla riapertura CueStage propone di recuperarlo. L'autosave viene cancellato
  quando si salva o si chiude normalmente.

## Schede audio e matrice

Tutta la gestione dell'audio di una cue audio o video sta nella scheda **Tempi, loop e audio**
(riquadro «Uscita audio», sotto ai tempi):
- **Scheda audio**: «Predefinita del progetto» (di serie è quella scelta nelle impostazioni audio di
  Ubuntu) oppure una qualsiasi scheda collegata. Più cue possono uscire contemporaneamente da schede
  diverse; le schede usate dalle cue vengono aperte all'apertura dello show.
- **Matrice**: righe = canali del file (L/R, oppure L R C LFE Ls Rs per un 5.1…), colonne = uscite della
  scheda. Clic su un incrocio = acceso (0 dB) / spento; trascina in verticale o usa la rotella per il
  volume (Ctrl+rotella: decimi); doppio clic = valore esatto; tasto destro = valori rapidi o «solo questa
  uscita». L'ultima riga («Vol. out») regola il volume di ogni uscita. Si può modificare anche durante la
  riproduzione. «Routing predefinito»: mono → Out 1-2, stereo → Out 1/2, multicanale canale per canale,
  5.1/7.1 su stereo in downmix.
- **Video**: l'audio del video passa di serie dal motore di CueStage (scheda e matrice) e l'immagine
  si aggancia all'audio (riallineata se si scosta di più di 150 ms, anche nei loop). In alternativa
  «Lettore di sistema»: scheda predefinita, stereo, senza matrice.

In **Impostazioni** si sceglie la scheda predefinita del progetto e i **canali usati per ogni scheda**:
«auto» = tutti i canali per le schede hardware (`hw:`), 2 per i dispositivi virtuali
(`default`, `pipewire`, `pulse`…), che dichiarano sempre molti canali anche quando sono stereo.
«Rileggi le schede collegate» serve per una scheda USB inserita dopo l'avvio.
Se una scheda scelta non è collegata, la cue esce dalla predefinita e un avviso lo segnala.

**Con PipeWire (Ubuntu predefinito)**: un dispositivo `hw:` usato da PipeWire può risultare occupato.
In quel caso PortAudio lo vede senza uscite: CueStage lo mostra comunque nell'elenco, in grigio, come
«‹scheda› — occupata da ‹programma›» (di solito `pipewire` o `wireplumber`). Per usarlo direttamente
(tutti i canali, latenza minima) in pavucontrol › Configurazione metti il profilo della scheda su «Spento»,
poi «Rileggi le schede collegate»; per vedere chi lo tiene aperto: `fuser -v /dev/snd/pcmC1D0p`.
Due strade: scegliere in Impostazioni › Audio di Ubuntu il profilo **Pro Audio** della scheda e usare il
dispositivo `pipewire` impostandone i canali (es. 8); oppure chiudere le altre applicazioni audio.

### Pannello di controllo delle schede audio (Focusrite Scarlett e altre)
Le funzioni che su Windows/macOS si regolano con il programma del costruttore (es. **Focusrite Control**) su
Linux sono controlli del mixer ALSA, esposti dal driver del kernel. In **Impostazioni › Mixer e schede
audio…** la ricerca trova le schede collegate:
- **Focusrite Scarlett / Clarett** (driver «scarlett2», kernel 6.7 o successivo; Gen 2/3 con kernel tra 5.14
  e 6.6: `echo "options snd_usb_audio device_setup=1" | sudo tee /etc/modprobe.d/scarlett.conf` e riavvio):
  ingressi (Line/Inst, Pad, Air, phantom 48V, guadagno sulle Gen 4), direct monitor, volume e mute delle
  uscite (sezione **Uscite**: funziona con le cue mute e fader, anche con i fade e «Leggi dal mixer»),
  speaker switching, talkback, matrice di mix interna e routing;
- **qualsiasi altra scheda** con controlli ALSA (driver «Scheda audio (controlli ALSA)»).
Sulle Scarlett più grandi (es. 18i20) le uscite possono avere il volume **sulla manopola della scheda (HW)**:
in quel caso volume e mute di quelle uscite sono in sola lettura e le cue lo segnalano. Nel pannello
mixer il pulsante **«Volume e mute delle uscite da CueStage (SW)…»** le passa tutte a SW (oppure si cambia
il singolo «Line Out NN Volume Control»); la manopola Monitor non agisce più su di esse finché non tornano su HW.
La matrice interna è divisa per mix (Mix A, Mix B, …) e ogni riga indica la sorgente dell'ingresso del mixer
(es. «Mix A ← in 03: PCM 5»); i gruppi grandi si costruiscono quando li apri.
Nel pannello mixer (F10) la scheda mostra i suoi controlli raggruppati (Ingressi, Uscite e monitor, Mix,
Routing, Altro): interruttori, menu e cursori agiscono subito; il phantom 48V chiede conferma. Una cue
Mixer con azione **«Imposta un controllo della scheda»** porta un controllo a un valore (es. ingresso 1 su
Inst, speaker switching su Alt, direct monitor spento). Serve `amixer` (pacchetto **alsa-utils**). I nomi
dei controlli dipendono dal modello e dalla versione del kernel: CueStage li legge dalla scheda.

### Ducking (microfono che abbassa la musica)

Un ingresso audio (es. il microfono dello speaker) abbassa automaticamente le cue quando si parla.

- **Impostazioni › Ducking…** (anche col tasto destro sul tasto «Duck» o con «Impostazioni…» nell'ispettore):
  - **scheda in ingresso** e **canale**;
  - **vumeter** dell'ingresso con la soglia (linea bianca), il LED del gate e quanta riduzione è applicata;
  - **gate**: solo la **soglia** (dBFS); sopra la soglia il ducking interviene;
  - **attacco** (0–1000 ms): il tempo per arrivare alla riduzione piena;
  - **hold** (0–5000 ms, di serie 1000): quanto la riduzione resta piena dopo che il segnale è sceso sotto la
    soglia, così il volume non risale tra una parola e l'altra;
  - **rilascio** (0–5000 ms): il tempo per tornare al volume pieno, dopo l'hold;
  - **Master ducking**: riduzione dell'uscita master (di serie 0 dB = nessuna);
  - **«Manda l'ingresso, dopo il gate, al preascolto»**: in cuffia si sente il microfono solo quando supera la
    soglia (e durante l'hold), con scheda, canali e volume del preascolto; utile per regolare la soglia. Il
    ritardo è di qualche decina di millisecondi.
  Le modifiche valgono subito (si vede e si sente l'effetto); «Annulla» rimette i valori di prima. Con la
  finestra aperta l'ingresso è in ascolto anche se il ducking è spento, per regolare la soglia.
- **Ducking della cue**: nell'ispettore delle cue audio e video (sezione «Uscita audio»), i dB di cui si
  abbassa la cue quando il ducking interviene («nessuno» = 0). Su un **gruppo** vale per tutte le sue cue che
  non hanno un ducking proprio. Funziona solo con «Attiva il ducking delle cue» acceso.
- **Tasto «Duck»** accanto al volume master: accende il Master ducking (abbassa tutta l'uscita, audio e audio
  dei video, della riduzione impostata). Grigio = spento, verde = acceso e in ascolto, rosso = sta
  intervenendo, ambra = acceso ma senza effetto (riduzione a 0 dB o ingresso non disponibile).
- **Icona del microfono** nella barra di avanzamento (lista, cue attive, caselle del GRID) delle cue con il
  ducking: **verde** = pronto, **rossa** = sta abbassando la cue, grigia = ducking spento nelle impostazioni.
- Cue e master si sommano: una cue con 10 dB di ducking e il Master ducking a 6 dB scende di 16 dB.
- **Fader del mixer agganciati al ducking**: cue Mixer, azione «Ducking: aggancia i fader alla curva del
  ducking» (mixer digitali e schede audio con fader). Si scelgono sezione e canali, il **livello normale** e la
  **riduzione** in dB: finché la cue è attiva i fader stanno al livello normale e scendono della riduzione quando
  il ducking interviene, seguendo attacco, hold e rilascio (al massimo 25 aggiornamenti al secondo). STOP
  sgancia i fader e li riporta al livello normale. Basta che l'ingresso del ducking sia configurato: non serve
  «Attiva il ducking delle cue».
- In ridondanza ogni istanza ascolta il proprio ingresso.

### Preascolto (cuffia)

- **Impostazioni › Audio, OSC e MIDI › Preascolto**: «Attiva il preascolto», la **scheda** e i **2 canali**
  (sinistro e destro) della cuffia del tecnico. Può essere un'altra scheda o due canali liberi della stessa
  («Scheda predefinita del progetto» = quella scelta in alto come scheda predefinita).
- **Ispettore** delle cue audio e video (sezione «Uscita audio») e dei **gruppi**: «Preascolto». La cue non va
  più alle uscite dello spettacolo ma solo alla cuffia, in stereo (mono su entrambi i lati; più canali
  alternati sinistra/destra). Su un gruppo vale per tutte le sue cue. Si può accendere e spegnere anche mentre
  la cue suona.
- Accanto al volume master compare il **volume del preascolto** (doppio clic = 0 dB): il volume master e il
  Master ducking non toccano il preascolto.
- Le cue in preascolto hanno l'etichetta blu **PFL** nella barra di avanzamento.
- Il video col «Lettore di sistema» non può andare alla cuffia: in preascolto il suo audio tace.

### Buffer audio e «underrun»
Un *underrun* è un piccolo buco nell'audio: la scheda ha chiesto il blocco successivo prima che il computer
lo avesse pronto (succede soprattutto con video in uscita o con il computer impegnato). In
**Impostazioni › Audio › Buffer audio** si sceglie la dimensione: *Bassa latenza* 512 campioni (~50 ms),
*Normale* 1024 (~120 ms, predefinita), *Sicura* 2048 (~250 ms), *Massima sicurezza* 4096 (~500 ms).
Un buffer più grande elimina i buchi; in cambio una cue si sente qualche decimo di secondo dopo il GO.
Se negli ultimi 10 secondi ci sono stati buchi, la barra di stato lo segnala in giallo con il numero.
I messaggi «ALSA lib … underrun occurred» non vengono più scritti nel terminale (li conta CueStage); per
rivederli: `CUESTAGE_ALSA_MESSAGES=1 cuestage`. Con PipeWire conviene usare la scheda «pipewire» o
«default» invece dell'accesso diretto (hw:), che è più sensibile ai ritardi.

## Marcatori

Tutti i marcatori di una cue sono visibili ovunque, con gli stessi colori:
IN (verde), OUT (rosso), loop (giallo, con le ripetizioni), fade in/out (rampe e linee tratteggiate),
punti della curva di volume (rosa), keyframe immagine (◆ arancione) e audio (◆ azzurro; vuoto = «base»).

- **Forma d'onda** (scheda «Tempi, loop e audio»): IN/OUT, loop, fade, curva e anche i keyframe, posti
  sulla posizione del file (un keyframe che cade in una ripetizione del loop è indicato come tale).
- **Timeline dei keyframe** (schede «Tempi, loop e audio» e crop), nel tempo della cue: corsia
  «Marcatori» con IN/OUT, loop con le ripetizioni, fade e punti della curva, sopra le corsie dei
  keyframe; frecce ◀ / ▶ per passare da un marcatore all'altro. Il cursore della timeline compare
  anche sulla forma d'onda come linea tratteggiata.
- **Barre di avanzamento** durante il play (colonna «Stato» e pannello «Cue attive»): tempo reale della
  cue con il loop «srotolato» nelle sue ripetizioni, rampe dei fade, curva e keyframe; con loop infinito
  il cursore gira dentro la regione di loop. Passando il mouse su un marcatore se ne vede la descrizione.

## Videoproiettori in rete

**Impostazioni › Proiettori…**: elenco dei proiettori con nome, driver, IP, porta e credenziali;
«Prova connessione» legge lo stato (accensione, otturatore, ingresso). Le cue di tipo **Proiettore**
scelgono un proiettore e un'azione; i comandi partono in un thread per proiettore (in ordine, senza
bloccare lo show) e l'esito compare nella barra di stato. «Invia ora» li prova dall'ispettore.

### Pannello «Proiettori» (controllo diretto)

Se c'è almeno un proiettore configurato, in alto, accanto al volume master, compare il pulsante
**📽 Proiettori ▾**: apre (e richiude) un pannello a scomparsa con un riquadro per ogni proiettore, con
**tutti i comandi che il suo driver/plugin mette a disposizione**:
- accendi / spegni (lo spegnimento chiede conferma), chiudi / apri otturatore, freeze, ingresso;
- ottica: zoom, fuoco, shift orizzontale e verticale con velocità e passi; **tenendo premuto** un
  pulsante il movimento continua (le ripetizioni non si accumulano se il proiettore è lento a rispondere);
- lens memory, azioni specifiche del modello (es. «Ottica: posizione iniziale» sul Panasonic),
  comando libero, **Leggi stato**;
- con più proiettori, la riga **Tutti**: chiudi / apri tutti gli otturatori, leggi tutti gli stati.

Sotto ogni riquadro compare l'esito dell'ultimo comando (verde = riuscito, rosso = errore). Anche con il
pannello in primo piano restano attivi Spazio = GO, Esc = STOP TUTTO, F7 e F8; si chiude con ✕ o
ripremendo il pulsante. Il pannello si apre e chiude anche con **F9** o da **Impostazioni › Pannello
proiettori** (senza proiettori configurati apre la loro configurazione).

### Correzione geometrica a 4 angoli

Per i proiettori il cui plugin la supporta, nel pannello «Proiettori» compare la sezione
**▸ Correzione 4 angoli**: per ogni angolo (alto sx, alto dx, basso sx, basso dx) uno spostamento X e Y
nelle unità del proiettore. Ogni modifica (frecce, rotella, digitazione) viene inviata **subito**, in
tempo reale: tenendo premuta una freccia il proiettore segue il valore; se è ancora occupato col comando
precedente riceve direttamente l'ultimo valore (nessuna coda, nessun ritardo che si accumula). Il primo
invio attiva la modalità «correzione angoli» del proiettore; **Leggi** carica i valori attuali, **Azzera** riporta
tutto a 0, **Invia tutti** rimanda gli 8 valori, **Disattiva** spegne la correzione.
«Valori per cue» contiene gli 8 numeri da incollare in una cue Proiettore con l'azione **Correzione 4
angoli: imposta**, che li richiama durante lo show (es. per passare da una scenografia all'altra).

- **Panasonic PT-MZ880/MZ882**: `VXX:GMMI0=+00010` (geometria = CORNER CORRECTION), `VXX:GMFI1…4`
  verticali e `GMFI6…9` orizzontali; pixel, 0…480 in orizzontale e 0…300 in verticale verso l'interno
  (valori per i modelli WUXGA). Lettura con `QVX:GMFIn`.
- **Optoma ZU506T**: keystone orizzontale e verticale (`~XX65` / `~XX66`, −40…40) come azioni del
  modello; 4 angoli con `~XX58` + indice 1…8, 0…120 / 0…80 verso l'interno. ⚠ La sintassi dei 4 angoli
  non è documentata per la serie 506 (viene dai documenti di altri Optoma serie ZU): se il proiettore
  risponde «F» va corretta la riga `CORNER_FORMAT` del plugin.

Azioni comuni: accendi / spegni, otturatore chiudi / apri, ingresso, freeze, zoom, fuoco, shift
orizzontale e verticale (direzione, passi, velocità), lens memory, comando libero, più le azioni
specifiche di ogni driver. Ogni driver mostra solo le azioni che il proiettore supporta.

| Driver | Protocollo | Supporta |
|---|---|---|
| **PJLink** (integrato, qualsiasi marca) | TCP 4352, password MD5 | accensione, ingresso (codici PJLink 11…59), otturatore = AV mute, comando libero |
| **Panasonic PT-MZ880 / MZ882** (plugin) | TCP 1024, NTCONTROL con hash MD5 utente:password | accensione, otturatore (OSH), ingressi HDMI 1/2, DIGITAL LINK, COMPUTER, freeze, zoom/fuoco/shift a 3 velocità, ottica in posizione iniziale. Nessuna lens memory su questi modelli |
| **Optoma ZU506T / ZH506 / ZW506** (plugin) | telnet TCP 23, comandi `~ID…` | accensione, otturatore = AV mute, ingressi, freeze. Ottica **manuale**: zoom, fuoco e shift non sono comandabili |

**Plugin**: un file `.py` con una classe derivata da `ProjectorDriver` in
`~/.local/share/cuestage/plugins/` aggiunge un driver (guida ed esempio in
`cuestage/plugins/LEGGIMI_PLUGIN.md`); «Ricarica plugin» lo carica senza riavviare. I plugin
difettosi vengono segnalati e ignorati.

**Da verificare sui proiettori reali**: i driver sono scritti dalla documentazione dei protocolli e
collaudati con simulatori. Alcuni codici sono marcati «da verificare» (ingressi Optoma oltre HDMI 1/2 e
VGA 1; mappatura degli assi ottici Panasonic LNSI2…5): se il proiettore risponde con un errore,
correggere la tabella nel file del plugin. Per comandare il proiettore da spento va attivato lo
standby di rete nel suo menu.

## Uscita video e correzione a 4 angoli

Menu **Uscita video**: scelta dello schermo (di default l'ultimo, cioè il proiettore), schermo intero,
**Geometria a 4 angoli** (Ctrl+G): trascina gli angoli nell'editor o direttamente sulla finestra di
uscita, con griglia di test, valori numerici e nudge da tastiera (1–4 = angolo, frecce, Maiusc ×10).
La correzione è una trasformazione prospettica applicata a tutti i layer, salvata nello show.

## Mixer digitali

**Impostazioni › Mixer…**: elenco dei mixer, con **🔍 Cerca mixer in rete**, che trova da solo:
- **Behringer X32 / Midas M32** — OSC su UDP 10023 (risposta a «/xinfo»: nome, modello, firmware);
- **Behringer WING** — «WING?» su UDP 2222, comandi OSC su 2223;
- **Behringer X Air XR12 / XR16 / XR18, Midas MR18** — «/xinfo» su UDP 10024 (anche il modello);
- **Soundcraft Ui12 / Ui16** — scansione della porta 80 e riconoscimento della pagina web del mixer;
- **Allen & Heath Qu e SQ** — MIDI su TCP 51325 (scansione della rete /24 e riconoscimento del modello
  e del canale MIDI).
Un mixer trovato si aggiunge con doppio clic. «Prova connessione» verifica il collegamento.

| Mixer | Collegamento | Mute | Fader |
|---|---|---|---|
| X32 / M32 | OSC UDP 10023 | 32 canali, 8 aux in, 8 ritorni FX, 16 mix bus (aux), 6 matrici, master, 8 DCA, 6 mute group | tutti tranne i mute group |
| WING | OSC UDP 2223 | 40 canali, 8 aux in, 16 bus, 4 main, 8 matrici, 16 DCA, 8 mute group | tutti tranne i mute group |
| X Air XR18 / MR18, XR16, XR12 | OSC UDP 10024 | 16 / 14 / 10 canali, aux in, 4 ritorni FX, 6 bus, 4 mandate FX, LR, 4 DCA, 4 mute group | tutti tranne i mute group |
| Soundcraft Ui16 / Ui12 | WebSocket porta 80 | 12 / 8 ingressi, line in 1–2, player USB 1–2, FX 1–4, 4 / 2 aux, master (= MUTE ALL), 6 mute group | tutti tranne i mute group |
| Qu-16/24/32, Pac, SB | MIDI TCP 51325 | ingressi, ST 1–3, ritorni e mandate FX, 7 mix, LR, gruppi e matrici (non Qu-16), 4 DCA, 4 mute group | ingressi, ST, ritorni FX, mandate FX, mix, LR, gruppi, matrici, DCA |
| SQ-5/6/7 | MIDI TCP 51325 | 48 ingressi, 12 gruppi, 8 ritorni FX, 12 aux, 4 mandate FX, 3 matrici, LR, 8 DCA, 8 mute group | ingressi e gruppi (mandata al LR), aux, mandate FX, matrici, LR, DCA |
| Yamaha 01V96 (i, VCM) | MIDI (SysEx Parameter Change) | 32 canali, 8 aux, 8 bus, stereo | gli stessi |
| Yamaha 02R96 | MIDI, **da verificare** | 56 canali, 8 aux, 8 bus, stereo | gli stessi |
| Yamaha 01V (originale) | MIDI Control Change (tabella iniziale) | 12 canali, 2 stereo (13-14, 15-16), 2 return, 4 aux, 4 bus, 2 effetti, ST OUT | gli stessi |
| Yamaha 02R (originale) | MIDI Control Change (mappa «02R») | 16 MIC, 4 LINE stereo, 2 EFF, ST MASTER | gli stessi + aux 1–8 e bus 1–8 (solo fader) |

Note: **X Air** e **Soundcraft Ui** seguono protocolli non documentati ufficialmente (tabella OSC della
comunità per X Air; libreria open source «soundcraft-ui-connection» per Ui): il numero di canali per modello
è preso dalle specifiche ed è da verificare, come i comandi, prima dello spettacolo.
Sul **Qu** e sull'**SQ** va indicato il canale MIDI del mixer (la ricerca lo rileva); sull'SQ la
«NRPN Fader Law» deve corrispondere a quella del mixer (consigliata *lineare*, conversione esatta). Gli
**Yamaha** si collegano con un'interfaccia MIDI: «Uscita MIDI → mixer» per comandare e «Ingresso MIDI ←
mixer» per leggere i livelli; sul mixer «Parameter Change» Rx (e Tx) ON. Per il 02R96 i messaggi sono
quelli della famiglia 01V96 ma non sono stati verificati su un 02R96: provali prima dello spettacolo.
I vecchi **01V** e **02R** si comandano con i Control Change della tabella di fabbrica (Control Change Rx ON sul
canale MIDI indicato): se sul mixer la tabella è stata cambiata, va corretta nel plugin `yamaha_vintage.py`.
Il 01V trasmette i Control Change quando si muove un fader (Tx ON): «Leggi dal mixer» usa l'ultimo valore
ricevuto, quindi prima si muove il fader sul mixer. Il 02R non trasmette: il livello si imposta nella cue.
Su questi modelli i dB sono approssimati (scala a 128 passi).

### Pannello «Mixer» (mute)
Con almeno un mixer configurato compare in alto il pulsante **≣ Mixer ▾** (o F10): un pannello con una scheda
per mixer e, per ogni sezione, i pulsanti dei mute (verde = acceso, rosso = muto, grigio = stato non ancora
letto). Un clic manda il mute; **Leggi** (per sezione) e **Leggi tutto** leggono lo stato dal mixer.

### Cue «Mixer»
- **Mute** (a scena): tutte le sezioni del mixer (canali, aux, gruppi, effetti, matrici, master, DCA,
  mute group) con una casella per elemento: **rosso** = forza il mute, **verde** = forza acceso,
  **grigio** = non toccare. Clic: grigio → rosso → verde → grigio; clic destro: grigio; Maiusc+clic:
  stesso stato su un intervallo. Per sezione: «tutti mute», «tutti on», «non toccare», **⟲ Leggi**
  (copia lo stato attuale del mixer); **⟲ Leggi tutto dal mixer** registra l'intera scena. Le cue
  delle versioni precedenti (mute/riattiva su sezione + canali) vengono convertite all'apertura;
- **Fader**: canali scelti con i pulsanti numerati della sezione (clic, Maiusc+clic, «Tutti» / «Nessuno»):
  porta un fader (canale, aux, gruppo, matrice, master, DCA…) a un livello, con **fade**
  facoltativo (25 passi al secondo, curva a S; legge prima la posizione attuale). **⟲ Leggi dal mixer**
  registra nella cue il livello presente in quel momento sul mixer: si regola il fader sul mixer, si
  preme Leggi, e la cue riporterà quel livello. STOP ferma un fade in corso.
**Nomi dei canali**: con i mixer che li forniscono (Behringer X32 / Midas M32, WING, X Air, Soundcraft Ui) i nomi vengono letti
automaticamente alla prima apertura del pannello mixer o di una cue mixer, e compaiono sotto il numero
nei pulsanti del pannello, nelle caselle della cue e nella lista (cue fader su un solo canale). Sono
salvati nello show, quindi restano visibili anche a mixer spento; si rileggono con «⟲ Nomi canali» nella
cue o con «Leggi tutto» nel pannello. Allen & Heath Qu/SQ e Yamaha via MIDI non forniscono i nomi.
I comandi di ogni mixer passano da una coda dedicata (in ordine, senza bloccare lo show); l'esito compare
nella barra di stato. Nuovi mixer si aggiungono con un plugin (vedi `cuestage/mixer_plugins/LEGGIMI.md`).

## Istanze secondarie: master, slave e ridondanza

**Sul master** (Impostazioni › Audio, OSC e MIDI › **Istanze**): «Abilita istanze secondarie», porta TCP
(predefinita 53900; la porta successiva, UDP, serve alla ricerca) e nome dell'istanza. Ogni richiesta di
collegamento va **accettata** in una finestra di conferma.

**Sull'altro computer**: Impostazioni › **Diventa slave…**
- ricerca dei master in rete (o indirizzo e porta scritti a mano);
- modalità **Slave** (copia del progetto sempre aggiornata, non esegue le cue del master) oppure
  **Ridondanza** (esegue esattamente le stesse cue comandate dal master);
- contenuti **differenziale** (scarica i file mancanti e aggiorna quelli diversi, confrontati con
  dimensione e impronta SHA-1 di inizio e fine file) o **totale** (riscarica tutto);
- **copia tutte le impostazioni del progetto** (schede audio, mixer, proiettori, MIDI, OSC…; restano
  locali solo le impostazioni delle istanze e dell'interfaccia web);
- cartella locale: lì vanno i file (stessa struttura di cartelle del master; i file esterni alla cartella
  dello show in `_esterni/`) e la copia dello show, che resta utilizzabile anche senza master.

Dopo la sincronizzazione CueStage **segnala l'hardware diverso** da quello del master (schede audio assenti o
con meno canali, mixer configurati diversamente o schede ALSA non collegate, uscite MIDI mancanti) e
colora di **rosso** le cue che lo usano (il motivo è nel suggerimento della riga).

Ogni **modifica del progetto** sul master arriva subito alle secondarie (controllo ogni 200 ms), con i nuovi
file scaricati in automatico. In **ridondanza** il master ritrasmette ogni comando: GO (anche locali e dei
PANEL), avvio/stop/pausa delle singole cue, STOP TUTTO, playhead, crossfade e crossfader, livelli e
opacità dal vivo, testi e uscita dai gruppi; i gruppi casuali usano lo stesso seme, quindi scelgono le
stesse cue. Sulla ridondanza i comandi locali sono bloccati (resta lo STOP TUTTO di emergenza) e l'editor
è in sola lettura. I comandi verso mixer e proiettori in rete partono da entrambe le istanze (sono
idempotenti: stesso mute, stesso livello).

**Le secondarie possono aggiornare il progetto** (opzione sul master, Impostazioni › Istanze): con
l'opzione attiva l'editor delle secondarie è sbloccato e ogni loro modifica (cue, liste, impostazioni se
copiate) arriva al master entro circa mezzo secondo e da lì a tutte le altre secondarie. I file nuovi
aggiunti su una secondaria vengono inviati al master e salvati nella cartella `da_slave` accanto allo show
(mai sovrascrivendo file esistenti). Sul master lo show risulta «modificato» finché non lo salvi. Se due
istanze modificano nello stesso momento vale l'ultima modifica arrivata. Senza l'opzione l'editor delle
secondarie resta bloccato; nell'indicatore in basso «✎» segnala che la modifica è consentita.

**Heartbeat**: il master ne invia uno ogni 100 ms. Se una secondaria non lo sente per il tempo scelto
(predefinito 500 ms) o la connessione cade, la barra di stato diventa rossa: un clic (o Impostazioni ›
«Esci dalla modalità slave») la **promuove**: diventa indipendente, le cue in corso continuano, e se ha
le istanze secondarie abilitate diventa a sua volta master. Con «promuovi automaticamente» lo fa da sola.
In basso a destra l'indicatore mostra «◉ MASTER · n secondarie» oppure «◎ RIDONDANZA di …».
Nota: il collegamento non è cifrato e la sola protezione è la conferma sul master: usalo sulla rete dello
spettacolo.

## Interfaccia web

**Impostazioni › Audio, OSC e MIDI… › scheda «Interfaccia web»**: attiva un piccolo server web sulla
porta scelta (di serie 8080). Nella stessa scheda c'è il **QR code** da inquadrare con la fotocamera del
telefono: apre direttamente l'indirizzo IP del computer (se il computer ha più reti, scegli l'indirizzo
nella tendina). Con «Includi il PIN nel QR code» chi lo inquadra entra senza digitare il PIN. Il QR è
generato da CueStage stesso (nessuna libreria aggiuntiva). La pagina mostra:
- **GO** (rispetta «GO su»), cue in attesa con la sua lista, **STOP**, **Pausa / Riprendi**;
- i tab di MAIN e PANEL; toccando una cue la si mette in attesa nella sua lista; nei PANEL, con
  «GO su: MAIN», c'è il **GO locale** del PANEL con il suo **STOP**;
- le **cue attive** con lista, avanzamento, tempo trascorso/rimanente, pausa e stop per cue.
- il tab **📽 Proiettori** (se ci sono proiettori configurati e l'opzione «Mostra il controllo dei
  proiettori» è attiva): per ogni proiettore gli stessi comandi del pannello «Proiettori» — accensione
  (lo spegnimento chiede conferma), otturatore, freeze, ingresso, ottica (tenendo premuto il movimento
  continua, senza accumulare comandi), lens memory, azioni del modello, comando libero, stato — e la
  **correzione 4 angoli** in tempo reale (− / + tenuti premuti, o il valore digitato; Leggi, Azzera,
  Invia tutti, Disattiva).

Si aggiorna 3-4 volte al secondo. Con un **PIN** impostato il browser lo chiede la prima volta e lo
ricorda. Senza PIN chiunque sulla rete può comandare lo show: su reti condivise impostalo. Il server
non usa HTTPS: è pensato per la rete locale dello spettacolo, non per Internet.

## Dispositivi OSC da profilo (es. ChamSys MagicQ)

In **Impostazioni › OSC › Dispositivi OSC** si aggiungono dispositivi a partire da un **profilo**: si sceglie
il profilo, poi si scrive l'indirizzo IP oppure si usa **🔍 Cerca in rete** (per la MagicQ la ricerca usa
Art-Net/ArtPoll e mette in cima le console riconosciute come MagicQ/ChamSys); la porta OSC è già quella del
profilo. In una cue **OSC** si sceglie il dispositivo e il comando, con i suoi parametri; sotto si vede il
messaggio che partirà. «Manuale» resta disponibile per scrivere indirizzo e argomenti a mano.

Profilo **ChamSys MagicQ** (porta 8000; sulla console: Setup › View Settings › Network › OSC, console
sbloccata; non su MQ40/MQ40N):
| Comando | Messaggio |
|---|---|
| GO di un playback | `/pb/N/go 1` |
| GOTO CUE di un playback | `/pb/N/CUE 1` (es. `/pb/2/12.5`) |
| Exec (attiva / rilascia) | `/exec/PAGINA/ELEMENTO 1` · `0` |
| Exec: livello del fader | `/exec/PAGINA/ELEMENTO 0…100` |
| DBO | `/dbo 0` attiva il blackout · `/dbo 1` lo disattiva |
| Tasto FLASH di un playback | `/pb/N/flash 1` premuto · `0` rilasciato |
| Stato SWAP | `/swap 1` swap · `0` add (nella MagicQ è globale, vale per tutti i flash) |
| Livello, PAUSE, RELEASE di un playback | `/pb/N 0…100`, `/pb/N/pause 1`, `/pb/N/release 1` |
| Comando remoto | `/rpc "…"` |
Via OSC la MagicQ accetta i playback 1–10. Nuovi profili: file JSON in `~/.local/share/cuestage/osc_profiles/`
(stesso formato di `cuestage/osc_profiles/chamsys_magicq.json`: indirizzi e argomenti con i parametri tra
graffe, parametri int/float/enum/text, ricerca facoltativa `{"type": "artnet", "match": [...]}`).

## OSC e MIDI

OSC in ricezione su UDP **53000** (configurabile): `/go`, `/stop`, `/panic`, `/pause`, `/resume`,
`/next`, `/previous` (sulla lista di GO), `/playhead/<n>`, `/list/<nome>`, `/list/<nome>/go`, `/cue/<n>/start|stop|pause|resume`, `/cue/<n>/level <dB> [sec]`,
più un indirizzo personalizzato per ogni cue. Elenco completo in *Aiuto → Comandi*.

MIDI in ingresso (Impostazioni): mappa GO / Stop / Pausa / Riprendi / Avanti / Indietro e un trigger per
ogni cue, nel formato `note 1 60`, `cc 1 20`, `pc 1 5` (canale 0 = qualsiasi), con pulsante **Impara**
(nelle impostazioni ascolta la porta scelta nel dialogo anche prima di premere OK).
La mappa comprende anche **Crossfade alla successiva**.

### Più schede MIDI

In Impostazioni › MIDI › «Schede MIDI in ingresso e filtri» si spuntano **tutte le schede MIDI** da usare: vengono aperte
insieme (le schede spuntate ma scollegate restano in elenco come «non collegata» e si riaprono quando
riavvii CueStage con la scheda collegata). Ogni comando — GO, Stop, i fader, il crossfader, il trigger e
i fader di ogni cue — può indicare **da quale scheda** deve arrivare, dopo «@»:
`note 1 60 @ Akai APC mini`, `cc 1 7 @ nanoKONTROL2`. «Impara» aggiunge da sé la scheda da cui arriva il
messaggio (togli la spunta «Solo dalla scheda da cui arriva» per accettarlo da tutte). Senza «@» il
comando vale da qualunque scheda. Così la stessa nota o lo stesso CC su due controller diversi possono
fare cose diverse. Il nome della scheda è salvato senza i numeri ALSA (es. «20:0»), che possono cambiare
a ogni avvio: la scheda viene riconosciuta lo stesso.

### Filtri MIDI per scheda

Selezionando una scheda nell'elenco, a destra compaiono i suoi **filtri**, per scartare messaggi non voluti
(es. un'interfaccia audio o una tastiera che inviano CC continui, clock, sustain…):
- **Accetta**: Note, Control change, Program change, Pitch bend (togli quelli che non servono);
- **Canali**: «tutti» (vuoto) oppure un elenco, es. `1, 10, 13-16`;
- **Ignora CC** / **Ignora note**: numeri o intervalli da scartare, es. `1, 64, 120-127`;
- **Escludi da «Impara»**: la scheda continua a comandare lo show, ma i suoi messaggi non vengono
  catturati da «Impara» — utile quando una scheda manda messaggi di continuo.

I messaggi scartati non arrivano né ai comandi né a «Impara». I filtri modificati valgono subito anche
per «Impara» nello stesso dialogo, prima di premere OK, e si salvano con lo show. Le schede senza filtri
accettano tutto.

### Fader e potenziometri MIDI (controlli continui)

Impostazioni › MIDI › «controlli continui»: si assegna un controllo che invia un **valore**
— control change (`cc 1 7`), pitch bend dei fader motorizzati (`pb 1`, 14 bit), oppure un program change
usato come valore (`pc 1 *`, il numero del programma 0…127) — con **Impara** (muovi il fader):
- **Volume della cue selezionata** — la cue selezionata nella lista: se è in riproduzione il volume cambia
  dal vivo (il livello programmato non cambia), se è ferma si regola il livello programmato. Legge del
  fader: in basso −∞, a ~70% della corsa 0 dB, in cima +6 dB;
- **Trasparenza della cue selezionata** (video e immagini): 0…100%, dal vivo o sul valore programmato;
- **Crossfader**: vedi «Crossfade».

Ogni cue può avere inoltre i **propri** controlli: scheda «Base» › **Fader MIDI volume** (audio, video) e
**Fader MIDI trasparenza** (video, immagini), con Impara e ✕ per toglierli. Valgono anche se la cue non è
selezionata. Il fader è «assoluto»: al primo movimento il valore salta alla posizione del fader.

### Crossfade tra la cue corrente e la successiva

- **Automatico**: Riproduzione › **Crossfade alla successiva** (F6, pulsante ⇄ X-fade della pagina web,
  MIDI, OSC `/xfade [secondi]`). La cue in attesa (con le sue «con la precedente») parte da zero e sale
  mentre la cue corrente scende e alla fine si ferma; durata in Impostazioni › Audio › «Durata crossfade»
  (di serie 3 s). Il ritardo (pre-wait) della cue in attesa non si applica.
- **Manuale**: un fader MIDI assegnato a **Crossfader** (oppure OSC `/xfader 0…1`). Allontanandolo dal lato
  a riposo parte la cue in attesa, e la posizione decide il passaggio; arrivato in fondo la cue corrente si
  ferma e il lato a riposo si inverte (il crossfade successivo si fa riportando il fader indietro, come su
  un mixer video); riportandolo all'inizio prima della fine il crossfade si annulla e la cue in attesa
  torna in standby.

«Cue corrente» = le cue audio/video/immagine della lista di GO partite per ultime insieme (es. un video e
la sua musica). L'audio passa a potenza costante (nessun calo a metà). Le immagini si dissolvono senza buio:
sfuma quella che sta sopra (layer più alto), quella sotto resta piena fino alla fine.

## Tastiera

Spazio GO · Alt+←/→ frase di testo precedente/successiva · F6 Crossfade alla successiva · Ctrl+Maiusc+G raggruppa · Ctrl+E espandi/compatta gruppo · F10 pannello mixer · Esc Stop tutto · F7 Pausa · F8 Riprendi · F9 pannello proiettori · Ctrl+T prova cue · Ctrl+D duplica · Canc elimina ·
Ctrl+↑/↓ sposta · Ctrl+G geometria · Ctrl+S salva. Si possono trascinare file e cartelle dal file manager
nella lista e riordinare le cue col mouse.

## Note tecniche

- Struttura del codice (`cuestage/`):
  - `model.py` (show, cue, liste), `engine.py` (esecuzione delle cue), `audio_engine.py` (mixer audio su
    PortAudio), `media.py` (ffmpeg/ffprobe, forme d'onda), `video_output.py`, `text_render.py`;
  - `mixers.py` + `mixer_plugins/` (mixer digitali e schede audio ALSA, con `alsactl.py`),
    `projectors.py` + `plugins/` (videoproiettori), `osc_profiles.py` + `osc_profiles/` (dispositivi OSC),
    `remote.py` (OSC/MIDI in ingresso), `webserver.py` (interfaccia web), `cluster.py` (master/slave);
  - `main_window.py` è il nucleo della finestra principale; le funzioni sono divise per area in
    `window/` (riproduzione, modifica, gruppi, liste, hardware, istanze, OSC/MIDI, web, video, file);
  - `ui_inspector.py` è il nucleo dell'ispettore; le schede sono in `inspector/` (media, testo,
    controllo OSC/MIDI, mixer, proiettori, preset); le altre finestre sono nei file `ui_*.py`.
- Le differenze tra sistemi operativi sono raccolte in `system.py` (cartelle dei dati, ffmpeg incluso nel
  pacchetto, percorsi portabili negli show e nella sincronizzazione master/slave). Un plugin mixer può
  limitarsi ad alcuni sistemi con `PLATFORMS = ("linux",)`.
- Pacchetti: `packaging/build_appimage.sh` (Linux), `packaging/build_windows.ps1` e
  `packaging/build_macos.sh` (PyInstaller con `packaging/cuestage.spec`), `.github/workflows/build.yml`
  (tutti e tre su GitHub). Su Windows e macOS senza console i messaggi vanno in `cuestage.log` nella
  cartella dei dati.
- Test di regressione rapidi (senza scheda audio, con mixer X32 simulato e due istanze in rete locale):
  `python3 tests/test_smoke.py`. Ogni pacchetto ha anche `--self-test`.
- ffmpeg incluso: build LGPL di BtbN (Linux, Windows) e build statiche di zackees/ffmpeg_bins (macOS);
  CueStage lo usa come programma esterno.

- Lo show è un file JSON `.cueshow`; i percorsi sono salvati anche relativi allo show, quindi la cartella
  si può spostare su un altro computer.
- L'audio (anche quello dei video) viene decodificato in memoria in anticipo, a 16 bit con i canali
  originali (avvio istantaneo, loop senza buchi, fade e curve precisi al campione): circa 11 MB al minuto
  per un file stereo.
- I loop video usano il seek di FFmpeg: possono avere una piccola discontinuità sul punto di loop.
- Se OpenGL non è disponibile l'uscita passa automaticamente al rendering software.
