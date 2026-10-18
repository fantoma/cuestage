"""Lista delle cue (modello tabellare con drag&drop) e pannello delle cue attive."""
import json

from PySide6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QWidget

from .media import fmt_time
from .model import TRIGGER_SHORT, TYPE_ICONS, TYPE_LABELS

COLS = ["", "#", "Tipo", "Nome", "File / Target", "Ritardo", "Durata", "Avvio", "Stato"]
C_PLAYHEAD, C_NUM, C_TYPE, C_NAME, C_TARGET, C_DELAY, C_DUR, C_TRIG, C_STATE = range(9)
ROW_MIME = "application/x-cuestage-rows"


def cue_duration(cue, loader):
    t = cue.type
    if t in ("audio", "video"):
        d = loader.duration(cue.file) if cue.file else 0.0
        if not d:
            return None
        end = cue.end if cue.end > 0 else d
        base = max(0.0, end - cue.start)
        if cue.loop:
            if not cue.loop_count:
                return float("inf")
            ls = cue.loop_start if cue.loop_start > 0 else cue.start
            le = cue.loop_end if cue.loop_end > 0 else end
            base += (int(cue.loop_count) - 1) * max(0.0, le - ls)
        return base
    if t == "image":
        return float(cue.duration) if cue.duration else float("inf")
    if t in ("fade", "preset"):
        return float(cue.fade_duration)
    if t == "wait":
        return float(cue.duration)
    return 0.0


