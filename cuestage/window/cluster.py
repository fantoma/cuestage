"""Istanze secondarie: master, slave, ridondanza (parte di MainWindow)."""
from ..i18n import tr
import copy
import os

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QMessageBox, )


FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


# impostazioni scritte da CueStage stesso (non sono modifiche dell'operatore della secondaria)
LOCAL_SKIP = ("mixer_names", "cluster_local_keys")


def setting_label(key):
    """Nome leggibile di un'impostazione (per l'elenco delle impostazioni locali della secondaria)."""
    groups = (("osc_", "OSC"), ("midi_", "MIDI"), ("output_", tr("Uscita video")), ("audio_", tr("Schede audio")),
              ("xfade", tr("Crossfade")))
    names = {"mixers": tr("Mixer"), "switchers": tr("Regie video"), "projectors": tr("Proiettori"),
             "ducking": tr("Ducking"), "preview": tr("Preascolto"), "device_channels": tr("Schede audio"),
             "master_db": tr("Volume master"), "stop_fade": tr("Fade dello STOP"), "go_target": tr("GO su"),
             "video_preload": tr("Precarica i video"), "edit_blind": "BLIND"}
    if key in names:
        return names[key]
    for pre, lab in groups:
        if key.startswith(pre):
            return lab
    return key


