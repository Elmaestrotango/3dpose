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
  **`1_calibrate.py` pins `opencv-contrib-python>=4.7`** because of this: it is a
  PEP 723 script, so uv re-resolves it freely, and OpenCV moved
  `CharucoBoard.chessboardCorners` (attribute) to `getChessboardCorners()`
  (method) across the 4.6/4.7 line. A `>=4.6` floor let uv land on either side —
  4.6 crashed on the accessor (seen 2026-07-27) and, worse, made the
  `hasattr(board, "setLegacyPattern")` guard no-op silently. Both call sites now
  go through `_apply_legacy_pattern()`, which raises rather than skip, and the
  accessor handles both spellings.
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

### Per-frame stimulus trace

`gui_app/stim_trace.py` writes **`stim_trace.csv`** beside the videos at stop
(`_write_stim_trace`), one row per recorded frame: `frame, blockid, t_s,
any_active`, per-chain step/active/freq/pw, and a modelled `pin<N>_ttl`.
`3_stim_trace.py` regenerates it for older recordings (`--all` for a whole tree).

The mapping is exact because one Arduino drives both: the sketch sets
`FRAME_START` and calls `initStim()` microseconds apart, so stim t=0 *is* trigger
t=0, no host clock involved. Two things that must not be simplified away:
- **`t = (unwrapped_blockid - 1) / fps`, never frame index.** Cameras drop frames
  independently, so frame *i* is not trigger *i*; using the index silently drifts
  the whole trace after the first gap.
- **Block IDs wrap at 65535** (~11 min at 100 fps) unless the camera negotiated
  64-bit IDs — reuse `alignment._unwrap_blockids`.

**It is derived, not observed.** It says what the paradigm should have delivered
given the uploaded firmware; it cannot know the laser was keyed on, the interlock
in, or the beam unblocked. For a real witness put the laser's sync LED in a
camera's field of view — but note that at 100 fps with a ~2 ms exposure you
resolve block envelopes, not individual pulses (a 20 Hz train aliases). Per-pulse
ground truth needs a photodiode on a spare Arduino input.

### Laser safety and the reset window (RESOLVED on the rig 2026-07-27)

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

**What replaced it (2026-07-27): one long-lived connection + an RDY ack.
Verified flash-free on the rig.** The reset isn't defeated, it's *relocated*.
`main_window` owns a single `TeensyController` (`_teensy_connection()`), claimed
at startup by `_warm_serial()` and held until quit — `_stop_acquisition` no
longer closes it. So the board resets at GUI launch and on Apply (arduino-cli
must reset to reach the bootloader), **never at the start of a recording**.

Both of those must stay eager. Opening the port lazily is not enough: first use
*is* the first Record, so the flash merely moves to recording #1 (observed
2026-07-27). Likewise Apply calls `release_serial_port()` for arduino-cli and
must reclaim the port in `_on_upload_done` — otherwise the next Record reopens it
and the flash comes back. The stim editor's Test borrows the same link rather
than opening its own.

**A pulldown resistor turned out not to be needed**, and would not have worked
anyway (see below). The remaining flashes at launch and Apply are outside the
experiment and accepted. Fit the interlock if you want a hard gate.

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

**Measured on the rig 2026-07-27: a pulldown does NOT work here — don't retry
it.** The CNI PSU-III's MOD input has an internal pullup far stronger than
6.8 kΩ. Shorting MOD to ground kills the beam, but 6.8 kΩ across it does nothing.
Beating a pullup that stiff needs a resistor low enough that the Arduino would
exceed its 20 mA per-pin limit driving high (220 Ω ≈ 23 mA), so there is no safe
value. The software fix above is what actually solved it.

If a hard gate is ever wanted, the options are the PSU's **interlock** (pulling
it stops the laser) or a normally-closed relay across MOD/GND held open by a
dedicated pin with a 10 kΩ gate pulldown — that resistor works because a MOSFET
gate really is high-impedance, unlike this MOD input.

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
  conflicts, end/test durations, generated-sketch structure, the RDY ack, and the
  per-frame trace (loop arithmetic, block-ID mapping, 16-bit unwrap). Tests 1-9
  run on a bare python; 10-13 need numpy and skip without it, so prefer
  `uv run python test_stim_compiler.py`. **Run after touching `stim_compiler.py`
  or `stim_trace.py`.**
