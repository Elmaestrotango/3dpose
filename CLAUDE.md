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
- **Real-time GPU encode is the default** (`realtime_encode: true`), decoupled from
  capture: the grab loop only does retrieve→copy→queue→release; encoders drain through
  PyNvVideoCodec (`gui_app/nvenc.py`) into `stream.h264`; on stop `encode_worker`
  remuxes to mp4 (`ffmpeg -c copy`). Inline encode starved GigE packet reassembly and
  dropped ~28% of frames — do NOT put work back on the grab loop's critical path. Deps:
  `PyNvVideoCodec`, `nvidia-cuda-runtime-cu12`.
- **Cameras drop frames independently (GigE packet loss), so frame i is NOT the same
  trigger across cameras.** Every recorded frame is tagged with its GigE BlockID (=
  trigger ordinal) in `blockids.npy`. Two ways the videos are made trigger-aligned:
  - **Real-time frame kick-out is the DEFAULT** (`realtime_kick: true`, the `3dpose`
    profile). A shared `FrameSyncCoordinator` (`gui_app/frame_sync.py`) + per-camera
    encoders (`gui_app/sync_encode.py` `SyncEncodeRouter`) release a trigger to the
    encoders only once ALL cameras caught it — so videos come out equal-length and
    aligned with NO post-hoc re-encode. `kick_max_lag` (default 240) bounds cross-camera
    lag; **do NOT raise it blindly — the NV12 ring scales with it; 1000 starved capture
    (24% loss, 2026-06-17).** Proven == post-hoc intersection in `test_frame_sync.py`;
    router smoke test `test_sync_router.py` (needs NVENC).
  - **Post-hoc block-ID alignment** (`gui_app/alignment.py`, `align_worker.py`) runs
    after encode when `realtime_kick: false`: re-encodes each video down to the common
    frames and replaces it. Jitter-immune (keeps slightly more) but costs a re-encode.
    Standalone CLI for old recordings: `uv run 2_align.py <recording_dir> --replace`.
  - `_unwrap_blockids` handles the 16-bit BlockID wrap at 65535 (~11 min). Cameras also
    try to enable 64-bit extended BlockIDs at open.
- `3dpose (raw)` profile (`realtime_encode: false`) = the proven raw.bin + post-hoc
  NVENC fallback (no GPU encode during capture).
- Blocking camera ops (open/close/reconfigure) run off the Qt main thread via
  `gui_app/ui_workers.py` `CallableWorker` (else the window goes "not responding").
  Quitting mid-session abandons + deletes the incomplete data (`_abandon_and_cleanup`).
- Calibration vs recording are distinct acquisitions: calibration uses
  `calibration_frame_rate` (default 30) + 1:1 preview; recording uses `frame_rate`
  (100) + decimated preview.
