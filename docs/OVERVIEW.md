# The interface

Panopticon drives a rig of hardware-triggered cameras. It previews them, records
trigger-synchronized video, captures the calibration footage that fixes the
rig's geometry in 3D, and talks to the microcontroller board that both triggers
the cameras and delivers optogenetic stimulation. All of that lives in two
windows: the main acquisition window, and the stimulation editor that opens from
it.

This page is the control-by-control reference for both windows — what each one
does, where it writes, and, for the controls whose behavior is not obvious from
the label, what it does that you would not guess from looking at it. If you are
running a session for the first time, [WORKFLOW.md](WORKFLOW.md) walks through
one from launch to finished files and is the better place to start; come here
when you want to know exactly what a particular button is about to do.

Screenshots are regenerated with `uv run python docs/make_screenshots.py`. The
callout positions come from Qt's own widget geometry, so they follow the layout
rather than being measured by hand, and the numbers in each figure always match
the numbers in the text beside it.

---

## The main window

![The Panopticon main window with numbered callouts](images/ui_annotated.png)

This is the window that opens when you launch Panopticon, and it is where a
whole session happens: you choose the rig configuration, fill in the metadata
that names the session, watch the cameras to check aim and focus, capture a
calibration, and then run the recording. There is no separate settings window —
anything that is not on this window lives in a configuration file rather than in
the interface.

The layout is a camera grid on the left and a fixed 260 px sidebar on the right.
The sidebar is arranged roughly in the order you use it, in three sections —
Metadata, Acquisition, Display — with a status label at its foot. On launch the
window sizes itself to about 80% of the screen height and the grid's aspect
ratio follows the camera count, so rigs with different numbers of cameras open
at different shapes.

### Preview

The preview is here so you can see what the cameras see: whether one is aimed
wrongly, out of focus, badly lit, or no longer delivering frames at all. It is
what you watch while positioning cameras, while walking the calibration board
through the arena, and any time a run looks like it is going wrong. It is also
the deliberately cheapest part of the application: the preview gives up
resolution and frame rate so that the recording never has to.

**1 — Camera preview grid.**
Live video from every open camera, three panes per row. The grid is rebuilt from
scratch whenever cameras are opened, so it always shows exactly the cameras that
enumerated and never leaves a stale pane behind for one that has gone away.

The preview is cheap on purpose. Frames are downsampled 3x in each axis inside
the grab thread — the per-camera thread that pulls frames out of the driver — so
1920x1200 becomes 640x400, and the panes repaint on a 33 ms timer rather than
once per arriving frame. Beyond that, how many frames reach the preview at all
depends on what the rig is doing:

| State | Camera mode | Frames sent to the preview |
|---|---|---|
| Idle | free-run at 30 fps | every frame |
| Calibrating | triggered at `calibration_frame_rate` (30) | every frame |
| Recording | triggered at `frame_rate` (100) | every 10th frame |

So the preview is decimated during a recording and not during a calibration. At
100 fps that leaves the panes updating about 10 times a second, which keeps
display work off the loop that has to drain the driver's buffer pool. The
preview is a monitor, not a measurement — nothing downstream reads it.

The preview also stops entirely during blocking camera work. Switching profiles
("Switching cameras…") and stopping an acquisition ("Finishing…") halt the
refresh timer, so the panes freeze on their last frame for a second or two. A
firmware flash freezes them for considerably longer — around 30 s — and the
state label (16) is what tells you which of the two you are looking at.

**2 — One camera pane.**
Each camera gets one pane, labeled `cam1`…`camN` in its top-left corner. Those
labels are not decoration: they are the identity every later stage uses, from
the video filenames to the calibration, so it is worth knowing where they come
from.

Names come from **serial-number order**: the backend sorts the enumerated
devices by serial number and the pane index becomes the name. Physical position,
switch port and boot order have no effect. That ordering is what the calibration
extrinsics attach to, so a camera that fails to enumerate would rename every
camera after it. Setting `n_cameras` in the profile makes the software refuse to
open a partial set rather than silently renumber; the error lists the serials it
did find.

**Double-click a pane to zoom that camera to the whole grid. Double-click it
again to go back.** While zoomed the other panes are hidden, so the way back is
the same pane you came in on.

**3 — Live frame rate.**
The small number at the bottom-left of each pane is that camera's delivered
frame rate. It is measured in the camera's own grab thread over the last ten
frames and pushed to the label about three times a second, so it responds within
a second of something changing.

It should sit at whatever the cameras are currently doing: ~30 fps idle, the
calibration rate while calibrating, the trigger rate while recording. One pane
reading low while the others are correct is usually the first visible sign of a
problem with that camera's link or triggers, and it is worth stopping for.

### Metadata

This top section of the sidebar settles two questions before anything runs:
which hardware Panopticon is talking to, and what the session will be called on
disk. It is the part you fill in at the start of a session and then leave alone,
since the fields lock themselves once an acquisition begins — by then they name
the folder being written into.

