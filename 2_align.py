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
the first dropped frame every later frame is off by one or more, so naive
frame-by-frame use silently compares different moments in time.

Each camera's ``blockids.npy`` records the GigE block ID (trigger ordinal) of
every recorded frame. The block IDs common to ALL cameras are the triggers
every camera captured, and the hardware trigger fires all cameras at once — so
those frames are a synchronized, equal-length set.

Usage:
  uv run 2_align.py <recording_dir>            # write aligned/ index only
  uv run 2_align.py <recording_dir> --replace  # also trim+replace the mp4s
  uv run 2_align.py <recording_dir> --dry-run   # report only

``<recording_dir>`` holds cam1/, cam2/, ... each with ``blockids.npy`` and the
camera's recording ``.mp4``. Always writes ``aligned/alignment.{npz,json}``
(the lossless index: frame_index[c, k] = frame number in camera c's video for
the k-th synchronized sample). With --replace, each camera's mp4 is re-encoded
to only the common frames and atomically replaces the original, and that
camera's blockids.npy + frametimes.npy are rewritten to match.

The GUI runs this automatically after a realtime recording; this CLI is for
reprocessing existing sessions.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gui_app import alignment


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording_dir", type=Path)
    ap.add_argument("--replace", action="store_true",
                    help="trim+replace the per-camera mp4s (re-encodes)")
    ap.add_argument("--quality", type=int, default=21,
                    help="NVENC QP for --replace re-encode (default 21)")
    ap.add_argument("--fps", type=int, default=100,
                    help="frame rate stamped on re-encoded videos (default 100)")
    ap.add_argument("--parallel", type=int, default=3,
                    help="cameras re-encoded concurrently with --replace")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing")
    args = ap.parse_args()

    names, blocks, videos = alignment.load_blockids(args.recording_dir)
    common, _ = alignment.compute_alignment(blocks)
    full_span = int(max(int(b[-1]) for b in blocks)
                    - min(int(b[0]) for b in blocks) + 1)

    print(f"Recording: {args.recording_dir}")
    print(f"Cameras:   {', '.join(names)}")
    print(f"Trigger span (union): {full_span}")
    print(f"Common (aligned) frames: {common.size}\n")
    print(f"{'cam':6} {'recorded':>9} {'dropped':>8} {'%drop':>7}")
    for nm, b in zip(names, blocks):
        dropped = full_span - b.size
        print(f"{nm:6} {b.size:>9} {dropped:>8} {100*dropped/full_span:>6.2f}%")
    total_drop = full_span - common.size
    print(f"\nAligned set keeps {common.size} of {full_span} triggers; drops "
          f"{total_drop} ({100*total_drop/full_span:.2f}%) any camera missed.")

    if args.dry_run:
        return

    def _progress(done, total, msg):
        print(f"  [{done}/{total}] {msg}")

    print("\nAligning..." + (" (re-encode + replace)" if args.replace else
                             " (index only)"))
    summary = alignment.align_recording(
        args.recording_dir, fps=args.fps, quality=args.quality,
        replace=args.replace, parallel=args.parallel, progress=_progress)
    if args.replace and summary["needed"]:
        if summary["replaced"]:
            print(f"\nReplaced all videos with {summary['common_frames']}-frame "
                  "aligned versions.")
        else:
            print("\nWARNING: some cameras were not replaced:", file=sys.stderr)
            for w in summary["warnings"]:
                print(f"  - {w}", file=sys.stderr)
    elif not summary["needed"]:
        print("\nNo loss — videos already aligned, nothing re-encoded.")
    else:
        print(f"\nWrote aligned/ index ({summary['common_frames']} common "
              "frames). Re-run with --replace to trim the videos.")


if __name__ == "__main__":
    main()
