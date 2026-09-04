"""Render an annotated screenshot of the Panopticon GUI for the README.

Launches the real application, waits for live camera frames, then draws a
numbered marker and leader line on each control and saves the result to
docs/images/. The numbers match the list in docs/UI_GUIDE.md.

Positions come from Qt's own widget geometry, not from guessing at pixels, so
the annotations stay correct when the layout, the window size or the camera
count changes. Re-run it after any UI change:

    uv run python docs/make_ui_guide.py

Needs the cameras free (close Panopticon first) and an interactive desktop
session. It opens the real serial port too, so on the first run after a stim
session it will sit through the ~30 s firmware clear before capturing.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QTimer, QPoint, QRect
from PyQt5.QtGui import QPainter, QPen, QColor, QFont, QBrush

from gui_app.main_window import MainWindow

OUT_DIR = REPO / "docs" / "images"
OUT = OUT_DIR / "ui_annotated.png"

ACCENT = QColor("#ff3b6b")     # marker + leader
HALO = QColor(0, 0, 0, 190)    # outline so it reads on any background

#: (number, resolver, placement[, dy]). `resolver` is given the MainWindow and
#: returns the widget to point at; returning None skips that number gracefully
#: rather than crashing the whole render. `dy` nudges the badge vertically, for
#: controls that share a row -- without it Calibrate and Solve, which sit side
#: by side, produced two badges at the same point and one hid the other.
TARGETS = [
    (1,  lambda w: w._camera_grid,                          "inside"),
    (2,  lambda w: _cell(w, 1),                             "inside"),
    (3,  lambda w: _cell(w, 1).fps_overlay,                 "right"),
    (4,  lambda w: w._sidebar._profile_combo,               "left"),
    (5,  lambda w: w._sidebar._dir_button,                  "left"),
    (6,  lambda w: _first_field(w),                         "left"),
    (7,  lambda w: w._sidebar._calibrate_toggle,            "left", -15),
    (8,  lambda w: w._sidebar._record_toggle,               "left"),
    (9,  lambda w: w._sidebar._run_calib_btn,               "left", 15),
    (10, lambda w: w._sidebar._snapshot_btn,                "left"),
    (11, lambda w: w._sidebar._stim_btn,                    "left"),
    (12, lambda w: w._sidebar._coverage_graph,              "left"),
    (13, lambda w: w._sidebar._brightness_slider,           "left"),
    (14, lambda w: w._sidebar._contrast_slider,             "left"),
    (15, lambda w: w._sidebar._progress,                    "left"),
    (16, lambda w: w._sidebar._status,                      "left"),
    (17, lambda w: w.statusBar(),                           "above"),
]


def _cell(w, i):
    cells = getattr(w._camera_grid, "_cells", [])
    return cells[i] if i < len(cells) else None


def _first_field(w):
    fields = getattr(w._sidebar, "_fields", {})
    return next(iter(fields.values()), None)


def _rect_in_window(win, widget) -> QRect | None:
    """Widget geometry mapped into the window's coordinate space."""
    if widget is None or not widget.isVisible():
        return None
    top_left = widget.mapTo(win, QPoint(0, 0))
    return QRect(top_left, widget.size())