- `python test_serial_handshake.py` — the four trigger-board handshake outcomes
  (confirmed / retry-after-reset / legacy firmware / regression). Stubs pyserial,
  so it needs no COM port. **Run this after touching `serial_controller.py`** —
  it is the guard against silently recording zero frames.
- `python test_frame_sync.py` — kick-out coordinator == post-hoc intersection.
- `python test_sync_router.py` — encoder router smoke test (needs NVENC).

## Conventions
- New OpenCV dependency: `opencv-contrib-python` (run `uv sync`). The coverage
  HUD self-disables if OpenCV is missing, so the GUI still runs.
- **Exposure/gain live in the `.pfs`, and the exposure ceiling is ~3.9 ms at 100 fps.**
  In trigger mode the frame-rate timer starts *after* exposure ends, so the minimum
  interval is `exposure + 1/AcquisitionFrameRate`. `_set_trigger_mode()` hardcodes 165
  for BOTH rigs (1/165 = 6.06 ms), so exposure past ~3.94 ms pushes the interval over
  the 10 ms trigger period and every second trigger is skipped — the 50 fps bug. The
  3dface pfs says `AcquisitionFrameRate 1000000`, but code overrides it, so the same
  ceiling applies there. Code never sets ExposureTime or Gain; the pfs is the only source.
  - **Raised to 3000 µs + 6 dB (3.0×) from 2000 µs / 0 dB on 2026-08-11**, both rigs.
    The old values put 65% of pixels in levels 0–15 with **21.5% clipped at exactly 0** —
    destroyed at the ADC, unrecoverable by brightening in a player, and crushed further
    by H.264 at qp21. Rig-confirmed a clear improvement on 3dpose; **3dface propagated
    but NOT yet validated** (different camera, optics and lighting — check before a real
    session). Modelled on a real frame, 3.0× lands ~4% saturated vs 1.43% already, while
    **7× would clip 12.7%** — don't just crank it. Prefer more IR illumination (real
    photons, better SNR) over gain, then exposure, then gain.
  - Verify any change against a **recording, not the preview**: preview is free-run at
    30 fps (33 ms headroom), so an over-long exposure looks fine there and only halves
    the frame rate once triggered.
  - **`trigger_rate_limit` (profile field, 2026-08-11).** `0` disables
    `AcquisitionFrameRateEnable` in trigger mode, removing the `exposure + 1/rate` floor
    and leaving only sensor readout. Both 3dpose profiles are `0` with exposure at
    **3500 µs**; both 3dface profiles stay at `165` until tested there. The two 3dpose
    profiles share one pfs, so they must agree — at 165 with 3.5 ms exposure the margin
    is only 0.44 ms.
- **`kick_max_lag: 480` on the `3dpose` profile (raised from 240, 2026-08-11).** One
  camera — **which one varies per session** — drifts to the cap and oscillates across it,
  force-dropping frames every camera captured: 12.3% on 2026-08-11 with cam5 at median
  238 / peak 330, having been cam1 two weeks earlier. **The July note blaming the cams
  1/4/6 packet loss was wrong**: that resend split persists (1/4/6 ~3800 requests vs
  2/3/5 ~3) but the 2026-08-11 laggard was cam5, from the *light* group, while cam1 sat
  at median 0. Resends are not the mechanism. The frames arrive intact, only late, so
  headroom is a real fix rather than masking. Ring RAM 10.5 → 15.4 GB of 64. **Do not go
  to 1000** — that starved capture outright (24% loss, 2026-06-17).
