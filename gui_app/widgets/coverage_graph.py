"""Live ChArUco coverage graph for the calibration HUD.

Numbered camera nodes on a ring, with an edge for every pair. A node pulses
(glows cyan) when that camera currently sees the board; each edge's width and
whiteness scale with how many ticks the pair co-detected the board, maxing out
at the detector's ``optimal_shared``. When the coverage graph is connected and
every camera is sufficiently covered, the whole graph freezes solid white.
"""
import math

import numpy as np
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import QPainter, QPen, QColor, QFont, QBrush


class CoverageGraphWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.setStyleSheet("background: transparent; border: none;")
        self._n = 0
        self._glow = None
        self._shared = None
        self._per_cam = None
        self._optimal = 50
        self._target = 40
        self._ready = False

    def setup(self, n_cams: int):
        self._n = int(n_cams)
        self._glow = np.zeros(self._n)
        self._shared = np.zeros((self._n, self._n), dtype=int)
        self._per_cam = np.zeros(self._n, dtype=int)
        self._ready = False
        self.update()

    def update_from(self, det):
        """Snapshot a BoardDetector's state and repaint."""
        self._n = det.n
        self._glow = np.asarray(det.glow, dtype=float).copy()
        self._shared = np.asarray(det.shared, dtype=int).copy()
        self._per_cam = np.asarray(det.per_cam_covis, dtype=int).copy()
        self._optimal = det.optimal_shared or 1
        self._target = getattr(det, "min_per_cam_shared", 40)
        self._ready = bool(det.ready)
        self.update()

    def _node_positions(self, w, h, r):
        cx, cy = w / 2.0, h / 2.0
        pos = []
        for i in range(self._n):
            ang = -math.pi / 2 + 2 * math.pi * i / max(self._n, 1)
            pos.append(QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang)))
        return pos

    def paintEvent(self, event):
        if not self._n:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = max(12.0, min(w, h) / 2.0 - 38)
        pos = self._node_positions(w, h - 8, r)
        ready = self._ready

        # --- edges ---
        for i in range(self._n):
            for j in range(i + 1, self._n):
                c = int(self._shared[i, j]) if self._shared is not None else 0
                strength = min(c / float(self._optimal), 1.0)
                if ready:
                    col, width = QColor(255, 255, 255), 4.0
                elif c <= 0:
                    col, width = QColor(55, 55, 75), 1.0
                else:
                    v = int(70 + 185 * strength)
                    col, width = QColor(v, v, v), 1.0 + 5.0 * strength
                pen = QPen(col)
                pen.setWidthF(width)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(pos[i], pos[j])

        # --- nodes ---
        node_r = 15.0
        for i in range(self._n):
            glow = float(self._glow[i]) if self._glow is not None else 0.0
            if ready:
                fill, border, txt = QColor(255, 255, 255), QColor(255, 255, 255), QColor(20, 20, 30)
            else:
                fill = QColor(int(50 + 40 * glow), int(80 + 120 * glow), int(120 + 135 * glow))
                border = QColor(130, 170, 235) if glow > 0.05 else QColor(70, 90, 130)
                txt = QColor(235, 235, 245)
            p.setBrush(QBrush(fill))
            pen = QPen(border)
            pen.setWidthF(2.0)
            p.setPen(pen)
            p.drawEllipse(pos[i], node_r, node_r)
            p.setPen(QPen(txt))
            p.setFont(QFont("Segoe UI", 10, QFont.Bold))
            p.drawText(QRectF(pos[i].x() - node_r, pos[i].y() - node_r, 2 * node_r, 2 * node_r),
                       Qt.AlignCenter, str(i + 1))

        # --- caption ---
        p.setFont(QFont("Segoe UI", 9, QFont.Bold))
        if ready:
            p.setPen(QPen(QColor(255, 255, 255)))
            p.drawText(QRectF(0, h - 18, w, 16), Qt.AlignCenter, "READY — coverage complete")
        else:
            mn = int(self._per_cam.min()) if (self._per_cam is not None and self._n) else 0
            p.setPen(QPen(QColor(150, 150, 170)))
            p.drawText(QRectF(0, h - 18, w, 16), Qt.AlignCenter,
                       f"weakest cam {mn}/{self._target} paired")
        p.end()
