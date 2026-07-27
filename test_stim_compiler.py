"""Lock down the stimulus workflow -> Arduino sketch translation.

The graph semantics here are easy to break and hard to notice on the rig: a
mis-resolved start silently produces no chains, a cycle used to hang the walker
forever, and a pulse width at or above the period once compiled to a pin that
never fired. Each of those cost a debugging session, so they are pinned here.

No Qt, no serial, no arduino-cli, so it runs anywhere:
    python test_stim_compiler.py

The per-frame trace tests (10-13) additionally need numpy and skip without it;
`uv run python test_stim_compiler.py` runs the full set.
"""
from gui_app import stim_compiler as sc

try:
    import numpy  # noqa: F401
    _HAS_NUMPY = True
except ImportError:                       # keeps 1-9 runnable on a bare python
    _HAS_NUMPY = False


def B(bid, dur=1.0, pin=53, freq=10.0, pw=10.0, start=False, end=False):
    return {"id": bid, "x": 0, "y": 0, "pin": pin, "freq": freq, "pw": pw,
            "dur": dur, "start": start, "end": end}


def E(src, dst):
    return {"src": src, "dst": dst}


def ids(chain):
    return [b["id"] for b in chain]


def test_start_resolution():
    # Linear: the block with no incoming arrow starts.
    starts, needs = sc.resolve_starts([B("A"), B("B"), B("C")],
                                      [E("A", "B"), E("B", "C")])
    assert starts == {"A"} and needs == set()

    # Fan-in: two parallel chains merging both start.
    starts, needs = sc.resolve_starts([B("A"), B("B"), B("C")],
                                      [E("A", "C"), E("B", "C")])
    assert starts == {"A", "B"} and needs == set()

    # Pure loop with no flag: nothing can start, everything is flagged as stuck.
    loop_edges = [E("A", "B"), E("B", "A")]
    starts, needs = sc.resolve_starts([B("A"), B("B")], loop_edges)
    assert starts == set() and needs == {"A", "B"}

    # Same loop, one block pinned: that block wins, nothing is stuck.
    starts, needs = sc.resolve_starts([B("A", start=True), B("B")], loop_edges)
    assert starts == {"A"} and needs == set()

    # An explicit flag overrides the no-incoming-arrow rule within its group,
    # so a chain can be made to begin midway.
    starts, _ = sc.resolve_starts([B("A"), B("B", start=True), B("C")],
                                  [E("A", "B"), E("B", "C")])
    assert starts == {"B"}

    # Disconnected groups resolve independently.
    starts, _ = sc.resolve_starts([B("A"), B("B"), B("C"), B("D")],
                                  [E("A", "B"), E("C", "D")])
    assert starts == {"A", "C"}
    print("1) start resolution (linear/fan-in/loop/pinned/split): PASS")


def test_chain_extraction_terminates():
    chains = sc._extract_chains([B("A"), B("B"), B("C")],
                                [E("A", "B"), E("B", "C")])
    assert [(ids(c), l) for c, l in chains] == [(["A", "B", "C"], -1)]

    # Pinned loop closes back on itself instead of running forever.
    chains = sc._extract_chains([B("A", start=True), B("B")],
                                [E("A", "B"), E("B", "A")])
    assert [(ids(c), l) for c, l in chains] == [(["A", "B"], 0)]

    # Rho shape: a tail feeding a loop. This is the case that used to spin
    # forever, because no block in the cycle is a source.
    chains = sc._extract_chains([B("A"), B("B"), B("C")],
                                [E("A", "B"), E("B", "C"), E("C", "B")])
    assert [(ids(c), l) for c, l in chains] == [(["A", "B", "C"], 1)]

    # A loop nobody starts produces nothing rather than a bogus chain.
    assert sc._extract_chains([B("A"), B("B")], [E("A", "B"), E("B", "A")]) == []
    print("2) chain extraction terminates on cycles (linear/loop/rho): PASS")


