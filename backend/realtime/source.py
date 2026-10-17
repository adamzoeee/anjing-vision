"""Unified input contract; isolate native OpenCV calls so shutdown is bounded."""
from __future__ import annotations

from dataclasses import dataclass
import math
import multiprocessing as mp
from pathlib import Path
import threading
import time
from typing import Protocol

import numpy as np

from .config import CaptureConfig


class SourceError(RuntimeError):
    """Stable diagnostic code; never exposes a local video path or pixel data."""


class SourceCancelled(SourceError):
    pass


@dataclass(frozen=True)
class SourceInfo:
    nominal_fps: float | None = None


@dataclass(frozen=True)
class SourceRead:
    image: np.ndarray | None = None
    eof: bool = False
    source_time_seconds: float | None = None


class VideoSource(Protocol):
    def open(self, timeout_s: float | None = None) -> SourceInfo: ...
    def read(self) -> SourceRead: ...
    def is_open(self) -> bool: ...
    def interrupt(self) -> None: ...
    def close(self) -> None: ...


class DeviceLease:
    """OS-released exclusive lease, shared by API workers using the same DATA_DIR."""
    def __init__(self, path: Path):
        self.path, self.file = path, None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = self.path.open("a+b")
        try:
            if f.tell() == 0:
                f.write(b"0"); f.flush()
            f.seek(0)
            import os
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            f.close()
            raise SourceError("device_in_use") from exc
        self.file = f

    def close(self):
        if self.file is not None:
            self.file.close(); self.file = None


def _capture_worker(connection, config):
    # Only this process owns the native handle. No audio, network source, recording or models.
    import cv2
    cap = None
    try:
        if hasattr(cv2, "setLogLevel"): cv2.setLogLevel(0)
        kind = config["source_type"]
        cap = cv2.VideoCapture(config["device_index"] if kind == "usb" else config["video_path"])
        if not cap.isOpened():
            connection.send(("error", "source_open_failed")); return
        if kind == "usb":
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, config["width"])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config["height"])
            cap.set(cv2.CAP_PROP_FPS, config["target_fps"])
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        connection.send(("opened", fps if math.isfinite(fps) and fps > 0 else None))
        count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        seen = 0
        while connection.recv() == "read":
            ok, image = cap.read()
            if not ok or image is None:
                eof = kind == "file" and seen > 0 and (not math.isfinite(count) or count <= 0 or seen >= count - 1)
                connection.send(("eof", None) if eof else ("error", "video_decode_failed" if kind == "file" else "device_disconnected"))
                return
            seen += 1
            if image.ndim != 3 or image.shape[2] != 3:
                connection.send(("error", "invalid_frame")); return
            # Bound IPC/frame memory even when a device ignores requested resolution.
            scale = min(1., config["width"] / image.shape[1], config["height"] / image.shape[0])
            if scale < 1:
                image = cv2.resize(image, (max(1, round(image.shape[1]*scale)), max(1, round(image.shape[0]*scale))))
            pts = float(cap.get(cv2.CAP_PROP_POS_MSEC)) / 1000 if kind == "file" else None
            if pts is not None and (not math.isfinite(pts) or pts < 0): pts = None
            connection.send(("frame", (image, pts)))
    except (EOFError, BrokenPipeError, OSError):
        pass
    except Exception:
        try: connection.send(("error", "capture_backend_error"))
        except (EOFError, BrokenPipeError, OSError): pass
    finally:
        if cap is not None: cap.release()
        connection.close()


class OpenCVSource:
    def __init__(self, config: CaptureConfig):
        self.config = config
        self._process = self._connection = self._lease = None
        self._cancel = threading.Event()
        self._opened = False

    def _receive(self, timeout_s):
        deadline = time.monotonic() + timeout_s
        while not self._cancel.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise SourceError("source_timeout")
            try:
                if self._connection.poll(min(.05, remaining)):
                    kind, value = self._connection.recv()
                    if kind == "error": raise SourceError(value)
                    return kind, value
            except (EOFError, OSError) as exc:
                raise SourceError("capture_process_exited") from exc
        raise SourceCancelled("capture_cancelled")

    def open(self, timeout_s=None):
        if self._process is not None: raise SourceError("source_already_open")
        if self.config.source_type == "file" and not self.config.video_path.is_file():
            raise SourceError("video_not_found")
        if self.config.source_type == "usb":
            self._lease = DeviceLease(self.config.lock_dir / f"usb-{self.config.device_index}.lock")
            self._lease.acquire()
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        self._connection = parent
        config = self.config.model_dump(mode="json")
        self._process = context.Process(target=_capture_worker, args=(child, config), daemon=True, name="opencv-capture")
        try:
            self._process.start()
            child.close()
            kind, fps = self._receive(timeout_s or self.config.io_timeout_s)
            if kind != "opened": raise SourceError("source_open_failed")
            self._opened = True
            return SourceInfo(fps)
        except BaseException:
            child.close(); self.close(); raise

    def read(self):
        if not self._opened: raise SourceError("source_not_open")
        try:
            self._connection.send("read")
            kind, value = self._receive(self.config.io_timeout_s)
        except (EOFError, BrokenPipeError, OSError) as exc:
            raise SourceError("capture_process_exited") from exc
        if kind == "eof":
            self._opened = False
            return SourceRead(eof=True)
        if kind != "frame": raise SourceError("invalid_backend_response")
        return SourceRead(image=value[0], source_time_seconds=value[1])

    def is_open(self):
        return self._opened and self._process is not None and self._process.is_alive()

    def interrupt(self):
        self._cancel.set()

    def close(self):
        self._cancel.set(); self._opened = False
        p, self._process = self._process, None
        if self._connection is not None:
            self._connection.close(); self._connection = None
        if p is not None and p.pid is not None:
            p.join(.25)
            if p.is_alive(): p.terminate(); p.join(.75)
            if p.is_alive(): p.kill(); p.join(.5)
            if not p.is_alive(): p.close()
        if self._lease is not None:
            self._lease.close(); self._lease = None
