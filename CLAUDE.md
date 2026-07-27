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
- READY thresholds (`board_detector.py` constructor defaults): `min_per_cam_shared = 100`
  co-visible frames/cam, `min_edge = 40` graph-connectivity, `optimal_shared = 200`
  edge-width max — raised hard on 2026-07-09 (`acac97e`) after 20 proved far too lenient
  and let under-covered cameras produce degenerate intrinsics (fx≠fy, extreme distortion).
  Shared by both rigs and tuned on 3dface; **if the 6-cam 3dpose rig can't reach 100
  co-visible/cam, dial these back** (they're constructor defaults, not yet profile fields).
- `1_calibrate.py` (post-`acac97e`) constrains intrinsics (`CALIB_FIX_ASPECT_RATIO` /
  `FIX_K3` / `ZERO_TANGENT_DIST`), requires ≥20 frames/cam, caps intrinsics+stereo at 30
  frames, and writes `reprojection_error_histogram.png` (matplotlib — a PEP 723 inline dep
  auto-installed by `uv run`; guarded/skipped if absent). The HUD saves `codet_frames.json`
  on calibration stop so the script seeks to co-detection frames instead of scanning full
  videos.

3dface mirror of `gui_app/` is deferred — this rig only runs 3dpose for now.

## Optostim: the Stimulation editor (added 2026-07-26)

Bonsai-style node editor (sidebar → **Stimulation**) that compiles a block graph
into the Arduino sketch. `gui_app/widgets/stimulation_window.py` (canvas + UI),
`gui_app/stim_compiler.py` (graph → `.ino`, pure functions, no Qt).

**The stim board IS the camera-trigger board** — one Arduino Mega 2560 on COM3.
The generated sketch does both: the original TTL trigger protocol plus a
non-blocking stim state machine, with `updateStim()` called inside the trigger's
busy-wait loops.

Non-obvious invariants — break these and the failure is silent or dangerous:
- **`allStimLow()` is the first statement in `setup()`, before `Serial.begin()`.**
  `setup()` blocks on the serial handshake until the GUI connects, so anything
  later leaves the pin floating — which a powered laser driver reads as ON. The
  pins come from the profile's **`stim_safe_pins`** (`[53]` on 3dpose = laser,
  `[]` on 3dface) unioned with whatever the workflow uses. Do NOT hardcode pins
  in `stim_compiler.py`; `gui_app/` is shared with 3dface.
- **The sequence is baked into the `.ino` at compile time**, not sent over
  serial. Editing blocks does nothing until **Apply** (arduino-cli compile +
  upload, ~30 s). Record then just sends the normal start command and the
  paradigm runs from t=0. Test/Record warn when the canvas has drifted from the
  last upload.
- **No floating-point math in `updateStim()`.** Period and pulse width are
  resolved to integer microseconds by the compiler. An AVR float divide is ~30 µs
  and runs inside the trigger busy-wait, blunting the firmware's ±0.35 µs edge
  precision. `test_stim_compiler.py` asserts no floats reach that function.
- **Pulse width ≥ period means constant ON**, not "invalid". Treating it as
  unrepresentable silently held the laser LOW for a whole recording (10 Hz +
  100 ms — an easy arithmetic slip). The bottom-left waveform preview red-glows
  on this and on pw > period.
- **Starts, and loops.** Per weakly-connected group: an explicit "Starting" flag
  wins, else every block with no incoming arrow starts a chain. A pure loop has
  neither, so it must be pinned or it compiles to nothing. `_extract_chains`
  is cycle-safe (revisit closes the loop via `loop_to`); it used to hang.
- **"Ending" stops the recording, not the chain** — a loop keeps running, so
  bound it with a parallel timer chain. Python arms a `QTimer` and flips the
  Record switch; the board is not asked to report back.
- Two chains on one pin fight over the output; `pin_conflicts()` blocks Apply
  and Test. Reusing a pin *within* a chain is fine (sequential).

Every recording writes **`stim_paradigm.json`** (graph, resolved chains, end
time, firmware SHA-256, `matches_uploaded_firmware`) and **`stim_paradigm.ino`**
(exact firmware) into the recording dir, so a session is self-describing.
`matches_uploaded_firmware: null` means nothing was uploaded that GUI session —
the board's contents are unknown, not wrong.

### Laser safety and the reset window (OPEN ITEM as of 2026-07-26)

`allStimLow()` runs first thing in `setup()`, but there is a window it cannot
cover: **during MCU reset and the ~1–2 s bootloader wait every GPIO is high-Z**,
because the sketch is not executing yet. A laser driver on a floating input reads
that as ON and fires a visible flash. Opening the serial port pulses DTR, which
auto-resets the Mega — so this happens once per Record, once per Apply (avrdude
must reset to reach the bootloader), and once at power-up.

