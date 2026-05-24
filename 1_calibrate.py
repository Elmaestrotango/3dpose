# /// script
# requires-python = ">=3.10,<3.12"
# dependencies = [
#     "sleap-anipose>=0.1.8",
#     "numpy<2",
#     "imageio-ffmpeg",
#     "pyyaml>=6.0",
#     "opencv-contrib-python>=4.6",
# ]
# ///
"""Multi-view camera calibration using sleap-anipose.

Run with: uv run 1_calibrate.py <session_dir> --board-config <board.yaml>

The session directory should contain a calibration/ subfolder with per-camera
video subdirectories (cam1/, cam2/, ...) produced by the Panopticon GUI.

Outputs calibration.toml into the calibration directory.

Calibration strategy (3 passes):
  Pass 1 — Subsample each video to max_frames, run sleap-anipose.
  Pass 2 — If pass 1 fails (disconnected camera graph), run a parallel
            pre-scan on the full videos to find frames where the board is
            visible, build subsampled videos from those frames, and retry.
            Cameras with 0 detections are auto-excluded.
  Pass 3 — If pass 2 still fails (isolated cameras despite detections),
            exclude cameras that cannot be paired and retry.
"""
import argparse
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import yaml
import sleap_anipose as slap
from imageio_ffmpeg import get_ffmpeg_exe

FFMPEG = get_ffmpeg_exe()

DEFAULT_MAX_FRAMES = 500
PRESCAN_MAX_DETECTIONS = 400


# ---------------------------------------------------------------------------
# Video helpers
# ---------------------------------------------------------------------------

def subsample_video(src, dst, max_frames):
    """Uniform temporal subsample using ffmpeg fps filter."""
    src, dst = Path(src), Path(dst)
    probe = subprocess.run(
        [FFMPEG, "-i", str(src)], capture_output=True, text=True,
    )
    duration = None
    src_fps = 100.0
    for line in probe.stderr.split("\n"):
        if "Duration:" in line:
            parts = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = parts.split(":")
            duration = float(h) * 3600 + float(m) * 60 + float(s)
        if "fps" in line and "Video:" in line:
            for token in line.split(","):
                token = token.strip()
                if token.endswith("fps"):
                    try:
                        src_fps = float(token.replace("fps", "").strip())
                    except ValueError:
                        pass

    if duration is None or duration <= 0:
        shutil.copy2(src, dst)
        return -1

    target_fps = max_frames / duration
    if target_fps >= src_fps:
        shutil.copy2(src, dst)
        return int(src_fps * duration)

    subprocess.run(
        [FFMPEG, "-y", "-i", str(src),
         "-vf", "fps={:.4f}".format(target_fps),
         "-an", str(dst)],
        capture_output=True,
    )
    return max_frames


def extract_frames_by_index(src, dst, frame_indices):
    """Extract specific frames from a video into a new video."""
    src, dst = Path(src), Path(dst)
    if not frame_indices:
        return 0

    cap = cv2.VideoCapture(str(src))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 100.0

    cmd = [
        FFMPEG, "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24", "-s", "{}x{}".format(w, h),
        "-r", "{:.2f}".format(fps), "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-crf", "23",
        "-loglevel", "error", str(dst),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    sorted_indices = sorted(set(frame_indices))
    idx_set = set(sorted_indices)
    frame_num = 0
    written = 0
    max_idx = sorted_indices[-1]

    while frame_num <= max_idx:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_num in idx_set:
            proc.stdin.write(frame.tobytes())
            written += 1
        frame_num += 1

    proc.stdin.close()
    proc.wait()
    cap.release()
    return written


# ---------------------------------------------------------------------------
# Parallel board pre-scan
# ---------------------------------------------------------------------------

def _prescan_single_camera(args_tuple):
    """Detect ChArUco boards in a single camera's video. Runs in a worker."""
    video_path, board_x, board_y, marker_bits, dict_size = args_tuple

    if marker_bits == 4:
        aruco_dict = cv2.aruco.getPredefinedDictionary(
            getattr(cv2.aruco, "DICT_4X4_{}".format(dict_size),
                    cv2.aruco.DICT_4X4_1000))
    elif marker_bits == 5:
        aruco_dict = cv2.aruco.getPredefinedDictionary(
            getattr(cv2.aruco, "DICT_5X5_{}".format(dict_size),
                    cv2.aruco.DICT_5X5_1000))
    elif marker_bits == 6:
        aruco_dict = cv2.aruco.getPredefinedDictionary(
            getattr(cv2.aruco, "DICT_6X6_{}".format(dict_size),
                    cv2.aruco.DICT_6X6_1000))
    else:
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_1000)

    charuco_board = cv2.aruco.CharucoBoard_create(
        board_x, board_y, 1.0, 0.8, aruco_dict)
    params = cv2.aruco.DetectorParameters_create()

    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    detected_frames = []
    frame_num = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = cv2.aruco.detectMarkers(gray, aruco_dict, parameters=params)
        if ids is not None and len(ids) >= 4:
            detected_frames.append(frame_num)
        frame_num += 1

    cap.release()
    cam_name = Path(video_path).parent.parent.name
    return cam_name, detected_frames, total


