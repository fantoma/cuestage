"""Profili OSC: dispositivi (console luci, mixer, software…) con i loro comandi già pronti.

Un profilo è un file JSON in cuestage/osc_profiles/ o in ~/.local/share/cuestage/osc_profiles/:
  id, name, port (porta OSC predefinita), discover (facoltativo: {"type": "artnet", "match": [...]}),
  notes, commands: [{id, label, address, args, params: [{id, label, type int|float|enum|text, …}], help}]
Indirizzo e argomenti usano i parametri tra graffe: "/pb/{pb}/go".
"""
import json
import os
import socket
import struct
import time

from .system import data_dir

BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "osc_profiles")
USER_DIR = data_dir("osc_profiles")

_cache = None


def profiles(reload=False):
    """{id: profilo}"""
    global _cache
    if _cache is not None and not reload:
        return _cache
    out = {}
    for d in (BUILTIN_DIR, USER_DIR):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(d, fn), encoding="utf-8") as f:
                        p = json.load(f)
                    if p.get("id") and p.get("commands"):
                        out[p["id"]] = p
                except (OSError, ValueError) as e:
                    print(f"Profilo OSC {fn} non valido: {e}")
    _cache = out
    return out


def command(profile_id, cmd_id):
    p = profiles().get(profile_id)
    if not p:
        return None, None
    for c in p["commands"]:
        if c["id"] == cmd_id:
            return p, c
    return p, None


def _fmt(v, param):
    t = param.get("type")
    if t == "int":
        return str(int(float(v)))
    if t == "float":
        return f"{float(v):g}"
    return str(v)


def param_values(cmd, values):
    vals = {}
    for prm in cmd.get("params") or []:
        v = (values or {}).get(prm["id"], prm.get("default", ""))
        vals[prm["id"]] = _fmt(v, prm)
    return vals


def render(profile_id, cmd_id, values):
    """(indirizzo, testo degli argomenti) del comando con i valori dei parametri."""
    p, c = command(profile_id, cmd_id)
    if c is None:
        raise KeyError(f"comando «{cmd_id}» non presente nel profilo «{profile_id}»")
    vals = param_values(c, values)
    address = c["address"].format(**vals)
    args = " ".join(a.format(**vals) for a in c.get("args") or [])
    return address, args


def describe(profile_id, cmd_id, values):
    """Testo breve per la lista delle cue: «GO di un playback · Playback 3»."""
    p, c = command(profile_id, cmd_id)
    if c is None:
        return cmd_id or "—"
    vals = param_values(c, values)
    parts = []
    for prm in c.get("params") or []:
        v = vals[prm["id"]]
        if prm.get("type") == "enum":
            v = dict((str(a), b) for a, b in prm.get("items") or []).get(str(v), v)
        parts.append(f"{prm['label']} {v}")
    return c["label"] + (" · " + ", ".join(parts) if parts else "")


# ---------------------------------------------------------------------------- ricerca in rete
def _artpoll_packet():
    return b"Art-Net\x00" + struct.pack("<H", 0x2000) + bytes([0, 14, 0x06, 0x00])


def discover_artnet(match=(), timeout=1.5):
    """Nodi Art-Net che rispondono ad ArtPoll: [{host, name, long, match}] (prima quelli che corrispondono)."""
    from .mixers import broadcast_addresses
    from .webserver import local_addresses
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", 6454))           # le risposte ArtPollReply arrivano sulla porta 6454
    except OSError:
        s.bind(("0.0.0.0", 0))
    found = {}
    try:
        for b in broadcast_addresses(local_addresses()) + ["2.255.255.255", "10.255.255.255"]:
            try:
                s.sendto(_artpoll_packet(), (b, 6454))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            s.settimeout(left)
            try:
                data, (ip, _p) = s.recvfrom(2048)
            except (socket.timeout, OSError):
                break
            if len(data) < 108 or not data.startswith(b"Art-Net\x00") or struct.unpack("<H", data[8:10])[0] != 0x2100:
                continue
            node_ip = ".".join(str(x) for x in data[10:14])
            short = data[26:44].split(b"\x00")[0].decode("latin-1", "replace").strip()
            long = data[44:108].split(b"\x00")[0].decode("latin-1", "replace").strip()
            text = f"{short} {long}".lower()
            hit = any(m.lower() in text for m in match) if match else True
            host = node_ip if node_ip != "0.0.0.0" else ip
            found[host] = {"host": host, "name": short or long or host, "long": long, "match": hit}
    finally:
        s.close()
    return sorted(found.values(), key=lambda r: (not r["match"], r["host"]))


def discover(profile):
    d = (profile or {}).get("discover") or {}
    if d.get("type") == "artnet":
        return discover_artnet(d.get("match") or ())
    return []