**4 — Rig profile.**
The dropdown at the top selects a *rig profile*: one YAML file in `profiles/`
describing one physical rig. Choosing it settles the entire hardware
configuration in a single click — resolution, the recording and calibration
frame rates, the camera settings file (`.pfs`, a saved dump of the cameras' own
registers, applied to every camera as it opens), the calibration board config,
the serial port and trigger pins of the trigger board, the encode mode, the
kick-out depth (how many frames the frame-synchronization coordinator will hold
while it waits for a lagging camera), and the calibration-only exposure and
gain. Changing the selection closes every camera and reopens them against the
new profile, which takes a second or two and runs off the UI thread so the
window stays responsive.

The choice is remembered per machine, not in the repo. Profile files are shared
between rigs through git, so the startup default is the profile last used on
this machine; failing that, the first profile whose `.pfs` file exists.

Selecting a profile also resets the output directory to that profile's
`output_dir`. Profile switching is ignored unless the app is idle.

**5 — Output directory.**
The root folder every session is written under. Clicking the button opens a
folder picker; since a full path rarely fits in a 260 px sidebar, the button
shows a shortened version of it and the whole thing as a tooltip. Sessions are
nested beneath that root by date and subject, so one root can hold a whole
project:

```
<output>/<date>/<mouse_1>_<mouse_2>/<calibration|recording>/
```

**6 — Session metadata.**
Eight text fields describe the session: Date, Mouse 1, Mouse 2, Assay,
Experimenter, Cohort, Cage and Notes. Date arrives filled in with today, Assay
with `open_field` and Experimenter with `IT`; the rest start empty.

Three of them are structural rather than merely descriptive. Date, Mouse 1 and
Mouse 2 build the folder path shown above, so they are worth filling in before
recording rather than after — renaming a folder afterwards is possible, but the
metadata written inside it will disagree. Leaving the subject fields blank is
allowed and gives you `m1` and `m2` in the path.

All eight fields are written to `session_metadata.json` in the session folder
when the acquisition stops, and they are joined there by everything about the
machine that could later explain the recording: camera names, resolution, frame
rates, time of day, host name, OS and Python version, and the GPU's name,
**driver version** and total memory. The number of concurrent NVENC sessions —
NVENC being the GPU's dedicated hardware video encoder — that the preflight
found available is recorded alongside them.

The driver version earns its place because the cap on concurrent NVENC sessions
has moved across driver generations, and each camera needs one session to record
compressed video. A driver update that drops the cap below the camera count
therefore changes how a session records without anything else on the machine
changing, and this line in the metadata is what makes that visible afterwards.

For the duration of an acquisition the metadata fields go read-only, along with
the output-directory button and the profile dropdown, since between them they
name the folder currently being written into.

### Acquisition

This is the group that actually does things. Calibrate and Record are toggle
switches that start and stop an acquisition, and they are deliberately mutually
exclusive — one is disabled while the other is on. Solve, Snapshot and
Stimulation are one-shot actions, and the coverage display underneath them is
not a control at all: it appears on its own while a calibration is running. A
typical session uses Calibrate, then Solve, then Record, with Snapshot and
Stimulation brought in as needed.

**7 — Calibrate.**
Starts and stops a *calibration acquisition*: a short recording of a printed
board carried around the arena, from which the solve later recovers each
camera's own lens parameters — its intrinsics — and where all the cameras sit
relative to one another. Without a calibration there is no 3D at all; the videos
are just unrelated views of the same animal.

While a calibration runs, the cameras are switched into trigger mode at the
profile's `calibration_frame_rate` (30 fps on the `3dpose` profile), and the
profile's `calibration_exposure_us` and `calibration_gain_db` are applied for
that run only. Those two settings exist because a slowly-moved board can afford
far more light than a fast recording can. The exposure and gain from the `.pfs`
are put back as soon as a recording starts, so a long calibration exposure
cannot leak into a 100 fps session and quietly halve its frame rate.

A calibration is also the one acquisition performed with a person inside the
arena, holding the board, so it is always given the **stimulation-free
firmware** — the recording-only sketch, which triggers cameras and drives no
stimulation pin at all. Panopticon flashes it for you if the board is not
already carrying it, which is why the state label may sit at `Flashing
recording-only firmware…` for about 30 s after you press Calibrate. You never
have to arrange this by hand, and there is no way to run a calibration with a
paradigm live on the board.

The board is a ChArUco board — a chessboard with a unique ArUco marker printed
inside each white square, so a detector can name individual corners even when
only part of the board is in view. Move it slowly through the arena so that
every camera sees it, every *pair* of cameras sees it at the same moment, and it
visits all four quadrants of every view. The coverage display (12) is what tells
you those conditions have been met, so watch it rather than guessing when to
stop. Turning Calibrate on disables Record until it is turned off.

