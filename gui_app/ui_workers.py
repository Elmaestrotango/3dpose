"""Tiny helper to run a blocking callable off the Qt main thread.

Used for the camera open/close/reconfigure operations that otherwise freeze the
UI (the window goes "not responding") because they make many synchronous GigE
round-trips. The callable runs in this QThread; its return value (or the raised
exception) is delivered back on the main thread via the ``done`` signal.
"""
import traceback
from PyQt5.QtCore import QThread, pyqtSignal


class CallableWorker(QThread):
    done = pyqtSignal(object)  # the callable's return value, or the Exception

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as e:  # surface, don't crash the worker
            traceback.print_exc()
            result = e
        self.done.emit(result)
