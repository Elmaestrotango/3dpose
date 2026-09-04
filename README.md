<h1 align="center">
  <img src="panopticon.ico" width="72" height="72" alt=""><br>
  Panopticon
</h1>

<p align="center"><b>Multi-camera hardware-synchronised video acquisition for 3D animal pose estimation.</b></p>

[![The Panopticon interface](docs/images/ui_annotated.png)](docs/OVERVIEW.md)

Reconstructing an animal's pose in three dimensions means fusing several views of the
same instant, so the hard requirement on the acquisition side is not resolution or frame
rate — it is that frame *i* of one camera really is frame *i* of all the others.
Panopticon exists to make that guarantee hold from the sensor all the way to the files on
disk.

It runs N cameras off a single trigger clock, so every camera opens its shutter on the
same electrical edge instead of on N independent internal timers. It encodes every frame
to H.264 on the GPU while capture is still running, so there is no enormous intermediate
file and no post-capture encode pass to wait through. And it writes the videos out
already frame-aligned, because alignment after the fact is both slow and easy to get
subtly wrong.

That last guarantee needs a coordinator, because cameras on a network drop frames
independently of one another — a single lost packet can cost one camera a frame that
every other camera recorded perfectly. Panopticon therefore tags each frame with the
trigger ordinal it came from, taken from the camera's GigE *block ID* (the counter a
GigE Vision camera increments once per frame it acquires), and a shared coordinator
releases a trigger to the encoders only once every camera has captured it. Triggers that
any camera missed are dropped before encoding rather than patched up afterwards. The
per-camera videos therefore come out equal in length and aligned trigger-for-trigger,
with no post-hoc pass.

The same application also covers the two jobs that usually live in separate tools. It
records the ChArUco calibration — a chessboard with a unique ArUco marker printed inside
each white square, so even a partial view of the board is identifiable — and solves it.
And it can compile an optogenetic stimulation paradigm into the firmware of the very
board that drives the camera triggers, which is what lets the stimulus and the frames
share one clock.

