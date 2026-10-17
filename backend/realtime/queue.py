from collections import deque
import threading
import time


class LatestFrameQueue:
    """One producer, shared work-queue consumers; drop old pending frames on overload."""
    def __init__(self, capacity: int):
        if capacity < 1: raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._frames = deque()
        self._condition = threading.Condition()
        self._closed = False
        self.overflow_dropped = self.consumer_skipped = 0

    def put(self, frame):
        with self._condition:
            if self._closed: return False
            if len(self._frames) == self.capacity:
                self._frames.popleft(); self.overflow_dropped += 1
            self._frames.append(frame); self._condition.notify()
            return True

    def get_latest(self, timeout: float = 0):
        if timeout < 0: raise ValueError("timeout must be nonnegative")
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._frames and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0: return None
                self._condition.wait(remaining)
            if not self._frames: return None
            self.consumer_skipped += len(self._frames)-1
            frame = self._frames[-1]; self._frames.clear()
            return frame

    def close(self, discard=False):
        with self._condition:
            self._closed = True
            if discard: self._frames.clear()
            self._condition.notify_all()

    def metrics(self):
        with self._condition:
            return {"queue_depth":len(self._frames), "queue_capacity":self.capacity,
                    "overflow_dropped_frames":self.overflow_dropped,
                    "consumer_skipped_frames":self.consumer_skipped,
                    "dropped_frames":self.overflow_dropped+self.consumer_skipped}
