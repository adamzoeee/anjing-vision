from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

from app.storage import media_path, save_media_stream


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


def test_concurrent_local_uploads_atomically_reserve_distinct_names(
    monkeypatch,
    tmp_path,
):
    from app import storage

    monkeypatch.setattr(
        storage,
        "s",
        SimpleNamespace(storage_backend="local", data_dir=str(tmp_path)),
    )
    original_open = Path.open
    barrier = Barrier(2)
    counter_lock = Lock()
    open_count = 0

    def synchronized_open(path, mode="r", *args, **kwargs):
        nonlocal open_count
        should_wait = False
        if mode == "xb":
            with counter_lock:
                open_count += 1
                should_wait = open_count <= 2
        if should_wait:
            barrier.wait(timeout=5)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", synchronized_open)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                save_media_stream,
                11,
                "room.mp4",
                BytesIO(content),
                1024,
            )
            for content in (b"first", b"second")
        ]
        stored = [future.result(timeout=5) for future in futures]

    assert {item.path for item in stored} == {
        "media/11/room.mp4",
        "media/11/room_1.mp4",
    }
    assert {
        (tmp_path / item.path).read_bytes()
        for item in stored
    } == {b"first", b"second"}


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
