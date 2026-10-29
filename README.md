# CueStage

**Designed by Diego Fantoma**

Show-control cue system for Linux (Ubuntu 22.04 and later), Windows 10/11 and macOS 12+, along the lines of
QLab / Show Cue System. Written in Python with Qt 6 (PySide6); audio uses its own mixer on PortAudio
(PipeWire/ALSA/JACK on Linux, WASAPI/ASIO on Windows, Core Audio on macOS), video uses the FFmpeg backend of
Qt Multimedia.

> ⚠ **Experimental software.** CueStage is still under development: do not use it in production (live shows,
> events, installations). It may contain bugs and behave unexpectedly. You use it at your own risk, without any
> warranty. At every start CueStage shows this notice (in Italian, English and Slovenian) and waits for you to
> accept it.

## Installation

```bash
unzip cuestage.zip && cd cuestage
./install.sh          # apt packages + isolated Python environment + menu entry
cuestage              # or from the applications menu
```

To try it without installing: `sudo apt install ffmpeg libportaudio2 python3-venv`, then
`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && ./run.sh`.

### AppImage (a single file, nothing to install)

`CueStage-<version>-x86_64.AppImage` contains everything: Python 3.12, Qt 6, numpy, PortAudio, mido/rtmidi,
ffmpeg/ffprobe and amixer. It works on any x86_64 distribution with glibc 2.34 or later
(Ubuntu/Xubuntu/Kubuntu 22.04+, Linux Mint 21+, Debian 12+, Fedora 35+…) without extra packages.

```bash
chmod +x CueStage-1.0.0-x86_64.AppImage
./CueStage-1.0.0-x86_64.AppImage [show.cueshow]
./CueStage-1.0.0-x86_64.AppImage --self-test     # checks audio, video, MIDI, ffmpeg, interface
```

- Settings, user plugins (`~/.local/share/cuestage/…`) and shows are the same as the installed version's:
  you can switch from one to the other.
- If the system has no FUSE the AppImage does not mount: start it with `--appimage-extract-and-run`.
- For a menu entry: copy the file to `~/Applications` and use *Gear Lever* or *AppImageLauncher*,
  or let `install.sh` create the entry for the installed version.
- The system's JACK libraries and `amixer`, if present, take precedence over the bundled ones
  (so PipeWire-JACK works as expected).

To rebuild it (on an Ubuntu/Debian x86_64 PC with an Internet connection, no root needed):

```bash
packaging/build_appimage.sh        # -> dist/CueStage-<version>-x86_64.AppImage
```

The script downloads into `build/cache` a portable Python (python-build-standalone), the Python libraries
from PyPI, ffmpeg (BtbN, LGPL licence), PortAudio/JACK/amixer/xcb-cursor from the Ubuntu 22.04 packages and
appimagetool; it strips the unused parts of Qt (`packaging/prune_qt.py`: from ~750 to ~210 MB), runs
`--self-test` and creates the AppImage (~160 MB). Versions are changed with the variables `PY_URL`,
`FFMPEG_URL` and `APPIMAGETOOL_URL`.

### Windows and macOS

The packages are downloaded from the *Releases* page of the GitHub repository (see `GITHUB.md` for how to
get them): GitHub Actions builds them and also tests the program on each system.

- **Windows 10/11 (64 bit)**: `CueStage-<version>-windows-setup.exe` (installer: Start menu, icon,
  opening of `.cueshow` files, firewall rule for the network) or `…-windows-portable.zip`
  (unzip it and start `CueStage.exe`, even from a USB stick). On the first start Windows SmartScreen may say
  “Windows protected your PC”: *More info › Run anyway* (the program is not signed with a commercial
  certificate).
- **macOS 12 or later**: `CueStage-<version>-macos-arm64.dmg` for Apple Silicon Macs (M1, M2…),
  `…-macos-x86_64.dmg` for Intel Macs. Open the .dmg and drag CueStage to Applications. If it is not
  notarised, on the first start: right-click (or ctrl-click) on CueStage › *Open* › *Open*; or
  System Settings › Privacy & Security › *Open Anyway*. When asked for access to the **local network**
  answer *Allow*: without it, mixers, projectors, OSC and secondary instances do not work.

Differences between the systems:

| | Linux | Windows | macOS |
|---|---|---|---|
| Audio | PipeWire / ALSA / JACK | WASAPI (and ASIO if the driver offers it) | Core Audio |
| User data (plugins, profiles, autosaves) | `~/.local/share/cuestage` | `%APPDATA%\CueStage` | `~/Library/Application Support/CueStage` |
| Sound card panel (Scarlett, ALSA) | yes | no (use Focusrite Control) | no (use Focusrite Control) |
| Mixers, projectors, OSC, MIDI, web, master/slave | yes | yes | yes |

Shows open on any system: file paths are also saved relative to the show, so copying the show folder is
enough. Master and slave can be on different systems too. Cues that use a Linux ALSA card show up without a
mixer (in red) on Windows/macOS.

Building the packages by hand (on the target system):

```text
Windows:  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
macOS:    packaging/build_macos.sh
```

## Interface language

**Settings › Language**: Italiano, English, slovenščina. The choice belongs to the computer (not the show)
and applies when CueStage is **restarted**. On the first start CueStage uses the system language if it is
available, otherwise English. For a quick test: `CUESTAGE_LANG=en ./run.sh`.

The texts are in `cuestage/lang/<code>.json` (`it`, `en`, `sl`): every Italian text of the program with its
translation. Translations must keep the placeholders `{0}`, `{1}`… (with their format, e.g. `{0:.1f}`), in any
order; an empty text stays in Italian. Languages can be corrected or added without touching the program, by
putting the file in the `lang` folder of the user data (`~/.local/share/cuestage/lang/` on Linux): it completes
and corrects the program's file.

For developers:
- interface texts are written in Italian inside `tr("…")` (`from .i18n import tr`); variable parts go in
  `tr("Scheda «{0}» con {1} canali", name, n)` instead of f-strings;
- `python3 tools/lang_update.py` adds the new texts (empty, to be translated) to the language files and removes
  the ones that no longer exist; `--check` reports missing texts and mismatched placeholders; `--new de "Deutsch"`
  creates a new language;
- the regression tests check that English and Slovenian are complete and have the same placeholders.

The web interface for phones and tablets and the start-up self-test messages are still in Italian.

## Cue types

| Type | What it does |
|---|---|
| **Audio** | WAV, MP3, FLAC, OGG, AIFF, M4A… IN/OUT, loop (region and number of repetitions, 0 = infinite), volume, automatic fade in/out, volume curve drawn on the waveform |
| **Video** | MP4, MOV, MKV, WebM… same options as audio + layer, opacity, fit, **crop**, **position** and **scale** |
| **Image** | PNG, JPG, … duration (0 = until stopped), fade in/out, layer, opacity, crop, position and scale |
| **Change preset** | Takes a running cue to one of its audio presets (volume) and/or crop/position presets (crop, offset, scale, perspective, opacity) over the chosen transition time. It only applies to that run: the saved cue does not change |
| **Fade** | Takes the volume and/or opacity of another cue to a value, with duration and shape (S, linear, dB). Presets “Fade OUT + stop” and “Fade IN” (starts the target from silence) |
| **Command** | Play, Stop (with fade), Pause, Resume, Stop all, Move playhead |
| **OSC** | Sends an OSC message (host, port, address, automatically typed arguments) |
| **Projector** | Command to a network video projector: power, shutter, input, freeze, lens, lens memory, specific commands (PJLink driver or plugin) |
| **Wait** | Timed pause, useful in chains |
| **Memo** | Note for the operator |

## Cue lists: MAIN and PANEL

Above the table are the list tabs: **MAIN** (the main list) and the **PANELs**, additional lists (e.g.
walk-in music, effects on call, emergency cues).
- **+ PANEL** (or Cue › New PANEL, Ctrl+Shift+N) creates a PANEL; double-click the tab to rename it;
  right-click the tab: rename, move left/right, delete. Ctrl+PgDown / Ctrl+PgUp switch from one list to
  another.
