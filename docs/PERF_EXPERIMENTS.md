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
