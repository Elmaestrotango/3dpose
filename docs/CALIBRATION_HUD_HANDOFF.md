# Session handoff — calibration coverage HUD PR

> **Historical note, kept for provenance.** Written 2026-05-29 to hand this work
> between machines. Everything it describes has since been merged and validated on
> the rig, so the branch, PR and "your job" instructions below are of their moment
> and no longer actions for anyone. It is preserved because it records the original
> design rationale for the coverage HUD and the reasoning behind the readiness
> criteria, which the merged code does not explain in one place.
>
> This is **not** user documentation and is not part of the documentation set — for
> how the coverage HUD behaves now see [OVERVIEW.md](OVERVIEW.md) and
> [WORKFLOW.md](WORKFLOW.md); for how it works see [INTERNALS.md](INTERNALS.md).
> Where this file and those disagree, those are right: the readiness criteria in
> particular were tightened after this was written.

## TL;DR

Draft **PR #1** (`feat/calibration-coverage-hud`) adds four things to the Panopticon
GUI. It was implemented off-rig (on the Linux processing box) and **compiles + has
unit-checked logic, but has never run on real cameras**. The rig is the test bench.
Your job on the rig: run it, fix what breaks, push fixes to the same branch, merge
when solid, then mirror to the 3dface repo.

## What was added (file map)

1. **Live ChArUco coverage graph** — sidebar widget shown only during calibration.
   - `gui_app/board_detector.py` (NEW): runs ChArUco detection on the existing
     per-camera preview frames (~10 Hz, in `MainWindow._refresh_displays`). Tracks
     per-camera glow, pairwise co-visible-detection counts, per-camera coverage,
     and a `ready` flag. Handles BOTH the pre-4.7 and ≥4.7 OpenCV ArUco APIs.
   - `gui_app/widgets/coverage_graph.py` (NEW): `QPainter` widget. Numbered nodes
     glow cyan on detection; edges widen/whiten with shared detections; freezes
     **solid white** when ready.
   - Readiness = **connected graph + every camera covered**, NOT all 15 pairs.
     Why: the board is one-sided, so above↔below camera pairs (cam1↔cam5/6) can't
     co-detect the same pose — a complete graph could never fill. The HUD's value
     is making the weak above↔below bridge obvious so you wave the board at the
     floor plane to connect the clusters.

2. **30 fps calibration** — `calibration_frame_rate` profile field (default 30).
   - Used for the Teensy trigger rate AND the encode fps during calibration;
     recording stays at `frame_rate` (100). See `SessionConfig.rate_for()`.
   - Calibration preview is 1:1 (`display_every=1`, smooth); recording stays
     decimated (`display_every=10`). Wired in `MainWindow._start_acquisition`.
   - Rationale: 100 fps is wasteful for a slowly-waved board; ~3× less raw /
     encode / detection time at 30 fps with no calibration-quality loss.

3. **Parallel NVENC encoding** — `gui_app/encode_worker.py` now runs all cameras'
   ffmpeg/`h264_nvenc` jobs concurrently (pool of `max_parallel`, default = all)
   instead of serially. NVENC is GPU; the bound is disk read bandwidth + the
   8-session GeForce limit, not CPU.

4. **Snapshot button** — full-res PNG per camera → `<session>/snapshots/<date>_<HHMMSS>/camN.png`.
   - Full-res capture path added to `gui_app/grab_thread.py`
     (`request_snapshot()` / `snapshot_frame`) and `gui_app/camera_manager.py`
     (`request_snapshots()` / `snapshots`). Handler: `MainWindow._on_snapshot` →
     `_save_snapshots` (250 ms later, via PIL).

Dependency added: `opencv-contrib-python>=4.6` in `pyproject.toml`. If OpenCV is
missing the GUI still launches — `BoardDetector` import is guarded and the HUD
just disables itself.

## Run it on the rig

```
git fetch origin
git checkout feat/calibration-coverage-hud
uv sync                 # pulls opencv-contrib-python
uv run gui.py           # or your usual launch (conda run -n 3dpose python gui.py)
```

## Rig-test checklist

- [ ] GUI launches with deps synced.
- [ ] Calibrate switch → cameras trigger at **30 fps** (bottom-left readout), preview **smooth**.
- [ ] Coverage graph appears; nodes glow as the board passes each view; edges thicken/whiten with shared views; physically-isolated pairs stay thin.
- [ ] Graph freezes white when connected + covered. **Tune thresholds** if it triggers too early/late.
- [ ] Record switch → 100 fps, normal (decimated) preview.
- [ ] Stop → **all cameras encode in parallel**; mp4s play; `raw.bin` removed; frame counts sane.
- [ ] Snapshot button → full-res PNGs in `snapshots/<...>/camN.png`.
- [ ] Verify the ≥4.7 OpenCV ArUco path works (off-rig box only had 4.6, so the
      `CharucoDetector`/`detectBoard` branch in `board_detector._CharucoEngine` is
      UNVERIFIED). If it errors, that branch is the first suspect.

## Tunables (likely need rig tuning)

In `BoardDetector.__init__` (`gui_app/board_detector.py`) — these are counts at the
**display sample rate** (~10 Hz), not recorded-frame totals:
- `optimal_shared` (default 50): edge width/whiteness maxes out here.
- `min_edge` (default 10): a pair counts as a graph link at this many shared detections.
- `min_per_cam_shared` (default 40): per-camera coverage required for "ready".
- `glow_threshold` (4) / `edge_threshold` (8): charuco-corner counts to glow / count an edge.

## How to iterate

Push fixes to **the same branch** (`feat/calibration-coverage-hud`); the PR updates
automatically. Merge PR #1 when it works. Then mirror the identical `gui_app/`
change to the **3dface repo** (calibration is config-driven — set
`calibration_frame_rate` in the 3dface profile and use the 5×5 board config).

## Background: calibration pipeline (from the Linux processing box)

Context that lives in the off-rig session's memory, summarized so you have it:
- The actual multi-view calibration (turning calibration videos into
  `calibration.toml`) ran on a separate Linux processing box, not the rig, via
  `sleap-anipose`. The reliable path was **full videos, all cameras** — a
  `sleap-anipose` wrapper script kept on that box — giving sub-pixel reprojection
  error (0.37 px on this rig, 0.063 px on the other, 2026-05-28). The GUI's
  "Solve" button / `1_calibrate.py` subsample path **degrades** the solve relative
  to that, so where the best possible calibration matters, solve from full videos.
- Known trap there: `slap.calibrate` globs `camN/*/*calibration.mp4` and
  `calibration_images/` sorts before `full/`, so a stale `calibration_images/`
  silently shadows the real video. Not relevant to the GUI itself, but explains
  why this HUD (ensuring good coverage *before* solving) is worth having.
- Boards: 3dpose ChArUco 8×8 / 15 mm / 10 mm; 3dface 5×5 / 0.5 mm / 0.4 mm; both
  ArUco 4×4_1000, marker_bits 4, dict_size 1000.
