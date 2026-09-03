"""PyNvVideoCodec loader shim + encoder factory for real-time GPU H.264 encoding.

The package only adds the CUDA runtime to the DLL search path when CUDA_PATH is
set; on this rig the CUDA toolkit isn't installed and we rely on the pip-provided
`nvidia-cuda-runtime-cu12` wheel (cudart64_12.dll — the NVENC-13 build links the
CUDA *12* runtime). So we add that directory ourselves before importing.

Everything is guarded: if PyNvVideoCodec or the runtime is missing, available()
returns False and the caller falls back to raw-to-disk. The GUI never breaks just
because the GPU encode path is unavailable.
"""
import os
import sysconfig
import threading

_nvc = None
_load_error = None
_loaded = False
_lock = threading.Lock()


def _load():
    global _nvc, _load_error, _loaded
    if _loaded:
        return
    # Serialize: the 6 grab threads call this near-simultaneously. Without the
    # lock the first thread sets _loaded and starts the (slow) import, the others
    # see _loaded=True, return early, and find _nvc still None -> they wrongly
    # fall back to raw ("unavailable: None"). The lock makes every caller wait
    # until the import has actually finished.
    with _lock:
        if _loaded:
            return
        try:
            cudart = os.path.join(sysconfig.get_paths()["purelib"],
                                  "nvidia", "cuda_runtime", "bin")
            if os.path.isdir(cudart) and hasattr(os, "add_dll_directory"):
                os.add_dll_directory(cudart)
            import PyNvVideoCodec as nvc
            _nvc = nvc
        except Exception as e:  # missing wheel, missing cudart, no GPU, etc.
            _load_error = e
            print(f"[nvenc] PyNvVideoCodec unavailable, will fall back to raw: {e}", flush=True)
        if _nvc is not None:
            _warm()
        _loaded = True


def _warm():
    """Force PyNvVideoCodec's first-Encode lazy import, single-threaded.

    Encode() pulls in more machinery the first time it is called. When six
    encoder threads hit that simultaneously they pile up on the import
    machinery and the WHOLE PROCESS wedges — proven 2026-08-11 with a
    faulthandler dump showing one thread parked in importlib.find_spec() under
    Encode() while every grab thread sat idle and the recording produced
    nothing at all. Doing one throwaway encode here, inside the load lock,
    means the encoder threads only ever meet the already-imported fast path.

    Tiny frame, and failures are swallowed: this is a hazard removal, not a
    requirement. create_h264_encoder() still surfaces real errors.
    """
    import numpy as np
    try:
        # 256x256, not smaller: NVENC rejects 128x128 with "CreateEncoder
        # Error code : 8", which silently skipped this warmup when first added.
        enc = _nvc.CreateEncoder(256, 256, "NV12", True, codec="h264")
        enc.Encode(np.full((384, 256), 128, np.uint8))
        try:
            enc.EndEncode()
        except Exception:
            pass
        print("[nvenc] warmed (first-Encode import done single-threaded)", flush=True)
    except Exception as e:
        print(f"[nvenc] warmup skipped: {e}", flush=True)


def available() -> bool:
    _load()
    return _nvc is not None


def load_error() -> str:
    return str(_load_error) if _load_error else ""


def create_h264_encoder(width: int, height: int, qp: int,
                        fps: int = 100,
                        preset: str = "P3", tuning: str = "low_latency"):
    """Create an NVENC H.264 encoder for NV12 input (CPU input buffer).

    Mirrors the validated PoC config. Mono frames are fed as NV12 where the Y
    plane is the gray data and the UV plane is a constant 128.
    """
    _load()
    if _nvc is None:
        raise RuntimeError(f"PyNvVideoCodec unavailable: {_load_error}")
    # Tolerate older/newer builds that may not accept every kwarg — but say so:
    # a reduced kwarg set silently changes rate control (constqp -> driver
    # default), i.e. different output quality than the profile asked for.
    gop = str(fps)
    last_err = None
    for n, kw in enumerate((
            dict(codec="h264", preset=preset, tuning_info=tuning, rc="constqp", qp=str(qp),
                 gopLength=gop, idrPeriod=gop),
            dict(codec="h264", preset=preset, tuning_info=tuning, rc="constqp", qp=str(qp)),
            dict(codec="h264", preset=preset, tuning_info=tuning),
            dict(codec="h264"))):
        try:
            enc = _nvc.CreateEncoder(width, height, "NV12", True, **kw)
            if n > 0:
                print(f"[nvenc] WARNING: full encoder config rejected ({last_err}); "
                      f"created with reduced settings {kw} — quality may differ "
                      f"from profile qp={qp}", flush=True)
            return enc
        except Exception as e:
            last_err = e
            continue
    # Last attempt: surface the real error.
    return _nvc.CreateEncoder(width, height, "NV12", True, codec="h264")
