"""Session configuration and path management."""
import json
import yaml
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"


@dataclass
class RigProfile:
    name: str = "default"
    frame_width: int = 1920
    frame_height: int = 1200
    frame_rate: int = 100
    calibration_frame_rate: int = 30
    quality: int = 21
    encode_parallel: int = 3
    realtime_encode: bool = True
    # Real-time frame kick-out: gate frames through the cross-camera coordinator
    # during capture so only frames every camera caught get encoded — videos come
    # out already trigger-aligned, no post-hoc re-encode. Experimental; default off.
    realtime_kick: bool = False
    # Kick-out coordinator buffer depth (frames). A camera may lag the others by
    # this many frames (e.g. while recovering lost packets via resends) before
    # its missing triggers are force-dropped to keep the pipeline flowing. Higher
    # = fewer late frames sacrificed, but more RAM held (ring scales with it).
    kick_max_lag: int = 240
    # GigE receive driver: "socket" (user-space, robust packet resends — the
    # proven path), "filter" (in-kernel pylon GigE Vision driver, less CPU but
    # measured 2026-06-12 silently dropping ~23% of frames with default resend
    # settings), or "auto" (leave pylon's default).
    gige_driver: str = "socket"
    pfs_path: str = ""
    output_dir: str = ""
    board_config: str = ""
    serial_port: str = "COM3"
    trigger_pins: list = field(default_factory=lambda: [2, 4, 6, 8, 10, 12])
    # Expected camera count. 0 = don't check. Nonzero makes open_all refuse a
    # partial set: names are positional by serial order, so a camera that fails
    # to ENUMERATE renames every camera after it and silently attaches the
    # calibration extrinsics to the wrong physical cameras.
    n_cameras: int = 0
    # Optostim output pins held LOW from the instant the sketch boots — before
    # the serial handshake, which blocks until the GUI connects. Without this a
    # powered laser driver reads the floating pin as ON at power-up. Pins used by
    # the stimulation workflow are added automatically; list here anything that
    # must be safe even when no paradigm is loaded.
    stim_safe_pins: list = field(default_factory=lambda: [53])
    # AcquisitionFrameRate applied in trigger mode, or 0 to disable the limiter.
    # While externally triggered the camera's internal rate generator serves no
    # purpose, but it still enforces a minimum interval of
    # `exposure + 1/AcquisitionFrameRate` — the thing that capped exposure at
    # ~3.94 ms at 100 fps, and (at the old value of 100) caused the 50 fps bug.
    # 0 leaves only the sensor readout as the constraint.
    trigger_rate_limit: float = 165.0

    @classmethod
    def load(cls, path: Path) -> "RigProfile":
        with open(path) as f:
            data = yaml.safe_load(f)
        def _resolve(raw: str) -> str:
            if not raw:
                return ""
            p = Path(raw)
            if not p.is_absolute():
                p = REPO_ROOT / p
            return str(p)

        return cls(
            name=data.get("name", path.stem),
            frame_width=data.get("frame_width", 1920),
            frame_height=data.get("frame_height", 1200),
            frame_rate=data.get("frame_rate", 100),
            calibration_frame_rate=data.get("calibration_frame_rate", 30),
            quality=data.get("quality", 21),
            encode_parallel=data.get("encode_parallel", 3),
            realtime_encode=data.get("realtime_encode", True),
            realtime_kick=data.get("realtime_kick", False),
            kick_max_lag=data.get("kick_max_lag", 240),
            gige_driver=data.get("gige_driver", "socket"),
            pfs_path=_resolve(data.get("pfs_path", "")),
            output_dir=_resolve(data.get("output_dir", "")),
            board_config=_resolve(data.get("board_config", "")),
            serial_port=data.get("serial_port", "COM3"),
            trigger_pins=data.get("trigger_pins", [2, 4, 6, 8, 10, 12]),
            n_cameras=data.get("n_cameras", 0),
            stim_safe_pins=data.get("stim_safe_pins", [53]),
            trigger_rate_limit=float(data.get("trigger_rate_limit", 165.0)),
        )

    @staticmethod
    def list_profiles() -> list[Path]:
        if not PROFILES_DIR.exists():
            return []
        return sorted(PROFILES_DIR.glob("*.yaml"))