- Video encoding is H.264 via `h264_nvenc` (GPU). Keep `yuv420p` for compatibility.
- **Every mp4 the rig writes needs `-g <fps>` AND `-movflags +faststart`** — both exist so
  the recordings load in the browser labeler (LUC3D), and both are easy to drop when adding
  an encode path. Verified against LUC3D's loader (`loading/video.js`):
  - **`-g <fps>`** (1 IDR/s, set across all paths 2026-07-24). Without an explicit `-g`,
    NVENC's default GOP length depends on the ffmpeg build and driver — the bundled
    `imageio-ffmpeg` emitted **ONE IDR for a whole 898 s / 415 MB recording**, so showing
    frame N cost a decode of all N frames and `ffprobe` could not walk the file in 10 min.
    1 IDR/s also matches LUC3D's own assumption (`kfInterval = Math.round(fps)`).
  - **`-movflags +faststart`** (moov atom to the front). LUC3D appends the file in 1 MB
    pieces from byte 0 and stops when moov parses, so moov-at-end forces a read of the
    ENTIRE file per camera (×6) before frame 1 appears.
  - `-qp` is constant-quantizer, so the extra IDRs cost almost nothing (measured +11%).
    Do NOT reach for `-preset superfast` to speed loading: it changes neither the GOP nor
    the atom order and inflates files ~64%, i.e. more bytes for the browser to pull.
  - Applies to the three mp4 writers — `encode_worker._cmd` (both branches),
    `acquire._encode_raw`, `alignment.extract_aligned` (that one REPLACES the session
    recording). Not to `_append_raw_tail` or `sync_encode`, which emit Annex-B `.h264`
    elementary streams that the stream-copy remux later wraps.
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
  - **Stall recovery + lag attribution (2026-07-27).** A GigE stream stall used to
    end the session for that camera: the grab loop timed out forever, its frontier
    froze, and the coordinator force-dropped every later trigger — so the whole
    recording yielded nothing from the stall onward. Now `grab_thread` re-arms
    (`StopGrabbing`/`StartGrabbing`) after 25 consecutive timeouts, up to 5 times.
    **`StartGrabbing` restarts the camera's block-ID counter**, which the
    coordinator's 16-bit unwrap would misread as a wrap and place the camera far
    *ahead*; `_resync_offset()` recovers the true ordinal from the device
    timestamp (a free-running hardware clock that survives the restart) and
    **refuses if the gap isn't within 0.25 of a trigger period** — publishing
    frames under a guessed ordinal is worse than losing the camera. If it can't
    realign, `FrameSyncCoordinator.retire()` drops that camera from the alignment
    set so the survivors keep recording aligned instead of everyone starving.
    The router logs `lag_behind_leader[...] forced_by[...]` every ~5 s, which is
    what identifies the camera causing forced drops.
  - **MEASURED 2026-07-27 (44 min, 265,586 triggers, 6.34% loss — a 23-min run
    earlier the same day lost 43%): the cameras split into two network groups.**

    | group | resend requests | resend packets | failed buffers |
    |---|---|---|---|
    | cams 2, 3, 5 | ~313 | ~57,000 | ~170 |
    | cams **1, 4, 6** | **~460,000** | **~1,325,000** | ~600 |

    Same driver and socket buffer on all six (`SocketDriver`, 262144 KB), so this
    is the physical path, not pylon config — those are the two switches / NIC
    ports. **cam1 is the worst of the bad group**: median lag 235 against
    `max_lag=240`, above 200 in 75% of reports, i.e. permanently ~2.3 s behind
    and riding the cap, so anything that tips it over force-drops frames every
    camera captured. cam1 is also the camera that stalled outright earlier that
    day. **Next step is physical: the switch/NIC port carrying cams 1/4/6, cam1's
    cable specifically, and MTU 9014 + max Receive Buffers on that leg.** Raising
    `kick_max_lag` would only paper over it (ring RAM is `max_lag+264` NV12
    buffers/cam ≈ 10.4 GB at 240, 15.4 GB at 480).
- `3dpose (raw)` profile (`realtime_encode: false`) = the proven raw.bin + post-hoc
  NVENC fallback (no GPU encode during capture).
- Blocking camera ops (open/close/reconfigure) run off the Qt main thread via
  `gui_app/ui_workers.py` `CallableWorker` (else the window goes "not responding").
  Quitting mid-session abandons + deletes the incomplete data (`_abandon_and_cleanup`).
- Calibration vs recording are distinct acquisitions: calibration uses
  `calibration_frame_rate` (default 30) + 1:1 preview; recording uses `frame_rate`
  (100) + decimated preview.
