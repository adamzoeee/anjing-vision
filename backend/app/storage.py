"""Local and MinIO media storage with bounded streaming writes."""
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .config import get_settings

s = get_settings()
_CHUNK_SIZE = 1024 * 1024


class MediaTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class StoredMedia:
    path: str
    size: int


def _local_root() -> Path:
    root = Path(s.data_dir)
    for child in ("media", "work", "reports", "cache"):
        (root / child).mkdir(parents=True, exist_ok=True)
    return root


def _safe_filename(filename: str) -> str:
    safe_name = Path(filename).name
    if safe_name in ("", ".", ".."):
        return "media.bin"
    return safe_name


def _minio_client():
    from minio import Minio

    return Minio(
        s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key,
        secure=s.minio_secure,
    )


def _ensure_bucket(client) -> None:
    if not client.bucket_exists(s.minio_bucket):
        client.make_bucket(s.minio_bucket)


def _reserve_local_file(directory: Path, filename: str) -> tuple[str, Path, BinaryIO]:
    """Atomically reserve a unique local filename and return its open stream."""
    stem, suffix = (
        filename.rsplit(".", 1) if "." in filename else (filename, "")
    )
    index = 0
    while True:
        candidate = (
            filename
            if index == 0
            else f"{stem}_{index}{'.' + suffix if suffix else ''}"
        )
        target = directory / candidate
        try:
            return candidate, target, target.open("xb")
        except FileExistsError:
            pass
        index += 1


def save_media_stream(
    scan_id: int,
    filename: str,
    stream: BinaryIO,
    max_bytes: int,
) -> StoredMedia:
    safe_name = _safe_filename(filename)
    if s.storage_backend == "minio":
        return _save_minio_stream(scan_id, safe_name, stream, max_bytes)
    return _save_local_stream(scan_id, safe_name, stream, max_bytes)


def _save_local_stream(
    scan_id: int,
    filename: str,
    stream: BinaryIO,
    max_bytes: int,
) -> StoredMedia:
    destination = _local_root() / "media" / str(scan_id)
    destination.mkdir(parents=True, exist_ok=True)
    safe_name, target, output = _reserve_local_file(destination, filename)
    size = 0
    completed = False
    try:
        with output:
            while chunk := stream.read(min(_CHUNK_SIZE, max_bytes - size + 1)):
                size += len(chunk)
                if size > max_bytes:
                    raise MediaTooLargeError
                output.write(chunk)
        completed = True
    finally:
        if not completed:
            target.unlink(missing_ok=True)
    return StoredMedia(f"media/{scan_id}/{safe_name}", size)


def _stream_size(stream: BinaryIO, max_bytes: int) -> int:
    position = stream.tell()
    stream.seek(0, 2)
    size = stream.tell() - position
    stream.seek(position)
    if size > max_bytes:
        raise MediaTooLargeError
    return size


def _save_minio_stream(
    scan_id: int,
    filename: str,
    stream: BinaryIO,
    max_bytes: int,
) -> StoredMedia:
    client = _minio_client()
    _ensure_bucket(client)
    size = _stream_size(stream, max_bytes)
    key = f"media/{scan_id}/{filename}"
    client.put_object(s.minio_bucket, key, stream, size)
    return StoredMedia(key, size)


def delete_media(relative_path: str) -> None:
    if s.storage_backend == "minio":
        _minio_client().remove_object(s.minio_bucket, relative_path)
        return
    target = _local_root() / relative_path
    if target.is_file():
        target.unlink(missing_ok=True)


def media_path(relative_path: str) -> Path:
    """Return a local path, materializing MinIO objects for the worker."""
    if s.storage_backend == "local":
        return _local_root() / relative_path

    client = _minio_client()
    cache_target = _local_root() / "cache" / relative_path
    objects = list(
        client.list_objects(
            s.minio_bucket,
            prefix=relative_path.rstrip("/") + "/",
            recursive=True,
        )
    )
    if objects:
        cache_target.mkdir(parents=True, exist_ok=True)
        for item in objects:
            name = Path(item.object_name).name
            client.fget_object(s.minio_bucket, item.object_name, str(cache_target / name))
        return cache_target

    cache_target.parent.mkdir(parents=True, exist_ok=True)
    client.fget_object(s.minio_bucket, relative_path, str(cache_target))
    return cache_target