def parallel_prescan(cam_dirs, board_params, excluded=()):
    """Detect board frames across all cameras in parallel."""
    tasks = []
    for cam_dir in cam_dirs:
        if cam_dir.name in excluded:
            continue
        mp4s = [f for f in cam_dir.iterdir()
                if f.is_file() and f.suffix == ".mp4" and "calibration" in f.name]
        if not mp4s:
            continue
        tasks.append((
            str(mp4s[0]),
            board_params["board_x"], board_params["board_y"],
            board_params.get("marker_bits", 4),
            board_params.get("dict_size", 1000),
        ))

    n_workers = min(len(tasks), os.cpu_count() or 4)
    results = {}
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        for cam_name, frames, total in pool.map(_prescan_single_camera, tasks):
            results[cam_name] = frames
            print("  {}: {}/{} frames with board".format(
                cam_name, len(frames), total))

    return results


def find_shared_frames(prescan_results, min_cameras=2, max_frames=PRESCAN_MAX_DETECTIONS):
    """Find frames where the board is visible in >= min_cameras simultaneously."""
    all_cams = list(prescan_results.keys())
    if len(all_cams) < min_cameras:
        return {}, set()

    frame_counts = {}
    for cam, frames in prescan_results.items():
        for f in frames:
            if f not in frame_counts:
                frame_counts[f] = set()
            frame_counts[f].add(cam)

    shared = {f: cams for f, cams in frame_counts.items()
              if len(cams) >= min_cameras}

    if not shared:
        return {}, set()

    selected = sorted(shared.keys())
    if len(selected) > max_frames:
        step = len(selected) / max_frames
        selected = [selected[int(i * step)] for i in range(max_frames)]

    selected_set = set(selected)
    per_cam_frames = {}
    for cam in all_cams:
        cam_frames = [f for f in prescan_results[cam] if f in selected_set]
        per_cam_frames[cam] = cam_frames

    zero_cams = {c for c, frames in per_cam_frames.items() if not frames}
    return per_cam_frames, zero_cams


# ---------------------------------------------------------------------------
# Calibration helpers
# ---------------------------------------------------------------------------

def prepare_calibration_videos(cam_dirs, max_frames, use_all_frames=False):
    """Subsample or link calibration videos for each camera."""
    for cam_dir in cam_dirs:
        mp4s = [f for f in cam_dir.iterdir()
                if f.is_file() and f.suffix == ".mp4" and "calibration" in f.name]
        if not mp4s:
            continue
        img_dir = cam_dir / "calibration_images"
        img_dir.mkdir(exist_ok=True)
        dst = img_dir / mp4s[0].name

        if use_all_frames:
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            try:
                dst.symlink_to(mp4s[0].resolve())
            except OSError:
                shutil.copy2(mp4s[0], dst)
            print("  {}: using ALL frames".format(cam_dir.name))
        elif not dst.exists():
            n = subsample_video(mp4s[0], dst, max_frames)
            print("  {}: subsampled to {} frames".format(cam_dir.name, n))
        else:
            print("  {}: using existing subsampled video".format(cam_dir.name))


def prepare_prescan_videos(cam_dirs, per_cam_frames):
    """Extract only board-detected frames into calibration_images/."""
    for cam_dir in cam_dirs:
        cam = cam_dir.name
        if cam not in per_cam_frames or not per_cam_frames[cam]:
            continue
        mp4s = [f for f in cam_dir.iterdir()
                if f.is_file() and f.suffix == ".mp4" and "calibration" in f.name]
        if not mp4s:
            continue
        img_dir = cam_dir / "calibration_images"
        img_dir.mkdir(exist_ok=True)
        dst = img_dir / mp4s[0].name
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        n = extract_frames_by_index(mp4s[0], dst, per_cam_frames[cam])
        print("  {}: extracted {} board frames".format(cam, n))


