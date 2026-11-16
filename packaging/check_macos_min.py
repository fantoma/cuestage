"""Controlla che ogni libreria e programma dentro CueStage.app giri sulla versione minima di macOS dichiarata.

    python3 packaging/check_macos_min.py dist/CueStage.app 12.0

pip, su un Mac recente, sceglie le librerie compilate per quel macOS (es. numpy e scipy «macosx_14_0»,
PySide6 6.10+ «macosx_13_0»): l'app si costruisce ma su un Mac più vecchio si chiude appena aperta, senza
messaggi. Qui si legge con otool la versione minima (minos / LC_VERSION_MIN_MACOSX) di ogni file Mach-O.
Esce con errore se una libreria Python o Qt ne richiede una più alta; per ffmpeg/ffprobe solo un avviso.
"""
import os
import re
import subprocess
import sys

MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def ver(s):
    return tuple(int(x) for x in s.split(".")[:2])


def is_macho(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) in MAGIC
    except OSError:
        return False


def min_versions(otool_text):
    """Versioni minime trovate nell'uscita di «otool -l» (una per architettura)."""
    out = []
    lines = otool_text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*minos\s+(\d+(?:\.\d+)*)", line)
        if m:
            out.append(m.group(1))
            continue
        if "LC_VERSION_MIN_MACOSX" in line:
            for nxt in lines[i + 1:i + 5]:
                m = re.match(r"\s*version\s+(\d+(?:\.\d+)*)", nxt)
                if m:
                    out.append(m.group(1))
                    break
    return out


def main(app, target):
    tgt = ver(target)
    bad, warn, n = [], [], 0
    for d, _dirs, files in os.walk(app):
        for f in files:
            p = os.path.join(d, f)
            if os.path.islink(p) or not is_macho(p):
                continue
            n += 1
            txt = subprocess.run(["otool", "-arch", "all", "-l", p], capture_output=True, text=True).stdout
            vs = min_versions(txt)
            if vs and max(ver(v) for v in vs) > tgt:
                rel = os.path.relpath(p, app)
                item = f"{rel}: richiede macOS {max(vs, key=ver)}"
                (warn if os.path.basename(p) in ("ffmpeg", "ffprobe") else bad).append(item)
    print(f"{n} file Mach-O controllati (minimo dichiarato: macOS {target})")
    for w in warn:
        print(f"::warning::{w}")
    for b in bad:
        print(f"::error::{b}")
    if bad:
        sys.exit(f"{len(bad)} file richiedono un macOS più recente di {target}: l'app non partirebbe")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "12.0")
