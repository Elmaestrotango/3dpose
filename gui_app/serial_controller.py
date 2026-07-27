"""Teensy trigger serial controller."""
import time
import serial


class TeensyController:
    def __init__(self, port: str = "COM3", baudrate: int = 115200):
        self._port = port
        self._baudrate = baudrate
        self._ser = None

    def open(self, retries: int = 10) -> bool:
        for _ in range(retries):
            try:
                # NOTE: opening the port pulses DTR, which auto-resets the Mega.
                # That reset is LOAD-BEARING: the board re-enters setup() with a
                # clean serial RX buffer and blocks on the handshake. Suppressing
                # it (dtr=False before open) to avoid the laser flash during the
                # bootloader's high-Z window was tried on 2026-07-26 and silently
                # broke recording — the board ignored the config and produced zero
                # triggers (Total_Packet_Count 0 on all six cameras). The flash is
                # a hardware problem: fit a pulldown on the stim pin instead.
                self._ser = serial.Serial(port=self._port, baudrate=self._baudrate, timeout=0.1)
                time.sleep(1.0)
                return True
            except serial.SerialException:
                time.sleep(1)
        return False

    def start_triggers(self, pins: list[int], fps: int):
        if not self._ser:
            print("[teensy] start_triggers called but port not open", flush=True)
            return
        cmd = ",".join(str(x) for x in [len(pins)] + pins + [fps])
        self._ser.write(cmd.encode())
        print(f"[teensy] sent start: {cmd!r}", flush=True)

    def stop_triggers(self, pins: list[int]):
        if not self._ser:
            return
        cmd = ",".join(str(x) for x in [len(pins)] + pins + [-1])
        self._ser.write(cmd.encode())
        print(f"[teensy] sent stop: {cmd!r}", flush=True)

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
            self._ser = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open
