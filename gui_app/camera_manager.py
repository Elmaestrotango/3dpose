"""Manages all 6 cameras — opening, closing, and switching between free-run and trigger modes."""
import numpy as np
from pathlib import Path
from PyQt5.QtCore import QObject, pyqtSignal
from gui_app.backends import load_backend
from gui_app.grab_thread import GrabThread

#: Driver-side buffers per camera. 1000 is 10 s of slack at 100 fps, and it
#: costs n_cams x 1000 x 2.304 MB of RAM — 13.8 GiB at 6 cameras, 20.7 GiB at 9.
#: Exported so the capacity preflight can do that arithmetic before a recording
#: starts instead of discovering it as a MemoryError inside a grab thread.
#:
#: The deep slack is also what let a 1.5% per-frame deficit hide for ~11 minutes
#: before anything went wrong (see docs/PERF_EXPERIMENTS.md): nothing errors, the
#: pool just quietly fills and every frame retrieved gets staler. Reducing it
#: would make that failure loud within a second — but it is ALSO what absorbs
#: genuine GigE jitter, and pool size is not monotonic (1000 NV12 ring buffers
#: starved capture outright in June), so it must not be changed without a rig
#: A/B. Left at 1000 deliberately.
MAX_NUM_BUFFER = 1000


class CameraManager(QObject):
    error = pyqtSignal(str)

    def __init__(self, backend: str = "basler"):
        # The only vendor-specific object in this class. Everything below is
        # camera-agnostic orchestration; see gui_app/backends/__init__.py for
        # what a new backend has to provide.
        self._backend = load_backend(backend)
        super().__init__()
        self._cameras: list = []
        self._geometry = None      # (w, h) agreed by every camera
        self._grab_threads: list[GrabThread] = []
        self._router = None  # SyncEncodeRouter in real-time kick-out mode

    @property
    def num_cameras(self) -> int:
        return len(self._cameras)

    @property
    def latest_frames(self) -> list:
        return [gt.latest_frame for gt in self._grab_threads]

    @property
    def current_fps(self) -> list[float]:
        return [gt.current_fps for gt in self._grab_threads]

    @property
    def snapshots(self) -> list:
        return [gt.snapshot_frame for gt in self._grab_threads]

    @property
    def latest_full_frames(self) -> list:
        return [gt.latest_full_frame for gt in self._grab_threads]

    @property
    def frame_counts(self) -> list[int]:
        return [gt.frame_count for gt in self._grab_threads]

    def request_snapshots(self):
        """Ask every camera to stash its next full-resolution frame."""
        for gt in self._grab_threads:
            gt.request_snapshot()

    def set_keep_full(self, flag: bool):
        """Toggle full-resolution frame retention (for the coverage HUD)."""
        for gt in self._grab_threads:
            gt.set_keep_full(flag)

    def open_all(self, pfs_path: str, gige_driver: str = "socket",
                 trigger_rate_limit: float = 165.0, expect_cameras: int = 0):
        """trigger_rate_limit: AcquisitionFrameRate to apply in trigger mode, or
        0 to disable the limiter altogether — see _set_trigger_mode.

        expect_cameras: if nonzero, refuse to start unless exactly this many
        cameras enumerate."""
        self._trigger_rate_limit = trigger_rate_limit
        devices = self._backend.enumerate_devices()
        if len(devices) == 0:
            self.error.emit("No cameras found")
            return False

        # A camera that fails to OPEN is caught below. A camera that never
        # ENUMERATES — dead switch port, unpowered, still booting — is invisible
        # to that check, and it is the more dangerous case: names are positional
        # by serial order (`cam{i+1}`), so a missing camera 3 silently renames
        # physical 4..9 to cam3..cam8. Every extrinsic in calibration.toml then
        # attaches to the wrong physical camera, triangulation still runs, and
        # the 3D output is simply wrong. Three switches make this likelier.
        if expect_cameras and len(devices) != expect_cameras:
            found = ", ".join(sorted(d.GetSerialNumber() for d in devices))
            self.error.emit(
                f"Expected {expect_cameras} cameras but {len(devices)} "
                f"enumerated.\n\nFound: {found}\n\n"
                f"Camera names are assigned by serial-number order, so starting "
                f"with a missing camera would rename every camera after it and "
                f"attach the calibration extrinsics to the wrong physical "
                f"cameras. Power-cycle the missing camera and reselect the "
                f"profile.")
            return False

        sorted_devs = devices          # backend guarantees a stable order

        for i, dev in enumerate(sorted_devs):
            try:
                cam = self._backend.open(dev, pfs_path, MAX_NUM_BUFFER)
                # Read back what the .pfs actually applied. FeaturePersistence
                # is loaded with validation disabled, and CLAUDE.md tells users
                # to edit the .pfs in pylon Viewer — where ROI and pixel format
                # are one click away. Both failure modes are severe:
                #   - a Width/Height divergence makes `buf[:H,:] = img` raise
                #     EVERY frame, which now retires the camera but wastes a
                #     session;
                #   - Mono12 makes the frame uint16 and that same assignment
                #     truncates **mod 256 with no error at all** (measured:
                #     300 -> 44), yielding a full-length, perfectly aligned,
                #     visually shredded recording that looks fine until someone
                #     tries to label it.
                info = self._backend.describe(cam)
                pf, w, h = info["pixel_format"], info["width"], info["height"]
                if pf != "Mono8":
                    raise RuntimeError(
                        f"PixelFormat is {pf}, not Mono8. The capture path "
                        f"assumes 8-bit; anything wider is silently truncated "
                        f"mod 256. Fix the .pfs.")
                if self._geometry and (w, h) != self._geometry:
                    raise RuntimeError(
                        f"resolution {w}x{h} differs from camera 1 "
                        f"({self._geometry[0]}x{self._geometry[1]}); all "
                        f"cameras must match.")
                self._geometry = (w, h)
                print(f"[cam{i+1}] {info['serial']} {w}x{h} {pf}", flush=True)
                self._backend.enable_extended_block_ids(i, cam)
                self._backend.select_gige_driver(i, cam, gige_driver)
            except Exception as e:
                # Don't continue with a partial set: camera names are assigned by
                # serial-number order, so a missing camera would silently shift
                # every later camera's name and mislabel the recorded data.
                self.close_all()
                self.error.emit(
                    f"Camera {dev.GetSerialNumber()} failed to open/configure:\n{e}\n\n"
                    "Power-cycle it (or close the app holding it) and reselect the profile.")
                return False
            self._cameras.append(cam)

        self._set_freerun_mode()
        self._start_grab_threads()
        return True

    def _set_freerun_mode(self):
        for i, cam in enumerate(self._cameras):
            try:
                self._backend.set_freerun(cam, 30.0)
            except Exception as e:
                # A camera that dropped off the bus must not abort teardown for
                # the rest — log and continue so the survivors still recover.
                print(f"[cam{i+1}] free-run config failed (camera offline?): {e}",
                      flush=True)

    def _set_trigger_mode(self):
        limit = getattr(self, "_trigger_rate_limit", 165.0)
        for i, cam in enumerate(self._cameras):
            try:
                self._backend.set_triggered(cam, limit, announce=(i == 0))
            except Exception as e:
                print(f"[cam{i+1}] trigger config failed (camera offline?): {e}",
                      flush=True)

    def _start_grab_threads(self, raw_paths=None, display_every=1,
                            realtime=False, width=0, height=0, quality=21,
                            fps=100):
        self._stop_grab_threads()
        for i, cam in enumerate(self._cameras):
            rp = raw_paths[i] if raw_paths else None
            gt = GrabThread(i, cam, raw_path=rp, display_every=display_every,
                            realtime=realtime, width=width, height=height,
                            quality=quality, fps=fps, router=self._router)
            gt.start()
            self._grab_threads.append(gt)

    def _stop_grab_threads(self):
        for gt in self._grab_threads:
            gt.stop()
        for gt in self._grab_threads:
            gt.wait(5000)
        self._grab_threads.clear()

    def start_acquisition(self, raw_paths: list[Path], display_every: int = 10,
                          realtime: bool = False, width: int = 0, height: int = 0,
                          quality: int = 21, fps: int = 100,
                          realtime_kick: bool = False,
                          kick_max_lag: int = 240):
        self._stop_grab_threads()
        self._router = None
        if realtime and realtime_kick:
            # Shared router gates frames through the cross-camera coordinator so
            # only frames every camera captured get encoded (already aligned, no
            # post-hoc re-encode). Falls back to the decoupled path if NVENC init
            # fails for any camera.
            from gui_app.sync_encode import SyncEncodeRouter
            router = SyncEncodeRouter(raw_paths, width, height, quality,
                                      fps=fps, max_lag=kick_max_lag)
            if router.available:
                router.start()
                self._router = router
                print("[acq] real-time kick-out router active", flush=True)
            else:
                print("[acq] kick-out unavailable, using decoupled encode", flush=True)
        self._set_trigger_mode()
        self._start_grab_threads(raw_paths=raw_paths, display_every=display_every,
                                 realtime=realtime, width=width, height=height, quality=quality,
                                 fps=fps)

    def stop_acquisition(self) -> list[tuple[int, list[float], list[int]]]:
        """Stop the grab threads and return each camera's
        (frame_count, timestamps, block_ids).

        Does NOT restore preview — the caller should save frametimes/metadata first,
        then call resume_preview(). Separating data collection from the camera
        reconfigure means a camera that dropped off the bus can't crash teardown
        (or take the other cameras' data down with it) before the data is written.
        """
        for gt in self._grab_threads:
            gt.signal_triggers_stopped()
        for gt in self._grab_threads:
            gt.wait(5000)
        # A thread still draining its encoder MUST be waited out: proceeding
        # would reconfigure/restart the camera while the old thread still calls
        # into pylon on it — concurrent native access, hard crash. The encoder
        # drain path is bounded (~95 s worst case: 30 s sentinel put + 60 s
        # join), so wait it out loudly rather than racing it.
        for i, gt in enumerate(self._grab_threads):
            if gt.isRunning():
                print(f"[cam{i+1}] grab thread still draining at stop, waiting...", flush=True)
                if not gt.wait(100000):
                    print(f"[cam{i+1}] grab thread DID NOT EXIT after 100 s "
                          f"(GPU wedged?) — preview restart may be unstable, "
                          f"consider restarting the app", flush=True)

        if self._router is not None:
            # Kick-out mode: grab threads have stopped submitting; flush the
            # coordinator and drain the shared encoders. Metadata (the released,
            # already-common frames) comes from the router, not the grab threads.
            results = self._router.stop()
            self._router = None
        else:
            results = [(gt.frame_count, gt.timestamps, gt.block_ids)
                       for gt in self._grab_threads]
        self._grab_threads.clear()
        return results

    def resume_preview(self):
        """Return all cameras to free-run preview after an acquisition. Resilient
        to a camera that went offline mid-session (it is skipped, not fatal)."""
        self._set_freerun_mode()
        self._start_grab_threads()

    def close_all(self):
        self._stop_grab_threads()
        for cam in self._cameras:
            try:
                self._backend.close(cam)
            except Exception:
                pass
        self._cameras.clear()

    def abandon(self):
        """Tear down capture immediately, WITHOUT draining encoders (for app
        quit mid-session). Closes the kick-out router's output fds so the
        half-baked stream files unlock and can be deleted."""
        for gt in self._grab_threads:
            gt.stop()
        for gt in self._grab_threads:
            gt.wait(3000)
        self._grab_threads.clear()
        if self._router is not None:
            try:
                self._router.abandon()
            except Exception:
                pass
            self._router = None
        for cam in self._cameras:
            try:
                self._backend.stop_grabbing(cam)
            except Exception:
                pass
            try:
                self._backend.close(cam)
            except Exception:
                pass
        self._cameras.clear()