def test_waveform_encoding():
    """freq/pulse-width -> integer microseconds, including the 100%-duty case."""
    def blk_line(freq, pw):
        ino = sc.compile_ino([B("A", dur=5, freq=freq, pw=pw)], [])
        return [l for l in ino.splitlines() if l.startswith("  {53u")][0]

    # 10 Hz / 10 ms = a real train: 100000 us period, 10000 us pulse.
    assert "{53u, 100000UL, 10000UL, 5000UL}" in blk_line(10, 10)
    # 10 Hz / 100 ms: pulse == period. Must still emit the pin, not skip it --
    # the sketch reads pw >= period as constant ON.
    assert "{53u, 100000UL, 100000UL, 5000UL}" in blk_line(10, 100)
    # 0 Hz = period 0 = hold LOW.
    assert "{53u, 0UL," in blk_line(0, 100)

    # No floating point may reach the sketch: updateStim() runs inside the
    # camera trigger busy-wait, where an AVR float divide (~30 us) blunts the
    # trigger edge precision.
    ino = sc.compile_ino([B("A", dur=5, freq=10, pw=10)], [])
    body = ino.split("void updateStim()")[1].split("// ===== SETUP")[0]
    assert "float" not in body and "0f" not in body, "float math in updateStim()"
    print("3) waveform -> integer microseconds, no floats in updateStim: PASS")


def test_safe_pins():
    # The laser pin is held LOW even when the workflow is empty.
    assert "const uint8_t STIM_PINS[] = {53};" in sc.compile_ino([], [], [53])
    # Workflow pins are unioned in, sorted.
    ino = sc.compile_ino([B("A", pin=44)], [], [53])
    assert "const uint8_t STIM_PINS[] = {44, 53};" in ino
    # A rig with no stim hardware must still emit valid C++ (no zero-size array).
    empty = sc.compile_ino([], [], [])
    assert "const uint8_t STIM_PINS[] = {0};" in empty
    assert "const int N_STIM_PINS = 0;" in empty
    # Pins go LOW before Serial.begin -- setup() blocks on the handshake, so
    # anything after it leaves the pin floating until the GUI connects.
    setup = sc.compile_ino([], [], [53]).split("void setup()")[1]
    assert setup.index("allStimLow();") < setup.index("Serial.begin")
    print("4) safe pins: empty workflow, union, no-stim rig, boot order: PASS")


def test_pin_conflicts():
    # Same pin twice in ONE chain is fine -- the blocks run in sequence.
    assert sc.pin_conflicts([B("A", pin=53), B("B", pin=53)], [E("A", "B")]) == []
    # Two independent chains on one pin would fight over the output.
    assert sc.pin_conflicts([B("A", pin=53), B("B", pin=53)], []) == [53]
    # Distinct pins are fine.
    assert sc.pin_conflicts([B("A", pin=53), B("B", pin=44)], []) == []
    print("5) pin conflict detection (within vs across chains): PASS")


def test_durations():
    # end_time_s = cumulative duration up to and including the flagged block.
    assert sc.end_time_s([B("A", 5), B("B", 3, end=True)], [E("A", "B")]) == 8.0
    # A parallel timer chain is how you bound a looping paradigm.
    assert sc.end_time_s(
        [B("A", 5, start=True), B("B", 5), B("C", 60, end=True)],
        [E("A", "B"), E("B", "A")]) == 60.0
    # Unflagged, and flagged-but-unreachable, both mean "no automatic stop".
    assert sc.end_time_s([B("A", 5), B("B", 3)], [E("A", "B")]) is None
    assert sc.end_time_s([B("A", 5, end=True), B("B", 5)],
                         [E("A", "B"), E("B", "A")]) is None

    # test_duration_s falls back to the longest terminating chain.
    assert sc.test_duration_s([B("A", 8), B("C", 20)], []) == 20.0
    # ...and is open-ended for a loop.
    assert sc.test_duration_s([B("A", 5, start=True), B("B", 5)],
                              [E("A", "B"), E("B", "A")]) is None
    print("6) end_time_s / test_duration_s across topologies: PASS")


