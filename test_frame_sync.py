"""Prove FrameSyncCoordinator == post-hoc block-ID intersection.

For each random scenario: build per-camera delivered-trigger streams (independent
drops, optional stragglers/freezes/wrap), feed them to the coordinator in a
randomized but per-camera-ordered interleaving, and check:
  - group integrity: every released trigger is released by ALL cameras, once,
    and in increasing order per camera (so encoders get a clean stream);
  - no forcing => released set EXACTLY equals the intersection of delivered sets;
  - with forcing => released is a subset of the intersection (only triggers the
    intersection would keep can ever be released), and loss is bounded.
"""
import random
from collections import defaultdict
from gui_app.frame_sync import FrameSyncCoordinator


def feed(n, delivered, max_lag, raw=None, max_skew=None):
    """delivered[c] = sorted list of triggers camera c captured.
    raw[c] = matching raw (possibly wrapped) block IDs to submit (defaults to
    delivered). Interleave submissions preserving per-camera order, with optional
    bounded skew between cameras."""
    raw = raw or delivered
    co = FrameSyncCoordinator(n, max_lag=max_lag)
    ptr = [0] * n
    out = []  # (cam, trigger)
    order_pos = [0] * n  # how many this cam has submitted
    while any(ptr[c] < len(delivered[c]) for c in range(n)):
        cand = [c for c in range(n) if ptr[c] < len(delivered[c])]
        if max_skew is not None:
            lead = min(order_pos[c] for c in cand)
            cand = [c for c in cand if order_pos[c] - lead < max_skew]
        c = random.choice(cand)
        for cam, t, _f in co.submit(c, raw[c][ptr[c]], None):
            out.append((cam, t))
        ptr[c] += 1
        order_pos[c] += 1
    for cam, t, _f in co.flush():
        out.append((cam, t))
    return co, out


def check_group_integrity(n, out):
    by_t = defaultdict(set)
    per_cam_seq = defaultdict(list)
    for cam, t in out:
        assert cam not in by_t[t], f"trigger {t} released twice for cam {cam}"
        by_t[t].add(cam)
        per_cam_seq[cam].append(t)
    for t, cams in by_t.items():
        assert len(cams) == n, f"trigger {t} released by {len(cams)}/{n} cams (partial!)"
    for cam, seq in per_cam_seq.items():
        assert seq == sorted(seq), f"cam {cam} released out of order"
    return set(by_t.keys())


def intersection(delivered):
    s = set(delivered[0])
    for d in delivered[1:]:
        s &= set(d)
    return s


def rand_delivered(n, total, drop_rate):
    return [sorted(t for t in range(1, total + 1) if random.random() >= drop_rate)
            for _ in range(n)]


def main():
    random.seed(1234)
    N = 6

    # 1) exact equivalence, no forcing (huge max_lag), varied drop rates
    for trial in range(400):
        total = random.randint(50, 1500)
        dr = random.choice([0.0, 0.001, 0.01, 0.05, 0.2])
        delivered = rand_delivered(N, total, dr)
        co, out = feed(N, delivered, max_lag=10 * total + 10)
        rel = check_group_integrity(N, out)
        assert rel == intersection(delivered), (
            f"trial {trial}: released != intersection "
            f"({len(rel)} vs {len(intersection(delivered))})")
        assert co.forced == 0
    print("1) exact equivalence (no forcing), 400 trials: PASS")

    # 2) bounded skew but within max_lag => still exact
    for trial in range(200):
        total = random.randint(100, 800)
        delivered = rand_delivered(N, total, 0.02)
        co, out = feed(N, delivered, max_lag=60, max_skew=40)
        rel = check_group_integrity(N, out)
        assert rel == intersection(delivered), f"skew trial {trial} mismatch"
        assert co.forced == 0
    print("2) bounded skew within max_lag, 200 trials: PASS")

    # 3) stragglers force-drop: released subset of intersection, integrity holds
    for trial in range(200):
        total = random.randint(200, 1000)
        delivered = rand_delivered(N, total, 0.02)
        co, out = feed(N, delivered, max_lag=30, max_skew=300)  # big skew -> forcing
        rel = check_group_integrity(N, out)
        inter = intersection(delivered)
        assert rel <= inter, f"forcing trial {trial}: released NOT subset of intersection"
    print("3) stragglers w/ forcing (subset + integrity), 200 trials: PASS")

    # 4) hard freeze: one camera stops for a long stretch then resumes
    for trial in range(100):
        total = 1000
        delivered = rand_delivered(N, total, 0.01)
        victim = random.randrange(N)
        # remove a 150-trigger block from the victim (freeze)
        start = random.randint(200, 700)
        delivered[victim] = [t for t in delivered[victim]
                             if not (start <= t < start + 150)]
        co, out = feed(N, delivered, max_lag=40, max_skew=500)
        rel = check_group_integrity(N, out)
        assert rel <= intersection(delivered)
        # cameras keep flowing: triggers well before and after the freeze survive
        assert any(t < start for t in rel) and any(t > start + 150 for t in rel)
    print("4) hard freeze recovery (others keep flowing), 100 trials: PASS")

    # 5) 16-bit wrap: raw IDs wrap 65535->1, unwrap must keep alignment
    total = 70000  # crosses 65535
    delivered = rand_delivered(N, total, 0.01)
    raw = [[((t - 1) % 65535) + 1 for t in d] for d in delivered]  # wrapped raw
    co, out = feed(N, delivered, max_lag=10 * total, raw=raw)
    rel = check_group_integrity(N, out)
    assert rel == intersection(delivered), "wrap: released != intersection"
    print("5) 16-bit wrap unwrap (70k triggers), 1 trial: PASS")

    # 6) all-perfect (no drops) => everything released, nothing dropped
    delivered = [list(range(1, 2001)) for _ in range(N)]
    co, out = feed(N, delivered, max_lag=240)
    rel = check_group_integrity(N, out)
    assert rel == set(range(1, 2001)) and co.dropped == 0
    print("6) zero-loss passthrough, 1 trial: PASS")

    print("\nALL FRAMESYNC EQUIVALENCE TESTS PASS")


if __name__ == "__main__":
    main()