**Do NOT fix this by suppressing the DTR reset.** Tried 2026-07-26 (`dtr=False` /
`rts=False` before `open()`): it did remove the flash and it silently broke
recording — the board ignored the config and emitted zero triggers
(`Total_Packet_Count: 0` on all six cameras; 15 s "recorded", empty
`stream.h264`, 0 frames encoded). **The reset is load-bearing**: it returns the
board to `setup()` with a cleared serial RX buffer. A comment in
`serial_controller.open()` marks it so nobody retries.

**What replaced it (2026-07-27): one long-lived connection + an RDY ack.** The
reset isn't defeated, it's *relocated*. `main_window` owns a single
`TeensyController` (`_teensy_connection()`), opened on first use and held until
quit — `_stop_acquisition` no longer closes it. So the board resets at GUI launch
and on Apply (arduino-cli must reset to reach the bootloader), never at the start
of a recording. Apply calls `release_serial_port()` to hand COM3 to arduino-cli
and it reopens on demand; the stim editor's Test borrows the same link rather
than opening its own.

Because a start command can now land in `loop()`'s reconfigure branch instead of
a freshly-reset `setup()`, **every start is confirmed**. The sketch prints
`RDY <n_cams> <fps>` from both config paths (`announceReady()`), and
`start_triggers()` returns a bool:

1. ack on the open connection → proceed, no reset, no flash
2. no ack → close/reopen (forcing a reset) and retry
3. still no ack, and this board has *never* acked → assume pre-RDY firmware
   (stock `trigger.ino`) and proceed; it was just reset, which is exactly the old
   behaviour, so camera-only rigs are unaffected
4. still no ack, but the board *has* acked before → real fault, return False;
   `_start_acquisition` rolls the cameras back and refuses rather than recording
   an empty session

That 3-vs-4 distinction is the whole safety property — conflating them either
locks legacy firmware out of recording or lets the zero-trigger bug through
again. `test_serial_handshake.py` pins all four branches.

The config command is now newline-terminated, which ends the sketch's final
`parseFloat()` immediately instead of burning its 1 s timeout.

Quitting always sends `stop_triggers` if the port is open (not just mid-
acquisition), so closing the GUI can never leave a paradigm or laser running.

**The fix is hardware: a 10 kΩ pulldown from the stim pin to GND** — in parallel
with the driver input, never in series (a bare wire would clamp the pin at 0 V so
it could never fire, and would pull ~200 mA through the output driver and destroy
it). This covers every case software cannot: record reset, Apply reset, power-up,
unplugged cable. Measure pin-to-GND with the board held in reset; if an internal
pullup in the driver holds the divider above ~0.8 V, drop to 2.2 kΩ.

**Measured on the rig 2026-07-27: a pulldown may not be viable here.** The CNI
PSU-III's MOD input has an internal pullup far stronger than 6.8 kΩ — shorting
MOD to ground kills the beam, but 6.8 kΩ across it does nothing. Beating a pullup
that stiff needs a low enough pulldown that the Arduino would exceed its 20 mA
per-pin limit driving high (220 Ω ≈ 23 mA). Get `V_open` and `I_short` on the MOD
input before fitting anything; if `I_short` is more than a few mA, no safe
resistor exists and the options are the PSU's **interlock** (pulling it stops the
laser) or a normally-closed relay across MOD/GND held open by a dedicated pin
with a 10 kΩ gate pulldown — that resistor works because a MOSFET gate really is
high-impedance, unlike this MOD input.

The PSU-III's rear toggles are TTL/Analog and CUR/R1/R2. There is **no TTL−
position**, so inverting the polarity to exploit the pullup is not available.
Stay on TTL: analog mode maps 0–5 V onto output power, which would make stim
power depend on the Arduino's exact rail voltage.

`campy/campy/trigger/trigger.ino` is **superseded** and no longer what the board
runs. It has no `stim_safe_pins` boot guard, so re-flashing it drops the laser
safety and all stim. Kept only for the legacy `acquire.py` path.

## Tests

Plain scripts, no pytest — run directly:
- `python test_stim_compiler.py` — stim graph → sketch. Start resolution,
  cycle-safe chain extraction, integer µs encoding, safe-pin boot order, pin
  conflicts, end/test durations, generated-sketch structure, the RDY ack. Pure
  Python, runs anywhere. **Run this after touching `stim_compiler.py`.**
- `python test_serial_handshake.py` — the four trigger-board handshake outcomes
  (confirmed / retry-after-reset / legacy firmware / regression). Stubs pyserial,
  so it needs no COM port. **Run this after touching `serial_controller.py`** —
  it is the guard against silently recording zero frames.
- `python test_frame_sync.py` — kick-out coordinator == post-hoc intersection.
- `python test_sync_router.py` — encoder router smoke test (needs NVENC).

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
