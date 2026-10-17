import math
import time

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from realtime.config import CaptureConfig
from realtime.frame import VideoFrame
from realtime.source import DeviceLease, OpenCVSource, SourceError


@pytest.fixture
def tiny_video(tmp_path):
    path = tmp_path / "test.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (64, 48))
    assert writer.isOpened()
    for i in range(5): writer.write(np.full((48, 64, 3), i*30, np.uint8))
    writer.release()
    return path


def test_config_rejects_invalid_values():
    for data in ({"source_type":"rtsp"}, {"source_type":"file"}, {"queue_capacity":0},
                 {"width":10000}, {"target_fps":math.inf}, {"retry_interval_s":5,"retry_max_interval_s":1}):
        with pytest.raises(ValidationError): CaptureConfig(**data)


def test_frame_contract_copies_pixels_and_keeps_coordinate_system():
    image = np.zeros((20,30,3),np.uint8)
    frame = VideoFrame.create(camera_id="test",session_id="s",frame_id=1,timestamp=time.time(),
                              monotonic_timestamp=time.monotonic(),image=image,source_type="file")
    image[:] = 255
    assert (frame.width,frame.height)==(30,20) and not frame.image.any()
    assert not frame.image.flags.writeable and frame.source_time_seconds is None
    with pytest.raises(ValueError): frame.image[0,0]=1
    with pytest.raises(ValueError): VideoFrame.create(camera_id="x",session_id="s",frame_id=0,
        timestamp=1,monotonic_timestamp=1,image=image,source_type="usb")


def test_file_source_reads_all_frames_and_ends_without_reconnect(tiny_video):
    source = OpenCVSource(CaptureConfig(source_type="file",video_path=tiny_video,io_timeout_s=8))
    try:
        assert source.open().nominal_fps == pytest.approx(5)
        assert source.is_open()
        frames = [source.read() for _ in range(5)]
        assert all(f.image.shape==(48,64,3) and not f.eof for f in frames)
        assert source.read().eof
        assert not source.is_open()
    finally: source.close(); source.close()
    assert source._process is None


def test_missing_file_does_not_spawn_process(tmp_path):
    source=OpenCVSource(CaptureConfig(source_type="file",video_path=tmp_path/"missing.avi"))
    with pytest.raises(SourceError,match="video_not_found"):source.open()
    source.close();assert source._process is None


def test_invalid_file_fails_and_releases_process(tmp_path):
    path=tmp_path/"bad.avi";path.write_bytes(b"not a video")
    source=OpenCVSource(CaptureConfig(source_type="file",video_path=path,io_timeout_s=8))
    with pytest.raises(SourceError,match="source_open_failed"):source.open()
    assert source._process is None


def test_device_lease_is_exclusive_and_reusable(tmp_path):
    first=DeviceLease(tmp_path/"usb.lock");second=DeviceLease(tmp_path/"usb.lock")
    first.acquire()
    try:
        with pytest.raises(SourceError,match="device_in_use"):second.acquire()
    finally:first.close()
    second.acquire();second.close()


def test_interrupt_wakes_a_waiting_process_read(tiny_video):
    source=OpenCVSource(CaptureConfig(source_type="file",video_path=tiny_video,io_timeout_s=8))
    source.open();source.interrupt()
    with pytest.raises(SourceError,match="capture_cancelled"):source.read()
    started=time.monotonic();source.close()
    assert time.monotonic()-started<2