def annotate(win) -> int:
    pix = win.grab()
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    font = QFont("Segoe UI", 11, QFont.Bold)
    p.setFont(font)

    sb_rect = _rect_in_window(win, win._sidebar)
    sidebar_left = sb_rect.left() if sb_rect else 0

    drawn = 0
    missing = []
    for entry in TARGETS:
        num, resolve, placement = entry[0], entry[1], entry[2]
        dy = entry[3] if len(entry) > 3 else 0
        try:
            widget = resolve(win)
        except Exception:
            widget = None
        rect = _rect_in_window(win, widget)
        if rect is None or rect.width() <= 0:
            missing.append(num)
            continue

        # Outline the element. NoBrush matters: the badge below sets a fill,
        # and leaving it set made every later outline draw as a solid block
        # that hid the control it was meant to point at.
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(HALO, 4))
        p.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 4, 4)
        p.setPen(QPen(ACCENT, 2))
        p.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 4, 4)

        r = 15
        if placement == "inside":
            # Big areas (the whole preview, one pane): sit the badge in the
            # corner, no leader — there is nothing to disambiguate.
            centre = QPoint(rect.left() + r + 8, rect.top() + r + 8)
            anchor = None
        elif placement == "right":
            centre = QPoint(rect.right() + 42, rect.center().y())
            anchor = QPoint(rect.right() + 6, rect.center().y())
        elif placement == "above":
            centre = QPoint(rect.left() + 60, rect.top() - 26)
            anchor = QPoint(rect.left() + 60, rect.top() - 3)
        else:  # "left"
            # All sidebar badges share one x, just outside the panel. Using each
            # widget's own left edge instead put badges for indented controls
            # (the sliders, the metadata fields) straight on top of their labels.
            col_x = (sidebar_left - 22) if sidebar_left else (rect.left() - 40)
            centre = QPoint(col_x, rect.center().y() + dy)
            anchor = QPoint(rect.left() - 6, rect.center().y() + dy)

        # Keep the badge inside the image no matter how the layout moves.
        centre.setX(max(r + 2, min(centre.x(), pix.width() - r - 2)))
        centre.setY(max(r + 2, min(centre.y(), pix.height() - r - 2)))

        if anchor is not None:
            p.setPen(QPen(HALO, 5))
            p.drawLine(centre, anchor)
            p.setPen(QPen(ACCENT, 2))
            p.drawLine(centre, anchor)

        p.setPen(QPen(HALO, 3))
        p.setBrush(QBrush(ACCENT))
        p.drawEllipse(centre, r, r)
        p.setPen(QPen(Qt.white))
        p.drawText(QRect(centre.x() - r, centre.y() - r, 2 * r, 2 * r),
                   Qt.AlignCenter, str(num))
        drawn += 1

    p.end()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pix.save(str(OUT))
    print(f"wrote {OUT}  ({pix.width()}x{pix.height()}, {drawn} labels)")
    if missing:
        print(f"NOT VISIBLE, so unlabelled: {missing} — a control that is "
              f"hidden in this state cannot be pointed at.")
    return drawn


def _demo_coverage(win):
    """Populate the calibration HUD so the guide shows what it looks like in use.

    The widget renders from a BoardDetector snapshot, so we build a real one and
    fill in a plausible mid-calibration state: most pairs well covered, two
    cameras still glowing, one pair still short.
    """
    win._sidebar.show_coverage()
    try:
        import numpy as np
        from gui_app.board_detector import BoardDetector
        n = max(win._camera_mgr.num_cameras, 6)
        det = BoardDetector(n, win._profile.board_config)
        det.shared = (np.full((n, n), 140, dtype=int)
                      - np.eye(n, dtype=int) * 140)
        det.shared[0, 3] = det.shared[3, 0] = 35        # one weak pair
        det.per_cam_covis = np.full(n, 190, dtype=int)
        det.glow = np.zeros(n)
        det.glow[1] = det.glow[4] = 1.0                 # two cameras seeing it now
        det.grid_cells_hit = np.full(n, 4, dtype=int)
        det.grid_cells_hit[3] = 2
        win._sidebar.update_coverage(det)
    except Exception as e:
        print(f"coverage demo skipped: {e}", flush=True)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    # Reveal the controls that are hidden until a run is in progress, so the
    # guide can show the whole interface rather than only its idle subset.
    _demo_coverage(win)
    win._sidebar.show_progress(3, 6, "Encoding")
    win.statusBar().showMessage(
        "Capture healthy — keeping up with the trigger (max lag 3 ms)")
    win.resize(1600, 950)
    win.show()

    def shoot():
        annotate(win)
        try:
            win._camera_mgr.close_all()
        except Exception:
            pass
        try:
            if win._teensy is not None and win._teensy.is_open:
                win._teensy.stop_triggers(win._profile.trigger_pins)
                win._teensy.close()
        except Exception:
            pass
        app.quit()

    # Long enough for the startup firmware check (~30 s worst case) and for
    # preview frames to arrive, so the panes show live video rather than black.
    QTimer.singleShot(50_000, shoot)
    app.exec_()


if __name__ == "__main__":
    main()