def test_describe():
    chains = sc.describe(
        [B("A", 5, freq=10, pw=100, start=True), B("B", 5, freq=0, pw=0)],
        [E("A", "B"), E("B", "A")])
    assert len(chains) == 1 and chains[0]["loops"] is True
    assert chains[0]["loops_back_to_step"] == 0
    assert [s["mode"] for s in chains[0]["steps"]] == ["constant ON", "off (pin LOW)"]
    assert [s["mode"] for s in sc.describe([B("A", freq=10, pw=10)], [])[0]["steps"]] \
        == ["10% duty"]
    print("7) provenance description of chains: PASS")


def test_generated_sketch_is_wellformed():
    ino = sc.compile_ino(
        [B("A", 5, freq=10, pw=100, start=True), B("B", 5, freq=0, pw=0),
         B("C", 30, pin=44, freq=20, pw=5)],
        [E("A", "B"), E("B", "A")], [53])
    assert ino.count("{") == ino.count("}"), "unbalanced braces"
    assert "const int NUM_CHAINS = 2;" in ino
    assert "{CHAIN_0, CHAIN_0_LEN, 0}" in ino      # loops back to step 0
    assert "{CHAIN_1, CHAIN_1_LEN, -1}" in ino     # one-shot
    # The camera trigger protocol must survive untouched.
    for expected in ("void loop()", "camsHigh();", "FRAME_START += FRAME_PERIOD;",
                     "N_CAMS = readPins();", "updateStim();"):
        assert expected in ino, f"missing {expected!r}"
    print("8) generated sketch structure + camera protocol intact: PASS")


def test_ready_ack():
    """The host gates recording on this ack, so both config paths must emit it."""
    ino = sc.compile_ino([B("A", 5)], [], [53])
    assert 'Serial.print(F("RDY "));' in ino
    setup = ino.split("void setup()")[1].split("// ===== LOOP")[0]
    loop = ino.split("void loop()")[1]
    for name, body in (("setup", setup), ("loop", loop)):
        assert "announceReady();" in body, f"no ack from the {name} config path"
        # Must precede FRAME_START or the ~1 ms print skews the first frame.
        assert body.index("announceReady();") < body.index("FRAME_START = micros();"), \
            f"{name}: ack printed after the timing reference is taken"
    print("9) RDY ack emitted from both setup and loop config paths: PASS")


# ── per-frame trace (gui_app/stim_trace.py) ──────────────────────────────────
# Same paradigm semantics, evaluated over time instead of compiled to C. If
# these two drift apart the trace silently mislabels which frames were stimulated.

def test_trace_locate():
    from gui_app.stim_trace import locate
    steps = [{"duration_s": 3.0}, {"duration_s": 3.0}]

    # non-looping: runs once, then the chain is done
    assert locate(steps, None, 0.0) == (0, 0.0)
    assert locate(steps, None, 2.999)[0] == 0
    assert locate(steps, None, 3.0) == (1, 0.0)
    assert locate(steps, None, 6.0) == (None, None)

    # looping back to 0: repeats forever
    assert locate(steps, 0, 6.0) == (0, 0.0)
    assert locate(steps, 0, 7.5) == (0, 1.5)
    assert locate(steps, 0, 9.0) == (1, 0.0)
    assert locate(steps, 0, 6000.0) == (0, 0.0)

    # lead-in then loop: step 0 runs once, then 1<->2 cycle
    three = [{"duration_s": 10.0}, {"duration_s": 2.0}, {"duration_s": 3.0}]
    assert locate(three, 1, 5.0) == (0, 5.0)      # still in the lead-in
    assert locate(three, 1, 10.0) == (1, 0.0)
    assert locate(three, 1, 15.0) == (1, 0.0)     # one cycle later, not back to 0
    assert locate(three, 1, 13.0) == (2, 1.0)
    print("10) trace step location incl. loop + lead-in: PASS")


