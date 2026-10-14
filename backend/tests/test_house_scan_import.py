"""Authenticated whole-house scan import; runnable directly without pytest fixtures."""
import tempfile
import hashlib
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.db import Base, get_db
from app.main import create_app


class HouseScanImportTest(unittest.TestCase):
    def test_import_is_idempotent_and_org_scoped(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        output = Path(tempfile.mkdtemp(prefix="anjing-house-scan-"))
        settings = Settings(auto_create_tables=False, data_dir=str(output))
        application = create_app(settings)

        def database():
            with session_factory() as db:
                yield db

        application.dependency_overrides[get_db] = database
        application.dependency_overrides[get_settings] = lambda: settings
        with TestClient(application) as client:
            def register(org, email):
                response = client.post("/api/auth/register", json={
                    "org_name": org, "name": "Tester", "email": email, "password": "secret123"
                })
                self.assertEqual(response.status_code, 200, response.text)
                return {"Authorization": f"Bearer {response.json()['token']}"}

            first = register("House Org A", "house-a@example.com")
            second = register("House Org B", "house-b@example.com")
            project = client.post("/api/projects", json={"name": "家庭点云演示"}, headers=first)
            self.assertEqual(project.status_code, 200, project.text)
            project_id = project.json()["id"]
            route = f"/api/projects/{project_id}/scans/house-demo"
            self.assertEqual(client.post(route, headers=second).status_code, 404)
            created = client.post(route, headers=first)
            self.assertEqual(created.status_code, 200, created.text)
            scan = created.json()
            self.assertEqual(scan["capture_type"], "house_demo")
            self.assertEqual(scan["status"], "done")
            self.assertEqual(client.post(route, headers=first).json()["id"], scan["id"])

            preview = f"/api/preview/{scan['id']}"
            self.assertEqual(client.get(f"{preview}/house-connections.json", headers=second).status_code, 404)
            manifest = client.get(f"{preview}/manifest.json", headers=first)
            self.assertEqual(manifest.status_code, 200, manifest.text)
            self.assertTrue(manifest.json()["house_connections"])
            graph = client.get(f"{preview}/house-connections.json", headers=first).json()
            structure = client.get(f"{preview}/structure.json", headers=first).json()
            self.assertEqual(len(graph["connections"]), 4)
            self.assertEqual(len(structure["doors"]), 4)
            cloud = client.get(f"{preview}/scene.ply", headers=first)
            self.assertEqual(cloud.status_code, 200)
            self.assertTrue(cloud.content.startswith(b"ply\n"))
            source_cloud = Path(__file__).resolve().parents[1] / "data/demos/house_demo_risk_20261013/house_complete.ply"
            self.assertEqual(hashlib.sha256(cloud.content).digest(), hashlib.sha256(source_cloud.read_bytes()).digest())


if __name__ == "__main__":
    unittest.main()