def run_calibration(calib_dir, board_toml, excluded, calib_fname,
                    metadata_fname, histogram_path, reproj_path):
    """Run sleap-anipose calibration. Returns (cgroup, metadata) or raises."""
    try:
        result = slap.calibrate(
            session=str(calib_dir),
            board=str(board_toml),
            excluded_views=excluded,
            calib_fname=str(calib_fname),
            metadata_fname=str(metadata_fname),
            histogram_path=str(histogram_path),
            reproj_path=str(reproj_path),
        )
    except (ValueError, TypeError) as e:
        if Path(calib_fname).exists():
            print("  (metadata generation failed: {}, but calibration.toml "
                  "was written successfully)".format(e))
            return True, None
        raise
    if not result or (hasattr(result, '__len__') and len(result) < 2):
        raise RuntimeError("Calibration returned empty result")
    if isinstance(result, tuple):
        return result[0], result[1]
    return result, None


def is_graph_error(error_msg):
    """Check if the error is a disconnected camera graph problem."""
    msg = str(error_msg).lower()
    return any(s in msg for s in (
        "calibration graph", "could not build", "could not be paired",
        "group numbers", "not enough", "unpack",
    ))


def _fatal_calibration_error(error_msg):
    """Print a calibration error and exit."""
    msg = error_msg.lower()
    if "singular" in msg or "linalg" in msg:
        print("\nERROR: Calibration solve failed (singular matrix).",
              file=sys.stderr)
        print("One or more cameras may have too few detections or "
              "insufficient viewing angles.", file=sys.stderr)
    else:
        print("\nERROR: Calibration failed: {}".format(error_msg),
              file=sys.stderr)
    sys.exit(1)


