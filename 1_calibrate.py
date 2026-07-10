# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy<2",
#     "pyyaml>=6.0",
#     "opencv-contrib-python>=4.6",
#     "matplotlib>=3.5",
# ]
# ///
"""Multi-view camera calibration using ArUco marker corners directly.

Run with: uv run 1_calibrate.py <session_dir> --board-config <board.yaml>

The session directory should contain a calibration/ subfolder with per-camera
video subdirectories (cam1/, cam2/, ...) produced by the Panopticon GUI.

Outputs calibration.toml into the calibration directory.
"""
import argparse
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import yaml


# ---------------------------------------------------------------------------
# Board setup
# ---------------------------------------------------------------------------

def create_board_and_dict(cfg):
    aruco = cv2.aruco
    bits = cfg.get("marker_bits", 4)
    dsz = cfg.get("dict_size", 1000)
    dict_name = "DICT_{}X{}_{}".format(bits, bits, dsz)
    aruco_dict = aruco.getPredefinedDictionary(
        getattr(aruco, dict_name, aruco.DICT_4X4_1000))

    bx, by = cfg["board_x"], cfg["board_y"]
    sq, mk = float(cfg["square_length"]), float(cfg["marker_length"])
    legacy = cfg.get("board_legacy", False)

    if hasattr(aruco, "CharucoBoard") and not hasattr(aruco, "CharucoBoard_create"):
        board = aruco.CharucoBoard((bx, by), sq, mk, aruco_dict)
    else:
        board = aruco.CharucoBoard_create(bx, by, sq, mk, aruco_dict)
    if legacy and hasattr(board, "setLegacyPattern"):
        board.setLegacyPattern(True)

    return board, aruco_dict


def get_marker_obj_points(board):
    """Return {marker_id: ndarray(4,3)} — each marker's 4 corner 3D positions."""
    ids = board.getIds().ravel()
    all_obj = board.getObjPoints()
    return {int(mid): np.asarray(pts, dtype=np.float32)
            for mid, pts in zip(ids, all_obj)}


# ---------------------------------------------------------------------------
# Detection (parallelized across cameras)
# ---------------------------------------------------------------------------

def _detect_one_camera(args_tuple):
    """Detect ArUco markers in a camera's video.
    If target_frames is provided, only those frame numbers are decoded and
    checked (fast seek). Otherwise every skip-th frame is processed."""
    video_path, board_cfg, target_frames = args_tuple
    aruco = cv2.aruco
    bits = board_cfg.get("marker_bits", 4)
    dsz = board_cfg.get("dict_size", 1000)
    dict_name = "DICT_{}X{}_{}".format(bits, bits, dsz)
    aruco_dict = aruco.getPredefinedDictionary(
        getattr(aruco, dict_name, aruco.DICT_4X4_1000))

    if hasattr(aruco, "ArucoDetector"):
        det = aruco.ArucoDetector(aruco_dict, aruco.DetectorParameters())
        def _detect(gray):
            return det.detectMarkers(gray)[:2]
    else:
        params = aruco.DetectorParameters_create()
        def _detect(gray):
            c, i, _ = aruco.detectMarkers(gray, aruco_dict, parameters=params)
            return c, i

    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    frames_with_det = []
    all_corners = []
    all_ids = []

    if target_frames is not None:
        # Fast path: only decode the frames the HUD flagged as co-detections
        for fn in sorted(target_frames):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fn)
            ret, frame = cap.read()
            if not ret:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids = _detect(gray)
            if ids is not None and len(ids) >= 3:
                frames_with_det.append(fn)
                all_corners.append(
                    np.array([c.reshape(4, 2) for c in corners], dtype=np.float32))
                all_ids.append(ids.ravel().astype(np.int32))
    else:
        # Full scan with skip + burst
        skip = board_cfg.get("_skip", 3)
        burst = max(1, skip)
        frame_n = 0
        go = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_n % skip != 0 and go <= 0:
                frame_n += 1
                continue
            go = max(0, go - 1)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids = _detect(gray)
            if ids is not None and len(ids) >= 3:
                frames_with_det.append(frame_n)
                all_corners.append(
                    np.array([c.reshape(4, 2) for c in corners], dtype=np.float32))
                all_ids.append(ids.ravel().astype(np.int32))
                go = burst
            frame_n += 1
    cap.release()

    cam_name = Path(video_path).parent.name
    return cam_name, frames_with_det, all_corners, all_ids, total, (w, h)