class CueTableModel(QAbstractTableModel):
    filesDropped = Signal(list, int)
    rowsMoved = Signal(list, int)
    edited = Signal()

    def __init__(self, show, ctrl, loader):
        super().__init__()
        self.show = show
        self.ctrl = ctrl
        self.loader = loader

    hw_bad = {}          # id cue -> motivo (istanza secondaria con hardware diverso dal master)

    def set_show(self, show):
        self.beginResetModel()
        self.show = show
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.show.cues)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[s]
        return None

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        cue = self.show.cues[idx.row()]
        col = idx.column()
        if role == Qt.DisplayRole or role == Qt.EditRole:
            if col == C_PLAYHEAD:
                return "▶" if idx.row() == self.ctrl.playhead else ""
            if col == C_NUM:
                return cue.number
            if col == C_TYPE:
                return f"{TYPE_ICONS.get(cue.type, '')} {TYPE_LABELS.get(cue.type, cue.type)}"
            if col == C_NAME:
                if role == Qt.EditRole:
                    return cue.name
                if cue.type == "group":
                    from .groups import GROUP_SHORT, block_end
                    lst = self.show.current
                    i = idx.row()
                    n = block_end(lst.cues, i) - i - 1
                    arrow = "▸" if cue.collapsed else "▾"
                    return f"{arrow} {cue.name or 'Gruppo'}   [{GROUP_SHORT.get(cue.group_mode, cue.group_mode)} · {n} cue]"
                if cue.group:
                    return f"      ↳ {cue.name}"
                return cue.name
            if col == C_TARGET:
                if cue.is_media():
                    return cue.file.rsplit("/", 1)[-1] if cue.file else "— nessun file —"
                if cue.type in ("fade", "control", "preset"):
                    if cue.type == "control" and cue.action == "stop_all":
                        return "tutte"
                    tg = self.show.resolve_target(cue.target)
                    return f"→ {tg.label()}" if tg else (f"→ ? ({cue.target})" if cue.target else "— nessun target —")
                if cue.type == "group":
                    m = cue.group_mode
                    from .engine import fmt_div, tap_active
                    if tap_active(cue):
                        return (f"♩ {float(cue.group_bpm):g} BPM · una cue ogni {fmt_div(cue.group_tap_div)} "
                                f"battit{'o' if float(cue.group_tap_div) <= 1 else 'i'}"
                                + (" · casuale" if m == "rloop" else " · col GO a tempo" if m == "sequence" else ""))
                    if m in ("loop", "rloop"):
                        hold = f"{float(cue.group_hold):g} s" if float(cue.group_hold or 0) > 0 else "durata della cue"
                        adv = "avanza col GO · ESC esce" if (m == "rloop" and cue.group_advance == "go") else f"ogni {hold}"
                        return f"{adv} · crossfade {float(cue.group_xfade):g} s"
                    if m in ("playlist", "rplaylist"):
                        xf = float(cue.group_xfade or 0)
                        how = "in ordine casuale, " if m == "rplaylist" else ""
                        return (f"{how}una dopo l'altra" + (" · in loop" if cue.group_loop else "")
                                + (f" · crossfade {xf:g} s" if xf > 0 else ""))
                    return {"sequence": "prima cue col GO, poi in sequenza", "random": "una cue a caso",
                            "all": "tutte insieme (con i loro pre-wait)"}.get(m, "")
                if cue.type == "text":
                    from .text_render import split_phrases
                    if cue.text_mode == "sequence":
                        ps = split_phrases(cue.text_content)
                        first = ps[0].replace("\n", " / ") if ps else "— nessun testo —"
                        return f"T {len(ps)} frasi · «{first[:50]}»"
                    t = (cue.text_content or "").replace("\n", " / ")
                    return f"T «{t[:60]}»" if t else "— nessun testo —"
                if cue.type == "midiout":
                    lines = [l.strip() for l in (cue.mo_msgs or "").splitlines() if l.strip() and not l.strip().startswith("#")]
                    return (f"♫ {cue.mo_port or 'predefinita'}: " + " · ".join(lines[:3]) + (" …" if len(lines) > 3 else "")) if lines else "— nessun messaggio —"
                if cue.type == "stoppanel":
                    from .engine import stop_panel_targets
                    fade = float(cue.stop_fade or 0)
                    tail = f" · fade {fade:g} s" if fade > 0 else ""
                    if "*" in (cue.sp_panels or []):
                        pre = f"«{self.show.main.name}» + " if self.show.main.id in cue.sp_panels else ""
                        return f"⊠ {pre}tutti i PANEL" + tail
                    names = [f"«{lst.name}»" for lst in stop_panel_targets(self.show, cue)]
                    return ("⊠ " + ", ".join(names) + tail) if names else "— nessuna lista scelta —"
                if cue.type == "groupexit":
                    tg = self.show.resolve_target(cue.target) if cue.target else self.show.by_id(cue.group)
                    return f"⇥ esce da «{tg.label()}»" if tg else "— nessun gruppo —"
                if cue.type == "mixer":
                    from .mixers import KIND_SHORT, fmt_db
                    mm = getattr(self.ctrl, "mixers", None)
                    conf = mm.find(cue.mix_target) if mm else None
                    who = (conf.get("name") or conf.get("host")) if conf else "— nessun mixer —"
                    if cue.mix_action == "control":
                        info = mm.control_info(cue.mix_target, cue.mix_ctl) if mm else None
                        if info:
                            from .ui_mixers import _ctl_text
                            val = _ctl_text(info, int(cue.mix_ctl_value or 0))
                        else:
                            val = str(cue.mix_ctl_value)
                        from .alsactl import pretty_name
                        return f"{who}: {pretty_name(cue.mix_ctl) if cue.mix_ctl else '— nessun controllo —'} = {val}"
                    if cue.mix_action in ("mute", "unmute"):
                        from .mixers import mute_states, mute_summary
                        return f"{who}: {mute_summary(mute_states(cue))}"
                    what = None
                    if cue.mix_action == "duck":
                        what = f"{fmt_db(float(cue.mix_level))} ⇣ ducking −{float(cue.mix_duck_db or 0):.1f} dB"
                    if what is None:
                        what = fmt_db(float(cue.mix_level)) + (f" in {float(cue.mix_fade):.1f} s" if cue.mix_fade else "")
                    from .mixers import parse_numbers
                    nums = parse_numbers(cue.mix_channels) or []
                    nm = mm.channel_name(cue.mix_target, cue.mix_kind, nums[0]) if mm and len(nums) == 1 else ""
                    return (f"{who}: {KIND_SHORT.get(cue.mix_kind, cue.mix_kind)} {cue.mix_channels}"
                            + (f" «{nm}»" if nm else "") + f" {what}")
                if cue.type == "projector":
                    pm = getattr(self.ctrl, "projectors", None)
                    conf = pm.find(cue.proj_target) if pm else None
                    from .projectors import ACTIONS
                    act = ACTIONS.get(cue.proj_action, cue.proj_action)
                    drv = pm.registry.get(conf.get("driver")) if conf is not None else None
                    if drv is not None:
                        acts = dict(drv.actions())
                        act = acts.get(cue.proj_action) or f"⚠ {act} (non supportata)"
                    elif conf is not None:
                        act = f"⚠ driver «{conf.get('driver')}» mancante"
                    who = conf.get("name") if conf else "— nessun proiettore —"
                    extra = ""
                    if cue.proj_action == "input" or cue.proj_action == "raw":
                        extra = f" {cue.proj_value}"
                    elif cue.proj_action in ("zoom", "focus", "shift_h", "shift_v"):
                        extra = f" {'+' if int(cue.proj_dir) >= 0 else '−'}{cue.proj_steps}"
                    return f"⌖ {who}: {act}{extra}"
                if cue.type == "osc":
                    if cue.osc_device:
                        from .osc_profiles import describe
                        dev = next((d for d in self.show.settings.get("osc_devices") or []
                                    if d.get("id") == cue.osc_device), None)
                        who = dev.get("name") if dev else "— dispositivo mancante —"
                        return f"{who}: {describe(dev.get('profile') if dev else '', cue.osc_cmd, cue.osc_params)}"
                    return f"{cue.osc_host}:{cue.osc_port} {cue.osc_address} {cue.osc_args}".strip()
                return ""
            if col == C_DELAY:
                return fmt_time(cue.delay) if cue.delay else ""
            if col == C_DUR:
                d = cue_duration(cue, self.loader)
                if d is None:
                    return "…" if cue.file else ""
                return fmt_time(d) if d else ""
            if col == C_TRIG:
                return TRIGGER_SHORT.get(cue.trigger, cue.trigger)
            if col == C_STATE:
                return ""
        if role == Qt.FontRole and cue.type == "group" and col in (C_TYPE, C_NAME):
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ToolTipRole and cue.type == "group" and col in (C_TYPE, C_NAME):
            return "Clic sulla colonna «Tipo» (o Ctrl+E): espandi / compatta il gruppo"
        if role == Qt.FontRole and col in (C_PLAYHEAD, C_NUM):
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ForegroundRole:
            if col == C_PLAYHEAD:
                return QColor(80, 220, 120)
            if col == C_TRIG and cue.trigger != "manual":
                return QColor(240, 190, 60)
            if col == C_TARGET and cue.is_media() and not cue.file:
                return QColor(230, 90, 90)
        if role == Qt.BackgroundRole and cue.id in self.hw_bad and self.ctrl.cue_state(cue.id) is None:
            return QColor(120, 28, 28)          # hardware diverso da quello del master
        if role == Qt.ToolTipRole and cue.id in self.hw_bad:
            return "⚠ " + self.hw_bad[cue.id]
        if role == Qt.BackgroundRole:
            st = self.ctrl.cue_state(cue.id)
            if cue.type == "group" and st is None and idx.row() != self.ctrl.playhead:
                return QColor(52, 44, 78)        # riga del gruppo
            if st is not None and not isinstance(st, float):
                return QColor(30, 70, 45)
            if isinstance(st, float):
                return QColor(70, 60, 25)
            if idx.row() == self.ctrl.playhead:
                return QColor(32, 52, 78)   # cue in standby (prossimo GO)
            if cue.color:                   # colore scelto dall'utente (anche casella del GRID)
                c = QColor(cue.color)
                c.setAlpha(110 if col == C_NUM else 60)
                return c
        if role == Qt.TextAlignmentRole and col in (C_DELAY, C_DUR):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and col == C_TARGET and cue.file:
            return cue.file
        if role == Qt.ToolTipRole and cue.notes:
            return cue.notes
        return None

    def setData(self, idx, value, role=Qt.EditRole):
        if role != Qt.EditRole or not idx.isValid():
            return False
        cue = self.show.cues[idx.row()]
        if idx.column() == C_NUM:
            cue.number = str(value).strip()
        elif idx.column() == C_NAME:
            cue.name = str(value)
        else:
            return False
        self.show.dirty = True
        self.dataChanged.emit(idx, idx)
        self.edited.emit()
        return True

    def flags(self, idx):
        base = Qt.ItemIsEnabled | Qt.ItemIsDropEnabled
        if not idx.isValid():
            return base
        f = base | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
        if idx.column() in (C_NUM, C_NAME):
            f |= Qt.ItemIsEditable
        return f

    def refresh(self, rows=None, cols=None):
        n = len(self.show.cues)
        if n == 0:
            return
        self.dataChanged.emit(self.index(0, 0), self.index(n - 1, len(COLS) - 1))

    def refresh_state(self):
        n = len(self.show.cues)
        if n:
            self.dataChanged.emit(self.index(0, C_STATE), self.index(n - 1, C_STATE))

    # ---------------------------------------------------- drag & drop
    def supportedDropActions(self):
        return Qt.MoveAction | Qt.CopyAction

    def supportedDragActions(self):
        return Qt.MoveAction

    def mimeTypes(self):
        return [ROW_MIME, "text/uri-list"]

    def mimeData(self, indexes):
        rows = sorted({i.row() for i in indexes})
        md = QMimeData()
        md.setData(ROW_MIME, json.dumps(rows).encode())
        return md

    def canDropMimeData(self, data, action, row, col, parent):
        return data.hasFormat(ROW_MIME) or data.hasUrls()

    def dropMimeData(self, data, action, row, col, parent):
        if row < 0:
            row = parent.row() if parent.isValid() else len(self.show.cues)
            cues = self.show.cues
            if parent.isValid() and 0 <= row < len(cues) and cues[row].type == "group":
                # rilasciate SOPRA la riga di un gruppo: entrano nel gruppo, in fondo
                from .groups import block_end
                row = block_end(cues, row)
        if data.hasFormat(ROW_MIME):
            rows = json.loads(bytes(data.data(ROW_MIME)).decode())
            self.rowsMoved.emit(rows, row)
            return False  # gestito da noi: la vista non deve rimuovere righe
        if data.hasUrls():
            paths = [u.toLocalFile() for u in data.urls() if u.isLocalFile()]
            if paths:
                self.filesDropped.emit(paths, row)
            return False
        return False


