from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.db import SessionLocal
from app.models import Report
from app.storage import media_path, save_media_stream
from app.tasks import pipeline_tasks


def _auth(client):
    registration = client.post(
        "/api/auth/register",
        json={
            "org_name": "存储测试机构",
            "name": "测试用户",
            "email": "storage@example.com",
            "password": "secret123",
        },
    ).json()
    return {"Authorization": f"Bearer {registration['token']}"}


def test_photo_upload_enforces_total_limit_and_cleans_partial_files(
    client,
    monkeypatch,
):
    from app.routers import scans

    headers = _auth(client)
    project_id = client.post(
        "/api/projects",
        json={"name": "照片上传"},
        headers=headers,
    ).json()["id"]
    scan_id = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "photos"},
        headers=headers,
    ).json()["id"]
    monkeypatch.setattr(scans, "MAX_UPLOAD_BYTES", 5)

    response = client.post(
        f"/api/scans/{scan_id}/upload",
        headers=headers,
        files=[
            ("files", ("one.jpg", b"123", "image/jpeg")),
            ("files", ("two.jpg", b"456", "image/jpeg")),
        ],
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    directory = media_path(f"media/{scan_id}")
    assert not directory.exists() or list(directory.iterdir()) == []


def test_minio_upload_sanitizes_object_name(monkeypatch, tmp_path):
    from app import storage

    class FakeClient:
        def __init__(self):
            self.key = None

        def bucket_exists(self, _):
            return True

        def put_object(self, _, key, stream, size):
            self.key = key
            assert stream.read() == b"content"
            assert size == 7

    client = FakeClient()
    monkeypatch.setattr(
        storage,
        "s",
        SimpleNamespace(
            storage_backend="minio",
            data_dir=str(tmp_path),
            minio_bucket="bucket",
        ),
    )
    monkeypatch.setattr(storage, "_minio_client", lambda: client)

    stored = save_media_stream(
        7,
        "../../unsafe.mp4",
        BytesIO(b"content"),
        max_bytes=10,
    )

    assert stored.path == "media/7/unsafe.mp4"
    assert client.key == "media/7/unsafe.mp4"


def test_minio_media_is_materialized_for_worker(monkeypatch, tmp_path):
    from app import storage

    class Item:
        def __init__(self, object_name):
            self.object_name = object_name

    class FakeClient:
        def list_objects(self, _, prefix, recursive):
            assert prefix == "media/9/"
            assert recursive is True
            return [Item("media/9/a.jpg"), Item("media/9/b.jpg")]

        def fget_object(self, _, object_name, destination):
            with open(destination, "wb") as output:
                output.write(object_name.encode("utf-8"))

    monkeypatch.setattr(
        storage,
        "s",
        SimpleNamespace(
            storage_backend="minio",
            data_dir=str(tmp_path),
            minio_bucket="bucket",
        ),
    )
    monkeypatch.setattr(storage, "_minio_client", FakeClient)

    local_directory = media_path("media/9")

    assert local_directory.is_dir()
    assert (local_directory / "a.jpg").read_bytes() == b"media/9/a.jpg"
    assert (local_directory / "b.jpg").read_bytes() == b"media/9/b.jpg"


def test_async_dispatch_failure_does_not_run_pipeline_in_request(monkeypatch):
    settings = SimpleNamespace(task_sync=False, allow_sync_fallback=False)
    sync_calls = []

    def fail_delay(_):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(pipeline_tasks, "s", settings)
    monkeypatch.setattr(pipeline_tasks.run_pipeline_async, "delay", fail_delay)
    monkeypatch.setattr(
        pipeline_tasks,
        "run_pipeline_sync",
        lambda scan_id: sync_calls.append(scan_id),
    )

    with pytest.raises(pipeline_tasks.TaskDispatchError):
        pipeline_tasks.dispatch_scan(12)
    assert sync_calls == []


def test_unexpected_dispatch_programming_error_is_not_swallowed(monkeypatch):
    settings = SimpleNamespace(task_sync=False, allow_sync_fallback=False)

    def fail_delay(_):
        raise TypeError("programming error")

    monkeypatch.setattr(pipeline_tasks, "s", settings)
    monkeypatch.setattr(pipeline_tasks.run_pipeline_async, "delay", fail_delay)

    with pytest.raises(TypeError, match="programming error"):
        pipeline_tasks.dispatch_scan(12)


def test_report_image_uses_expiring_signed_url(client, tmp_path):
    headers = _auth(client)
    project_id = client.post(
        "/api/projects",
        json={"name": "报告资源"},
        headers=headers,
    ).json()["id"]
    scan_id = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()["id"]
    image = tmp_path / "annotation.png"
    image.write_bytes(b"safe-image")

    db = SessionLocal()
    try:
        db.add(Report(scan_id=scan_id, score=80, images=[str(image)]))
        db.commit()
    finally:
        db.close()

    report = client.get(f"/api/reports/scans/{scan_id}", headers=headers)
    signed_url = report.json()["images"][0]
    parsed = urlsplit(signed_url)
    query = parse_qs(parsed.query)
    response = client.get(signed_url)
    tampered = client.get(
        f"{parsed.path}?expires={query['expires'][0]}&signature={'0' * 64}"
    )

    assert str(tmp_path) not in report.text
    assert response.status_code == 200
    assert response.content == b"safe-image"
    assert tampered.status_code == 401
