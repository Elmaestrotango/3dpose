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

        for dev in sorted_devs:
            cam = pylon.InstantCamera(tlf.CreateDevice(dev))
            cam.Open()
            pylon.FeaturePersistence.Load(pfs_path, cam.GetNodeMap(), False)
            cam.MaxNumBuffer.SetValue(500)
            self._cameras.append(cam)

        self._set_freerun_mode()
        self._start_grab_threads()
        return True

    def _set_freerun_mode(self):
        for cam in self._cameras:
            try:
                cam.StopGrabbing()
            except Exception:
                pass
            cam.TriggerMode.SetValue("Off")
            cam.AcquisitionFrameRateEnable.SetValue(True)
            cam.AcquisitionFrameRate.SetValue(30.0)

    def _set_trigger_mode(self):
        for cam in self._cameras:
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

    def _start_grab_threads(self, raw_paths=None, display_every=1):
        self._stop_grab_threads()
        for i, cam in enumerate(self._cameras):
            rp = raw_paths[i] if raw_paths else None
            gt = GrabThread(i, cam, raw_path=rp, display_every=display_every)
            gt.start()
            self._grab_threads.append(gt)

    def _stop_grab_threads(self):
        for gt in self._grab_threads:
            gt.stop()
        for gt in self._grab_threads:
            gt.wait(5000)
        self._grab_threads.clear()

    def start_acquisition(self, raw_paths: list[Path], display_every: int = 10):
        self._stop_grab_threads()
        self._set_trigger_mode()
        self._start_grab_threads(raw_paths=raw_paths, display_every=display_every)

    def stop_acquisition(self) -> list[tuple[int, list[float]]]:
        for gt in self._grab_threads:
            gt.signal_triggers_stopped()
        for gt in self._grab_threads:
            gt.wait(5000)

        results = []
        for gt in self._grab_threads:
            results.append((gt.frame_count, gt.timestamps))
        self._grab_threads.clear()

        self._set_freerun_mode()
        self._start_grab_threads()
        return results

    def close_all(self):
        self._stop_grab_threads()
        for cam in self._cameras:
            try:
                cam.Close()
            except Exception:
                pass
        self._cameras.clear()
