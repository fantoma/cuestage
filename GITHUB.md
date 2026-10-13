# CueStage su GitHub: pacchetti per Linux, Windows e macOS

GitHub Actions costruisce e prova CueStage su macchine Linux, Windows e Mac messe a disposizione da GitHub.
Tu carichi il progetto una volta. Poi, per ogni nuova versione, basta un comando: dopo circa mezz'ora la
pagina **Releases** contiene:

| File | Per chi |
|---|---|
| `CueStage-1.0.0-x86_64.AppImage` | Linux (Ubuntu/Xubuntu 22.04+, Debian 12+, Fedora 35+…) |
| `CueStage-1.0.0-windows-setup.exe` | Windows 10/11, installatore |
| `CueStage-1.0.0-windows-portable.zip` | Windows, senza installazione (anche da chiavetta) |
| `CueStage-1.0.0-macos-arm64.dmg` | Mac Apple Silicon (M1, M2, M3, M4) |
| `CueStage-1.0.0-macos-x86_64.dmg` | Mac Intel |

Prima di creare ogni pacchetto, GitHub prova CueStage su quel sistema:
- test di regressione;
- `--self-test` del pacchetto finito: audio, ffmpeg, plugin, finestra.

Se qualcosa non va, la Release non riceve quel file e il motivo si legge nella scheda **Actions**.

---

## 1. Account e repository (una volta sola)

1. Crea un account su <https://github.com> (gratuito), se non lo hai.
2. Scegli se il repository sarà **pubblico** o **privato**:
   - **Pubblico**: chiunque vede il codice. GitHub Actions è **gratuito senza limiti** di minuti e di spazio.
   - **Privato**: lo vedi solo tu (e chi inviti). Il piano Free comprende **2000 minuti al mese** e
     **500 MB** di spazio per gli artifact.
     - I minuti Windows contano doppio e quelli Mac **10 volte**.
     - Una versione completa costa circa 300–400 minuti: bastano per **5–6 versioni al mese**.
     - I semplici push costano pochi minuti, perché provano solo su Linux.
     - Esauriti i minuti, i lavori si fermano senza addebiti, salvo che tu imposti un limite di spesa.
3. Non serve creare il repository dal sito: lo crea il comando del punto 2.

## 2. Caricare il progetto (da Ubuntu, una volta sola)

```bash
sudo apt install git gh                  # git e lo strumento ufficiale di GitHub
gh auth login                            # GitHub.com › HTTPS › Login with a web browser
                                         # (copia il codice, si apre il browser, confermi)
git config --global user.name  "Il tuo nome"
git config --global user.email "la-tua-email@esempio.it"

unzip cuestage.zip && cd cuestage        # la cartella del progetto che ti ho mandato
git init -b main
git add .
git commit -m "CueStage 1.0.0"
gh repo create cuestage --private --source=. --push     # oppure --public
```

Fatto: il progetto è su `https://github.com/<tuo-utente>/cuestage`. Nella scheda **Actions** vedrai partire
subito il primo «Build». È il test su Linux e dura circa 3 minuti; il segno verde ✔ vuol dire che è riuscito.

> Se preferisci non usare la riga di comando: sul sito, *New repository* › nome `cuestage` › *Create*,
> poi *uploading an existing file* e trascina il **contenuto** della cartella. Attenzione: la cartella
> nascosta `.github` deve esserci, altrimenti non parte nulla. Su Ubuntu, nel file manager, mostra i file
> nascosti con Ctrl+H prima di trascinare.

## 3. Pubblicare una versione (ogni volta)

1. Scegli il numero, per esempio 1.0.1, e scrivilo in `cuestage/__init__.py`:
   ```python
   __version__ = "1.0.1"
   ```
2. Salva su GitHub e crea il **tag** con lo stesso numero, preceduto da `v`:
   ```bash
   git add -A
   git commit -m "Versione 1.0.1"
   git push
   git tag v1.0.1
   git push origin v1.0.1
   ```
3. Su GitHub › **Actions** vedrai il lavoro «Build» per `v1.0.1`, con queste fasi:
   - **Test** su Ubuntu, Windows e macOS (5–10 minuti);
   - **Prepara la Release**, che controlla che il tag corrisponda alla versione;
   - **Linux AppImage**, **Windows** e **macOS (arm64 / x86_64)**, in parallelo (10–25 minuti).
