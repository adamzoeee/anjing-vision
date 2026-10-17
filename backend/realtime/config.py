from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CaptureConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    camera_id: str = Field(default="camera_0", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    source_type: Literal["usb", "file"] = "usb"
    device_index: int = Field(default=0, ge=0, le=64)
    video_path: Path | None = None
    width: int = Field(default=640, ge=16, le=1920)
    height: int = Field(default=480, ge=16, le=1080)
    target_fps: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)
    queue_capacity: int = Field(default=2, ge=1, le=32)
    retry_interval_s: float = Field(default=.5, gt=0, le=30, allow_inf_nan=False)
    retry_max_interval_s: float = Field(default=4, gt=0, le=60, allow_inf_nan=False)
    reconnect_max_wait_s: float = Field(default=15, gt=0, le=300, allow_inf_nan=False)
    io_timeout_s: float = Field(default=3, ge=.1, le=15, allow_inf_nan=False)
    stop_timeout_s: float = Field(default=4, ge=1, le=15, allow_inf_nan=False)
    preview_enabled: bool = True
    lock_dir: Path = Path("data/.camera-locks")

    @model_validator(mode="after")
    def valid_source(self):
        if self.source_type == "file" and self.video_path is None:
            raise ValueError("file input requires video_path")
        if self.retry_max_interval_s < self.retry_interval_s:
            raise ValueError("retry_max_interval_s must be >= retry_interval_s")
        return self

    def public_source(self):
        return {"type": self.source_type, "device_index": self.device_index if self.source_type == "usb" else None,
                "filename": self.video_path.name if self.source_type == "file" else None}
