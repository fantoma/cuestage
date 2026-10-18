"""Istanze secondarie: master / slave e ridondanza.

Una istanza MASTER (Impostazioni › Istanze: «Abilita istanze secondarie») apre una porta TCP; un'istanza che
«diventa slave» si collega, il master chiede conferma, poi lo slave scarica lo show e i file multimediali
(tutti o solo quelli mancanti/diversi) e riceve subito ogni modifica del progetto.
Il master manda un heartbeat ogni 100 ms: se lo slave non lo sente più può essere promosso a master.
In modalità RIDONDANZA lo slave esegue gli stessi comandi del master (GO, stop, playhead, crossfade, livelli…)
con gli stessi semi casuali, così i gruppi random scelgono le stesse cue.

Protocollo: frame = 4 byte di lunghezza + JSON (UTF-8); un frame con "bin": n è seguito da n byte di dati.
Ricerca: datagramma UDP «CUESTAGE-MASTER?» sulla porta TCP+1 → risposta JSON del master.
"""
import hashlib
import json
import os
import socket
import struct
import threading
import time
import uuid

from PySide6.QtCore import QObject, QTimer, Signal

from .system import file_basename, local_path, portable_rel

PROTO = 1
DEFAULT_PORT = 53900
HEARTBEAT_MS = 100
CHUNK = 256 * 1024
DISCOVER_MSG = b"CUESTAGE-MASTER?"
# metodi del controller che sulle istanze in ridondanza si ripetono identici
REPLICATED = ("go", "start_cue", "trigger", "stop_cue", "stop_all", "stop_list", "pause_cue", "pause_all",
              "resume_all", "set_playhead", "step_playhead", "playhead_to", "crossfade", "set_crossfader",
              "set_live_level", "set_live_opacity", "exit_captured_groups", "text_step", "text_goto",
              "group_tap", "set_blind")
GUARDED = ("_tick",)             # chiamate interne: quello che fanno non va ritrasmesso


# =========================================================================== rete
def send_frame(sock, lock, obj, payload=None):
    head = dict(obj)
    if payload is not None:
        head["bin"] = len(payload)
    data = json.dumps(head, ensure_ascii=False).encode("utf-8")
    with lock:
        sock.sendall(struct.pack(">I", len(data)) + data + (payload or b""))


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise ConnectionError("connessione chiusa")
        buf += chunk
    return bytes(buf)


def recv_frame(sock):
    (n,) = struct.unpack(">I", _recv_exact(sock, 4))
    if n > 64 * 1024 * 1024:
        raise ConnectionError("frame troppo grande")
    obj = json.loads(_recv_exact(sock, n).decode("utf-8"))
    payload = _recv_exact(sock, obj["bin"]) if obj.get("bin") else None
    return obj, payload


_fp_cache = {}


