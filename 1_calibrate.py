# /// script
# requires-python = ">=3.10,<3.12"
# dependencies = [
#     "sleap-anipose>=0.1.8",
#     "numpy<2",
#     "imageio-ffmpeg",
#     "pyyaml>=6.0",
# ]
# ///
"""Multi-view camera calibration using sleap-anipose.

Run with: uv run 1_calibrate.py <session_dir> --board-config <board.yaml>

The session directory should contain a calibration/ subfolder with per-camera
video subdirectories (cam1/, cam2/, ...) produced by the Panopticon GUI.

Outputs calibration.toml into the calibration directory.
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml
import sleap_anipose as slap
from imageio_ffmpeg import get_ffmpeg_exe

FFMPEG = get_ffmpeg_exe()

DEFAULT_MAX_FRAMES = 200


def subsample_video(src: Path, dst: Path, max_frames: int):
    """Create a subsampled copy of a video using ffmpeg."""
    probe = subprocess.run(
        [FFMPEG, "-i", str(src), "-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    lines = probe.stderr.split("\n")
    n_frames = 0
    for line in lines:
        if "frame=" in line:
            parts = line.split("frame=")[-1].strip().split()
            if parts:
                try:
                    n_frames = int(parts[0])
                except ValueError:
                    pass

    if n_frames <= max_frames:
        import shutil
        shutil.copy2(src, dst)
        return n_frames

    step = n_frames / max_frames
    subprocess.run(
        [FFMPEG, "-y", "-i", str(src),
         "-vf", f"select=not(mod(n\\,{int(step)}))",
         "-vsync", "vfr", "-an",
         str(dst)],
        capture_output=True,
    )
    return max_frames


def check_calibration_quality(calib_dir: Path, metadata) -> list[str]:
    """Check calibration results and return warnings."""
    warnings = []

    try:
        if hasattr(metadata, "attrs"):
            attrs = metadata.attrs
        elif hasattr(metadata, "keys"):
            attrs = metadata
        else:
            return warnings
    except Exception:
        return warnings

    cam_dirs = sorted(
        d for d in calib_dir.iterdir()
        if d.is_dir() and d.name.startswith("cam")
    )

    # Check per-camera detection counts from metadata HDF5
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
                                f"{cam}: 0 board detections — was the ChArUco board visible to this camera?"
                            )
                        elif n_detected < 10:
                            warnings.append(
                                f"{cam}: only {n_detected} board detections (10+ recommended for reliable calibration)"
                            )
    except Exception:
        pass

    return warnings


def main():
    parser = argparse.ArgumentParser(
        description="Run multi-view calibration on a session's calibration videos.",
    )
    parser.add_argument(
        "session_dir",
        type=Path,
        help="Path to session directory (contains calibration/ with cam1/, cam2/, ...)",
    )
    parser.add_argument(
        "--board-config",
        type=Path,
        required=True,
        help="Path to board YAML (e.g. configs/boards/charuco_5x5_05mm.yaml)",
    )
    parser.add_argument(
        "--excluded-views",
        nargs="*",
        default=[],
        help="Camera names to exclude from calibration (e.g. cam5 cam6)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help=f"Max frames per camera for calibration (default: from board config or {DEFAULT_MAX_FRAMES})",
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
    print(f"Board config: {args.board_config}")
    print(f"  {board_x}x{board_y}, square={square_length}mm, marker={marker_length}mm")

    calib_dir = args.session_dir / "calibration"
    if not calib_dir.exists():
        raise FileNotFoundError(f"Calibration directory not found: {calib_dir}")

    cam_dirs = sorted(
        d for d in calib_dir.iterdir()
        if d.is_dir() and d.name.startswith("cam")
    )
    if not cam_dirs:
        print("ERROR: No camera directories (cam1/, cam2/, ...) found in calibration/", file=sys.stderr)
        sys.exit(1)

    board_toml = calib_dir / "board.toml"
    board_img = calib_dir / "board.jpg"
    slap.draw_board(
        board_name=str(board_img),
        board_x=board_x,
        board_y=board_y,
        square_length=square_length,
        marker_length=marker_length,
        marker_bits=marker_bits,
        dict_size=dict_size,
        img_width=1440,
        img_height=1440,
        save=str(board_toml),
    )
    print(f"Board TOML: {board_toml}")

    for cam_dir in cam_dirs:
        mp4s = [f for f in cam_dir.iterdir() if f.is_file() and f.suffix == ".mp4" and "calibration" in f.name]
        if not mp4s:
            continue
        img_dir = cam_dir / "calibration_images"
        img_dir.mkdir(exist_ok=True)
        dst = img_dir / mp4s[0].name
        if not dst.exists():
            n = subsample_video(mp4s[0], dst, max_frames)
            print(f"  {cam_dir.name}: subsampled to {n} frames")
        else:
            print(f"  {cam_dir.name}: using existing subsampled video")

    calib_fname = calib_dir / "calibration.toml"
    metadata_fname = calib_dir / "calibration_metadata.h5"
    histogram_path = calib_dir / "reprojection_error_histogram.png"
    reproj_path = calib_dir / "reprojections"

    non_cam_dirs = [d.name for d in calib_dir.iterdir() if d.is_dir() and not d.name.startswith("cam")]
    excluded = tuple(list(args.excluded_views) + non_cam_dirs)

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
        if not result or len(result) < 2:
            print(f"\nERROR: No ChArUco board detections found in any camera.", file=sys.stderr)
            print(f"Make sure the ChArUco board was clearly visible to all cameras during calibration recording.", file=sys.stderr)
            print(f"Board parameters: {board_x}x{board_y}, square={square_length}mm, marker={marker_length}mm", file=sys.stderr)
            sys.exit(1)
        cgroup, metadata = result
    except (ValueError, TypeError) as e:
        if "unpack" in str(e).lower() or "not enough" in str(e).lower():
            print(f"\nERROR: No ChArUco board detections found in any camera.", file=sys.stderr)
            print(f"Make sure the ChArUco board was clearly visible to all cameras during calibration recording.", file=sys.stderr)
            print(f"Board parameters: {board_x}x{board_y}, square={square_length}mm, marker={marker_length}mm", file=sys.stderr)
        else:
            print(f"\nERROR: Calibration failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        error_msg = str(e)
        if "singular" in error_msg.lower() or "linalg" in error_msg.lower():
            print(f"\nERROR: Calibration solve failed (singular matrix).", file=sys.stderr)
            print(f"One or more cameras may have too few detections or insufficient viewing angles.", file=sys.stderr)
        else:
            print(f"\nERROR: Calibration failed: {error_msg}", file=sys.stderr)
        sys.exit(1)

    warnings = check_calibration_quality(calib_dir, metadata)

    print(f"\nCalibration complete.")
    print(f"  calibration.toml:  {calib_fname}")
    print(f"  metadata:          {metadata_fname}")
    print(f"  histogram:         {histogram_path}")
    print(f"  reprojections:     {reproj_path}")

    if warnings:
        print(f"\nWARNINGS:")
        for w in warnings:
            print(f"  - {w}")
        print(f"\nCalibration completed with warnings. Review the reprojection error histogram.", file=sys.stderr)


if __name__ == "__main__":
    main()
