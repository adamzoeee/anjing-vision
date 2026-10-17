from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True, slots=True)
class VideoFrame:
    camera_id: str
    session_id: str
    frame_id: int
    timestamp: float  # Unix arrival time; not the original camera exposure time.
    monotonic_timestamp: float  # Local monotonic arrival clock, seconds.
    image: np.ndarray  # Read-only uint8 BGR image; image coordinates only.
    width: int
    height: int
    source_type: str
    source_time_seconds: float | None = None  # Optional decoder PTS, not a wall clock or pose.

    @classmethod
    def create(cls, *, camera_id, session_id, frame_id, timestamp, monotonic_timestamp,
               image, source_type, source_time_seconds=None):
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 1:
            raise ValueError("Expected nonempty uint8 BGR frame")
        if frame_id < 1 or not all(math.isfinite(t) for t in (timestamp, monotonic_timestamp)):
            raise ValueError("Invalid frame sequence or arrival clock")
        copy = np.ascontiguousarray(image).copy()
        copy.flags.writeable = False
        return cls(camera_id, session_id, frame_id, timestamp, monotonic_timestamp, copy,
                   copy.shape[1], copy.shape[0], source_type, source_time_seconds)