def _environment_metadata() -> dict:
    """Host/GPU facts worth freezing into every recording.

    Cheap, entirely best-effort, and never allowed to break saving metadata:
    a session that failed to record its environment is still a session, but a
    session whose environment is unknown is much harder to explain later.
    """
    import platform
    import subprocess
    import sys

    env = {
        "host": platform.node(),
        "os": platform.platform(),
        "python": sys.version.split()[0],
    }
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            name, driver, mem = [x.strip() for x in
                                 r.stdout.strip().splitlines()[0].split(",")]
            env.update(gpu=name, gpu_driver=driver, gpu_memory=mem)
    except Exception:
        pass
    try:
        from gui_app import hardware_check
        # Cached from the acquisition preflight; do not probe again here.
        n = getattr(hardware_check, "_nvenc_sessions", None)
        if n is not None:
            env["nvenc_sessions_available"] = n
    except Exception:
        pass
    return env


@dataclass
class SessionConfig:
    date: str = ""
    mouse_1: str = ""
    mouse_2: str = ""
    assay: str = "open_field"
    experimenter: str = "IT"
    cohort: str = ""
    cage: str = ""
    notes: str = ""

    base_data_dir: Path = Path("")
    pfs_path: Path = Path("")
    serial_port: str = "COM3"
    trigger_pins: list = field(default_factory=lambda: [2, 4, 6, 8, 10, 12])
    # Expected camera count. 0 = don't check. Nonzero makes open_all refuse a
    # partial set: names are positional by serial order, so a camera that fails
    # to ENUMERATE renames every camera after it and silently attaches the
    # calibration extrinsics to the wrong physical cameras.
    n_cameras: int = 0
    frame_rate: int = 100
    calibration_frame_rate: int = 30
    frame_width: int = 1920
    frame_height: int = 1200
    camera_names: list = field(default_factory=lambda: ["cam1", "cam2", "cam3", "cam4", "cam5", "cam6"])
    quality: int = 21
    encode_parallel: int = 3
    realtime_encode: bool = True
    realtime_kick: bool = False
    kick_max_lag: int = 240

    def __post_init__(self):
        if not self.date:
            self.date = datetime.now().strftime("%Y%m%d")
        if not self.mouse_1:
            self.mouse_1 = "m1"
        if not self.mouse_2:
            self.mouse_2 = "m2"

    @classmethod
    def from_profile(cls, profile: RigProfile, **overrides) -> "SessionConfig":
        defaults = dict(
            base_data_dir=Path(profile.output_dir) if profile.output_dir else Path(""),
            pfs_path=Path(profile.pfs_path) if profile.pfs_path else Path(""),
            serial_port=profile.serial_port,
            trigger_pins=profile.trigger_pins,
            frame_rate=profile.frame_rate,
            calibration_frame_rate=profile.calibration_frame_rate,
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
            quality=profile.quality,
            encode_parallel=profile.encode_parallel,
            realtime_encode=profile.realtime_encode,
            realtime_kick=profile.realtime_kick,
            kick_max_lag=profile.kick_max_lag,
        )
        defaults.update(overrides)
        return cls(**defaults)

    @property
    def session_id(self) -> str:
        return f"{self.mouse_1}_{self.mouse_2}"

    @property
    def session_dir(self) -> Path:
        return self.base_data_dir / self.date / self.session_id

    def video_dir(self, acq_type: str) -> Path:
        return self.session_dir / acq_type

    def rate_for(self, acq_type: str) -> int:
        """Trigger/encode frame rate for an acquisition type."""
        return self.calibration_frame_rate if acq_type == "calibration" else self.frame_rate

    def save_metadata(self):
        now = datetime.now()
        # NOTE: _environment_metadata() is folded in below. It records the GPU
        # driver and the NVENC session count because both are *silent* failure
        # sources that move underneath you: NVIDIA has changed the concurrent
        # session cap across driver generations (2 -> 3 -> 5 -> 8 -> 12), and a
        # driver update that lowers it below the camera count pushes cameras
        # onto the raw fallback. Without this in the metadata, a session that
        # breaks after a driver update is undiagnosable after the fact.
        meta = dict(
            date=self.date, session_id=self.session_id,
            mouse_1=self.mouse_1, mouse_2=self.mouse_2,
            assay=self.assay, cohort=self.cohort, cage=self.cage,
            experimenter=self.experimenter, notes=self.notes,
            num_cameras=len(self.camera_names), camera_names=self.camera_names,
            frame_rate=self.frame_rate, calibration_frame_rate=self.calibration_frame_rate,
            resolution=[self.frame_width, self.frame_height],
            time_of_day=now.strftime("%H:%M:%S"),
            timestamp_iso=now.isoformat(),
            **_environment_metadata(),
        )
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_dir / "session_metadata.json"
        with open(path, "w") as f:
            json.dump(meta, f, indent=2)
        return path
