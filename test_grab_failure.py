"""A camera that fails to start must be RETIRED, not left silent.

This is the highest-damage failure mode in the capture path, and it is entirely
invisible while it happens. In kick-out mode `FrameSyncCoordinator` releases
trigger N only once EVERY camera has delivered N. A camera that never publishes
a frame therefore holds the frontier at 0 forever and the coordinator
force-drops every trigger for every camera — so ONE dead camera produces an
empty recording from ALL of them, with no error beyond a single line on stdout.

`retire()` is the escape hatch: it drops the camera from the alignment set so
the survivors keep recording aligned. These tests pin that every early-return
path in `GrabThread.run()` takes it.

No cameras and no NVENC needed — the camera and router are stubs.

    uv run python test_grab_failure.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# GrabThread subclasses QThread, so Qt must exist. Offscreen: no display needed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
_APP = QApplication.instance() or QApplication([])

import numpy as np

from gui_app import grab_thread as gt
from gui_app.grab_thread import GrabThread

W, H = 64, 48          # tiny: these tests never encode, only allocate


class FakeRouter:
    """Records retire() calls; mimics just enough of SyncEncodeRouter."""

    def __init__(self, max_lag=8):
        self.max_lag = max_lag
        self.retired = []

    def retire(self, cam, reason=""):
        self.retired.append((cam, reason))

    def pending(self):
        return 0

    def submit(self, *a, **k):
        raise AssertionError("submit() must not be reached in these tests")


class DeadCamera:
    """A camera whose stream refuses to start — the real-world case is a dead
    switch port, an unpowered camera, or a transport left in a bad state."""

    def __init__(self, exc=RuntimeError("the camera is offline")):
        self._exc = exc

    def StartGrabbing(self, *_a, **_k):
        raise self._exc

    def IsGrabbing(self):
        return False

    def StopGrabbing(self):
        pass


def _make(router, camera, tmp):
    t = GrabThread(cam_index=3, camera=camera, raw_path=tmp / "raw.bin",
                   display_every=10 ** 9, realtime=True,
                   width=W, height=H, router=router)
    t._running = True
    return t


def test_start_grabbing_failure_retires(tmp):
    router = FakeRouter()
    t = _make(router, DeadCamera(), tmp)
    t.run()                      # synchronous: no thread, no Qt event loop
    assert router.retired, ("StartGrabbing failed and the camera was NOT retired "
                            "— the coordinator would force-drop every trigger for "
                            "every camera and the whole session would be empty")
    cam, reason = router.retired[0]
    assert cam == 3, cam
    assert "grab" in reason.lower(), reason
    print("1) StartGrabbing failure retires the camera: PASS")


def test_ring_allocation_failure_retires(tmp, monkey_full):
    """A MemoryError allocating the NV12 ring must retire, not escape run().

    Reachable at scale rather than theoretical: the ring is `max_lag +
    ENCODE_QUEUE_DEPTH + 64` buffers per camera — 2.39 GiB each at max_lag=480,
    so ~21.5 GiB across 9 cameras on top of ~20.7 GiB of pylon pool. Escaping
    run() would take the GUI down with it.
    """
    router = FakeRouter()
    t = _make(router, DeadCamera(), tmp)
    with monkey_full:
        t.run()                  # must NOT raise
    assert router.retired, "ring MemoryError did not retire the camera"
    cam, reason = router.retired[0]
    assert cam == 3, cam
    assert "ring" in reason.lower(), reason
    print("2) NV12 ring MemoryError retires the camera, does not escape run(): PASS")


class _RaisingFull:
    """Context manager swapping np.full for one that raises MemoryError."""

    def __enter__(self):
        self._orig = gt.np.full

        def boom(*_a, **_k):
            raise MemoryError("simulated: cannot allocate the NV12 ring")

        gt.np.full = boom
        return self

    def __exit__(self, *_exc):
        gt.np.full = self._orig
        return False


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_start_grabbing_failure_retires(tmp)
        test_ring_allocation_failure_retires(tmp, _RaisingFull())
    print("\nALL GRAB-FAILURE TESTS PASS")


if __name__ == "__main__":
    main()
