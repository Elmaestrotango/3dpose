"""Sidebar widget with session parameters, toggle switches, progress bar, and status."""
from datetime import datetime
from pathlib import Path
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QLabel,
    QProgressBar, QFrame, QPushButton, QFileDialog, QSlider, QComboBox,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor

from gui_app.widgets.toggle_switch import ToggleSwitch
from gui_app.session_config import RigProfile, REPO_ROOT


class SidebarWidget(QWidget):
    calibrate_toggled = pyqtSignal(bool)
    record_toggled = pyqtSignal(bool)
    run_calibration_clicked = pyqtSignal()
    profile_changed = pyqtSignal(object)

    def __init__(self, default_output_dir: str = str(REPO_ROOT / "data"), parent=None):
        super().__init__(parent)
        self.setFixedWidth(260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel("Metadata")
        title.setFont(QFont("Segoe UI", 13, QFont.Bold))
        title.setStyleSheet("color: #dcdcdc; border: none;")
        layout.addWidget(title)

        # Profile selector
        self._profile_combo = QComboBox()
        self._profile_combo.setStyleSheet(
            "QComboBox { background: #1a1a2e; color: #dcdcdc; border: 1px solid #444; "
            "border-radius: 3px; padding: 4px 6px; font-size: 11px; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView { background: #1a1a2e; color: #dcdcdc; selection-background-color: #5078c8; }"
        )
        self._profiles: list[RigProfile] = []
        for path in RigProfile.list_profiles():
            profile = RigProfile.load(path)
            self._profiles.append(profile)
            self._profile_combo.addItem(profile.name)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        layout.addWidget(self._profile_combo)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #444;")
        layout.addWidget(sep)

        self._output_dir = default_output_dir
        self._dir_button = QPushButton(self._truncate_path(self._output_dir))
        self._dir_button.setToolTip(self._output_dir)
        self._dir_button.setStyleSheet(
            "QPushButton { background: #1a1a2e; color: #88aadd; border: 1px solid #444; "
            "border-radius: 3px; padding: 5px 8px; font-size: 10px; text-align: left; }"
            "QPushButton:hover { border-color: #5078c8; background: #222244; }"
        )
        self._dir_button.clicked.connect(self._pick_output_dir)
        layout.addWidget(self._dir_button)

        layout.addSpacing(4)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self._fields: dict[str, QLineEdit] = {}
        defaults = [
            ("date", datetime.now().strftime("%Y%m%d")),
            ("mouse_1", ""),
            ("mouse_2", ""),
            ("assay", "open_field"),
            ("experimenter", "IT"),
            ("cohort", ""),
            ("cage", ""),
            ("notes", ""),
        ]
        for name, default in defaults:
            field = QLineEdit(default)
            field.setStyleSheet(
                "QLineEdit { background: #1a1a2e; color: #dcdcdc; border: 1px solid #444; "
                "border-radius: 3px; padding: 4px 6px; font-size: 11px; }"
                "QLineEdit:focus { border-color: #5078c8; }"
                "QLineEdit:read-only { background: #111122; color: #888; }"
            )
            label_text = name.replace("_", " ").title()
            label = QLabel(label_text)
            label.setStyleSheet("color: #aaa; font-size: 11px; border: none;")
            form.addRow(label, field)
            self._fields[name] = field

        layout.addLayout(form)
        layout.addSpacing(12)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #444;")
        layout.addWidget(sep2)
        layout.addSpacing(4)

        acq_label = QLabel("Acquisition")
        acq_label.setFont(QFont("Segoe UI", 11, QFont.Bold))
        acq_label.setStyleSheet("color: #dcdcdc; border: none;")
        layout.addWidget(acq_label)

        self._calibrate_toggle = ToggleSwitch("Calibrate", QColor(66, 133, 244))
        self._record_toggle = ToggleSwitch("Record", QColor(234, 67, 53))
        self._calibrate_toggle.toggled.connect(self._on_calibrate)
        self._record_toggle.toggled.connect(self._on_record)

        calib_row = QHBoxLayout()
        calib_row.setSpacing(6)
        calib_row.addWidget(self._calibrate_toggle, stretch=1)
        self._run_calib_btn = QPushButton("Solve")
        self._run_calib_btn.setFixedSize(50, 28)
        self._run_calib_btn.setToolTip("Run sleap-anipose calibration on recorded videos")
        self._run_calib_btn.setStyleSheet(
            "QPushButton { background: #2a2a4a; color: #88aadd; border: 1px solid #444; "
            "border-radius: 3px; font-size: 10px; }"
            "QPushButton:hover { background: #333366; border-color: #5078c8; }"
            "QPushButton:disabled { color: #555; border-color: #333; }"
        )
        self._run_calib_btn.clicked.connect(self.run_calibration_clicked.emit)
        calib_row.addWidget(self._run_calib_btn)
        layout.addLayout(calib_row)

        layout.addWidget(self._record_toggle)

        layout.addSpacing(12)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet("color: #444;")
        layout.addWidget(sep3)
        layout.addSpacing(4)

        display_label = QLabel("Display")
        display_label.setFont(QFont("Segoe UI", 11, QFont.Bold))
        display_label.setStyleSheet("color: #dcdcdc; border: none;")
        layout.addWidget(display_label)

        slider_style = (
            "QSlider::groove:horizontal { background: #1a1a2e; height: 4px; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #5078c8; width: 12px; margin: -4px 0; border-radius: 6px; }"
            "QSlider::sub-page:horizontal { background: #5078c8; border-radius: 2px; }"
        )

        bright_row = QHBoxLayout()
        bright_lbl = QLabel("Brightness")
        bright_lbl.setStyleSheet("color: #aaa; font-size: 10px; border: none;")
        bright_lbl.setFixedWidth(65)
        self._brightness_slider = QSlider(Qt.Horizontal)
        self._brightness_slider.setRange(-100, 100)
        self._brightness_slider.setValue(0)
        self._brightness_slider.setStyleSheet(slider_style)
        bright_row.addWidget(bright_lbl)
        bright_row.addWidget(self._brightness_slider)
        layout.addLayout(bright_row)

        contrast_row = QHBoxLayout()
        contrast_lbl = QLabel("Contrast")
        contrast_lbl.setStyleSheet("color: #aaa; font-size: 10px; border: none;")
        contrast_lbl.setFixedWidth(65)
        self._contrast_slider = QSlider(Qt.Horizontal)
        self._contrast_slider.setRange(-100, 100)
        self._contrast_slider.setValue(0)
        self._contrast_slider.setStyleSheet(slider_style)
        contrast_row.addWidget(contrast_lbl)
        contrast_row.addWidget(self._contrast_slider)
        layout.addLayout(contrast_row)

        layout.addSpacing(8)

        self._progress = QProgressBar()
        self._progress.setStyleSheet(
            "QProgressBar { background: #1a1a2e; border: 1px solid #444; border-radius: 3px; "
            "text-align: center; color: #dcdcdc; font-size: 10px; height: 18px; }"
            "QProgressBar::chunk { background: #ffaa00; border-radius: 2px; }"
        )
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        layout.addStretch()

        self._status = QLabel("IDLE")
        self._status.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self._status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._status.setStyleSheet("color: #888; border: none; padding: 4px;")
        layout.addWidget(self._status)

    def _truncate_path(self, path: str, max_len: int = 32) -> str:
        if len(path) <= max_len:
            return path
        parts = Path(path).parts
        return str(Path(parts[0], "...", *parts[-2:]))

    def _pick_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Directory", self._output_dir)
        if d:
            self._output_dir = d
            self._dir_button.setText(self._truncate_path(d))
            self._dir_button.setToolTip(d)

    @property
    def output_dir(self) -> str:
        return self._output_dir

    def _on_calibrate(self, checked):
        if checked:
            self._record_toggle.setEnabled(False)
        else:
            self._record_toggle.setEnabled(True)
        self.calibrate_toggled.emit(checked)

    def _on_record(self, checked):
        if checked:
            self._calibrate_toggle.setEnabled(False)
        else:
            self._calibrate_toggle.setEnabled(True)
        self.record_toggled.emit(checked)

    def _on_profile_changed(self, index: int):
        if 0 <= index < len(self._profiles):
            profile = self._profiles[index]
            if profile.output_dir:
                self._output_dir = profile.output_dir
                self._dir_button.setText(self._truncate_path(self._output_dir))
                self._dir_button.setToolTip(self._output_dir)
            self.profile_changed.emit(profile)

    @property
    def current_profile(self) -> RigProfile:
        idx = self._profile_combo.currentIndex()
        if 0 <= idx < len(self._profiles):
            return self._profiles[idx]
        return RigProfile()

    @property
    def brightness(self) -> int:
        return self._brightness_slider.value()

    @property
    def contrast(self) -> int:
        return self._contrast_slider.value()

    def get_field_values(self) -> dict:
        return {k: v.text() for k, v in self._fields.items()}

    def set_fields_editable(self, editable: bool):
        for field in self._fields.values():
            field.setReadOnly(not editable)
        self._dir_button.setEnabled(editable)

    def set_status(self, text: str, color: str):
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color}; border: none; padding: 4px;")

    def show_progress(self, current: int, total: int):
        self._progress.setVisible(True)
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._progress.setFormat(f"Encoding {current}/{total}")

    def hide_progress(self):
        self._progress.setVisible(False)
        self._progress.setValue(0)

    def set_toggles_enabled(self, enabled: bool):
        self._calibrate_toggle.setEnabled(enabled)
        self._record_toggle.setEnabled(enabled)

    def reset_toggles(self):
        self._calibrate_toggle.setChecked(False)
        self._record_toggle.setChecked(False)
        self._calibrate_toggle.setEnabled(True)
        self._record_toggle.setEnabled(True)
