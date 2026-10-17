"""Opt-in, offline-capable P0 acceptance using the real app, Uvicorn and capture.

No downloader, camera access, models or alternate capture implementation. A supplied
licensed file is optional; --synthetic creates a small local clip for repeatability.
Run from backend: python scripts/validate_live_capture.py --help
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import math
import multiprocessing as mp
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Timed out waiting for capture state')


def remaining_resources():
    return {
        'capture_threads': [t.name for t in threading.enumerate()
                            if t.name.startswith('capture-') and t.is_alive()],
        'capture_children': [p.pid for p in mp.active_children()
                             if p.name == 'opencv-capture' and p.is_alive()],
    }


def assert_released():
    wait_for(lambda: not any(remaining_resources().values()), timeout=5)


def generate_video(path, frames=30, fps=10):
    import cv2
    import numpy as np
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), fps, (160, 96))
    assert writer.isOpened(), 'MJPG encoder unavailable'
    try:
        for i in range(frames):
            image = np.full((96, 160, 3), 30, np.uint8)
            cv2.rectangle(image, (i % 110, 20), (i % 110 + 30, 70), (40, 190, 220), -1)
            writer.write(image)
    finally:
        writer.release()


class Harness:
    def __init__(self, output, fps):
        self.output, self.fps = output, fps
        self._clients = []
        # Set BEFORE importing app modules. Never use the user's database or media.
        os.environ.update(
            DATABASE_URL=f'sqlite:///{(output / "validation.db").as_posix()}',
            DATA_DIR=str(output / 'data'), ENVIRONMENT='test',
            SECRET_KEY=secrets.token_hex(32), STORAGE_BACKEND='local',
            TASK_SYNC='true', DEMO_LOGIN='false', AUTO_CREATE_TABLES='true',
            REALTIME_ENABLED='true', REALTIME__SOURCE_TYPE='usb',
        )
        # No device is opened: each app below explicitly configures source_type=file.
        from app.config import Settings
        from app.main import create_app
        from realtime.config import CaptureConfig
        self.Settings, self.create_app, self.CaptureConfig = Settings, create_app, CaptureConfig

    def new_client(self, **kwargs):
        import httpx
        client = httpx.Client(timeout=10, trust_env=False, **kwargs)
        self._clients.append(client)
        return client

    @contextmanager
    def serve(self, video):
        import uvicorn
        capture = self.CaptureConfig(source_type='file', video_path=video,
                                     target_fps=self.fps, queue_capacity=2)
        settings = self.Settings(_env_file=None, realtime_enabled=True, realtime=capture)
        app = self.create_app(settings)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))  # Never steal an existing API/demo port.
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                               log_config=None, access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]),
                                  name='validation-uvicorn', daemon=True)
        thread.start()
        client = None
        try:
            wait_for(lambda: server.started, timeout=15)
            client = self.new_client(base_url=f'http://127.0.0.1:{port}')
            assert client.get('/api/health').status_code == 200
            suffix = uuid.uuid4().hex
            registration = client.post('/api/auth/register', json={
                'org_name': f'capture-validation-{suffix}', 'name': 'Local validation',
                'email': f'{suffix}@example.com', 'password': secrets.token_urlsafe(20),
            })
            assert registration.status_code == 200, registration.status_code
            client.headers['Authorization'] = 'Bearer ' + registration.json()['token']
            yield app.state.live_capture, client
        finally:
            for opened_client in self._clients:
                opened_client.close()
            self._clients.clear()
            server.should_exit = True  # Runs the real app lifespan stop hook.
            thread.join(10)
            sock.close()
            assert not thread.is_alive(), 'Uvicorn did not shut down'
            assert_released()


def start(client):
    response = client.post('/api/live-capture/start')
    assert response.status_code == 200, response.status_code
    return response.json()['session_id']


def stop(manager, client):
    began = time.perf_counter()
    response = client.post('/api/live-capture/stop')
    elapsed = (time.perf_counter() - began) * 1000
    assert response.status_code == 200 and not response.json()['thread_alive']
    assert manager.latest_frame() is None
    assert client.get('/api/live-capture/frame.jpg').status_code == 204
    assert_released()
    return round(elapsed, 3)


def continuous(harness, video, deadline_s):
    import cv2
    import numpy as np
    with harness.serve(video) as (manager, client):
        session = start(client)
        began = time.monotonic()
        next_consume = began
        observed, samples, hashes = [], [], set()
        decoded, max_depth, disconnected = 0, 0, False
        previous = None
        timestamps_file = harness.output / 'frames.jsonl'
        with timestamps_file.open('w', encoding='utf-8') as frame_log:
            while time.monotonic() - began < deadline_s:
                response = client.get('/api/live-capture/status')
                assert response.status_code == 200
                status = response.json()
                assert status['state'] != 'error', status['last_error']
                max_depth = max(max_depth, status['queue_depth'])
                assert status['queue_depth'] <= 2
                assert status['end_to_end_latency_ms'] is None
                samples.append({'elapsed_s': time.monotonic() - began, **status})
                frame = manager.latest_frame()
                if frame is not None and (previous is None or frame.frame_id != previous.frame_id):
                    assert frame.session_id == session and frame.frame_id >= 1
                    assert frame.image.dtype == np.uint8 and not frame.image.flags.writeable
                    assert frame.image.shape == (frame.height, frame.width, 3)
                    assert frame.width <= 640 and frame.height <= 480
                    assert math.isfinite(frame.timestamp) and abs(time.time() - frame.timestamp) < 5
                    if previous is not None:
                        assert frame.frame_id > previous.frame_id
                        assert frame.monotonic_timestamp > previous.monotonic_timestamp
                    observed.append((frame.frame_id, frame.monotonic_timestamp))
                    frame_log.write(json.dumps({
                        'session_id': frame.session_id, 'frame_id': frame.frame_id,
                        'timestamp': frame.timestamp, 'monotonic_timestamp': frame.monotonic_timestamp,
                        'width': frame.width, 'height': frame.height,
                    }) + '\n')
                    previous = frame
                if time.monotonic() >= next_consume:
                    manager.consume_latest()
                    next_consume = time.monotonic() + .5  # Deliberately slow; not inference.
                count = manager.status()['processed_frame_count']
                jpeg = client.get('/api/live-capture/frame.jpg')
                assert jpeg.status_code in {200, 204}
                if jpeg.status_code == 200:
                    image = cv2.imdecode(np.frombuffer(jpeg.content, np.uint8), cv2.IMREAD_COLOR)
                    assert image is not None and image.shape == (frame.height, frame.width, 3)
                    assert jpeg.headers['x-capture-session'] == session
                    assert jpeg.headers['cache-control'] == 'no-store'
                    assert int(jpeg.headers['x-frame-id']) >= 1
                    hashes.add(hashlib.sha256(jpeg.content).hexdigest())
                    decoded += 1
                assert manager.status()['processed_frame_count'] == count, 'Preview stole queue frames'
                if not disconnected and manager.frame_count >= 5 and status['state'] == 'running':
                    before = manager.frame_count
                    # Actually close the HTTP client; reconnect after one second.
                    base, headers = str(client.base_url), dict(client.headers)
                    client.close()
                    time.sleep(1)
                    client = harness.new_client(base_url=base, headers=headers)
                    assert manager.frame_count > before and manager.status()['thread_alive']
                    disconnected = True
                if status['reason'] == 'end_of_file':
                    break
                time.sleep(.1)
        elapsed = time.monotonic() - began
        assert manager.status()['reason'] == 'end_of_file', 'Did not complete playback within deadline'
        wait_for(lambda: not manager.status()['thread_alive'])
        assert_released()
        final = manager.status()  # Snapshot after cleanup, not the transient EOF-before-finally state.
        count = final['frame_count']
        time.sleep(.3)
        assert manager.frame_count == count and manager.reconnect_count == 0
        assert decoded > 1 and len(hashes) > 1 and len(observed) > 1
        assert final['dropped_frames'] > 0 and disconnected
        assert count == final['processed_frame_count'] + final['dropped_frames'] + final['queue_depth']
        rolling_input = [s['input_fps'] for s in samples if s['state'] == 'running' and s['frame_count'] >= 20]
        rolling_consumer = [s['processed_fps'] for s in samples
                            if s['state'] == 'running' and s['processed_fps'] is not None and s['frame_count'] >= 20]
        result = {
            'passed': True, 'elapsed_s': round(elapsed, 3), 'final_status': final,
            'observed_distinct_frames': len(observed), 'decoded_jpeg_responses': decoded,
            'distinct_previews': len(hashes), 'max_queue_depth': max_depth,
            'delivery_fps_from_observed_ids': round((observed[-1][0] - observed[0][0]) /
                (observed[-1][1] - observed[0][1]), 3),
            'median_rolling_input_fps': float(np.median(rolling_input)) if rolling_input else None,
            'median_rolling_consumer_fps': float(np.median(rolling_consumer)) if rolling_consumer else None,
            'frontend_disconnect_capture_continued': disconnected,
            'end_to_end_p50_ms': None, 'end_to_end_p95_ms': None,
            'latency_unavailable_reason': 'No exposure timestamp, inference completion or frontend presentation acknowledgement',
            'resources_after_eof': remaining_resources(),
        }
        result['stop_after_eof_ms'] = stop(manager, client)
        client.close()  # May be a replacement for the context manager's original client.
    (harness.output / 'status-samples.json').write_text(json.dumps(samples, indent=2), encoding='utf-8')
    return result


def repeated_stop(harness, video):
    sessions, timings = [], []
    with harness.serve(video) as (manager, client):
        for _ in range(3):
            sessions.append(start(client))
            assert start(client) == sessions[-1], 'Duplicate start created another session'
            wait_for(lambda: manager.frame_count >= 2)
            timings.append(stop(manager, client))
            assert client.post('/api/live-capture/stop').status_code == 200
        assert len(set(sessions)) == 3
    return {'passed': True, 'cycles': 3, 'stop_ms': timings, 'resources': remaining_resources()}


def backend_shutdown(harness, video):
    with harness.serve(video) as (manager, client):
        start(client)
        wait_for(lambda: manager.frame_count >= 2)
        # Intentionally do not POST stop: real FastAPI lifespan must release it.
    assert manager.state == 'stopped' and not manager.status()['thread_alive']
    return {'passed': True, 'method': 'Uvicorn graceful shutdown / FastAPI lifespan',
            'resources': remaining_resources()}


def error_case(harness, video, expected, kill_reader=False):
    with harness.serve(video) as (manager, client):
        start(client)
        if kill_reader:
            wait_for(lambda: manager.frame_count >= 2)
            # Fault injection into THIS runner's child, never another service/process.
            manager._source._process.terminate()
        wait_for(lambda: manager.state == 'error')
        wait_for(lambda: not manager.status()['thread_alive'])
        response = client.get('/api/live-capture/status')
        status = response.json()
        assert response.status_code == 200 and status['last_error']['code'] == expected, status
        assert status['reconnect_count'] == 0
        assert_released()
        stop(manager, client)
    return {'passed': True, 'actual_error': status['last_error'],
            'fault_injection': 'terminate own decoder child' if kill_reader else 'local file fixture',
            'resources': remaining_resources()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, help='Existing, legally obtained local video; never downloaded here')
    parser.add_argument('--synthetic', action='store_true', help='Use offline generated MJPG, not public-video acceptance')
    parser.add_argument('--output-root', type=Path, default=BACKEND / 'tmp' / 'realtime-public-validation')
    parser.add_argument('--fps', type=float, default=10)
    parser.add_argument('--deadline', type=float, default=120, help='Playback acceptance deadline, seconds')
    args = parser.parse_args(argv)
    if args.synthetic == bool(args.video):
        parser.error('Choose exactly one of --video or --synthetic')
    if not math.isfinite(args.fps) or not 0 < args.fps <= 60:
        parser.error('fps must be finite and in (0,60]')
    if not math.isfinite(args.deadline) or args.deadline <= 0:
        parser.error('deadline must be positive')
    if args.video and not args.video.is_file():
        parser.error('--video must exist (missing-file negative test is automatic)')
    output = args.output_root.resolve() / f'{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}'
    output.mkdir(parents=True)
    logging.basicConfig(filename=output / 'runtime.log', level=logging.INFO,
                        format='%(asctime)s %(name)s %(levelname)s %(message)s', encoding='utf-8')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    video = args.video.resolve() if args.video else output / 'offline-synthetic.avi'
    if args.synthetic:
        generate_video(video)
    harness = Harness(output, args.fps)
    corrupted = output / 'corrupted.mp4'
    corrupted.write_bytes(b'not an mp4; intentionally invalid local fixture')
    with video.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    metadata = None
    ffprobe = shutil.which('ffprobe')
    if ffprobe:
        probe = subprocess.run([ffprobe, '-v', 'error', '-show_entries',
            'format=duration,size,format_name:stream=codec_name,codec_type,width,height,avg_frame_rate,nb_frames',
            '-of', 'json', str(video)], capture_output=True, text=True, timeout=20)
        if probe.returncode == 0:
            metadata = json.loads(probe.stdout)
    report = {'mode': 'synthetic' if args.synthetic else 'local-public-video',
              'input_file': str(video), 'input_bytes': video.stat().st_size,
              'input_sha256': digest, 'input_ffprobe': metadata,
              'config': {'source_type': 'file', 'target_fps': args.fps, 'width': 640,
                         'height': 480, 'queue_capacity': 2},
              'hardware_usb_verified': False, 'cases': {}}
    try:
        for name, run in [
            ('continuous_playback', lambda: continuous(harness, video, args.deadline)),
            ('repeated_stop', lambda: repeated_stop(harness, video)),
            ('backend_shutdown', lambda: backend_shutdown(harness, video)),
            ('missing_file', lambda: error_case(harness, output / 'missing.mp4', 'video_not_found')),
            ('corrupted_file', lambda: error_case(harness, corrupted, 'source_open_failed')),
            ('reader_failure', lambda: error_case(harness, video, 'capture_process_exited', kill_reader=True)),
        ]:
            report['cases'][name] = run()
            if name == 'continuous_playback' and metadata:
                streams = [s for s in metadata['streams'] if s['codec_type'] == 'video']
                expected = streams[0].get('nb_frames') if streams else None
                if expected and expected.isdigit():
                    actual = report['cases'][name]['final_status']['frame_count']
                    assert actual == int(expected), f'Incomplete decode: {actual}/{expected}'
            print(f'{name}: PASS', flush=True)
        report['passed'] = True
    except Exception as exc:
        logging.exception('validation failed')
        report.update(passed=False, exception_type=type(exc).__name__, exception_message=str(exc))
        raise
    finally:
        report['final_resources'] = remaining_resources()
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'RESULT: {output / "result.json"}', flush=True)
    return report


if __name__ == '__main__':
    main()