def _marker_bar_data(ctrl, cue, inst):
    """(asse, marcatori, posizione) per una cue in riproduzione, oppure None se non ha una timeline."""
    from .markers import cue_axis, cue_marks, display_pos
    if cue.type not in ("audio", "video", "image"):
        return None
    dur = ctrl.loader.duration(cue.file) if cue.file and cue.type != "image" else 0
    if cue.type == "video" and not dur:
        dur = getattr(inst, "file_dur", 0)
    ax = cue_axis(cue, dur)
    if ax is None:
        return None
    t = inst.cue_time() if hasattr(inst, "cue_time") else inst.elapsed
    return ax, cue_marks(cue, ax), display_pos(ax, t)


def _bar_text(inst):
    tot = inst.total()
    if tot:
        return f"{fmt_time(inst.elapsed)} / -{fmt_time(max(0.0, tot - inst.elapsed))}"
    return f"{fmt_time(inst.elapsed)}  ∞"


class StateDelegate(QStyledItemDelegate):
    """Disegna l'avanzamento nella colonna 'Stato'."""

    def __init__(self, ctrl, show_fn, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.show_fn = show_fn

    def paint(self, p, opt, idx):
        if opt.state & QStyle.State_Selected:
            p.fillRect(opt.rect, opt.palette.highlight())
        cue = self.show_fn().cues[idx.row()]
        st = self.ctrl.cue_state(cue.id)
        r = QRectF(opt.rect).adjusted(3, 4, -3, -4)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if isinstance(st, float):
            p.setPen(QColor(240, 200, 80))
            p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft, f"⧗ {fmt_time(st)}")
        elif st is not None and _marker_bar_data(self.ctrl, cue, st) is not None:
            from .markers import paint_bar
            ax, marks, pos = _marker_bar_data(self.ctrl, cue, st)
            paused = st.paused or self.ctrl.paused_all
            col = QColor(230, 170, 40) if paused else QColor(60, 200, 110)
            f = QFont(opt.font)
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85))
            p.setFont(f)
            paint_bar(p, r, ax, marks, pos, col, ("⏸ " if paused else "") + _bar_text(st))
        elif st is not None:
            prog = st.progress()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(40, 42, 50))
            p.drawRoundedRect(r, 3, 3)
            col = QColor(230, 170, 40) if (st.paused or self.ctrl.paused_all) else QColor(60, 200, 110)
            if prog is None:
                # durata infinita (loop): barra che pulsa
                p.setBrush(col.darker(160))
                p.drawRoundedRect(r, 3, 3)
                txt = f"{fmt_time(st.elapsed)}  ∞"
            else:
                rr = QRectF(r)
                rr.setWidth(r.width() * prog)
                p.setBrush(col)
                p.drawRoundedRect(rr, 3, 3)
                tot = st.total() or 0
                txt = f"{fmt_time(st.elapsed)} / -{fmt_time(max(0.0, tot - st.elapsed))}"
                if hasattr(st, "status_text"):
                    txt = st.status_text()
            p.setPen(QColor(255, 255, 255))
            f = QFont(opt.font)
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85))
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, ("⏸ " if st.paused or self.ctrl.paused_all else "") + txt)
        if st is not None and not isinstance(st, float):
            from .ui_duckicon import paint_duck, paint_pfl
            paint_duck(p, r, st, self.ctrl)
            paint_pfl(p, r, st)
        p.restore()

    def helpEvent(self, event, view, opt, idx):
        cue = self.show_fn().cues[idx.row()]
        st = self.ctrl.cue_state(cue.id)
        if st is not None and not isinstance(st, float):
            data = _marker_bar_data(self.ctrl, cue, st)
            if data:
                from PySide6.QtWidgets import QToolTip
                from .markers import marks_tooltip
                ax, marks, pos = data
                r = opt.rect.adjusted(3, 4, -3, -4)
                t = (event.pos().x() - r.left()) / max(1, r.width()) * ax.length
                txt = marks_tooltip(marks, ax, t, r.width())
                if txt:
                    QToolTip.showText(event.globalPos(), txt, view)
                    return True
        return super().helpEvent(event, view, opt, idx)


