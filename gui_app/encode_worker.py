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
                 date: str, session_id: str, max_parallel: int = 0,
                 realtime: bool = False):
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
        # realtime: frames were already H.264-encoded on the GPU during capture;
        # here we only wrap the .h264 elementary stream into mp4 (stream copy).
        self._realtime = realtime

    def _cmd(self, src_path: Path, mp4_path: Path) -> list:
        if src_path.suffix == ".h264":
            # Stream-copy remux — no re-encode, finishes in seconds, no GPU.
            return [
                FFMPEG, "-y", "-fflags", "+genpts",
                "-r", str(self._fps), "-i", str(src_path),
                "-c:v", "copy", "-movflags", "+faststart",
                "-loglevel", "warning",
                str(mp4_path),
            ]
        return [
            FFMPEG, "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{self._w}x{self._h}", "-pix_fmt", "gray",
            "-r", str(self._fps), "-an",
            "-i", str(src_path),
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

        # Build the job list; cameras with no source file are immediate failures.
        # raw-to-disk mode reads raw.bin; real-time mode reads the GPU-produced
        # H.264 elementary stream (stream.h264) and just stream-copies it to mp4.
        jobs = []  # (idx, cam, src_path, mp4_path, n_frames)
        for i, cam in enumerate(self._camera_names):
            cam_dir = self._video_dir / cam
            h264_path = cam_dir / "stream.h264"
            raw_bin = cam_dir / "raw.bin"
            # Prefer the GPU-produced H.264 stream (real-time mode); fall back to
            # raw.bin (raw-to-disk mode, or a camera whose NVENC init failed) so a
            # runtime encoder failure can never strand a camera's data.
            if self._realtime and h264_path.exists():
                raw_path = h264_path
                ft = cam_dir / "frametimes.npy"
                try:
                    import numpy as np
                    n_frames = int(np.load(ft).shape[1]) if ft.exists() else 0
                except Exception:
                    n_frames = 0
            elif raw_bin.exists():
                raw_path = raw_bin
                n_frames = os.path.getsize(raw_bin) // (self._w * self._h)
            else:
                results[i] = (cam, 0, False)
                continue
            mp4_path = cam_dir / (
                f"{self._date}-{self._session_id}-{cam}-{self._acq_type}.mp4")
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
