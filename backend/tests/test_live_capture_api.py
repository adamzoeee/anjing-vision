from dataclasses import replace
import time

from fastapi.testclient import TestClient
import numpy as np
import pytest

from app.config import Settings
from app.main import create_app
from realtime.config import CaptureConfig
from realtime.manager import CaptureManager
from realtime.source import SourceError, SourceInfo, SourceRead


class FakeSource:
    def __init__(self, broken=False):self.closed=False;self.broken=broken
    def open(self, timeout_s=None):
        if self.broken:raise SourceError("source_open_failed")
        return SourceInfo(30)
    def read(self):return SourceRead(np.zeros((16,24,3),np.uint8))
    def close(self):self.closed=True
    def interrupt(self):pass


def wait_frames(manager):
    until=time.monotonic()+2
    while time.monotonic()<until and manager.frame_count<2:time.sleep(.01)
    assert manager.frame_count>=2


def users(client):
    def register(org,email):
        response=client.post('/api/auth/register',json={'org_name':org,'name':'Test','email':email,'password':'secret123'})
        assert response.status_code==200
        return {'Authorization':'Bearer '+response.json()['token']}
    return register('capture-a','cap-a@example.com'),register('capture-a','member@example.com'),register('capture-b','cap-b@example.com')


def install(client, *, enabled=True, preview=True, broken=False):
    manager=CaptureManager(CaptureConfig(target_fps=30,preview_enabled=preview),lambda c:FakeSource(broken))
    client.app.state.live_capture=manager
    original=client.app.state.settings
    client.app.state.settings=original.model_copy(update={'realtime_enabled':enabled})
    return manager


def test_requires_auth_and_admin_and_honors_disabled(client):
    manager=install(client,enabled=False)
    assert client.get('/api/live-capture/status').status_code==401
    assert client.get('/api/live-capture/frame.jpg').status_code==401
    owner,member,stranger=users(client)
    assert client.post('/api/live-capture/start',headers=member).status_code==403
    assert client.post('/api/live-capture/start',headers=owner).status_code==503
    assert client.get('/api/live-capture/status',headers=owner).json()['enabled'] is False
    assert manager.frame_count==0


def test_start_status_preview_ownership_idempotence_and_stop(client):
    manager=install(client)
    owner,member,stranger=users(client)
    try:
        assert client.get('/api/live-capture/frame.jpg',headers=owner).status_code==204
        started=client.post('/api/live-capture/start',headers=owner)
        assert started.status_code==200
        session=started.json()['session_id']
        assert client.post('/api/live-capture/start',headers=owner).json()['session_id']==session
        wait_frames(manager)
        status=client.get('/api/live-capture/status',headers=member).json()
        assert status['state']=='running' and (status['width'],status['height'])==(24,16)
        assert status['processed_fps'] is None and status['end_to_end_latency_ms'] is None
        assert status['source']['filename'] is None
        before=manager.status()['processed_frame_count']
        image=client.get('/api/live-capture/frame.jpg',headers=member)
        assert image.status_code==200 and image.headers['content-type']=='image/jpeg'
        assert image.content[:2]==b'\xff\xd8' and image.headers['cache-control']=='no-store'
        assert image.headers['x-capture-session']==session
        assert manager.status()['processed_frame_count']==before
        for method,path in [('get','status'),('get','frame.jpg'),('post','start'),('post','stop')]:
            assert getattr(client,method)('/api/live-capture/'+path,headers=stranger).status_code==403
        assert client.post('/api/live-capture/stop',headers=member).status_code==403
        assert client.post('/api/live-capture/stop',headers=owner).json()['state']=='stopped'
        assert client.post('/api/live-capture/stop',headers=owner).status_code==200
        assert not manager.status()['thread_alive']
        assert client.get('/api/live-capture/frame.jpg',headers=owner).status_code==204
    finally:manager.stop()


def test_preview_disconnect_does_not_stop_shared_capture(client):
    manager=install(client);owner,_,_=users(client)
    try:
        client.post('/api/live-capture/start',headers=owner);wait_frames(manager)
        client.get('/api/live-capture/frame.jpg',headers=owner).close()
        number=manager.frame_count;time.sleep(.1)
        assert manager.frame_count>number and manager.state=='running'
    finally:manager.stop()


def test_disabled_preview_keeps_internal_consumption_running(client):
    manager=install(client,preview=False);owner,_,_=users(client)
    try:
        client.post('/api/live-capture/start',headers=owner);wait_frames(manager)
        assert client.get('/api/live-capture/frame.jpg',headers=owner).status_code==403
        assert manager.consume_latest() is not None and manager.state=='running'
    finally:manager.stop()


def test_hardware_unavailable_is_a_visible_error_not_server_failure(client):
    manager=install(client,broken=True);owner,_,_=users(client)
    try:
        client.post('/api/live-capture/start',headers=owner)
        until=time.monotonic()+1
        while time.monotonic()<until and manager.status()['last_error'] is None:time.sleep(.01)
        response=client.get('/api/live-capture/status',headers=owner)
        assert response.status_code==200
        assert response.json()['state'] in {'starting','reconnecting','running'}
        assert response.json()['last_error']['code']=='source_open_failed'
        assert client.get('/api/live-capture/frame.jpg',headers=owner).status_code==204
    finally:manager.stop()


def test_lifespan_always_releases_capture_and_disabled_startup_has_no_camera(tmp_path):
    application=create_app(Settings(data_dir=str(tmp_path),realtime_enabled=True))
    source=FakeSource();manager=CaptureManager(CaptureConfig(),lambda c:source)
    application.state.live_capture=manager
    with TestClient(application):
        assert manager.state=='stopped'
        manager.start(1);wait_frames(manager)
    assert source.closed and not manager.status()['thread_alive']


def test_nested_settings_validate_existing_env_style(monkeypatch):
    monkeypatch.setenv('REALTIME_ENABLED','true');monkeypatch.setenv('REALTIME__TARGET_FPS','7')
    settings=Settings(_env_file=None)
    assert settings.realtime_enabled and settings.realtime.target_fps==7
    monkeypatch.setenv('REALTIME__QUEUE_CAPACITY','0')
    with pytest.raises(ValueError):Settings(_env_file=None)
