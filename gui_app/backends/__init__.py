"""Camera backends — the vendor-specific layer, and the contract it must meet.

WHY THIS EXISTS
Everything else in `gui_app/` is vendor-neutral. Historically the Basler/pypylon
calls were spread through `camera_manager.py` and `grab_thread.py`, which made
"what would it take to support another camera?" unanswerable without reading
both files. This package is the answer: implement `CameraBackend`, and the rest
of the application does not change.

WHAT IS AND IS NOT ABSTRACTED
The **cold path** (enumerate, open, configure, trigger mode, teardown,
statistics) goes through `CameraBackend` methods. It runs a handful of times per
session, so an extra layer costs nothing and buys clarity.

The **hot path** — retrieving a frame, 100 times a second per camera — is
deliberately NOT wrapped in per-field accessor methods. The backend's
`retrieve()` returns a *native* grab-result object and this module documents the
attributes it must expose (see `GrabResultProtocol`). A new backend supplies a
thin adapter object rather than paying an indirection per attribute.

To be clear about the reasoning, because it would be easy to assume otherwise:
Python call overhead is NOT why. A call is ~60 ns, so even seven per frame
across nine cameras is ~40 microseconds per second — irrelevant. The real reason
is that the hot path has invariants a wrapper tends to quietly break: the frame
view must not outlive `Release()`, and it must not be copied on the way through
(a hidden copy here is exactly the 2.3 MB GIL-held memcpy that cost this project
years of frame loss — see docs/PERF_EXPERIMENTS.md E3). A documented duck-type
contract keeps those invariants visible at the point they matter.

WRITING A NEW BACKEND
1. Implement `CameraBackend` for your SDK.
2. Return result objects satisfying `GrabResultProtocol`.
3. Register it in `load_backend()`.
4. Run `test_grab_failure.py` (no hardware needed) and then `probe_lag.py`
   against real cameras, and check `cycle` equals your frame period.

The hard part is rarely the API. It is the guarantees:
  - a per-frame **monotonic trigger ordinal** that survives a stream restart
    (Basler: GigE Vision BlockID). Without it, cross-camera alignment has
    nothing to align on and every downstream stage silently mis-associates.
  - a **buffer pool** deep enough to absorb jitter, and a way to observe when it
    is exhausted (Basler: MaxNumBuffer, Statistic_Buffer_Underrun_Count).
  - **zero-copy access** to the pixel data. If your SDK only offers a copying
    accessor, measure it before assuming it is affordable.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class GrabResultProtocol(Protocol):
    """What `CameraBackend.retrieve()` must hand back.

    Attribute access here happens on the 10 ms hot path, so keep implementations
    cheap — no allocation, no copying, no locking.
    """

    def GrabSucceeded(self) -> bool:
        """False if this result is an error rather than an image."""

    @property
    def ErrorCode(self) -> int: ...

    @property
    def ErrorDescription(self) -> str: ...

    @property
    def BlockID(self) -> int:
        """Monotonic trigger ordinal, the SAME value on every camera for a given
        hardware trigger. This is the entire basis of cross-camera alignment.
        May wrap (Basler wraps at 65535 unless 64-bit IDs are negotiated);
        `alignment._unwrap_blockids` handles that."""

    @property
    def TimeStamp(self) -> int:
        """Device-clock timestamp in nanoseconds. Must be a free-running camera
        clock, not a host clock — it is used to re-derive the trigger ordinal
        after a stream restart, when BlockID resets."""

    @property
    def PaddingX(self) -> int:
        """Row padding in bytes. MUST be 0, or the (H, W) reshape of the raw
        buffer shears the image. The grab loop refuses to record if it is not."""

    @property
    def PaddingY(self) -> int: ...

    def GetArrayZeroCopy(self):
        """Context manager yielding a (H, W) uint8 view over the driver buffer
        WITHOUT copying.

        The view must remain valid until the context exits, and the caller
        guarantees it does not outlive `Release()`. If your SDK cannot do this,
        say so loudly in the backend rather than silently substituting a copy."""

    def Release(self) -> None:
        """Return the buffer to the driver pool. Called once per result."""


class CameraBackend(Protocol):
    """The cold path. One instance per application, stateless w.r.t. cameras."""

    #: Human-readable, e.g. "basler".
    name: str

    #: The exception `retrieve()` raises on timeout. The grab loop catches this
    #: specifically — a timeout is normal (triggers stopped) and must be
    #: distinguishable from a real failure.
    TimeoutException: type

    def enumerate_devices(self) -> list:
        """All attached cameras, in a STABLE order (sort by serial number).

        Order defines camera names (`cam1`...`camN`), which are baked into the
        calibration extrinsics — so an unstable order silently mislabels data."""

    def open(self, device, pfs_path: str, max_num_buffer: int):
        """Open and configure one camera. Raise on any problem; the caller
        refuses to start a partial set rather than shifting camera names."""

    def describe(self, cam) -> dict:
        """`{"width", "height", "pixel_format", "serial"}` read back FROM THE
        CAMERA, not from config. The caller aborts unless every camera agrees
        with the profile — a mismatched pixel format is silently destructive."""

    def set_freerun(self, cam, fps: float) -> None: ...

    def set_triggered(self, cam, rate_limit: float) -> None:
        """Hardware-trigger mode. `rate_limit` sets the camera's internal frame
        rate; note the minimum interval becomes `exposure + 1/rate_limit`, which
        is what caps usable exposure."""

    def start_grabbing(self, cam) -> None: ...
    def stop_grabbing(self, cam) -> None: ...
    def is_grabbing(self, cam) -> bool: ...

    def retrieve(self, cam, timeout_ms: int):
        """Block for the next frame. Returns a `GrabResultProtocol`.
        Raises `self.TimeoutException` if none arrives in time."""

    def close(self, cam) -> None: ...

    def stream_stats(self, cam) -> dict:
        """Per-stream counters for the log. Keys are backend-specific; include
        whatever distinguishes *host* starvation from *network* loss, since that
        is the distinction every capture problem eventually reduces to."""


def load_backend(name: str = "basler") -> CameraBackend:
    """Return a backend by name. Import is lazy so a missing SDK only breaks the
    backend that needs it, not the whole application."""
    if name == "basler":
        from gui_app.backends.basler import BaslerBackend
        return BaslerBackend()
    raise ValueError(
        f"unknown camera backend {name!r}. Available: 'basler'. "
        f"To add one, implement CameraBackend in gui_app/backends/ and register "
        f"it here — see this module's docstring for the guarantees required.")
