# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy",
#     "opencv-python-headless>=4.6",
#     "imageio-ffmpeg",
# ]
# ///
"""Align multi-camera recordings by GigE block ID (trigger ordinal).

At 6x100 fps over GigE the host/network occasionally drops a frame, which
breaks the assumption that frame *i* is the same trigger across cameras: after
the first dropped frame every later frame is off by one (or more), so naive
frame-by-frame use silently compares different moments in time.

Each camera's ``blockids.npy`` records the GigE Vision block ID (= trigger
ordinal) of every recorded frame, written by the acquisition GUI. The block IDs
common to ALL cameras are exactly the triggers every camera captured, and the
hardware trigger fires all cameras simultaneously — so selecting those frames
gives a synchronized, equal-length set.

Usage:
  uv run 2_align.py <recording_dir> [--extract] [--quality 18] [--dry-run]

``<recording_dir>`` holds cam1/, cam2/, ... each with ``blockids.npy`` and the
camera's recording ``.mp4``. Writes into ``<recording_dir>/aligned/``:
  alignment.npz    common_block_ids (M,) + frame_index (n_cams, M) + cam names
  alignment.json   human-readable manifest (counts, drops, common span)
  cam*/<name>.mp4  only with --extract: trimmed, frame-for-frame aligned videos

The .npz alone is lossless and cheap: frame_index[c, k] is the frame number in
camera c's original video for the k-th synchronized sample. Downstream can seek
those frames directly instead of re-encoding. --extract is for tools that want
ready-made aligned videos.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np


def _camera_dirs(rec_dir: Path) -> list[Path]:
    return sorted(d for d in rec_dir.iterdir()
                  if d.is_dir() and d.name.startswith("cam"))


def _video_for(cam_dir: Path) -> Path | None:
    mp4s = [f for f in cam_dir.iterdir()
            if f.suffix == ".mp4" and "recording" in f.name]
    if not mp4s:
        mp4s = [f for f in cam_dir.iterdir() if f.suffix == ".mp4"]
    return mp4s[0] if mp4s else None


def load_blockids(rec_dir: Path):
    """Return (cam_names, [blockids arrays], [video paths]). Raises on missing."""
    cam_dirs = _camera_dirs(rec_dir)
    if not cam_dirs:
        raise FileNotFoundError(f"No cam*/ directories in {rec_dir}")
    names, blocks, videos = [], [], []
    for cd in cam_dirs:
        bpath = cd / "blockids.npy"
        if not bpath.exists():
            raise FileNotFoundError(
                f"{bpath} missing — this recording predates block-ID logging "
                "and cannot be block-ID aligned.")
        b = np.load(bpath).astype(np.int64)
        # Block IDs are a monotonically increasing trigger ordinal; guard the
        # assumption searchsorted relies on.
        if b.ndim != 1 or np.any(np.diff(b) <= 0):
            raise ValueError(f"{bpath}: block IDs not strictly increasing")
        names.append(cd.name)
        blocks.append(b)
        videos.append(_video_for(cd))
    return names, blocks, videos


def compute_alignment(blocks: list[np.ndarray]):
    """Common block IDs across all cameras + each camera's frame indices.

    frame_index[c, k] = position in camera c's video of common_ids[k].
    """
    common = blocks[0]
    for b in blocks[1:]:
        common = np.intersect1d(common, b, assume_unique=True)
    frame_index = np.empty((len(blocks), common.size), dtype=np.int64)
    for c, b in enumerate(blocks):
        pos = np.searchsorted(b, common)
        # searchsorted lands exactly because common ⊆ b; verify defensively.
        if np.any(b[pos] != common):
            raise RuntimeError(f"camera {c}: block-ID index mismatch")
        frame_index[c] = pos
    return common, frame_index


def _ffmpeg_exe() -> str:
    from imageio_ffmpeg import get_ffmpeg_exe
    return get_ffmpeg_exe()


def extract_aligned(video: Path, frame_idx: np.ndarray, dst: Path,
                    fps: int, quality: int):
    """Re-encode only the selected frame indices, in order, into dst."""
    import cv2
    keep = set(int(i) for i in frame_idx)
    cap = cv2.VideoCapture(str(video))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    last = int(frame_idx[-1])
    cmd = [
        _ffmpeg_exe(), "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
        "-c:v", "h264_nvenc", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-qp", str(quality), "-bf:v", "0",
        "-loglevel", "error", str(dst),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = 0
    written = 0
    while n <= last:
        ok, frame = cap.read()
        if not ok:
            break
        if n in keep:
            if frame.ndim == 3:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            proc.stdin.write(np.ascontiguousarray(frame).tobytes())
            written += 1
        n += 1
    proc.stdin.close()
    proc.wait()
    cap.release()
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording_dir", type=Path)
    ap.add_argument("--extract", action="store_true",
                    help="also write trimmed, frame-aligned mp4s (re-encodes)")
    ap.add_argument("--quality", type=int, default=18,
                    help="NVENC QP for --extract (default 18)")
    ap.add_argument("--fps", type=int, default=100,
                    help="frame rate stamped on extracted videos (default 100)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing")
    args = ap.parse_args()

    rec = args.recording_dir
    names, blocks, videos = load_blockids(rec)
    common, frame_index = compute_alignment(blocks)

    spans = [int(b[-1] - b[0] + 1) for b in blocks]
    full_span = int(max(b[-1] for b in blocks) - min(b[0] for b in blocks) + 1)
    print(f"Recording: {rec}")
    print(f"Cameras:   {', '.join(names)}")
    print(f"Trigger span (union): {full_span}")
    print(f"Common (aligned) frames: {common.size}\n")
    print(f"{'cam':6} {'recorded':>9} {'dropped':>8} {'%drop':>7}")
    report = {}
    for nm, b in zip(names, blocks):
        dropped = full_span - b.size
        print(f"{nm:6} {b.size:>9} {dropped:>8} {100*dropped/full_span:>6.2f}%")
        report[nm] = dict(recorded=int(b.size), dropped=int(dropped),
                          first=int(b[0]), last=int(b[-1]))
    total_drop = full_span - common.size
    print(f"\nAligned set drops {total_drop} of {full_span} triggers "
          f"({100*total_drop/full_span:.2f}%) — frames any camera missed.")

    if args.dry_run:
        return

    out = rec / "aligned"
    out.mkdir(exist_ok=True)
    np.savez(out / "alignment.npz",
             common_block_ids=common, frame_index=frame_index,
             camera_names=np.array(names))
    manifest = dict(
        recording=str(rec), camera_names=names,
        trigger_span=full_span, common_frames=int(common.size),
        common_first=int(common[0]), common_last=int(common[-1]),
        per_camera=report,
        note="frame_index[c, k] = frame number in camera c's video for the "
             "k-th synchronized sample (common_block_ids[k]).",
    )
    with open(out / "alignment.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nWrote {out/'alignment.npz'} and alignment.json")

    if args.extract:
        print("\nExtracting aligned videos (re-encode)...")
        for c, (nm, vid) in enumerate(zip(names, videos)):
            if vid is None:
                print(f"  {nm}: no video, skipped")
                continue
            cam_out = out / nm
            cam_out.mkdir(exist_ok=True)
            dst = cam_out / vid.name
            w = extract_aligned(vid, frame_index[c], dst, args.fps, args.quality)
            tag = "OK" if w == common.size else f"WARNING wrote {w}/{common.size}"
            print(f"  {nm}: {w} frames -> {dst.name} [{tag}]")


if __name__ == "__main__":
    main()
