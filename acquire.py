"""Multi-camera acquisition helpers for 3dpose using Campy."""
import json
import os
import subprocess
import signal
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import psutil
import serial
import yaml
from imageio_ffmpeg import get_ffmpeg_exe

CAMERA_INIT_WAIT_SEC = 1
MAX_REC_SEC = 3600
FFMPEG = get_ffmpeg_exe()

_active_serial = None


def build_campy_config(base_config_path, video_folder, date, session_id, camera_names, acquisition_type):
    with open(base_config_path) as f:
        cfg = yaml.safe_load(f)

    cfg["videoFolder"] = str(video_folder)
    cfg["recTimeInSec"] = MAX_REC_SEC
    cfg["startArduino"] = False
    cfg["videoFilename"] = [
        f"{date}-{session_id}-{cam}-{acquisition_type}.mp4"
        for cam in camera_names
    ]

    tmp_cfg_path = video_folder / f"_campy_config_{acquisition_type}.yaml"
    with open(tmp_cfg_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)

    return tmp_cfg_path


def _start_campy(config_path):
    proc = subprocess.Popen(
        ["campy-acquire", str(config_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    return proc


def _stop_campy(proc, timeout=30):
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass

    output = ""
    if proc.stdout:
        output = proc.stdout.read()
    return output


def _start_triggers(ser, pins, fps):
    cmd = ",".join(str(x) for x in [len(pins)] + pins + [fps])
    ser.write(cmd.encode())


def _stop_triggers(ser, pins):
    cmd = ",".join(str(x) for x in [len(pins)] + pins + [-1])
    ser.write(cmd.encode())


def _encode_raw(args):
    """Encode a single raw file to H.264 via NVENC. Runs in a worker process."""
    raw_path, mp4_path, w, h, fps, quality = args
    n_bytes = os.path.getsize(raw_path)
    n_frames = n_bytes // (w * h)
    cmd = [
        FFMPEG, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{w}x{h}", "-pix_fmt", "gray",
        "-r", str(fps), "-an",
        "-i", raw_path,
        "-c:v", "h264_nvenc",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-qp", str(quality),
        "-g", str(fps),
        "-bf:v", "0", "-gpu", "0",
        # moov atom to the front — see gui_app/encode_worker.py for why LUC3D needs it.
        "-movflags", "+faststart",
        "-loglevel", "warning",
        mp4_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    os.remove(raw_path)
    cam_name = Path(mp4_path).parent.name
    return f"  {cam_name}: {n_frames} frames -> {Path(mp4_path).name}"


def encode_raw_videos(video_dir, camera_names, config_path):
    """Encode all raw.bin files to MP4 in parallel."""
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    w = cfg["frameWidth"]
    h = cfg["frameHeight"]
    fps = cfg["frameRate"]
    quality = cfg.get("quality", 21)

    tasks = []
    for i, cam in enumerate(camera_names):
        cam_dir = video_dir / cam
        raw_path = str(cam_dir / "raw.bin")
        if not os.path.exists(raw_path):
            continue
        fname = cfg["videoFilename"]
        mp4_name = fname[i] if isinstance(fname, list) else fname
        mp4_path = str(cam_dir / mp4_name)
        tasks.append((raw_path, mp4_path, w, h, fps, quality))

    if not tasks:
        print("No raw files to encode.")
        return

    print(f"Encoding {len(tasks)} videos (NVENC)...")
    with ProcessPoolExecutor(max_workers=len(tasks)) as pool:
        for result in pool.map(_encode_raw, tasks):
            print(result)
    print("Encoding complete.")


def acquire(config_path, serial_port, trigger_pins, frame_rate, label=""):
    """Full acquisition flow: launch campy, Enter to start triggers, Enter to stop."""
    global _active_serial
    cleanup(serial_port)
    proc = _start_campy(config_path)

    for _attempt in range(10):
        try:
            ser = serial.Serial(port=serial_port, baudrate=115200, timeout=0.1)
            _active_serial = ser
            break
        except serial.SerialException:
            time.sleep(1)
    else:
        raise serial.SerialException(f"Could not open {serial_port} after 10 attempts")

    time.sleep(3)
    time.sleep(CAMERA_INIT_WAIT_SEC)

    try:
        input(f">> Press ENTER to START {label}...")
        t_start = time.time()
        _start_triggers(ser, trigger_pins, frame_rate)
        print("Recording...")

        input(f">> Press ENTER to STOP {label}...")
        elapsed = time.time() - t_start
        _stop_triggers(ser, trigger_pins)
        print(f"Stopped ({elapsed:.1f}s, ~{int(elapsed * frame_rate)} frames)")

    finally:
        ser.close()
        _active_serial = None
        output = _stop_campy(proc)
        if "ERROR" in output:
            print("-- campy errors --")
            for line in output.splitlines():
                if "ERROR" in line or "Exception" in line:
                    print(line)
            print("-- end --")


def cleanup(serial_port=None):
    """Kill orphaned campy/ffmpeg processes and close any held serial port."""
    global _active_serial
    if _active_serial is not None:
        try:
            _active_serial.close()
        except Exception:
            pass
        _active_serial = None

    this_pid = os.getpid()
    killed = 0
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["pid"] == this_pid:
            continue
        name = proc.info["name"].lower()
        if "ffmpeg" in name:
            proc.kill()
            killed += 1
        elif "python" in name:
            try:
                if "campy" in " ".join(proc.cmdline()):
                    proc.kill()
                    killed += 1
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

    print(f"Cleanup: killed {killed} orphaned process(es).")


def verify_videos(video_dir, camera_names):
    """Print frame counts and durations for all camera videos."""
    print(f"{'Camera':<8} {'Frames':>8} {'Duration':>10} {'File'}")
    print("-" * 65)
    for cam in camera_names:
        cam_dir = video_dir / cam
        if not cam_dir.exists():
            print(f"{cam:<8} {'MISSING':>8}")
            continue

        ft_path = cam_dir / "frametimes.npy"
        if ft_path.exists():
            ft = np.load(ft_path)
            n_frames = str(ft.shape[1])
            duration = f"{ft[1][-1] - ft[1][0]:.1f}s"
        else:
            n_frames = "?"
            duration = "?"

        mp4s = list(cam_dir.glob("*.mp4"))
        vid_name = mp4s[0].name if mp4s else "NO VIDEO"
        print(f"{cam:<8} {n_frames:>8} {duration:>10} {vid_name}")