**8 — Record.**
Starts and stops the experiment recording itself, with the cameras triggered at
the profile's `frame_rate` — 100 fps on the `3dpose` profile.

A recording that fails quietly costs an experiment rather than a minute, so a
preflight runs before anything starts and refuses outright in four cases:

- **No cameras are open.** Recording would still run the trigger protocol — and
  with it any stimulation paradigm baked into the trigger board — while saving
  nothing at all.
- **Not enough RAM** for the driver's buffer pool plus the ring of NV12 frame
  buffers (NV12 being the pixel layout the GPU encoder consumes) at this camera
  count. The message gives the arithmetic as well as what the machine actually
  has, so you can see how far short it falls.
- **Too few NVENC sessions.** The GPU driver caps how many encode sessions may
  run at once and each camera needs one of them. Cameras beyond the cap would
  silently fall back to raw capture, which writes every full frame to disk
  uncompressed, so the refusal message states the size that implies for a
  10-minute recording.
- **A stimulation workflow that must not be recorded**: a loop with no starting
  block, a stim block sitting on a camera trigger pin or on the board's UART
  pins, or a single pin driven by two chains at once.

A tight disk is treated as a warning rather than a refusal, and every warning
becomes a "Start anyway?" prompt, so you can proceed with your eyes open.
Existing data in the target folder also prompts before it is overwritten, and
that check deliberately looks beyond the videos: it also counts `blockids.npy`,
`frametimes.npy` and `alignment.npz`, so a folder whose mp4s were moved away for
labeling is still recognized as holding data.

Once the preflight is satisfied, the next thing that happens may be a wait. A
recording runs under whichever firmware matches the session: the recording-only
sketch if no stimulation paradigm has been applied since launch, and the
paradigm's own sketch if one has. Panopticon compares what the board is carrying
against what this recording needs and, if they differ, flashes the right one
before going any further. That is the `Flashing recording-only firmware…` or
`Flashing recording + stimulation firmware…` state, it takes about 30 s with
every control grayed out, and it is the expected behavior rather than a hang —
wait for it rather than force-quitting.

The flash is skipped entirely when the board already holds the right sketch, so
it costs nothing at all in the common order of a session — calibrate first, then
build and Apply the paradigm, then record — because each acquisition finds the
firmware it needs already in place. Where it does show up is when the two kinds
of acquisition are interleaved: a calibration that follows an Apply has to have
the paradigm swapped out, and the recording after that calibration has to have
it swapped back in.

If the flash fails, the recording is refused rather than started, because a
failed flash means the board's contents are unknown and an unknown board may be
holding a stimulation pin high. The dialog says to key off the laser and check
the board before retrying, and that is the order to do it in.

With the right firmware in place, the trigger board still has to acknowledge the
start command before the recording proceeds. If that acknowledgement never
arrives, the cameras are rolled back out of trigger mode, the board is stood
down, and the recording is refused rather than started. The alternative is
worse: an unacknowledged start produces a full-length session containing no
frames, which is a failure you would only discover long after the animal has
gone back in its cage.

A recording normally ends when you turn Record off, but it can also end by
itself. A stimulation paradigm containing an "Ending" block stops the recording
at the moment that block finishes, and stopping by hand at any point does
exactly the same thing.

**9 — Solve.**
Turns the calibration footage into geometry. It runs the calibration solve
(`1_calibrate.py`, launched through `uv run`) over the recorded calibration
videos and writes the result — each camera's intrinsics and the poses of all the
cameras relative to one another — into `calibration.toml` in the calibration
folder. That file is then copied into the recording folder too, so every session
carries its own copy of the calibration it was shot with instead of pointing at
one that might later be replaced.

Several things about Solve are worth knowing in advance, because nothing on
screen advertises them:

- It takes several minutes, and the button is disabled for the whole of that
  time. Nothing else about the app changes state, so the status bar and the
  state label are the only indication that it is working. The state label reads
  `CALIBRATING...` in purple during a solve, even though no camera is capturing.
- It resolves which folder to work on from the metadata fields **as they read
  now**, not from the calibration that was most recently recorded. Change the
  date or a subject ID after capturing and Solve will go looking somewhere else.
- It refuses with a dialog if the calibration folder does not exist, contains no
  mp4 files, or the profile's board config file is missing.
- If the calibration run saved `codet_frames.json` — the frame indices at which
  the coverage display saw the board in two or more cameras at the same time —
  the solve decodes only those frames, which is much faster than scanning
  everything. Without that file it falls back to a full scan at the script's
  default of every third frame (`--skip 3`).
- It is unavailable while an acquisition or an encode is in progress, and a
  second click while a solve is already running is ignored rather than queued.

**10 — Snapshot.**
Saves one still image from every camera at once. It is the right tool for
documenting how the arena was set up on a given day, and for checking focus and
framing honestly: the preview is downsampled 3x, which hides precisely the
softness you would be looking for. Each grab thread is asked to stash its next
**full-resolution** frame, and one PNG per camera is written to:

```
<output>/<date>/<mouse_1>_<mouse_2>/snapshots/<date>_<HHMMSS>/cam<N>.png
```

The status bar then reports how many cameras were saved and where they went.
Snapshots are taken from the raw frame, so the brightness and contrast sliders
do not affect them: what a snapshot shows is what the camera actually delivered.

**11 — Stimulation.**
Opens the optogenetic stimulation editor — the second of Panopticon's two
windows, described in its own section further down this page. It is not modal,
so you can leave it open beside the main window and carry on working in both.

One thing is worth understanding before you open it. A stimulation sequence is
**compiled into the Arduino's firmware** rather than streamed over the wire
while it runs, so editing the canvas changes nothing at all until **Apply to
Arduino** is pressed, and that takes about 30 s. Firmware also survives closing
the app, which is why Panopticon reflashes a recording-only sketch at every
launch whenever the board is not already known to be carrying one. Stimulation
is therefore opt-in per session, and a paradigm from last week cannot fire just
because someone pressed Record. To reuse a paradigm, **Load** it and Apply it
again.

Within a single session, though, you only ever Apply once per paradigm. Once you
have applied one, Panopticon holds two sketches — the recording-only one and
yours — and puts whichever the next acquisition calls for onto the board by
itself, so **Apply is needed only when the stimulation paradigm itself
changes**, not before every run. That automatic swap is what the `Flashing …
firmware…` states described under Calibrate (7), Record (8) and the state label
(16) are doing.

**12 — Calibration coverage HUD.**
The live ChArUco coverage graph — a heads-up display, or HUD, that tells you how
much of the calibration you have actually captured while you are still holding
the board. It is hidden when the app is idle and appears in this slot when a
calibration starts. If OpenCV is not installed, or the profile's board config
file is missing, it stays hidden for the whole run and the calibration records
video without any live feedback.

Board detection for the HUD runs on its own worker thread at roughly 30 Hz, and
it works on **full-resolution** frames rather than the downsampled preview
copies. That matters because obliquely-mounted cameras see the board at a steep
angle and need every pixel to resolve its corners at all. These are the same
full-resolution frames that go into the recorded video, so the HUD is judging
the footage the solve will later read rather than a cheaper copy of it. The next
section explains what the display is showing.

### Display

The bottom of the sidebar is for looking rather than changing. Two sliders
adjust how the preview is drawn on screen, a progress bar reports the work that
happens after a recording stops, a label gives the application's current state,
and the status bar runs along the foot of the window itself. None of it touches
the data.

**13 — Brightness.**  **14 — Contrast.**
Both sliders range from -100 to +100, and both affect the **preview only**.
Contrast is applied first, scaling each pixel about mid-gray by
`(100 + contrast) / 100`; brightness is then added on top, and the result is
clipped into the 0-255 range.

They change nothing about the recorded video, the snapshots, or board detection.
The coverage HUD reads frames straight from the grab threads and the solve reads
the recorded file, so both bypass the sliders entirely — a board that looks
brighter here is not any easier to detect.

If the board really is too dark to detect, the fix is either physical or in the
rig profile, never in these sliders. Better infrared illumination is the first
thing to reach for, because more photons genuinely improve the signal-to-noise
ratio. After that comes `calibration_exposure_us`, and only then
`calibration_gain_db`, which amplifies the noise along with the signal.

Calibration has plenty of room for a longer exposure. In trigger mode a camera's
frame-rate timer only starts once exposure ends, so the longest exposure that
still lets a camera answer every trigger — its *exposure ceiling* — is roughly
27 ms at the 30 fps calibration rate, against about 3.94 ms for a 100 fps
recording. That is about seven times the light budget, for free, which is why a
`calibration_exposure_us` of 15000 (15 ms) is safe. Panopticon works the ceiling
out for itself rather than trusting the profile, and clamps the exposure to 90%
of it — about 24.5 ms at 30 fps and about 3.5 ms at 100 fps — so a margin always
survives before a camera would start ignoring triggers. When a clamp happens it
is announced on the console, because exceeding the ceiling otherwise fails
silently.

What actually limits calibration exposure is motion blur, not the ceiling: at
15 ms a briskly waved board smears and its ChArUco corners stop resolving at
all. Move the board slowly and pause at each pose.

**15 — Progress.**
The progress bar stays hidden unless a post-recording stage is running, which is
about the only time Panopticon asks you to wait for it. It reads `Encoding N/M`
while the videos are being finalized, and `Aligning N/M` if a post-hoc alignment
pass is needed, where M is the camera count.

How long that wait is depends on the encode mode in the profile. With real-time
GPU encode — the default — the frames were already compressed during capture,
so finalizing is only a stream-copy remux into mp4 and finishes in seconds. In
the raw fallback there is a full encode pass still to do, and it takes
correspondingly longer.

