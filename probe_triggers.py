"""Probe whether Teensy pulses are reaching the cameras' Line1 inputs.

Opens all cameras, sends start command to Teensy, then polls LineStatus on Line1
for each camera over 1 second at ~1 kHz. Reports: did we see any HIGH samples?
"""
import time
import serial
import pypylon.pylon as pylon

PORT = "COM3"
BAUD = 115200
PINS = [2, 4, 6, 8, 10, 12]
FPS = 100
PFS = r"C:\Users\itang\Desktop\3dpose\configs\mono8_mono.pfs"


def main():
    tlf = pylon.TlFactory.GetInstance()
    devices = sorted(tlf.EnumerateDevices(), key=lambda d: d.GetSerialNumber())
    print(f"Found {len(devices)} cameras")

    cams = []
    for dev in devices:
        cam = pylon.InstantCamera(tlf.CreateDevice(dev))
        cam.Open()
        pylon.FeaturePersistence.Load(PFS, cam.GetNodeMap(), False)
        cam.LineSelector.SetValue("Line1")
        try:
            inv = cam.LineInverter.GetValue()
        except Exception:
            inv = "?"
        try:
            mode = cam.LineMode.GetValue()
        except Exception:
            mode = "?"
        cams.append((dev.GetSerialNumber(), cam, inv, mode))

    print("\nper-camera Line1 config:")
    for sn, _, inv, mode in cams:
        print(f"  {sn}: LineMode={mode} LineInverter={inv}")

    def poll(label, duration=1.0):
        samples = {sn: {"high": 0, "low": 0} for sn, *_ in cams}
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < duration:
            for sn, cam, *_ in cams:
                try:
                    if cam.LineStatus.GetValue():
                        samples[sn]["high"] += 1
                    else:
                        samples[sn]["low"] += 1
                except Exception as e:
                    print(f"  {sn}: probe error: {e}")
            time.sleep(0.001)
        print(f"\n[{label}]")
        for i, (sn, *_) in enumerate(cams):
            s = samples[sn]
            total = s["high"] + s["low"]
            pct = 100.0 * s["high"] / total if total else 0
            verdict = "PULSING" if 5 < pct < 95 else ("STUCK_HIGH" if pct > 95 else "NO_SIGNAL")
            print(f"  pin {PINS[i]:2d} -> cam{i+1} (sn {sn}): {s['high']:4d}H/{s['low']:4d}L ({pct:5.1f}%) -> {verdict}")

    ser = serial.Serial(PORT, BAUD, timeout=0.1)
    time.sleep(1.0)

    poll("BEFORE start (Teensy idle)", duration=0.5)

    cmd = ",".join(str(x) for x in [len(PINS)] + PINS + [FPS])
    ser.write(cmd.encode())
    print(f"\nsent: {cmd!r}")
    time.sleep(0.1)

    poll("DURING pulsing (100 Hz)", duration=1.0)

    stop_cmd = ",".join(str(x) for x in [len(PINS)] + PINS + [-1])
    ser.write(stop_cmd.encode())
    print(f"\nsent: {stop_cmd!r}")
    time.sleep(0.1)

    poll("AFTER stop (Teensy idle)", duration=0.5)

    # Hard test: put cameras in trigger mode, start pulses, try to grab a frame
    print("\n[frame-grab test in trigger mode]")
    for sn, cam, *_ in cams:
        try:
            cam.TriggerSelector.SetValue("FrameStart")
            cam.TriggerMode.SetValue("On")
            cam.TriggerSource.SetValue("Line1")
            cam.TriggerActivation.SetValue("RisingEdge")
        except Exception as e:
            print(f"  {sn}: failed to set trigger mode: {e}")

    for _, cam, *_ in cams:
        cam.StartGrabbing(pylon.GrabStrategy_OneByOne)

    ser.write(cmd.encode())
    print(f"sent: {cmd!r}; trying to grab 1 frame per cam with 2s timeout...")
    for i, (sn, cam, *_) in enumerate(cams):
        try:
            result = cam.RetrieveResult(2000, pylon.TimeoutHandling_Return)
            if result is None or not result.IsValid() or not result.GrabSucceeded():
                print(f"  pin {PINS[i]:2d} -> cam{i+1} (sn {sn}): NO FRAME (timeout 2s)")
            else:
                arr = result.Array
                print(f"  pin {PINS[i]:2d} -> cam{i+1} (sn {sn}): GOT FRAME shape={arr.shape}")
                result.Release()
        except Exception as e:
            print(f"  pin {PINS[i]:2d} -> cam{i+1} (sn {sn}): exception {e}")

    ser.write(stop_cmd.encode())
    for _, cam, *_ in cams:
        try:
            cam.StopGrabbing()
        except Exception:
            pass

    ser.close()
    for _, cam, *_ in cams:
        cam.Close()


if __name__ == "__main__":
    main()
