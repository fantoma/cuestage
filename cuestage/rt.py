"""Tempo reale per l'audio: priorità del thread che calcola i blocchi e garbage collector di Python.

- Priorità: il callback di PortAudio chiama raise_thread_priority() al primo blocco, dal proprio thread.
  Linux: SCHED_FIFO (serve il permesso «rtprio», es. utente nel gruppo audio con /etc/security/limits.d),
  altrimenti nice più basso se concesso; Windows: MMCSS «Pro Audio» e THREAD_PRIORITY_TIME_CRITICAL;
  macOS: il thread di CoreAudio è già in tempo reale.
- Garbage collector: dopo l'apertura di uno show gc_settle() raccoglie e «congela» gli oggetti dello show
  (gc.freeze), così le raccolte complete non li riesaminano più e restano brevi; le pause del collector
  vengono misurate (gc.callbacks) per la Diagnostica.
- L'intervallo di cambio del GIL scende a 2 ms: il thread audio aspetta meno l'interfaccia.
"""
import gc
import os
import sys
import threading
import time

from .i18n import tr

SWITCH_INTERVAL = 0.002
RT_PRIORITY = 70

_gc = {"count": 0, "max": 0.0, "last": 0.0, "total": 0.0, "max_gen": 0, "frozen": 0, "t0": None,
       "settle_ms": 0.0}
_lock = threading.Lock()


def _gc_cb(phase, info):
    if phase == "start":
        _gc["t0"] = time.perf_counter()
        return
    t0 = _gc["t0"]
    if t0 is None:
        return
    dt = time.perf_counter() - t0
    _gc["t0"] = None
    _gc["count"] += 1
    _gc["last"] = dt
    _gc["total"] += dt
    if dt > _gc["max"]:
        _gc["max"] = dt
        _gc["max_gen"] = int(info.get("generation", 0))


def install():
    """All'avvio: misura delle pause del garbage collector e cambio del GIL più frequente."""
    if _gc_cb not in gc.callbacks:
        gc.callbacks.append(_gc_cb)
    try:
        sys.setswitchinterval(SWITCH_INTERVAL)
    except (AttributeError, ValueError):
        pass


def gc_settle():
    """Dopo l'apertura di uno show: libera lo show precedente e congela gli oggetti rimasti."""
    t = time.perf_counter()
    if hasattr(gc, "freeze"):
        gc.unfreeze()                 # lo show precedente torna raccoglibile
        gc.collect()
        gc.freeze()
        _gc["frozen"] = gc.get_freeze_count()
    else:                             # (Python senza gc.freeze: almeno una raccolta ora, non durante lo show)
        gc.collect()
    _gc["settle_ms"] = (time.perf_counter() - t) * 1000


def gc_stats():
    return dict(_gc)


def reset_gc_stats():
    _gc.update(count=0, max=0.0, last=0.0, total=0.0, max_gen=0)


# ------------------------------------------------------------------ priorità del thread audio
def _linux_raise():
    try:
        p = max(1, min(RT_PRIORITY, os.sched_get_priority_max(os.SCHED_FIFO)))
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(p))   # 0 = questo thread (Linux)
        return True, tr("tempo reale (SCHED_FIFO {0})", p)
    except (PermissionError, OSError, AttributeError):
        pass
    try:
        tid = threading.get_native_id()
        os.setpriority(os.PRIO_PROCESS, tid, -11)
        return True, tr("priorità alta (nice −11), tempo reale non concesso")
    except (PermissionError, OSError, AttributeError):
        pass
    return False, tr("normale: tempo reale non concesso (aggiungi l'utente al gruppo «audio» con rtprio, "
                     "vedi README)")


def _windows_raise():
    import ctypes
    ok_mm = False
    try:
        avrt = ctypes.WinDLL("avrt")
        idx = ctypes.c_ulong(0)
        avrt.AvSetMmThreadCharacteristicsW.restype = ctypes.c_void_p
        ok_mm = bool(avrt.AvSetMmThreadCharacteristicsW("Pro Audio", ctypes.byref(idx)))
    except Exception:
        ok_mm = False
    try:
        k = ctypes.windll.kernel32
        ok_tp = bool(k.SetThreadPriority(k.GetCurrentThread(), 15))       # THREAD_PRIORITY_TIME_CRITICAL
    except Exception:
        ok_tp = False
    if ok_mm or ok_tp:
        return True, tr("tempo reale (MMCSS «Pro Audio»)") if ok_mm else tr("priorità massima del thread")
    return False, tr("normale: priorità non cambiata")


def raise_thread_priority():
    """Da chiamare dal thread audio. Restituisce (riuscito, descrizione)."""
    try:
        if sys.platform.startswith("linux"):
            return _linux_raise()
        if sys.platform.startswith("win"):
            return _windows_raise()
        if sys.platform == "darwin":
            return True, tr("tempo reale (thread di CoreAudio)")
    except Exception as e:                       # mai bloccare l'audio per questo
        return False, str(e)
    return False, tr("normale")
