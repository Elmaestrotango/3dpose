# A session, end to end

This page walks through one session in the order it happens: launch, profile, output
directory, metadata, calibrate, solve, optional stimulation, record, and what the session
leaves on disk. It assumes no computing background. Every path, filename and message
quoted here is the one the software actually produces.

For what each individual control is, see [OVERVIEW.md](OVERVIEW.md). For installing and
sizing the rig, see [INSTALLATION.md](INSTALLATION.md).

- [1. Launch](#1-launch)
- [2. Choose a profile](#2-choose-a-profile)
- [3. Set the output directory](#3-set-the-output-directory)
- [4. Fill in the metadata](#4-fill-in-the-metadata)
- [5. Calibrate](#5-calibrate)
- [6. Solve](#6-solve)
- [7. Optional: stimulation](#7-optional-stimulation)
- [8. Record](#8-record)
- [9. After you stop](#9-after-you-stop)
- [10. What the session leaves on disk](#10-what-the-session-leaves-on-disk)
- [11. Troubleshooting](#11-troubleshooting)

---

## 1. Launch

Three ways to start it, all equivalent:

- Double-click the **Panopticon** desktop shortcut, if one has been made
  (`make_shortcut.ps1` writes one that starts the application with no console window).
- Double-click **`_launch.bat`** in the repository folder. This one goes through `uv`, so
  it installs or updates dependencies first if `pyproject.toml` has changed; the shortcut
  does not.
- In a terminal, from the repository folder: `uv run gui.py`. Use this when something is
  going wrong — the diagnostic output appears in the terminal as it happens.

A small "Panopticon / Loading cameras..." splash appears, then the main window. Opening the
cameras and loading their settings file takes a second or two.

Everything the application prints is also written to a log file:
`logs/panopticon_<date>_<time>.log` inside the repository folder. Quote that file when
reporting a problem.

Two things happen on their own in the first half-minute.

**A hardware check** runs in the background. It only interrupts you if something is short:
fewer than 4 CPU cores, less than 16 GB of RAM, less than 500 GB free on the output drive,
a measured write speed under 500 MB/s, or no NVENC encoder found. A clean machine shows no
dialog at all.

**The trigger board is put back to a stimulation-free state.** A stimulation paradigm lives
in the board's flash memory, so it survives closing the application, a power cycle and
unplugging the USB cable — and it cannot be read back over the serial link. The application
therefore reflashes the recording-only sketch at every launch unless it has recorded that
the board already carries it. When it does reflash, the sidebar reads
`Clearing stim firmware…` for about 30 seconds. Stimulation is opt-in per launch: unless a
paradigm was applied since this launch, the board carries none.

After that the serial port is opened and held until you quit. Opening it resets the board,
and during the reset and bootloader wait (one to two seconds) the board is not running any
code, so every pin floats. A powered laser driver reads a floating input as on and flashes.
Holding the connection open for the whole session means that flash happens at launch and at
every **Apply**, and never at the start of a recording. If you need a hard gate, use the
laser's interlock.

**What a good launch looks like:**

![Panopticon at idle](images/main_idle.png)

- One live pane per camera, filling the grid.
- The frame-rate number on each pane sitting near **30**. Between acquisitions the cameras
  run free at 30 fps to feed the preview; they only run at the trigger rate while
  calibrating or recording.
- The state label bottom-right reading **`IDLE`**.
- No dialogs.

If a pane is black or missing, stop here and fix it — see
[Troubleshooting](#11-troubleshooting). Camera names are assigned by serial-number order,
so a camera that is absent at launch renames every camera after it.

---

## 2. Choose a profile

The dropdown at the top of the sidebar lists every file in `profiles/*.yaml`. The profile
decides the whole hardware configuration:

| The profile sets | Effect |
|---|---|
| `frame_width`, `frame_height` | Sensor region used |
| `frame_rate` | Trigger rate for a recording |
| `calibration_frame_rate` | Trigger rate for a calibration |
| `pfs_path` | The camera settings file, which is the only source of exposure and gain |
| `board_config` | Which ChArUco board the coverage display and the solve expect |
| `serial_port`, `trigger_pins` | Which board, and which of its pins drive the cameras |
| `stim_safe_pins` | Pins forced low the instant the board boots |
| `n_cameras` | How many cameras must be present before anything starts |
| `output_dir` | The default output directory |
| `realtime_encode`, `realtime_kick`, `quality`, `encode_parallel` | How frames are encoded |

Selecting a profile closes and reopens the cameras — the sidebar reads
`Switching cameras…` and the controls grey out for a second or two. The choice is
remembered per machine, so the next launch comes up on the same profile.

If the profile sets `n_cameras` to a non-zero number and a different number of cameras
enumerates, the open is refused with a dialog listing the serial numbers it found. This is
deliberate. Cameras are named `cam1`…`camN` in serial-number order, so if camera 3 fails
to appear, the physical cameras 4 onwards are renamed `cam3` onwards, and the calibration
extrinsics then attach to the wrong physical cameras. Triangulation still runs, and the 3D
output is wrong. Power-cycle the missing camera and reselect the profile.

You cannot change profile during an acquisition; the dropdown is disabled.

---

## 3. Set the output directory

Below the dropdown is a button showing a folder path. Click it, pick a folder, done. It is
the root for every session, and data goes into
`<output>/<date>/<mouse1>_<mouse2>/<calibration|recording>/`.

Selecting a profile sets this to the profile's `output_dir`, so a manual choice does not
survive a profile switch or a relaunch. Set it after choosing the profile, not before.

The disk-space check that runs when you press Record measures this drive, so point it at
the fast, large drive rather than accepting a default.

---

## 4. Fill in the metadata

Eight fields, in the sidebar:

| Field | Default | Used for |
|---|---|---|
| Date | today, as `YYYYMMDD` | folder name and filename |
| Mouse 1 | blank → `m1` | folder name and filename |
| Mouse 2 | blank → `m2` | folder name and filename |
| Assay | `open_field` | recorded in `session_metadata.json` |
| Experimenter | `IT` | recorded in `session_metadata.json` |
| Cohort | blank | recorded in `session_metadata.json` |
| Cage | blank | recorded in `session_metadata.json` |
| Notes | blank | recorded in `session_metadata.json` |

The first three build the paths. With Date `20260904`, Mouse 1 `m1` and Mouse 2 `m2`, the
session folder is `<output>/20260904/m1_m2/` and each video is named
`20260904-m1_m2-cam1-recording.mp4`.

**Fill these in before you calibrate, not after.** Solve looks for the calibration videos
under the path built from the fields as they are *at the moment you press it*. Change a
subject ID between calibrating and solving and Solve looks in a folder that does not exist.
The same applies to Record: it writes to the path built from the current fields.

The fields lock (grey, read-only) while an acquisition is running, and unlock when it
finishes.

Everything here is written to `session_metadata.json` at the end of each acquisition,
together with the host name, operating system, Python version, GPU model, **GPU driver
version**, GPU memory and the number of NVENC encode sessions the driver granted. The
driver facts are there because the concurrent-session cap is set by the driver and has
changed across driver generations; a driver update that lowers it below the camera count
pushes cameras onto the raw fallback, and without this record a session that behaves
differently after an update cannot be explained afterwards.

**Optional, useful here: Snapshot.** The Snapshot button saves one full-resolution PNG per
camera into `<session>/snapshots/<date>_<HHMMSS>/`. Use it to check focus and exposure and
to document the arena before committing to a recording. The status bar confirms
`Snapshot: saved 6/6 cameras → <folder>`.

---

## 5. Calibrate

Flip the **Calibrate** toggle. The cameras switch to hardware-triggered mode at the
profile's `calibration_frame_rate` (30 fps on the reference profile), the preview shows
every frame rather than every tenth, and the profile's calibration exposure and gain are
applied.

Calibration gets its own exposure because the board usually needs far more light than the
experiment does. In triggered mode the camera's frame-rate timer starts after exposure
ends, so the minimum interval is `exposure + 1/AcquisitionFrameRate`. With that limiter at
165 (the profile's `trigger_rate_limit`), a 100 fps recording (10 ms period) leaves an
exposure ceiling near 3.5 ms, while a 30 fps calibration (33.3 ms period) allows about
24.5 ms — roughly seven times the light budget, for free. The ceiling is computed and
enforced in code, and a profile value above
it is clamped with a log line rather than silently halving the frame rate. The real limit
in practice is motion blur, not the ceiling: at long exposures a briskly moved board smears
and its corners stop resolving.

Now the part you actually do: **move the board slowly through the arena and pause at each
pose.** The coverage display in the sidebar tells you when to stop.

The four figures below are rendered illustrations of particular states, not frames from one
session.

### Stage 1 — nothing detected yet

![Coverage graph, nothing detected](images/calib_stage_1_start.png)

Each numbered circle is a camera. Each line is a *pair* of cameras. Everything is dim, the
caption reads `paired 0/250  grid 0/3`, and the timer has started.

**What to do:** hold the board up where at least two cameras can see it. A node brightens
when that camera can see the board right now. If no node ever brightens, the board is too
dark or the wrong board config is selected — see
[Troubleshooting](#11-troubleshooting).

### Stage 2 — partial

![Coverage graph, partial coverage](images/calib_stage_2_partial.png)

Cameras 1 and 3 are glowing: they can see the board at this instant. Some lines have begun
to brighten and thicken, which means those pairs have accumulated shared detections. The
small four-cell badge on each node is that camera's field of view divided into quadrants;
a cell turns green once the board's centre has been seen in it. The caption
`paired 60/250  grid 2/3` reports the **worst** camera on each count.

**What to do:** keep moving, but deliberately. Two things need to grow at once. Carry the
board into the regions where two cameras overlap, so the lines fill in, and carry it into
the corners of each view, so the badges fill in. A board waved in the middle of the arena
grows neither.

### Stage 3 — nearly there

![Coverage graph, nearly ready](images/calib_stage_3_nearly.png)

Most lines are now thick and bright, `grid 3/3` says every camera has hit its quadrant
minimum, and `paired 200/250` says the weakest camera is close. One pair — the vertical
line between 1 and 4 — is still thin and dark.

**What to do:** work that pair specifically. Find where both of those cameras can see the
board at the same time, which for opposed cameras usually means holding it edge-on between
them, and hold it there. A single thin line is enough to keep the display out of READY,
because the pair graph has to be connected.

### Stage 4 — READY

![Coverage graph, ready](images/calib_stage_4_ready.png)

The whole graph freezes solid white and the caption reads `READY — m:ss`. Counting stops at
this point; the display will not change further.

**What to do:** flip Calibrate off. You are done.

### What READY requires, and why

Three conditions, all of them per-camera, all of them checked on every detection tick:

1. **Every camera has at least 250 paired detections.** A tick counts for a camera only
   when that camera *and at least one other* saw the board in the same tick. Detection runs
   at up to about 30 ticks per second, so 250 is a coverage target, not a frame count.
2. **The pair graph is connected**, counting only pairs with at least 80 shared detections.
   Two well-covered clusters that never see the board together cannot be placed in one
   coordinate frame, so they do not count as a calibration.
3. **Every camera has seen the board in at least 3 of the 4 quadrants of its field of
   view.** The quadrant is chosen by the centroid of the detected markers.

The third condition exists because the first two can be satisfied by waving the board in
one spot. That produces a calibration that looks fine in the summary numbers and behaves
badly away from the image centre: lens distortion is only constrained where there is data,
so a solve fitted from the middle of the frame extrapolates at the edges. Requiring
quadrant spread forces the data to cover the frame.

The brightness and contrast sliders do not affect this. They change the preview only. The
coverage detector reads the cameras' own full-resolution frames and the solve reads the
recorded file, so neither sees the adjustment. If the board is too dark to detect, raise
`calibration_exposure_us` in the profile.

When you flip Calibrate off, the frame indices where cameras co-detected the board are
written to `codet_frames.json`, then the videos are finalised the same way a recording is
(see [After you stop](#9-after-you-stop)).

---

## 6. Solve

With the state at `IDLE`, press **Solve**. Both toggles and the Solve button grey out while
it works — typically 4 to 5 minutes, with a hard timeout at 30 minutes. The status bar
reads `Running sleap-anipose calibration...` and then `Running calibration...`; the solve's
own per-camera and per-pair progress goes to the console and the log file.

What it does:

- Reads the `*-calibration.mp4` in each `calibration/camN/` folder.
- Uses `codet_frames.json` if it is there, decoding only the frames where cameras
  co-detected the board instead of scanning whole videos. This is the difference between
  minutes and much longer.
- Calibrates each camera's intrinsics, then every camera pair by stereo, then chains the
  pairs into one coordinate frame with `cam1` as the reference.
- Writes **`calibration.toml`** (aniposelib-compatible: per-camera size, matrix,
  distortions, rotation, translation) and **`reprojection_error_histogram.png`** into the
  `calibration/` folder.

The application then copies `calibration.toml` into the `recording/` folder, creating it if
needed, so every recording carries the calibration it was shot with. The status bar
confirms `Calibration solved — copied to <path>`.

Cameras with fewer than 5 detection frames are dropped from the solve. It needs at least
two cameras with detections, at least two with valid intrinsics, and at least one
co-detecting pair; short of that it fails with a message rather than producing a partial
calibration.

A **Calibration Warnings** dialog appears when the solve is not confident: a camera pair
with a stereo RMS above 20 px, or a camera with fewer than 30 detection frames. The dialog
tells you to record a longer calibration with the board visible to more cameras at once;
the per-pair numbers are in the console and in the log file. The status bar then reads
`Calibration solved — copied to … (with warnings)`. A calibration with warnings is still
written — decide for yourself whether to redo it.

Pressing Solve twice does nothing the second time: the status bar reads
`A solve is already running`.

---

## 7. Optional: stimulation

Skip this section entirely if the session has no optogenetic stimulation.

Press **Stimulation** to open the editor. The block canvas is on top; the waveform preview,
the parameter fields and the buttons are along the bottom.
[OVERVIEW.md](OVERVIEW.md) has the same window with every control numbered.

![The stimulation editor](images/stim_clean.png)

### Pins, for a reader who has not used a microcontroller

The trigger board is a small computer with rows of numbered sockets along its edges, called
pins. A **digital output pin** is one the board's program can hold at either 0 V (LOW) or
5 V (HIGH). Instruments that take a TTL input read that as off or on. The number printed
beside the socket on the board's header *is* the pin number you type into the editor —
there is no mapping table. Wire the instrument's modulation or gate input to that pin and
its ground to any pin marked GND. Nothing in software knows what is connected; the pin
number is the only link between the paradigm and the physical world.

On the reference rig, pin 53 goes to the laser driver's modulation input. If the instrument
has a TTL/analog mode switch, use TTL: TTL treats the pin as on/off, while an analog mode
maps 0–5 V onto output power, which would make the delivered power depend on the board's
exact rail voltage.

**The stimulation pin must not be a camera trigger pin.** The same board drives both. Its
trigger pins — `[2, 4, 6, 8, 10, 12]` on the reference profile — carry the pulse train that
exposes the cameras. A stimulation block on one of those pins adds extra rising edges to
one camera, so that camera's frame counter advances faster than the others, and "frame
number N" stops meaning the same instant in every view. Frame alignment, the real-time
kick-out coordinator and the per-frame stimulus trace all take that identity as given, so
nothing downstream would detect the problem. The editor refuses such a graph outright:

> Pin 2 cannot carry a stim waveform: camera trigger line — extra edges on one camera would
> break cross-camera block-ID alignment.

Pins 0 and 1 are refused for the same class of reason: they are the serial link to the
board, and driving them garbles both the protocol and the acknowledgement that confirms a
recording actually started. Pin 0 is easy to hit by accident, which is why a blank pin
field is refused (`Enter a pin number.`) rather than treated as 0.

### The safe-pins guard

The profile's `stim_safe_pins` (`[53]` on the reference rig) are driven LOW by the
very first statement of the generated firmware's `setup()`, before the serial link is even
opened. The order matters: `setup()` then blocks waiting for the application to send its
configuration, so anything placed after that wait would leave the pin floating for as long
as the application takes to connect — and a powered laser driver reads a floating input as
on. The guard sets each pin to OUTPUT first and then writes LOW, because writing LOW to a
pin still configured as an input only disables its pull-up and leaves it floating. Every
pin the loaded paradigm uses is added to this list automatically; the profile entry is the
floor, for pins that must be safe even when no paradigm is loaded.

There is one window the guard cannot cover: while the board is resetting and waiting in its
bootloader, no program is running at all and every pin is high-impedance. Opening the
serial port resets the board, so this happens once at launch and once per Apply — outside
the experiment — and not at the start of a recording, because the connection is held open
for the whole session.

### The paradigm is compiled into firmware, not streamed

Nothing is streamed to the board while a recording runs. The block graph is turned into
Arduino source and **flashed onto the board**, and the board then runs it on its own clock.

Consequences:

- **Editing the canvas changes nothing until you press Apply.** Apply compiles and uploads,
  which takes about 30 seconds. Until then the board is still running whatever was flashed
  last.
- **Record does not compile anything.** It sends the same start command it always sends;
  the paradigm on the board runs from t = 0 alongside the triggers. There is no host clock
  in the timing.
- **Test warns when the canvas has drifted from the board** and offers to upload first,
  because otherwise you would be testing the previous paradigm.
- **Firmware outlives the application**, so it is reflashed to a stimulation-free sketch at
  every launch (see [Launch](#1-launch)). Stimulation is opt-in per session. To reuse a
  saved paradigm, **Load** it and press **Apply**.

### Building a paradigm

A block is one step: a pin, a frequency, a pulse width and a duration.

1. Type into **Pin**, **Freq** (Hz), **PW** (ms) and **Dur** (s), then press
   **Create Block**. Pin has no default; the other three default to 0, 0 and 1.
2. Drag from a circle on the edge of one block to a circle on another to connect them. The
   target port highlights when you are close enough to snap. An arrow means "when this
   block finishes, start that one".
3. Click a block to select it and load its values into the fields; press Enter in a field
   to apply edits to the selected block.
4. Mouse wheel zooms, middle-button drag pans, `Delete` removes the selection, `Ctrl+C` /
   `Ctrl+V` copy and paste blocks.
5. **Save** writes the graph as JSON (offered as `stim_config.json` in the output
   directory); **Load** reads one back; **Clear** empties the canvas after a confirmation.

### Frequency and pulse width: what you actually get

The preview at the bottom left draws one second of the waveform the current numbers
produce. These are rendered illustrations of specific parameter combinations.

| Preview | Numbers | What the pin does |
|---|---|---|
| ![10 Hz, 10 ms](images/wave_10hz_10ms.png) | 10 Hz, 10 ms | A normal train: 10 pulses per second, each 10 ms long, 10% duty |
| ![40 Hz, 5 ms](images/wave_40hz_5ms.png) | 40 Hz, 5 ms | A denser train, 20% duty |
| ![20 Hz, 25 ms](images/wave_20hz_25ms.png) | 20 Hz, 25 ms | 50% duty — on half the time |
| ![0 Hz](images/wave_0hz.png) | 0 Hz | Pin held LOW. This is how you write an off-period |
| ![10 Hz, 100 ms](images/wave_10hz_100ms.png) | 10 Hz, 100 ms | **100% duty — constant ON, not 10 Hz** |

The last row is the trap, and it is an easy arithmetic slip: at 10 Hz each cycle is 100 ms
long, so a 100 ms pulse fills it completely and the pin never returns low. The firmware
treats a pulse width at or above the period as a deliberate constant ON rather than as an
invalid waveform — treating it as unrepresentable would hold the pin LOW for the whole
recording instead, which is a worse failure because nothing looks wrong. The preview
red-glows and captions it `100% duty — constant ON, not 10 Hz`. A pulse width *longer* than
the period gets the same red treatment, captioned `pulse 150 ms > period 100 ms`, and also
holds the pin HIGH.

Read the caption, not the numbers. It states the duty cycle explicitly.

### Starting, Ending, and loops

**Starting** pins a block as the beginning of its sequence. The rule the compiler applies,
per group of connected blocks: an explicit Starting flag wins; failing that, every block
with no incoming arrow starts a chain, which is how parallel chains work. A pure loop has
neither — every block in it has an incoming arrow — so it must be pinned by hand or it
compiles to nothing. The editor says so and blocks Apply, Test and Record:

> 2 block(s) form a loop with no start — select one and tick 'Starting'.

Only one Starting flag per connected group; joining two pinned blocks with an arrow makes
the loser give up its flag so the canvas cannot disagree with what gets uploaded.

**Ending stops the recording, not the chain.** Ticking Ending on a block means: when that
block finishes for the first time, flip Record off. The application arms a timer for that
moment and the status line reads `Recording will stop 45 s after start.` The board is not
asked to report back, and it is not told to stop stimulating — a looping chain keeps
running until the recording's stop command reaches it. To bound a loop, either flag an
Ending block on a parallel timer chain or stop the recording by hand.

If the flagged Ending block cannot be reached from any start, the status line warns
`The 'Ending' block is not reachable from any start — the recording will not stop on its
own.`

**One pin per chain.** Chains run at the same time, so two chains driving the same pin
fight over the output and the result is neither waveform. That is refused:

> Pin 53 is driven by more than one chain. Chains run at the same time, so they would fight
> over the output and the waveform would be neither one. Give each chain its own pin, or
> merge them into a single chain.

Reusing a pin *within* one chain is fine — those blocks run in sequence.

### Test, then Apply

**Test** runs the paradigm on the board with **zero camera pins**, so the firmware's camera
loops iterate over nothing and no triggers go out. Use it to watch the laser (behind
appropriate protection) without recording. A terminating paradigm counts down
(`Testing — 12 s remaining.`); a looping one has no end and reads
`Testing — looping, press Stop Test to end.` Pressing **Stop Test** is the only thing that
ends a loop, so if the status turns red with `STOP NOT CONFIRMED — stim may still be
running.`, power-cycle the board and key off the laser.

**Apply to Arduino** compiles and uploads. On success the status reads
`Upload successful — press Record to run paradigm.` The port is handed to `arduino-cli` for
the upload and reclaimed immediately afterwards, so the board's reset stays inside Apply
rather than moving into your next recording.

Do not quit the application during an Apply. The upload takes about 30 seconds and killing
it part-way can leave the board with firmware that has no safe-pin boot guard.

Whenever the canvas holds any blocks at the moment a recording starts, Record writes the
paradigm down beside the videos: `stim_paradigm.json` (the graph, the resolved chains, the
end time, the firmware's SHA-256 and whether that firmware matches what was uploaded during
this run of the application) and `stim_paradigm.ino` (the exact firmware source). The
session then describes itself. `matches_uploaded_firmware: null` means nothing was uploaded
during this run, so what the board holds is unknown rather than wrong.

---

## 8. Record

Flip the **Record** toggle. Before anything starts, several checks run in order, and each
one refuses rather than half-recording.

**The stimulation graph**, if the editor has been opened. A graph with a forbidden pin, a
pin driven by two chains, or an unstartable loop stops the recording with
`Cannot record with this stim workflow`. Record runs whatever is on the board, but the
canvas is what `stim_paradigm.json` and `stim_trace.csv` will claim about the session, so a
graph that contradicts the rig's assumptions is refused here too.

**Capacity**, against the actual number of open cameras. Blocking failures:

- `No cameras are open. Recording would run the trigger protocol — and any baked-in stim
  paradigm — while saving nothing.`
- `Not enough RAM for 6 cameras: …` with the arithmetic (the pylon buffer pool plus the
  NV12 ring, against available memory) and the suggestion to lower `MaxNumBuffer` or
  `kick_max_lag` or close other applications.
- `NVENC granted only N concurrent sessions but 6 cameras need one each.` The driver caps
  this, and cameras beyond the cap would silently fall back to writing raw frames to disk
  at hundreds of gigabytes per ten minutes each.

Warnings, which you can override with **Start anyway?**, cover tight RAM and short disk
space. The disk figure assumes a ten-minute recording, which is a guess, so it warns rather
than blocks.

**An existing recording in the target folder.** If the folder already holds an `.mp4`,
`raw.bin`, `stream.h264`, `blockids.npy`, `frametimes.npy` or `alignment.npz`, you get
`Existing files found in: <path>  Overwrite?` and nothing is touched unless you say yes.
Metadata counts as data here: a folder whose videos were moved away for labelling still
holds the files that make them interpretable.

Then the cameras go to triggered mode at the profile's `frame_rate` with the `.pfs`
exposure and gain *restored* — the calibration exposure is put back rather than
recalculated, which is what guarantees a long calibration exposure can never leak into a
100 fps session and silently halve its frame rate. Finally the start command goes to the
board and the board must acknowledge it. If it does not acknowledge even after a forced
reset, and it has acknowledged before, the start is rolled back:

> The trigger board did not acknowledge the start command, so no triggers would be sent.

That check exists because a mis-parsed configuration looks exactly like a good start until
the session comes back with no frames in it.

### While it runs

- The state label reads **`RECORDING`** in red.
- Each pane's frame-rate number should sit at the trigger rate — 100 on the reference
  profile. One pane reading low is worth stopping for.
- The status bar reports how far behind real time the worst camera is:

| Status bar | Meaning |
|---|---|
| `Capture healthy — keeping up with the trigger (max lag 3 ms)` | What you want. The number is shown even when healthy so the claim can be checked. |
| `CAPTURE FALLING BEHIND: cam5 is 0.42 s behind real time and growing. Close other applications.` | Act now. |
| `CAPTURE 2.1 s BEHIND REAL TIME (cam5). Frames will be lost when the buffer pool fills. Stop and investigate.` | Stop. |

That readout exists because the failure it catches is invisible otherwise. A grab loop a
fraction of a millisecond over budget loses nothing at first — the driver's buffer pool
absorbs the deficit — so there is no error and no dropped frame for up to ten minutes,
while every frame retrieved gets staler. A large lag also means the preview is showing you
the past.

The preview updates from every tenth frame during a recording, so it looks less smooth than
during calibration. That is deliberate; the preview never has priority over capture.

### Stopping

Flip Record off. If a stimulation block is flagged **Ending**, the toggle flips itself when
that block first completes.

---

## 9. After you stop

Four things happen, and the state label names each one.

**`Finishing…`** — the stop command goes to the board (the serial port stays open so the
next recording does not reset it), the encoders drain, the cameras go back to free-run
preview, and the per-camera `frametimes.npy` and `blockids.npy` are written. Then
`session_metadata.json`, then `stim_trace.csv` if a paradigm was recorded. This is about a
second.

**`ENCODING`** — the sidebar shows `Encoding 3/6`. With real-time encode (the default) the
frames were already compressed on the GPU during capture, so this is a stream copy of each
camera's `stream.h264` into an `.mp4` and finishes in seconds. In the raw fallback it is a
full NVENC encode pass over `raw.bin`, `encode_parallel` cameras at a time. Either way the
source file is deleted only after the `.mp4` has been confirmed to exist and be non-trivial
in size; a camera whose encode failed keeps its source and gets an `encode_error.log`.

**`ALIGNING`** — usually skipped. With real-time kick-out on, a trigger only reaches the
encoders once every camera has captured it, so the videos are already equal in length and
aligned trigger-for-trigger and there is nothing to do. Alignment runs when kick-out is off,
or when something went wrong during capture. When it runs it always writes the index
(`aligned/alignment.npz` and `alignment.json`) and, if some camera really is holding frames
another camera is missing, re-encodes each video down to the frames common to all cameras,
replaces the original atomically, and rewrites that camera's `blockids.npy` and
`frametimes.npy` to match. The status bar then reads
`Aligned: 32901 synchronized frames per camera`.

**`IDLE`** — the session is complete.

### Telling a good session from a bad one

Three checks, in order of how quickly you can do them.

**1. The status line.** It is built from every camera, not only the ones that worked:

```
Recording encoded: 6022 frames, 100.0 fps
```

A single frame count means every camera produced the same number. A range means they did
not:

```
Recording encoded: 5980-6022 frames, 99.8 fps
```

and a failure is named outright:

```
Recording encoded: 6022 frames, 100.0 fps  —  1 CAMERA(S) FAILED: cam4
```

The frame rate is computed from each camera's own timestamps and should read the trigger
rate.

**2. No `WARNINGS.txt`.** A clean session has none anywhere under its folder. Any problem
that is worth knowing about months later is written down as well as shown in a dialog,
because a dialog gets dismissed and forgotten. Stale ones are deleted when a new
acquisition starts in the same folder, so a `WARNINGS.txt` you find is about the recording
sitting next to it. The two places it appears:

- `<recording>/WARNINGS.txt` — for the acquisition as a whole: capture warnings, retired
  cameras, cameras that produced no usable video.
- `<recording>/camN/WARNINGS.txt` — that one camera's frame-to-trigger bookkeeping was
  repaired or could not be verified.

**3. Equal `blockids.npy` lengths.** This is the ground truth. Each file holds one trigger
ordinal per recorded frame. In kick-out mode every camera's array should be identical; if
they are not, the videos are not trigger-aligned regardless of what anything else says.

A clean session produces no dialog at all.

### Quitting in the middle

Closing the window during an acquisition, an encode, an alignment or a solve asks
`State is RECORDING. Quit anyway?` and warns that the unfinished session's data will be
**deleted**. That is the design: a half-finished recording cannot be interpreted, and
leaving it on disk to be found later is worse than not having it. Answer No if you want to
keep it — stop the acquisition normally first. Quitting always sends the stop command to
the board, so closing the application cannot leave a paradigm or a laser running.

---

## 10. What the session leaves on disk

### Paths and names

```
<output directory>/<date>/<mouse1>_<mouse2>/<calibration|recording>/<camN>/
```

- The **date** and the two **subject IDs** come from the sidebar fields. Blank subject
  fields become `m1` and `m2`.
- **`calibration/`** and **`recording/`** are the two acquisition types. They are separate
  folders under one session, so one calibration can serve the recording beside it.
- **`camN`** is `cam1`…`camN` in camera serial-number order.

Video filenames are `<date>-<mouse1>_<mouse2>-<camN>-<acquisition type>.mp4`:

```
20260904-m1_m2-cam1-calibration.mp4
20260904-m1_m2-cam1-recording.mp4
```

The acquisition type is part of the name, not decoration: the solve looks for `.mp4` files
with `calibration` in the name, and the alignment pass prefers ones with `recording` in it.

### Every file a session can contain

**In each `camN/` folder:**

| File | What it is | What reads it |
|---|---|---|
| `<date>-<session>-<cam>-<type>.mp4` | The video. H.264 in `yuv420p`, one IDR per second, `moov` atom at the front. Both properties exist so the file opens in a browser: without an explicit GOP length a whole recording can come out with a single keyframe, which makes seeking impossible, and with the `moov` atom at the end a browser has to download the entire file before showing frame 1. | LUC3D; the calibration solve; any pose pipeline |
| `frametimes.npy` | 2 × N array: frame numbers `1..N` and each frame's timestamp in seconds from the first frame. | the encode worker (frame count), the alignment pass, analysis |
| `blockids.npy` | N int64 values: the GigE block ID — the trigger ordinal — of every recorded frame. A dropped frame shows as a gap, which is what makes cross-camera alignment possible after a drop. | `alignment.py`, `stim_trace.py`, `2_align.py` |
| `WARNINGS.txt` | Present only when this camera's frame-to-trigger mapping was repaired or could not be verified. | you |
| `encode_error.log` | Present only when this camera's encode failed. `ffmpeg`'s stderr. Removed on success. | you |
| `stream.h264` | Transient. The GPU-produced elementary stream during real-time capture; remuxed to `.mp4` and deleted. Left behind means the encode failed. | the encode worker |
| `raw.bin` | Transient. Uncompressed frames, in the raw fallback profile or for a camera whose GPU encoder failed to start. Deleted after a successful encode. | the encode worker |
| `raw_tail.bin`, `tail.h264` | Transient. Frames captured after a camera's GPU encoder died mid-recording, appended to `stream.h264` before the remux. | the encode worker |
| `aligned_tmp.mp4` | Transient. An alignment pass in progress; renamed over the original on success. | the alignment pass |

**In the acquisition folder (`calibration/` or `recording/`):**

| File | What it is | What reads it |
|---|---|---|
| `calibration.toml` | Camera parameters: per camera a `size`, `matrix`, `distortions`, `rotation` and `translation`, in aniposelib's layout. Written into `calibration/` by the solve and copied into `recording/` so each recording carries the calibration it was shot with. | LUC3D; triangulation (aniposelib / sleap-anipose) |
| `codet_frames.json` | `{"cam1": [frame numbers], …}` — the frames where this camera and at least one other saw the board, collected live by the coverage display. Calibration only. | `1_calibrate.py`, to avoid scanning whole videos |
| `reprojection_error_histogram.png` | Per-pair stereo RMS from the solve, as a chart. Calibration only. | you |
| `stim_paradigm.json` | The stimulus paradigm as recorded at the moment the recording started: `safe_low_pins`, `end_time_s`, `firmware_sha256`, `matches_uploaded_firmware`, the resolved `chains` (pin, frequency, pulse width, duration and duty `mode` per step, plus whether the chain loops) and the raw `blocks`/`edges`. `matches_uploaded_firmware: null` means nothing was uploaded during that run of the application, so the board's contents are unknown — not wrong, unknown. | `stim_trace.py`, `3_stim_trace.py`, you |
| `stim_paradigm.ino` | The exact firmware source the graph compiles to. Nothing reads it; it is the record of what the board would have run. | you |
| `stim_trace.csv` | One row per recorded frame: `frame`, `blockid`, `t_s`, `any_active`, then `chain<i>_step`, `chain<i>_active`, `chain<i>_freq_hz`, `chain<i>_pw_ms` per chain, then a modelled `pin<N>_ttl` per pin. Time comes from `(blockid − 1) / fps`, never the frame index, because cameras drop frames independently and frame *i* is not trigger *i*. **Derived, not observed** — it says what the paradigm should have delivered given the uploaded firmware, and cannot know whether the laser was keyed on, the interlock in, or the beam unblocked. For a witness, put the laser's sync LED in a camera's field of view. | analysis, you |
| `WARNINGS.txt` | Present only when something went wrong: a retired camera, truncated bookkeeping, a camera with no usable video. Its absence is a positive signal. | you |
| `aligned/alignment.npz` | `common_block_ids`, `frame_index` (cameras × common frames) and `camera_names` — the lossless alignment index. Written whenever an alignment pass runs, whether or not it re-encoded anything. | analysis, `2_align.py` |
| `aligned/alignment.json` | The same thing readable: `recording`, `camera_names`, `trigger_span`, `common_frames`, `replaced`, and per camera `recorded` and `dropped`. | you |

**In the session folder:**

| File | What it is | What reads it |
|---|---|---|
| `session_metadata.json` | The sidebar fields plus camera names and count, frame rates, resolution, time of day, ISO timestamp, host, OS, Python version, GPU, GPU driver version, GPU memory and the NVENC session count. Rewritten at the end of every acquisition, so its timestamp is the end of the last one. | you |
| `snapshots/<date>_<HHMMSS>/camN.png` | Full-resolution stills, one per camera, one folder per press of Snapshot. | you |

Application logs live outside the session, in `logs/panopticon_<date>_<time>.log` in the
repository folder.

### A calibration-only session

```
data/
└── 20260904/
    └── m1_m2/
        ├── session_metadata.json
        ├── snapshots/
        │   └── 20260904_101500/
        │       ├── cam1.png
        │       └── …  cam2.png … cam6.png
        └── calibration/
            ├── codet_frames.json
            ├── calibration.toml
            ├── reprojection_error_histogram.png
            ├── cam1/
            │   ├── 20260904-m1_m2-cam1-calibration.mp4
            │   ├── blockids.npy
            │   └── frametimes.npy
            └── …  cam2/ … cam6/, each with the same three files
```

Pressing Solve also creates `recording/` containing nothing but the copied
`calibration.toml`, ready for the recording that follows.

### A recording with stimulation

```
data/
└── 20260904/
    └── m1_m2/
        ├── session_metadata.json
        ├── calibration/                          (as above)
        └── recording/
            ├── calibration.toml                  copied by Solve
            ├── stim_paradigm.json
            ├── stim_paradigm.ino
            ├── stim_trace.csv
            ├── cam1/
            │   ├── 20260904-m1_m2-cam1-recording.mp4
            │   ├── blockids.npy
            │   └── frametimes.npy
            └── …  cam2/ … cam6/, each with the same three files
```

That is a clean session: no `WARNINGS.txt`, no `aligned/`, no leftover `stream.h264` or
`raw.bin`. A session that hit trouble adds `WARNINGS.txt` at the recording level and
possibly inside a camera folder; a session recorded without real-time kick-out, or one that
lost frames unevenly, adds `aligned/`.

---

## 11. Troubleshooting

### At launch

| What you see | What it means |
|---|---|
| `No cameras found or .pfs missing. Check connections and profile.` | Either nothing enumerated, or the profile's `pfs_path` does not exist. Check the cameras have power and link lights, then check the path in the profile YAML. |
| `Expected 6 cameras but 5 enumerated.` with a list of serial numbers | A camera did not appear: dead switch port, no power, or still booting. The open is refused rather than continuing with five, because camera names are positional by serial number and a missing camera would rename every camera after it and attach the calibration extrinsics to the wrong physical cameras. Power-cycle the missing camera and reselect the profile. |
| `Camera <serial> failed to open/configure: PixelFormat is Mono12, not Mono8.` | The settings file was changed in pylon Viewer and the pixel format moved. The capture path assumes 8-bit, and anything wider is truncated silently, producing a full-length, perfectly aligned, visually shredded recording. Fix the `.pfs`. |
| `Camera <serial> failed to open/configure: resolution 1920x1080 differs from camera 1 …` | One camera has a different region of interest. All cameras must match. |
| A `Hardware Check` dialog | Advisory: cores, RAM, free space, measured disk write speed or a missing NVENC encoder. It does not block anything. |
| `Could not clear stim firmware` | The board could not be reflashed, so it may still carry a paradigm from a previous session — including a looping one. Open Stimulation and press Apply with an empty canvas, or key off the laser. |
| The laser flashes briefly at launch | Expected. During the board's reset and bootloader wait no program is running and every pin floats. It happens at launch and at every Apply, and not at the start of a recording. Fit the interlock if you need a hard gate. |
| A pane is black but the frame rate is counting | A display problem, not a capture problem. Check the brightness and contrast sliders — they affect the preview only. |

### Starting an acquisition

| What you see | What it means |
|---|---|
| `No cameras are open. Recording would run the trigger protocol — and any baked-in stim paradigm — while saving nothing.` | The cameras never opened. Fix that first; a recording here would produce triggers, possibly stimulation, and no data. |
| `Not enough RAM for 6 cameras: …` | The pylon buffer pool plus the NV12 ring exceeds available memory. Close other applications, or lower `kick_max_lag` in the profile — the ring scales with it. |
| `NVENC granted only 5 concurrent sessions but 6 cameras need one each.` | The GPU driver caps concurrent encode sessions, and that cap has changed across driver generations. Cameras beyond it would silently fall back to writing raw frames. Record fewer cameras or use the raw profile. |
| `Disk may be short: a 10-minute recording would need ~X GiB and only Y GiB is free.` | A warning, not a refusal: ten minutes is an assumption, not a known recording length. A shorter recording is fine. |
| `Existing files found in: <path>  Overwrite?` | The target folder already holds videos or their metadata. Change the metadata fields to name a new session, or confirm the overwrite. |
| `Could not open serial port COM3. Close Arduino Serial Monitor / other apps holding the port and retry.` | Something else has the port: an Arduino Serial Monitor, a second copy of the application, or the wrong port in the profile. |
| `The trigger board did not acknowledge the start command, so no triggers would be sent.` | The board did not confirm the configuration, even after a forced reset, and it has confirmed before. The cameras are rolled back rather than recording a full-length session with no frames in it. Check the USB cable and that the board is running the Panopticon sketch. |
| `Cannot record with this stim workflow` | The canvas has a forbidden pin, one pin driven by two chains, or a loop with no Starting block. Fix the graph. The message names which. |
| `Solve unavailable while acquiring/encoding` | Solve only runs at `IDLE`. |
| `A solve is already running` | One solve at a time. It takes 4 to 5 minutes and does not change the state label, which is why a second press is easy to make and why it is refused. |

### During a recording

| What you see | What it means |
|---|---|
| `CAPTURE FALLING BEHIND: cam5 is 0.42 s behind real time and growing. Close other applications.` | That camera's grab loop is over budget. Nothing has been lost yet — the driver's buffer pool is absorbing the deficit — but frames will be lost when the pool fills. Close whatever else is using the machine. |
| One pane's frame rate reading about half the trigger rate | Classic symptom of an exposure over the ceiling. In triggered mode the minimum interval is `exposure + 1/AcquisitionFrameRate`, so an exposure that pushes it past the trigger period makes the camera skip every second trigger. Exposure and gain come from the `.pfs`; check it against the rate you are recording at. It looks fine in the preview, because the preview runs free at 30 fps with 33 ms of headroom. |
| The recording stops on its own | A stimulation block flagged **Ending** finished. That is what the flag does. |

### After a recording

| What you see | What it means |
|---|---|
| `1 CAMERA(S) FAILED: cam4` in the status line, and a dialog | That camera produced no usable video. Its source files were **kept** rather than deleted — look for `raw.bin` / `stream.h264` and `encode_error.log` in its folder. |
| `cam3: block-ID bookkeeping claimed 6022 frames but only 5990 were persisted … truncated to 5990 so frame indices still map to the correct triggers` | An encoder fell behind or died. A frame accepted by the queue is not necessarily a frame that got encoded, so the metadata is trimmed to what is actually in the video. Without that repair, frame *i* of the mp4 would map to the wrong trigger and nothing downstream would notice. |
| `cam3: encoder did not finish draining, so the frame-to-trigger mapping is UNVERIFIED.` | Counters were still moving at teardown, so no repair was attempted rather than one being guessed from a moving target. Check that camera's mp4 frame count against `blockids.npy` before trusting its alignment. |
| `cam5 was RETIRED mid-recording (…). Its video ends at that point; the other cameras continued and stay aligned with each other.` | That camera's stream stalled or its trigger ordinals could not be re-established after a restart, so it was dropped from the alignment set. The alternative — publishing frames under a guessed ordinal — would corrupt every camera. The survivors are fine. |
| `Recording did not finish cleanly` | Saving failed, for example a full disk. The raw capture files are still in the folder and have **not** been encoded or deleted. Do not start another recording into that folder. |
| `Alignment failed: … — videos left as-is` | The alignment pass could not complete. The originals are untouched. `uv run 2_align.py <recording_dir> --replace` retries it from a terminal. |
| `Trigger board did not confirm the stop` | The stop command was not accepted. The board may still be triggering, and a looping stimulation chain never ends on its own. Power-cycle the board and key off the laser. |

### Calibration and solve

| What you see | What it means |
|---|---|
| No node ever brightens in the coverage display | Nothing is detecting the board. Either it is too dark — raise `calibration_exposure_us`, and note calibration allows about 24.5 ms at 30 fps against roughly 3.5 ms at 100 fps — or the profile's `board_config` does not describe the physical board. A board printed before the OpenCV 4.6 layout change needs `board_legacy: true`, without which newer detectors find every marker and return zero board corners, silently. |
| The coverage display never appears | OpenCV is missing, or the profile's `board_config` path does not exist. The display disables itself so the rest of the application still runs. |
| `paired` climbs but `grid` sticks at `1/3` or `2/3` | The board is being waved in one place. Carry it into the corners of each camera's view. |
| One line stays thin while everything else is bright | That pair of cameras rarely sees the board together, and READY needs the pair graph connected. Work that pair on its own. |
| `No ChArUco board detections found.` | The solve found nothing to work with. Check the board was visible to all cameras during the calibration recording and that the board config matches the physical board. |
| `Calibration solve failed (singular matrix).` | Too few detections, or the board only ever seen from one angle. Record a longer calibration with more orientations. |
| `Consider recording calibration longer with the board visible to more cameras simultaneously.` | The solve produced a calibration but is not confident: a pair with a stereo RMS above 20 px, or a camera with fewer than 30 detection frames. The per-pair numbers are in the log. |
| `Calibration timed out (30 min)` | The solve did not finish. The likeliest cause is a calibration recorded with no coverage display running: without `codet_frames.json` the solve scans every third frame of every video instead of only the co-detection frames. |
| `Calibration failed (exit code 1): …` | The tail of the solve's error output. `fewer than 2 cameras with detections`, `fewer than 2 cameras with valid intrinsics` and `no camera pairs with co-detections` all mean the calibration recording was too thin to solve — record it again and watch the coverage display. |
| `Board config not found: …  Set board_config in your profile YAML to a valid file in configs/boards/.` | The profile points at a board description that is not there. |
| `uv not found on PATH` | The solve runs as a self-contained script through `uv`. Install `uv`, or run `1_calibrate.py` by hand. |
| `Calibration directory not found: <path>` or `No calibration videos found in: <path>` | The metadata fields do not match the calibration that was recorded. Solve builds the path from the fields as they are when you press it. |

### Stimulation

| What you see | What it means |
|---|---|
| `arduino-cli was not found, so the stim firmware cannot be compiled or uploaded.` | Install the Arduino IDE (which bundles it), or put `arduino-cli` on `PATH`, or set `PANOPTICON_ARDUINO_CLI` to its full path. Camera acquisition does not need it — only Apply and Test. |
| `Compile failed (arduino-cli exit …)` | A toolchain problem, and nothing was flashed, so the board still runs what it ran before. If the error mentions a missing core: `arduino-cli core install arduino:avr`. |
| `Upload failed on COM3 (…)` | The sketch compiled, so this is the link to the board: the port is held by something else, the profile names the wrong port, or the board is not an Arduino Mega. An upload that failed part-way leaves the firmware — including the laser-pin boot guard — in an unknown state. Power-cycle the board. |
| `The serial port is in use by the running acquisition. Stop it before uploading.` | Apply and Test need the port. Stop the acquisition first. |
| `2 block(s) form a loop with no starting block, so they would never run.` | Every block in that loop has an incoming arrow, so nothing marks the beginning. Select one and tick **Starting**. |
| `Pin 2 cannot carry a stim waveform: camera trigger line …` | The block is on a camera trigger pin. Extra edges on one camera break the assumption that a given trigger ordinal is the same instant in every view. Move the block to a free pin. |
| `Pin 53 is driven by more than one chain.` | Chains run concurrently and would fight over the output. Give each chain its own pin, or merge them into one chain. |
| The preview is red and says `100% duty — constant ON, not 10 Hz` | The pulse width is at least as long as the period, so the pin never returns low. Almost always an arithmetic slip. Fix the numbers, or keep it if a constant output is what you want. |
| The paradigm did not run at all | It was probably never applied. The sequence is compiled into the firmware, so editing the canvas changes nothing until **Apply**. `matches_uploaded_firmware` in `stim_paradigm.json` records whether what was on the canvas matched what was flashed. |
| A paradigm ran that nobody chose | This is what the launch-time reflash prevents, so it means the reflash failed — check for the `Could not clear stim firmware` warning at launch. Firmware survives closing the application. |
| `STOP NOT CONFIRMED — stim may still be running.` | The board did not accept the stop. A looping chain never ends on its own. Power-cycle the board and key off the laser. |