def test_trace_ttl_matches_firmware():
    from gui_app.stim_trace import ttl_level
    off = {"freq_hz": 0.0, "pulse_width_ms": 10.0}
    assert ttl_level(off, 0.0) == 0 and ttl_level(off, 1.234) == 0
    const = {"freq_hz": 10.0, "pulse_width_ms": 100.0}      # pw == period
    assert ttl_level(const, 0.0) == 1 and ttl_level(const, 4.9) == 1
    train = {"freq_hz": 10.0, "pulse_width_ms": 10.0}       # 10 ms pulse per 100 ms
    assert ttl_level(train, 0.0) == 1, "pulse must lead the period, as the sketch does"
    assert ttl_level(train, 0.005) == 1
    assert ttl_level(train, 0.015) == 0
    assert ttl_level(train, 0.100) == 1                     # next period
    print("11) trace TTL level agrees with the sketch's waveform: PASS")


def test_trace_rows_use_blockids_not_frame_index():
    """Dropped frames must shift time, or every later frame is mislabelled."""
    import numpy as np
    from gui_app.stim_trace import build_rows
    paradigm = {"chains": [{
        "loops": True, "loops_back_to_step": 0,
        "steps": [
            {"pin": 53, "freq_hz": 10.0, "pulse_width_ms": 10.0,
             "duration_s": 3.0, "mode": "10% duty"},
            {"pin": 53, "freq_hz": 0.0, "pulse_width_ms": 0.0,
             "duration_s": 3.0, "mode": "off (pin LOW)"},
        ]}]}

    # contiguous: blockid 1..600 -> t 0..5.99, flipping at exactly 3 s
    fields, rows = build_rows(paradigm, np.arange(1, 601), 100.0)
    assert rows[0]["t_s"] == 0.0 and rows[0]["any_active"] == 1
    assert rows[299]["any_active"] == 1 and rows[300]["any_active"] == 0
    assert "pin53_ttl" in fields

    # drop 100 triggers mid-recording: the frame *after* the gap must jump 1 s
    b = np.concatenate([np.arange(1, 101), np.arange(201, 301)])
    _f, rows = build_rows(paradigm, b, 100.0)
    assert rows[99]["t_s"] == 0.99
    assert rows[100]["t_s"] == 2.00, "gap ignored — trace would drift by 1 s"
    assert rows[100]["frame"] == 100 and rows[100]["blockid"] == 201
    print("12) trace maps frames via block IDs, so drops shift time: PASS")


def test_trace_unwraps_16bit_blockids():
    """Recordings past ~11 min wrap at 65535; without unwrapping, time restarts."""
    import numpy as np
    from gui_app.stim_trace import build_rows
    paradigm = {"chains": [{
        "loops": False, "loops_back_to_step": None,
        "steps": [{"pin": 53, "freq_hz": 20.0, "pulse_width_ms": 20.0,
                   "duration_s": 1e6, "mode": "40% duty"}]}]}
    b = np.concatenate([np.arange(65530, 65536), np.arange(1, 7)])
    _f, rows = build_rows(paradigm, b, 100.0)
    times = [r["t_s"] for r in rows]
    assert times == sorted(times), f"time went backwards across the wrap: {times}"
    assert abs(times[-1] - times[0] - 0.11) < 1e-6
    print("13) trace unwraps 16-bit block-ID rollover: PASS")


def main():
    test_start_resolution()
    test_chain_extraction_terminates()
    test_waveform_encoding()
    test_safe_pins()
    test_pin_conflicts()
    test_durations()
    test_describe()
    test_generated_sketch_is_wellformed()
    test_ready_ack()
    if _HAS_NUMPY:
        test_trace_locate()
        test_trace_ttl_matches_firmware()
        test_trace_rows_use_blockids_not_frame_index()
        test_trace_unwraps_16bit_blockids()
    else:
        print("10-13) per-frame trace tests SKIPPED — no numpy in this "
              "interpreter; rerun with `uv run python test_stim_compiler.py`")
    print("\nALL STIM COMPILER TESTS PASS")


if __name__ == "__main__":
    main()