LIST_COLORS = [QColor(70, 110, 170), QColor(150, 90, 170), QColor(40, 140, 140), QColor(180, 110, 50),
               QColor(160, 70, 100), QColor(90, 140, 60), QColor(120, 100, 70), QColor(70, 90, 150)]


def list_color(index):
    """Colore di una lista: MAIN = blu, poi un colore per PANEL."""
    return LIST_COLORS[index % len(LIST_COLORS)]


def _paint_cue_meter(p, area, inst, ctrl):
    """Piccolo vumeter verticale dell'audio della cue (1 barra mono, 2 per stereo/multicanale)."""
    voice = getattr(inst, "voice", None)
    lv = getattr(voice, "meter", None)
    if voice is None or lv is None or not len(lv) or voice.done:
        return
    vals = [float(lv[0])] if len(lv) == 1 else [float(max(lv[0::2])), float(max(lv[1::2]))]
    if voice.paused or ctrl.paused_all:
        vals = [0.0] * len(vals)
    n = len(vals)
    w = (area.width() - (n - 1)) / n
    import math
    for i, a in enumerate(vals):
        col = QRectF(area.left() + i * (w + 1), area.top(), w, area.height())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 21, 26))
        p.drawRect(col)
        db = 20 * math.log10(a) if a > 1e-6 else -90.0
        frac = max(0.0, min(1.0, (db + 60.0) / 60.0))        # scala −60…0 dBFS
        if frac <= 0:
            continue
        h = col.height() * frac
        fill = QRectF(col.left(), col.bottom() - h, col.width(), h)
        c = QColor(220, 60, 50) if db > -1.0 else QColor(230, 190, 60) if db > -9.0 else QColor(70, 200, 110)
        p.setBrush(c)
        p.drawRect(fill)