- Each list has its own **playhead** (cue in standby): clicking a cue in a PANEL does not move the MAIN.
  “With previous” and “After previous” apply within the same list.
- **Moving cues** between lists: select the cues, then right-click › Move to (or Cue › Move the selected
  to), also into a new PANEL.
- Fade, Change preset and Command cues can target cues in any list (in the target menus PANEL cues are
  marked [PANEL name]). New numbers are unique across the whole show; “Renumber” in a PANEL uses P1.1,
  P1.2… so it does not overlap the MAIN.
- The tab with **⏵GO** is the list GO acts on; ▶ marks a list with cues playing.
- Each list has a colour (the small square on the tab). In the **Active cues** panel every playing or waiting
  cue carries a label with the name of its list, in the same colour; clicking the label opens that list with
  the cue selected (the playhead does not move).
- STOP ALL, Pause and Resume act on all cues of all lists.

**GO on** (next to the GO button):
- **MAIN**: GO always starts the standby cue of the MAIN, whatever tab you are looking at — you can prepare or
  check a PANEL without risking starting the wrong cue;
- **PANEL**: GO follows the selected tab (MAIN or PANEL).

With “GO on: MAIN”, when looking at a PANEL the **local GO** bar appears under the tabs: the “GO <PANEL name>”
button starts the waiting cue of that PANEL (its name is shown next to it) and advances it, while the main GO
and the space bar stay on the MAIN. Next to it, **“STOP <PANEL name>”** stops only the cues of that PANEL
(including those in pre-wait, with the stop time from the settings); the MAIN and the other PANELs keep going.
It is active only while something is running in the PANEL. With “GO on: PANEL” the bar is not needed and does
not appear.

The “Standby” label also shows the list (e.g. “Music › 4 Applause”). The choice is saved in the show. With OSC:
`/list/<name>` shows a list, `/list/<name>/go` does GO on that list, `/list/<name>/stop` stops only that list;
`/next` and `/previous` (and the matching MIDI) act on the GO list.

## PANEL GRID (tiles)

A PANEL can show its cues as **tiles** to click, like a sampler: **+ GRID** next to + PANEL, or right-click a
PANEL › “Show as grid” (and “Show as list” to go back: the cues stay the same).

- **Size** in tiles (rows × columns, up to 16 × 16) and **several pages**: press **⚙ Options** to see
  “+ Page”, rows, columns and the click behaviour. Double-click a page to rename it; right-click to delete it
  (only if it is empty). If you shrink the grid, the cues that no longer fit move to the first free tiles (on a
  new page if needed): none is lost.
- **Click** on a tile: *start / stop* (default), *restart from the beginning* or *start (does nothing if already
  active)*. The active tile has a green border, the progress bar and the remaining time; in pre-wait the border
  is amber.
- With **⚙ Options** open: click = select (inspector) · drag = move (onto an occupied tile the two cues swap) ·
  double-click an empty tile = new cue · drag audio/video files from the file manager onto a tile = new cues
  starting from there.
- **Colour** of each tile: right-click › Colour (12 ready-made colours or “Other colour…”), or from the
  inspector's “Basic” tab. The colour also tints the cue's row in the lists.
- **Keyboard shortcuts**: right-click › “Learn keyboard shortcut…” (or “Learn…” in the Basic tab) and press the
  key or combination (F5, 1, Q, Ctrl+1…). It works for any cue, also in the MAIN or in a list PANEL, while the
  CueStage window is active but not while typing in a text field. Keys already used by CueStage (Space, Esc,
  menu commands…) cannot be assigned; if the key already belongs to another cue, it moves to the new one. The
  key appears in the top right corner of the tile.
- **Local GO**: the GRID also has the PANEL's GO / STOP; GO advances tile by tile (page, row, column) and the
  next tile has a dashed yellow border.
- **SOLO** (always visible in the bar, next to BLIND; orange when active): when an audio, video, image or text
  cue or a group starts, the other active cues of the GRID stop. Control cues (OSC, MIDI, mixer, fade…) do not
  stop anything. It is saved in the show.
- **Fade out** (in ⚙ Options): the fade used by all GRID stops: click on an active tile, Solo mode, the PANEL's
  STOP.
- **BLIND** (always visible in the bar): blind test. The GRID cues work in every respect (times, bars, cue
  meters, GO, Solo, groups) but **send no audio or video to the outputs**; control cues (OSC, MIDI, mixer…) still
  act. Tiles in BLIND have the red “BLIND” label. It is not saved in the show (always off when opening) and in
  redundancy it also applies to the secondaries.
- **Groups**: a group takes one tile (click = start the group). To see and edit its cues: right-click › “Open
  the group”, or double-click the tile with Options open. In the open group every cue is a tile in playing
  order: double-click an empty tile (or right-click) = new cue in the group, drag to change the order, drag files
  to add them, right-click › “Remove from group”; “◀ Back to the grid” to leave. To put an existing cue into a
  group, drag its tile onto the group's tile (with Options open).

## Adding cues and groups in the lists

- **Double-click the empty space** under the cues (MAIN or list PANEL): menu to add a cue at the end.
  Right-click always has “New cue” (after the selection, or at the end when clicking the empty space).
- **Several cues into a group**: select them (Ctrl/Shift + click) and drag them **onto the group's row**: they go
  to the end of the group. Dropped between two cues of the group, they go to that point.

## “Stop panel” cue

Stops the running cues (and pre-waits) of the chosen lists, with an optional **fade**; the other lists keep
going. In the inspector tab you choose the **MAIN** and one or more PANELs (GRIDs too), or **“All PANELs”**,
which also covers PANELs created later (the MAIN is added separately). The cue does not stop itself: it can be
in any list, also in a GRID as a “stop everything” tile.

## “Text” cue (subtitles, surtitles, captions)

The text is drawn on the video output, above (or below, with the **layer**) the other cues, and follows the
4-corner correction of the output.
- **Sequence of lines** (subtitles): in the text box each line is separated by an **empty line** (a line can
  span several rows); “_” alone = empty screen. GO on the cue shows the first line; **each following GO moves to
  the next line** (the “Standby” label shows the next one). After the last line the following GO exits: the text
  fades and GO moves to the next cue. **Alt+←/Alt+→** previous/next line, **ESC** leaves the text, OSC
  `/text/next`, `/text/prev`, `/text/goto <n>`. **Import .txt / .srt** loads the lines from a file (only the
  texts are kept from an .srt).
- **Free text**: all the text together, until STOP or for the given **duration**.
- **Area** in % of the output (x, y, width, height) with quick positions (subtitle at the bottom, surtitle at the
  top, centre, full screen); horizontal and vertical **alignment**; font, size (px referred to an output 1080
  high), bold/italic, colour, **outline**, **background** (behind the text or the whole area, with opacity),
  padding, line spacing, **fade** between lines.
- **Fitting** the text to the area: *wrap* (fixed size; with “maximum lines” the extra lines are cut with “…”),
  *wrap and shrink if it does not fit*, *shrink / enlarge to fill the area*, *one line, shrink to fit*.
  **Maximum lines**: 0 = no limit.
- Preview in the inspector and “Show on the output from this line”; style changes also apply immediately to the
  text on the output.

## “MIDI out” cue

Sends arbitrary MIDI messages, one per line, on the MIDI output chosen in the cue or on the default one
(Settings › MIDI › MIDI output). The syntax check is immediate and points to the wrong line.

    note 1 C4 100        Note On (channel, note or number, velocity)   · off 1 C4   Note Off
    cc 1 7 127           Control Change          · pc 1 5         Program Change
    pb 1 0               Pitch Bend (−8192…8191) · at 1 64        Channel aftertouch
    nrpn 1 300 8000      NRPN (14-bit parameter and value)
    msc all go 12.5      MIDI Show Control (go, stop, resume, load, fire, reset, all_off…; cue and list)
    sysex 43 10 3E …     System Exclusive        · hex 90 3C 64   raw bytes
    wait 0.5             wait before the next message                 · # comment

