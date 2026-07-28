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
import numpy as np
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
            "-g", str(self._fps),
            "-bf:v", "0", "-gpu", "0",
            # moov atom to the FRONT, matching the stream-copy branch above. The
            # browser labeler (LUC3D) appends the file in 1 MB pieces from byte 0
            # and stops as soon as moov parses, so moov-at-end costs a read of the
            # ENTIRE file per camera (x6) before frame 1 appears. Whether the mp4
            # muxer front-loads moov on its own varies by ffmpeg build, so say it.
            "-movflags", "+faststart",
            "-loglevel", "warning",
            str(mp4_path),
        ]

    def _append_raw_tail(self, cam: str, h264_path: Path, tail_bin: Path) -> bool:
        """A camera whose encoder died mid-recording has stream.h264 (frames
        0..k) plus raw_tail.bin (frames k..end). Encode the tail to an Annex-B
        elementary stream with the same settings and append the bytes — both
        segments carry their own SPS/PPS+IDR, so decoders handle the splice and
        a single stream-copy remux finalizes the camera as usual."""
        tail_h264 = tail_bin.parent / "tail.h264"
        cmd = [
            FFMPEG, "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{self._w}x{self._h}", "-pix_fmt", "gray",
            "-r", str(self._fps), "-an",
            "-i", str(tail_bin),
            "-c:v", "h264_nvenc",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-qp", str(self._quality),
            "-g", str(self._fps),
            "-bf:v", "0", "-gpu", "0",
            "-loglevel", "warning",
            "-f", "h264", str(tail_h264),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=1800,
                               startupinfo=_STARTUPINFO)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode(errors="replace")[-500:])
            with open(h264_path, "ab") as dst, open(tail_h264, "rb") as src:
                while chunk := src.read(8 << 20):
                    dst.write(chunk)
            tail_h264.unlink()
            tail_bin.unlink()
            print(f"[encode] {cam}: appended raw tail "
                  f"({os.path.getsize(h264_path)} bytes total)", flush=True)
            return True
        except Exception as e:
            # Keep the raw tail on disk — the data is safe, just unmerged.
            print(f"[encode] {cam}: raw tail merge FAILED, tail kept at "
                  f"{tail_bin}: {e}", flush=True)
            return False

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
                tail_bin = cam_dir / "raw_tail.bin"
                if tail_bin.exists() and tail_bin.stat().st_size > 0:
                    self._append_raw_tail(cam, h264_path, tail_bin)
                raw_path = h264_path
                ft = cam_dir / "frametimes.npy"
                try:
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
        running = {}  # idx -> (proc, cam, raw_path, n_frames, err_fd, err_path)
        ji = 0
        while ji < len(jobs) or running:
            while ji < len(jobs) and len(running) < max_par:
                i, cam, raw_path, mp4_path, n_frames = jobs[ji]
                ji += 1
                # ffmpeg's stderr goes to a per-camera file so a failed encode
                # is diagnosable (kept on failure, removed on success).
                err_path = raw_path.parent / "encode_error.log"
                try:
                    err_fd = open(err_path, "wb")
                    proc = subprocess.Popen(
                        self._cmd(raw_path, mp4_path),
                        stdout=subprocess.DEVNULL, stderr=err_fd,
                        startupinfo=_STARTUPINFO)
                    running[i] = (proc, cam, raw_path, n_frames, err_fd, err_path)
                except Exception as e:
                    print(f"[encode] {cam}: ffmpeg launch failed: {e}", flush=True)
                    results[i] = (cam, n_frames, False)
                    done += 1
                    self.progress.emit(done, total)

            for i in list(running.keys()):
                proc, cam, raw_path, n_frames, err_fd, err_path = running[i]
                ret = proc.poll()
                if ret is None:
                    continue
                err_fd.close()
                ok = (ret == 0)
                if ok:
                    try:
                        os.remove(raw_path)
                        err_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                else:
                    try:
                        tail = err_path.read_text(errors="replace")[-2000:]
                    except OSError:
                        tail = "(no stderr captured)"
                    print(f"[encode] {cam}: ffmpeg exited {ret}; source kept at "
                          f"{raw_path}; stderr in {err_path}:\n{tail}", flush=True)
                results[i] = (cam, n_frames, ok)
                done += 1
                self.progress.emit(done, total)
                del running[i]

            if running or ji < len(jobs):
                time.sleep(0.15)

        results = [r if r is not None else (self._camera_names[i], 0, False)
                   for i, r in enumerate(results)]
        self.finished_all.emit(results)
