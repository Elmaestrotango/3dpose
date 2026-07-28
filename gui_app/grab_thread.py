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
    """Drains ready-made NV12 frames from a queue into an NVENC H.264 stream.

    The grab thread copies each gray frame straight into a preallocated NV12
    ring buffer (one memcpy, GIL-held ~0.3 ms) and queues the buffer; this
    thread then only calls Encode() (releases the GIL) and os.write (ditto) —
    so the encoder side holds the GIL for ~zero time per frame. All cameras'
    encoder threads run truly concurrently (PoC: ~1400 fps aggregate).

    If the encoder dies mid-recording, the thread switches to writing the
    remaining queued frames' Y planes (the gray data) raw to ``raw_tail.bin``
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
        self.queue = queue.Queue(maxsize=ENCODE_QUEUE_DEPTH)
        self.encoded = 0
        self.spilled = 0
        self.failed = False

    def run(self):
        spill_fd = None
        try:
            while True:
                nv12 = self.queue.get()
                if nv12 is None:  # sentinel: flush + exit
                    if spill_fd is None:
                        try:
                            bs = self._enc.EndEncode()
                            if bs:
                                os.write(self._fd, bs)
                        except Exception as e:
                            print(f"[enc{self._cam_index}] EndEncode failed: {e}", flush=True)
                    return
                if spill_fd is not None:
                    os.write(spill_fd, nv12[:self._height])  # Y plane = gray
                    self.spilled += 1
                    continue
                try:
                    bs = self._enc.Encode(nv12)
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
                    os.write(spill_fd, nv12[:self._height])
                    self.spilled += 1
        finally:
            if spill_fd is not None:
                os.close(spill_fd)


class GrabThread(QThread):
    def __init__(self, cam_index: int, camera: pylon.InstantCamera,
                 raw_path: Path = None, display_every: int = 1,
                 downsample: int = 3, realtime: bool = False,
                 width: int = 0, height: int = 0, quality: int = 21,
                 fps: int = 100, router=None):
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
        self._fps = fps
        # Real-time kick-out: when set, frames go to this shared router (which
        # gates them through the cross-camera coordinator) instead of a private
        # encoder. The router owns the encoders + the recorded metadata.
        self._router = router
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
        # Stall recovery state (see _rearm_stream / _resync_offset).
        self._last_ts = None          # device timestamp of the last good frame
        self._last_bid_eff = -1       # its globally-consistent block ID
        self.rearms = 0               # stream restarts this run
        self.desynced = False         # stalled and could not be realigned

    def _rearm_stream(self, attempt: int) -> bool:
        """Restart this camera's stream after a stall.

        A GigE Vision stream stall wedges the driver pipeline and never
        self-recovers — the grab loop just times out for the rest of the session
        (cam6 2026-06-10, cam1 2026-07-27). Restarting the grab is the only way
        back without restarting the GUI.
        """
        print(f"[grab{self._cam_index}] STALLED — re-arming stream "
              f"(attempt {attempt})", flush=True)
        try:
            self._camera.StopGrabbing()
            self._camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
            return True
        except Exception as e:
            print(f"[grab{self._cam_index}] re-arm failed: "
                  f"{type(e).__name__}: {e}", flush=True)
            return False

    def _resync_offset(self, raw_bid: int, ts: float):
        """Block-ID offset that keeps trigger ordinals globally consistent.

        StartGrabbing restarts the camera's block-ID counter, so a re-armed
        camera looks like it jumped tens of thousands of triggers backwards —
        which the coordinator's 16-bit unwrap would misread as a wrap, place it
        far AHEAD, and force-drop every other camera.

        The device timestamp is a free-running hardware clock that survives the
        restart, and the triggers are hardware-timed, so the number of missed
        periods is measurable rather than guessed. Returns None when the gap
        doesn't land cleanly on a period boundary — better to declare desync
        than to publish frames under the wrong trigger ordinal.
        """
        if self._last_ts is None or self._fps <= 0:
            return None
        periods = (ts - self._last_ts) * self._fps
        k = round(periods)
        resid = abs(periods - k)
        if k < 1 or resid > 0.25:
            print(f"[grab{self._cam_index}] cannot resync: {periods:.2f} trigger "
                  f"periods elapsed, {resid:.2f} off a boundary", flush=True)
            return None
        return (self._last_bid_eff + k) - raw_bid

    def _put_frame(self, enc_thread: _EncoderThread, img) -> bool:
        """Copy the gray frame into the next NV12 ring buffer and queue it.

        The ring has queue-capacity + slack buffers, so a buffer can only be
        reused after the encoder has long since consumed it. Copying straight
        into NV12 here (instead of img.copy() + a second copy in the encoder)
        halves the GIL-held memcpy work per frame."""
        buf = self._nv12_ring[self._ring_i]
        self._ring_i = (self._ring_i + 1) % len(self._nv12_ring)
        buf[:self._height, :] = img  # Y plane = gray; UV stays 128
        try:
            enc_thread.queue.put(buf, timeout=PUT_TIMEOUT_S)
            return True
        except queue.Full:
            self.drops += 1
            if self.drops in (1, 10, 100) or self.drops % 1000 == 0:
                print(f"[grab{self._cam_index}] ENCODER BACKPRESSURE: encoder "
                      f"not draining, dropped {self.drops} frames so far", flush=True)
            return False

    def _log_stream_stats(self):
        """Dump pylon's per-stream counters — distinguishes network packet loss
        (Failed_Packet/Resend) from pool exhaustion (Buffer_Underrun)."""
        try:
            sg = self._camera.GetStreamGrabberNodeMap()
            stats = {}
            for s in ("Statistic_Total_Buffer_Count",
                      "Statistic_Failed_Buffer_Count",
                      "Statistic_Buffer_Underrun_Count",
                      "Statistic_Total_Packet_Count",
                      "Statistic_Failed_Packet_Count",
                      "Statistic_Resend_Request_Count",
                      "Statistic_Resend_Packet_Count"):
                n = sg.GetNode(s)
                if n is not None:
                    stats[s.replace("Statistic_", "")] = n.GetValue()
            print(f"[grab{self._cam_index}] stream stats: {stats}", flush=True)
        except Exception as e:
            print(f"[grab{self._cam_index}] stream stats unavailable: {e}", flush=True)

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
        kick = recording and self._realtime and self._router is not None

        if kick:
            # Frames go to the shared router; this thread keeps no encoder. The
            # ring must outlast a frame's whole journey (held by the coordinator
            # up to max_lag, then queued at the encoder) before its slot reuses.
            ring_n = self._router.max_lag + ENCODE_QUEUE_DEPTH + 64
            self._nv12_ring = [
                np.full((self._height * 3 // 2, self._width), 128, np.uint8)
                for _ in range(ring_n)]
            self._ring_i = 0
            print(f"[grab{self._cam_index}] real-time kick-out -> shared router "
                  f"(ring={ring_n})", flush=True)
        elif recording and self._realtime:
            try:
                from gui_app import nvenc
                enc = nvenc.create_h264_encoder(self._width, self._height, self._quality, fps=self._fps)
                h264_path = self._raw_path.parent / "stream.h264"
                h264_fd = os.open(str(h264_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_BINARY)
                enc_thread = _EncoderThread(
                    self._cam_index, enc, h264_fd,
                    self._raw_path.parent / "raw_tail.bin",
                    self._width, self._height)
                # NV12 ring: grab copies gray directly into these (UV preset to
                # 128); +4 slack over queue capacity so reuse can't catch up.
                self._nv12_ring = [
                    np.full((self._height * 3 // 2, self._width), 128, np.uint8)
                    for _ in range(ENCODE_QUEUE_DEPTH + 4)]
                self._ring_i = 0
                enc_thread.start()
                print(f"[grab{self._cam_index}] real-time NVENC encode (decoupled) -> {h264_path.name}", flush=True)
            except Exception as e:
                # Encoder unavailable: fall back to raw-to-disk so no data is lost.
                print(f"[grab{self._cam_index}] NVENC init failed, falling back to raw.bin: {e}", flush=True)
                enc_thread = None
                if h264_fd is not None:
                    os.close(h264_fd); h264_fd = None

        if recording and not kick and enc_thread is None:
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
        consec_timeouts = 0    # reset by every successful grab; stall detector
        bid_offset = 0         # added to raw block IDs after a re-arm
        awaiting_resync = False
        # ~5 s of silence at the 200 ms recording timeout. Long enough that a
        # burst of GigE resends can't trip it, short enough to lose seconds
        # rather than the rest of the session.
        STALL_TIMEOUTS = 25
        MAX_REARMS = 5         # stop thrashing if the link is genuinely dead
        first_frame_logged = False
        t_wait = 0.0   # cumulative s blocked in RetrieveResult (per 1000 frames)
        t_proc = 0.0   # cumulative s spent processing a frame (per 1000 frames)

        try:
            while self._running and self._camera.IsGrabbing():
                try:
                    timeout = 200 if recording else 2000
                    t0 = time.perf_counter()
                    result = self._camera.RetrieveResult(timeout, pylon.TimeoutHandling_ThrowException)
                    t1 = time.perf_counter()
                    t_wait += t1 - t0
                    if not result.GrabSucceeded():
                        print(f"[grab{self._cam_index}] grab failed: {result.ErrorCode} {result.ErrorDescription}", flush=True)
                        result.Release()
                        continue

                    img = result.Array

                    if self._snapshot_requested:
                        self.snapshot_frame = img.copy()  # full-resolution still
                        self._snapshot_requested = False

                    consec_timeouts = 0
                    if recording:
                        if kick:
                            # Copy gray into the next ring slot and submit to the
                            # router; it records metadata for frames it RELEASES
                            # (the common set), so this thread records none.
                            buf = self._nv12_ring[self._ring_i]
                            self._ring_i = (self._ring_i + 1) % len(self._nv12_ring)
                            buf[:self._height, :] = img
                            try:
                                raw_bid = result.BlockID
                            except Exception:
                                raw_bid = -1
                            dev_ts = result.TimeStamp * 1e-9
                            if awaiting_resync:
                                awaiting_resync = False
                                off = self._resync_offset(raw_bid, dev_ts)
                                if off is None:
                                    self.desynced = True
                                    self._router.retire(
                                        self._cam_index,
                                        "stream stalled and block IDs could not "
                                        "be realigned")
                                else:
                                    bid_offset = off
                                    print(f"[grab{self._cam_index}] resynced after "
                                          f"re-arm, block-ID offset {off}", flush=True)
                            if not self.desynced:
                                bid = raw_bid + bid_offset
                                self._last_bid_eff = bid
                                self._last_ts = dev_ts
                                self._router.submit(self._cam_index, bid,
                                                    dev_ts, buf)
                            self.frame_count += 1  # grabbed count (for logging)
                        elif enc_thread is not None:
                            persisted = self._put_frame(enc_thread, img)
                            if persisted:
                                self.frame_count += 1
                                self.timestamps.append(result.TimeStamp * 1e-9)
                                # GigE Vision block ID = trigger ordinal: makes a
                                # dropped frame a detectable, re-alignable gap
                                # instead of a silent cross-camera desync.
                                try:
                                    self.block_ids.append(result.BlockID)
                                except Exception:
                                    self.block_ids.append(-1)
                        else:
                            os.write(fd, img)
                            self.frame_count += 1
                            self.timestamps.append(result.TimeStamp * 1e-9)
                            try:
                                self.block_ids.append(result.BlockID)
                            except Exception:
                                self.block_ids.append(-1)

                    frame_n += 1
                    if recording and not first_frame_logged:
                        print(f"[grab{self._cam_index}] first frame received", flush=True)
                        first_frame_logged = True
                    t_proc += time.perf_counter() - t1
                    if recording and frame_n % 1000 == 0:
                        # wait >> proc and ~10 ms/frame -> loop keeps up (waits
                        # for triggers); proc-bound or wait ~0 -> loop is the
                        # bottleneck and a pool backlog is building.
                        qd = enc_thread.queue.qsize() if enc_thread else (
                            self._router.pending() if kick else 0)
                        print(f"[grab{self._cam_index}] frames={frame_n} timeouts={timeout_n} "
                              f"avg_wait={t_wait:.2f}ms avg_proc={t_proc:.2f}ms "
                              f"qsize={qd}", flush=True)
                        t_wait = t_proc = 0.0
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
                    consec_timeouts += 1
                    if recording and timeout_n in (1, 5, 20):
                        print(f"[grab{self._cam_index}] TIMEOUT #{timeout_n} (no frame in {timeout}ms, triggers_stopped={self._triggers_stopped})", flush=True)
                    if recording and self._triggers_stopped:
                        break
                    if not self._running:
                        break
                    # Stalled: the stream has gone quiet while triggers are still
                    # running. Restart it rather than time out for the rest of
                    # the session.
                    if (consec_timeouts >= STALL_TIMEOUTS and not self.desynced
                            and self.rearms < MAX_REARMS):
                        self.rearms += 1
                        consec_timeouts = 0
                        if self._rearm_stream(self.rearms):
                            awaiting_resync = recording and kick
                        elif recording and kick:
                            self.desynced = True
                            self._router.retire(self._cam_index,
                                                "stream stalled, re-arm failed")
                except Exception as e:
                    print(f"[grab{self._cam_index}] exception: {type(e).__name__}: {e}", flush=True)
                    if not self._running:
                        break
                    time.sleep(0.001)
        finally:
            print(f"[grab{self._cam_index}] exiting: frames={frame_n} "
                  f"timeouts={timeout_n} drops={self.drops} rearms={self.rearms}"
                  + (" DESYNCED" if self.desynced else ""), flush=True)
            if recording:
                self._log_stream_stats()  # before StopGrabbing resets counters
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
