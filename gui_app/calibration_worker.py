"""Background worker for running sleap-anipose calibration via uv run."""
import subprocess
import shutil
import sys
from pathlib import Path
from PyQt5.QtCore import QThread, pyqtSignal


class CalibrationWorker(QThread):
    status = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, session_dir: Path, script_path: Path, board_config: str = ""):
        super().__init__()
        self._session_dir = session_dir
        self._script_path = script_path
        self._board_config = board_config

    def run(self):
        uv = shutil.which("uv")
        if not uv:
            self.finished.emit(False, "uv not found on PATH")
            return

        if not self._script_path.exists():
            self.finished.emit(False, f"Script not found: {self._script_path}")
            return

        self.status.emit("Running calibration...")
        cmd = [uv, "run", str(self._script_path), str(self._session_dir)]
        if self._board_config and Path(self._board_config).exists():
            cmd.extend(["--board-config", self._board_config])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            if stdout:
                print(stdout, flush=True)

            if result.returncode == 0:
                self.status.emit("Calibration complete")
                msg = ""
                if stderr:
                    msg = stderr
                self.finished.emit(True, msg)
            else:
                error_msg = _parse_calibration_error(stdout, stderr, result.returncode)
                self.finished.emit(False, error_msg)
        except subprocess.TimeoutExpired:
            self.finished.emit(False, "Calibration timed out (10 min)")
        except Exception as e:
            self.finished.emit(False, str(e))


def _parse_calibration_error(stdout: str, stderr: str, returncode: int) -> str:
    combined = f"{stdout}\n{stderr}".lower()

    if any(s in combined for s in ("no charuco", "no points", "no corners", "0 board detections", "not enough values to unpack")):
        return (
            "No ChArUco board detections found.\n\n"
            "Make sure the ChArUco board was clearly visible to all cameras "
            "during the calibration recording, and that the board parameters "
            "in your board config YAML match the physical board."
        )

    if "no module named" in combined:
        module = ""
        for line in stderr.split("\n"):
            if "no module named" in line.lower():
                module = line.strip()
                break
        return f"Missing dependency:\n{module}\n\nTry: uv cache clean && uv run 1_calibrate.py"

    if "singular matrix" in combined or "linalg" in combined:
        return (
            "Calibration solve failed (singular matrix).\n\n"
            "This usually means one or more cameras had too few board detections, "
            "or the board was only visible from a single angle. Try recording "
            "calibration videos with more board orientations."
        )

    # Fall back to last meaningful lines from stderr
    error_lines = [
        line for line in stderr.split("\n")
        if line.strip() and not line.startswith("  ")
    ]
    if error_lines:
        tail = "\n".join(error_lines[-5:])
        return f"Calibration failed (exit code {returncode}):\n\n{tail}"

    return f"Calibration failed (exit code {returncode})"
