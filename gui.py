"""Panopticon Acquisition GUI — launch with: conda run -n 3dpose python gui.py"""
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QSplashScreen, QLabel, QMessageBox
from PyQt5.QtCore import Qt, QTimer, QThread
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap

LOG_DIR = Path(__file__).parent / "logs"


class _Tee:
    """Write to several streams at once (e.g. the console and a log file).

    Under the pythonw launcher sys.stdout/stderr are None, so the diagnostic
    prints from the grab threads would otherwise be discarded — this captures
    them to a file so a crash can be diagnosed after the fact."""
    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass


def _setup_logging():
    """Tee stdout/stderr to a timestamped log file under logs/ (pythonw discards
    them otherwise). Returns the log path, or None if logging couldn't be set up."""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        log_path = LOG_DIR / f"panopticon_{datetime.now():%Y%m%d_%H%M%S}.log"
        f = open(log_path, "a", buffering=1, encoding="utf-8")
        sys.stdout = _Tee(sys.__stdout__, f)
        sys.stderr = _Tee(sys.__stderr__, f)
        # Dump every thread's Python stack into the log on a NATIVE crash
        # (access violation / abort — e.g. Qt's 0xc0000409 fail-fast), which
        # sys.excepthook can't see. Needs the real file, not the _Tee.
        import faulthandler
        faulthandler.enable(file=f, all_threads=True)
        print(f"[startup] logging to {log_path}", flush=True)
        return log_path
    except Exception:
        return None


def _install_excepthook(log_path):
    """Surface unhandled exceptions as a dialog + log entry instead of letting
    them escape a Qt slot — PyQt responds to that by calling abort() (the silent
    crash we hit when a dropped camera raised during teardown)."""
    def hook(exc_type, exc, tb):
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        print(f"[UNHANDLED]\n{msg}", flush=True)
        try:
            app = QApplication.instance()
            on_main = app is not None and QThread.currentThread() == app.thread()
            if on_main:  # QMessageBox is only safe on the GUI thread
                QMessageBox.critical(
                    None, "Panopticon — Unexpected Error",
                    f"{exc_type.__name__}: {exc}\n\nThe app is still running. "
                    f"Full traceback logged to:\n{log_path}")
        except Exception:
            pass
    sys.excepthook = hook


def make_splash():
    px = QPixmap(360, 120)
    px.fill(QColor(25, 25, 42))
    p = QPainter(px)
    p.setPen(QColor(220, 220, 220))
    p.setFont(QFont("Segoe UI", 18, QFont.Bold))
    p.drawText(px.rect(), Qt.AlignCenter, "Panopticon")
    p.setPen(QColor(120, 120, 160))
    p.setFont(QFont("Segoe UI", 10))
    p.drawText(px.rect().adjusted(0, 40, 0, 0), Qt.AlignCenter, "Loading cameras...")
    p.end()
    return px


def main():
    # ~13 busy threads share the GIL during recording (6 grab + 6 encode + UI).
    # The default 5 ms switch interval makes a GIL-holding thread stall the
    # others for whole milliseconds; 1 ms keeps grab-loop latency bounded.
    sys.setswitchinterval(0.001)
    log_path = _setup_logging()

    # Without this, Windows groups the taskbar entry under python.exe and shows
    # its icon instead of Panopticon's.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "salk.talmo.panopticon")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("Panopticon Acquisition")
    icon_path = Path(__file__).parent / "panopticon.ico"
    if icon_path.exists():
        from PyQt5.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_path)))

    _install_excepthook(log_path)

    splash = QSplashScreen(make_splash())
    splash.show()
    app.processEvents()

    from gui_app.main_window import MainWindow
    window = MainWindow()
    window.show()
    splash.finish(window)

    sys.exit(app.exec_())


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
