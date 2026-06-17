"""Real-time cross-camera frame sync — the "kick out frames not seen by every
camera, before they're encoded" path.

Each grab thread submits its successfully-grabbed frames in trigger (block-ID)
order. The coordinator releases a trigger to the encoders ONLY once every camera
has reached it AND all of them captured it; triggers any camera missed are
dropped before encoding. So each camera's encoder receives a gapless stream of
frames that are identical across cameras — normal GOP encoding then yields
equal-length, trigger-aligned videos with no post-hoc re-encode.

This is pure logic (no Qt, no pylon, no frame copies of its own) so it can be
proven equivalent to the post-hoc block-ID intersection in a headless test
before it is wired into capture. Frame objects are opaque pass-through tokens.

Design notes:
- Cameras are hardware-triggered in lockstep, so confirmation lag is ~1-2
  frames. A camera that missed trigger N reveals it by delivering N+1 (a gap in
  its block-ID sequence); a camera with incomplete/underrun frames simply never
  submits that block ID.
- `max_lag` bounds how far ahead the fastest camera may get before a lagging
  camera's missing triggers are force-dropped — so one stalled camera can't
  freeze the others (it only drops triggers the post-hoc intersection would
  drop too). Set it comfortably above the resend-recovery window.
- Block IDs are unwrapped per camera (16-bit GVSP wrap at 65535) so the path
  works even if 64-bit extended IDs aren't honored.
"""
from collections import deque

BLOCKID_WRAP = 65535


class FrameSyncCoordinator:
    def __init__(self, n_cams: int, max_lag: int = 240):
        self.n = int(n_cams)
        self.max_lag = int(max_lag)
        self._pending = [deque() for _ in range(self.n)]  # (block_id, frame)
        self._frontier = [0] * self.n        # highest unwrapped ID seen per cam
        self._seen_any = [False] * self.n
        self._last_raw = [0] * self.n         # for incremental wrap unwrap
        self._wrap_off = [0] * self.n
        self._decided_upto = 0                # highest block ID whose fate is set
        # stats
        self.released = 0                     # common frames passed to encoders
        self.dropped = 0                      # frames kicked out (not common)
        self.forced = 0                       # frames dropped by max_lag forcing

    def pending_depth(self) -> int:
        """Max frames buffered awaiting a release decision (for monitoring)."""
        return max((len(p) for p in self._pending), default=0)

    def _unwrap(self, cam: int, raw: int) -> int:
        if self._seen_any[cam] and raw < self._last_raw[cam] - (BLOCKID_WRAP // 2):
            self._wrap_off[cam] += BLOCKID_WRAP
        self._last_raw[cam] = raw
        return raw + self._wrap_off[cam]

    def submit(self, cam: int, raw_block_id: int, frame):
        """Register a successfully-grabbed frame. Returns a list of
        (cam, block_id, frame) ready to encode now, in per-camera order."""
        bid = self._unwrap(cam, raw_block_id)
        self._seen_any[cam] = True
        if bid <= self._decided_upto:
            # Late arrival (e.g. recovered after we force-dropped its trigger);
            # its slot is already decided, so it can't be aligned — drop it.
            self.dropped += 1
            return []
        self._pending[cam].append((bid, frame))
        self._frontier[cam] = bid
        return self._advance()

    def _watermark(self) -> int:
        # All cameras have reached at least this trigger.
        wm = min(self._frontier)
        # Force progress if the fastest camera is too far ahead of a laggard.
        fastest = max(self._frontier)
        if fastest - wm > self.max_lag:
            wm = fastest - self.max_lag
        return wm

    def _advance(self):
        ready = []
        wm = self._watermark()
        while True:
            heads = [self._pending[c][0][0] for c in range(self.n) if self._pending[c]]
            if not heads:
                break
            t = min(heads)
            if t > wm:
                break
            havers = [c for c in range(self.n)
                      if self._pending[c] and self._pending[c][0][0] == t]
            if len(havers) == self.n:
                for c in havers:
                    _bid, frame = self._pending[c].popleft()
                    ready.append((c, t, frame))
                self.released += self.n
            else:
                forced = t > min(self._frontier)  # dropped only because of max_lag
                for c in havers:
                    self._pending[c].popleft()
                self.dropped += len(havers)
                if forced:
                    self.forced += len(havers)
            self._decided_upto = t
        return ready

    def flush(self):
        """End of recording: no more frames will arrive, so decide every
        remaining trigger. Returns the final ready list."""
        ready = []
        while True:
            heads = [self._pending[c][0][0] for c in range(self.n) if self._pending[c]]
            if not heads:
                break
            t = min(heads)
            havers = [c for c in range(self.n)
                      if self._pending[c] and self._pending[c][0][0] == t]
            if len(havers) == self.n:
                for c in havers:
                    _bid, frame = self._pending[c].popleft()
                    ready.append((c, t, frame))
                self.released += self.n
            else:
                for c in havers:
                    self._pending[c].popleft()
                self.dropped += len(havers)
            self._decided_upto = t
        return ready
