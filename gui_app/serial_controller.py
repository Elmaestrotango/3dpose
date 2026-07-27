"""Teensy trigger serial controller."""
import time
import serial


class TeensyController:
    """Serial link to the Arduino camera-trigger / stim board.

    Meant to be held open for the life of the GUI. Opening the port pulses DTR,
    which auto-resets the board, and for the ~1-2 s of reset + bootloader every
    pin is high-Z — long enough for a connected laser driver to read its floating
    modulation input as ON. Keeping the port open across recordings moves that
    reset out of the experiment; it then happens only at first use and on upload.

    Because the board is no longer guaranteed to be freshly reset when a start
    command arrives, every start is confirmed by an `RDY <n_cams> <fps>` ack from
    the sketch. Without that check a mis-parsed config is indistinguishable from
    a good one until the recording comes back empty.
    """

    # The sketch's readFPS() ends in a parseFloat that can burn its 1 s timeout,
    # followed by delay(500), so the ack legitimately takes ~1.5 s to appear.
    ACK_TIMEOUT = 4.0

    def __init__(self, port: str = "COM3", baudrate: int = 115200):
        self._port = port
        self._baudrate = baudrate
        self._ser = None
        # None until we learn whether this board's firmware acks at all.
        self._acks = False

    def open(self, retries: int = 10) -> bool:
        for _ in range(retries):
            try:
                # NOTE: this pulses DTR and resets the board. That reset is
                # LOAD-BEARING — it returns the sketch to setup() with a cleared
                # serial RX buffer. Suppressing it (dtr=False before open) was
                # tried on 2026-07-26 and silently broke recording: zero triggers,
                # Total_Packet_Count 0 on all six cameras. Do not do that again.
                # The laser flash it causes is a hardware problem; the fix is to
                # keep this connection open rather than to defeat the reset.
                self._ser = serial.Serial(port=self._port, baudrate=self._baudrate, timeout=0.1)
                time.sleep(1.0)
                return True
            except serial.SerialException:
                time.sleep(1)
        return False

    def start_triggers(self, pins: list[int], fps: int) -> bool:
        """Configure the board for acquisition and confirm it understood.

        Tries the existing connection first (no reset, no laser flash). If the
        board does not confirm, reopens the port to force a reset — the proven
        path — and retries. Returns False only when the board is genuinely
        unreachable, so the caller can abort instead of recording nothing.
        """
        if not self._ser:
            print("[teensy] start_triggers called but port not open", flush=True)
            return False

        if self._send(pins, fps):
            return True

        print("[teensy] no ack — reopening port to force a board reset", flush=True)
        self.close()
        if not self.open():
            print("[teensy] could not reopen port", flush=True)
            return False
        if self._send(pins, fps):
            return True

        if self._acks:
            # This board has acked before, so silence now is a real fault.
            print("[teensy] board acked previously but not now — aborting", flush=True)
            return False
        # Never seen an ack on this board: almost certainly firmware predating
        # the RDY handshake (stock trigger.ino, or a sketch built before
        # 2026-07-27). It has just been reset, which is exactly what the old
        # code did, so let the recording proceed.
        print("[teensy] no ack support detected — assuming pre-RDY firmware", flush=True)
        return True

    def _send(self, pins: list[int], fps: int) -> bool:
        # Trailing newline terminates the sketch's final parseFloat immediately
        # instead of letting it burn its 1 s timeout. Harmless to older firmware.
        cmd = ",".join(str(x) for x in [len(pins)] + list(pins) + [fps]) + "\n"
        try:
            self._ser.reset_input_buffer()
            self._ser.write(cmd.encode())
        except (serial.SerialException, OSError) as e:
            print(f"[teensy] write failed: {e}", flush=True)
            return False
        print(f"[teensy] sent: {cmd!r}", flush=True)
        return self._await_ack(len(pins), fps)

    def _await_ack(self, n_pins: int, fps: int) -> bool:
        want = f"RDY {n_pins} {max(int(fps), 0)}"
        deadline = time.monotonic() + self.ACK_TIMEOUT
        buf = ""
        while time.monotonic() < deadline:
            try:
                chunk = self._ser.read(64)
            except (serial.SerialException, OSError) as e:
                print(f"[teensy] read failed: {e}", flush=True)
                return False
            if chunk:
                buf += chunk.decode("ascii", "ignore")
                if want in buf:
                    self._acks = True
                    print(f"[teensy] ack {want!r}", flush=True)
                    return True
        if buf.strip():
            print(f"[teensy] wanted {want!r}, got {buf.strip()!r}", flush=True)
        return False

    def stop_triggers(self, pins: list[int]):
        if not self._ser:
            return
        cmd = ",".join(str(x) for x in [len(pins)] + list(pins) + [-1]) + "\n"
        try:
            self._ser.write(cmd.encode())
        except (serial.SerialException, OSError) as e:
            print(f"[teensy] stop write failed: {e}", flush=True)
            return
        print(f"[teensy] sent stop: {cmd!r}", flush=True)

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
        self._ser = None

    @property
    def port(self) -> str:
        return self._port

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open
