"""Per-camera grab thread — grabs frames, hands them to disk or the encoder.

Real-time (online) encode is DECOUPLED from the grab loop: the grab thread only
does retrieve -> copy -> queue.put -> Release (raw-to-disk weight, proven at
100 fps), while a per-camera _EncoderThread drains the queue through NVENC.
Encoding inline in the grab loop (pre-2026-06-12) blew the 10 ms/frame budget
under 6-thread contention, exhausted the pylon buffer pool, and dropped ~28%
of frames as GigE "buffer incompletely grabbed" errors.
"""
import os
import queue
import threading
import time
import numpy as np
from collections import deque
from pathlib import Path
from PyQt5.QtCore import QThread
import pypylon.pylon as pylon

# Frames of slack per camera between grab and encode (~2.3 MB each at
# 1920x1200). The pylon buffer pool upstream adds ~10 s more.
ENCODE_QUEUE_DEPTH = 200
# How long the grab thread may block on a full queue before dropping the
# frame. Backpressure this long means the encoder is wedged (GPU stall) —
# blocking longer would exhaust the pylon pool and lose frames anyway.
PUT_TIMEOUT_S = 2.0


class _EncoderThread(threading.Thread):
    """Drains gray frames from a queue into an NVENC H.264 elementary stream.

    Encode() releases the GIL, so all cameras' encoder threads run truly
    concurrently (PoC: ~1400 fps aggregate, ~2.3x the 600 fps needed).

    If the encoder dies mid-recording, the thread switches to writing the
    remaining queued frames (and everything after) raw to ``raw_tail.bin`` —
    in arrival order, so the recording stays gapless: stream.h264 holds frames
    [0..encoded) and the tail holds [encoded..end). encode_worker encodes the
    tail and concatenates it onto the stream at stop.
    """

    def __init__(self, cam_index: int, enc, h264_fd: int, spill_path: Path,
                 width: int, height: int):
        super().__init__(daemon=True, name=f"encoder{cam_index}")
        self._cam_index = cam_index
        self._enc = enc
        self._fd = h264_fd
        self._spill_path = spill_path
        self._height = height
        self._nv12 = np.full((height * 3 // 2, width), 128, np.uint8)
        self.queue = queue.Queue(maxsize=ENCODE_QUEUE_DEPTH)
        self.encoded = 0
        self.spilled = 0
        self.failed = False

    def run(self):
        spill_fd = None
        try:
            while True:
                img = self.queue.get()
                if img is None:  # sentinel: flush + exit
                    if spill_fd is None:
                        try:
                            bs = self._enc.EndEncode()
                            if bs:
                                os.write(self._fd, bs)
                        except Exception as e:
                            print(f"[enc{self._cam_index}] EndEncode failed: {e}", flush=True)
                    return
                if spill_fd is not None:
                    os.write(spill_fd, img)
                    self.spilled += 1
                    continue
                try:
                    self._nv12[:self._height, :] = img  # gray -> Y plane
                    bs = self._enc.Encode(self._nv12)
                    if bs:
                        os.write(self._fd, bs)
                    self.encoded += 1
                except Exception as e:
                    # Encoder died: flush what it already accepted, then write
                    # this and all later frames raw so nothing is lost.
                    print(f"[enc{self._cam_index}] encoder FAILED after "
                          f"{self.encoded} frames, spilling raw to "
                          f"{self._spill_path.name}: {e}", flush=True)
                    self.failed = True
                    try:
                        bs = self._enc.EndEncode()
                        if bs:
                            os.write(self._fd, bs)
                    except Exception:
                        pass
                    spill_fd = os.open(str(self._spill_path),
                                       os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_BINARY)
                    os.write(spill_fd, img)
                    self.spilled += 1
        finally:
            if spill_fd is not None:
                os.close(spill_fd)


class GrabThread(QThread):
    def __init__(self, cam_index: int, camera: pylon.InstantCamera,
                 raw_path: Path = None, display_every: int = 1,
                 downsample: int = 3, realtime: bool = False,
                 width: int = 0, height: int = 0, quality: int = 21):
        super().__init__()
        self._cam_index = cam_index
        self._camera = camera
        self._raw_path = raw_path
        self._display_every = display_every
        self._downsample = downsample
        self._realtime = realtime
        self._width = width
        self._height = height
        self._quality = quality
        self._running = False
        self._triggers_stopped = False
        self.frame_count = 0
        self.timestamps = []
        self.block_ids = []
        self.drops = 0
        self.latest_frame = None
        self.current_fps = 0.0
        self._fps_times = deque(maxlen=10)
        self._snapshot_requested = False
        self.snapshot_frame = None
        self._keep_full = False
        self.latest_full_frame = None

    def _put_frame(self, enc_thread: _EncoderThread, img) -> bool:
        """Queue a frame for encoding; True if it will be persisted."""
        try:
            enc_thread.queue.put(img.copy(), timeout=PUT_TIMEOUT_S)
            return True
        except queue.Full:
            self.drops += 1
            if self.drops in (1, 10, 100) or self.drops % 1000 == 0:
                print(f"[grab{self._cam_index}] ENCODER BACKPRESSURE: encoder "
                      f"not draining, dropped {self.drops} frames so far", flush=True)
            return False

    def run(self):
        self._running = True
        self._triggers_stopped = False
        self.frame_count = 0
        self.timestamps = []
        self.block_ids = []
        self.drops = 0
        recording = self._raw_path is not None
        fd = None              # raw.bin descriptor (raw-to-disk mode / fallback)
        h264_fd = None         # H.264 elementary-stream descriptor
        enc_thread = None      # decoupled NVENC drain thread (real-time mode)

        if recording and self._realtime:
            try:
                from gui_app import nvenc
                enc = nvenc.create_h264_encoder(self._width, self._height, self._quality)
                h264_path = self._raw_path.parent / "stream.h264"
                h264_fd = os.open(str(h264_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_BINARY)
                enc_thread = _EncoderThread(
                    self._cam_index, enc, h264_fd,
                    self._raw_path.parent / "raw_tail.bin",
                    self._width, self._height)
                enc_thread.start()
                print(f"[grab{self._cam_index}] real-time NVENC encode (decoupled) -> {h264_path.name}", flush=True)
            except Exception as e:
                # Encoder unavailable: fall back to raw-to-disk so no data is lost.
                print(f"[grab{self._cam_index}] NVENC init failed, falling back to raw.bin: {e}", flush=True)
                enc_thread = None
                if h264_fd is not None:
                    os.close(h264_fd); h264_fd = None

        if recording and enc_thread is None:
            fd = os.open(str(self._raw_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_BINARY)

        print(f"[grab{self._cam_index}] StartGrabbing (recording={recording})", flush=True)
        try:
            self._camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
        except Exception as e:
            # Camera offline / in a bad transport state: exit this thread cleanly
            # rather than letting the exception escape run() and abort Qt.
            print(f"[grab{self._cam_index}] StartGrabbing failed (camera offline?): {e}", flush=True)
            if fd is not None:
                os.close(fd)
            if enc_thread is not None:
                enc_thread.queue.put(None)
                enc_thread.join(timeout=10)
            if h264_fd is not None:
                os.close(h264_fd)
            return
        print(f"[grab{self._cam_index}] grabbing={self._camera.IsGrabbing()}", flush=True)
        frame_n = 0
        timeout_n = 0
        first_frame_logged = False

        try:
            while self._running and self._camera.IsGrabbing():
                try:
                    timeout = 200 if recording else 2000
                    result = self._camera.RetrieveResult(timeout, pylon.TimeoutHandling_ThrowException)
                    if not result.GrabSucceeded():
                        print(f"[grab{self._cam_index}] grab failed: {result.ErrorCode} {result.ErrorDescription}", flush=True)
                        result.Release()
                        continue

                    img = result.Array

                    if self._snapshot_requested:
                        self.snapshot_frame = img.copy()  # full-resolution still
                        self._snapshot_requested = False

                    if recording:
                        if enc_thread is not None:
                            persisted = self._put_frame(enc_thread, img)
                        else:
                            os.write(fd, img)
                            persisted = True
                        if persisted:
                            self.frame_count += 1
                            self.timestamps.append(result.TimeStamp * 1e-9)
                            # GigE Vision block ID = trigger ordinal: makes any
                            # dropped frame a detectable, re-alignable gap
                            # instead of a silent cross-camera desync.
                            try:
                                self.block_ids.append(result.BlockID)
                            except Exception:
                                self.block_ids.append(-1)

                    frame_n += 1
                    if recording and not first_frame_logged:
                        print(f"[grab{self._cam_index}] first frame received", flush=True)
                        first_frame_logged = True
                    if recording and frame_n % 100 == 0:
                        print(f"[grab{self._cam_index}] frames={frame_n} timeouts={timeout_n}", flush=True)
                    now = time.perf_counter()
                    self._fps_times.append(now)
                    if len(self._fps_times) >= 2:
                        dt = self._fps_times[-1] - self._fps_times[0]
                        if dt > 0:
                            self.current_fps = (len(self._fps_times) - 1) / dt

                    if frame_n % self._display_every == 0:
                        d = self._downsample
                        self.latest_frame = img[::d, ::d].copy()
                        # Full-res copy for the coverage HUD detector (calibration
                        # only) — oblique cams (1/4) need full res to resolve the
                        # board, same as the post-hoc calibration.
                        if self._keep_full:
                            self.latest_full_frame = img.copy()

                    result.Release()

                except pylon.TimeoutException:
                    timeout_n += 1
                    if recording and timeout_n in (1, 5, 20):
                        print(f"[grab{self._cam_index}] TIMEOUT #{timeout_n} (no frame in {timeout}ms, triggers_stopped={self._triggers_stopped})", flush=True)
                    if recording and self._triggers_stopped:
                        break
                    if not self._running:
                        break
                except Exception as e:
                    print(f"[grab{self._cam_index}] exception: {type(e).__name__}: {e}", flush=True)
                    if not self._running:
                        break
                    time.sleep(0.001)
        finally:
            print(f"[grab{self._cam_index}] exiting: frames={frame_n} timeouts={timeout_n} drops={self.drops}", flush=True)
            if enc_thread is not None:
                # Drain: sentinel, then wait for the backlog (~1 s at full queue).
                try:
                    enc_thread.queue.put(None, timeout=30)
                except queue.Full:
                    print(f"[grab{self._cam_index}] encoder queue wedged at stop, abandoning drain", flush=True)
                enc_thread.join(timeout=60)
                if enc_thread.is_alive():
                    print(f"[grab{self._cam_index}] encoder thread did not exit (GPU stall?)", flush=True)
                else:
                    print(f"[grab{self._cam_index}] encoded={enc_thread.encoded} spilled={enc_thread.spilled}", flush=True)
            if h264_fd is not None:
                os.close(h264_fd)
            if fd is not None:
                os.close(fd)
            try:
                self._camera.StopGrabbing()
            except Exception:
                pass

    def signal_triggers_stopped(self):
        self._triggers_stopped = True

    def request_snapshot(self):
        """Ask the grab loop to stash the next full-resolution frame."""
        self.snapshot_frame = None
        self._snapshot_requested = True

    def set_keep_full(self, flag: bool):
        """Keep a full-resolution copy of each display-cadence frame for the
        coverage HUD detector. Off by default to avoid recording-loop overhead."""
        self._keep_full = flag
        if not flag:
            self.latest_full_frame = None

    def stop(self):
        self._running = False
