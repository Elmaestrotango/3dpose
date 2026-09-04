<h1 align="center">
  <img src="panopticon.ico" width="72" height="72" alt=""><br>
  Panopticon
</h1>

<p align="center"><b>Multi-camera hardware-synchronised video acquisition for 3D animal pose estimation.</b></p>

[![The Panopticon interface](docs/images/ui_annotated.png)](docs/OVERVIEW.md)

Panopticon runs N cameras off one trigger clock, encodes every frame to H.264 on the GPU
while capture is still running, and writes the videos out already frame-aligned, so frame
*i* is the same instant in every view. Cameras on a network can drop a frame
independently of each other, so a shared coordinator releases a trigger to the encoders
only once every camera has captured it; triggers a camera missed are dropped before
encoding. The per-camera videos therefore come out equal in length and aligned
trigger-for-trigger, with no post-hoc pass. The same application records the ChArUco
calibration, solves it, and can compile an optogenetic stimulation paradigm into the
board that drives the camera triggers.

The output loads directly in **[LUC3D](https://talmolab.github.io/luc3d/)**, a
browser-based multi-view pose annotation tool by Eric Leonardis (Salk Institute), hosted
by the Talmo Lab
([repo](https://github.com/talmolab/luc3d) ·
[docs](https://talmolab.github.io/luc3d-docs/)).
LUC3D takes browser-playable video plus TOML or JSON calibration, which is exactly what a
session directory already holds: H.264 mp4 with the moov atom at the front and one IDR
per second, plus `calibration.toml`.

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

**What it does.** Triggers N cameras from one board, so every camera exposes on the same
edge. Encodes H.264 on the GPU during capture, one NVENC session per camera, so there is
no large intermediate file and no post-capture encode pass. Tags every frame with the
trigger ordinal it came from and keeps only the triggers all cameras caught. Records a
ChArUco calibration and solves it with
[sleap-anipose](https://github.com/talmolab/sleap-anipose). Optionally compiles an
optogenetic paradigm into the trigger board's firmware and writes a per-frame record of
what that paradigm delivered.

**What it needs.** Basler cameras (via pypylon), an NVIDIA GPU that can grant one
concurrent NVENC encode session per camera, Windows, and an Arduino- or Teensy-class
board on a serial port. The rest scales with pixel rate rather than with any part number:
one 1920x1200 mono8 camera at 100 fps produces about 1.84 Gbit/s, so three cameras on one
port needs a 10 GbE port, while a lower frame rate or resolution can fit on 1 GbE.
`docs/INSTALLATION.md` gives the arithmetic for CPU, RAM, disk and network so a rig can be
sized from what it is meant to record. Panopticon screens the machine at launch and
re-checks capacity against the actual number of cameras before each acquisition, refusing
to start rather than half-record. Without a usable NVENC encoder it falls back to writing
raw frames to disk and encoding after the session, which needs roughly a hundred times
more disk.

**What it outputs.** Per camera, an mp4 (H.264, `yuv420p`, `+faststart`, one IDR per
second) named `<date>-<session>-<cam>-<recording|calibration>.mp4`, plus `frametimes.npy`
and `blockids.npy` (each frame's trigger ordinal). Per session, `session_metadata.json`,
plus `calibration.toml` and `board.toml` from the calibration solve; the
`calibration.toml` is also copied next to the recording. A session that used stimulation
adds `stim_paradigm.json`, `stim_paradigm.ino` (the exact firmware that ran) and
`stim_trace.csv` (one row per recorded frame).