def detect_all_cameras(cam_dirs, board_cfg, excluded, codet_path=None):
    """Run marker detection on all cameras. If codet_path points to a
    codet_frames.json (saved by the coverage HUD), only those frames are
    decoded — much faster than scanning the full video."""
    codet = None
    if codet_path and codet_path.exists():
        import json
        with open(codet_path) as f:
            codet = json.load(f)
        print("  Using co-detection hints ({} cameras)".format(len(codet)))

    tasks = []
    for cam_dir in cam_dirs:
        if cam_dir.name in excluded:
            continue
        mp4s = sorted(f for f in cam_dir.iterdir()
                      if f.is_file() and f.suffix == ".mp4"
                      and "calibration" in f.name)
        if not mp4s:
            continue
        target = None
        if codet and cam_dir.name in codet:
            target = codet[cam_dir.name]
        tasks.append((str(mp4s[0]), board_cfg, target))

    results = {}
    sizes = {}
    for task in tasks:
        cam, fns, corners, ids, total, sz = _detect_one_camera(task)
        results[cam] = (fns, corners, ids)
        sizes[cam] = sz
        n_markers = sum(len(i) for i in ids)
        avg = n_markers / len(fns) if fns else 0
        print("  {}: {}/{} frames with markers (avg {:.1f}/frame)".format(
            cam, len(fns), total, avg), flush=True)
    return results, sizes


# ---------------------------------------------------------------------------
# Correspondences: vectorized marker-corner → 3D/2D point arrays
# ---------------------------------------------------------------------------

def _build_pts(corners, ids, marker_obj):
    """Build (obj_pts, img_pts) for one frame. Returns None pair if empty."""
    mask = np.isin(ids, list(marker_obj.keys()))
    valid_ids = ids[mask]
    valid_corners = corners[mask]  # (M, 4, 2)
    if len(valid_ids) == 0:
        return None, None
    obj = np.stack([marker_obj[int(m)] for m in valid_ids])  # (M, 4, 3)
    return (obj.reshape(-1, 1, 3).astype(np.float32),
            valid_corners.reshape(-1, 1, 2).astype(np.float32))


# ---------------------------------------------------------------------------
# Intrinsic calibration
# ---------------------------------------------------------------------------

def calibrate_intrinsics(fns, corners_list, ids_list, marker_obj, image_size,
                         min_markers=3, max_frames=30, min_frames=20):
    obj_all, img_all = [], []
    for corners, ids in zip(corners_list, ids_list):
        if len(ids) < min_markers:
            continue
        obj, img = _build_pts(corners, ids, marker_obj)
        if obj is None:
            continue
        obj_all.append(obj)
        img_all.append(img)

    if len(obj_all) < min_frames:
        return None

    if len(obj_all) > max_frames:
        step = len(obj_all) / max_frames
        indices = [int(i * step) for i in range(max_frames)]
        obj_all = [obj_all[i] for i in indices]
        img_all = [img_all[i] for i in indices]

    flags = (cv2.CALIB_FIX_ASPECT_RATIO    # fx == fy
             | cv2.CALIB_FIX_K3            # don't fit k3 (overfits with few points)
             | cv2.CALIB_ZERO_TANGENT_DIST) # p1=p2=0 (negligible for machine-vision lenses)
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(
        obj_all, img_all, image_size, None, None, flags=flags)
    return rms, K, dist, len(obj_all)


# ---------------------------------------------------------------------------
# Pairwise stereo calibration
# ---------------------------------------------------------------------------

