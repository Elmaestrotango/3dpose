# Panopticon — 3DPose

Multi-camera synchronized acquisition GUI for 3D pose estimation. This is the **3dpose rig** deployment of Panopticon.

The same GUI codebase (`gui_app/`) is shared with the [3dface](https://github.com/Elmaestrotango/3dface) repository. The only difference between deployments is the rig profile, which configures camera resolution, board parameters, and data paths.

| | 3dpose (this repo) | 3dface |
|---|---|---|
| **Cameras** | 6x Basler a2A1920-165g5m (GigE) | 6x Basler acA1300-200um (USB3) |
| **Resolution** | 1920x1200 | 1280x1024 (full sensor) |
| **Interface** | GigE | USB 3.0 |
| **Storage / 10 min** | ~a few GB (online encode) · ~138 GB/cam in raw fallback | ~a few GB · ~79 GB/cam raw |
| **Calibration board** | ChArUco 8x8, 15mm squares | ChArUco 5x5, 0.5mm squares |

> **Offline use:** Once installed, the entire pipeline (acquisition, encoding, calibration) runs fully offline. Only initial setup (`uv sync`, `git clone`) requires internet.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **uv** | Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/)) |
| **Basler Pylon SDK** | [Download](https://www.baslerweb.com/en/downloads/software-downloads/) — needed for USB3/GigE camera drivers |
| **NVIDIA GPU + driver 550+** | NVENC H.264 encoding, used live during capture by default (online encode). Without it the GUI falls back to raw-to-disk + post-hoc encode. |
| **Teensy/Arduino** | Flashed with `campy/campy/trigger/trigger.ino` via Arduino IDE |
| **NVMe SSD** | Online encode writes only a few GB/session. The raw-to-disk fallback needs 500+ GB free and 1000+ MB/s sustained write. |

### Hardware requirements

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 4 cores | 8+ cores |
| RAM | 16 GB | 32 GB |
| Disk free | 500 GB | 1 TB+ |
| Disk write | 500 MB/s | 1400+ MB/s (NVMe) |
| GPU | NVIDIA with NVENC | Any modern NVIDIA GPU |

On launch, Panopticon runs a hardware check and shows a warning if any resource falls below the minimum. The warning is non-blocking — you can dismiss it and continue.

---

## Installation (step by step)

### 1. Clone the repository

```bash
git clone https://github.com/Elmaestrotango/3dpose.git
cd 3dpose
```

### 2. Install Python dependencies

```bash
uv sync
```

This creates a `.venv` with all dependencies (pypylon, PyQt5, numpy, plus PyNvVideoCodec + the CUDA runtime for online GPU encoding, etc.). No conda required. Requires internet access once.

### 3. Generate camera settings (.pfs file)

Connect all cameras to the machine, then open **Pylon Viewer** (installed with the Pylon SDK):

1. Open a camera in Pylon Viewer
2. Set **Pixel Format** to `Mono8`
3. Set **Width** and **Height** to full sensor (e.g., 1920x1200 for a2A1920-165g5m)
4. Set **Acquisition Frame Rate** to `165` (or higher than your target fps)
5. File > **Save Features** > save as `configs/<descriptive_name>.pfs`

Example naming: `mono8_1920x1200.pfs`, `mono8_1280x1024.pfs`

### 4. Update the rig profile

Edit `profiles/<your_rig>.yaml` to point to your `.pfs` file:

```yaml
name: my_rig
frame_width: 1920          # Must match .pfs Width
frame_height: 1200         # Must match .pfs Height
frame_rate: 100            # Trigger rate in Hz
quality: 21                # NVENC QP (lower = higher quality, 15-30 typical)

pfs_path: "configs/mono8_1920x1200.pfs"               # Relative to repo root
output_dir: "data"                                      # Relative to repo root
board_config: "configs/boards/charuco_8x8_15mm.yaml"   # Calibration board

serial_port: COM3                 # Teensy serial port
trigger_pins: [2, 4, 6, 8, 10, 12]  # One Teensy GPIO pin per camera
```

All paths can be **relative** (resolved against the repo root) or **absolute**.

### 5. Configure cameras (GigE)

```powershell
# Run as Administrator
& "C:\Program Files\Basler\pylon\Runtime\x64\PylonGigEConfigurator.exe" auto-all

# Add firewall rule
New-NetFirewallRule -DisplayName PanopticonGigE -Direction Inbound -Action Allow -Protocol UDP -Program "<repo>\.venv\Scripts\python.exe"
```

GigE cameras require IP configuration and a firewall rule to allow UDP discovery. Both commands must be run as Administrator.

### 6. Flash the Teensy

1. Open `campy/campy/trigger/trigger.ino` in Arduino IDE
2. Select your Teensy board and COM port
3. Upload the sketch
4. Close Arduino Serial Monitor (it holds the COM port)

### 7. Test the installation

```bash
uv run python gui.py
```

Cameras should appear in the live preview grid at ~30 fps. If you see a hardware check warning, review the recommendations.

### 8. Create a Desktop shortcut (optional)

- Right-click Desktop > New > Shortcut
- Target: `<repo-path>\_launch.bat`
- Change icon to `<repo-path>\panopticon.ico`

---

## Directory structure

```
<repo>/
  gui.py                    Entry point (splash screen + main window)
  _launch.bat               Windows batch launcher
  pyproject.toml             Python dependencies (uv sync)
  panopticon.ico             Application icon
  1_calibrate.py             sleap-anipose calibration script
  configs/                   Camera and board config files (tracked in git)
    *.pfs                    Basler camera feature persistence files
    boards/                  ChArUco board definitions
      *.yaml                 One file per board (see Board Configuration)
  profiles/                  Rig profiles (tracked in git)
    <rigname>.yaml           One file per rig (see Profile Configuration)
  data/                      Default output directory (gitignored)
    YYYYMMDD/                Date directories
      subject1_subject2/     Session directories
        ...                  Videos, frametimes, metadata
  gui_app/                   Application code
    main_window.py           Layout, theme, state machine, acquisition flow
    camera_manager.py        Camera lifecycle, mode switching, grab threads
    grab_thread.py           Per-camera: frame grab + raw binary write
    encode_worker.py         Background NVENC H.264 encoding
    calibration_worker.py    Background sleap-anipose calibration
    serial_controller.py     Teensy serial trigger control
    session_config.py        Profile loading, session paths, metadata
    hardware_check.py        Startup hardware screening
    widgets/
      camera_grid.py         Dynamic NxM camera grid with zoom
      sidebar.py             Metadata form, profile selector, toggles, sliders
      toggle_switch.py       Animated toggle switch widget
  campy/                     Teensy firmware (git submodule)
    campy/trigger/trigger.ino
```

---

## Profile configuration

Each rig has a YAML profile in `profiles/`. The profile defines everything specific to one camera setup.

| Field | Type | Description |
|---|---|---|
| `name` | string | Display name in the GUI dropdown |
| `frame_width` | int | Sensor width in pixels (must match .pfs) |
| `frame_height` | int | Sensor height in pixels (must match .pfs) |
| `frame_rate` | int | Trigger rate in Hz (typically 100) |
| `quality` | int | NVENC QP parameter (0-51, lower = better quality, 21 is default) |
| `realtime_encode` | bool | Encode H.264 on the GPU during capture (default `true`). `false` = raw-to-disk + post-hoc encode |
| `encode_parallel` | int | Concurrent ffmpeg jobs at stop: `.h264`→mp4 remuxes (online) or raw→H.264 encodes (fallback). Default 3 |
| `pfs_path` | string | Path to Basler .pfs file (relative or absolute) |
| `output_dir` | string | Base data directory (relative or absolute) |
| `board_config` | string | Path to ChArUco board YAML (relative or absolute) |
| `serial_port` | string | Teensy COM port (e.g., `COM3`) |
| `trigger_pins` | list[int] | Teensy GPIO pins, one per camera (e.g., `[2, 4, 6, 8, 10, 12]`) |

This repo ships two profiles: **`3dpose`** (online GPU encode — the default) and
**`3dpose (raw)`** (`profiles/3dpose_raw.yaml`, the raw-to-disk fallback). Pick the
latter from the dropdown if NVENC misbehaves or for a recording you can't risk.

To create a new profile for a different rig:

1. Copy an existing profile: `cp profiles/3dpose.yaml profiles/my_rig.yaml`
2. Update `name`, resolution, `.pfs` path, and board config
3. Ensure the `.pfs` file exists in `configs/`
4. Ensure the board YAML exists in `configs/boards/`
5. Launch the GUI — your new profile appears in the dropdown

---

## Board configuration

ChArUco calibration boards are defined in `configs/boards/*.yaml`. Each rig references one board in its profile.

```yaml
board_x: 8            # Squares along width
board_y: 8            # Squares along height
square_length: 15.0   # Square side length in mm
marker_length: 12.0   # ArUco marker side length in mm
marker_bits: 4        # Marker dictionary bit count
dict_size: 1000       # Marker dictionary size
max_frames: 200       # Max frames for calibration subsampling
```

To create a new board config:

1. Measure your physical ChArUco board
2. Create `configs/boards/<descriptive_name>.yaml` with the parameters above
3. Update `board_config` in your rig profile to point to it

---

## Usage

### Recording workflow

1. Launch the GUI (`uv run python gui.py` or Desktop shortcut)
2. Select the rig profile from the dropdown
3. Fill in session metadata (date, subject IDs, assay, experimenter, etc.)
4. Flip **Calibrate** toggle — record calibration videos with ChArUco board visible
5. Flip it off — the per-camera H.264 streams are wrapped into mp4 (seconds; a brief progress bar shows during the remux)
6. Click **Solve** — runs sleap-anipose calibration on the encoded videos
7. Flip **Record** toggle — record behavioral data
8. Flip it off — videos are finalized (seconds), and `calibration.toml` is copied to the recording directory

> With online encode (the default), encoding happens live during capture, so step 5/8 is a near-instant remux. In the **3dpose (raw)** fallback profile these steps run the full post-hoc encode instead (~10 min for a 10-min recording).

### Keyboard / mouse

- **Double-click** a camera view to zoom (fills entire grid area)
- **Double-click** the zoomed view to return to grid
- **Brightness / Contrast** sliders adjust display only (recorded data is unaffected)

---

## Naming conventions

| Item | Convention | Example |
|---|---|---|
| Date | `YYYYMMDD` | `20260517` |
| Session ID | `<subject1>_<subject2>` | `slmc001_slmc002` |
| Camera names | `cam1`, `cam2`, ..., `camN` (auto-assigned by serial number order) | `cam1` |
| Video files | `<date>-<session_id>-<cam>-<acq_type>.mp4` | `20260517-slmc001_slmc002-cam1-recording.mp4` |
| Frame times | `frametimes.npy` (numpy array: row 0 = frame numbers, row 1 = relative timestamps in seconds) | |
| Session metadata | `session_metadata.json` | |
| Calibration output | `calibration.toml` (sleap-anipose camera parameters) | |
| Board definition | `board.toml` (generated during calibration solve) | |

---

## Output structure

```
data/
  YYYYMMDD/
    subject1_subject2/
      session_metadata.json
      calibration/
        cam1/
          YYYYMMDD-subject1_subject2-cam1-calibration.mp4
          frametimes.npy
        cam2/
          ...
        camN/
          ...
        board.toml
        board.jpg
        calibration.toml
        calibration_metadata.h5
        reprojection_error_histogram.png
        reprojections/
      recording/
        cam1/
          YYYYMMDD-subject1_subject2-cam1-recording.mp4
          frametimes.npy
        cam2/
          ...
        camN/
          ...
        calibration.toml          (copied from calibration/)
```

During an online-encode recording each `camN/` briefly holds a `stream.h264` that is
replaced by the `.mp4` at stop; in raw fallback mode it holds `raw.bin`, removed after
the post-hoc encode.

---

## Architecture

### Real-time GPU encoding (default) — "online encode"

During recording, each camera's grab thread does only retrieve → copy → queue → release
(so the GigE buffer pool always drains at full rate), and a dedicated per-camera
**encoder thread** converts the mono8 frame to NV12 (the gray frame *is* the luma plane;
chroma is a constant) and encodes it to H.264 on the GPU via
[PyNvVideoCodec](https://pypi.org/project/PyNvVideoCodec/) (NVENC) — one encoder session
per camera, all running concurrently. Frames go straight to a per-camera `stream.h264`
elementary stream. On stop, each stream is wrapped into an `.mp4` with a fast
`ffmpeg -c copy` remux (no re-encode — finishes in seconds).

Encoding must never stall capture: if a camera's encoder dies mid-recording, its
remaining frames are written raw (`raw_tail.bin`) in arrival order and merged back into
the stream at stop, so the recording stays gapless. Each frame's GigE block ID (trigger
ordinal) is saved to `blockids.npy` alongside `frametimes.npy`, making any dropped frame
a detectable, re-alignable gap instead of a silent cross-camera desync.

So there is **no large transient file and no post-hoc encode pass**: a 10-minute
6-camera session writes a few GB total and is fully encoded the instant recording stops.

**Why this works now (it didn't before):** the old path piped frames through *ffmpeg*,
whose CPU-side gray→yuv420p conversion bottlenecked at ~80 fps for 6 cameras.
PyNvVideoCodec does the conversion *and* encode on the GPU, so the CPU only copies the
gray bytes — measured ~1400 fps aggregate for 6×1920x1200 on an RTX 5080 (~2.3× the
600 fps needed), at under 2 CPU cores.

Controlled by the `realtime_encode` profile field (default **true**). If PyNvVideoCodec
or the CUDA runtime is unavailable, each camera transparently falls back to the
raw-to-disk path below — no data is lost.

### Raw capture + post-hoc encoding (fallback)

Used when `realtime_encode: false` (the **3dpose (raw)** profile) or when NVENC is
unavailable. Raw mono8 frames are written directly to disk via `os.write()` into
`raw.bin`; after recording stops, ffmpeg encodes them to H.264 via NVENC
(`encode_parallel` cameras at a time). Rock-solid — the disk write can never fall
behind — but it writes ~138 GB per camera per 10 min at 1920x1200, and the encode pass
runs at roughly real-time (~10 min for a 10-min session).

### Camera modes

| Mode | TriggerMode | Frame Rate | Purpose |
|---|---|---|---|
| Idle (preview) | Off | 30 fps free-run | Live camera grid |
| Acquiring | On (Line1, rising edge) | 100 fps hardware-triggered | Recording / calibration |

Mode switching: `StopGrabbing() -> reconfigure -> StartGrabbing()`

### Frame synchronization

The Teensy delivers TTL pulses to all cameras simultaneously (~30 ns cross-pin skew). After acquisition, all cameras' data is truncated to the minimum frame count across cameras.

### Calibration

Uses [sleap-anipose](https://github.com/talmolab/sleap-anipose). Board parameters are loaded from the board config YAML. Calibration videos are subsampled to `max_frames` (default 200) before solving to keep runtime under 2 minutes.

---

## Troubleshooting

### Cameras not found

| Symptom | Fix |
|---|---|
| "No cameras found" on launch | Check that cameras are connected and powered. Close PylonViewer or other apps holding the cameras. |
| .pfs file not found | Verify `pfs_path` in your profile points to an existing file in `configs/`. |
| GigE cameras not detected | Run `PylonGigEConfigurator auto-all` as Administrator. Add firewall rule. |

### Serial port errors

| Symptom | Fix |
|---|---|
| "Could not open COM3" | Close Arduino Serial Monitor. Check `serial_port` in profile. Restart GUI. |
| Teensy not responding | Verify `trigger.ino` is flashed. Check USB connection. |

### Recording issues

| Symptom | Fix |
|---|---|
| Frame counts differ (±5) | Normal — data is auto-truncated to min count. |
| Very low fps | Check `AcquisitionFrameRate` in `.pfs` is set to 165+. |
| Encoding fails | Check NVIDIA drivers are installed. Look for ffmpeg errors in console output. |

### Calibration issues

| Symptom | Fix |
|---|---|
| "0 boards detected" | Check board parameters in `configs/boards/*.yaml` match your physical board. |
| numpy ABI error | `1_calibrate.py` pins `numpy<2`. Run `uv cache clean` and retry. |

---

## Teensy wiring

| Teensy Pin | Destination |
|---|---|
| USB | Computer (serial port, 115200 baud) |
| 2, 4, 6, 8, 10, 12 | Camera Line1 trigger inputs (one per camera) |
| 13 | Stim Arduino interrupt pin (optional) |
| GND | Camera GND (shared ground) |

Serial protocol: `<num_pins>,<pin1>,<pin2>,...,<fps>` to start, `<num_pins>,<pin1>,<pin2>,...,-1` to stop.

Example: `6,2,4,6,8,10,12,100` (start 6 cameras at 100 fps), `6,2,4,6,8,10,12,-1` (stop).

---

## Cross-references

- **3dface rig**: https://github.com/Elmaestrotango/3dface
- **sleap-anipose**: https://github.com/talmolab/sleap-anipose
- **stac-mjx**: https://github.com/talmolab/stac-mjx
- **Basler Pylon SDK**: https://www.baslerweb.com/en/downloads/software-downloads/
