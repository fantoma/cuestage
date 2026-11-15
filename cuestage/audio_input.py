"""Ingresso audio fisico (microfono, linea) per i bus effetti: un flusso PortAudio in ingresso aperto in un
thread (se la scheda non risponde l'interfaccia non si blocca) e una coda di campioni letta dal mixer.

La coda tiene al massimo ~100 ms: se l'ingresso corre più dell'uscita (schede o orologi diversi) i campioni
più vecchi si scartano; se resta indietro si riparte con un piccolo margine. Così la latenza resta bassa e
costante anche senza ricampionamento."""
import threading

import numpy as np

from .i18n import tr
from .media import SR

MAX_LAG = SR // 10          # 100 ms al massimo in coda
MARGIN = 512                # margine prima di cominciare a leggere (contro i buchi)


class LiveInput:
    def __init__(self, device="", ch=1, ch2=0):
        self.device, self.ch, self.ch2 = device or "", max(1, int(ch or 1)), max(0, int(ch2 or 0))
        self.stream = None
        self.error = ""
        self.lock = threading.Lock()
        self.buf = np.zeros((0, 2), np.float32)
        self.primed = False
        self.meter = np.zeros(2, np.float32)
        self._gen = 0

    def same(self, device, ch, ch2):
        return (device or "", max(1, int(ch or 1)), max(0, int(ch2 or 0))) == (self.device, self.ch, self.ch2)

    # ---------------------------------------------------------------- apertura / chiusura
    OPEN_TIMEOUT = 2.0

    def open(self):
        self._gen += 1
        gen = self._gen
        done = threading.Event()

        def job():
            try:
                self._open_now(gen)
            finally:
                done.set()
        threading.Thread(target=job, daemon=True, name="bus-input-open").start()
        if not done.wait(self.OPEN_TIMEOUT):
            self.error = tr("l'ingresso non risponde: si riprova in sottofondo")

    def _open_now(self, gen):
        from .audio_engine import sd
        from .ducking import find_input
        if sd is None:
            self.error = tr("PortAudio non installato")
            return
        dev = find_input(self.device) if self.device else None
        if self.device and dev is None:
            self.error = tr("Scheda in ingresso «{0}» non trovata", self.device.split('|', 1)[-1])
            return
        try:
            if dev is None:
                info = sd.query_devices(kind="input")
                max_ch = int(info.get("max_input_channels") or 0)
            else:
                max_ch = dev["max_ch"]
            need = max(self.ch, self.ch2)
            if need > max_ch:
                self.error = tr("la scheda ha {0} canali in ingresso: il canale {1} non esiste", max_ch, need)
                return
            err = None
            for n in dict.fromkeys((need, max_ch)):   # alcune schede «hw:» vogliono tutti i canali
                try:
                    st = sd.InputStream(samplerate=SR, channels=n, dtype="float32", blocksize=256,
                                        device=dev["index"] if dev else None, callback=self._cb)
                    st.start()
                    if gen != self._gen:
                        _close(st)
                        return
                    self.stream = st
                    self.error = ""
                    return
                except Exception as e:
                    err = e
            raise err
        except Exception as e:
            if gen == self._gen:
                self.error = tr("Impossibile aprire l'ingresso: {0}", e)

    def close(self):
        self._gen += 1
        s, self.stream = self.stream, None
        if s is not None:
            threading.Thread(target=_close, args=(s,), daemon=True, name="bus-input-close").start()
        with self.lock:
            self.buf = np.zeros((0, 2), np.float32)
            self.primed = False

    # ---------------------------------------------------------------- campioni
    def _cb(self, indata, frames, t, st):
        a = indata[:, self.ch - 1] if indata.ndim == 2 else indata.reshape(-1)
        b = indata[:, self.ch2 - 1] if (self.ch2 and indata.ndim == 2 and indata.shape[1] >= self.ch2) else a
        self.push(np.stack((a, b), axis=1))

    def push(self, block):
        """Un blocco d'ingresso (frames × 2, o mono): in coda per il mixer (usato anche dalle prove)."""
        x = np.asarray(block, np.float32)
        if x.ndim == 1:
            x = np.stack((x, x), axis=1)
        if len(x):
            self.meter = np.maximum(np.abs(x).max(axis=0), self.meter * 0.85).astype(np.float32)
        with self.lock:
            buf = np.concatenate((self.buf, x))
            if len(buf) > MAX_LAG:
                buf = buf[-(MARGIN + 256):]
            self.buf = buf

    def read(self, frames):
        """frames × 2 campioni per il mixer, o None se non c'è (ancora) nulla."""
        with self.lock:
            buf = self.buf
            if not self.primed:
                if len(buf) < frames + MARGIN:
                    return None
                self.primed = True
            if len(buf) < frames:                     # in ritardo: quel che c'è, poi si riparte con il margine
                out = np.zeros((frames, 2), np.float32)
                out[:len(buf)] = buf
                self.buf = np.zeros((0, 2), np.float32)
                self.primed = False
                return out
            out, self.buf = buf[:frames], buf[frames:]
            return out


def _close(st):
    try:
        st.stop()
        st.close()
    except Exception:
        pass
