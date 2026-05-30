"""Per-camera grab thread — grabs frames, writes raw to disk, provides display thumbnails."""
import os
import time
import numpy as np
from collections import deque
from pathlib import Path
from PyQt5.QtCore import QThread
import pypylon.pylon as pylon


class GrabThread(QThread):
    def __init__(self, cam_index: int, camera: pylon.InstantCamera,
                 raw_path: Path = None, display_every: int = 1,
                 downsample: int = 3):
        super().__init__()
        self._cam_index = cam_index
        self._camera = camera
        self._raw_path = raw_path
        self._display_every = display_every
        self._downsample = downsample
        self._running = False
        self._triggers_stopped = False
        self.frame_count = 0
        self.timestamps = []
        self.latest_frame = None
        self.current_fps = 0.0
        self._fps_times = deque(maxlen=10)
        self._snapshot_requested = False
        self.snapshot_frame = None
        self._keep_full = False
        self.latest_full_frame = None

    def run(self):
        self._running = True
        self._triggers_stopped = False
        self.frame_count = 0
        self.timestamps = []
        recording = self._raw_path is not None
        fd = None

        if recording:
            fd = os.open(str(self._raw_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_BINARY)

        print(f"[grab{self._cam_index}] StartGrabbing (recording={recording})", flush=True)
        self._camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
        print(f"[grab{self._cam_index}] grabbing={self._camera.IsGrabbing()}", flush=True)
        frame_n = 0
        timeout_n = 0
        first_frame_logged = False

        try:
            while self._running and self._camera.IsGrabbing():
                try:
                    timeout = 200 if (recording and self._triggers_stopped) else (200 if recording else 2000)
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
                        os.write(fd, img)
                        self.frame_count += 1
                        self.timestamps.append(result.TimeStamp * 1e-9)

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
            print(f"[grab{self._cam_index}] exiting: frames={frame_n} timeouts={timeout_n}", flush=True)
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