**16 — State.**
The state label at the bottom-right of the sidebar names what the application is
doing at this moment, color-coded so it reads at a glance:

| Text | Meaning |
|---|---|
| `IDLE` (gray) | nothing running; cameras in free-run preview |
| `CALIBRATING` (blue) | calibration acquisition in progress |
| `RECORDING` (red) | recording in progress |
| `ENCODING` (amber) | finalizing video after a stop |
| `ALIGNING` (amber) | post-hoc trigger alignment re-encode |
| `CALIBRATING...` (purple) | a Solve is running — no capture |
| `Switching cameras…` (amber) | a profile change is closing every camera and reopening it |
| `Finishing…` (amber) | an acquisition is being stopped and closed out |
| `Clearing stim firmware…` (amber) | the launch-time reflash back to the recording-only sketch |
| `Flashing recording-only firmware…` (amber) | the board is being given the stimulation-free sketch |
| `Flashing recording + stimulation firmware…` (amber) | the board is being given the applied paradigm's sketch |

The first six of those are states the application settles into and stays in. The
last five are blocking operations: while one of them is on screen every control
that could start something is disabled — all of those listed at the end of this
page, which is everything except the Stimulation button and the two display
sliders — the cursor becomes a wait cursor, and the preview freezes on its
last frame. Nothing is wrong; something is being done that cannot be interrupted
halfway.

The two `Flashing …` states are the ones worth recognizing on sight, because
they are by far the longest and they arrive at the least convenient moment —
straight after you press Calibrate or Record, quite possibly with an animal
already in the arena. All they mean is that the trigger board is being given the
firmware this particular acquisition needs, which takes about 30 s and happens
only when the board is not already carrying it. A calibration always calls for
the stimulation-free sketch, and a recording for whichever sketch matches the
session, so an Apply followed by a calibration and then a recording will show
`Flashing recording-only firmware…` before the calibration and `Flashing
recording + stimulation firmware…` before the recording. The right response is
to wait rather than to force-quit; the acquisition starts by itself when the
flash finishes, or is refused with a dialog if it fails.

**17 — Status bar / capture health.**
The status bar along the foot of the window carries whatever the application has
to say at the time: where a snapshot was written, how a solve is progressing,
the encode summary after a recording, the results of an alignment pass. At idle,
before the first event of the session, it is simply empty.

During a recording it takes on a more specific job and reports **how far behind
real time the worst camera is**, refreshed about three times a second:

- under 0.25 s: `Capture healthy — keeping up with the trigger (max lag N ms)`
- under 1 s: `CAPTURE FALLING BEHIND: camN is X s behind real time and growing.
  Close other applications.`
- otherwise: `CAPTURE X s BEHIND REAL TIME (camN). Frames will be lost when the
  buffer pool fills. Stop and investigate.`

The number appears even when everything is healthy, so the reassurance can be
checked rather than taken on trust. It is computed per camera by comparing that
camera's own hardware timestamp with the moment its frame arrived, referenced to
the first frame of the run, which means it never depends on the host clock being
right. The figure comes from the real-time kick-out path, so if `realtime_kick`
is disabled in the profile it sits at zero and carries no information at all.

This readout exists because the failure it catches is otherwise invisible. A
grab loop running a fraction of a millisecond over budget loses nothing at
first — the driver's buffer pool absorbs the deficit — so there is no error and
no dropped frame for as long as ten minutes, while every frame retrieved gets
staler. A large lag also means the preview is showing the past, not the present,
which matters when aiming cameras.

After a recording stops, the same bar reports the encode summary: frame counts,
average frame rate, and any camera that produced no usable video. Problems are
also written to `WARNINGS.txt` in the recording folder, because a dialog is
dismissed and forgotten. A clean session produces neither a dialog nor that
file.

---

## The calibration coverage HUD

This is what you watch while a calibration is running and you are standing in
the arena with the board in your hands, trying to decide whether you have moved
it around enough. It answers the one question that would otherwise wait for a
solve several minutes long: is there enough data here to recover this rig's
geometry?

The display is one numbered node per camera, arranged on a ring, with an edge
drawn between every pair of nodes. A node reports what that one camera has seen;
an edge reports what a *pair* of cameras has seen at the same time, which is the
quantity that stereo calibration actually depends on. When every condition is
satisfied the whole graph freezes and the caption reads `READY`, and that is your
cue to stop.

The four figures below are rendered illustrations of particular states rather
than captures of one continuous session — which is why the elapsed timer reads
`0:00` in all of them.