4. A lavoro finito, su GitHub › **Releases** › *CueStage v1.0.1* trovi tutti i file. Puoi mandare quel link a
   chi deve installare CueStage.

Se il tag non corrisponde alla versione, il lavoro si ferma con un messaggio chiaro. Per riprovare,
correggi la versione, poi cancella il tag e ricrealo:
```bash
git tag -d v1.0.1 && git push origin :refs/tags/v1.0.1
git tag v1.0.1 && git push origin v1.0.1
```

### Prova senza pubblicare

GitHub › **Actions** › **Build** (a sinistra) › **Run workflow** › *Run workflow*.
- Costruisce tutti i pacchetti senza creare una Release.
- I file restano 3 giorni in fondo alla pagina del lavoro, sotto **Artifacts**.
- Con un repository **privato** i pacchetti insieme superano i 500 MB gratuiti per gli artifact, quindi
  qualcuno potrebbe non essere caricato. In quel caso usa i tag, perché i file delle Release non contano.

## 4. Quando qualcosa va storto

- **Actions** › il lavoro con la ✘ rossa › il passaggio con la ✘ › leggi le ultime righe del registro.
- Il **self-test** stampa una riga ✔ o ✘ per ogni parte (PortAudio, ffmpeg, plugin, interfaccia).
- Mandami il testo dell'errore: di solito è una correzione di una riga.
- Un caso noto: GitHub ritira ogni tanto i tipi di macchina. Se il lavoro macOS Intel dice che
  `macos-15-intel` non esiste:
  - in `.github/workflows/build.yml`, sostituiscilo con il nome indicato da GitHub;
  - oppure togli le due righe `- runner: macos-15-intel` / `arch: x86_64`, se non ti servono Mac Intel.

## 5. Avvisi al primo avvio (e come toglierli)

Senza firme commerciali i pacchetti funzionano, ma i sistemi avvisano al primo avvio:

- **Windows**: «Windows ha protetto il PC» › *Ulteriori informazioni* › *Esegui comunque*.
  Per togliere l'avviso serve un certificato di firma del codice, a pagamento.
- **macOS**: «CueStage non può essere aperto…» › tasto destro su CueStage › *Apri* › *Apri*
  (oppure Impostazioni di Sistema › Privacy e sicurezza › *Apri comunque*). Basta la prima volta.
  - Alla domanda sulla **rete locale** rispondi *Consenti*: senza, mixer, proiettori, OSC e istanze non
    funzionano.

### Firma e notarizzazione Apple (facoltative)

Servono l'**Apple Developer Program** (99 $/anno) e un Mac per esportare il certificato:

1. Su developer.apple.com crea un certificato **Developer ID Application**.
2. Installalo sul Mac (doppio clic) e da *Accesso Portachiavi* esportalo come `.p12`, con una password.
3. Converti il file per GitHub: `base64 -i certificato.p12 | pbcopy` (il testo finisce negli appunti).
4. Su appleid.apple.com › *Password specifiche per le app* crea una password per «CueStage CI».
5. Su GitHub › repository › **Settings** › **Secrets and variables** › **Actions** › *New repository secret*,
   crea questi segreti:

   | Nome | Valore |
   |---|---|
   | `MACOS_CERT_P12` | il testo copiato al punto 3 |
   | `MACOS_CERT_PASSWORD` | la password del .p12 |
   | `MACOS_CODESIGN_ID` | es. `Developer ID Application: Mario Rossi (AB12CD34EF)` |
   | `APPLE_ID` | la tua email Apple |
   | `APPLE_TEAM_ID` | il Team ID (10 caratteri, su developer.apple.com › Membership) |
   | `APPLE_APP_PASSWORD` | la password del punto 4 |

Dalla versione successiva i .dmg escono firmati e notarizzati, senza avvisi. Se i segreti mancano, il
flusso continua a funzionare con la firma «ad hoc».

## 6. Costruire a mano (senza GitHub)

Ogni pacchetto si può costruire anche sul sistema di destinazione, con la connessione a Internet:

| Sistema | Comando (dalla cartella del progetto) | Requisiti |
|---|---|---|
| Linux | `packaging/build_appimage.sh` | — |
| Windows | `powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1` | Python 3.12 da python.org; Inno Setup 6 per l'installatore |
| macOS | `packaging/build_macos.sh` | Python 3.12 da python.org e `xcode-select --install` |

I file finiscono nella cartella `dist/`.
