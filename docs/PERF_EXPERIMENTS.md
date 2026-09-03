# 9-camera performance experiments

Running log for the 6→9 camera optimization effort. **Every experiment goes here,
including failures and dead ends.** Branch: `perf/9cam-optimization`.

Background: the frame-drop root cause (2026-08-11) is that each grab thread must
finish its cycle inside 10 ms; when it can't, every camera silently accumulates
backlog and the coordinator's "lag" is the *difference* between backlogs. See
`CLAUDE.md` and the session notes for the full derivation.

Hardware: DESKTOP-JLD8DB7, Intel Ultra 9 285K (24 cores / 24 threads, 8P+16E),
63.4 GB RAM, RTX 5080, 6× Basler a2A1920-165g5m on Ethernet 4/5 (10 GbE each).

Baseline per-frame budget as measured on 2026-08-11 (6 cameras, in the GUI):

| step | ms |
|---|---|
| copy into NV12 ring | 2.7 |
| `result.Release()` | 1.2 |
| display downsample | 0.3 |
| router submit | 0.02 |
| **work** | **~4.2** |
| **slack in the 10 ms period** | **~5.8** |

---

## E1 — Is the gray→NV12 copy GIL-bound? (`probe_copy_scaling.py`, 2026-09-03)

**Question.** The production copy was measured at 2.7 ms for 2.304 MB ≈ 850 MB/s,
roughly 10× slower than memory bandwidth. Two competing explanations gate the whole
architecture: (a) it is **GIL-bound**, i.e. serialized, so 9 cameras is impossible with
threads and the per-camera process split is mandatory; or (b) it is a **memory-locality**
problem — the NV12 ring is `max_lag + ENCODE_QUEUE_DEPTH + 64` = 744 buffers × 3.456 MB
= **2.39 GiB per camera** (`grab_thread.py:244`), so every write hits a cold cache line
and a freshly-faulted page.

**Method.** No cameras. Reproduce exactly `nv12_buf[:H, :] = gray` in N concurrent
threads, each with its own ring and source, started on a barrier. Vary thread count
(1/3/6/9), ring depth, and pre-faulting. Two controls included so the scaling numbers
can be trusted at all: `sleep` (releases the GIL → must scale) and a pure-Python loop
(holds the GIL → must serialize). Read **aggregate ops/s** for scaling, not per-op median.

**Controls behaved.** `sleep` 426 → 3921 ops/s across 1→9 threads (**9.2×**, perfect
parallelism detected). `pyloop` 3160 → 3964 ops/s (**1.25×**, serialization detected).
The harness can distinguish the two cases.

**Results.**

| config | threads | per-op median | aggregate |
|---|---|---|---|
| small ring (4 buf), prefaulted | 1 | 0.079 ms | 12,152 ops/s |
| small ring, prefaulted | 9 | 0.648 ms | 11,044 ops/s |
| large ring (200 buf), **cold** | 1 | **0.395 ms** | 2,423 ops/s |
| large ring, **cold** | 9 | 0.826 ms | 9,861 ops/s (**4.07× scaling**) |
| large ring, **prefaulted** | 1 | **0.080 ms** | 12,124 ops/s |
| large ring, prefaulted | 9 | 0.662 ms | 10,009 ops/s |

Single-thread ring-size sweep, cold: 4 buf **0.074 ms** → 50 buf 0.474 → 200 buf 0.504
→ 744 buf (full production size) 0.457 ms. The penalty appears as soon as the ring
exceeds cache and then flattens.

**Findings.**

1. **The copy is NOT GIL-bound.** numpy releases the GIL for the contiguous copy. The
   cold large-ring case scaled **4.07×** across 9 threads — impossible if serialized.
   Hypothesis (a) is dead; the process split cannot be justified on the copy alone.
2. **Pre-faulting the ring is a 5–6× win on the copy**: 0.395–0.504 ms cold →
   **0.080 ms** warm, single-threaded. Same allocation, same access pattern; the only
   difference is who pays the page fault. Free, ~5 lines.
3. **Aggregate copy throughput is memory-bandwidth limited at ~24–26 GB/s**, and flat
   across thread counts. This does *not* constrain us: 9 cameras at 100 fps need **900
   copies/s** against ~11,000 achievable — an **8% duty cycle**. Copy bandwidth is a
   non-issue at the real frame rate. (The benchmark's per-op inflation at 9 threads is
   an artifact of hammering continuously; the real workload is one copy per 10 ms.)

**The important reinterpretation.** Pre-faulting explains only 0.5 ms of the production
2.7 ms. Cold faults cost ~0.46 ms, not 2.7. So what is the other ~2.2 ms?

The answer is where the timer sits. numpy **releases the GIL** for the memcpy and must
**re-acquire** it before returning — and that re-acquisition wait happens *inside* the
`perf_counter` bracket around `buf[:H,:] = img`. So `t_copy = 2.7 ms` was never 2.7 ms
of copying. It is ~0.1–0.5 ms of copying **plus GIL re-acquisition latency**, i.e. the
2.7 ms figure is *evidence of GIL contention that was misattributed to the copy*.

