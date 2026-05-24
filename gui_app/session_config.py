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
    quality: int = 21
    pfs_path: str = ""
    output_dir: str = ""
    board_config: str = ""
    serial_port: str = "COM3"
    trigger_pins: list = field(default_factory=lambda: [2, 4, 6, 8, 10, 12])

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
            quality=data.get("quality", 21),
            pfs_path=_resolve(data.get("pfs_path", "")),
            output_dir=_resolve(data.get("output_dir", "")),
            board_config=_resolve(data.get("board_config", "")),
            serial_port=data.get("serial_port", "COM3"),
            trigger_pins=data.get("trigger_pins", [2, 4, 6, 8, 10, 12]),
        )

    @staticmethod
    def list_profiles() -> list[Path]:
        if not PROFILES_DIR.exists():
            return []
        return sorted(PROFILES_DIR.glob("*.yaml"))


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
    frame_rate: int = 100
    frame_width: int = 1920
    frame_height: int = 1200
    camera_names: list = field(default_factory=lambda: ["cam1", "cam2", "cam3", "cam4", "cam5", "cam6"])
    quality: int = 21

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
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
            quality=profile.quality,
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

    def video_filename(self, cam: str, acq_type: str) -> str:
        return f"{self.date}-{self.session_id}-{cam}-{acq_type}.mp4"

    def ensure_dirs(self, acq_type: str):
        d = self.video_dir(acq_type)
        for cam in self.camera_names:
            (d / cam).mkdir(parents=True, exist_ok=True)
        return d

    def save_metadata(self):
        now = datetime.now()
        meta = dict(
            date=self.date, session_id=self.session_id,
            mouse_1=self.mouse_1, mouse_2=self.mouse_2,
            assay=self.assay, cohort=self.cohort, cage=self.cage,
            experimenter=self.experimenter, notes=self.notes,
            num_cameras=len(self.camera_names), camera_names=self.camera_names,
            frame_rate=self.frame_rate, resolution=[self.frame_width, self.frame_height],
            time_of_day=now.strftime("%H:%M:%S"),
            timestamp_iso=now.isoformat(),
        )
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_dir / "session_metadata.json"
        with open(path, "w") as f:
            json.dump(meta, f, indent=2)
        return path
