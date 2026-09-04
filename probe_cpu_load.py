"""Background CPU load generator, for testing acquisition margin.

Separate processes (not threads) so this contends for CPU the way other
applications do, rather than for the GIL.
    uv run probe_cpu_load.py --workers 4 --seconds 200
"""
import argparse, multiprocessing as mp, time


def burn(deadline):
    x = 0.0
    while time.time() < deadline:
        for i in range(200000):
            x += i * 1.000001
    return x


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seconds", type=float, default=200)
    a = ap.parse_args()
    end = time.time() + a.seconds
    ps = [mp.Process(target=burn, args=(end,)) for _ in range(a.workers)]
    [p.start() for p in ps]
    print(f"[load] {a.workers} workers for {a.seconds:g}s", flush=True)
    [p.join() for p in ps]
    print("[load] done", flush=True)
