"""Shared capture session, bounded queue, independent preview and arrival statistics."""
from __future__ import annotations

from collections import deque
import logging
import threading
import time
import uuid

from .config import CaptureConfig
from .frame import VideoFrame
from .queue import LatestFrameQueue
from .source import OpenCVSource, SourceCancelled, SourceError

logger = logging.getLogger("anjing.capture")
ERROR_MESSAGES = {
    "source_open_failed":"无法打开摄像头或视频，请检查设备占用及输入配置",
    "video_not_found":"配置的视频文件不存在",
    "video_decode_failed":"视频解码失败，尚未正常到达文件末尾",
    "device_disconnected":"摄像头读取失败或连接中断",
    "device_in_use":"摄像头已被另一采集进程占用",
    "source_timeout":"输入源响应超时",
    "capture_process_exited":"底层采集进程异常退出",
    "invalid_frame":"输入源返回无效图像",
    "capture_backend_error":"底层视频采集发生异常",
    "capture_thread_error":"采集线程异常退出",
    "stop_timeout":"采集未在停止期限内退出，禁止重复启动",
}


class CaptureManager:
    def __init__(self, config: CaptureConfig, source_factory=OpenCVSource):
        self.config, self.source_factory = config, source_factory
        self._lock = threading.RLock()
        self._control = threading.RLock()
        self._stop = threading.Event()
        self._thread = self._source = self._latest = None
        self._queue = LatestFrameQueue(config.queue_capacity)
        self._queue.close()
        self.state, self.reason = "stopped", "not_started"
        self.owner_org_id = self.session_id = None
        self.frame_count = self.reconnect_count = self.processed_frame_count = 0
        self._input_times = deque(maxlen=256)
        self._processed_times = deque(maxlen=256)
        self.last_error = self.nominal_fps = self.capture_latency_ms = self.consumer_latency_ms = None
        self.last_frame_timestamp = self.last_monotonic_timestamp = self.actual_width = self.actual_height = None
        self._preview_lock = threading.Lock()
        self._jpeg_key = self._jpeg = None

    def assert_owner(self, org_id):
        with self._lock:
            if self.owner_org_id is not None and self.owner_org_id != org_id:
                raise PermissionError("采集会话属于其他机构")

    def _state(self, state, reason=None):
        with self._lock:
            self.state, self.reason = state, reason
        logger.info("capture_state camera_id=%s state=%s reason=%s", self.config.camera_id, state, reason)

    def _error(self, exc):
        code = str(exc) if isinstance(exc, SourceError) and str(exc) in ERROR_MESSAGES else "capture_thread_error"
        with self._lock:
            self.last_error = {"code":code,"message":ERROR_MESSAGES[code],"timestamp":time.time()}
        logger.warning("capture_error camera_id=%s code=%s exception_type=%s", self.config.camera_id, code, type(exc).__name__)
        return code

    def start(self, owner_org_id: int):
        with self._control, self._lock:
            if self._thread is not None and self._thread.is_alive():
                self.assert_owner(owner_org_id)
                if self._stop.is_set(): raise RuntimeError("采集仍在停止中")
                return self.session_id
            self._queue.close(discard=True)
            self._queue = LatestFrameQueue(self.config.queue_capacity)
            self._stop = threading.Event()
            self.session_id, self.owner_org_id = uuid.uuid4().hex, owner_org_id
            self.frame_count = self.reconnect_count = self.processed_frame_count = 0
            self._input_times.clear(); self._processed_times.clear()
            self._latest = self._jpeg_key = self._jpeg = None
            self.last_error = self.nominal_fps = self.capture_latency_ms = self.consumer_latency_ms = None
            self.last_frame_timestamp = self.last_monotonic_timestamp = self.actual_width = self.actual_height = None
            self._state("starting")
            self._thread = threading.Thread(target=self._run, name=f"capture-{self.config.camera_id}", daemon=True)
            self._thread.start()
            return self.session_id

    def _run(self):
        failure_started = None
        failed_attempts = 0
        try:
            while not self._stop.is_set():
                source = None
                try:
                    source = self.source_factory(self.config)
                    with self._lock: self._source = source
                    if self._stop.is_set(): break
                    remaining = self.config.io_timeout_s if failure_started is None else min(
                        self.config.io_timeout_s, self.config.reconnect_max_wait_s-(time.monotonic()-failure_started))
                    if remaining <= 0:
                        self._state("error", "reconnect_exhausted"); return
                    info = source.open(timeout_s=remaining)
                    with self._lock: self.nominal_fps = info.nominal_fps
                    self._state("running")
                    # File playback is paced and never duplicates low-FPS source frames.
                    fps = min(self.config.target_fps, info.nominal_fps) if self.config.source_type == "file" and info.nominal_fps else self.config.target_fps
                    next_read = time.monotonic()
                    while not self._stop.is_set():
                        if self._stop.wait(max(0., next_read-time.monotonic())): break
                        began = time.monotonic()
                        packet = source.read()
                        arrived = time.monotonic()
                        if self._stop.is_set(): break
                        if packet.eof:
                            if self.config.source_type != "file": raise SourceError("device_disconnected")
                            self._state("stopped", "end_of_file"); return
                        with self._lock: number = self.frame_count+1
                        frame = VideoFrame.create(camera_id=self.config.camera_id, session_id=self.session_id,
                            frame_id=number, timestamp=time.time(), monotonic_timestamp=arrived,
                            image=packet.image, source_type=self.config.source_type, source_time_seconds=packet.source_time_seconds)
                        if not self._queue.put(frame): break
                        with self._lock:
                            self._latest = frame; self.frame_count = number
                            self._input_times.append(arrived)
                            self.last_frame_timestamp, self.last_monotonic_timestamp = frame.timestamp, arrived
                            self.actual_width, self.actual_height = frame.width, frame.height
                            self.capture_latency_ms = (arrived-began)*1000
                        failure_started = None; failed_attempts = 0
                        next_read = max(next_read+1/fps, arrived)
                    break
                except SourceCancelled:
                    if not self._stop.is_set():
                        self._error(SourceError("capture_backend_error")); self._state("error", "capture_failed")
                    return
                except SourceError as exc:
                    if self._stop.is_set(): break
                    code = self._error(exc)
                    if self.config.source_type == "file" or code == "device_in_use":
                        self._state("error", "source_failed"); return
                    if failure_started is None: failure_started = time.monotonic()
                    failed_attempts += 1
                finally:
                    if source is not None: source.close()
                    with self._lock: self._source = None
                remaining = self.config.reconnect_max_wait_s-(time.monotonic()-failure_started)
                if remaining <= 0:
                    self._state("error", "reconnect_exhausted"); return
                self._state("reconnecting")
                delay = min(self.config.retry_max_interval_s,
                            self.config.retry_interval_s*2**min(failed_attempts-1,10), remaining)
                if self._stop.wait(delay): break
                if time.monotonic()-failure_started >= self.config.reconnect_max_wait_s:
                    self._state("error", "reconnect_exhausted"); return
                with self._lock: self.reconnect_count += 1
        except Exception as exc:
            if not self._stop.is_set():
                self._error(exc); self._state("error", "capture_thread_failed")
        finally:
            self._queue.close()
            if self._stop.is_set(): self._state("stopped", "user_stopped")

    def stop(self, owner_org_id=None):
        with self._control:
            if owner_org_id is not None: self.assert_owner(owner_org_id)
            self._stop.set(); self._queue.close(discard=True)
            with self._lock:
                thread, source = self._thread, self._source
                self._latest = self._jpeg = self._jpeg_key = None
            if source is not None: source.interrupt()
            if thread is not None and thread is not threading.current_thread(): thread.join(self.config.stop_timeout_s)
            if thread is not None and thread.is_alive():
                self._error(SourceError("stop_timeout")); self._state("error", "stop_timeout")
                return False
            self._state("stopped", "user_stopped")
            return True

    def consume_latest(self, timeout=0):
        """Shared work queue. Preview never calls this; this is not a broadcast bus."""
        frame = self._queue.get_latest(timeout)
        if frame is not None:
            now = time.monotonic()
            with self._lock:
                # An old waiter may wake after a fresh session starts.
                if frame.session_id != self.session_id: return None
                self.processed_frame_count += 1; self._processed_times.append(now)
                self.consumer_latency_ms = max(0., (now-frame.monotonic_timestamp)*1000)
        return frame

    def latest_frame(self):
        with self._lock: return self._latest

    def preview_jpeg(self):
        if not self.config.preview_enabled: raise PermissionError("预览已关闭")
        frame = self.latest_frame()
        if frame is None: return None
        key = frame.session_id, frame.frame_id
        with self._preview_lock:
            if key != self._jpeg_key:
                import cv2
                ok, encoded = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ok: raise RuntimeError("预览编码失败")
                self._jpeg_key, self._jpeg = key, encoded.tobytes()
            result = self._jpeg
        return frame, result

    @staticmethod
    def _fps(times, now):
        recent = [t for t in times if t >= now-2]
        return round((len(recent)-1)/(recent[-1]-recent[0]),2) if len(recent)>1 and recent[-1]>recent[0] else 0.

    def status(self):
        now = time.monotonic()
        with self._lock:
            return {"camera_id":self.config.camera_id,"session_id":self.session_id,"state":self.state,"reason":self.reason,
                "source":self.config.public_source(),"connected":self.state=="running",
                "thread_alive":bool(self._thread and self._thread.is_alive()),
                "preview_enabled":self.config.preview_enabled,
                "width":self.actual_width,"height":self.actual_height,"requested_fps":self.config.target_fps,
                "nominal_fps":self.nominal_fps,"input_fps":self._fps(self._input_times,now),
                "processed_fps":self._fps(self._processed_times,now) if self.processed_frame_count else None,
                "frame_count":self.frame_count,"processed_frame_count":self.processed_frame_count,
                "last_frame_timestamp":self.last_frame_timestamp,
                "last_frame_age_ms":max(0.,(now-self.last_monotonic_timestamp)*1000) if self.last_monotonic_timestamp is not None else None,
                "capture_latency_ms":self.capture_latency_ms,"consumer_latency_ms":self.consumer_latency_ms,
                "end_to_end_latency_ms":None,"reconnect_count":self.reconnect_count,"last_error":self.last_error,
                **self._queue.metrics()}