def check_calibration_quality(calib_dir, metadata):
    """Check calibration results and return warnings."""
    warnings = []
    try:
        if not hasattr(metadata, "attrs") and not hasattr(metadata, "keys"):
            return warnings
    except Exception:
        return warnings

    cam_dirs = sorted(
        d for d in calib_dir.iterdir()
        if d.is_dir() and d.name.startswith("cam")
    )
    try:
        import h5py
        metadata_path = calib_dir / "calibration_metadata.h5"
        if metadata_path.exists():
            with h5py.File(metadata_path, "r") as f:
                for cam_dir in cam_dirs:
                    cam = cam_dir.name
                    if cam in f:
                        detections = f[cam]
                        n_detected = sum(
                            1 for key in detections
                            if np.any(np.isfinite(detections[key][()]))
                        ) if len(detections) > 0 else 0
                        if n_detected == 0:
                            warnings.append(
                                "{}: 0 board detections".format(cam))
                        elif n_detected < 10:
                            warnings.append(
                                "{}: only {} board detections (10+ recommended)".format(
                                    cam, n_detected))
    except Exception:
        pass
    return warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Run multi-view calibration on a session's calibration videos.",
    )
    parser.add_argument(
        "session_dir", type=Path,
        help="Path to session directory (contains calibration/ with cam1/, cam2/, ...)",
    )
    parser.add_argument(
        "--board-config", type=Path, required=True,
        help="Path to board YAML (e.g. configs/boards/charuco_8x8_15mm.yaml)",
    )
    parser.add_argument(
        "--excluded-views", nargs="*", default=[],
        help="Camera names to exclude from calibration (e.g. cam5 cam6)",
    )
    parser.add_argument(
        "--max-frames", type=int, default=None,
        help="Max frames per camera for calibration (default: from board config or {})".format(
            DEFAULT_MAX_FRAMES),
    )
    parser.add_argument(
        "--no-fallback", action="store_true",
        help="Disable automatic fallback on graph failure",
    )
    args = parser.parse_args()

    with open(args.board_config) as f:
        board = yaml.safe_load(f)
    board_x = board["board_x"]
    board_y = board["board_y"]
    square_length = board["square_length"]
    marker_length = board["marker_length"]
    marker_bits = board.get("marker_bits", 4)
    dict_size = board.get("dict_size", 1000)
    max_frames = args.max_frames or board.get("max_frames", DEFAULT_MAX_FRAMES)
    print("Board config: {}".format(args.board_config))
    print("  {}x{}, square={}mm, marker={}mm".format(
        board_x, board_y, square_length, marker_length))

    calib_dir = args.session_dir / "calibration"
    if not calib_dir.exists():
        raise FileNotFoundError(
            "Calibration directory not found: {}".format(calib_dir))

    cam_dirs = sorted(
        d for d in calib_dir.iterdir()
        if d.is_dir() and d.name.startswith("cam")
    )
    if not cam_dirs:
        print("ERROR: No camera directories (cam1/, cam2/, ...) found.",
              file=sys.stderr)
        sys.exit(1)

    board_toml = calib_dir / "board.toml"
    board_img = calib_dir / "board.jpg"
    slap.draw_board(
        board_name=str(board_img),
        board_x=board_x, board_y=board_y,
        square_length=square_length, marker_length=marker_length,
        marker_bits=marker_bits, dict_size=dict_size,
        img_width=1440, img_height=1440,
        save=str(board_toml),
    )
    print("Board TOML: {}".format(board_toml))

    non_cam_dirs = [d.name for d in calib_dir.iterdir()
                    if d.is_dir() and not d.name.startswith("cam")]
    excluded = tuple(list(args.excluded_views) + non_cam_dirs)

    calib_fname = calib_dir / "calibration.toml"
    metadata_fname = calib_dir / "calibration_metadata.h5"
    histogram_path = calib_dir / "reprojection_error_histogram.png"
    reproj_path = calib_dir / "reprojections"

    # --- Pass 1: subsampled videos ---
    print("\n--- Pass 1: subsampled ({} frames) ---".format(max_frames))
    prepare_calibration_videos(cam_dirs, max_frames)

    cgroup = None
    metadata = None
    try:
        cgroup, metadata = run_calibration(
            calib_dir, board_toml, excluded,
            calib_fname, metadata_fname, histogram_path, reproj_path)
    except Exception as e:
        error_msg = str(e)
        if not (is_graph_error(error_msg) and not args.no_fallback):
            _fatal_calibration_error(error_msg)

        # --- Pass 2: parallel pre-scan + smart subsample ---
        print("\nSubsampled calibration failed: {}".format(error_msg))
        print("\n--- Pass 2: parallel board pre-scan on full videos ---")

        board_params = {
            "board_x": board_x, "board_y": board_y,
            "marker_bits": marker_bits, "dict_size": dict_size,
        }
        prescan = parallel_prescan(cam_dirs, board_params, excluded)

        per_cam_frames, zero_cams = find_shared_frames(
            prescan, min_cameras=2, max_frames=PRESCAN_MAX_DETECTIONS)

        if zero_cams:
            print("\n  Cameras with 0 shared detections: {}".format(
                ", ".join(sorted(zero_cams))))

        cams_with_frames = {c for c, f in per_cam_frames.items() if f}
        if len(cams_with_frames) < 2:
            print("\nERROR: Fewer than 2 cameras share board-visible frames.",
                  file=sys.stderr)
            sys.exit(1)

        auto_excluded = tuple(list(excluded) + sorted(zero_cams))
        total_shared = sum(len(f) for f in per_cam_frames.values())
        print("\n  Extracting {} shared board frames across {} cameras".format(
            total_shared, len(cams_with_frames)))

        prepare_prescan_videos(cam_dirs, per_cam_frames)

        try:
            cgroup, metadata = run_calibration(
                calib_dir, board_toml, auto_excluded,
                calib_fname, metadata_fname, histogram_path, reproj_path)
        except Exception as e2:
            error_msg2 = str(e2)
            if not is_graph_error(error_msg2):
                _fatal_calibration_error(error_msg2)

            # --- Pass 3: exclude isolated cameras ---
            print("\nPass 2 calibration failed: {}".format(error_msg2))
            remaining = [d.name for d in cam_dirs
                         if d.name not in auto_excluded
                         and d.name in cams_with_frames]
            if len(remaining) < 2:
                print("\nERROR: Too few cameras remain for calibration.",
                      file=sys.stderr)
                sys.exit(1)

            print("\n--- Pass 3: retrying with {} cameras ---".format(
                len(remaining)))
            print("  Cameras: {}".format(", ".join(sorted(remaining))))
            all_excluded = tuple(
                d.name for d in cam_dirs if d.name not in remaining
            ) + tuple(non_cam_dirs)
            try:
                cgroup, metadata = run_calibration(
                    calib_dir, board_toml, all_excluded,
                    calib_fname, metadata_fname, histogram_path, reproj_path)
            except Exception as e3:
                print("\nERROR: Calibration failed: {}".format(e3),
                      file=sys.stderr)
                sys.exit(1)

    warnings = check_calibration_quality(calib_dir, metadata)

    print("\nCalibration complete.")
    print("  calibration.toml:  {}".format(calib_fname))
    print("  metadata:          {}".format(metadata_fname))
    print("  histogram:         {}".format(histogram_path))
    print("  reprojections:     {}".format(reproj_path))

    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print("  - {}".format(w))
        print("\nCalibration completed with warnings. Review the reprojection "
              "error histogram.", file=sys.stderr)


if __name__ == "__main__":
    main()
