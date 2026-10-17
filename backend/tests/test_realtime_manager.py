import threading
import time

import numpy as np
import pytest

from realtime.config import CaptureConfig
from realtime.manager import CaptureManager
from realtime.queue import LatestFrameQueue
from realtime.source import SourceCancelled, SourceError, SourceInfo, SourceRead


class FakeSource:
    def __init__(self, *, fail_open=False, fail_read=False, eof_after=None, unexpected=False, block=False):
        self.fail_open,self.fail_read,self.eof_after,self.unexpected,self.block=fail_open,fail_read,eof_after,unexpected,block
        self.frames=0;self.closed=False;self.opened=False;self.cancel=threading.Event()

    def open(self, timeout_s=None):
        if self.fail_open:raise SourceError("source_open_failed")
        self.opened=True;return SourceInfo(20)

    def read(self):
        if self.block:
            self.cancel.wait(5)
            raise SourceCancelled("capture_cancelled")
        if self.unexpected:raise ValueError("unexpected driver exception")
        if self.fail_read:raise SourceError("device_disconnected")
        if self.eof_after is not None and self.frames>=self.eof_after:return SourceRead(eof=True)
        self.frames+=1
        return SourceRead(np.full((16,24,3),self.frames%255,np.uint8))

    def close(self):self.closed=True;self.opened=False
    def interrupt(self):self.cancel.set()
    def is_open(self):return self.opened


def wait_for(predicate, timeout=2):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.005)
    assert predicate(),"condition did not become true"


def test_queue_overflow_latest_and_accounting():
    q=LatestFrameQueue(2)
    for i in range(10):assert q.put(i)
    assert q.metrics()["queue_depth"]==2
    assert q.metrics()["overflow_dropped_frames"]==8
    assert q.get_latest()==9
    assert q.metrics()["dropped_frames"]==9
    assert q.metrics()["consumer_skipped_frames"]==1
    assert q.metrics()["queue_depth"]==0


def test_queue_concurrent_producer_and_slow_consumer_are_bounded():
    q=LatestFrameQueue(3);seen=[];max_depth=[]
    def producer():
        for i in range(3000):q.put(i);max_depth.append(q.metrics()["queue_depth"])
        q.close()
    def consumer():
        while True:
            item=q.get_latest(.02)
            if item is None:break
            seen.append(item);time.sleep(.001)
    threads=[threading.Thread(target=f) for f in (producer,consumer)]
    for t in threads:t.start()
    for t in threads:t.join(2);assert not t.is_alive()
    assert max(max_depth)<=3 and seen==sorted(set(seen)) and seen[-1]==2999
    assert q.metrics()["dropped_frames"]+len(seen)==3000


def test_close_wakes_all_waiters_without_deadlock():
    q=LatestFrameQueue(1);values=[]
    threads=[threading.Thread(target=lambda:values.append(q.get_latest(30))) for _ in range(3)]
    for t in threads:t.start()
    q.close(discard=True)
    for t in threads:t.join(.5);assert not t.is_alive()
    assert values==[None,None,None] and not q.put(1)


def test_manager_ids_clock_resolution_and_independent_preview():
    source=FakeSource();manager=CaptureManager(CaptureConfig(target_fps=60),lambda c:source)
    session=manager.start(1)
    try:
        assert manager.start(1)==session
        wait_for(lambda:manager.frame_count>=4)
        one=manager.consume_latest();wait_for(lambda:manager.frame_count>one.frame_id)
        two=manager.consume_latest()
        assert two.frame_id>one.frame_id and two.monotonic_timestamp>one.monotonic_timestamp
        assert (two.width,two.height)==(24,16)
        before=manager.status();jpeg=manager.preview_jpeg()
        assert jpeg[1].startswith(b'\xff\xd8')
        assert manager.status()["processed_frame_count"]==before["processed_frame_count"]
        assert manager.status()["end_to_end_latency_ms"] is None
        assert manager.status()["capture_latency_ms"]>=0
        assert manager.status()["consumer_latency_ms"]>=0
    finally:assert manager.stop(1)
    assert source.closed and not manager.status()["thread_alive"]
    assert manager.latest_frame() is None and manager.status()["queue_depth"]==0
    assert manager.stop(1)


