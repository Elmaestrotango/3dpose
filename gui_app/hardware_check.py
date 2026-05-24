"""Startup hardware screening — warns about insufficient resources."""
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import psutil
from imageio_ffmpeg import get_ffmpeg_exe
from PyQt5.QtCore import QThread, pyqtSignal


@dataclass
class HardwareReport:
    cpu_cores: int = 0
    cpu_threads: int = 0
    ram_total_gb: float = 0.0
    ram_available_gb: float = 0.0
    disk_free_gb: float = 0.0
    disk_write_mb_s: float = -1.0
    has_nvenc: bool = False
    warnings: list = field(default_factory=list)


def check_nvenc() -> bool:
    try:
        ffmpeg = get_ffmpeg_exe()
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        return "h264_nvenc" in result.stdout
    except Exception:
        return False


def estimate_disk_speed(target_dir: Path, size_mb: int = 16) -> float:
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return -1.0
    test_file = target_dir / ".panopticon_speed_test"
    data = np.random.bytes(size_mb * 1024 * 1024)
    try:
        t0 = time.perf_counter()
        with open(test_file, "wb") as f:
            f.write(data)
            f.flush()
        elapsed = time.perf_counter() - t0
        return size_mb / elapsed if elapsed > 0 else -1.0
    except OSError:
        return -1.0
    finally:
        try:
            test_file.unlink(missing_ok=True)
        except OSError:
            pass


def _get_disk_free(target: Path) -> float:
    for p in [target] + list(target.parents):
        try:
            return shutil.disk_usage(p).free / (1024 ** 3)
        except OSError:
            continue
    return -1.0


def run_hardware_check(output_dir: str = "") -> HardwareReport:
    report = HardwareReport()

    report.cpu_cores = psutil.cpu_count(logical=False) or 1
    report.cpu_threads = psutil.cpu_count(logical=True) or 1

    mem = psutil.virtual_memory()
    report.ram_total_gb = mem.total / (1024 ** 3)
    report.ram_available_gb = mem.available / (1024 ** 3)

    target = Path(output_dir) if output_dir else Path(".")
    report.disk_free_gb = _get_disk_free(target)
    report.disk_write_mb_s = estimate_disk_speed(target)
    report.has_nvenc = check_nvenc()

    if report.cpu_cores < 4:
        report.warnings.append(
            f"CPU: {report.cpu_cores} cores detected (4+ recommended for multi-camera capture)"
        )
    if report.ram_total_gb < 16:
        report.warnings.append(
            f"RAM: {report.ram_total_gb:.0f} GB total (16 GB+ recommended)"
        )
    if report.disk_free_gb >= 0 and report.disk_free_gb < 500:
        report.warnings.append(
            f"Disk: {report.disk_free_gb:.0f} GB free (500 GB+ recommended for raw capture)"
        )
    if report.disk_write_mb_s >= 0 and report.disk_write_mb_s < 500:
        report.warnings.append(
            f"Disk write speed: {report.disk_write_mb_s:.0f} MB/s (NVMe SSD with 1000+ MB/s recommended)"
        )
    if not report.has_nvenc:
        report.warnings.append(
            "NVENC not available — encoding will fall back to CPU (much slower)"
        )

    return report


def format_report(report: HardwareReport) -> str:
    lines = [
        "Hardware Check Results",
        "=" * 40,
        f"CPU:   {report.cpu_cores} cores / {report.cpu_threads} threads",
        f"RAM:   {report.ram_total_gb:.1f} GB total, {report.ram_available_gb:.1f} GB available",
    ]
    if report.disk_free_gb >= 0:
        lines.append(f"Disk:  {report.disk_free_gb:.0f} GB free")
    if report.disk_write_mb_s >= 0:
        lines[-1] += f", {report.disk_write_mb_s:.0f} MB/s write"
    lines.append(f"NVENC: {'Available' if report.has_nvenc else 'Not found'}")
    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in report.warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)


class HardwareCheckThread(QThread):
    finished = pyqtSignal(object)

    def __init__(self, output_dir: str = ""):
        super().__init__()
        self._output_dir = output_dir

    def run(self):
        report = run_hardware_check(self._output_dir)
        self.finished.emit(report)
