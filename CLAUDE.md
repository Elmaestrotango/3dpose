# CLAUDE.md — Panopticon (3dpose)

Multi-camera synchronized acquisition GUI (PyQt5 + pypylon + NVENC) for the 3dpose
rig. The `gui_app/` codebase is shared with the **3dface** repo; only the rig
profile (`profiles/*.yaml`) differs. Launch: `uv run gui.py` (or
`conda run -n 3dpose python gui.py`).

## Active work — read this if continuing the calibration-HUD feature

There is an in-progress **draft PR #1** on branch `feat/calibration-coverage-hud`
adding: a live ChArUco coverage graph (sidebar, during calibration), 30 fps
calibration capture+preview, parallel NVENC encoding, and a snapshot button.

**It was implemented off-rig and has NOT been tested on real cameras.** Before
changing anything, read **`docs/CALIBRATION_HUD_HANDOFF.md`** — it has the full
file map, design rationale, the rig-test checklist, the tunable thresholds, and
how to iterate. Push fixes to the same branch; merge PR #1 when it works; then
mirror `gui_app/` to the 3dface repo.

## Conventions
- New OpenCV dependency: `opencv-contrib-python` (run `uv sync`). The coverage
  HUD self-disables if OpenCV is missing, so the GUI still runs.
- Video encoding is H.264 via `h264_nvenc` (GPU). Keep `yuv420p` for compatibility.
- Calibration vs recording are distinct acquisitions: calibration uses
  `calibration_frame_rate` (default 30) + 1:1 preview; recording uses `frame_rate`
  (100) + decimated preview.