def calibrate_pair(data_a, data_b, marker_obj, K_a, d_a, K_b, d_b,
                   image_size, min_markers=3, min_frames=3):
    fns_a, corners_a, ids_a = data_a
    fns_b, corners_b, ids_b = data_b

    # Index by frame number for fast lookup
    idx_a = {}
    for i, fn in enumerate(fns_a):
        idx_a[fn] = i
    idx_b = {}
    for i, fn in enumerate(fns_b):
        idx_b[fn] = i

    shared = sorted(set(fns_a) & set(fns_b))

    obj_list, img_a_list, img_b_list = [], [], []
    for fn in shared:
        ia, ib = idx_a[fn], idx_b[fn]
        ca, ida = corners_a[ia], ids_a[ia]
        cb, idb = corners_b[ib], ids_b[ib]

        common = np.intersect1d(ida, idb)
        common = common[np.isin(common, list(marker_obj.keys()))]
        if len(common) < min_markers:
            continue

        # Vectorized gather: for each common marker, find its index in each camera
        idx_in_a = np.searchsorted(np.sort(ida), common)
        sort_order_a = np.argsort(ida)
        idx_in_a = sort_order_a[idx_in_a]

        idx_in_b = np.searchsorted(np.sort(idb), common)
        sort_order_b = np.argsort(idb)
        idx_in_b = sort_order_b[idx_in_b]

        obj = np.stack([marker_obj[int(m)] for m in common])  # (M, 4, 3)
        obj_list.append(obj.reshape(-1, 1, 3).astype(np.float32))
        img_a_list.append(ca[idx_in_a].reshape(-1, 1, 2).astype(np.float32))
        img_b_list.append(cb[idx_in_b].reshape(-1, 1, 2).astype(np.float32))

    if len(obj_list) < min_frames:
        return None

    # Cap frames to keep stereoCalibrate fast
    if len(obj_list) > 30:
        step = len(obj_list) / 30
        idx = [int(i * step) for i in range(30)]
        obj_list = [obj_list[i] for i in idx]
        img_a_list = [img_a_list[i] for i in idx]
        img_b_list = [img_b_list[i] for i in idx]

    rms, _, _, _, _, R, T, _, _ = cv2.stereoCalibrate(
        obj_list, img_a_list, img_b_list,
        K_a, d_a, K_b, d_b, image_size,
        flags=cv2.CALIB_FIX_INTRINSIC)
    return R, T, rms, len(obj_list)


# ---------------------------------------------------------------------------
# Global extrinsics via spanning tree
# ---------------------------------------------------------------------------

def build_graph(cam_names, pairwise):
    """Check connectivity and build minimum-error spanning tree (Prim's)."""
    adj = defaultdict(list)
    for (a, b), (_, _, rms, _) in pairwise.items():
        adj[a].append((b, rms))
        adj[b].append((a, rms))

    visited = {cam_names[0]}
    queue = [cam_names[0]]
    while queue:
        node = queue.pop(0)
        for nb, _ in adj[node]:
            if nb not in visited:
                visited.add(nb)
                queue.append(nb)

    if len(visited) < len(cam_names):
        return None, set(cam_names) - visited

    in_tree = {cam_names[0]}
    edges = []
    while len(in_tree) < len(cam_names):
        best = None
        for node in in_tree:
            for nb, rms in adj[node]:
                if nb not in in_tree and (best is None or rms < best[2]):
                    best = (node, nb, rms)
        if best is None:
            break
        edges.append((best[0], best[1]))
        in_tree.add(best[1])
    return edges, None


def chain_extrinsics(cam_names, tree_edges, pairwise, ref_cam):
    """Chain pairwise R,T along spanning tree into global poses."""
    g_R = {ref_cam: np.eye(3, dtype=np.float64)}
    g_t = {ref_cam: np.zeros((3, 1), dtype=np.float64)}

    adj = defaultdict(list)
    for a, b in tree_edges:
        adj[a].append(b)
        adj[b].append(a)

    queue, visited = [ref_cam], {ref_cam}
    while queue:
        node = queue.pop(0)
        for nb in adj[node]:
            if nb in visited:
                continue
            visited.add(nb)
            queue.append(nb)

            if (node, nb) in pairwise:
                R_ab, T_ab = pairwise[(node, nb)][:2]
            else:
                R_ba, T_ba = pairwise[(nb, node)][:2]
                R_ab, T_ab = R_ba.T, -R_ba.T @ T_ba

            g_R[nb] = R_ab @ g_R[node]
            g_t[nb] = R_ab @ g_t[node] + T_ab

    out = {}
    for cam in cam_names:
        rvec, _ = cv2.Rodrigues(g_R[cam])
        out[cam] = (rvec.ravel(), g_t[cam].ravel())
    return out


# ---------------------------------------------------------------------------
# Reprojection error histogram
# ---------------------------------------------------------------------------

def compute_reprojection_errors(all_dets, active_cams, intrinsics, extrinsics,
                                marker_obj):
    """Compute per-camera reprojection errors across all detected frames."""
    per_cam_errors = {}
    for cam in active_cams:
        fns, corners_list, ids_list = all_dets[cam]
        K, dist = intrinsics[cam]
        rvec_global, tvec_global = extrinsics[cam]
        R_global, _ = cv2.Rodrigues(rvec_global)

        errors = []
        for corners, ids in zip(corners_list, ids_list):
            obj, img = _build_pts(corners, ids, marker_obj)
            if obj is None:
                continue
            obj_3d = obj.reshape(-1, 3)
            img_2d = img.reshape(-1, 2)
            projected, _ = cv2.projectPoints(
                obj_3d, rvec_global, tvec_global, K, dist)
            projected = projected.reshape(-1, 2)
            err = np.linalg.norm(projected - img_2d, axis=1)
            errors.extend(err.tolist())
        per_cam_errors[cam] = np.array(errors)
    return per_cam_errors