def fingerprint(path):
    """Impronta veloce: dimensione + SHA-1 del primo e dell'ultimo MB (con cache per data di modifica)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_size, st.st_mtime)
    if key in _fp_cache:
        return _fp_cache[key]
    h = hashlib.sha1(str(st.st_size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if st.st_size > 2 << 20:
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read(1 << 20))
    fp = f"{st.st_size}:{h.hexdigest()[:20]}"
    _fp_cache[key] = fp
    return fp


def media_files(show):
    """{percorso assoluto: [id cue]} dei file usati dallo show."""
    out = {}
    for c in show.all_cues():
        if getattr(c, "file", "") and os.path.isfile(c.file):
            out.setdefault(os.path.abspath(c.file), []).append(c.id)
    return out


def local_rel(path, base, fp):
    """Percorso relativo del file nella cartella dello slave."""
    if base:
        try:
            rel = os.path.relpath(path, base)
            if not rel.startswith(".."):
                return portable_rel(rel)
        except ValueError:
            pass
    return f"_esterni/{fp.split(':')[-1][:8]}_{file_basename(path)}"


def discover_masters(port=DEFAULT_PORT, timeout=1.2):
    """[{name, host, port, show}] dei master in rete (broadcast UDP sulla porta+1)."""
    from .mixers import broadcast_addresses
    from .webserver import local_addresses
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("0.0.0.0", 0))
        ips = local_addresses()
        for b in broadcast_addresses(ips) + ["127.0.0.1"]:
            try:
                s.sendto(DISCOVER_MSG, (b, port + 1))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            s.settimeout(left)
            try:
                data, (ip, _p) = s.recvfrom(4096)
            except socket.timeout:
                break
            except OSError:
                break
            try:
                info = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            if info.get("app") == "CueStage":
                host = ip if ip != "127.0.0.1" or not ips else ip
                found[(info.get("id"), host)] = dict(info, host=host)
    finally:
        s.close()
    # la stessa istanza vista da più interfacce: una sola riga (preferisce l'indirizzo non locale)
    by_id = {}
    for (iid, host), info in found.items():
        if iid not in by_id or by_id[iid]["host"].startswith("127."):
            by_id[iid] = info
    return list(by_id.values())


# =========================================================================== hub
class ClusterHub(QObject):
    """Ruolo di questa istanza nella rete: master (server), slave (client) o nessuno."""
    joinRequest = Signal(str, str, str, str)       # id client, nome, indirizzo, modalità
    clientsChanged = Signal()
    showReceived = Signal(object, object)          # dati dello show (rimappati), info hardware del master
    commandReceived = Signal(str, object, object, object)   # metodo, args, kwargs, seme
    syncProgress = Signal(str, int, int)           # testo, fatti, totale
    syncDone = Signal(bool, str)
    masterLost = Signal()
    masterBack = Signal()
    status = Signal(str)
    slaveShow = Signal(object, str)                # (master) show modificato da una secondaria, nome
    _startWatch = Signal()

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.role = None             # None | "master" | "slave"
        self.instance_id = uuid.uuid4().hex[:8]
        self.name = socket.gethostname()
        self.allow_slave_edit = False   # (master) le secondarie possono aggiornare il progetto
        self.upload_dir_fn = None    # (master) () -> cartella per i file inviati dalle secondarie
        self.can_edit = False        # (slave) il master accetta le nostre modifiche
        self.to_master = {}          # (slave) percorso locale -> percorso sul master
        self._put_wait = {}          # (slave) percorso locale -> [Event, percorso sul master | errore]
        self.show_fn = None          # () -> Show (master)
        self.hw_fn = None            # () -> dict hardware (master)
        # master
        self.server = None
        self.udp = None
        self.port = 0
        self.clients = {}            # id -> dict(sock, lock, name, addr, mode, accepted, last)
        self._pending = {}           # id -> threading.Event / risposta
        self._last_hash = None
        self._depth = 0
        self._applying = False
        self._wrapped = False
        # slave
        self.sock = None
        self.slock = threading.Lock()
        self.mode = "slave"          # "slave" | "redundancy"
        self.sync_mode = "diff"      # "full" | "diff"
        self.copy_settings = True
        self.media_dir = ""
        self.master_info = {}
        self.last_hb = 0.0
        self.lost = False
        self.lost_ms = 500
        self.hb_timer = QTimer(self)
        self.hb_timer.setInterval(HEARTBEAT_MS)
        self.hb_timer.timeout.connect(self._heartbeat)
        self.watch = QTimer(self)
        self.watch.setInterval(50)
        self.watch.timeout.connect(self._watchdog)
        self.change_timer = QTimer(self)
        self.change_timer.setInterval(200)
        self.change_timer.timeout.connect(self._check_changes)
        self._startWatch.connect(self.watch.start)          # il thread di rete avvia il timer nel thread di Qt
        self._wrap_controller()

    # ------------------------------------------------------------------ controller
    def _wrap_controller(self):
        """I metodi «REPLICATED» del controller passano dall'hub: sul master si ritrasmettono (solo la
        chiamata esterna, non quelle che fa al suo interno); sullo slave in ridondanza i comandi locali sono
        bloccati (tranne lo stop di emergenza) perché comanda il master."""
        if self._wrapped:
            return
        self._wrapped = True
        ctrl = self.ctrl
        for name in REPLICATED + GUARDED:
            orig = getattr(ctrl, name)

            def w(*a, _orig=orig, _name=name, **k):
                if self._depth == 0 and _name not in GUARDED:
                    if self.role == "slave" and self.mode == "redundancy" and not self._applying \
                            and _name not in ("stop_all",) and not self.lost:
                        self.status.emit("Ridondanza: i comandi arrivano dal master")
                        return None
                    if self.role == "master" and self.clients:
                        self._broadcast_cmd(_name, a, k)
                self._depth += 1
                try:
                    return _orig(*a, **k)
                finally:
                    self._depth -= 1
            setattr(ctrl, name, w)
        try:                                   # il timer del motore chiama _tick: va ricollegato al wrapper
            ctrl.timer.timeout.disconnect()
        except (RuntimeError, TypeError):
            pass
        ctrl.timer.timeout.connect(ctrl._tick)

    @staticmethod
    def _enc(v):
        from .model import Cue, CueList
        if isinstance(v, Cue):
            return {"$cue": v.id}
        if isinstance(v, CueList):
            return {"$list": v.id}
        if isinstance(v, (list, tuple)):
            return [ClusterHub._enc(x) for x in v]
        if isinstance(v, (int, float, str, bool)) or v is None:
            return v
        return None

    def _dec(self, v):
        show = self.ctrl.show
        if isinstance(v, dict) and "$cue" in v:
            for c in show.all_cues():
                if c.id == v["$cue"]:
                    return c
            raise KeyError(v["$cue"])
        if isinstance(v, dict) and "$list" in v:
            lst = show.list_by_id(v["$list"])
            if lst is None:
                raise KeyError(v["$list"])
            return lst
        if isinstance(v, list):
            return [self._dec(x) for x in v]
        return v

    def _broadcast_cmd(self, name, a, k):
        msg = {"t": "cmd", "fn": name, "args": [self._enc(x) for x in a],
               "kw": {kk: self._enc(vv) for kk, vv in k.items()},
               "seed": [self.ctrl.seed_base, self.ctrl.seed_count]}
        for cid, c in list(self.clients.items()):
            if c.get("accepted") and c.get("mode") == "redundancy":
                self._send_client(cid, msg)

    def apply_command(self, fn, args, kw, seed):
        """Sullo slave: esegue il comando del master (nel thread di Qt)."""
        if fn not in REPLICATED:
            return
        try:
            a = [self._dec(x) for x in args or []]
            k = {kk: self._dec(vv) for kk, vv in (kw or {}).items()}
        except KeyError:
            return                    # cue non (ancora) presente
        if seed:
            self.ctrl.seed_base, self.ctrl.seed_count = str(seed[0]), int(seed[1])
        self._applying = True
        try:
            getattr(self.ctrl, fn)(*a, **k)
        except Exception as e:
            self.status.emit(f"Comando del master «{fn}» non eseguito: {e}")
        finally:
            self._applying = False

    # ================================================================== MASTER
    def start_master(self, port, show_fn, hw_fn):
        self.stop_master()
        self.show_fn, self.hw_fn = show_fn, hw_fn
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", int(port)))
            srv.listen(8)
        except OSError as e:
            self.status.emit(f"Istanze secondarie: porta {port} non disponibile ({e})")
            return False
        self.server, self.port, self.role = srv, int(port), "master"
        threading.Thread(target=self._accept_loop, args=(srv,), daemon=True, name="cluster-accept").start()
        try:
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            u.bind(("0.0.0.0", int(port) + 1))
            self.udp = u
            threading.Thread(target=self._udp_loop, args=(u,), daemon=True, name="cluster-udp").start()
        except OSError:
            self.udp = None
        self._last_hash = None
        self.hb_timer.start()
        self.change_timer.start()
        self.clientsChanged.emit()
        return True

    def stop_master(self):
        for cid in list(self.clients):
            self._drop(cid)
        for s in (self.server, self.udp):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass
        self.server = self.udp = None
        if self.role == "master":
            self.role = None
        self.hb_timer.stop()
        self.change_timer.stop()
        self.clientsChanged.emit()

    def _udp_loop(self, u):
        while self.udp is u:
            try:
                data, addr = u.recvfrom(1024)
            except OSError:
                return
            if data.strip() == DISCOVER_MSG:
                sh = self.show_fn() if self.show_fn else None
                info = {"app": "CueStage", "id": self.instance_id, "name": self.name, "port": self.port,
                        "show": os.path.basename(sh.path) if sh is not None and sh.path else "senza titolo",
                        "slaves": sum(1 for c in self.clients.values() if c.get("accepted"))}
                try:
                    u.sendto(json.dumps(info).encode("utf-8"), addr)
                except OSError:
                    pass

    def _accept_loop(self, srv):
        while self.server is srv:
            try:
                sock, addr = srv.accept()
            except OSError:
                return
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            cid = uuid.uuid4().hex[:8]
            self.clients[cid] = {"sock": sock, "lock": threading.Lock(), "addr": addr[0], "name": "?",
                                 "mode": "slave", "accepted": False, "last": time.monotonic()}
            threading.Thread(target=self._client_loop, args=(cid,), daemon=True, name=f"cluster-{cid}").start()

    def _send_client(self, cid, obj, payload=None):
        c = self.clients.get(cid)
        if c is None:
            return False
        try:
            send_frame(c["sock"], c["lock"], obj, payload)
            return True
        except OSError:
            self._drop(cid)
            return False

    def _drop(self, cid):
        c = self.clients.pop(cid, None)
        if c is not None:
            try:
                c["sock"].close()
            except OSError:
                pass
            if c.get("accepted"):
                self.status.emit(f"Istanza secondaria «{c['name']}» scollegata")
            self.clientsChanged.emit()

    def answer(self, cid, ok):
        """Risposta della finestra di conferma (thread di Qt)."""
        ev = self._pending.get(cid)
        if ev is not None:
            ev["ok"] = ok
            ev["ev"].set()

    def _client_loop(self, cid):
        c = self.clients.get(cid)
        sock = c["sock"]
        try:
            hello, _ = recv_frame(sock)
            if hello.get("t") != "hello" or hello.get("proto") != PROTO:
                self._send_client(cid, {"t": "reject", "why": "versione del protocollo diversa"})
                raise ConnectionError("hello non valido")
            c.update(name=str(hello.get("name") or c["addr"]), mode=hello.get("mode") or "slave")
            ev = {"ev": threading.Event(), "ok": False}
            self._pending[cid] = ev
            self.joinRequest.emit(cid, c["name"], c["addr"], c["mode"])
            ev["ev"].wait(120)
            self._pending.pop(cid, None)
            if not ev["ok"]:
                self._send_client(cid, {"t": "reject", "why": "richiesta rifiutata dal master"})
                raise ConnectionError("rifiutato")
            c["accepted"] = True
            self._send_client(cid, {"t": "welcome", "name": self.name, "id": self.instance_id,
                                    "edit": self.allow_slave_edit})
            self.clientsChanged.emit()
            self.status.emit(f"Istanza secondaria «{c['name']}» collegata ({c['mode']})")
            while cid in self.clients:
                msg, _ = recv_frame(sock)
                c["last"] = time.monotonic()
                t = msg.get("t")
                if t == "get_show":
                    self._send_show(cid)
                elif t == "get_file":
                    self._send_file(cid, msg.get("path"))
                elif t == "put_begin":
                    self._receive_upload(cid, sock, msg)
                elif t == "show_up":
                    if self.allow_slave_edit:
                        self.slaveShow.emit(msg.get("data"), c["name"])
                    else:
                        self._send_client(cid, {"t": "edit_refused"})
        except (OSError, ConnectionError, ValueError, struct.error):
            pass
        finally:
            self._drop(cid)

    def _receive_upload(self, cid, sock, msg):
        """File nuovo aggiunto su una secondaria: si salva nella cartella dello show del master."""
        name = file_basename(msg.get("name") or "file")
        local = msg.get("local")
        ok = self.allow_slave_edit and self.upload_dir_fn is not None
        dest = None
        out = None
        if ok:
            d = self.upload_dir_fn()
            os.makedirs(d, exist_ok=True)
            dest = os.path.join(d, name)
            k = 1
            while os.path.exists(dest):          # mai sovrascrivere un file del master
                root, ext = os.path.splitext(name)
                dest = os.path.join(d, f"{root}_{k}{ext}")
                k += 1
            out = open(dest + ".part", "wb")
        try:
            while True:
                m, payload = recv_frame(sock)
                t = m.get("t")
                if t == "put_chunk":
                    if out is not None:
                        out.write(payload or b"")
                elif t == "put_end":
                    break
                elif t == "hb":
                    pass
        finally:
            if out is not None:
                out.close()
        if ok:
            os.replace(dest + ".part", dest)
            self._send_client(cid, {"t": "put_ok", "local": local, "path": dest})
        else:
            self._send_client(cid, {"t": "put_err", "local": local, "why": "aggiornamento del progetto non consentito"})

    def _show_message(self):
        sh = self.show_fn()
        base = os.path.dirname(os.path.abspath(sh.path)) if sh.path else ""
        data = sh.to_data(base or None)
        files = []
        for path, ids in media_files(sh).items():
            fp = fingerprint(path)
            if fp:
                files.append({"path": path, "rel": local_rel(path, base, fp), "fp": fp,
                              "size": int(fp.split(":")[0]), "cues": ids})
        try:
            self._last_hash = hashlib.sha1(json.dumps(sh.to_data(None), sort_keys=True, default=str)
                                           .encode()).hexdigest()
        except Exception:
            pass
        return {"t": "show", "data": data, "base": base, "files": files, "edit": self.allow_slave_edit,
                "name": os.path.basename(sh.path) if sh.path else "senza-titolo.cueshow",
                "playheads": dict(self.ctrl.playheads), "hw": self.hw_fn() if self.hw_fn else {},
                "seed": [self.ctrl.seed_base, self.ctrl.seed_count]}

    def _send_show(self, cid):
        self._send_client(cid, self._show_message())

    def _send_file(self, cid, path):
        sh = self.show_fn()
        allowed = media_files(sh)
        if path not in allowed:
            self._send_client(cid, {"t": "file_err", "path": path, "why": "file non usato dallo show"})
            return
        try:
            size = os.path.getsize(path)
            self._send_client(cid, {"t": "file_begin", "path": path, "size": size})
            with open(path, "rb") as f:
                while True:
                    block = f.read(CHUNK)
                    if not block:
                        break
                    if not self._send_client(cid, {"t": "chunk"}, block):
                        return
            self._send_client(cid, {"t": "file_end", "path": path, "fp": fingerprint(path)})
        except OSError as e:
            self._send_client(cid, {"t": "file_err", "path": path, "why": str(e)})

    def _heartbeat(self):
        now = time.monotonic()
        msg = {"t": "hb", "ts": time.time()}
        for cid, c in list(self.clients.items()):
            if not c.get("accepted"):
                continue
            if now - c["last"] > 5.0:             # lo slave non risponde più
                self._drop(cid)
                continue
            self._send_client(cid, msg)

    def _check_changes(self):
        """Modifiche del progetto sul master → subito alle secondarie."""
        if not any(c.get("accepted") for c in self.clients.values()) or self.show_fn is None:
            self._last_hash = None
            return
        sh = self.show_fn()
        try:
            blob = json.dumps(sh.to_data(None), sort_keys=True, default=str)
        except Exception:
            return
        h = hashlib.sha1(blob.encode()).hexdigest()
        if h == self._last_hash:
            return
        if self._last_hash is None:
            self._last_hash = h
            return
        msg = self._show_message()
        msg["update"] = True
        for cid, c in list(self.clients.items()):
            if c.get("accepted"):
                self._send_client(cid, msg)

    def client_list(self):
        return [(c["name"], c["addr"], c["mode"]) for c in self.clients.values() if c.get("accepted")]

    # ================================================================== SLAVE
    def connect_slave(self, host, port, mode, sync_mode, copy_settings, media_dir, lost_ms=500):
        """Avvia il collegamento (thread); l'esito arriva con syncDone / showReceived."""
        self.disconnect_slave()
        self.stop_master()
        self.mode, self.sync_mode, self.copy_settings = mode, sync_mode, copy_settings
        self.media_dir = media_dir
        self.lost_ms = max(200, int(lost_ms))
        self.master_info = {"host": host, "port": int(port)}
        threading.Thread(target=self._slave_loop, args=(host, int(port)), daemon=True, name="cluster-slave").start()

    def disconnect_slave(self):
        s, self.sock = self.sock, None
        self.watch.stop()
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        if self.role == "slave":
            self.role = None
        self.lost = False

    def _send(self, obj):
        if self.sock is not None:
            send_frame(self.sock, self.slock, obj)

    def _slave_loop(self, host, port):
        sock = None
        try:
            self.syncProgress.emit(f"Collegamento a {host}:{port}…", 0, 0)
            sock = socket.create_connection((host, port), timeout=5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = sock
            send_frame(sock, self.slock, {"t": "hello", "proto": PROTO, "name": self.name, "mode": self.mode,
                                          "id": self.instance_id})
            self.syncProgress.emit("In attesa della conferma sul master…", 0, 0)
            sock.settimeout(130)
            msg, _ = recv_frame(sock)
            if msg.get("t") != "welcome":
                raise ConnectionError(msg.get("why") or "rifiutato")
            self.master_info.update(name=msg.get("name"), id=msg.get("id"))
            self.can_edit = bool(msg.get("edit"))
            sock.settimeout(None)
            self.role = "slave"
            self.last_hb = time.monotonic()
            self.lost = False
            self._startWatch.emit()
            self._send({"t": "get_show"})
            first = True
            self._queue = []
            while self.sock is sock:
                msg, payload = self._next(sock)
                if msg.get("t") == "show":
                    self._sync_show(sock, msg, full=first and self.sync_mode == "full", first=first)
                    first = False
        except (OSError, ConnectionError, ValueError, struct.error) as e:
            if self.role != "slave":
                self.syncDone.emit(False, str(e))
            elif self.sock is sock:
                self.status.emit(f"Collegamento al master interrotto: {e}")
                if not self.lost:                          # connessione chiusa = master perso subito
                    self.lost = True
                    self.masterLost.emit()
        finally:
            if self.sock is sock and sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def _next(self, sock):
        """Messaggio successivo; heartbeat e comandi si smistano subito (anche durante un download)."""
        if self._queue:
            return self._queue.pop(0), None
        while True:
            msg, payload = recv_frame(sock)
            t = msg.get("t")
            if t == "hb":
                self.last_hb = time.monotonic()
                if self.lost:
                    self.lost = False
                    self.masterBack.emit()
                try:
                    self._send({"t": "hb"})
                except OSError:
                    pass
                continue
            if t == "cmd":
                self.last_hb = time.monotonic()
                self.commandReceived.emit(msg.get("fn"), msg.get("args"), msg.get("kw"), msg.get("seed"))
                continue
            if self._dispatch_upload_reply(msg):
                continue
            return msg, payload

    def _dispatch_upload_reply(self, msg):
        t = msg.get("t")
        if t in ("put_ok", "put_err"):
            w = self._put_wait.get(msg.get("local"))
            if w is not None:
                w[1] = msg.get("path") if t == "put_ok" else RuntimeError(msg.get("why") or "rifiutato")
                w[0].set()
            return True
        if t == "edit_refused":
            self.can_edit = False
            self.status.emit("Il master non accetta modifiche dalle secondarie")
            return True
        return False

    def _sync_show(self, sock, msg, full, first):
        files = msg.get("files") or []
        for f in files:                          # «/» dal master (anche Windows ↔ Linux/macOS), mai fuori cartella
            f["rel"] = local_path(f.get("rel") or file_basename(f.get("path", "")))
        os.makedirs(self.media_dir, exist_ok=True)
        need = []
        for f in files:
            local = os.path.join(self.media_dir, f["rel"])
            if full or not os.path.isfile(local) or fingerprint(local) != f["fp"]:
                need.append(f)
        total = sum(f["size"] for f in need)
        done = 0
        errors = []
        from_here = {m: l for l, m in self.to_master.items()}     # file caricati da qui: copia locale
        for f in list(need):
            src = from_here.get(f["path"])
            local = os.path.join(self.media_dir, f["rel"])
            if src and os.path.isfile(src) and fingerprint(src) == f["fp"]:
                import shutil
                os.makedirs(os.path.dirname(local), exist_ok=True)
                if os.path.abspath(src) != os.path.abspath(local):
                    shutil.copy2(src, local)
                need.remove(f)
        total = sum(f["size"] for f in need)
        for i, f in enumerate(need, 1):
            local = os.path.join(self.media_dir, f["rel"])
            os.makedirs(os.path.dirname(local), exist_ok=True)
            tmp = local + ".part"
            self.syncProgress.emit(f"File {i}/{len(need)}: {os.path.basename(f['rel'])}", done, total)
            self._send({"t": "get_file", "path": f["path"]})
            out = None
            while True:
                m, payload = recv_frame(sock)
                t = m.get("t")
                if t == "hb":
                    self.last_hb = time.monotonic()
                    self._send({"t": "hb"})
                elif t == "cmd":
                    self.commandReceived.emit(m.get("fn"), m.get("args"), m.get("kw"), m.get("seed"))
                elif t == "show":
                    self._queue.append(m)          # aggiornamento arrivato durante il download
                elif self._dispatch_upload_reply(m):
                    pass
                elif t == "file_begin":
                    out = open(tmp, "wb")
                elif t == "chunk" and out is not None:
                    out.write(payload or b"")
                    done += len(payload or b"")
                    self.syncProgress.emit(f"File {i}/{len(need)}: {os.path.basename(f['rel'])}", done, total)
                elif t == "file_end":
                    if out is not None:
                        out.close()
                        os.replace(tmp, local)
                    break
                elif t == "file_err":
                    if out is not None:
                        out.close()
                    errors.append(f"{f['rel']}: {m.get('why')}")
                    break
        # percorsi dello show rimappati sulla cartella locale
        remap = {f["path"]: os.path.join(self.media_dir, f["rel"]) for f in files}
        self.to_master.update({v: k for k, v in remap.items()})
        self.can_edit = bool(msg.get("edit", self.can_edit))
        info = {"remap": remap, "base": msg.get("base"), "name": msg.get("name"), "hw": msg.get("hw") or {},
                "playheads": msg.get("playheads") or {}, "seed": msg.get("seed"), "first": first,
                "errors": errors, "downloaded": len(need), "files": len(files), "update": bool(msg.get("update"))}
        self.showReceived.emit(msg.get("data"), info)
        if first:
            self.syncDone.emit(True, f"{len(need)} file scaricati su {len(files)}"
                                     + (f", {len(errors)} errori" if errors else ""))

    # ------------------------------------------------------------------ slave → master
    def push_show(self, show):
        """Modifiche fatte sulla secondaria (se il master lo consente): carica i file nuovi e manda lo show
        con i percorsi del master. Gira in un thread: l'interfaccia non aspetta."""
        if self.role != "slave" or not self.can_edit or self.sock is None or self.lost:
            return
        data = show.to_data(None)
        threading.Thread(target=self._push_job, args=(data,), daemon=True, name="cluster-push").start()

    def _push_job(self, data):
        try:
            cues = list(data.get("cues") or [])
            for p in data.get("panels") or []:
                cues += p.get("cues") or []
            for d in cues:
                f = d.get("file") or ""
                d.pop("file_rel", None)
                if not f:
                    continue
                if f in self.to_master:
                    d["file"] = self.to_master[f]
                elif os.path.isfile(f):
                    d["file"] = self._upload(f)
                    self.to_master[f] = d["file"]
            if not self.copy_settings:
                data.pop("settings", None)
            self._send({"t": "show_up", "data": data})
            self.status.emit("Modifiche inviate al master")
        except (OSError, RuntimeError) as e:
            self.status.emit(f"Modifiche non inviate al master: {e}")

    def _upload(self, path):
        ev = [threading.Event(), None]
        self._put_wait[path] = ev
        self.status.emit(f"Invio al master: {os.path.basename(path)}…")
        self._send({"t": "put_begin", "name": os.path.basename(path), "local": path,
                    "size": os.path.getsize(path)})
        with open(path, "rb") as f:
            while True:
                block = f.read(CHUNK)
                if not block:
                    break
                send_frame(self.sock, self.slock, {"t": "put_chunk"}, block)
        self._send({"t": "put_end", "local": path})
        if not ev[0].wait(60):
            raise RuntimeError(f"il master non conferma {os.path.basename(path)}")
        self._put_wait.pop(path, None)
        if isinstance(ev[1], Exception):
            raise ev[1]
        return ev[1]

    def _watchdog(self):
        if self.role != "slave" or self.lost:
            return
        if (time.monotonic() - self.last_hb) * 1000 > self.lost_ms:
            self.lost = True
            self.masterLost.emit()

    def promote(self):
        """Lo slave diventa indipendente (e master, se le istanze secondarie sono abilitate)."""
        self.disconnect_slave()
        self.role = None
        self.lost = False