## Cue groups

In any list (MAIN or PANEL) cues can be gathered into **groups**: select the cues and
**Cue › Group the selected** (Ctrl+Shift+G, also from the right-click menu), or create a “Group” cue and insert
cues with the group selected, or drag them in. The group's cues appear indented (↳); a **click on the “Type”
column** of the group (or Ctrl+E) **collapses** or **expands** it. “Remove from group” and “Ungroup” do the
opposite; moving, duplicating and deleting a group acts on all its cues (when deleting you can choose to keep
them).

The kind is chosen in the inspector's **Group** tab:
- **Sequence** — GO on the group starts the first cue; the following GOs the others, in sequence (“With previous”
  and “After previous” apply). After the last one, the following GO leaves the group.
- **Random** — GO starts a random cue (never the same twice in a row). The following GO leaves.
- **Loop** — the cues follow each other on their own forever: **hold** on each cue (0 = its duration) and
  **crossfade** to the next (audio at constant power, images and video with a dissolve). The following GO
  leaves the group.
- **Random loop** — like Loop, in random order. **Advance**: *on time* (the following GO leaves) or *with GO*:
  each GO moves to another random cue with the crossfade; you leave with **ESC** (a second ESC is STOP ALL) or
  with the **“Exit group”** cue, created automatically at the end of the group, which can be called from another
  PANEL (Command cue › Start), with its MIDI/OSC trigger or with `/cue/<number>/start`.
- **All** — GO starts all the cues together; each one's pre-wait is respected. The following GO leaves.
- **Playlist** — GO starts the first cue; the others start on their own, each as soon as the previous one ends,
  in list order. The pre-wait and “with/after previous” of the group's cues do not count. Each cue plays once
  and after the last one the group ends. An optional **crossfade** between one cue and the next. The following
  GO leaves the group; STOP stops it.
- **Random playlist** — like Playlist, but in random order: each cue plays once, in a different order at every
  start (the same on the master and on redundant instances).

In playlists the **loop of single cues is ignored** (each plays once per round; in the inspector the cue's
“Loop” box appears disabled). With **Loop** on for the group, after the last cue the playlist starts again; the
Random playlist reshuffles every round without repeating the same cue twice in a row. The crossfade, if any,
never exceeds half the cue's duration.

### Tap Tempo

For **Sequence**, **Loop** and **Random loop** (Group tab › “Tap Tempo”) the cues follow each other in time, as
if GO were pressed on every beat: **one cue every 1/16, 1/8, 1/4, 1/2, 1, 2, 4, 8 or 16 beats** at the tempo in
**BPM** (20–300). The tempo is tapped, also while the group is not playing:
- with the inspector's **TAP** button;
- with a **key** (“Learn…”: also a pedal or a USB button box that present themselves as a keyboard; holding it
  down does not tap);
- with a **MIDI note/CC** (“Learn”: pedal, pad, controller);
- via **OSC**: `/cue/<number>/tap` (always) or a chosen address (e.g. `/tap`).

The BPM is the average of the last 8 taps (at least 2; after a 2.5 s pause counting starts again); each tap
also puts the beat back **in phase**. The value can also be typed by hand. In the list the group shows
“♩ 128 BPM · one cue every 1/2 beat”, in the GRID the tempo appears in the tile. In redundancy each tap also
reaches the secondaries, which keep the same tempo.

How it behaves (and where it makes no sense):
- **Sequence** — on every beat the group advances as with a GO; after the last cue the beat stops (it does not
  advance beyond the group). Cues with a pre-wait start later than the beat; those “with previous” start with
  theirs; a GO given by hand advances one more and the beat continues from there.
- **Loop / Random loop** — the beat replaces the “hold” (and, in Random loop *with GO*, the GO: GO goes back to
  leaving the group). A cue longer than the interval is **cut** (with the crossfade, which never exceeds half the
  interval); a shorter one leaves **silence** until the beat.
- **Random, All, Playlist, Random playlist** — the option is disabled: Random starts a single cue and All all of
  them together (there is no sequence to pace); playlists follow the natural duration of the cues (in time they
  would become a Loop: use Loop or Random loop).
- Below 100 ms between one cue and the next (e.g. 1/16 at high tempos) the inspector warns you; the absolute
  minimum is 50 ms. Video and images cannot keep up at those speeds.
- A TAP key that matches a CueStage command is not accepted; if it is already used by another cue it moves to
  the new one. If the same MIDI note is also the general GO, GO wins.

**When leaving** a Loop/Random loop/Playlist: the current cue fades with the crossfade time, or it is left to
finish. A running group appears among the “Active cues”: its ■ (or STOP ALL) stops it.

## How each cue starts

- **Manual (GO)** – starts with GO (Space).
- **With previous** – starts when the cue above starts, after the *delay*.
- **After previous** – starts when the cue above ends naturally, after the *delay*.

After a GO the playhead (and the selection) jumps to the next manual cue; if the list has only one cue it stays
on it, so GO restarts it. A click on a row always puts it in standby. The state of each cue (waiting, progress,
elapsed/remaining time) is visible in the *Status* column and in the *Active cues* panel, where cues can be
paused or stopped one by one. Next to the bar of each cue with audio there is a small **meter** (one bar for mono
cues, two for stereo and multichannel; scale −60…0 dBFS, green / yellow above −9 dB / red above −1 dB): it
measures the cue's audio with level, fades, live trim and master volume.

## Waveform editor

