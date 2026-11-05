"""Comandi verso i dispositivi esterni (mixer, regie video, proiettori, OSC e MIDI in uscita).

Su un'istanza in RIDONDANZA collegata al master i comandi esterni sono sospesi: li manda solo il master, così
mixer, regie e proiettori non ricevono tutto due volte. Appena il master è perso (o l'istanza viene promossa o
scollegata) ripartono. Il pannello principale lo segnala in arancione sopra al volume master.
"""
from concurrent.futures import Future

_held = False


def hold(on):
    """Sospende (True) o riattiva (False) i comandi verso i dispositivi esterni."""
    global _held
    _held = bool(on)


def held():
    return _held


def done_future(result=None):
    """Future già concluso: per chi aspetta il risultato di un comando che non è partito."""
    f = Future()
    f.set_result(result)
    return f
