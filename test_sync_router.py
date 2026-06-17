"""Headless integration test of SyncEncodeRouter with REAL NVENC encoders and
concurrent submitting threads (no cameras). Verifies the router produces, per
camera, a stream.h264 that decodes to the common frame count, with identical
block IDs across cameras."""
import os, shutil, tempfile, threading, time, random
from pathlib import Path
import numpy as np, cv2
from gui_app.sync_encode import SyncEncodeRouter

W, H, N, NCAM = 1920, 1200, 3000, 6
random.seed(7)

tmp = Path(tempfile.mkdtemp(prefix="router_test_"))
raw_paths = []
for i in range(NCAM):
    d = tmp / f"cam{i+1}"; d.mkdir()
    raw_paths.append(d / "raw.bin")

# Each camera drops a different ~2% of triggers (independent), cam3 also freezes.
delivered = []
for i in range(NCAM):
    ids = [t for t in range(1, N + 1) if random.random() >= 0.02]
    if i == 2:
        ids = [t for t in ids if not (1200 <= t < 1320)]  # 120-frame freeze
    delivered.append(ids)
common_expected = set(delivered[0])
for d in delivered[1:]:
    common_expected &= set(d)
common_expected = sorted(common_expected)
print(f"simulated: per-cam {[len(d) for d in delivered]}, expected common {len(common_expected)}")

router = SyncEncodeRouter(raw_paths, W, H, 21)
assert router.available, "NVENC unavailable"
router.start()

def cam_thread(i):
    ring = [np.full((H * 3 // 2, W), 128, np.uint8) for _ in range(router.max_lag + 256)]
    ri = 0
    nxt = {t: k for k, t in enumerate(delivered[i])}
    # Pace at ~100 fps (hardware trigger period) so the encoders, which run
    # ~192 fps, stay ahead and queues never fill — the real capture regime.
    for k, t in enumerate(delivered[i]):
        buf = ring[ri]; ri = (ri + 1) % len(ring)
        buf[:H, :] = (t % 256)
        router.submit(i, t, t * 0.01, buf)
        time.sleep(0.01)

threads = [threading.Thread(target=cam_thread, args=(i,)) for i in range(NCAM)]
t0 = time.perf_counter()
for th in threads: th.start()
for th in threads: th.join()
results = router.stop()
dt = time.perf_counter() - t0
print(f"router ran in {dt:.1f}s")

ok = True
for i in range(NCAM):
    count, ts, bids = results[i]
    h264 = raw_paths[i].parent / "stream.h264"
    # remux-free decode count via cv2 on the elementary stream
    cap = cv2.VideoCapture(str(h264)); nframes = 0
    while cap.read()[0]: nframes += 1
    cap.release()
    cam_ok = (count == len(common_expected) and bids == common_expected
              and nframes == len(common_expected))
    ok = ok and cam_ok
    print(f"cam{i+1}: meta={count} h264_frames={nframes} ids==common={bids==common_expected} -> {'OK' if cam_ok else 'FAIL'}")

ids_identical = all(results[i][2] == results[0][2] for i in range(NCAM))
print(f"\nall cameras identical block IDs: {ids_identical}")
print("RESULT:", "ALL PASS" if (ok and ids_identical) else "FAIL")
shutil.rmtree(tmp, ignore_errors=True)
