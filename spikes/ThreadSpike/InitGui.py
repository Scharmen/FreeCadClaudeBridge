# Starts the spike listener as soon as the Qt event loop is running.
from PySide import QtCore


def _start_thread_spike():
    import threadspike

    threadspike.start()


QtCore.QTimer.singleShot(0, _start_thread_spike)
