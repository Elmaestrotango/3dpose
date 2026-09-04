# Installation

This page takes a rig that is already wired up to a first working launch. It
assumes no terminal experience: every command is written out in full, with the
output you should expect so you can tell whether the step worked.

Physical construction — where the cameras are mounted, how the enclosure is lit,
how each piece is powered — is not covered here, because it depends on what you
are filming rather than on the software. The one piece of wiring that is neither
optional nor obvious is the trigger line from the microcontroller to the
cameras, and that is described under [A trigger source](#a-trigger-source)
below.

The page comes in four parts. The first works out whether the hardware you have,
or are about to buy, can carry the acquisition you want to run, and ends with one
configuration that is known to work, for comparison. The second
installs and configures everything, in ten steps. The third checks the result,
first without cameras and then with them. The fourth is a catalogue of what goes
wrong, written as the messages you will actually see.

Contents:

1. [What the rig needs](#1-what-the-rig-needs)
2. [Install the software](#2-install-the-software)
3. [Verify it works](#3-verify-it-works)
4. [Troubleshooting](#4-troubleshooting)

---

## 1. What the rig needs

Nothing here names a part number, because the hardware a rig needs is not really
a property of the software. It is a property of the *kind* of acquisition you
intend to run, and that is captured by four numbers you choose before you buy
anything: resolution, bit depth, frame rate, and camera count. Multiply them out
and you have the load every part of the rig has to carry, from the camera's
Ethernet port to the disk.

Those four numbers deserve some thought, because the same software is
comfortable in one configuration and impossible in another. Two cameras at
1920x1200 and 30 fps are happy on ordinary gigabit hardware. Six of them at
100 fps need a 10 GbE network, tens of gigabytes of RAM and a GPU encoder — and
a rig that is short on any one of those does not stop with an error. It quietly
delivers fewer frames than it triggered, which is the most expensive kind of
problem to discover after the animal has gone home. So the arithmetic below is
written out rather than summarized: work through it with your own numbers and
you will know which part of your rig is the binding constraint before it costs
you a session.

Everything follows from the size of one frame. For mono8 — one byte per pixel,
and the only format the capture path accepts — that is just width times height:

```
frame_bytes = width x height
1920 x 1200 = 2,304,000 bytes  (~2.3 MB per frame per camera)
```

### Cameras

The camera is the one choice that is awkward to reverse, so it is worth deciding
first and deliberately. What matters is not the brand: it is whether the camera
can do three specific things that everything downstream depends on. No amount of
software makes up for a camera that cannot.

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
  memcpy on the per-frame hot path while holding the global interpreter lock —
  the GIL, the lock that lets only one thread run Python at a time — which is
  enough on its own to lose frames on every camera. Measure it before assuming
  it is affordable.

On top of those three, each camera needs a **hardware trigger input**, because
that is how it is told when to expose. In triggered mode the app sets
`TriggerSelector=FrameStart`, `TriggerMode=On`, `TriggerSource=Line1`,
`TriggerActivation=RisingEdge`.

Finally, every camera must be set to **Mono8** and to the **same resolution**.
That is not a preference. The capture path assumes 8 bits, and a 12-bit frame
arrives as 16-bit data and is
truncated mod 256 with no error at all, producing a full-length, perfectly
aligned, visually shredded recording. Opening refuses on both mismatches rather
than recording that.

### A trigger source

Next comes the piece that decides what "at the same time" means for this rig.
It is an Arduino- or Teensy-class microcontroller on a serial port, with one
output pin per camera (the profile's `trigger_pins`) and, if you use optogenetic
stimulation, one more pin for the stimulus driver. The reference firmware
targets `arduino:avr:mega`. It is a cheap component, and it is the reason the
recordings are usable for 3D at all.

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

#### Wiring the trigger line

Getting this wrong is not something you can repair afterwards: a camera that
misses triggers, or that is exposing on anything other than the shared line,
yields a recording whose views are not simultaneous, and no amount of
post-processing recovers a moment that was never captured. So it is worth doing
deliberately the first time. On the reference rig it is as plain as it sounds:
the profile's `trigger_pins` are
`[2, 4, 6, 8, 10, 12]` — six digital output pins on an Arduino Mega — and each
one runs to one camera's `Line1`, the input the cameras are configured to
trigger on (`TriggerSource=Line1`, set for you when acquisition starts).
`Line1` is a pin on the camera's I/O connector, which is a separate connector
from the network one; the camera's data sheet gives its pinout.

Three things have to be true besides the signal wire itself.

The board and the cameras need a **common ground**. A trigger is a voltage
difference, and without a shared reference there is nothing for the camera to
measure the board's output against. Run a ground wire from the board to the
camera I/O connector's ground pin alongside each signal wire, or to a single
ground point that the board and all the cameras share.

The cameras need **power**, and it does not come from the trigger line.
Depending on the model that is either Power over Ethernet from the switch — in
which case the switch has to supply it and has to have the power budget for
every camera plugged into it — or an external supply into the I/O connector.
The camera's data sheet says which, and it is worth settling before ordering
the switch.

The signal has to be one the camera **recognizes**. An Arduino Mega drives 5 V
when a pin is HIGH and 0 V when it is LOW, which is an ordinary TTL level, but
the input at the far end varies by camera model. Some machine-vision trigger
inputs are opto-isolated, and an opto-coupler is driven by current rather than
by voltage, so "it is 5 V, it will work" is not enough on its own. Two numbers
from the camera's I/O documentation settle it: the input's switching threshold,
and the current it draws when driven. Those are also the numbers that answer
the next question.

**One pin per camera, or one pin fanned out to several?** The generated sketch
writes every pin in `trigger_pins` inside a single `noInterrupts()` block, so
every camera receives its edge in the same instant — the firmware lineage this
comes from, campy by Kyle Severson, documents roughly 30 ns of synchronicity
between pins. Because the pins are electrically identical in time, one pin
fanned out to several cameras is equivalent to several pins, *provided that one
output can source the current every input draws*. An ATmega2560 output is rated
for roughly 20 mA, so a fan-out is only safe if the inputs' combined current
stays well inside that — and getting it wrong risks the output driver, not just
the trigger. One pin per camera, the arrangement shipped in the profile, gives
each input the whole 20 mA budget and needs no arithmetic at all, which is why
it is the recommendation.

The *order* of the list carries no meaning, incidentally. All of the pins are
driven together with the same edge, so which entry corresponds to which physical
camera makes no difference to anything. What matters is that the list has one
entry per camera and that no camera's `Line1` is left unconnected: a camera fed
by a pin that is not in `trigger_pins` simply never triggers, and because the
default mode holds every trigger until all cameras have delivered it — see
*RAM* below — one camera that never delivers stalls the whole recording.

### Network

The network is where an undersized rig usually fails first, and it fails in the
least helpful way: a link near capacity does not slow down, it drops packets, and
a GigE Vision frame with a missing packet is a frame you do not get. So work out
what the cameras will actually put on the wire before choosing ports and
switches, rather than after. One camera's demand on its link is the frame size
again, this time in bits and multiplied by the frame rate:

```
bits per frame  = width x height x bits per pixel
                = 1920 x 1200 x 8            = 18,432,000 bits
bits per second = bits per frame x frame rate
                = 18,432,000 x 100           = 1.84 Gbit/s per camera
```

A few common configurations, for reference:

| Format | Bytes per frame | Payload rate |
|---|---|---|
| 1920x1200 mono8, 100 fps | 2,304,000 | 1.84 Gbit/s |
| 1920x1200 mono8, 30 fps | 2,304,000 | 553 Mbit/s |
| 1280x1024 mono8, 100 fps | 1,310,720 | 1.05 Gbit/s |

Those are payload only, before the headers of GVSP (the GigE Vision streaming
protocol the cameras speak), UDP, IP and Ethernet, so treat them as a floor
rather than a budget.

**Two links have to carry that rate, not one.** This is the distinction worth
getting straight before spending any money, because both links get loosely
called "GigE" and the word invites exactly the wrong purchase. "GigE Vision" is
the name of the protocol, not a claim about speed: the same standard runs over
1, 2.5, 5, 10 and 25 Gbit/s links alike. The first link is the **camera's own**,
and it has to carry that one camera's full payload rate by itself, with nothing
to share the load. The second is the **host port** the cameras eventually arrive
on, which has to carry the sum of everything behind it. Sizing the second
correctly while getting the first wrong is the classic irrecoverable mistake,
because no amount of tuning fixes a camera whose own cable is too slow.

So take the camera's own link first. One 1920x1200 mono8 camera at 100 fps wants
1.84 Gbit/s, which is nearly twice what a 1 Gbit/s link can carry, so a plain
1 GigE camera cannot do that configuration at any packet size: 1 Gbit/s divided
by 18,432,000 bits per frame is about 54 fps at line rate, and less once headers
and a sane margin are counted. Running 1920x1200 at 100 fps therefore needs a
camera with a multi-gigabit interface — 5GBASE-T or 10GBASE-T — which is why the
reference rig uses the a2A1920-165g5m, a 5 GigE model. The same format at 30 fps
is a different story: 553 Mbit/s fits on 1 GbE with room to spare, so if 30 fps
is genuinely enough for what you are filming, gigabit cameras are enough too.
Cutting resolution is a lever as well, but a weaker one than it looks — the
table above puts 1280x1024 mono8 at 100 fps at 1.05 Gbit/s, still over 1 GbE
line rate. Frame rate is the term that moves this number the furthest.

**Port speed and cameras per port.** Then add up the cameras sharing a host port
and keep the total comfortably under line rate — a link running near capacity
turns ordinary bursts into packet loss. Three 1920x1200 mono8 cameras at 100 fps
is 5.53 Gbit/s, so that group needs a 10 GbE port and still leaves about 45%
headroom. A 1 GbE host port fits exactly one camera at 30 fps and no more: two
of them are already 1.1 Gbit/s, over line rate before a single header. The rule
that falls out of this is just

```
host ports = ceil(n_cameras / cameras that fit on one port)
```

Six 100 fps cameras at three per 10 GbE port is therefore two host ports, and
something has to sit between the cameras and those ports, because three cameras
cannot share one port without a switch. That is how the reference rig is built:
six cameras in two groups of three, each group behind its own switch, each
switch uplinked to its own 10 GbE port on the host. Nine cameras would be three
groups, three switches and three ports.

Two things about those switches are easy to overlook. Their access ports have to
run at the camera's own link speed, not just the uplink — a switch with 1 GbE
access ports throttles a 5 GigE camera to 1 GbE however fast its uplink is,
which puts you straight back into the first failure above. And every switch in
the path is one more device that has to pass jumbo frames, covered next: one
switch left at the default 1500-byte MTU silently discards every frame from the
cameras behind it.

**Jumbo frames.** The reference camera settings file sets
`GevSCPSPacketSize 9000`, so each GVSP packet carries 9000 bytes. Every device
in the path — NIC and any switch — must accept an MTU of 9014 bytes (9000 of
payload plus headers), or those packets are dropped. Set the adapter's *Jumbo
Packet* property to 9014 and its *Receive Buffers* to the maximum.

**Inter-packet delay.** `GevSCPD` (10000 in the reference settings file) spaces
out one camera's packets so that cameras sharing a port do not burst into each
other; at zero they collide and the switch drops what it cannot forward. The
right value depends on how many cameras share the port, how fast the link is and
how big a frame is, so it has to be re-tuned rather than copied when any of those
change.

**Cabling.** The cable has to match the link it is carrying: a 10 GbE copper run
needs Cat6a or better, and a marginal cable shows up as packet loss rather than
as a link that refuses to come up.

### CPU

Sizing the CPU is less about raw speed than about meeting a deadline: every
camera's grab loop has to finish everything it does with a frame before the next
frame arrives. Three things consume CPU, and only the first two are visible
inside the application:

1. **One grab thread per camera.** Per frame it retrieves the driver buffer,
   copies the gray plane into a buffer from the NV12 ring — NV12 is the pixel
   layout the GPU encoder consumes, a full-resolution gray plane followed by a
   half-size color plane — hands that buffer to the encoder, and releases the
   driver buffer. Measured at 1920x1200 that is about
   0.8 ms of work per frame per camera, roughly 8% of one core at 100 fps. The
   whole loop iteration must finish inside one frame period (10 ms at 100 fps).
   When it does not, nothing errors: the driver buffer pool absorbs the deficit
   and every retrieved frame is a little staler than the last, until the pool
   runs out.
2. **One encoder thread per camera.** The compression itself happens on NVENC —
   the dedicated video encoder built into NVIDIA GPUs — so these threads mostly
   move bytes.
3. **GigE packet reassembly**, which is in the network stack rather than the
   app. With `gige_driver: socket` the packet resends run in user space; that
   costs more host CPU than the in-kernel driver but recovers lost packets
   instead of discarding the frame. Each port's receive work runs as deferred
   procedure calls, and if the adapter spreads that work over only one
   receive-side-scaling (RSS) queue, a single core carries the entire port.
   Three 1920x1200 cameras at 100 fps is about 78,000 packets/s per port,
   measured at 46% of a single core against a 4% average across 24 cores.

**How it scales.** Threads grow at two per camera plus the UI — about 13 busy
threads at six cameras. All of them share the one GIL, so the binding
constraint is GIL-held work per thread per frame rather than total core count:
up to about 300 µs per thread per frame is safe even at 17 threads, while
~1000 µs breaks the 10 ms budget at 11. Cores beyond the grab and encode threads
mostly help the network stack.

Against all of that, the startup hardware check asks only one question about the
CPU: it warns when it finds fewer than 4 physical cores, which is a floor for
running the application rather than a verdict on the rig you are sizing.

### RAM

RAM is the requirement the application will not negotiate over, because these
buffers are allocated up front: they either fit or the recording does not start.
That is deliberate — running out of memory halfway through a session is worse
than being told before it begins. Memory is dominated by two buffer allocations,
both linear in camera count.

The second of the two needs a word of explanation before its formula reads as
anything but symbols. By default the application runs in **kick-out mode**: a
trigger's frames are held rather than encoded until every camera has delivered
that same trigger, which is what makes the videos come out aligned with no
post-processing. Holding frames costs memory, and `kick_max_lag` — a profile
field, covered in step 7 — is how many frames of that holding are allowed
before a straggler's missing triggers are given up on. It sets the size of the
ring, so it also sets the RAM bill.

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
total                                            = 27.2 GiB
```

The same settings at 9 cameras: 19.3 + 21.6 = **40.9 GiB**. Halving the cap to
`kick_max_lag: 240` does not halve the ring, because the queue depth and the
spare slots stay where they are: 504 buffers instead of 744, or 1.62 GiB per
camera, which gives 22.6 GiB at 6 cameras and 33.9 GiB at 9.

Turning those totals into a purchase takes one more step, because they are what
has to be *available* when Record is pressed — the operating system, the GUI
itself and anything else the machine is running all sit on top. So budget
roughly twice the pool-plus-ring figure for your camera count rather than a
little more than it: a six-camera rig at `kick_max_lag: 480` wants something
like twice its 27.2 GiB, which is why the reference rig carries 63.4 GB for
exactly that configuration.

The application does this arithmetic itself at Record time, against the actual
number of cameras open, and **refuses to start if it will not fit** in available
memory:

```
Not enough RAM for 9 cameras: 40.9 GiB needed (19.3 pylon pool + 21.6 NV12
ring), 31.2 GiB available. Lower MaxNumBuffer or kick_max_lag, or close other
applications.
```

Above 75% of available memory it warns and asks before proceeding.

Separately from all of that, the startup hardware check warns when the machine
has less than 16 GB of RAM in total. That number is a floor for the application
to run at all, and it has nothing to do with the rig you are building — a 16 GB
machine passes the launch check happily and is then refused by every single
six-camera recording, because 16 GB is nowhere near the 27.2 GiB the buffers
ask for. The figure to size against is the one you worked out above, not the
startup warning.

### GPU

The GPU is what keeps the disk requirement in the next section sane: H.264
compression runs on NVENC, the card's dedicated video encoder, while capture is
still going on — which is also why the CPU section above has almost no encoding
work in it. Get this part wrong and the recording still happens — it just
happens uncompressed, at a rate few disks can absorb.

What is needed is an NVIDIA GPU with NVENC. Encoding runs through
PyNvVideoCodec, and the CUDA runtime arrives as a Python dependency
(`nvidia-cuda-runtime-cu12`), so a CUDA toolkit installation is not required.

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
~129 GiB per 10 min each. Record fewer cameras, or set `realtime_encode: false`
in the rig profile to put every camera on the raw path deliberately.
```

On the reference rig that probe returns **12 sessions on an RTX 5080**, which is
useful to know in both directions. Twelve is comfortably more than the six
cameras that rig runs and more than the nine it is being scaled towards, so a
current consumer card is not the binding constraint here. But the historic caps
of 2 and 3 are exactly why the number is probed instead of assumed, and an older
card really can grant fewer sessions than you have cameras.

You can ask a candidate GPU the same question before committing to it, and it
does not need the whole install — only uv, the repository and `uv sync`, which
is steps 1, 3 and 4, since this touches neither the cameras nor the network:

```powershell
uv run python -c "from gui_app import nvenc; print(nvenc.probe_max_sessions())"
```

It prints a line about warming the encoder and then a single number: how many
concurrent sessions the driver actually granted. Anything at or above your
intended camera count is fine. `0` means NVENC is not usable on this machine at
all, which puts you in the raw fallback described under *Disk* below — and that
is a very different disk budget.

With no working NVENC the startup check warns that `NVENC not found in ffmpeg —
there is no CPU fallback, so encoding raw.bin to mp4 and the post-hoc alignment
re-encode will FAIL`, and the capture path falls back to writing raw frames to
disk. Note what that warning says: there is no software encoder to fall back on,
because every mp4 writer asks for `h264_nvenc` by name, so on such a machine the
raw frames are captured and then cannot be turned into video at all. (The
real-time path's `.h264` to mp4 step is a stream copy and needs no encoder, but
that path needs NVENC to have produced the `.h264` in the first place.) ffmpeg
itself is bundled with the Python dependencies (`imageio-ffmpeg`); there is
nothing to install separately.

### Disk

Storage is the one requirement with two completely different answers, and which
one applies to you is decided by a single profile switch, `realtime_encode`.
They are not in the same league, so it is worth knowing which mode you intend to
run before you buy drives.

**Real-time GPU encode (the default).** Frames are compressed as they arrive and
only H.264 reaches the disk. At qp 21 that is about 4.6 KB per frame:

```
6 x 100 x 4600 B = 2.8 MB/s  ~= 10 GB per hour
```

A session is a few GB, and any ordinary drive keeps up.

**Raw fallback (`realtime_encode: false`).** This is the mode to use when the GPU
cannot hold one live encode session per camera, or the real-time path is
misbehaving: every frame is written to disk uncompressed and encoded afterwards.
It still needs NVENC — the post-hoc pass encodes with `h264_nvenc` as well — so
it is a way around a shortage of concurrent sessions, not around a card that has
no encoder at all. The rate is the full sensor payload, the same number the
network section arrived at:

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

Windows, today. The dependency is not deep, though, so it is worth knowing
exactly where it lives if you are considering a port. The Windows-specific
pieces are `PylonGigEConfigurator` and the
inbound firewall rule, the `configure_nic.ps1` and `make_shortcut.ps1` scripts,
and the two performance probes `probe_gil_wait.py` and `probe_native_cpu.py`.
The capture path itself does not depend on them: the binary-file flag is
resolved with `getattr(os, "O_BINARY", 0)`, the `subprocess.STARTUPINFO` use in
`encode_worker.py` is guarded by `sys.platform`, the serial port is a profile
field, and `arduino-cli` is found via PATH or an environment variable. NVENC and
pypylon both support Linux.

### The reference rig, for comparison

Everything above is arithmetic to apply to your own four numbers, and that is
deliberate — a part number would go stale long before the reasoning does. But
arithmetic is easier to trust next to a machine that is known to work, so here
is the configuration every measurement quoted on this page was taken on. Read it
as one known-good point and a sanity check on your own sums, not as a
requirement or a shopping list.

| Part | What the reference rig has |
|---|---|
| CPU | Intel Ultra 9 285K, 24 cores (8 performance + 16 efficiency) |
| RAM | 63.4 GB |
| GPU | NVIDIA RTX 5080, 12 concurrent NVENC sessions measured |
| Cameras | 6x Basler a2A1920-165g5m (5 GigE), 1920x1200 mono8 at 100 fps |
| Network | 2 switches, 3 cameras each, one 10 GbE host port per switch |
| Trigger board | Arduino Mega 2560 on `COM3`, one pin per camera |
| OS | Windows |

On that machine a 60-second six-camera run at 100 fps captured 100.00% of
triggers, and section 3 below quotes the rest of that run's numbers. It is also
the rig [PERF_EXPERIMENTS.md](PERF_EXPERIMENTS.md) logs every performance
measurement against, which is the place to look for the numbers behind the
numbers. Having the machine in view explains a couple of figures quoted earlier,
too: the "46% of a single core against a 4% average across 24 cores" is 24 cores
of that CPU, and the 63.4 GB is what makes 27.2 GiB of buffers comfortable
rather than marginal. Scaling in any direction away from this means going back
to the arithmetic in this section rather than adjusting the table.

---

## 2. Install the software

The ten steps below build up four things, in this order: a Python environment
that can talk to cameras (steps 1 to 4), a network the cameras can stream over
(step 5), the two files that describe your particular rig (steps 6 and 7), and a
trigger board carrying firmware you can account for (step 8). Step 9 starts it
all for the first time, and step 10 turns that into a double-click.

Do them in order. Each one ends with something you can check, and discovering at
step 9 that the camera settings file was wrong is a much slower way to find out
than checking at step 6.

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

pypylon, which the application uses to talk to the cameras, is only a thin
binding onto Basler's own SDK — so the SDK has to be there first, and pypylon
will not work if it is not. Download it from
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

With the two prerequisites in place, clone the repository. It lives wherever you
like; the Desktop is used throughout this page only so the example paths are
short.

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

One command reads `pyproject.toml` and builds the environment the application
runs in:

```powershell
uv sync
```

The first run downloads a Python interpreter and about two dozen packages, and
ends with a line like `Installed 22 packages in 42s`. Later runs are instant:

```
Resolved 26 packages in 1ms
warning: Skipping installation of entry points (`project.scripts`) for package
`panopticon` because this project is not packaged; ...
Checked 22 packages in 0.82ms
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

A GigE Vision camera is a network device, and three separate things have to be
true before it will stream: the camera and the adapter it is plugged into need
addresses on the same subnet, Windows has to let the camera's traffic reach the
application, and the adapter has to be configured for the kind of traffic the
camera sends. Miss the first and the camera never appears at all; miss the
second and it is discovered but never delivers an image; miss the third and it
delivers images that are missing packets. This step covers all three.

Start with addressing. **In an Administrator PowerShell window**, give the
adapters and cameras compatible addresses:

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

Steps 6 and 7 write the two files that describe your particular rig, and it is
worth being clear from the outset about which settings live where, because that
is the most common source of confusion later on. Every setting in this project
comes from one of four homes. The **camera settings file** (`.pfs`) is a dump of
the cameras' own registers: exposure, gain, resolution, pixel format and the
GigE transport parameters. The **rig profile** (a YAML file in `profiles/`) is
the application's own configuration: how many cameras, what frame rate, which
serial port, which pins. The **board config** (`configs/boards/*.yaml`)
describes the printed calibration board and is read only by calibration. Every
remaining number is a **code constant**, compiled in and changeable only by
editing the source. Those homes do not overlap — in particular the application
never writes exposure or gain into the `.pfs`, and the `.pfs` never overrides
the profile.

This step makes the first of the two. A `.pfs` — pylon's own name for it is a
GenApi persistence file — is a plain-text list of camera features that pylon
loads into every camera at open. **It is the only source of a recording's
exposure and gain** — the application never invents values of its own for one.
It does write both to the camera at every acquisition start, but what it writes
is the pair it read back from this file when the camera opened, capped at the
exposure ceiling below. Build it in pylon Viewer with one camera open, then save
it once and reuse it for all cameras.

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

**Getting enough light without overdoing it.** The reference values come from
measurement rather than taste. They were raised to 3000 µs and 6.0 dB from
2000 µs and 0 dB on 2026-08-11, because the older pair left 65% of pixels in
levels 0–15 with **21.5% clipped at exactly 0** — destroyed at the converter and
unrecoverable, however much you brighten the video afterwards. Overshooting is
just as easy: the 3.0x increase those values represent lands about 4% of pixels
saturated, whereas 7x would clip 12.7%. That is the reason for the order
illumination, then exposure, then gain — more infrared light is real photons and
better signal-to-noise, while gain amplifies the noise along with the signal.

Judge any change against a real recording, never against the live preview. The
preview free-runs at 30 fps, where the 33 ms period has room for almost any
exposure, so a setting that is over the ceiling looks perfectly healthy there
and only halves the frame rate once the cameras are actually triggered.

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

The `.pfs` from step 6 describes the cameras. The profile describes everything
else about the rig, and it is the file a new site really has to think about: it
is where the numbers from section 1 stop being arithmetic and become
configuration.

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

stim_safe_pins: [53]                 # YOUR stim pins, forced LOW from the
                                     # instant the sketch boots; [] if none
calibration_exposure_us: 15000       # calibration-only exposure; 0 = keep .pfs
calibration_gain_db: -1              # calibration-only gain; -1 = keep .pfs
```

Field by field, for when you need to look one up. The middle column is what the
application uses when the field is absent from the file, which is not always the
same thing as what the reference rig runs — the paragraphs after the table sort
out where those two part company.

| Field | If the field is omitted | What it does |
|---|---|---|
| `name` | file stem | Label in the profile dropdown. |
| `frame_width`, `frame_height` | 1920, 1200 | Frame geometry. Must match what the cameras report after the `.pfs` loads. |
| `frame_rate` | 100 | Trigger rate for recordings. Sets the frame period the grab loop must keep up with, and the H.264 GOP length — the spacing of the keyframes a player can start decoding from, one per second here. |
| `calibration_frame_rate` | 30 | Trigger rate for calibration captures. A slowly waved board gains nothing from 100 fps, and the longer period raises the exposure ceiling from about 3.94 ms to about 27 ms (about 3.5 ms and 24.5 ms after the 90% clamp), which is why `calibration_exposure_us: 15000` is safe. |
| `quality` | 21 | NVENC constant quantizer. |
| `encode_parallel` | 3 | Concurrent encode/remux jobs after a recording. Counts against the NVENC session budget. |
| `realtime_encode` | `true` | GPU H.264 during capture. `false` writes raw frames and encodes afterwards, at the raw disk rate from section 1 (1.38 GB/s for six cameras at 100 fps, against 2.8 MB/s encoded). |
| `realtime_kick` | `false`, which selects post-hoc alignment instead | Gate frames through the cross-camera coordinator during capture, so the videos are trigger-aligned with no post-hoc re-encode. With it off, alignment runs after encoding and re-encodes each video. The shipped `3dpose` profile sets `true`, and kick-out is the mode the rest of this documentation describes. |
| `kick_max_lag` | 240; the shipped `3dpose` profile sets 480 | How many frames one camera may lag the others before its missing triggers are force-dropped. Drives the NV12 ring size, so it is the main RAM lever — see *Choosing `kick_max_lag`* below. |
| `gige_driver` | `socket` | `socket` is user-space with reliable packet resends. `filter` is the in-kernel driver: less CPU, but with default resend settings it discards a frame rather than asking for the lost packet again — measured dropping about 23% of frames under six cameras at 100 fps on 2026-06-12. `auto` leaves pylon's default. |
| `trigger_rate_limit` | 165 — the reference camera's own maximum frame rate, not a property of Panopticon | `AcquisitionFrameRate` written in trigger mode. Set it to *your* camera's maximum frame rate; 165 is that number for the reference a2A1920-165g5m. A higher limiter means a smaller `1/rate` floor and therefore more exposure headroom, so this is the knob to reach for when frames come out dark. But a value above the camera's own maximum buys nothing: the camera clamps it silently, and the ceiling the software computes from the profile number would then be larger than the real one — which is the condition behind the "looks perfect but is out of sync" failure in section 4. Keep it above the trigger rate, and never set it to `0`; see the exposure ceiling in step 6. |
| `pfs_path` | — | The camera settings file from step 6. |
| `output_dir` | — | Where recordings go. The sidebar's directory button overrides it per machine. |
| `board_config` | — | Description of the printed calibration board used by calibration (`configs/boards/*.yaml`) — a ChArUco board, meaning a chessboard with a unique ArUco marker printed inside each white square so that a detector can name individual corners from a partial view. The shipped `charuco_8x8_15mm.yaml` describes the reference rig's own printed board, `board_legacy: true` and all, so it is the wrong file to point a freshly printed board at — see the `0 boards detected` entry in section 4. Whatever board you use, measure it and correct `square_length`: it sets the world scale of the whole solve, so a wrong value scales every 3D coordinate downstream. |
| `serial_port` | `COM3` — the reference rig's port rather than a sensible fallback | The trigger board's serial port. Step 8 shows how to find yours. |
| `trigger_pins` | `[2, 4, 6, 8, 10, 12]` — the reference rig's wiring rather than a sensible fallback | One output pin per camera, each wired to that camera's `Line1`; see *Wiring the trigger line* in section 1. Also refused as stimulation pins, since extra edges on one camera would break alignment. |
| `n_cameras` | 0, a code fallback nobody should keep | Refuse to start unless exactly this many cameras enumerate. `0` disables the check. Camera names are positional by serial-number order, so a camera that fails to enumerate renames every camera after it and attaches the calibration extrinsics to the wrong physical cameras. Set it. |
| `stim_safe_pins` | `[53]` — the reference rig's laser pin, which is no protection at all on a rig wired differently | **Set this to the pin or pins your own stimulus hardware is wired to.** They are driven LOW in the first statement of the sketch's `setup()`, before the serial handshake. `setup()` blocks on that handshake until the GUI connects, so a pin not listed here is left floating for the whole wait — which a powered laser driver reads as ON. Pins a loaded stimulation paradigm uses are added to this list automatically, so what you write here is the floor that protects the launch-time recording-only sketch: exactly the sketch that is running when no paradigm is loaded. `[]` on a rig with no stimulation hardware. |
| `calibration_exposure_us` | 0.0 | Exposure used for calibration captures only; the `.pfs` values are restored for recordings. `0` keeps the `.pfs` value. The binding limit here is motion blur rather than the ceiling: at 15 ms a briskly waved board smears and its corners stop resolving, so move the board slowly and pause at each pose. |
| `calibration_gain_db` | -1.0 | Same for gain. `-1` keeps the `.pfs` value. |

Paths may be relative to the repository root or absolute.

**What the shipped profiles deliberately override.** Copying
`profiles/3dpose.yaml` and editing it, as suggested above, gets all of this
right without thinking about it. Writing a minimal profile from scratch does
not, because three of the fallbacks in that table are not what the reference rig
runs: the shipped profile sets `realtime_kick: true`, `kick_max_lag: 480` and
`n_cameras: 6`. Real-time kick-out — the mode the rest of this documentation
describes, and the mode the RAM arithmetic in section 1 assumes — is on because
the profile says so, not because the field is optional. Leave it out and you
silently get post-hoc alignment with a re-encode instead, and nothing anywhere
will tell you that is what happened.

**Choosing `kick_max_lag`.** This is a decision rather than a lookup, because it
trades RAM against frames and the evidence for that trade is specific. A clean
A/B on 2026-08-11 — 100,968 frames over 17 minutes, identical camera settings,
only the cap differing — released 87.68 fps with 12.34% loss at 240, against
99.14 fps with 0.88% loss at 480. So the 4.7 GiB that 240 saves at six cameras
was not free when it was measured. Going the other way is not a free win
either: `kick_max_lag: 1000` starved capture outright, losing 24% of frames on
2026-06-17, and the ring grows linearly with the cap the whole way there.

What has changed since is the grab loop. After the fix of 2026-09-03 the
observed cross-camera lag on the reference rig is median 0, p95 1, max 2 frames
— so 480 frames of headroom currently buys nothing, because no camera is
lagging. Dropping back to 240 would very likely be free now, and that reduction
is deferred pending an A/B on the rig rather than being wrong. Until that
measurement exists the practical rule is: size RAM for 480 if you want the
shipped configuration unchanged, and treat 240 as a tested-but-superseded option
to A/B on your own rig, never as a blind saving.

### Step 8 — flash the trigger firmware

The board needs firmware before it can trigger anything, and this step is a
little unusual: there is no `.ino` file for you to open and upload. The sketch
is generated rather than stored. `gui_app/stim_compiler.py` compiles the
combined camera-trigger and stimulation sketch from the profile and the node
graph, and `recording_only_sketch()` is that same sketch with no stimulation in
it — camera triggers plus the safe-pin boot guard.

The application flashes it for you. At every launch it builds the
recording-only sketch, compares its SHA-256 against the last sketch it uploaded,
and reflashes if they differ. A stimulation paradigm lives in the board's flash
memory, so it survives closing the GUI, a power cycle and unplugging the USB
cable, and nothing can read it back over serial; the launch-time flash is what
guarantees the board carries no stimulation unless you deliberately applied one
this session.

That is a safety property as much as a convenience, and on a rig with a laser
wired to the board it is the important one: you never have to trust your memory
of what was uploaded last week.

One window is beyond the reach of any firmware, though, and a rig with a laser on
it should be built in full knowledge of that. Flashing the board resets it, and
while it is resetting and waiting in its bootloader no program is running at all,
so every pin is high-impedance — which a powered laser driver reads as ON and
answers with a visible flash. The safe-pin guard in `setup()` cannot help,
because it only runs once the sketch is running. Nor is a pulldown resistor a
general answer: a driver input with a stiff internal pullup would need a resistor
low enough to exceed the board's per-pin current limit when the pin is driven
high. The only hard gate, then, is the laser's own interlock. Fit one if you want
that guarantee, and until you do, key the laser off or block the beam before
anything that flashes the board — [WORKFLOW.md](WORKFLOW.md) sets out exactly
when a session does that.

So installing the firmware really means installing the compiler and letting the
application do the flashing:

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

Everything is now in place, so start it up and see what it says. The first
launch is also the most informative one: the console tells you what it found,
camera by camera, and reading that output is the quickest way to confirm that
the previous eight steps landed.

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
[acq] board already carries the recording-only sketch (no stim); skipping flash
[acq] opening teensy on COM3
```

The last two lines arrive about a second and a half after the window does: the
firmware check and the serial open are deliberately deferred so the window can
paint and Windows can finish registering the taskbar entry first.

What to check in that output:

- one `[camN] <serial> <width>x<height> Mono8` line per camera, with the
  geometry and format you configured;
- `zero-copy view OK (PaddingX=0 PaddingY=0)` for every camera;
- an `[hw] NVENC sessions:` count at least equal to your camera count. That line
  is not printed at launch — the session probe runs at the first Calibrate or
  Record, alongside `[nvenc] warmed (first-Encode import done
  single-threaded)` — so it is the first acquisition rather than the launch that
  answers this. The probe asks for two more sessions than there are cameras and
  stops as soon as it gets them, which is why it reads `[hw] NVENC sessions: 8
  (at least — probe stopped at its limit), needed 8` on the six-camera reference
  rig even though that card will grant 12 if asked for more.

Every launch also writes the same text to `logs/panopticon_<date>_<time>.log`,
which is where to look when the app is started from the desktop shortcut and has
no console.

A *Hardware Check Results* dialog appears only if something is below the
recommended minimum (cores, RAM, free disk, disk write speed, NVENC). It lists
what it found.

### Step 10 — desktop shortcut

Nobody wants to open a terminal to start a session, so the last step makes the
launch a double-click:

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

A window that opens is not proof that the rig works. The capture path is
timing-bound, and its failures are quiet by nature: a frame that never arrived
looks much like a frame that was never triggered. So verification comes at two
levels. First the code on its own, which needs no hardware and takes a minute.
Then the real capture path with the cameras running, which is the only thing
that can tell you whether *this* machine keeps up.

### Without any cameras

Start with the level that cannot fail for physical reasons. Four test suites run
on the code alone — they are plain scripts, not pytest.

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

The suites above say the logic is sound. They cannot say whether this particular
machine, network and set of cameras hold the frame-period deadline, and that is
the question worth answering before a real session:

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

The tables below are grouped by where the trouble shows up, and the left-hand
column quotes what the application actually prints — so the quickest way in is
to search this page for a fragment of the message you got. Most entries are
refusals rather than crashes: where recording something unusable is a real
possibility, the application would rather stop and explain itself. One entry
gets a subsection of its own, under *Recording quality*, because it is the
failure that looks like success.

### Installing

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

### Cameras and the network

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
| `NVENC granted only N concurrent sessions but M cameras need one each.` | The driver's session cap is below the camera count, often because another process holds sessions (a browser's hardware encode, an orphaned ffmpeg). Close them, record fewer cameras, or set `realtime_encode: false` in the profile to put every camera on the raw path deliberately — both shipped profiles carry that field set to `true`, so it is a value you change rather than a line you add. Read the *Disk* part of section 1 first: raw needs roughly 500x the space. |
| `NVENC granted no encode sessions, so real-time encoding cannot start.` | No sessions available at all. Set `realtime_encode: false` in the profile to write raw frames and encode afterwards — and read the *Disk* part of section 1 first, because that is a completely different disk budget. |
| `Disk may be short: a 10-minute recording would need ~N GiB` | A warning, not a refusal: 10 minutes is an assumed worst case, not a known length. A shorter recording is fine. |
| `Disk is tight: a 10-minute recording needs ~X GiB of Y GiB free` | The milder version of the same check, raised once a ten-minute recording would use more than 80% of the free space. It will fit, but there is no room for a second session — clear space before the day's work rather than mid-experiment. |
| `Raw capture will write N GiB/s.` | Spread the output across drives. A single consumer NVMe falls to about 1.6 GB/s once its SLC cache is exhausted. |

### Recording quality

| Symptom | Cause and fix |
|---|---|
| `cycle` above the frame period | The grab loop is not finishing inside one period. Something else is using the CPU, or a change added work to the hot path. |
| Frame rate about half of what was asked | `exposure + 1/AcquisitionFrameRate` exceeds the trigger period, so every second trigger is skipped. Lower `ExposureTime` in the `.pfs` until it is under the ceiling for your trigger rate. Raising `trigger_rate_limit` buys headroom in principle too, but only on a camera whose maximum frame rate is above the current limiter — the reference a2A1920-165g5m is already at its maximum of 165, and a higher value is clamped silently by the camera, so the change appears to apply and does nothing. Never set the limiter to `0`; see step 6. |
| `Buffer_Underrun_Count` nonzero | The driver's buffer pool ran dry: a host-side problem, not the network. |
| Frames lost with high `Resend_Request_Count` and nonzero `Failed_Buffer_Count` | Network. Check RSS receive queues, jumbo frames end to end, Energy Efficient Ethernet, and cameras per port. |
| Roughly a quarter of frames missing, in single-frame gaps | `gige_driver: filter` discards a frame with a lost packet instead of asking for it again. Use `socket`. |
| `camera did not start grabbing` / `stream dead after N re-arms` | That camera was retired from the alignment set so the others keep recording aligned. The session yields N-1 cameras instead of nothing. |
| `block-ID bookkeeping claimed X frames but only Y were persisted` | An encoder fell behind or died. The metadata is truncated to what is actually in the video and a `WARNINGS.txt` is written beside it. |
| Video will not seek in the labeler | The mp4 lost its explicit GOP. Every encode path must pass `-g <fps>` and `-movflags +faststart`. |
| Every camera has the same frame count and no counter moved, but the views are out of sync | The block IDs are not trigger ordinals. See the subsection below. |

### The recording looks fine but the views are out of sync

This one has a heading of its own because it is the only failure on this page
that presents as success. You meet it in one of two ways. Either the recording
passes every check you would think to run — the videos play, every camera has
the same number of frames, no dropped-frame, packet, underrun or forced-drop
counter has moved — and yet triangulated points sit nowhere near the animal and
the views visibly disagree about when a fast movement happened. Or a
`WARNINGS.txt` appears in the recording folder saying that one camera's block IDs
advanced at the wrong rate.

**What it means.** Everything downstream of the cameras treats "same block ID"
as "same instant". A block ID is the GigE Vision frame counter that arrives with
each frame, and the alignment scheme rests on it being the *trigger* ordinal:
the frame carrying block ID *N* is the frame the *N*th trigger produced, on
every camera. That identity holds only while a camera produces exactly one frame
per trigger. A camera whose exposure exceeds the ceiling — `ExposureTime`
plus `1/trigger_rate_limit` must stay under `1/frame_rate`, so about 3.94 ms of
exposure at 100 fps with the limiter at 165 — is still busy when the next pulse
arrives, and it does not drop that frame. It never acquires it, so it never
consumes a block ID for it either. From that moment its block ID *N* is trigger
*N+k*, and the offset grows every time it happens. Its IDs stay gapless, its
frame count still matches the other cameras because only common IDs are kept,
and no other check in the pipeline can see it, because the release rule compares
block IDs and nothing else. The result is videos that look perfect while
drifting apart in time.

**The software now detects it.** The camera's device clock is a free-running
hardware oscillator, independent of its block-ID counter, so the two together
settle the question: over any span, block IDs must advance at the trigger rate.
That comparison runs per camera when a recording stops, and again inside
`align_recording()`, which means `uv run 2_align.py <recording_dir>` can
re-examine a recording you already have. Nothing was added to the per-frame hot
path — the device timestamps were already being collected. When a camera fails
the check, a *Recording completed with problems* dialog names the camera, its
measured rate and roughly how far the views drift apart by the end, and the same
text is written to `WARNINGS.txt` beside the videos so it is still there months
later.

The tolerance is 0.3%, and it comes from measurement rather than theory. Across
74 camera-sessions of real data (2026-06-12 to 2026-09-03, at both 30 and
100 fps, including the sessions that lost 24% and 43% of frames) the measured
rate sits between **+220 and +250 ppm** of the configured rate — the fixed
offset between the trigger board's resonator and the cameras' own oscillators.
0.3% leaves 12x margin over the worst real sample while still catching a camera
that ignores one trigger in a hundred. The check abstains below 300 frames or
2 seconds of data, where end effects and a genuinely skipped trigger cannot be
told apart, so silence on a two-second test clip means nothing was judged rather
than that all is well.

**The fix is exposure — but read the log before you edit the `.pfs`.** The
application does not take the profile's word for this. `apply_exposure_gain()`
recomputes the ceiling at every acquisition start and clamps to it
unconditionally, logging `CLAMPED from ...` when it does. So the natural first
move — open the `.pfs`, find a value comfortably under 3.94 ms, and then be
baffled — is the wrong one, because if the clamp had had anything to act on it
would already have acted.

What an enforced clamp still rests on is its inputs, and both of them come from
the profile. The ceiling is `(1/frame_rate - 1/trigger_rate_limit) x 0.9`, so
the clamp can be entirely correct about a ceiling that is not the real one. If
`trigger_rate_limit` is set above the camera's own maximum frame rate the camera
clamps it silently, which means the software subtracts a smaller `1/rate` floor
than the camera actually enforces and lands on a ceiling that is too generous —
the reason step 7 says to set that field to your camera's real maximum and
nothing else. Equally, if the board is triggering faster than the profile's
`frame_rate` claims, the true period is shorter than the arithmetic assumed.
And there is one case where the clamp does not run at all: it compares against
the baseline exposure read back when the camera opened, so a camera whose
exposure could not be read back has nothing to compare against and simply keeps
whatever the `.pfs` gave it.

That makes the diagnostic order clear. The first thing to read is not the `.pfs`
but the exposure line the application logs as each acquisition starts, in the
console and in `logs/panopticon_<date>_<time>.log`:

```
[cam1] exposure=3000 us gain=6.0 dB (ceiling 3545 us at 100 fps)
```

It states the exposure that was actually applied and the ceiling it was measured
against, so the two numbers you need are side by side. The line is printed for
camera 1 on every start, and for any camera that was clamped — in which case it
carries a ` CLAMPED from ...` suffix naming the value it rejected and why. If
the clamp fired, the
exposure was too long and lowering `ExposureTime` in the `.pfs` is exactly the
fix. If it did not fire and the `.pfs` really is under the ceiling, then the
ceiling itself is the next suspect rather than the exposure: check
`trigger_rate_limit` against the camera's maximum frame rate, and `frame_rate`
against what the trigger board is actually driving.

Once you know the true ceiling, lower `ExposureTime` in the `.pfs` until the
exposure is under it for the frame rate you record at, and take the recording
again. The affected recording cannot be repaired and must not be used for 3D
reconstruction. If you need the light back, add illumination rather than
exposure — see step 6.

**One pattern that is not this.** If *every* camera reports the same wrong rate,
suspect the reference rather than the cameras, because cameras do not fail
identically. The usual causes are a profile `frame_rate` that does not match
what the trigger board is really driving, or a camera model that does not report
its device timestamp in nanoseconds. The message says exactly that instead of
blaming exposure once per camera; in that case the videos are probably aligned
with each other and it is the absolute timebase that is in question.

### Calibration

| Symptom | Cause and fix |
|---|---|
| `0 boards detected` | The board description in `configs/boards/*.yaml` must match the physical board, and the flag to check first is `board_legacy` — it decides which of two ChArUco corner layouts the detector maps the markers onto. It must be `false` for a board generated with OpenCV 4.6 or newer, and `true` only for one printed to the older layout. A mismatch in *either* direction detects every marker perfectly well, maps them onto the wrong layout, and returns zero board corners with no error and no warning, so flip the flag before suspecting the print, the lighting or anything else. Note that the shipped `configs/boards/charuco_8x8_15mm.yaml` carries `board_legacy: true`, because the reference rig's physical board predates the layout change — which makes a copy of that file the wrong starting point for a board you printed yourself with a current OpenCV. |
| numpy ABI error from `1_calibrate.py` | The environment is half-built. The solve runs in the project environment rather than resolving a set of its own, so its OpenCV and numpy must be the ones `uv sync` installed. Run `uv sync` again in the repository and retry. |
