#!/usr/bin/env python3
"""Diagnostica del collegamento a OBS Studio (obs-websocket 5), passo per passo.

    .venv/bin/python tools/obs_check.py [indirizzo] [porta] [password]
    (predefiniti: localhost 4455, nessuna password)

Stampa cosa succede a ogni passo (TCP, handshake WebSocket, Hello, Identify, GetVersion): utile quando
CueStage dice «collegamento a OBS non riuscito».
"""
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cuestage.mixers import WebSocketClient  # noqa: E402


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 4455
    pw = sys.argv[3] if len(sys.argv) > 3 else ""
    print(f"1. TCP {host}:{port} …")
    for fam, _t, _p, _c, addr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        try:
            with socket.create_connection(addr[:2], timeout=2):
                print(f"   {addr[0]}: aperta")
        except OSError as e:
            print(f"   {addr[0]}: {e}")
    for proto in ("obswebsocket.json", None):
        print(f"2. handshake WebSocket (sottoprotocollo: {proto or 'nessuno'}) …")
        t0 = time.monotonic()
        try:
            ws = WebSocketClient(host, port, timeout=5, protocol=proto)
        except Exception as e:  # noqa: BLE001
            print(f"   ERRORE: {e!r}")
            continue
        print("   risposta:\n      " + ws.response.replace("\r\n", "\n      "))
        ws.settimeout(8)
        print("3. attesa del Hello …")
        try:
            raw = ws.recv()
        except Exception as e:  # noqa: BLE001
            print(f"   ERRORE dopo {time.monotonic() - t0:.1f} s: {e!r}  (buffer: {ws.buf[:200]!r})")
            ws.close()
            continue
        print(f"   {raw}")
        hello = json.loads(raw or "{}")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = (hello.get("d") or {}).get("authentication")
        if auth:
            import importlib.util
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "cuestage", "mixer_plugins", "obs_studio.py")
            spec = importlib.util.spec_from_file_location("obs_studio", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            ident["authentication"] = mod.obs_auth(pw, auth["salt"], auth["challenge"])
            print("   OBS chiede la password" + ("" if pw else " (non indicata: terzo argomento)"))
        print("4. Identify …")
        ws.send(json.dumps({"op": 1, "d": ident}))
        try:
            print(f"   {ws.recv()}")
            ws.send(json.dumps({"op": 6, "d": {"requestType": "GetVersion", "requestId": "1"}}))
            print("5. GetVersion …")
            print(f"   {ws.recv()}")
        except Exception as e:  # noqa: BLE001
            print(f"   ERRORE: {e!r}")
        ws.close()
        return
    print("Nessun collegamento riuscito.")


if __name__ == "__main__":
    main()