def save_reprojection_histogram(path, pair_rms):
    """Save a pairwise stereo RMS bar chart as PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib not available, skipping histogram")
        return

    labels = sorted(pair_rms.keys())
    values = [pair_rms[k] for k in labels]
    if not values:
        return

    fig, ax = plt.subplots(figsize=(max(6, len(labels) * 0.8), 4))
    colors = ["#4CAF50" if v < 10 else "#FF9800" if v < 20 else "#F44336"
              for v in values]
    ax.bar(range(len(labels)), values, color=colors)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Stereo RMS (px)")
    ax.set_title("Pairwise calibration quality")
    ax.axhline(10, color="gray", linestyle="--", alpha=0.5, label="good (<10px)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(str(path), dpi=120)
    plt.close(fig)
    print("  Histogram: {}".format(path))


# ---------------------------------------------------------------------------
# Output (aniposelib-compatible calibration.toml)
# ---------------------------------------------------------------------------

def write_calibration_toml(path, cam_names, intrinsics, extrinsics, sizes):
    lines = []
    for i, cam in enumerate(cam_names):
        K, dist = intrinsics[cam]
        rvec, tvec = extrinsics[cam]
        w, h = sizes[cam]
        d = dist.ravel()
        if len(d) < 5:
            d = np.concatenate([d, np.zeros(5 - len(d))])
        lines.append("[cam_{}]".format(i))
        lines.append('name = "{}"'.format(cam))
        lines.append("size = [ {}, {},]".format(w, h))
        lines.append("matrix = [ [ {}, 0.0, {},], [ 0.0, {}, {},], [ 0.0, 0.0, 1.0,],]".format(
            repr(K[0, 0]), repr(K[0, 2]), repr(K[1, 1]), repr(K[1, 2])))
        lines.append("distortions = [ {}, {}, {}, {}, {},]".format(
            repr(float(d[0])), repr(float(d[1])), repr(float(d[2])),
            repr(float(d[3])), repr(float(d[4]))))
        lines.append("rotation = [ {}, {}, {},]".format(
            repr(float(rvec[0])), repr(float(rvec[1])), repr(float(rvec[2]))))
        lines.append("translation = [ {}, {}, {},]".format(
            repr(float(tvec[0])), repr(float(tvec[1])), repr(float(tvec[2]))))
        lines.append("")
    lines.append("[metadata]")
    lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Multi-view calibration using ArUco marker corners.")
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--board-config", type=Path, required=True)
    parser.add_argument("--excluded-views", nargs="*", default=[])
    parser.add_argument("--ref-camera", type=str, default="cam1")
    parser.add_argument("--skip", type=int, default=3,
                        help="Process every Nth frame (default 3). Lower = more "
                             "detections but slower.")
    args = parser.parse_args()

    with open(args.board_config) as f:
        board_cfg = yaml.safe_load(f)

    sys.stdout.reconfigure(line_buffering=True)
    print("Board: {}x{}, sq={}, mk={}, legacy={}".format(
        board_cfg["board_x"], board_cfg["board_y"],
        board_cfg["square_length"], board_cfg["marker_length"],
        board_cfg.get("board_legacy", False)))

    board, _ = create_board_and_dict(board_cfg)
    marker_obj = get_marker_obj_points(board)
    print("  {} markers, IDs: {}".format(
        len(marker_obj), sorted(marker_obj.keys())))

    calib_dir = args.session_dir / "calibration"
    if not calib_dir.exists():
        print("ERROR: calibration/ not found in {}".format(args.session_dir),
              file=sys.stderr)
        sys.exit(1)

    cam_dirs = sorted(d for d in calib_dir.iterdir()
                      if d.is_dir() and d.name.startswith("cam"))
    non_cam = {d.name for d in calib_dir.iterdir()
               if d.is_dir() and not d.name.startswith("cam")}
    excluded = set(args.excluded_views) | non_cam

    # --- Detect ---
    board_cfg["_skip"] = args.skip
    codet_path = calib_dir / "codet_frames.json"
    if codet_path.exists():
        print("\nDetecting markers (co-detection hints)...")
    else:
        print("\nDetecting markers (full scan, skip={})...".format(args.skip))
    all_dets, all_sizes = detect_all_cameras(
        cam_dirs, board_cfg, excluded, codet_path=codet_path)

    active = sorted(c for c in all_dets if len(all_dets[c][0]) >= 5)
    dropped = sorted(set(all_dets) - set(active))
    if dropped:
        print("  Dropping (<5 detections): {}".format(", ".join(dropped)))
    if len(active) < 2:
        print("ERROR: fewer than 2 cameras with detections", file=sys.stderr)
        sys.exit(1)

    # --- Intrinsics (parallel — cv2 releases the GIL) ---
    print("\nIntrinsics...")
    def _intrinsic_job(cam):
        fns, corners, ids = all_dets[cam]
        return cam, calibrate_intrinsics(fns, corners, ids, marker_obj,
                                         all_sizes[cam])

    intrinsics = {}
    with ThreadPoolExecutor(max_workers=len(active)) as pool:
        for cam, result in pool.map(_intrinsic_job, active):
            if result is None:
                print("  {}: FAILED".format(cam))
                continue
            rms, K, dist, n = result
            intrinsics[cam] = (K, dist)
            print("  {}: RMS={:.3f}px  fx={:.0f}  ({} frames)".format(
                cam, rms, K[0, 0], n))

    active = [c for c in active if c in intrinsics]
    if len(active) < 2:
        print("ERROR: fewer than 2 cameras with valid intrinsics",
              file=sys.stderr)
        sys.exit(1)

    # --- Pairwise stereo (parallel — cv2 releases the GIL) ---
    print("\nPairwise stereo...")
    pairs = [(active[i], active[j])
             for i in range(len(active)) for j in range(i + 1, len(active))]

    def _stereo_job(pair):
        ca, cb = pair
        Ka, da = intrinsics[ca]
        Kb, db = intrinsics[cb]
        result = calibrate_pair(
            all_dets[ca], all_dets[cb], marker_obj,
            Ka, da, Kb, db, all_sizes[ca])
        return ca, cb, result

    pairwise = {}
    with ThreadPoolExecutor(max_workers=min(len(pairs), 8)) as pool:
        for ca, cb, result in pool.map(_stereo_job, pairs):
            if result is None:
                continue
            R, T, rms, n = result
            pairwise[(ca, cb)] = (R, T, rms, n)
            print("  {}-{}: RMS={:.3f}  {} frames".format(ca, cb, rms, n))

    if not pairwise:
        print("ERROR: no camera pairs with co-detections", file=sys.stderr)
        sys.exit(1)

    # --- Global extrinsics ---
    print("\nCamera graph...")
    tree, missing = build_graph(active, pairwise)
    if tree is None:
        print("  Disconnected — isolated: {}".format(
            ", ".join(sorted(missing))))
        active = [c for c in active if c not in missing]
        if len(active) < 2:
            print("ERROR: too few connected cameras", file=sys.stderr)
            sys.exit(1)
        tree, _ = build_graph(active, pairwise)

    ref = args.ref_camera if args.ref_camera in active else active[0]
    print("  ref={}, tree: {}".format(
        ref, " ".join("{}->{}".format(a, b) for a, b in tree)))

    extrinsics = chain_extrinsics(active, tree, pairwise, ref)

    # --- Quality summary + histogram ---
    print("\nPairwise quality:")
    pair_rms = {}
    warnings = []
    for (ca, cb), (_, _, rms, n) in sorted(pairwise.items()):
        pair_rms["{}-{}".format(ca, cb)] = rms
        print("  {}-{}: RMS={:.1f}px  ({} frames)".format(ca, cb, rms, n))
        if rms > 20:
            warnings.append("{}-{}: high stereo RMS ({:.1f}px)".format(
                ca, cb, rms))

    # Per-camera intrinsics quality
    for cam in active:
        K, dist = intrinsics[cam]
        fns = all_dets[cam][0]
        if len(fns) < 30:
            warnings.append("{}: only {} detection frames (30+ recommended)".format(
                cam, len(fns)))

    hist_path = calib_dir / "reprojection_error_histogram.png"
    save_reprojection_histogram(hist_path, pair_rms)

    # --- Write ---
    out = calib_dir / "calibration.toml"
    write_calibration_toml(out, active, intrinsics, extrinsics, all_sizes)

    print("\nCalibration complete.")
    print("  {}".format(out))
    print("  Cameras: {}".format(" ".join(active)))
    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print("  - {}".format(w))
        print("\n  Consider recording calibration longer with the board "
              "visible to more cameras simultaneously.", file=sys.stderr)


if __name__ == "__main__":
    main()