| | |
|---|---|
| ![Coverage graph, nothing detected](images/calib_stage_1_start.png) | ![Coverage graph, partial coverage](images/calib_stage_2_partial.png) |
| **Stage 1.** Nothing detected yet. Every edge is dark and thin, every node is dull. `paired 0/250 grid 0/3`. | **Stage 2.** Cameras 1 and 3 are lit — they can see the board right now. Edges have begun to thicken. `paired 60/250 grid 2/3`. |
| ![Coverage graph, nearly ready](images/calib_stage_3_nearly.png) | ![Coverage graph, READY](images/calib_stage_4_ready.png) |
| **Stage 3.** Nearly there. Camera 5 is lit, most edges are bright and thick, and the 1-4 edge is still thin — that pair has barely seen the board together. `paired 200/250 grid 3/3`. | **Stage 4.** Every condition met. The whole graph freezes solid white and the caption reads `READY — m:ss`, with the elapsed time stopped at the moment it got there. |

Everything on the graph is counted in *detection ticks*. One tick is a single
pass of the board detector across the current frame from every camera, repeated
about 30 times a second, so a tick is a moment in time rather than a recorded
frame. With that in mind, here is what each element of the display is telling
you:

- **A node lights up** (cyan, with a brighter rim) when that camera can see at
  least 4 board markers in the current detection tick. The glow decays over
  about 0.4 s, so it reads as a pulse rather than a latch.
- **An edge thickens and whitens** as that pair of cameras accumulates ticks
  where both saw at least 5 markers. It saturates at 200 shared ticks. A thin
  edge is a pair that has not yet seen the board together — walk the board
  through the space those two cameras share.
- **The 2x2 badge** on the bottom-right of each node is that camera's spatial
  coverage. Each detection's marker centroid is binned into one of four
  quadrants of that camera's field of view, and the cell turns green the first
  time it is hit.
- **The caption** reads `<elapsed>  paired <worst>/250  grid <worst>/3`. Both
  numbers are the worst camera, not an average, so they move only when the
  camera that is furthest behind improves.

**READY requires three things at once:**

1. Every camera has at least **250** co-detection ticks — ticks where it and at
   least one other camera both saw the board.
2. Every camera has hit at least **3 of its 4** field-of-view quadrants.
3. The graph is **connected** through edges of at least **80** shared ticks, so
   every camera is linked to every other directly or through a chain.

The quadrant condition exists because the first and third can be satisfied by
waving the board in one spot in front of all the cameras. That produces poorly
constrained intrinsics: the solve looks fine and behaves badly toward the frame
edges.

Because these counts are in detection ticks rather than recorded frames, treat
them as relative coverage signals and not as totals you could go looking for in
the finished videos. Once READY is reached detection stops, and the elapsed
timer freezes at the point it got there, so you can see how long the run took.

Turning Calibrate off hides the graph and, on the way out, writes
`codet_frames.json` beside the videos: the frame indices at which two or more
cameras saw the board at once. That is the file Solve picks up later so that it
can decode only the frames worth looking at instead of scanning every frame of
every video.

---

## The stimulation editor

![The stimulation editor with numbered callouts](images/stim_annotated.png)

This is where an optogenetic stimulation paradigm gets built, and it is a window
you work in before a recording rather than during one — uploading a paradigm to
the board is refused while an acquisition is running. A paradigm is drawn as a
graph of blocks: each block drives one output pin with one square wave for one
fixed duration, and arrows chain blocks together into a sequence that runs from
the moment the recording starts.

One fact shapes everything else about this window. The graph is **compiled into
the trigger board's firmware**, not streamed to the board while it runs, so
nothing you draw here reaches the hardware until **Apply to Arduino** compiles
and uploads it — a step that takes about 30 s. The same Arduino Mega both
triggers the cameras and delivers the stimulation, which is why the editor is
strict about which pins a block may use.

**1 — Node canvas.**
The canvas is the graph itself. Blocks are dragged around to arrange them, and
each one carries four connector ports — top, bottom, left and right — so arrows
can be routed whichever way reads most clearly.

- **Drag from a port** to another block's port to create an arrow. A port within
  snapping distance highlights and the arrow lands on it. A block can have at
  most one **outgoing** arrow — that is what makes a chain a sequence — but any
  number of incoming ones. Dragging from a port on a block that already has an
  outgoing arrow moves the block instead of starting a new arrow.
- **Click** a block to select it and load its values into the fields below.
  **Drag on empty space** for a rubber-band selection, shift-drag to add to it.
- **Delete** removes the selected blocks and arrows. **Ctrl+C / Ctrl+V** copies
  and pastes blocks at the cursor. **Middle-drag** pans, **scroll** zooms.
- Each block shows its pin in the header, then its frequency, pulse width,
  duration, and the resulting mode: `10% duty`, `constant ON`, or `pin LOW`.
  Blocks are tinted by pin number.
- A **filled red dot** in a block's top-right corner means this block starts its
  chain; a white ring around that dot means it was pinned by hand. A **hollow
  amber dashed ring** means the block sits in a loop with no start, so it would
  never run. A **black dot** to the left of the start dot marks the "Ending"
  block. Blocks that are not chain starts are drawn faded, so the block that
  begins the sequence is the one that reads as live.

