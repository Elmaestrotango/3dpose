"""Main application window — wires cameras, sidebar, state machine, and encoding."""
import shutil
from datetime import datetime
import numpy as np
from enum import Enum
from pathlib import Path

from PyQt5.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QApplication, QMessageBox
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QPalette, QColor, QIcon

from gui_app.camera_manager import CameraManager
from gui_app.serial_controller import TeensyController
from gui_app.encode_worker import EncodeWorker
from gui_app.calibration_worker import CalibrationWorker
from gui_app.hardware_check import HardwareCheckThread, format_report
from gui_app.coverage_worker import CoverageWorker
from gui_app.session_config import SessionConfig, RigProfile
from gui_app.widgets.camera_grid import CameraGridWidget
from gui_app.widgets.sidebar import SidebarWidget

try:
    from gui_app.board_detector import BoardDetector
except Exception:  # OpenCV missing → coverage HUD disabled, rest of GUI still runs
    BoardDetector = None

CALIBRATION_SCRIPT = Path(__file__).parent.parent / "1_calibrate.py"


class State(Enum):
    IDLE = "IDLE"
    CALIBRATING = "CALIBRATING"
    RECORDING = "RECORDING"
    ENCODING = "ENCODING"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Panopticon")
        self.setMinimumSize(1000, 400)
        icon_path = Path(__file__).parent.parent / "panopticon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._apply_theme()

        self._state = State.IDLE
        self._acq_type = ""
        self._acq_fps = 0
        self._detector = None
        self._coverage_worker: CoverageWorker | None = None
        self._encode_worker: EncodeWorker | None = None
        self._calib_worker: CalibrationWorker | None = None
        self._config: SessionConfig | None = None
        self._video_dir: Path | None = None

        self._camera_mgr = CameraManager()
        self._teensy = TeensyController()
        self._hw_check_thread: HardwareCheckThread | None = None

        self._camera_grid = CameraGridWidget()
        self._sidebar = SidebarWidget()

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._camera_grid, stretch=3)

        sidebar_container = QWidget()
        sidebar_container.setStyleSheet("background-color: #141428; border-left: 1px solid #333;")
        sidebar_layout = QHBoxLayout(sidebar_container)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(self._sidebar)
        layout.addWidget(sidebar_container, stretch=0)

        self.setCentralWidget(central)

        self._sidebar.calibrate_toggled.connect(self._on_calibrate_toggle)
        self._sidebar.record_toggled.connect(self._on_record_toggle)
        self._sidebar.run_calibration_clicked.connect(self._on_run_calibration)
        self._sidebar.snapshot_clicked.connect(self._on_snapshot)
        self._sidebar.profile_changed.connect(self._on_profile_changed)
        self._camera_mgr.error.connect(self._on_camera_error)

        self._display_timer = QTimer()
        self._display_timer.timeout.connect(self._refresh_displays)
        self._display_timer.start(33)

        # Find best profile (first with valid pfs), block signals during selection
        self._profile = self._sidebar.current_profile
        for i, prof in enumerate(self._sidebar._profiles):
            if prof.pfs_path and Path(prof.pfs_path).exists():
                self._sidebar._profile_combo.blockSignals(True)
                self._sidebar._profile_combo.setCurrentIndex(i)
                self._sidebar._profile_combo.blockSignals(False)
                self._profile = prof
                if prof.output_dir:
                    self._sidebar._output_dir = prof.output_dir
                    self._sidebar._dir_button.setText(self._sidebar._truncate_path(prof.output_dir))
                    self._sidebar._dir_button.setToolTip(prof.output_dir)
                break
        self._open_cameras()
        self._size_to_screen()
        self._sidebar.set_status("IDLE", "#888")
        self._run_hardware_check()

    def _open_cameras(self):
        pfs = self._profile.pfs_path
        if pfs and Path(pfs).exists():
            if self._camera_mgr.open_all(pfs):
                n = self._camera_mgr.num_cameras
                self._camera_grid.setup_grid(n)
                self._camera_names = [f"cam{i+1}" for i in range(n)]
                return
        self._camera_grid.setup_grid(0)
        self._camera_names = []
        QTimer.singleShot(100, lambda: QMessageBox.warning(
            self, "Camera Error", "No cameras found or .pfs missing. Check connections and profile."))

    def _size_to_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        sidebar_w = 260
        grid_aspect = self._camera_grid.grid_aspect()
        target_h = int(screen.height() * 0.8)
        target_w = int(target_h * grid_aspect) + sidebar_w
        if target_w > screen.width() * 0.9:
            target_w = int(screen.width() * 0.9)
            target_h = int((target_w - sidebar_w) / grid_aspect)
        self.resize(target_w, target_h)
        self.move(
            (screen.width() - target_w) // 2 + screen.x(),
            (screen.height() - target_h) // 2 + screen.y(),
        )

    def _run_hardware_check(self):
        output_dir = self._profile.output_dir if self._profile else ""
        self._hw_check_thread = HardwareCheckThread(output_dir)
        self._hw_check_thread.finished.connect(self._on_hardware_check_done)
        self._hw_check_thread.start()

    def _on_hardware_check_done(self, report):
        if report.warnings:
            msg = format_report(report)
            print(msg, flush=True)
            QMessageBox.warning(self, "Hardware Check", msg)

    def _on_profile_changed(self, profile: RigProfile):
        if self._state != State.IDLE:
            return
        self._camera_mgr.close_all()
        self._profile = profile
        self._open_cameras()
        self._size_to_screen()

    def _apply_theme(self):
        app = QApplication.instance()
        app.setStyle("Fusion")
        p = QPalette()
        p.setColor(QPalette.Window, QColor(25, 25, 42))
        p.setColor(QPalette.WindowText, QColor(220, 220, 220))
        p.setColor(QPalette.Base, QColor(15, 15, 30))
        p.setColor(QPalette.AlternateBase, QColor(35, 35, 55))
        p.setColor(QPalette.Text, QColor(220, 220, 220))
        p.setColor(QPalette.Button, QColor(40, 40, 65))
        p.setColor(QPalette.ButtonText, QColor(220, 220, 220))
        p.setColor(QPalette.Highlight, QColor(80, 120, 200))
        p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        p.setColor(QPalette.ToolTipBase, QColor(40, 40, 65))
        p.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
        app.setPalette(p)

    def _refresh_displays(self):
        self._display_tick = getattr(self, '_display_tick', 0) + 1
        brightness = self._sidebar.brightness
        contrast = self._sidebar.contrast

        for i, frame in enumerate(self._camera_mgr.latest_frames):
            if frame is not None:
                if brightness != 0 or contrast != 0:
                    f = frame.astype(np.float32)
                    if contrast != 0:
                        factor = (100 + contrast) / 100
                        np.subtract(f, 128, out=f)
                        np.multiply(f, factor, out=f)
                        np.add(f, 128, out=f)
                    if brightness != 0:
                        np.add(f, brightness, out=f)
                    np.clip(f, 0, 255, out=f)
                    frame = f.astype(np.uint8)
                self._camera_grid.update_frame(i, frame)

        if self._display_tick % 10 == 0:
            for i, fps in enumerate(self._camera_mgr.current_fps):
                self._camera_grid.update_fps(i, fps)

    def _build_config(self) -> SessionConfig:
        vals = self._sidebar.get_field_values()
        return SessionConfig.from_profile(
            self._profile,
            date=vals["date"],
            mouse_1=vals["mouse_1"],
            mouse_2=vals["mouse_2"],
            assay=vals["assay"],
            experimenter=vals["experimenter"],
            cohort=vals["cohort"],
            cage=vals["cage"],
            notes=vals["notes"],
            base_data_dir=Path(self._sidebar.output_dir),
            camera_names=self._camera_names,
        )

    def _on_calibrate_toggle(self, checked):
        if checked:
            self._start_acquisition("calibration")
        elif self._state == State.CALIBRATING:
            self._stop_acquisition()

    def _on_record_toggle(self, checked):
        if checked:
            self._start_acquisition("recording")
        elif self._state == State.RECORDING:
            self._stop_acquisition()

    def _start_acquisition(self, acq_type: str):
        self._config = self._build_config()
        self._acq_type = acq_type
        video_dir = self._config.video_dir(acq_type)

        if video_dir.exists() and any(video_dir.rglob("*.mp4")):
            reply = QMessageBox.question(
                self, "Overwrite?",
                f"Existing files found in:\n{video_dir}\n\nOverwrite?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply == QMessageBox.No:
                self._sidebar.reset_toggles()
                return

        self._video_dir = video_dir
        for cam in self._camera_names:
            (self._video_dir / cam).mkdir(parents=True, exist_ok=True)

        raw_paths = [
            self._video_dir / cam / "raw.bin"
            for cam in self._camera_names
        ]

        # Calibration runs at a lower trigger rate (still sharp, plenty of distinct
        # board poses) with a smooth 1:1 preview; recording stays at the full rate
        # with a decimated preview to protect the disk-write loop.
        fps = self._config.rate_for(acq_type)
        self._acq_fps = fps
        display_every = 1 if acq_type == "calibration" else 10

        print(f"[acq] start_acquisition({acq_type}) fps={fps}: switching cameras to trigger mode", flush=True)
        self._camera_mgr.start_acquisition(raw_paths, display_every=display_every)

        print(f"[acq] opening teensy on {self._profile.serial_port}", flush=True)
        self._teensy = TeensyController(port=self._profile.serial_port)
        if not self._teensy.open():
            self._on_camera_error("Could not open serial port")
            return
        print(f"[acq] sending start_triggers pins={self._profile.trigger_pins} fps={fps}", flush=True)
        self._teensy.start_triggers(self._profile.trigger_pins, fps)
        print(f"[acq] start_acquisition done", flush=True)

        self._sidebar.set_fields_editable(False)
        if acq_type == "calibration":
            self._state = State.CALIBRATING
            self._sidebar.set_status("CALIBRATING", "#4488ff")
            self._start_coverage_hud()
        else:
            self._state = State.RECORDING
            self._sidebar.set_status("RECORDING", "#ff4444")

    def _start_coverage_hud(self):
        """Spin up the live ChArUco coverage graph for this calibration run."""
        self._detector = None
        board_cfg = self._profile.board_config
        n = self._camera_mgr.num_cameras
        if BoardDetector is None or n == 0 or not board_cfg or not Path(board_cfg).exists():
            return
        try:
            self._detector = BoardDetector(n, board_cfg)
        except Exception as e:
            print(f"[hud] coverage detector unavailable: {e}", flush=True)
            self._detector = None
            return
        self._sidebar.setup_coverage(n)
        self._sidebar.show_coverage()

        # Run detection off the UI thread on full-res frames (resolves oblique
        # cams, same as the post-hoc calibration).
        self._camera_mgr.set_keep_full(True)
        self._coverage_worker = CoverageWorker(self._detector, self._camera_mgr)
        self._coverage_worker.updated.connect(self._on_coverage_updated)
        self._coverage_worker.start()

    def _on_coverage_updated(self):
        if self._detector is not None:
            self._sidebar.update_coverage(self._detector)

    def _stop_coverage_hud(self):
        if self._coverage_worker is not None:
            self._coverage_worker.stop()
            self._coverage_worker.wait(2000)
            self._coverage_worker = None
        try:
            self._camera_mgr.set_keep_full(False)
        except Exception:
            pass

    def _stop_acquisition(self):
        self._teensy.stop_triggers(self._profile.trigger_pins)
        self._teensy.close()

        self._stop_coverage_hud()
        self._detector = None
        self._sidebar.hide_coverage()

        cam_results = self._camera_mgr.stop_acquisition()

        self._save_frametimes(cam_results)
        self._config.save_metadata()

        self._state = State.ENCODING
        self._sidebar.set_status("ENCODING", "#ffaa00")
        self._sidebar.set_toggles_enabled(False)

        self._encode_worker = EncodeWorker(
            self._video_dir,
            self._camera_names,
            self._acq_type,
            self._config.frame_width,
            self._config.frame_height,
            self._acq_fps,
            self._config.quality,
            self._config.date,
            self._config.session_id,
            max_parallel=self._config.encode_parallel,
        )
        self._encode_worker.progress.connect(self._sidebar.show_progress)
        self._encode_worker.finished_all.connect(self._on_encoding_done)
        self._encode_worker.start()

    def _save_frametimes(self, cam_results: list[tuple[int, list[float]]]):
        counts = [len(ts) for _, ts in cam_results if ts]
        if not counts:
            return
        min_frames = min(counts)
        frame_nums = np.arange(1, min_frames + 1, dtype=np.float64)

        for i, (count, timestamps) in enumerate(cam_results):
            if not timestamps:
                continue
            cam = self._camera_names[i]
            cam_dir = self._video_dir / cam

            ts_arr = np.array(timestamps[:min_frames])
            ts_arr -= ts_arr[0]

            np.save(cam_dir / "frametimes.npy", np.stack([frame_nums, ts_arr]))

            raw_path = cam_dir / "raw.bin"
            if raw_path.exists():
                frame_size = self._config.frame_width * self._config.frame_height
                expected_size = min_frames * frame_size
                actual_size = raw_path.stat().st_size
                if actual_size > expected_size:
                    with open(raw_path, "r+b") as f:
                        f.truncate(expected_size)

    def _on_encoding_done(self, results):
        self._sidebar.hide_progress()
        self._sidebar.set_fields_editable(True)
        self._sidebar.reset_toggles()
        self._state = State.IDLE
        self._sidebar.set_status("IDLE", "#888")

        frame_counts = [n for _, n, ok in results if ok]
        fps_vals = []
        for cam, n_frames, ok in results:
            if not ok:
                continue
            cam_dir = self._video_dir / cam / "frametimes.npy"
            try:
                ft = np.load(cam_dir)
                duration = ft[1][-1] - ft[1][0]
                fps_vals.append(ft.shape[1] / duration if duration > 0 else 0)
            except Exception:
                fps_vals.append(0)

        avg_fps = sum(fps_vals) / len(fps_vals) if fps_vals else 0
        min_frames = min(frame_counts) if frame_counts else 0
        max_frames = max(frame_counts) if frame_counts else 0

        if min_frames == max_frames:
            count_str = str(min_frames)
        else:
            count_str = f"{min_frames}-{max_frames}"

        self.statusBar().showMessage(
            f"{self._acq_type.title()} complete: {count_str} frames, {avg_fps:.1f} fps",
            15000,
        )

    def _on_run_calibration(self):
        config = self._build_config()
        calib_dir = config.video_dir("calibration")

        if not calib_dir.exists():
            QMessageBox.warning(self, "No Data", f"Calibration directory not found:\n{calib_dir}")
            return

        mp4s = list(calib_dir.rglob("*.mp4"))
        if not mp4s:
            QMessageBox.warning(self, "No Data", f"No calibration videos found in:\n{calib_dir}")
            return

        board_cfg = self._profile.board_config
        if not board_cfg or not Path(board_cfg).exists():
            QMessageBox.warning(
                self, "Missing Board Config",
                f"Board config not found: {board_cfg or '(not set)'}\n\n"
                "Set board_config in your profile YAML to a valid file in configs/boards/.")
            return

        self._sidebar.set_status("CALIBRATING...", "#aa88ff")
        self._sidebar.set_toggles_enabled(False)
        self.statusBar().showMessage("Running sleap-anipose calibration...")

        self._calib_worker = CalibrationWorker(
            config.session_dir, CALIBRATION_SCRIPT, board_cfg)
        self._calib_worker.status.connect(lambda s: self.statusBar().showMessage(s))
        self._calib_worker.finished.connect(self._on_calibration_done)
        self._calib_worker.start()

    def _on_calibration_done(self, success: bool, msg: str):
        self._sidebar.set_toggles_enabled(True)
        self._sidebar.set_status("IDLE", "#888")
        if success:
            config = self._build_config()
            src = config.video_dir("calibration") / "calibration.toml"
            dst = config.video_dir("recording") / "calibration.toml"
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                status = f"Calibration solved — copied to {dst}"
            else:
                status = "Calibration solved (no toml found to copy)"
            if msg:
                QMessageBox.warning(self, "Calibration Warnings", msg[:800])
                status += " (with warnings)"
            self.statusBar().showMessage(status, 15000)
        else:
            QMessageBox.warning(self, "Calibration Failed", msg[:800])
            self.statusBar().showMessage("Calibration failed", 10000)

    def _on_snapshot(self):
        if self._camera_mgr.num_cameras == 0:
            self.statusBar().showMessage("Snapshot: no cameras open", 5000)
            return
        self._camera_mgr.request_snapshots()
        # Give the grab threads a moment to stash the next full-res frame.
        QTimer.singleShot(250, self._save_snapshots)

    def _save_snapshots(self):
        from PIL import Image
        cfg = self._build_config()
        out_dir = cfg.session_dir / "snapshots" / f"{cfg.date}_{datetime.now().strftime('%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)
        frames = self._camera_mgr.snapshots
        saved = 0
        for i, frame in enumerate(frames):
            if frame is None:
                continue
            cam = self._camera_names[i] if i < len(self._camera_names) else f"cam{i+1}"
            try:
                Image.fromarray(frame).save(out_dir / f"{cam}.png")
                saved += 1
            except Exception as e:
                print(f"[snapshot] {cam} failed: {e}", flush=True)
        self.statusBar().showMessage(
            f"Snapshot: saved {saved}/{len(frames)} cameras → {out_dir}", 10000)

    def _on_camera_error(self, msg: str):
        QMessageBox.critical(self, "Error", msg)

    def closeEvent(self, event):
        self._display_timer.stop()
        self._stop_coverage_hud()
        if self._state in (State.CALIBRATING, State.RECORDING):
            self._teensy.stop_triggers(self._profile.trigger_pins)
            self._teensy.close()
        self._camera_mgr.close_all()
        if self._encode_worker and self._encode_worker.isRunning():
            self._encode_worker.wait(5000)
        if self._calib_worker and self._calib_worker.isRunning():
            self._calib_worker.wait(5000)
        event.accept()
