"""Lock down the stimulus workflow -> Arduino sketch translation.

The graph semantics here are easy to break and hard to notice on the rig: a
mis-resolved start silently produces no chains, a cycle used to hang the walker
forever, and a pulse width at or above the period once compiled to a pin that
never fired. Each of those cost a debugging session, so they are pinned here.

Pure-Python only (no Qt, no serial, no arduino-cli), so it runs anywhere:
    python test_stim_compiler.py
"""
from gui_app import stim_compiler as sc


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
    print("\nALL STIM COMPILER TESTS PASS")


if __name__ == "__main__":
    main()