That reframes the problem rather than solving it: the copy is cheap and parallel, but
the grab loop is still waiting on the GIL — just for other reasons (surrounding Python
bytecode, pypylon calls, Qt main thread, 6 encoder threads). Consistent with the June
observation of `avg_proc` 6.7 ms against ~1–2 ms of real work, and with the NVENC PoC
scaling only 1.66× across 6 threads.

**Consequences for the plan.**
- Pre-fault the ring: confirmed win, do it.
- Do **not** justify the process split by the copy. It must be justified (or refuted) by
  measuring the GIL-held fraction of the *whole grab loop*, which E2 must do.
- `t_copy`, `t_proc` and friends are contaminated by GIL wait wherever a GIL-releasing
  call sits inside the timed region. Any future instrumentation must separate
  "time executing" from "time waiting for the GIL", or it will keep misattributing.

**Status.** `probe_copy_scaling.py` committed. Raw numbers in `probe_out/copy_scaling.json`.

---

## E1a — Correction: the ring is ALREADY pre-faulted (2026-09-03)

**This retracts E1's headline recommendation.** Before changing `grab_thread.py` I read
the allocation at line 246 and found the ring is built with
`np.full((h*3//2, w), 128, np.uint8)`. `np.full` allocates with `np.empty` and then
*writes every byte*, so all pages are committed and resident before capture starts.

Verified directly — first write to each buffer of a fresh 60-buffer ring vs a second
pass over the same buffers:

| allocation | first-touch median | warm median |
|---|---|---|
| `np.empty` (untouched) | **0.402 ms** | 0.080 ms |
| `np.full(…, 128)` — **production** | **0.079 ms** | 0.079 ms |
| `np.zeros` | 0.398 ms | 0.076 ms |

`np.full` shows no first-touch penalty at all: the fault is already paid. (`np.zeros`
does show it — `calloc` hands back lazily-zeroed pages.) So the UV-plane initialization
to 128, which exists for correctness, has been doing pre-faulting for free all along.

**Consequences.**
- **No code change. The optimization was already there.** One of my top three candidates
  is void; recording it so nobody re-proposes it.
- E1's cold-ring numbers are still valid physics, they just **don't describe production**.
- This is now the important part: with a warm ring the copy should cost **~0.08 ms**, yet
  production reported **2.7 ms** — a 34× gap with page faults eliminated as the cause,
  and memory bandwidth eliminated too (9 cameras need 2.76 GB/s of traffic against a
  measured ~24 GB/s ceiling). **GIL re-acquisition wait inside the timer bracket is now
  the only surviving explanation**, which promotes it from "a factor" to "the dominant
  cost" — and makes GIL escape (processes / subinterpreters / free-threaded CPython /
  a C extension holding the release across the whole cycle) the main line of attack.
- Confidence is by elimination, not direct measurement. E2 must measure GIL wait
  directly before this is treated as established.

**Gap in E1 to close:** the prefaulted case was tested at 200 buffers (691 MB), not the
full production 744 buffers (2.39 GiB). TLB/cache behavior at true production ring size,
with 6–9 rings live and ~28 GB committed, is untested — Windows working-set trimming
could reintroduce soft faults that the small test cannot show.

---

## E2 — GIL wait, measured directly (`probe_gil_wait.py`, 2026-09-03)

**Question.** E1a left GIL re-acquisition wait as the only surviving explanation for the
2.7 ms copy, but by elimination. Prove it, and find how much GIL-held work per thread
per frame the system actually tolerates.

**Method.** `QueryThreadCycleTime` gives cycles a thread actually *executed*; wall comes
from `perf_counter`; the difference is wait. One measured thread does the production copy
at a realistic 100 Hz duty (not continuously — that would measure bandwidth saturation),
alongside N competitor threads each burning a fixed amount of GIL-held Python work every
10 ms. Competitor counts map to real configurations: 5 = 6 grab threads, 11 = today
(6 grab + 6 encoder), 17 = the 9-camera target.

**Results** (per-copy wall median, ms):

| GIL-held µs per competitor per frame | 0 comp | 5 | 11 | 17 |
|---|---|---|---|---|
| 100 µs | 0.135 | 0.145 | 0.125 | 0.223 |
| 300 µs | 0.130 | 0.321 | 0.324 | 0.128 |
| **1000 µs** | 0.130 | 1.031 | **10.19** | **17.14** |

Worst case: wall **×127** (0.135 → 17.14 ms) while exec went **×1.6** (0.139 → 0.220 ms).
At 17 competitors × 1000 µs, wait median was 16.82 ms of a 17.14 ms bracket.

**Findings.**
1. **CONFIRMED.** Contention inflates the *bracket*, not the work. A wall-clock timer
   around a GIL-releasing call reports waiting as working. The production 2.7 ms was
   never 2.7 ms of copying.
