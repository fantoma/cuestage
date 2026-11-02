"""Bundle dello show: un unico file ZIP con lo show e tutti i file multimediali (di tutte le versioni),
per replicare il progetto su un altro computer (File › Export bundle / Import bundle).

Contenuto dello ZIP:
  cuestage-bundle.json   descrizione (app, versione, nome dello show, data, numero di file)
  <nome>.cueshow         lo show, con i percorsi dei file relativi alla cartella del bundle
  <percorsi relativi>    i file che stanno nella cartella dello show (stessa struttura)
  _esterni/…             i file che stanno fuori dalla cartella dello show
"""
import json
import os
import time
import zipfile

from .i18n import tr
from .system import file_basename, local_path, portable_rel

MANIFEST = "cuestage-bundle.json"
BLOCK = 1 << 20
MISSING_PREFIX = "/__cuestage_bundle__/"     # percorso assoluto inesistente: al caricamento vale «file_rel»


class BundleError(Exception):
    pass


def _cue_dicts(data):
    """Tutte le cue (dict) dei dati di uno show: MAIN, PANEL, GRID e le altre versioni."""
    yield from data.get("cues") or []
    for p in data.get("panels") or []:
        yield from p.get("cues") or []
    for v in data.get("versions") or []:
        yield from v.get("cues") or []
        for p in v.get("panels") or []:
            yield from p.get("cues") or []


def _arcname(path, base, used):
    from .cluster import fingerprint, local_rel
    rel = local_rel(path, base, fingerprint(path) or "0:00000000")
    rel = portable_rel(rel)
    if rel == MANIFEST or rel.lower().endswith(".cueshow") and "/" not in rel:
        rel = "_esterni/" + rel
    root, ext = os.path.splitext(rel)
    k = 1
    while rel in used and used[rel] != path:     # due file diversi con lo stesso nome
        rel = f"{root}_{k}{ext}"
        k += 1
    used[rel] = path
    return rel


def export_bundle(show, zip_path, progress=None):
    """Scrive il bundle. progress(fatti, totale, nome) -> False per annullare.
    Restituisce (file inclusi, [file mancanti])."""
    base = os.path.dirname(os.path.abspath(show.path)) if show.path else None
    data = show.to_data(None)
    data["original_path"] = None
    used, files, missing = {}, {}, []
    for d in _cue_dicts(data):
        f = d.get("file") or ""
        d.pop("file_rel", None)
        if not f:
            continue
        if not os.path.isfile(f):
            if f not in missing:
                missing.append(f)
            continue
        ap = os.path.abspath(f)
        if ap not in files:
            files[ap] = _arcname(ap, base, used)
        d["file"] = MISSING_PREFIX + files[ap]
        d["file_rel"] = files[ap]
    show_name = file_basename(show.path) if show.path else "show.cueshow"
    if not show_name.lower().endswith(".cueshow"):
        show_name += ".cueshow"
    from . import __version__
    manifest = {"app": "CueStage", "bundle": 1, "version": __version__, "show": show_name,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"), "files": len(files), "missing": missing}
    total = sum(os.path.getsize(p) for p in files) or 1
    done = 0
    tmp = zip_path + ".part"
    try:
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False), zipfile.ZIP_DEFLATED)
            zf.writestr(show_name, json.dumps(data, indent=2, ensure_ascii=False), zipfile.ZIP_DEFLATED)
            for path, arc in files.items():
                info = zipfile.ZipInfo.from_file(path, arc)
                info.compress_type = zipfile.ZIP_STORED        # audio e video sono già compressi
                with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                    while True:
                        block = src.read(BLOCK)
                        if not block:
                            break
                        dst.write(block)
                        done += len(block)
                        if progress is not None and progress(done, total, arc) is False:
                            raise BundleError(tr("annullato"))
        os.replace(tmp, zip_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return len(files), missing


def bundle_info(zip_path):
    """Descrizione del bundle (manifest) e nome dello show contenuto."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            info = {}
            if MANIFEST in names:
                info = json.loads(zf.read(MANIFEST).decode("utf-8"))
            show = info.get("show")
            if not show or show not in names:
                show = next((n for n in names if n.lower().endswith(".cueshow") and "/" not in n), None)
            if show is None:
                raise BundleError(tr("nel file non c'è uno show CueStage"))
            info["show"] = show
            return info
    except zipfile.BadZipFile as e:
        raise BundleError(tr("non è un file ZIP valido ({0})", e))


def import_bundle(zip_path, dest_dir, progress=None):
    """Estrae il bundle in dest_dir (mai fuori da quella cartella) e restituisce il percorso dello show."""
    info = bundle_info(zip_path)
    os.makedirs(dest_dir, exist_ok=True)
    root = os.path.abspath(dest_dir)
    with zipfile.ZipFile(zip_path) as zf:
        items = [i for i in zf.infolist() if not i.is_dir()]
        total = sum(i.file_size for i in items) or 1
        done = 0
        for i in items:
            target = os.path.abspath(os.path.join(root, local_path(i.filename)))
            if not (target == root or target.startswith(root + os.sep)):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(i) as src, open(target + ".part", "wb") as dst:
                while True:
                    block = src.read(BLOCK)
                    if not block:
                        break
                    dst.write(block)
                    done += len(block)
                    if progress is not None and progress(done, total, i.filename) is False:
                        dst.close()
                        os.remove(target + ".part")
                        raise BundleError(tr("annullato"))
            os.replace(target + ".part", target)
            if i.date_time:
                try:
                    t = time.mktime(i.date_time + (0, 0, -1))
                    os.utime(target, (t, t))
                except (OverflowError, ValueError, OSError):
                    pass
    return os.path.join(root, local_path(info["show"]))
