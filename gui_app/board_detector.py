"""Live ChArUco coverage detector for the calibration HUD.

Runs lightweight ChArUco detection on the per-camera *preview* frames (already
downsampled, grayscale) and tracks:

  - ``glow``          : per-camera decaying pulse, set to 1.0 on each detection
  - ``shared``        : pairwise co-detection counts (board seen by both cams in
                        the same display tick)
  - ``per_cam_covis`` : per-camera co-visibility coverage (ticks where this cam
                        detected the board AND at least one other cam did too)
  - ``ready``         : the co-visibility graph is one connected component (via
                        edges >= ``min_edge``) AND every camera has
                        >= ``min_per_cam_shared`` co-visible detections.

Counts are at the *display sample rate*, so the thresholds are relative coverage
signals, not recorded-frame totals — tune them on the rig.

Works across both the pre-4.7 and >=4.7 OpenCV ArUco APIs.
"""
import math
import time

import numpy as np
import cv2
import yaml


class _CharucoEngine:
    """Returns a charuco-corner count for a grayscale frame, across cv2 versions."""

    def __init__(self, board_x, board_y, marker_bits, dict_size):
        aruco = cv2.aruco
        dict_name = "DICT_{0}X{0}_{1}".format(marker_bits, dict_size)
        self._dict = aruco.getPredefinedDictionary(getattr(aruco, dict_name))
        # Lengths are arbitrary here — only board topology matters for detection.
        self._new_api = hasattr(aruco, "CharucoDetector")
        if self._new_api:
            self._board = aruco.CharucoBoard((board_x, board_y), 1.0, 0.8, self._dict)
            self._detector = aruco.CharucoDetector(self._board)
        else:
            self._board = aruco.CharucoBoard_create(board_x, board_y, 1.0, 0.8, self._dict)
            self._params = aruco.DetectorParameters_create()

    def count(self, gray):
        if gray is None:
            return 0
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
        if not gray.flags["C_CONTIGUOUS"]:
            gray = np.ascontiguousarray(gray)
        try:
            if self._new_api:
                ch_corners, _ch_ids, _m_corners, _m_ids = self._detector.detectBoard(gray)
                return 0 if ch_corners is None else int(len(ch_corners))
            corners, ids, _ = cv2.aruco.detectMarkers(gray, self._dict, parameters=self._params)
            if ids is None or len(ids) == 0:
                return 0
            retval, _cc, _ci = cv2.aruco.interpolateCornersCharuco(
                corners, ids, gray, self._board)
            return int(retval) if retval else 0
        except cv2.error:
            return 0


class BoardDetector:
    def __init__(self, n_cams, board_config_path,
                 glow_threshold=4, edge_threshold=8,
                 optimal_shared=50, min_edge=10, min_per_cam_shared=40,
                 glow_decay_s=0.4):
        self.n = int(n_cams)
        self.glow_threshold = glow_threshold      # corners to "glow" a node
        self.edge_threshold = edge_threshold      # corners to count toward edges
        self.optimal_shared = optimal_shared      # edge width maxes out here
        self.min_edge = min_edge                  # edge counts as a graph link here
        self.min_per_cam_shared = min_per_cam_shared
        self.glow_decay_s = glow_decay_s
        with open(board_config_path) as f:
            b = yaml.safe_load(f)
        self._engine = _CharucoEngine(
            b["board_x"], b["board_y"],
            b.get("marker_bits", 4), b.get("dict_size", 1000))
        self.reset()

    def reset(self):
        n = self.n
        self.glow = np.zeros(n)
        self.shared = np.zeros((n, n), dtype=int)
        self.per_cam_covis = np.zeros(n, dtype=int)
        self.ready = False
        self._last = time.perf_counter()

    def update(self, frames):
        """Run one detection tick over the list of per-camera preview frames."""
        if self.ready:
            return self  # frozen once coverage is complete
        now = time.perf_counter()
        dt = max(0.0, now - self._last)
        self._last = now
        self.glow *= math.exp(-dt / self.glow_decay_s)

        seen = []
        for i in range(self.n):
            fr = frames[i] if frames is not None and i < len(frames) else None
            nc = self._engine.count(fr)
            if nc >= self.glow_threshold:
                self.glow[i] = 1.0
            if nc >= self.edge_threshold:
                seen.append(i)

        if len(seen) >= 2:
            for i in seen:
                self.per_cam_covis[i] += 1
            for a in range(len(seen)):
                for b in range(a + 1, len(seen)):
                    self.shared[seen[a], seen[b]] += 1
                    self.shared[seen[b], seen[a]] += 1

        self._update_ready()
        return self

    def _update_ready(self):
        if self.n == 0 or np.any(self.per_cam_covis < self.min_per_cam_shared):
            self.ready = False
            return
        parent = list(range(self.n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(self.n):
            for j in range(i + 1, self.n):
                if self.shared[i, j] >= self.min_edge:
                    parent[find(i)] = find(j)
        roots = {find(i) for i in range(self.n)}
        self.ready = (len(roots) == 1)