class ActivePanel(QWidget):
    """Elenco grafico delle cue in esecuzione/in attesa con avanzamento e tasto stop."""
    stopRequested = Signal(str)
    pauseRequested = Signal(str)
    showCueRequested = Signal(str)      # clic sull'etichetta della lista: mostra la cue nella sua lista

    ROW_H = 50

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.rows = []
        self.setMinimumHeight(120)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(50)
        self.setMouseTracking(True)
        self.bars = []

    def _items(self):
        items = []
        for cue, rem in self.ctrl.waiting():
            items.append(("wait", cue, rem))
        for inst in self.ctrl.instances.values():
            items.append(("run", inst.cue, inst))
        return items

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(26, 27, 32))
        p.setRenderHint(QPainter.Antialiasing)
        items = self._items()
        self.rows = []
        self.bars = []
        self.badges = []
        W = self.width()
        if not items:
            p.setPen(QColor(120, 120, 130))
            p.drawText(self.rect(), Qt.AlignCenter, "Nessuna cue attiva")
            p.end()
            return
        y = 4
        f = QFont(self.font())
        fb = QFont(f)
        fb.setBold(True)
        for kind, cue, x in items:
            r = QRectF(4, y, W - 8, self.ROW_H - 6)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(38, 40, 48))
            p.drawRoundedRect(r, 5, 5)
            p.setFont(fb)
            tx = r.left() + 8
            show = self.ctrl.show
            lst, _ = show.list_of(cue.id) if hasattr(show, "list_of") else (None, -1)
            if lst is not None and len(show.lists) > 1:
                # etichetta della lista (MAIN / nome del PANEL)
                fs = QFont(fb)
                fs.setPointSizeF(max(6.0, fb.pointSizeF() * 0.85))
                p.setFont(fs)
                name = lst.name if len(lst.name) <= 16 else lst.name[:15] + "…"
                tw = p.fontMetrics().horizontalAdvance(name) + 12
                badge = QRectF(tx, r.top() + 3, tw, 16)
                p.setPen(Qt.NoPen)
                p.setBrush(list_color(show.lists.index(lst)))
                p.drawRoundedRect(badge, 3, 3)
                p.setPen(QColor(255, 255, 255))
                p.drawText(badge, Qt.AlignCenter, name)
                self.badges.append((badge, cue.id, lst.name))
                tx += tw + 6
                p.setFont(fb)
            p.setPen(QColor(235, 235, 240))
            p.drawText(QRectF(tx, r.top() + 2, r.right() - 72 - tx, 18),
                       Qt.AlignLeft | Qt.AlignVCenter, f"{TYPE_ICONS.get(cue.type, '')}  {cue.label()}")
            bar = QRectF(r.left() + 8, r.top() + 22, r.width() - 92, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(55, 57, 66))
            p.drawRoundedRect(bar, 3, 3)
            p.setFont(f)
            if kind == "wait":
                p.setPen(QColor(240, 200, 80))
                p.drawText(bar, Qt.AlignCenter, f"in attesa  ⧗ {fmt_time(x)}")
                sb = QRectF(r.right() - 34, r.top() + 8, 28, 26)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(170, 50, 50))
                p.drawRoundedRect(sb, 4, 4)
                p.setPen(QColor(255, 255, 255))
                p.drawText(sb, Qt.AlignCenter, "■")
                self.rows.append((None, sb, cue.id))
            else:
                inst = x
                prog = inst.progress()
                paused = inst.paused or self.ctrl.paused_all
                col = QColor(230, 170, 40) if paused else QColor(60, 200, 110)
                data = _marker_bar_data(self.ctrl, cue, inst)
                if data is not None:
                    from .markers import paint_bar
                    ax, marks, pos = data
                    paint_bar(p, bar, ax, marks, pos, col)
                    self.bars.append((bar, ax, marks))
                    p.setPen(QColor(200, 200, 210))
                    p.drawText(QRectF(r.left() + 8, r.top() + 2, r.width() - 92, 18),
                               Qt.AlignRight | Qt.AlignVCenter,
                               ("⏸ " if paused else "") + _bar_text(inst))
                elif prog is not None:
                    bb = QRectF(bar)
                    bb.setWidth(bar.width() * prog)
                    p.setBrush(col)
                    p.drawRoundedRect(bb, 3, 3)
                    rem = max(0.0, (inst.total() or 0) - inst.elapsed)
                    txt = f"-{fmt_time(rem)}"
                else:
                    p.setBrush(col.darker(170))
                    p.drawRoundedRect(bar, 3, 3)
                    txt = "∞"
                if data is None:
                    if hasattr(inst, "status_text"):
                        txt = inst.status_text()
                    p.setPen(QColor(255, 255, 255))
                    p.drawText(bar, Qt.AlignCenter, f"{fmt_time(inst.elapsed)}   {txt}")
                from .ui_duckicon import paint_duck, paint_pfl
                paint_duck(p, bar, inst, self.ctrl)
                paint_pfl(p, bar, inst)
                _paint_cue_meter(p, QRectF(bar.right() + 4, r.top() + 6, 10, r.height() - 12), inst, self.ctrl)
                # pulsanti pausa / stop
                pb = QRectF(r.right() - 66, r.top() + 8, 28, 26)
                sb = QRectF(r.right() - 34, r.top() + 8, 28, 26)
                p.setBrush(QColor(80, 80, 92))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(pb, 4, 4)
                p.setBrush(QColor(170, 50, 50))
                p.drawRoundedRect(sb, 4, 4)
                p.setPen(QColor(255, 255, 255))
                p.drawText(pb, Qt.AlignCenter, "▶" if paused else "❚❚")
                p.drawText(sb, Qt.AlignCenter, "■")
                self.rows.append((pb, sb, cue.id))
            y += self.ROW_H
        self.setMinimumHeight(max(120, y + 4))
        p.end()

    def mouseMoveEvent(self, e):
        from PySide6.QtWidgets import QToolTip
        from .markers import marks_tooltip
        pos = e.position()
        for badge, _cid, name in getattr(self, "badges", []):
            if badge.contains(pos):
                QToolTip.showText(e.globalPosition().toPoint(), f"Lista «{name}» · clic per mostrarla", self)
                return
        for bar, ax, marks in getattr(self, "bars", []):
            if bar.adjusted(0, -3, 0, 3).contains(pos):
                txt = marks_tooltip(marks, ax, (pos.x() - bar.left()) / max(1, bar.width()) * ax.length,
                                    bar.width())
                if txt:
                    QToolTip.showText(e.globalPosition().toPoint(), txt, self)
                    return
        QToolTip.hideText()

    def mousePressEvent(self, e):
        pos = e.position()
        for badge, cid, _name in getattr(self, "badges", []):
            if badge.contains(pos):
                self.showCueRequested.emit(cid)
                return
        for pb, sb, cid in self.rows:
            if sb.contains(pos):
                self.stopRequested.emit(cid)
                return
            if pb is not None and pb.contains(pos):
                self.pauseRequested.emit(cid)
                return
