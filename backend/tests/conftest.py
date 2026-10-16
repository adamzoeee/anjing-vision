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

def _scheme_measurements():
    return {
        "room": {"length_m": 4.0, "width_m": 4.0, "height_m": 2.7,
                 "measurement_status": "verified", "confidence": "medium"},
        "openings": [
            {"id": "door_01", "type": "door", "center": [0.6, 0.0, 1.05], "width_m": 0.85,
             "height_m": 2.1, "rotation_z_deg": 0.0, "measurement_status": "verified"},
        ],
        "objects": [
            {"id": "bed_001", "instance_id": "bed_001", "type": "bed", "center": [1.0, 3.0, 0.3],
             "length_m": 2.0, "width_m": 1.2, "height_m": 0.6, "rotation_z_deg": 0.0,
             "measurement_status": "verified", "confidence": "medium"},
            {"id": "desk_001", "instance_id": "desk_001", "type": "desk", "center": [1.2, 2.0, 0.4],
             "length_m": 1.0, "width_m": 0.5, "height_m": 0.8, "rotation_z_deg": 0.0,
             "measurement_status": "verified", "confidence": "medium"},
        ],
    }


def _scheme_structure():
    return {
        "room": {"bounds_xy": {"min": [0.0, 0.0], "max": [4.0, 4.0]}, "height_m": 2.7},
        "walls": [], "geometric_obstacles": [],
        "doors": [{"instance_id": "door_01", "center": [0.6, 0.0, 1.05],
                   "size": [0.85, 0.12, 2.1], "rotation_z_deg": 0.0}],
        "semantic_instances": [
            {"instance_id": "bed_001", "label": "bed", "center": [1.0, 3.0, 0.3], "size": [2.0, 1.2, 0.6]},
            {"instance_id": "desk_001", "label": "desk", "center": [1.2, 2.0, 0.4], "size": [1.0, 0.5, 0.8]},
        ],
    }


@pytest.fixture
def scheme_inputs():
    """一套可评分的合成房间：4×4m、一床一桌一门。"""
    return _scheme_measurements(), _scheme_structure()


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
