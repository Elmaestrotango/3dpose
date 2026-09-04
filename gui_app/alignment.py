"""Block-ID alignment core, shared by the GUI (align_worker) and 2_align.py.

At 6x100 fps over GigE the host/network occasionally drops a frame, so frame
*i* is not the same trigger across cameras. Each camera's ``blockids.npy``
records the GigE block ID (trigger ordinal) of every recorded frame; the block
IDs common to ALL cameras are the triggers every camera captured, and the
hardware trigger fires all cameras simultaneously — so those frames are a
synchronized, equal-length set.

Imports are limited to numpy + (lazily) cv2 / imageio-ffmpeg so this module is
importable both in the GUI venv and under ``uv run 2_align.py`` (isolated env).
"""
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

# Stdlib-only module (collections.deque), so it does not widen what this file
# needs — 2_align.py runs in an isolated env with numpy/cv2/imageio-ffmpeg.
from gui_app.frame_sync import block_rate_warnings as _block_rate_warnings

# GigE Vision 16-bit block IDs cycle through 1..65535 (0 is reserved "no block
# id"), so they wrap 65535 -> 1 every 65535 triggers unless extended 64-bit IDs
# are enabled. camera_manager tries to enable the 64-bit mode; this is the
# software safety net that unwraps a stream that wrapped anyway (and recovers
# recordings made before the 64-bit mode was set).
BLOCKID_WRAP = 65535


