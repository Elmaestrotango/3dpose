"""Background worker: block-ID align a finished recording (realtime path).

Runs after EncodeWorker. When cameras dropped different frames it re-encodes
each mp4 down to the frames every camera captured and replaces the original
(the user chose replace-in-place), so the per-camera videos end up equal-length
and trigger-aligned. A loss-free recording is a no-op fast path.
"""
from pathlib import Path
from PyQt5.QtCore import QThread, pyqtSignal

from gui_app import alignment


class AlignWorker(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, message
    finished_align = pyqtSignal(dict)      # summary from align_recording

    def __init__(self, video_dir: Path, fps: int, quality: int,
                 parallel: int = 3):
        super().__init__()
        self._video_dir = video_dir
        self._fps = fps
        self._quality = quality
        self._parallel = parallel

    def run(self):
        try:
            summary = alignment.align_recording(
                self._video_dir, fps=self._fps, quality=self._quality,
                replace=True, parallel=self._parallel,
                progress=lambda d, t, m: self.progress.emit(d, t, m))
        except Exception as e:
            print(f"[align] failed: {e}", flush=True)
            summary = dict(error=str(e), needed=False, replaced=False,
                           common_frames=0, warnings=[str(e)])
        self.finished_align.emit(summary)