Drag the **IN**/**OUT** markers and the loop ones; **double-click** (or Ctrl+click) adds a point to the volume
curve, which can be dragged; **right-click** on a point deletes it, elsewhere it opens a menu (IN/OUT/loop here,
clear curve). Wheel = zoom, Shift+wheel or dragging = scroll.

## Crop, position and perspective

“Video / image · crop” tab (video and images). Two views side by side:
- **Source · crop**: the still frame with the crop rectangle (drag edges, corners or the rectangle;
  double-click = reset). For **videos** a timeline above the views (with IN/OUT) chooses the still frame.
- **Output · rendering**: the frame as it reaches the projector (crop, position, scale, perspective and 4-corner
  correction, together with the other active cues). Drag a **corner** for the **perspective distortion**, the
  inside to move, wheel to scale, double-click a corner to reset it.

While the tab is open, **the video output shows the still frame with the current settings** (if the cue is not
already playing), so you can adjust while looking at the projector. The **Blind** box turns this off (it stays
saved in the show).

In the fields column: crop in source pixels, X/Y offset in output pixels (0,0 = centred), scale in %,
perspective as the offset in pixels of the 4 corners. The cue's perspective adds to the 4-corner correction of
the whole output.

## Presets

Presets belong **to the single cue**: each cue has its own. In the “Times, loop and audio” tab (**audio**
presets: volume, fade in, fade out, matrix and output volumes) and “Video / image · crop” (**crop / position**
presets: crop, offset, scale, perspective, opacity, fit) there is the cue's preset bar: **Apply**, **Save as…**,
**Update**, **Rename…**, **Delete**. Duplicating a cue duplicates its presets too.

A **Change preset** cue chooses the target cue and then one of *its* presets (the lists update when the target
changes). Shows saved with the previous version (global presets) are converted when opened: each preset goes to
the cue that used it.

### Preset keyframes

The timeline above the views of the “Video / image · crop” tab and the one at the bottom of the “Times, loop and
audio” tab contain the **keyframes**, on two lanes:
- **Image** (orange): crop/position presets — crop, offset, scale, perspective, opacity;
- **Audio** (light blue): audio presets — volume, **matrix** and **output volumes**.

Each keyframe uses a preset of the cue or the cue's **base values**. Between two consecutive keyframes of the
same lane the values fade from one to the other: the keyframe positions are the start and end of the fades (two
keyframes with the same preset = hold). Before the first and after the last keyframe the value stays still;
adding the first keyframe at a time > 0 creates a “base” one at 0, so the cue starts from its own values. The
timeline is in **cue time** (0 = start, i.e. the IN point; end = OUT) with the loop **unrolled** into its
repetitions, like the progress bars: a keyframe in the second repetition of the loop is placed in the second
repetition. With an infinite loop two extra rounds are shown. On the left you can read the cue time and the
matching file position (used for the still frame and for the cursor on the waveform). Matrix fades are at
constant power: moving a sound from one output to another keeps the volume constant, with no dip half way.

- **Add**: double-click the lane, or “+◆ Image” / “+◆ Audio” at the cursor time;
- **Edit**: click the ◆ (the cursor jumps to it) and choose the preset and the time in the row below; drag it to
  move it; right-click to change preset or delete it;
- **Delete**: “Delete” or the Del key.
- **Navigate**: the **◀ / ▶** arrows (or Ctrl+← / Ctrl+→ on the timeline) jump to the previous or next marker:
  IN, OUT, start/end of each loop repetition, end of fade in, start of fade out, curve points and keyframes.
  A click on a marker in the “Markers” lane also takes the cursor there.
- **Preset in use**: under the timeline a row shows the marker you are on and, for each lane, the preset in use
  at that point (or the fade in progress, e.g. “base” → “soft” 40%); the preset bar shows “▸ in use: …” and
  selects that preset, ready for “Update”.

Moving the cursor, the views, the video output and the matrix show the cue **as it is at that time**; where a
preset (or a fade) applies, direct editing is suspended and a notice explains it: edit the base values where
“base” applies, or update the preset with the preset bar.

A **Change preset** cue on the same cue takes control of the lane it changes (which from then on stops following
the keyframes), starting from the value it has at that moment.

## Master volume, last project and autosave

- The **master volume** (at the top, before “Pause”) acts on audio and video; double-click = 0 dB.
- On start CueStage reopens **the last project** used (or the one given on the command line).
- Every 5 seconds, if there are unsaved changes, a **hidden autosave** is written:
  `.name.cueshow.autosave` next to the show, or
  `~/.local/share/cuestage/.senza-titolo.cueshow.autosave` for a show never saved.
  After a crash, on reopening CueStage offers to recover it. The autosave is deleted when you save or close
  normally.

## Undo, redo and history

- **Edit › Undo** (Ctrl+Z) and **Redo** (Ctrl+Shift+Z or Ctrl+Y): the menu says what is undone
  (“Undo: Cue 5 «Rain»: level, fade out”). It covers everything that changes the show: cues, lists, PANELs and
  GRIDs, groups, settings, versions.
- Inside a text field Ctrl+Z undoes the typing in the field; outside, it undoes the last change to the show.
- A change becomes a step after half a second of pause: typing a name or dragging a slider is a single step.
- **Edit › History…** (Ctrl+H): the list of steps since the show was opened, with the time. Double-click (or
  “Go back to this point”) brings the show back to that step; the later steps stay grey and can be taken back
  with Redo, until you make a new change. **★ Mark point…** gives a step a name (“before the rehearsal”);
  **Make a version from this point…** creates a new version with the show as it was at that step, without
  touching the current one.
- Undoing does not stop running cues and does not touch the live controls: master volume, Duck, DIM, preview
  volume, BLIND, open or collapsed groups.
- Going back to the saved step makes the show “unmodified” again.
- The history lasts while the show is open (up to 500 steps): to keep a point, make a version of it.

## Show versions

**File › Show versions…** (Ctrl+Alt+V): several versions of the same show in the same file, for example
“Main theatre”, “Tour”, “School matinée”.

- Each version has its own cues, MAIN, PANELs and GRIDs, and its own **notes**: free text for what changes, for
  which theatre, things to try (“Insert date and time” adds a dated line).
- The **settings** (sound cards, matrix, mixers, projectors, OSC, MIDI, ducking, preview) are shared by all
  versions.
- You work and go on stage with the **active version** (●). “＋ New version from this one…” makes a copy;
  “● Make active” (or double-click) switches to another one. Running cues keep playing.
- For each version the window shows dates, number of cues and the differences from the active version
  (more cues, fewer, different).
- With more than one version, the name of the active one appears in the window title and in the status bar
  (click it to open the versions).
- Switching, creating or deleting a version or editing its notes can be undone with Ctrl+Z.
- Secondary instances receive all the versions, with their media files.
- A CueStage older than 1.0.6 opens the active version and ignores the others; if it saves, the other versions
  are lost.

## Sound cards and matrix

All the audio handling of an audio or video cue is in the **Times, loop and audio** tab (“Audio output” box,
under the times):
- **Sound card**: “Project default” (by default the one chosen in the Ubuntu audio settings) or any connected
  card. Several cues can play at the same time through different cards; the cards used by the cues are opened
  when the show is opened.
- **Matrix**: rows = channels of the file (L/R, or L R C LFE Ls Rs for a 5.1…), columns = card outputs. Click a
  crosspoint = on (0 dB) / off; drag vertically or use the wheel for the volume (Ctrl+wheel: tenths);
  double-click = exact value; right-click = quick values or “only this output”. The last row (“Vol. out”) sets
  the volume of each output. It can be changed during playback too. “Default routing”: mono → Out 1-2,
  stereo → Out 1/2, multichannel channel by channel, 5.1/7.1 to stereo as a downmix.
- **Video**: by default the video's audio goes through the CueStage engine (card and matrix) and the picture
  locks to the audio (realigned if it drifts by more than 150 ms, loops included). Alternatively “System player”:
  default card, stereo, no matrix.

In **Settings** you choose the project's default card and the **channels used for each card**: “auto” = all the
channels for hardware cards (`hw:`), 2 for virtual devices (`default`, `pipewire`, `pulse`…), which always
declare many channels even when they are stereo. “Rescan connected cards” is for a USB card plugged in after
start-up. If a chosen card is not connected, the cue plays through the default one and a warning says so.

**With PipeWire (Ubuntu default)**: an `hw:` device used by PipeWire may be busy. In that case PortAudio sees it
without outputs: CueStage still shows it in the list, in grey, as “‹card› — busy with ‹program›” (usually
`pipewire` or `wireplumber`). To use it directly (all channels, lowest latency) set the card's profile to “Off”
in pavucontrol › Configuration, then “Rescan connected cards”; to see who keeps it open:
`fuser -v /dev/snd/pcmC1D0p`. Two ways: choose the card's **Pro Audio** profile in Ubuntu's Settings › Sound and
use the `pipewire` device setting its channels (e.g. 8); or close the other audio applications.

### Sound card control panel (Focusrite Scarlett and others)

The functions that on Windows/macOS are set with the manufacturer's program (e.g. **Focusrite Control**) are ALSA
mixer controls on Linux, exposed by the kernel driver. In **Settings › Mixers and sound cards…** the search finds
the connected cards:
- **Focusrite Scarlett / Clarett** (“scarlett2” driver, kernel 6.7 or later; Gen 2/3 with kernels between 5.14
  and 6.6: `echo "options snd_usb_audio device_setup=1" | sudo tee /etc/modprobe.d/scarlett.conf` and reboot):
  inputs (Line/Inst, Pad, Air, 48V phantom, gain on Gen 4), direct monitor, output volume and mute (**Outputs**
  section: works with mute and fader cues, also with fades and “Read from the mixer”), speaker switching,
  talkback, internal mix matrix and routing;
- **any other card** with ALSA controls (“Sound card (ALSA controls)” driver).

On the bigger Scarletts (e.g. 18i20) the outputs may have their volume **on the card's knob (HW)**: in that case
volume and mute of those outputs are read-only and the cues say so. In the mixer panel the **“Output volume and
mute from CueStage (SW)…”** button switches them all to SW (or change the single “Line Out NN Volume Control”);
the Monitor knob no longer acts on them until they go back to HW. The internal matrix is split by mix (Mix A,
Mix B, …) and each row shows the source of the mixer input (e.g. “Mix A ← in 03: PCM 5”); big groups are built
when you open them. In the mixer panel (F10) the card shows its controls grouped (Inputs, Outputs and monitors,
Mix, Routing, Other): switches, menus and sliders act immediately; 48V phantom asks for confirmation. A Mixer cue
with the action **“Set a card control”** takes a control to a value (e.g. input 1 to Inst, speaker switching to
Alt, direct monitor off). It needs `amixer` (package **alsa-utils**). Control names depend on the model and on the
kernel version: CueStage reads them from the card.

### Ducking (microphone that lowers the music)

An audio input (e.g. the announcer's microphone) automatically lowers the cues while someone is speaking.

- **Settings › Ducking…** (also with a right-click on the “Duck” button or with “Settings…” in the inspector):
  - **input card** and **channel**;
  - input **meter** with the threshold (white line), the gate LED and how much reduction is applied;
  - **gate**: only the **threshold** (dBFS); above the threshold the ducking acts;
  - **attack** (0–1000 ms): the time to reach full reduction;
  - **hold** (0–5000 ms, default 1000): how long the reduction stays full after the signal has dropped below the
    threshold, so the volume does not come back up between words;
  - **release** (0–5000 ms): the time to return to full volume, after the hold;
  - **Master ducking**: reduction of the master output (default 0 dB = none);
  - **“Send the input, after the gate, to the preview”**: in the headphones you hear the microphone only when it is
    above the threshold (and during the hold), with the preview's card, channels and volume; useful to set the
    threshold. The delay is a few tens of milliseconds.

  Changes apply immediately (you see and hear the effect); “Cancel” restores the previous values. With the
  window open the input is listening even if ducking is off, so you can set the threshold.
- **Cue ducking**: in the inspector of audio and video cues (“Audio output” section), the dB by which the cue is
  lowered when the ducking acts (“none” = 0). On a **group** it applies to all its cues that have no ducking of
  their own. It only works with “Enable cue ducking” on.
- **“Duck” button** next to the master volume: turns on the Master ducking (lowers the whole output, audio and
  video audio, by the set reduction). Grey = off, green = on and listening, red = acting, amber = on but with no
  effect (0 dB reduction or input not available).
- **“DIM” button** under “Duck”: immediately lowers the master output by the Master ducking reduction and keeps
  it there until you turn it off (no microphone needed). Orange = on, amber = on but with 0 dB reduction. With
  Duck and DIM on together the reductions do not add up. It can also be controlled from **MIDI** (Settings ›
  MIDI › “DIM on / off” and “Duck on / off”) and from **OSC**: `/dim` toggles, `/dim 1` turns on, `/dim 0` turns
  off; the same for `/duck`. Right-click on Duck or DIM: ducking settings.
- **Microphone icon** in the progress bar (list, active cues, GRID tiles) of cues with ducking: **green** = ready,
  **red** = lowering the cue, grey = ducking off in the settings.
- Cue and master add up: a cue with 10 dB of ducking and the Master ducking at 6 dB goes down by 16 dB.
- **Mixer faders hooked to the ducking**: Mixer cue, action “Ducking: hook the faders to the ducking curve”
  (digital mixers and sound cards with faders). You choose section and channels, the **normal level** and the
  **reduction** in dB: while the cue is active the faders stay at the normal level and go down by the reduction
  when the ducking acts, following attack, hold and release (at most 25 updates per second). STOP unhooks the
  faders and brings them back to the normal level. The ducking input just has to be configured: “Enable cue
  ducking” is not needed.
- In redundancy each instance listens to its own input.

### Preview (headphones)

- **Settings › Audio, OSC and MIDI › Preview**: “Enable preview”, the **card** and the **2 channels** (left and
  right) of the operator's headphones. It can be another card or two free channels of the same one (“Project
  default card” = the one chosen at the top as default card).
- **PFL button**: the list has the **PFL** column (it appears only with the preview on) and in the GRID a **PFL**
  button inside the tile, at the top right. A click sends the audio or video cue or the **group** (with all its
  cues) to the headphones instead of the show outputs; another click brings it back to the outputs. Clicking the
  button does not start the cue and does not move the playhead. It can also be used while the cue is playing.
  Solid blue button = on; blue border = the cue is in preview because its group is; grey = off. The audio goes
  in stereo (mono on both sides; more channels alternating left/right).
- Next to the master volume appears the **preview volume** (double-click = 0 dB): the master volume and the
  Master ducking do not touch the preview.
- In the active cues panel the cues in preview carry the blue **PFL** label.
- Video with the “System player” cannot go to the headphones: in preview its audio is silent.

### Audio buffer and “underruns”

An *underrun* is a small gap in the audio: the card asked for the next block before the computer had it ready
(it happens mostly with video on the output or with a busy computer). In **Settings › Audio › Audio buffer** you
choose the size: *Low latency* 512 samples (~50 ms), *Normal* 1024 (~120 ms, default), *Safe* 2048 (~250 ms),
*Maximum safety* 4096 (~500 ms). A bigger buffer removes the gaps; in exchange a cue is heard a few tenths of a
second after GO. If there were gaps in the last 10 seconds, the status bar shows it in yellow with their number.
The “ALSA lib … underrun occurred” messages are no longer written to the terminal (CueStage counts them); to see
them again: `CUESTAGE_ALSA_MESSAGES=1 cuestage`. With PipeWire it is better to use the “pipewire” or “default”
card instead of direct access (hw:), which is more sensitive to delays.

## Markers

All the markers of a cue are visible everywhere, with the same colours: IN (green), OUT (red), loop (yellow, with
the repetitions), fade in/out (ramps and dashed lines), volume curve points (pink), image keyframes (orange ◆)
and audio keyframes (light blue ◆; hollow = “base”).

- **Waveform** (“Times, loop and audio” tab): IN/OUT, loop, fades, curve and also the keyframes, placed at the
  file position (a keyframe that falls in a loop repetition is marked as such).
- **Keyframe timeline** (“Times, loop and audio” and crop tabs), in cue time: “Markers” lane with IN/OUT, loop
  with its repetitions, fades and curve points, above the keyframe lanes; ◀ / ▶ arrows to move from one marker
  to another. The timeline cursor also appears on the waveform as a dashed line.
- **Progress bars** during playback (“Status” column and “Active cues” panel): real cue time with the loop
  unrolled into its repetitions, fade ramps, curve and keyframes; with an infinite loop the cursor runs inside
  the loop region. Hovering a marker shows its description.

## Network video projectors

**Settings › Projectors…**: list of projectors with name, driver, IP, port and credentials; “Test connection”
reads the state (power, shutter, input). **Projector** cues choose a projector and an action; commands run in one
thread per projector (in order, without blocking the show) and the result appears in the status bar. “Send now”
tries them from the inspector.

### “Projectors” panel (direct control)

If at least one projector is configured, the **📽 Projectors ▾** button appears at the top, next to the master
volume: it opens (and closes) a drop-down panel with a box for each projector, with **all the commands its
driver/plugin provides**:
- power on / off (power off asks for confirmation), close / open shutter, freeze, input;
- lens: zoom, focus, horizontal and vertical shift with speed and steps; **holding** a button keeps the movement
  going (repetitions do not pile up if the projector is slow to answer);
- lens memory, model-specific actions (e.g. “Lens: home position” on Panasonic), free command, **Read status**;
- with several projectors, the **All** row: close / open all shutters, read all states.

Under each box appears the result of the last command (green = done, red = error). Even with the panel in front,
Space = GO, Esc = STOP ALL, F7 and F8 stay active; it closes with ✕ or by pressing the button again. The panel also
opens and closes with **F9** or from **Settings › Projector panel** (with no projectors configured it opens their
configuration).

### 4-corner geometric correction

For projectors whose plugin supports it, the “Projectors” panel shows the **▸ 4-corner correction** section: for
each corner (top left, top right, bottom left, bottom right) an X and Y offset in projector units. Each change
(arrows, wheel, typing) is sent **immediately**, in real time: holding an arrow the projector follows the value; if
it is still busy with the previous command it directly receives the latest value (no queue, no growing delay). The
first send turns on the projector's “corner correction” mode; **Read** loads the current values, **Reset** brings
everything back to 0, **Send all** sends the 8 values again, **Disable** turns the correction off. “Values for
cue” contains the 8 numbers to paste into a Projector cue with the action **4-corner correction: set**, which
recalls them during the show (e.g. to switch from one set to another).

- **Panasonic PT-MZ880/MZ882**: `VXX:GMMI0=+00010` (geometry = CORNER CORRECTION), `VXX:GMFI1…4` vertical and
  `GMFI6…9` horizontal; pixels, 0…480 horizontally and 0…300 vertically towards the inside (values for the WUXGA
  models). Read with `QVX:GMFIn`.
- **Optoma ZU506T**: horizontal and vertical keystone (`~XX65` / `~XX66`, −40…40) as model actions; 4 corners with
  `~XX58` + index 1…8, 0…120 / 0…80 towards the inside. ⚠ The 4-corner syntax is not documented for the 506
  series (it comes from the documents of other Optoma ZU series): if the projector answers “F” the
  `CORNER_FORMAT` line of the plugin must be corrected.

Common actions: power on / off, shutter close / open, input, freeze, zoom, focus, horizontal and vertical shift
(direction, steps, speed), lens memory, free command, plus the specific actions of each driver. Each driver shows
only the actions the projector supports.

| Driver | Protocol | Supports |
|---|---|---|
| **PJLink** (built in, any brand) | TCP 4352, MD5 password | power, input (PJLink codes 11…59), shutter = AV mute, free command |
| **Panasonic PT-MZ880 / MZ882** (plugin) | TCP 1024, NTCONTROL with MD5 hash user:password | power, shutter (OSH), HDMI 1/2, DIGITAL LINK, COMPUTER inputs, freeze, zoom/focus/shift at 3 speeds, lens home position. No lens memory on these models |
| **Optoma ZU506T / ZH506 / ZW506** (plugin) | telnet TCP 23, `~ID…` commands | power, shutter = AV mute, inputs, freeze. **Manual** lens: zoom, focus and shift cannot be controlled |

**Plugins**: a `.py` file with a class derived from `ProjectorDriver` in `~/.local/share/cuestage/plugins/` adds a
driver (guide and example in `cuestage/plugins/LEGGIMI_PLUGIN.md`); “Reload plugins” loads it without
restarting. Faulty plugins are reported and ignored.

**To be checked on real projectors**: the drivers are written from the protocol documentation and tested with
simulators. Some codes are marked “to be checked” (Optoma inputs beyond HDMI 1/2 and VGA 1; mapping of the
Panasonic lens axes LNSI2…5): if the projector answers with an error, correct the table in the plugin file. To
control the projector while it is off, network standby must be enabled in its menu.

## Video output and 4-corner correction

**Video output** menu: choice of screen (by default the last one, i.e. the projector), full screen,
**4-corner geometry** (Ctrl+G): drag the corners in the editor or directly on the output window, with a test grid,
numeric values and keyboard nudge (1–4 = corner, arrows, Shift ×10). The correction is a perspective
transformation applied to all layers, saved in the show.

### Preloaded videos (immediate start and loops without waiting)

Opening a big video (and jumping to the IN point) can take from a few tenths of a second to a few seconds. With
**“Preload”** (video cue inspector, next to the loop; on by default) CueStage keeps the video **ready in
advance**: file open and paused on the first frame to show. On GO it starts immediately.

- The videos kept ready are those **near the playhead** of each list (the standby cue and the next 3), then
  those of the **visible GRID page**, then the others, up to **8** videos together. They are opened one at a
  time, so as not to slow down the cues that are playing.
- In **loops** a second player waits, paused at the loop start: at the repetition it switches from one to the
  other without seeking in the file, then the first one repositions itself for the next repetition. It applies to
  loops of at least 1 second.
- Each ready video takes a decoder and some memory: with many 4K videos turn “Preload” off for those that do not
  need it, or turn it all off from **Video output › Preload videos**.
- The video's audio (CueStage engine) is already decoded in memory when the show is opened.

## Digital mixers

**Settings › Mixers…**: list of mixers, with **🔍 Search for mixers on the network**, which finds on its own:
- **Behringer X32 / Midas M32** — OSC on UDP 10023 (answer to “/xinfo”: name, model, firmware);
- **Behringer WING** — “WING?” on UDP 2222, OSC commands on 2223;
- **Behringer X Air XR12 / XR16 / XR18, Midas MR18** — “/xinfo” on UDP 10024 (the model too);
- **Soundcraft Ui12 / Ui16** — scan of port 80 and recognition of the mixer's web page;
- **Allen & Heath Qu and SQ** — MIDI over TCP 51325 (scan of the /24 network and recognition of model and MIDI
  channel).

A mixer found is added with a double-click. “Test connection” checks the link.

| Mixer | Connection | Mute | Fader |
|---|---|---|---|
| X32 / M32 | OSC UDP 10023 | 32 channels, 8 aux in, 8 FX returns, 16 mix buses (aux), 6 matrices, master, 8 DCAs, 6 mute groups | all except mute groups |
| WING | OSC UDP 2223 | 40 channels, 8 aux in, 16 buses, 4 mains, 8 matrices, 16 DCAs, 8 mute groups | all except mute groups |
| X Air XR18 / MR18, XR16, XR12 | OSC UDP 10024 | 16 / 14 / 10 channels, aux in, 4 FX returns, 6 buses, 4 FX sends, LR, 4 DCAs, 4 mute groups | all except mute groups |
| Soundcraft Ui16 / Ui12 | WebSocket port 80 | 12 / 8 inputs, line in 1–2, USB player 1–2, FX 1–4, 4 / 2 aux, master (= MUTE ALL), 6 mute groups | all except mute groups |
| Qu-16/24/32, Pac, SB | MIDI TCP 51325 | inputs, ST 1–3, FX returns and sends, 7 mixes, LR, groups and matrices (not Qu-16), 4 DCAs, 4 mute groups | inputs, ST, FX returns, FX sends, mixes, LR, groups, matrices, DCAs |
| SQ-5/6/7 | MIDI TCP 51325 | 48 inputs, 12 groups, 8 FX returns, 12 aux, 4 FX sends, 3 matrices, LR, 8 DCAs, 8 mute groups | inputs and groups (send to LR), aux, FX sends, matrices, LR, DCAs |
| Yamaha 01V96 (i, VCM) | MIDI (SysEx Parameter Change) | 32 channels, 8 aux, 8 buses, stereo | the same |
| Yamaha 02R96 | MIDI, **to be checked** | 56 channels, 8 aux, 8 buses, stereo | the same |
| Yamaha 01V (original) | MIDI Control Change (initial table) | 12 channels, 2 stereo (13-14, 15-16), 2 returns, 4 aux, 4 buses, 2 effects, ST OUT | the same |
| Yamaha 02R (original) | MIDI Control Change (“02R” map) | 16 MIC, 4 stereo LINE, 2 EFF, ST MASTER | the same + aux 1–8 and bus 1–8 (faders only) |

Notes: **X Air** and **Soundcraft Ui** follow protocols that are not officially documented (community OSC table for
X Air; open source library “soundcraft-ui-connection” for Ui): the number of channels per model comes from the
specifications and must be checked, like the commands, before the show. On the **Qu** and the **SQ** the mixer's
MIDI channel must be set (the search detects it); on the SQ the “NRPN Fader Law” must match the mixer's
(recommended *linear*, exact conversion). The **Yamaha** mixers are connected through a MIDI interface: “MIDI
output → mixer” to control and “MIDI input ← mixer” to read the levels; on the mixer “Parameter Change” Rx (and Tx)
ON. For the 02R96 the messages are those of the 01V96 family but they have not been checked on a 02R96: try them
before the show. The old **01V** and **02R** are controlled with the Control Changes of the factory table (Control
Change Rx ON on the given MIDI channel): if the table on the mixer has been changed, it must be corrected in the
`yamaha_vintage.py` plugin. The 01V transmits the Control Changes when a fader moves (Tx ON): “Read from the mixer”
uses the last value received, so move the fader on the mixer first. The 02R does not transmit: set the level in
the cue. On these models the dB values are approximate (128-step scale).

### “Mixer” panel (mutes)

With at least one mixer configured the **≣ Mixer ▾** button (or F10) appears at the top: a panel with a tab per
mixer and, for each section, the mute buttons (green = on, red = muted, grey = state not read yet). A click sends
the mute; **Read** (per section) and **Read all** read the state from the mixer.

### “Mixer” cue

- **Mute** (scene): all the sections of the mixer (channels, aux, groups, effects, matrices, master, DCAs, mute
  groups) with a box per element: **red** = force mute, **green** = force on, **grey** = do not touch. Click:
  grey → red → green → grey; right-click: grey; Shift+click: same state on a range. Per section: “all mute”,
  “all on”, “do not touch”, **⟲ Read** (copies the current state of the mixer); **⟲ Read all from the mixer**
  records the whole scene. Cues of previous versions (mute/unmute on section + channels) are converted when
  opened;
- **Fader**: channels chosen with the numbered buttons of the section (click, Shift+click, “All” / “None”): takes a
  fader (channel, aux, group, matrix, master, DCA…) to a level, with an optional **fade** (25 steps per second,
  S curve; it reads the current position first). **⟲ Read from the mixer** records in the cue the level present on
  the mixer at that moment: set the fader on the mixer, press Read, and the cue will bring back that level. STOP
  stops a fade in progress.

**Channel names**: with the mixers that provide them (Behringer X32 / Midas M32, WING, X Air, Soundcraft Ui) the
names are read automatically the first time the mixer panel or a mixer cue is opened, and appear under the number
in the panel buttons, in the cue boxes and in the list (fader cues on a single channel). They are saved in the
show, so they stay visible with the mixer off too; they are read again with “⟲ Channel names” in the cue or with
“Read all” in the panel. Allen & Heath Qu/SQ and Yamaha over MIDI do not provide names. The commands of each mixer
go through a dedicated queue (in order, without blocking the show); the result appears in the status bar. New
mixers are added with a plugin (see `cuestage/mixer_plugins/LEGGIMI.md`).

## Secondary instances: master, slave and redundancy

**On the master** (Settings › Audio, OSC and MIDI › **Instances**): “Enable secondary instances”, TCP port
(default 53900; the next port, UDP, is used for discovery) and instance name. Each connection request must be
**accepted** in a confirmation window.

**On the other computer**: Settings › **Become slave…**
- search for masters on the network (or address and port typed by hand);
- **Slave** mode (always up-to-date copy of the project, does not run the master's cues) or **Redundancy** (runs
  exactly the same cues triggered by the master);
- **differential** contents (downloads the missing files and updates the different ones, compared by size and
  SHA-1 fingerprint of the start and end of the file) or **full** (downloads everything again);
- **copy all project settings** (sound cards, mixers, projectors, MIDI, OSC…; only the instance and web interface
  settings stay local);
- local folder: the files go there (same folder structure as on the master; files outside the show folder in
  `_esterni/`) together with the copy of the show, which stays usable without the master too.

After synchronisation CueStage **reports hardware that differs** from the master's (missing sound cards or cards
with fewer channels, mixers configured differently or ALSA cards not connected, missing MIDI outputs) and colours
in **red** the cues that use it (the reason is in the row's tooltip).

Every **project change** on the master reaches the secondaries immediately (checked every 200 ms), with the new
files downloaded automatically. In **redundancy** the master relays every command: GO (local and PANEL ones too),
start/stop/pause of single cues, STOP ALL, playhead, crossfade and crossfader, live levels and opacity, texts and
leaving groups; random groups use the same seed, so they choose the same cues. On the redundant instance local
commands are blocked (the emergency STOP ALL remains) and the editor is read-only. Commands to network mixers and
projectors leave from both instances (they are idempotent: same mute, same level).

**Secondaries can update the project** (option on the master, Settings › Instances): with the option on the
secondaries' editor is unlocked and each of their changes (cues, lists, settings if copied) reaches the master
within about half a second and from there all the other secondaries. New files added on a secondary are sent to
the master and saved in the `da_slave` folder next to the show (never overwriting existing files). On the master
the show is “modified” until you save it. If two instances change things at the same moment, the last change that
arrives wins. Without the option the secondaries' editor stays locked; in the indicator at the bottom “✎” shows
that editing is allowed.

**Heartbeat**: the master sends one every 100 ms. If a secondary does not hear it for the chosen time (default
500 ms) or the connection drops, the status bar turns red: a click (or Settings › “Leave slave mode”) **promotes**
it: it becomes independent, the running cues continue, and if it has secondary instances enabled it becomes a
master in turn. With “promote automatically” it does so on its own. At the bottom right the indicator shows
“◉ MASTER · n secondaries” or “◎ REDUNDANCY of …”. Note: the connection is not encrypted and the only protection is
the confirmation on the master: use it on the show network.

## Web interface

**Settings › Audio, OSC and MIDI… › “Web interface” tab**: starts a small web server on the chosen port (default
8080). The server answers on **all the computer's network addresses** (cable, Wi-Fi, USB…): the tab lists them all
with the name of their interface, and the drop-down chooses the one for the **QR code** to frame with the phone
camera (the one of the network the phone is connected to); the other addresses are shown under the QR code, and
when the server starts the status bar lists them all. With “Include the PIN in the QR code” whoever frames it gets
in without typing the PIN. The QR is generated by CueStage itself (no extra library). The page shows:
- **GO** (follows “GO on”), the waiting cue with its list, **STOP**, **Pause / Resume**;
- the MAIN and PANEL tabs; tapping a cue puts it in standby in its list; in the PANELs, with “GO on: MAIN”,
  there is the PANEL's **local GO** with its **STOP**;
- the **active cues** with list, progress, elapsed/remaining time, pause and stop per cue;
- the **📽 Projectors** tab (if there are projectors configured and the “Show projector control in the web
  interface” option is on): for each projector the same commands as the “Projectors” panel — power (power off
  asks for confirmation), shutter, freeze, input, lens (holding the movement continues, without piling up
  commands), lens memory, model actions, free command, status — and the real-time **4-corner correction** (− / +
  held down, or the typed value; Read, Reset, Send all, Disable).

It refreshes 3-4 times per second. With a **PIN** set the browser asks for it the first time and remembers it.
Without a PIN anyone on the network can control the show: set one on shared networks. The server does not use
HTTPS: it is meant for the show's local network, not for the Internet.

## Profile-based OSC devices (e.g. ChamSys MagicQ)

In **Settings › OSC › OSC devices** you add devices starting from a **profile**: choose the profile, then type the
IP address or use **🔍 Search the network** (for the MagicQ the search uses Art-Net/ArtPoll and puts the consoles
recognised as MagicQ/ChamSys at the top); the OSC port is already the profile's. In an **OSC** cue you choose the
device and the command, with its parameters; below you see the message that will be sent. “Manual” remains
available to type address and arguments by hand.

**ChamSys MagicQ** profile (port 8000; on the console: Setup › View Settings › Network › OSC, console unlocked; not
on MQ40/MQ40N):

| Command | Message |
|---|---|
| GO of a playback | `/pb/N/go 1` |
| GOTO CUE of a playback | `/pb/N/CUE 1` (e.g. `/pb/2/12.5`) |
| Exec (activate / release) | `/exec/PAGE/ITEM 1` · `0` |
| Exec: fader level | `/exec/PAGE/ITEM 0…100` |
| DBO | `/dbo 0` turns blackout on · `/dbo 1` turns it off |
| FLASH key of a playback | `/pb/N/flash 1` pressed · `0` released |
| SWAP state | `/swap 1` swap · `0` add (global in the MagicQ, applies to all flashes) |
| Level, PAUSE, RELEASE of a playback | `/pb/N 0…100`, `/pb/N/pause 1`, `/pb/N/release 1` |
| Remote command | `/rpc "…"` |

Over OSC the MagicQ accepts playbacks 1–10. New profiles: JSON files in `~/.local/share/cuestage/osc_profiles/`
(same format as `cuestage/osc_profiles/chamsys_magicq.json`: addresses and arguments with the parameters in braces,
int/float/enum/text parameters, optional search `{"type": "artnet", "match": [...]}`).

## OSC and MIDI

OSC input on UDP **53000** (configurable): `/go`, `/stop`, `/panic`, `/pause`, `/resume`, `/next`, `/previous`
(on the GO list), `/playhead/<n>`, `/list/<name>`, `/list/<name>/go`, `/cue/<n>/start|stop|pause|resume`,
`/cue/<n>/level <dB> [sec]`, plus a custom address for each cue. Full list in *Help → Commands*.

MIDI input (Settings): map of GO / Stop / Pause / Resume / Next / Previous and a trigger for each cue, in the format
`note 1 60`, `cc 1 20`, `pc 1 5` (channel 0 = any), with a **Learn** button (in the settings it listens to the port
chosen in the dialog even before pressing OK). The map also includes **Crossfade to the next**.

### Several MIDI devices

In Settings › MIDI › “MIDI input devices and filters” you tick **all the MIDI devices** to use: they are opened
together (ticked devices that are disconnected stay in the list as “not connected” and reopen when you restart
CueStage with the device connected). Each command — GO, Stop, the faders, the crossfader, the trigger and the
faders of each cue — can state **which device** it must come from, after “@”: `note 1 60 @ Akai APC mini`,
`cc 1 7 @ nanoKONTROL2`. “Learn” adds by itself the device the message comes from (untick “Only from the device it
comes from” to accept it from all). Without “@” the command applies from any device. So the same note or the same
CC on two different controllers can do different things. The device name is saved without the ALSA numbers (e.g.
“20:0”), which may change at every start: the device is recognised anyway.

### MIDI filters per device

Selecting a device in the list shows its **filters** on the right, to discard unwanted messages (e.g. an audio
interface or a keyboard that send continuous CCs, clock, sustain…):
- **Accept**: Note, Control change, Program change, Pitch bend (untick those not needed);
- **Channels**: “all” (empty) or a list, e.g. `1, 10, 13-16`;
- **Ignore CC** / **Ignore notes**: numbers or ranges to discard, e.g. `1, 64, 120-127`;
- **Exclude from “Learn”**: the device still controls the show, but its messages are not captured by “Learn” —
  useful when a device sends messages all the time.

Discarded messages reach neither the commands nor “Learn”. Changed filters apply immediately to “Learn” in the
same dialog too, before pressing OK, and are saved with the show. Devices without filters accept everything.

### MIDI faders and knobs (continuous controls)

Settings › MIDI › “continuous controls”: you assign a control that sends a **value** — control change (`cc 1 7`),
pitch bend of motorised faders (`pb 1`, 14 bit), or a program change used as a value (`pc 1 *`, the program number
0…127) — with **Learn** (move the fader):
- **Volume of the selected cue** — the cue selected in the list: if it is playing the volume changes live (the
  programmed level does not change), if it is stopped the programmed level is set. Fader law: bottom −∞, ~70% of
  the travel 0 dB, top +6 dB;
- **Transparency of the selected cue** (video and images): 0…100%, live or on the programmed value;
- **Crossfader**: see “Crossfade”.

Each cue can also have its **own** controls: “Basic” tab › **MIDI volume fader** (audio, video) and **MIDI
transparency fader** (video, images), with Learn and ✕ to remove them. They apply even if the cue is not selected.
The fader is “absolute”: on the first movement the value jumps to the fader position.

### Crossfade between the current cue and the next

- **Automatic**: Playback › **Crossfade to the next** (F6, ⇄ X-fade button of the web page, MIDI, OSC
  `/xfade [seconds]`). The waiting cue (with its “with previous” cues) starts from zero and rises while the current
  cue goes down and stops at the end; duration in Settings › Audio › “Crossfade time” (default 3 s). The waiting
  cue's delay (pre-wait) does not apply.
- **Manual**: a MIDI fader assigned to **Crossfader** (or OSC `/xfader 0…1`). Moving it away from the resting side
  starts the waiting cue, and the position decides the transition; at the end the current cue stops and the
  resting side is swapped (the next crossfade is done by bringing the fader back, like on a video mixer); bringing
  it back to the start before the end cancels the crossfade and the waiting cue goes back to standby.

“Current cue” = the audio/video/image cues of the GO list started last together (e.g. a video and its music). Audio
passes at constant power (no dip half way). Images dissolve without black: the one on top (highest layer) fades,
the one below stays full until the end.

## Keyboard

Space GO · Alt+←/→ previous/next text line · F6 Crossfade to the next · Ctrl+Shift+G group · Ctrl+E expand/collapse
group · F10 mixer panel · Esc Stop all · F7 Pause · F8 Resume · F9 projector panel · Ctrl+T test cue · Ctrl+D
duplicate · Del delete · Ctrl+↑/↓ move · Ctrl+G geometry · Ctrl+S save · Ctrl+Z undo · Ctrl+Shift+Z / Ctrl+Y redo ·
Ctrl+H history · Ctrl+Alt+V show versions. Files and folders can be dragged from the file manager into the list and
cues can be reordered with the mouse.

## Technical notes

- Code structure (`cuestage/`):
  - `model.py` (show, cues, lists, versions), `engine.py` (cue playback), `audio_engine.py` (audio mixer on
    PortAudio), `media.py` (ffmpeg/ffprobe, waveforms), `video_output.py`, `video_deck.py` (video preload),
    `text_render.py`, `history.py` (undo/redo), `i18n.py` + `lang/` (languages);
  - `mixers.py` + `mixer_plugins/` (digital mixers and ALSA sound cards, with `alsactl.py`), `projectors.py` +
    `plugins/` (video projectors), `osc_profiles.py` + `osc_profiles/` (OSC devices), `remote.py` (OSC/MIDI input),
    `webserver.py` (web interface), `cluster.py` (master/slave), `ducking.py`;
  - `main_window.py` is the core of the main window; its functions are split by area in `window/` (playback,
    editing, groups, lists, hardware, instances, OSC/MIDI, web, video, files, history and versions);
  - `ui_inspector.py` is the core of the inspector; its tabs are in `inspector/` (media, text, OSC/MIDI control,
    mixer, projectors, presets); the other windows are in the `ui_*.py` files.
- The differences between operating systems are gathered in `system.py` (data folders, ffmpeg bundled in the
  package, portable paths in shows and in master/slave synchronisation). A mixer plugin can restrict itself to some
  systems with `PLATFORMS = ("linux",)`.
- Packages: `packaging/build_appimage.sh` (Linux), `packaging/build_windows.ps1` and `packaging/build_macos.sh`
  (PyInstaller with `packaging/cuestage.spec`), `.github/workflows/build.yml` (all three on GitHub). On Windows and
  macOS without a console the messages go to `cuestage.log` in the data folder.
- Quick regression tests (no sound card, with a simulated X32 mixer and two instances on the local network):
  `python3 tests/test_smoke.py`. Each package also has `--self-test`.
- Bundled ffmpeg: BtbN LGPL builds (Linux, Windows) and zackees/ffmpeg_bins static builds (macOS); CueStage uses it
  as an external program.
- The show is a `.cueshow` JSON file; paths are also saved relative to the show, so the folder can be moved to
  another computer.
- Audio (video audio too) is decoded into memory in advance, at 16 bit with the original channels (instant start,
  gapless loops, sample-accurate fades and curves): about 11 MB per minute for a stereo file.
- Video loops without “Preload” use FFmpeg seeking: there may be a small discontinuity at the loop point.
- If OpenGL is not available the output automatically switches to software rendering.