def test_no_inference_statistics_are_fabricated():
    manager=CaptureManager(CaptureConfig(target_fps=60),lambda c:FakeSource())
    manager.start(1)
    try:
        wait_for(lambda:manager.frame_count>=4)
        data=manager.status()
        assert data["input_fps"]>0 and data["processed_fps"] is None
        assert data["end_to_end_latency_ms"] is None and data["consumer_latency_ms"] is None
        assert data["dropped_frames"]>0
    finally:manager.stop()


def test_file_eof_stops_once_and_queued_last_frame_can_be_consumed(tmp_path):
    source=FakeSource(eof_after=2);calls=[]
    def factory(c):calls.append(1);return source
    manager=CaptureManager(CaptureConfig(source_type="file",video_path=tmp_path/'fake.avi',target_fps=60),factory)
    manager.start(1);wait_for(lambda:manager.status()["reason"]=="end_of_file")
    wait_for(lambda:source.closed)
    assert len(calls)==1 and manager.status()["reconnect_count"]==0
    assert manager.consume_latest().frame_id==2
    manager.stop()


def test_usb_disconnect_recovers_and_frame_ids_continue():
    sources=[FakeSource(fail_read=True),FakeSource()]
    manager=CaptureManager(CaptureConfig(retry_interval_s=.01,retry_max_interval_s=.02),lambda c:sources.pop(0))
    manager.start(1)
    try:
        wait_for(lambda:manager.frame_count>=2)
        assert manager.state=="running" and manager.reconnect_count==1
        assert manager.status()["last_error"]["code"]=="device_disconnected"
    finally:manager.stop()


def test_open_failures_back_off_and_exhaust_without_tight_loop():
    calls=[]
    def factory(c):calls.append(time.monotonic());return FakeSource(fail_open=True)
    manager=CaptureManager(CaptureConfig(retry_interval_s=.02,retry_max_interval_s=.04,reconnect_max_wait_s=.11),factory)
    manager.start(1);wait_for(lambda:manager.state=="error")
    assert manager.reason=="reconnect_exhausted" and 2<=len(calls)<=5
    assert all(b-a>=.015 for a,b in zip(calls,calls[1:]))
    manager.stop()


def test_stop_interrupts_reconnect_and_no_more_devices_open():
    calls=[]
    manager=CaptureManager(CaptureConfig(retry_interval_s=2,retry_max_interval_s=2),lambda c:calls.append(1) or FakeSource(fail_open=True))
    manager.start(1);wait_for(lambda:manager.state=="reconnecting")
    start=time.monotonic();assert manager.stop()
    assert time.monotonic()-start<.5
    count=len(calls);time.sleep(.03);assert len(calls)==count


def test_stop_interrupts_waiting_reader_and_repeated_sessions_release_sources():
    sources=[]
    def factory(c):s=FakeSource(block=True);sources.append(s);return s
    manager=CaptureManager(CaptureConfig(),factory)
    sessions=[]
    for _ in range(3):
        sessions.append(manager.start(1));wait_for(lambda:manager.state=="running")
        start=time.monotonic();assert manager.stop()
        assert time.monotonic()-start<.5
    assert len(set(sessions))==3 and all(s.closed for s in sources)


def test_unexpected_thread_exception_is_visible_and_releases_source():
    source=FakeSource(unexpected=True);manager=CaptureManager(CaptureConfig(),lambda c:source)
    manager.start(1);wait_for(lambda:manager.state=="error")
    wait_for(lambda:source.closed)
    assert manager.last_error["code"]=="capture_thread_error"
    manager.stop()


def test_preview_disabled_does_not_stop_consumers_and_org_isolation():
    manager=CaptureManager(CaptureConfig(preview_enabled=False),lambda c:FakeSource())
    manager.start(7)
    try:
        wait_for(lambda:manager.frame_count>0)
        with pytest.raises(PermissionError):manager.preview_jpeg()
        assert manager.consume_latest() is not None
        with pytest.raises(PermissionError):manager.assert_owner(8)
        with pytest.raises(PermissionError):manager.start(8)
        with pytest.raises(PermissionError):manager.stop(8)
        assert manager.state=="running"
    finally:manager.stop()