2. **The tolerance boundary is between 300 µs and 1000 µs of GIL-held work per thread per
   frame.** ≤300 µs is safe even at 17 threads; ~1000 µs blows the 10 ms budget at 11.
   This is the acceptance criterion for any hot-path change from here on.

---

## E3 — Zero-copy frame access (`probe_zerocopy.py`, 2026-09-03)

**The finding that mattered, and I did not find it — three independent audit lanes did.**
`grab_thread.py:339` did `img = result.Array`. pypylon's `GetArray()` **allocates a fresh
2.3 MB array and memcpys the driver buffer into it, with the GIL HELD** (pypylon is built
with SWIG `-threads`, but `GetArray` is on the explicit `%nothread` list). That is exactly
the ~1000 µs regime E2 identified as fatal, and it is the term that scales with camera
count.

**Method.** A/B three routes on a real camera in the production access pattern
(NV12 ring copy + preview decimate), measuring fps, wall, and exec via
`QueryThreadCycleTime`. Also gate two correctness risks: `GetArrayZeroCopy` reshapes the
buffer to (H, W) without accounting for row padding, and pypylon raises on context exit
if a reference to the view escaped.

| route | exec median | vs production |
|---|---|---|
| `result.Array` (production) | **0.8371 ms** | — |
| **`GetArrayZeroCopy`** (context manager) | **0.1571 ms** | **5.33× cheaper** |
| `np.frombuffer(GetBuffer())` | 0.9016 ms | 0.93× — **no gain** |

**Findings.**
1. **`GetArrayZeroCopy` removes ~0.68 ms of GIL-held work per frame per camera** —
   6.1 ms per 10 ms window at 9 cameras.
2. **The context manager survives our exact six-way access pattern** (snapshot copy, NV12
   ring copy, `os.write`, preview decimate, full-res HUD copy, encoder put). Every
   consumer copies, so no reference escapes and no exit error is raised.
3. **`np.frombuffer(GetBuffer())` is NOT a substitute** — measured no better than
   `.Array`, presumably because `GetBuffer()` copies too. This mattered: it was the
   minimal-diff option that would have avoided re-indenting 100 lines of hot path, and
   it does not work. **Recorded so nobody retries it.**
4. `PaddingX` is **not implemented** on the a2A1920-165g5m (`LogicalErrorException`), so
   padding cannot be checked directly. Implemented instead as a one-time comparison of
   the zero-copy view against `GetArray()` on the first frame of each acquisition; on
   mismatch the camera is retired and the thread stops rather than recording sheared
   frames.

### E3 rig validation — 6 cameras, 90 s, real triggers

Before, from the round-1 agents' production-shaped loop: **83 fps, cycle 12.0 ms,
avg_proc 5.2–5.5 ms, ~30 buffers backlogged.**

After, every camera, sustained for the whole run:

| metric | before | after |
|---|---|---|
| `cycle` | 12.0 ms | **10.00 ms** (= the trigger period, exactly) |
| `avg_proc` | 5.2–5.5 ms | **0.79–0.84 ms** |
| `avg_wait` (slack) | ~4.1 ms | **8.48–8.66 ms** |
| `copy` bracket | 2.7 ms | **0.71–0.75 ms** |
| `deliv_lag` | +10.7 s @20k frames | **−0.002 to −0.037 s** |
| `Buffer_Underrun_Count` | 245–882 | **0 on all six** |
| coordinator lag (median / p95 / max) | median 235–479, riding the cap | **0 / 1 / 2** |
| `forced` drops | 3413–12.34% | **0** |
| frames per camera | unequal | **9075 on all six, identical** |

`dropped=12` of 54,450 submissions (0.02%). `Failed_Buffer_Count` 4 on cams 1/4/6 and 0
on cams 2/3/5 — the known Eth5 leg, now negligible.

**THE ROTATING-LAGGARD MYSTERY IS SOLVED.** Since July the project has recorded that
"one camera — which one varies per session — drifts ~2.4 s behind in *submission* while
still capturing ~100% of triggers" and that it "is still unknown; it is not packet loss,
not the encoders, and not the cameras." It was `result.Array`: a GIL-held 2.3 MB memcpy
per frame per camera, ~837 µs, which put the pipeline in the regime E2 shows breaks at
11+ threads. Whichever thread lost the GIL/scheduling lottery accumulated backlog fastest
and became "the laggard" — hence the rotation. Median cross-camera lag is now **0**.

**Consequence for `kick_max_lag`.** 480 was validated on 2026-08-11 as the fix for 12.34%
loss, and it costs ring RAM linearly (`max_lag + ENCODE_QUEUE_DEPTH + 64` buffers/cam).
With observed lag now 0/1/2, that headroom is buying nothing. Lowering it back toward 240
(or below) should be tested — it halves the ring, which is what makes the 9-camera RAM
budget comfortable. **Do not lower it without a rig A/B**: the pool may be absorbing real
network jitter that this 90 s run did not sample.

**Status.** Implemented in `grab_thread.py`. All three test suites pass
(`test_frame_sync.py`, `test_stim_compiler.py`, `test_serial_handshake.py`).
