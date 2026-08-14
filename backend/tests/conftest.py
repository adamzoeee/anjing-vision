import os
from pathlib import Path

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite:///./test.db")
os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-with-at-least-32-bytes"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["TASK_SYNC"] = "true"  # 测试环境管道同步执行，避免 Redis/Celery 依赖
# 测试存储隔离到专用目录：否则 clean_db 的 rmtree 会删掉生产 backend/data/
# 用绝对路径，避免从仓库根跑 pytest 时目录落错位置
os.environ["DATA_DIR"] = str(Path(__file__).resolve().parent.parent / ".test-data")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app

engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    # 清理本地存储目录，避免文件残留影响跨测试用例断言
    import shutil
    from app.storage import _local_root
    shutil.rmtree(_local_root(), ignore_errors=True)


@pytest.fixture
def client():
    def override():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
