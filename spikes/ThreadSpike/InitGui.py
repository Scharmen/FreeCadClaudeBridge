# Startet den Spike-Listener, sobald die Qt-Ereignisschleife läuft.
from PySide import QtCore


def _start_thread_spike():
    import threadspike

    threadspike.start()


QtCore.QTimer.singleShot(0, _start_thread_spike)
