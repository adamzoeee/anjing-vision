"""存储抽象：local（开发默认）| minio（S3 兼容）。"""
from io import BytesIO
from pathlib import Path

from .config import get_settings

s = get_settings()


def _local_root() -> Path:
    root = Path(s.data_dir)
    (root / "media").mkdir(parents=True, exist_ok=True)
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(parents=True, exist_ok=True)
    return root


def save_media(scan_id: int, filename: str, content: bytes) -> str:
    """保存原始视频/照片，返回存储相对路径。"""
    if s.storage_backend == "minio":
        from minio import Minio
        client = Minio(s.minio_endpoint, access_key=s.minio_access_key,
                       secret_key=s.minio_secret_key, secure=False)
        if not client.bucket_exists(s.minio_bucket):
            client.make_bucket(s.minio_bucket)
        key = f"media/{scan_id}/{filename}"
        client.put_object(s.minio_bucket, key, BytesIO(content), len(content))
        return key
    root = _local_root()
    dest = root / "media" / str(scan_id)
    dest.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name  # 防路径注入（../ 等）
    (dest / safe_name).write_bytes(content)
    return f"media/{scan_id}/{safe_name}"


def media_path(rel: str) -> Path:
    return _local_root() / rel
