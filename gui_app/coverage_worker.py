"""Background worker that runs ChArUco coverage detection off the UI thread.

The detector is fed the per-camera preview frames at ~30 Hz (matching the
calibration capture rate) so the coverage graph fills as fast as data is
recorded, without blocking the Qt UI thread — six charuco detections per tick
would otherwise blow the display-refresh budget and stutter the live preview.
"""
import time

from PyQt5.QtCore import QThread, pyqtSignal


class CoverageWorker(QThread):
    updated = pyqtSignal()  # detector state advanced; UI should repaint the graph

    def __init__(self, detector, camera_mgr, interval_ms: int = 33, parent=None):
        super().__init__(parent)
        self._detector = detector
        self._camera_mgr = camera_mgr
        self._interval = interval_ms / 1000.0
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            t0 = time.perf_counter()
            # Prefer full-res frames (resolves oblique cams); fall back to the
            # downsampled preview per-camera until the first full frame lands.
            full = self._camera_mgr.latest_full_frames
            preview = self._camera_mgr.latest_frames
            frames = [f if f is not None else (preview[i] if i < len(preview) else None)
                      for i, f in enumerate(full)]
            fc = self._camera_mgr.frame_counts
            try:
                self._detector.update(frames, frame_counts=fc)
            except Exception as e:
                print(f"[hud] detection error: {e}", flush=True)
            self.updated.emit()
            remaining = self._interval - (time.perf_counter() - t0)
            if remaining > 0:
                time.sleep(remaining)

    def stop(self):
        self._running = False