**2 — Waveform preview.**
A small plot of one second of the wave that the current Freq and PW fields would
produce. It follows what is typed into those fields rather than whichever block
is selected, so it shows the values you are about to commit. Its own section at
the end of this page explains how to read it, and the one mistake it exists to
catch.

**3 — Pin.**
Which output pin this block drives — the pin for a new block, or for the block
currently selected. The field has no default on purpose: an empty Pin is refused
rather than quietly treated as pin 0, which on a Mega is UART RX0 and would
garble the link between the host and the board.

Two classes of pin are rejected outright, and the refusal applies equally at
Apply, Test and Record: the camera trigger pins named in the rig profile, and
pins 0 and 1, the board's own serial lines. A stimulation waveform on a trigger
line injects extra rising edges into one camera, so that camera counts more
frames than the others and its *trigger ordinals* — the running count of trigger
pulses that every frame carries with it, and that `blockids.npy` records — stop
lining up with everyone else's. That a given ordinal means the same instant on
every camera is the assumption every alignment step in the pipeline rests on.

**4 — Freq (Hz).**
The pulse frequency of this block's square wave. A frequency of `0` is
legitimate rather than an error: it holds the pin LOW for the block's whole
duration, which is how a gap between stimulation periods is written.

**5 — PW (ms).**
Pulse width in milliseconds — how long the pin stays HIGH within each cycle.
Frequency and pulse width are independent fields, so the duty cycle they imply
is worth checking in the preview before committing them.

**6 — Dur (s).**
How long this block runs, in seconds, before the chain moves on to the next
block.

Pressing Enter in any of these four fields applies the values to the selected
block. With nothing selected, Enter creates a block, the same as **Create
Block**.

**7 — Starting.**
Pins the selected block as the start of its group, and is disabled until exactly
one block is selected.

Most of the time you will not need it, because any block with no incoming arrow
already starts a chain. A pure loop has no such block, so it must be pinned by
hand or it compiles to nothing at all. There can only be one start per
weakly-connected group: pinning one clears the flag on the others in that group,
and if an arrow later merges two pinned groups, one of the flags is dropped so
that the canvas cannot claim something the compiler would not do.

**8 — Ending.**
Marks the one block whose completion **stops the recording**. There is at most
one Ending block per canvas, and like Starting it is disabled until exactly one
block is selected.

The distinction to hold on to is that it stops the *recording*, not the chain. A
looping chain carries on running until the recording ends, so to bound a loop
you either give it an Ending block or pair it with a parallel timer chain that
carries one. The countdown is armed on the host when Record starts and the board
is never asked to report back; when the time is up, the host turns Record off
exactly as a hand would.

**9 — Status line.**
A running commentary on what the current graph would do and what is wrong with
it. In normal use it reports the stop time (`Recording will stop 15 s after
start.`), load and save confirmations, upload progress and test countdowns. In
red it reports the blocking problems: blocks forming a loop with no start, a pin
driven by two chains at once, an Ending block that no chain reaches, or
unparseable field values. (The text in the figure above is placeholder copy for
the illustration.)

**10 — Test.**
Runs the paradigm on the bench. The start command is sent with **zero camera
pins**, so the stimulation outputs fire while no camera is triggered and nothing
is recorded — this is how you confirm that a paradigm does what you meant
before an animal is involved. The button becomes **Stop Test** while a test is
running and the status line counts down; a looping paradigm has no end time, so
it runs until you stop it.

Test runs whatever is on the board, not what is on the canvas. If the canvas has
changed since the last upload it offers to upload first, because otherwise it
would silently test the previous paradigm and tell you nothing about the one you
are looking at. It borrows the main window's serial connection rather than
opening its own, which is what keeps a test from resetting the board. If the
board does not confirm the stop, the status line says so in red and a dialog
appears — during a bench test there is no recording whose end would stop the
output, so that single write is the only thing that does.

**11 — Apply to Arduino.**
Compiles the graph into an `.ino` sketch and uploads it to the board, which
takes about 30 s. This is the step that makes a paradigm real: nothing on the
canvas reaches the hardware until it runs. Apply and Test are both disabled
during the upload, and the serial port is handed over to the upload tool and
reclaimed again afterwards.

On success the status line reads `Upload successful — press Record to run
paradigm.` and the sketch's hash is recorded, which is what lets the next launch
tell whether the board still carries a paradigm.

Apply is refused while an acquisition is running, and refused for any graph
carrying one of the blocking problems listed above.

Three more buttons share that row. **Load** and **Save** read and write the
graph as JSON, defaulting to the output directory, and **Clear** empties the
canvas after a confirmation. Saving is separate from applying: a saved file is a
paradigm that can be reloaded, while the board only ever holds what was last
uploaded.

A recording started with blocks on the canvas also writes `stim_paradigm.json`
and `stim_paradigm.ino` into the recording folder, including the firmware hash
and whether it matches what was uploaded this session, so a session describes
the stimulation it delivered without needing the editor.

---

