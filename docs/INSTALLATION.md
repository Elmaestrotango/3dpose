# Installation

This page takes a rig that is already wired up to a first working launch. It
assumes no terminal experience: every command is written out in full, with the
output you should expect so you can tell whether the step worked.

Physical wiring — mounting the cameras, running the trigger line from the
microcontroller to each camera's trigger input, powering everything — is not
covered here; follow the camera vendor's I/O documentation and the
microcontroller board's pinout.

Contents:

1. [What the rig needs](#1-what-the-rig-needs)
2. [Install the software](#2-install-the-software)
3. [Verify it works](#3-verify-it-works)
4. [Troubleshooting](#4-troubleshooting)

---

## 1. What the rig needs

There is no fixed parts list, because every requirement below follows from four
numbers you choose: resolution, bit depth, frame rate, and camera count. The
arithmetic is given so you can size your own rig.

One quantity appears in every calculation. For mono8 (one byte per pixel):

```
frame_bytes = width x height
1920 x 1200 = 2,304,000 bytes  (~2.3 MB per frame per camera)
```

### Cameras

Basler cameras work today, through pypylon. `gui_app/backends/basler.py` is the
only module that imports pypylon; everything else is vendor-neutral. Another
vendor means writing one file against the `CameraBackend` contract in
`gui_app/backends/__init__.py` and registering it in `load_backend()`. The API
is the easy part. The contract requires three guarantees:

- a per-frame **monotonic trigger ordinal** that is the same value on every
  camera for a given trigger, and that survives a stream restart (Basler: the
  GigE Vision BlockID). Cross-camera alignment has nothing to align on without
  it.
- a **buffer pool** deep enough to absorb network jitter, plus a counter that
  reveals when it has run dry.
- **zero-copy access** to the pixel data. A copying accessor puts a full-frame
  memcpy on the per-frame hot path with the GIL held, which is enough on its own
  to lose frames on every camera. Measure it before assuming it is affordable.

Each camera needs a **hardware trigger input**. In triggered mode the app sets
`TriggerSelector=FrameStart`, `TriggerMode=On`, `TriggerSource=Line1`,
`TriggerActivation=RisingEdge`.

Every camera must be set to **Mono8** and to the **same resolution**. The
capture path assumes 8 bits; a 12-bit frame arrives as 16-bit data and is
truncated mod 256 with no error at all, producing a full-length, perfectly
aligned, visually shredded recording. Opening refuses on both mismatches rather
than recording that.

### A trigger source

An Arduino- or Teensy-class microcontroller on a serial port, with one output
pin per camera (the profile's `trigger_pins`) and, if you use optogenetic
stimulation, one more pin for the stimulus driver. The reference firmware
targets `arduino:avr:mega`.

**Why hardware triggering rather than software sync.** Software sync means each
camera is told to start and then free-runs on its own crystal oscillator. Those
oscillators differ, so the cameras drift apart continuously, and there is no
per-frame correspondence to recover — only an estimate that gets worse the
longer the recording runs. A shared TTL edge instead makes frame *N* of every
camera the same instant of the world, to within the cameras' trigger-to-exposure
jitter. That matters for 3D because triangulation intersects rays from several
views taken at one instant; if the views are not simultaneous, the rays of a
moving animal do not meet at its true position, and the error scales with the
animal's speed times the timing offset.

Hardware triggering also gives every frame a shared ordinal. Cameras lose
packets independently, so frame *i* of one video is not frame *i* of another,
but the trigger ordinal is, and it is recorded per frame. That is what makes
alignment exact instead of approximate.

### Network

Derive the bandwidth before choosing ports:

```
bits per frame  = width x height x bits per pixel
                = 1920 x 1200 x 8            = 18,432,000 bits
bits per second = bits per frame x frame rate
                = 18,432,000 x 100           = 1.84 Gbit/s per camera
```

| Format | Bytes per frame | Payload rate |
|---|---|---|
| 1920x1200 mono8, 100 fps | 2,304,000 | 1.84 Gbit/s |
| 1920x1200 mono8, 30 fps | 2,304,000 | 553 Mbit/s |
| 1280x1024 mono8, 100 fps | 1,310,720 | 1.05 Gbit/s |

That is payload only, before GVSP, UDP, IP and Ethernet headers.

**Port speed and cameras per port.** Add up the cameras sharing a port and keep
the total comfortably under line rate — a link running near capacity turns
ordinary bursts into packet loss. Three 1920x1200 mono8 cameras at 100 fps is
5.53 Gbit/s, so that group needs a 10 GbE port and still leaves about 45%
headroom. At 30 fps, or at lower resolution, one camera fits on a 1 GbE port;
two at 100 fps do not.

**Jumbo frames.** The reference camera settings file sets
`GevSCPSPacketSize 9000`, so each GVSP packet carries 9000 bytes. Every device
in the path — NIC and any switch — must accept an MTU of 9014 bytes (9000 of
payload plus headers), or those packets are dropped. Set the adapter's *Jumbo
Packet* property to 9014 and its *Receive Buffers* to the maximum.

**Inter-packet delay.** `GevSCPD` (10000 in the reference settings file) spaces
out one camera's packets so that cameras sharing a port do not burst into each
other. Zero means they collide. It scales with cameras per port and link speed.

**Cabling.** A 10 GbE copper run needs Cat6a or better.

### CPU

Three things consume CPU, and only the first two are visible inside the
application:

1. **One grab thread per camera.** Per frame it retrieves the driver buffer,
   copies the gray plane into an NV12 ring buffer, hands that buffer to the
   encoder, and releases the driver buffer. Measured at 1920x1200 that is about
   0.8 ms of work per frame per camera, roughly 8% of one core at 100 fps. The
   whole loop iteration must finish inside one frame period (10 ms at 100 fps).
   When it does not, nothing errors: the driver buffer pool absorbs the deficit
   and every retrieved frame is a little staler than the last, until the pool
   runs out.
2. **One encoder thread per camera.** NVENC does the compression on the GPU, so
   these threads mostly move bytes.
3. **GigE packet reassembly**, which is in the network stack rather than the
   app. With `gige_driver: socket` the packet resends run in user space; that
   costs more host CPU than the in-kernel driver but recovers lost packets
   instead of discarding the frame. Each port's receive work runs as deferred
   procedure calls, and if the adapter presents a single RSS receive queue, one
   core carries the entire port. Three 1920x1200 cameras at 100 fps is about
   78,000 packets/s per port, measured at 46% of a single core against a 4%
   average across 24 cores.

**How it scales.** Threads grow at two per camera plus the UI — about 13 busy
threads at six cameras. They share one GIL, so the binding constraint is
GIL-held work per thread per frame, not total core count: up to about 300 µs per
thread per frame is safe even at 17 threads, while ~1000 µs breaks the 10 ms
budget at 11. Cores beyond the grab and encode threads mostly help the network
stack.

Fewer than 4 physical cores raises a warning at startup.

### RAM

Memory is dominated by two buffer allocations, both linear in camera count.

```
driver buffer pool = n_cams x MaxNumBuffer x frame_bytes
NV12 ring          = n_cams x (kick_max_lag + ENCODE_QUEUE_DEPTH + 64)
                            x 1.5 x frame_bytes
```

`MAX_NUM_BUFFER = 1000` (`gui_app/camera_manager.py`) — 10 seconds of slack at
100 fps. `ENCODE_QUEUE_DEPTH = 200` (`gui_app/grab_thread.py`). The factor 1.5
is NV12: a full-size luma plane plus a half-size chroma plane held at a constant
128. The `+ 64` is spare slots so a buffer cannot be reused while it is still in
flight. The ring exists only in real-time encode mode; in kick-out mode it is
sized as above, otherwise it is `ENCODE_QUEUE_DEPTH + 4` buffers per camera.

Worked example — 6 cameras, 1920x1200, `kick_max_lag: 480`:

```
pool  = 6 x 1000 x 2,304,000 B                   = 12.9 GiB
ring buffers per camera = 480 + 200 + 64         = 744
ring  = 6 x 744 x (1920 x 1800) B                = 14.4 GiB
total                                            = 27.3 GiB
```

The same settings at 9 cameras: 19.3 + 21.5 = **40.9 GiB**. At
`kick_max_lag: 240` the ring halves to 1.62 GiB per camera, giving 22.6 GiB at
6 cameras and 33.9 GiB at 9.

The application does this arithmetic itself at Record time, against the actual
number of cameras open, and **refuses to start if it will not fit** in available
memory:

```
Not enough RAM for 9 cameras: 40.9 GiB needed (19.3 pylon pool + 21.5 NV12
ring), 31.2 GiB available. Lower MaxNumBuffer or kick_max_lag, or close other
applications.
```

Above 75% of available memory it warns and asks before proceeding. Less than
16 GB total raises a warning at startup.

### GPU

An NVIDIA GPU with NVENC. Encoding runs through PyNvVideoCodec, and the CUDA
runtime arrives as a Python dependency (`nvidia-cuda-runtime-cu12`), so a CUDA
toolkit installation is not required.

A recording needs **one concurrent encode session per camera**. The driver caps
concurrent sessions, and that cap has moved across driver generations (2, then
3, 5, 8, 12), so it is probed rather than assumed — `nvenc.probe_max_sessions`,
called from `hardware_check.nvenc_session_capacity` before each recording. The
budget is one session per camera, plus `encode_parallel` for the remux jobs,
plus one warm-up session. If the grant is below the camera count, Record
refuses:

```
NVENC granted only 8 concurrent sessions but 9 cameras need one each. The
driver caps this. Cameras beyond the cap would silently fall back to raw.bin at
~129 GiB per 10 min each. Use the raw profile, or record fewer cameras.
```

With no working NVENC the startup check warns (`NVENC not available — encoding
will fall back to CPU (much slower)`) and the capture path falls back to writing
raw frames to disk. ffmpeg itself is bundled with the Python dependencies
(`imageio-ffmpeg`); there is nothing to install separately.

### Disk

Two very different rates, depending on `realtime_encode`.

**Real-time GPU encode (the default).** H.264 at qp 21 runs about 4.6 KB per
frame:

```
6 x 100 x 4600 B = 2.8 MB/s  ~= 10 GB per hour
```

A session is a few GB.

**Raw fallback (`realtime_encode: false`).** Every frame is written
uncompressed:

```
n_cams x fps x frame_bytes
6 x 100 x 2,304,000 B = 1.38 GB/s   (83 GB per minute)
9 x 100 x 2,304,000 B = 2.07 GB/s
```

Above 1.5 GiB/s the preflight advises spreading the output across drives,
because a single consumer NVMe drops to roughly 1.6 GB/s once its SLC cache is
exhausted. Startup warns below 500 GB free, and below 500 MB/s measured write
speed (it writes and deletes a 16 MB file to find out).

### Operating system

Windows. The Windows-specific pieces are `PylonGigEConfigurator` and the
inbound firewall rule, the `configure_nic.ps1` and `make_shortcut.ps1` scripts,
and the two performance probes `probe_gil_wait.py` and `probe_native_cpu.py`.
The capture path itself does not depend on them: the binary-file flag is
resolved with `getattr(os, "O_BINARY", 0)`, the `subprocess.STARTUPINFO` use in
`encode_worker.py` is guarded by `sys.platform`, the serial port is a profile
field, and `arduino-cli` is found via PATH or an environment variable. NVENC and
pypylon both support Linux.

---

## 2. Install the software

Everything in this section is typed into PowerShell.

**Opening PowerShell:** press the Windows key, type `powershell`, press Enter. A
window opens with a prompt like `PS C:\Users\you>`. Type a command, press Enter,
wait for the prompt to come back. Right-click pastes.

**Opening it as Administrator** (needed in step 5): press the Windows key, type
`powershell`, right-click *Windows PowerShell* in the results, choose *Run as
administrator*. The window title will say *Administrator*.

### Step 1 — install uv

uv manages the Python version and every Python package, so nothing needs to be
installed into a system Python and conda is not involved. Installation
instructions are at <https://docs.astral.sh/uv/getting-started/installation/>;
the Windows command given there is:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close PowerShell, open it again (the installer adds uv to PATH, and only new
windows see the change), then check:

```powershell
uv --version
```

Expected: a single line starting with `uv` and a version number, followed by a
build hash and the platform. The exact version does not matter. If you get
`uv : The term 'uv' is not recognized`, the new window did not pick up PATH —
sign out and back in.

### Step 2 — install the Basler pylon SDK

Download it from
<https://www.baslerweb.com/en/downloads/software-downloads/> and install it
**before** installing the Python dependencies. It provides the camera driver,
**pylon Viewer** (used in step 6) and **PylonGigEConfigurator** (used in
step 5).

Check it landed:

```powershell
Test-Path "C:\Program Files\Basler\pylon\Runtime\x64\PylonGigEConfigurator.exe"
```

Expected: `True`. If it prints `False`, find the install directory and use that
path in step 5 instead.

### Step 3 — get the code

```powershell
cd $HOME\Desktop
git clone --recurse-submodules https://github.com/Elmaestrotango/3dpose.git
cd 3dpose
```

Expected output ends with something like:

```
Cloning into '3dpose'...
remote: Enumerating objects: ...
Receiving objects: 100% ...
Resolving deltas: 100% ...
Submodule 'campy' (https://github.com/Elmaestrotango/campy.git) registered for path 'campy'
```

`--recurse-submodules` matters: `campy` is a submodule. If you already cloned
without it, run `git submodule update --init --recursive`.

If `git` is not recognized, install Git for Windows from
<https://git-scm.com/download/win> and reopen PowerShell.

Every later command in this page assumes the prompt is inside the repository
(`PS C:\Users\you\Desktop\3dpose>`). To get back there in a new window:
`cd $HOME\Desktop\3dpose`.

### Step 4 — install the Python dependencies

```powershell
uv sync
```

The first run downloads a Python interpreter and about a dozen packages, and
ends with a line like `Installed 13 packages in 42s`. Later runs are instant:

```
Resolved 15 packages in 1ms
warning: Skipping installation of entry points (`project.scripts`) for package
`panopticon` because this project is not packaged; ...
Checked 13 packages in 0.49ms
```

That warning is normal and harmless. The command creates a `.venv` folder inside
the repository holding the interpreter and all packages; nothing is installed
system-wide.

Check the important imports:

```powershell
uv run python -c "import pypylon.pylon, PyQt5, numpy; print('ok')"
```

Expected: `ok`. Anything else means the sync did not complete — run `uv sync`
again and read the error.

This step and the clone are the only ones that need internet. Acquisition,
encoding and calibration all run offline afterwards.

### Step 5 — put the cameras on the network (GigE)

**In an Administrator PowerShell window**, give the adapters and cameras
compatible addresses:

```powershell
& "C:\Program Files\Basler\pylon\Runtime\x64\PylonGigEConfigurator.exe" auto-all
```

Then allow the camera traffic through the firewall. GigE Vision discovery and
streaming are UDP, and Windows blocks inbound UDP to an unknown program by
default. Replace the path with your own:

```powershell
New-NetFirewallRule -DisplayName "PanopticonGigE" -Direction Inbound -Action Allow -Protocol UDP -Program "C:\Users\you\Desktop\3dpose\.venv\Scripts\python.exe"
New-NetFirewallRule -DisplayName "PanopticonGigE-w" -Direction Inbound -Action Allow -Protocol UDP -Program "C:\Users\you\Desktop\3dpose\.venv\Scripts\pythonw.exe"
```

Each command echoes the rule it created, ending with `Enabled : True`. Two rules
are needed because a program-scoped rule matches one executable: `uv run gui.py`
runs `python.exe`, while the desktop shortcut and `_launch.bat` run
`pythonw.exe`.

Now set the adapter properties for each camera port. Open Device Manager
(Windows key, type `device manager`), expand *Network adapters*, right-click the
camera port, *Properties*, *Advanced* tab:

- **Jumbo Packet** — 9014 bytes, matching the camera's `GevSCPSPacketSize 9000`.
- **Receive Buffers** — the maximum the driver offers.

Optionally spread each port's receive processing across cores:

```powershell
powershell -ExecutionPolicy Bypass -File configure_nic.ps1
```

Run it elevated. It defaults to ports named `Ethernet 4` and `Ethernet 5`; list
yours with `Get-NetAdapter` and pass `-Ports "Name1","Name2"`. It prints BEFORE
and AFTER tables of `NumberOfReceiveQueues` and ends with `OK: every port
reports 4 receive queues.` A driver that only applies RSS to TCP will accept the
call and keep fewer queues, which the script reports as `NOT APPLIED on: ...`.
Applying it resets the adapters, so the cameras disappear and re-enumerate over
a few seconds. Never run it during a recording.

Confirm the cameras are reachable: open pylon Viewer from the Start menu. Every
camera should be listed, and each should open and show live video.

### Step 6 — make the camera settings file (.pfs)

A `.pfs` is a plain-text list of camera features that pylon loads into every
camera at open. **It is the only source of exposure and gain** — the application
never sets them for a recording. Build it in pylon Viewer with one camera open,
then save it once and reuse it for all cameras.

The settings that matter, and why:

| Feature | Reference value | Why |
|---|---|---|
| `PixelFormat` | `Mono8` | The capture path assumes 8 bits. Anything wider is truncated mod 256 with no error, so opening refuses with `PixelFormat is Mono12, not Mono8`. |
| `Width` / `Height` | 1920 / 1200 | Must match the profile, and must be identical on every camera — opening refuses if camera 2 disagrees with camera 1. |
| `ExposureAuto` | `Off` | Auto exposure drifts between cameras and can wander past the ceiling below. |
| `GainAuto` | `Off` | Same reason. |
| `ExposureTime` | 3000 µs | Subject to the ceiling below. |
| `Gain` | 6.0 dB | Each +6 dB doubles signal, and noise with it. |
| `AcquisitionFrameRate` | 165 | Sets the exposure ceiling. See below. |
| `AcquisitionFrameRateEnable` | 1 | Leave enabled. |
| `GevSCPSPacketSize` | 9000 | Jumbo packets. Only if the NIC and switches pass an MTU of 9014. |
| `GevSCPD` | 10000 | Inter-packet delay. Spaces one camera's packets so cameras sharing a port do not collide; 0 collides. |

**The exposure ceiling.** In triggered mode the camera's frame-rate timer starts
*after* exposure ends, so the minimum interval between frames is
`exposure + 1/AcquisitionFrameRate`. With the rate limiter at 165 Hz, 6.06 ms of
a 100 fps trigger period is already spent, leaving about 3.94 ms for exposure.
Exceed it and the camera skips every second trigger — you get 50 fps and no
error message anywhere. The application computes the ceiling from the profile's
`trigger_rate_limit`, clamps to 90% of it (about 3.5 ms at 100 fps), and logs
`CLAMPED from ...` when it does. For more light, add illumination first, then
exposure, then gain.

Do not set `trigger_rate_limit: 0`. Turning the limiter off does remove the
exposure ceiling, and it costs 8-15% of frames in transmission: the limiter also
paces sensor readout across 6.06 ms, and without it every camera bursts onto the
link immediately after the shared trigger.

Steps in pylon Viewer:

1. Open one camera.
2. Set the features above in the feature tree (use the search box).
3. **File > Save Features**, and save into the repository as
   `configs/<descriptive_name>.pfs`, for example
   `configs/mono8_1920x1200.pfs`.

Check the result by opening the file in Notepad. It is one `Feature<TAB>value`
per line. Confirm `Width`, `Height`, `PixelFormat`, `ExposureTime`, `Gain`,
`GevSCPSPacketSize` and `GevSCPD` read what you expect.

A `.pfs` saved from a different camera of the same model is fine. It is loaded
with validation disabled, and geometry and pixel format are read back from each
camera afterwards and checked against each other.

### Step 7 — write the rig profile

A profile is one YAML file in `profiles/`. Every `.yaml` in that folder appears
in the sidebar dropdown under its `name`. Copy `profiles/3dpose.yaml` to
`profiles/my_rig.yaml` and edit it.

```yaml
name: my_rig                 # what the dropdown shows
frame_width: 1920            # must match the .pfs Width
frame_height: 1200           # must match the .pfs Height
frame_rate: 100              # trigger rate for recordings, Hz
calibration_frame_rate: 30   # trigger rate for calibration captures
quality: 21                  # NVENC constant quantizer; lower = better, bigger
encode_parallel: 3           # concurrent remux / post-hoc encode jobs
realtime_encode: true        # GPU H.264 during capture; false = raw fallback
realtime_kick: true          # release a trigger to the encoders only once every
                             # camera has caught it, so videos come out aligned
kick_max_lag: 480            # frames of cross-camera lag tolerated; the NV12
                             # ring scales with this
gige_driver: socket          # socket | filter | auto
trigger_rate_limit: 165      # AcquisitionFrameRate applied in trigger mode

pfs_path: "configs/mono8_1920x1200.pfs"
output_dir: "data"
board_config: "configs/boards/charuco_8x8_15mm.yaml"

serial_port: COM3                    # the trigger board's port
trigger_pins: [2, 4, 6, 8, 10, 12]   # one output pin per camera
n_cameras: 6                         # refuse to start unless exactly this many

stim_safe_pins: [53]                 # pins forced LOW from the instant the
                                     # sketch boots; [] if no stim hardware
calibration_exposure_us: 15000       # calibration-only exposure; 0 = keep .pfs
calibration_gain_db: -1              # calibration-only gain; -1 = keep .pfs
```

Field by field:

| Field | Default | What it does |
|---|---|---|
| `name` | file stem | Label in the profile dropdown. |
| `frame_width`, `frame_height` | 1920, 1200 | Frame geometry. Must match what the cameras report after the `.pfs` loads. |
| `frame_rate` | 100 | Trigger rate for recordings. Sets the frame period the grab loop must keep up with, and the H.264 GOP length. |
| `calibration_frame_rate` | 30 | Trigger rate for calibration captures. A slowly waved board gains nothing from 100 fps, and the longer period raises the exposure ceiling from about 3.5 ms to about 24 ms. |
| `quality` | 21 | NVENC constant quantizer. |
| `encode_parallel` | 3 | Concurrent encode/remux jobs after a recording. Counts against the NVENC session budget. |
| `realtime_encode` | `true` | GPU H.264 during capture. `false` writes raw frames and encodes afterwards — about 500x the disk rate. |
| `realtime_kick` | `false` | Gate frames through the cross-camera coordinator during capture, so the videos are trigger-aligned with no post-hoc re-encode. With it off, alignment runs after encoding and re-encodes each video. |
| `kick_max_lag` | 240 | How many frames one camera may lag the others before its missing triggers are force-dropped. Drives the NV12 ring size, so it is the main RAM lever. |
| `gige_driver` | `socket` | `socket` is user-space with reliable packet resends. `filter` is the in-kernel driver: less CPU, but with default resend settings it discards a frame rather than asking for the lost packet again — measured dropping about 23% of frames under six cameras at 100 fps. `auto` leaves pylon's default. |
| `trigger_rate_limit` | 165 | `AcquisitionFrameRate` written in trigger mode. Keep it above the trigger rate; see the exposure ceiling in step 6. |
| `pfs_path` | — | The camera settings file from step 6. |
| `output_dir` | — | Where recordings go. The sidebar's directory button overrides it per machine. |
| `board_config` | — | ChArUco board description used by calibration (`configs/boards/*.yaml`). |
| `serial_port` | `COM3` | The trigger board's serial port. |
| `trigger_pins` | `[2, 4, 6, 8, 10, 12]` | One output pin per camera. Also refused as stimulation pins, since extra edges on one camera would break alignment. |
| `n_cameras` | 0 | Refuse to start unless exactly this many cameras enumerate. `0` disables the check. Camera names are positional by serial-number order, so a camera that fails to enumerate renames every camera after it and attaches the calibration extrinsics to the wrong physical cameras. Set it. |
| `stim_safe_pins` | `[53]` | Pins driven LOW in the first statement of the sketch's `setup()`, before the serial handshake. `setup()` blocks on that handshake until the GUI connects, so a pin not listed here is left floating — which a powered laser driver reads as ON. `[]` on a rig with no stimulation hardware. |
| `calibration_exposure_us` | 0.0 | Exposure used for calibration captures only; the `.pfs` values are restored for recordings. `0` keeps the `.pfs` value. |
| `calibration_gain_db` | -1.0 | Same for gain. `-1` keeps the `.pfs` value. |

Paths may be relative to the repository root or absolute.

### Step 8 — flash the trigger firmware

The sketch is generated rather than stored: `gui_app/stim_compiler.py` compiles
the combined camera-trigger and stimulation sketch from the profile and the node
graph, and `recording_only_sketch()` is that same sketch with no stimulation in
it — camera triggers plus the safe-pin boot guard.

The application flashes it for you. At every launch it builds the
recording-only sketch, compares its SHA-256 against the last sketch it uploaded,
and reflashes if they differ. A stimulation paradigm lives in the board's flash
memory, so it survives closing the GUI, a power cycle and unplugging the USB
cable, and nothing can read it back over serial; the launch-time flash is what
guarantees the board carries no stimulation unless you deliberately applied one
this session.

So installing the firmware means installing the compiler and launching the app:

1. Install the **Arduino IDE** (which bundles `arduino-cli`) or `arduino-cli`
   standalone.
2. Install the AVR core once: `arduino-cli core install arduino:avr`.
3. If `arduino-cli` lives somewhere unusual, set `PANOPTICON_ARDUINO_CLI` to its
   full path. The search order is `PANOPTICON_ARDUINO_CLI`, then PATH, then the
   bundled Arduino IDE locations.
4. Close the Arduino IDE's Serial Monitor. It holds the port, and both flashing
   and recording need it.

Find the board's port for `serial_port`: Device Manager > *Ports (COM & LPT)*,
or

```powershell
[System.IO.Ports.SerialPort]::GetPortNames()
```

which prints e.g. `COM3`.

On the first launch after installing, the log shows:

```
[acq] board may carry a stim paradigm from a previous session — flashing the recording-only sketch
[acq] board flashed with the recording-only sketch; stim is off until you Apply one
```

Flashing takes roughly 30 seconds and the window shows *Clearing stim
firmware...* while it runs. On later launches it is skipped:

```
[acq] board already carries the recording-only sketch (no stim); skipping flash
```

Camera acquisition itself does not need `arduino-cli` — only this flash and the
Stimulation editor's Apply and Test. Without it, launch reports
`arduino-cli was not found` and lists everywhere it looked.

### Step 9 — first launch

```powershell
uv run gui.py
```

A splash panel reads *Panopticon / Loading cameras...*, then the main window
opens with one live preview pane per camera, free-running at about 30 fps.
[OVERVIEW.md](OVERVIEW.md) names every control.

![The main window at idle, six live preview panes and the sidebar](images/main_idle.png)

The console shows one block per camera:

```
[startup] logging to C:\Users\you\Desktop\3dpose\logs\panopticon_20260903_191735.log
[acq] profile: 3dpose
[cam1] 41920544 1920x1200 Mono8
[cam1] extended (64-bit) block IDs: enabled
[cam1] GigE stream driver: SocketDriver (SocketBufferSize=262144 KB)
...
[grab0] StartGrabbing (recording=False)
[grab0] zero-copy view OK (PaddingX=0 PaddingY=0)
[nvenc] warmed (first-Encode import done single-threaded)
[hw] NVENC sessions: 8 (at least — probe stopped at its limit), needed 8
```

What to check in that output:

- one `[camN] <serial> <width>x<height> Mono8` line per camera, with the
  geometry and format you configured;
- `zero-copy view OK (PaddingX=0 PaddingY=0)` for every camera;
- an `[hw] NVENC sessions:` count at least equal to your camera count.

Every launch also writes the same text to `logs/panopticon_<date>_<time>.log`,
which is where to look when the app is started from the desktop shortcut and has
no console.

A *Hardware Check Results* dialog appears only if something is below the
recommended minimum (cores, RAM, free disk, disk write speed, NVENC). It lists
what it found.

### Step 10 — desktop shortcut

```powershell
powershell -ExecutionPolicy Bypass -File make_shortcut.ps1
```

Expected:

```
Shortcut written: C:\Users\you\Desktop\Panopticon.lnk
  Target : C:\Users\you\Desktop\3dpose\.venv\Scripts\pythonw.exe
  Args   : "C:\Users\you\Desktop\3dpose\gui.py"
  WorkDir: C:\Users\you\Desktop\3dpose
```

The shortcut points at the virtual environment's `pythonw.exe`, a
GUI-subsystem binary that Windows never gives a console, so launching it opens
exactly one window. It bypasses `uv run`, which means it does not sync
dependencies — run `uv sync` yourself after `pyproject.toml` changes.

`_launch.bat` is the alternative: it goes through `uv run` and keeps a console
window open for the life of the app, which is what you want when the app fails
to start and you need to see why.

If the script prints `No venv at ...`, run `uv sync` first.

---

## 3. Verify it works

### Without any cameras

Four test suites run on the code alone. They are plain scripts, not pytest.

```powershell
uv run python test_frame_sync.py
uv run python test_grab_failure.py
uv run python test_stim_compiler.py
uv run python test_serial_handshake.py
```

Each ends with one summary line:

```
ALL FRAMESYNC EQUIVALENCE TESTS PASS
ALL GRAB-FAILURE TESTS PASS
ALL STIM COMPILER TESTS PASS
ALL SERIAL HANDSHAKE TESTS PASS
```

Read the last line only. The suites deliberately exercise failure paths, so
alarming-looking output on the way through is expected:

```
[sync] cam3 RETIRED from the alignment set: stalled in test.
[grab3] STALLED — re-arming stream (attempt 1)
[teensy] no ack — reopening port to force a board reset
```

What each one covers:

| Suite | Covers |
|---|---|
| `test_frame_sync.py` | The kick-out coordinator gives the same result as a post-hoc block-ID intersection: exact equivalence, bounded skew, forced drops, freeze recovery, the 16-bit block-ID wrap, retiring a stalled camera. |
| `test_grab_failure.py` | Camera-failure paths against a stub camera and router: a camera that cannot start grabbing, a ring allocation that fails, a grab thread that exits quietly, repeated grab errors, and re-arm exhaustion. Each must retire that camera so the others keep recording aligned. |
| `test_stim_compiler.py` | Stimulation graph to firmware: start resolution, cycle-safe chain extraction, integer-microsecond encoding, safe-pin boot order, pin conflicts, forbidden pins, the RDY ack, the per-frame stimulus trace. |
| `test_serial_handshake.py` | The four trigger-board handshake outcomes — confirmed, retry after reset, legacy firmware, and a board that has acknowledged before going silent (which must refuse to record). pyserial is stubbed, so no COM port is needed. |

Run them through `uv run`, not a bare `python`: tests 10-13 of the stimulation
suite need numpy and skip silently without it.

A fifth suite needs an NVENC GPU but still no cameras:

```powershell
uv run python test_sync_router.py
```

### With real cameras

```powershell
uv run probe_lag.py --seconds 90 --label install-check
```

This drives the real capture path headless — `CameraManager`, `GrabThread`,
`SyncEncodeRouter`, `FrameSyncCoordinator`, `TeensyController` — so its result
applies to the GUI. The only missing piece is Qt's own display work. It writes
`probe_out/install-check/trace.json`; the video goes to a scratch directory and
is deleted unless you pass `--keep`.

A healthy run looks like this:

- **`cycle` equals the trigger period.** At 100 fps that is `cycle=10.00ms` on
  every camera, for the whole run. Anything above it means the loop is falling
  behind by that much per frame, and the buffer pool hides it until it is full.
- **`avg_wait` is much larger than `avg_proc`.** The thread should spend most of
  its period waiting for the next trigger: about 8.5 ms of wait against 0.8 ms
  of work at 100 fps and 1920x1200.
- **`deliv_lag` sits near zero** and does not grow. It measures how stale a
  frame is when retrieved, against the camera's own clock.
- **`Buffer_Underrun_Count` is 0** on every camera, in the per-camera
  `stream stats` line printed at stop. Nonzero means the host did not keep up.
- **Frame counts are equal across cameras**, and `forced` is 0.

A 60-second six-camera run on the reference rig reported:

```
cycle=10.00ms on all six    avg_proc 0.80-0.90ms    avg_wait 8.40-8.62ms
deliv_lag ~0                Buffer_Underrun_Count 0 on all six
grabbed per camera: [6022, 6022, 6022, 6022, 6022, 6022]
released=6022  dropped=0  forced=0  queue_full_drops=0
```

with per-camera lag behind the leader at median 0, p95 1, max 2 frames.

Two numbers in the stream statistics are easy to misread.
`Resend_Request_Count` counts packets that were lost and asked for again; a high
count with `Failed_Buffer_Count` at 0 is a noisy link that is recovering
everything, which costs nothing. `Failed_Buffer_Count` above 0 is a frame that
was given up on.

Measure on an otherwise idle machine. Competing CPU load has moved the cycle
from 10.00 to 10.32 ms, which accumulates 5.6 seconds of backlog over
150 seconds.

Then make a short real recording and check the output.
[WORKFLOW.md](WORKFLOW.md) covers that end to end.

---

## 4. Troubleshooting

### Installation

| Symptom | Cause and fix |
|---|---|
| `uv : The term 'uv' is not recognized` | PowerShell was opened before uv was installed. Open a new window; if it persists, sign out and back in. |
| `git : The term 'git' is not recognized` | Install Git for Windows, then reopen PowerShell. |
| `uv sync` fails to download | No internet, or a proxy. The clone and this step are the only ones that need it. |
| `warning: Skipping installation of entry points (project.scripts)` | Normal. The project is not packaged; nothing is wrong. |
| `git status` shows `m campy` | Line-ending differences inside the submodule, not real edits. It affects nothing. |
| `make_shortcut.ps1` prints `No venv at ...` | `uv sync` has not been run in this copy of the repository. |
| `... cannot be loaded because running scripts is disabled` | Launch the script as `powershell -ExecutionPolicy Bypass -File <script>.ps1`, as shown above. |
| `This must run elevated (Set-NetAdapterRss needs admin).` | `configure_nic.ps1` needs an Administrator PowerShell window. |

### Cameras

| Symptom | Cause and fix |
|---|---|
| `No cameras found` | No camera enumerated. Check power and cabling, and close pylon Viewer or anything else holding the cameras. |
| `No cameras found or .pfs missing. Check connections and profile.` | Either the above, or `pfs_path` in the profile does not point at an existing file. |
| Cameras absent from pylon Viewer as well | GigE addressing or firewall. Re-run `PylonGigEConfigurator auto-all` elevated, and add the inbound UDP rule for both `python.exe` and `pythonw.exe`. |
| `Expected 6 cameras but 5 enumerated.` | One camera did not appear — dead switch port, unpowered, still booting. Camera names are positional by serial-number order, so starting anyway would rename every later camera and attach the calibration extrinsics to the wrong physical camera. The message lists the serials it found. Power-cycle the missing one and reselect the profile. |
| `Camera <serial> failed to open/configure: ...` | The camera enumerated but would not configure. Power-cycle it, or close whatever else holds it. The whole set is closed rather than continuing with a partial one. |
| `PixelFormat is Mono12, not Mono8` | The `.pfs` was saved with the wrong pixel format. Wider than 8-bit is truncated mod 256 with no error, so this refuses instead of recording shredded video. Fix the `.pfs`. |
| `resolution 1280x1024 differs from camera 1 (1920x1200); all cameras must match` | One camera has a different ROI. Reapply the same `.pfs`. |
| `FATAL: PaddingX=... rows would shear. Refusing to record.` | The camera reports row padding, which the zero-copy frame view cannot represent. That camera is retired from the alignment set; the others keep recording aligned. |
| `extended (64-bit) block IDs: UNAVAILABLE — relying on software unwrap` | Informational. The 16-bit block ID wraps every 65,535 triggers (about 11 minutes at 100 fps) and the software unwrap handles it. |
| Cameras vanish right after a NIC change | Expected. Changing adapter settings resets the adapters; the cameras re-enumerate within seconds. |
| Preview very dark, or nearly black | Exposure and gain come only from the `.pfs`. Add illumination, then raise `ExposureTime` up to the ceiling, then `Gain`. |

### Trigger board

| Symptom | Cause and fix |
|---|---|
| `arduino-cli was not found` | Install the Arduino IDE or `arduino-cli`, or set `PANOPTICON_ARDUINO_CLI`. The message lists every location searched. Only the firmware flash and the Stimulation editor need it. |
| `Compile failed (arduino-cli exit N)` mentioning a missing core | `arduino-cli core install arduino:avr`. Nothing was flashed, so the board still runs what it ran before. |
| `Upload failed on COM3 (arduino-cli exit N)` | The port is held by something else (Arduino Serial Monitor, another instance), the profile names the wrong port, or the board is not an `arduino:avr:mega`. A part-way upload leaves the firmware — including the safe-pin boot guard — in an unknown state; power-cycle the board. |
| `Could not clear stim firmware` at launch | The board may still hold a paradigm from a previous session, including one that loops and never ends. Open Stimulation and press Apply with an empty canvas, or key off the laser. |
| `[teensy] no ack — reopening port to force a board reset` | One occurrence is normal: the start command is retried after forcing a board reset. |
| `[teensy] board acked previously but not now — aborting` | This board has acknowledged before, so silence is a real fault. The cameras are rolled back and the recording refused rather than recording an empty session. Check the cable and power. |
| `Trigger board did not confirm the stop` | The board may still be triggering, and a looping stimulation chain never ends on its own. Power-cycle the board and key off the laser. |
| `Pin 4 cannot carry a stim waveform: camera trigger line — ...` | A stimulation block targets a camera trigger pin (extra edges would break that camera's alignment) or UART RX0/TX0. Move it to another pin. |

### It refuses to start a recording

| Message | Cause and fix |
|---|---|
| `No cameras are open. Recording would run the trigger protocol — and any baked-in stim paradigm — while saving nothing.` | Open the cameras first: pick a profile whose `pfs_path` resolves and whose cameras enumerate. |
| `Not enough RAM for N cameras: ...` | The buffer arithmetic does not fit in available memory. The message breaks it into pool and ring. Lower `kick_max_lag`, lower `MAX_NUM_BUFFER`, or close other applications. |
| `RAM is tight for N cameras: ...` | Over 75% of available memory. It asks before proceeding. |
| `NVENC granted only N concurrent sessions but M cameras need one each.` | The driver's session cap is below the camera count, often because another process holds sessions (a browser's hardware encode, an orphaned ffmpeg). Close them, or use a raw profile, or record fewer cameras. |
| `NVENC granted no encode sessions, so real-time encoding cannot start.` | No sessions available at all. Use a raw profile (`realtime_encode: false`). |
| `Disk may be short: a 10-minute recording would need ~N GiB` | A warning, not a refusal: 10 minutes is an assumed worst case, not a known length. A shorter recording is fine. |
| `Raw capture will write N GiB/s.` | Spread the output across drives. A single consumer NVMe falls to about 1.6 GB/s once its SLC cache is exhausted. |

### Recording quality

| Symptom | Cause and fix |
|---|---|
| `cycle` above the frame period | The grab loop is not finishing inside one period. Something else is using the CPU, or a change added work to the hot path. |
| Frame rate about half of what was asked | `exposure + 1/AcquisitionFrameRate` exceeds the trigger period, so every second trigger is skipped. Lower exposure, or raise `trigger_rate_limit`. |
| `Buffer_Underrun_Count` nonzero | The driver's buffer pool ran dry: a host-side problem, not the network. |
| Frames lost with high `Resend_Request_Count` and nonzero `Failed_Buffer_Count` | Network. Check RSS receive queues, jumbo frames end to end, Energy Efficient Ethernet, and cameras per port. |
| Roughly a quarter of frames missing, in single-frame gaps | `gige_driver: filter` discards a frame with a lost packet instead of asking for it again. Use `socket`. |
| `camera did not start grabbing` / `stream dead after N re-arms` | That camera was retired from the alignment set so the others keep recording aligned. The session yields N-1 cameras instead of nothing. |
| `block-ID bookkeeping claimed X frames but only Y were persisted` | An encoder fell behind or died. The metadata is truncated to what is actually in the video and a `WARNINGS.txt` is written beside it. |
| Video will not seek in the labeler | The mp4 lost its explicit GOP. Every encode path must pass `-g <fps>` and `-movflags +faststart`. |

### Calibration

| Symptom | Cause and fix |
|---|---|
| `0 boards detected` | The board description in `configs/boards/*.yaml` must match the physical board, including `board_legacy: true` for a board printed with the pre-OpenCV-4.6 ChArUco layout — without it, a 4.7 or newer detector returns zero corners silently. |
| numpy ABI error from `1_calibrate.py` | That script pins its own dependencies. Run `uv cache clean` and retry. |
