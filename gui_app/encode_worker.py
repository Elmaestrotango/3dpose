"""Background encoding worker — converts raw.bin files to H.264 MP4 via NVENC.

Cameras are encoded concurrently (a pool of up to ``max_parallel`` ffmpeg/NVENC
processes). NVENC offloads the compression to the GPU, so the CPU mostly feeds
raw bytes; running several at once is bounded by disk read bandwidth and the
NVENC concurrent-session limit (8 on current GeForce drivers), not the CPU.
"""
import os
import subprocess
import sys
import time
from pathlib import Path
from PyQt5.QtCore import QThread, pyqtSignal
from imageio_ffmpeg import get_ffmpeg_exe


FFMPEG = get_ffmpeg_exe()

_STARTUPINFO = None
if sys.platform == "win32":
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0


class EncodeWorker(QThread):
    progress = pyqtSignal(int, int)
    finished_all = pyqtSignal(list)

    def __init__(self, video_dir: Path, camera_names: list[str],
                 acq_type: str, w: int, h: int, fps: int, quality: int,
                 date: str, session_id: str, max_parallel: int = 0):
        super().__init__()
        self._video_dir = video_dir
        self._camera_names = camera_names
        self._acq_type = acq_type
        self._w = w
        self._h = h
        self._fps = fps
        self._quality = quality
        self._date = date
        self._session_id = session_id
        # 0 => encode all cameras concurrently
        self._max_parallel = max_parallel

    def _cmd(self, raw_path: Path, mp4_path: Path) -> list:
        return [
            FFMPEG, "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{self._w}x{self._h}", "-pix_fmt", "gray",
            "-r", str(self._fps), "-an",
            "-i", str(raw_path),
            "-c:v", "h264_nvenc",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-qp", str(self._quality),
            "-bf:v", "0", "-gpu", "0",
            "-loglevel", "warning",
            str(mp4_path),
        ]

    def run(self):
        total = len(self._camera_names)
        results = [None] * total

        # Build the job list; cameras with no raw.bin are immediate failures.
        jobs = []  # (idx, cam, raw_path, mp4_path, n_frames)
        for i, cam in enumerate(self._camera_names):
            raw_path = self._video_dir / cam / "raw.bin"
            if not raw_path.exists():
                results[i] = (cam, 0, False)
                continue
            mp4_path = self._video_dir / cam / (
                f"{self._date}-{self._session_id}-{cam}-{self._acq_type}.mp4")
            n_frames = os.path.getsize(raw_path) // (self._w * self._h)
            jobs.append((i, cam, raw_path, mp4_path, n_frames))

        done = sum(1 for r in results if r is not None)
        if done:
            self.progress.emit(done, total)

        max_par = self._max_parallel if self._max_parallel > 0 else max(1, len(jobs))
        running = {}  # idx -> (proc, cam, raw_path, n_frames)
        ji = 0
        while ji < len(jobs) or running:
            while ji < len(jobs) and len(running) < max_par:
                i, cam, raw_path, mp4_path, n_frames = jobs[ji]
                ji += 1
                try:
                    proc = subprocess.Popen(
                        self._cmd(raw_path, mp4_path),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        startupinfo=_STARTUPINFO)
                    running[i] = (proc, cam, raw_path, n_frames)
                except Exception:
                    results[i] = (cam, n_frames, False)
                    done += 1
                    self.progress.emit(done, total)

            for i in list(running.keys()):
                proc, cam, raw_path, n_frames = running[i]
                ret = proc.poll()
                if ret is None:
                    continue
                ok = (ret == 0)
                if ok:
                    try:
                        os.remove(raw_path)
                    except OSError:
                        pass
                results[i] = (cam, n_frames, ok)
                done += 1
                self.progress.emit(done, total)
                del running[i]

            if running or ji < len(jobs):
                time.sleep(0.15)

        results = [r if r is not None else (self._camera_names[i], 0, False)
                   for i, r in enumerate(results)]
        self.finished_all.emit(results)
