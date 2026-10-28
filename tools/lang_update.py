#!/usr/bin/env python3
"""Aggiorna i file di lingua (cuestage/lang/*.json) con i testi tr("…") del programma.

    python3 tools/lang_update.py            aggiunge i testi nuovi (vuoti = da tradurre) e toglie quelli spariti
    python3 tools/lang_update.py --check    segnala soltanto: testi mancanti, segnaposto {0}… diversi
    python3 tools/lang_update.py --new xx "Nome lingua"   crea il file di una nuova lingua

Un testo vuoto nel file di lingua resta in italiano nel programma.
"""
import ast
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANG = os.path.join(ROOT, "cuestage", "lang")
PH = re.compile(r"\{(\d+)[^{}]*\}")


def source_strings():
    """{testo italiano: [file:riga, …]} di tutti i tr("…") con testo letterale."""
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "cuestage", "**", "*.py"), recursive=True)):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr" and n.args:
                a = n.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.setdefault(a.value, []).append(f"{os.path.relpath(path, ROOT)}:{n.lineno}")
    return out


def placeholders(s):
    return sorted(set(PH.findall(s)))


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")


def main():
    args = sys.argv[1:]
    src = source_strings()
    if args[:1] == ["--new"]:
        code, name = args[1], (args[2] if len(args) > 2 else args[1])
        path = os.path.join(LANG, f"{code}.json")
        if os.path.exists(path):
            sys.exit(f"{path} esiste già")
        save(path, {"_meta": {"name": name, "code": code}, "strings": {k: "" for k in src}})
        print(f"creato {path}: {len(src)} testi da tradurre")
        return
    check = args[:1] == ["--check"]
    bad = 0
    for path in sorted(glob.glob(os.path.join(LANG, "*.json"))):
        data = load(path)
        strings = data.get("strings") or {}
        missing = [k for k in src if not strings.get(k)]
        unused = [k for k in strings if k not in src]
        wrong = [k for k, v in strings.items() if v and k in src and placeholders(k) != placeholders(v)]
        print(f"{os.path.basename(path)}: {len(src) - len(missing)}/{len(src)} tradotti, {len(missing)} mancanti, "
              f"{len(unused)} non più usati, {len(wrong)} con segnaposto diversi")
        for k in wrong:
            print(f"   segnaposto diversi: {k!r} → {strings[k]!r}")
        bad += len(wrong)
        if not check:
            new = {k: strings.get(k, "") for k in src}
            data["strings"] = new
            save(path, data)
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
