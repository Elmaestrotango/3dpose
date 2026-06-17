"""Replicate the GUI's exact camera setup sequence and try to sustain a grab.

If this fails to get frames, the bug is in how the GUI configures the cameras.
If this works, the bug is elsewhere (threading, raw.bin writes, etc.).
"""
import time
import serial
import pypylon.pylon as pylon

PORT = "COM3"
BAUD = 115200
PINS = [2, 4, 6, 8, 10, 12]
FPS = 100
PFS = r"C:\Users\itang\Desktop\3dpose\configs\mono8_mono.pfs"

tlf = pylon.TlFactory.GetInstance()
devices = sorted(tlf.EnumerateDevices(), key=lambda d: d.GetSerialNumber())
print(f"Found {len(devices)} cameras")

cams = []
for dev in devices:
    cam = pylon.InstantCamera(tlf.CreateDevice(dev))
    cam.Open()
    # GUI: load pfs, set MaxNumBuffer
    pylon.FeaturePersistence.Load(PFS, cam.GetNodeMap(), False)
    cam.MaxNumBuffer.SetValue(500)
    cams.append((dev.GetSerialNumber(), cam))

# GUI: _set_freerun_mode then _start_grab_threads (we'll skip the freerun grab to avoid threading complexity)
print("setting freerun mode (matches GUI)...")
for sn, cam in cams:
    cam.TriggerMode.SetValue("Off")
    cam.AcquisitionFrameRateEnable.SetValue(True)
    cam.AcquisitionFrameRate.SetValue(30.0)

# Simulate having freerun grabbing for a moment then stopping (like _stop_grab_threads then _set_trigger_mode)
print("starting freerun grab for 1 sec then stopping (matches GUI idle->calibrate transition)...")
for sn, cam in cams:
    cam.StartGrabbing(pylon.GrabStrategy_OneByOne)
time.sleep(1.0)
for sn, cam in cams:
    cam.StopGrabbing()

# GUI: _set_trigger_mode
print("setting trigger mode (matches GUI _set_trigger_mode exactly)...")
for sn, cam in cams:
    try:
        cam.StopGrabbing()
    except Exception:
        pass
    cam.TriggerSelector.SetValue("FrameStart")
    cam.TriggerMode.SetValue("On")
    cam.TriggerSource.SetValue("Line1")
    cam.TriggerActivation.SetValue("RisingEdge")
    cam.AcquisitionFrameRateEnable.SetValue(True)
    cam.AcquisitionFrameRate.SetValue(165.0)

# GUI: _start_grab_threads -> each grab thread calls StartGrabbing(OneByOne)
print("StartGrabbing on all cameras...")
for sn, cam in cams:
    cam.StartGrabbing(pylon.GrabStrategy_OneByOne)

# GUI: _teensy.open() does time.sleep(1.0)
print("waiting 1s (matches teensy.open sleep)...")
ser = serial.Serial(PORT, BAUD, timeout=0.1)
time.sleep(1.0)

# GUI: _teensy.start_triggers
cmd = ",".join(str(x) for x in [len(PINS)] + PINS + [FPS])
ser.write(cmd.encode())
print(f"sent: {cmd!r}")

# Try to grab 100 frames per cam with 200ms per-frame timeout (matches GUI grab thread)
print("\ngrabbing up to 100 frames per cam (200ms timeout each)...")
results = {sn: {"got": 0, "timeouts": 0} for sn, _ in cams}
t_start = time.perf_counter()
for sn, cam in cams:
    for _ in range(100):
        try:
            r = cam.RetrieveResult(200, pylon.TimeoutHandling_Return)
            if r is not None and r.IsValid() and r.GrabSucceeded():
                results[sn]["got"] += 1
                r.Release()
            else:
                results[sn]["timeouts"] += 1
                if r is not None:
                    r.Release()
        except Exception as e:
            print(f"  {sn}: exception {e}")
            break
elapsed = time.perf_counter() - t_start

stop_cmd = ",".join(str(x) for x in [len(PINS)] + PINS + [-1])
ser.write(stop_cmd.encode())
ser.close()

print(f"\ndone in {elapsed:.1f}s:")
for i, (sn, _) in enumerate(cams):
    r = results[sn]
    print(f"  pin {PINS[i]:2d} -> cam{i+1} (sn {sn}): {r['got']} frames, {r['timeouts']} timeouts")

for _, cam in cams:
    try:
        cam.StopGrabbing()
    except Exception:
        pass
    cam.Close()