class ClusterMixin:
    def _cluster_apply_settings(self):
        if not hasattr(self, "cluster"):
            return
        hub = self.cluster
        s = self.show_data.settings
        if s.get("cluster_name"):
            hub.name = s["cluster_name"]
        hub.allow_slave_edit = bool(s.get("cluster_slave_edit"))
        want = bool(s.get("cluster_enabled")) and hub.role != "slave"
        port = int(s.get("cluster_port") or 53900)
        if want and (hub.role != "master" or hub.port != port):
            if hub.start_master(port, lambda: self.show_data, self._cluster_hw):
                self.status(tr("Istanze secondarie abilitate: porta TCP {0}", port))
        elif not want and hub.role == "master":
            hub.stop_master()
        self._cluster_indicator()

    def _cluster_upload_dir(self):
        p = self.show_data.path
        base = os.path.dirname(os.path.abspath(p)) if p else os.path.expanduser("~/CueStage-master")
        return os.path.join(base, "da_slave")

    def _cluster_hw(self):
        """Hardware usato dallo show su questa macchina (per il confronto sulle secondarie)."""
        audio = {}
        for c in self.show_data.all_cues():
            if c.type in ("audio", "video"):
                key = c.audio_device or ""
                if key not in audio:
                    name, ch, found = self.audio.resolve(key or None)
                    audio[key] = {"name": name, "channels": ch, "found": found}
        try:
            from ..remote import mido as _m
            outs = list(_m.get_output_names()) if _m else []
        except Exception:
            outs = []
        return {"audio": audio, "midi_out": outs, "mixers": self.show_data.settings.get("mixers") or [],
                "switchers": self.show_data.settings.get("switchers") or []}

    def _cluster_join(self, cid, name, addr, mode):
        from ..ui_cluster import JoinDialog
        d = JoinDialog(name, addr, mode, self.show_data.lists, self.cluster.allow_slave_edit, self)
        ok = d.exec() == JoinDialog.Accepted
        self.cluster.answer(cid, ok, d.perms() if ok else None)

    def become_slave(self):
        if self.cluster.role == "slave":
            QMessageBox.information(self, tr("Slave"), tr("Questa istanza è già slave: «Esci dalla modalità slave» prima."))
            return
        if self.ctrl.instances and QMessageBox.question(
                self, tr("Diventa slave"), tr("Ci sono cue in esecuzione: verranno fermate. Continuare?")) != QMessageBox.Yes:
            return
        if not self._confirm_discard():
            return
        from ..ui_cluster import BecomeSlaveDialog
        BecomeSlaveDialog(self.cluster, self).exec()
        self._cluster_indicator()

    def _cluster_show(self, data, info):
        """Show ricevuto dal master (primo collegamento o modifica)."""
        from ..model import Show
        hub = self.cluster
        new = Show.from_data(data, None)
        for c in new.every_cue():
            if c.file in info["remap"]:
                c.file = info["remap"][c.file]
        sh = self.show_data
        if hub.copy_settings or info["first"] and not sh.path:
            # impostazioni cambiate qui (OSC, MIDI, mixer…): restano locali e il master non le aggiorna più
            local = self._cluster_local_keys()
            self._cluster_master_settings = copy.deepcopy(new.settings)
            keep = {k: v for k, v in sh.settings.items() if k.startswith("cluster_") or k.startswith("web_")}
            new.settings.update(keep)
            for k in local:
                if k in sh.settings:
                    new.settings[k] = copy.deepcopy(sh.settings[k])
                else:
                    new.settings.pop(k, None)
            new.settings["cluster_local_keys"] = sorted(local)
            new.settings["cluster_enabled"] = sh.settings.get("cluster_enabled", False)
        else:
            new.settings = sh.settings
        path = os.path.join(hub.media_dir, info.get("name") or "show.cueshow")
        if info["first"]:
            self.ctrl.stop_all(0)
            self._set_show(new)
            self.show_data.path = path
            self.ctrl.playheads = {k: int(v) for k, v in (info.get("playheads") or {}).items()}
            if info.get("seed"):
                self.ctrl.seed_base, self.ctrl.seed_count = str(info["seed"][0]), int(info["seed"][1])
            self.act_leave.setEnabled(True)
            self._rd_align = True                   # ridondanza: al primo stato del master ci si allinea subito
            changed = True
        elif self._lists_hash(new) == self._lists_hash(sh) and (new.settings is sh.settings or
                                                                 new.settings == sh.settings):
            changed = False          # è l'eco di una modifica fatta qui: niente da cambiare (non disturba l'editor)
        elif hub.can_edit and self._show_hash() != getattr(self, "_slave_hash", None):
            # modifiche fatte qui e non ancora partite: prima vanno al master, che poi rimanda lo show
            # aggiornato (altrimenti si tornerebbe indietro mentre si scrive)
            self._cluster_hw_check(info.get("hw") or {}, data.get("settings") or {})
            return
        else:
            changed = self._cluster_replace(new, new.settings is not sh.settings)
        self._slave_edit_state()
        try:
            self.show_data.save(path)
        except OSError as e:
            self.status(tr("Copia locale dello show non salvata: {0}", e))
        self._slave_hash = self._show_hash()          # base per riconoscere le modifiche fatte qui
        self._cluster_settings_base = copy.deepcopy(self.show_data.settings)   # … anche alle impostazioni
        diffs = self._cluster_hw_check(info.get("hw") or {}, data.get("settings") or {})
        if info["first"]:
            msg = (tr("Collegato a «{0}» come {1}.\nFile: {2} scaricati su {3}.", hub.master_info.get('name'), 'RIDONDANZA' if hub.mode == 'redundancy' else 'SLAVE', info['downloaded'], info['files']))
            if info.get("errors"):
                msg += tr("\n\nErrori:\n") + "\n".join(info["errors"][:10])
            if diffs:
                msg += tr("\n\n⚠ Hardware diverso dal master (le cue interessate sono in rosso):\n• ") + \
                       "\n• ".join(diffs[:20])
                QMessageBox.warning(self, tr("Istanza secondaria"), msg)
            else:
                self.status(msg.replace("\n", " "))
        elif changed or info["downloaded"]:
            self.status(tr("Show aggiornato dal master ({0} file scaricati)", info['downloaded'])
                        + (tr(" · ⚠ {0} differenze hardware", len(diffs)) if diffs else ""))
        self._cluster_indicator()

    # ------------------------------------------------------------------ impostazioni locali della secondaria
    def _cluster_local_keys(self):
        """Impostazioni cambiate su questa secondaria: quelle già note più quelle diverse da come le aveva
        lasciate l'ultimo aggiornamento del master (qualunque finestra le abbia cambiate)."""
        sh = self.show_data
        keys = set(sh.settings.get("cluster_local_keys") or [])
        base = getattr(self, "_cluster_settings_base", None)
        if base is not None:
            for k in set(base) | set(sh.settings):
                if k.startswith("cluster_") or k.startswith("web_") or k in LOCAL_SKIP:
                    continue
                if sh.settings.get(k) != base.get(k):
                    keys.add(k)
        return keys

    def cluster_local_settings(self):
        """[(chiave, nome leggibile)] delle impostazioni locali (non aggiornate dal master)."""
        return sorted(((k, setting_label(k)) for k in self._cluster_local_keys()), key=lambda x: (x[1], x[0]))

    def cluster_reset_local(self, keys=None):
        """Le impostazioni locali tornano quelle del master (tutte, o solo «keys») e lui le aggiorna di nuovo."""
        sh = self.show_data
        local = self._cluster_local_keys()
        drop = set(keys) if keys else set(local)
        master = getattr(self, "_cluster_master_settings", None) or {}
        old = dict(sh.settings)
        for k in drop:
            if k in master:
                sh.settings[k] = copy.deepcopy(master[k])
        sh.settings["cluster_local_keys"] = sorted(local - drop)
        self._apply_settings(old=old)
        self._cluster_settings_base = copy.deepcopy(sh.settings)
        sh.dirty = True
        self.status(tr("Impostazioni di nuovo dal master: {0}", ", ".join(sorted({setting_label(k) for k in drop}))
                       or "—"))

    def _cluster_push_settings(self):
        """Impostazioni da mandare al master con le modifiche fatte qui: quelle locali restano qui (al master
        va il suo valore), così le preferenze di questa istanza non cambiano il master."""
        sh = self.show_data
        local = self._cluster_local_keys()
        master = getattr(self, "_cluster_master_settings", None) or {}
        out = copy.deepcopy(sh.settings)
        for k in local:
            if k in master:
                out[k] = copy.deepcopy(master[k])
            else:
                out.pop(k, None)
        out.pop("cluster_local_keys", None)
        return out

    def _cluster_replace(self, new, take_settings):
        """Aggiorna liste e cue dello show aperto con quelle arrivate da un'altra istanza, senza fermare le
        cue in corso e senza disturbare chi sta lavorando: le cue rimaste uguali non si toccano, quelle
        cambiate si aggiornano sul posto (stesso oggetto: l'ispettore resta aperto e non perde il focus)."""
        from ..model import Cue
        sh = self.show_data
        view_id = sh.current.id
        old = dict(sh.settings)
        self._history_flush()
        self._hist_restoring = True
        changed = set()
        try:
            before = [(l.id, [c.id for c in l.cues]) for l in sh.lists]
            old_cues = {c.id: c for c in sh.all_cues()}
            old_lists = {l.id: l for l in sh.lists}
            lists = []
            for nl in new.lists:
                cues = []
                for nc in nl.cues:
                    oc = old_cues.get(nc.id)
                    if oc is not None and oc.type == nc.type:
                        if oc.to_dict(None) != nc.to_dict(None):
                            for k in Cue.FIELDS:
                                setattr(oc, k, getattr(nc, k))
                            changed.add(oc.id)
                        cues.append(oc)
                    else:
                        cues.append(nc)
                        changed.add(nc.id)
                ol = old_lists.get(nl.id)
                if ol is not None:
                    ol.name = nl.name
                    ol.set_grid_data(nl.grid_data())
                    ol.cues = cues
                    lists.append(ol)
                else:
                    nl.cues = cues
                    lists.append(nl)
            sh.lists = lists
            sh.versions = new.versions
            sh.active_version = new.active_version
            sh.view = next((i for i, l in enumerate(sh.lists) if l.id == view_id), 0)
            if take_settings and new.settings != sh.settings:
                sh.settings = new.settings
                self._apply_settings(old=old)
            same = before == [(l.id, [c.id for c in l.cues]) for l in sh.lists]
            cur = self.inspector.cue
            still = cur is not None and any(c is cur for c in sh.all_cues())
            if same:
                self.model.refresh()
            else:
                self.model.set_show(sh)             # righe cambiate: la selezione si ripristina qui sotto
                self._apply_collapse()
                self._update_tabs()
                if still and sh.current.index_of(cur.id) >= 0:
                    self._select_guard = True
                    try:
                        self._select_rows([sh.current.index_of(cur.id)])
                    finally:
                        self._select_guard = False
            for c in sh.all_cues():
                if c.id in changed:
                    self._preload(c)
            for inst in list(self.ctrl.instances.values()):   # cue che suonano: curva e volume subito
                if inst.cue.id in changed:
                    inst.live_edit()
            if not still:
                self.inspector.set_cue(None)
            elif cur.id in changed:
                self._inspector_remote_update()
            self._update_standby()
        finally:
            self._hist_restoring = False
        if changed or not same:
            self._history_capture(tr("Modifica da un'altra istanza"))
        return bool(changed) or not same

    def _inspector_remote_update(self):
        """La cue nell'ispettore è cambiata su un'altra istanza: se qui si sta scrivendo nell'ispettore si
        aggiornano solo gli altri campi (il campo con il cursore resta com'è), altrimenti si ricostruisce."""
        from PySide6.QtWidgets import QApplication
        fw = QApplication.focusWidget()
        insp = self.inspector
        if fw is not None and (fw is insp or insp.isAncestorOf(fw)):
            insp.refresh_fields(skip=fw)
        else:
            insp.rebuild()

    @staticmethod
    def _lists_hash(show):
        import hashlib
        import json as _json
        data = [[l.id, l.name, l.grid_data(), [c.to_dict(None) for c in l.cues]] for l in show.lists]
        data.append([show.active_version, show.versions_data(None)])
        return hashlib.sha1(_json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()

    def _show_hash(self):
        import hashlib
        import json as _json
        try:
            return hashlib.sha1(_json.dumps(self.show_data.to_data(None), sort_keys=True, default=str)
                                .encode()).hexdigest()
        except Exception:
            return None

    def _cluster_slave_tick(self):
        """Secondaria con modifica consentita: le modifiche fatte qui partono verso il master."""
        hub = self.cluster
        if hub.role != "slave" or not hub.can_edit or hub.lost:
            return
        h = self._show_hash()
        if h is None or h == getattr(self, "_slave_hash", None):
            return
        if getattr(self, "_slave_hash", None) is None:
            self._slave_hash = h
            return
        self._slave_hash = h
        hub.push_show(self.show_data, self._cluster_push_settings())

    def _slave_edit_state(self):
        """Secondaria: l'ispettore si usa solo sulle liste che il master le lascia modificare."""
        hub = self.cluster
        if hub.role == "slave":
            self.inspector.setEnabled(hub.can_edit_list(self.show_data.current.id))
        else:
            self.inspector.setEnabled(True)

    def _cluster_slave_show(self, data, who, perms=None):
        """(master) Show modificato su una secondaria: entra nel progetto e riparte verso tutte. Con accesso
        limitato entrano solo le liste che quella secondaria può modificare (le altre restano come sono)."""
        from ..model import Show
        new = Show.from_data(data, None)
        sh = self.show_data
        perms = perms or {"*": "edit"}
        if "*" not in perms:
            got = {l.id: l for l in new.lists}
            merged = [got[l.id] if perms.get(l.id) == "edit" and l.id in got else l for l in sh.lists]
            new.lists = merged
            new.versions, new.active_version = sh.versions, sh.active_version
            if not any(perms.get(l.id) == "edit" and l.id in got for l in sh.lists):
                return
            self._cluster_replace(new, False)
            sh.dirty = True
            self._update_title()
            self.status(tr("Progetto aggiornato da «{0}» (istanza secondaria)", who))
            return
        take = "settings" in data
        if take:
            keep = {k: v for k, v in sh.settings.items() if k.startswith("cluster_") or k.startswith("web_")}
            new.settings.update(keep)
        self._cluster_replace(new, take)
        sh.dirty = True
        self._update_title()
        self.status(tr("Progetto aggiornato da «{0}» (istanza secondaria)", who))

    def _cluster_hw_check(self, hw, master_settings):
        """Cue che usano hardware diverso da quello del master → rosse nella lista."""
        bad, diffs = {}, []
        sh = self.show_data
        # schede audio
        for key, m in (hw.get("audio") or {}).items():
            name, ch, found = self.audio.resolve(key or None)
            why = None
            if not found:
                why = tr("scheda audio «{0}» non presente qui (uso «{1}»)", m.get('name'), name)
            elif m.get("channels") and ch < int(m["channels"]):
                why = tr("scheda «{0}»: qui {1} canali, sul master {2}", m.get('name'), ch, m['channels'])
            if why:
                diffs.append(why)
                for c in sh.all_cues():
                    if c.type in ("audio", "video") and (c.audio_device or "") == key:
                        bad[c.id] = why
        # mixer (configurazione diversa o scheda ALSA assente)
        mm_master = {m.get("id"): m for m in (hw.get("mixers") or master_settings.get("mixers") or [])}
        local = {m.get("id"): m for m in sh.settings.get("mixers") or []}
        try:
            from ..alsactl import alsa_cards
            cards = {cid for _i, cid, _s, _l in alsa_cards()}
        except Exception:
            cards = set()
        for mid, m in mm_master.items():
            lm = local.get(mid)
            why = None
            if lm is None:
                why = tr("mixer «{0}» non configurato qui", m.get('name'))
            elif any((lm.get(k) or "") != (m.get(k) or "") for k in ("driver", "host", "port", "card", "midi_out")):
                why = tr("mixer «{0}» configurato in modo diverso", m.get('name'))
            elif lm.get("card") and cards and lm.get("card") not in cards:
                why = tr("scheda «{0}» del mixer «{1}» non collegata qui", lm.get('card_name') or lm.get('card'), m.get('name'))
            if why:
                diffs.append(why)
                for c in sh.all_cues():
                    if c.type == "mixer" and c.mix_target == mid:
                        bad[c.id] = why
        # regie video
        sw_master = {m.get("id"): m for m in (hw.get("switchers") or master_settings.get("switchers") or [])}
        sw_local = {m.get("id"): m for m in sh.settings.get("switchers") or []}
        for sid, m in sw_master.items():
            lm = sw_local.get(sid)
            why = None
            if lm is None:
                why = tr("regia video «{0}» non configurata qui", m.get('name'))
            elif any((lm.get(k) or "") != (m.get(k) or "") for k in ("driver", "midi_out", "midi_ch", "host", "port")):
                why = tr("regia video «{0}» configurata in modo diverso", m.get('name'))
            if why:
                diffs.append(why)
                for c in sh.all_cues():
                    if c.type == "switcher" and c.sw_target == sid:
                        bad[c.id] = why
        # uscita MIDI
        try:
            from ..remote import mido as _m, port_key
            outs = [port_key(n) for n in (_m.get_output_names() if _m else [])]
        except Exception:
            outs, port_key = [], (lambda x: x)
        for c in sh.all_cues():
            if c.type == "midiout":
                port = c.mo_port or sh.settings.get("midi_out_port") or ""
                if port and not any(o.startswith(port_key(port)) for o in outs):
                    why = tr("uscita MIDI «{0}» non presente qui", port)
                    if why not in diffs:
                        diffs.append(why)
                    bad[c.id] = why
        self.hw_bad = bad
        self.model.hw_bad = bad
        self.model.refresh()
        return diffs

    # ------------------------------------------------------------------ stato del master sulle secondarie
    def _cluster_state_info(self):
        """(master) Cosa sta facendo questa istanza: playhead, cue attive (secondi), lista visualizzata."""
        sh, ctrl = self.show_data, self.ctrl
        insts = list(ctrl.instances.items())
        return {"ph": {l.id: ctrl.playhead_of(l) for l in sh.lists},
                "run": {cid: round(float(inst.elapsed), 2) for cid, inst in insts},
                "paused": sorted(cid for cid, inst in insts if inst.paused),
                "paused_all": bool(ctrl.paused_all),
                "view": sh.current.id}

    def _cluster_state(self, st):
        """(secondaria) Stato del master: il playhead segue quello del master; in SLAVE le cue attive sul
        master si vedono nella colonna Stato; in RIDONDANZA si fa esattamente quello che fa il master
        (stessa lista visualizzata, cue avviate o fermate se un comando si fosse perso)."""
        hub, ctrl, sh = self.cluster, self.ctrl, self.show_data
        if hub.role != "slave" or hub.lost:
            return
        red = hub.mode == "redundancy"
        if red and st.get("view") and sh.current.id != st["view"]:
            i = next((i for i, l in enumerate(sh.lists) if l.id == st["view"]), None)
            if i is not None:
                self.show_list(i)
        moved = False
        for lid, idx in (st.get("ph") or {}).items():
            lst = sh.list_by_id(lid)
            if lst is not None and ctrl.playheads.get(lid, 0) != int(idx):
                ctrl.playheads[lid] = max(0, min(int(idx), len(lst.cues)))
                moved = True
        if moved:
            ctrl.playheadChanged.emit(ctrl.playhead)
        run = {k: float(v) for k, v in (st.get("run") or {}).items()}
        if not red:
            ctrl.remote_running = run
            self.model.refresh_state()
            return
        ctrl.remote_running = {}
        self._redundancy_align(st, run)

    SEEK_TOL = 0.3          # scarto (s) oltre il quale la ridondanza si riallinea al punto del master
    SEEK_TYPES = ("audio", "video", "image", "wait", "text")

    def _redundancy_align(self, st, run):
        """Ridondanza: fa esattamente quello che fa il master. Le cue che suonano sul master partono qui al
        punto in cui sono sul master (anche appena collegata), quelle ferme sul master si fermano, chi si è
        scostato si riallinea; pausa generale e pause delle singole cue come sul master."""
        import time as _t
        hub, ctrl, sh = self.cluster, self.ctrl, self.show_data
        now = _t.monotonic()
        first = getattr(self, "_rd_align", False)       # appena collegata: subito, senza aspettare
        self._rd_align = False
        local = set(ctrl.instances)
        extra = getattr(self, "_rd_extra", {})
        miss = getattr(self, "_rd_miss", {})
        self._rd_extra = {k: extra.get(k, now) for k in local - set(run)}
        self._rd_miss = {k: miss.get(k, now) for k in set(run) - local}
        paused = set(st.get("paused") or [])
        hub._applying = True
        try:
            if bool(st.get("paused_all")) != bool(ctrl.paused_all):
                (ctrl.pause_all if st.get("paused_all") else ctrl.resume_all)()
            for cid, t0 in list(self._rd_extra.items()):
                if first or now - t0 > 1.0:              # ferma sul master da più di un secondo
                    ctrl.stop_cue(cid, 0.3)
                    self._rd_extra.pop(cid, None)
            for cid, t0 in list(self._rd_miss.items()):
                cue = sh.by_id(cid)
                if cue is None:
                    self._rd_miss.pop(cid, None)
                    continue
                if not (first or now - t0 > 0.6):        # partita sul master, qui no
                    continue
                self._rd_miss.pop(cid, None)
                ctrl.start_cue(cue, cascade=False)
                inst = ctrl.instances.get(cid)
                if inst is not None and run[cid] > 0.05 and cue.type in self.SEEK_TYPES:
                    tot = inst.total()
                    if tot and run[cid] >= tot - 0.1:   # sul master sta per finire: non serve
                        inst.stop(0.0)
                        continue
                    inst.seek_to(run[cid])             # al punto in cui è sul master
            for cid, inst in list(ctrl.instances.items()):
                if cid not in run or inst.done:
                    continue
                if inst.cue.type in self.SEEK_TYPES and abs(float(inst.elapsed) - run[cid]) > self.SEEK_TOL \
                        and not inst.paused and not ctrl.paused_all:
                    inst.seek_to(run[cid])
                if bool(inst.paused) != (cid in paused):
                    ctrl.pause_cue(cid, cid in paused)
        finally:
            hub._applying = False

    def _cluster_lost(self):
        self.status(tr("⚠ MASTER NON RAGGIUNGIBILE (nessun heartbeat)"))
        self.ctrl.remote_running = {}
        self._cluster_indicator()
        hub = self.cluster
        self.lost_alarm.alarm(hub.master_info.get("name") or hub.master_info.get("host") or "?")
        self.fire_event("master_lost")
        if QSettings("CueStage", "CueStage").value("cluster/auto_promote", "false") in (True, "true"):
            self.cluster_promote(ask=False)

    def _cluster_back(self):
        self.status(tr("Master di nuovo raggiungibile"))
        self._cluster_indicator()
        self.lost_alarm.master_back()

    def cluster_promote(self, ask=True):
        hub = self.cluster
        if hub.role != "slave":
            return
        if ask and QMessageBox.question(
                self, tr("Promuovi"), tr("Scollegarsi dal master e diventare indipendente (master, se le istanze "
                "secondarie sono abilitate)? Le cue in esecuzione continuano.")) != QMessageBox.Yes:
            return
        old_host = (hub.master_info or {}).get("host") or ""
        hub.promote()
        self.lost_alarm.promoted()
        self.ctrl.remote_running = {}
        self.inspector.setEnabled(True)
        self.act_leave.setEnabled(False)
        self.hw_bad = {}
        self.model.hw_bad = {}
        self.model.refresh()
        self._cluster_apply_settings()
        msg = tr("Questa istanza ora è indipendente") + (tr(" (master)") if hub.role == "master" else "")
        s = self.show_data.settings
        if hub.master_osc_clients and s.get("osc_in_enabled", True):
            # i programmi collegati al vecchio master (es. Script Follower) mandano i comandi qui
            n = hub.announce_master(int(s.get("osc_in_port", 53000)), old_host)
            if n:
                msg += " · " + tr("comunicato a {0} programmi collegati via OSC", n)
        self.status(msg)
        self._cluster_indicator()
        self.fire_event("promoted")

    def _cluster_kicked(self, why):
        """Il master ci ha disconnessi: si torna indipendenti (non è un «master perso»)."""
        hub = self.cluster
        hub.disconnect_slave()
        self.ctrl.remote_running = {}
        self.inspector.setEnabled(True)
        self.hw_bad = {}
        self.model.hw_bad = {}
        self.model.refresh()
        self._cluster_indicator()
        self.status(tr("⚠ Disconnessa dal master: {0}", why))
        QMessageBox.information(self, tr("Istanza secondaria"), tr("Il master ha chiuso il collegamento.\n{0}", why))

    def _cluster_chat(self, m):
        """Messaggio nuovo: barra di stato e finestra dei messaggi (aperta se chiusa)."""
        hub = self.cluster
        mine = m.get("from_id") == hub.instance_id and m.get("from") == hub.name
        if mine:
            return
        self.status(tr("💬 {0}: {1}", m.get("from"), m.get("text")))
        self.open_cluster_window(activate=False)

    def open_cluster_window(self, activate=True):
        from ..ui_cluster import ClusterWindow
        w = getattr(self, "_cluster_win", None)
        if w is None or not w.isVisible():
            w = ClusterWindow(self.cluster, self, self)
            w.setModal(False)
            self._cluster_win = w
            if not activate:
                from PySide6.QtCore import Qt
                w.setAttribute(Qt.WA_ShowWithoutActivating)
            w.show()
        if activate:
            w.raise_()
            w.activateWindow()
        return w

    def _cluster_indicator(self):
        from ..outputs import hold
        hub = self.cluster
        # ridondanza collegata e master vivo: i comandi ai dispositivi esterni li manda solo il master
        held = hub.role == "slave" and hub.mode == "redundancy" and not hub.lost
        hold(held)
        if hasattr(self, "held_label"):
            self.held_label.setVisible(held)
        if hasattr(self, "act_leave"):        # anche durante il primo download: si può sempre uscire
            self.act_leave.setEnabled(self.cluster.role == "slave")
        if not hasattr(self, "cluster_btn"):
            return
        hub = self.cluster
        b = self.cluster_btn
        if hub.role == "master":
            n = len(hub.client_list())
            b.setText(tr("◉ MASTER · {0} secondari{1}", n, 'a' if n == 1 else 'e'))
            b.setStyleSheet("color:#6c6;font-weight:bold;")
        elif hub.role == "slave":
            who = hub.master_info.get("name") or hub.master_info.get("host")
            kind = "RIDONDANZA" if hub.mode == "redundancy" else "SLAVE"
            if hub.lost:
                b.setText(tr("⚠ {0}: master «{1}» perso — clic per promuovere", kind, who))
                b.setStyleSheet("color:white;background:#b3261e;font-weight:bold;padding:0 6px;")
            else:
                b.setText(tr("◎ {0} di «{1}» ♥", kind, who) + ("  ✎" if hub.can_edit else ""))
                b.setStyleSheet("color:#e0b040;font-weight:bold;")
        b.setVisible(hub.role is not None)

    def _cluster_clicked(self):
        hub = self.cluster
        if hub.role == "slave" and hub.lost:
            self.cluster_promote()
            return
        self.open_cluster_window()
