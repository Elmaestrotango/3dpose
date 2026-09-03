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

from pypylon import pylon

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


class QuietCamera:
    """Arms fine, then reports it is no longer grabbing.

    The nastiest shape of this failure: no exception, no timeout, nothing in the
    log. The while-loop condition simply goes false and run() falls out through
    `finally`. Before the catch-all retire, that left the coordinator waiting on
    a camera that had already gone home.
    """

    def __init__(self, grabs=False):
        self._grabs = grabs

    def StartGrabbing(self, *_a, **_k):
        pass

    def IsGrabbing(self):
        return self._grabs

    def StopGrabbing(self):
        pass


class ErroringCamera(QuietCamera):
    """Raises a non-timeout exception on every retrieve.

    Hits the broad `except Exception` handler, which used to print, sleep 1 ms
    and loop forever without ever arming the stall detector — so the camera
    consumed frames at 100 fps, discarded all of them, and starved every OTHER
    camera through the coordinator.
    """

    def __init__(self):
        super().__init__(grabs=True)
        self.calls = 0

    def RetrieveResult(self, *_a, **_k):
        self.calls += 1
        raise RuntimeError("simulated per-frame failure")


class TimingOutCamera(QuietCamera):
    """Times out forever: a GigE stream that went quiet and never came back.

    Exercises the re-arm ladder to exhaustion. Before the fix, once `rearms`
    reached MAX_REARMS the stall branch went false permanently and the thread
    sat timing out in silence for the rest of the session.
    """

    def __init__(self):
        super().__init__(grabs=True)
        self.rearms = 0

    def RetrieveResult(self, *_a, **_k):
        raise pylon.TimeoutException("simulated stream stall")

    def StartGrabbing(self, *_a, **_k):
        self.rearms += 1


def test_quiet_exit_retires(tmp):
    router = FakeRouter()
    t = _make(router, QuietCamera(grabs=False), tmp)
    t.run()
    assert router.retired, ("the grab loop exited without retiring — the "
                            "coordinator would wait forever on a camera that "
                            "is gone and force-drop every trigger for all of them")
    assert "exited" in router.retired[0][1].lower(), router.retired
    print("3) a silent exit (IsGrabbing goes False) retires the camera: PASS")


def test_repeated_errors_retire(tmp):
    router = FakeRouter()
    cam = ErroringCamera()
    t = _make(router, cam, tmp)
    t.run()
    assert router.retired, "a camera raising every frame was never retired"
    assert "error" in router.retired[0][1].lower(), router.retired
    # Must give up quickly rather than spin: bounded by MAX_CONSEC_ERRORS.
    assert cam.calls <= 25, f"spun {cam.calls} times before giving up"
    print(f"4) repeated frame errors retire after {cam.calls} attempts, "
          f"no infinite spin: PASS")


def test_rearm_exhaustion_retires(tmp):
    router = FakeRouter()
    cam = TimingOutCamera()
    t = _make(router, cam, tmp)
    t.run()
    assert router.retired, ("re-arms were exhausted and the camera was never "
                            "retired — the thread would time out in silence for "
                            "the rest of the session")
    assert "re-arm" in router.retired[0][1].lower(), router.retired
    print(f"5) re-arm exhaustion retires the camera "
          f"(after {cam.rearms} StartGrabbing calls): PASS")


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
        test_quiet_exit_retires(tmp)
        test_repeated_errors_retire(tmp)
        test_rearm_exhaustion_retires(tmp)
    print("\nALL GRAB-FAILURE TESTS PASS")


if __name__ == "__main__":
    main()