## The waveform preview

The small plot in the stimulation editor (2) is worth a glance every time you
type numbers into Freq and PW. It draws one second of the wave those two fields
would produce and captions it, so the shape of the stimulation is visible before
it is compiled into firmware and delivered to an animal.

It earns its space because frequency and pulse width are independent fields, and
neither of them shows you the duty cycle the pair implies. A pulse width at or
above the period is an easy thing to type by accident — 10 Hz with a 100 ms
pulse is one arithmetic slip from a 10 ms one — and it is impossible to spot from
the numbers alone.

The arithmetic behind the caption is simple. The period is `1000 / freq_hz`
milliseconds, and the duty cycle as a percentage is
`pulse_width_ms x freq_hz / 10`.

These five figures are rendered illustrations of specific parameter pairs.

| Fields | Preview |
|---|---|
| 10 Hz, 10 ms — period 100 ms, 10% duty | ![10 Hz, 10 ms pulse](images/wave_10hz_10ms.png) |
| 40 Hz, 5 ms — period 25 ms, 20% duty | ![40 Hz, 5 ms pulse](images/wave_40hz_5ms.png) |
| 20 Hz, 25 ms — period 50 ms, 50% duty | ![20 Hz, 25 ms pulse](images/wave_20hz_25ms.png) |
| 0 Hz — pin held LOW | ![0 Hz](images/wave_0hz.png) |
| 10 Hz, 100 ms — period 100 ms, 100% duty | ![10 Hz, 100 ms pulse](images/wave_10hz_100ms.png) |

The first three are normal trains: same shape, different density and duty. A
train denser than 400 cycles per second of preview is drawn as a band rather
than individual pulses.

`0 Hz` (or a pulse width of 0) means the pin is held LOW for the block's
duration. That is a legitimate and common block — it is how a rest interval
between stimulation periods is written.

**The 100% case is the one to watch.** 10 Hz with a 100 ms pulse width is a
100 ms period fully filled: the pin goes HIGH and stays HIGH for the whole
block. It is not a 10 Hz train and it is not an error. The preview draws the
single rising edge, glows the border red, and captions it
`100% duty — constant ON, not 10 Hz`. A pulse width *longer* than the period
does the same thing physically, and is captioned
`pulse 150 ms > period 100 ms`.

The firmware drives the pin constantly HIGH in both cases rather than rejecting
them, so that is what the preview draws and what the block's own body text says
(`constant ON`). A block that refused to represent them would hold the pin LOW
for its whole duration instead — a silent nothing where stimulation was
intended, which is not visible anywhere in the recorded data.

---

## Controls that hide, and controls that disable

The interface changes shape as it works. A few things appear only when they have
something to report, and rather more stay visible but refuse to be pressed while
they would do the wrong thing. The difference is deliberate: a control that is
present but grayed out tells you it exists and is simply not available yet,
whereas one that vanished would leave you hunting for it. This is the section to
check when a control is not where you expected to find it.

**Hidden until something is running:**

- The **coverage HUD** (12) exists only while a calibration is in progress, and
  only if OpenCV and the board config are both available.
- The **progress bar** (15) exists only during encoding or alignment.
- The **status bar** (17) has no message until the first event that produces
  one.

**Disabled rather than hidden:**

- **Record** is disabled while Calibrate is on, and Calibrate is disabled while
  Record is on.
- Both toggles are disabled during encoding and alignment.
- **Solve** is disabled for the duration of a solve, and refuses while
  acquiring or encoding.
- During any blocking operation — a profile switch, the end-of-acquisition
  finalize, the startup firmware flash, or the per-acquisition firmware flash
  that precedes a calibration or a recording — the profile dropdown, output
  directory, both toggles, Solve, Snapshot and every metadata field are disabled
  and the cursor becomes a wait cursor. The state label (16) names which
  operation it is, and the firmware flashes are the long ones at about 30 s.
- The metadata fields and the output directory go read-only for the duration of
  an acquisition, since they name the folder being written.
- In the stimulation editor, **Starting** and **Ending** are disabled until
  exactly one block is selected; **Apply** and **Test** are both disabled while
  an upload is in flight, and **Apply** is disabled while a test is running.

**At launch**, a hardware check runs quietly in the background and raises a
dialog only if it finds something worth mentioning: fewer than 4 physical CPU
cores, under 16 GB of RAM, under 500 GB of free disk, a measured disk write
speed under 500 MB/s, or no `h264_nvenc` encoder in the bundled ffmpeg. The
dialog is advisory and stops nothing; it exists so that an underpowered machine
is known about before a session rather than discovered during one.

**On quit**, if a session is still in progress the app asks for confirmation and
then deletes the incomplete data, on the grounds that a half-written session is
worse than none. It also stands the trigger board down whether or not an
acquisition was running, so closing the window cannot leave a paradigm — or a
laser — running behind it, and it says so loudly if the board does not accept
that stop.