The output loads directly in **[LUC3D](https://talmolab.github.io/luc3d/)**, a
browser-based multi-view pose annotation tool by Eric Leonardis (Salk Institute), hosted
by the Talmo Lab
([repo](https://github.com/talmolab/luc3d) ·
[docs](https://talmolab.github.io/luc3d-docs/)).
LUC3D takes browser-playable video plus TOML or JSON calibration, which is exactly what a
session directory already holds. Two details of the mp4s exist for its benefit: the moov
atom (the index a player needs before it can seek) is written at the front of the file
rather than the end, and there is one IDR frame — a keyframe a decoder can start cold
from — every second, so scrubbing to frame N does not mean decoding the N frames before
it. Alongside those sits `calibration.toml`, which LUC3D reads as-is.

Built on **[campy](https://github.com/ksseverson57/campy)** by Kyle Severson. A fork is
vendored here as a git submodule; the trigger firmware lineage and the raw-capture
approach come from campy.

**Credits:** Isaac Tang (author and maintainer), Kay Tye, Talmo Pereira. Tye Lab and
Talmo Lab, Salk Institute.

---

## Where to go next

| Page | What is in it |
|---|---|
| **[docs/INSTALLATION.md](docs/INSTALLATION.md)** | Get it running: what to install, how to size the hardware for the rig you want, and what a working first launch looks like. |
| **[docs/OVERVIEW.md](docs/OVERVIEW.md)** | What every control does, screen by screen, including the calibration coverage display and the stimulation editor. |
| **[docs/WORKFLOW.md](docs/WORKFLOW.md)** | A session from start to finish: calibrate, solve, record, and check the result. |
| **[docs/INTERNALS.md](docs/INTERNALS.md)** | How it works underneath: the grab loop, GPU encoding, frame alignment, tuning and porting. |

---

## At a glance

**What it does.** All N cameras are triggered from one board, so every camera exposes on
the same edge. Each frame is encoded to H.264 on the GPU as it arrives, using one NVENC
session per camera — NVENC being the dedicated video encoder built into NVIDIA GPUs —
which is what removes both the large intermediate file and the post-capture encode pass.
Every frame is tagged with the trigger ordinal it came from, and only the triggers that
all cameras caught survive into the videos.

Calibration is part of the same application rather than a separate step in another tool.
Panopticon records a ChArUco calibration and solves it in-repo with `1_calibrate.py`,
which is built on OpenCV: ArUco/ChArUco detection to find the board corners in each
frame, `cv2.calibrateCamera` for each camera's intrinsics, `cv2.stereoCalibrate` for each
camera pair, and then the pairs chained into a single coordinate frame. The result is
written as `calibration.toml` in aniposelib's layout, so LUC3D and downstream
triangulation — aniposelib itself, or
[sleap-anipose](https://github.com/talmolab/sleap-anipose) — read it directly with no
conversion step in between.

Optionally, Panopticon also compiles an optogenetic paradigm into the trigger board's
firmware and writes a per-frame record of what that paradigm delivered.

**What it needs.** Four things are non-negotiable: Basler cameras (driven through
pypylon), an NVIDIA GPU that can grant one concurrent NVENC encode session per camera,
Windows, and an Arduino- or Teensy-class board on a serial port.

Everything else scales with pixel rate rather than with any particular part number. One
1920x1200 mono8 camera at 100 fps produces about 1.84 Gbit/s, so three cameras on one
port already needs a 10 GbE port, while a lower frame rate or resolution can fit
comfortably on 1 GbE. `docs/INSTALLATION.md` gives the arithmetic for CPU, RAM, disk and
network, so a rig can be sized from what it is meant to record rather than from a shopping
list. Panopticon also screens the machine at launch and re-checks capacity against the
actual number of cameras before each acquisition, and it refuses to start rather than
half-record a session.

If no usable NVENC encoder is available, it falls back to writing raw frames straight to
disk and encoding after the session — and that fallback needs to be budgeted for, because
raw means the full 1920x1200 bytes of every frame from every camera. One camera at 100 fps
writes about **129 GiB of raw per ten minutes**, so six cameras for ten minutes is about
**830 GB (773 GiB)**, against roughly 1.7 GB for the same recording in H.264. That is a
factor of about **500**, not a rounding error, and it is the number to size drives on.

**What it outputs.** Each camera gets an mp4 (H.264, `yuv420p`, `+faststart`, one IDR per
second) named `<date>-<session>-<cam>-<recording|calibration>.mp4`, accompanied by
`frametimes.npy` and `blockids.npy` — the latter holding each frame's trigger ordinal, so
a frame can always be traced back to the trigger that produced it.

At the session level there is `session_metadata.json`, and from the calibration solve two
files: `calibration.toml` and `reprojection_error_histogram.png`. The `calibration.toml`
is also copied next to the recording, so a recording always carries the calibration it was
shot with. The `.png`, despite its name, is a bar chart with one bar per camera pair
showing that pair's stereo RMS error in pixels, and it is how you judge whether the solve
is good enough to use — `docs/WORKFLOW.md` explains how to read it. Note that the board's
physical geometry is an *input*, not an output: it lives in the board config the profile
points at (`configs/boards/*.yaml`), and the solve reads its square and marker sizes from
there.

A session that used stimulation adds three more files: `stim_paradigm.json`,
`stim_paradigm.ino` (the exact firmware that ran) and `stim_trace.csv` (one row per
recorded frame).

---

## Definitions and hyperparameters

This system borrows vocabulary from machine vision, GigE networking, video encoding and
camera calibration, and it has a fair number of tunable numbers spread across several
files. This section is the one place to look them up.

**Where a setting lives matters as much as its value**, and this is the single most
common source of confusion. There are four separate homes, and changing the wrong one
does nothing at all:

1. **The camera settings file** (`configs/*.pfs`) is a *pylon persistence file* — a dump
   of the camera's own internal registers, produced by Basler's pylon Viewer application
   rather than written by hand. Panopticon applies it to every camera at open. Crucially,
   **the exposure and gain a recording uses come only from here**, so changing exposure
   means editing the `.pfs` (or re-saving it from pylon Viewer), not the profile. At the
   start of each acquisition Panopticon reads that baseline back and re-applies it —
   which is how a calibration-specific exposure is prevented from leaking into a
   recording — and it will *clamp* the value down if the `.pfs` asks for an exposure the
   frame rate cannot accommodate. So the `.pfs` is the only source of the number, but the
   number actually applied may be lower; the log line at acquisition start says which.
2. **The rig profile** (`profiles/*.yaml`) holds Panopticon's own settings: how many
   cameras, what frame rate, which serial port, which pins. This is the file a new site
   edits, and the one place where "this rig" is described.
3. **The board config** (`configs/boards/*.yaml`) describes the physical printed
   calibration board.
4. **Code constants** are compiled in and change only by editing the source. They are
   listed here because a few of them are worth knowing about when something goes wrong.

Values shown below are the ones on the reference 3dpose rig. Where a number is a
program-wide default rather than a rig choice, it says so.

### Terms

| Term | What it means |
|---|---|
| **Trigger** | A single electrical pulse that makes every camera expose at once. The unit of synchronisation in this system: "trigger 500" names one instant across all cameras. |
| **Trigger board** | The Arduino- or Teensy-class microcontroller that generates those pulses. It also runs the stimulation paradigm, which is why stimulus and frames share one clock. |
| **Block ID** | A counter a GigE Vision camera increments once per frame **it acquires**, carried with every frame. Panopticon treats it as the trigger ordinal, which is what makes cross-camera alignment possible. |
| **Trigger ordinal** | Which trigger, counting from the start of the acquisition, a frame belongs to. Stored per frame in `blockids.npy`. |
| **Free-run vs triggered** | In free-run the camera paces itself from an internal timer; when triggered it exposes only on an external edge. The preview is free-run; recording and calibration are triggered. Several settings behave differently between the two. |
| **Frame kick-out** | Discarding a trigger that not every camera captured *before* it reaches the encoders, so the videos are aligned as written instead of being repaired afterwards. |
| **The coordinator** | The component that does this (`gui_app/frame_sync.py`). It holds each camera's frames briefly and releases a trigger only once every camera has it. |
| **Forced drop** | What happens when one camera falls further behind than `kick_max_lag` allows: the coordinator stops waiting and discards that trigger, including the frames other cameras captured perfectly well, so everyone stays aligned. The `forced=` figure in the log counts *frames* discarded this way, not triggers. A healthy session has none. |
| **Retirement** | Dropping a camera from the alignment set entirely, so the survivors keep recording in alignment with each other and the retired camera's video simply ends early. Without it, one dead camera would force-drop every trigger for everyone. Triggered by an unrecoverable stall, a failure to start the stream, no memory for the frame ring, or a camera reporting row padding. |
| **Laggard** | Whichever camera is furthest behind in submitting frames. Reported in the log so a persistent one can be identified. |
| **Exposure ceiling** | The longest exposure usable at a given frame rate. In triggered mode the camera's rate timer starts *after* exposure ends, so the minimum interval is `exposure + 1/AcquisitionFrameRate`. Exceed it and the camera ignores alternate triggers. See the `.pfs` table. |
| **NVENC** | The dedicated hardware video encoder built into NVIDIA GPUs. It encodes without using the CPU or the shader cores. |
| **NVENC session** | One concurrent encode stream on that hardware. Panopticon needs one per camera. The driver caps how many exist at once, so the cap is a real constraint on camera count. |
| **NV12** | The pixel layout NVENC wants: a full-resolution brightness plane followed by a half-resolution colour plane. A mono8 frame becomes NV12 by using it as the brightness plane and filling colour with a constant. |
| **IDR frame / GOP** | An IDR is a keyframe a decoder can start from cold; the GOP is the interval between them. Panopticon writes one IDR per second so seeking to frame *N* does not require decoding the *N* frames before it. |
| **moov atom / faststart** | The moov atom is an mp4's index. Written at the end of the file by default, which forces a player to read the whole file before it can start; `+faststart` moves it to the front. |
| **Remux** | Rewriting a video's container without re-encoding the pictures (`ffmpeg -c copy`). Lossless and fast. Panopticon captures to a raw H.264 stream and remuxes to mp4 at stop. |
| **GVSP** | GigE Vision Streaming Protocol — the UDP protocol cameras use to send frames. One frame is split across many packets. |
| **Jumbo frames** | Ethernet packets larger than the usual 1500 bytes. Panopticon uses 9000, which cuts per-packet overhead substantially, but **every device in the path must be configured for it** or frames are silently lost. |
| **Inter-packet delay** | A deliberate pause the camera inserts between packets, spreading one frame's burst over time so switch buffers do not overflow. `GevSCPD` in the `.pfs`. |
| **Resend** | GVSP is UDP, so there is no automatic retransmission; the driver explicitly asks for packets that did not arrive. Resends are normal and recovered ones cost nothing but latency. |
| **Buffer underrun** | The camera had a frame ready but no free buffer to put it in, so the frame was lost at the camera. Means the host is not draining fast enough. |
| **Failed buffer** | A frame that arrived incomplete — packets lost and not recovered in time. Means the network path is lossy. |
| **ChArUco** | A chessboard with a unique ArUco marker printed inside each white square, so a partial or rotated view is still unambiguously identifiable. Used for calibration. |
| **Intrinsics** | One camera's own optical properties: focal length, optical centre, lens distortion. Independent of where the camera is. |
| **Extrinsics** | Where a camera sits and points relative to a chosen reference camera. Rotation and translation. |
| **Reprojection error** | Take a known 3D board point, project it through a camera's solved intrinsics and extrinsics, and measure how far the result lands from where the corner was actually detected. In pixels. The basic measure of whether a calibration is right. |
| **Stereo RMS** | The root-mean-square reprojection error for one *pair* of cameras. What the bars in `reprojection_error_histogram.png` show. |
| **Reference camera** | The camera whose coordinate frame becomes the world frame. `--ref-camera`, default `cam1`. |
| **Spanning tree** | Pairwise calibrations are chained into one coordinate frame by walking a tree of camera pairs, preferring low-error pairs. There is **no global bundle adjustment**, so a poor pair on the chosen path propagates. |
| **Paradigm** | A stimulation protocol built in the node editor: which pins fire, at what frequency and pulse width, in what order. |
| **Chain** | One connected sequence of stimulation blocks. Independent chains run concurrently, so two chains driving one pin conflict and are refused. |
| **Safe pin** | A pin forced LOW at boot before the serial handshake, so a powered laser driver never reads a floating pin as ON. `stim_safe_pins`. |
| **The raw fallback** | Writing full uncompressed frames to disk during capture and encoding after the session, used when real-time GPU encoding is unavailable. Needs roughly 500x the disk. |
| **LUC3D** | The browser-based multi-view annotation tool this software's output is designed to load into, without conversion. |

### Rig profile — `profiles/3dpose.yaml`

Panopticon's own settings. This is the file to copy and edit for a new rig.

| Field | This rig | What it controls | If you get it wrong |
|---|---|---|---|
| `name` | `3dpose` | Label in the profile dropdown | Cosmetic |
| `frame_width` / `frame_height` | 1920 / 1200 | Expected frame geometry | Must match the `.pfs`, because every buffer size downstream is computed from it. This is *not* checked against the camera at open — a disagreement instead throws on the first frame and retires the camera, wasting the session. Cameras are checked against *each other*, so a mixed-resolution rig is refused |
| `frame_rate` | 100 | Trigger rate for recordings, Hz | Sets the exposure ceiling. Raising it without shortening exposure makes cameras ignore alternate triggers |
| `calibration_frame_rate` | 30 | Trigger rate while calibrating | A slowly-waved board gains nothing from 100 fps, and 30 buys roughly 7x the light budget |
| `quality` | 21 | H.264 constant quantiser (`qp`) | Lower is better quality and larger files. Not a bitrate — file size varies with scene content |
| `encode_parallel` | 3 | Concurrent NVENC encode jobs in raw mode; concurrent remux jobs in real-time mode | Too low makes the post-session pass slow. Too high matters mainly in raw mode, where the jobs really do consume NVENC sessions — real-time remuxes are stream copies and use none, so they only compete if one session's encode overlaps the next recording |
| `realtime_encode` | `true` | GPU-encode during capture rather than writing raw | `false` selects the raw fallback: no GPU encoder needed, ~500x the disk |
| `realtime_kick` | `true` | Align by discarding unanimous-miss triggers during capture | `false` falls back to aligning after the fact, which costs a full re-encode |
| `kick_max_lag` | 480 | How many frames the coordinator will wait for a lagging camera | Sets RAM directly: the frame ring is `kick_max_lag + 200 + 64` buffers per camera, so 744 at 480 against 504 at 240. Too high starves capture outright — **1000 cost 24% of frames.** Too low used to force-drop heavily (an A/B measured 12.34% loss at 240 against 0.88% at 480), but that was before a capture-loop fix removed the drift causing it; observed cross-camera lag is now 0–2 frames, so this headroom currently buys nothing and 240 would probably do. It is kept at 480 pending a fresh comparison rather than because the old figures still hold |
| `n_cameras` | 6 | Cameras that **must** enumerate before a session will start | A safety interlock, not a convenience. Camera names are assigned positionally by serial number, so one camera failing to appear renames every camera after it and silently attaches the calibration to the wrong physical cameras. Set it to the real count |
| `calibration_exposure_us` | 15000 | Exposure during calibration only; `0` leaves the `.pfs` value alone | Restored after calibration, so a long calibration exposure cannot leak into a 100 fps recording. Clamped in code if it would breach the ceiling. The practical limit is **motion blur**, not the ceiling — a briskly waved board smears and its corners stop resolving |
| `calibration_gain_db` | -1 | Gain during calibration only; `-1` leaves the `.pfs` value alone | Prefer more light, then exposure, then gain — each +6 dB doubles noise along with signal |
| `pfs_path` | `configs/mono8_1920x1200.pfs` | Which camera settings file to apply | |
| `output_dir` | `data` | Where sessions are written | |
| `board_config` | `configs/boards/charuco_8x8_15mm.yaml` | Which physical board is in use | Wrong board geometry produces a confident, wrongly-scaled calibration |
| `serial_port` | `COM3` | The trigger board's port | Wrong port means no triggers and a refused start |
| `trigger_pins` | `[2,4,6,8,10,12]` | Board pins wired to the cameras' trigger inputs | A camera on an unlisted pin never fires |
| `gige_driver` | `socket` | Which pylon transport to use | `socket` is user-space with reliable resends and is the proven choice. `filter` is in-kernel and uses less CPU but **silently dropped ~23% of frames** with default resend settings |
| `stim_safe_pins` | `[53]` | Pins driven LOW at boot before the serial handshake | Pin 53 is the laser. Omitting it leaves the pin floating through boot, which a powered driver reads as ON. Workflow pins are added automatically; this list is the floor |
| `trigger_rate_limit` | 165 | Value written to `AcquisitionFrameRate` in triggered mode | **Do not set 0.** Disabling the limiter does remove the exposure ceiling, but it was tried and reverted the same day: delivery fell to 85–92% from 99.98%. The limiter paces each frame's readout across 6.06 ms; without it every camera bursts at once after the shared trigger and marginal links drop packets |

### Camera registers — `configs/mono8_1920x1200.pfs`

**These live in the `.pfs` and nowhere else** — no Panopticon config overrides them. Edit
the file in pylon Viewer and re-save, or change the values in the text file directly; it
is a plain tab-separated list. Exposure and gain are re-applied from this baseline at the
start of every acquisition, and clamped if they exceed the ceiling for the frame rate in
use, so check the `[cam1] exposure=… gain=…` line in the log to see what was really set.

| Register | This rig | Notes |
|---|---|---|
| `Width` / `Height` | 1920 / 1200 | Region of the sensor read out. Must agree with the profile |
| `OffsetX` / `OffsetY` | 8 / 8 | Where that region starts |
| `PixelFormat` | `Mono8` | 8-bit greyscale, one byte per pixel. Anything else is refused at open |
| `ExposureTime` | 3000.0 µs | **Subject to the exposure ceiling below.** Raised from 2000 µs, because the old value left 65% of pixels in levels 0–15 with 21.5% clipped at exactly 0 — destroyed at the sensor and unrecoverable by brightening later |
| `Gain` | 6.000 dB | About 2x. Raised from 0 dB at the same time |
| `GainAuto` | `Off` | Must stay off. Per-camera automatic gain destroys photometric consistency between views |
| `AcquisitionFrameRate` | 165.0 | The internal rate limiter. Written from `trigger_rate_limit` |
| `AcquisitionFrameRateEnable` | 1 | Limiter active |
| `TriggerMode` (FrameStart) | `On` | Each frame waits for an external edge |
| `TriggerSource` (FrameStart) | `Line1` | That edge arrives on hardware line 1, from the trigger board |
| `GevSCPSPacketSize` | 9000 | Jumbo frames. **The NIC and every switch in the path must also be set to 9000** |
| `GevSCPD` | 10000 | Inter-packet delay in device ticks — paces packets so switch buffers do not overflow |
| `BandwidthReserveMode` | `Standard` | Headroom the camera keeps in reserve for resends |

**The exposure ceiling.** Because the rate timer starts after exposure ends, the usable
exposure is `1/frame_rate − 1/trigger_rate_limit`:

| Frame rate | Trigger period | Theoretical ceiling | Enforced clamp (90%) |
|---|---|---|---|
| 100 fps (recording) | 10.0 ms | 3.94 ms | 3.55 ms |
| 30 fps (calibration) | 33.3 ms | 27.3 ms | 24.5 ms |

At the 3000 µs in use there is 0.94 ms of margin against the theoretical ceiling and
0.55 ms against the clamp, so the `.pfs` value is applied as written. Exceed the ceiling
and the camera
is still busy when the next trigger arrives and **ignores** it, halving its effective rate
— and because an ignored trigger produces no frame, it consumes no block ID, so this does
*not* show up as a gap or as any packet error. Panopticon checks for it explicitly by
comparing each camera's block-ID rate against its own hardware clock; `docs/INTERNALS.md`
covers why that check is necessary. **Verify any exposure change against a real recording,
never the preview** — the preview is free-run at 30 fps and an over-long exposure looks
perfectly fine there.

### Board config — `configs/boards/charuco_8x8_15mm.yaml`

| Field | This board | Notes |
|---|---|---|
| `board_x` / `board_y` | 8 / 8 | Squares across and down |
| `square_length` | 15.0 mm | **Sets the world scale of the entire reconstruction.** Measure the printed board and use the real value — printers scale. Every 3D coordinate downstream is in these units |
| `marker_length` | 10.0 mm | Side of the ArUco marker inside each white square |
| `marker_bits` | 4 | ArUco dictionary bit size (4x4) |
| `dict_size` | 1000 | Dictionary size, i.e. `DICT_4X4_1000` |
| `board_legacy` | `true` | This board was printed with the pre-OpenCV-4.6 ChArUco layout. Without this flag, OpenCV 4.7+ detects **zero corners, silently**. Defaults to false for boards printed since |
| `max_frames` | 500 | Present in the file but **not read** by the current solve, which caps intrinsics frames at its own built-in 60 |

### Code constants

Compiled in; listed so they can be found and so log messages make sense.

| Constant | Value | File | Meaning |
|---|---|---|---|
| `MAX_NUM_BUFFER` | 1000 | `gui_app/camera_manager.py` | Driver buffers queued per camera. Together with `kick_max_lag` this dominates RAM |
| `ENCODE_QUEUE_DEPTH` | 200 | `gui_app/grab_thread.py` | Frames that may queue to one encoder thread |
| `PUT_TIMEOUT_S` | 2.0 | `gui_app/grab_thread.py` | How long a grab thread waits on a full encoder queue before dropping the frame and logging encoder backpressure. Only reached in the decoupled real-time path, not in kick-out mode |
| `BLOCKID_WRAP` | 65535 | `gui_app/frame_sync.py` | 16-bit block IDs wrap here — about 11 minutes at 100 fps. Unwrapped in software; cameras also try to negotiate 64-bit IDs at open |
| `BLOCK_RATE_TOL` | 0.003 | `gui_app/frame_sync.py` | Allowed disagreement between a camera's block-ID rate and its device clock. Set from measurement: real sessions sit within 250 ppm |
| `BLOCK_RATE_MIN_FRAMES` / `_SECONDS` | 300 / 2.0 | `gui_app/frame_sync.py` | Below this the rate check abstains rather than guess |
| `min_per_cam_shared` | 250 | `gui_app/board_detector.py` | Co-detections each camera needs before calibration reports READY |
| `min_edge` | 80 | `gui_app/board_detector.py` | Co-detections that make a camera *pair* count as connected. READY needs the resulting graph to be **one connected component**, not all pairs connected — with 6 cameras 5 good edges suffice, which matters because a one-sided board means cameras facing each other can never co-detect |
| `MIN_GRID_CELLS` | 3 of 4 | `gui_app/board_detector.py` | Quadrants of each camera's view the board must visit, so the board cannot be waved in one spot |
| `optimal_shared` | 200 | `gui_app/board_detector.py` | Where a coverage-graph edge reads as "full" |
| `glow_threshold` / `edge_threshold` | 4 / 5 | `gui_app/board_detector.py` | Markers needed to light a node, and to count an edge |
| `RESERVED_SERIAL_PINS` | 0, 1 | `gui_app/stim_compiler.py` | The board's serial TX/RX. Using them for stimulation is refused, since it would break the link that starts the recording |
| `FQBN` | `arduino:avr:mega` | `gui_app/stim_compiler.py` | The arduino-cli board target |
| NVENC session cap | 12 measured here | GPU driver | Not a constant in this code — it is probed, because the cap has moved across driver versions (2, 3, 5, 8, 12). Budget one per camera, plus `encode_parallel`, plus the warm-up session; the preflight probes for `n_cameras + 2` |

### Solve parameters — `1_calibrate.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--board-config` | required | The board config to use |
| `--ref-camera` | `cam1` | Camera whose frame becomes the world frame |
| `--skip` | 3 | Use every Nth frame. Lower finds more detections and runs slower; 3 is the tested value and 10 visibly degrades the result |
| `--excluded-views` | none | Cameras to leave out of the solve |

Internally a frame is usable if it shows at least 2 markers and 6 ChArUco corners.
Intrinsics use up to 60 pose-diverse frames and need at least 20; when there are more
than 60 to choose from, frames above the 90th percentile of reprojection error against a
rough pinhole guess are dropped before selecting, which is a coarse outlier filter rather
than a quality metric. Each stereo pair needs at least 3 shared frames and runs with
intrinsics held fixed. The pairs are then chained from the
reference camera along a lowest-error spanning tree — **with no global bundle adjustment**,
which is why the pairwise chart is worth reading rather than trusting a single overall
number.
