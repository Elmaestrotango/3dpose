"""Manages all 6 cameras — opening, closing, and switching between free-run and trigger modes."""
import numpy as np
from pathlib import Path
from PyQt5.QtCore import QObject, pyqtSignal
import pypylon.pylon as pylon

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

    def __init__(self):
        super().__init__()
        self._cameras: list[pylon.InstantCamera] = []
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
                 trigger_rate_limit: float = 165.0):
        """trigger_rate_limit: AcquisitionFrameRate to apply in trigger mode, or
        0 to disable the limiter altogether — see _set_trigger_mode."""
        self._trigger_rate_limit = trigger_rate_limit
        tlf = pylon.TlFactory.GetInstance()
        devices = tlf.EnumerateDevices()
        if len(devices) == 0:
            self.error.emit("No cameras found")
            return False

        sorted_devs = sorted(devices, key=lambda d: d.GetSerialNumber())

        for i, dev in enumerate(sorted_devs):
            try:
                cam = pylon.InstantCamera(tlf.CreateDevice(dev))
                cam.Open()
                pylon.FeaturePersistence.Load(pfs_path, cam.GetNodeMap(), False)
                # 1000 buffers = ~2.3 GB/cam at 1920x1200 (10 s of slack at
                # 100 fps); ~13.8 GB across 6 cams, fine on the 64 GB machine.
                cam.MaxNumBuffer.SetValue(MAX_NUM_BUFFER)
                self._enable_extended_block_ids(i, cam)
                self._select_gige_driver(i, cam, gige_driver)
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

    @staticmethod
    def _enable_extended_block_ids(i: int, cam: pylon.InstantCamera):
        """Use 64-bit GVSP block IDs so the trigger ordinal doesn't wrap at
        65535 (~11 min at 100 fps). alignment._unwrap_blockids is the software
        fallback if the camera/driver can't honor this."""
        ok = False
        try:
            nm = cam.GetNodeMap()
            cam_node = nm.GetNode("GevGVSPExtendedIDMode")
            if cam_node is not None:
                cam_node.FromString("On")
                ok = True
        except Exception as e:
            print(f"[cam{i+1}] GevGVSPExtendedIDMode unavailable: {e}", flush=True)
        try:
            sg = cam.GetStreamGrabberNodeMap()
            sg_node = sg.GetNode("UseExtendedIdIfAvailable")
            if sg_node is not None:
                sg_node.SetValue(True)
                ok = True
        except Exception as e:
            print(f"[cam{i+1}] UseExtendedIdIfAvailable unavailable: {e}", flush=True)
        print(f"[cam{i+1}] extended (64-bit) block IDs: "
              f"{'enabled' if ok else 'UNAVAILABLE — relying on software unwrap'}",
              flush=True)

    @staticmethod
    def _select_gige_driver(i: int, cam: pylon.InstantCamera, which: str = "socket"):
        """Select the GigE receive driver per the profile's `gige_driver`.

        "socket": user-space driver — costs more CPU but its packet resends
        reliably recover lost packets (raw mode held 100 fps +-1 on it).
        "filter": in-kernel pylon GigE Vision driver — far less host CPU, but
        with default resend settings it silently dropped ~23% of frames
        (~5,800 single-frame gaps/cam, 2026-06-12 test) under 6x100 fps load.
        "auto": leave pylon's own default. No-op for non-GigE cameras."""
        sym = {"socket": "SocketDriver", "filter": "WindowsFilterDriver"}.get(which)
        try:
            sg = cam.GetStreamGrabberNodeMap()
            t = sg.GetNode("Type")
            if t is None:
                return
            if sym is not None:
                avail = sg.GetNode(f"TypeIs{sym}Available")
                if avail is None or avail.GetValue():
                    t.FromString(sym)
            extra = ""
            if t.ToString() == "SocketDriver":
                # Max out the per-stream socket receive buffer (KB): more slack
                # for the receive thread when encode threads contend for CPU.
                try:
                    sbs = sg.GetNode("SocketBufferSize")
                    sbs_max = sg.GetNode("SocketBufferSize_Max")
                    if sbs is not None and sbs_max is not None:
                        sbs.SetValue(sbs_max.GetValue())
                        extra = f" (SocketBufferSize={sbs.GetValue()} KB)"
                except Exception:
                    pass
            print(f"[cam{i+1}] GigE stream driver: {t.ToString()}{extra}", flush=True)
        except Exception as e:
            print(f"[cam{i+1}] GigE driver selection skipped: {e}", flush=True)

    def _set_freerun_mode(self):
        for i, cam in enumerate(self._cameras):
            try:
                try:
                    cam.StopGrabbing()
                except Exception:
                    pass
                cam.TriggerMode.SetValue("Off")
                cam.AcquisitionFrameRateEnable.SetValue(True)
                cam.AcquisitionFrameRate.SetValue(30.0)
            except Exception as e:
                # A camera that dropped off the bus must not abort teardown for the
                # rest — log and continue so the surviving cameras still recover.
                print(f"[cam{i+1}] free-run config failed (camera offline?): {e}", flush=True)

    def _set_trigger_mode(self):
        for i, cam in enumerate(self._cameras):
            try:
                try:
                    cam.StopGrabbing()
                except Exception:
                    pass
                cam.TriggerSelector.SetValue("FrameStart")
                cam.TriggerMode.SetValue("On")
                cam.TriggerSource.SetValue("Line1")
                cam.TriggerActivation.SetValue("RisingEdge")
                # The camera's internal rate generator does nothing useful while
                # it is externally triggered, but it still caps the minimum
                # interval at `exposure + 1/AcquisitionFrameRate` — which is what
                # capped exposure at ~3.94 ms and, at the old rate of 100, caused
                # the original 50 fps bug. Disabling it (`trigger_rate_limit: 0`)
                # leaves only the sensor readout in the way. Kept per-profile so
                # the 3dface rig stays on the proven 165 until it is tested there.
                limit = getattr(self, "_trigger_rate_limit", 165.0)
                if limit and limit > 0:
                    cam.AcquisitionFrameRateEnable.SetValue(True)
                    cam.AcquisitionFrameRate.SetValue(float(limit))
                else:
                    cam.AcquisitionFrameRateEnable.SetValue(False)
                    if i == 0:
                        print("[cam] trigger-rate limiter DISABLED "
                              "(exposure bounded by sensor readout only)", flush=True)
            except Exception as e:
                print(f"[cam{i+1}] trigger config failed (camera offline?): {e}", flush=True)

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
                cam.Close()
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
                cam.StopGrabbing()
            except Exception:
                pass
            try:
                cam.Close()
            except Exception:
                pass
        self._cameras.clear()
