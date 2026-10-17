from app.config import Settings


def test_default_video_upload_limit_is_1_5_gib(monkeypatch):
    monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)

    settings = Settings(_env_file=None)

    assert settings.max_upload_bytes == 1_610_612_736
