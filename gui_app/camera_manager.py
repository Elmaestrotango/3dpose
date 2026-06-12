"""Manages all 6 cameras — opening, closing, and switching between free-run and trigger modes."""
import numpy as np
from pathlib import Path
from PyQt5.QtCore import QObject, pyqtSignal
import pypylon.pylon as pylon

from gui_app.grab_thread import GrabThread


class CameraManager(QObject):
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._cameras: list[pylon.InstantCamera] = []
        self._grab_threads: list[GrabThread] = []

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

    def request_snapshots(self):
        """Ask every camera to stash its next full-resolution frame."""
        for gt in self._grab_threads:
            gt.request_snapshot()

    def set_keep_full(self, flag: bool):
        """Toggle full-resolution frame retention (for the coverage HUD)."""
        for gt in self._grab_threads:
            gt.set_keep_full(flag)

    def open_all(self, pfs_path: str):
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
                cam.MaxNumBuffer.SetValue(1000)
                self._select_gige_driver(i, cam)
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
    def _select_gige_driver(i: int, cam: pylon.InstantCamera):
        """Prefer the in-kernel pylon GigE Vision (filter) driver over the
        user-space socket driver — kernel packet reassembly costs far less host
        CPU at 6x100 fps. No-op for non-GigE cameras or if unavailable."""
        try:
            sg = cam.GetStreamGrabberNodeMap()
            t = sg.GetNode("Type")
            if t is None:
                return
            avail = sg.GetNode("TypeIsWindowsFilterDriverAvailable")
            if avail is not None and avail.GetValue():
                t.FromString("WindowsFilterDriver")
            print(f"[cam{i+1}] GigE stream driver: {t.ToString()}", flush=True)
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
                cam.AcquisitionFrameRateEnable.SetValue(True)
                cam.AcquisitionFrameRate.SetValue(165.0)
            except Exception as e:
                print(f"[cam{i+1}] trigger config failed (camera offline?): {e}", flush=True)

    def _start_grab_threads(self, raw_paths=None, display_every=1,
                            realtime=False, width=0, height=0, quality=21):
        self._stop_grab_threads()
        for i, cam in enumerate(self._cameras):
            rp = raw_paths[i] if raw_paths else None
            gt = GrabThread(i, cam, raw_path=rp, display_every=display_every,
                            realtime=realtime, width=width, height=height, quality=quality)
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
                          quality: int = 21):
        self._stop_grab_threads()
        self._set_trigger_mode()
        self._start_grab_threads(raw_paths=raw_paths, display_every=display_every,
                                 realtime=realtime, width=width, height=height, quality=quality)

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

        results = []
        for gt in self._grab_threads:
            results.append((gt.frame_count, gt.timestamps, gt.block_ids))
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
