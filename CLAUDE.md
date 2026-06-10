# CLAUDE.md — Panopticon (3dpose)

Multi-camera synchronized acquisition GUI (PyQt5 + pypylon + NVENC) for the 3dpose
rig. The `gui_app/` codebase is shared with the **3dface** repo; only the rig
profile (`profiles/*.yaml`) differs. Launch: `uv run gui.py` (or
`conda run -n 3dpose python gui.py`).

## Calibration coverage HUD (merged — PR #1, rig-validated 2026-05-29)

The live ChArUco coverage graph (sidebar, during calibration), 30 fps calibration
capture+preview, parallel NVENC encoding, and snapshot button are **merged to
master and validated on the rig**. `docs/CALIBRATION_HUD_HANDOFF.md` has the
original design rationale and file map.

Rig-validated fixes layered on top of the original PR (all on master):
- **`board_legacy: true`** in the board config — the physical 3dpose board uses
  the pre-OpenCV-4.6 ChArUco layout; without `setLegacyPattern(True)` the ≥4.7
  `CharucoDetector` returns 0 corners silently. Defaults false for other boards.
- **HUD counts markers, not charuco corners** (`board_detector.py`) — matches the
  `1_calibrate.py` prescan (`len(ids) >= 4`). Corner-counting was far stricter
  than calibration eligibility and starved oblique cameras (cam1/cam4).
- **`coverage_worker.py`** runs detection off the UI thread at ~30 Hz on full-res
  frames (`GrabThread.set_keep_full`).
- **`encode_parallel`** profile field (default 3) caps concurrent NVENC jobs.
- READY target `min_per_cam_shared = 20` (~2× the ~10-frame sleap-anipose floor).

3dface mirror of `gui_app/` is deferred — this rig only runs 3dpose for now.

## Conventions
- New OpenCV dependency: `opencv-contrib-python` (run `uv sync`). The coverage
  HUD self-disables if OpenCV is missing, so the GUI still runs.
- Video encoding is H.264 via `h264_nvenc` (GPU). Keep `yuv420p` for compatibility.
- **Online (real-time) GPU encode is the default** (`realtime_encode: true`): grab
  threads encode mono→NV12→H.264 via PyNvVideoCodec during capture
  (`gui_app/nvenc.py`), writing `stream.h264`; on stop `encode_worker` remuxes to mp4
  (`ffmpeg -c copy`). Transparently falls back to raw.bin + post-hoc encode if NVENC is
  unavailable or `realtime_encode: false` (the `3dpose (raw)` profile). New deps:
  `PyNvVideoCodec`, `nvidia-cuda-runtime-cu12`.
- Calibration vs recording are distinct acquisitions: calibration uses
  `calibration_frame_rate` (default 30) + 1:1 preview; recording uses `frame_rate`
  (100) + decimated preview.
