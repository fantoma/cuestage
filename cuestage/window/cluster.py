"""Istanze secondarie: master, slave, ridondanza (parte di MainWindow)."""
from ..i18n import tr
import os

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QMessageBox, )


FILE_FILTER = "Show CueStage (*.cueshow);;JSON (*.json);;Tutti i file (*)"


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
        return {"audio": audio, "midi_out": outs, "mixers": self.show_data.settings.get("mixers") or []}

    def _cluster_join(self, cid, name, addr, mode):
        what = tr("RIDONDANZA (esegue le stesse cue)") if mode == "redundancy" else tr("SLAVE (copia del progetto)")
        r = QMessageBox.question(self, tr("Istanza secondaria"),
                                 tr("«{0}» ({1}) chiede di collegarsi come {2}.\n\nRiceverà lo show, i file multimediali e ogni modifica. Accetti?", name, addr, what))
        self.cluster.answer(cid, r == QMessageBox.Yes)

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
            keep = {k: v for k, v in sh.settings.items() if k.startswith("cluster_") or k.startswith("web_")}
            new.settings.update(keep)
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
            self.inspector.setEnabled(False)
            self.act_leave.setEnabled(True)
        elif self._lists_hash(new) == self._lists_hash(sh) and (new.settings is sh.settings or
                                                                 new.settings == sh.settings):
            pass                     # è l'eco di una modifica fatta qui: niente da cambiare (non disturba l'editor)
        else:
            self._cluster_replace(new, new.settings is not sh.settings)
        self.inspector.setEnabled(hub.can_edit)
        try:
            self.show_data.save(path)
        except OSError as e:
            self.status(tr("Copia locale dello show non salvata: {0}", e))
        self._slave_hash = self._show_hash()          # base per riconoscere le modifiche fatte qui
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
        else:
            self.status(tr("Show aggiornato dal master ({0} file scaricati)", info['downloaded'])
                        + (tr(" · ⚠ {0} differenze hardware", len(diffs)) if diffs else ""))
        self._cluster_indicator()

    def _cluster_replace(self, new, take_settings):
        """Sostituisce liste e cue dello show aperto senza fermare le cue in corso."""
        sh = self.show_data
        view_id = sh.current.id
        sel = self.inspector.cue.id if getattr(self.inspector, "cue", None) else None
        old = dict(sh.settings)
        self._history_flush()
        self._hist_restoring = True
        try:
            sh.lists = new.lists
            sh.versions = new.versions
            sh.active_version = new.active_version
            sh.view = next((i for i, l in enumerate(sh.lists) if l.id == view_id), 0)
            if take_settings:
                sh.settings = new.settings
                self._apply_settings(old=old)
            self.model.set_show(sh)
            for c in sh.all_cues():
                self._preload(c)
            self._apply_collapse()
            cue = next((c for c in sh.all_cues() if c.id == sel), None) if sel else None
            self.inspector.set_cue(cue)
            self._update_tabs()
            self._update_standby()
        finally:
            self._hist_restoring = False
        self._history_capture(tr("Modifica da un'altra istanza"))

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
        hub.push_show(self.show_data)

    def _cluster_slave_show(self, data, who):
        """(master) Show modificato su una secondaria: entra nel progetto e riparte verso tutte."""
        from ..model import Show
        new = Show.from_data(data, None)
        sh = self.show_data
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

    def _cluster_lost(self):
        self.status(tr("⚠ MASTER NON RAGGIUNGIBILE (nessun heartbeat)"))
        self._cluster_indicator()
        if QSettings("CueStage", "CueStage").value("cluster/auto_promote", "false") in (True, "true"):
            self.cluster_promote(ask=False)

    def _cluster_back(self):
        self.status(tr("Master di nuovo raggiungibile"))
        self._cluster_indicator()

    def cluster_promote(self, ask=True):
        hub = self.cluster
        if hub.role != "slave":
            return
        if ask and QMessageBox.question(
                self, tr("Promuovi"), tr("Scollegarsi dal master e diventare indipendente (master, se le istanze "
                "secondarie sono abilitate)? Le cue in esecuzione continuano.")) != QMessageBox.Yes:
            return
        hub.promote()
        self.inspector.setEnabled(True)
        self.act_leave.setEnabled(False)
        self.hw_bad = {}
        self.model.hw_bad = {}
        self.model.refresh()
        self._cluster_apply_settings()
        self.status(tr("Questa istanza ora è indipendente") + (tr(" (master)") if hub.role == "master" else ""))
        self._cluster_indicator()

    def _cluster_indicator(self):
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
        if hub.role == "slave":
            if hub.lost:
                self.cluster_promote()
            else:
                QMessageBox.information(self, tr("Istanza secondaria"),
                                        tr("Collegata a «{0}» ({1}:{2}) in modalità {3}.\n", hub.master_info.get('name'), hub.master_info.get('host'), hub.master_info.get('port'), 'RIDONDANZA' if hub.mode == 'redundancy' else 'SLAVE')
                                        + (tr("Le modifiche fatte qui vengono inviate al master.") if hub.can_edit else
                                           tr("Le modifiche al progetto arrivano dal master; l'editor è bloccato.")))
        elif hub.role == "master":
            rows = hub.client_list()
            QMessageBox.information(self, tr("Istanze collegate"),
                                    "\n".join(f"• {n} ({a}) — {'ridondanza' if m == 'redundancy' else 'slave'}"
                                              for n, a, m in rows) or tr("Nessuna istanza secondaria collegata."))
        else:
            QMessageBox.information(self, tr("Istanze"), tr("Questa istanza non è né master né slave. Per accettare "
                                    "istanze secondarie: Impostazioni › Audio, OSC e MIDI › Istanze."))
