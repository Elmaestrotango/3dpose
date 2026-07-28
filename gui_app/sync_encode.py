"""Router for the real-time frame kick-out path.

Owns one NVENC encoder thread per camera and a shared FrameSyncCoordinator.
Grab threads call submit() with each successfully-grabbed frame; the coordinator
releases a trigger only once every camera has it, and the router routes those
(and only those) frames to the per-camera encoders — so each stream.h264 holds
exactly the common, trigger-aligned frames. No post-hoc re-encode is needed.

submit() holds a short lock: the coordinator step is integer-only and routing is
put_nowait into queues the encoders drain faster than the 100 fps inflow, so the
lock is held for microseconds and never blocks on a full queue. Releases are
routed under the lock so each encoder receives its frames in trigger order.
"""
import os
import threading
from pathlib import Path

import numpy as np

from gui_app import nvenc
from gui_app.frame_sync import FrameSyncCoordinator
from gui_app.grab_thread import _EncoderThread


class SyncEncodeRouter:
    def __init__(self, raw_paths, width: int, height: int, quality: int,
                 fps: int = 100, max_lag: int = 240):
        self._n = len(raw_paths)
        self._w, self._h, self._q = width, height, quality
        self.max_lag = max_lag  # grab threads read this to size their NV12 ring
        self._coord = FrameSyncCoordinator(self._n, max_lag=max_lag)
        self._lock = threading.Lock()
        self._encoders = []
        self._fds = []
        self.timestamps = [[] for _ in range(self._n)]
        self.block_ids = [[] for _ in range(self._n)]
        self.available = False
        self.dropped_full = 0  # frames lost to a wedged encoder queue (should be 0)
        self._log_every = max(int(fps), 1) * 5 * self._n   # ~5 s of submissions
        self._since_log = 0

        try:
            for i, rp in enumerate(raw_paths):
                enc = nvenc.create_h264_encoder(width, height, quality, fps=fps)
                h264_path = Path(rp).parent / "stream.h264"
                fd = os.open(str(h264_path),
                             os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_BINARY)
                et = _EncoderThread(i, enc, fd, Path(rp).parent / "raw_tail.bin",
                                    width, height)
                self._encoders.append(et)
                self._fds.append(fd)
            self.available = True
        except Exception as e:
            print(f"[sync] NVENC init failed, kick-out unavailable: {e}", flush=True)
            for fd in self._fds:
                try:
                    os.close(fd)
                except Exception:
                    pass
            self._encoders = []

    def start(self):
        for et in self._encoders:
            et.start()

    def pending(self) -> int:
        return self._coord.pending_depth()

    def retire(self, cam: int, reason: str = ""):
        """Drop a camera from the alignment set (stalled and unrecoverable)."""
        with self._lock:
            self._coord.retire(cam, reason)

    def lag_report(self) -> str:
        return self._coord.lag_report()

    def _route(self, releases):
        for cam, bid, payload in releases:
            ts, buf = payload
            try:
                self._encoders[cam].queue.put_nowait(buf)
                self.block_ids[cam].append(bid)
                self.timestamps[cam].append(ts)
            except Exception:
                # Encoder queue full => encoder wedged (GPU stall). Dropping
                # here desyncs this camera; it should never happen because the
                # encoder drains faster than inflow. Count it loudly.
                self.dropped_full += 1
                if self.dropped_full in (1, 100):
                    print(f"[sync] cam{cam+1} encoder queue full, dropped a "
                          f"released frame ({self.dropped_full})", flush=True)

    def submit(self, cam: int, block_id: int, ts: float, buf: np.ndarray):
        with self._lock:
            releases = self._coord.submit(cam, block_id, (ts, buf))
            if releases:
                self._route(releases)
            # Periodic lag report. Cross-camera skew beyond max_lag is what
            # force-drops frames every camera actually captured (43% of a
            # 23-min session on 2026-07-27), and nothing in the old logs said
            # which camera was behind.
            self._since_log += 1
            if self._since_log >= self._log_every:
                self._since_log = 0
                print(self._coord.lag_report(), flush=True)

    def abandon(self):
        """Tear down WITHOUT draining (app quit mid-recording): close the output
        fds so stream.h264 unlocks and can be deleted. Encoder threads are
        daemon and die on process exit."""
        for fd in self._fds:
            try:
                os.close(fd)
            except Exception:
                pass

    def stop(self):
        """Flush the coordinator, drain + join encoders, return per-camera
        (count, timestamps, block_ids)."""
        with self._lock:
            self._route(self._coord.flush())
        for et in self._encoders:
            try:
                et.queue.put(None, timeout=30)
            except Exception:
                pass
        for et in self._encoders:
            et.join(timeout=60)
        for fd in self._fds:
            try:
                os.close(fd)
            except Exception:
                pass
        print(f"[sync] released={self._coord.released} dropped={self._coord.dropped} "
              f"forced={self._coord.forced} queue_full_drops={self.dropped_full}",
              flush=True)
        return [(len(self.block_ids[i]), self.timestamps[i], self.block_ids[i])
                for i in range(self._n)]
