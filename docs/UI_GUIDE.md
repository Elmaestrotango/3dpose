# The Panopticon interface

A tour of every control, for someone sitting at the rig for the first time.

![Annotated Panopticon interface](images/ui_annotated.png)

> Regenerate this image after any UI change with `uv run python docs/make_ui_guide.py`.
> It launches the real application and reads the positions from Qt's own widget
> geometry, so the numbers cannot drift out of sync with the layout.

---

## Preview

**1 — Camera preview grid.**
Live video from every camera, refreshed about 30 times a second. It is
deliberately cheap: during a **recording** only every 10th frame reaches the
preview, and during **calibration** every frame does. The preview never has
priority over capture, so it is a monitor, not a measurement.

**2 — One camera pane.**
One per camera, named `cam1`…`camN` **by serial-number order** — not by physical
position, and not by which switch port they are on. That ordering is what the
calibration extrinsics attach to, which is why the software refuses to start if a
camera fails to appear rather than silently renaming the rest.
**Double-click a pane to zoom to that camera alone; double-click again to go back.**

**3 — Live frame rate.**
Per camera, measured in the grab thread. It should sit at the trigger rate — 100
fps for a recording, 30 fps for calibration. A single pane reading low is usually
the first visible sign of a problem, and worth stopping for.

---

## Metadata

**4 — Rig profile.**
Selects the entire hardware configuration: resolution, camera settings file
(`.pfs`), calibration board, trigger pins, exposure, encode mode. Changing it
closes and reopens the cameras. The choice is remembered per machine, because the
profile list is shared between rigs and alphabetical order would pick the wrong
one here.

**5 — Output directory.**
Root folder for all sessions. Click to change. Data is written to
`<output>/<date>/<mouse1>_<mouse2>/<calibration|recording>/`.

**6 — Session metadata.**
Date, subject IDs, assay, experimenter, cohort, cage, notes. Saved to
`session_metadata.json` beside the videos, together with the host, GPU and
**driver version** — so a session that behaves differently after a driver update
can still be explained afterwards. The date and subject IDs also build the folder
path and the video filenames, so fill them in before recording rather than after.

---

## Acquisition

**7 — Calibrate.**
Starts and stops a calibration acquisition at 30 fps. Move the ChArUco board
slowly through the arena so that every camera sees it, every *pair* of cameras
sees it together, and it reaches all four quadrants of each view. Watch **12** to
know when you are done.

**8 — Record.**
Starts and stops an experiment recording at 100 fps. Before it starts, Panopticon
checks RAM, available NVENC encode sessions, free disk and the camera count
against what this profile actually needs, and **refuses with a reason** rather
than half-recording. If a stim paradigm is loaded, it starts from t = 0 with the
recording.

**9 — Solve.**
Runs the calibration solve over the recorded calibration videos — 4 to 5 minutes,
and the button is disabled while it works. Produces `calibration.toml`, which is
copied into the recording folder so each session carries the calibration it was
shot with.

**10 — Snapshot.**
Saves one full-resolution still from every camera into
`snapshots/<date>_<time>/`. Useful for documenting the arena setup, and for
checking focus and exposure without starting a recording.

**11 — Stimulation.**
Opens the optostim node editor. **The important thing to understand:** a stim
sequence is *compiled into the Arduino's firmware*, not streamed over the wire.
Editing the canvas changes nothing until you press **Apply** (~30 s). Because
firmware survives closing the app, Panopticon reflashes a **recording-only
sketch at every launch** — so stim is opt-in per session. To reuse a paradigm,
**Load** it in the editor and Apply.

**12 — Calibration coverage HUD.**
Appears while calibrating. Each numbered node is a camera; it pulses when that
camera can see the board right now. Each edge thickens and brightens as that
*pair* of cameras accumulates shared detections. The readout underneath shows
elapsed time, co-detections against the target, and spatial grid coverage.

The graph turns solid white — **READY** — only when three things are true: the
graph is connected, every camera has enough co-detections, and every camera has
seen the board in at least 3 of 4 quadrants of its field of view. That last
condition exists because you can otherwise satisfy the first two by waving the
board in one spot, which produces a calibration that looks fine and behaves badly
at the frame edges.

---

## Display

**13 — Brightness.  14 — Contrast.**
**Preview only.** These do not affect recorded video, and they do not affect
board detection — the coverage HUD reads raw frames and the solve reads the
recorded file. If the board is too dark to detect, raise
`calibration_exposure_us` in the profile instead; calibration runs at 30 fps,
where the exposure ceiling is about 24 ms rather than the ~3.5 ms a 100 fps
recording allows.

**15 — Progress.**
Encoding and alignment progress after a recording stops. With real-time encode
this is a fast remux; in the raw fallback it is a full encode pass.

**16 — State.**
`IDLE`, `CALIBRATING`, `RECORDING`, `ENCODING`, `ALIGNING`.

**17 — Status bar / capture health.**
During a recording this reports **how far behind real time the worst camera is**.
`Capture healthy — keeping up with the trigger (max lag N ms)` is what you want,
and the number is shown even when healthy so the claim can be checked.

This readout exists because the failure it catches is otherwise invisible. A grab
loop a fraction of a millisecond over budget loses nothing at first — the driver
buffer pool absorbs it — so there is no error and no dropped frame for up to ten
minutes, while every frame retrieved gets staler. If it says the capture is
falling behind, close whatever else is using the machine.

---

## A first session, in order

1. Pick the **profile** (4) and set the **output directory** (5).
2. Fill in the **metadata** (6) — it names the folders.
3. **Calibrate** (7): wave the board until the HUD (12) reads READY.
4. Stop the calibration, then press **Solve** (9) and wait for `calibration.toml`.
5. If using optostim, open **Stimulation** (11), build or Load a paradigm, **Apply**.
6. **Record** (8). Watch the status bar (17) and the per-camera fps (3).
7. Stop. Encoding runs automatically (15); the session is complete at `IDLE` (16).

If anything went wrong, you get a dialog **and** a `WARNINGS.txt` in the session
folder. A clean session produces neither.