def _unwrap_blockids(b: np.ndarray, period: int = BLOCKID_WRAP) -> np.ndarray:
    """Undo 16-bit block-ID wrap-around so IDs are globally monotonic.

    All cameras are hardware-triggered together and start at block ID 1, so they
    wrap at the same trigger; unwrapping each stream independently yields trigger
    ordinals that stay consistent across cameras. A wrap is a large negative step
    (~ -(period-1)); a small decrease means genuinely corrupt/reordered data.
    """
    b = b.astype(np.int64)
    if b.size < 2:
        return b
    d = np.diff(b)
    wrap_at = d < -(period // 2)
    if wrap_at.any():
        offsets = np.concatenate([[0], np.cumsum(wrap_at.astype(np.int64))]) * period
        b = b + offsets
    if np.any(np.diff(b) <= 0):
        raise ValueError("block IDs not monotonic even after wrap-unwrap "
                         "(corrupt or reordered)")
    return b


def camera_dirs(rec_dir: Path) -> list[Path]:
    return sorted(d for d in Path(rec_dir).iterdir()
                  if d.is_dir() and d.name.startswith("cam"))


def video_for(cam_dir: Path):
    mp4s = [f for f in cam_dir.iterdir()
            if f.suffix == ".mp4" and "recording" in f.name]
    if not mp4s:
        mp4s = [f for f in cam_dir.iterdir() if f.suffix == ".mp4"]
    return mp4s[0] if mp4s else None


def load_blockids(rec_dir: Path):
    """Return (cam_names, [blockids], [video paths]). Raises if any missing."""
    cam_dirs = camera_dirs(rec_dir)
    if not cam_dirs:
        raise FileNotFoundError(f"No cam*/ directories in {rec_dir}")
    names, blocks, videos = [], [], []
    for cd in cam_dirs:
        bpath = cd / "blockids.npy"
        if not bpath.exists():
            raise FileNotFoundError(
                f"{bpath} missing — recording predates block-ID logging, "
                "cannot be block-ID aligned.")
        b = np.load(bpath)
        if b.ndim != 1:
            raise ValueError(f"{bpath}: expected 1-D block IDs, got {b.shape}")
        b = _unwrap_blockids(b)  # undo 16-bit wrap so IDs are globally monotonic
        names.append(cd.name)
        blocks.append(b)
        videos.append(video_for(cd))
    return names, blocks, videos


def block_rate_warnings(rec_dir: Path, names, blocks, fps: int) -> list:
    """Check each camera's block IDs really are trigger ordinals.

    The intersection below matches on block ID and nothing else, so it is only
    an alignment if every camera consumed one block ID per trigger. A camera
    that ignored triggers (exposure over the ceiling) still writes gapless
    block IDs and still ends up with the same frame count as everyone else, so
    this pass would report "already aligned" on a recording that is skewed by
    seconds. ``frame_sync.check_block_id_rate`` catches it by comparing the
    block-ID counter against the camera's own device clock.

    Timestamps come from ``frametimes.npy`` row 1 (device seconds, shifted to
    start at zero). A camera missing that file is skipped, not failed — old
    recordings should still align.
    """
    ids, times, have = [], [], []
    for nm, b in zip(names, blocks):
        ft_path = rec_dir / nm / "frametimes.npy"
        if not ft_path.exists():
            continue
        try:
            ft = np.load(ft_path)
        except Exception:
            continue
        if ft.ndim != 2 or ft.shape[0] < 2 or ft.shape[1] < b.size:
            continue
        ids.append(b)
        times.append(ft[1])
        have.append(nm)
    if not have:
        return []
    return _block_rate_warnings(ids, times, fps, have)


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
        if pos.size and np.any(b[pos] != common):
            raise RuntimeError(f"camera {c}: block-ID index mismatch")
        frame_index[c] = pos
    return common, frame_index


def needs_alignment(blocks: list[np.ndarray]) -> bool:
    """True iff some camera holds a frame another camera is missing."""
    common, _ = compute_alignment(blocks)
    return any(b.size > common.size for b in blocks)


def _ffmpeg_exe() -> str:
    from imageio_ffmpeg import get_ffmpeg_exe
    return get_ffmpeg_exe()


def extract_aligned(video: Path, frame_idx: np.ndarray, dst: Path,
                    fps: int, quality: int) -> int:
    """Re-encode only the selected frame indices, in order, into dst (gray)."""
    import cv2
    keep = set(int(i) for i in frame_idx)
    cap = cv2.VideoCapture(str(video))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    last = int(frame_idx[-1]) if frame_idx.size else -1
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    cmd = [
        _ffmpeg_exe(), "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
        "-c:v", "h264_nvenc", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-qp", str(quality), "-g", str(fps),
        "-bf:v", "0", "-gpu", "0",
        # moov atom to the front — this output REPLACES the session recording
        # (os.replace below), so it is the file LUC3D actually opens. See
        # gui_app/encode_worker.py for why moov-at-end breaks the browser labeler.
        "-movflags", "+faststart",
        "-loglevel", "error", str(dst),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, startupinfo=startupinfo)
    n = written = 0
    try:
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
    finally:
        proc.stdin.close()
        proc.wait()
        cap.release()
    return written


def _rewrite_metadata(cam_dir: Path, cam_blockids: np.ndarray,
                      frame_idx: np.ndarray, common: np.ndarray, fps: int):
    """Replace blockids.npy + frametimes.npy with the aligned (common) set."""
    np.save(cam_dir / "blockids.npy", common.astype(np.int64))
    ft_path = cam_dir / "frametimes.npy"
    m = common.size
    frame_nums = np.arange(1, m + 1, dtype=np.float64)
    ts = None
    if ft_path.exists():
        ft = np.load(ft_path)
        # Original frametimes line up with the camera's own frames only when it
        # was saved untruncated (realtime path). Otherwise synthesize from the
        # uniform hardware trigger.
        if ft.ndim == 2 and ft.shape[1] == cam_blockids.size:
            ts = ft[1][frame_idx].astype(np.float64)
            ts = ts - ts[0]
    if ts is None:
        ts = (common - common[0]).astype(np.float64) / float(fps)
    np.save(ft_path, np.stack([frame_nums, ts]))


def align_recording(rec_dir, fps: int = 100, quality: int = 21,
                    replace: bool = False, parallel: int = 3,
                    progress=None) -> dict:
    """Align a recording by block ID.

    Always writes ``aligned/alignment.{npz,json}`` (the lossless index). When
    ``replace`` and some camera has extra frames, re-encodes each camera's mp4
    down to the common frames and atomically replaces the original, rewriting
    that camera's blockids.npy + frametimes.npy to match.

    ``progress(done, total, msg)`` is called as cameras complete (thread-safe).
    Returns a summary dict.
    """
    rec_dir = Path(rec_dir)
    names, blocks, videos = load_blockids(rec_dir)
    common, frame_index = compute_alignment(blocks)
    full_span = int(max(int(b[-1]) for b in blocks)
                    - min(int(b[0]) for b in blocks) + 1)
    need = any(b.size > common.size for b in blocks)

    out = rec_dir / "aligned"
    out.mkdir(exist_ok=True)
    np.savez(out / "alignment.npz", common_block_ids=common,
             frame_index=frame_index, camera_names=np.array(names))
    manifest = dict(
        recording=str(rec_dir), camera_names=names, trigger_span=full_span,
        common_frames=int(common.size), replaced=bool(replace and need),
        per_camera={nm: dict(recorded=int(b.size),
                             dropped=int(full_span - b.size))
                    for nm, b in zip(names, blocks)},
    )
    with open(out / "alignment.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # Runs before the early returns below, because the dangerous case reports
    # "already aligned": a camera ignoring triggers keeps its block IDs
    # gapless, so the intersection is total and nothing here looks wrong.
    rate_warnings = block_rate_warnings(rec_dir, names, blocks, fps)
    for msg in rate_warnings:
        print(f"[align] WARNING: {msg}", flush=True)

    summary = dict(common_frames=int(common.size), trigger_span=full_span,
                   needed=need, replaced=False, camera_names=names,
                   per_camera=manifest["per_camera"],
                   warnings=list(rate_warnings))

    total = len(names)
    if not need:
        if progress:
            progress(total, total, "already aligned")
        return summary
    if not replace:
        return summary  # index written; videos left as-is

    lock = threading.Lock()
    done = [0]

    def _one(c):
        nm, vid = names[c], videos[c]
        if vid is None:
            with lock:
                summary["warnings"].append(f"{nm}: no video, skipped")
                done[0] += 1
                if progress:
                    progress(done[0], total, f"{nm} skipped (no video)")
            return
        cam_dir = vid.parent
        tmp = cam_dir / "aligned_tmp.mp4"
        written = extract_aligned(vid, frame_index[c], tmp, fps, quality)
        if written != common.size:
            tmp.unlink(missing_ok=True)
            with lock:
                summary["warnings"].append(
                    f"{nm}: extracted {written}/{common.size}, original KEPT")
                done[0] += 1
                if progress:
                    progress(done[0], total, f"{nm} FAILED, kept original")
            return
        _rewrite_metadata(cam_dir, blocks[c], frame_index[c], common, fps)
        os.replace(tmp, vid)  # atomic; original disjoint mp4 replaced
        with lock:
            done[0] += 1
            if progress:
                progress(done[0], total, f"{nm} aligned")

    workers = max(1, min(parallel or total, total))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_one, range(total)))

    summary["replaced"] = not summary["warnings"]
    return summary
